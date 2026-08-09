import json
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922.lan_client import LANClient


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
        critical = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        self.assertTrue(self.client.send_hit(
            2, 4, 500, 2, 1, (1, 2, 3), critical,
            critical_target_base_revision=8,
            critical_target_ack_seq=3, hull_damage=120))
        self.assertTrue(self.client.send_bot_manifest([{'id': 1}] * 40))
        self.assertTrue(self.client.send_bot_state([{'id': 1}]))
        self.assertTrue(self.client.send_bot_observation([{}] * 70, [{}] * 20))
        self.assertTrue(self.client.send_bot_bot_hit(1, 2, 3, 120, 2))
        self.assertTrue(self.client.send_bot_ram(
            1, 'human', 2, 4, 20, 80))
        self.assertTrue(self.client.send_rules_state({'1': {'points': 10}}))
        self.assertTrue(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 17,
            'item_index': 4, 'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.5, 'speed': 12.0, 'is_shot': False}))
        self.assertTrue(self.client.send_battle_result(1, 'elimination'))
        self.assertEqual('leave_battle', self.sent[0]['type'])
        self.assertEqual('hit_report', self.sent[1]['type'])
        self.assertEqual(critical, self.sent[1]['critical'])
        self.assertEqual(8, self.sent[1][
            'critical_target_base_revision'])
        self.assertEqual(3, self.sent[1]['critical_target_ack_seq'])
        self.assertEqual(120, self.sent[1]['hull_damage'])
        self.assertEqual(30, len(self.sent[2]['bots']))
        self.assertEqual(64, len(self.sent[4]['contacts']))
        self.assertEqual(1, self.sent[5]['attacker_bot'])
        self.assertEqual('bot_ram_report', self.sent[6]['type'])
        self.assertEqual(80, self.sent[6]['damage_to_target'])
        self.assertEqual('rules_state', self.sent[7]['type'])
        self.assertEqual('destructible', self.sent[8]['type'])
        self.assertEqual('tree', self.sent[8]['destructible_kind'])
        self.assertEqual(17, self.sent[8]['chunk_id'])
        self.assertTrue(all(message['round_id'] == 7
                            for message in self.sent))

    def test_critical_hit_requires_exact_target_contract(self):
        critical = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}

        with self.assertRaises(ValueError):
            self.client.send_hit(2, 1, 100, 2, critical=critical)
        with self.assertRaises(ValueError):
            self.client.send_bot_hit(
                2, 1, 100, 2, critical=critical,
                critical_target_base_revision=True,
                critical_target_ack_seq=0, hull_damage=100)
        with self.assertRaises(ValueError):
            self.client.send_bot_human_hit(
                1, 2, 1, 100, 2, critical=critical,
                critical_target_base_revision=0,
                critical_target_ack_seq=0.5, hull_damage=100)
        with self.assertRaises(ValueError):
            self.client.send_bot_bot_hit(
                1, 2, 1, 100, 2, critical=critical,
                critical_target_base_revision=0,
                critical_target_ack_seq=0, hull_damage=-1)
        self.assertEqual([], self.sent)

    def test_destructible_report_requires_exact_identity_fields(self):
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 1.5,
            'item_index': 2}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'unknown', 'chunk_id': 1,
            'item_index': 2}))

    def test_input_carries_local_fire_or_drowning_state(self):
        critical = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}

        self.assertTrue(self.client.send_input(
            0, 0, speed=-12.5, reported_health=0,
            reported_critical=critical,
            reported_reason=5, reported_display_health=321,
            reported_attacker_bot=7,
            reported_critical_base_revision=3,
            reported_critical_seq=4))

        message = self.sent[-1]
        self.assertEqual(critical, message['reported_critical'])
        self.assertEqual(3, message['reported_critical_base_revision'])
        self.assertEqual(4, message['reported_critical_seq'])
        self.assertEqual(5, message['reported_reason'])
        self.assertEqual(321, message['reported_display_health'])
        self.assertEqual(7, message['reported_attacker_bot'])
        self.assertEqual(-12.5, message['speed'])

    def test_only_authority_can_send_bot_or_rule_messages(self):
        self.client.bot_authority_id = 2
        self.assertFalse(self.client.send_bot_manifest([{'id': 1}]))
        self.assertFalse(self.client.send_bot_state([{'id': 1}]))
        self.assertFalse(self.client.send_bot_observation([{}]))
        self.assertFalse(self.client.send_bot_human_hit(1, 2, 1, 100, 2))
        self.assertFalse(self.client.send_bot_bot_hit(1, 2, 1, 100, 2))
        self.assertFalse(self.client.send_bot_ram(
            1, 'human', 2, 1, 20, 80))
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

    def test_worker_sends_hello_before_exposing_connected_socket(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        sent = []
        outer = self

        class FakeSocket(object):
            def settimeout(self, unused_timeout):
                pass

            def connect(self, unused_address):
                pass

            def setsockopt(self, *unused_args):
                pass

            def sendall(self, payload):
                outer.assertFalse(client.connected)
                sent.append(json.loads(payload.decode('utf-8')))

            def recv(self, unused_size):
                return b''

            def close(self):
                pass

        import gui.mods.offline_lan_0922.lan_client as lan_client_module
        original_socket = lan_client_module.socket.socket
        lan_client_module.socket.socket = lambda *unused_args: FakeSocket()
        client.running = True
        try:
            client._worker()
        finally:
            lan_client_module.socket.socket = original_socket

        self.assertEqual('hello', sent[0]['type'])
        self.assertEqual(5, sent[0]['protocol'])
        self.assertEqual('wot-0.9.22.0.1-cn-1513',
                         sent[0]['client_build'])

    def test_pong_uses_network_receive_time_before_main_thread_delay(self):
        self.client.rtt_ms = None
        self.client._handle_message({
            'type': 'pong', 'client_time': 10.0,
            '_client_received_time': 10.025,
        })

        self.assertAlmostEqual(25.0, self.client.rtt_ms, places=3)

    def test_server_timing_projects_receive_time_and_half_rtt(self):
        self.client.rtt_ms = 100.0
        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}))

        self.assertAlmostEqual(114.95, self.client.combat_deadline, places=3)
        self.assertAlmostEqual(
            1014.95, self.client.combat_end_deadline, places=3)
        self.assertEqual('prebattle', self.client.combat_phase)

    def test_server_timing_rejects_inconsistent_payload(self):
        self.assertFalse(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900001, 'duration_ms': 900000}}))

    def test_snapshot_missing_bot_combat_contract_disconnects_before_runtime(self):
        self.client.running = True
        self.client._handle_message({
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1,
            'players': [{
                'id': 1, 'critical_revision': 0,
                'critical_base_revision': 0, 'critical_ack_seq': 0}],
            'bots': [{'id': 11, 'health': 500, 'alive': True}],
        })

        self.assertEqual('invalid snapshot message', self.client.last_error)
        self.assertEqual('disconnected', self.client.phase)
        self.assertIsNone(self.client.last_snapshot)

    def test_snapshot_rejects_non_exact_bot_combat_revisions(self):
        cases = (
            ('combat_revision', True),
            ('combat_base_revision', True),
            ('combat_ack_seq', True),
            ('combat_base_revision', 2),
            ('combat_ack_seq', -1),
            ('combat_fire_elapsed', float('nan')),
            ('combat_fire_elapsed', -0.1),
            ('combat_fire_elapsed', 10.1),
            ('combat_fire_timer', True),
            ('combat_fire_timer', 1.0),
        )
        for field, value in cases:
            client = LANClient(
                '127.0.0.1', 28782, 'P', 'ussr:MS-1')
            client.running = True
            client.ready = True
            client.phase = 'battle'
            client.round_id = 7
            client._send = lambda unused: True
            bot = {
                'id': 11, 'health': 500, 'alive': True,
                'critical': {},
                'combat_revision': 1, 'combat_base_revision': 1,
                'combat_ack_seq': 0,
                'combat_fire_elapsed': 0.0,
                'combat_fire_timer': 0.0,
            }
            bot[field] = value

            client._handle_message({
                'type': 'snapshot', 'protocol': 5, 'round_id': 7,
                'server_tick': 1,
                'players': [{
                    'id': 1, 'critical_revision': 0,
                    'critical_base_revision': 0, 'critical_ack_seq': 0}],
                'bots': [bot],
            })

            self.assertEqual(
                'invalid snapshot message', client.last_error,
                '%s=%r' % (field, value))
            self.assertEqual('disconnected', client.phase)

    def test_snapshot_rejects_missing_or_non_object_bot_critical(self):
        for critical in (None, [], 'broken'):
            client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
            client.running = True
            client.ready = True
            client.phase = 'battle'
            client.round_id = 7
            client._send = lambda unused: True
            bot = {
                'id': 11, 'health': 500, 'alive': True,
                'critical': critical,
                'combat_revision': 1, 'combat_base_revision': 1,
                'combat_ack_seq': 0,
                'combat_fire_elapsed': 0.0,
                'combat_fire_timer': 0.0,
            }
            if critical is None:
                bot.pop('critical')

            client._handle_message({
                'type': 'snapshot', 'protocol': 5, 'round_id': 7,
                'server_tick': 1,
                'players': [{
                    'id': 1, 'critical_revision': 0,
                    'critical_base_revision': 0,
                    'critical_ack_seq': 0}],
                'bots': [bot],
            })

            self.assertEqual('invalid snapshot message', client.last_error)
            self.assertEqual('disconnected', client.phase)

    def test_loading_ready_and_battle_live_form_one_transition(self):
        self.client.phase = 'loading'
        bases = {'1': [(-10.0, -20.0)], '2': [(10.0, 20.0)]}
        self.assertTrue(self.client.send_battle_ready(bases))
        self.assertEqual('battle_ready', self.sent[-1]['type'])
        self.assertEqual(bases, self.sent[-1]['bases'])

        self.client._handle_message({
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0,
            'state_revision': 5, 'countdown_seconds': 30.0,
            'battle_duration_seconds': 900.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 30000,
                'remaining_ms': 900000, 'duration_ms': 900000}})

        self.assertEqual('battle', self.client.phase)

    def test_older_timing_cannot_rewind_a_newer_snapshot_deadline(self):
        self.client.phase = 'loading'
        self.client.rtt_ms = 0.0
        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 1,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 14967,
                'remaining_ms': 900000, 'duration_ms': 900000}}))
        deadline = self.client.combat_deadline

        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            '_client_received_time': 100.5,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}))

        self.assertEqual(deadline, self.client.combat_deadline)
        self.assertEqual(1, self.client._combat_timing_tick)

    def test_newer_roster_revision_cannot_swallow_first_battle_live(self):
        events = []
        self.client.on_event = lambda kind, message: events.append(kind)
        self.client.phase = 'loading'
        self.client.state_revision = 5
        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 7,
            'state_revision': 7, 'phase': 'battle',
            'map': '01_karelia', 'host_player_id': 1,
            'players': [{'id': 1}]})
        live = {
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0, 'state_revision': 6,
            'countdown_seconds': 15.0,
            'battle_duration_seconds': 900.0,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}

        self.client._handle_message(live)
        self.client._handle_message(live)

        self.assertEqual('battle', self.client.phase)
        self.assertEqual(7, self.client.state_revision)
        self.assertEqual(7, self.client._battle_live_round_id)
        self.assertAlmostEqual(115.0, self.client.combat_deadline)
        self.assertEqual(['roster', 'battle_live'], events)

    def test_transition_repair_roster_replaces_failed_authority(self):
        self.client.player_id = 2
        self.client.phase = 'waiting'
        players = [
            {'id': 1, 'team': 1, 'slot': 0, 'name': 'Failed',
             'vehicle': 'ussr:MS-1', 'x': 0, 'y': 0, 'z': 0},
            {'id': 2, 'team': 2, 'slot': 0, 'name': 'Survivor',
             'vehicle': 'ussr:MS-1', 'x': 1, 'y': 0, 'z': 1},
        ]
        self.client._handle_message({
            'type': 'battle_start', 'protocol': 5, 'round_id': 7,
            'state_revision': 5, 'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': 1, 'players': players})
        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 7,
            'state_revision': 6, 'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 2,
            'bot_authority_id': 2, 'players': [players[1]]})

        self.assertEqual('loading', self.client.phase)
        self.assertEqual(6, self.client.state_revision)
        self.assertEqual(2, self.client.host_player_id)
        self.assertEqual(2, self.client.bot_authority_id)
        self.assertEqual([2], [player['id'] for player in self.client.roster])
        self.assertTrue(self.client.is_bot_authority())

    def test_same_round_waiting_roster_cannot_demote_accepted_battle(self):
        players = [{
            'id': 1, 'team': 1, 'slot': 0, 'name': 'P',
            'vehicle': 'ussr:MS-1', 'x': 0, 'y': 0, 'z': 0}]
        self.client._handle_message({
            'type': 'battle_start', 'protocol': 5, 'round_id': 7,
            'state_revision': 5,
            'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 1,
            'players': players})
        self.client._handle_message({
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0, 'state_revision': 6,
            'countdown_seconds': 15.0,
            'battle_duration_seconds': 900.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}})

        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 7,
            'state_revision': 7,
            'phase': 'waiting', 'map': '05_prohorovka',
            'host_player_id': 1, 'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Changed',
                'vehicle': 'ussr:MS-1', 'x': 1, 'y': 0, 'z': 1}]})

        self.assertEqual('battle', self.client.phase)
        self.assertEqual('01_karelia', self.client.map_name)
        self.assertEqual(players, self.client.roster)

if __name__ == '__main__':
    unittest.main()
