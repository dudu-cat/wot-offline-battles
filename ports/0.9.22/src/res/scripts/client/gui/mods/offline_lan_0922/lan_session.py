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
                 on_event=None):
        self._config = dict(config or {})
        self._client_factory = client_factory or _load_client
        self._queue_factory = queue_factory or queue_ui.QueueUI
        self._picker_opener = picker_opener or queue_ui.open_picker
        self._battle_runtime = battle_runtime
        self._on_snapshot = on_snapshot
        self._on_event_callback = on_event
        self.client = None
        self.snapshot = None
        self.state = 'idle'
        self._map_pool = []
        self._queue = None
        self._picker_open = False
        self._start_requested = False
        self._active_round_id = None
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
                                              self._map_pool_value)
            self._queue.install()

    def _open_waiting_picker(self):
        if self._stopped or self._picker_open:
            return False
        self._ensure_queue()
        self._picker_open = bool(self._picker_opener())
        return self._picker_open

    def _close_picker(self):
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

    def _waiting_event(self, message):
        self._map_pool = list(_message_value(
            message, 'map_pool', getattr(self.client, 'map_pool', [])) or [])
        phase = _message_value(message, 'phase', getattr(self.client, 'phase', None))
        if phase == 'waiting':
            if self._battle_started:
                if self._battle_runtime is not None:
                    self._battle_runtime.stop(show_login=False)
                self._battle_started = False
                self._active_round_id = None
                self.snapshot = None
                self._start_requested = False
            self.state = 'waiting'
            self._open_waiting_picker()
        elif phase == 'battle':
            # Disconnect/failover roster updates are broadcast during a live
            # round.  They update membership but must not demote the active
            # local battle back to an awaiting state.
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
        if self._battle_started and round_id == self._active_round_id:
            return False
        if self._battle_started:
            if self._battle_runtime is not None:
                self._battle_runtime.stop(show_login=False)
            self._battle_started = False
            self._active_round_id = None
            self.snapshot = None
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
            config, message=message, lan_client=self.client))
        if started:
            self._battle_started = True
            self._active_round_id = round_id
            self._start_requested = False
            self.state = 'battle'
            self._close_picker()
        return started

    def _on_event(self, kind, message):
        if self._stopped:
            return
        if kind in ('welcome', 'roster'):
            self._waiting_event(message)
        elif kind == 'start_denied':
            if self._battle_started:
                return
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

    def stop(self, show_login=True):
        if self._stopped:
            return
        self._stopped = True
        self._close_picker()
        if self._queue is not None:
            self._queue.uninstall()
        if self.client is not None:
            self.client.on_event = None
            self.client.stop()
        if self._battle_runtime is not None:
            self._battle_runtime.stop(show_login=show_login)
        self.state = 'stopped'

    fini = stop
