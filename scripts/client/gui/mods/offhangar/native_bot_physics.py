# -*- coding: utf-8 -*-
"""Authority-side adapter for the retail 0.8.2 vehicle physics stack.

The bot director, path planner, combat model and LAN protocol remain Python
owned.  This module replaces only local rigid-body integration with the
client's version-locked ``WGVehicleFilter2`` / ``WGVehiclePhysics2`` pair.
Replicas continue consuming authority snapshots and never create native bot
bodies.

Every body is staged before activation.  A setup failure restores the existing
AvatarFilter path before native ownership begins.  Once activated, an invalid
sample freezes that body until the normal battle sweep; it never hot-switches
to a second movement owner in the middle of a round.
"""

import math
import weakref

import BigWorld
import Math

from gui.mods.offhangar.logging import LOG_ERROR, LOG_NOTE


STATE_ATTR = '_offh_native_bot_physics_state'
SEED_CHECK_SECONDS = 0.10
WARMUP_SECONDS = 0.35
SETTLE_SECONDS = 0.10
POSE_POSITION_TOLERANCE = 2.0
POSE_YAW_TOLERANCE = 0.35
MAX_FRAME_DISPLACEMENT = 12.0
MAX_SAMPLE_GAP_SECONDS = 2.0
DISPLACEMENT_SPEED_FACTOR = 1.75
MAX_ABS_COORDINATE = 12000.0

_COUNTERS = {
	'prepared': 0,
	'active': 0,
	'failed': 0,
}
_LAST_ATTACH_TIME = [None]


def _now():
	try:
		return float(BigWorld.time())
	except Exception:
		return 0.0


def _finite(value):
	try:
		value = float(value)
		return not math.isnan(value) and not math.isinf(value)
	except Exception:
		return False


def _point_tuple(value):
	try:
		return (float(value.x), float(value.y), float(value.z))
	except Exception:
		try:
			return (float(value[0]), float(value[1]), float(value[2]))
		except Exception:
			return None


def _normalise_angle(value):
	value = float(value)
	while value > math.pi:
		value -= math.pi * 2.0
	while value < -math.pi:
		value += math.pi * 2.0
	return value


def _distance(first, second):
	first = _point_tuple(first)
	second = _point_tuple(second)
	if first is None or second is None:
		return None
	dx = second[0] - first[0]
	dy = second[1] - first[1]
	dz = second[2] - first[2]
	return math.sqrt(dx * dx + dy * dy + dz * dz)


def _descriptor_value(container, name, default=None):
	try:
		return container[name]
	except Exception:
		return getattr(container, name, default)


def _config():
	try:
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		return CONFIG_OPTIONS
	except Exception:
		return {}


def enabled_for(player):
	"""Return whether this process is allowed to own native bot bodies."""
	cfg = _config()
	if not bool(cfg.get('experimental_native_bot_physics', False)):
		return False
	client = getattr(player, '_offhangar_network_client', None)
	if client is not None:
		if bool(getattr(player, '_offhangar_network_fallback_local', False)):
			return True
		if (not getattr(client, 'ready', False) or
				getattr(client, 'phase', None) != 'battle'):
			return False
		try:
			from gui.mods.offhangar.network_battle import network_is_authority
			return bool(network_is_authority(player))
		except Exception:
			return False
	return True


def _eligible_mock(mock):
	"""Reject human snapshot proxies; only local/shared AI may own a body."""
	if mock is None:
		return False
	return not (bool(getattr(mock, '_network_remote', False)) and
		not bool(getattr(mock, '_network_shared_bot', False)))


def _visibility_mask(player):
	try:
		import ArenaType
		return int(ArenaType.getVisibilityMask(
			int(getattr(player, 'arenaTypeID', 0)) >> 16))
	except Exception:
		return 1


