import json
from collections import OrderedDict
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest import mock


SERVER_ROOT = Path(__file__).resolve().parents[1] / 'server'
sys.path.insert(0, str(SERVER_ROOT))

import lan_battle_server as server_module  # noqa: E402
from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, ClientHandler, PROJECTILE_CAPABILITY,
    PREBATTLE_SECONDS,
    SimulationWorker,
    SIMULATION_WORKER_AUTHORITY_ID, SIMULATION_WORKER_CAPABILITY,
    SIMULATION_WORKER_ROLE, TICK_HZ, ThreadedTCPServer,
)


class _Connection(object):
    def __init__(self):
        self.messages = []

    def sendall(self, payload):
        self.messages.append(json.loads(payload.decode('utf-8')))


class _Peer(object):
    def __init__(self, address):
        self.socket = socket.create_connection(address, timeout=2.0)
        self.socket.settimeout(2.0)
        self.stream = self.socket.makefile('rwb')

    def send(self, message):
        payload = (json.dumps(message, separators=(',', ':')) + '\n')
        self.stream.write(payload.encode('utf-8'))
        self.stream.flush()

    def receive_until(self, kind, limit=32):
        for _unused in range(limit):
            line = self.stream.readline()
            if not line:
                raise AssertionError('connection closed before %s' % kind)
            message = json.loads(line.decode('utf-8'))
            if message.get('type') == kind:
                return message
        raise AssertionError('did not receive %s' % kind)

    def close(self):
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.stream.close()
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass


def _worker_hello():
    return {
        'type': 'hello', 'protocol': 5,
        'role': SIMULATION_WORKER_ROLE,
        'client_build': CLIENT_BUILD_0922,
        'capabilities': [
            PROJECTILE_CAPABILITY, SIMULATION_WORKER_CAPABILITY],
    }


def _player_hello(name='Human'):
    # Deliberately omit role: protocol-v5 player hellos predate workers.
    return {
        'type': 'hello', 'protocol': 5,
        'client_build': CLIENT_BUILD_0922,
        'capabilities': [PROJECTILE_CAPABILITY],
        'name': name, 'vehicle': 'ussr:R11_MS-1', 'max_health': 90,
    }


def _manifest(roster):
    result = []
    for entry in roster:
        team = int(entry['team'])
        slot = int(entry['slot'])
        result.append({
            'id': int(entry['id']), 'team': team, 'slot': slot,
            'name': entry['name'], 'vehicle': 'ussr:R11_MS-1',
            'health': 90, 'max_health': 90,
            'x': float(slot * 12), 'y': 0.0,
            'z': -35.0 if team == 1 else 35.0,
            'yaw': 0.0 if team == 1 else 3.141592,
            'world_pose': True, 'profile': {},
            'route': {'id': 'worker-test', 'waypoints': []},
        })
    return result


def _bot_publication(manifest, x_offset=0.0):
    return [{
        'id': entry['id'],
        'x': entry['x'] + x_offset, 'y': entry['y'], 'z': entry['z'],
        'yaw': entry['yaw'], 'health': entry['health'], 'alive': True,
        'fire_seq': 0, 'critical': {},
        'combat_base_revision': 0, 'combat_seq': 0,
        'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
    } for entry in manifest]


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('timed out waiting for server state')


