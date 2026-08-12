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
DYNAMICS_MAX_DT = 0.10
DRIVE_DIAGNOSTIC_IDS = (1000, 1001)
GROUND_SUPPORT_RAY_UP = 3.0
GROUND_SUPPORT_RAY_DOWN = 12.0
GROUND_SUPPORT_TOLERANCE = 3.0
WARMUP_SUPPORT_SINK_TOLERANCE = 0.35
WARMUP_MIN_UP_Y = 0.70710678
WARMUP_POSITION_TOLERANCE = 0.35
WARMUP_YAW_TOLERANCE = 0.05
SEED_POSITION_TOLERANCE = 0.10
SEED_YAW_TOLERANCE = 0.05
POSE_POSITION_TOLERANCE = 2.0
POSE_YAW_TOLERANCE = 0.35
MAX_FRAME_DISPLACEMENT = 12.0
MAX_SAMPLE_GAP_SECONDS = 2.0
DISPLACEMENT_SPEED_FACTOR = 1.75
MAX_ABS_COORDINATE = 12000.0
MOVE_FORWARD_SIGNAL = 1
MOVE_BACKWARD_SIGNAL = 2
ROTATE_LEFT_SIGNAL = 4
ROTATE_RIGHT_SIGNAL = 8

_COUNTERS = {
	'attempted': 0,
	'prepared': 0,
	'activated': 0,
	'active': 0,
	'startup_failed': 0,
	'runtime_failed': 0,
	'stopped': 0,
	'failed': 0,
	'expected': 0,
}
_LAST_ATTACH_TIME = [None]
_STARTUP_SUMMARY_LOGGED = [False]
_DRIVE_LOGGED = [False]
_DYNAMICS_SIMULATOR = [None]
_LAST_SIMULATION_AT = [None]
_SIMULATION_FAILED = [False]
_SIMULATION_LOGGED = [False]
_SIMULATION_DT_CLAMP_LOGGED = [False]


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


def _matrix_pose(provider):
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


def _filter_pose(vehicle_filter):
	return _matrix_pose(getattr(vehicle_filter, 'bodyMatrix', None))


def _entity_pose(state):
	return _matrix_pose(state.get('entity_provider'))


def _frame_pose(state, timestamp):
	"""Return the previous native solve published by this engine filter tick."""
	if state.get('frame_pose_at') == timestamp:
		return state.get('frame_pose')
	return _entity_pose(state)


def _physics_speed(physics, name):
	try:
		value = float(getattr(physics, name))
	except Exception:
		raise RuntimeError('native physics %s is unavailable' % name)
	if not _finite(value):
		raise RuntimeError('native physics %s is invalid' % name)
	return value


def _ground_support(state):
	"""Return the real static support under the staged world-space root."""
	position = _point_tuple(state.get('seed_position'))
	if position is None:
		return None
	try:
		hit = BigWorld.wg_collideSegment(
			int(state.get('space_id', 0)),
			Math.Vector3(position[0], position[1] + GROUND_SUPPORT_RAY_UP,
				position[2]),
			Math.Vector3(position[0], position[1] - GROUND_SUPPORT_RAY_DOWN,
				position[2]),
			128)
	except Exception:
		return None
	if hit is None:
		return None
	try:
		support = _point_tuple(hit[0])
	except Exception:
		support = None
	if support is None:
		return None
	if abs(float(support[1]) - float(position[1])) > GROUND_SUPPORT_TOLERANCE:
		return None
	return support


def _safe_int_attr(owner, name, default=None):
	try:
		return int(getattr(owner, name))
	except Exception:
		return default


def _diagnostic_attr(owner, name):
	try:
		value = getattr(owner, name)
	except Exception:
		return 'unavailable'
	if value is None:
		return 'None'
	if value is True:
		return 'True'
	if value is False:
		return 'False'
	point = _point_tuple(value)
	if point is not None:
		return '(%.3f,%.3f,%.3f)' % point
	try:
		return str(value)
	except Exception:
		return 'unprintable'


def _diagnostic_pose_y(provider):
	pose = _matrix_pose(provider)
	return 'invalid' if pose is None else '%.3f' % float(pose[1])


def _diagnostic_seed_y(state):
	position = _point_tuple(state.get('seed_position'))
	return 'invalid' if position is None else '%.3f' % float(position[1])


def _diagnostic_motor_count(mock):
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	try:
		return str(len(chassis.motors))
	except Exception:
		return 'unknown'


def _arm_drive_diagnostic(mock, state, when, signals):
	if (not signals or _safe_int_attr(mock, 'id', -1) not in
			DRIVE_DIAGNOSTIC_IDS or
			state.get('drive_diagnostic_at') is not None):
		return
	state['drive_diagnostic_at'] = float(when)


