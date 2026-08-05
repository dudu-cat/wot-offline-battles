import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.ai import cover, maps
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid
from lan_battle_server import MAP_POOL


class BotAiPortTests(unittest.TestCase):
    def test_preserves_annotated_standard_maps(self):
        # The server pool is the exact #1513 standard-mode candidate set.
        # TACTICAL_MAPS also retains older annotations that are useful for
        # other supported clients, so it may be a strict superset.
        self.assertTrue(set(MAP_POOL).issubset(set(maps.TACTICAL_MAPS)))
        karelia = maps.get_tactical_map('spaces/01_karelia')
        self.assertEqual('01_karelia', karelia['name'])
        self.assertTrue(karelia['routes'][1])
        self.assertTrue(karelia['routes'][2])

    def test_cover_contract_is_plain_data_and_deterministic(self):
        result = cover.score_candidates([{
            'id': 'ridge', 'position': (1, 2, 3), 'travel_distance': 5,
            'route_alignment': 1, 'enemy_occlusion': 1, 'exposure': 0,
            'slope': 0, 'water': 0, 'ally_congestion': 0,
            'peek_feasible': True, 'peek_position': (2, 2, 3),
            'escape_feasible': True,
        }])
        self.assertEqual('ridge', result[0]['id'])
        self.assertEqual({'x': 1.0, 'y': 2.0, 'z': 3.0}, result[0]['position'])

    def test_adapter_returns_no_engine_objects(self):
        descriptor = {'type': {'name': 'MS-1', 'tags': ('mediumTank',)},
                      'physics': {'speedLimits': (18.0,)}, 'hull': {},
                      'turret': {}, 'gun': {'shots': ()}}
        adapter = BotAdapter('01_karelia', 7)
        adapter.register(1, 1, descriptor)
        order = adapter.decide({
            'id': 1, 'position': (0, 0, 0), 'yaw': 0, 'speed': 0,
            'dt': 0.05, 'now': 1, 'health': 100, 'max_health': 100,
            'contacts': (), 'neighbours': (),
        }, lambda yaw: True)
        self.assertEqual(1, order['bot_id'])
        self.assertIn('throttle', order)
        self.assertIsInstance(order['move_position'], tuple)

    def test_navigation_accepts_caller_probes(self):
        grid = TerrainGrid(lambda x, z, hint_y: 0.0,
                           bounds=(-50, -50, 50, 50))
        path = grid.plan((0, 0, 0), (30, 0, 30))
        self.assertTrue(path)


if __name__ == '__main__':
    unittest.main()
