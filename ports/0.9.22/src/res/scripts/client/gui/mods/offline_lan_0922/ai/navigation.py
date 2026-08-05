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


SQRT_TWO = math.sqrt(2.0)


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
			return True

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
				pass
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
		expansions = 0
		while frontier and expansions < int(max_expansions):
			_unused_priority, _unused_sequence, current = heapq.heappop(frontier)
			expansions += 1
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
					heuristic = math.sqrt(dx * dx + dz * dz) * self.cell_size * 1.35
					sequence += 1
					heapq.heappush(frontier,
					               (new_cost + heuristic, sequence, next_cell))
			yield None
		if reached is None:
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
		self.bot_states = {}
		self.search_frame = None
		self.search_budget = 0
		self.search_budget_per_frame = 24
		self.search_budget_per_path = 4

	def _cache_key(self, path_key, goal):
		return (tuple(path_key), self.grid.cell_for(goal))

	def _trim_cache(self, now):
		if len(self.paths) <= 96:
			return
		ordered = sorted(self.path_times.items(), key=lambda item: item[1])
		for key, _timestamp in ordered[:len(ordered) - 80]:
			self.paths.pop(key, None)
			self.path_times.pop(key, None)

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
				if float(now) - self.path_times.get(key, 0.0) < 3.0:
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
			search = self.grid.begin_plan(
				start, goal, avoid_points=avoid_points, now=now)
			self.searches[key] = search
		frame = int(float(now) * 30.0 + 0.5)
		if frame != self.search_frame:
			self.search_frame = frame
			self.search_budget = self.search_budget_per_frame
		if search.last_frame != frame and self.search_budget > 0:
			budget = min(self.search_budget_per_path, self.search_budget)
			search.step(budget)
			search.last_frame = frame
			self.search_budget -= budget
		if not search.done:
			return key, None
		path = search.result or ()
		self.searches.pop(key, None)
		self.paths[key] = path
		self.path_times[key] = float(now)
		self._trim_cache(now)
		return key, path

	def next_target(self, bot_id, current, goal, path_key, now,
			anchor=None, avoid_points=None):
		"""Return a terrain-safe local target, holding if no safe path is ready."""
		bot_id = int(bot_id)
		self.grid.prune_failed_edges(now)
		self.grid.trim_caches()
		state = self.bot_states.get(bot_id)
		if state is None:
			state = {'last_position': tuple(current), 'progress_time': float(now),
			         'path_key': None, 'index': 0, 'recovery': 0,
			         'recovery_until': 0.0, 'recovery_key': None,
			         'recovery_start': None}
			self.bot_states[bot_id] = state
		if _distance_2d(current, state['last_position']) >= 2.0:
			state['last_position'] = tuple(current)
			state['progress_time'] = float(now)
			state['recovery'] = 0
			state['recovery_until'] = 0.0
		stalled = (state.get('path_key') is not None and
		           float(now) - state['progress_time'] >= 4.0)
		plan_start = tuple(anchor or current)
		effective_key = tuple(path_key)
		if stalled:
			state['recovery'] = int(state.get('recovery', 0)) + 1
			failed_target = state.get('last_target')
			if failed_target is not None:
				self.grid.remember_failed_segment(
					current, failed_target, now,
					ttl=14.0 + min(18.0, state['recovery'] * 3.0))
			state['recovery_key'] = ('recovery', bot_id, state['recovery'],
			                         self.grid.cell_for(current))
			state['recovery_start'] = tuple(current)
			state['recovery_until'] = float(now) + 6.0
			state['progress_time'] = float(now)
		recovering = (state.get('recovery_key') is not None and
		              float(now) < float(state.get('recovery_until', 0.0)))
		if recovering:
			effective_key = state['recovery_key']
			plan_start = state['recovery_start']
		key, path = self._path(effective_key, plan_start, goal, now,
		                       avoid_points if recovering else None)
		if not path:
			if (self.grid.segment_penalty(current, goal, now) <= 0.0 and
					self.grid.segment_clear(current, goal)):
				state['last_target'] = tuple(goal)
				return tuple(goal)
			state['last_target'] = tuple(current)
			return tuple(current)
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
			if not joined_path:
				state['last_target'] = tuple(current)
				return tuple(current)
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
		state['index'] = lookahead
		state['last_target'] = tuple(path[lookahead])
		return tuple(path[lookahead])