def _maybe_log_drive_diagnostic(mock, state, when, signals_before,
		expected_signals, signals_repaired):
	armed_at = state.get('drive_diagnostic_at')
	if (armed_at is None or state.get('drive_diagnostic_logged') or
		float(when) <= float(armed_at) or not expected_signals):
		return
	physics = state.get('physics')
	vehicle_filter = state.get('filter')
	state['drive_diagnostic_logged'] = True
	LOG_NOTE('NATIVE_BOT_PHYSICS drive_diagnostic id=%s signals_before=%s '
		'signals=%s repaired=%s engine_power=%s '
		'normal_engine_power=%s engine_power_mode=%s frozen=%s '
		'frozen_during_frame=%s static_mode=%s tracks_contact=%s allow_tracks=%s '
		'carcass_contact=%s allow_carcass=%s ground_type=%s '
		'seed_y=%s entity_y=%s body_y=%s placing_y=%s root_motors=%s '
		'left_contacts=%s right_contacts=%s force=%s '
		'torque=%s speed=%s rspeed=%s longitudinal_speed=%s angular_speed=%s' % (
			getattr(mock, 'id', '?'),
			str(signals_before),
			_diagnostic_attr(physics, 'movementSignals'),
			str(bool(signals_repaired)),
			_diagnostic_attr(physics, 'enginePower'),
			_diagnostic_attr(physics, 'normalEnginePower'),
			_diagnostic_attr(physics, 'enginePowerMode'),
			_diagnostic_attr(physics, 'isFrozen'),
			_diagnostic_attr(physics, 'isFrozenDuringFrame'),
			_diagnostic_attr(physics, 'staticMode'),
			_diagnostic_attr(physics, 'gotTracksContact'),
			_diagnostic_attr(physics, 'allowTracksContacts'),
			_diagnostic_attr(physics, 'gotCarcassContact'),
			_diagnostic_attr(physics, 'allowCarcassContacts'),
			_diagnostic_attr(physics, 'groundType'),
			_diagnostic_seed_y(state),
			_diagnostic_pose_y(state.get('entity_provider')),
			_diagnostic_pose_y(getattr(vehicle_filter, 'bodyMatrix', None)),
			_diagnostic_pose_y(getattr(
				vehicle_filter, 'placingCompensationMatrix', None)),
			_diagnostic_motor_count(mock),
			_diagnostic_attr(vehicle_filter, 'numLeftTrackContacts'),
			_diagnostic_attr(vehicle_filter, 'numRightTrackContacts'),
			_diagnostic_attr(physics, 'forceApplied'),
			_diagnostic_attr(physics, 'torqueApplied'),
			_diagnostic_attr(physics, 'speed'),
			_diagnostic_attr(physics, 'rspeed'),
			_diagnostic_attr(vehicle_filter, 'longitudinalSpeed'),
			_diagnostic_attr(vehicle_filter, 'angularSpeed')))


def _pose_delta(expected_position, expected_yaw, actual):
	if actual is None:
		return None
	expected = _point_tuple(expected_position)
	if expected is None:
		return None
	dx = float(actual[0]) - expected[0]
	dy = float(actual[1]) - expected[1]
	dz = float(actual[2]) - expected[2]
	distance = math.sqrt(dx * dx + dy * dy + dz * dz)
	yaw_delta = abs(_normalise_angle(actual[3] - float(expected_yaw)))
	return (dx, dy, dz, distance, yaw_delta)


def _pose_matches(expected_position, expected_yaw, actual,
		position_tolerance=POSE_POSITION_TOLERANCE,
		yaw_tolerance=POSE_YAW_TOLERANCE):
	delta = _pose_delta(expected_position, expected_yaw, actual)
	return (delta is not None and
		delta[3] <= float(position_tolerance) and
		delta[4] <= float(yaw_tolerance))


def _upright_pose(pose):
	"""Return the root up-axis Y and total tilt for a finite YPR pose."""
	if pose is None or len(pose) < 6:
		return None
	try:
		up_y = math.cos(float(pose[4])) * math.cos(float(pose[5]))
	except Exception:
		return None
	if not _finite(up_y):
		return None
	up_y = max(-1.0, min(1.0, up_y))
	return (up_y, math.degrees(math.acos(up_y)))


def _warmup_upright_reason(mock, state, pose):
	upright = _upright_pose(pose)
	if upright is not None and upright[0] >= WARMUP_MIN_UP_Y:
		return None
	up_y = 'invalid' if upright is None else '%.4f' % upright[0]
	tilt = 'invalid' if upright is None else '%.1f' % upright[1]
	support = _point_tuple(state.get('ground_support'))
	support_y = 'invalid' if support is None else '%.3f' % support[1]
	vehicle_filter = state.get('filter')
	return ('native body lost an upright spawn pose vehicle=%s pose=%s '
		'up_y=%s tilt_deg=%s support_y=%s left_contacts=%s '
		'right_contacts=%s simulated_frames=%d') % (
		state.get('vehicle_name', '?'), _pose_text(pose), up_y, tilt,
		support_y,
		_diagnostic_attr(vehicle_filter, 'numLeftTrackContacts'),
		_diagnostic_attr(vehicle_filter, 'numRightTrackContacts'),
		int(state.get('simulated_frames', 0) or 0))


