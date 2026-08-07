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

    @staticmethod
    def baked_graph(width, height, blocked=()):
        directions = (
            (-1, -1), (0, -1), (1, -1),
            (-1, 0), (1, 0),
            (-1, 1), (0, 1), (1, 1),
        )
        blocked = set(blocked)
        heights = [0 if (x, z) not in blocked else None
                   for z in range(height) for x in range(width)]
        links = [0] * (width * height)
        for z in range(height):
            for x in range(width):
                index = z * width + x
                if heights[index] is None:
                    continue
                for direction_index, (dx, dz) in enumerate(directions):
                    nx, nz = x + dx, z + dz
                    if not (0 <= nx < width and 0 <= nz < height):
                        continue
                    if heights[nz * width + nx] is None:
                        continue
                    if dx and dz:
                        if (heights[z * width + nx] is None or
                                heights[nz * width + x] is None):
                            continue
                    links[index] |= 1 << direction_index
        return {
            "format": "offhangar-navgraph",
            "version": 1,
            "cell_size": 4.0,
            "origin": [10.0, 20.0],
            "bounds": [8.0, 18.0, 10.0 + width * 4.0,
                       20.0 + height * 4.0],
            "width": width,
            "height": height,
            "heights_mm": heights,
            "links": links,
        }

    def test_prebaked_astar_uses_shipped_links_without_engine_probes(self):
        probe_calls = []

        def forbidden_probe(*args):
            probe_calls.append(args)
            raise AssertionError("prebaked navigation must not probe the engine")

        graph = self.baked_graph(5, 3, blocked=((2, 0),))
        grid = self.navigation.TerrainGrid(
            forbidden_probe, obstacle_probe=forbidden_probe,
            baked_graph=graph,
        )

        path = grid.plan((10.0, 0.0, 20.0), (26.0, 0.0, 20.0),
                         max_expansions=100)

        self.assertTrue(path)
        self.assertGreater(max(point[2] for point in path), 20.0)
        self.assertEqual([], probe_calls)
        self.assertEqual((0, 0), grid.cell_for((10.0, 0.0, 20.0)))

    def test_prebaked_navigator_uses_larger_cheap_search_budget(self):
        navigator = self.navigation.TerrainNavigator(
            lambda *args: None, baked_graph=self.baked_graph(3, 3)
        )

        self.assertTrue(navigator.grid.prebaked)
        self.assertEqual(4.0, navigator.grid.cell_size)
        self.assertEqual(768, navigator.search_budget_per_frame)
        self.assertEqual(4096, navigator.search_max_expansions)

        diagnostics = navigator.fallback_diagnostics()
        self.assertEqual("baked", diagnostics["graph"]["source"])
        self.assertEqual(4000, diagnostics["graph"]["cell_mm"])
        self.assertEqual(9, diagnostics["graph"]["nodes"])

    def test_prebaked_corridor_allows_one_cell_shoulder_only(self):
        graph = self.baked_graph(5, 1, blocked=((2, 0), (3, 0), (4, 0)))
        grid = self.navigation.TerrainGrid(lambda *args: None, baked_graph=graph)

        self.assertTrue(grid.near_baked_navigation((14.0, 0.0, 20.0), 0))
        self.assertTrue(grid.near_baked_navigation((18.0, 0.0, 20.0), 1))
        self.assertFalse(grid.near_baked_navigation((22.0, 0.0, 20.0), 1))

    def test_prebaked_hazard_mask_does_not_confuse_obstacles_with_cliffs(self):
        graph = self.baked_graph(3, 1)
        graph["hazards"] = [0, 4, 2]
        grid = self.navigation.TerrainGrid(lambda *args: None, baked_graph=graph)

        self.assertFalse(grid.baked_hazard_near((10.0, 0.0, 20.0)))
        self.assertFalse(grid.baked_hazard_near((14.0, 0.0, 20.0)))
        self.assertTrue(grid.baked_hazard_near((18.0, 0.0, 20.0)))
        self.assertGreater(grid._penalty((1, 0), None), 0.0)

    def test_shipped_lakeville_graph_connects_every_route_both_ways(self):
        graph_path = (
            ROOT / "scripts/client/gui/mods/offhangar/navgraphs/07_lakeville.json"
        )
        maps_path = (
            ROOT / "scripts/client/gui/mods/offhangar/bot_ai_maps_group_a.py"
        )
        import json
        graph = json.loads(graph_path.read_text())
        spec = importlib.util.spec_from_file_location(
            "bot_ai_maps_group_a_route_audit", maps_path
        )
        maps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(maps)
        probe_calls = []

        def forbidden_probe(*args):
            probe_calls.append(args)
            raise AssertionError("the shipped graph must be self-contained")

        grid = self.navigation.TerrainGrid(
            forbidden_probe, obstacle_probe=forbidden_probe,
            baked_graph=graph,
        )
        segment_count = 0
        for team in (1, 2):
            enemy_base = maps.LAKEVILLE["bases"][3 - team]
            for route in maps.LAKEVILLE["routes"][team]:
                points = [(point[0], 0.0, point[1])
                          for point in route["waypoints"]]
                points.append((enemy_base[0], 0.0, enemy_base[1]))
                for start, goal in zip(points, points[1:]):
                    segment_count += 1
                    self.assertTrue(
                        grid.plan(start, goal, max_expansions=4096),
                        (team, route["id"], start, goal),
                    )
                    path = grid.plan(start, goal, max_expansions=4096)
                    self.assertLessEqual(
                        math.hypot(path[-1][0] - goal[0],
                                   path[-1][2] - goal[2]),
                        12.0,
                        (team, route["id"], start, goal, path[-1]),
                    )

        self.assertEqual(48, segment_count)
        self.assertEqual([], probe_calls)

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

    def test_obstacle_probe_failure_is_blocked_not_clear(self):
        def broken_obstacle_probe(start, end, half_width):
            raise RuntimeError("collision service unavailable")

        grid = self.navigation.TerrainGrid(
            lambda x, z, hint: 0.0,
            obstacle_probe=broken_obstacle_probe,
            bounds=(-10.0, -10.0, 10.0, 10.0),
            cell_size=1.0,
        )

        self.assertFalse(grid.segment_clear(
            (-5.0, 0.0, 0.0), (5.0, 0.0, 0.0)
        ))

    def test_malformed_bounds_fail_closed(self):
        grid = self.navigation.TerrainGrid(
            lambda x, z, hint: 0.0,
            bounds=("broken",),
            cell_size=1.0,
        )

        self.assertFalse(grid._inside(0.0, 0.0))
        self.assertIsNone(grid._ground(0.0, 0.0, 0.0))

    def test_astar_stops_at_nearest_reachable_cell_for_a_slightly_bad_anchor(self):
        def ground(x, z, hint):
            return 0.0

        def obstacle(start, end, half_width):
            return end[0] > 6.5

        grid = self.navigation.TerrainGrid(
            ground, obstacle_probe=obstacle,
            bounds=(-10.0, -10.0, 10.0, 10.0), cell_size=1.0
        )
        path = grid.plan((-8.0, 0.0, 0.0), (8.0, 0.0, 0.0))

        self.assertTrue(path)
        self.assertLessEqual(path[-1][0], 6.0)
        self.assertGreaterEqual(path[-1][0], 5.0)

    def test_astar_does_not_mask_a_grossly_wrong_anchor(self):
        def ground(x, z, hint):
            return None if x > 0.0 else 0.0

        grid = self.navigation.TerrainGrid(
            ground, bounds=(-20.0, -5.0, 20.0, 5.0), cell_size=1.0
        )

        self.assertEqual((), grid.plan(
            (-15.0, 0.0, 0.0), (15.0, 0.0, 0.0)
        ))

    def test_astar_only_queues_cost_improvements(self):
        grid = self.navigation.TerrainGrid(
            lambda x, z, hint: 0.0,
            bounds=(0.0, 0.0, 2.0, 2.0),
            cell_size=1.0,
        )
        real_push = self.navigation.heapq.heappush
        pushed_cells = []

        def recording_push(frontier, value):
            pushed_cells.append(value[2])
            return real_push(frontier, value)

        self.navigation.heapq.heappush = recording_push
        try:
            path = grid.plan((0.0, 0.0, 0.0), (2.0, 0.0, 2.0),
                             max_expansions=20)
        finally:
            self.navigation.heapq.heappush = real_push

        self.assertEqual((2.0, 0.0, 2.0), path[-1])
        self.assertEqual(9, len(pushed_cells))
        self.assertEqual(9, len(set(pushed_cells)))

    def test_expansion_limit_returns_supported_partial_progress(self):
        def ground(x, z, hint):
            if -1.0 < x < 1.0 and z < 50.0:
                return None
            return 0.0

        grid = self.navigation.TerrainGrid(
            ground,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=1.0,
        )

        path = grid.plan(
            (-90.0, 0.0, 0.0), (90.0, 0.0, 0.0), max_expansions=5
        )

        self.assertTrue(path)
        self.assertGreater(path[-1][0], -90.0)
        self.assertLess(path[-1][0], 90.0)

    def test_failed_global_path_uses_only_a_probed_local_fallback(self):
        def ground(x, z, hint):
            return 0.0 if x <= 4.0 else None

        navigator = self.navigation.TerrainNavigator(
            ground,
            bounds=(-20.0, -20.0, 20.0, 20.0),
            cell_size=1.0,
        )
        navigator.search_budget_per_frame = 512
        navigator.search_budget_per_path = 512
        navigator.search_time_budget = 0.0
        current = (-10.0, 0.0, 0.0)
        goal = (10.0, 0.0, 0.0)
        target = current
        for frame in range(10):
            target = navigator.next_target(
                3, current, goal, ("local", 3, "blocked"),
                1.0 + frame / 30.0,
            )
            if navigator.fallback_modes.get(3) == "safe_local":
                break

        self.assertNotEqual(current, target)
        self.assertLessEqual(target[0], 4.0)
        self.assertTrue(navigator.grid.segment_clear(current, target))

    def test_fallback_diagnostics_count_transitions_not_frames(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0, cell_size=10.0
        )

        navigator._set_fallback_mode(1, "reactive")
        navigator._set_fallback_mode(1, "reactive")
        navigator._set_fallback_mode(1, "safe_local")
        navigator._set_fallback_mode(2, "reactive")
        navigator._set_fallback_mode(1, None)

        diagnostics = navigator.fallback_diagnostics([1])
        self.assertEqual(2, diagnostics["total"]["reactive"])
        self.assertEqual(1, diagnostics["total"]["safe_local"])
        self.assertEqual(1, diagnostics["recovered"])
        self.assertEqual(0, diagnostics["active"]["reactive"])
        self.assertEqual(0, diagnostics["active"]["safe_local"])

    def test_failed_join_path_uses_the_same_safe_local_fallback(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=10.0,
        )
        current = (0.0, 0.0, 0.0)
        fallback = (6.0, 0.0, 4.0)
        calls = []

        def fake_path(path_key, start, goal, now, avoid_points):
            if path_key[0] == "join":
                return (("join-result",), ())
            return (("cached-result",), ((30.0, 0.0, 0.0), (60.0, 0.0, 0.0)))

        def fake_safe_local_target(*args):
            calls.append(args)
            return fallback

        navigator._path = fake_path
        navigator.grid.segment_clear = lambda start, end: False
        navigator.grid.safe_local_target = fake_safe_local_target

        target = navigator.next_target(
            5, current, (60.0, 0.0, 0.0), ("route", 1, "blocked-join"), 1.0
        )

        self.assertEqual(fallback, target)
        self.assertEqual(1, len(calls))

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

    def test_shared_route_anchor_uses_the_live_terrain_layer(self):
        hints = []

        def elevated_ground(x, z, hint):
            hints.append(hint)
            return 32.0 if hint > 20.0 else None

        navigator = self.navigation.TerrainNavigator(
            elevated_ground,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=10.0,
        )
        current = (-40.0, 32.0, 0.0)
        target = current
        for frame in range(120):
            target = navigator.next_target(
                1, current, (40.0, 0.0, 0.0),
                (1, "elevated", 1), 1.0 + frame / 30.0,
                anchor=(-40.0, 0.0, 0.0),
            )
            if target != current:
                break

        self.assertNotEqual(current, target)
        self.assertTrue(hints)
        self.assertGreater(min(hints), 20.0)

    def test_navigation_pause_is_explicit_for_an_unresolved_long_route(self):
        current = (5.0, 20.0, -7.0)
        self.assertTrue(self.navigation.TerrainNavigator.navigation_paused(
            current, (100.0, 0.0, -7.0), current
        ))
        self.assertFalse(self.navigation.TerrainNavigator.navigation_paused(
            current, (100.0, 0.0, -7.0), (15.0, 20.0, -7.0)
        ))

    def test_new_request_does_not_inherit_an_old_stall_timer(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=10.0,
        )
        current = (0.0, 0.0, 0.0)
        navigator.next_target(9, current, (50.0, 0.0, 0.0),
                              ("local", 9, "first"), 0.0)
        navigator.bot_states[9]["progress_time"] = 0.0

        navigator.next_target(9, current, (0.0, 0.0, 50.0),
                              ("local", 9, "second"), 10.0)

        self.assertEqual(0, navigator.bot_states[9]["recovery"])
        self.assertEqual(0.0, navigator.bot_states[9]["recovery_until"])

    def test_cached_partial_path_is_continued_without_waiting_for_a_stall(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=10.0,
        )
        path_key = ("route", 1, "partial", 2)
        goal = (80.0, 0.0, 0.0)
        cache_key = navigator._cache_key(path_key, goal)
        navigator.paths[cache_key] = (
            (0.0, 0.0, 0.0),
            (20.0, 0.0, 0.0),
        )
        navigator.path_times[cache_key] = 0.0

        target = navigator.next_target(
            12, (20.0, 0.0, 0.0), goal, path_key, 1.0,
            anchor=(0.0, 0.0, 0.0),
        )

        self.assertGreater(target[0], 20.0)

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

        self.assertEqual(goal, first)
        self.assertEqual(goal, same_frame)
        self.assertEqual("reactive", navigator.fallback_modes.get(7))
        self.assertEqual(first_probe_count, len(probes))

        target = current
        for frame in range(1, 120):
            target = navigator.next_target(
                7, current, goal, (1, "ravine", 1),
                1.0 + frame / 30.0, anchor=current
            )
            if navigator.fallback_modes.get(7) is None:
                break

        self.assertNotEqual(current, target)
        self.assertGreater(target[2], 2.0)

    def test_search_budget_rotates_across_every_pending_path(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0, cell_size=10.0
        )
        navigator.search_budget_per_frame = 24
        navigator.search_budget_per_path = 4

        class PendingSearch:
            def __init__(self):
                self.done = False
                self.result = None
                self.last_frame = None
                self.steps = 0

            def step(self, budget):
                self.steps += budget
                return False

        searches = {}
        for index in range(29):
            key = ("search", index)
            searches[key] = PendingSearch()
            navigator.search_times[key] = 0.0
        navigator.searches = searches

        navigator._advance_searches(1.0)
        navigator._advance_searches(1.0 + 1.0 / 30.0)

        steps = [search.steps for search in searches.values()]
        self.assertEqual(48, sum(steps))
        self.assertGreater(min(steps), 0)
        self.assertLessEqual(max(steps) - min(steps), 1)

    def test_global_tick_advances_searches_without_a_long_distance_bot_request(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0, cell_size=10.0
        )
        navigator.search_budget_per_frame = 1
        navigator.search_time_budget = 0.0

        class PendingSearch:
            def __init__(self):
                self.done = False
                self.result = None
                self.steps = 0

            def step(self, budget):
                self.steps += budget
                return False

        key = (("join", 9), (3, 3))
        search = PendingSearch()
        navigator.searches[key] = search
        navigator.search_times[key] = 0.0

        navigator.tick(1.0)
        navigator.tick(1.0)
        self.assertEqual(1, search.steps)
        navigator.tick(1.01)
        self.assertEqual(2, search.steps)

    def test_moving_contact_keeps_a_short_lived_stable_planning_goal(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=10.0,
        )
        current = (0.0, 0.0, 0.0)
        path_key = ("local", 7, "advance_contact", 3)
        first_goal = (0.0, 0.0, 60.0)
        navigator.next_target(7, current, first_goal, path_key, 1.0)
        first_request = navigator.bot_states[7]["request_key"]

        navigator.next_target(
            7, current, (15.0, 0.0, 60.0), path_key, 1.5
        )

        self.assertEqual(first_goal, navigator.bot_states[7]["planned_goal"])
        self.assertEqual(first_request, navigator.bot_states[7]["request_key"])

    def test_blocked_first_path_edge_uses_fallback_instead_of_current_position(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=10.0,
        )
        current = (0.0, 0.0, 0.0)
        goal = (0.0, 0.0, 60.0)
        key = navigator._cache_key(("route", 1, "blocked", 1), goal)
        navigator._path = lambda *args: (
            key, (current, (0.0, 0.0, 10.0), goal)
        )
        navigator.grid.segment_clear = lambda start, end: start == end
        navigator.grid.safe_local_target = lambda *args: None

        selected = navigator.next_target(
            7, current, goal, ("route", 1, "blocked", 1), 1.0
        )

        self.assertEqual(goal, selected)
        self.assertEqual("reactive", navigator.fallback_modes[7])

    def test_cached_path_still_advances_unrelated_pending_searches(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0, cell_size=10.0
        )
        navigator.search_budget_per_frame = 4

        class PendingSearch:
            def __init__(self):
                self.done = False
                self.result = None
                self.last_frame = None
                self.steps = 0

            def step(self, budget):
                self.steps += budget
                return False

        current = (0.0, 0.0, 0.0)
        goal = (0.0, 0.0, 40.0)
        route_key = ("route", 1, "cached", 1)
        cache_key = navigator._cache_key(route_key, goal)
        navigator.paths[cache_key] = (current, goal)
        navigator.path_times[cache_key] = 0.0
        pending_key = (("join", 99, (0, 0), "route"), (9, 9))
        pending = PendingSearch()
        navigator.searches[pending_key] = pending
        navigator.search_times[pending_key] = 0.0

        navigator.next_target(1, current, goal, route_key, 1.0)

        self.assertGreater(pending.steps, 0)

    def test_new_bot_request_cancels_only_its_superseded_private_searches(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0, cell_size=10.0
        )

        class PendingSearch:
            done = False
            result = None
            last_frame = None

            def step(self, budget):
                return False

        own_key = (("local", 7, "withdraw", 3), (4, 4))
        other_key = (("local", 8, "withdraw", 3), (4, 4))
        navigator.searches = {
            own_key: PendingSearch(),
            other_key: PendingSearch(),
        }
        navigator.search_times = {own_key: 0.0, other_key: 0.0}
        navigator.bot_states[7] = {
            "last_position": (0.0, 0.0, 0.0),
            "progress_time": 0.0,
            "path_key": None,
            "index": 0,
            "recovery": 0,
            "recovery_until": 0.0,
            "recovery_key": None,
            "recovery_start": None,
            "request_key": (("local", 7, "withdraw", 3), (4, 4)),
        }

        navigator.next_target(
            7, (0.0, 0.0, 0.0), (0.0, 0.0, 50.0),
            ("local", 7, "advance_contact", 3), 1.0,
        )

        self.assertNotIn(own_key, navigator.searches)
        self.assertIn(other_key, navigator.searches)

    def test_completed_join_path_is_not_replaced_by_shared_path_next_frame(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0,
            bounds=(-100.0, -100.0, 100.0, 100.0),
            cell_size=10.0,
        )
        main_key = ("route", 1, "joined", 0)
        goal = (60.0, 0.0, 0.0)
        joined_key = navigator._cache_key(
            ("join", 5, navigator.grid.cell_for((0.0, 0.0, 0.0))) + main_key,
            goal,
        )
        joined_path = (
            (0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0),
            goal,
        )
        join_calls = []

        def fake_path(path_key, start, target, now, avoid_points):
            if path_key[0] == "join":
                join_calls.append(path_key)
                navigator.paths[joined_key] = joined_path
                navigator.path_times[joined_key] = now
                return joined_key, joined_path
            return navigator._cache_key(path_key, target), (
                (30.0, 0.0, 0.0), goal
            )

        navigator._path = fake_path
        navigator.grid.segment_clear = (
            lambda start, end: float(end[0]) <= 10.0
        )

        first = navigator.next_target(5, (0.0, 0.0, 0.0), goal, main_key, 1.0)
        second = navigator.next_target(5, (5.0, 0.0, 0.0), goal, main_key, 1.1)

        self.assertEqual((10.0, 0.0, 0.0), first)
        self.assertEqual((10.0, 0.0, 0.0), second)
        self.assertEqual(1, len(join_calls))

    def test_navigation_failure_is_loud_and_does_not_disable_tactical_ai(self):
        battle_source = (
            ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
        ).read_text()
        reporter = battle_source[
            battle_source.index("def _offh_ai_navigation_failure"):
            battle_source.index("def _offh_ai_navigator")
        ]
        initialization = battle_source[
            battle_source.index("_ai_navigator = _offh_ai_navigator"):
            battle_source.index("# Registration order affects route capacity")
        ]

        self.assertIn("navigation failure stage=%s error=%s", reporter)
        self.assertIn("LOG_ERROR(detail)", reporter)
        self.assertIn("pushMessage", reporter)
        self.assertIn("_offh_ai_refresh_contacts", initialization)
        self.assertNotIn("_ai_director = None", initialization)
        self.assertIn("if _navigator is not None:", battle_source)

    def test_proximity_spotting_does_not_grant_a_firing_lane_through_cover(self):
        battle_source = (
            ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
        ).read_text()
        contacts = battle_source[
            battle_source.index("def _offh_ai_refresh_contacts"):
            battle_source.index("def _offh_battle_sweep")
        ]

        self.assertIn("proximity_visible = distance_sq <= 2500.0", contacts)
        self.assertIn("if proximity_visible or has_los:", contacts)
        self.assertIn("if not has_los:\n\t\t\t\t\t\tcontinue", contacts)
        self.assertGreater(
            contacts.index("shootable_by_bot_ids.append"),
            contacts.index("if not has_los:"),
        )

    def test_local_driver_rejects_deep_water_and_unsafe_grades(self):
        battle_source = (
            ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
        ).read_text()

        self.assertIn(
            "_OFFH_AI_WATER_AVOID_DEPTH = 0.90",
            battle_source,
        )
        water_helper = battle_source[
            battle_source.index("def _offh_water_depth"):
            battle_source.index("_OFFH_AI_WATER_AVOID_DEPTH")
        ]
        self.assertIn("return 20.0 - _w", water_helper)
        self.assertNotIn("water_depth > 1.0", battle_source)
        self.assertIn("wet_escape = current_water >", battle_source)
        self.assertIn("7.0 + speed * 1.2", battle_source)
        self.assertIn("14.0 + speed * 2.2", battle_source)
        self.assertIn("delta > run * 0.48", battle_source)
        self.assertIn("delta < -run * 0.38", battle_source)
        self.assertIn("lambda _driver_yaw: _offh_ai_direction_clear(", battle_source)
        self.assertIn("ground_step = 3.0", battle_source)
        self.assertIn(
            "# Fall back to the old reactive steering intent.",
            battle_source,
        )
        self.assertIn("drive_pos = _requested_drive_pos", battle_source)
        self.assertIn("def _offh_ai_pose_water_depth", battle_source)
        self.assertNotIn("def _offh_ai_dry_rollback", battle_source)
        self.assertNotIn("_offh_ai_dry_history", battle_source)
        self.assertIn("m_veh._offh_ai_driver_mode = 'water_guard'", battle_source)
        self.assertIn("def _offh_ai_baked_pose_safe", battle_source)
        self.assertIn("m_veh._offh_ai_driver_mode = 'edge_guard'", battle_source)
        self.assertGreater(
            battle_source.index("# Final realised-pose water guard."),
            battle_source.index("# --- Slope slide (bot):"),
        )
        lines = battle_source.splitlines()

        def leading_tabs(marker):
            line = next(line for line in lines if marker in line)
            return len(line) - len(line.lstrip("\t"))

        # The transaction must run for active battles too, and the final veto must
        # run after both grounded slide and airborne drift before model/network state.
        self.assertEqual(
            leading_tabs("if not _battle_active:"),
            leading_tabs("bot_gravity = _PHY.GRAVITY"),
        )
        self.assertEqual(
            leading_tabs("# --- Slope slide (bot):"),
            leading_tabs("# Final realised-pose water guard."),
        )
        final_guard = battle_source.index("# Final realised-pose water guard.")
        final_guard_end = battle_source.index(
            "# The baked hazard mask marks water and cliff shoulders separately",
            final_guard,
        )
        water_guard = battle_source[final_guard:final_guard_end]
        self.assertIn(
            "_dry_anchor = getattr(m_veh, '_offh_ai_tick_dry_pose', None)",
            water_guard,
        )
        self.assertNotIn("dry_rollback", water_guard)
        self.assertLess(
            final_guard,
            battle_source.index("m_veh.matrix.setRotateYPR", final_guard),
        )
        self.assertLess(
            final_guard,
            battle_source.index("publish_authoritative_bots", final_guard),
        )
        edge_guard = battle_source.index(
            "# The baked hazard mask marks water and cliff shoulders separately"
        )
        self.assertGreater(edge_guard, final_guard)
        self.assertLess(
            edge_guard,
            battle_source.index("m_veh.matrix.setRotateYPR", edge_guard),
        )

    def test_astar_jobs_are_bounded_and_ignore_transient_vehicle_penalties(self):
        navigator = self.navigation.TerrainNavigator(
            lambda x, z, hint: 0.0, cell_size=10.0
        )
        navigator.grid.segment_clear = lambda start, end: False
        captured = {}

        class PendingSearch:
            done = False
            result = None
            last_frame = None

            def step(self, budget):
                return False

        def begin_plan(start, goal, avoid_points=None, max_expansions=1600,
                       now=0.0):
            captured["avoid_points"] = avoid_points
            captured["max_expansions"] = max_expansions
            return PendingSearch()

        navigator.grid.begin_plan = begin_plan
        navigator._path(
            ("local", 7, "advance_contact", 2),
            (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), 1.0,
            [(10.0, 0.0, 0.0)] * 28,
        )

        self.assertIsNone(captured["avoid_points"])
        self.assertEqual(128, captured["max_expansions"])

    def test_stationary_traffic_does_not_poison_the_shared_static_route(self):
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
        self.assertNotEqual(current, target)
        self.assertEqual({}, navigator.grid._failed_edges)
        self.assertFalse(any(
            isinstance(key[0], tuple) and key[0] and key[0][0] == "recovery"
            for key in navigator.searches
        ))

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
