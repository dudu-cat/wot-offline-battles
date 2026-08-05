from __future__ import print_function

"""Coordinator between LAN protocol, stock map picker and battle runtime."""

from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922 import queue_ui


RECONNECT_DELAY = 2.0


def _show_status(message):
    """Show one native lobby notification without owning mouse focus."""
    try:
        from gui import SystemMessages
        SystemMessages.pushMessage(
            message, type=SystemMessages.SM_TYPE.Warning)
    except Exception:
        # A notification must never become a second connection failure.
        pass


def _load_client():
    from gui.mods.offline_lan_0922.lan_client import LANClient
    return LANClient


def _load_battle_runtime():
    from gui.mods.offline_lan_0922.battle_runtime import g_battle_runtime
    return g_battle_runtime


def _message_value(message, name, default=None):
    if isinstance(message, dict):
        return message.get(name, default)
    return default


def _spawn(value):
    """Normalize trusted server spawn data for the battle runtime."""
    if isinstance(value, dict):
        value = (value.get('x'), value.get('y'), value.get('z'))
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None


class LANSession(object):
    """Owns one LAN connection and its reversible lobby/battle transitions."""

    def __init__(self, config, client_factory=None, queue_factory=None,
                 picker_opener=None, battle_runtime=None, on_snapshot=None,
                 on_event=None, lobby_ready=None, callback=None,
                 cancel_callback=None, status_notifier=None):
        self._config = dict(config or {})
        self._client_factory = client_factory or _load_client
        self._queue_factory = queue_factory or queue_ui.QueueUI
        self._picker_opener = picker_opener or queue_ui.open_picker
        self._battle_runtime = battle_runtime
        self._on_snapshot = on_snapshot
        self._on_event_callback = on_event
        self._lobby_ready = lobby_ready or (lambda: True)
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._status_notifier = status_notifier or _show_status
        self.client = None
        self.snapshot = None
        self.state = 'idle'
        # None means the server policy is not known yet.  The stock picker can
        # still show every locally installed standard map while connecting.
        self._map_pool = None
        self._queue = None
        self._picker_open = False
        self._picker_callback_id = None
        self._picker_close_callback_id = None
        self._battle_start_callback_id = None
        self._retry_callback_id = None
        self._retry_token = None
        self._pending_battle_start = None
        self._pending_map = None
        self._start_requested = False
        self._starting_round_id = None
        self._active_round_id = None
        self._departed_round_id = None
        self._battle_started = False
        self._stopped = False
        self._connection_error_notified = False
        self._client_generation = 0

    def _new_client(self):
        factory = self._client_factory
        if factory is _load_client:
            factory = factory()
        self._client_generation += 1
        generation = self._client_generation

        def on_event(kind, message):
            # A cancelled BigWorld poll can still already be dispatching.  Do
            # not let a retired socket mutate the replacement LAN session.
            if (not self._stopped and
                    generation == self._client_generation):
                self._on_event(kind, message)

        return factory(
            self._config.get('host', '127.0.0.1'),
            self._config.get('port', 28782),
            self._config.get('name', 'Player'),
            self._config.get('vehicle', 'ussr:R11_MS-1'),
            max_health=self._config.get('max_health', 100),
            on_event=on_event)

    def start(self):
        if self._stopped or self.client is not None:
            return False
        self.client = self._new_client()
        self.state = 'connecting'
        started = bool(self.client.start())
        if started:
            # This is the LAN settings surface as well as the map picker.  It
            # must exist even when the endpoint is wrong or the server starts
            # after the client.
            self._open_waiting_picker()
        return started

    def _endpoint_value(self):
        return port_config.format_endpoint(
            self._config.get('host', '127.0.0.1'),
            self._config.get('port', 28782))

    def _cancel_retry_callback(self):
        callback_id = self._retry_callback_id
        self._retry_callback_id = None
        self._retry_token = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _schedule_connection_retry(self):
        if self._retry_callback_id is not None or not callable(self._callback):
            return False
        token = object()
        source_client = self.client
        self._retry_token = token

        def retry():
            if (self._retry_token is not token or
                    self.client is not source_client):
                return
            self._retry_callback_id = None
            self._retry_token = None
            if self._stopped:
                return
            old_client = self.client
            if old_client is not None:
                old_client.on_event = None
                old_client.stop()
            self.client = self._new_client()
            self.state = 'connecting'
            if not self.client.start():
                self.state = 'retrying'
                self._schedule_connection_retry()

        callback_id = self._callback(RECONNECT_DELAY, retry)
        # BigWorld callbacks are asynchronous, but retaining this guard makes
        # the owner correct under a synchronous test scheduler as well.
        if self._retry_token is token:
            self._retry_callback_id = callback_id
        return True

    def _retry_initial_connection(self, message):
        self.state = 'retrying'
        if not self._connection_error_notified:
            error = _message_value(message, 'message', 'connection failed')
            host = self._config.get('host', '127.0.0.1')
            port = self._config.get('port', 28782)
            self._status_notifier(
                'LAN server %s:%s is unavailable (%s). Retrying...' %
                (host, port, error))
            self._connection_error_notified = True
        if self._schedule_connection_retry():
            self._open_waiting_picker()
            return True
        return False

    def _map_pool_value(self):
        if self._map_pool is None:
            return None
        return list(self._map_pool)

    def _ensure_queue(self):
        if self._queue is None:
            self._queue = self._queue_factory(self.request_start,
                                              self._map_pool_value,
                                              endpoint=self._endpoint_value,
                                              on_close=self._on_picker_closed)
            self._queue.install()

    def _on_picker_closed(self):
        self._picker_open = False
        if (not self._stopped and self._pending_map is None and
                self.state in ('connecting', 'retrying', 'waiting')):
            # The retail fight button is deliberately not wired to this LAN
            # protocol.  Keep the native LAN settings surface reachable if it
            # is dismissed before a start request.
            self._schedule_picker_when_lobby_ready()

    def _cancel_picker_callback(self):
        callback_id = self._picker_callback_id
        self._picker_callback_id = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _cancel_picker_close_callback(self):
        callback_id = self._picker_close_callback_id
        self._picker_close_callback_id = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _close_picker_after_event(self):
        """Close the Scaleform picker after its current event stack returns."""
        if self._picker_close_callback_id is not None:
            return True
        if not callable(self._callback):
            # Never fall back to a synchronous native view teardown.  The
            # battle_start poll event can safely close an otherwise-retained
            # picker after the Scaleform update callback has returned.
            return False

        def close_picker():
            self._picker_close_callback_id = None
            if not self._stopped:
                self._close_picker()

        self._picker_close_callback_id = self._callback(0.0, close_picker)
        return True

    def _schedule_picker_when_lobby_ready(self):
        if self._picker_callback_id is not None or not callable(self._callback):
            return False

        def retry():
            self._picker_callback_id = None
            if (not self._stopped and self.state in
                    ('connecting', 'retrying', 'waiting')):
                self._open_waiting_picker()

        self._picker_callback_id = self._callback(0.10, retry)
        return True

    def _cancel_battle_start_callback(self):
        callback_id = self._battle_start_callback_id
        self._battle_start_callback_id = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _clear_pending_battle_start(self):
        self._cancel_battle_start_callback()
        self._pending_battle_start = None

    def _schedule_battle_when_lobby_ready(self):
        if (self._battle_start_callback_id is not None or
                not callable(self._callback)):
            return False

        def retry():
            self._battle_start_callback_id = None
            if self._stopped or self._pending_battle_start is None:
                return
            if not self._lobby_ready():
                self._schedule_battle_when_lobby_ready()
                return
            message = self._pending_battle_start
            self._pending_battle_start = None
            self._start_battle(message)

        self._battle_start_callback_id = self._callback(0.10, retry)
        return True

    def _defer_battle_until_lobby_ready(self, message):
        self._pending_battle_start = dict(message or {})
        self.state = 'awaiting_lobby_for_battle'
        self._cancel_picker_close_callback()
        self._cancel_picker_callback()
        if self._picker_open:
            self._close_picker()
        self._schedule_battle_when_lobby_ready()
        return False

    def _open_waiting_picker(self):
        if self._stopped or self._picker_open:
            return False
        if not self._lobby_ready():
            self._schedule_picker_when_lobby_ready()
            return False
        self._cancel_picker_callback()
        self._ensure_queue()
        self._picker_open = bool(self._picker_opener())
        return self._picker_open

    def _close_picker(self):
        self._cancel_picker_callback()
        self._picker_open = False
        if self._queue is not None:
            close = getattr(self._queue, 'close', None)
            if callable(close):
                close()

    def _save_endpoint(self, value):
        try:
            host, port = port_config.parse_endpoint(
                value, self._config.get('port', 28782))
        except ValueError as error:
            self._status_notifier(str(error))
            return False
        changed = (host != self._config.get('host') or
                   port != self._config.get('port'))
        if not changed:
            return True
        self._config['host'] = host
        self._config['port'] = port
        port_config.write_json(port_config.CONFIG_PATH, self._config)
        self._connection_error_notified = False
        self._cancel_retry_callback()
        old_client = self.client
        if old_client is not None:
            old_client.on_event = None
            old_client.stop()
        self.client = self._new_client()
        self.state = 'connecting'
        if not self.client.start():
            self.state = 'retrying'
            self._schedule_connection_retry()
        return True

    def request_start(self, map_name, endpoint=None):
        if self._stopped:
            return False
        # The native button can deliver more than one event before the
        # next-tick picker close runs.  One accepted selection owns the
        # transition until the server accepts or denies it.
        if self._start_requested:
            return False
        if endpoint is not None and not self._save_endpoint(endpoint):
            return False
        if self._pending_map is not None:
            return False
        if self.client is None:
            return False
        if self.state not in ('waiting', 'connecting', 'retrying'):
            return False
        if not bool(getattr(self.client, 'ready', False)):
            self._pending_map = map_name
            self._status_notifier(
                'Connecting to %s. The selected map will start automatically.' %
                self._endpoint_value())
            self._close_picker_after_event()
            return True
        accepted = bool(self.client.request_start(map_name))
        if accepted:
            self._start_requested = True
            self._pending_map = None
            self.state = 'awaiting_battle_start'
            self._close_picker_after_event()
        return accepted

    def _stop_active_round(self):
        try:
            if self._battle_runtime is not None:
                self._battle_runtime.stop(show_login=False)
        finally:
            # Runtime cleanup can fail while reconstructing the Account.  The
            # old round is still gone, so never retain ownership flags that
            # route later snapshots into a stopped runtime.
            self._battle_started = False
            self._starting_round_id = None
            self._active_round_id = None
            self.snapshot = None
            self._start_requested = False

    def _waiting_event(self, message):
        self._cancel_retry_callback()
        if self._connection_error_notified:
            self._status_notifier('LAN server connected. Choose a map to start.')
        self._connection_error_notified = False
        previous_map_pool = self._map_pool
        self._map_pool = list(_message_value(
            message, 'map_pool', getattr(self.client, 'map_pool', [])) or [])
        map_pool_changed = previous_map_pool != self._map_pool
        phase = _message_value(message, 'phase', getattr(self.client, 'phase', None))
        if phase == 'waiting':
            self._clear_pending_battle_start()
            self._starting_round_id = None
            if self._battle_started:
                try:
                    self._stop_active_round()
                except Exception:
                    self.state = 'error'
                    try:
                        self.stop(show_login=True)
                    except Exception:
                        pass
                    raise
            # A waiting roster is the server-owned barrier that makes a
            # locally departed player eligible for another battle.
            self._departed_round_id = None
            if self._start_requested:
                self.state = 'awaiting_battle_start'
                return
            self.state = 'waiting'
            if map_pool_changed and self._picker_open and self._queue is not None:
                refresh = getattr(self._queue, 'refresh', None)
                if callable(refresh):
                    refresh()
            if self._pending_map is not None:
                pending_map = self._pending_map
                self._pending_map = None
                if pending_map not in self._map_pool:
                    self._status_notifier(
                        'The LAN server does not offer the selected map.')
                    self._open_waiting_picker()
                    return
                if self.client.request_start(pending_map):
                    self._start_requested = True
                    self.state = 'awaiting_battle_start'
                    self._close_picker()
                    return
                self._status_notifier(
                    'The LAN server did not accept the start request.')
            self._open_waiting_picker()
        elif phase == 'battle':
            # Disconnect/failover roster updates are broadcast during a live
            # round.  They update membership but must not demote the active
            # local battle back to an awaiting state.
            round_id = _message_value(
                message, 'round_id', getattr(self.client, 'round_id', None))
            if (self._departed_round_id is not None and
                    round_id == self._departed_round_id):
                self.state = 'awaiting_round_end'
            else:
                self.state = ('battle' if self._battle_started
                              else 'awaiting_battle_start')

    def _player_details(self, message):
        player_id = getattr(self.client, 'player_id', None)
        spawn = _message_value(message, 'spawn')
        vehicle = _message_value(message, 'vehicle')
        roster = (_message_value(message, 'players') or
                  _message_value(message, 'roster', []) or [])
        for player in roster:
            if not isinstance(player, dict):
                continue
            identifier = player.get('id', player.get('player_id'))
            if player_id is not None and identifier != player_id:
                continue
            spawn = player.get('spawn', spawn)
            if spawn is None and all(axis in player for axis in ('x', 'y', 'z')):
                spawn = {'x': player['x'], 'y': player['y'], 'z': player['z']}
            vehicle = player.get('vehicle', vehicle)
            break
        if spawn is None:
            spawn = getattr(self.client, 'spawn', None)
        if vehicle is None:
            vehicle = getattr(self.client, 'vehicle', None)
        return (_spawn(spawn), vehicle)

    def _start_battle(self, message):
        round_id = _message_value(message, 'round_id',
                                  getattr(self.client, 'round_id', None))
        if round_id == self._departed_round_id:
            return False
        if (self._departed_round_id is not None and
                round_id != self._departed_round_id):
            self._departed_round_id = None
        if self._battle_started and round_id == self._active_round_id:
            return False
        if self._battle_started:
            try:
                self._stop_active_round()
            except Exception:
                self.state = 'error'
                try:
                    self.stop(show_login=True)
                except Exception:
                    pass
                raise
        # LAN messages are dispatched from a BigWorld poll callback, after the
        # Scaleform method that sent the request has returned.  If the next-tick
        # picker close has not run yet, it is safe to finish it on this stack
        # before any native Account/Avatar transition begins.
        self._cancel_picker_close_callback()
        self._cancel_picker_callback()
        if self._picker_open:
            self._close_picker()
        if not self._lobby_ready():
            return self._defer_battle_until_lobby_ready(message)
        self._clear_pending_battle_start()
        map_name = _message_value(message, 'map',
                                  _message_value(message, 'map_name',
                                                 getattr(self.client, 'map_name', None)))
        spawn, vehicle = self._player_details(message)
        if not map_name or spawn is None or not vehicle:
            self.state = 'error'
            return False
        if self._battle_runtime is None:
            self._battle_runtime = _load_battle_runtime()
        config = dict(self._config)
        config.update({'map': map_name, 'spawn': spawn, 'vehicle': vehicle})
        if 'startupTimeoutSeconds' not in config:
            config['startupTimeoutSeconds'] = 30.0
        # The runtime can report a synchronous native failure from inside
        # start().  Record ownership before entering it, then only commit the
        # active round if that ownership token survived the callback.
        self._starting_round_id = round_id
        try:
            started = bool(self._battle_runtime.start(
                config, message=message, lan_client=self.client,
                on_local_leave=self._on_local_battle_leave))
        except Exception:
            if self._starting_round_id == round_id:
                self._starting_round_id = None
            raise
        owns_start = self._starting_round_id == round_id
        if owns_start:
            self._starting_round_id = None
        if started and owns_start and not self._stopped:
            self._battle_started = True
            self._active_round_id = round_id
            self._start_requested = False
            self._pending_map = None
            self.state = 'battle'
            return True
        return False

    def _on_local_battle_leave(self):
        """Retire one local round while retaining the waiting-room socket."""
        if self._stopped or not self._battle_started:
            return False
        self._departed_round_id = self._active_round_id
        errors = []
        try:
            leave = getattr(self.client, 'leave_battle', None)
            if not callable(leave) or not leave():
                raise RuntimeError('LAN server did not accept battle leave')
        except Exception as error:
            errors.append(error)
        try:
            self._stop_active_round()
        except Exception as error:
            errors.append(error)
        if errors:
            self.state = 'error'
            try:
                # _stop_active_round already retired (or attempted to retire)
                # the native runtime.  Finish the remaining owners without a
                # duplicate stop call through the same failed boundary.
                self.stop(show_login=True, stop_runtime=False)
            except Exception:
                pass
            raise errors[0]
        self.state = 'awaiting_round_end'
        return True

    def _on_battle_failed(self, message):
        """Handle a local native-runtime failure without faking socket loss."""
        owned_round_id = self._starting_round_id
        if owned_round_id is None and self._battle_started:
            owned_round_id = self._active_round_id
        round_id = _message_value(message, 'round_id', owned_round_id)
        # A duplicate failure for a locally-departed round, or a delayed
        # callback from an older round, must not tear down a newer Avatar or
        # send a second leave_battle request.
        if owned_round_id is None or round_id != owned_round_id:
            return False
        self._clear_pending_battle_start()
        self._starting_round_id = None
        self._battle_started = False
        self._active_round_id = None
        self._departed_round_id = round_id
        self.snapshot = None
        self._start_requested = False
        self._pending_map = None
        reason = _message_value(message, 'message', 'unknown error')

        if bool(_message_value(message, 'lobby_restored', False)):
            leave = getattr(self.client, 'leave_battle', None)
            try:
                if not callable(leave) or not leave():
                    raise RuntimeError(
                        'LAN server did not accept failed battle leave')
            except Exception:
                self.state = 'error'
                self._status_notifier(
                    'Battle could not start (%s). LAN session stopped.' %
                    reason)
                try:
                    # BattleRuntime already owns cleanup and Account restore.
                    # Close only the LAN/picker owners on this failure path.
                    self.stop(show_login=True, restore_account=False,
                              stop_runtime=False)
                except Exception:
                    pass
                return False
            self.state = 'awaiting_round_end'
            self._status_notifier(
                'Battle could not start (%s). Returning to the map picker.' %
                reason)
            return True

        self.state = 'error'
        self._status_notifier(
            'Battle could not start and the lobby was not restored (%s).' %
            reason)
        try:
            # Runtime recovery already failed.  Do not recurse through its
            # cleanup or Account reconstruction a second time.
            self.stop(show_login=True, restore_account=False,
                      stop_runtime=False)
        except Exception:
            pass
        return False

    def _on_event(self, kind, message):
        if self._stopped:
            return
        if kind in ('welcome', 'roster'):
            self._waiting_event(message)
        elif kind == 'start_denied':
            # Concurrent start requests can produce an accepted battle_start
            # and a denial for this client's losing request in either order.
            # Once an accepted start is active or retained behind the lobby
            # gate, the denial must not cancel that server-owned transition.
            if (self._battle_started or
                    self._pending_battle_start is not None):
                return
            self._clear_pending_battle_start()
            self._start_requested = False
            self.state = 'waiting'
            self._open_waiting_picker()
        elif kind == 'battle_start':
            self._start_battle(message)
        elif kind == 'battle_failed':
            self._on_battle_failed(message)
        elif kind == 'snapshot':
            round_id = _message_value(message, 'round_id')
            if (not self._battle_started or
                    (round_id is not None and
                     round_id != self._active_round_id)):
                return
            self.snapshot = message
            if self._battle_runtime is not None:
                self._battle_runtime.on_snapshot(message)
            if self._on_snapshot is not None:
                self._on_snapshot(message)
        elif kind == 'events':
            round_id = _message_value(message, 'round_id')
            if (not self._battle_started or
                    (round_id is not None and
                     round_id != self._active_round_id)):
                return
            if self._battle_runtime is not None:
                self._battle_runtime.on_events(message)
        elif kind in ('disconnected', 'connection_lost', 'error'):
            if (kind == 'error' and not self._battle_started and
                    not bool(getattr(self.client, 'ready', False)) and
                    self._retry_initial_connection(message)):
                if self._on_event_callback is not None:
                    self._on_event_callback(kind, message)
                return
            # The stock window owns cursor capture.  Closing it before
            # uninstalling our wrappers lets its normal close path release it.
            self.stop(show_login=True)
        if self._on_event_callback is not None:
            self._on_event_callback(kind, message)

    def stop(self, show_login=True, restore_account=True,
             stop_runtime=True):
        if self._stopped:
            return
        self._stopped = True
        errors = []
        try:
            self._cancel_retry_callback()
        except Exception as error:
            errors.append(error)
        try:
            self._cancel_picker_close_callback()
        except Exception as error:
            errors.append(error)
        try:
            self._clear_pending_battle_start()
        except Exception as error:
            errors.append(error)
        try:
            self._close_picker()
        except Exception as error:
            errors.append(error)
        if self._queue is not None:
            try:
                self._queue.uninstall()
            except Exception as error:
                errors.append(error)
        if self.client is not None:
            try:
                self.client.on_event = None
            except Exception as error:
                errors.append(error)
            try:
                self.client.stop()
            except Exception as error:
                errors.append(error)
        if self._battle_runtime is not None and stop_runtime:
            try:
                self._battle_runtime.stop(
                    show_login=show_login,
                    restore_account=restore_account)
            except Exception as error:
                errors.append(error)
        self._battle_started = False
        self._starting_round_id = None
        self._active_round_id = None
        self._departed_round_id = None
        self.snapshot = None
        self._start_requested = False
        self._pending_map = None
        self.state = 'stopped'
        if errors:
            raise errors[0]

    fini = stop