def _warmup_pose_reason(mock, state, pose):
	"""Return why a hidden native body is unsafe to reveal, if any."""
	if pose is None or _pose_delta(
			state.get('seed_position'), state.get('seed_yaw'), pose) is None:
		return _pose_mismatch_reason('warmup', state, pose)
	upright_reason = _warmup_upright_reason(mock, state, pose)
	if upright_reason is not None:
		return upright_reason
	support = _point_tuple(state.get('ground_support'))
	if (support is None or pose is None or
			float(pose[1]) <
			float(support[1]) - WARMUP_SUPPORT_SINK_TOLERANCE):
		actual_y = 'invalid' if pose is None else '%.3f' % float(pose[1])
		support_y = 'invalid' if support is None else '%.3f' % support[1]
		return ('native body sank below ground support during warmup '
			'support_y=%s actual_y=%s tolerance=%.3f') % (
				support_y, actual_y, WARMUP_SUPPORT_SINK_TOLERANCE)
	if not _pose_matches(
			state.get('seed_position'), state.get('seed_yaw'), pose,
			WARMUP_POSITION_TOLERANCE, WARMUP_YAW_TOLERANCE):
		return _pose_mismatch_reason('warmup', state, pose)
	return None


def _pose_text(pose):
	if pose is None:
		return 'invalid'
	return '(%.3f,%.3f,%.3f yaw=%.4f pitch=%.4f roll=%.4f)' % (
		pose[0], pose[1], pose[2], pose[3], pose[4], pose[5])


def _pose_mismatch_reason(stage, state, actual, expected_position=None,
		expected_yaw=None):
	if expected_position is None:
		expected_position = state.get('seed_position')
	if expected_yaw is None:
		expected_yaw = float(state.get('seed_yaw', 0.0) or 0.0)
	else:
		expected_yaw = float(expected_yaw)
	delta = _pose_delta(expected_position, expected_yaw, actual)
	if delta is None:
		delta_text = 'delta=invalid distance=invalid yaw_delta=invalid'
	else:
		delta_text = ('delta=(%.3f,%.3f,%.3f) distance=%.3f '
			'yaw_delta=%.4f') % delta
	expected = _point_tuple(expected_position)
	if expected is None:
		expected_text = 'invalid'
	else:
		expected_text = '(%.3f,%.3f,%.3f yaw=%.4f)' % (
			expected[0], expected[1], expected[2], expected_yaw)
	return ('native root pose mismatch stage=%s vehicle=%s expected=%s '
		'actual=%s %s body=%s') % (
		stage, state.get('vehicle_name', '?'), expected_text,
		_pose_text(actual), delta_text,
		_pose_text(_filter_pose(state.get('filter'))))


def _expected_body_count(player):
	try:
		manifest = getattr(player, '_offhangar_network_bot_manifest', None)
		if manifest:
			return len(manifest)
	except Exception:
		pass
	# Stock 15-versus-15 offline battles contain one local player and 29 bots.
	return 29


def _maybe_log_startup_complete():
	if _STARTUP_SUMMARY_LOGGED[0]:
		return
	expected = int(_COUNTERS.get('expected', 0) or 0)
	completed = (int(_COUNTERS['active']) + int(_COUNTERS['startup_failed']) +
		int(_COUNTERS['runtime_failed']) + int(_COUNTERS['stopped']))
	if (expected <= 0 or _COUNTERS['attempted'] < expected or
			completed < _COUNTERS['attempted']):
		return
	_STARTUP_SUMMARY_LOGGED[0] = True
	LOG_NOTE('NATIVE_BOT_PHYSICS startup_complete expected=%d attempted=%d '
		'prepared=%d active=%d failed=%d stopped=%d' % (
		expected, _COUNTERS['attempted'], _COUNTERS['prepared'],
		_COUNTERS['active'], _COUNTERS['failed'],
		_COUNTERS['stopped']))


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


def _fail(mock, state, reason, preserve_pose=False):
	was_live = state.get('phase') == 'active'
	was_active = state.get('phase') in ('active', 'faulted')
	try:
		physics = state.get('physics')
		if physics is not None:
			physics.movementSignals = 0
	except Exception:
		pass
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
		# Filter::input acknowledges a network sample, not a synchronous rigid-body
		# teleport. Freeze the current native root instead of publishing an
		# unverified reseed as the canonical pose.
		if not preserve_pose:
			pose = _entity_pose(state)
			if pose is not None:
				state['last_pose'] = pose
				state['last_pose_at'] = _now()
		state['phase'] = 'faulted'
		if was_live:
			if state.get('counted_active'):
				_COUNTERS['active'] = max(0, _COUNTERS['active'] - 1)
				state['counted_active'] = False
			_COUNTERS['runtime_failed'] += 1
	else:
		provider_restored = True
		if state.get('native_servo') is not None:
			provider_restored = _restore_python_model_provider(mock, state)
		if not provider_restored:
			LOG_ERROR('NATIVE_BOT_PHYSICS Python Servo restore failed id=%s' % (
				getattr(mock, 'id', '?')))
			# The native Servo is still the only attached root owner. Returning None
			# would let the caller start Python motion beside it, so retain the native
			# references and freeze the last verified pose until normal cleanup retries
			# the detach.
			if not preserve_pose:
				pose = _entity_pose(state)
				if pose is not None:
					state['last_pose'] = pose
					state['last_pose_at'] = _now()
			state['pending_fashion'] = None
			state['phase'] = 'faulted'
		else:
			state['physics'] = None
			state['filter'] = None
			state['entity_provider'] = None
			state['pending_fashion'] = None
			state['phase'] = 'failed'
			_restore_avatar_filter(mock)
		_COUNTERS['startup_failed'] += 1
	state['frame_pose'] = None
	state['frame_pose_at'] = None
	state['frame_output_generation'] = -1
	_COUNTERS['failed'] += 1
	LOG_ERROR('NATIVE_BOT_PHYSICS FAIL id=%s phase=%s reason=%s' % (
		getattr(mock, 'id', '?'), state.get('phase'), str(reason)))
	_maybe_log_startup_complete()
	return False


