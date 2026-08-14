# -*- coding: utf-8 -*-
"""Authority-side adapter for the retail 0.8.2 vehicle physics stack.

The bot director, path planner, combat model and LAN protocol remain Python
owned.  This module replaces only local rigid-body integration with the
client's version-locked ``WGVehicleFilter2`` / ``WGVehiclePhysics2`` pair.
Replicas continue consuming authority snapshots and never create native bot
bodies.

Every body is staged before activation. In this native-only build, setup
failure is fail-closed: the bot remains at its last verified pose and never
enters the legacy Python kinematics path.  Once activated, an invalid sample
freezes that body until the normal battle sweep; it never hot-switches to a
second movement owner in the middle of a round.
"""

import math
import weakref

import BigWorld
import Math

from gui.mods.offhangar.logging import LOG_ERROR, LOG_NOTE


STATE_ATTR = '_offh_native_bot_physics_state'
REQUIRED_ATTR = '_offh_native_movement_required'
SEED_CHECK_SECONDS = 0.10
WARMUP_SECONDS = 0.35
DYNAMICS_MAX_DT = 0.10
DESTRUCTIBLE_BATCH_EVENT_LIMIT = 128
DESTRUCTIBLE_WARMUP_EVENT_LIMIT = 64
DRIVE_DIAGNOSTIC_IDS = (1000, 1001)
DRIVE_DIAGNOSTIC_DELAY = 1.0
PRESENTATION_DIAGNOSTIC_VEHICLES = ('Ferdinand',)
PRESENTATION_DIAGNOSTIC_COOLDOWN = 2.0
PRESENTATION_DIAGNOSTIC_LOG_LIMIT = 20
PRESENTATION_DIAGNOSTIC_GLOBAL_LOG_LIMIT = 20
STEERING_INPUT_THRESHOLD = 0.10
GROUND_SUPPORT_RAY_UP = 3.0
GROUND_SUPPORT_RAY_DOWN = 12.0
GROUND_SUPPORT_TOLERANCE = 3.0
GROUND_SUPPORT_RETRY_SECONDS = 0.25
GROUND_SUPPORT_LOG_SECONDS = 3.0
WARMUP_SUPPORT_SINK_TOLERANCE = 0.35
WARMUP_MIN_UP_Y = 0.70710678
WARMUP_POSITION_TOLERANCE = 0.35
WARMUP_YAW_TOLERANCE = 0.06
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
_PRESENTATION_DIAGNOSTIC_LOGGED = [0]

try:
	_INTEGER_TYPES = (int, long)
except NameError:
	_INTEGER_TYPES = (int,)


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


def enabled_for(player):
	"""Return whether this native-only process currently owns bot simulation."""
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


def _physics_pose(physics):
	"""Copy the solved C++ chassis root exposed by WGVehiclePhysics2.matrix."""
	return _matrix_pose(getattr(physics, 'matrix', None))


def _frame_pose(state, timestamp):
	"""Return the exact C++ root cached for this native solve timestamp."""
	if state.get('frame_pose_at') == timestamp:
		return state.get('frame_pose')
	# Never combine an arbitrary entity-provider pose with speed/rspeed cached
	# from a different solve. Missing explicit output is a native failure.
	return None


def _physics_speed(physics, name):
	try:
		value = float(getattr(physics, name))
	except Exception:
		raise RuntimeError('native physics %s is unavailable' % name)
	if not _finite(value):
		raise RuntimeError('native physics %s is invalid' % name)
	return value


def _ground_support(state):
	"""Return live static support at the native seed pose.

	A prebaked navigation height is only a placement hint.  It cannot prove that
	the client-side dynamics world currently owns the corresponding terrain
	chunk, so a collision miss must remain retryable instead of attaching a rigid
	body over an absent floor.
	"""
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
	except Exception as error:
		state['ground_support_source'] = 'error'
		raise RuntimeError(
			'native ground support probe failed: %s' % str(error))
	if hit is None:
		state['ground_support_source'] = 'unavailable'
		return None
	try:
		support = _point_tuple(hit[0])
	except Exception as error:
		state['ground_support_source'] = 'invalid'
		raise RuntimeError(
			'native ground support result is invalid: %s' % str(error))
	if (support is None or
			not all(_finite(value) for value in support)):
		state['ground_support_source'] = 'invalid'
		raise RuntimeError(
			'native ground support result is invalid or non-finite')
	if abs(float(support[1]) - float(position[1])) > GROUND_SUPPORT_TOLERANCE:
		state['ground_support_source'] = 'mismatch'
		raise RuntimeError(
			'native ground support height differs from the seed pose')
	state['ground_support_source'] = 'collision'
	return support


def _ground_support_diagnostic(state):
	"""Return bounded loading hints without treating them as support proof."""
	space_load = 'unavailable'
	chunk_loaded = 'unknown'
	try:
		space_load = '%.3f' % float(BigWorld.spaceLoadStatus())
	except Exception:
		pass
	try:
		position = _point_tuple(state.get('seed_position'))
		if position is not None:
			chunk = BigWorld.findChunkFromPoint(
				int(state.get('space_id', 0)), Math.Vector3(*position))
			chunk_loaded = '1' if chunk is not None else '0'
	except Exception:
		pass
	return space_load, chunk_loaded


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


def _diagnostic_pose_delta(first, second):
	if first is None or second is None:
		return 'invalid'
	try:
		dx = float(second[0]) - float(first[0])
		dz = float(second[2]) - float(first[2])
		distance = math.sqrt(dx * dx + dz * dz)
		yaw = abs(_normalise_angle(float(second[3]) - float(first[3])))
		return 'xz=%.3f yaw=%.4f' % (distance, yaw)
	except Exception:
		return 'invalid'


