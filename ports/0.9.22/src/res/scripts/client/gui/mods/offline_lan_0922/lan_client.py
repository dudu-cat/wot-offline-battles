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
    'welcome', 'roster', 'battle_start', 'battle_live',
    'start_denied', 'events', 'error'))
SERVER_STATE_TYPES = frozenset((
    'welcome', 'roster', 'battle_start', 'battle_live', 'start_denied',
    'snapshot', 'events'))


def _monotonic_time():
    """Return one non-adjustable process clock on #1513 and test hosts."""
    function = getattr(time, 'monotonic', None)
    if callable(function):
        return float(function())
    # Python 2.7 on Windows defines time.clock() as elapsed wall time backed
    # by QueryPerformanceCounter.  That is the clock used by the #1513 client.
    return float(time.clock())


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


def _exact_finite_float(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return parsed
    except (TypeError, ValueError, OverflowError):
        return default


def _attach_critical_proposal(message, critical, base_revision, ack_seq,
                              hull_damage):
    """Attach one strict #1513 compare-and-swap critical proposal."""
    if not isinstance(critical, dict):
        return
    parsed = []
    for name, value in (
            ('critical_target_base_revision', base_revision),
            ('critical_target_ack_seq', ack_seq),
            ('hull_damage', hull_damage)):
        exact = _exact_int(value)
        if exact is None or exact < 0:
            raise ValueError('%s must be a non-negative integer' % name)
        parsed.append((name, exact))
    message['critical'] = critical
    for name, value in parsed:
        message[name] = value


def _valid_bot_combat_contract(bot):
    if not isinstance(bot, dict) or not isinstance(bot.get('critical'), dict):
        return False
    revision = _exact_int(bot.get('combat_revision'))
    base_revision = _exact_int(bot.get('combat_base_revision'))
    ack_seq = _exact_int(bot.get('combat_ack_seq'))
    fire_elapsed = _exact_finite_float(bot.get('combat_fire_elapsed'))
    fire_timer = _exact_finite_float(bot.get('combat_fire_timer'))
    if (revision is None or revision < 0 or
            base_revision is None or base_revision < 0 or
            base_revision > revision or ack_seq is None or ack_seq < 0 or
            fire_elapsed is None or fire_elapsed < 0.0 or
            fire_elapsed > 10.0 or fire_timer is None or
            fire_timer < 0.0 or fire_timer >= 1.0):
        return False
    if (not bool(bot['critical'].get('fire', False)) and
            (fire_elapsed != 0.0 or fire_timer != 0.0)):
        return False
    return True


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
        self._battle_live_round_id = None
        self.roster = []
        self.host_player_id = None
        self.bot_authority_id = None
        self.last_snapshot = None
        self.last_error = None
        self.rtt_ms = None
        self.combat_phase = 'loading'
        self.combat_deadline = None
        self.combat_end_deadline = None
        self.combat_duration = 900.0
        self._combat_timing_round_id = None
        self._combat_timing_tick = -1
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
        if not self.ready or self.phase not in ('loading', 'battle'):
            return False
        return self._send({
            'type': 'leave_battle',
            'round_id': self.round_id,
        })

    def send_input(self, forward, turn, aim_yaw=0.0, gun_pitch=0.0,
                   position=None, yaw=None, fire_seq=0,
                   speed=None,
                   reported_health=None, reported_critical=None,
                   reported_reason=None, reported_display_health=None,
                   reported_attacker=None, reported_attacker_bot=None,
                   reported_critical_base_revision=None,
                   reported_critical_seq=None):
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
        if speed is not None:
            message['speed'] = max(
                -200.0, min(200.0, _finite_float(speed)))
        if reported_health is not None:
            message['reported_health'] = max(0, int(reported_health))
        if isinstance(reported_critical, dict):
            message['reported_critical'] = reported_critical
            if (reported_critical_base_revision is None or
                    reported_critical_seq is None):
                raise ValueError(
                    '#1513 critical report requires revision and sequence')
            message['reported_critical_base_revision'] = max(
                0, int(reported_critical_base_revision))
            message['reported_critical_seq'] = max(
                1, int(reported_critical_seq))
        if reported_reason is not None:
            message['reported_reason'] = max(
                0, min(int(reported_reason or 0), 255))
        if reported_display_health is not None:
            message['reported_display_health'] = max(
                0, int(reported_display_health or 0))
        if reported_attacker is not None:
            message['reported_attacker'] = max(
                0, int(reported_attacker or 0))
        if reported_attacker_bot is not None:
            message['reported_attacker_bot'] = max(
                0, int(reported_attacker_bot or 0))
        return self._send(message)

    def send_battle_ready(self, bases=None):
        """Join the server-owned #1513 load barrier exactly once per round."""
        if not self.ready or self.phase != 'loading':
            return False
        message = {
            'type': 'battle_ready',
            'round_id': self.round_id,
        }
        if isinstance(bases, dict):
            message['bases'] = bases
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
                 shell_index=0, impact_position=None, critical=None,
                 splash=False, critical_target_base_revision=None,
                 critical_target_ack_seq=None, hull_damage=None):
        if not self.ready or self.phase != 'battle':
            return False
        message = {'type': 'hit_report', 'round_id': self.round_id,
                   'target': int(target_id),
                   'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2)),
                   'shell_index': max(0, min(int(shell_index or 0), 9)),
                   'splash': bool(splash)}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        _attach_critical_proposal(
            message, critical, critical_target_base_revision,
            critical_target_ack_seq, hull_damage)
        return self._send(message)

    def send_destructible(self, event):
        """Report one map-object result resolved by the trusted client."""
        if (not self.ready or self.phase != 'battle' or
                not isinstance(event, dict)):
            return False
        kind = _safe_text(event.get('destructible_kind'), '', 16)
        if kind not in ('tree', 'column', 'fragile', 'module'):
            return False
        chunk_id = _exact_int(event.get('chunk_id'))
        item_index = _exact_int(event.get('item_index'))
        is_shot = event.get('is_shot')
        if (chunk_id is None or item_index is None or
                not isinstance(is_shot, bool)):
            return False
        message = {
            'type': 'destructible', 'round_id': self.round_id,
            'destructible_kind': kind,
            'chunk_id': chunk_id, 'item_index': item_index,
            'x': _finite_float(event.get('x')),
            'y': _finite_float(event.get('y')),
            'z': _finite_float(event.get('z')),
            'fall_yaw': _finite_float(event.get('fall_yaw')),
            'speed': _finite_float(event.get('speed')),
            'is_shot': is_shot,
        }
        if event.get('mat_kind') is not None:
            mat_kind = _exact_int(event.get('mat_kind'))
            if mat_kind is None:
                return False
            message['mat_kind'] = mat_kind
        return self._send(message)

    def is_bot_authority(self):
        """Whether this connection currently owns authoritative bot messages."""
        if not self.ready or self.phase not in ('loading', 'battle'):
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
                     impact_position=None, critical=None, splash=False,
                     critical_target_base_revision=None,
                     critical_target_ack_seq=None, hull_damage=None):
        if not self.ready or self.phase != 'battle':
            return False
        message = {'type': 'bot_hit_report', 'round_id': self.round_id,
                   'target': int(target_id),
                   'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2)),
                   'splash': bool(splash)}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        _attach_critical_proposal(
            message, critical, critical_target_base_revision,
            critical_target_ack_seq, hull_damage)
        return self._send(message)

    def send_bot_human_hit(self, bot_id, target_id, shot_seq, damage,
                           shot_result, impact_position=None, critical=None,
                           splash=False,
                           critical_target_base_revision=None,
                           critical_target_ack_seq=None, hull_damage=None):
        if not self.is_bot_authority():
            return False
        message = {'type': 'bot_human_hit', 'round_id': self.round_id,
                   'attacker_bot': int(bot_id),
                   'target': int(target_id), 'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2)),
                   'splash': bool(splash)}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        _attach_critical_proposal(
            message, critical, critical_target_base_revision,
            critical_target_ack_seq, hull_damage)
        return self._send(message)

    def send_bot_bot_hit(self, bot_id, target_id, shot_seq, damage,
                         shot_result, impact_position=None, critical=None,
                         splash=False,
                         critical_target_base_revision=None,
                         critical_target_ack_seq=None, hull_damage=None):
        """Report an authority-simulated bot shot against another bot."""
        if not self.is_bot_authority():
            return False
        message = {'type': 'bot_hit_report', 'round_id': self.round_id,
                   'attacker_bot': int(bot_id),
                   'target': int(target_id), 'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2)),
                   'splash': bool(splash)}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        _attach_critical_proposal(
            message, critical, critical_target_base_revision,
            critical_target_ack_seq, hull_damage)
        return self._send(message)

    def send_bot_ram(self, bot_id, target_kind, target_id, ram_seq,
                     damage_to_bot, damage_to_target):
        """Report one mature cooldown-gated tank collision as authority."""
        if not self.is_bot_authority():
            return False
        kind = str(target_kind)
        if kind not in ('bot', 'human'):
            return False
        return self._send({
            'type': 'bot_ram_report', 'round_id': self.round_id,
            'bot_id': int(bot_id), 'target_kind': kind,
            'target_id': int(target_id), 'ram_seq': max(1, int(ram_seq)),
            'damage_to_bot': max(0, min(int(damage_to_bot or 0), 500)),
            'damage_to_target': max(
                0, min(int(damage_to_target or 0), 500)),
        })

    def send_rules_state(self, bases):
        """Send the server's documented standard-base state shape."""
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
            # The server requires hello to be the first wire message.  Do not
            # expose the socket to the BigWorld poller until it is on the
            # wire, or the first main-thread ping can win this race.
            hello = {
                'type': 'hello',
                'protocol': PROTOCOL_VERSION,
                'client_build': CLIENT_BUILD,
                'name': self.name,
                'vehicle': self.vehicle,
                'max_health': self.max_health,
            }
            payload = (json.dumps(
                hello, separators=(',', ':')) + '\n').encode('utf-8')
            with self._send_lock:
                sock.sendall(payload)
            self.connected = True
            while self.running:
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    if self.running:
                        self.last_error = 'server closed the connection'
                    break
                received_time = _monotonic_time()
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
                    if isinstance(message, dict):
                        # Frame stalls must not inflate RTT or countdown
                        # projection: both end in this network thread, not
                        # when the BigWorld main thread eventually drains.
                        message['_client_received_time'] = received_time
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
                    raise RuntimeError(
                        'LAN receive queue overflowed on ordered state')
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
            elif (message.get('type') == 'events' and
                  latest_snapshot is not None and
                  message.get('round_id') ==
                  latest_snapshot.get('round_id') and
                  message.get('server_tick') ==
                  latest_snapshot.get('server_tick')):
                # Preserve event-before-state semantics even if a fixture or
                # an older relay batches one tick in the opposite order.
                self._handle_message(message)
                self._handle_message(latest_snapshot)
                latest_snapshot = None
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
        now = _monotonic_time()
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

    def _load_server_timing(self, message):
        """Project relative server timing onto this client's receive clock."""
        timing = message.get('timing') if isinstance(message, dict) else None
        if not isinstance(timing, dict):
            return False
        round_id = _exact_int(message.get('round_id'))
        server_tick = _exact_int(message.get('server_tick'))
        phase = _safe_text(timing.get('phase'), '', 16)
        start_ms = _exact_int(timing.get('start_in_ms'))
        remaining_ms = _exact_int(timing.get('remaining_ms'))
        duration_ms = _exact_int(timing.get('duration_ms'))
        if (round_id is None or round_id != self.round_id or
                server_tick is None or server_tick < 0 or
                phase not in ('loading', 'prebattle', 'battle', 'finished') or
                start_ms is None or start_ms < 0 or
                remaining_ms is None or remaining_ms < 0 or
                duration_ms is None or duration_ms <= 0 or
                remaining_ms > duration_ms):
            return False
        if (self._combat_timing_round_id == round_id and
                server_tick <= self._combat_timing_tick):
            return True
        received = _finite_float(
            message.get('_client_received_time'), _monotonic_time())
        one_way = 0.0
        if self.rtt_ms is not None:
            one_way = max(
                0.0, min(0.25, float(self.rtt_ms) / 2000.0))
        duration = float(duration_ms) / 1000.0
        if phase == 'prebattle':
            projected_start = received + float(start_ms) / 1000.0 - one_way
            if (self.combat_deadline is None or
                    abs(self.combat_deadline - projected_start) > 0.25):
                self.combat_deadline = projected_start
            else:
                self.combat_deadline = (
                    self.combat_deadline * 0.8 + projected_start * 0.2)
            projected_end = self.combat_deadline + duration
        elif phase == 'battle':
            if self.combat_deadline is None:
                self.combat_deadline = received - one_way
            projected_end = (
                received + float(remaining_ms) / 1000.0 - one_way)
        elif phase == 'finished':
            projected_end = received - one_way
        else:
            projected_end = received + duration - one_way
        self.combat_phase = phase
        self.combat_duration = duration
        if (self.combat_end_deadline is None or
                abs(self.combat_end_deadline - projected_end) > 0.25):
            self.combat_end_deadline = projected_end
        else:
            self.combat_end_deadline = (
                self.combat_end_deadline * 0.8 + projected_end * 0.2)
        self._combat_timing_round_id = round_id
        self._combat_timing_tick = server_tick
        return True

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
            self._battle_live_round_id = None
            self._combat_timing_round_id = None
            self._combat_timing_tick = -1
            self.combat_deadline = None
            self.combat_end_deadline = None
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
            if (phase not in ('waiting', 'loading', 'battle') or not map_name or
                    players is None or host_player_id not in player_ids):
                self.last_error = 'invalid roster message'
                self.stop()
                return
            # Different server threads serialize through Player.send_lock, but
            # a new battle_start can acquire it before the reset thread sends
            # its same-round waiting roster.  Round phase is monotonic: once
            # this client has entered battle, that older waiting roster cannot
            # demote it or cancel a deferred local start.
            if (round_id == self.round_id and
                    self.phase in ('loading', 'battle') and
                    phase == 'waiting'):
                return
            if round_id != self.round_id:
                self.last_snapshot = None
                self._fire_seq = 0
                self._battle_start_round_id = None
                self._battle_live_round_id = None
                self._combat_timing_round_id = None
                self._combat_timing_tick = -1
                self.combat_deadline = None
                self.combat_end_deadline = None
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
            phase = _safe_text(message.get('phase'), '')
            players = _strict_mapping_list(message.get('players'), 64)
            local_ids = set(_exact_int(value.get('id')) for value in players or ())
            host_player_id = _exact_int(message.get('host_player_id'))
            if (phase != 'loading' or
                    not map_name or not players or
                    self.player_id not in local_ids or
                    host_player_id not in local_ids):
                self.last_error = 'invalid battle_start message'
                self.stop()
                return
            if round_id != self.round_id:
                self.last_snapshot = None
                self._fire_seq = 0
                self._battle_start_round_id = None
                self._battle_live_round_id = None
                self._combat_timing_round_id = None
                self._combat_timing_tick = -1
                self.combat_deadline = None
                self.combat_end_deadline = None
            self.phase = phase
            self.map_name = map_name
            self.round_id = round_id
            self.state_revision = state_revision
            self.roster = players
            self.host_player_id = host_player_id
            self._battle_start_round_id = round_id
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
        elif kind == 'battle_live':
            round_id = _exact_int(message.get('round_id'))
            state_revision = _exact_int(message.get('state_revision'))
            countdown = _finite_float(
                message.get('countdown_seconds'), -1.0)
            duration = _finite_float(
                message.get('battle_duration_seconds'), -1.0)
            if (round_id is None or round_id != self.round_id or
                    self.phase not in ('loading', 'battle') or
                    state_revision is None or state_revision < 0 or
                    countdown < 0.0 or duration <= 0.0):
                self.last_error = 'invalid battle_live message'
                self.stop()
                return
            if not self._load_server_timing(message):
                self.last_error = 'invalid battle timing'
                self.stop()
                return
            if self._battle_live_round_id == round_id:
                return
            self.phase = 'battle'
            if (self.state_revision is None or
                    state_revision > self.state_revision):
                self.state_revision = state_revision
            self._battle_live_round_id = round_id
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
            manifest = None
            if 'bot_manifest' in message:
                manifest = _strict_mapping_list(
                    message.get('bot_manifest'), 30)
            orders = None
            order_revision = None
            if 'bot_orders' in message:
                orders = _strict_mapping_list(message.get('bot_orders'), 30)
                order_revision = _exact_int(
                    message.get('bot_order_revision'))
            destructibles = None
            destructible_revision = None
            if 'destructibles' in message:
                destructibles = _strict_mapping_list(
                    message.get('destructibles'), 4096)
                destructible_revision = _exact_int(
                    message.get('destructible_revision'))
            player_critical_contract = all(
                _exact_int(player.get('critical_revision')) is not None and
                _exact_int(player.get('critical_revision')) >= 0 and
                _exact_int(player.get('critical_base_revision')) is not None and
                _exact_int(player.get('critical_base_revision')) >= 0 and
                _exact_int(player.get('critical_ack_seq')) is not None and
                _exact_int(player.get('critical_ack_seq')) >= 0
                for player in players or ())
            bot_combat_contract = all(
                _valid_bot_combat_contract(bot) for bot in bots or ())
            if (server_tick is None or server_tick < 0 or
                    players is None or bots is None or
                    not player_critical_contract or
                    not bot_combat_contract or
                    ('bot_manifest' in message and manifest is None) or
                    ('bot_orders' in message and
                     (orders is None or order_revision is None or
                      order_revision < 0)) or
                    ('destructibles' in message and
                     (destructibles is None or
                      destructible_revision is None or
                      destructible_revision < 0))):
                self.last_error = 'invalid snapshot message'
                self.stop()
                return
            if ('timing' in message and
                    not self._load_server_timing(message)):
                self.last_error = 'invalid battle timing'
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
                received_time = _finite_float(
                    message.get('_client_received_time'), _monotonic_time())
                sample = max(
                    0.0, (received_time - client_time) * 1000.0)
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