def guard_fault(mock, reason):
	"""Freeze one active native owner without submitting a filter sample."""
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict) or state.get('phase') != 'active':
		return False
	pose = _entity_pose(state)
	if pose is not None:
		state['last_pose'] = pose
		state['last_pose_at'] = _now()
	_fail(mock, state, 'native guard fault: %s' % str(reason), True)
	return state.get('phase') == 'faulted'


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
			'seed_wait', 'warmup', 'active', 'faulted') and
		state.get('filter') is not None)


def _create_dynamics_simulator():
	"""Create the one retail batch solver owned by this battle."""
	import physics_shared
	simulator = BigWorld.WGDynamicsSimulator()
	settings = (
		('numSubsteps', int(getattr(physics_shared, 'NUM_SUBSTEPS', 2))),
		('numIterations', int(getattr(physics_shared, 'NUM_ITERATIONS', 10))),
		('frictionRatio', float(getattr(
			physics_shared, 'FRICTION_RATIO', 1.0))),
		('restitution', float(getattr(physics_shared, 'RESTITUTION', 0.5))),
		('allowedPenetration', float(getattr(
			physics_shared, 'ALLOWED_PENETRATION', 0.01))),
		('midSolvingIterations', int(getattr(
			physics_shared, 'MID_SOLVING_ITERATIONS', 4))),
	)
	for name, value in settings:
		setattr(simulator, name, value)
		actual = getattr(simulator, name)
		if abs(float(actual) - float(value)) > 0.000001:
			raise RuntimeError(
				'native dynamics setting readback mismatch %s=%s expected=%s' % (
					name, str(actual), str(value)))
	return simulator


def simulate_frame(mocks, dt, timestamp=None):
	"""Capture filter output, then advance every bot in one retail batch.

	The 0.8.2 engine does not schedule client-created WGVehiclePhysics2 bodies.
	WGDynamicsSimulator.update owns frame reset, terrain/track/carcass contacts,
	bot-to-bot pairs, force solving and integration. Calling it per vehicle would
	reset the batch repeatedly and lose native pair contacts. WGVehicleFilter2
	publishes an integrated body on the following engine filter tick, so capture
	that completed previous solve before advancing the next shared batch.
	"""
	if _SIMULATION_FAILED[0]:
		return 0
	when = _now() if timestamp is None else float(timestamp)
	if not _finite(when):
		return 0
	previous = _LAST_SIMULATION_AT[0]
	if previous is not None and when <= float(previous):
		return 0
	entries = []
	for mock_id in sorted((mocks or {}).keys()):
		mock = (mocks or {}).get(mock_id)
		state = getattr(mock, STATE_ATTR, None)
		if (not isinstance(state, dict) or state.get('physics') is None or
				state.get('phase') not in ('warmup', 'active', 'faulted')):
			continue
		entries.append((mock, state))
	if not entries:
		return 0
	solve_entries = []
	for mock, state in entries:
		if state.get('phase') == 'faulted':
			# A faulted body remains in the shared batch as a static collision
			# obstacle, but the property that caused the fault may stay unreadable.
			# Never re-run live pose/speed validation or count the same fault again.
			pose = state.get('last_pose')
			if pose is None or len(pose) < 6:
				continue
			solve_entries.append((
				mock, state, int(state.get('simulated_frames', 0) or 0),
				pose, 0.0, 0.0))
			continue
		try:
			pose = _entity_pose(state)
			if pose is None:
				raise RuntimeError(
					'native filter tick returned an invalid pose')
			output_generation = int(
				state.get('simulated_frames', 0) or 0)
			if state.get('phase') == 'warmup' and output_generation > 0:
				warmup_reason = _warmup_pose_reason(mock, state, pose)
				if warmup_reason is not None:
					raise RuntimeError(warmup_reason)
			solve_entries.append((
				mock, state, output_generation, pose,
				_physics_speed(state['physics'], 'speed'),
				_physics_speed(state['physics'], 'rspeed')))
		except Exception as error:
			_fail(mock, state, error)
	if not solve_entries:
		return 0
	try:
		solver_dt = float(dt)
		if not _finite(solver_dt) or solver_dt <= 0.0:
			return 0
		if solver_dt > DYNAMICS_MAX_DT:
			if not _SIMULATION_DT_CLAMP_LOGGED[0]:
				_SIMULATION_DT_CLAMP_LOGGED[0] = True
				LOG_ERROR('NATIVE_BOT_PHYSICS dynamics dt clamped '
					'actual_ms=%d limit_ms=%d' % (
						int(solver_dt * 1000.0 + 0.5),
						int(DYNAMICS_MAX_DT * 1000.0 + 0.5)))
			solver_dt = DYNAMICS_MAX_DT
		if _DYNAMICS_SIMULATOR[0] is None:
			_DYNAMICS_SIMULATOR[0] = _create_dynamics_simulator()
		physics = tuple(
			state['physics']
			for mock, state, output_generation, pose, speed, turn_speed
			in solve_entries)
		_DYNAMICS_SIMULATOR[0].update(solver_dt, physics, ())
	except Exception as error:
		_SIMULATION_FAILED[0] = True
		_DYNAMICS_SIMULATOR[0] = None
		for mock, state, output_generation, pose, speed, turn_speed in solve_entries:
			if state.get('phase') in ('warmup', 'active'):
				_fail(mock, state, RuntimeError(
					'native batch simulation failed: %s' % str(error)), True)
		return 0
	_LAST_SIMULATION_AT[0] = when
	valid_count = 0
	for mock, state, output_generation, pose, speed, turn_speed in solve_entries:
		if state.get('phase') not in ('warmup', 'active', 'faulted'):
			continue
		state['simulated_frames'] = output_generation + 1
		state['frame_pose'] = pose
		state['frame_pose_at'] = when
		state['frame_output_generation'] = output_generation
		state['frame_speed'] = speed
		state['frame_turn_speed'] = turn_speed
		valid_count += 1
	if valid_count and not _SIMULATION_LOGGED[0]:
		_SIMULATION_LOGGED[0] = True
		LOG_NOTE('NATIVE_BOT_PHYSICS dynamics active bodies=%d '
			'substeps=%s iterations=%s' % (
				valid_count,
				str(getattr(_DYNAMICS_SIMULATOR[0], 'numSubsteps', '?')),
				str(getattr(_DYNAMICS_SIMULATOR[0], 'numIterations', '?'))))
	return valid_count


