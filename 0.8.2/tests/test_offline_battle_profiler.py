import unittest
import math
import sys
import textwrap
import types
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"


class FakeTime:
    def __init__(self):
        self.wall = 100.0
        self.precise = 10.0

    def time(self):
        return self.wall

    def clock(self):
        return self.precise


def load_profiler(fake_time):
    source = SOURCE.read_text()
    start = source.index("_OFFH_PERF_SAMPLE_EVERY =")
    end = source.index("def _get_destr_authority", start)
    namespace = {"time": fake_time}
    exec(source[start:end], namespace)
    return namespace


def load_module_factor(device_td):
    source = SOURCE.read_text()
    start = source.index("\t\t\tdef _module_factor")
    end = source.index("\n\t\t\tdef _knock_out_crew", start)
    namespace = {"_device_td": device_td}
    exec(textwrap.dedent(source[start:end]), namespace)
    return namespace["_module_factor"]


def load_native_hazard_transition():
    source = SOURCE.read_text()
    marker = source.index("_native_hazard_reason = None")
    start = source.rfind("\n", 0, marker) + 1
    end = source.index("# Native destructible health", start)
    block = textwrap.dedent(source[start:end])
    wrapper = (
        "def run_transition(m_veh, manager, driver, water, was_safe, "
        "now_safe):\n"
        "    _native_body_manager = manager\n"
        "    _native_water = water\n"
        "    _native_was_safe = was_safe\n"
        "    _native_now_safe = now_safe\n"
        "    _OFFH_AI_WATER_AVOID_DEPTH = 0.90\n"
        "    _b_ypr = None\n"
        "    eid = 17\n"
        "    target_yaw = 0.0\n"
        "    _native_previous_pose = (1.0, 2.0, 3.0, 0.0, 0.0, 0.0)\n"
        "    _offh_ai_probe_reject = lambda *args: None\n"
        "    LOG_NOTE = lambda *args: None\n" +
        textwrap.indent(block, "    ") +
        "    return bool(getattr(m_veh, "
        "'_offh_native_hazard_recovering', False))\n"
    )
    namespace = {}
    exec(wrapper, namespace)
    return namespace["run_transition"]


def load_native_hazard_escape_target():
    source = SOURCE.read_text()
    start = source.index("def _offh_native_hazard_escape_target(")
    end = source.index("\n\ndef _offh_ai_pose_water_depth", start)
    namespace = {}
    exec(source[start:end], namespace)
    return namespace["_offh_native_hazard_escape_target"]


def load_native_hazard_recovery_complete():
    source = SOURCE.read_text()
    marker = "def _offh_native_hazard_recovery_complete("
    if marker not in source:
        return None
    start = source.index(marker)
    end = source.index("\n\ndef ", start + len(marker))
    namespace = {}
    exec(source[start:end], namespace)
    return namespace["_offh_native_hazard_recovery_complete"]


def device_damage_modules(module_stat_factor):
    gui = types.ModuleType("gui")
    mods = types.ModuleType("gui.mods")
    offhangar = types.ModuleType("gui.mods.offhangar")
    device_damage = types.ModuleType("gui.mods.offhangar.device_damage")
    device_damage.module_stat_factor = module_stat_factor
    gui.mods = mods
    mods.offhangar = offhangar
    offhangar.device_damage = device_damage
    return {
        "gui": gui,
        "gui.mods": mods,
        "gui.mods.offhangar": offhangar,
        "gui.mods.offhangar.device_damage": device_damage,
    }


