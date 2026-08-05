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

    def test_local_driver_rejects_deep_water_and_unsafe_grades(self):
        battle_source = (
            ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
        ).read_text()

        self.assertIn(
            "_OFFH_AI_WATER_AVOID_DEPTH = 0.12",
            battle_source,
        )
        water_helper = battle_source[
            battle_source.index("def _offh_water_depth"):
            battle_source.index("_OFFH_AI_WATER_AVOID_DEPTH")
        ]
        self.assertIn("return 20.0 - _w", water_helper)
        self.assertNotIn("water_depth > 1.0", battle_source)
        self.assertIn("wet_escape = current_water >", battle_source)
        self.assertIn("16.0 + speed * 2.2", battle_source)
        self.assertIn("delta > run * 0.48", battle_source)
        self.assertIn("delta < -run * 0.38", battle_source)
        self.assertIn("lambda _driver_yaw: _offh_ai_direction_clear(", battle_source)
        self.assertIn("ground_step = 3.0", battle_source)
        self.assertIn(
            "# Fall back to the old reactive steering intent.",
            battle_source,
        )
        self.assertIn("drive_pos = _requested_drive_pos", battle_source)

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