def _attach_physics(player, mock, descriptor, state):
	entity = getattr(mock, 'bw_entity', None)
	if entity is None:
		raise RuntimeError('OfflineEntity disappeared before physics attach')
	import OfflineEntity
	if not OfflineEntity.install_native_destructible_callback_adapter():
		raise RuntimeError('native destructible callback adapter was rejected')
	import physics_shared
	physics = BigWorld.WGVehiclePhysics2()
	old_is_client = physics_shared.IS_CLIENT
	old_roller_mode = physics_shared.ROLLER_MODE
	try:
		# WGDynamicsSimulator is the retail server-style vehicle solver. Its
		# suspension contract needs the non-client hull and roller setup even
		# though this client owns the simulation locally.
		physics_shared.IS_CLIENT = False
		physics_shared.ROLLER_MODE = True
		physics_shared.initVehiclePhysics(physics, descriptor)
	finally:
		physics_shared.IS_CLIENT = old_is_client
		physics_shared.ROLLER_MODE = old_roller_mode
	physics.setArenaBounds((-10000, -10000), (10000, 10000))
	base_power = float(physics.enginePower)
	physics.owner = weakref.ref(entity)
	physics.staticMode = False
	physics.movementSignals = 0
	_clear_callbacks(physics)
	physics.visibilityMask = _visibility_mask(player)
	# WGVehiclePhysics2 leaves these two contact gates uninitialized in the
	# supported 0.8.2 executable, so client-only bodies must define them before
	# simulation. Without the track gate there is no suspension or track force;
	# without the carcass gate hull/static-world contacts are skipped.
	physics.allowTracksContacts = True
	physics.allowCarcassContacts = True
	if (not bool(getattr(physics, 'allowTracksContacts', False)) or
			not bool(getattr(physics, 'allowCarcassContacts', False))):
		raise RuntimeError('native contact enable readback mismatch')
	state['filter'].setVehiclePhysics(physics)
	# Do not call WGVehicleFilter2.syncGunAngles here.  Its native 0.8.2
	# implementation reads EntityManager's retail ServerConnection clock, which
	# is deliberately absent for client-only OfflineEntity instances. Bot turret
	# and gun angles remain owned by the existing Python combat/model path.
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
	state['base_engine_power'] = base_power
	state['last_input'] = (0, 0)


def _activate_model_provider(mock, state):
	"""Make the native entity matrix the sole root-model motion provider."""
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	entity = getattr(mock, 'bw_entity', None)
	if chassis is None or entity is None:
		return False
	provider = state.get('entity_provider')
	if provider is None:
		return False
	if state.get('native_servo') is not None:
		return True
	old_servo = getattr(mock, '_pose_servo', None)
	servo = None
	old_detached = False
	try:
		# Create the replacement object first, but never attach two root motors.
		# Detach the proven Python Servo, attach the native one, and restore the
		# old owner if the second operation fails.
		servo = BigWorld.Servo(provider)
		if old_servo is not None:
			chassis.delMotor(old_servo)
			old_detached = True
		chassis.addMotor(servo)
	except Exception:
		if old_detached:
			try:
				chassis.addMotor(old_servo)
			except Exception:
				mock._pose_servo = None
				mock._servo_added = False
		return False
	mock._native_pose_servo = servo
	mock._pose_servo = None
	mock._servo_added = True
	state['native_servo'] = servo
	return True


