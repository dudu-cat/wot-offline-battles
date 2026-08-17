"""Pure-data world backends for the server-hosted 0.9.22 battle authority.

Answers the world queries that battle_runtime answers with native BigWorld
rays on the #1513 client, from the shipped baked navigation graphs,
destructible catalogs and foliage maps. Every method keeps the client
contract: same result shapes, and failure always lands on the conservative
side (unknown terrain is never a clear corridor).
"""

import math
import os
import sys


_PORT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_CLIENT_SCRIPT_ROOT = os.path.join(
    _PORT_ROOT, 'src', 'res', 'scripts', 'client')
if _CLIENT_SCRIPT_ROOT not in sys.path:
    sys.path.insert(0, _CLIENT_SCRIPT_ROOT)

from gui.mods.offline_lan_0922 import prebaked_destructibles
from gui.mods.offline_lan_0922 import prebaked_foliage
from gui.mods.offline_lan_0922 import prebaked_navigation
from gui.mods.offline_lan_0922 import vehicle_physics
from gui.mods.offline_lan_0922.ai import cover as bot_cover
from gui.mods.offline_lan_0922.ai import planner as bot_planner


HAZARD_WATER = 1
HAZARD_EDGE = 2
HAZARD_SHALLOW_WATER = 4

# Neighbour order matches ai.navigation.TerrainGrid._NEIGHBOURS and the
# baked links bitmask.
_NEIGHBOURS = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
)
_NEIGHBOUR_BITS = dict(
    (offset, index) for index, offset in enumerate(_NEIGHBOURS))


def default_data_dir():
    """Resolve the baked-data root: env override, then the port tree."""
    override = os.environ.get('WOT_0922_SERVER_DATA')
    if override:
        return override
    return _PORT_ROOT


def load_world(map_name, base_dir=None):
    """Return a BakedWorld for one supported map, or None when not baked."""
    base_dir = base_dir if base_dir is not None else default_data_dir()
    graph = prebaked_navigation.load_graph(map_name, base_dir=base_dir)
    if graph is None:
        return None
    catalog = prebaked_destructibles.load_catalog(map_name, base_dir=base_dir)
    foliage = prebaked_foliage.load_foliage(map_name, base_dir=base_dir)
    return BakedWorld(graph, catalog=catalog, foliage=foliage)


