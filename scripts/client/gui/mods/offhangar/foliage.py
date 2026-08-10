# -*- coding: utf-8 -*-
"""Pair-specific concealment from prebaked World of Tanks vegetation.

The module is engine-free and Python 2.6 compatible.  Map resources are baked
into oriented foliage volumes; runtime spotting only visits spatial cells
crossed by the observer-to-target segment.
"""

import math


FOLIAGE_CAMOUFLAGE_PER_VOLUME = 0.15
FOLIAGE_CAMOUFLAGE_LIMIT = 0.60
FIRE_TRANSPARENCY_DISTANCE = 15.0
OBSERVER_EYE_HEIGHT = 2.0
TARGET_CHECK_HEIGHT = 1.5


def _segment_cells(start, end, cell_size):
	"""Return a cheap super-sampled grid traversal for one sight segment."""
	cell_size = max(1.0, float(cell_size))
	dx = float(end[0]) - float(start[0])
	dz = float(end[2]) - float(start[2])
	steps = max(1, int(math.ceil(max(abs(dx), abs(dz)) / cell_size * 2.0)))
	result = []
	seen = set()
	for index in range(steps + 1):
		fraction = float(index) / float(steps)
		cell = (int(math.floor((float(start[0]) + dx * fraction) / cell_size)),
		        int(math.floor((float(start[2]) + dz * fraction) / cell_size)))
		if cell not in seen:
			seen.add(cell)
			result.append(cell)
	return result


def _slab_interval(origin, delta, minimum, maximum, low, high):
	if abs(delta) <= 1e-9:
		if origin < minimum or origin > maximum:
			return None
		return low, high
	first = (minimum - origin) / delta
	second = (maximum - origin) / delta
	if first > second:
		first, second = second, first
	low = max(low, first)
	high = min(high, second)
	if low > high:
		return None
	return low, high


def _intersects(instance, start, end):
	"""Test one 3-D sight segment against an oriented foliage box.

	Rows store the inverse horizontal transform, avoiding a matrix inversion in
	the 2 Hz spotting loop:
	[cx, min_y, cz, max_y, i00, i01, i10, i11, strength, radius].
	"""
	dx0 = float(start[0]) - float(instance[0])
	dz0 = float(start[2]) - float(instance[2])
	dx1 = float(end[0]) - float(instance[0])
	dz1 = float(end[2]) - float(instance[2])
	u0 = float(instance[4]) * dx0 + float(instance[5]) * dz0
	v0 = float(instance[6]) * dx0 + float(instance[7]) * dz0
	u1 = float(instance[4]) * dx1 + float(instance[5]) * dz1
	v1 = float(instance[6]) * dx1 + float(instance[7]) * dz1
	interval = _slab_interval(u0, u1 - u0, -1.0, 1.0, 0.0, 1.0)
	if interval is None:
		return False
	interval = _slab_interval(v0, v1 - v0, -1.0, 1.0,
	                         interval[0], interval[1])
	if interval is None:
		return False
	middle = (interval[0] + interval[1]) * 0.5
	y = float(start[1]) + (float(end[1]) - float(start[1])) * middle
	return float(instance[1]) <= y <= float(instance[3])


class FoliageMap(object):
	"""Validated spatial foliage index for one arena."""

	def __init__(self, data):
		self.map_name = str(data.get('map') or '')
		self.cell_size = max(1.0, float(data.get('cell_size', 32.0)))
		self.instances = tuple(data.get('instances') or ())
		self.cells = {}
		for key, values in (data.get('cells') or {}).items():
			parts = str(key).split(',', 1)
			if len(parts) == 2:
				self.cells[(int(parts[0]), int(parts[1]))] = tuple(values)

	def camouflage_bonus(self, observer, target, fired_recently=False):
		"""Return additive camouflage for this observer-target pair."""
		start = (float(observer[0]), float(observer[1]) + OBSERVER_EYE_HEIGHT,
		         float(observer[2]))
		end = (float(target[0]), float(target[1]) + TARGET_CHECK_HEIGHT,
		       float(target[2]))
		candidate_ids = []
		seen = set()
		for cell_x, cell_z in _segment_cells(start, end, self.cell_size):
			for instance_id in self.cells.get((cell_x, cell_z), ()):
				instance_id = int(instance_id)
				if instance_id not in seen:
					seen.add(instance_id)
					candidate_ids.append(instance_id)
		bonus = 0.0
		for instance_id in candidate_ids:
			if instance_id < 0 or instance_id >= len(self.instances):
				continue
			instance = self.instances[instance_id]
			if fired_recently:
				dx = float(target[0]) - float(instance[0])
				dz = float(target[2]) - float(instance[2])
				if math.sqrt(dx * dx + dz * dz) <= (
						FIRE_TRANSPARENCY_DISTANCE + float(instance[9])):
					continue
			if _intersects(instance, start, end):
				bonus += float(instance[8])
				if bonus >= FOLIAGE_CAMOUFLAGE_LIMIT:
					return FOLIAGE_CAMOUFLAGE_LIMIT
		return min(FOLIAGE_CAMOUFLAGE_LIMIT, max(0.0, bonus))