def _restore_python_model_provider(mock, state):
	"""Restore the proven Python matrix Servo after a staged setup failure."""
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	if chassis is None:
		return False
	native_servo = state.get('native_servo')
	if native_servo is None:
		return (getattr(mock, '_pose_servo', None) is not None and
			bool(getattr(mock, '_servo_added', False)))
	matrix = getattr(mock, 'matrix', None)
	if matrix is None:
		return False
	try:
		# Detach first so a model that refuses all delMotor calls can never retain
		# both the native and Python root owners. If this fails, the proven native
		# Servo remains the only attached motor and the caller freezes it.
		chassis.delMotor(native_servo)
	except Exception:
		return False
	try:
		fallback_servo = BigWorld.Servo(matrix)
		chassis.addMotor(fallback_servo)
	except Exception:
		# Restore the native owner if the Python replacement cannot be attached.
		# If even recovery fails, record that no Servo is attached; the caller still
		# fails closed and never starts Python motion beside an unknown owner.
		try:
			chassis.addMotor(native_servo)
		except Exception:
			state['native_servo'] = None
			mock._native_pose_servo = None
			mock._servo_added = False
		return False
	state['native_servo'] = None
	mock._native_pose_servo = None
	mock._pose_servo = fallback_servo
	mock._servo_added = True
	return True