class BakedWorld(object):
    """One map's static world, answered purely from versioned baked data."""

    def __init__(self, graph, catalog=None, foliage=None):
        prebaked_navigation._validate(graph, graph.get('map'))
        self.graph = graph
        self.catalog = catalog
        self.foliage = foliage
        self._origin = (float(graph['origin'][0]), float(graph['origin'][1]))
        self._cell_size = float(graph['cell_size'])
        self._width = int(graph['width'])
        self._height = int(graph['height'])
        self._heights = graph['heights_mm']
        self._links = graph['links']
        self._hazards = graph.get('hazards') or (0,) * (
            self._width * self._height)
        self._structure_bins = {}
        self._structure_boxes = []
        if catalog is not None:
            self._index_structures(catalog)

    # -- grid primitives ---------------------------------------------------

    def _cell_for(self, x, z):
        return (
            int(math.floor((float(x) - self._origin[0]) /
                           self._cell_size + 0.5)),
            int(math.floor((float(z) - self._origin[1]) /
                           self._cell_size + 0.5)))

    def _index(self, cell):
        column, row = cell
        if (column < 0 or column >= self._width or
                row < 0 or row >= self._height):
            return None
        return row * self._width + column

    def _cell_height(self, cell):
        index = self._index(cell)
        if index is None or self._heights[index] is None:
            return None
        return float(self._heights[index]) / 1000.0

    def _cell_hazard(self, cell):
        index = self._index(cell)
        if index is None:
            return 0
        return int(self._hazards[index])

    def _edge_linked(self, cell, next_cell):
        index = self._index(cell)
        if index is None or self._index(next_cell) is None:
            return False
        offset = (next_cell[0] - cell[0], next_cell[1] - cell[1])
        bit = _NEIGHBOUR_BITS.get(offset)
        if bit is None:
            return False
        return bool(int(self._links[index]) & (1 << bit))

    def _nearest_baked_cell(self, cell, max_radius):
        if self._cell_height(cell) is not None:
            return cell
        best = None
        best_distance = None
        for radius in range(1, max(0, int(max_radius)) + 1):
            for z in range(cell[1] - radius, cell[1] + radius + 1):
                for x in range(cell[0] - radius, cell[0] + radius + 1):
                    if max(abs(x - cell[0]), abs(z - cell[1])) != radius:
                        continue
                    candidate = (x, z)
                    if self._cell_height(candidate) is None:
                        continue
                    distance = ((x - cell[0]) ** 2 + (z - cell[1]) ** 2)
                    if best_distance is None or distance < best_distance:
                        best = candidate
                        best_distance = distance
            if best is not None:
                return best
        return None

    def _segment_cells(self, start, end):
        """Cells crossed by a straight 2D segment, start cell included."""
        cell = self._nearest_baked_cell(
            self._cell_for(start[0], start[2]), 2)
        if cell is None:
            return []
        target = self._cell_for(end[0], end[2])
        x, z = cell
        target_x, target_z = target
        cells = [(x, z)]
        dx = abs(target_x - x)
        dz = abs(target_z - z)
        step_x = 1 if x < target_x else -1
        step_z = 1 if z < target_z else -1
        error = dx - dz
        while x != target_x or z != target_z:
            double_error = error * 2
            if double_error > -dz:
                error -= dz
                x += step_x
            if double_error < dx:
                error += dx
                z += step_z
            cells.append((x, z))
        return cells

    def ground_height(self, x, z):
        """Bilinear ground height over baked cell centres, in metres."""
        u = (float(x) - self._origin[0]) / self._cell_size
        v = (float(z) - self._origin[1]) / self._cell_size
        column = int(math.floor(u))
        row = int(math.floor(v))
        fraction_u = u - column
        fraction_v = v - row
        corners = (
            ((column, row), (1.0 - fraction_u) * (1.0 - fraction_v)),
            ((column + 1, row), fraction_u * (1.0 - fraction_v)),
            ((column, row + 1), (1.0 - fraction_u) * fraction_v),
            ((column + 1, row + 1), fraction_u * fraction_v),
        )
        total = 0.0
        weight_sum = 0.0
        for cell, weight in corners:
            height = self._cell_height(cell)
            if height is None or weight <= 0.0:
                continue
            total += height * weight
            weight_sum += weight
        if weight_sum < 0.25:
            # The pose is mostly over unbaked cells (building footprints,
            # out-of-graph ground); refuse rather than guess.
            return None
        return total / weight_sum

    # -- battle_runtime probe contracts -------------------------------------

    def ground_y(self, x, z, hint=0.0, allow_wide=False):
        """Near-hull ground: reject heights outside the client hint window."""
        height = self.ground_height(x, z)
        if height is None:
            return None
        if -14.0 < height - float(hint) < 6.0:
            return height
        return height if allow_wide else None

    def navigation_ground(self, x, z, hint_y=0.0):
        """Same-layer graph ground, refusing deep water like the client."""
        height = self.ground_height(x, z)
        if height is None or height > float(hint_y) + 4.5:
            return None
        if self.water_depth((x, height, z)) > 1.0:
            return None
        return height

    def water_depth(self, point):
        """Depth classes from baked hazards: deep > 1.0 m, ford <= 1.0 m."""
        hazard = self._cell_hazard(self._cell_for(point[0], point[2]))
        if hazard & HAZARD_WATER:
            return 2.0
        if hazard & HAZARD_SHALLOW_WATER:
            return 0.5
        return -1.0

    def navigation_obstacle(self, start, end, half_width):
        """Three-lane baked corridor sweep; True means blocked."""
        dx = float(end[0]) - float(start[0])
        dz = float(end[2]) - float(start[2])
        length = math.sqrt(dx * dx + dz * dz)
        if length < 0.1:
            return False
        lateral_x, lateral_z = dz / length, -dx / length
        for offset in (-float(half_width), 0.0, float(half_width)):
            lane_start = (float(start[0]) + lateral_x * offset, 0.0,
                          float(start[2]) + lateral_z * offset)
            lane_end = (float(end[0]) + lateral_x * offset, 0.0,
                        float(end[2]) + lateral_z * offset)
            if self._lane_blocked(lane_start, lane_end):
                return True
        return False

    def _lane_blocked(self, start, end):
        cells = self._segment_cells(start, end)
        if not cells:
            return True
        for cell, next_cell in zip(cells, cells[1:]):
            if not self._edge_linked(cell, next_cell):
                return True
        return False

    def direction_probe(self, position, yaw, speed=0.0, descriptor=None):
        """Baked twin of the dual-height three-lane hull corridor probe."""
        x, y, z = (float(position[0]), float(position[1]),
                   float(position[2]))
        far_distance = 20.0 if abs(float(speed or 0.0)) > 5.0 else 15.0
        previous_y = y
        previous_distance = 0.0
        sine = math.sin(float(yaw))
        cosine = math.cos(float(yaw))
        lateral_x = cosine
        lateral_z = -sine
        maximum_slope = 0.0
        if descriptor is not None:
            try:
                vehicle_physics.derive_params(descriptor)
            except (AttributeError, KeyError, TypeError, ValueError):
                raise RuntimeError(
                    'bot destructible planning speed is unavailable')
        for unused_height, distance in ((0.7, 8.0), (1.5, far_distance)):
            nx = x + sine * distance
            nz = z + cosine * distance
            run = distance - previous_distance
            next_y = self.ground_height(nx, nz)
            if next_y is None:
                return {'clear': False, 'collision': False,
                        'water': False, 'slope': 99.0}
            if self.water_depth((nx, next_y, nz)) > 1.0:
                return {'clear': False, 'collision': False,
                        'water': True, 'slope': 0.0}
            delta = next_y - previous_y
            slope = delta / max(0.1, run)
            maximum_slope = max(maximum_slope, abs(slope))
            if delta > run * 0.48 or delta < -run * 0.38:
                return {'clear': False, 'collision': False,
                        'water': False, 'slope': slope}
            for offset in (-2.2, 0.0, 2.2):
                lane_start = (x + lateral_x * offset, y,
                              z + lateral_z * offset)
                lane_end = (nx + lateral_x * offset, next_y,
                            nz + lateral_z * offset)
                if self._lane_blocked(lane_start, lane_end):
                    return {'clear': False, 'collision': True,
                            'water': False, 'slope': slope}
            previous_y = next_y
            previous_distance = distance
        return {'clear': True, 'collision': False,
                'water': False, 'slope': maximum_slope}

    def world_receipt(self, position, travel_yaw, signed_speed, descriptor):
        """Baked twin of the exact 3x3 hull corridor containment proof."""
        try:
            hull_bbox = _descriptor_hull_bbox(descriptor)
            minimum, maximum = hull_bbox[:2]
            half_width = max(
                abs(float(minimum[0])), abs(float(maximum[0]))) - 0.1
            leading = (-float(minimum[2]) if signed_speed < 0.0 else
                       float(maximum[2]))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return None
        if half_width <= 0.0 or leading <= 0.0:
            return None
        x, y, z = (float(position[0]), float(position[1]),
                   float(position[2]))
        sine = math.sin(float(travel_yaw))
        cosine = math.cos(float(travel_yaw))
        lateral_x = cosine
        lateral_z = -sine
        proof_distance = 15.0
        for offset in (-half_width, 0.0, half_width):
            lane_start = (x + lateral_x * offset - sine * 0.5, y,
                          z + lateral_z * offset - cosine * 0.5)
            lane_end = (x + lateral_x * offset + sine * proof_distance, y,
                        z + lateral_z * offset + cosine * proof_distance)
            if self._lane_blocked(lane_start, lane_end):
                return False
        return {
            'distance': proof_distance,
            'half_width': half_width,
            'leading': leading,
            'origin': (x, y, z),
            'yaw': float(travel_yaw),
            'direction': (-1 if signed_speed < 0.0 else 1),
        }


    # -- static occluders from the destructible catalog ---------------------

    _STRUCTURE_BIN_SIZE = 16.0

    def _index_structures(self, catalog):
        """Build world OBBs for solid structures from quantized locators."""
        quantization = float(catalog.get('locator_quantization') or 1000)
        resources = catalog.get('resources') or {}
        for row in catalog.get('instances') or ():
            filename = row[12]
            record = resources.get(filename) or {}
            if record.get('kind') != 'structure':
                continue
            transform = [float(value) / quantization for value in row[:12]]
            origin = transform[0:3]
            basis = (transform[3:6], transform[6:9], transform[9:12])
            for box in record.get('boxes') or ():
                world_box = _world_obb(origin, basis, box)
                if world_box is None:
                    continue
                index = len(self._structure_boxes)
                self._structure_boxes.append(world_box)
                for key in _obb_bin_keys(world_box,
                                         self._STRUCTURE_BIN_SIZE):
                    self._structure_bins.setdefault(key, []).append(index)

    def _structure_hit(self, start, end):
        """Nearest structure-OBB hit fraction on a segment, or None."""
        if not self._structure_boxes:
            return None
        candidates = set()
        for key in _segment_bin_keys(start, end, self._STRUCTURE_BIN_SIZE):
            candidates.update(self._structure_bins.get(key, ()))
        nearest = None
        for index in candidates:
            fraction = _segment_obb_entry(
                start, end, self._structure_boxes[index])
            if fraction is not None and (nearest is None or
                                         fraction < nearest):
                nearest = fraction
        return nearest

    def _terrain_hit(self, start, end):
        """First fraction where the segment dips under baked ground."""
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
        dz = float(end[2]) - float(start[2])
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        steps = max(1, int(math.ceil(length / (self._cell_size * 0.5))))
        previous_fraction = 0.0
        previous_clearance = None
        for index in range(1, steps + 1):
            fraction = float(index) / float(steps)
            x = float(start[0]) + dx * fraction
            y = float(start[1]) + dy * fraction
            z = float(start[2]) + dz * fraction
            ground = self.ground_height(x, z)
            if ground is None:
                previous_fraction = fraction
                previous_clearance = None
                continue
            clearance = y - ground
            if clearance < 0.0:
                if previous_clearance is not None and previous_clearance > 0.0:
                    crossing = previous_clearance / (
                        previous_clearance - clearance)
                    return previous_fraction + (
                        fraction - previous_fraction) * crossing
                return fraction
            previous_fraction = fraction
            previous_clearance = clearance
        return None

    def segment_hit(self, start, end):
        """World hit point on a segment, or None when the air path is clear.

        Terrain comes from the baked height field; solid occluders are the
        catalog's structure boxes. Falling and fragile items never block,
        matching the navigation bake that excludes them from links.
        """
        fractions = [value for value in (
            self._terrain_hit(start, end),
            self._structure_hit(start, end)) if value is not None]
        if not fractions:
            return None
        fraction = min(fractions)
        return (float(start[0]) + (float(end[0]) - float(start[0])) * fraction,
                float(start[1]) + (float(end[1]) - float(start[1])) * fraction,
                float(start[2]) + (float(end[2]) - float(start[2])) * fraction)

    # -- visibility, firing lanes, cover -------------------------------------

    def visibility(self, source, target, fired_recently=False):
        """Baked twin of the client's bot visibility probe."""
        source_position = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        start = (source_position[0], source_position[1] + 2.0,
                 source_position[2])
        end = (target_position[0], target_position[1] + 1.5,
               target_position[2])
        hit = self.segment_hit(start, end)
        line_of_sight = bool(
            hit is None or
            _distance_3d(hit, start) + 1.5 >= _distance_3d(end, start))
        foliage_bonus = 0.0
        if line_of_sight and self.foliage is not None:
            foliage_bonus = self.foliage.camouflage_bonus(
                source_position, target_position, fired_recently)
        return {
            'line_of_sight': line_of_sight,
            'foliage_bonus': foliage_bonus,
        }

    def firing_lane(self, source, target):
        """Baked twin of the non-SPG firing lane probe."""
        source_position = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        dx = target_position[0] - source_position[0]
        dz = target_position[2] - source_position[2]
        distance = math.sqrt(dx * dx + dz * dz)
        clearance = min(4.0, max(0.0, (distance - 0.75) * 0.5))
        for target_height in (1.5, 2.2):
            segment = bot_planner.trimmed_sight_segment(
                source_position, target_position, 2.5, target_height,
                clearance, clearance)
            if segment is None:
                return False
            if not segment:
                return False
            start, end = segment
            if self.segment_hit(start, end) is None:
                return True
        return False

    def has_los(self, observer, target):
        start = (observer[0], observer[1] + 2.5, observer[2])
        for height in (1.5, 2.2):
            end = (target[0], target[1] + height, target[2])
            if self.segment_hit(start, end) is None:
                return True
        return False

    def arc_probe(self, start, end):
        """Artillery chord probe: hit point, or None for one clear chord."""
        return self.segment_hit(start, end)

    def cover_slope(self, point):
        maximum = 0.0
        for offset_x, offset_z in ((2.5, 0.0), (-2.5, 0.0),
                                   (0.0, 2.5), (0.0, -2.5)):
            height = self.ground_y(
                point[0] + offset_x, point[2] + offset_z, point[1])
            if height is None:
                return 90.0
            maximum = max(maximum, math.degrees(math.atan2(
                abs(height - point[1]), 2.5)))
        return maximum

    def sample_cover(self, source, target, route_position,
                     ally_positions, segment_clear):
        """Baked twin of the four-point cover fan probe."""
        current = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        dx = current[0] - float(target_position[0])
        dz = current[2] - float(target_position[2])
        length = math.sqrt(dx * dx + dz * dz)
        if length < 2.0 or not callable(segment_clear):
            return ()
        away_x, away_z = dx / length, dz / length
        right_x, right_z = away_z, -away_x
        route = _xyz(route_position)
        route_dx, route_dz = route[0] - current[0], route[2] - current[2]
        route_length = math.sqrt(route_dx * route_dx + route_dz * route_dz)
        candidates = []
        for away, lateral in ((0.0, 0.0), (14.0, 0.0),
                              (10.0, 13.0), (10.0, -13.0)):
            x = current[0] + away_x * away + right_x * lateral
            z = current[2] + away_z * away + right_z * lateral
            ground = self.ground_y(x, z, current[1])
            if ground is None:
                continue
            point = (x, ground, z)
            water_depth = self.water_depth(point)
            if water_depth > 1.0 or not segment_clear(current, point):
                continue
            occluded = not self.has_los(point, target_position)
            if not occluded:
                continue
            slope = self.cover_slope(point)
            if slope > 24.0:
                continue
            peek = None
            for side in (-1.0, 1.0):
                peek_x = point[0] + right_x * side * 6.5 - away_x * 2.0
                peek_z = point[2] + right_z * side * 6.5 - away_z * 2.0
                peek_y = self.ground_y(peek_x, peek_z, point[1])
                if peek_y is None:
                    continue
                peek_point = (peek_x, peek_y, peek_z)
                if (self.water_depth(peek_point) <= 1.0 and
                        segment_clear(point, peek_point) and
                        self.has_los(peek_point, target_position)):
                    peek = peek_point
                    break
            move_dx, move_dz = point[0] - current[0], point[2] - current[2]
            move_length = math.sqrt(move_dx * move_dx + move_dz * move_dz)
            alignment = 0.5
            if move_length > 0.1 and route_length > 0.1:
                dot = ((move_dx / move_length) * (route_dx / route_length) +
                       (move_dz / move_length) * (route_dz / route_length))
                alignment = max(0.0, min(1.0, (dot + 1.0) * 0.5))
            nearby = sum(1 for ally in (ally_positions or ())
                         if 0.5 < _distance_2d(point, ally) < 13.0)
            candidate = {
                'id': '%s:%d:%d' % (
                    source.get('id'), int(round(point[0] / 4.0)),
                    int(round(point[2] / 4.0))),
                'position': point,
                'travel_distance': _distance_2d(point, current),
                'route_alignment': alignment,
                'enemy_occlusion': 1.0,
                'exposure': 0.12,
                'slope': slope,
                'water': max(0.0, min(1.0, water_depth)),
                'ally_congestion': max(0.0, min(1.0, nearby / 3.0)),
                'peek_feasible': peek is not None,
                'escape_feasible': True,
            }
            if peek is not None:
                candidate['peek_position'] = peek
            candidates.append(candidate)
        ranked = bot_cover.score_candidates(candidates)
        for candidate in ranked:
            for key in ('breakdown', 'reasons', 'rank', 'score'):
                candidate.pop(key, None)
        return tuple(ranked)