def _configure_filter(vehicle_filter, descriptor):
	chassis = _descriptor_value(descriptor, 'chassis', {})
	physics = _descriptor_value(descriptor, 'physics', {})
	try:
		vehicle_filter.vehicleWidth = float(
			_descriptor_value(chassis, 'topRightCarryingPoint')[0]) * 2.0
	except Exception:
		vehicle_filter.vehicleWidth = 3.0
	try:
		vehicle_filter.vehicleMaxMove = float(
			_descriptor_value(physics, 'speedLimits')[0]) * 2.0
	except Exception:
		vehicle_filter.vehicleMaxMove = 100.0
	try:
		vehicle_filter.vehicleMinNormalY = float(
			_descriptor_value(physics, 'minPlaneNormalY'))
	except Exception:
		vehicle_filter.vehicleMinNormalY = 0.5
	try:
		vehicle_filter.vehicleCollisionCallback = None
	except Exception:
		pass
	try:
		vehicle_filter.isLaggingStateChangedCallback = None
	except Exception:
		pass
	try:
		vehicle_filter.allowStrafeCompensation = True
	except Exception:
		pass
	for triangle in (_descriptor_value(
			physics, 'carryingTriangles', ()) or ()):
		p1, p2, p3 = triangle
		vehicle_filter.addTriangle(
			(p1[0], 0.0, p1[1]),
			(p2[0], 0.0, p2[1]),
			(p3[0], 0.0, p3[1]))


def _filter_pose(vehicle_filter):
	provider = getattr(vehicle_filter, 'bodyMatrix', None)
	if provider is None:
		return None
	try:
		matrix = Math.Matrix(provider)
	except Exception:
		matrix = provider
	position = _point_tuple(getattr(matrix, 'translation', None))
	if position is None:
		position = _point_tuple(getattr(provider, 'translation', None))
	if position is None:
		return None
	try:
		yaw = float(matrix.yaw)
	except Exception:
		try:
			forward = matrix.applyVector(Math.Vector3(0.0, 0.0, 1.0))
			yaw = math.atan2(float(forward.x), float(forward.z))
		except Exception:
			return None
	try:
		pitch = float(matrix.pitch)
	except Exception:
		pitch = 0.0
	try:
		roll = float(matrix.roll)
	except Exception:
		roll = 0.0
	values = position + (yaw, pitch, roll)
	if not all(_finite(value) for value in values):
		return None
	if (abs(position[0]) > MAX_ABS_COORDINATE or
			abs(position[1]) > MAX_ABS_COORDINATE or
			abs(position[2]) > MAX_ABS_COORDINATE):
		return None
	return values


def _speed(vehicle_filter, name, default):
	try:
		value = float(getattr(vehicle_filter, name))
		return value if _finite(value) else float(default)
	except Exception:
		return float(default)


def _pose_matches(expected_position, expected_yaw, actual):
	if actual is None:
		return False
	distance = _distance(expected_position, actual[:3])
	if distance is None or distance > POSE_POSITION_TOLERANCE:
		return False
	return abs(_normalise_angle(actual[3] - float(expected_yaw))) <= POSE_YAW_TOLERANCE


def _clear_callbacks(physics):
	if physics is None:
		return
	for name in ('damageDestructibleCb', 'destructibleHealthRequestCb',
			'onRammingCb', 'onBecameFrozenCb', 'onStaticDamageCb'):
		try:
			setattr(physics, name, None)
		except Exception:
			pass


def _restore_avatar_filter(mock):
	entity = getattr(mock, 'bw_entity', None)
	if entity is None:
		return
	try:
		entity.wgPhysics = None
		entity.isStarted = False
		entity.typeDescriptor = None
	except Exception:
		pass
	try:
		entity.filter = BigWorld.AvatarFilter()
		mock.filter = entity.filter
	except Exception:
		pass
	try:
		installer = getattr(mock, '_offh_install_collision_obstacle', None)
		if installer is not None:
			installer()
	except Exception:
		pass