class SimulationWorkerStateTests(unittest.TestCase):
    def test_worker_is_not_a_player_and_survives_round_reset(self):
        state = BattleState(
            map_name='01_karelia', max_players=1, team_size=1)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())

        self.assertIsNone(error)
        self.assertEqual({}, state.players)
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.bot_authority_id)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        self.assertEqual(1, player.player_id)
        self.assertNotIn(SIMULATION_WORKER_AUTHORITY_ID, state.players)

        extra, error = state.add_player(
            _Connection(), ('127.0.0.1', 1002), _player_hello('Extra'))
        self.assertIsNone(extra)
        self.assertEqual('full', error)
        duplicate, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1003), _worker_hello())
        self.assertIsNone(duplicate)
        self.assertEqual('worker_already_connected', error)

        state._reset_round()

        self.assertIs(worker, state.simulation_worker)
        self.assertTrue(worker.connected)
        self.assertEqual(0, worker.battle_ready_round)
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.bot_authority_id)
        self.assertEqual([1], [row['id']
                              for row in state.lobby_message()['players']])
        self.assertNotIn(SIMULATION_WORKER_AUTHORITY_ID,
                         state.round_participants)
        self.assertFalse(state.result_receipts)

    def test_worker_ready_is_a_separate_loading_barrier(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest}))

        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))
        self.assertEqual('loading', state.phase)
        live = state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id})

        self.assertIsNotNone(live)
        self.assertEqual('battle', state.phase)
        recipients = state.pending_live_message['recipients']
        self.assertIn(player, recipients)
        self.assertIn(worker, recipients)
        self.assertEqual([player.account_key],
                         list(state.round_participants))

    def test_loading_disconnect_rebuilds_manifest_without_deadlock(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest}))
        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))

        old_epoch = state.authority_epoch
        removed, failed_over = state.remove_simulation_worker(worker)

        self.assertIs(worker, removed)
        self.assertTrue(failed_over)
        self.assertEqual('loading', state.phase)
        self.assertEqual(player.player_id, state.bot_authority_id)
        self.assertEqual(old_epoch + 1, state.authority_epoch)
        self.assertIsNone(state.bot_manifest_authority_id)
        self.assertTrue(state.update_bot_manifest(
            player.player_id,
            {'round_id': state.round_id, 'bots': manifest}))
        self.assertIsNotNone(state.activate_battle_if_ready())
        self.assertEqual('battle', state.phase)

    def test_tick_never_delivers_player_receipts_to_worker(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        worker_connection = _Connection()
        worker, error = state.add_simulation_worker(
            worker_connection, ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player_connection = _Connection()
        player, error = state.add_player(
            player_connection, ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        state.phase = 'battle'
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.bot_roster = []
        state.result_receipts = OrderedDict((('receipt-1', {
            'type': 'battle_receipt', 'receipt_id': 'receipt-1',
            'account_key': player.account_key,
        }),))

        state.tick_once(1.0 / TICK_HZ)

        self.assertIn('battle_receipt', [
            message.get('type') for message in player_connection.messages])
        self.assertNotIn('battle_receipt', [
            message.get('type') for message in worker_connection.messages])
        self.assertIs(worker, state.simulation_worker)

    def test_pending_live_refreshes_authority_after_worker_failover(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        worker_connection = _Connection()
        worker, error = state.add_simulation_worker(
            worker_connection, ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player_connection = _Connection()
        player, error = state.add_player(
            player_connection, ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest}))
        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id}))
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.pending_live_message[
                             'message']['bot_authority_id'])
        old_epoch = state.authority_epoch

        removed, failed_over = state.remove_simulation_worker(worker)
        self.assertIs(worker, removed)
        self.assertTrue(failed_over)
        self.assertEqual(old_epoch + 1, state.authority_epoch)
        state.tick_once(1.0 / TICK_HZ)

        live_messages = [
            message for message in player_connection.messages
            if message.get('type') == 'battle_live']
        self.assertEqual(1, len(live_messages))
        self.assertEqual(player.player_id,
                         live_messages[0]['bot_authority_id'])
        self.assertEqual(state.authority_epoch,
                         live_messages[0]['authority_epoch'])
        self.assertEqual(state.state_revision,
                         live_messages[0]['state_revision'])
        self.assertEqual(state._server_time_ms(),
                         live_messages[0]['server_time_ms'])
        self.assertEqual(state._timing_payload(),
                         live_messages[0]['timing'])
        self.assertEqual('fallback', live_messages[0]['worker_status'])
        self.assertEqual('worker_disconnected',
                         live_messages[0]['worker_fallback_reason'])
        self.assertFalse(any(
            message.get('type') == 'battle_live'
            for message in worker_connection.messages))

    def test_dead_human_leave_adjudicates_and_notifies_worker_once(self):
        state = BattleState(map_name='01_karelia', team_size=2)
        worker_connection = _Connection()
        worker, error = state.add_simulation_worker(
            worker_connection, ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest}))
        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id}))
        state.tick_once(1.0 / TICK_HZ)
        player.alive = False
        player.health = 0
        player.death_reason = 2
        state._statistics_row(
            'player', player.player_id)['damage_dealt'] = 123

        accepted = state.leave_battle(
            player.player_id, {'round_id': state.round_id})

        self.assertTrue(accepted)
        self.assertIs(player, state.players[player.player_id])
        self.assertFalse(player.participating)
        self.assertIs(worker, state.simulation_worker)
        self.assertEqual('battle', state.phase)
        self.assertEqual(2, state.battle_result['winner'])
        self.assertEqual('battle_timeout', state.battle_result['reason'])
        receipts = [
            receipt for receipt in state.result_receipts.values()
            if receipt.get('account_key') == player.account_key]
        self.assertEqual(1, len(receipts))
        self.assertEqual(2, receipts[0]['death_reason'])
        self.assertEqual(3, receipts[0]['finish_reason'])
        self.assertEqual(123, receipts[0]['stats']['damage'])
        self.assertTrue(receipts[0]['premature_leave'])

        result_events = len([
            event for event in state.pending_events
            if event.get('kind') == 'battle_result'])
        receipt_ids = list(state.result_receipts)
        self.assertFalse(state._finish_abandoned_battle())
        self.assertEqual(result_events, len([
            event for event in state.pending_events
            if event.get('kind') == 'battle_result']))
        self.assertEqual(receipt_ids, list(state.result_receipts))

        state.tick_once(1.0 / TICK_HZ)
        worker_results = [
            message.get('battle_result')
            for message in worker_connection.messages
            if message.get('type') == 'snapshot' and
            message.get('battle_result') is not None]
        self.assertEqual([state.battle_result], worker_results)
        self.assertEqual(1, sum(
            event.get('kind') == 'battle_result'
            for message in worker_connection.messages
            if message.get('type') == 'events'
            for event in message.get('events', ())))

    def test_live_human_disconnect_remains_abandoned_result(self):
        state = BattleState(map_name='01_karelia', team_size=2)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest}))
        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id}))

        removed, reset = state.remove_player(player.player_id)

        self.assertIs(player, removed)
        self.assertFalse(reset)
        self.assertIs(worker, state.simulation_worker)
        self.assertIsNotNone(state.battle_result)
        self.assertEqual(0, state.battle_result['winner'])
        self.assertEqual('all_players_left',
                         state.battle_result['reason'])
        receipt = next(
            value for value in state.result_receipts.values()
            if value.get('account_key') == player.account_key)
        self.assertEqual(4, receipt['finish_reason'])

    def test_live_graceful_leave_disconnect_preserves_abandoned_result(self):
        state = BattleState(map_name='01_karelia', team_size=2)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        first, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello('First'))
        self.assertIsNone(error)
        second, error = state.add_player(
            _Connection(), ('127.0.0.1', 1002), _player_hello('Second'))
        self.assertIsNone(error)
        start, error = state.request_start(first.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest}))
        self.assertIsNone(state.mark_battle_ready(
            first.player_id, {'round_id': state.round_id}))
        self.assertIsNone(state.mark_battle_ready(
            second.player_id, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id}))

        self.assertTrue(state.leave_battle(
            first.player_id, {'round_id': state.round_id}))
        frozen = state.round_participants[first.account_key]
        self.assertTrue(frozen['alive'])
        removed, reset = state.remove_player(first.player_id)
        self.assertIs(first, removed)
        self.assertFalse(reset)
        self.assertTrue(frozen['alive'])

        second.alive = False
        second.health = 0
        second.death_reason = 2
        self.assertTrue(state.leave_battle(
            second.player_id, {'round_id': state.round_id}))

        self.assertIs(worker, state.simulation_worker)
        self.assertEqual('all_players_left', state.battle_result['reason'])
        self.assertEqual(0, state.battle_result['winner'])

    def test_remaining_bot_adjudication_is_deterministic(self):
        state = BattleState(map_name='01_karelia')

        def forces(team_1, team_2):
            state.bot_manifest = []
            state.bot_states = {}
            bot_id = 1
            for team, values in ((1, team_1), (2, team_2)):
                for health, maximum in values:
                    state.bot_manifest.append({
                        'id': bot_id, 'team': team,
                        'health': health, 'max_health': maximum,
                    })
                    state.bot_states[bot_id] = {
                        'id': bot_id, 'team': team, 'health': health,
                        'max_health': maximum, 'alive': health > 0,
                    }
                    bot_id += 1

        forces(((10, 100), (10, 100)), ((100, 100), (0, 100)))
        self.assertEqual(1, state._remaining_bot_winner())
        forces(((40, 100),), ((50, 200),))
        self.assertEqual(1, state._remaining_bot_winner())
        forces(((50, 100),), ((100, 200),))
        self.assertEqual(2, state._remaining_bot_winner())
        forces(((50, 100),), ((50, 100),))
        self.assertEqual(0, state._remaining_bot_winner())

        capture_state = BattleState(map_name='01_karelia')
        capture_state.phase = 'battle'
        capture_state.simulation_worker = SimulationWorker(
            _Connection(), ('127.0.0.1', 1000))
        capture_state.round_participants = {
            'dead': {'alive': False},
        }
        capture_state.rules_state['bases']['2']['points'] = 100
        self.assertTrue(capture_state._finish_abandoned_battle())
        self.assertEqual(1, capture_state.battle_result['winner'])
        self.assertEqual('base captured',
                         capture_state.battle_result['reason'])
        self.assertEqual(2, capture_state.battle_result['base_team'])


