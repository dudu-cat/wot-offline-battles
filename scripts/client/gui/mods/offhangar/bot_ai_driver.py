# -*- coding: utf-8 -*-
"""Engine-free short-range driver for offline battle bots.

The strategic director supplies a waypoint.  Callers supply ``direction_clear``
for the current collision/terrain query; this module chooses only throttle and
steering, so it is safe to exercise outside the BigWorld client.
"""

import math


def _angle_delta(target, current):
	value = float(target) - float(current)
	while value > math.pi:
		value -= math.pi * 2.0
	while value < -math.pi:
		value += math.pi * 2.0
	return value


def _yaw_to(first, second):
	return math.atan2(float(second[0]) - float(first[0]),
	                  float(second[2]) - float(first[2]))


def _distance(first, second):
	dx = float(first[0]) - float(second[0])
	dz = float(first[2]) - float(second[2])
	return math.sqrt(dx * dx + dz * dz)


def _identity_phase(bot_id):
	"""Stable 0..1 value without Python's randomized string hash."""
	text = str(bot_id)
	value = 0
	for char in text:
		value = (value * 33 + ord(char)) & 0x7fffffff
	return float(value % 997) / 997.0


class LocalDriver(object):
	"""Stateful local steering, keyed only by bot id.

	``direction_clear(absolute_yaw)`` must return whether a short vehicle-length
	segment in that direction is drivable.  It may raise; a failed probe is
	treated as blocked.
	"""
	_CANDIDATE_OFFSETS = (0.0, 0.42, -0.42, 0.78, -0.78, 1.18, -1.18)

	def __init__(self, stuck_seconds=1.8, recovery_seconds=0.85,
			separation_radius=12.0, failure_ttl=2.0):
		self.stuck_seconds = max(0.4, float(stuck_seconds))
		self.recovery_seconds = max(0.25, float(recovery_seconds))
		self.separation_radius = max(2.0, float(separation_radius))
		self.failure_ttl = max(0.25, float(failure_ttl))
		self.states = {}

	@staticmethod
	def resolve_order_positions(position, aim_position, move_position, face_position):
		"""Resolve optional tactical targets without mistaking travel for a hold."""
		stop_without_route = aim_position is None and move_position is None
		if stop_without_route:
			aim_position = position
		elif aim_position is None:
			# Server route orders intentionally omit an aim target until an enemy is
			# spotted.  Use the route target for facing, but do not apply idle braking.
			aim_position = move_position
		if move_position is None:
			move_position = aim_position
		if face_position is None:
			face_position = move_position
		return aim_position, move_position, face_position, stop_without_route

	def forget(self, bot_id):
		self.states.pop(bot_id, None)

	def _state(self, bot_id, position):
		state = self.states.get(bot_id)
		if state is None:
			phase = _identity_phase(bot_id)
			state = {
				'last_position': (float(position[0]), float(position[2])),
				'stuck_time': 0.0,
				'recovery_time': 0.0,
				'recovery_count': 0,
				'steering_yaw': None,
				'steering_age': 999.0,
				'plan_age': 999.0,
				'phase': phase,
				'clock': 0.0,
				'failed_yaws': {},
			}
			self.states[bot_id] = state
		return state

	def _yaw_key(self, yaw):
		return int(math.floor(float(yaw) * 4.0 + 0.5))

	def remember_failure(self, bot_id, yaw, ttl=None):
		"""Temporarily penalize a direction after a caller-observed bad path.

		Use this when a terrain probe was clear but later movement establishes that
		the direction is a ditch, steep lip, or another unusable local route.
		"""
		state = self.states.get(bot_id)
		if state is None:
			return
		if ttl is None:
			ttl = self.failure_ttl
		state['failed_yaws'][self._yaw_key(yaw)] = state['clock'] + max(0.1, float(ttl))

	def _failure_penalty(self, state, yaw):
		key = self._yaw_key(yaw)
		expires = state['failed_yaws'].get(key)
		if expires is None:
			return 0.0
		if expires <= state['clock']:
			state['failed_yaws'].pop(key, None)
			return 0.0
		return 3.0 + (expires - state['clock']) / self.failure_ttl

	def _prune_failures(self, state):
		failed = state['failed_yaws']
		for key, expires in list(failed.items()):
			if expires <= state['clock']:
				failed.pop(key, None)
		if len(failed) > 32:
			ordered = sorted(failed.items(), key=lambda item: item[1])
			for key, unused in ordered[:len(failed) - 32]:
				failed.pop(key, None)

	def _neighbour_position(self, neighbour):
		if isinstance(neighbour, dict):
			return neighbour.get('position') or neighbour.get('pos')
		return neighbour

	def _separation_yaw(self, position, neighbours):
		push_x = 0.0
		push_z = 0.0
		for neighbour in neighbours or ():
			other = self._neighbour_position(neighbour)
			if other is None:
				continue
			try:
				if _distance(position, other) > 20.0:
					continue
			except Exception:
				continue
			try:
				if abs(float(other[1]) - float(position[1])) > 5.0:
					continue
			except Exception:
				pass
			try:
				dx = float(position[0]) - float(other[0])
				dz = float(position[2]) - float(other[2])
				dist = math.sqrt(dx * dx + dz * dz)
			except Exception:
				continue
			if dist < 0.05 or dist >= self.separation_radius:
				continue
			weight = (self.separation_radius - dist) / self.separation_radius
			push_x += dx / dist * weight
			push_z += dz / dist * weight
		if abs(push_x) + abs(push_z) < 0.001:
			return None
		return math.atan2(push_x, push_z)

	def _clear(self, direction_clear, yaw):
		try:
			return bool(direction_clear(yaw))
		except Exception:
			return False

	def _velocity(self, value):
		if value is None:
			return (0.0, 0.0)
		try:
			return (float(value[0]), float(value[2]))
		except Exception:
			try:
				return (float(value[0]), float(value[1]))
			except Exception:
				return (0.0, 0.0)

	def _obb_overlap(self, first, first_yaw, first_length, first_width,
				 second, second_yaw, second_length, second_width):
		"""2D rectangle SAT, using yaw convention atan2(x, z)."""
		axes = ((math.sin(first_yaw), math.cos(first_yaw)),
		        (math.cos(first_yaw), -math.sin(first_yaw)),
		        (math.sin(second_yaw), math.cos(second_yaw)),
		        (math.cos(second_yaw), -math.sin(second_yaw)))
		forward_a = (math.sin(first_yaw), math.cos(first_yaw))
		side_a = (math.cos(first_yaw), -math.sin(first_yaw))
		forward_b = (math.sin(second_yaw), math.cos(second_yaw))
		side_b = (math.cos(second_yaw), -math.sin(second_yaw))
		dx = float(second[0]) - float(first[0])
		dz = float(second[2]) - float(first[2])
		for axis in axes:
			distance = abs(dx * axis[0] + dz * axis[1])
			radius_a = (abs(forward_a[0] * axis[0] + forward_a[1] * axis[1]) * first_length +
			            abs(side_a[0] * axis[0] + side_a[1] * axis[1]) * first_width)
			radius_b = (abs(forward_b[0] * axis[0] + forward_b[1] * axis[1]) * second_length +
			            abs(side_b[0] * axis[0] + side_b[1] * axis[1]) * second_width)
			if distance > radius_a + radius_b:
				return False
		return True

	def _prediction_clear(self, position, candidate_yaw, speed, velocity,
				neighbours, half_length, half_width):
		"""Reject a locally clear ray if its next 1.2s overlaps another OBB."""
		own_speed = max(0.0, abs(float(speed)))
		# At walking pace there is not enough velocity for an OBB extrapolation
		# to be useful.  In a dense line-up it instead predicts every neighbour's
		# acceleration against a nearly stationary hull and vetoes all exits.
		# Separation steering and the physical tank resolver remain active; resume
		# predictive collision avoidance once the bot has actually got moving.
		if own_speed < 1.25:
			return True
		desired_vx = math.sin(candidate_yaw) * own_speed
		desired_vz = math.cos(candidate_yaw) * own_speed
		actual_vx, actual_vz = self._velocity(velocity)
		# Tanks cannot instantaneously rotate their velocity vector.  Blend the
		# observed velocity into the short prediction whenever the caller has it.
		if abs(actual_vx) + abs(actual_vz) > 0.05:
			own_vx = actual_vx * 0.45 + desired_vx * 0.55
			own_vz = actual_vz * 0.45 + desired_vz * 0.55
		else:
			own_vx = desired_vx
			own_vz = desired_vz
		for neighbour in neighbours or ():
			other = self._neighbour_position(neighbour)
			if other is None:
				continue
			try:
				if abs(float(other[1]) - float(position[1])) > 5.0:
					continue
			except Exception:
				pass
			other_yaw = 0.0
			other_velocity = None
			other_length = half_length
			other_width = half_width
			if isinstance(neighbour, dict):
				other_yaw = float(neighbour.get('yaw', 0.0) or 0.0)
				other_velocity = neighbour.get('velocity') or neighbour.get('vel')
				other_length = float(neighbour.get('half_length', half_length) or half_length)
				other_width = float(neighbour.get('half_width', half_width) or half_width)
			other_vx, other_vz = self._velocity(other_velocity)
			# Spawn formations can place two hull boxes slightly inside each other.
			# Treating that existing overlap as a future collision rejects every
			# steering candidate, so all bots stop and enter the recovery turn loop.
			# Separation steering already handles this case; predictive vetoes resume
			# as soon as the hulls have moved apart.
			if self._obb_overlap(position, candidate_yaw, half_length, half_width,
					other, other_yaw, other_length, other_width):
				continue
			for horizon in (0.35, 0.75, 1.20):
				own = (float(position[0]) + own_vx * horizon, 0.0,
				       float(position[2]) + own_vz * horizon)
				predicted = (float(other[0]) + other_vx * horizon, 0.0,
				             float(other[2]) + other_vz * horizon)
				if self._obb_overlap(own, candidate_yaw, half_length, half_width,
						predicted, other_yaw, other_length, other_width):
					return False
		return True

	def _choose_yaw(self, state, desired_yaw, position, speed, velocity,
				neighbours, direction_clear, half_length, half_width):
		separation = self._separation_yaw(position, neighbours)
		candidates = []
		for offset in self._CANDIDATE_OFFSETS:
			candidate = desired_yaw + offset
			score = abs(offset) + self._failure_penalty(state, candidate)
			if separation is not None:
				# When bodies overlap, separation outranks route alignment; otherwise
				# two tanks can choose the same narrow opening forever.
				score = score * 0.30 + abs(_angle_delta(candidate, separation))
			candidates.append((score, candidate))
		candidates.sort(key=lambda item: item[0])
		# Probe in score order and return the first fully viable direction. Most
		# frames need one terrain ray set instead of probing all seven candidates.
		for unused_score, candidate in candidates:
			if (self._clear(direction_clear, candidate) and
					self._prediction_clear(position, candidate, speed, velocity,
					neighbours, half_length, half_width)):
				return candidate
		return None

	def drive(self, bot_id, position, yaw, speed, dt, target, neighbours,
			direction_clear, velocity=None, half_length=3.5, half_width=1.7):
		"""Return ``throttle``, ``turn``, ``target_yaw`` and ``recovery_mode``.

		All timing uses supplied seconds.  ``dt`` is clamped so a paused client
		cannot immediately declare every bot stuck when it resumes.
		"""
		state = self._state(bot_id, position)
		step = min(0.35, max(0.0, float(dt)))
		state['clock'] += step
		self._prune_failures(state)
		state['steering_age'] += step
		state['plan_age'] += step
		desired_yaw = _yaw_to(position, target)
		displacement = _distance((position[0], 0.0, position[2]),
		                         (state['last_position'][0], 0.0,
		                          state['last_position'][1]))
		state['last_position'] = (float(position[0]), float(position[2]))

		# A low reported speed alone is not enough: waiting at a hold point should
		# not trigger recovery.  Both physical displacement and velocity must fail.
		if _distance(position, target) > 3.0 and displacement < 0.08 and abs(float(speed)) < 0.35:
			state['stuck_time'] += step
		else:
			state['stuck_time'] = max(0.0, state['stuck_time'] - step * 2.0)

		threshold = self.stuck_seconds + state['phase'] * 0.42
		if state['recovery_time'] > 0.0:
			state['recovery_time'] = max(0.0, state['recovery_time'] - step)
			if state['recovery_time'] == 0.0:
				state['recovery_count'] += 1
				state['stuck_time'] = 0.0
		else:
			if state['stuck_time'] >= threshold:
				if state.get('last_clear_yaw') is not None:
					state['failed_yaws'][self._yaw_key(state['last_clear_yaw'])] = (
						state['clock'] + self.failure_ttl)
				state['recovery_time'] = self.recovery_seconds + state['phase'] * 0.28

		if state['recovery_time'] > 0.0:
			# Alternate the turn direction each recovery so a bot does not grind a
			# wall forever.  Phase makes adjacent ids leave a traffic jam apart.
			direction = 1.0 if ((state['recovery_count'] + int(state['phase'] * 10)) % 2) else -1.0
			recovery_yaw = float(yaw) + direction * 0.85
			return {
				'throttle': -0.72,
				'turn': direction,
				'target_yaw': recovery_yaw,
				'recovery_mode': 'reverse_turn',
			}

		own_half_length = max(0.5, float(half_length))
		own_half_width = max(0.3, float(half_width))
		chosen_yaw = None
		old_yaw = state.get('steering_yaw')
		# Keep a recently selected steering direction for 180 ms, but revalidate
		# both the hard terrain probe and moving OBBs every frame. This avoids a
		# full seven-way probe when the previous safe direction still works.
		if (old_yaw is not None and state['plan_age'] < 0.18 and
				abs(_angle_delta(desired_yaw, old_yaw)) < 0.70 and
				self._failure_penalty(state, old_yaw) <= 0.0 and
				self._clear(direction_clear, old_yaw) and
				self._prediction_clear(position, old_yaw, speed, velocity,
					neighbours, own_half_length, own_half_width)):
			chosen_yaw = old_yaw
		if chosen_yaw is None:
			chosen_yaw = self._choose_yaw(
				state, desired_yaw, position, speed, velocity, neighbours,
				direction_clear, own_half_length, own_half_width)
			state['plan_age'] = 0.0
		if chosen_yaw is None:
			# No forward ray is usable.  Start a timed recovery on the next tick
			# rather than issuing an unsafe blind turn.
			state['stuck_time'] = max(state['stuck_time'], threshold)
			return {
				'throttle': 0.0,
				'turn': 0.0,
				'target_yaw': float(yaw),
				'recovery_mode': 'blocked',
			}
		state['last_clear_yaw'] = chosen_yaw

		# Retain a selected side for a short time. This removes left/right flip
		# flop while the per-frame hard terrain veto remains active above.
		old_yaw = state['steering_yaw']
		if old_yaw is None or abs(_angle_delta(chosen_yaw, old_yaw)) > 0.04:
			state['steering_yaw'] = chosen_yaw
			state['steering_age'] = 0.0

		delta = _angle_delta(chosen_yaw, yaw)
		turn = max(-1.0, min(1.0, delta / 0.58))
		throttle = 1.0
		if abs(delta) > 1.0:
			# 0.35 cannot overcome the steering resistance in the shared vehicle
			# physics: the tank settles below walking speed and traces a tiny circle
			# around its spawn.  Keep enough drive to make a real clearing arc.
			throttle = 0.72
		elif abs(delta) > 0.55:
			throttle = 0.78
		return {
			'throttle': throttle,
			'turn': turn,
			'target_yaw': chosen_yaw,
			'recovery_mode': 'avoid' if abs(_angle_delta(chosen_yaw, desired_yaw)) > 0.05 else 'drive',
		}