def _fail(mock, state, reason):
	was_active = state.get('phase') in ('active', 'faulted')
	try:
		vehicle_filter = state.get('filter')
		if vehicle_filter is not None:
			vehicle_filter.notifyInputKeysDown(0, 0)
	except Exception:
		pass
	try:
		physics = state.get('physics')
		if physics is not None:
			physics.staticMode = True
	except Exception:
		pass
	_clear_callbacks(state.get('physics'))
	state['reason'] = str(reason)
	if was_active:
		# Never hot-swap a live native body back to Python. Freeze at the last
		# validated pose, keep one motion owner, and release it during the normal
		# battle sweep. The next battle may use the Python fallback cleanly.
		try:
			state['physics'].staticMode = True
		except Exception:
			pass
		# bodyMatrix may already contain the sample that tripped validation.
		# Best-effort reseed the now-static body to the last accepted pose so the
		# native root model, collision body and Python compatibility mirror remain
		# together. Failure stays fail-closed and is made explicit in the log.
		freeze_reseed = False
		pose = state.get('last_pose')
		entity = getattr(mock, 'bw_entity', None)
		if pose is not None and len(pose) >= 6 and entity is not None:
			try:
				from gui.mods.offhangar import native_filter_bridge
				freeze_reseed = native_filter_bridge.seed_filter(
					state.get('filter'), _now(),
					int(state.get('space_id', 0)), int(entity.id), pose[:3],
					(pose[5], pose[4], pose[3]))
			except Exception:
				freeze_reseed = False
		state['freeze_reseed'] = bool(freeze_reseed)
		state['phase'] = 'faulted'
	else:
		state['physics'] = None
		state['filter'] = None
		state['phase'] = 'failed'
		_restore_avatar_filter(mock)
	_COUNTERS['failed'] += 1
	LOG_ERROR('NATIVE_BOT_PHYSICS FAIL id=%s phase=%s freeze_reseed=%s reason=%s' % (
		getattr(mock, 'id', '?'), state.get('phase'),
		str(state.get('freeze_reseed', 'n/a')), str(reason)))
	return False


def _frozen_result(state):
	pose = state.get('last_pose')
	if pose is None or len(pose) < 6:
		return None
	return {
		'position': pose[:3],
		'yaw': pose[3],
		'pitch': pose[4],
		'roll': pose[5],
		'velocity': 0.0,
		'turn_velocity': 0.0,
		'faulted': True,
	}


def _staged_result(state):
	"""Keep the Python proxy at the seeded pose until native ownership starts."""
	pose = state.get('last_pose')
	if pose is None or len(pose) < 6:
		return None
	return {
		'position': pose[:3],
		'yaw': pose[3],
		'pitch': pose[4],
		'roll': pose[5],
		'velocity': 0.0,
		'turn_velocity': 0.0,
		'staging': True,
	}


def is_prepared(mock):
	return isinstance(getattr(mock, STATE_ATTR, None), dict)


def is_active(mock):
	state = getattr(mock, STATE_ATTR, None)
	return isinstance(state, dict) and state.get('phase') == 'active'


def owns_filter(mock):
	state = getattr(mock, STATE_ATTR, None)
	return (isinstance(state, dict) and
		state.get('phase') in (
			'seed_wait', 'warmup', 'settle', 'active', 'faulted') and
		state.get('filter') is not None)