def _xyz(value):
    if isinstance(value, dict):
        if 'position' in value:
            return _xyz(value['position'])
        return (float(value.get('x', 0.0)), float(value.get('y', 0.0)),
                float(value.get('z', 0.0)))
    return (float(value[0]), float(value[1]), float(value[2]))


def _distance_2d(first, second):
    dx = float(first[0]) - float(second[0])
    dz = float(first[2]) - float(second[2])
    return math.sqrt(dx * dx + dz * dz)


def _distance_3d(first, second):
    dx = float(first[0]) - float(second[0])
    dy = float(first[1]) - float(second[1])
    dz = float(first[2]) - float(second[2])
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _world_obb(origin, basis, box):
    """World (center, half_axes, mat_kind) from a local AABB and transform."""
    local_center = ((box[0] + box[3]) * 0.5, (box[1] + box[4]) * 0.5,
                    (box[2] + box[5]) * 0.5)
    center = tuple(
        origin[axis] +
        basis[0][axis] * local_center[0] +
        basis[1][axis] * local_center[1] +
        basis[2][axis] * local_center[2]
        for axis in range(3))
    half_axes = tuple(
        tuple(basis[row][axis] * (box[row + 3] - box[row]) * 0.5
              for axis in range(3))
        for row in range(3))
    volume = abs(
        half_axes[0][0] * (half_axes[1][1] * half_axes[2][2] -
                           half_axes[1][2] * half_axes[2][1]) -
        half_axes[0][1] * (half_axes[1][0] * half_axes[2][2] -
                           half_axes[1][2] * half_axes[2][0]) +
        half_axes[0][2] * (half_axes[1][0] * half_axes[2][1] -
                           half_axes[1][1] * half_axes[2][0]))
    if volume <= 1.0e-9:
        return None
    return (center, half_axes, box[6] if len(box) > 6 else None)


