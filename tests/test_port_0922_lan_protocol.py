import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922.lan_client import LANClient
from gui.mods.offline_lan_0922.battle_rpc_translator import BattleRpcTranslator


class LanProtocolTests(unittest.TestCase):
    def setUp(self):
        self.client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        self.client.ready = True
        self.client.phase = 'battle'
        self.client.round_id = 7
        self.client.state_revision = 4
        self.client.player_id = 1
        self.client.host_player_id = 1
        self.client.bot_authority_id = 1
        self.sent = []
        def send(message):
            self.sent.append(message)
            return True
        self.client._send = send

    def test_v5_explicit_control_messages(self):
        self.assertTrue(self.client.leave_battle())
        self.assertTrue(self.client.send_hit(2, 4, 500, 2, 1, (1, 2, 3)))
        self.assertTrue(self.client.send_bot_manifest([{'id': 1}] * 40))
        self.assertTrue(self.client.send_bot_state([{'id': 1}]))
        self.assertTrue(self.client.send_bot_observation([{}] * 70, [{}] * 20))
        self.assertTrue(self.client.send_bot_bot_hit(1, 2, 3, 120, 2))
        self.assertTrue(self.client.send_rules_state({'1': {'points': 10}}))
        self.assertTrue(self.client.send_battle_result(1, 'elimination'))
        self.assertEqual('leave_battle', self.sent[0]['type'])
        self.assertEqual('hit_report', self.sent[1]['type'])
        self.assertEqual(30, len(self.sent[2]['bots']))
        self.assertEqual(64, len(self.sent[4]['contacts']))
        self.assertEqual(1, self.sent[5]['attacker_bot'])
        self.assertEqual('rules_state', self.sent[6]['type'])
        self.assertTrue(all(message['round_id'] == 7
                            for message in self.sent))

    def test_only_authority_can_send_bot_or_rule_messages(self):
        self.client.bot_authority_id = 2
        self.assertFalse(self.client.send_bot_manifest([{'id': 1}]))
        self.assertFalse(self.client.send_bot_state([{'id': 1}]))
        self.assertFalse(self.client.send_bot_observation([{}]))
        self.assertFalse(self.client.send_bot_human_hit(1, 2, 1, 100, 2))
        self.assertFalse(self.client.send_bot_bot_hit(1, 2, 1, 100, 2))
        self.assertFalse(self.client.send_rules_state({}))
        self.assertFalse(self.client.send_battle_result(1, 'elimination'))
        self.assertTrue(self.client.send_bot_hit(1, 1, 100, 2))
        self.assertEqual('bot_hit_report', self.sent[0]['type'])

    def test_failed_fire_send_does_not_create_sequence_gap(self):
        self.client._send = lambda unused_message: False

        self.assertIsNone(self.client.send_fire())
        self.assertEqual(0, self.client._fire_seq)

        self.client._send = lambda message: self.sent.append(message) or True
        self.assertEqual(1, self.client.send_fire())
        self.assertEqual(1, self.sent[-1]['fire_seq'])

    def test_same_round_waiting_roster_cannot_demote_accepted_battle(self):
        players = [{
            'id': 1, 'team': 1, 'slot': 0, 'name': 'P',
            'vehicle': 'ussr:MS-1', 'x': 0, 'y': 0, 'z': 0}]
        self.client._handle_message({
            'type': 'battle_start', 'protocol': 5, 'round_id': 7,
            'state_revision': 5,
            'map': '01_karelia', 'host_player_id': 1,
            'players': players})

        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 7,
            'state_revision': 6,
            'phase': 'waiting', 'map': '05_prohorovka',
            'host_player_id': 1, 'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Changed',
                'vehicle': 'ussr:MS-1', 'x': 1, 'y': 0, 'z': 1}]})

        self.assertEqual('battle', self.client.phase)
        self.assertEqual('01_karelia', self.client.map_name)
        self.assertEqual(players, self.client.roster)

    def test_battle_rpc_translator_only_maps_existing_input_fields(self):
        translator = BattleRpcTranslator(self.client, lambda: ((10, 0, 20), 0.5))
        self.assertTrue(translator.translate({'kind': 'battle_rpc',
            'method': 'vehicle_moveWith', 'flags': 9}))
        self.assertTrue(translator.translate({'kind': 'battle_rpc',
            'method': 'vehicle_stopTrackingWithGun', 'turret_yaw': 0.3,
            'gun_pitch': -0.1}))
        shot = translator.translate({'kind': 'battle_rpc',
            'method': 'vehicle_shoot'})
        self.assertEqual(1, shot)
        self.assertEqual('input', self.sent[0]['type'])
        self.assertEqual(1.0, self.sent[0]['forward'])
        self.assertEqual(1.0, self.sent[0]['turn'])
        self.assertEqual('input', self.sent[2]['type'])
        self.assertEqual(1, self.sent[2]['fire_seq'])
        self.assertFalse(translator.translate({'kind': 'battle_rpc',
            'method': 'setClientReady'}))


if __name__ == '__main__':
    unittest.main()