def _presentation_jump_reasons(previous, current, speed, sample_gap,
		turn_speed=0.0):
	"""Classify a visible-pose pulse without changing movement ownership."""
	reasons = []
	try:
		sample_gap = max(0.0, min(float(sample_gap), 0.5))
		speed = abs(float(speed))
		turn_speed = abs(float(turn_speed))
	except Exception:
		return reasons

	def _motion(first, second):
		if first is None or second is None:
			return None
		try:
			dx = float(second[0]) - float(first[0])
			dy = float(second[1]) - float(first[1])
			dz = float(second[2]) - float(first[2])
			dyaw = abs(_normalise_angle(float(second[3]) - float(first[3])))
			return (dx, dy, dz, math.sqrt(dx * dx + dz * dz), dyaw)
		except Exception:
			return None

	root_motion = _motion(previous.get('physics'), current.get('physics'))
	if root_motion is not None:
		root_limit = max(0.40, speed * sample_gap * 1.5 + 0.20)
		if (root_motion[3] > root_limit or abs(root_motion[1]) > 0.50):
			reasons.append('canonical_root')

	root_mock = _motion(current.get('physics'), current.get('mock'))
	if root_mock is not None and (root_mock[3] > 0.05 or
			abs(root_mock[1]) > 0.05 or root_mock[4] > 0.01):
		reasons.append('root_mock_split')

	mock_chassis = _motion(current.get('mock'), current.get('chassis'))
	chassis_limit = max(0.15, speed * sample_gap * 1.5 + 0.05)
	chassis_yaw_limit = max(
		0.03, turn_speed * sample_gap * 1.5 + 0.01)
	if mock_chassis is not None and (mock_chassis[3] > chassis_limit or
			abs(mock_chassis[1]) > max(0.20, chassis_limit) or
			mock_chassis[4] > chassis_yaw_limit):
		reasons.append('chassis_root_split')

	# Compare HP_gui in the chassis' complete local frame.  A yaw-only world
	# comparison misreports normal native pitch/roll at this 3.9 m-high node.
	try:
		old_local = previous.get('hp_local')
		new_local = current.get('hp_local')
		if old_local is not None and new_local is not None:
			dx = float(new_local[0]) - float(old_local[0])
			dy = float(new_local[1]) - float(old_local[1])
			dz = float(new_local[2]) - float(old_local[2])
			if (math.sqrt(dx * dx + dz * dz) > 0.15 or abs(dy) > 0.25):
				reasons.append('hp_gui_split')
	except Exception:
		pass

	placing_motion = _motion(previous.get('placing'), current.get('placing'))
	if placing_motion is not None and (placing_motion[3] > 0.10 or
			abs(placing_motion[1]) > 0.10 or placing_motion[4] > 0.03):
		reasons.append('placing_pulse')
	return reasons


def observe_presentation(mock, timestamp=None):
	"""Log a Ferdinand root/HP_gui pulse only when a measurable split occurs."""
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict) or state.get('phase') != 'active':
		return False
	if (int(state.get('presentation_log_count', 0) or 0) >=
			PRESENTATION_DIAGNOSTIC_LOG_LIMIT or
			int(_PRESENTATION_DIAGNOSTIC_LOGGED[0]) >=
			PRESENTATION_DIAGNOSTIC_GLOBAL_LOG_LIMIT):
		return False
	vehicle_name = str(state.get('vehicle_name', '') or '')
	if not any(name in vehicle_name for name in PRESENTATION_DIAGNOSTIC_VEHICLES):
		return False
	when = _now() if timestamp is None else float(timestamp)
	physics_pose = _physics_pose(state.get('physics'))
	mock_pose = _matrix_pose(getattr(mock, 'matrix', None))
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	chassis_provider = getattr(chassis, 'matrix', None)
	chassis_pose = _matrix_pose(chassis_provider)
	hp_pose = None
	hp_local = None
	try:
		hp_pose = _matrix_pose(chassis.node('HP_gui'))
		if hp_pose is not None:
			world_to_chassis = Math.Matrix(chassis_provider)
			world_to_chassis.invert()
			hp_local = _point_tuple(world_to_chassis.applyPoint(
				Math.Vector3(hp_pose[0], hp_pose[1], hp_pose[2])))
	except Exception:
		pass
	vehicle_filter = state.get('filter')
	sample = {
		'at': when,
		'physics': physics_pose,
		'mock': mock_pose,
		'chassis': chassis_pose,
		'hp': hp_pose,
		'hp_local': hp_local,
		'entity': _entity_pose(state),
		'body': _filter_pose(vehicle_filter),
		'placing': _matrix_pose(getattr(
			vehicle_filter, 'placingCompensationMatrix', None)),
	}
	previous = state.get('presentation_sample')
	state['presentation_sample'] = sample
	if not isinstance(previous, dict):
		return False
	reasons = _presentation_jump_reasons(
		previous, sample, state.get('frame_speed', 0.0),
		max(0.0, when - float(previous.get('at', when) or when)),
		state.get('frame_turn_speed', 0.0))
	if not reasons:
		return False
	last_log = float(state.get('presentation_log_at', -999.0) or -999.0)
	log_count = int(state.get('presentation_log_count', 0) or 0)
	if (when - last_log < PRESENTATION_DIAGNOSTIC_COOLDOWN or
			log_count >= PRESENTATION_DIAGNOSTIC_LOG_LIMIT):
		return False
	state['presentation_log_at'] = when
	state['presentation_log_count'] = log_count + 1
	_PRESENTATION_DIAGNOSTIC_LOGGED[0] += 1
	LOG_NOTE('NATIVE_BOT_PRESENTATION jump reasons=%s vehicle=%s name=%s '
		'id=%s server=%s team=%s slot=%s dt=%.3f speed=%.2f rspeed=%.3f '
		'physics=%s mock=%s chassis=%s hp=%s entity=%s body=%s placing=%s' % (
			','.join(reasons), vehicle_name,
			str(getattr(getattr(mock, 'publicInfo', None), 'name', '') or
				(getattr(mock, 'publicInfo', {}) or {}).get('name', '')),
			str(getattr(mock, 'id', '?')),
			str(getattr(mock, '_network_bot_id', '?')),
			str(getattr(mock, '_bot_team', '?')),
			str(getattr(mock, '_network_bot_slot', '?')),
			max(0.0, when - float(previous.get('at', when) or when)),
			float(state.get('frame_speed', 0.0) or 0.0),
			float(state.get('frame_turn_speed', 0.0) or 0.0),
			_pose_text(physics_pose), _pose_text(mock_pose),
			_pose_text(chassis_pose), _pose_text(hp_pose),
			_pose_text(sample.get('entity')), _pose_text(sample.get('body')),
			_pose_text(sample.get('placing'))))
	return True


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


def _model_motors(chassis):
	"""Return the exact root-motor list, or None when ownership is unreadable."""
	try:
		return list(chassis.motors)
	except Exception:
		return None


def _sole_model_motor(chassis, motor):
	motors = _model_motors(chassis)
	return (motors is not None and len(motors) == 1 and
		motors[0] is motor)


def _model_has_motor(chassis, motor):
	motors = _model_motors(chassis)
	if motors is None:
		return None
	return any(candidate is motor for candidate in motors)


def _arm_drive_diagnostic(mock, state, when, signals):
	if (not signals or _safe_int_attr(mock, 'id', -1) not in
			DRIVE_DIAGNOSTIC_IDS or
			state.get('drive_diagnostic_at') is not None):
		return
	state['drive_diagnostic_at'] = float(when)
	state['drive_diagnostic_root_start'] = state.get('frame_pose')
	state['drive_diagnostic_entity_start'] = _entity_pose(state)