def _attach_physics(player, mock, descriptor, state):
	entity = getattr(mock, 'bw_entity', None)
	if entity is None:
		raise RuntimeError('OfflineEntity disappeared before physics attach')
	import OfflineEntity
	if not OfflineEntity.install_native_destructible_callback_adapter():
		raise RuntimeError('native destructible callback adapter was rejected')
	import physics_shared
	physics = BigWorld.WGVehiclePhysics2()
	physics_shared.initVehiclePhysics(physics, descriptor)
	physics.setArenaBounds((-10000, -10000), (10000, 10000))
	base_power = float(_descriptor_value(
		_descriptor_value(descriptor, 'physics', {}),
		'enginePower', 0.0)) / 1000.0
	physics.enginePower = base_power
	physics.owner = weakref.ref(entity)
	physics.staticMode = False
	physics.movementSignals = 0
	_clear_callbacks(physics)
	physics.visibilityMask = _visibility_mask(player)
	state['filter'].setVehiclePhysics(physics)
	state['filter'].syncGunAngles(0.0, 0.0)
	state['filter'].notifyInputKeysDown(0, 0)
	entity.typeDescriptor = descriptor
	# Keep OfflineEntity outside the retail Vehicle UI lifecycle. Engine entity
	# ids can collide with the fake arena playerVehicleID; the stock arcade,
	# sniper and strategic cameras then treat any entity with isStarted=True as
	# the local Vehicle and dereference its full VehicleAppearance. Native
	# physics only needs the owner/filter/descriptor references established here.
	entity.isStarted = False
	entity.isPlayer = False
	entity.wgPhysics = physics
	mock.filter = state['filter']
	state['physics'] = physics
	# The proxy is held at the exact seed while providers are wired.  Letting
	# this body move before the model switches from the Python Servo would create
	# two visible/physical poses for several frames.
	physics.staticMode = True
	state['base_engine_power'] = base_power
	state['last_input'] = (0, 0)


def _activate_model_provider(mock, state):
	"""Make the native entity matrix the sole root-model motion provider."""
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	entity = getattr(mock, 'bw_entity', None)
	if chassis is None or entity is None:
		return False
	provider = getattr(entity, 'matrix', None)
	if provider is None:
		return False
	try:
		# VehicleAppearance.__setupModels marks the entity provider this way
		# before installing its Servo.  It prevents the attached root model from
		# feeding its own transform back into the entity/filter chain.
		provider.notModel = True
	except Exception:
		return False
	old_servo = getattr(mock, '_pose_servo', None)
	servo = None
	try:
		# Create and attach the replacement first.  If detaching the old Servo
		# fails, remove the replacement again and leave the proven Python path
		# intact so the caller can fail before native activation.
		servo = BigWorld.Servo(provider)
		chassis.addMotor(servo)
		if old_servo is not None:
			chassis.delMotor(old_servo)
	except Exception:
		if servo is not None:
			try:
				chassis.delMotor(servo)
			except Exception:
				pass
		return False
	mock._native_pose_servo = servo
	mock._pose_servo = None
	mock._servo_added = True
	state['native_servo'] = servo
	return True


def prepare(player, mock, descriptor, space_id, timestamp=None):
	"""Attach one staged native body to an already bound OfflineEntity."""
	if (not _eligible_mock(mock) or descriptor is None or
			not enabled_for(player)):
		return False
	old_state = getattr(mock, STATE_ATTR, None)
	if isinstance(old_state, dict):
		if old_state.get('phase') == 'stopped':
			# A delayed callback or a deliberately reused mock must build a new
			# filter/physics pair; a stopped state owns no usable native objects.
			try:
				delattr(mock, STATE_ATTR)
			except Exception:
				setattr(mock, STATE_ATTR, None)
		else:
			return old_state.get('phase') != 'failed'
	try:
		max_speed = abs(float(_descriptor_value(
			_descriptor_value(descriptor, 'physics', {}),
			'speedLimits', (50.0, 0.0))[0]))
	except Exception:
		max_speed = 50.0
	state = {
		'phase': 'preparing',
		'filter': None,
		'physics': None,
		'last_input': None,
		'last_pose': None,
		'last_pose_at': 0.0,
		'max_speed': max(1.0, max_speed),
		'activate_at': 0.0,
		'settle_at': 0.0,
		'seed_check_at': 0.0,
		'seed_position': None,
		'seed_yaw': 0.0,
		'space_id': int(space_id),
	}
	setattr(mock, STATE_ATTR, state)
	try:
		entity = getattr(mock, 'bw_entity', None)
		if entity is None:
			raise RuntimeError('OfflineEntity is not bound')
		position = _point_tuple(getattr(mock, 'position', None))
		if position is None:
			raise RuntimeError('mock pose is unavailable')
		yaw = float(getattr(mock, 'yaw', 0.0) or 0.0)
		when = _now() if timestamp is None else float(timestamp)

		vehicle_filter = BigWorld.WGVehicleFilter2()
		entity.filter = vehicle_filter
		_configure_filter(vehicle_filter, descriptor)
		state['filter'] = vehicle_filter

		from gui.mods.offhangar import native_filter_bridge
		if not native_filter_bridge.seed_filter(
				vehicle_filter, when, int(space_id), int(entity.id), position,
				(0.0, 0.0, yaw)):
			raise RuntimeError('Filter::input seed was rejected')
		entity.typeDescriptor = descriptor
		# See _attach_physics: this flag belongs to the stock Vehicle appearance
		# lifecycle, not WGVehicleFilter2/WGVehiclePhysics2 ownership.
		entity.isStarted = False
		entity.isPlayer = False
		mock.filter = vehicle_filter
		state['seed_position'] = position
		state['seed_yaw'] = yaw
		state['phase'] = 'seed_wait'
		state['seed_check_at'] = when + SEED_CHECK_SECONDS
		state['last_pose'] = position + (yaw,
			float(getattr(mock, 'pitch', 0.0) or 0.0),
			float(getattr(mock, 'roll', 0.0) or 0.0))
		state['last_pose_at'] = when
		_COUNTERS['prepared'] += 1
		if _COUNTERS['prepared'] in (1, 5, 15, 29):
			LOG_NOTE('NATIVE_BOT_PHYSICS prepared=%d id=%s' % (
				_COUNTERS['prepared'], getattr(mock, 'id', '?')))
		return True
	except Exception as error:
		return _fail(mock, state, error)