def _obb_radius(world_box, axis):
    return sum(abs(half[axis]) for half in world_box[1])


def _obb_bin_keys(world_box, bin_size):
    center = world_box[0]
    radius_x = _obb_radius(world_box, 0)
    radius_z = _obb_radius(world_box, 2)
    min_x = int(math.floor((center[0] - radius_x) / bin_size))
    max_x = int(math.floor((center[0] + radius_x) / bin_size))
    min_z = int(math.floor((center[2] - radius_z) / bin_size))
    max_z = int(math.floor((center[2] + radius_z) / bin_size))
    return [(x, z) for x in range(min_x, max_x + 1)
            for z in range(min_z, max_z + 1)]


def _segment_bin_keys(start, end, bin_size):
    min_x = int(math.floor(min(start[0], end[0]) / bin_size))
    max_x = int(math.floor(max(start[0], end[0]) / bin_size))
    min_z = int(math.floor(min(start[2], end[2]) / bin_size))
    max_z = int(math.floor(max(start[2], end[2]) / bin_size))
    return [(x, z) for x in range(min_x, max_x + 1)
            for z in range(min_z, max_z + 1)]


def _segment_obb_entry(start, end, world_box):
    """Entry fraction of a segment into one OBB via the slab test."""
    center, half_axes, unused_kind = world_box
    lengths = []
    axes = []
    for half in half_axes:
        length = math.sqrt(half[0] * half[0] + half[1] * half[1] +
                           half[2] * half[2])
        if length <= 1.0e-9:
            return None
        axes.append((half[0] / length, half[1] / length, half[2] / length))
        lengths.append(length)
    delta = (float(start[0]) - center[0], float(start[1]) - center[1],
             float(start[2]) - center[2])
    direction = (float(end[0]) - float(start[0]),
                 float(end[1]) - float(start[1]),
                 float(end[2]) - float(start[2]))
    enter = 0.0
    exit_ = 1.0
    for axis, length in zip(axes, lengths):
        origin = (delta[0] * axis[0] + delta[1] * axis[1] +
                  delta[2] * axis[2])
        speed = (direction[0] * axis[0] + direction[1] * axis[1] +
                 direction[2] * axis[2])
        if abs(speed) < 1.0e-9:
            if abs(origin) > length:
                return None
            continue
        low = (-length - origin) / speed
        high = (length - origin) / speed
        if low > high:
            low, high = high, low
        enter = max(enter, low)
        exit_ = min(exit_, high)
        if enter > exit_:
            return None
    return enter


def _descriptor_hull_bbox(descriptor):
    """Hull bounding box from a descriptor or a donated projection."""
    hull = getattr(descriptor, 'hull', None)
    if hull is None and isinstance(descriptor, dict):
        hull = descriptor.get('hull')
    if isinstance(hull, dict):
        tester = hull.get('hitTester')
    else:
        tester = getattr(hull, 'hitTester', None)
    bbox = getattr(tester, 'bbox', None)
    if bbox is None and isinstance(tester, dict):
        bbox = tester.get('bbox')
    if bbox is None:
        raise ValueError('descriptor hull bbox is unavailable')
    return bbox