def _maybe_log_drive_diagnostic(mock, state, when, signals_before,
		expected_signals, signals_repaired):
	armed_at = state.get('drive_diagnostic_at')
	if (armed_at is None or state.get('drive_diagnostic_logged') or
		float(when) - float(armed_at) < DRIVE_DIAGNOSTIC_DELAY):
		return
	physics = state.get('physics')
	vehicle_filter = state.get('filter')
	physics_pose = _physics_pose(physics)
	entity_pose = _entity_pose(state)
	state['drive_diagnostic_logged'] = True
	LOG_NOTE('NATIVE_BOT_PHYSICS drive_diagnostic id=%s signals_before=%s '
		'signals=%s repaired=%s engine_power=%s '
		'normal_engine_power=%s engine_power_mode=%s frozen=%s '
		'frozen_during_frame=%s static_mode=%s tracks_contact=%s allow_tracks=%s '
		'carcass_contact=%s allow_carcass=%s ground_type=%s '
		'seed_y=%s entity_y=%s body_y=%s placing_y=%s root_motors=%s '
		'physics_root=%s entity_pose=%s root_delta=%s entity_delta=%s '
		'root_entity_gap=%s sample_seconds=%.3f '
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
			_pose_text(physics_pose),
			_pose_text(entity_pose),
			_diagnostic_pose_delta(
				state.get('drive_diagnostic_root_start'), physics_pose),
			_diagnostic_pose_delta(
				state.get('drive_diagnostic_entity_start'), entity_pose),
			_diagnostic_pose_delta(physics_pose, entity_pose),
			float(when) - float(armed_at),
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


def _callback_integer(value, name):
	if isinstance(value, bool) or not isinstance(value, _INTEGER_TYPES):
		raise ValueError('%s must be an integer' % name)
	return int(value)


def _copy_destructible_point(value):
	point = _point_tuple(value)
	if point is None or not all(_finite(component) for component in point):
		raise ValueError('destructible hit point is invalid')
	return point


def _remember_destructible_error(state, error):
	if state.get('destructible_callback_error') is None:
		state['destructible_callback_error'] = str(error)


def _make_destructible_callbacks(state):
	"""Build the exact two- and six-argument callbacks used by 0.8.2."""
	def _health(chunkID, itemIndex):
		try:
			from gui.mods.offhangar import destructibles_authority
			return destructibles_authority.collision_health(
				state['space_id'], chunkID, itemIndex)
		except Exception as error:
			# Native health lookup is synchronous. None keeps the contact solid; the
			# owning body is faulted only after the shared batch and all filter outputs.
			_remember_destructible_error(state, error)
			return None

	def _damage(chunkID, itemIndex, matKind, damage, hitPoint,
			normalImpactSpeed):
		try:
			if not state.get('destructible_generation_open'):
				raise RuntimeError(
					'destructible damage arrived outside a native batch')
			events = state.get('destructible_events')
			if not isinstance(events, list):
				raise RuntimeError(
					'destructible event generation is unavailable')
			damage_value = _callback_integer(damage, 'damage')
			if damage_value < 0:
				raise ValueError('damage must be positive')
			if damage_value == 0:
				# The 0.8.2 engine can quantise a low-energy contact to a zero
				# damage notification. It has no ledger effect and is not a broken
				# native callback contract.
				return
			if len(events) >= DESTRUCTIBLE_BATCH_EVENT_LIMIT:
				raise RuntimeError(
					'destructible batch event limit exceeded')
			impact_speed = float(normalImpactSpeed)
			if not _finite(impact_speed):
				raise ValueError('normalImpactSpeed must be finite')
			# Copy every engine-owned argument now. The queue contains only plain
			# Python ints, floats and tuples after the native callback returns.
			events.append((
				_callback_integer(chunkID, 'chunkID'),
				_callback_integer(itemIndex, 'itemIndex'),
				_callback_integer(matKind, 'matKind'),
				damage_value,
				_copy_destructible_point(hitPoint),
				impact_speed,
			))
		except Exception as error:
			_remember_destructible_error(state, error)

	return (_health, _damage)


def _clear_callbacks(physics):
	"""Clear callbacks; the retail write-only setters are the only receipt."""
	if physics is None:
		return True
	cleared = True
	for name in ('damageDestructibleCb', 'destructibleHealthRequestCb',
			'onRammingCb', 'onBecameFrozenCb', 'onStaticDamageCb'):
		try:
			setattr(physics, name, None)
		except Exception:
			cleared = False
	return cleared


def _release_callbacks(state):
	cleared = _clear_callbacks(state.get('physics'))
	state['destructible_generation_open'] = False
	state['destructible_events'] = []
	state['destructible_pending'] = []
	state['destructible_callback_error'] = None
	if cleared:
		state['destructible_health_callback'] = None
		state['destructible_damage_callback'] = None
	return cleared


def _install_destructible_callbacks(state, physics):
	health_callback, damage_callback = _make_destructible_callbacks(state)
	state['destructible_health_callback'] = health_callback
	state['destructible_damage_callback'] = damage_callback
	physics.destructibleHealthRequestCb = health_callback
	physics.damageDestructibleCb = damage_callback
	physics.onRammingCb = None
	# These five retail extension attributes are deliberately write-only. A
	# successful setter is the complete observable contract; reading them raises.


def _begin_destructible_generation(state):
	state['destructible_generation'] = int(
		state.get('destructible_generation', 0) or 0) + 1
	state['destructible_events'] = []
	state['destructible_generation_open'] = True


def _close_destructible_generation(state, discard=False):
	state['destructible_generation_open'] = False
	if discard:
		state['destructible_events'] = []


def _merge_destructible_event(events, event, limit):
	key = event[:3]
	for index in range(len(events)):
		previous = events[index]
		if previous[:3] == key:
			events[index] = (
				previous[0], previous[1], previous[2],
				previous[3] + event[3], event[4], event[5])
			return
	if len(events) >= limit:
		raise RuntimeError('destructible warmup event limit exceeded')
	events.append(event)


def _destructible_fall_yaw(state):
	pose = state.get('frame_pose') or state.get('last_pose')
	if pose is None or len(pose) < 4:
		raise RuntimeError('destructible damage pose is unavailable')
	yaw = float(pose[3])
	if float(state.get('frame_speed', 0.0) or 0.0) < 0.0:
		yaw += math.pi
	return _normalise_angle(yaw)


def _drain_destructible_generation(mock, state):
	"""Apply one completed generation after every native filter was published."""
	events = list(state.get('destructible_events') or ())
	state['destructible_events'] = []
	error = state.get('destructible_callback_error')
	state['destructible_callback_error'] = None
	if state.get('phase') == 'faulted':
		# A callback setter can fail while teardown is proving ownership release.
		# The body remains static in the shared batch so other native vehicles still
		# collide with it, but it is already fail-closed. Discard any late callback
		# record instead of counting and logging the same fault every rendered frame.
		return True
	if error is not None:
		_fail(mock, state, 'native destructible callback failed: %s' % error)
		return False
	phase = state.get('phase')
	if phase == 'warmup':
		try:
			pending = state.get('destructible_pending')
			if not isinstance(pending, list):
				raise RuntimeError('destructible warmup queue is unavailable')
			for event in events:
				_merge_destructible_event(
					pending, event, DESTRUCTIBLE_WARMUP_EVENT_LIMIT)
			return True
		except Exception as merge_error:
			_fail(mock, state,
				'native destructible queue failed: %s' % str(merge_error))
			return False
	if phase != 'active':
		return True
	pending = list(state.get('destructible_pending') or ())
	state['destructible_pending'] = []
	if not pending and not events:
		return True
	try:
		from gui.mods.offhangar import destructibles_authority
		fall_yaw = _destructible_fall_yaw(state)
		for event in pending + events:
			destructibles_authority.apply_collision_damage(
				state['space_id'], event[0], event[1], event[2], event[3],
				event[4], fall_yaw, event[5])
		return True
	except Exception as apply_error:
		_fail(mock, state,
			'native destructible damage failed: %s' % str(apply_error))
		return False


def _restore_avatar_filter(mock):
	"""Release the native entity owner and prove the relay filter took over."""
	entity = getattr(mock, 'bw_entity', None)
	if entity is None:
		return True
	original_filter = getattr(entity, 'filter', None)
	original_physics = getattr(entity, 'wgPhysics', None)
	try:
		entity.wgPhysics = None
	except Exception:
		return False
	try:
		if getattr(entity, 'wgPhysics', original_physics) is not None:
			return False
	except Exception:
		return False
	relay_filter = None
	try:
		relay_filter = BigWorld.AvatarFilter()
		entity.filter = relay_filter
		if getattr(entity, 'filter', None) is not relay_filter:
			raise RuntimeError('AvatarFilter attach readback mismatch')
		mock.filter = relay_filter
		if getattr(mock, 'filter', None) is not relay_filter:
			raise RuntimeError('mock AvatarFilter readback mismatch')
	except Exception:
		# The physics pointer was already detached. Restore the original pair so the
		# caller keeps one complete retryable graph instead of forgetting half of it.
		try:
			entity.filter = original_filter
			mock.filter = original_filter
		except Exception:
			pass
		try:
			entity.wgPhysics = original_physics
		except Exception:
			pass
		return False
	try:
		entity.isStarted = False
		entity.typeDescriptor = None
	except Exception:
		pass
	# A native startup failure is a static, visible failure state. Do not install
	# the legacy PyModelObstacle: doing so would silently restore Python collision
	# ownership beside the failed native contract.
	return True


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
	callbacks_released = _release_callbacks(state)
	state['reason'] = str(reason)
	if not callbacks_released:
		# A live native callback still owns this Python state. Keep the complete
		# filter/physics/provider graph so stop_mock() can retry the release; never
		# detach the entity or model underneath an uncleared callback.
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
		elif not was_active:
			_COUNTERS['startup_failed'] += 1
		state['frame_pose'] = None
		state['frame_pose_at'] = None
		state['frame_output_generation'] = -1
		_COUNTERS['failed'] += 1
		LOG_ERROR('NATIVE_BOT_PHYSICS FAIL id=%s phase=%s reason=%s' % (
			getattr(mock, 'id', '?'), state.get('phase'), str(reason)))
		_maybe_log_startup_complete()
		return False
	if was_active:
		# Never hot-swap a live native body back to Python. Freeze at the last
		# validated pose, keep one motion owner, and release it during the normal
		# battle sweep. A later battle builds a fresh native owner.
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
		# A fashion which could not be rolled back or detached still owns native
		# filter providers from the chassis. Keep the entire frozen native graph
		# intact so stop_mock() can retry that detach transaction; restoring only the
		# Python Servo here would create a mixed presentation owner.
		fashion_release_blocked = bool(state.get('fashion_detach_blocked'))
		provider_restored = not fashion_release_blocked
		if (not fashion_release_blocked and
				state.get('native_servo') is not None):
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
			if not fashion_release_blocked:
				state['pending_fashion'] = None
			state['phase'] = 'faulted'
		elif not _restore_avatar_filter(mock):
			LOG_ERROR('NATIVE_BOT_PHYSICS entity owner release failed id=%s' % (
				getattr(mock, 'id', '?')))
			# A native setter may report success without changing the bound object.
			# Retain every state reference so stop_mock() can retry the release; never
			# label this body failed while the entity still owns native physics.
			state['phase'] = 'faulted'
		else:
			state['physics'] = None
			state['filter'] = None
			state['entity_provider'] = None
			state['pending_fashion'] = None
			state['phase'] = 'failed'
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


def _failed_result(mock, state=None):
	"""Return a visible, immobile pose for a failed native-only bot."""
	pose = state.get('last_pose') if isinstance(state, dict) else None
	if pose is None or len(pose) < 6:
		position = _point_tuple(getattr(mock, 'position', None))
		if position is None:
			return None
		pose = position + (
			float(getattr(mock, 'yaw', 0.0) or 0.0),
			float(getattr(mock, 'pitch', 0.0) or 0.0),
			float(getattr(mock, 'roll', 0.0) or 0.0))
	return {
		'position': pose[:3],
		'yaw': pose[3],
		'pitch': pose[4],
		'roll': pose[5],
		'velocity': 0.0,
		'turn_velocity': 0.0,
		'failed': True,
	}


def _pending_result(mock):
	"""Hold one required bot while its asynchronous entity is not bound yet."""
	result = _failed_result(mock, None)
	if result is None:
		return None
	try:
		del result['failed']
	except Exception:
		pass
	result['staging'] = True
	return result


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


def requires_native(player, mock):
	"""Return whether this authority bot must never use Python kinematics."""
	if bool(getattr(mock, REQUIRED_ATTR, False)):
		return True
	required = enabled_for(player) and _eligible_mock(mock)
	if required:
		setattr(mock, REQUIRED_ATTR, True)
	return required


def claims_movement(mock):
	"""Reserve one bot's movement slot even after fail-closed startup."""
	if bool(getattr(mock, REQUIRED_ATTR, False)):
		return True
	state = getattr(mock, STATE_ATTR, None)
	return (isinstance(state, dict) and state.get('phase') in (
		'preparing', 'seed_wait', 'warmup', 'active', 'faulted', 'failed'))


def fail_closed_result(mock):
	"""Expose a native startup failure without entering Python movement."""
	return _failed_result(mock, getattr(mock, STATE_ATTR, None))


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
	"""Advance one native batch, publish each filter, then capture its root.

	The 0.8.2 engine does not schedule client-created WGVehiclePhysics2 bodies.
	WGDynamicsSimulator.update owns frame reset, terrain/track/carcass contacts,
	bot-to-bot pairs, force solving and integration. Calling it per vehicle would
	reset the batch repeatedly and lose native pair contacts. Retail Vehicle
	entities schedule WGVehicleFilter2::output automatically; OfflineEntity does
	not. Retail Filter::output also does not copy the attached rigid root. The
	pinned bridge submits that root to the filter history, while Python consumes
	the same solve's WGVehiclePhysics2.matrix and native speeds as canonical state.
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
				mock, state, int(state.get('simulated_frames', 0) or 0)))
			continue
		solve_entries.append((
			mock, state, int(state.get('simulated_frames', 0) or 0)))
	if not solve_entries:
		return 0
	generation_states = []
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
			for mock, state, output_generation in solve_entries)
		for mock, state, output_generation in solve_entries:
			if state.get('phase') in ('warmup', 'active'):
				_begin_destructible_generation(state)
				generation_states.append(state)
		_DYNAMICS_SIMULATOR[0].update(solver_dt, physics, ())
	except Exception as error:
		for state in generation_states:
			_close_destructible_generation(state, True)
		_SIMULATION_FAILED[0] = True
		_DYNAMICS_SIMULATOR[0] = None
		for mock, state, output_generation in solve_entries:
			if state.get('phase') in ('warmup', 'active'):
				_fail(mock, state, RuntimeError(
					'native batch simulation failed: %s' % str(error)), True)
		return 0
	for state in generation_states:
		_close_destructible_generation(state, False)
	_LAST_SIMULATION_AT[0] = when
	valid_count = 0
	for mock, state, output_generation in solve_entries:
		if state.get('phase') not in ('warmup', 'active', 'faulted'):
			continue
		if state.get('phase') == 'faulted':
			state['simulated_frames'] = output_generation + 1
			state['frame_pose'] = state.get('last_pose')
			state['frame_pose_at'] = when
			state['frame_output_generation'] = output_generation + 1
			state['frame_speed'] = 0.0
			state['frame_turn_speed'] = 0.0
			valid_count += 1
			continue
		try:
			from gui.mods.offhangar import native_filter_bridge
			if not native_filter_bridge.publish_physics_root(
					state['filter'], state['physics'], when,
					int(state.get('space_id', 0) or 0)):
				raise RuntimeError(
					'native physics root publish was rejected')
			presentation_pose = _entity_pose(state)
			if presentation_pose is None:
				raise RuntimeError(
					'native Filter::output returned an invalid pose')
			pose = _physics_pose(state['physics'])
			if pose is None:
				raise RuntimeError(
					'native physics root matrix is unavailable')
			if state.get('phase') == 'warmup':
				warmup_reason = _warmup_pose_reason(mock, state, pose)
				if warmup_reason is not None:
					raise RuntimeError(warmup_reason)
			state['simulated_frames'] = output_generation + 1
			state['frame_pose'] = pose
			state['frame_pose_at'] = when
			state['frame_output_generation'] = output_generation + 1
			state['frame_speed'] = _physics_speed(
				state['physics'], 'speed')
			state['frame_turn_speed'] = _physics_speed(
				state['physics'], 'rspeed')
			valid_count += 1
		except Exception as error:
			# If output succeeded before a later getter failed, the entity provider
			# already contains the new real pose. Let _fail retain that exact pose;
			# if output itself failed, the provider still contains the prior one.
			_fail(mock, state, error)
	# Native callbacks only collect plain data while the shared solver is live.
	# Apply after every output/capture, preserving the batch's stable body order.
	for mock, state, output_generation in solve_entries:
		had_valid_output = state.get('frame_pose_at') == when
		if not _drain_destructible_generation(mock, state):
			if had_valid_output:
				valid_count = max(0, valid_count - 1)
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
	if not _clear_callbacks(physics):
		raise RuntimeError('native callback initialization was rejected')
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
	from gui.mods.offhangar import native_filter_bridge
	if not native_filter_bridge.filter_has_physics(
			state['filter'], physics):
		raise RuntimeError('WGVehicleFilter2 physics owner readback mismatch')
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
	_install_destructible_callbacks(state, physics)


def _activate_model_provider(mock, state):
	"""Make the canonical physics-root matrix the sole model provider."""
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	entity = getattr(mock, 'bw_entity', None)
	if chassis is None or entity is None:
		return False
	# WGVehicleFilter2 keeps a presentation history on entity.matrix.  Gameplay,
	# collision and LAN publication use the solved WGVehiclePhysics2 root which
	# offline_battle commits to this persistent matrix every frame.  Binding the
	# chassis to the delayed entity provider made distant models visibly catch up
	# by a vehicle length when their Filter output resumed.
	provider = getattr(mock, 'matrix', None)
	if provider is None:
		return False
	if state.get('native_servo') is not None:
		return _sole_model_motor(chassis, state.get('native_servo'))
	old_servo = getattr(mock, '_pose_servo', None)
	servo = None
	old_detached = False
	try:
		# Create the replacement object first, but never attach two root motors.
		# Detach the proven Python Servo, attach the native one, and restore the
		# old owner if the second operation fails.
		servo = BigWorld.Servo(provider)
		motors = _model_motors(chassis)
		if motors is None:
			raise RuntimeError('model motor readback unavailable')
		if old_servo is not None:
			if not _sole_model_motor(chassis, old_servo):
				raise RuntimeError('Python Servo is not the sole model owner')
			chassis.delMotor(old_servo)
			if _model_motors(chassis) != []:
				raise RuntimeError('Python Servo detach readback mismatch')
			old_detached = True
		elif motors:
			raise RuntimeError('model already has an unknown root owner')
		chassis.addMotor(servo)
		if not _sole_model_motor(chassis, servo):
			raise RuntimeError('native Servo attach readback mismatch')
	except Exception:
		try:
			if _model_has_motor(chassis, servo):
				chassis.delMotor(servo)
		except Exception:
			pass
		if old_detached:
			try:
				chassis.addMotor(old_servo)
				if not _sole_model_motor(chassis, old_servo):
					raise RuntimeError('Python Servo restore readback mismatch')
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
		pose_servo = getattr(mock, '_pose_servo', None)
		return (pose_servo is not None and
			bool(getattr(mock, '_servo_added', False)) and
			_sole_model_motor(chassis, pose_servo))
	matrix = getattr(mock, 'matrix', None)
	if matrix is None:
		return False
	fallback_servo = None
	try:
		# Detach first so a model that refuses all delMotor calls can never retain
		# both the native and Python root owners. If this fails, the proven native
		# Servo remains the only attached motor and the caller freezes it.
		if not _sole_model_motor(chassis, native_servo):
			return False
		chassis.delMotor(native_servo)
		if _model_motors(chassis) != []:
			return False
	except Exception:
		return False
	try:
		fallback_servo = BigWorld.Servo(matrix)
		chassis.addMotor(fallback_servo)
		if not _sole_model_motor(chassis, fallback_servo):
			raise RuntimeError('Python Servo attach readback mismatch')
	except Exception:
		try:
			if _model_has_motor(chassis, fallback_servo):
				chassis.delMotor(fallback_servo)
		except Exception:
			pass
		# Restore the native owner if the Python replacement cannot be attached.
		# If even recovery fails, record that no Servo is attached; the caller still
		# fails closed and never starts Python motion beside an unknown owner.
		try:
			chassis.addMotor(native_servo)
			if not _sole_model_motor(chassis, native_servo):
				raise RuntimeError('native Servo restore readback mismatch')
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
	if not requires_native(player, mock):
		return False
	# A relay proxy can be promoted after it already installed the legacy static
	# obstacle. Native contact must never collide with that duplicate own hull.
	try:
		mock._collision_obstacle = None
	except Exception:
		pass
	if descriptor is None or _SIMULATION_FAILED[0]:
		return False
	# OfflineEntity creation is asynchronous. The mock is registered before its
	# engine entity becomes visible to BigWorld.entity(), so a normal frame may
	# reach prepare() while bw_entity is still None. That is a pending condition,
	# not a terminal native failure; do not create or count a state until the
	# entity-ready callback (or a later frame) can retry the same native contract.
	entity = getattr(mock, 'bw_entity', None)
	if entity is None:
		return False
	if getattr(mock, '_offh_native_model_root_ready', False) is not True:
		# The entity/model assignment may still be retrying removal of BigWorld's
		# default motor. Attaching a native Servo before that transaction completes
		# would give the chassis two competing root providers.
		return False
	old_state = getattr(mock, STATE_ATTR, None)
	if isinstance(old_state, dict):
		if old_state.get('phase') == 'faulted':
			# A fault is fail-closed for this battle. Demotion/cleanup owns the only
			# valid release transaction; never discard its retry references here.
			return False
		if old_state.get('phase') == 'stopped':
			# A failed Servo detach deliberately keeps the only retry reference in the
			# stopped/faulted state. Do not discard it or attach a second root owner.
			if (old_state.get('native_servo') is not None or
					old_state.get('filter') is not None or
					old_state.get('physics') is not None):
				stop_mock(mock, False)
				if (old_state.get('native_servo') is not None or
						old_state.get('filter') is not None or
						old_state.get('physics') is not None):
					if not old_state.get('reuse_blocked_logged'):
						old_state['reuse_blocked_logged'] = True
						LOG_ERROR('NATIVE_BOT_PHYSICS reuse blocked id=%s '
							'reason=native owner is still attached' % (
								getattr(mock, 'id', '?')))
					return False
			# A delayed callback or promoted relay must build a new filter/physics
			# pair once the previous state owns no native objects.
			try:
				delattr(mock, STATE_ATTR)
			except Exception:
				setattr(mock, STATE_ATTR, None)
		else:
			return old_state.get('phase') != 'failed'
	_clear_hazard_recovery(mock)
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
		'destructible_generation': 0,
		'destructible_generation_open': False,
		'destructible_events': [],
		'destructible_pending': [],
		'destructible_callback_error': None,
		'destructible_health_callback': None,
		'destructible_damage_callback': None,
		# A relay can already own a normal WGVehicleFashion when it is promoted.
		# Keep it staged until the native Servo/filter handoff becomes active, then
		# bind the same retail placement providers used by an initially local bot.
		'pending_fashion': getattr(mock, '_fashion', None),
		'fashion_detach_blocked': False,
		'native_servo': None,
		'counted_active': False,
		'max_speed': max(1.0, max_speed),
		'activate_at': 0.0,
		'drive_diagnostic_at': None,
		'drive_diagnostic_logged': False,
		'drive_diagnostic_root_start': None,
		'drive_diagnostic_entity_start': None,
		'presentation_sample': None,
		'presentation_log_at': -999.0,
		'presentation_log_count': 0,
		'seed_check_at': 0.0,
		'seed_position': None,
		'seed_yaw': 0.0,
		'ground_support': None,
		'ground_support_source': None,
		'ground_support_retry_at': 0.0,
		'ground_support_wait_started_at': None,
		'ground_support_log_at': 0.0,
		'ground_support_misses': 0,
		'space_id': int(space_id),
		'vehicle_name': str(_descriptor_value(descriptor, 'name', '?') or '?'),
	}
	setattr(mock, STATE_ATTR, state)
	try:
		position = _point_tuple(getattr(mock, 'position', None))
		if position is None:
			raise RuntimeError('mock pose is unavailable')
		yaw = float(getattr(mock, 'yaw', 0.0) or 0.0)
		when = _now() if timestamp is None else float(timestamp)
		state['seed_position'] = position
		state['seed_yaw'] = yaw
		state['last_pose'] = position + (yaw,
			float(getattr(mock, 'pitch', 0.0) or 0.0),
			float(getattr(mock, 'roll', 0.0) or 0.0))
		state['last_pose_at'] = when

		vehicle_filter = BigWorld.WGVehicleFilter2()
		entity.filter = vehicle_filter
		if getattr(entity, 'filter', None) is not vehicle_filter:
			raise RuntimeError('WGVehicleFilter2 attach readback mismatch')
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
				(yaw, 0.0, 0.0)):
			raise RuntimeError('Filter::input seed was rejected')
		entity.typeDescriptor = descriptor
		# See _attach_physics: this flag belongs to the stock Vehicle appearance
		# lifecycle, not WGVehicleFilter2/WGVehiclePhysics2 ownership.
		entity.isStarted = False
		entity.isPlayer = False
		mock.filter = vehicle_filter
		state['phase'] = 'seed_wait'
		state['seed_check_at'] = when + SEED_CHECK_SECONDS
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


def _steering_sign(value):
	"""Quantise continuous steering without reacting to sub-three-degree noise."""
	value = float(value or 0.0)
	if value > STEERING_INPUT_THRESHOLD:
		return 1
	if value < -STEERING_INPUT_THRESHOLD:
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
	are validated. In this native-only build a failed setup returns an immobile
	fail-closed result; it never authorizes legacy Python kinematics. An active
	result contains position, yaw, pitch, roll, longitudinal and angular speed.
	"""
	if not requires_native(player, mock):
		return None
	state = getattr(mock, STATE_ATTR, None)
	if (not isinstance(state, dict) or
			state.get('phase') == 'stopped'):
		if not prepare(player, mock, descriptor, space_id, timestamp):
			if (not isinstance(getattr(mock, STATE_ATTR, None), dict) and
					getattr(mock, 'bw_entity', None) is None):
				return _pending_result(mock)
			return _failed_result(
				mock, getattr(mock, STATE_ATTR, None))
		state = getattr(mock, STATE_ATTR, None)
	if state.get('phase') == 'failed':
		return _failed_result(mock, state)
	if state.get('phase') == 'faulted':
		return _frozen_result(state)
	if _SIMULATION_FAILED[0]:
		_fail(mock, state, 'native batch simulation is unavailable', True)
		if state.get('phase') == 'faulted':
			return _frozen_result(state)
		return _failed_result(mock, state)
	when = _now() if timestamp is None else float(timestamp)
	newly_activated = False
	try:
		if state.get('phase') == 'seed_wait':
			if (when < float(state.get('seed_check_at', 0.0)) or
					when < float(state.get('ground_support_retry_at', 0.0))):
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
				if state.get('ground_support_wait_started_at') is None:
					state['ground_support_wait_started_at'] = when
				state['ground_support_misses'] = int(
					state.get('ground_support_misses', 0) or 0) + 1
				state['ground_support_retry_at'] = (
					when + GROUND_SUPPORT_RETRY_SECONDS)
				if when >= float(state.get('ground_support_log_at', 0.0)):
					space_load, chunk_loaded = _ground_support_diagnostic(state)
					LOG_NOTE('NATIVE_BOT_PHYSICS ground_wait id=%s elapsed=%.2f '
						'misses=%d source=%s space_load=%s chunk_loaded=%s' % (
							getattr(mock, 'id', '?'),
							when - float(state['ground_support_wait_started_at']),
							state['ground_support_misses'],
							state.get('ground_support_source', 'unavailable'),
							space_load, chunk_loaded))
					state['ground_support_log_at'] = (
						when + GROUND_SUPPORT_LOG_SECONDS)
				return _staged_result(state)
			state['ground_support'] = support
			state['ground_support_retry_at'] = 0.0
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
				# Activation requires one explicit post-solve Filter::output sample.
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
					state.get('physics') or getattr(entity, 'filter', None) is not
					state.get('filter') or getattr(mock, 'filter', None) is not
					state.get('filter') or bool(getattr(entity, 'isStarted', False))):
				raise RuntimeError(
					'native wiring invariant failed: owner references changed')
			# Continue validating the hidden body for the entire countdown. Only the
			# first active battle frame may transfer the visible model owner.
			if not active:
				return _staged_result(state)
			if not _activate_model_provider(mock, state):
				raise RuntimeError('native entity matrix could not own the model')
			fashion = state.get('pending_fashion')
			if fashion is not None:
				if not _bind_fashion_providers(mock, state, fashion):
					raise RuntimeError('native fashion provider bind failed')
				state['pending_fashion'] = None
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
			return _failed_result(mock, state)

		movement = _input_sign(throttle) if active else 0
		rotation = _steering_sign(turn) if active else 0
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
		return _failed_result(mock, state)


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
	names = (
		'placingCompensationMatrix', 'physicsInfo', 'movementInfo')
	missing = object()
	original = {}
	for name in names:
		try:
			original[name] = getattr(fashion, name)
		except Exception:
			original[name] = missing
	try:
		expected = {
			'placingCompensationMatrix':
				state['filter'].placingCompensationMatrix,
			'physicsInfo': state['filter'].physicsInfo,
			'movementInfo': state['filter'].movementInfo,
		}
		for name in names:
			setattr(fashion, name, expected[name])
		for name in names:
			if getattr(fashion, name, missing) is not expected[name]:
				raise RuntimeError(
					'fashion provider readback mismatch: %s' % name)
		state['fashion_detach_blocked'] = False
		return True
	except Exception as error:
		rollback_ok = True
		for name in names:
			try:
				value = original[name]
				if value is missing:
					delattr(fashion, name)
				else:
					setattr(fashion, name, value)
				actual = getattr(fashion, name, missing)
				if value is missing:
					if actual is not missing:
						raise RuntimeError(
							'fashion provider removal readback mismatch')
				elif actual is not value:
					raise RuntimeError(
						'fashion provider rollback readback mismatch')
			except Exception:
				rollback_ok = False
		if (not rollback_ok and
				getattr(mock, '_fashion', None) is fashion):
			# A partially rebound fashion must not retain a mixed set of old and
			# native providers. Remove it from the chassis if rollback is impossible.
			chassis = (getattr(mock, '_chassis_model', None) or
				getattr(mock, 'model', None))
			detached = chassis is None
			if chassis is not None:
				try:
					delattr(chassis, 'wg_fashion')
					detached = getattr(
						chassis, 'wg_fashion', missing) is not fashion
				except Exception:
					detached = False
			state['fashion_detach_blocked'] = not detached
			if detached:
				try:
					mock._fashion = None
				except Exception:
					pass
		else:
			state['fashion_detach_blocked'] = False
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
		# providers here would keep a failed filter/physics pair alive after failure.
		state['pending_fashion'] = fashion
		return True
	if _bind_fashion_providers(mock, state, fashion):
		return True
	_fail(mock, state, 'native fashion provider bind failed', True)
	return False


