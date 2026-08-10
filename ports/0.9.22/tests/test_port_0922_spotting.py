from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
CLIENT_SCRIPTS = (
    ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import spotting


class SpottingTests(unittest.TestCase):

    def test_base_camouflage_uses_legacy_crew_curve_and_additive_paint(self):
        moving, still = spotting.base_camouflage(
            0.288, 0.300, crew_skill_level=0.0,
            invisibility_factor=1.0, paint_bonus=0.03)

        self.assertAlmostEqual(0.288 * (4.0 / 7.0) + 0.03, moving)
        self.assertAlmostEqual(0.300 * (4.0 / 7.0) + 0.03, still)

    def test_net_and_shot_apply_to_vehicle_before_foliage(self):
        result = spotting.effective_camouflage(
            (0.20, 0.30), moving=False,
            camouflage_net_bonus=0.10, camouflage_net_active=True,
            shot_factor=0.25, fired_recently=True,
            foliage_bonus=0.15)

        self.assertAlmostEqual((0.30 + 0.10) * 0.25 + 0.15, result)

    def test_detection_distance_keeps_floor_and_ceiling(self):
        self.assertEqual(67.5, spotting.detection_distance(400.0, 0.95))
        self.assertEqual(225.0, spotting.detection_distance(400.0, 0.5))
        self.assertEqual(500.0, spotting.detection_distance(700.0, 0.0))
        self.assertTrue(spotting.is_detected(50.0, 50.0, 0.95, False))


if __name__ == '__main__':
    unittest.main()
