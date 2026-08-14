from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import foliage


def _row(x, strength=0.15):
    # Unit horizontal OBB, y=-1..3, identity inverse transform.
    return [x, -1.0, 0.0, 3.0, 1.0, 0.0, 0.0, 1.0,
            strength, 1.0]


class FoliageTests(unittest.TestCase):

    def test_pair_specific_segment_intersects_or_misses_same_bush(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [_row(5.0)], 'cells': {'0,0': [0]}})

        self.assertEqual(
            0.15, foliage_map.camouflage_bonus(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))
        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(
                (0.0, 0.0, 5.0), (10.0, 0.0, 5.0)))

    def test_stacked_bushes_cap_at_sixty_percent(self):
        rows = [_row(x) for x in (2.0, 4.0, 6.0, 8.0, 10.0)]
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': rows, 'cells': {'0,0': list(range(len(rows)))}})

        self.assertEqual(
            0.60, foliage_map.camouflage_bonus(
                (0.0, 0.0, 0.0), (12.0, 0.0, 0.0)))

    def test_recent_shot_ignores_bush_near_target(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [_row(8.0)], 'cells': {'0,0': [0]}})

        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), True))


if __name__ == '__main__':
    unittest.main()
