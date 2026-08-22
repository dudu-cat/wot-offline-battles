import json
import math
import socket
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922 import lan_client as module
from gui.mods.offline_lan_0922.lan_client import LANClient


class RecordingSocket(object):
    def __init__(self):
        self.sent = []
        self.closed = False

    def sendall(self, payload):
        self.sent.append(payload)

    def settimeout(self, unused_timeout):
        pass

    def connect(self, unused_address):
        pass

    def setsockopt(self, *unused_args):
        pass

    def close(self):
        self.closed = True


def wire_copy(value):
    return json.loads(json.dumps(value, separators=(',', ':')))


class ProjectileWireTests(unittest.TestCase):
    def active_client(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.sock = RecordingSocket()
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = 'battle'
        client.round_id = 3
        client.player_id = 7
        client.bot_authority_id = 7
        client.authority_epoch = 4
        client.capabilities = [module.PROJECTILE_LEDGER_CAPABILITY]
        with client._outbound_lock:
            client._outbound_accepting = True
        return client

    @staticmethod
    def launch(client, shooter_kind='player', shooter_id=7,
               shot_seq=None, authority_epoch=None, **overrides):
        values = {
            'shell_index': 2,
            'origin': [1.0, 2.0, 3.0],
            'velocity': [100.0, 20.0, -30.0],
            'gravity': 9.81,
            'max_distance': 720.0,
            'max_time_ms': 5000,
            'is_he': True,
            'splash_radius': 4.5,
            'penetration_factor': 1.25,
        }
        values.update(overrides)
        return client.send_projectile_launch(
            shooter_kind, shooter_id, shot_seq, values['shell_index'],
            values['origin'], values['velocity'], values['gravity'],
            values['max_distance'], values['max_time_ms'], values['is_he'],
            values['splash_radius'], authority_epoch=authority_epoch,
            penetration_factor=values['penetration_factor'])

    def test_player_launch_is_frozen_fifo_wire_and_commits_sequence(self):
        client = self.active_client()
        origin = [1.0, 2.0, 3.0]

        self.assertEqual(1, self.launch(client, origin=origin))
        origin[0] = 999.0
        self.assertEqual(1, client._fire_seq)
        self.assertEqual(1, len(client._outbound_queue))
        frozen = client._outbound_queue[0][1]
        self.assertEqual((1.0, 2.0, 3.0), frozen['origin'])

        self.assertTrue(client._send_wire(
            frozen, client.sock, client._transport_generation))
        self.assertTrue(client.sock.sent[0].endswith(b'\n'))
        message = json.loads(client.sock.sent[0].decode('utf-8'))
        self.assertEqual({
            'type', 'round_id', 'shooter_kind', 'shooter_id', 'shot_seq',
            'shell_index', 'origin', 'velocity', 'gravity', 'max_distance',
            'max_time_ms', 'is_he', 'splash_radius', 'penetration_factor',
        }, set(message))
        self.assertEqual('player', message['shooter_kind'])
        self.assertEqual(7, message['shooter_id'])
        self.assertEqual(1, message['shot_seq'])
        self.assertEqual([1.0, 2.0, 3.0], message['origin'])

    def test_failed_player_enqueue_rolls_back_sequence(self):
        client = self.active_client()
        client._send = lambda unused_message: False

        self.assertIsNone(self.launch(client))
        self.assertEqual(0, client._fire_seq)

        client._send = lambda unused_message: True
        self.assertEqual(1, self.launch(client))
        self.assertEqual(1, client._fire_seq)

    def test_player_supplied_sequence_must_be_exact_next_value(self):
        client = self.active_client()

        self.assertIsNone(self.launch(client, shot_seq=2))
        self.assertEqual(0, client._fire_seq)
        self.assertEqual(1, self.launch(client, shot_seq=1))

    def test_bot_launch_requires_current_authority_and_does_not_use_player_seq(self):
        client = self.active_client()
        client._fire_seq = 9

        client.bot_authority_id = 8
        self.assertIsNone(self.launch(
            client, 'bot', 17, 3, authority_epoch=4))
        client.bot_authority_id = 7
        self.assertIsNone(self.launch(
            client, 'bot', 17, 3, authority_epoch=3))
        self.assertEqual(3, self.launch(
            client, 'bot', 17, 3, authority_epoch=4))
        self.assertEqual(9, client._fire_seq)
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual(4, message['authority_epoch'])
        self.assertEqual('bot', message['shooter_kind'])

    def test_launch_rejects_non_plain_nonfinite_and_out_of_bounds_physics(self):
        client = self.active_client()

        invalid = (
            {'origin': (1.0, 2.0, 3.0)},
            {'velocity': [float('nan'), 0.0, 0.0]},
            {'velocity': [0.0, 0.0, 0.0]},
            {'gravity': float('inf')},
            {'gravity': '9.81'},
            {'gravity': 0.0},
            {'max_time_ms': 20001},
            {'penetration_factor': 101.0},
            {'is_he': False, 'splash_radius': 1.0},
        )
        for values in invalid:
            with self.subTest(values=values):
                self.assertIsNone(self.launch(client, **values))
        self.assertEqual([], client._outbound_queue)
        self.assertEqual(0, client._fire_seq)

    def test_stock_b4_gravity_is_within_the_wire_contract(self):
        client = self.active_client()

        self.assertEqual(1, self.launch(
            client, gravity=143.0, velocity=[0.0, 100.0, 425.0]))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual(143.0, message['gravity'])

    def test_send_fire_never_falls_back_to_instant_input(self):
        client = self.active_client()
        self.assertIsNone(client.send_fire(shell_index=1))
        self.assertEqual([], client._outbound_queue)

        self.assertEqual(1, client.send_fire(
            shell_index=1, position=[1.0, 2.0, 3.0],
            velocity=[100.0, 0.0, 0.0], gravity=9.81,
            max_distance=500.0, max_time_ms=5000))
        self.assertEqual(
            'projectile_launch', client._outbound_queue[0][1]['type'])

    def test_progress_shape_is_exact_and_duplicate_ids_fail_closed(self):
        client = self.active_client()
        cursor = {
            'projectile_id': 'player:7:1',
            'base_checked_ms': 100,
            'checked_through_ms': 150,
            'checked_distance': 52.5,
            'piercing_loss': 4.0,
            'penetration_factor': 0.8,
            'destructibles': [],
        }

        self.assertTrue(client.send_projectile_progress(4, [cursor]))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual({
            'type', 'round_id', 'authority_epoch', 'cursors'}, set(message))
        self.assertEqual(cursor, message['cursors'][0])
        cursor['checked_distance'] = 999.0
        self.assertEqual(52.5, message['cursors'][0]['checked_distance'])

        self.assertFalse(client.send_projectile_progress(4, [
            dict(message['cursors'][0]), dict(message['cursors'][0])]))
        self.assertFalse(client.send_projectile_progress(
            4, [dict(message['cursors'][0]) for unused in range(31)]))
        bad = dict(message['cursors'][0])
        bad['unknown'] = 1
        self.assertFalse(client.send_projectile_progress(4, [bad]))
        bad = dict(message['cursors'][0])
        bad['checked_through_ms'] = '150'
        self.assertFalse(client.send_projectile_progress(4, [bad]))
        self.assertFalse(client.send_projectile_progress(3, [cursor]))

    @staticmethod
    def effect(kind, target_id, x=10.0):
        return {
            'target_kind': kind,
            'target_id': target_id,
            'damage': 120,
            'shot_result': 1,
            'x': x,
            'y': 2.0,
            'z': 3.0,
        }

    def test_resolve_is_atomic_and_rejects_duplicate_targets(self):
        client = self.active_client()
        direct = self.effect('player', 8)
        splash = [self.effect('bot', 17, 12.0)]

        self.assertTrue(client.send_projectile_resolve(
            4, 'player:7:1', 150, 'impact', 180,
            [11.0, 2.0, 3.0], direct, splash,
            checked_distance=61.0, piercing_loss=3.0,
            penetration_factor=0.75))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual({
            'type', 'round_id', 'authority_epoch', 'projectile_id',
            'base_checked_ms', 'outcome', 'resolved_time_ms',
            'checked_distance', 'piercing_loss', 'penetration_factor',
            'impact', 'direct', 'splash', 'destructibles'}, set(message))
        self.assertEqual('player:7:1', message['projectile_id'])

        duplicate = self.effect('player', 8)
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0], direct, [duplicate]))
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'miss', 10,
            [0.0, 0.0, 0.0], direct, []))

    def test_resolve_critical_contract_and_plain_impact_are_strict(self):
        client = self.active_client()
        incomplete = self.effect('bot', 17)
        incomplete['critical'] = {'fire': True}
        self.assertFalse(client.send_projectile_resolve(
            4, 'bot:17:1', 0, 'impact', 10,
            [0.0, 0.0, 0.0], incomplete, []))

        complete = dict(incomplete)
        complete.update({
            'critical_target_base_revision': 3,
            'critical_target_ack_seq': 4,
            'hull_damage': 120,
        })
        self.assertTrue(client.send_projectile_resolve(
            4, 'bot:17:1', 0, 'impact', 10,
            [0.0, 0.0, 0.0], complete, []))
        self.assertFalse(client.send_projectile_resolve(
            4, 'bot:17:2', 0, 'expired', 10,
            (0.0, 0.0, 0.0), None, []))
        self.assertTrue(client.send_projectile_resolve(
            4, 'bot:17:2', 0, 'expired', 10,
            None, None, [], checked_distance=12.0))

    def test_hello_advertises_ledger_before_transport_is_published(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        fake = RecordingSocket()
        original_socket = module.socket.socket
        module.socket.socket = lambda *unused_args: fake
        client.running = True
        client._publish_connected_transport = (
            lambda unused_sock, unused_generation: False)
        try:
            client._worker(client._transport_generation)
        finally:
            module.socket.socket = original_socket

        hello = json.loads(fake.sent[0].decode('utf-8'))
        self.assertEqual('hello', hello['type'])
        self.assertEqual(
            [module.PROJECTILE_LEDGER_CAPABILITY], hello['capabilities'])

    @staticmethod
    def welcome(capabilities=None, authority_epoch=2):
        return {
            'type': 'welcome',
            'protocol': module.PROTOCOL_VERSION,
            'client_build': module.CLIENT_BUILD,
            'capabilities': ([module.PROJECTILE_LEDGER_CAPABILITY]
                             if capabilities is None else capabilities),
            'player_id': 7,
            'host_player_id': 7,
            'bot_authority_id': 7,
            'authority_epoch': authority_epoch,
            'name': 'P',
            'vehicle': 'ussr:MS-1',
            'max_health': 100,
            'team': 1,
            'slot': 0,
            'map': '01_karelia',
            'phase': 'waiting',
            'round_id': 3,
            'state_revision': 1,
            'spawn': {'x': 0, 'y': 0, 'z': 0},
        }

    def test_welcome_requires_server_echoed_capability(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome([]))
        self.assertFalse(client.ready)
        self.assertEqual(
            'projectile ledger capability mismatch', client.last_error)

        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome())
        self.assertTrue(client.ready)
        self.assertEqual(2, client.authority_epoch)
        self.assertTrue(client.has_projectile_ledger())

    @staticmethod
    def active_projectile(epoch=2):
        return {
            'projectile_id': 'player:7:1',
            'shooter_kind': 'player',
            'shooter_id': 7,
            'source_vehicle': 'ussr:R11_MS-1',
            'shot_seq': 1,
            'shell_index': 0,
            'team': 1,
            'origin': [0.0, 2.0, 0.0],
            'velocity': [100.0, 10.0, 0.0],
            'gravity': 9.81,
            'max_distance': 500.0,
            'max_time_ms': 5000,
            'is_he': False,
            'splash_radius': 0.0,
            'penetration_factor': 1.0,
            'launch_server_time_ms': 900,
            'checked_through_ms': 50,
            'checked_distance': 5.0,
            'piercing_loss': 0.0,
            'authority_epoch': epoch,
        }

    def test_snapshot_preserves_and_validates_ledger_then_failover_epoch(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome())
        client.phase = 'battle'
        snapshot = {
            'type': 'snapshot',
            'protocol': module.PROTOCOL_VERSION,
            'round_id': 3,
            'server_tick': 10,
            'bot_state_revision': 0,
            'server_time_ms': 1000,
            'authority_epoch': 2,
            'projectile_revision': 1,
            'projectiles': [self.active_projectile()],
            'players': [{
                'critical_revision': 0,
                'critical_base_revision': 0,
                'critical_ack_seq': 0,
            }],
            'bots': [],
        }
        client._handle_message(snapshot)

        self.assertIs(snapshot, client.last_snapshot)
        self.assertEqual(1000, client.server_time_ms)
        self.assertEqual('player:7:1',
                         client.last_snapshot['projectiles'][0]['projectile_id'])
        client._handle_message({
            'type': 'events',
            'protocol': module.PROTOCOL_VERSION,
            'round_id': 3,
            'server_tick': 11,
            'server_time_ms': 1010,
            'authority_epoch': 3,
            'events': [{
                'kind': 'authority',
                'player_id': 7,
                'authority_epoch': 3,
            }],
        })
        self.assertEqual(3, client.authority_epoch)
        self.assertEqual(1010, client.server_time_ms)

    def test_events_require_monotonic_time_and_epoch_envelope(self):
        def client_at(time_ms=1000, epoch=2):
            client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
            client.running = True
            client._handle_message(self.welcome(authority_epoch=epoch))
            client.phase = 'battle'
            client.server_time_ms = time_ms
            return client

        client = client_at()
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'authority_epoch': 2, 'events': [],
        })
        self.assertFalse(client.running)
        self.assertEqual('invalid events message', client.last_error)

        client = client_at()
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': 1001, 'events': [],
        })
        self.assertFalse(client.running)
        self.assertEqual('invalid events message', client.last_error)

        client = client_at()
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': 1001, 'authority_epoch': 1, 'events': [],
        })
        self.assertFalse(client.running)
        self.assertEqual('invalid events message', client.last_error)

        client = client_at()
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': 1001, 'authority_epoch': 3, 'events': [],
        })
        self.assertTrue(client.running)
        self.assertEqual(1001, client.server_time_ms)
        self.assertEqual(3, client.authority_epoch)

    def test_authority_events_must_not_advance_past_envelope_epoch(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome(authority_epoch=2))
        client.phase = 'battle'
        client.server_time_ms = 1000

        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': 1001, 'authority_epoch': 3,
            'events': [{
                'kind': 'authority', 'player_id': 7,
                'authority_epoch': 4,
            }],
        })

        self.assertFalse(client.running)
        self.assertEqual('invalid bot authority event', client.last_error)

    def test_invalid_snapshot_ledger_stops_client(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome())
        client.phase = 'battle'
        projectile = self.active_projectile()
        projectile['origin'] = (0.0, 2.0, 0.0)
        client._handle_message({
            'type': 'snapshot',
            'protocol': module.PROTOCOL_VERSION,
            'round_id': 3,
            'server_tick': 10,
            'bot_state_revision': 0,
            'server_time_ms': 1000,
            'authority_epoch': 2,
            'projectile_revision': 1,
            'projectiles': [projectile],
            'players': [],
            'bots': [],
        })
        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

    def test_regressing_wire_time_is_clamped_without_dropping_events(self):
        received = []
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            on_event=lambda kind, message: received.append((kind, message)))
        client.running = True
        client._handle_message(self.welcome())
        client.phase = 'battle'
        client.server_time_ms = 1000
        client._handle_message({
            'type': 'events',
            'protocol': module.PROTOCOL_VERSION,
            'round_id': 3,
            'server_tick': 11,
            'server_time_ms': 999,
            'authority_epoch': 2,
            'events': [],
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(1000, client.server_time_ms)
        self.assertEqual(1000, received[-1][1]['server_time_ms'])

    def test_malformed_server_time_still_stops_client(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome())
        client.server_time_ms = 1000
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': -1, 'authority_epoch': 2, 'events': [],
        })

        self.assertFalse(client.running)
        self.assertEqual('invalid server time', client.last_error)


if __name__ == '__main__':
    unittest.main()
