import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
sys.path.insert(0, str(PORT_ROOT / 'server'))

import server_world


def _graph(width=6, height=6, cell_size=4.0, origin=(0.0, 0.0),
           heights=None, links=None, hazards=None):
    count = width * height
    if heights is None:
        heights = [0] * count
    if links is None:
        links = [0xff] * count
    if hazards is None:
        hazards = [0] * count
    span = (width - 1) * cell_size
    waypoints = ((0.0, 0.0, False), (span, 0.0, False))
    return {
        'format': 'offline-lan-0922-navgraph', 'version': 2,
        'game_version': '0.9.22.0.1-cn-1513', 'map': '01_karelia',
        'cell_size': cell_size, 'origin': list(origin),
        'bounds': (0, 0, int(span), int(span)),
        'width': width, 'height': height,
        'heights_mm': list(heights),
        'links': list(links),
        'hazards': list(hazards),
        'spawn_anchors': ((0.0, 0.0), (span, span)),
        'objective_bases': ((span, span), (0.0, 0.0)),
        'spawn_formations': {
            '1': tuple((float(slot % 5) * 12.0, 0.0,
                        -60.0 - float(slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple((float(slot % 5) * 12.0, 0.0,
                        span + 60.0 + float(slot // 5) * 12.0, 3.14159)
                       for slot in range(15)),
        },
        'routes': {
            '1': ({'id': 'safe-1', 'waypoints': waypoints},),
            '2': ({'id': 'safe-2',
                   'waypoints': tuple(reversed(waypoints))},),
        },
        'bake': {'max_grade': 0.30},
    }


def _world(**kwargs):
    return server_world.BakedWorld(_graph(**kwargs))


class GroundHeightTest(unittest.TestCase):
    def test_flat_ground_interpolates_to_cell_height(self):
        world = _world(heights=[2000] * 36)
        self.assertAlmostEqual(world.ground_height(6.0, 6.0), 2.0)

    def test_bilinear_between_two_heights(self):
        heights = [0] * 36
        for row in range(6):
            for column in range(3, 6):
                heights[row * 6 + column] = 4000
        world = _world(heights=heights)
        self.assertAlmostEqual(world.ground_height(0.0, 8.0), 0.0)
        self.assertAlmostEqual(world.ground_height(20.0, 8.0), 4.0)
        self.assertAlmostEqual(world.ground_height(10.0, 8.0), 2.0)

    def test_unbaked_area_returns_none(self):
        heights = [None] * 36
        world = _world(heights=heights)
        self.assertIsNone(world.ground_height(8.0, 8.0))

    def test_outside_grid_returns_none(self):
        world = _world()
        self.assertIsNone(world.ground_height(-500.0, 8.0))


class GroundProbeContractTest(unittest.TestCase):
    def test_ground_y_respects_hint_window(self):
        world = _world(heights=[2000] * 36)
        self.assertAlmostEqual(world.ground_y(8.0, 8.0, hint=2.0), 2.0)
        self.assertIsNone(world.ground_y(8.0, 8.0, hint=30.0))
        self.assertIsNone(world.ground_y(8.0, 8.0, hint=-10.0))
        self.assertAlmostEqual(
            world.ground_y(8.0, 8.0, hint=30.0, allow_wide=True), 2.0)

    def test_navigation_ground_rejects_layer_mismatch_and_deep_water(self):
        world = _world(heights=[2000] * 36)
        self.assertAlmostEqual(world.navigation_ground(8.0, 8.0), 2.0)
        self.assertIsNone(world.navigation_ground(8.0, 8.0, hint_y=-4.0))
        hazards = [0] * 36
        hazards[2 * 6 + 2] = server_world.HAZARD_WATER
        wet = _world(heights=[0] * 36, hazards=hazards)
        self.assertIsNone(wet.navigation_ground(8.0, 8.0))

    def test_water_depth_classes(self):
        hazards = [0] * 36
        hazards[0] = server_world.HAZARD_WATER
        hazards[1] = server_world.HAZARD_SHALLOW_WATER
        world = _world(hazards=hazards)
        self.assertGreater(world.water_depth((0.0, 0.0, 0.0)), 1.0)
        depth = world.water_depth((4.0, 0.0, 0.0))
        self.assertGreater(depth, 0.0)
        self.assertLessEqual(depth, 1.0)
        self.assertLess(world.water_depth((8.0, 0.0, 0.0)), 0.0)


class ObstacleSweepTest(unittest.TestCase):
    def test_linked_corridor_is_clear(self):
        world = _world(width=12, height=12)
        self.assertFalse(world.navigation_obstacle(
            (4.0, 0.0, 24.0), (28.0, 0.0, 24.0), 2.15))

    def test_unlinked_edge_blocks(self):
        links = [0xff] * 36
        for row in range(6):
            links[row * 6 + 2] &= ~(1 << 4)
            links[row * 6 + 3] &= ~(1 << 3)
        world = _world(links=links)
        self.assertTrue(world.navigation_obstacle(
            (0.0, 0.0, 0.0), (20.0, 0.0, 0.0), 2.15))

    def test_short_segment_is_clear(self):
        world = _world()
        self.assertFalse(world.navigation_obstacle(
            (0.0, 0.0, 0.0), (0.05, 0.0, 0.0), 2.15))


class DirectionProbeTest(unittest.TestCase):
    def test_flat_linked_ground_is_clear(self):
        world = _world(width=12, height=12)
        result = world.direction_probe((8.0, 0.0, 8.0), 0.0, 3.0)
        self.assertEqual(result, {
            'clear': True, 'collision': False,
            'water': False, 'slope': 0.0})

    def test_deep_water_ahead_reports_water(self):
        hazards = [0] * 144
        for column in range(12):
            for row in range(4, 12):
                hazards[row * 12 + column] = server_world.HAZARD_WATER
        world = _world(width=12, height=12, hazards=hazards)
        result = world.direction_probe((8.0, 0.0, 8.0), 0.0, 3.0)
        self.assertTrue(result['water'])
        self.assertFalse(result['clear'])

    def test_steep_rise_reports_slope(self):
        heights = [0] * 144
        for column in range(12):
            for row in range(4, 12):
                heights[row * 12 + column] = 30000
        world = _world(width=12, height=12, heights=heights)
        result = world.direction_probe((8.0, 0.0, 8.0), 0.0, 3.0)
        self.assertFalse(result['clear'])
        self.assertFalse(result['collision'])
        self.assertGreater(result['slope'], 0.48)

    def test_unlinked_wall_reports_collision(self):
        links = [0xff] * 144
        for column in range(12):
            links[4 * 12 + column] &= ~((1 << 6) | (1 << 5) | (1 << 7))
            links[5 * 12 + column] &= ~((1 << 1) | (1 << 0) | (1 << 2))
        world = _world(width=12, height=12, links=links)
        result = world.direction_probe((8.0, 0.0, 8.0), 0.0, 3.0)
        self.assertTrue(result['collision'])
        self.assertFalse(result['clear'])

    def test_unbaked_ground_ahead_fails_closed(self):
        heights = [0] * 144
        for column in range(12):
            for row in range(4, 12):
                heights[row * 12 + column] = None
        world = _world(width=12, height=12, heights=heights)
        result = world.direction_probe((8.0, 0.0, 8.0), 0.0, 3.0)
        self.assertFalse(result['clear'])
        self.assertEqual(result['slope'], 99.0)


class _Tester(object):
    def __init__(self, bbox):
        self.bbox = bbox


class _Hull(object):
    def __init__(self, bbox):
        self.hitTester = _Tester(bbox)


class _Descriptor(object):
    def __init__(self, bbox=((-1.5, 0.0, -3.2), (1.5, 2.4, 3.2))):
        self.hull = _Hull(bbox)


class WorldReceiptTest(unittest.TestCase):
    def test_clear_corridor_returns_receipt(self):
        world = _world(width=12, height=12)
        receipt = world.world_receipt((8.0, 0.0, 8.0), 0.0, 4.0,
                                      _Descriptor())
        self.assertIsInstance(receipt, dict)
        self.assertEqual(receipt['distance'], 15.0)
        self.assertAlmostEqual(receipt['half_width'], 1.4)
        self.assertAlmostEqual(receipt['leading'], 3.2)
        self.assertEqual(receipt['direction'], 1)

    def test_reverse_receipt_uses_rear_reach(self):
        world = _world(width=12, height=12)
        receipt = world.world_receipt(
            (8.0, 0.0, 8.0), 0.0, -4.0,
            _Descriptor(bbox=((-1.5, 0.0, -2.0), (1.5, 2.4, 3.2))))
        self.assertAlmostEqual(receipt['leading'], 2.0)
        self.assertEqual(receipt['direction'], -1)

    def test_blocked_corridor_returns_false(self):
        links = [0xff] * 144
        for column in range(12):
            links[4 * 12 + column] &= ~((1 << 6) | (1 << 5) | (1 << 7))
            links[5 * 12 + column] &= ~((1 << 1) | (1 << 0) | (1 << 2))
        world = _world(width=12, height=12, links=links)
        self.assertIs(
            world.world_receipt((8.0, 0.0, 8.0), 0.0, 4.0, _Descriptor()),
            False)

    def test_missing_bbox_returns_none(self):
        world = _world(width=12, height=12)
        self.assertIsNone(
            world.world_receipt((8.0, 0.0, 8.0), 0.0, 4.0, object()))

    def test_donated_projection_bbox_is_accepted(self):
        world = _world(width=12, height=12)
        projection = {'hull': {'hitTester': {
            'bbox': ((-1.2, 0.0, -2.8), (1.2, 2.2, 2.8))}}}
        receipt = world.world_receipt((8.0, 0.0, 8.0), 0.0, 4.0, projection)
        self.assertAlmostEqual(receipt['half_width'], 1.1)


class LoaderTest(unittest.TestCase):
    def test_load_world_reads_repo_data(self):
        world = server_world.load_world('01_karelia',
                                        base_dir=str(PORT_ROOT))
        self.assertIsNotNone(world)
        self.assertEqual(world.graph['map'], '01_karelia')
        anchors = world.graph.get('spawn_anchors') or ()
        self.assertTrue(anchors)
        x, z = anchors[0]
        self.assertIsNotNone(world.ground_height(float(x), float(z)))

    def test_unsupported_map_returns_none(self):
        self.assertIsNone(server_world.load_world(
            'no_such_map', base_dir=str(PORT_ROOT)))

    def test_missing_optional_dataset_disables_only_that_feature(self):
        loaders = (
            (server_world.prebaked_destructibles, 'load_catalog',
             'destructible catalog'),
            (server_world.prebaked_foliage, 'load_foliage', 'foliage data'),
        )
        for owner, name, warning in loaders:
            with self.subTest(dataset=name):
                with mock.patch.object(owner, name, return_value=None):
                    world = server_world.load_world(
                        '01_karelia', base_dir=str(PORT_ROOT))
                    self.assertIsNotNone(world)
                    self.assertTrue(any(
                        line.startswith(warning)
                        for line in world.optional_warnings))
                    if warning == 'destructible catalog':
                        self.assertEqual(
                            'destructible_catalog_unavailable',
                            world.destructibles_disabled_reason())

    def test_optional_loader_exception_is_a_bounded_single_line_warning(self):
        with mock.patch.object(
                server_world.prebaked_destructibles, 'load_catalog',
                side_effect=RuntimeError('catalog failed\n' + 'x' * 300)):
            world = server_world.load_world(
                '01_karelia', base_dir=str(PORT_ROOT))

        self.assertIsNotNone(world)
        warning = next(line for line in world.optional_warnings
                       if line.startswith('destructible catalog'))
        self.assertNotIn('\n', warning)
        self.assertLessEqual(len(warning), 240)

    def test_missing_static_occluders_rejects_the_world(self):
        with mock.patch.object(
                server_world, 'load_occluders', return_value=None):
            self.assertIsNone(server_world.load_world(
                '01_karelia', base_dir=str(PORT_ROOT)))


class SegmentUnknownTerrainTest(unittest.TestCase):
    def test_segment_leaving_the_baked_grid_hits_at_first_outside_sample(self):
        world = _world()
        fraction = world._terrain_hit((8.0, 10.0, 8.0),
                                      (40.0, 10.0, 8.0))
        self.assertIsNotNone(fraction)
        self.assertGreater(fraction, 0.0)
        self.assertLess(fraction, 1.0)

    def test_segment_starting_outside_fails_closed_immediately(self):
        world = _world()
        self.assertEqual(0.0, world._terrain_hit(
            (-40.0, 10.0, 8.0), (8.0, 10.0, 8.0)))

    def test_interior_missing_height_is_not_an_infinite_vertical_column(self):
        heights = [0] * 36
        heights[2 * 6 + 2] = None
        world = _world(heights=heights)
        self.assertIsNone(world._terrain_hit(
            (0.0, 100.0, 8.0), (20.0, 100.0, 8.0)))


class DestructibleIdentityReadinessTest(unittest.TestCase):
    @staticmethod
    def _catalog():
        basis = [1000, 0, 0, 0, 1000, 0, 0, 0, 1000]
        return {
            'locator_quantization': 1000,
            'resources': {
                'env/fence.model': {
                    'kind': 'fragile',
                    'boxes': [[-1.0, 0.0, -0.2, 1.0, 1.0, 0.2, None]],
                },
            },
            'instances': [
                [1000, 0, 1000] + basis + ['env/fence.model', 0],
                [2000, 0, 2000] + basis + ['env/fence.model', 0],
            ],
        }

    @staticmethod
    def _donation_rows(catalog):
        return [
            [row[:12], 7, item_index, 10.0, None]
            for item_index, row in enumerate(catalog['instances'])
        ]

    def test_world_without_catalog_instances_needs_no_native_identity_map(self):
        world = _world()
        self.assertFalse(world.requires_destructible_identities())
        self.assertTrue(world.destructible_identities_ready())

    def test_partial_native_identity_map_is_not_ready(self):
        catalog = self._catalog()
        world = server_world.BakedWorld(_graph(), catalog=catalog)

        installed = world.install_destructible_map(
            self._donation_rows(catalog)[:1], {}, 8000.0)

        self.assertEqual(installed, 1)
        self.assertTrue(world.has_destructible_identities())
        self.assertFalse(world.destructible_identities_ready())

    def test_complete_native_identity_map_is_ready(self):
        catalog = self._catalog()
        world = server_world.BakedWorld(_graph(), catalog=catalog)

        installed = world.install_destructible_map(
            self._donation_rows(catalog), {}, 8000.0)

        self.assertEqual(installed, 2)
        self.assertTrue(world.destructible_identities_ready())

    def test_disabled_destructibles_are_ready_but_never_emit_native_wires(self):
        catalog = self._catalog()
        world = server_world.BakedWorld(_graph(), catalog=catalog)
        world.install_destructible_map(
            self._donation_rows(catalog)[:1], {}, 8000.0)

        self.assertTrue(world.disable_destructibles('test_mismatch'))
        self.assertFalse(world.disable_destructibles('duplicate'))
        self.assertTrue(world.destructible_identities_ready())
        self.assertFalse(world.requires_destructible_identities())
        self.assertFalse(world.has_destructible_identities())
        self.assertFalse(world.mark_destroyed_wire(7, 0))
        self.assertEqual('test_mismatch',
                         world.destructibles_disabled_reason())


if __name__ == '__main__':
    unittest.main()
