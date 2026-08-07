import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
NETWORK = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"


class OfflineBattleFeedbackIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.battle_source = BATTLE.read_text()
        cls.network_source = NETWORK.read_text()

    def test_stock_sixth_sense_and_scout_message_paths_are_used(self):
        self.assertIn("battle.showSixthSenseIndicator(True)", self.battle_source)
        self.assertIn("'SPOTTED': 'ENEMY_SPOTTED'", self.battle_source)
        self.assertIn("panel.showMessage(message_type", self.battle_source)

    def test_result_screen_uses_observed_feedback_values(self):
        self.assertIn("_offh_feedback_results.result_values(", self.battle_source)
        self.assertIn("'damageDealt': total_dmg_dealt", self.battle_source)
        self.assertIn("'damageAssisted': (_feedback_values", self.battle_source)

    def test_lan_events_feed_the_same_local_statistics(self):
        self.assertGreaterEqual(
            self.network_source.count("record_network_combat_stats"), 3
        )
        self.assertIn("record_network_spot_assist", self.network_source)

    def test_spawn_hides_components_and_uses_baked_ground_layer(self):
        self.assertIn("for _loaded_component in (ch, hu, tu, gu):", self.battle_source)
        self.assertIn("nearest_ground_point(_spawn_graph, _x, _z, 3)", self.battle_source)

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
        self.assertIn("not _offh_local_lineup_ready(player)", self.battle_source)
        self.assertIn("loading screen waiting for local bot resources", self.battle_source)

    def test_forced_lineup_vehicle_skips_random_candidate_scan(self):
        forced = self.battle_source.index("if _fv:")
        candidate_scan = self.battle_source.index("for nation in nations.AVAILABLE_NAMES", forced)
        fallback = self.battle_source.rfind("else:", forced, candidate_scan)
        self.assertGreater(fallback, forced)


if __name__ == "__main__":
    unittest.main()
