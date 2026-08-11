# -*- coding: utf-8 -*-
"""Opt-in runtime probe for the retail 0.8.2 vehicle physics stack.

Press F6 during a live offline/LAN battle to create one invisible temporary
``OfflineEntity``.  The probe binds the same ``WGVehicleFilter2`` and
``WGVehiclePhysics2`` objects used by retail ``Vehicle.startVisual()``, sends a
short forward input, records the native body transform, then destroys the
entity.  It never owns a real bot and therefore cannot silently change the
production simulation path.
"""

import math
import time
import weakref

import BigWorld
import Math

from gui.mods.offhangar.logging import LOG_ERROR, LOG_NOTE


INITIAL_DELAY = 0.40
ENTITY_WAIT_SECONDS = 2.0
SETTLE_SECONDS = 0.35
DRIVE_SECONDS = 2.0
CLEANUP_DELAY = 0.20
MIN_PASS_DISTANCE = 0.50

_STATE_ATTR = '_offh_native_vehicle_physics_probe_state'
_REQUEST_SERIAL = [0]
_ACTIVE_SERIAL = [0]


def _system_message(message, level='information'):
	try:
		from gui.SystemMessages import SM_TYPE, pushMessage
		if level == 'error':
			message_type = SM_TYPE.Error
		elif level == 'warning':
			message_type = SM_TYPE.Warning
		else:
			message_type = SM_TYPE.Information
		text = str(message)
		try:
			text = text.encode('utf-8')
		except Exception:
			pass
		pushMessage(text, message_type)
		return True
	except Exception:
		return False


def _now():
	try:
		return float(BigWorld.time())
	except Exception:
		return time.time()


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


def _point_text(value):
	point = _point_tuple(value)
	if point is None:
		return '<unavailable>'
	return '(%.2f, %.2f, %.2f)' % point


def request():
	"""Arm one probe run. A second F6 press after completion runs it again."""
	_REQUEST_SERIAL[0] += 1
	_ACTIVE_SERIAL[0] = _REQUEST_SERIAL[0]
	LOG_NOTE('NATIVE_PHYSICS_PROBE requested token=%d' % _REQUEST_SERIAL[0])
	_system_message(
		'Native vehicle physics probe armed (F6). See python.log.',
		'information')
	return _REQUEST_SERIAL[0]


def is_requested():
	return _ACTIVE_SERIAL[0] > 0


def _ground_y(space_id, x, z, hint_y):
	start = Math.Vector3(float(x), float(hint_y) + 35.0, float(z))
	end = Math.Vector3(float(x), float(hint_y) - 120.0, float(z))
	hit = BigWorld.wg_collideSegment(int(space_id), start, end, 128)
	if hit is None:
		return None
	try:
		return float(hit[0].y)
	except Exception:
		return None


def _corridor_clear(space_id, start, end, start_y, end_y):
	"""Require a 3 m-wide, 18 m-long obstacle-free native corridor."""
	dx = float(end[0]) - float(start[0])
	dz = float(end[1]) - float(start[1])
	length = math.sqrt(dx * dx + dz * dz)
	if length <= 0.001:
		return False
	right_x = dz / length
	right_z = -dx / length
	for lateral in (-1.5, 0.0, 1.5):
		first = Math.Vector3(
			float(start[0]) + right_x * lateral, float(start_y) + 1.25,
			float(start[1]) + right_z * lateral)
		second = Math.Vector3(
			float(end[0]) + right_x * lateral, float(end_y) + 1.25,
			float(end[1]) + right_z * lateral)
		if BigWorld.wg_collideSegment(
				int(space_id), first, second, 128) is not None:
			return False
	return True


