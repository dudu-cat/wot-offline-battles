import importlib.util
import math
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/client/gui/mods/offhangar/vehicle_collision.py"


def load_module():
    spec = importlib.util.spec_from_file_location("vehicle_collision", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Vector(object):
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]


class HitTester(object):
    def __init__(self, minimum, maximum):
        self.bbox = (minimum, maximum)


class Descriptor(object):
    def __init__(self):
        self.chassis = {
            "hitTester": HitTester(Vector(-1.7, -0.6, -3.2), Vector(1.7, 0.8, 3.2)),
            "hullPosition": Vector(0.0, 0.7, 0.0),
        }
        self.hull = {
            "hitTester": HitTester(Vector(-1.2, -0.2, -2.0), Vector(1.2, 1.5, 2.0))
        }


class VehicleCollisionTests(unittest.TestCase):
    def setUp(self):
        self.collision = load_module()
        self.shape = (1.5, 3.0, -0.5, 2.0)

    def test_shape_uses_chassis_width_and_length(self):
        shape = self.collision.chassis_shape(Descriptor())

        self.assertEqual(1.7, shape[0])
        self.assertEqual(3.2, shape[1])
        self.assertEqual(-0.6, shape[2])
        self.assertEqual(2.2, shape[3])

    def test_separated_boxes_have_no_contact(self):
        contact = self.collision.obb_contact(
            0.0, 0.0, 0.0, self.shape,
            10.0, 0.0, 0.0, self.shape,
        )

        self.assertIsNone(contact)

    def test_axis_aligned_overlap_reports_smallest_translation(self):
        contact = self.collision.obb_contact(
            0.0, 0.0, 0.0, self.shape,
            2.5, 0.0, 0.0, self.shape,
        )

        self.assertAlmostEqual(-1.0, contact[0])
        self.assertAlmostEqual(0.0, contact[1])
        self.assertAlmostEqual(0.5, contact[2])

    def test_rotated_corner_overlap_is_detected(self):
        contact = self.collision.obb_contact(
            0.0, 0.0, math.radians(25.0), self.shape,
            2.2, 1.0, math.radians(-20.0), self.shape,
        )

        self.assertIsNotNone(contact)
        self.assertGreater(contact[2], 0.0)

    def test_vertical_intervals_reject_stacked_false_contact(self):
        self.assertFalse(
            self.collision.vertical_overlap(0.0, self.shape, 5.0, self.shape)
        )
        self.assertTrue(
            self.collision.vertical_overlap(0.0, self.shape, 1.0, self.shape)
        )

    def test_high_support_is_an_obstacle_instead_of_a_vertical_snap(self):
        self.assertTrue(
            self.collision.support_rise_is_obstacle(0.0, 1.4, 0.65)
        )
        self.assertTrue(
            self.collision.support_rise_is_obstacle(0.0, 1.4, 2.5)
        )
        self.assertFalse(
            self.collision.support_rise_is_obstacle(0.0, 0.55, 0.65)
        )
        self.assertFalse(
            self.collision.support_rise_is_obstacle(0.0, None, 0.65)
        )

    def test_only_gradual_uphill_profiles_bypass_wall_collision(self):
        self.assertFalse(
            self.collision.drivable_rising_profile(
                [4.0, 4.0, 4.0, 4.0], 0.75
            )
        )
        self.assertFalse(
            self.collision.drivable_rising_profile(
                [4.5, 4.3, 4.1, 3.9], 0.75
            )
        )
        self.assertTrue(
            self.collision.drivable_rising_profile(
                [4.0, 4.3, 4.7, 5.1], 0.75
            )
        )
        self.assertFalse(
            self.collision.drivable_rising_profile(
                [4.0, 4.2, 5.5, 5.7], 0.75
            )
        )

    def test_equal_mass_pair_separates_and_stops_closing_velocity(self):
        response = self.collision.pair_response(
            (-1.0, 0.0, 0.5), 1.0, 1.0,
            (2.0, 0.0), (-2.0, 0.0),
            slop=0.0, percent=1.0,
        )

        self.assertAlmostEqual(-0.25, response[0])
        self.assertAlmostEqual(0.25, response[4])
        self.assertAlmostEqual(-2.0, response[2])
        self.assertAlmostEqual(2.0, response[6])

    def test_heavier_body_receives_less_position_correction(self):
        response = self.collision.pair_response(
            (1.0, 0.0, 0.6), 1.0 / 60.0, 1.0 / 20.0,
            (0.0, 0.0), (0.0, 0.0),
            slop=0.0, percent=1.0,
        )

        self.assertAlmostEqual(0.15, response[0])
        self.assertAlmostEqual(-0.45, response[4])

    def test_dynamic_body_resolves_fully_against_native_static_owner(self):
        response = self.collision.pair_response(
            (1.0, 0.0, 0.6), 1.0 / 25.0, 0.0,
            (-3.0, 0.0), (0.0, 0.0),
            slop=0.0, percent=1.0,
        )

        self.assertAlmostEqual(0.6, response[0])
        self.assertAlmostEqual(0.0, response[4])
        self.assertAlmostEqual(3.0, response[2])
        self.assertAlmostEqual(0.0, response[6])

    def test_spatial_index_returns_only_neighbouring_cells(self):
        bodies = {
            1: {"position": (1.0, 0.0, 1.0)},
            2: {"position": (25.0, 0.0, 1.0)},
            3: {"position": (-23.0, 0.0, -23.0)},
            4: {"position": (80.0, 0.0, 80.0)},
        }

        index = self.collision.build_spatial_index(bodies, 24.0)

        self.assertEqual({1, 2, 3}, set(self.collision.nearby_ids(index, 0.0, 0.0)))
        self.assertNotIn(4, self.collision.nearby_ids(index, 0.0, 0.0))

    def test_spatial_index_handles_negative_cell_boundaries(self):
        bodies = {
            10: {"position": (-0.1, 0.0, -0.1)},
            11: {"position": (-24.1, 0.0, -24.1)},
            12: {"position": (48.1, 0.0, 48.1)},
        }

        index = self.collision.build_spatial_index(bodies, 24.0)

        self.assertEqual({10, 11}, set(self.collision.nearby_ids(index, -0.1, -0.1)))

    def test_unique_candidate_map_assigns_each_pair_once_to_a_local_solver(self):
        bodies = {
            1: {"position": (0.0, 0.0, 0.0)},
            2: {"position": (2.0, 0.0, 0.0)},
            3: {"position": (4.0, 0.0, 0.0)},
            9: {"position": (80.0, 0.0, 80.0)},
        }
        index = self.collision.build_spatial_index(bodies, 24.0)

        candidates = self.collision.unique_candidate_map(index, bodies, (2, 3))

        self.assertEqual((1, 3), candidates[2])
        self.assertEqual((1,), candidates[3])
        pairs = set()
        for solver_id, candidate_ids in candidates.items():
            for candidate_id in candidate_ids:
                pair = tuple(sorted((solver_id, candidate_id)))
                self.assertNotIn(pair, pairs)
                pairs.add(pair)
        self.assertEqual({(1, 2), (1, 3), (2, 3)}, pairs)

    def test_unique_candidate_map_keeps_local_remote_pair_on_local_solver(self):
        bodies = {
            10: {"position": (0.0, 0.0, 0.0)},
            11: {"position": (1.0, 0.0, 0.0)},
        }
        index = self.collision.build_spatial_index(bodies, 24.0)

        candidates = self.collision.unique_candidate_map(index, bodies, (11,))

        self.assertEqual({11: (10,)}, candidates)

    def test_unique_candidate_map_covers_all_eight_neighbouring_cells(self):
        bodies = {1: {"position": (1.0, 0.0, 1.0)}}
        body_id = 2
        for cell_z in (-1, 0, 1):
            for cell_x in (-1, 0, 1):
                if cell_x == 0 and cell_z == 0:
                    continue
                bodies[body_id] = {
                    "position": (cell_x * 24.0 + 1.0, 0.0, cell_z * 24.0 + 1.0)
                }
                body_id += 1
        bodies[99] = {"position": (49.0, 0.0, 49.0)}
        index = self.collision.build_spatial_index(bodies, 24.0)

        candidates = self.collision.unique_candidate_map(index, bodies, (1,))

        self.assertEqual(set(range(2, 10)), set(candidates[1]))
        self.assertNotIn(99, candidates[1])


if __name__ == "__main__":
    unittest.main()