def _seed_current(mock, state, space_id, timestamp):
	position = _point_tuple(getattr(mock, 'position', None))
	if position is None:
		return False
	yaw = float(getattr(mock, 'yaw', 0.0) or 0.0)
	from gui.mods.offhangar import native_filter_bridge
	if not native_filter_bridge.seed_filter(
			state['filter'], timestamp, int(space_id),
			int(getattr(getattr(mock, 'bw_entity', None), 'id', 0)),
			position, (0.0, 0.0, yaw)):
		return False
	state['seed_position'] = position
	state['seed_yaw'] = yaw
	state['last_pose'] = position + (
		yaw, float(getattr(mock, 'pitch', 0.0) or 0.0),
		float(getattr(mock, 'roll', 0.0) or 0.0))
	state['last_pose_at'] = float(timestamp)
	return True


def _input_sign(value):
	value = float(value or 0.0)
	if value > 0.05:
		return 1
	if value < -0.05:
		return -1
	return 0


def step(player, mock, descriptor, throttle, turn, space_id,
		timestamp=None, active=True):
	"""Advance staged ownership and return a canonical native pose sample.

	A staged result holds the canonical seed while the filter and model provider
	are validated.  ``None`` means native setup failed before ownership and the
	caller may use the established Python fallback.  An active result contains
	position, yaw, pitch, roll, longitudinal and angular speed.
	"""
	if not enabled_for(player):
		return None
	if not _eligible_mock(mock):
		return None
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict):
		if not prepare(player, mock, descriptor, space_id, timestamp):
			return None
		state = getattr(mock, STATE_ATTR, None)
	if state.get('phase') == 'failed':
		return None
	if state.get('phase') == 'faulted':
		return _frozen_result(state)
	when = _now() if timestamp is None else float(timestamp)
	try:
		if state.get('phase') == 'seed_wait':
			if when < float(state.get('seed_check_at', 0.0)):
				return _staged_result(state)
			# physics_shared.initVehiclePhysics is native-heavy. Attach at most
			# one bot for a rendered frame so a full line-up cannot create one
			# countdown spike.
			if _LAST_ATTACH_TIME[0] == when:
				return _staged_result(state)
			if not _pose_matches(
					state.get('seed_position'), state.get('seed_yaw'),
					_filter_pose(state['filter'])):
				raise RuntimeError('filter pose mismatch after staged seed')
			_attach_physics(player, mock, descriptor, state)
			_LAST_ATTACH_TIME[0] = when
			state['phase'] = 'warmup'
			state['activate_at'] = when + WARMUP_SECONDS
			return _staged_result(state)

		if state.get('phase') == 'warmup':
			if when < float(state.get('activate_at', 0.0)):
				return _staged_result(state)
			if not _seed_current(mock, state, space_id, when):
				raise RuntimeError('activation seed was rejected')
			state['filter'].notifyInputKeysDown(0, 0)
			state['last_input'] = (0, 0)
			state['phase'] = 'settle'
			state['settle_at'] = when + SETTLE_SECONDS
			return _staged_result(state)

		if state.get('phase') == 'settle':
			if when < float(state.get('settle_at', 0.0)):
				return _staged_result(state)
			pose = _filter_pose(state['filter'])
			if not _pose_matches(
					state.get('seed_position'), state.get('seed_yaw'), pose):
				raise RuntimeError('native body moved away during activation')
			if not _activate_model_provider(mock, state):
				raise RuntimeError('native entity matrix could not own the model')
			state['physics'].staticMode = False
			state['phase'] = 'active'
			state['last_pose'] = pose
			state['last_pose_at'] = when
			_COUNTERS['active'] += 1
			if _COUNTERS['active'] in (1, 5, 15, 29):
				LOG_NOTE('NATIVE_BOT_PHYSICS active=%d prepared=%d failed=%d' % (
					_COUNTERS['active'], _COUNTERS['prepared'],
					_COUNTERS['failed']))

		if state.get('phase') != 'active':
			return None

		movement = _input_sign(throttle) if active else 0
		rotation = _input_sign(turn) if active else 0
		keys = (movement, rotation)
		if state.get('last_input') != keys:
			state['filter'].notifyInputKeysDown(movement, rotation)
			state['last_input'] = keys

		pose = _filter_pose(state['filter'])
		if pose is None:
			raise RuntimeError('bodyMatrix returned an invalid pose')
		previous = state.get('last_pose')
		frame_distance = _distance(previous, pose) if previous is not None else 0.0
		last_pose_at = float(state.get('last_pose_at', when) or when)
		sample_gap = max(0.0, min(
			MAX_SAMPLE_GAP_SECONDS, when - last_pose_at))
		max_displacement = (MAX_FRAME_DISPLACEMENT +
			float(state.get('max_speed', 50.0) or 50.0) *
			DISPLACEMENT_SPEED_FACTOR * sample_gap)
		if frame_distance is None or frame_distance > max_displacement:
			raise RuntimeError(
				'implausible native displacement %.2fm limit=%.2fm gap=%.3fs' % (
					-1.0 if frame_distance is None else frame_distance,
					max_displacement, sample_gap))
		state['last_pose'] = pose
		state['last_pose_at'] = when
		return {
			'position': pose[:3],
			'yaw': pose[3],
			'pitch': pose[4],
			'roll': pose[5],
			'velocity': _speed(
				state['filter'], 'longitudinalSpeed',
				getattr(mock, '_veh_velocity', 0.0) or 0.0),
			'turn_velocity': _speed(
				state['filter'], 'angularSpeed',
				getattr(mock, '_veh_turn_velocity', 0.0) or 0.0),
		}
	except Exception as error:
		was_active = state.get('phase') in ('active', 'faulted')
		_fail(mock, state, error)
		if was_active and state.get('phase') == 'faulted':
			return _frozen_result(state)
		return None


