import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922.battle_rpc import BattleRpc


class BattleRpcTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.rpc = BattleRpc(self.messages.append)

    def test_avatar_base_and_cell_emit_explicit_plain_commands(self):
        self.rpc.bind(42)
        self.rpc.avatar_base.setClientReady()
        self.rpc.avatar_base.vehicle_moveWith(5)
        self.rpc.avatar_base.setCruiseControlMode(2)
        self.rpc.avatar_base.vehicle_trackWorldPointWithGun((1, 2, 3))
        self.rpc.avatar_base.vehicle_stopTrackingWithGun(0.2, -0.1)
        self.rpc.avatar_base.vehicle_shoot(True)
        self.rpc.avatar_base.leaveArena({'win': False})
        self.assertEqual('bindToVehicle', self.messages[0]['method'])
        self.assertEqual('setClientReady', self.messages[1]['method'])
        self.assertEqual(42, self.messages[2]['vehicle_id'])
        self.assertEqual({'x': 1.0, 'y': 2.0, 'z': 3.0}, self.messages[4]['point'])
        self.assertTrue(self.messages[6]['is_repeat'])

    def test_vehicle_and_settings_calls_do_not_touch_entities(self):
        self.rpc.bind(3)
        self.rpc.vehicle_cell.moveWith(1)
        self.rpc.vehicle_cell.trackWorldPointWithGun((0, 0, 5))
        self.rpc.avatar_base.changeIntUserSettings(12, [1, 2])
        self.rpc.avatar_base.changeIntUserSettings(13, [3], delete=True)
        self.assertEqual(['moveWith', 'trackWorldPointWithGun',
                          'addIntUserSettings', 'deleteIntUserSettings'],
                         [item['method'] for item in self.messages[1:]])

    def test_unknown_calls_are_not_successful_noops(self):
        with self.assertRaises(AttributeError):
            self.rpc.avatar_base.teleportTo((0, 0, 0))
        with self.assertRaises(RuntimeError):
            BattleRpc(self.messages.append).avatar_base.vehicle_shoot()


if __name__ == '__main__':
    unittest.main()
