from __future__ import print_function

"""Coordinator between LAN protocol, stock map picker and battle runtime."""

from gui.mods.offline_lan_0922 import queue_ui


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
                 cancel_callback=None):
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
        self.client = None
        self.snapshot = None
        self.state = 'idle'
        self._map_pool = []
        self._queue = None
        self._picker_open = False
        self._picker_callback_id = None
        self._battle_start_callback_id = None
        self._pending_battle_start = None
        self._start_requested = False
        self._active_round_id = None
        self._departed_round_id = None
        self._battle_started = False
        self._stopped = False

    def start(self):
        if self._stopped or self.client is not None:
            return False
        factory = self._client_factory
        if factory is _load_client:
            factory = factory()
        self.client = factory(
            self._config.get('host', '127.0.0.1'),
            self._config.get('port', 28782),
            self._config.get('name', 'Player'),
            self._config.get('vehicle', 'ussr:R11_MS-1'),
            max_health=self._config.get('max_health', 100),
            on_event=self._on_event)
        self.state = 'connecting'
        return bool(self.client.start())

    def _map_pool_value(self):
        return list(self._map_pool)

    def _ensure_queue(self):
        if self._queue is None:
            self._queue = self._queue_factory(self.request_start,
                                              self._map_pool_value,
                                              on_close=self._on_picker_closed)
            self._queue.install()

    def _on_picker_closed(self):
        self._picker_open = False

    def _cancel_picker_callback(self):
        callback_id = self._picker_callback_id
        self._picker_callback_id = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _schedule_picker_when_lobby_ready(self):
        if self._picker_callback_id is not None or not callable(self._callback):
            return False

        def retry():
            self._picker_callback_id = None
            if not self._stopped and self.state == 'waiting':
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

    def request_start(self, map_name):
        if self._stopped or self.state != 'waiting' or self.client is None:
            return False
        accepted = bool(self.client.request_start(map_name))
        if accepted:
            self._start_requested = True
            self._close_picker()
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
            self._active_round_id = None
            self.snapshot = None
            self._start_requested = False

    def _waiting_event(self, message):
        self._map_pool = list(_message_value(
            message, 'map_pool', getattr(self.client, 'map_pool', [])) or [])
        phase = _message_value(message, 'phase', getattr(self.client, 'phase', None))
        if phase == 'waiting':
            self._clear_pending_battle_start()
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
            self.state = 'waiting'
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
        started = bool(self._battle_runtime.start(
            config, message=message, lan_client=self.client,
            on_local_leave=self._on_local_battle_leave))
        if started:
            self._battle_started = True
            self._active_round_id = round_id
            self._start_requested = False
            self.state = 'battle'
            self._close_picker()
        return started

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
        self._active_round_id = None
        self._departed_round_id = None
        self.snapshot = None
        self._start_requested = False
        self.state = 'stopped'
        if errors:
            raise errors[0]

    fini = stop
