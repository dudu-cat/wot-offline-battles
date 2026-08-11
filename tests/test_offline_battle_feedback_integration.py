import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
NETWORK = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"
LOADER = ROOT / "scripts/client/gui/mods/mod_offhangar.py"
PEN_INDICATOR = ROOT / "scripts/client/gui/mods/offhangar/pen_indicator.py"
PROJECTILE_RUNTIME = (
    ROOT / "scripts/client/gui/mods/offhangar/projectile_runtime.py"
)


class OfflineBattleFeedbackIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.battle_source = BATTLE.read_text()
        cls.network_source = NETWORK.read_text()
        cls.loader_source = LOADER.read_text()
        cls.pen_indicator_source = PEN_INDICATOR.read_text()
        cls.projectile_source = PROJECTILE_RUNTIME.read_text()

    def test_stock_sixth_sense_and_scout_message_paths_are_used(self):
        self.assertIn("battle.showSixthSenseIndicator(True)", self.battle_source)
        self.assertIn("'SPOTTED': 'ENEMY_SPOTTED'", self.battle_source)
        self.assertIn("panel.showMessage(message_type", self.battle_source)

    def test_ai_and_foliage_runtime_loggers_are_bound_globally(self):
        self.assertIn(
            "from gui.mods.offhangar.logging import LOG_DEBUG, LOG_ERROR, LOG_NOTE",
            self.battle_source,
        )
        self.assertIn(
            "LOG_NOTE('OfflineBattle BUILD %s' % _OFFH_BUILD)",
            self.battle_source,
        )
        foliage_start = self.battle_source.index("def _offh_spot_foliage(")
        foliage_end = self.battle_source.index(
            "def _offh_spot_detection_range(", foliage_start
        )
        foliage_source = self.battle_source[foliage_start:foliage_end]
        self.assertIn("LOG_NOTE('SPOTTING:", foliage_source)
        self.assertIn("LOG_ERROR('SPOTTING:", foliage_source)

    def test_result_screen_uses_observed_feedback_values(self):
        self.assertIn("_offh_feedback_results.result_values(", self.battle_source)
        self.assertIn("'damageDealt': total_dmg_dealt", self.battle_source)
        self.assertIn("'damageAssisted': (_feedback_values", self.battle_source)

    def test_lan_events_feed_the_same_local_statistics(self):
        self.assertGreaterEqual(
            self.network_source.count("record_network_combat_stats"), 3
        )
        self.assertIn("record_network_spot_assist", self.network_source)

    def test_capture_progress_is_owned_per_vehicle_and_reset_by_real_damage(self):
        self.assertIn("'capture_rules'", self.loader_source)
        self.assertIn("_capture_rules_tick.advance(", self.battle_source)
        self.assertIn("def _offh_drop_capture_for_vehicle(", self.battle_source)
        self.assertIn("'module or crew damage'", self.battle_source)
        self.assertIn("apply_network_capture_damage", self.network_source)
        self.assertIn("'critical': bool(critical)", self.network_source)

    def test_bot_hit_callback_resolves_player_in_its_own_scope(self):
        callback = self.battle_source.index(
            "def _resolve_bot_projectile_hit("
        )
        next_callback = self.battle_source.index(
            "def _resolve_player_projectile_hit(", callback
        )
        callback_source = self.battle_source[callback:next_callback]
        binding = callback_source.index(
            "player_mock = mock_vehicles.get(\n\t\t\t\t\tgetattr(player, 'playerVehicleID', -1))"
        )
        hit_branch = callback_source.index("if hit_veh == player_mock")

        self.assertLess(binding, hit_branch)
        self.assertIn(
            "send_authoritative_bot_human_hit(",
            callback_source[hit_branch:],
        )

    def test_bot_accuracy_uses_the_installed_gun_dispersion(self):
        self.assertIn(
            "_bot_gun.get('shotDispersionAngle', 0.03)",
            self.battle_source,
        )
        self.assertNotIn("sigma = 0.03 / 3.0", self.battle_source)

    def test_player_and_bot_shells_share_arrival_time_collision_runtime(self):
        self.assertIn(
            "from projectile_trajectory import computeProjectileTrajectory",
            self.battle_source,
        )
        self.assertGreaterEqual(
            self.battle_source.count("_offh_launch_live_projectile("), 3
        )
        self.assertIn("_offh_live_projectile_advance(", self.battle_source)
        self.assertIn(
            "compensate_segment_for_moving_target(", self.battle_source
        )
        self.assertIn("trajectory_position(", self.battle_source)
        self.assertIn("substep_boundaries(", self.battle_source)
        self.assertIn(
            "PROJECTILE_MAX_SUBSTEP_SECONDS = 0.025",
            self.projectile_source,
        )
        self.assertIn(
            "_bp_want = float(_artillery_solution['pitch'])",
            self.battle_source,
        )
        self.assertIn(
            "_bp_want = float(_direct_fire_solution['pitch'])",
            self.battle_source,
        )
        self.assertIn(
            "from projectile_trajectory import getShotAngles",
            self.battle_source,
        )
        active_shell = self.battle_source.index(
            "descr.activeGunShotIndex = _gun_state.get('shot_index', 0)"
        )
        native_solution = self.battle_source.index(
            "(shotTurretYaw, shotGunPitch) = getShotAngles(", active_shell
        )
        self.assertLess(active_shell, native_solution)
        self.assertIn("StrategicControlMode", self.battle_source)
        self.assertIn("_fired_bot_gravity = Math.Vector3(", self.battle_source)
        self.assertIn("_fired_gravity = Math.Vector3(", self.battle_source)
        self.assertNotIn(
            "return (r0 + v0.scale(2000.0), 2000.0 / speed)",
            self.battle_source,
        )

    def test_spawn_hides_components_and_uses_baked_ground_layer(self):
        self.assertIn("for _loaded_component in (ch, hu, tu, gu):", self.battle_source)
        self.assertIn("nearest_ground_point(_spawn_graph, _x, _z, 3)", self.battle_source)
        self.assertIn(
            "if _gc is not None:\n\t\t\t\t\t\t\t\t\t\t_gy = _gc[0].y",
            self.battle_source,
        )
        self.assertNotIn(
            "_gy = _gc[0].y if _gc is not None else _baked_y",
            self.battle_source,
        )

    def test_bot_pose_is_committed_before_native_consumers_are_registered(self):
        commit = self.battle_source.index("_VP.commit_pose(e_mock, e_mock.position")
        obstacle = self.battle_source.index("e_mock._collision_obstacle =")
        minimap = self.battle_source.index("minimap.notifyVehicleStart(e_mock.id)")

        self.assertLess(commit, obstacle)
        self.assertLess(commit, minimap)
        self.assertNotIn("m_veh.position = m_veh.model.position", self.battle_source)
        self.assertNotIn("ch.position = e_mock.position", self.battle_source)
        self.assertNotIn("m.position = m.model.position", self.pen_indicator_source)

    def test_bot_rejects_an_unclimbable_support_rise_without_popping_up(self):
        branch = self.battle_source.index("_VC.support_rise_is_obstacle(")
        end = self.battle_source.index(
            "elif m_veh.position.y <= _bg_y", branch
        )
        branch_source = self.battle_source[branch:end]

        self.assertIn("_offh_ai_tick_dry_pose", branch_source)
        self.assertIn("remember_failure(eid, target_yaw, 5.0)", branch_source)
        self.assertIn("_offh_ai_probe_reject(m_veh, 'obstacle')", branch_source)
        self.assertNotIn("Math.Vector3(m_veh.position.x, _bc_y", branch_source)

    def test_realised_wall_and_tank_contacts_invalidate_driver_orders(self):
        wall = self.battle_source.index("if _hit_wall:")
        wall_end = self.battle_source.index("else:", wall)
        wall_source = self.battle_source[wall:wall_end]
        self.assertIn("_offh_ai_probe_reject(m_veh, 'obstacle')", wall_source)
        self.assertIn("eid, target_yaw, 5.0", wall_source)

        tank = self.battle_source.index("if abs(_btr[0]) + abs(_btr[1]) > 0.01:")
        tank_source = self.battle_source[tank:tank + 900]
        self.assertIn("m_veh._offh_ai_driver_cache = None", tank_source)
        self.assertIn("eid, target_yaw, 0.8", tank_source)

    def test_bot_spawn_stages_cosmetic_stickers_and_batches_roster_refresh(self):
        self.assertIn("_sticker_setup_done = False", self.battle_source)
        self.assertIn("_offh_queue_sticker_warmup(player, e_mock)", self.battle_source)
        self.assertIn("_offh_battle_callback(0.03, _drain_one)", self.battle_source)
        self.assertIn("_target_sticker_map(target_mock, component_name=None)", self.battle_source)
        self.assertIn("_offh_auto_spawn_completed >= int(getattr(", self.battle_source)

    def test_lan_countdown_and_duration_use_server_deadlines(self):
        self.assertIn("_offhangar_network_combat_deadline", self.battle_source)
        self.assertIn("_offh_server_battle_remaining(player, 900.0)", self.battle_source)
        self.assertIn("self._load_server_timing(message)", self.network_source)

    def test_lan_prepares_lineup_behind_loading_page_without_gating_countdown(self):
        self.assertIn("_auto_spawn_not_before = time.time() + _auto_spawn_delay", self.battle_source)
        self.assertIn("0.25, _auto_spawn_teams", self.battle_source)
        self.assertIn("_place_delay = max(0.0, _spawn_not_before - time.time())", self.battle_source)
        self.assertNotIn("_offh_local_lineup_ready", self.battle_source)
        self.assertNotIn("loading screen waiting for local bot resources", self.battle_source)

    def test_forced_lineup_vehicle_skips_random_candidate_scan(self):
        forced = self.battle_source.index("if _fv:")
        candidate_scan = self.battle_source.index("for nation in nations.AVAILABLE_NAMES", forced)
        fallback = self.battle_source.rfind("else:", forced, candidate_scan)
        self.assertGreater(fallback, forced)


if __name__ == "__main__":
    unittest.main()
