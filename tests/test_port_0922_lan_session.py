import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
    full_name = 'gui.mods.offline_lan_0922.lan_session'
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(full_name,
                                                   PACKAGE_ROOT / 'lan_session.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


class _Client(object):
    def __init__(self, host, port, name, vehicle, max_health=100, on_event=None):
        self.host = host
        self.port = port
        self.name = name
        self.vehicle = vehicle
        self.max_health = max_health
        self.on_event = on_event
        self.player_id = 'p1'
        self.phase = 'waiting'
        self.map_pool = []
        self.map_name = None
        self.spawn = [1, 2, 3]
        self.round_id = None
        self.start_calls = 0
        self.stop_calls = 0
        self.requests = []

    def start(self):
        self.start_calls += 1
        return True

    def stop(self):
        self.stop_calls += 1

    def request_start(self, map_name):
        self.requests.append(map_name)
        return True


class _Queue(object):
    def __init__(self, request_start, map_pool):
        self.request_start = request_start
        self.map_pool = map_pool
        self.install_calls = 0
        self.uninstall_calls = 0
        self.close_calls = 0

    def install(self):
        self.install_calls += 1

    def uninstall(self):
        self.uninstall_calls += 1

    def close(self):
        self.close_calls += 1


class _BattleRuntime(object):
    def __init__(self):
        self.started = []
        self.stopped = []
        self.snapshots = []
        self.events = []

    def start(self, config, message=None, lan_client=None):
        self.started.append({
            'config': dict(config), 'message': message,
            'lan_client': lan_client})
        return True

    def on_snapshot(self, message):
        self.snapshots.append(message)

    def on_events(self, message):
        self.events.append(message)

    def stop(self, show_login=True):
        self.stopped.append(show_login)


class LANSessionTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.clients = []
        self.queues = []
        self.opens = []
        self.battle_runtime = _BattleRuntime()
        self.snapshots = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            self.clients.append(client)
            return client

        def queue_factory(*args):
            queue = _Queue(*args)
            self.queues.append(queue)
            return queue

        self.session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782, 'name': 'P',
             'vehicle': 'ussr:MS-1', 'startupTimeoutSeconds': 12.0},
            client_factory=client_factory, queue_factory=queue_factory,
            picker_opener=lambda: self.opens.append(True) or True,
            battle_runtime=self.battle_runtime,
            on_snapshot=self.snapshots.append)
        self.assertTrue(self.session.start())
        self.client = self.clients[0]

    def emit(self, kind, message):
        self.client.on_event(kind, message)

    def test_waiting_messages_install_and_open_picker_once(self):
        message = {'phase': 'waiting', 'map_pool': ['01_karelia']}
        self.emit('welcome', message)
        self.emit('roster', message)

        self.assertEqual('waiting', self.session.state)
        self.assertEqual(1, len(self.queues))
        self.assertEqual(1, self.queues[0].install_calls)
        self.assertEqual([True], self.opens)
        self.assertEqual(['01_karelia'], self.queues[0].map_pool())

    def test_selection_only_sends_start_request_and_denial_reopens(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertTrue(self.queues[0].request_start('01_karelia'))
        self.assertEqual(['01_karelia'], self.client.requests)
        self.assertEqual([], self.battle_runtime.started)

        self.emit('start_denied', {'reason': 'host only'})
        self.assertEqual('waiting', self.session.state)
        self.assertEqual([True, True], self.opens)
        self.assertEqual([], self.battle_runtime.started)

    def test_battle_start_uses_server_map_and_local_roster_spawn_once(self):
        self.emit('welcome', {'phase': 'battle'})
        self.assertEqual('awaiting_battle_start', self.session.state)
        start = {'round_id': 7, 'map': '05_prohorovka', 'players': [
            {'id': 'other', 'x': 0, 'y': 0, 'z': 0, 'vehicle': 'germany:PzI'},
            {'id': 'p1', 'x': 7, 'y': 8, 'z': 9, 'vehicle': 'ussr:T-34'},
        ]}
        self.emit('battle_start', start)
        self.emit('battle_start', start)

        self.assertEqual('battle', self.session.state)
        self.assertEqual(1, len(self.battle_runtime.started))
        config = self.battle_runtime.started[0]['config']
        self.assertEqual('05_prohorovka', config['map'])
        self.assertEqual([7.0, 8.0, 9.0], config['spawn'])
        self.assertEqual('ussr:T-34', config['vehicle'])
        self.assertIs(start, self.battle_runtime.started[0]['message'])
        self.assertIs(self.client,
                      self.battle_runtime.started[0]['lan_client'])

    def test_active_round_snapshot_is_stored_and_forwarded(self):
        self.emit('battle_start', {
            'round_id': 1, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        snapshot = {'round_id': 1, 'entities': [{'id': 3}]}
        self.emit('snapshot', snapshot)
        self.assertIs(snapshot, self.session.snapshot)
        self.assertEqual([snapshot], self.snapshots)

    def test_waiting_roster_after_result_stops_old_battle_and_allows_next_round(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        first = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', first)
        self.emit('snapshot', {
            'round_id': 7, 'battle_result': {'winner': 1}})

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})

        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual('waiting', self.session.state)
        self.assertFalse(self.session._battle_started)
        self.assertIsNone(self.session.snapshot)
        self.assertEqual([True, True], self.opens)

        second = {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', second)
        self.assertEqual(2, len(self.battle_runtime.started))
        self.assertEqual('battle', self.session.state)

    def test_battle_phase_roster_during_disconnect_keeps_active_battle(self):
        start = {'round_id': 7, 'map': '01_karelia', 'players': [{
            'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
            'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)

        self.emit('roster', {
            'phase': 'battle', 'round_id': 7, 'players': start['players']})

        self.assertEqual('battle', self.session.state)
        self.assertTrue(self.session._battle_started)
        self.assertEqual([], self.battle_runtime.stopped)

    def test_late_start_denied_cannot_demote_active_battle(self):
        start = {'round_id': 7, 'map': '01_karelia', 'players': [{
            'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
            'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)

        self.emit('start_denied', {'round_id': 7, 'code': 'already_started'})

        self.assertEqual('battle', self.session.state)
        self.assertTrue(self.session._battle_started)
        self.assertEqual([], self.battle_runtime.stopped)

    def test_new_round_start_is_a_defensive_barrier_without_roster(self):
        first = {'round_id': 7, 'map': '01_karelia', 'players': [{
            'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
            'vehicle': 'ussr:T-34'}]}
        second = {'round_id': 8, 'map': '05_prohorovka', 'players': [{
            'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
            'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', first)

        self.emit('battle_start', second)

        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual(2, len(self.battle_runtime.started))
        self.assertEqual(8, self.session._active_round_id)

    def test_stale_round_snapshot_and_events_are_not_forwarded(self):
        start = {'round_id': 7, 'map': '01_karelia', 'players': [{
            'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
            'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)
        current = {'round_id': 7, 'server_tick': 2}
        self.emit('snapshot', current)

        self.emit('snapshot', {'round_id': 6, 'server_tick': 99})
        self.emit('events', {'round_id': 6, 'events': [
            {'kind': 'authority', 'player_id': 2}]})

        self.assertIs(current, self.session.snapshot)
        self.assertEqual([current], self.battle_runtime.snapshots)
        self.assertEqual([], self.battle_runtime.events)

    def test_stop_is_idempotent_and_releases_every_owned_boundary_once(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.session.stop(show_login=False)
        self.session.fini(show_login=False)

        self.assertEqual('stopped', self.session.state)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertEqual(1, self.queues[0].uninstall_calls)
        self.assertEqual(1, self.client.stop_calls)
        self.assertIsNone(self.client.on_event)
        self.assertEqual([False], self.battle_runtime.stopped)

    def test_disconnect_closes_stock_picker_before_uninstalling_adapter(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('disconnected', {'reason': 'network'})

        self.assertEqual('stopped', self.session.state)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertEqual(1, self.queues[0].uninstall_calls)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([True], self.battle_runtime.stopped)