def _select_probe_pose(space_id, origin, yaw):
	"""Find a short clear patch away from the player without map assumptions."""
	origin = _point_tuple(origin)
	if origin is None:
		raise RuntimeError('player position is unavailable')
	forward_x = math.sin(float(yaw))
	forward_z = math.cos(float(yaw))
	right_x = math.cos(float(yaw))
	right_z = -math.sin(float(yaw))
	# Keep the temporary body away from the spawn crowd. The ordering favours
	# behind/side patches, then falls back to open ground in front.
	offsets = (
		(-38.0, -20.0), (38.0, -20.0),
		(-48.0, 0.0), (48.0, 0.0),
		(-32.0, 28.0), (32.0, 28.0),
		(0.0, -52.0), (0.0, 52.0),
	)
	for lateral, forward in offsets:
		x = origin[0] + right_x * lateral + forward_x * forward
		z = origin[2] + right_z * lateral + forward_z * forward
		end_x = x + forward_x * 18.0
		end_z = z + forward_z * 18.0
		start_y = _ground_y(space_id, x, z, origin[1])
		middle_y = _ground_y(
			space_id, (x + end_x) * 0.5, (z + end_z) * 0.5,
			origin[1])
		end_y = _ground_y(space_id, end_x, end_z, origin[1])
		if start_y is None or middle_y is None or end_y is None:
			continue
		if (abs(middle_y - start_y) > 2.8 or
				abs(end_y - middle_y) > 2.8):
			continue
		if not _corridor_clear(
				space_id, (x, z), (end_x, end_z), start_y, end_y):
			continue
		return Math.Vector3(x, start_y + 0.10, z), float(yaw)
	raise RuntimeError('no clear probe corridor near the player spawn')


def _descriptor_value(container, name, default=None):
	try:
		return container[name]
	except Exception:
		return getattr(container, name, default)


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
	for triangle in (_descriptor_value(
			physics, 'carryingTriangles', ()) or ()):
		p1, p2, p3 = triangle
		vehicle_filter.addTriangle(
			(p1[0], 0.0, p1[1]),
			(p2[0], 0.0, p2[1]),
			(p3[0], 0.0, p3[1]))


def _visibility_mask(player):
	try:
		import ArenaType
		return int(ArenaType.getVisibilityMask(
			int(getattr(player, 'arenaTypeID', 0)) >> 16))
	except Exception:
		# Standard/CTF is bit zero in the pinned 0.8.2 client.
		return 1


def _filter_position(vehicle_filter, entity):
	provider = getattr(vehicle_filter, 'bodyMatrix', None)
	if provider is not None:
		point = _point_tuple(getattr(provider, 'translation', None))
		if point is not None:
			return point
		try:
			point = _point_tuple(Math.Matrix(provider).translation)
			if point is not None:
				return point
		except Exception:
			pass
	return _point_tuple(getattr(entity, 'position', None))


def _speed_snapshot(vehicle_filter):
	values = []
	for name in ('longitudinalSpeed', 'strafeSpeed', 'angularSpeed'):
		try:
			values.append(float(getattr(vehicle_filter, name)))
		except Exception:
			values.append(None)
	try:
		speed_info = getattr(vehicle_filter, 'speedInfo')
		values.append(float(speed_info.value[0]))
	except Exception:
		values.append(None)
	return tuple(values)


def _contact_snapshot(vehicle_filter):
	values = []
	for name in ('numLeftTrackContacts', 'numRightTrackContacts'):
		try:
			values.append(int(getattr(vehicle_filter, name)))
		except Exception:
			values.append(None)
	return tuple(values)


def _destroy_entity(state):
	entity_id = state.get('entity_id')
	state['entity'] = None
	state['filter'] = None
	state['base_filter'] = None
	state['physics'] = None
	if entity_id is not None:
		try:
			BigWorld.destroyEntity(int(entity_id))
		except Exception:
			pass
	state['entity_id'] = None


def _stop_native_input(state):
	vehicle_filter = state.get('filter')
	if vehicle_filter is not None:
		try:
			vehicle_filter.notifyInputKeysDown(0, 0)
		except Exception:
			pass
	physics = state.get('physics')
	if physics is not None:
		for name in ('damageDestructibleCb',
				'destructibleHealthRequestCb', 'onRammingCb',
				'onBecameFrozenCb', 'onStaticDamageCb'):
			try:
				setattr(physics, name, None)
			except Exception:
				pass


