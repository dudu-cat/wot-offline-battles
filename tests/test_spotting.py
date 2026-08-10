import importlib.util
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/client/gui/mods/offhangar/spotting.py"
CAMOUFLAGE_DATA = ROOT / "scripts/client/gui/mods/offhangar/vehicle_camouflage.py"
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
NETWORK = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"
ENTRY = ROOT / "scripts/client/gui/mods/mod_offhangar.py"


def load_spotting():
    spec = importlib.util.spec_from_file_location("offhangar_spotting", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_camouflage_data():
    spec = importlib.util.spec_from_file_location(
        "offhangar_vehicle_camouflage", CAMOUFLAGE_DATA
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpottingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spotting = load_spotting()
        cls.camouflage_data = load_camouflage_data()

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

    def test_vehicle_camouflage_is_per_vehicle_not_per_class(self):
        type62 = self.camouflage_data.camouflage_for_vehicle(
            "china:Ch02_Type62"
        )
        amx_1390 = self.camouflage_data.camouflage_for_vehicle(
            "france:AMX_13_90"
        )
        self.assertIsNotNone(type62)
        self.assertIsNotNone(amx_1390)
        self.assertNotEqual(type62, amx_1390)
        self.assertEqual(
            self.camouflage_data.DATA_COVERED_VEHICLES,
            len(self.camouflage_data.VEHICLE_CAMOUFLAGE),
        )

    def test_missing_camouflage_data_is_not_replaced_by_a_class_average(self):
        battle = BATTLE.read_text()
        spotting = SOURCE.read_text()
        self.assertIn("raise ValueError(message)", battle)
        self.assertIn("missing per-vehicle camouflage data", battle)
        self.assertNotIn("class-fallback", battle)
        self.assertNotIn("CLASS_CAMOUFLAGE", spotting)

    def test_raw_camouflage_has_nonzero_baseline_without_crew_skill(self):
        self.assertAlmostEqual(
            4.0 / 7.0, self.spotting.crew_camouflage_factor(0.0), places=6
        )
        self.assertAlmostEqual(
            1.0, self.spotting.crew_camouflage_factor(100.0), places=6
        )

    def test_battle_ai_render_and_network_share_one_spotting_path(self):
        battle = BATTLE.read_text()
        network = NETWORK.read_text()
        self.assertIn("_offh_spot_detection_range(", battle)
        self.assertIn("for distance_sq, observer in candidates[:3]:", battle)
        self.assertIn("_offh_spot_visible_for_player", network)
        self.assertIn("_offh_spot_refresh_sixth_sense", battle)
        self.assertIn("spotting_player", battle)
        self.assertNotIn("g_offh_viewrange", battle)

    def test_foliage_is_pair_specific_and_native_probe_is_removed(self):
        battle = BATTLE.read_text()
        entry = ENTRY.read_text()
        self.assertIn("foliage_map.camouflage_bonus(", battle)
        self.assertIn("observer['position'], target['position']", battle)
        self.assertIn("load_foliage(map_name)", battle)
        self.assertNotIn("wg_visibilityCollideSegment", battle)
        self.assertNotIn("VISIBILITY_PROBE", battle)
        self.assertIn("'foliage', 'prebaked_foliage'", entry)

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

    def test_new_mock_with_none_still_timestamp_does_not_abort_spotting(self):
        battle = BATTLE.read_text()
        start = battle.index("def _offh_spot_motion(vehicle, now):")
        end = battle.index("\ndef _offh_spot_damage_vision_factor", start)
        function_source = battle[start:end].replace(
            "\tfrom gui.mods.offhangar import spotting\n", ""
        )
        namespace = {
            "spotting": types.SimpleNamespace(
                STILL_DEVICE_DELAY_SECONDS=3.0,
                MOVING_SPEED_EPSILON=0.1,
            )
        }
        exec(function_source, namespace)
        vehicle = types.SimpleNamespace(
            _veh_velocity=0.0,
            _offh_spot_still_since=None,
        )

        moving, still_for = namespace["_offh_spot_motion"](vehicle, 100.0)

        self.assertFalse(moving)
        self.assertEqual(0.0, still_for)
        self.assertEqual(100.0, vehicle._offh_spot_still_since)


if __name__ == "__main__":
    unittest.main()
