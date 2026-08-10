import unittest
import sys
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


class OfflineBattleProfilerTests(unittest.TestCase):
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
        self.assertIn("_candidate_ids = _VC.nearby_ids", source)

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
        self.assertIn("arc_queue.request(", contact)
        self.assertIn("float(now) - last_cover >= 0.10", contact)
        self.assertIn("offset_index", contact)
        self.assertIn("bot_observation_due", contact)

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

        self.assertIn("_is_network_replica = not network_is_authority(player)", source)
        self.assertIn("if not _is_network_replica:\n\t\t\t\t\t\t_ai_director", source)
        self.assertGreaterEqual(source.count("if not _is_network_replica:"), 3)
        self.assertIn("_driver_frame[_frame_eid]", source)
        self.assertIn("_collision_bodies[_frame_eid]", source)
        self.assertIn(
            "if (_is_network_replica and\n"
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
            "_mi = _offh_mat_info_for_segment_hit(\n\t\t\t\t\t\t\tspaceID, seg_start, hit_pt)",
            source,
        )

    def test_bot_collision_checks_each_unordered_pair_once_in_stable_order(self):
        source = SOURCE.read_text()
        self.assertIn("for eid in sorted(mock_vehicles):", source)
        start = source.index("def _tank_resolve")
        pair = source[start:source.index("def _drive_pitch", start)]
        self.assertEqual(1, pair.count("_tank_pair_seen[_pair] = True"))
        self.assertLess(
            pair.index("_tank_pair_seen[_pair] = True"),
            pair.index("_VC.vertical_overlap"),
        )

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
