# -*- coding: utf-8 -*-
"""Terrain-aware hierarchical navigation for offline/LAN bots.

Strategic map annotations decide where a vehicle should fight. This module
connects those sparse anchors with a low-resolution A* path, while the battle
driver remains responsible for short-range steering around moving vehicles.
The implementation is engine-free; the caller supplies terrain and collision
probes so it can be tested outside the legacy client.
"""

import heapq
import math
import time


SQRT_TWO = math.sqrt(2.0)

try:
	_CLOCK = time.clock
except AttributeError:
	_CLOCK = time.perf_counter


def _distance_2d(first, second):
	dx = float(first[0]) - float(second[0])
	dz = float(first[2]) - float(second[2])
	return math.sqrt(dx * dx + dz * dz)


class TerrainGrid(object):
	"""Lazy terrain graph. Cells and edges are probed only when A* needs them."""

	_NEIGHBOURS = (
		(-1, -1, SQRT_TWO), (0, -1, 1.0), (1, -1, SQRT_TWO),
		(-1, 0, 1.0),                         (1, 0, 1.0),
		(-1, 1, SQRT_TWO),  (0, 1, 1.0),  (1, 1, SQRT_TWO),
	)

	def __init__(self, ground_probe, obstacle_probe=None, bounds=None,
			cell_size=18.0, max_grade_up=0.48, max_grade_down=0.38):
		self.ground_probe = ground_probe
		self.obstacle_probe = obstacle_probe
		self.bounds = bounds
		self.cell_size = max(1.0, float(cell_size))
		self.max_grade_up = float(max_grade_up)
		self.max_grade_down = float(max_grade_down)
		# Weighted A* deliberately favours forward progress over a perfectly
		# shortest coarse-grid route. Every returned edge is still terrain-probed;
		# only the amount of side exploration changes.
		self.heuristic_weight = 1.70
		self._ground_cache = {}
		self._edge_cache = {}
		self._segment_cache = {}
		self._failed_edges = {}

	def cell_for(self, point):
		return (int(math.floor(float(point[0]) / self.cell_size + 0.5)),
		        int(math.floor(float(point[2]) / self.cell_size + 0.5)))

	def point_for(self, cell, height):
		return (cell[0] * self.cell_size, float(height),
		        cell[1] * self.cell_size)

	def _inside(self, x, z):
		if self.bounds is None:
			return True
		try:
			return (float(self.bounds[0]) <= x <= float(self.bounds[2]) and
			        float(self.bounds[1]) <= z <= float(self.bounds[3]))
		except Exception:
			# Invalid bounds must not silently disable the map-edge guard.
			return False

	def clear_negative_cache(self):
		"""Retry cells that may have missed while distant chunks streamed in."""
		for cache in (self._ground_cache, self._edge_cache):
			for key, value in list(cache.items()):
				if value is None:
					cache.pop(key, None)
		for key, value in list(self._segment_cache.items()):
			if not value:
				self._segment_cache.pop(key, None)

	def _layer(self, hint_y):
		return int(math.floor(float(hint_y) / 8.0 + 0.5))

	def _point_key(self, point):
		return (int(math.floor(float(point[0]) * 0.5 + 0.5)),
		        int(math.floor(float(point[2]) * 0.5 + 0.5)),
		        self._layer(point[1]))

	def _edge_cells_for_segment(self, start, end):
		edges = self._edge_keys_for_segment(start, end)
		return edges[0] if edges else None

	def _edge_keys_for_segment(self, start, end):
		start_cell = self.cell_for(start)
		end_cell = self.cell_for(end)
		if start_cell == end_cell:
			return ()
		x, z = start_cell
		target_x, target_z = end_cell
		dx = abs(target_x - x)
		dz = abs(target_z - z)
		step_x = 1 if x < target_x else -1
		step_z = 1 if z < target_z else -1
		error = dx - dz
		cells = [start_cell]
		while x != target_x or z != target_z:
			double_error = error * 2
			if double_error > -dz:
				error -= dz
				x += step_x
			if double_error < dx:
				error += dx
				z += step_z
			cells.append((x, z))
		return tuple(tuple(sorted((cells[index], cells[index + 1])))
		             for index in range(len(cells) - 1))

	def prune_failed_edges(self, now):
		for key, value in list(self._failed_edges.items()):
			if float(now) >= value[0]:
				self._failed_edges.pop(key, None)
		if len(self._failed_edges) > 128:
			ordered = sorted(self._failed_edges.items(), key=lambda item: item[1][0])
			for key, unused in ordered[:len(self._failed_edges) - 128]:
				self._failed_edges.pop(key, None)

	def trim_caches(self):
		for cache, limit in ((self._ground_cache, 4096),
		                     (self._edge_cache, 4096),
		                     (self._segment_cache, 4096)):
			while len(cache) > limit:
				try:
					cache.popitem()
				except Exception:
					break

	def remember_failed_segment(self, start, end, now, ttl=18.0, penalty=240.0):
		"""Penalize the first coarse edge of a route that made no progress."""
		key = self._edge_cells_for_segment(start, end)
		if key is None:
			return
		self._failed_edges[key] = (float(now) + max(1.0, float(ttl)),
		                           max(0.0, float(penalty)))
		if len(self._failed_edges) > 512:
			alive = [(edge, value) for edge, value in self._failed_edges.items()
			         if value[0] > float(now)]
			alive.sort(key=lambda item: item[1][0], reverse=True)
			self._failed_edges = dict(alive[:384])

	def _failed_edge_penalty(self, first_cell, second_cell, now):
		key = tuple(sorted((first_cell, second_cell)))
		value = self._failed_edges.get(key)
		if value is None:
			return 0.0
		if float(now) >= value[0]:
			self._failed_edges.pop(key, None)
			return 0.0
		return value[1]

	def segment_penalty(self, start, end, now):
		penalty = 0.0
		for key in self._edge_keys_for_segment(start, end):
			penalty = max(penalty,
			              self._failed_edge_penalty(key[0], key[1], now))
		return penalty

	def path_has_penalty(self, path, now):
		for index in range(len(path) - 1):
			if self.segment_penalty(path[index], path[index + 1], now) > 0.0:
				return True
		return False

	def _ground(self, x, z, hint_y):
		if not self._inside(x, z):
			return None
		key = (int(math.floor(x * 10.0 + 0.5)),
		       int(math.floor(z * 10.0 + 0.5)), self._layer(hint_y))
		if key in self._ground_cache:
			return self._ground_cache[key]
		try:
			height = self.ground_probe(float(x), float(z), float(hint_y))
			if height is not None:
				height = float(height)
		except Exception:
			height = None
		self._ground_cache[key] = height
		return height

	def segment_clear(self, start, end):
		"""Check continuous support and drivable grade, not just both endpoints."""
		distance = _distance_2d(start, end)
		if distance < 0.25:
			return True
		start_key = self._point_key(start)
		end_key = self._point_key(end)
		key = (start_key, end_key)
		cached = self._segment_cache.get(key)
		if cached is not None:
			return bool(cached)
		steps = max(1, int(math.ceil(distance / (self.cell_size * 0.42))))
		start_y = self._ground(float(start[0]), float(start[2]), float(start[1]))
		if start_y is None:
			start_y = float(start[1])
		previous = (float(start[0]), start_y, float(start[2]))
		grounded_start = previous
		clear = True
		for index in range(1, steps + 1):
			fraction = float(index) / float(steps)
			x = float(start[0]) + (float(end[0]) - float(start[0])) * fraction
			z = float(start[2]) + (float(end[2]) - float(start[2])) * fraction
			horizontal = math.sqrt((x - previous[0]) ** 2 + (z - previous[2]) ** 2)
			y = self._ground(x, z, previous[1])
			if y is None:
				clear = False
				break
			delta = y - previous[1]
			if (delta > horizontal * self.max_grade_up or
			        delta < -horizontal * self.max_grade_down):
				clear = False
				break
			current = (x, y, z)
			previous = current
		if clear and self.obstacle_probe is not None:
			try:
				if self.obstacle_probe(grounded_start, previous, 2.15):
					clear = False
			except Exception:
				# Collision-query failure is unknown terrain, not proof that a
				# several-ton vehicle has a clear corridor.
				clear = False
		self._segment_cache[key] = bool(clear)
		self._segment_cache[(end_key, start_key)] = bool(clear)
		return clear

	def _edge(self, cell, height, next_cell):
		key = (cell, next_cell, self._layer(height))
		if key in self._edge_cache:
			return self._edge_cache[key]
		start = self.point_for(cell, height)
		x = next_cell[0] * self.cell_size
		z = next_cell[1] * self.cell_size
		end_y = self._ground(x, z, height)
		if end_y is None:
			result = None
		else:
			end = (x, end_y, z)
			result = end_y if self.segment_clear(start, end) else None
		self._edge_cache[key] = result
		return result

	def _penalty(self, cell, avoid_points):
		if not avoid_points:
			return 0.0
		x = cell[0] * self.cell_size
		z = cell[1] * self.cell_size
		penalty = 0.0
		for point in avoid_points:
			dx = x - float(point[0])
			dz = z - float(point[2])
			distance = math.sqrt(dx * dx + dz * dz)
			if distance < self.cell_size * 1.5:
				penalty += (self.cell_size * 1.5 - distance) * 3.0
		return penalty

	def safe_local_target(self, current, goal, now, avoid_points=None,
			side_preference=1.0):
		"""Choose one short, fully probed detour when the global search fails.

		This is deliberately not a direct-to-goal fallback. Every candidate must
		have supported ground, a safe grade, no deep water, no static collision and
		no remembered failed edge. Returning ``None`` means the only safe action is
		to stop and retry the global planner later.
		"""
		dx = float(goal[0]) - float(current[0])
		dz = float(goal[2]) - float(current[2])
		if abs(dx) + abs(dz) < 0.1:
			return None
		desired_yaw = math.atan2(dx, dz)
		side = 1.0 if float(side_preference) >= 0.0 else -1.0
		offsets = (0.0, side * 0.45, -side * 0.45,
		           side * 0.85, -side * 0.85,
		           side * 1.30, -side * 1.30,
		           side * 1.75, -side * 1.75)
		distances = (self.cell_size * 0.78, self.cell_size * 0.52)
		best = None
		for distance in distances:
			for offset in offsets:
				yaw = desired_yaw + offset
				x = float(current[0]) + math.sin(yaw) * distance
				z = float(current[2]) + math.cos(yaw) * distance
				y = self._ground(x, z, float(current[1]))
				if y is None:
					continue
				candidate = (x, y, z)
				if (self.segment_penalty(current, candidate, now) > 0.0 or
						not self.segment_clear(current, candidate)):
					continue
				cell = self.cell_for(candidate)
				score = (_distance_2d(candidate, goal) + abs(offset) * 3.5 +
				         self._penalty(cell, avoid_points) * 2.0)
				value = (score, abs(offset), candidate)
				if best is None or value[:2] < best[:2]:
					best = value
		return best[2] if best is not None else None

	def plan(self, start, goal, avoid_points=None, max_expansions=1600, now=0.0):
		"""Return a supported path synchronously (mainly for tests/tools)."""
		search = self.begin_plan(start, goal, avoid_points, max_expansions, now)
		while not search.done:
			search.step(256)
		return search.result

	def begin_plan(self, start, goal, avoid_points=None, max_expansions=1600,
			now=0.0):
		return _TerrainSearch(self._plan_steps(
			start, goal, avoid_points, max_expansions, now))

	def _plan_steps(self, start, goal, avoid_points, max_expansions, now):
		start_cell = self.cell_for(start)
		goal_cell = self.cell_for(goal)
		start_y = self._ground(float(start[0]), float(start[2]), float(start[1]))
		if start_y is None:
			start_y = float(start[1])
		frontier = []
		sequence = 0
		heapq.heappush(frontier, (0.0, sequence, start_cell))
		came_from = {}
		cost_so_far = {start_cell: 0.0}
		heights = {start_cell: start_y}
		reached = None
		closest = start_cell
		closest_distance = math.sqrt(
			(start_cell[0] - goal_cell[0]) ** 2 +
			(start_cell[1] - goal_cell[1]) ** 2)
		expansions = 0
		while frontier and expansions < int(max_expansions):
			_unused_priority, _unused_sequence, current = heapq.heappop(frontier)
			expansions += 1
			goal_distance = math.sqrt(
				(current[0] - goal_cell[0]) ** 2 +
				(current[1] - goal_cell[1]) ** 2)
			if goal_distance < closest_distance:
				closest = current
				closest_distance = goal_distance
			if current == goal_cell:
				reached = current
				break
			current_y = heights[current]
			for offset_x, offset_z, length_scale in self._NEIGHBOURS:
				next_cell = (current[0] + offset_x, current[1] + offset_z)
				next_y = self._edge(current, current_y, next_cell)
				if next_y is None:
					continue
				if offset_x and offset_z:
					# Do not squeeze diagonally across a blocked corner.
					if (self._edge(current, current_y,
					               (current[0] + offset_x, current[1])) is None or
					        self._edge(current, current_y,
					               (current[0], current[1] + offset_z)) is None):
						continue
				run = self.cell_size * length_scale
				slope = abs(next_y - current_y) / max(run, 0.1)
				new_cost = (cost_so_far[current] + run * (1.0 + slope * 3.0) +
				            self._penalty(next_cell, avoid_points) +
				            self._failed_edge_penalty(current, next_cell, now))
				if next_cell not in cost_so_far or new_cost < cost_so_far[next_cell]:
					cost_so_far[next_cell] = new_cost
					came_from[next_cell] = current
					heights[next_cell] = next_y
					dx = next_cell[0] - goal_cell[0]
					dz = next_cell[1] - goal_cell[1]
					# A modest weighted heuristic keeps the old 32-bit client from
					# exploring a broad irrelevant front around long ridges.
					heuristic = (math.sqrt(dx * dx + dz * dz) * self.cell_size *
					             self.heuristic_weight)
					sequence += 1
					heapq.heappush(frontier,
					               (new_cost + heuristic, sequence, next_cell))
			yield None
		if reached is None:
			# Sparse strategic anchors are hand placed on a minimap. A point a few
			# metres inside a building footprint, cliff lip or water edge must not
			# invalidate an otherwise complete route. Use the nearest cell A* could
			# actually reach, but only within three coarse cells: a grossly wrong
			# anchor still fails instead of silently changing battle lanes.
			if closest_distance <= 3.0:
				reached = closest
			elif frontier and closest != start_cell:
				# The bounded search still has work, so return the safest progress it
				# has already proved instead of reporting a false hard failure. The next
				# request continues from that supported partial path.
				reached = closest
			else:
				yield ()
				return
		cells = [reached]
		while cells[-1] != start_cell:
			cells.append(came_from[cells[-1]])
		cells.reverse()
		path = [(float(start[0]), start_y, float(start[2]))]
		for cell in cells[1:]:
			path.append(self.point_for(cell, heights[cell]))
		goal_y = self._ground(float(goal[0]), float(goal[2]), path[-1][1])
		goal_point = (float(goal[0]), goal_y if goal_y is not None else path[-1][1],
		              float(goal[2]))
		if self.segment_clear(path[-1], goal_point):
			path.append(goal_point)
		yield self._smooth(tuple(path), now)

	def _smooth(self, path, now=0.0):
		if len(path) < 3:
			return path
		result = [path[0]]
		index = 0
		while index < len(path) - 1:
			furthest = min(len(path) - 1, index + 6)
			while furthest > index + 1:
				if (self.segment_penalty(path[index], path[furthest], now) <= 0.0 and
						self.segment_clear(path[index], path[furthest])):
					break
				furthest -= 1
			result.append(path[furthest])
			index = furthest
		return tuple(result)


