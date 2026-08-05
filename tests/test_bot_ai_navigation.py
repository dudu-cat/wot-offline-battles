import importlib.util
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAVIGATION_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/bot_ai_navigation.py"
)


def load_navigation():
    spec = importlib.util.spec_from_file_location(
        "bot_ai_navigation_under_test", NAVIGATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotNavigationTest(unittest.TestCase):
    def setUp(self):
        self.navigation = load_navigation()

    def test_astar_routes_around_an_unsupported_ravine(self):
        def ground(x, z, hint):
            if -1.25 < x < 1.25 and z < 3.0:
                return None
            return 0.0

        grid = self.navigation.TerrainGrid(
            ground, bounds=(-6.0, -6.0, 6.0, 6.0), cell_size=1.0
        )
        path = grid.plan((-4.0, 0.0, 0.0), (4.0, 0.0, 0.0))

        self.assertTrue(path)
        self.assertGreater(max(point[2] for point in path), 2.0)
        self.assertTrue(
            all(not (-1.25 < point[0] < 1.25 and point[2] < 3.0) for point in path)
        )

    def test_astar_rejects_an_unavoidable_cliff(self):
        def ground(x, z, hint):
            return 0.0 if x < 0.0 else -10.0

        grid = self.navigation.TerrainGrid(
            ground, bounds=(-5.0, -3.0, 5.0, 3.0), cell_size=1.0
        )

        self.assertEqual((), grid.plan((-4.0, 0.0, 0.0), (4.0, -10.0, 0.0)))

    def test_shared_route_path_is_cached_and_followed(self):
        probes = []

        def ground(x, z, hint):
            probes.append((x, z))
            return math.sin(z * 0.01) * 0.1

        navigator = self.navigation.TerrainNavigator(
            ground, bounds=(-100.0, -100.0, 100.0, 100.0), cell_size=10.0
        )
        first = navigator.next_target(
            1, (-40.0, 0.0, 0.0), (40.0, 0.0, 0.0),
            (1, "main", 1), 1.0, anchor=(-40.0, 0.0, 0.0)
        )
        probe_count = len(probes)
        second = navigator.next_target(
            2, (-38.0, 0.0, 1.0), (40.0, 0.0, 0.0),
            (1, "main", 1), 2.0, anchor=(-40.0, 0.0, 0.0)
        )

        self.assertGreater(first[0], -40.0)
        self.assertGreater(second[0], -38.0)
        self.assertEqual(1, len(navigator.paths))
        self.assertLess(len(probes) - probe_count, 40)

    def test_blocked_astar_is_resumed_with_a_per_frame_budget(self):
        probes = []

        def ground(x, z, hint):
            probes.append((x, z))
            if -1.25 < x < 1.25 and z < 3.0:
                return None
            return 0.0

        navigator = self.navigation.TerrainNavigator(
            ground, bounds=(-6.0, -6.0, 6.0, 6.0), cell_size=1.0
        )
        current = (-4.0, 0.0, 0.0)
        goal = (4.0, 0.0, 0.0)
        first = navigator.next_target(
            7, current, goal, (1, "ravine", 1), 1.0, anchor=current
        )
        first_probe_count = len(probes)
        same_frame = navigator.next_target(
            7, current, goal, (1, "ravine", 1), 1.0, anchor=current
        )

        self.assertEqual(current, first)
        self.assertEqual(current, same_frame)
        self.assertEqual(first_probe_count, len(probes))

        target = current
        for frame in range(1, 120):
            target = navigator.next_target(
                7, current, goal, (1, "ravine", 1),
                1.0 + frame / 30.0, anchor=current
            )
            if target != current:
                break

        self.assertNotEqual(current, target)
        self.assertGreater(target[2], 2.0)

    def test_local_driver_rejects_deep_water_and_unsafe_grades(self):
        battle_source = (
            ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
        ).read_text()

        self.assertIn(
            "_offh_water_depth(x, y, z) > 1.0",
            battle_source,
        )
        self.assertIn("delta > run * 0.48", battle_source)
        self.assertIn("delta < -run * 0.38", battle_source)
        self.assertIn("lambda _driver_yaw: _offh_ai_direction_clear(", battle_source)

    def test_stalled_route_temporarily_penalizes_its_failed_first_edge(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=10.0,
        )
        current = (0.0, 0.0, 0.0)
        goal = (60.0, 0.0, 0.0)
        first = navigator.next_target(
            12, current, goal, (1, "failed-road", 0), 0.0, anchor=current
        )
        self.assertGreater(first[0], current[0])

        target = first
        for frame in range(1, 220):
            target = navigator.next_target(
                12, current, goal, (1, "failed-road", 0),
                frame / 30.0, anchor=current,
            )
            if target != current and abs(target[2]) > 0.1:
                break

        self.assertNotEqual(current, target)
        self.assertGreater(abs(target[2]), 0.1)
        self.assertTrue(navigator.grid._failed_edges)

    def test_failed_edges_are_bidirectional_bounded_and_invalidate_cached_path(self):
        grid = self.navigation.TerrainGrid(lambda x, z, hint: 0.0, cell_size=1.0)
        start = (0.0, 0.0, 0.0)
        goal = (1.0, 0.0, 0.0)
        grid.remember_failed_segment(start, goal, 1.0, ttl=10.0)
        self.assertGreater(grid.segment_penalty(start, goal, 2.0), 0.0)
        self.assertGreater(grid.segment_penalty(goal, start, 2.0), 0.0)
        self.assertTrue(grid.path_has_penalty((start, goal), 2.0))

        grid.remember_failed_segment((18.0, 0.0, 0.0), (60.0, 0.0, 0.0),
                                     2.0, ttl=20.0)
        self.assertTrue(grid.path_has_penalty(
            ((0.0, 0.0, 0.0), (60.0, 0.0, 0.0)), 2.0
        ))

        for index in range(180):
            grid.remember_failed_segment(
                (float(index), 0.0, 0.0), (float(index + 2), 0.0, 0.0),
                2.0, ttl=20.0
            )
        grid.prune_failed_edges(2.0)
        self.assertLessEqual(len(grid._failed_edges), 128)


if __name__ == "__main__":
    unittest.main()