def prepare(player, mock, descriptor, space_id, timestamp=None):
	"""Attach one staged native body to an already bound OfflineEntity."""
	if (not _eligible_mock(mock) or descriptor is None or
			not enabled_for(player) or _SIMULATION_FAILED[0]):
		return False
	old_state = getattr(mock, STATE_ATTR, None)
	if isinstance(old_state, dict):
		if old_state.get('phase') == 'stopped':
			# A failed Servo detach deliberately keeps the only retry reference in the
			# stopped state. Do not discard it or attach a second root motion owner.
			if old_state.get('native_servo') is not None:
				stop_mock(mock, False)
				if old_state.get('native_servo') is not None:
					if not old_state.get('reuse_blocked_logged'):
						old_state['reuse_blocked_logged'] = True
						LOG_ERROR('NATIVE_BOT_PHYSICS reuse blocked id=%s '
							'reason=native Servo is still attached' % (
								getattr(mock, 'id', '?')))
					return False
			# A delayed callback or a deliberately reused mock must build a new
			# filter/physics pair; a stopped state owns no usable native objects.
			try:
				delattr(mock, STATE_ATTR)
			except Exception:
				setattr(mock, STATE_ATTR, None)
		else:
			return old_state.get('phase') != 'failed'
	_COUNTERS['attempted'] += 1
	if not _COUNTERS['expected']:
		_COUNTERS['expected'] = _expected_body_count(player)
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
		'entity_provider': None,
		'last_input': None,
		'last_pose': None,
		'last_pose_at': 0.0,
		'frame_pose': None,
		'frame_pose_at': None,
		'frame_output_generation': -1,
		'frame_speed': 0.0,
		'frame_turn_speed': 0.0,
		'simulated_frames': 0,
		'pending_fashion': None,
		'native_servo': None,
		'counted_active': False,
		'max_speed': max(1.0, max_speed),
		'activate_at': 0.0,
		'drive_diagnostic_at': None,
		'drive_diagnostic_logged': False,
		'seed_check_at': 0.0,
		'seed_position': None,
		'seed_yaw': 0.0,
		'ground_support': None,
		'space_id': int(space_id),
		'vehicle_name': str(_descriptor_value(descriptor, 'name', '?') or '?'),
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
		provider = getattr(entity, 'matrix', None)
		if provider is None:
			raise RuntimeError('OfflineEntity matrix provider is unavailable')
		# Stock VehicleAppearance sets this before advanced physics is attached.
		# It prevents the root model from feeding its own transform back into the
		# Entity/filter chain while the native body is staged out of sight.
		provider.notModel = True
		state['entity_provider'] = provider

		from gui.mods.offhangar import native_filter_bridge
		if not native_filter_bridge.seed_filter(
				vehicle_filter, when, int(space_id), position,
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


def _input_sign(value):
	value = float(value or 0.0)
	if value > 0.05:
		return 1
	if value < -0.05:
		return -1
	return 0


def _movement_signals(movement, rotation):
	"""Encode the retail Avatar vehicle movement bit field."""
	signals = 0
	if movement > 0:
		signals |= MOVE_FORWARD_SIGNAL
	elif movement < 0:
		signals |= MOVE_BACKWARD_SIGNAL
	if rotation < 0:
		signals |= ROTATE_LEFT_SIGNAL
	elif rotation > 0:
		signals |= ROTATE_RIGHT_SIGNAL
	return signals


def _set_drive_input(state, movement, rotation):
	"""Apply filter prediction and the offline native track-force adapter."""
	physics = state.get('physics')
	vehicle_filter = state.get('filter')
	if physics is None or vehicle_filter is None:
		raise RuntimeError('native drive objects are unavailable')
	signals = _movement_signals(movement, rotation)
	# notifyInputKeysDown updates WGVehicleFilter2 prediction only. Track force is
	# gated independently by WGVehiclePhysics2.movementSignals in this client-only
	# adapter. Neither the generic setter nor notifyInputKeysDown wakes a rigid
	# body that went to sleep during the countdown.
	physics.movementSignals = signals
	if signals:
		physics.isFrozen = False
	vehicle_filter.notifyInputKeysDown(movement, rotation)
	if int(getattr(physics, 'movementSignals', -1)) != signals:
		raise RuntimeError('native movementSignals readback mismatch')
	if signals and bool(getattr(physics, 'isFrozen', True)):
		raise RuntimeError('native rigid body wake readback mismatch')
	state['last_input'] = (movement, rotation)
	return signals


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
	if _SIMULATION_FAILED[0]:
		_fail(mock, state, 'native batch simulation is unavailable', True)
		if state.get('phase') == 'faulted':
			return _frozen_result(state)
		return None
	when = _now() if timestamp is None else float(timestamp)
	newly_activated = False
	try:
		if state.get('phase') == 'seed_wait':
			if when < float(state.get('seed_check_at', 0.0)):
				return _staged_result(state)
			# physics_shared.initVehiclePhysics is native-heavy. Attach at most
			# one bot for a rendered frame so a full line-up cannot create one
			# countdown spike.
			if _LAST_ATTACH_TIME[0] == when:
				return _staged_result(state)
			pose = _entity_pose(state)
			if not _pose_matches(
					state.get('seed_position'), state.get('seed_yaw'), pose,
					SEED_POSITION_TOLERANCE,
					SEED_YAW_TOLERANCE):
				raise RuntimeError(_pose_mismatch_reason(
					'model_handoff', state, pose))
			support = _ground_support(state)
			if support is None:
				raise RuntimeError(
					'native ground support is unavailable at the seed pose')
			state['ground_support'] = support
			# Keep the proven Python Servo visible while the native body settles in
			# the background. A client-created rigid body can remain stable for the
			# short minimum warmup and tip later in the countdown; revealing it here
			# exposes that invalid pose before the battle starts.
			_attach_physics(player, mock, descriptor, state)
			_LAST_ATTACH_TIME[0] = when
			state['phase'] = 'warmup'
			state['activate_at'] = when + WARMUP_SECONDS
			return _staged_result(state)

		if state.get('phase') == 'warmup':
			if when < float(state.get('activate_at', 0.0)):
				return _staged_result(state)
			if int(state.get('simulated_frames', 0) or 0) <= 0:
				raise RuntimeError(
					'native dynamics simulator did not advance during warmup')
			if int(state.get('frame_output_generation', -1)) <= 0:
				# The first solve still shares a callback with the pre-solve seed
				# capture. Wait for the following engine filter tick to publish it.
				return _staged_result(state)
			pose = _frame_pose(state, when)
			warmup_reason = _warmup_pose_reason(mock, state, pose)
			if warmup_reason is not None:
				raise RuntimeError(warmup_reason)
			if bool(getattr(state.get('physics'), 'staticMode', True)):
				raise RuntimeError(
					'native wiring invariant failed: physics became static')
			entity = getattr(mock, 'bw_entity', None)
			if (entity is None or getattr(entity, 'wgPhysics', None) is not
					state.get('physics') or getattr(mock, 'filter', None) is not
					state.get('filter') or bool(getattr(entity, 'isStarted', False))):
				raise RuntimeError(
					'native wiring invariant failed: owner references changed')
			# Continue validating the hidden body for the entire countdown. Only the
			# first active battle frame may transfer the visible model owner.
			if not active:
				return _staged_result(state)
			if not _activate_model_provider(mock, state):
				raise RuntimeError('native entity matrix could not own the model')
			state['last_pose'] = pose
			state['last_pose_at'] = when
			_COUNTERS['activated'] += 1
			_COUNTERS['active'] += 1
			state['counted_active'] = True
			state['phase'] = 'active'
			# Native ownership begins with the Servo swap, but do not report this body
			# active until its first complete input/readback sample is validated below.
			newly_activated = True

		if state.get('phase') != 'active':
			return None

		movement = _input_sign(throttle) if active else 0
		rotation = _input_sign(turn) if active else 0
		keys = (movement, rotation)
		physics = state.get('physics')
		expected_signals = _movement_signals(movement, rotation)
		signals_before = _safe_int_attr(physics, 'movementSignals')
		signals_repaired = (state.get('last_input') == keys and
			signals_before is not None and
			signals_before != expected_signals)
		needs_drive_update = state.get('last_input') != keys
		if (signals_before is not None and
				signals_before != expected_signals):
			# Keep the native movement owner authoritative if an engine-side path
			# rewrites the bit field between rendered frames.
			needs_drive_update = True
		if (not needs_drive_update and keys != (0, 0) and physics is not None and
				bool(getattr(physics, 'isFrozen', False))):
			# A blocked bot may fall asleep while its high-level command is
			# unchanged. Wake it without disabling normal zero-input auto-freeze.
			needs_drive_update = True
		if needs_drive_update:
			signals = _set_drive_input(state, movement, rotation)
			_arm_drive_diagnostic(mock, state, when, signals)
			if signals and not _DRIVE_LOGGED[0]:
				_DRIVE_LOGGED[0] = True
				LOG_NOTE('NATIVE_BOT_PHYSICS drive active id=%s signals=%d frozen=%s' % (
					getattr(mock, 'id', '?'), signals,
					str(bool(getattr(state.get('physics'), 'isFrozen', True)))))
		_maybe_log_drive_diagnostic(
			mock, state, when, signals_before, expected_signals,
			signals_repaired)

		pose = _frame_pose(state, when)
		if pose is None:
			raise RuntimeError('entity matrix returned an invalid pose')
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
		if newly_activated:
			fashion = state.get('pending_fashion')
			if fashion is not None:
				_bind_fashion_providers(mock, state, fashion)
				state['pending_fashion'] = None
			if (_COUNTERS['activated'] in (1, 5, 15, 29) or
					_COUNTERS['activated'] == _COUNTERS['expected']):
				LOG_NOTE('NATIVE_BOT_PHYSICS active=%d prepared=%d failed=%d' % (
					_COUNTERS['active'], _COUNTERS['prepared'],
					_COUNTERS['failed']))
			_maybe_log_startup_complete()
		return {
			'position': pose[:3],
			'yaw': pose[3],
			'pitch': pose[4],
			'roll': pose[5],
			'velocity': float(state.get('frame_speed', 0.0) or 0.0),
			'turn_velocity': float(state.get('frame_turn_speed', 0.0) or 0.0),
		}
	except Exception as error:
		_fail(mock, state, error)
		if state.get('phase') == 'faulted':
			return _frozen_result(state)
		return None


def hold(mock):
	"""Release both native drive inputs without detaching the rigid body."""
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict) or state.get('phase') != 'active':
		return False
	try:
		_set_drive_input(state, 0, 0)
		return True
	except Exception as error:
		_fail(mock, state, 'hold failed: %s' % str(error))
		return False