class _TerrainSearch(object):
	"""Small resumable A* task so collision probes are spread across frames."""

	def __init__(self, generator):
		self.generator = generator
		self.done = False
		self.result = None
		self.last_frame = None

	def step(self, budget):
		if self.done:
			return True
		for _unused in range(max(1, int(budget))):
			try:
				value = next(self.generator)
			except StopIteration:
				self.done = True
				self.result = ()
				break
			if value is not None:
				self.done = True
				self.result = value
				break
		return self.done


class TerrainNavigator(object):
	"""Shared strategic path cache plus per-bot path following and recovery."""

	def __init__(self, ground_probe, obstacle_probe=None, bounds=None,
			cell_size=18.0):
		self.grid = TerrainGrid(ground_probe, obstacle_probe, bounds, cell_size)
		self.paths = {}
		self.path_times = {}
		self.searches = {}
		self.search_times = {}
		self.bot_states = {}
		self.search_frame_time = None
		self.search_next_key = None
		self.search_budget_per_frame = 128
		self.search_budget_per_path = 4
		self.search_time_budget = 0.0025
		# A bounded search returns its best fully-probed partial path. This keeps a
		# 29-bot room from waiting tens of seconds for 1600 expansions per job; the
		# continuation search starts after the bot reaches that safe endpoint.
		self.search_max_expansions = 128
		self.search_completed = 0
		self.search_failed = 0
		self.search_now = 0.0
		self.fallback_totals = {
			'safe_direct': 0, 'safe_local': 0, 'reactive': 0}
		self.fallback_recovered = 0
		self.fallback_modes = {}

	def _set_fallback_mode(self, bot_id, mode):
		old_mode = self.fallback_modes.get(int(bot_id))
		if old_mode == mode:
			return
		if old_mode is not None and mode is None:
			self.fallback_recovered += 1
		if mode is None:
			self.fallback_modes.pop(int(bot_id), None)
		else:
			self.fallback_modes[int(bot_id)] = mode
			self.fallback_totals[mode] = self.fallback_totals.get(mode, 0) + 1

	def fallback_diagnostics(self, active_bot_ids=None, now=None):
		if active_bot_ids is not None:
			active_ids = set(int(value) for value in active_bot_ids)
			for bot_id in list(self.fallback_modes):
				if bot_id not in active_ids:
					self.fallback_modes.pop(bot_id, None)
		active = {'safe_direct': 0, 'safe_local': 0, 'reactive': 0}
		for mode in self.fallback_modes.values():
			active[mode] = active.get(mode, 0) + 1
		return {
			'total': dict(self.fallback_totals),
			'active': active,
			'recovered': int(self.fallback_recovered),
			'search': {
				'pending': len(self.searches),
				'completed': int(self.search_completed),
				'failed': int(self.search_failed),
				'oldest_ms': int(max(0.0, self.search_now -
					min(self.search_times.values())) * 1000.0)
					if self.search_times else 0,
				'tick_age_ms': int(max(0.0, float(now) - self.search_now) * 1000.0)
					if now is not None else 0,
			},
		}

	def _fallback_target(self, bot_id, current, goal, now, avoid_points, state,
			allow_safe_local=True):
		"""Keep moving without treating an unproved long segment as drivable.

		A fully probed short waypoint is preferred after a conclusive A* failure.
		If none exists (or the search is merely pending), return the strategic goal
		as steering intent for LocalDriver. The caller still probes every candidate
		vehicle-width corridor and can only throttle into one that is locally safe.
		"""
		if allow_safe_local:
			fallback = self.grid.safe_local_target(
				current, goal, now, avoid_points,
				1.0 if (int(bot_id) % 2) else -1.0)
			if fallback is not None:
				state['last_target'] = tuple(fallback)
				self._set_fallback_mode(bot_id, 'safe_local')
				return tuple(fallback)
		state['last_target'] = tuple(goal)
		self._set_fallback_mode(bot_id, 'reactive')
		return tuple(goal)

	def _cache_key(self, path_key, goal):
		return (tuple(path_key), self.grid.cell_for(goal))

	def _trim_cache(self, now):
		if len(self.paths) <= 96:
			return
		ordered = sorted(self.path_times.items(), key=lambda item: item[1])
		for key, _timestamp in ordered[:len(ordered) - 80]:
			self.paths.pop(key, None)
			self.path_times.pop(key, None)

	def _finish_search(self, key, search, now):
		path = search.result or ()
		self.searches.pop(key, None)
		self.search_times.pop(key, None)
		self.paths[key] = path
		self.path_times[key] = float(now)
		if path:
			self.search_completed += 1
		else:
			self.search_failed += 1

	def _cancel_bot_searches(self, bot_id):
		"""Discard superseded private jobs without touching shared route plans."""
		bot_id = int(bot_id)
		for key in list(self.searches):
			try:
				path_key = key[0]
				owned = (isinstance(path_key, tuple) and len(path_key) > 1 and
				         path_key[0] in ('local', 'join', 'recovery', 'continue') and
				         int(path_key[1]) == bot_id)
			except Exception:
				owned = False
			if owned:
				self.searches.pop(key, None)
				self.search_times.pop(key, None)

	def _advance_searches(self, now):
		"""Give every pending A* task a fair share of the current frame.

		The old on-demand scheduler handed the whole frame budget to whichever bots
		were updated first. With a 29-bot room, later join searches could therefore
		remain pending forever. This rotating queue gives every task one expansion
		before any task receives a second, and remembers the next task across frames.
		"""
		self.search_now = float(now)
		if (self.search_frame_time is not None and
				abs(float(now) - self.search_frame_time) < 0.000001):
			return
		self.search_frame_time = float(now)
		keys = sorted(self.searches, key=lambda value: repr(value))
		if not keys:
			self.search_next_key = None
			return
		if self.search_next_key in keys:
			start = keys.index(self.search_next_key)
			queue = keys[start:] + keys[:start]
		else:
			queue = keys
		steps = {}
		budget = max(0, int(self.search_budget_per_frame))
		per_path = max(1, int(self.search_budget_per_path))
		# Preserve the old 32-expansion floor so one expensive probe cannot reduce
		# a lone search to one node per frame; the clock budget limits only the
		# additional burst up to the new hard cap.
		minimum_round = min(budget, max(len(queue), 32))
		processed = 0
		started = _CLOCK()
		while budget > 0 and queue:
			key = queue.pop(0)
			search = self.searches.get(key)
			if search is None:
				continue
			search.step(1)
			search.last_frame = float(now)
			budget -= 1
			processed += 1
			steps[key] = steps.get(key, 0) + 1
			if search.done:
				self._finish_search(key, search, now)
			elif steps[key] < per_path:
				queue.append(key)
			if (processed >= minimum_round and self.search_time_budget > 0.0 and
					_CLOCK() - started >= self.search_time_budget):
				break
		self.search_next_key = queue[0] if queue else None
		self._trim_cache(now)

	def tick(self, now):
		"""Advance shared path jobs once per rendered frame, even when bots hold."""
		self._advance_searches(now)
		self.grid.prune_failed_edges(now)
		self.grid.trim_caches()

	def _path(self, path_key, start, goal, now, avoid_points):
		key = self._cache_key(path_key, goal)
		if key in self.paths:
			path = self.paths[key]
			# A probe can fail while distant chunks are still streaming. Successful
			# paths are permanent for the battle; failed ones get another chance.
			if path and not self.grid.path_has_penalty(path, now):
				self.path_times[key] = float(now)
				return key, path
			if path:
				del self.paths[key]
				self.path_times.pop(key, None)
			else:
				if float(now) - self.path_times.get(key, 0.0) < 8.0:
					return key, path
				del self.paths[key]
				self.path_times.pop(key, None)
				self.grid.clear_negative_cache()
		search = self.searches.get(key)
		if search is None:
			# Most annotated segments are already open roads. Avoid invoking A*
			# when one continuous support/collision check proves the direct link.
			if (self.grid.segment_penalty(start, goal, now) <= 0.0 and
					self.grid.segment_clear(start, goal)):
				path = (tuple(start), tuple(goal))
				self.paths[key] = path
				self.path_times[key] = float(now)
				return key, path
			# Moving tanks do not belong in a cached static terrain path. Including all
			# 28 peers made every expansion scan transient positions, permanently baked
			# traffic into shared paths, and multiplied probe cost. LocalDriver handles
			# moving OBBs every frame; A* only owns static terrain and remembered edges.
			search = self.grid.begin_plan(
				start, goal, avoid_points=None,
				max_expansions=self.search_max_expansions, now=now)
			self.searches[key] = search
			self.search_times[key] = float(now)
		self._advance_searches(now)
		if key in self.paths:
			return key, self.paths[key]
		if not search.done:
			return key, None
		# _advance_searches normally caches completed jobs. This branch only covers
		# a test double or an externally completed task.
		self._finish_search(key, search, now)
		return key, self.paths[key]

	def next_target(self, bot_id, current, goal, path_key, now,
			anchor=None, avoid_points=None):
		"""Return a terrain-safe local target, holding if no safe path is ready."""
		bot_id = int(bot_id)
		# Search progress is a navigator-wide frame task, not a cache-miss side
		# effect. Once every active bot had a cached/partial path, _path() returned
		# before advancing unrelated join and continuation jobs, leaving the whole
		# room parked with an ever-growing pending queue.
		self.tick(now)
		state = self.bot_states.get(bot_id)
		if state is None:
			state = {'last_position': tuple(current), 'progress_time': float(now),
			         'path_key': None, 'index': 0, 'recovery': 0,
			         'recovery_until': 0.0, 'recovery_key': None,
			         'recovery_start': None, 'request_key': None,
			         'request_path_key': None, 'planned_goal': None,
			         'planned_at': 0.0}
			self.bot_states[bot_id] = state
		path_identity = tuple(path_key)
		planned_goal = state.get('planned_goal')
		if (state.get('request_path_key') == path_identity and
				planned_goal is not None and
				_distance_2d(planned_goal, goal) < self.grid.cell_size * 2.0 and
				float(now) - float(state.get('planned_at', 0.0)) < 2.0):
			# A moving contact may cross a coarse cell every observation. Keep the
			# current terrain plan briefly instead of cancelling it before A* can
			# finish; aiming still uses the target's current live pose.
			goal = tuple(planned_goal)
		else:
			state['request_path_key'] = path_identity
			state['planned_goal'] = tuple(goal)
			state['planned_at'] = float(now)
		request_key = self._cache_key(path_key, goal)
		if state.get('request_key') != request_key:
			# A new route segment or combat target is not evidence that the previous
			# request stalled. Reset recovery before evaluating progress.
			self._cancel_bot_searches(bot_id)
			state['request_key'] = request_key
			state['path_key'] = None
			state['index'] = 0
			state['last_position'] = tuple(current)
			state['progress_time'] = float(now)
			state['recovery'] = 0
			state['recovery_until'] = 0.0
			state['recovery_key'] = None
			state['recovery_start'] = None
		if _distance_2d(current, state['last_position']) >= 2.0:
			state['last_position'] = tuple(current)
			state['progress_time'] = float(now)
			state['recovery'] = 0
			state['recovery_until'] = 0.0
		plan_start = tuple(anchor or current)
		if anchor is not None:
			# Strategic route annotations are two-dimensional and LAN protocol v5
			# historically transported them with y=0.  Use the live vehicle layer as
			# the terrain-probe hint; otherwise elevated spawns make every shared
			# route search start below the map and fail before its first edge.
			plan_start = (float(plan_start[0]), float(current[1]),
			              float(plan_start[2]))
		# A lack of displacement is not proof that the static terrain edge is bad.
		# It is commonly a traffic jam, a tank-to-tank push, or LocalDriver turning
		# in place.  The former recovery path marked that edge globally, invalidated
		# every bot's shared route, and caused an expanding replan/failure storm.
		# LocalDriver already owns short-range stuck recovery; the terrain graph is
		# now changed only by actual terrain/collision probes.
		effective_key = tuple(path_key)
		key, path = self._path(effective_key, plan_start, goal, now,
		                       None)
		if path is None:
			if (self.grid.segment_penalty(current, goal, now) <= 0.0 and
					self.grid.segment_clear(current, goal)):
				state['last_target'] = tuple(goal)
				self._set_fallback_mode(bot_id, 'safe_direct')
				return tuple(goal)
			return self._fallback_target(
				bot_id, current, goal, now, avoid_points, state, False)
		if not path:
			if (self.grid.segment_penalty(current, goal, now) <= 0.0 and
					self.grid.segment_clear(current, goal)):
				state['last_target'] = tuple(goal)
				self._set_fallback_mode(bot_id, 'safe_direct')
				return tuple(goal)
			state['path_key'] = key
			return self._fallback_target(
				bot_id, current, goal, now, avoid_points, state, True)
		active_key = state.get('path_key')
		if active_key is not None and active_key != key:
			active_path = self.paths.get(active_key)
			if (active_path and
					not self.grid.path_has_penalty(active_path, now)):
				# A join/recovery/continuation path starts at this hull's real
				# position. Follow it to completion instead of replacing it with
				# the shared strategic path again on the next frame.
				key = active_key
				path = active_path
				self.path_times[key] = float(now)
		if state.get('path_key') != key:
			state['path_key'] = key
			state['index'] = 0
			best_index = 0
			best_distance = 1e18
			for index, point in enumerate(path):
				distance = _distance_2d(current, point)
				if distance < best_distance:
					best_distance = distance
					best_index = index
			state['index'] = best_index
		index = min(int(state.get('index', 0)), len(path) - 1)
		if (self.grid.segment_penalty(current, path[index], now) > 0.0 or
				not self.grid.segment_clear(current, path[index])):
			join_key = ('join', bot_id, self.grid.cell_for(current)) + tuple(path_key)
			key, joined_path = self._path(join_key, current, goal, now, avoid_points)
			if joined_path is None:
				return self._fallback_target(
					bot_id, current, goal, now, avoid_points, state, False)
			if not joined_path:
				# The cached strategic path is unusable from this hull's actual
				# position and the join search has conclusively failed. Reuse the
				# same fully probed short fallback as a failed global search; if no
				# safe candidate exists, remain stopped and retry.
				state['path_key'] = key
				return self._fallback_target(
					bot_id, current, goal, now, avoid_points, state, True)
			path = joined_path
			state['path_key'] = key
			state['index'] = 0
			index = 0
		reach_radius = min(10.0, max(1.5, self.grid.cell_size * 0.55))
		while (index + 1 < len(path) and
		       _distance_2d(current, path[index]) < reach_radius and
		       self.grid.segment_penalty(current, path[index + 1], now) <= 0.0 and
		       self.grid.segment_clear(current, path[index + 1])):
			index += 1
		# Look ahead only while every skipped piece is continuously supported.
		lookahead = index
		for candidate in range(index + 1, min(len(path), index + 3)):
			if (self.grid.segment_penalty(current, path[candidate], now) <= 0.0 and
					self.grid.segment_clear(current, path[candidate])):
				lookahead = candidate
			else:
				break
		if (lookahead == len(path) - 1 and
				_distance_2d(current, path[lookahead]) < reach_radius and
				_distance_2d(path[lookahead], goal) > reach_radius):
			# A bounded A* may return a safe partial path. Reaching that endpoint
			# means "continue planning from here", not "the strategic goal is
			# complete" and not "wait four seconds until stall recovery".
			continue_key = (('continue', bot_id, self.grid.cell_for(current)) +
			                tuple(path_key))
			next_key, continued = self._path(
				continue_key, current, goal, now, avoid_points)
			if continued:
				path = continued
				state['path_key'] = next_key
				next_index = 0
				for candidate in range(1, min(len(path), 3)):
					if (self.grid.segment_penalty(current, path[candidate], now) <= 0.0 and
							self.grid.segment_clear(current, path[candidate])):
						next_index = candidate
					else:
						break
				state['index'] = next_index
				state['last_target'] = tuple(path[next_index])
				return tuple(path[next_index])
			if continued is None:
				return self._fallback_target(
					bot_id, current, goal, now, avoid_points, state, False)
			return self._fallback_target(
				bot_id, current, goal, now, avoid_points, state, True)
		selected = tuple(path[lookahead])
		if (_distance_2d(current, selected) <= 0.5 and
				_distance_2d(current, goal) > 15.0):
			# A cached path whose first usable edge has become blocked must not park
			# the hull on its own position until the four-second stall timer fires.
			return self._fallback_target(
				bot_id, current, goal, now, avoid_points, state, True)
		state['index'] = lookahead
		state['last_target'] = selected
		self._set_fallback_mode(bot_id, None)
		return selected

	@staticmethod
	def navigation_paused(current, requested_goal, selected_target,
			minimum_request_distance=15.0, hold_radius=0.5):
		"""True when pathfinding intentionally returned the current position."""
		return (_distance_2d(current, requested_goal) > float(minimum_request_distance) and
		        _distance_2d(current, selected_target) <= float(hold_radius))