def _clear_hazard_recovery(mock):
	"""Discard tactical recovery state only after native ownership is clean."""
	for name in (
			'_offh_native_hazard_recovering',
			'_offh_native_hazard_anchor',
			'_offh_native_hazard_entry_yaw',
			'_offh_native_hazard_escape_endpoint',
			'_offh_native_hazard_safe_since',
			'_offh_native_last_safe_pose'):
		try:
			delattr(mock, name)
		except Exception:
			pass


def stop_mock(mock, restore_filter=False):
	state = getattr(mock, STATE_ATTR, None)
	if not isinstance(state, dict):
		try:
			setattr(mock, REQUIRED_ATTR, False)
		except Exception:
			pass
		_clear_hazard_recovery(mock)
		# No native objects exist, so demotion already satisfies the ownership
		# barrier and the relay presentation may proceed.
		return True
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
	if not _release_callbacks(state):
		# Do not detach the filter, physics, Servo or fashion while native code still
		# retains a Python callback. Keep the complete owner graph for the next retry.
		if state.get('phase') == 'active' and state.get('counted_active'):
			_COUNTERS['active'] = max(0, _COUNTERS['active'] - 1)
			state['counted_active'] = False
		state['phase'] = 'faulted'
		state['reason'] = 'native callback release failed'
		LOG_ERROR('NATIVE_BOT_PHYSICS callback release failed id=%s' % (
			getattr(mock, 'id', '?')))
		return False
	native_servo = state.get('native_servo')
	chassis = (getattr(mock, '_chassis_model', None) or
		getattr(mock, 'model', None))
	# WGVehicleFashion retains the native filter's placing, physics and movement
	# providers.  Release it before changing either model Servo or entity filter;
	# otherwise a failed delattr would leave an unretryable mixed-owner chassis.
	fashion = getattr(mock, '_fashion', None)
	if fashion is not None:
		fashion_detached = chassis is None
		if chassis is not None:
			try:
				delattr(chassis, 'wg_fashion')
				fashion_detached = not hasattr(chassis, 'wg_fashion')
			except Exception as error:
				fashion_detached = False
				LOG_ERROR(
					'NATIVE_BOT_PHYSICS fashion detach failed id=%s error=%s' % (
						getattr(mock, 'id', '?'), str(error)))
		if not fashion_detached:
			# Keep the complete native owner graph for the next demotion retry.  The
			# caller must not apply a replica pose while this provider is attached.
			if state.get('phase') == 'active' and state.get('counted_active'):
				_COUNTERS['active'] = max(0, _COUNTERS['active'] - 1)
				state['counted_active'] = False
			state['phase'] = 'faulted'
			state['fashion_detach_blocked'] = True
			return False
		state['fashion_detach_blocked'] = False
		try:
			mock._fashion = None
		except Exception:
			pass
	old_pose_servo = getattr(mock, '_pose_servo', None)
	relay_servo_attached = (old_pose_servo is not None and
		bool(getattr(mock, '_servo_added', False)))
	servo_detached = native_servo is None
	if native_servo is not None:
		if chassis is not None:
			try:
				if not _sole_model_motor(chassis, native_servo):
					raise RuntimeError('native Servo is not the sole model owner')
				chassis.delMotor(native_servo)
				servo_detached = _model_motors(chassis) == []
				if not servo_detached:
					raise RuntimeError('native Servo detach readback mismatch')
			except Exception as error:
				LOG_ERROR('NATIVE_BOT_PHYSICS servo detach failed id=%s error=%s' % (
					getattr(mock, 'id', '?'), str(error)))
	if not servo_detached:
		# The relay snapshot must not acquire the model while the native Servo is
		# still attached. Keep this body frozen and retain every native reference so
		# the next demotion message can retry the ownership release.
		try:
			if physics is not None:
				physics.staticMode = True
		except Exception:
			pass
		if state.get('phase') == 'active' and state.get('counted_active'):
			_COUNTERS['active'] = max(0, _COUNTERS['active'] - 1)
			state['counted_active'] = False
		state['phase'] = 'faulted'
		return False
	if servo_detached:
		state['native_servo'] = None
		try:
			mock._native_pose_servo = None
			# During seed/warmup the original Python Servo is still the one proven
			# attached root owner. Only clear the shared flag after an actual native
			# Servo detach; otherwise demotion retries would add a duplicate Servo.
			if native_servo is not None:
				mock._servo_added = False
		except Exception:
			pass
		# A replica snapshot drives mock.matrix. Restore exactly one Python Servo
		# before dropping the native provider; otherwise pose commits update the
		# marker but the chassis remains stranded at the demotion pose.
		if (restore_filter and chassis is not None and
				getattr(mock, 'matrix', None) is not None):
			relay_servo = old_pose_servo if relay_servo_attached else None
			try:
				if relay_servo is None:
					if _model_motors(chassis) != []:
						raise RuntimeError('model still has a root owner')
					relay_servo = BigWorld.Servo(mock.matrix)
					chassis.addMotor(relay_servo)
					if not _sole_model_motor(chassis, relay_servo):
						raise RuntimeError('relay Servo attach readback mismatch')
					mock._pose_servo = relay_servo
					mock._servo_added = True
				elif not _sole_model_motor(chassis, relay_servo):
					raise RuntimeError('relay Servo ownership readback mismatch')
			except Exception:
				try:
					if relay_servo is not None and relay_servo is not old_pose_servo and \
							_model_has_motor(chassis, relay_servo):
						chassis.delMotor(relay_servo)
				except Exception:
					pass
				# Reattach the native owner if the relay presentation cannot acquire the
				# model. This keeps the demotion barrier retryable and single-owner.
				if native_servo is not None:
					try:
						chassis.addMotor(native_servo)
						if not _sole_model_motor(chassis, native_servo):
							raise RuntimeError('native Servo restore readback mismatch')
						state['native_servo'] = native_servo
						mock._native_pose_servo = native_servo
						mock._pose_servo = old_pose_servo
						mock._servo_added = True
					except Exception:
						pass
				state['phase'] = 'faulted'
				return False
	filter_released = _restore_avatar_filter(mock)
	if not filter_released:
		# The native Servo is already detached, but the entity still has an unknown
		# filter/physics owner. Do not apply a replica pose until readback proves the
		# old native owner is gone; a later demotion message safely retries here.
		state['phase'] = 'faulted'
		return False
	state['filter'] = None
	state['physics'] = None
	state['entity_provider'] = None
	state['pending_fashion'] = None
	state['frame_pose'] = None
	state['frame_pose_at'] = None
	state['frame_output_generation'] = -1
	state['destructible_health_callback'] = None
	state['destructible_damage_callback'] = None
	state['phase'] = 'stopped'
	try:
		setattr(mock, REQUIRED_ATTR, False)
	except Exception:
		pass
	if previous_phase in ('preparing', 'seed_wait', 'warmup', 'active'):
		if previous_phase == 'active' and state.get('counted_active'):
			_COUNTERS['active'] = max(0, _COUNTERS['active'] - 1)
			state['counted_active'] = False
		_COUNTERS['stopped'] += 1
		_maybe_log_startup_complete()
	_clear_hazard_recovery(mock)
	return True


