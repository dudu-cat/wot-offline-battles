from __future__ import print_function

import json
import math
import socket
import threading
import time


PROTOCOL_VERSION = 5
CLIENT_BUILD = 'wot-0.9.22.0.1-cn-1513'
POLL_INTERVAL = 1.0 / 60.0
PING_INTERVAL = 1.0
MAX_MESSAGE_BYTES = 256 * 1024
MAX_BUFFER_BYTES = MAX_MESSAGE_BYTES * 2
MAX_PENDING_MESSAGES = 512
STATE_BARRIER_TYPES = frozenset((
    'welcome', 'roster', 'battle_start', 'start_denied', 'error'))
SERVER_STATE_TYPES = frozenset((
    'welcome', 'roster', 'battle_start', 'start_denied',
    'snapshot', 'events'))


try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


def _finite_float(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    try:
        if math.isnan(value) or math.isinf(value):
            return float(default)
    except Exception:
        pass
    return value


def _safe_text(value, default='', limit=80):
    if value is None:
        value = default
    if not isinstance(value, string_types):
        value = str(value)
    return value[:limit]


def _exact_int(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
        if float(value) != parsed:
            return default
        return parsed
    except (TypeError, ValueError, OverflowError):
        return default


def _mapping_list(value, limit=30):
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value[:limit]
            if isinstance(item, dict)]


def _strict_mapping_list(value, limit=30):
    if (not isinstance(value, (list, tuple)) or len(value) > limit or
            any(not isinstance(item, dict) for item in value)):
        return None
    return [dict(item) for item in value]


def _load_bigworld():
    import BigWorld
    return BigWorld


class LANClient(object):

    def __init__(self, host, port, name, vehicle, max_health=100,
                 on_event=None, bigworld=None):
        self.host = _safe_text(host, '127.0.0.1', 255)
        self.port = int(port or 28782)
        self.name = _safe_text(name, 'Player')
        self.vehicle = _safe_text(vehicle, 'ussr:R11_MS-1')
        self.max_health = max(1, int(max_health or 100))
        self.on_event = on_event
        self.bigworld = bigworld
        self.sock = None
        self.thread = None
        self.running = False
        self.connected = False
        self.ready = False
        self.phase = 'disconnected'
        self.player_id = None
        self.team = None
        self.slot = 0
        self.map_name = None
        self.map_pool = []
        self.spawn = None
        self.round_id = None
        self.state_revision = None
        self._battle_start_round_id = None
        self.roster = []
        self.host_player_id = None
        self.bot_authority_id = None
        self.last_snapshot = None
        self.last_error = None
        self.rtt_ms = None
        self._recv_buffer = u''
        self._pending = []
        self._pending_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._poll_callback = None
        self._last_ping = 0.0
        self._ping_seq = 0
        self._fire_seq = 0

    def start(self):
        if self.running:
            return False
        self.running = True
        self.phase = 'connecting'
        self.thread = threading.Thread(
            target=self._worker, name='offline-lan-0922')
        self.thread.setDaemon(True)
        self.thread.start()
        self._schedule_poll()
        return True

    def stop(self):
        if (not self.running and self.sock is None and
                self._poll_callback is None):
            return
        self._send({'type': 'leave'})
        self.running = False
        self.connected = False
        self.ready = False
        self.phase = 'disconnected'
        if self._poll_callback is not None and self.bigworld is not None:
            try:
                self.bigworld.cancelCallback(self._poll_callback)
            except Exception:
                pass
            self._poll_callback = None
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def request_start(self, map_name=None):
        if (not self.ready or self.phase != 'waiting' or
                self.player_id != self.host_player_id):
            return False
        message = {'type': 'start_battle', 'round_id': self.round_id}
        if map_name:
            map_name = _safe_text(map_name, '', 80)
            if self.map_pool and map_name not in self.map_pool:
                return False
            message['map'] = map_name
        return self._send(message)

    def is_room_host(self):
        return (self.ready and self.phase == 'waiting' and
                self.player_id is not None and
                self.player_id == self.host_player_id)

    def leave_battle(self):
        """Retire this player from the current round without closing TCP."""
        if not self.ready or self.phase != 'battle':
            return False
        return self._send({
            'type': 'leave_battle',
            'round_id': self.round_id,
        })

    def send_input(self, forward, turn, aim_yaw=0.0, gun_pitch=0.0,
                   position=None, yaw=None, fire_seq=0):
        if not self.ready or self.phase != 'battle':
            return False
        message = {
            'type': 'input',
            'round_id': self.round_id,
            'forward': max(-1.0, min(1.0, _finite_float(forward))),
            'turn': max(-1.0, min(1.0, _finite_float(turn))),
            'aim_yaw': _finite_float(aim_yaw),
            'gun_pitch': _finite_float(gun_pitch),
            'fire_seq': max(0, int(fire_seq or 0)),
        }
        if position is not None and len(position) >= 3:
            message['x'] = _finite_float(position[0])
            message['y'] = _finite_float(position[1])
            message['z'] = _finite_float(position[2])
            message['yaw'] = _finite_float(yaw)
        return self._send(message)

    def send_fire(self, shell_index=0, position=None, yaw=None,
                  aim_yaw=None, gun_pitch=None):
        """Send the v5 server's existing fire edge via an ``input`` message."""
        if not self.ready or self.phase != 'battle':
            return None
        previous = getattr(self, '_fire_seq', 0)
        self._fire_seq = previous + 1
        message = {'type': 'input', 'round_id': self.round_id,
                   'fire_seq': self._fire_seq,
                   'shell_index': max(0, min(int(shell_index or 0), 9))}
        if position is not None and len(position) >= 3:
            message['x'] = _finite_float(position[0])
            message['y'] = _finite_float(position[1])
            message['z'] = _finite_float(position[2])
            message['yaw'] = _finite_float(yaw)
        if aim_yaw is not None:
            message['aim_yaw'] = _finite_float(aim_yaw)
        if gun_pitch is not None:
            message['gun_pitch'] = _finite_float(gun_pitch)
        if not self._send(message):
            self._fire_seq = previous
            return None
        return self._fire_seq

    def send_hit(self, target_id, shot_seq, damage, shot_result,
                 shell_index=0, impact_position=None):
        if not self.ready or self.phase != 'battle':
            return False
        message = {'type': 'hit_report', 'round_id': self.round_id,
                   'target': int(target_id),
                   'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2)),
                   'shell_index': max(0, min(int(shell_index or 0), 9))}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        return self._send(message)

    def is_bot_authority(self):
        """Whether this connection currently owns authoritative bot messages."""
        if not self.ready or self.phase != 'battle':
            return False
        try:
            return int(self.player_id) == int(self.bot_authority_id)
        except (TypeError, ValueError):
            return False

    def send_bot_manifest(self, bots):
        if not self.is_bot_authority():
            return False
        return self._send({'type': 'bot_manifest',
                           'round_id': self.round_id,
                           'bots': list(bots or ())[:30]})

    def send_bot_state(self, bots):
        if not self.is_bot_authority():
            return False
        return self._send({'type': 'bot_state', 'round_id': self.round_id,
                           'bots': list(bots or ())[:30]})

    def send_bot_observation(self, contacts, affordances=None):
        if not self.is_bot_authority():
            return False
        return self._send({'type': 'bot_observation',
                           'round_id': self.round_id,
                           'contacts': list(contacts or ())[:64],
                           'affordances': list(affordances or ())[:16]})

    def send_bot_hit(self, target_id, shot_seq, damage, shot_result,
                     impact_position=None):
        if not self.ready or self.phase != 'battle':
            return False
        message = {'type': 'bot_hit_report', 'round_id': self.round_id,
                   'target': int(target_id),
                   'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2))}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        return self._send(message)

    def send_bot_human_hit(self, bot_id, target_id, shot_seq, damage,
                           shot_result, impact_position=None):
        if not self.is_bot_authority():
            return False
        message = {'type': 'bot_human_hit', 'round_id': self.round_id,
                   'attacker_bot': int(bot_id),
                   'target': int(target_id), 'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2))}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        return self._send(message)

    def send_bot_bot_hit(self, bot_id, target_id, shot_seq, damage,
                         shot_result, impact_position=None):
        """Report an authority-simulated bot shot against another bot."""
        if not self.is_bot_authority():
            return False
        message = {'type': 'bot_hit_report', 'round_id': self.round_id,
                   'attacker_bot': int(bot_id),
                   'target': int(target_id), 'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2))}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        return self._send(message)

    def send_rules_state(self, bases):
        """Send only the server's documented base points/stopped rule shape."""
        if not self.is_bot_authority():
            return False
        rules = {'bases': bases if isinstance(bases, dict) else {}}
        return self._send({'type': 'rules_state', 'round_id': self.round_id,
                           'rules': rules})

    def send_battle_result(self, winner, reason, base_team=0):
        if not self.is_bot_authority():
            return False
        return self._send({'type': 'battle_result',
                           'round_id': self.round_id,
                           'winner': int(winner),
                           'reason': _safe_text(reason, 'battle finished', 80),
                           'base_team': int(base_team or 0)})

    def _worker(self):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect((self.host, self.port))
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            sock.settimeout(0.5)
            self.sock = sock
            self.connected = True
            self._send({
                'type': 'hello',
                'protocol': PROTOCOL_VERSION,
                'client_build': CLIENT_BUILD,
                'name': self.name,
                'vehicle': self.vehicle,
                'max_health': self.max_health,
            })
            while self.running:
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    if self.running:
                        self.last_error = 'server closed the connection'
                    break
                try:
                    self._recv_buffer += chunk.decode('utf-8')
                except UnicodeError:
                    self.last_error = 'server sent invalid UTF-8'
                    break
                if len(self._recv_buffer) > MAX_BUFFER_BYTES:
                    self.last_error = 'server message buffer exceeded limit'
                    break
                while u'\n' in self._recv_buffer:
                    line, self._recv_buffer = self._recv_buffer.split(u'\n', 1)
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    self._queue_message(message)
        except Exception as error:
            if self.running:
                self.last_error = str(error)
        finally:
            self.connected = False
            self.running = False
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass
            self.sock = None

    def _queue_message(self, message):
        if not isinstance(message, dict):
            return
        with self._pending_lock:
            if len(self._pending) >= MAX_PENDING_MESSAGES:
                snapshot_index = next((
                    index for index, value in enumerate(self._pending)
                    if value.get('type') == 'snapshot'), None)
                removable_index = snapshot_index
                if removable_index is None:
                    removable_index = next((
                        index for index, value in enumerate(self._pending)
                        if value.get('type') not in STATE_BARRIER_TYPES), None)
                if removable_index is not None:
                    del self._pending[removable_index]
                elif message.get('type') not in STATE_BARRIER_TYPES:
                    return
                else:
                    self._pending.pop(0)
            self._pending.append(message)

    def _send(self, message):
        if not self.connected or self.sock is None:
            return False
        try:
            payload = (json.dumps(
                message, separators=(',', ':')) + '\n').encode('utf-8')
            if len(payload) > MAX_MESSAGE_BYTES:
                return False
            with self._send_lock:
                self.sock.sendall(payload)
            return True
        except Exception as error:
            self.last_error = str(error)
            return False

    def _schedule_poll(self):
        if self._poll_callback is not None:
            return
        if self.bigworld is None:
            self.bigworld = _load_bigworld()
        self._poll_callback = self.bigworld.callback(
            POLL_INTERVAL, self._poll)

    def _poll(self):
        self._poll_callback = None
        messages = []
        with self._pending_lock:
            if self._pending:
                messages = self._pending
                self._pending = []
        latest_snapshot = None
        for message in messages:
            if message.get('type') == 'snapshot':
                latest_snapshot = message
            else:
                # A roster/battle_start is a state-transition barrier.  Flush
                # the newest preceding snapshot before it so a terminal round
                # cannot be replayed after the waiting-room reset.
                if latest_snapshot is not None:
                    self._handle_message(latest_snapshot)
                    latest_snapshot = None
                self._handle_message(message)
        if latest_snapshot is not None:
            self._handle_message(latest_snapshot)
        now = time.time()
        if self.connected and now - self._last_ping >= PING_INTERVAL:
            self._last_ping = now
            self._ping_seq += 1
            self._send({
                'type': 'ping',
                'seq': self._ping_seq,
                'client_time': now,
            })
        if self.last_error is not None:
            self._notify('error', {'message': self.last_error})
            self.last_error = None
        if self.running:
            self._schedule_poll()

    def _handle_message(self, message):
        if not isinstance(message, dict):
            return
        kind = message.get('type')
        protocol = message.get('protocol')
        if protocol is not None or kind in SERVER_STATE_TYPES:
            try:
                matches_protocol = int(protocol) == PROTOCOL_VERSION
            except (TypeError, ValueError):
                matches_protocol = False
            if not matches_protocol:
                self.last_error = 'protocol mismatch'
                self.stop()
                return
        if kind == 'welcome':
            if _safe_text(message.get('client_build'), '') != CLIENT_BUILD:
                self.last_error = 'client build mismatch'
                self.stop()
                return
            player_id = _exact_int(message.get('player_id'))
            team = _exact_int(message.get('team'))
            round_id = _exact_int(message.get('round_id'))
            state_revision = _exact_int(message.get('state_revision'))
            host_player_id = _exact_int(message.get('host_player_id'))
            slot = _exact_int(message.get('slot'))
            max_health = _exact_int(message.get('max_health'))
            phase = _safe_text(message.get('phase'), '')
            map_name = _safe_text(message.get('map'), '')
            spawn = message.get('spawn')
            if (player_id is None or state_revision is None or
                    state_revision < 0 or host_player_id is None or
                    host_player_id <= 0 or team not in (1, 2) or
                    round_id is None or slot is None or not 0 <= slot < 15 or
                    max_health is None or max_health <= 0 or
                    phase != 'waiting' or not map_name or
                    not isinstance(spawn, dict) or
                    not all(axis in spawn for axis in ('x', 'y', 'z'))):
                self.last_error = 'invalid welcome message'
                self.stop()
                return
            self.ready = True
            self.player_id = player_id
            self.name = _safe_text(message.get('name'), self.name)
            self.vehicle = _safe_text(message.get('vehicle'), self.vehicle)
            self.team = team
            self.slot = slot
            self.max_health = max_health
            self.map_name = map_name
            self.map_pool = self._map_names(message.get('map_pool'))
            self.spawn = dict(spawn)
            self.phase = phase
            self.round_id = round_id
            self.state_revision = state_revision
            self.host_player_id = host_player_id
            self.bot_authority_id = message.get('bot_authority_id')
        elif kind == 'roster':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None:
                self.last_error = 'invalid roster message'
                self.stop()
                return
            if self.round_id is not None and round_id < self.round_id:
                return
            state_revision = _exact_int(message.get('state_revision'))
            if state_revision is None or state_revision < 0:
                self.last_error = 'invalid roster message'
                self.stop()
                return
            if (round_id == self.round_id and
                    self.state_revision is not None and
                    state_revision < self.state_revision):
                return
            phase = _safe_text(message.get('phase'), '')
            map_name = _safe_text(message.get('map'), '')
            players = _strict_mapping_list(message.get('players'), 64)
            host_player_id = _exact_int(message.get('host_player_id'))
            player_ids = set(_exact_int(value.get('id'))
                             for value in players or ())
            if (phase not in ('waiting', 'battle') or not map_name or
                    players is None or host_player_id not in player_ids):
                self.last_error = 'invalid roster message'
                self.stop()
                return
            # Different server threads serialize through Player.send_lock, but
            # a new battle_start can acquire it before the reset thread sends
            # its same-round waiting roster.  Round phase is monotonic: once
            # this client has entered battle, that older waiting roster cannot
            # demote it or cancel a deferred local start.
            if (round_id == self.round_id and self.phase == 'battle' and
                    phase == 'waiting'):
                return
            if round_id != self.round_id:
                self.last_snapshot = None
                self._fire_seq = 0
                self._battle_start_round_id = None
            self.round_id = round_id
            self.state_revision = state_revision
            self.phase = phase
            self.map_name = map_name
            maps = self._map_names(message.get('map_pool'))
            if maps:
                self.map_pool = maps
            self.roster = players
            self.host_player_id = host_player_id
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
        elif kind == 'battle_start':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None:
                self.last_error = 'invalid battle_start message'
                self.stop()
                return
            if self.round_id is not None and round_id < self.round_id:
                return
            state_revision = _exact_int(message.get('state_revision'))
            if state_revision is None or state_revision < 0:
                self.last_error = 'invalid battle_start message'
                self.stop()
                return
            stale_revision = (round_id == self.round_id and
                              self.state_revision is not None and
                              state_revision < self.state_revision)
            if (stale_revision and
                    self._battle_start_round_id == round_id):
                return
            if stale_revision:
                # battle_start is a transition barrier, not only a state
                # snapshot. A newer membership roster can overtake it on a
                # different server thread. Preserve that newer roster while
                # delivering the first start barrier exactly once.
                message = dict(message)
                state_revision = self.state_revision
                message['state_revision'] = state_revision
                if self.map_name:
                    message['map'] = self.map_name
                if self.roster:
                    message['players'] = list(self.roster)
                if self.host_player_id is not None:
                    message['host_player_id'] = self.host_player_id
                if self.bot_authority_id is not None:
                    message['bot_authority_id'] = self.bot_authority_id
            map_name = _safe_text(message.get('map'), '')
            players = _strict_mapping_list(message.get('players'), 64)
            local_ids = set(_exact_int(value.get('id')) for value in players or ())
            host_player_id = _exact_int(message.get('host_player_id'))
            if (not map_name or not players or
                    self.player_id not in local_ids or
                    host_player_id not in local_ids):
                self.last_error = 'invalid battle_start message'
                self.stop()
                return
            if round_id != self.round_id:
                self.last_snapshot = None
                self._fire_seq = 0
                self._battle_start_round_id = None
            self.phase = 'battle'
            self.map_name = map_name
            self.round_id = round_id
            self.state_revision = state_revision
            self.roster = players
            self.host_player_id = host_player_id
            self._battle_start_round_id = round_id
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
        elif kind == 'start_denied':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None or round_id != self.round_id:
                return
        elif kind == 'snapshot':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None:
                self.last_error = 'invalid snapshot message'
                self.stop()
                return
            if round_id != self.round_id:
                return
            server_tick = _exact_int(message.get('server_tick'))
            players = _strict_mapping_list(message.get('players'), 64)
            bots = _strict_mapping_list(message.get('bots'), 30)
            orders = None
            order_revision = None
            if 'bot_orders' in message:
                orders = _strict_mapping_list(message.get('bot_orders'), 30)
                order_revision = _exact_int(
                    message.get('bot_order_revision'))
            if (server_tick is None or server_tick < 0 or
                    players is None or bots is None or
                    ('bot_orders' in message and
                     (orders is None or order_revision is None or
                      order_revision < 0))):
                self.last_error = 'invalid snapshot message'
                self.stop()
                return
            self.last_snapshot = message
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
        elif kind == 'events':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None:
                self.last_error = 'invalid events message'
                self.stop()
                return
            if round_id != self.round_id:
                return
            server_tick = _exact_int(message.get('server_tick'))
            events = _strict_mapping_list(message.get('events'), 256)
            if server_tick is None or server_tick < 0 or events is None:
                self.last_error = 'invalid events message'
                self.stop()
                return
        elif kind == 'pong':
            client_time = _finite_float(message.get('client_time'), 0.0)
            if client_time > 0.0:
                sample = max(0.0, (time.time() - client_time) * 1000.0)
                if self.rtt_ms is None:
                    self.rtt_ms = sample
                else:
                    self.rtt_ms = self.rtt_ms * 0.75 + sample * 0.25
        elif kind == 'error':
            error_message = _safe_text(
                message.get('message'), message.get('code') or 'server error')
            self._notify('error', {'message': error_message})
            return
        self._notify(kind, message)

    def _notify(self, kind, message):
        if self.on_event is not None and kind is not None:
            self.on_event(kind, message)

    @staticmethod
    def _map_names(values):
        if not isinstance(values, (list, tuple)):
            return []
        result = []
        for value in values or ():
            name = _safe_text(value, '', 80)
            if name and name not in result:
                result.append(name)
        return result