def reseed(mock, position, yaw, space_id, timestamp=None):
	"""Apply one bounded authoritative correction to an active native body."""
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict) or state.get('phase') != 'active':
		return False
	when = _now() if timestamp is None else float(timestamp)
	point = _point_tuple(position)
	if point is None:
		return False
	try:
		from gui.mods.offhangar import native_filter_bridge
		if not native_filter_bridge.seed_filter(
				state['filter'], when, int(space_id),
				int(getattr(getattr(mock, 'bw_entity', None), 'id', 0)),
				point, (0.0, 0.0, float(yaw))):
			raise RuntimeError('Filter::input correction was rejected')
		state['last_pose'] = point + (
			float(yaw), float(getattr(mock, 'pitch', 0.0) or 0.0),
			float(getattr(mock, 'roll', 0.0) or 0.0))
		state['last_pose_at'] = when
		state['space_id'] = int(space_id)
		return True
	except Exception as error:
		_fail(mock, state, 'reseed failed: %s' % str(error))
		return False


def hold(mock):
	"""Release both native drive inputs without detaching the rigid body."""
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict) or state.get('phase') != 'active':
		return False
	try:
		state['filter'].notifyInputKeysDown(0, 0)
		state['last_input'] = (0, 0)
		return True
	except Exception as error:
		_fail(mock, state, 'hold failed: %s' % str(error))
		return False