def stop_all(mocks):
	count = 0
	targets = list((mocks or {}).values())
	for mock in targets:
		if stop_mock(mock, False):
			count += 1
	if count != len(targets):
		# Keep the batch simulator, adapter and counters alive while any entity still
		# owns native callbacks/filter/physics. The battle sweep will retry this
		# transaction before it destroys models or OfflineEntity instances.
		LOG_ERROR('NATIVE_BOT_PHYSICS stop_all incomplete stopped=%d total=%d' % (
			count, len(targets)))
		return -1
	if count:
		LOG_NOTE('NATIVE_BOT_PHYSICS stopped=%d active=%d failed=%d' % (
			count, _COUNTERS['active'], _COUNTERS['failed']))
	# The adapter replaces a process-global class descriptor. Restoring it is part
	# of the same ownership transaction as releasing every body: if that restore
	# is rejected, the battle sweep must keep its models/global solver state and
	# retry instead of destroying the last objects which make recovery observable.
	try:
		import OfflineEntity
		if not OfflineEntity.restore_native_destructible_callback_adapter():
			LOG_ERROR('NATIVE_BOT_PHYSICS destructible adapter restore failed')
			return -1
	except Exception as error:
		LOG_ERROR('NATIVE_BOT_PHYSICS destructible adapter restore error=%s' % (
			str(error)))
		return -1
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
	_PRESENTATION_DIAGNOSTIC_LOGGED[0] = 0
	return count
