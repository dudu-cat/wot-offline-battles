import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


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
        self.ready = False
        self.start_calls = 0
        self.stop_calls = 0
        self.leave_calls = 0
        self.requests = []

    def start(self):
        self.start_calls += 1
        return True

    def stop(self):
        self.stop_calls += 1

    def request_start(self, map_name):
        self.requests.append(map_name)
        return True

    def leave_battle(self):
        self.leave_calls += 1
        return True


class _Queue(object):
    def __init__(self, request_start, map_pool, endpoint=None, on_close=None):
        self.request_start = request_start
        self.map_pool = map_pool
        self.endpoint = endpoint
        self.on_close = on_close
        self.install_calls = 0
        self.uninstall_calls = 0
        self.close_calls = 0
        self.refresh_calls = 0

    def install(self):
        self.install_calls += 1

    def uninstall(self):
        self.uninstall_calls += 1

    def close(self):
        self.close_calls += 1

    def refresh(self):
        self.refresh_calls += 1
        return True


class _BattleRuntime(object):
    def __init__(self):
        self.started = []
        self.stopped = []
        self.restore_accounts = []
        self.snapshots = []
        self.events = []

    def start(self, config, message=None, lan_client=None,
              on_local_leave=None):
        self.started.append({
            'config': dict(config), 'message': message,
            'lan_client': lan_client,
            'on_local_leave': on_local_leave})
        return True

    def on_snapshot(self, message):
        self.snapshots.append(message)

    def on_events(self, message):
        self.events.append(message)

    def stop(self, show_login=True, restore_account=True):
        self.stopped.append(show_login)
        self.restore_accounts.append(restore_account)


class LANSessionTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.clients = []
        self.queues = []
        self.opens = []
        self.battle_runtime = _BattleRuntime()
        self.snapshots = []
        self.statuses = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            self.clients.append(client)
            return client

        def queue_factory(*args, **kwargs):
            queue = _Queue(*args, **kwargs)
            self.queues.append(queue)
            return queue

        self.session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782, 'name': 'P',
             'vehicle': 'ussr:MS-1', 'startupTimeoutSeconds': 12.0},
            client_factory=client_factory, queue_factory=queue_factory,
            picker_opener=lambda: self.opens.append(True) or True,
            battle_runtime=self.battle_runtime,
            on_snapshot=self.snapshots.append,
            status_notifier=self.statuses.append)
        self.assertTrue(self.session.start())
        self.client = self.clients[0]

    def emit(self, kind, message):
        if kind == 'welcome':
            self.client.ready = True
            self.client.phase = message.get('phase', self.client.phase)
        self.client.on_event(kind, message)

    def test_waiting_messages_install_and_open_picker_once(self):
        message = {'phase': 'waiting', 'map_pool': ['01_karelia']}
        self.emit('welcome', message)
        self.emit('roster', message)

        self.assertEqual('waiting', self.session.state)
        self.assertEqual(1, len(self.queues))
        self.assertEqual(1, self.queues[0].install_calls)
        self.assertEqual(1, self.queues[0].refresh_calls)
        self.assertEqual([True], self.opens)
        self.assertEqual(['01_karelia'], self.queues[0].map_pool())
        self.assertEqual(
            'LAN SERVER: 10.0.0.5:28782',
            self.queues[0].endpoint())

    def test_picker_is_available_before_server_welcome(self):
        self.assertEqual('connecting', self.session.state)
        self.assertEqual([True], self.opens)
        self.assertIsNone(self.queues[0].map_pool())

    def test_dismissed_preconnection_picker_reopens_without_key_hook(self):
        pending = []
        self.session._callback = (
            lambda delay, function: pending.append((delay, function)) or 1)

        self.queues[0].on_close()

        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, len(pending))
        delay, reopen = pending.pop()
        self.assertEqual(0.10, delay)
        reopen()
        self.assertEqual([True, True], self.opens)
        self.assertTrue(self.session._picker_open)

    def test_preconnection_selection_starts_once_after_welcome(self):
        self.assertTrue(self.queues[0].request_start(
            '01_karelia', 'LAN SERVER: 10.0.0.5:28782'))
        self.assertFalse(self.queues[0].request_start(
            '05_prohorovka', 'LAN SERVER: 10.0.0.5:28782'))
        self.assertEqual('01_karelia', self.session._pending_map)
        self.assertEqual([], self.client.requests)
        self.assertTrue(self.session._picker_open)

        message = {'phase': 'waiting', 'map_pool': ['01_karelia']}
        self.emit('welcome', message)
        self.emit('roster', message)

        self.assertEqual(['01_karelia'], self.client.requests)
        self.assertTrue(self.session._start_requested)
        self.assertEqual('awaiting_battle_start', self.session.state)
        self.assertFalse(self.session._picker_open)

    def test_pending_map_rejected_by_server_reopens_picker(self):
        self.assertTrue(self.queues[0].request_start(
            '01_karelia', 'LAN SERVER: 10.0.0.5:28782'))

        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['05_prohorovka']})

        self.assertEqual([], self.client.requests)
        self.assertEqual('waiting', self.session.state)
        self.assertTrue(self.session._picker_open)
        self.assertIn('does not offer', self.statuses[-1])

    def test_edited_endpoint_is_saved_and_replaces_client_generation(self):
        old_client = self.client
        stale_event = old_client.on_event
        with mock.patch.object(
                self.module.port_config, 'write_json') as write_json:
            self.assertTrue(self.queues[0].request_start(
                '01_karelia', 'LAN SERVER: 192.168.1.164:30000'))

        self.assertIsNone(old_client.on_event)
        self.assertEqual(1, old_client.stop_calls)
        self.assertEqual(2, len(self.clients))
        replacement = self.session.client
        self.assertEqual('192.168.1.164', replacement.host)
        self.assertEqual(30000, replacement.port)
        self.assertEqual('01_karelia', self.session._pending_map)
        saved_path, saved_value = write_json.call_args[0]
        self.assertEqual(self.module.port_config.CONFIG_PATH, saved_path)
        self.assertEqual('192.168.1.164', saved_value['host'])
        self.assertEqual(30000, saved_value['port'])

        stale_event('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertEqual('connecting', self.session.state)
        self.assertEqual([], replacement.requests)

    def test_cancelled_retry_cannot_replace_edited_endpoint_client(self):
        callbacks = {}
        cancelled = []

        def schedule(unused_delay, function):
            callbacks[7] = function
            return 7

        self.session._callback = schedule
        self.session._cancel_callback = cancelled.append
        self.emit('error', {'message': 'connection refused'})
        stale_retry = callbacks[7]

        with mock.patch.object(self.module.port_config, 'write_json'):
            self.assertTrue(self.queues[0].request_start(
                '01_karelia', 'LAN SERVER: 192.168.1.164:30000'))
        replacement = self.session.client

        stale_retry()

        self.assertEqual([7], cancelled)
        self.assertIs(replacement, self.session.client)
        self.assertEqual(2, len(self.clients))
        self.assertEqual(0, replacement.stop_calls)

    def test_invalid_edited_endpoint_keeps_picker_open(self):
        self.assertFalse(self.queues[0].request_start(
            '01_karelia', 'LAN SERVER: bad host:28782'))

        self.assertIs(self.client, self.session.client)
        self.assertTrue(self.session._picker_open)
        self.assertIn('invalid', self.statuses[-1])

    def test_initial_connection_failure_is_visible_and_retries(self):
        callbacks = {}
        cancelled = []

        def schedule(delay, function):
            callback_id = len(callbacks) + 1
            callbacks[callback_id] = (delay, function)
            return callback_id

        self.session._callback = schedule
        self.session._cancel_callback = cancelled.append

        self.emit('error', {'message': 'connection refused'})

        self.assertEqual('retrying', self.session.state)
        self.assertEqual(1, len(self.statuses))
        self.assertIn('10.0.0.5:28782', self.statuses[0])
        self.assertEqual(1, len(callbacks))
        callback_id, (delay, retry) = next(iter(callbacks.items()))
        self.assertEqual(self.module.RECONNECT_DELAY, delay)

        del callbacks[callback_id]
        retry()

        self.assertEqual(2, len(self.clients))
        self.assertEqual(1, self.client.stop_calls)
        replacement = self.clients[-1]
        self.assertEqual(1, replacement.start_calls)
        self.assertEqual('connecting', self.session.state)

        replacement.ready = True
        replacement.on_event(
            'welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertEqual('waiting', self.session.state)
        self.assertEqual(2, len(self.statuses))
        self.assertEqual([True], self.opens)
        self.assertIsNone(self.session._retry_callback_id)
        self.assertEqual([], cancelled)

    def test_stop_cancels_pending_initial_connection_retry(self):
        callbacks = {}
        cancelled = []

        def schedule(unused_delay, function):
            callbacks[7] = function
            return 7

        def cancel(callback_id):
            cancelled.append(callback_id)
            callbacks.pop(callback_id, None)

        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('error', {'message': 'connection refused'})

        self.session.stop(show_login=False)

        self.assertEqual([7], cancelled)
        self.assertEqual({}, callbacks)
        self.assertEqual('stopped', self.session.state)

    def test_selection_only_sends_start_request_and_denial_reopens(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertTrue(self.queues[0].request_start('01_karelia'))
        self.assertFalse(self.queues[0].request_start('01_karelia'))
        self.assertEqual(['01_karelia'], self.client.requests)
        self.assertEqual([], self.battle_runtime.started)

        self.emit('start_denied', {'reason': 'host only'})
        self.assertEqual('waiting', self.session.state)
        self.assertEqual([True], self.opens)
        self.assertEqual([], self.battle_runtime.started)

    def test_selection_closes_picker_after_scaleform_event_returns(self):
        callbacks = {}
        cancelled = []

        def schedule(delay, function):
            callback_id = len(callbacks) + 1
            callbacks[callback_id] = (delay, function)
            return callback_id

        def cancel(callback_id):
            cancelled.append(callback_id)
            callbacks.pop(callback_id, None)

        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertTrue(self.queues[0].request_start('01_karelia'))

        self.assertTrue(self.session._picker_open)
        self.assertEqual(0, self.queues[0].close_calls)
        self.assertEqual(1, len(callbacks))
        callback_id, (delay, close_picker) = next(iter(callbacks.items()))
        self.assertEqual(0.0, delay)

        callbacks.pop(callback_id)
        close_picker()

        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertIsNone(self.session._picker_close_callback_id)
        self.assertEqual([], cancelled)

    def test_early_battle_start_finishes_deferred_picker_close_first(self):
        callbacks = {}
        cancelled = []

        def schedule(delay, function):
            callbacks[7] = (delay, function)
            return 7

        def cancel(callback_id):
            cancelled.append(callback_id)
            callbacks.pop(callback_id, None)

        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertTrue(self.queues[0].request_start('01_karelia'))

        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        self.assertEqual([7], cancelled)
        self.assertEqual({}, callbacks)
        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertEqual(1, len(self.battle_runtime.started))
        self.assertEqual('battle', self.session.state)

    def test_real_close_notification_cannot_reopen_before_early_start(self):
        callbacks = {}

        def schedule(delay, function):
            callbacks[7] = (delay, function)
            return 7

        self.session._callback = schedule
        self.session._cancel_callback = lambda callback_id: callbacks.pop(
            callback_id, None)
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        queue = self.queues[0]
        original_close = queue.close

        def close_with_native_notification():
            original_close()
            queue.on_close()

        queue.close = close_with_native_notification
        self.assertTrue(queue.request_start('01_karelia'))

        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        self.assertEqual({}, callbacks)
        self.assertEqual([True], self.opens)
        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, queue.close_calls)
        self.assertEqual('battle', self.session.state)

    def test_stock_picker_close_allows_the_waiting_view_to_reopen(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.queues[0].on_close()
        self.emit('start_denied', {'reason': 'try again'})

        self.assertEqual([True, True], self.opens)
        self.assertTrue(self.session._picker_open)

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

    def test_local_avatar_leave_retires_round_and_waits_for_server_reset(self):
        first = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', first)

        self.assertTrue(
            self.battle_runtime.started[0]['on_local_leave']())

        self.assertEqual(1, self.client.leave_calls)
        self.assertEqual(0, self.client.stop_calls)
        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertFalse(self.session._battle_started)
        self.assertEqual(7, self.session._departed_round_id)
        self.assertEqual('awaiting_round_end', self.session.state)

        # A duplicate start already queued for the departed round cannot put
        # the recovered Account straight back into an Avatar.
        self.emit('battle_start', first)
        self.assertEqual(1, len(self.battle_runtime.started))

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        self.assertEqual('waiting', self.session.state)
        self.assertIsNone(self.session._departed_round_id)

        second = {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', second)
        self.assertEqual(2, len(self.battle_runtime.started))

    def test_synchronous_runtime_failure_keeps_lan_until_server_reset(self):
        start = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}

        def fail_start(config, message=None, lan_client=None,
                       on_local_leave=None):
            self.battle_runtime.started.append({
                'config': dict(config), 'message': message,
                'lan_client': lan_client,
                'on_local_leave': on_local_leave})
            lan_client.on_event('battle_failed', {
                'round_id': 7, 'message': 'invalid entity property',
                'lobby_restored': True})
            # Even a buggy runtime return cannot reclaim a round whose
            # synchronous failure callback already consumed start ownership.
            return True

        self.battle_runtime.start = fail_start
        self.emit('battle_start', start)

        self.assertEqual('awaiting_round_end', self.session.state)
        self.assertFalse(self.session._battle_started)
        self.assertEqual(7, self.session._departed_round_id)
        self.assertEqual(1, self.client.leave_calls)
        self.assertEqual(0, self.client.stop_calls)
        self.assertEqual([], self.battle_runtime.stopped)
        self.assertIn('Returning to the map picker', self.statuses[-1])

        self.emit('battle_start', start)
        self.assertEqual(1, len(self.battle_runtime.started))
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        self.assertEqual('waiting', self.session.state)
        self.assertIsNone(self.session._departed_round_id)
        self.assertTrue(self.session._picker_open)

    def test_unrestored_runtime_failure_stops_only_lan_owners(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'lobby restore failed',
            'lobby_restored': False})

        self.assertTrue(self.session._stopped)
        self.assertEqual('stopped', self.session.state)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([], self.battle_runtime.stopped)
        self.assertEqual([], self.battle_runtime.restore_accounts)

    def test_failed_battle_leave_does_not_reenter_runtime_cleanup(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.client.leave_battle = mock.Mock(return_value=False)

        self.emit('battle_failed', {
            'round_id': 7, 'message': 'invalid entity property',
            'lobby_restored': True})

        self.client.leave_battle.assert_called_once_with()
        self.assertTrue(self.session._stopped)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([], self.battle_runtime.stopped)

    def test_duplicate_and_stale_battle_failures_do_not_retire_new_round(self):
        first = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', first)
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'first round failed',
            'lobby_restored': True})

        self.assertEqual(1, self.client.leave_calls)
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'duplicate failure',
            'lobby_restored': True})
        self.assertEqual(1, self.client.leave_calls)
        self.assertFalse(self.session._stopped)

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        second = {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', second)
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'late old failure',
            'lobby_restored': False})

        self.assertEqual('battle', self.session.state)
        self.assertTrue(self.session._battle_started)
        self.assertEqual(8, self.session._active_round_id)
        self.assertEqual(1, self.client.leave_calls)
        self.assertEqual(0, self.client.stop_calls)

    def test_failed_local_leave_still_cleans_runtime_and_stops_session(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.client.leave_battle = lambda: False

        with self.assertRaisesRegex(
                RuntimeError, 'did not accept battle leave'):
            self.battle_runtime.started[0]['on_local_leave']()

        self.assertEqual('stopped', self.session.state)
        self.assertTrue(self.session._stopped)
        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual(1, self.client.stop_calls)

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

    def test_next_picker_waits_for_native_lobby_recovery(self):
        ready = [True]
        pending = []
        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = (
            lambda unused_delay, function: pending.append(function) or
            len(pending))
        self.session._cancel_callback = lambda unused_id: None
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        ready[0] = False
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})

        self.assertEqual([True], self.opens)
        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, len(pending))
        ready[0] = True
        pending.pop(0)()
        self.assertEqual([True, True], self.opens)
        self.assertTrue(self.session._picker_open)

    def test_next_battle_start_waits_for_native_lobby_recovery(self):
        ready = [True]
        pending = {}
        next_id = [0]

        def schedule(unused_delay, function):
            next_id[0] += 1
            pending[next_id[0]] = function
            return next_id[0]

        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = schedule
        self.session._cancel_callback = lambda callback_id: pending.pop(
            callback_id, None)
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        ready[0] = False
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        self.emit('battle_start', {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]})

        self.assertEqual(1, len(self.battle_runtime.started))
        self.assertEqual('awaiting_lobby_for_battle', self.session.state)
        self.assertIsNotNone(self.session._pending_battle_start)
        self.assertEqual(1, len(pending))

        ready[0] = True
        pending.pop(next(iter(pending)))()

        self.assertEqual(2, len(self.battle_runtime.started))
        self.assertEqual('battle', self.session.state)
        self.assertIsNone(self.session._pending_battle_start)
        self.assertIsNone(self.session._battle_start_callback_id)

    def test_late_start_denial_cannot_cancel_deferred_accepted_battle(self):
        ready = [False]
        pending = []
        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = (
            lambda unused_delay, function: pending.append(function) or
            len(pending))
        self.session._cancel_callback = lambda unused_id: None
        start = {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)

        self.emit('start_denied', {
            'round_id': 8, 'code': 'already_started'})

        self.assertEqual('awaiting_lobby_for_battle', self.session.state)
        self.assertEqual(start, self.session._pending_battle_start)
        self.assertEqual(1, len(pending))
        ready[0] = True
        pending.pop()()
        self.assertEqual(1, len(self.battle_runtime.started))
        self.assertEqual('battle', self.session.state)

    def test_new_waiting_round_discards_deferred_stale_battle(self):
        ready = [True]
        callbacks = {}
        cancelled = []
        next_id = [0]

        def schedule(unused_delay, function):
            next_id[0] += 1
            callbacks[next_id[0]] = function
            return next_id[0]

        def cancel(callback_id):
            cancelled.append(callback_id)
            callbacks.pop(callback_id, None)

        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        ready[0] = False
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        self.emit('battle_start', {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]})
        stale_callback_id = self.session._battle_start_callback_id

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 9,
            'map_pool': ['01_karelia']})

        self.assertIn(stale_callback_id, cancelled)
        self.assertIsNone(self.session._pending_battle_start)
        self.assertIsNone(self.session._battle_start_callback_id)
        self.assertEqual('waiting', self.session.state)
        ready[0] = True
        for callback in list(callbacks.values()):
            callback()
        self.assertEqual(1, len(self.battle_runtime.started))

    def test_stop_cancels_deferred_battle_start(self):
        ready = [False]
        pending = {}
        cancelled = []

        def schedule(unused_delay, function):
            pending[1] = function
            return 1

        def cancel(callback_id):
            cancelled.append(callback_id)
            pending.pop(callback_id, None)

        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.assertEqual('awaiting_lobby_for_battle', self.session.state)

        self.session.stop(show_login=False)

        self.assertEqual([1], cancelled)
        self.assertEqual({}, pending)
        self.assertIsNone(self.session._pending_battle_start)

    def test_failed_round_cleanup_cannot_leave_session_half_in_battle(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.battle_runtime.stop = mock.Mock(
            side_effect=RuntimeError('account restore failed'))

        with self.assertRaisesRegex(RuntimeError,
                                    'account restore failed'):
            self.emit('roster', {
                'phase': 'waiting', 'round_id': 8,
                'map_pool': ['05_prohorovka']})

        self.assertEqual('stopped', self.session.state)
        self.assertTrue(self.session._stopped)
        self.assertFalse(self.session._battle_started)
        self.assertIsNone(self.session._active_round_id)
        self.assertIsNone(self.session.snapshot)
        self.assertIsNone(self.session._picker_callback_id)
        self.assertIsNone(self.client.on_event)

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
        self.assertEqual([True], self.battle_runtime.restore_accounts)

    def test_global_shutdown_skips_account_restore(self):
        self.session.stop(show_login=False, restore_account=False)

        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual([False], self.battle_runtime.restore_accounts)

    def test_disconnect_closes_stock_picker_before_uninstalling_adapter(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('disconnected', {'reason': 'network'})

        self.assertEqual('stopped', self.session.state)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertEqual(1, self.queues[0].uninstall_calls)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([True], self.battle_runtime.stopped)

    def test_stop_finishes_every_cleanup_stage_then_raises_first_error(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        queue = self.queues[0]
        calls = []

        def fail_close():
            queue.close_calls += 1
            calls.append('close')
            raise RuntimeError('close failed')

        def fail_uninstall():
            queue.uninstall_calls += 1
            calls.append('uninstall')
            raise RuntimeError('uninstall failed')

        def fail_client_stop():
            self.client.stop_calls += 1
            calls.append('client')
            raise RuntimeError('client failed')

        def fail_battle_stop(show_login=True, restore_account=True):
            self.battle_runtime.stopped.append(show_login)
            self.battle_runtime.restore_accounts.append(restore_account)
            calls.append('battle')
            raise RuntimeError('battle failed')

        queue.close = fail_close
        queue.uninstall = fail_uninstall
        self.client.stop = fail_client_stop
        self.battle_runtime.stop = fail_battle_stop

        with self.assertRaisesRegex(RuntimeError, 'close failed'):
            self.session.stop(show_login=False)

        self.assertEqual(['close', 'uninstall', 'client', 'battle'], calls)
        self.assertEqual('stopped', self.session.state)
        self.assertIsNone(self.client.on_event)
        self.assertEqual(1, queue.close_calls)
        self.assertEqual(1, queue.uninstall_calls)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([False], self.battle_runtime.stopped)
        self.session.stop(show_login=False)
