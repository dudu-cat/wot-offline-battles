import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/client/gui/mods/offhangar/spotting.py"
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
NETWORK = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"


def load_spotting():
    spec = importlib.util.spec_from_file_location("offhangar_spotting", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpottingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spotting = load_spotting()

    def test_100_percent_commander_preserves_nominal_view_range(self):
        self.assertAlmostEqual(
            400.0, self.spotting.effective_view_range(400.0), places=6
        )

    def test_old_equipment_and_skills_stack_on_uncapped_view_range(self):
        value = self.spotting.effective_view_range(
            400.0,
            commander_level=110.0,
            vision_factor=1.10,
            recon_level=100.0,
            situational_level=100.0,
            still_device_factor=1.25,
            still_device_active=True,
        )
        expected = (
            400.0
            * ((0.5 + 0.00375 * 110.0) / 0.875)
            * 1.10
            * 1.02
            * 1.03
            * 1.25
        )
        self.assertAlmostEqual(expected, value, places=6)
        self.assertGreater(value, self.spotting.MAX_SPOT_DISTANCE)

    def test_camouflage_reduces_detection_but_never_proximity_spot(self):
        detection = self.spotting.detection_distance(400.0, 0.25)
        self.assertAlmostEqual(312.5, detection, places=6)
        self.assertTrue(self.spotting.is_detected(50.0, 100.0, 0.95, False))
        self.assertFalse(self.spotting.is_detected(313.0, 400.0, 0.25, True))

    def test_movement_shot_and_old_multiplicative_devices_change_camo(self):
        still = self.spotting.effective_camouflage(
            0.12,
            0.20,
            moving=False,
            crew_skill_level=100.0,
            paint_factor=1.05,
            camouflage_net_factor=1.25,
            camouflage_net_active=True,
        )
        moving = self.spotting.effective_camouflage(
            0.12,
            0.20,
            moving=True,
            crew_skill_level=100.0,
            paint_factor=1.05,
            camouflage_net_factor=1.25,
            camouflage_net_active=True,
        )
        fired = self.spotting.effective_camouflage(
            0.12,
            0.20,
            moving=False,
            crew_skill_level=100.0,
            paint_factor=1.05,
            camouflage_net_factor=1.25,
            camouflage_net_active=True,
            shot_factor=0.25,
            fired_recently=True,
        )
        self.assertGreater(still, moving)
        self.assertGreater(moving, fired)

    def test_class_fallback_keeps_expected_concealment_order(self):
        light = self.spotting.class_camouflage({"lightTank"})
        heavy = self.spotting.class_camouflage({"heavyTank"})
        destroyer = self.spotting.class_camouflage({"AT-SPG"})
        self.assertGreater(light[0], heavy[0])
        self.assertGreater(destroyer[1], destroyer[0])

    def test_battle_ai_render_and_network_share_one_spotting_path(self):
        battle = BATTLE.read_text()
        network = NETWORK.read_text()
        self.assertIn("_offh_spot_detection_range(", battle)
        self.assertIn("for distance_sq, observer in candidates[:3]:", battle)
        self.assertIn("_offh_spot_visible_for_player", network)
        self.assertIn("_offh_spot_refresh_sixth_sense", battle)
        self.assertIn("spotting_player", battle)
        self.assertNotIn("g_offh_viewrange", battle)

    def test_combined_role_crew_can_supply_both_view_skills(self):
        battle = BATTLE.read_text()
        self.assertIn("result['recon_level'] = max", battle)
        self.assertIn("result['situational_level'] = max", battle)

    def test_spotting_reads_damage_without_nested_battle_closures(self):
        battle = BATTLE.read_text()
        self.assertIn("def _offh_spot_damage_vision_factor", battle)
        self.assertIn("module_stat_factor(", battle)
        self.assertIn(
            "vision_factor *= _offh_spot_damage_vision_factor", battle
        )

    def test_every_local_and_relayed_shot_breaks_camouflage(self):
        battle = BATTLE.read_text()
        self.assertGreaterEqual(
            battle.count("_offh_spot_last_shot = float(BigWorld.time())"), 3
        )


if __name__ == "__main__":
    unittest.main()