class OfflineBattleProfilerTests(unittest.TestCase):
    def test_ballistic_marker_preview_is_throttled_and_has_no_duplicate_ray(self):
        source = SOURCE.read_text()
        start = source.index("# Stock VehicleGunRotator walks the shell parabola")
        end = source.index("# UPDATE CROSSHAIR", start)
        gun_marker = source[start:end]

        self.assertIn("_offh_player_gun_marker_impact(", gun_marker)
        self.assertIn("'marker_preview_at', -999.0", gun_marker)
        self.assertIn("< 0.1", gun_marker)
        self.assertIn("_marker_preview_allowed = (_period_g == 3 or not", gun_marker)
        self.assertIn("if _marker_preview_allowed and not _marker_preview_cached:", gun_marker)
        self.assertIn("if _marker_preview_cached:", gun_marker)
        self.assertIn("_offh_perf_stop('player_gun_marker'", gun_marker)
        self.assertIn("profile_candidates=(_marker_perf is not None)", gun_marker)
        self.assertIn("marker_preview_error_at", gun_marker)
        self.assertNotIn("BigWorld.wg_collideSegment", gun_marker)

        ordered_start = source.index("ordered = ('player_loop'")
        ordered_end = source.index("\n\tparts = []", ordered_start)
        ordered = source[ordered_start:ordered_end]
        self.assertIn("'player_gun_marker'", ordered)
        self.assertIn("'marker_vehicle_candidates'", ordered)

    def test_module_factor_pristine_fast_path_skips_damage_helper(self):
        device_td = mock.Mock(side_effect=AssertionError("descriptor lookup"))
        module_stat_factor = mock.Mock(side_effect=AssertionError("damage helper"))
        factor = load_module_factor(device_td)
        vehicle = types.SimpleNamespace(
            devices_hp={},
            _destroyed_devices=set(),
        )

        with mock.patch.dict(
            sys.modules, device_damage_modules(module_stat_factor)
        ):
            result = factor(vehicle, "mobility")

        self.assertEqual(1.0, result)
        module_stat_factor.assert_not_called()
        device_td.assert_not_called()

    def test_module_factor_damaged_and_destroyed_paths_still_delegate(self):
        descriptor = object()
        device_td = mock.Mock(return_value=descriptor)
        module_stat_factor = mock.Mock(side_effect=[0.5, 0.0])
        factor = load_module_factor(device_td)
        damaged_hp = {"engineHealth": 40.0}
        damaged_destroyed = set()
        destroyed_hp = {}
        destroyed_devices = set(["engineHealth"])
        damaged = types.SimpleNamespace(
            devices_hp=damaged_hp,
            _destroyed_devices=damaged_destroyed,
        )
        destroyed = types.SimpleNamespace(
            devices_hp=destroyed_hp,
            _destroyed_devices=destroyed_devices,
        )

        with mock.patch.dict(
            sys.modules, device_damage_modules(module_stat_factor)
        ):
            damaged_factor = factor(damaged, "mobility")
            destroyed_factor = factor(destroyed, "mobility")

        self.assertEqual(0.5, damaged_factor)
        self.assertEqual(0.0, destroyed_factor)
        self.assertEqual(
            [
                mock.call(
                    damaged_hp, damaged_destroyed, descriptor, "mobility"
                ),
                mock.call(
                    destroyed_hp, destroyed_devices, descriptor, "mobility"
                ),
            ],
            module_stat_factor.call_args_list,
        )
        self.assertEqual([mock.call(damaged), mock.call(destroyed)],
                         device_td.call_args_list)

    def test_retail_model_fetch_aggregates_four_parallel_components_once(self):
        profiler = load_profiler(FakeTime())
        fetches = []
        bigworld = types.ModuleType("BigWorld")
        bigworld.fetchModel = lambda path, callback: fetches.append((path, callback))
        descriptor = types.SimpleNamespace(
            chassis={"models": {"undamaged": "chassis.model"}},
            hull={"models": {"undamaged": "hull.model"}},
            turret={"models": {"undamaged": "turret.model"}},
            gun={"models": {"undamaged": "gun.model"}},
        )
        completed = []

        with mock.patch.dict(sys.modules, {"BigWorld": bigworld}):
            profiler["_offh_fetch_vehicle_models"](
                descriptor, lambda refs: completed.append(refs)
            )
            self.assertEqual(4, len(fetches))
            self.assertEqual([], completed)
            for path, callback in reversed(fetches):
                callback("model:" + path)

        self.assertEqual(1, len(completed))
        self.assertEqual(
            {
                "chassis.model": "model:chassis.model",
                "hull.model": "model:hull.model",
                "turret.model": "model:turret.model",
                "gun.model": "model:gun.model",
            },
            completed[0],
        )

    def test_ai_deadlines_are_staggered_without_reducing_the_cadence(self):
        profiler = load_profiler(FakeTime())
        deadline = profiler["_offh_ai_cache_deadline"]
        now = 100.0
        interval = 0.1

        values = [deadline(now, entity_id, interval, 3, True)
                  for entity_id in range(1, 30)]

        self.assertEqual(29, len(set(round(value, 6) for value in values)))
        self.assertTrue(all(
            now + interval <= value < now + interval * 2.0 + 1e-9
            for value in values
        ))
        regular = deadline(values[0], 1, interval, 3, False)
        self.assertAlmostEqual(interval, regular - values[0], places=6)

    def test_frame_budget_caps_work_and_covers_all_29_bots(self):
        profiler = load_profiler(FakeTime())
        profiler["g_offh_battle_gen"] = 11
        bot_ids = list(range(1, 30))
        plans = [profiler["_offh_ai_frame_budget_plan"](bot_ids)
                 for _unused in range(5)]

        self.assertTrue(all(len(plan["order"]) <= 10 for plan in plans))
        self.assertTrue(all(len(plan["nav"]) <= 6 for plan in plans))
        self.assertTrue(all(len(plan["driver"]) <= 6 for plan in plans))
        self.assertTrue(all(len(plan["tree"]) <= 6 for plan in plans))
        self.assertTrue(all(plan["order_horizon"] > 0.0 for plan in plans))
        self.assertTrue(all(plan["nav_horizon"] > 0.0 for plan in plans))
        self.assertTrue(all(plan["driver_horizon"] > 0.0 for plan in plans))
        self.assertEqual(set(bot_ids), set().union(*(
            plan["order"] for plan in plans)))
        self.assertEqual(set(bot_ids), set().union(*(
            plan["nav"] for plan in plans)))
        self.assertEqual(set(bot_ids), set().union(*(
            plan["driver"] for plan in plans)))
        self.assertEqual(set(bot_ids), set().union(*(
            plan["tree"] for plan in plans)))

    def test_frame_budget_resets_with_battle_generation(self):
        profiler = load_profiler(FakeTime())
        profiler["g_offh_battle_gen"] = 3
        first = profiler["_offh_ai_frame_budget_plan"](range(1, 30))
        profiler["_offh_ai_frame_budget_plan"](range(1, 30))
        profiler["g_offh_battle_gen"] = 4
        reset = profiler["_offh_ai_frame_budget_plan"](range(1, 30))

        self.assertEqual(first, reset)

    def test_cold_and_changed_caches_still_obey_the_frame_quota(self):
        profiler = load_profiler(FakeTime())
        due = profiler["_offh_ai_refresh_due"]

        self.assertFalse(due(False, False, False, 0.0, 10.0, 0.2))
        self.assertTrue(due(True, False, False, 0.0, 10.0, 0.2))
        self.assertFalse(due(False, True, False, 9.0, 10.0, 0.2))
        self.assertTrue(due(True, True, False, 9.0, 10.0, 0.2))
        self.assertFalse(due(True, True, True, 11.0, 10.0, 0.2))
        self.assertTrue(due(True, True, True, 10.1, 10.0, 0.2))

        source = SOURCE.read_text()
        self.assertEqual(4, source.count("_offh_ai_refresh_due("))
        self.assertIn("'recovery_mode': 'budget_wait'", source)
        self.assertIn("drive_pos = _current_nav_pos", source)

    def test_profiler_samples_one_frame_in_four(self):
        fake_time = FakeTime()
        profiler = load_profiler(fake_time)
        profiler["g_offh_battle_gen"] = 7

        self.assertIsNone(profiler["_offh_perf_frame_begin"](29))
        self.assertIsNone(profiler["_offh_perf_frame_begin"](29))
        self.assertIsNone(profiler["_offh_perf_frame_begin"](29))
        started = profiler["_offh_perf_frame_begin"](29)
        self.assertEqual(10.0, started)

        fake_time.precise += 0.012
        profiler["_offh_perf_stop"]("driver", started)
        profiler["_offh_perf_count"]("collision_candidates", 17)
        state = profiler["g_offh_perf_state"]
        self.assertAlmostEqual(0.012, state["times"]["driver"], places=6)
        self.assertEqual(1, state["calls"]["driver"])
        self.assertEqual(17, state["calls"]["collision_candidates"])

    def test_profiler_wraps_the_known_bot_hot_paths(self):
        source = SOURCE.read_text()
        for stage in (
            "network_smoothing",
            "contacts",
            "contact_build",
            "contact_targets",
            "contact_foliage",
            "contact_cover",
            "artillery_arc",
            "artillery_rays",
            "nav_tick",
            "nav_server",
            "ai_order",
            "order_refresh",
            "order_deferred",
            "nav_target",
            "nav_refresh",
            "nav_deferred",
            "bot_loop",
            "traffic_snapshot",
            "driver",
            "driver_refresh",
            "driver_deferred",
            "direction",
            "physics",
            "physics_state",
            "physics_motion",
            "physics_ground",
            "physics_safety",
            "physics_rays",
            "drive_pitch_reuse",
            "drive_pitch_exact",
            "tilt_support_reuse",
            "bot_effects",
            "kinematics",
            "bot_audio",
            "pose_water",
            "terrain_support",
            "terrain_tilt",
            "tree_scan",
            "tree_deferred",
            "wall_collision",
            "wall_fast",
            "wall_exact",
            "tank_collision",
            "collision_candidates",
            "pose_commit",
            "visibility",
            "los",
            "network_publish",
        ):
            self.assertIn("'%s'" % stage, source)
        self.assertIn("PERF window=%.1fs role=%s bots=%d fps=%.1f", source)
        self.assertIn("_OFFH_PERF_REPORT_SECONDS = 5.0", source)

    def test_bot_ground_samples_are_reused_only_behind_pose_fences(self):
        source = SOURCE.read_text()

        self.assertIn("return (best, centre, front, back)", source)
        self.assertIn("def _support_drive_pitch(y, support, half_span):", source)
        self.assertIn("m_veh._offh_drive_support = (", source)
        self.assertIn("_bdx * _bdx + _bdz * _bdz <= 0.16", source)
        self.assertIn("abs(_bdy) <= 0.40 and abs(_bda) <= 0.10", source)
        self.assertIn("if _braw is None:", source)
        self.assertIn("_braw = _drive_pitch(", source)
        self.assertIn("_bsup if not _bsup_rejected else None, _bhl", source)
        self.assertIn("_offh_perf_count('tilt_support_reuse')", source)

    def test_driver_mode_diagnostics_are_refreshed_after_server_wait(self):
        source = SOURCE.read_text()
        block_start = source.index(
            "_driver_mode = _driver_order.get('recovery_mode', 'drive')"
        )
        block_end = source.index("_feeler_steer_yaw =", block_start)
        block = source[block_start:block_end]
        lines = block.splitlines()
        assignment = next(
            line for line in lines
            if "m_veh._offh_ai_driver_mode = _driver_mode" in line
        )
        wait_if = next(line for line in lines if "if _ai_server_wait:" in line)
        assignment_tabs = len(assignment) - len(assignment.lstrip("\t"))
        wait_tabs = len(wait_if) - len(wait_if.lstrip("\t"))

        self.assertEqual(wait_tabs, assignment_tabs)

    def test_driver_diagnostics_measure_sustained_full_throttle_speed(self):
        source = SOURCE.read_text()

        self.assertIn("m_veh._offh_ai_throttle = float(throttle)", source)
        self.assertIn("_offh_ai_full_throttle_seconds", source)
        self.assertIn("_driver['speed_pct']", source)
        self.assertIn("_driver['slow']", source)

    def test_traffic_wait_diagnostics_record_final_right_of_way_result(self):
        source = SOURCE.read_text()
        arbitration = source.index("_ai_driver.friendly_traffic_throttle(")
        waiting = source.index("if _waiting_for_traffic:", arbitration)
        mode = source.index(
            "m_veh._offh_ai_driver_mode = 'traffic_wait'", waiting
        )
        native_step = source.index("_native_body_manager.step,", mode)

        self.assertLess(arbitration, waiting)
        self.assertLess(waiting, mode)
        self.assertLess(mode, native_step)
        self.assertIn("'traffic_wait': 0", source)
        self.assertIn("_offh_perf_count('driver_traffic_wait')", source)

    def test_capture_rules_do_not_log_every_vehicle_distance_tick(self):
        source = SOURCE.read_text()

        self.assertNotIn("LOUD: Capture tick started running!", source)
        self.assertNotIn("LOUD: Distance to base", source)

    def test_navigation_uses_one_timestamp_and_spatial_broad_phase_per_frame(self):
        source = SOURCE.read_text()

        self.assertIn("_ai_now = BigWorld.time()", source)
        self.assertIn("_ai_navigator.tick, _ai_now", source)
        self.assertIn("'nav_target', _navigator.next_target, eid", source)
        self.assertIn("_current_nav_pos, drive_pos, _nav_key, _ai_now", source)
        self.assertIn("_traffic_spatial[0] = _VC.build_spatial_index", source)
        self.assertIn("_collision_spatial[0] = _VC.build_spatial_index", source)
        self.assertIn("_collision_cell_size = _collision_max_radius * 2.0 + 4.0", source)
        self.assertIn("_collision_bodies.get(oid)", source)
        self.assertIn("_offh_perf_count('collision_candidates', len(_candidate_ids))", source)
        self.assertIn("_candidate_ids = (_VC.nearby_ids", source)
        self.assertIn("_VC.unique_candidate_map(", source)
        self.assertIn("_python_collision_ids = []", source)
        self.assertIn("_python_collision_ids.append(int(_frame_eid))", source)
        self.assertIn("\n\t\t\t\t\t_python_collision_ids)", source)
        self.assertIn("_bot_collision_ids = _collision_candidates[0].get(eid)", source)
        self.assertIn("_bot_collision_ids == ()", source)
        self.assertIn("eid not in _tank_pair_pending", source)
        self.assertIn("_offh_perf_count('tank_collision_empty')", source)
        self.assertIn("'native_owner': _native_collision_owner", source)
        self.assertIn("_o_body.get('native_owner', False)", source)
        self.assertNotIn("_native_body_manager.reseed(", source)

    def test_base_defense_local_navigation_key_ignores_combat_target_changes(self):
        source = SOURCE.read_text()
        start = source.index("_nav_mode = _ai_order.get('combat_mode', 'route')")
        end = source.index("_navigator = _offh_ai_navigator", start)
        block = source[start:end]

        self.assertIn("_nav_mode == 'base_defense'", block)
        self.assertIn("_ai_order.get('defense_base_id')", block)
        self.assertIn("('local', int(eid), _nav_mode", block)

        native_hazard = source[
            source.index("# Native contact resolves terrain"):
            source.index("# Native destructible health", source.index(
                "# Native contact resolves terrain"
            ))
        ]
        self.assertIn("_offh_native_hazard_recovering", native_hazard)
        self.assertEqual(1, native_hazard.count(
            "_native_body_manager.hold("
        ))
        self.assertIn("elif not _native_hazard_recovering:", native_hazard)
        self.assertIn("if _native_hazard_recovering:", native_hazard)
        self.assertIn(
            "m_veh._offh_native_hazard_recovering = False",
            native_hazard,
        )
        self.assertIn("_offh_ai_probe_reject(", native_hazard)
        self.assertNotIn("guard_fault(", native_hazard)

    def test_native_hazard_recovery_requires_safe_fixed_endpoint_hysteresis(self):
        recovery_complete = load_native_hazard_recovery_complete()
        self.assertIsNotNone(
            recovery_complete,
            "native hazard recovery needs an explicit completion contract",
        )
        vehicle = types.SimpleNamespace(
            position=types.SimpleNamespace(x=-1.0, y=2.0, z=0.0),
            _offh_native_hazard_escape_endpoint=(-12.0, 2.0, 0.0),
        )

        # A safe footprint away from the fixed endpoint must not arm the timer.
        self.assertFalse(recovery_complete(vehicle, True, True, 10.0))
        self.assertIsNone(getattr(
            vehicle, "_offh_native_hazard_safe_since", None
        ))

        # Reaching the endpoint starts, but does not immediately complete, the
        # continuously-safe interval.
        vehicle.position.x = -11.0
        self.assertFalse(recovery_complete(vehicle, True, True, 20.0))
        self.assertEqual(20.0, vehicle._offh_native_hazard_safe_since)
        self.assertFalse(recovery_complete(vehicle, True, True, 20.24))
        self.assertTrue(recovery_complete(vehicle, True, True, 20.25))

        # Either footprint or water becoming unsafe resets hysteresis.
        self.assertFalse(recovery_complete(vehicle, False, True, 21.0))
        self.assertIsNone(vehicle._offh_native_hazard_safe_since)
        self.assertFalse(recovery_complete(vehicle, True, True, 21.1))
        self.assertFalse(recovery_complete(vehicle, True, False, 21.3))
        self.assertIsNone(vehicle._offh_native_hazard_safe_since)
        self.assertFalse(recovery_complete(vehicle, True, True, 21.4))
        self.assertTrue(recovery_complete(vehicle, True, True, 21.66))

        # Sliding out of endpoint tolerance also resets the interval.
        vehicle.position.x = -8.0
        self.assertFalse(recovery_complete(vehicle, True, True, 22.0))
        self.assertIsNone(vehicle._offh_native_hazard_safe_since)

    def test_native_hazard_transition_guards_clear_with_completion_contract(self):
        source = SOURCE.read_text()
        start = source.index("# Native contact resolves terrain")
        end = source.index("# Native destructible health", start)
        native_hazard = source[start:end]
        clear = native_hazard.index(
            "m_veh._offh_native_hazard_recovering = False"
        )

        self.assertIn("_offh_native_hazard_recovery_complete(", native_hazard)
        safety_start = native_hazard.index(
            "_native_recovery_baked_safe"
        )
        completion_start = native_hazard.index(
            "_offh_native_hazard_recovery_complete(", safety_start
        )
        safety_contract = " ".join(
            native_hazard[safety_start:completion_start].split()
        )
        self.assertIn(
            "_native_water <= _OFFH_AI_WATER_AVOID_DEPTH",
            safety_contract,
        )
        self.assertIn(
            "_native_recovery_baked_safe = _native_now_safe",
            safety_contract,
        )
        self.assertNotIn("_offh_ai_baked_footprint_safe", native_hazard)
        self.assertRegex(
            " ".join(native_hazard.split()),
            r"_offh_native_hazard_recovery_complete\(\s*m_veh,\s*"
            r"_native_recovery_baked_safe,\s*"
            r"_native_recovery_water_safe,\s*_ai_now\s*\)",
        )
        self.assertGreater(
            clear,
            native_hazard.rindex(
                "_offh_native_hazard_recovery_complete(", 0, clear
            ),
        )
        self.assertIn(
            "m_veh._offh_native_hazard_escape_endpoint = None",
            native_hazard[clear:],
        )
        self.assertIn(
            "m_veh._offh_native_hazard_safe_since = None",
            native_hazard[clear:],
        )

    def test_native_hazard_escape_target_is_nonzero_for_hold_and_missing_anchor(self):
        escape_target = load_native_hazard_escape_target()
        position = types.SimpleNamespace(x=4.0, y=2.0, z=8.0)

        anchored = types.SimpleNamespace(
            position=position,
            yaw=0.25,
            _offh_native_hazard_anchor=(4.0, 2.0, 8.0),
            _offh_native_hazard_entry_yaw=math.pi / 2.0,
        )
        missing = types.SimpleNamespace(
            position=position,
            yaw=math.pi,
            _offh_native_hazard_anchor=None,
            _offh_native_hazard_entry_yaw=0.0,
        )

        anchored_target = escape_target(anchored)
        missing_target = escape_target(missing)

        for target in (anchored_target, missing_target):
            distance = math.sqrt(
                (target[0] - position.x) ** 2 +
                (target[2] - position.z) ** 2
            )
            self.assertGreaterEqual(distance, 6.0)
        self.assertLess(anchored_target[0], position.x)
        self.assertAlmostEqual(position.z, anchored_target[2])
        self.assertAlmostEqual(position.x, missing_target[0])
        self.assertLess(missing_target[2], position.z)

    def test_native_hazard_escape_endpoint_does_not_flip_after_anchor_crossing(self):
        escape_target = load_native_hazard_escape_target()
        position = types.SimpleNamespace(x=0.0, y=2.0, z=0.0)
        vehicle = types.SimpleNamespace(
            position=position,
            yaw=0.0,
            _offh_native_hazard_anchor=(-1.0, 2.0, 0.0),
            _offh_native_hazard_entry_yaw=math.pi / 2.0,
        )

        first_target = escape_target(vehicle)
        self.assertEqual(
            first_target,
            getattr(vehicle, "_offh_native_hazard_escape_endpoint", None),
        )

        # Once the hull passes the anchor, recomputing anchor-current reverses
        # the direction and makes recovery oscillate. The first endpoint is an
        # immutable recovery-session target instead.
        position.x = -2.0
        self.assertEqual(first_target, escape_target(vehicle))

    def test_native_hazard_recovery_bypasses_tactical_and_navigation_holds(self):
        source = SOURCE.read_text()
        recovery_start = source.index(
            "# Safety recovery outranks a tactical hold"
        )
        driver_start = source.index("# Pure local driver:", recovery_start)
        driver_end = source.index("_perf_physics =", driver_start)
        recovery_driver = source[recovery_start:driver_end]

        self.assertIn(
            "_native_escape_target = _offh_native_hazard_escape_target(m_veh)",
            recovery_driver,
        )
        self.assertIn("drive_pos = _native_escape_target", recovery_driver)
        self.assertIn("_nav_paused = False", recovery_driver)
        self.assertIn("_ai_throttle_override = None", recovery_driver)
        self.assertIn("_ai_server_wait = False", recovery_driver)
        self.assertIn(
            "_native_hazard_recovery_pre or not (",
            recovery_driver,
        )
        self.assertIn(
            "_native_hazard_recovery_pre or _offh_ai_refresh_due(",
            recovery_driver,
        )
        self.assertLess(
            recovery_driver.index("drive_pos = _native_escape_target"),
            recovery_driver.index("dx = drive_pos[0]"),
        )
        self.assertIn("_ai_driver.drive, eid,", recovery_driver)
        self.assertIn(
            "lambda _driver_yaw: _offh_ai_direction_clear(",
            recovery_driver,
        )

    def test_native_hazard_escape_still_uses_local_driver_direction_probe(self):
        from test_bot_ai_driver import load_driver

        escape_target = load_native_hazard_escape_target()
        driver = load_driver().LocalDriver()
        position = types.SimpleNamespace(x=4.0, y=2.0, z=8.0)
        vehicle = types.SimpleNamespace(
            position=position,
            yaw=0.0,
            _offh_native_hazard_anchor=(4.0, 2.0, 8.0),
            _offh_native_hazard_entry_yaw=0.0,
        )
        probes = []

        target = escape_target(vehicle)
        order = driver.drive(
            17, (position.x, position.y, position.z), vehicle.yaw,
            0.0, 0.1, target, (),
            lambda candidate_yaw: probes.append(candidate_yaw) or True,
            movement_intent=True,
        )

        self.assertTrue(probes)
        self.assertNotEqual("arrived", order["recovery_mode"])
        self.assertNotIn(order["recovery_mode"], (
            "arrived", "nav_wait", "budget_wait"
        ))
        self.assertGreater(abs(order["turn"]), 0.0)

    def test_static_gun_yaw_limits_are_cached_per_vehicle(self):
        source = SOURCE.read_text()

        self.assertIn("m_veh, '_offh_ai_gun_yaw_limits', None", source)
        self.assertIn(
            "m_veh._offh_ai_gun_yaw_limits = _bot_gun_yaw_limits", source
        )

    def test_ai_planning_is_rate_limited_but_motion_stays_per_frame(self):
        source = SOURCE.read_text()

        self.assertIn("_offh_ai_order_cache", source)
        self.assertIn("_offh_nav_target_cache", source)
        self.assertIn("_offh_ai_driver_cache", source)
        self.assertIn("_offh_ai_cache_deadline", source)
        self.assertIn("_offh_ai_frame_budget_plan", source)
        self.assertIn("eid in _order_refresh_ids", source)
        self.assertIn("eid in _nav_refresh_ids", source)
        self.assertIn("eid in _driver_refresh_ids", source)
        self.assertIn("eid in _tree_refresh_ids", source)
        self.assertIn("_order_refresh_horizon", source)
        self.assertIn("_nav_refresh_horizon", source)
        self.assertIn("_driver_refresh_horizon", source)
        self.assertNotIn("_offh_ai_simulation_elapsed", source)
        self.assertNotIn("if _simulation_dt <= 0.0:", source)
        self.assertIn("_tank_pair_pending.clear()", source)
        self.assertIn("every live local bot must move continuously", source)
        self.assertIn("_perf_physics = _offh_perf_start()", source)
        self.assertIn("_PHY.longitudinal_step", source)

    def test_contact_and_cover_native_probes_are_sliced_across_frames(self):
        source = SOURCE.read_text()
        contact_start = source.index("def _offh_ai_refresh_contacts")
        contact_end = source.index("def _offh_battle_sweep", contact_start)
        contact = source[contact_start:contact_end]

        self.assertIn("_OFFH_AI_CONTACT_TARGETS_PER_FRAME = 2", source)
        self.assertIn("targets = targets[:_OFFH_AI_CONTACT_TARGETS_PER_FRAME]", contact)
        self.assertIn("_offh_ai_advance_artillery_arcs(now)", contact)
        self.assertIn("_OFFH_AI_ARTILLERY_CHORDS_PER_FRAME = 4", source)
        self.assertIn("arc_queue.request_lazy(", contact)
        self.assertIn("float(now) - last_cover >= 0.10", contact)
        self.assertIn("offset_index", contact)
        self.assertIn("bot_observation_due", contact)
        self.assertIn("_OFFH_AI_CONTACT_FULL_INTERVAL = 3.0", source)
        self.assertIn("network_contact_dirty.add(_contact_key)", contact)
        self.assertIn("network_contact_dirty.discard(_contact_key)", contact)
        self.assertIn("_OFFH_AI_DIAGNOSTICS_INTERVAL = 3.0", source)
        self.assertIn("if _diagnostics_due else None", contact)

    def test_bot_frame_reuses_stable_engine_context_without_slicing_motion(self):
        source = SOURCE.read_text()

        self.assertIn("_ai_space_id = _offh_bspace()", source)
        self.assertIn("_ai_driver = _offh_ai_driver()", source)
        self.assertIn(
            "m_veh, _driver_yaw, _ai_now, _ai_space_id", source
        )
        self.assertIn(
            "every live local bot must move continuously", source
        )

    def test_contact_foliage_is_evaluated_after_cheap_distance_ordering(self):
        source = SOURCE.read_text()
        contact_start = source.index("def _offh_ai_refresh_contacts")
        contact_end = source.index("def _offh_battle_sweep", contact_start)
        contact = source[contact_start:contact_end]

        distance_sort = contact.index("observer_distances.sort")
        foliage_query = contact.index("_offh_spot_detection_range(", distance_sort)
        closest_three = contact.index("if len(candidates) >= 3:", foliage_query)
        self.assertLess(distance_sort, foliage_query)
        self.assertLess(foliage_query, closest_three)
        self.assertNotIn("for entry in living:\n\t\tentry['_spot_view_range']", contact)
        self.assertIn("observer['_spot_view_range'] = _view_range", contact)

    def test_wall_sweep_precedes_expensive_slope_classification(self):
        source = SOURCE.read_text()
        wall_start = source.index("def _check_horizontal_collision")
        wall_end = source.index("def _offh_land_impact", wall_start)
        wall = source[wall_start:wall_end]

        lower_sweep = wall.index("_bottom_hits = []")
        fast_return = wall.index("_offh_perf_count('wall_fast')", lower_sweep)
        slope_profile = wall.index("_VC.drivable_rising_profile", fast_return)
        self.assertLess(lower_sweep, fast_return)
        self.assertLess(fast_return, slope_profile)
        self.assertIn(
            "_ahead = max(0.4, abs(vel) * max(0.0, dt) + 0.2)", wall
        )
        self.assertNotIn("_cw_fc", source)

    def test_network_replica_skips_authority_ai_but_keeps_collision_bodies(self):
        source = SOURCE.read_text()

        self.assertIn("_network_bot_role = _offh_network_bot_role(player)", source)
        self.assertIn(
            "_network_simulation_authority = (\n"
            "\t\t\t\t\t_network_bot_role in ('local', 'authority'))",
            source,
        )
        self.assertIn("if _network_simulation_authority:\n\t\t\t\t\t\t_ai_director", source)
        self.assertIn("_driver_frame[_frame_eid]", source)
        self.assertIn("_collision_bodies[_frame_eid]", source)
        self.assertIn(
            "if ((not _network_simulation_authority) and\n"
            "\t\t\t\t\t\t\t\t\t(getattr(m_veh, '_network_remote', False)",
            source,
        )

    def test_clear_road_driver_and_tree_scans_are_time_rate_limited(self):
        source = SOURCE.read_text()

        self.assertIn("_driver_mode_for_cache == 'drive'", source)
        self.assertIn("not _driver_neighbours", source)
        self.assertIn("_driver_interval = 0.145", source)
        self.assertIn("_driver_interval = 0.060", source)
        self.assertIn("_order_interval = (0.075 if _order_is_combat", source)
        self.assertIn("else 0.160)", source)
        self.assertIn("_offh_next_tree_scan", source)
        self.assertIn("_ai_now, eid, 0.150, 5", source)
        self.assertIn("'tree_scan', _fell_trees_near", source)

    def test_lineup_uses_retail_two_stage_model_loading(self):
        source = SOURCE.read_text()
        preparation = source.index("LAN lineup prepared")
        placement = source.index("def _begin_bot_placement", preparation)

        self.assertLess(preparation, placement)
        self.assertIn("_offh_load_hit_testers(_lineup_td)", source[:placement])
        self.assertIn("BigWorld.loadResourceListBG(", source[preparation:placement])
        self.assertIn("tuple(_lineup_model_paths)", source[preparation:placement])
        self.assertIn("_offh_fetch_vehicle_models(", source[preparation:placement])
        self.assertIn("BigWorld.fetchModel(path", source)
        self.assertNotIn(
            "BigWorld.loadResourceListBG((\n"
            "\t\t\t\t\t\t\ttd.chassis['models']['undamaged']",
            source,
        )

    def test_prefetched_entities_are_assembled_one_per_rendered_frame(self):
        source = SOURCE.read_text()
        spawn_start = source.index("def _spawn_next(_rest):")
        spawn_end = source.index("def _begin_bot_placement", spawn_start)
        spawn = source[spawn_start:spawn_end]

        self.assertIn("_offh_forced_model_refs", spawn)
        self.assertIn("_offh_battle_callback(0.0", spawn)
        self.assertIn("lambda _remaining=_rest[1:]: _spawn_next(_remaining)", spawn)
        self.assertNotIn("\n\t\t\t\t\t\t_spawn_next(_rest[1:])", spawn)
        self.assertNotIn("_offh_battle_callback(0.3", spawn)
        self.assertIn("LAN bot entities assembled", spawn)
        self.assertIn("LAN bot lineup ready", source)

    def test_direction_probe_accepts_crushables_and_verifies_hill_chords(self):
        source = SOURCE.read_text()
        probe_start = source.index("def _offh_ai_direction_clear")
        probe_end = source.index("def _offh_ai_class_tag", probe_start)
        probe = source[probe_start:probe_end]

        self.assertIn("_offh_ai_corridor_segment_clear", probe)
        self.assertIn("ground_profile", probe)
        self.assertIn("piece_start", probe)
        self.assertIn("piece_end", probe)
        self.assertIn("_offh_ai_probe_reject(vehicle, 'obstacle')", probe)

    def test_direction_probe_uses_only_proven_wide_baked_corridors_as_fast_path(self):
        source = SOURCE.read_text()
        probe_start = source.index("def _offh_ai_direction_clear")
        probe_end = source.index("def _offh_ai_class_tag", probe_start)
        probe = source[probe_start:probe_end]

        self.assertIn("_offh_ai_baked_open_corridor", source)
        self.assertIn("_baked_drive_clear", probe)
        self.assertIn("_baked_motion_clear", probe)

    def test_runtime_collision_and_ai_share_destructible_material_resolution(self):
        source = SOURCE.read_text()

        self.assertIn("def _offh_destructible_mat_passable", source)
        self.assertIn("AreaDestructibles.DESTR_TYPE_STRUCTURE", source)
        self.assertIn(
            "_mi = _offh_mat_info_for_segment_hit(\n\t\t\t\t\t\t\tspaceID, hit_pt, surface_normal)",
            source,
        )

    def test_bot_collision_checks_each_unordered_pair_once_in_stable_order(self):
        source = SOURCE.read_text()
        self.assertIn("for eid in sorted(mock_vehicles):", source)
        self.assertIn("_VC.unique_candidate_map(", source)
        start = source.index("def _tank_resolve")
        pair = source[start:source.index("def _drive_pitch", start)]
        self.assertEqual(1, pair.count("_tank_pair_seen[_pair] = True"))
        self.assertLess(
            pair.index("_tank_pair_seen[_pair] = True"),
            pair.index("_VC.vertical_overlap"),
        )

    def test_player_loop_reports_non_overlapping_major_stages(self):
        source = SOURCE.read_text()

        for stage in (
            "player_setup",
            "player_physics",
            "player_aim",
            "player_pose",
            "player_gun",
            "player_effects",
        ):
            self.assertIn("_offh_perf_stop('%s'" % stage, source)

    def test_native_bot_audio_and_exhaust_updates_are_rate_limited(self):
        source = SOURCE.read_text()

        self.assertIn("interval = 0.10", source)
        self.assertIn("_offh_last_sound_load", source)
        self.assertIn("_offhangar_exhaust_rate_index", source)
        self.assertIn("'bot_audio', _sync_bot_motion_sounds", source)

    def test_water_pose_probes_use_prebaked_hazard_broad_phase(self):
        source = SOURCE.read_text()

        self.assertIn("_initial_hazard = _offh_ai_baked_hazard_near", source)
        self.assertIn("_final_hazard = _offh_ai_baked_hazard_near", source)
        self.assertIn("_offh_ai_pose_water_depth(m_veh)", source)

    def test_profiler_emits_one_compact_note_after_five_seconds(self):
        fake_time = FakeTime()
        profiler = load_profiler(fake_time)
        profiler["g_offh_battle_gen"] = 9
        notes = []
        logging_module = types.ModuleType("gui.mods.offhangar.logging")
        logging_module.LOG_NOTE = notes.append
        modules = {
            "gui": types.ModuleType("gui"),
            "gui.mods": types.ModuleType("gui.mods"),
            "gui.mods.offhangar": types.ModuleType("gui.mods.offhangar"),
            "gui.mods.offhangar.logging": logging_module,
        }

        with mock.patch.dict(sys.modules, modules):
            for index in range(4):
                started = profiler["_offh_perf_frame_begin"](29)
                fake_time.precise += 0.010
                fake_time.wall += 1.3
                profiler["_offh_perf_frame_end"](
                    started, 0.050, types.SimpleNamespace()
                )

        self.assertEqual(1, len(notes))
        self.assertIn("role=offline bots=29", notes[0])
        self.assertIn("samples=1", notes[0])
        self.assertIn("callback=10.00ms", notes[0])


if __name__ == "__main__":
    unittest.main()
