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
			separation_radius=12.0):
		self.stuck_seconds = max(0.4, float(stuck_seconds))
		self.recovery_seconds = max(0.25, float(recovery_seconds))
		self.separation_radius = max(2.0, float(separation_radius))
		self.states = {}

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
				'phase': phase,
			}
			self.states[bot_id] = state
		return state

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

	def _choose_yaw(self, desired_yaw, position, neighbours, direction_clear):
		separation = self._separation_yaw(position, neighbours)
		best_yaw = None
		best_score = None
		for offset in self._CANDIDATE_OFFSETS:
			candidate = desired_yaw + offset
			if not self._clear(direction_clear, candidate):
				continue
			score = abs(offset)
			if separation is not None:
				# When bodies overlap, separation outranks route alignment; otherwise
				# two tanks can choose the same narrow opening forever.
				score = score * 0.30 + abs(_angle_delta(candidate, separation))
			if best_score is None or score < best_score:
				best_score = score
				best_yaw = candidate
		return best_yaw

	def drive(self, bot_id, position, yaw, speed, dt, target, neighbours,
			direction_clear):
		"""Return ``throttle``, ``turn``, ``target_yaw`` and ``recovery_mode``.

		All timing uses supplied seconds.  ``dt`` is clamped so a paused client
		cannot immediately declare every bot stuck when it resumes.
		"""
		state = self._state(bot_id, position)
		step = min(0.35, max(0.0, float(dt)))
		state['steering_age'] += step
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

		chosen_yaw = self._choose_yaw(desired_yaw, position, neighbours, direction_clear)
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

		# Retain a selected side for a short time.  This removes left/right flip
		# flop when two collision rays alternate around another moving vehicle.
		old_yaw = state['steering_yaw']
		if old_yaw is not None and state['steering_age'] < 0.32 and self._clear(direction_clear, old_yaw):
			if abs(_angle_delta(chosen_yaw, old_yaw)) < 0.62:
				chosen_yaw = old_yaw
		if old_yaw is None or abs(_angle_delta(chosen_yaw, old_yaw)) > 0.04:
			state['steering_yaw'] = chosen_yaw
			state['steering_age'] = 0.0

		delta = _angle_delta(chosen_yaw, yaw)
		turn = max(-1.0, min(1.0, delta / 0.58))
		throttle = 1.0
		if abs(delta) > 1.0:
			throttle = 0.35
		elif abs(delta) > 0.55:
			throttle = 0.62
		return {
			'throttle': throttle,
			'turn': turn,
			'target_yaw': chosen_yaw,
			'recovery_mode': 'avoid' if abs(_angle_delta(chosen_yaw, desired_yaw)) > 0.05 else 'drive',
		}