def bind_fashion(mock, fashion):
	"""Bind the retail rigid-body placement compensation when available."""
	state = getattr(mock, STATE_ATTR, None)
	if (not isinstance(state, dict) or fashion is None or
			state.get('filter') is None):
		return False
	try:
		fashion.placingCompensationMatrix = (
			state['filter'].placingCompensationMatrix)
		fashion.physicsInfo = state['filter'].physicsInfo
		fashion.movementInfo = state['filter'].movementInfo
		return True
	except Exception as error:
		if not state.get('fashion_error_logged'):
			state['fashion_error_logged'] = True
			LOG_ERROR('NATIVE_BOT_PHYSICS fashion bind failed id=%s error=%s' % (
				getattr(mock, 'id', '?'), str(error)))
		return False


def stop_mock(mock, restore_filter=False):
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict):
		return False
	try:
		vehicle_filter = state.get('filter')
		if vehicle_filter is not None:
			vehicle_filter.notifyInputKeysDown(0, 0)
	except Exception:
		pass
	physics = state.get('physics')
	try:
		if physics is not None:
			physics.staticMode = True
	except Exception:
		pass
	_clear_callbacks(physics)
	native_servo = state.get('native_servo')
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	if native_servo is not None:
		if chassis is not None:
			try:
				chassis.delMotor(native_servo)
			except Exception:
				pass
	state['native_servo'] = None
	try:
		mock._native_pose_servo = None
		mock._servo_added = False
	except Exception:
		pass
	# WGVehicleFashion keeps all three native filter providers alive. Detach it
	# with the retail delattr pattern before dropping the filter/physics refs.
	if getattr(mock, '_fashion', None) is not None:
		if chassis is not None:
			try:
				delattr(chassis, 'wg_fashion')
			except Exception:
				pass
		try:
			mock._fashion = None
		except Exception:
			pass
	entity = getattr(mock, 'bw_entity', None)
	if entity is not None:
		try:
			entity.wgPhysics = None
			entity.isStarted = False
			entity.typeDescriptor = None
		except Exception:
			pass
		try:
			entity.filter = BigWorld.AvatarFilter()
			mock.filter = entity.filter
		except Exception:
			pass
	state['filter'] = None
	state['physics'] = None
	state['phase'] = 'stopped'
	if restore_filter:
		_restore_avatar_filter(mock)
	return True


def stop_all(mocks):
	count = 0
	for mock in list((mocks or {}).values()):
		if stop_mock(mock, False):
			count += 1
	if count:
		LOG_NOTE('NATIVE_BOT_PHYSICS stopped=%d active=%d failed=%d' % (
			count, _COUNTERS['active'], _COUNTERS['failed']))
	for name in _COUNTERS:
		_COUNTERS[name] = 0
	_LAST_ATTACH_TIME[0] = None
	try:
		import OfflineEntity
		if not OfflineEntity.restore_native_destructible_callback_adapter():
			LOG_ERROR('NATIVE_BOT_PHYSICS destructible adapter restore failed')
	except Exception as error:
		LOG_ERROR('NATIVE_BOT_PHYSICS destructible adapter restore error=%s' % (
			str(error)))
	return count
