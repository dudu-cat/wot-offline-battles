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


if __name__ == "__main__":
    unittest.main()