class SimulationWorkerSocketTests(unittest.TestCase):
    def setUp(self):
        self.state = BattleState(
            map_name='01_karelia', max_players=1, team_size=1)
        self.server = ThreadedTCPServer(
            ('127.0.0.1', 0), ClientHandler)
        self.server.game_server = type(
            'GameServer', (), {'state': self.state})()
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.peers = []

    def tearDown(self):
        for peer in self.peers:
            peer.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2.0)

    def _connect(self):
        peer = _Peer(self.server.server_address)
        self.peers.append(peer)
        return peer

    def _enter_worker_countdown(self, worker, player):
        player.send({
            'type': 'start_battle', 'round_id': self.state.round_id})
        player.receive_until('battle_start')
        worker_start = worker.receive_until('battle_start')
        manifest = _manifest(worker_start['bots'])
        worker.send({
            'type': 'bot_manifest', 'round_id': self.state.round_id,
            'bots': manifest})
        _wait_until(lambda: self.state.bot_manifest_authority_id ==
                    SIMULATION_WORKER_AUTHORITY_ID)
        player.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        worker.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        _wait_until(lambda: self.state.phase == 'battle')
        self.assertLess(
            self.state.tick, int(round(PREBATTLE_SECONDS * TICK_HZ)))
        return manifest

    def test_handler_worker_authority_and_disconnect_takeover(self):
        worker = self._connect()
        worker.send(_worker_hello())
        welcome = worker.receive_until('welcome')

        self.assertEqual(SIMULATION_WORKER_ROLE, welcome['role'])
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         welcome['worker_id'])
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         welcome['bot_authority_id'])
        self.assertEqual([], self.state.lobby_message()['players'])

        # These are player commands.  The worker dispatcher must ignore all
        # of them, then process ping as an ordered barrier.
        revision = self.state.state_revision
        forbidden = (
            {'type': 'start_battle', 'round_id': self.state.round_id},
            {'type': 'select_vehicle', 'vehicle': 'ussr:R06_T-28'},
            {'type': 'input', 'round_id': self.state.round_id,
             'forward': 1.0},
            {'type': 'leave_battle', 'round_id': self.state.round_id},
            {'type': 'battle_receipt_ack', 'receipt_id': 'not-a-receipt'},
        )
        for message in forbidden:
            worker.send(message)
        worker.send({'type': 'ping', 'seq': 1})
        worker.receive_until('pong')
        self.assertEqual('waiting', self.state.phase)
        self.assertEqual(revision, self.state.state_revision)
        self.assertEqual({}, self.state.players)
        self.assertFalse(self.state.result_receipts)

        player = self._connect()
        player.send(_player_hello())
        player_welcome = player.receive_until('welcome')
        self.assertNotIn('role', player_welcome)
        self.assertEqual(1, player_welcome['player_id'])
        self.assertEqual([1], sorted(self.state.players))

        extra = self._connect()
        extra.send(_player_hello('Extra'))
        self.assertEqual('full', extra.receive_until('error')['code'])

        player.send({
            'type': 'start_battle', 'round_id': self.state.round_id})
        player_start = player.receive_until('battle_start')
        worker_start = worker.receive_until('battle_start')
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         player_start['bot_authority_id'])
        self.assertEqual([1], [entry['id']
                              for entry in worker_start['players']])
        self.assertNotIn(SIMULATION_WORKER_AUTHORITY_ID,
                         self.state.round_participants)

        manifest = _manifest(worker_start['bots'])
        worker.send({
            'type': 'bot_manifest', 'round_id': self.state.round_id,
            'bots': manifest})
        _wait_until(lambda: self.state.bot_manifest_authority_id ==
                    SIMULATION_WORKER_AUTHORITY_ID)
        player.receive_until('snapshot')
        worker.receive_until('snapshot')
        player.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        worker.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        _wait_until(lambda: self.state.phase == 'battle')
        self.state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))

        publication = _bot_publication(manifest, x_offset=1.0)
        player.send({
            'type': 'bot_state', 'round_id': self.state.round_id,
            'bots': publication})
        player.send({'type': 'ping', 'seq': 2})
        player.receive_until('pong')
        self.assertEqual('authority',
                         self.state.last_bot_state_reject_code)
        revision = self.state.bot_state_revision

        worker.send({
            'type': 'bot_state', 'round_id': self.state.round_id,
            'bots': publication})
        worker.send({'type': 'ping', 'seq': 3})
        worker.receive_until('pong')
        self.assertEqual(revision + 1, self.state.bot_state_revision)
        self.assertEqual(1.0 + manifest[0]['x'],
                         self.state.bot_states[manifest[0]['id']]['x'])
        self.state.bot_pending_projectile_launches.add(
            (manifest[0]['id'], 99))

        old_epoch = self.state.authority_epoch
        worker.send({'type': 'leave'})
        _wait_until(lambda: self.state.simulation_worker is None)

        self.assertEqual(1, self.state.bot_authority_id)
        self.assertEqual(old_epoch + 1, self.state.authority_epoch)
        self.assertIsNone(self.state.bot_manifest_authority_id)
        self.assertEqual('worker_disconnected',
                         self.state.worker_fallback_reason)
        self.assertEqual([1], sorted(self.state.players))
        self.assertFalse(self.state.bot_pending_projectile_launches)
        self.assertFalse(self.state._projectile_authority_matches(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'authority_epoch': old_epoch}))
        self.assertTrue(self.state._projectile_authority_matches(
            1, {'authority_epoch': self.state.authority_epoch}))

        replacement = self._connect()
        replacement.send(_worker_hello())
        self.assertEqual(
            'battle_in_progress',
            replacement.receive_until('error')['code'])

        player.receive_until('roster')
        player.send({
            'type': 'bot_manifest', 'round_id': self.state.round_id,
            'bots': manifest})
        player.send({'type': 'ping', 'seq': 4})
        player.receive_until('pong')
        self.assertEqual(1, self.state.bot_manifest_authority_id)

        takeover_publication = _bot_publication(manifest, x_offset=2.0)
        player.send({
            'type': 'bot_state', 'round_id': self.state.round_id,
            'bots': takeover_publication})
        player.send({'type': 'ping', 'seq': 5})
        player.receive_until('pong')
        self.assertEqual(2.0 + manifest[0]['x'],
                         self.state.bot_states[manifest[0]['id']]['x'])

    def test_socket_failover_refreshes_queued_live_barrier(self):
        worker = self._connect()
        worker.send(_worker_hello())
        worker.receive_until('welcome')
        player = self._connect()
        player.send(_player_hello())
        player_welcome = player.receive_until('welcome')

        player.send({
            'type': 'start_battle', 'round_id': self.state.round_id})
        player.receive_until('battle_start')
        worker_start = worker.receive_until('battle_start')
        manifest = _manifest(worker_start['bots'])
        worker.send({
            'type': 'bot_manifest', 'round_id': self.state.round_id,
            'bots': manifest})
        _wait_until(lambda: self.state.bot_manifest_authority_id ==
                    SIMULATION_WORKER_AUTHORITY_ID)
        player.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        worker.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        _wait_until(lambda: self.state.pending_live_message is not None)
        old_epoch = self.state.authority_epoch

        worker.send({'type': 'leave'})
        _wait_until(lambda: self.state.simulation_worker is None)
        roster = player.receive_until('roster')
        self.assertEqual(player_welcome['player_id'],
                         roster['bot_authority_id'])
        self.assertEqual(old_epoch + 1, roster['authority_epoch'])

        self.state.tick_once(1.0 / TICK_HZ)
        live = player.receive_until('battle_live')

        self.assertEqual(roster['bot_authority_id'],
                         live['bot_authority_id'])
        self.assertEqual(roster['authority_epoch'],
                         live['authority_epoch'])
        self.assertGreaterEqual(live['state_revision'],
                                roster['state_revision'])

    def test_silent_open_worker_times_out_to_player_authority(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.1):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            time.sleep(0.65)
            self.assertIsNotNone(self.state.simulation_worker)
            self.assertEqual('waiting', self.state.phase)

            player = self._connect()
            player.send(_player_hello())
            player_welcome = player.receive_until('welcome')
            old_epoch = self.state.authority_epoch

            player.send({
                'type': 'start_battle',
                'round_id': self.state.round_id,
            })
            player.receive_until('battle_start')
            worker.receive_until('battle_start')

            # Keep the client-side socket open but publish no worker messages.
            # This models a native callback loop that stopped while TCP stayed
            # established.
            self.assertNotEqual(-1, worker.socket.fileno())
            _wait_until(
                lambda: self.state.simulation_worker is None,
                timeout=2.0)

            roster = player.receive_until('roster')
            self.assertEqual(player_welcome['player_id'],
                             roster['bot_authority_id'])
            self.assertEqual(player_welcome['player_id'],
                             self.state.bot_authority_id)
            self.assertEqual(old_epoch + 1, self.state.authority_epoch)
            self.assertEqual('worker_disconnected',
                             self.state.worker_fallback_reason)
            self.assertNotEqual(-1, worker.socket.fileno())

    def test_loading_worker_ping_refreshes_liveness(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.35):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player.receive_until('welcome')
            player.send({
                'type': 'start_battle',
                'round_id': self.state.round_id,
            })
            player.receive_until('battle_start')
            worker.receive_until('battle_start')

            for sequence in range(1, 5):
                time.sleep(0.2)
                worker.send({'type': 'ping', 'seq': sequence})
                pong = worker.receive_until('pong')
                self.assertEqual(sequence, pong['seq'])

            self.assertIsNotNone(self.state.simulation_worker)
            self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                             self.state.bot_authority_id)

    def test_countdown_progress_refreshes_worker_liveness(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.3):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player.receive_until('welcome')
            self._enter_worker_countdown(worker, player)
            epoch = self.state.authority_epoch

            for frame_seq in range(1, 5):
                time.sleep(0.12)
                worker.send({
                    'type': 'simulation_progress',
                    'round_id': self.state.round_id,
                    'authority_epoch': epoch,
                    'frame_seq': frame_seq,
                })
                worker.send({'type': 'ping', 'seq': frame_seq})
                self.assertEqual(
                    frame_seq, worker.receive_until('pong')['seq'])

            endpoint = self.state.simulation_worker
            self.assertIsNotNone(endpoint)
            self.assertEqual(4, endpoint.simulation_progress_frame_seq)
            self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                             self.state.bot_authority_id)

    def test_hidden_worker_observation_is_an_accepted_battle_frame(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.3), \
                mock.patch.object(server_module, '_server_log') as log:
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player_welcome = player.receive_until('welcome')
            manifest = self._enter_worker_countdown(worker, player)
            self.state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))

            worker.send({
                'type': 'bot_state', 'round_id': self.state.round_id,
                'bots': _bot_publication(manifest),
            })
            worker.send({'type': 'ping', 'seq': 0})
            self.assertEqual(0, worker.receive_until('pong')['seq'])
            _wait_until(lambda: self.state.bot_state_revision == 1)

            target = self.state.players[player_welcome['player_id']]
            observing_team = next(
                entry['team'] for entry in manifest
                if entry['team'] != target.team)
            observation = {
                'type': 'bot_observation',
                'round_id': self.state.round_id,
                'contacts': [{
                    'observing_team': observing_team,
                    'target_kind': 'human',
                    'target_id': target.player_id,
                    'target_team': target.team,
                    'visible': False,
                    'shootable_by_bot_ids': [],
                    'x': target.x, 'y': target.y, 'z': target.z,
                    'health': target.health,
                    'max_health': target.max_health,
                }],
                'affordances': [],
            }
            for sequence in range(1, 6):
                time.sleep(0.12)
                worker.send(observation)
                worker.send({'type': 'ping', 'seq': sequence})
                self.assertEqual(
                    sequence, worker.receive_until('pong')['seq'])

            self.assertIsNotNone(self.state.simulation_worker)
            rejects = [
                call.args[0] for call in log.call_args_list
                if call.args and
                'WORKER COMMAND rejected type=bot_observation' in
                call.args[0]
            ]
            self.assertEqual([], rejects)

    def test_battle_ping_only_worker_times_out(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.3):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player_welcome = player.receive_until('welcome')
            self._enter_worker_countdown(worker, player)

            for sequence in range(1, 4):
                time.sleep(0.12)
                worker.send({'type': 'ping', 'seq': sequence})
                self.assertEqual(sequence,
                                 worker.receive_until('pong')['seq'])

            _wait_until(
                lambda: self.state.simulation_worker is None, timeout=2.0)
            self.assertEqual(player_welcome['player_id'],
                             self.state.bot_authority_id)

    def test_duplicate_and_stale_progress_do_not_refresh_liveness(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.35):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player_welcome = player.receive_until('welcome')
            self._enter_worker_countdown(worker, player)
            epoch = self.state.authority_epoch
            endpoint = self.state.simulation_worker

            worker.send({
                'type': 'simulation_progress',
                'round_id': self.state.round_id,
                'authority_epoch': epoch,
                'frame_seq': 10,
            })
            worker.send({'type': 'ping', 'seq': 1})
            worker.receive_until('pong')
            self.assertEqual(10, endpoint.simulation_progress_frame_seq)

            time.sleep(0.12)
            worker.send({
                'type': 'simulation_progress',
                'round_id': self.state.round_id,
                'authority_epoch': epoch,
                'frame_seq': 10,
            })
            worker.send({'type': 'ping', 'seq': 2})
            worker.receive_until('pong')
            self.assertEqual(10, endpoint.simulation_progress_frame_seq)

            time.sleep(0.12)
            worker.send({
                'type': 'simulation_progress',
                'round_id': self.state.round_id,
                'authority_epoch': epoch - 1,
                'frame_seq': 11,
            })
            worker.send({'type': 'ping', 'seq': 3})
            worker.receive_until('pong')
            self.assertEqual(10, endpoint.simulation_progress_frame_seq)

            time.sleep(0.12)
            worker.send({'type': 'ping', 'seq': 4})
            worker.receive_until('pong')
            _wait_until(
                lambda: self.state.simulation_worker is None, timeout=2.0)
            self.assertEqual(player_welcome['player_id'],
                             self.state.bot_authority_id)

    def test_player_socket_has_no_worker_liveness_timeout(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.1):
            player = self._connect()
            player.send(_player_hello())
            welcome = player.receive_until('welcome')
            self.assertEqual({
                'type', 'protocol', 'client_build', 'player_id', 'name',
                'vehicle', 'outfits', 'team', 'slot', 'max_health', 'map',
                'map_pool', 'host_player_id', 'phase', 'round_id',
                'state_revision', 'spawn', 'bot_authority_id', 'team_size',
                'authority_epoch', 'capabilities',
            }, set(welcome))
            roster = player.receive_until('roster')
            self.assertNotIn('worker_status', roster)
            self.assertNotIn('worker_fallback_reason', roster)
            player.send({
                'type': 'start_battle',
                'round_id': self.state.round_id,
            })
            player.receive_until('battle_start')

            time.sleep(0.65)

            self.assertIn(welcome['player_id'], self.state.players)
            self.assertTrue(self.state.players[welcome['player_id']].connected)
            self.assertEqual('loading', self.state.phase)


if __name__ == '__main__':
    unittest.main()