def _bind_fashion_providers(mock, state, fashion):
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


def bind_fashion(mock, fashion):
	"""Bind retail placement providers only after native ownership is active."""
	state = getattr(mock, STATE_ATTR, None)
	if (not isinstance(state, dict) or fashion is None or
			state.get('filter') is None or state.get('phase') not in (
				'seed_wait', 'warmup', 'active')):
		return False
	if state.get('phase') != 'active':
		# The visual fashion can exist during staggered startup, but retaining native
		# providers here would keep a failed filter/physics pair alive after fallback.
		state['pending_fashion'] = fashion
		return True
	return _bind_fashion_providers(mock, state, fashion)


def stop_mock(mock, restore_filter=False):
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict):
		return False
	previous_phase = state.get('phase')
	physics = state.get('physics')
	try:
		if physics is not None:
			physics.movementSignals = 0
	except Exception:
		pass
	try:
		vehicle_filter = state.get('filter')
		if vehicle_filter is not None:
			vehicle_filter.notifyInputKeysDown(0, 0)
	except Exception:
		pass
	try:
		if physics is not None:
			physics.staticMode = True
	except Exception:
		pass
	_clear_callbacks(physics)
	native_servo = state.get('native_servo')
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	servo_detached = native_servo is None
	if native_servo is not None:
		if chassis is not None:
			try:
				chassis.delMotor(native_servo)
				servo_detached = True
			except Exception as error:
				LOG_ERROR('NATIVE_BOT_PHYSICS servo detach failed id=%s error=%s' % (
					getattr(mock, 'id', '?'), str(error)))
	if servo_detached:
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
	state['entity_provider'] = None
	state['pending_fashion'] = None
	state['frame_pose'] = None
	state['frame_pose_at'] = None
	state['frame_output_generation'] = -1
	state['phase'] = 'stopped'
	if previous_phase in ('preparing', 'seed_wait', 'warmup', 'active'):
		if previous_phase == 'active' and state.get('counted_active'):
			_COUNTERS['active'] = max(0, _COUNTERS['active'] - 1)
			state['counted_active'] = False
		_COUNTERS['stopped'] += 1
		_maybe_log_startup_complete()
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
	_STARTUP_SUMMARY_LOGGED[0] = False
	_DRIVE_LOGGED[0] = False
	_DYNAMICS_SIMULATOR[0] = None
	_LAST_SIMULATION_AT[0] = None
	_SIMULATION_FAILED[0] = False
	_SIMULATION_LOGGED[0] = False
	_SIMULATION_DT_CLAMP_LOGGED[0] = False
	try:
		import OfflineEntity
		if not OfflineEntity.restore_native_destructible_callback_adapter():
			LOG_ERROR('NATIVE_BOT_PHYSICS destructible adapter restore failed')
	except Exception as error:
		LOG_ERROR('NATIVE_BOT_PHYSICS destructible adapter restore error=%s' % (
			str(error)))
	return count