def _finish(state, passed, detail):
	_stop_native_input(state)
	state['passed'] = bool(passed)
	state['detail'] = str(detail)
	state['phase'] = 'cleanup'
	state['next_at'] = _now() + CLEANUP_DELAY
	if passed:
		LOG_NOTE('NATIVE_PHYSICS_PROBE PASS %s' % detail)
		_system_message('Native vehicle physics probe passed.', 'information')
	else:
		LOG_ERROR('NATIVE_PHYSICS_PROBE FAIL %s' % detail)
		_system_message(
			'Native vehicle physics probe failed. See python.log.', 'error')


def _fail(state, stage, error):
	_finish(state, False, 'stage=%s error=%s' % (stage, str(error)))


def cancel(player):
	"""Stop and destroy a live probe before the battle resource sweep."""
	state = getattr(player, _STATE_ATTR, None) if player is not None else None
	if isinstance(state, dict):
		_stop_native_input(state)
		_destroy_entity(state)
		state['phase'] = 'done'
	_ACTIVE_SERIAL[0] = 0
	return True


def maybe_run(player, descriptor, position, yaw, space_id,
		battle_generation):
	"""Advance one staged probe step; called from the normal battle tick."""
	if player is None or descriptor is None or not is_requested():
		return False
	try:
		if getattr(getattr(player, 'arena', None), 'period', 0) != 3:
			return False
	except Exception:
		return False

	token = int(_ACTIVE_SERIAL[0])
	state = getattr(player, _STATE_ATTR, None)
	if (not isinstance(state, dict) or state.get('token') != token or
			state.get('generation') != int(battle_generation or 0)):
		if isinstance(state, dict):
			_stop_native_input(state)
			_destroy_entity(state)
		state = {
			'token': token,
			'generation': int(battle_generation or 0),
			'phase': 'prepare',
			'next_at': _now() + INITIAL_DELAY,
			'entity_id': None,
			'entity': None,
			'filter': None,
			'base_filter': None,
			'physics': None,
		}
		setattr(player, _STATE_ATTR, state)
		LOG_NOTE(
			'NATIVE_PHYSICS_PROBE ARM token=%d generation=%d' % (
				token, state['generation']))
		return False

	if state.get('phase') == 'done' or _now() < float(
			state.get('next_at', 0.0)):
		return False

	phase = state.get('phase')
	try:
		if phase == 'prepare':
			probe_position, probe_yaw = _select_probe_pose(
				space_id, position, yaw)
			LOG_NOTE(
				'NATIVE_PHYSICS_PROBE STAGE create pos=%s yaw=%.3f' % (
					_point_text(probe_position), probe_yaw))
			entity_id = BigWorld.createEntity(
				'OfflineEntity', int(space_id), 0, probe_position,
				(0.0, 0.0, probe_yaw), dict())
			state['entity_id'] = int(entity_id)
			state['position'] = probe_position
			state['yaw'] = probe_yaw
			state['phase'] = 'wait_entity'
			state['deadline'] = _now() + ENTITY_WAIT_SECONDS
			state['next_at'] = _now()
			return False

		if phase == 'wait_entity':
			entity = BigWorld.entity(state['entity_id'])
			if entity is None:
				if _now() >= float(state.get('deadline', 0.0)):
					raise RuntimeError('temporary OfflineEntity did not bind')
				state['next_at'] = _now() + 0.05
				return False
			LOG_NOTE('NATIVE_PHYSICS_PROBE STAGE filter entity=%d' % entity.id)
			entity.typeDescriptor = descriptor
			entity.isStarted = True
			entity.isPlayer = False
			# Match retail VehicleAppearance.start(): construct WGVehicleFilter2
			# directly, then feed it the initial authoritative pose through the
			# inherited AvatarFilter input surface.
			vehicle_filter = BigWorld.WGVehicleFilter2()
			vehicle_filter.set(
				_now(), int(space_id), entity.id, state['position'],
				(0.0, 0.0, state['yaw']), 0)
			_configure_filter(vehicle_filter, descriptor)
			entity.filter = vehicle_filter
			state['entity'] = entity
			state['base_filter'] = None
			state['filter'] = vehicle_filter
			state['phase'] = 'physics'
			state['next_at'] = _now()
			return False

		if phase == 'physics':
			LOG_NOTE('NATIVE_PHYSICS_PROBE STAGE physics')
			import physics_shared
			physics = BigWorld.WGVehiclePhysics2()
			physics_shared.initVehiclePhysics(physics, descriptor)
			physics.setArenaBounds((-10000, -10000), (10000, 10000))
			physics.enginePower = float(
				_descriptor_value(
					_descriptor_value(descriptor, 'physics', {}),
					'enginePower', 0.0)) / 1000.0
			physics.owner = weakref.ref(state['entity'])
			physics.staticMode = False
			physics.movementSignals = 0
			for name in ('damageDestructibleCb',
					'destructibleHealthRequestCb', 'onRammingCb',
					'onBecameFrozenCb', 'onStaticDamageCb'):
				try:
					setattr(physics, name, None)
				except Exception:
					pass
			physics.visibilityMask = _visibility_mask(player)
			state['filter'].setVehiclePhysics(physics)
			state['filter'].syncGunAngles(0.0, 0.0)
			state['entity'].wgPhysics = physics
			state['physics'] = physics
			state['phase'] = 'settle'
			state['next_at'] = _now() + SETTLE_SECONDS
			return False

		if phase == 'settle':
			initial = _filter_position(state['filter'], state['entity'])
			if initial is None:
				raise RuntimeError('native body matrix has no position')
			state['initial'] = initial
			state['initial_speed'] = _speed_snapshot(state['filter'])
			state['initial_contacts'] = _contact_snapshot(state['filter'])
			LOG_NOTE(
				'NATIVE_PHYSICS_PROBE STAGE drive initial=%s speed=%s contacts=%s' % (
					_point_text(initial), state['initial_speed'],
					state['initial_contacts']))
			state['filter'].notifyInputKeysDown(1, 0)
			state['phase'] = 'drive'
			state['next_at'] = _now() + DRIVE_SECONDS
			return False

		if phase == 'drive':
			state['filter'].notifyInputKeysDown(0, 0)
			final = _filter_position(state['filter'], state['entity'])
			if final is None:
				raise RuntimeError('native body matrix lost its position')
			initial = state['initial']
			dx = final[0] - initial[0]
			dz = final[2] - initial[2]
			distance = math.sqrt(dx * dx + dz * dz)
			speeds = _speed_snapshot(state['filter'])
			contacts = _contact_snapshot(state['filter'])
			detail = (
				'distance=%.2fm initial=%s final=%s speed0=%s speed1=%s '
				'contacts0=%s contacts1=%s' % (
					distance, _point_text(initial), _point_text(final),
					state.get('initial_speed'), speeds,
					state.get('initial_contacts'), contacts))
			passed = (_finite(distance) and distance >= MIN_PASS_DISTANCE and
					all(_finite(value) for value in final))
			_finish(state, passed, detail)
			return passed

		if phase == 'cleanup':
			_destroy_entity(state)
			state['phase'] = 'done'
			if _ACTIVE_SERIAL[0] == state['token']:
				_ACTIVE_SERIAL[0] = 0
			LOG_NOTE(
				'NATIVE_PHYSICS_PROBE CLEANUP token=%d passed=%s' % (
					state['token'], state.get('passed')))
			return bool(state.get('passed'))

		raise RuntimeError('unknown probe phase %r' % phase)
	except Exception as error:
		_fail(state, phase, error)
		return False
