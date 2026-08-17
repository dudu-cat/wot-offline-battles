from __future__ import print_function

import json
import math
import socket
import threading
import time


PROTOCOL_VERSION = 5
CLIENT_BUILD = 'wot-0.9.22.0.1-cn-1513'
PROJECTILE_LEDGER_CAPABILITY = 'projectile_ledger_v1'
CLIENT_CAPABILITIES = (PROJECTILE_LEDGER_CAPABILITY,)
POLL_INTERVAL = 1.0 / 60.0
PING_INTERVAL = 1.0
MAX_MESSAGE_BYTES = 256 * 1024
MAX_BUFFER_BYTES = MAX_MESSAGE_BYTES * 2
MAX_PENDING_MESSAGES = 512
MAX_OUTBOUND_MESSAGES = 256
MAX_OUTBOUND_BYTES = MAX_MESSAGE_BYTES * 4
MAX_OUTBOUND_NODES = 16384
MAX_OUTBOUND_DEPTH = 16
MAX_PROJECTILE_BATCH = 30
MAX_PROJECTILE_DESTRUCTIBLES = 64
MAX_PROJECTILE_ID = 2147483647
MAX_PROJECTILE_ORIGIN = 5000.0
MAX_PROJECTILE_VELOCITY = 3000.0
# #1513 includes SPG shells such as the B-4 with gravity=143.  Keep the
# protocol bound finite without rejecting stock descriptors.
MAX_PROJECTILE_GRAVITY = 500.0
MAX_PROJECTILE_DISTANCE = 10000.0
MAX_PROJECTILE_TIME_MS = 20000
MAX_PROJECTILE_SPLASH_RADIUS = 100.0
MAX_PROJECTILE_PIERCING_LOSS = 100000.0
SENDER_JOIN_TIMEOUT = 0.1
LEAVE_SEND_TIMEOUT = 0.05
LEAVE_PAYLOAD = b'{"type":"leave"}\n'
_BOT_STATE_WIRE_FIELDS = (
    'id', 'x', 'y', 'z', 'yaw', 'aim_yaw', 'gun_pitch',
    'movement_dir', 'rotation_dir', 'fire_seq', 'shell_index',
    'next_shell_index', 'ammo_remaining', 'ammo_reload_pending',
    'health', 'alive', 'critical', 'combat_base_revision', 'combat_seq',
    'combat_fire_elapsed', 'combat_fire_timer',
    'death_reason', 'display_health', 'world_pose')
STATE_BARRIER_TYPES = frozenset((
    'welcome', 'roster', 'battle_start', 'battle_live',
    'start_denied', 'events', 'error'))
SERVER_STATE_TYPES = frozenset((
    'welcome', 'roster', 'battle_start', 'battle_live', 'start_denied',
    'snapshot', 'events', 'bot_observation'))


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

try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


class _OutboundPayloadError(Exception):
    pass


def _json_text_size(value):
    """Return a conservative UTF-8 byte bound for JSON's quoted text."""
    size = 2
    for character in value:
        if not isinstance(character, string_types):
            character = chr(character)
        number = ord(character)
        if character in ('"', '\\') or number in (8, 9, 10, 12, 13):
            size += 2
        elif number < 32 or number == 127:
            size += 6
        elif number < 128:
            size += 1
        elif number <= 65535:
            size += 6
        else:
            size += 12
    return size


def _freeze_outbound(value, budget, depth=0):
    """Copy plain JSON data and estimate its maximum encoded wire size."""
    if depth > MAX_OUTBOUND_DEPTH:
        raise _OutboundPayloadError('outbound payload nesting exceeded limit')
    budget[0] += 1
    if budget[0] > MAX_OUTBOUND_NODES:
        raise _OutboundPayloadError('outbound payload node count exceeded limit')
    if value is None:
        return None, 4
    if isinstance(value, bool):
        return value, 4 if value else 5
    if isinstance(value, integer_types):
        try:
            return value, len(str(value))
        except Exception:
            raise _OutboundPayloadError('outbound integer is not encodable')
    if isinstance(value, float):
        try:
            if math.isnan(value) or math.isinf(value):
                raise _OutboundPayloadError(
                    'outbound float must be finite')
            # Allow for encoder spelling differences across Python 2 and 3.
            return value, len(repr(value)) + 8
        except _OutboundPayloadError:
            raise
        except Exception:
            raise _OutboundPayloadError('outbound float is not encodable')
    if isinstance(value, string_types):
        return value, _json_text_size(value)
    if isinstance(value, (list, tuple)):
        frozen = []
        size = 2
        for item in value:
            copied, item_size = _freeze_outbound(item, budget, depth + 1)
            if frozen:
                size += 1
            size += item_size
            if size + 1 > MAX_MESSAGE_BYTES:
                raise _OutboundPayloadError(
                    'outbound payload exceeded wire limit')
            frozen.append(copied)
        return tuple(frozen), size
    if isinstance(value, dict):
        frozen = {}
        size = 2
        for key, item in value.items():
            if not isinstance(key, string_types):
                raise _OutboundPayloadError(
                    'outbound mapping key must be text')
            copied, item_size = _freeze_outbound(item, budget, depth + 1)
            if frozen:
                size += 1
            size += _json_text_size(key) + 1 + item_size
            if size + 1 > MAX_MESSAGE_BYTES:
                raise _OutboundPayloadError(
                    'outbound payload exceeded wire limit')
            frozen[key] = copied
        return frozen, size
    raise _OutboundPayloadError('outbound payload contains non-plain data')


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


def _project_bot_state(state):
    """Return only fields consumed by the v5 server bot-state sanitizer."""
    if not isinstance(state, dict):
        return None
    projected = dict((name, state[name]) for name in _BOT_STATE_WIRE_FIELDS
                     if name in state)
    has_shot_yaw = 'shot_yaw' in state
    has_shot_pitch = 'shot_pitch' in state
    if has_shot_yaw != has_shot_pitch:
        return None
    if has_shot_yaw:
        projected['shot_yaw'] = state['shot_yaw']
        projected['shot_pitch'] = state['shot_pitch']
    ammo_fields = ('shell_index', 'next_shell_index', 'ammo_remaining',
                   'ammo_reload_pending')
    present = tuple(name in state for name in ammo_fields)
    if any(present) and not all(present):
        return None
    if (all(present) and
            not isinstance(state.get('ammo_reload_pending'), bool)):
        return None
    return projected


def _projectile_int_range(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, integer_types):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _projectile_float_range(value, minimum, maximum):
    if (isinstance(value, bool) or
            not isinstance(value, integer_types + (float,))):
        return None
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def _strict_vector3(value, maximum_abs):
    """Return one detached JSON vector, rejecting tuples and coercion gaps."""
    if not isinstance(value, list) or len(value) != 3:
        return None
    result = []
    for component in value:
        parsed = _projectile_float_range(
            component, -float(maximum_abs), float(maximum_abs))
        if parsed is None:
            return None
        result.append(parsed)
    return result


def _strict_vector3_bounds(value, lows, highs):
    if (not isinstance(value, list) or len(value) != 3 or
            len(lows) != 3 or len(highs) != 3):
        return None
    result = []
    for index, component in enumerate(value):
        parsed = _projectile_float_range(
            component, float(lows[index]), float(highs[index]))
        if parsed is None:
            return None
        result.append(parsed)
    return result


def _strict_world_position(value):
    return _strict_vector3_bounds(
        value, (-5000.0, -1000.0, -5000.0),
        (5000.0, 3000.0, 5000.0))


def _strict_launch_velocity(value):
    result = _strict_vector3(value, MAX_PROJECTILE_VELOCITY)
    if result is None:
        return None
    speed_sq = sum(component * component for component in result)
    if speed_sq <= 0.000001 or speed_sq > MAX_PROJECTILE_VELOCITY ** 2:
        return None
    return result


def _strict_capabilities(value):
    if not isinstance(value, list) or len(value) > 32:
        return None
    result = []
    for item in value:
        if not isinstance(item, string_types):
            return None
        item = _safe_text(item, '', 80)
        if not item or item in result:
            return None
        result.append(item)
    return result


def _strict_projectile_id(value):
    if (not isinstance(value, string_types) or not value or
            len(value) > 96):
        return None
    for character in value:
        if (ord(character) >= 128 or
                not (character.isalnum() or character in ':_-')):
            return None
    return value


def _strict_projectile_effect(value):
    """Validate one terminal direct/splash damage proposal."""
    if not isinstance(value, dict):
        return None
    required = frozenset((
        'target_kind', 'target_id', 'damage', 'shot_result', 'x', 'y', 'z'))
    critical_fields = frozenset((
        'critical', 'critical_target_base_revision',
        'critical_target_ack_seq', 'hull_damage'))
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(
            required | critical_fields):
        return None
    kind = value.get('target_kind')
    target_id = _projectile_int_range(
        value.get('target_id'), 1, MAX_PROJECTILE_ID)
    damage = _projectile_int_range(value.get('damage'), 0, 5000)
    shot_result = _projectile_int_range(value.get('shot_result'), 0, 2)
    position = []
    for axis in ('x', 'y', 'z'):
        position.append(_projectile_float_range(
            value.get(axis),
            -1000.0 if axis == 'y' else -MAX_PROJECTILE_ORIGIN,
            3000.0 if axis == 'y' else MAX_PROJECTILE_ORIGIN))
    has_critical = 'critical' in value
    if (kind not in ('player', 'bot') or target_id is None or
            damage is None or shot_result is None or
            any(component is None for component in position) or
            (has_critical and keys != required | critical_fields) or
            (not has_critical and keys != required)):
        return None
    result = {
        'target_kind': kind,
        'target_id': target_id,
        'damage': damage,
        'shot_result': shot_result,
        'x': position[0],
        'y': position[1],
        'z': position[2],
    }
    if has_critical:
        critical = value.get('critical')
        base_revision = _projectile_int_range(
            value.get('critical_target_base_revision'), 0,
            MAX_PROJECTILE_ID)
        ack_seq = _projectile_int_range(
            value.get('critical_target_ack_seq'), 0,
            MAX_PROJECTILE_ID)
        hull_damage = _projectile_int_range(
            value.get('hull_damage'), 0, 5000)
        if (not isinstance(critical, dict) or base_revision is None or
                ack_seq is None or hull_damage is None):
            return None
        result['critical'] = critical
        result['critical_target_base_revision'] = base_revision
        result['critical_target_ack_seq'] = ack_seq
        result['hull_damage'] = hull_damage
    return result


def _strict_projectile_destructible(value):
    """Validate one shot-created destructible receipt for ledger CAS."""
    if not isinstance(value, dict):
        return None
    required = frozenset((
        'destructible_kind', 'chunk_id', 'item_index',
        'x', 'y', 'z', 'fall_yaw', 'speed', 'is_shot'))
    optional = frozenset(('mat_kind',))
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        return None
    kind = value.get('destructible_kind')
    chunk_id = _projectile_int_range(value.get('chunk_id'), 0, 4294967295)
    item_index = _projectile_int_range(value.get('item_index'), 0, 1048575)
    position = _strict_world_position([
        value.get('x'), value.get('y'), value.get('z')])
    fall_yaw = _projectile_float_range(
        value.get('fall_yaw'), -math.pi * 4.0, math.pi * 4.0)
    speed = _projectile_float_range(value.get('speed'), -200.0, 200.0)
    if (kind not in ('tree', 'column', 'fragile', 'module') or
            chunk_id is None or item_index is None or position is None or
            fall_yaw is None or speed is None or value.get('is_shot') is not True):
        return None
    result = {
        'destructible_kind': kind,
        'chunk_id': chunk_id,
        'item_index': item_index,
        'x': position[0], 'y': position[1], 'z': position[2],
        'fall_yaw': fall_yaw,
        'speed': speed,
        'is_shot': True,
    }
    if 'mat_kind' in value:
        mat_kind = _projectile_int_range(value.get('mat_kind'), 0, 65535)
        if mat_kind is None:
            return None
        result['mat_kind'] = mat_kind
    if kind == 'module' and 'mat_kind' not in result:
        return None
    return result


def _valid_active_projectiles(value, authority_epoch, server_time_ms):
    """Validate the complete server snapshot ledger without normalizing it."""
    if not isinstance(value, list) or len(value) > 256:
        return False
    expected = frozenset((
        'projectile_id', 'shooter_kind', 'shooter_id', 'shot_seq',
        'source_vehicle', 'shell_index', 'team', 'origin', 'velocity', 'gravity',
        'max_distance', 'max_time_ms', 'is_he', 'splash_radius',
        'penetration_factor', 'launch_server_time_ms',
        'checked_through_ms', 'checked_distance', 'piercing_loss',
        'authority_epoch'))
    seen = set()
    for projectile in value:
        if not isinstance(projectile, dict) or set(projectile) != expected:
            return False
        projectile_id = _strict_projectile_id(
            projectile.get('projectile_id'))
        shooter_kind = projectile.get('shooter_kind')
        source_vehicle = projectile.get('source_vehicle')
        shooter_id = _projectile_int_range(
            projectile.get('shooter_id'), 1, MAX_PROJECTILE_ID)
        shot_seq = _projectile_int_range(
            projectile.get('shot_seq'), 1, MAX_PROJECTILE_ID)
        shell_index = _projectile_int_range(
            projectile.get('shell_index'), 0, 9)
        team = _projectile_int_range(projectile.get('team'), 1, 2)
        origin = _strict_world_position(projectile.get('origin'))
        velocity = _strict_launch_velocity(projectile.get('velocity'))
        gravity = _projectile_float_range(
            projectile.get('gravity'), 0.000001, MAX_PROJECTILE_GRAVITY)
        max_distance = _projectile_float_range(
            projectile.get('max_distance'), 0.000001,
            MAX_PROJECTILE_DISTANCE)
        max_time_ms = _projectile_int_range(
            projectile.get('max_time_ms'), 1, MAX_PROJECTILE_TIME_MS)
        splash_radius = _projectile_float_range(
            projectile.get('splash_radius'), 0.0,
            MAX_PROJECTILE_SPLASH_RADIUS)
        penetration_factor = _projectile_float_range(
            projectile.get('penetration_factor'), 0.0, 100.0)
        launch_time = _projectile_int_range(
            projectile.get('launch_server_time_ms'), 0,
            MAX_PROJECTILE_ID)
        checked_through = _projectile_int_range(
            projectile.get('checked_through_ms'), 0,
            MAX_PROJECTILE_TIME_MS)
        checked_distance = _projectile_float_range(
            projectile.get('checked_distance'), 0.0,
            MAX_PROJECTILE_DISTANCE + 0.1)
        piercing_loss = _projectile_float_range(
            projectile.get('piercing_loss'), 0.0,
            MAX_PROJECTILE_PIERCING_LOSS)
        is_he = projectile.get('is_he')
        epoch = _projectile_int_range(
            projectile.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
        if (projectile_id is None or projectile_id in seen or
                shooter_kind not in ('player', 'bot') or
                not isinstance(source_vehicle, string_types) or
                not source_vehicle or len(source_vehicle) > 128 or
                shooter_id is None or shot_seq is None or
                shell_index is None or team is None or origin is None or
                velocity is None or gravity is None or
                max_distance is None or max_time_ms is None or
                not isinstance(is_he, bool) or splash_radius is None or
                penetration_factor is None or launch_time is None or
                launch_time > server_time_ms or checked_through is None or
                checked_through > max_time_ms or checked_distance is None or
                checked_distance > max_distance + 0.1 or
                piercing_loss is None or epoch != authority_epoch):
            return False
        seen.add(projectile_id)
    return True


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
        self.authority_epoch = None
        self.server_time_ms = None
        self.capabilities = []
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
        self._outbound_lock = threading.Lock()
        self._outbound_event = threading.Event()
        self._outbound_queue = []
        self._outbound_bytes = 0
        self._outbound_seq = 0
        self._outbound_accepting = False
        self._sender_thread = None
        self._transport_generation = 0
        self._stopping = False
        self._poll_callback = None
        self._last_ping = 0.0
        self._ping_seq = 0
        self._fire_seq = 0
        self._projectile_lock = threading.Lock()

    def start(self):
        with self._outbound_lock:
            if self.running:
                return False
            self._transport_generation += 1
            generation = self._transport_generation
            self._outbound_queue = []
            self._outbound_bytes = 0
            self._outbound_seq = 0
            self._outbound_accepting = False
            self._stopping = False
            self.running = True
            self.connected = False
            self.last_error = None
            self.phase = 'connecting'
            self.capabilities = []
            self.authority_epoch = None
            self.server_time_ms = None
        with self._pending_lock:
            self._pending = []
            self._recv_buffer = u''
        self._outbound_event.clear()
        self.thread = threading.Thread(
            target=self._worker, args=(generation,),
            name='offline-lan-0922')
        self.thread.setDaemon(True)
        self.thread.start()
        self._schedule_poll()
        return True

    def stop(self):
        with self._outbound_lock:
            if (not self.running and self.sock is None and
                    self._poll_callback is None and
                    self._sender_thread is None):
                return
            generation = self._transport_generation
            sender_thread = self._sender_thread
            receive_thread = self.thread
            sock = self.sock
            was_connected = self.connected
            self._stopping = True
            self._outbound_accepting = False
            self.running = False
            self.connected = False
            self.ready = False
            self.phase = 'disconnected'
        self._outbound_event.set()

        # Leave must not sit behind stale state.  Send it synchronously when
        # the sender is not already back-pressured; never wait for that lock.
        acquired = False
        try:
            acquired = self._send_lock.acquire(False)
            if (acquired and was_connected and sock is not None):
                with self._outbound_lock:
                    may_leave = (
                        generation == self._transport_generation and
                        self.sock is sock and self._stopping)
                if may_leave:
                    try:
                        sock.settimeout(LEAVE_SEND_TIMEOUT)
                    except Exception:
                        pass
                    sock.sendall(LEAVE_PAYLOAD)
        except Exception:
            pass
        finally:
            if acquired:
                self._send_lock.release()

        if self._poll_callback is not None and self.bigworld is not None:
            try:
                self.bigworld.cancelCallback(self._poll_callback)
            except Exception:
                pass
            self._poll_callback = None
        with self._pending_lock:
            self._pending = []
            self._recv_buffer = u''
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
        with self._outbound_lock:
            if (generation == self._transport_generation and
                    self.sock is sock):
                self.sock = None
        current = threading.current_thread()
        for worker in (sender_thread, receive_thread):
            if worker is not None and worker is not current:
                try:
                    worker.join(SENDER_JOIN_TIMEOUT)
                except Exception:
                    pass
        with self._outbound_lock:
            if generation == self._transport_generation:
                self._outbound_queue = []
                self._outbound_bytes = 0
                self._outbound_accepting = False
                if (sender_thread is not None and
                        self._sender_thread is sender_thread):
                    is_alive = getattr(sender_thread, 'is_alive', None)
                    if is_alive is None:
                        is_alive = sender_thread.isAlive
                    if not is_alive():
                        self._sender_thread = None

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
            # SpawnPlanner deliberately indexes its local formations by the
            # integer team ids 1 and 2.  JSON writes those keys as text, but
            # the reliable sender freezes only already-canonical JSON data so
            # it can reject ambiguous mappings before the worker thread.  Do
            # the one schema conversion at this wire boundary.
            wire_bases = {}
            for team in (1, 2):
                points = bases.get(str(team))
                if points is None:
                    points = bases.get(team)
                if points is not None:
                    wire_bases[str(team)] = points
            message['bases'] = wire_bases
        return self._send(message)

    def send_fire(self, shell_index=0, position=None, yaw=None,
                  aim_yaw=None, gun_pitch=None, velocity=None, gravity=None,
                  max_distance=None, max_time_ms=None, is_he=False,
                  splash_radius=0.0, penetration_factor=1.0):
        """Compatibility wrapper for a modern player projectile launch.

        A #1513 client must never fall back to the old instantaneous ``input``
        fire edge.  Callers therefore have to provide the frozen launch
        physics; legacy positional aim arguments remain only for call-site
        compatibility and are not put on the wire.
        """
        if (position is None or velocity is None or gravity is None or
                max_distance is None or max_time_ms is None):
            return None
        return self.send_projectile_launch(
            'player', self.player_id, None, shell_index, position, velocity,
            gravity, max_distance, max_time_ms, is_he, splash_radius,
            penetration_factor=penetration_factor)

    def send_projectile_launch(
            self, shooter_kind, shooter_id, shot_seq, shell_index, origin,
            velocity, gravity, max_distance, max_time_ms, is_he,
            splash_radius, authority_epoch=None, penetration_factor=1.0):
        """Enqueue one immutable projectile launch and return its shot seq."""
        if not self.ready or self.phase != 'battle':
            return None
        if shooter_kind not in ('player', 'bot'):
            return None
        parsed_shooter_id = _projectile_int_range(
            shooter_id, 1, MAX_PROJECTILE_ID)
        parsed_shell = _projectile_int_range(shell_index, 0, 9)
        parsed_origin = _strict_world_position(origin)
        parsed_velocity = _strict_launch_velocity(velocity)
        parsed_gravity = _projectile_float_range(
            gravity, 0.000001, MAX_PROJECTILE_GRAVITY)
        parsed_distance = _projectile_float_range(
            max_distance, 0.000001, MAX_PROJECTILE_DISTANCE)
        parsed_time = _projectile_int_range(
            max_time_ms, 1, MAX_PROJECTILE_TIME_MS)
        parsed_splash = _projectile_float_range(
            splash_radius, 0.0, MAX_PROJECTILE_SPLASH_RADIUS)
        parsed_penetration = _projectile_float_range(
            penetration_factor, 0.0, 100.0)
        if (parsed_shooter_id is None or parsed_shell is None or
                parsed_origin is None or parsed_velocity is None or
                parsed_gravity is None or parsed_distance is None or
                parsed_time is None or parsed_splash is None or
                parsed_penetration is None or
                not isinstance(is_he, bool) or
                (not is_he and parsed_splash != 0.0)):
            return None

        parsed_epoch = None
        if shooter_kind == 'player':
            if parsed_shooter_id != _exact_int(self.player_id):
                return None
        else:
            parsed_epoch = _projectile_int_range(
                authority_epoch, 0, MAX_PROJECTILE_ID)
            if (not self.is_bot_authority() or parsed_epoch is None or
                    parsed_epoch != _exact_int(self.authority_epoch)):
                return None

        with self._projectile_lock:
            previous = getattr(self, '_fire_seq', 0)
            if shooter_kind == 'player':
                expected = previous + 1
                if shot_seq is not None:
                    supplied = _projectile_int_range(
                        shot_seq, 1, MAX_PROJECTILE_ID)
                    if supplied != expected:
                        return None
                parsed_seq = expected
            else:
                parsed_seq = _projectile_int_range(
                    shot_seq, 1, MAX_PROJECTILE_ID)
                if parsed_seq is None:
                    return None

            message = {
                'type': 'projectile_launch',
                'round_id': self.round_id,
                'shooter_kind': shooter_kind,
                'shooter_id': parsed_shooter_id,
                'shot_seq': parsed_seq,
                'shell_index': parsed_shell,
                'origin': parsed_origin,
                'velocity': parsed_velocity,
                'gravity': parsed_gravity,
                'max_distance': parsed_distance,
                'max_time_ms': parsed_time,
                'is_he': is_he,
                'splash_radius': parsed_splash,
                'penetration_factor': parsed_penetration,
            }
            if parsed_epoch is not None:
                message['authority_epoch'] = parsed_epoch
            if not self._send(message):
                return None
            if shooter_kind == 'player':
                self._fire_seq = parsed_seq
            return parsed_seq

    def send_projectile_progress(self, authority_epoch, cursors):
        """CAS-advance at most thirty server-ledger projectile cursors."""
        if self.phase != 'battle' or not self.is_bot_authority():
            return False
        parsed_epoch = _projectile_int_range(
            authority_epoch, 0, MAX_PROJECTILE_ID)
        if parsed_epoch is None or parsed_epoch != _exact_int(
                self.authority_epoch):
            return False
        if (not isinstance(cursors, list) or not cursors or
                len(cursors) > MAX_PROJECTILE_BATCH):
            return False
        parsed_cursors = []
        seen = set()
        exact_keys = frozenset((
            'projectile_id', 'base_checked_ms', 'checked_through_ms',
            'checked_distance', 'piercing_loss', 'penetration_factor',
            'destructibles'))
        destructible_count = 0
        for cursor in cursors:
            if not isinstance(cursor, dict) or set(cursor) != exact_keys:
                return False
            projectile_id = _strict_projectile_id(
                cursor.get('projectile_id'))
            base_checked = _projectile_int_range(
                cursor.get('base_checked_ms'), 0, MAX_PROJECTILE_TIME_MS)
            checked_through = _projectile_int_range(
                cursor.get('checked_through_ms'), 0,
                MAX_PROJECTILE_TIME_MS)
            checked_distance = _projectile_float_range(
                cursor.get('checked_distance'), 0.0,
                MAX_PROJECTILE_DISTANCE)
            piercing_loss = _projectile_float_range(
                cursor.get('piercing_loss'), 0.0,
                MAX_PROJECTILE_PIERCING_LOSS)
            penetration_factor = _projectile_float_range(
                cursor.get('penetration_factor'), 0.0, 100.0)
            raw_destructibles = cursor.get('destructibles')
            if not isinstance(raw_destructibles, list):
                return False
            parsed_destructibles = []
            for raw in raw_destructibles:
                parsed = _strict_projectile_destructible(raw)
                if parsed is None:
                    return False
                parsed_destructibles.append(parsed)
            destructible_count += len(parsed_destructibles)
            if (projectile_id is None or projectile_id in seen or
                    base_checked is None or checked_through is None or
                    checked_through < base_checked or
                    checked_distance is None or piercing_loss is None or
                    penetration_factor is None or
                    destructible_count > MAX_PROJECTILE_DESTRUCTIBLES):
                return False
            seen.add(projectile_id)
            parsed_cursors.append({
                'projectile_id': projectile_id,
                'base_checked_ms': base_checked,
                'checked_through_ms': checked_through,
                'checked_distance': checked_distance,
                'piercing_loss': piercing_loss,
                'penetration_factor': penetration_factor,
                'destructibles': parsed_destructibles,
            })
        return self._send({
            'type': 'projectile_progress',
            'round_id': self.round_id,
            'authority_epoch': parsed_epoch,
            'cursors': parsed_cursors,
        })

    def send_projectile_resolve(
            self, authority_epoch, projectile_id, base_checked_ms, outcome,
            resolved_time_ms, impact, direct, splash, checked_distance=0.0,
            piercing_loss=0.0, penetration_factor=1.0,
            destructibles=None):
        """Resolve one server-ledger projectile with an atomic effect set."""
        if self.phase != 'battle' or not self.is_bot_authority():
            return False
        parsed_epoch = _projectile_int_range(
            authority_epoch, 0, MAX_PROJECTILE_ID)
        parsed_projectile_id = _strict_projectile_id(projectile_id)
        parsed_base = _projectile_int_range(
            base_checked_ms, 0, MAX_PROJECTILE_TIME_MS)
        parsed_time = _projectile_int_range(
            resolved_time_ms, 0, MAX_PROJECTILE_TIME_MS)
        parsed_impact = (_strict_world_position(impact)
                         if outcome == 'impact' else None)
        parsed_distance = _projectile_float_range(
            checked_distance, 0.0, MAX_PROJECTILE_DISTANCE)
        parsed_loss = _projectile_float_range(
            piercing_loss, 0.0, MAX_PROJECTILE_PIERCING_LOSS)
        parsed_factor = _projectile_float_range(
            penetration_factor, 0.0, 100.0)
        if destructibles is None:
            destructibles = []
        if (not isinstance(destructibles, list) or
                len(destructibles) > MAX_PROJECTILE_DESTRUCTIBLES):
            return False
        parsed_destructibles = []
        for raw in destructibles:
            parsed = _strict_projectile_destructible(raw)
            if parsed is None:
                return False
            parsed_destructibles.append(parsed)
        if (parsed_epoch is None or parsed_epoch != _exact_int(
                self.authority_epoch) or parsed_projectile_id is None or
                parsed_base is None or parsed_time is None or
                parsed_time < parsed_base or
                outcome not in ('impact', 'miss', 'expired') or
                (outcome == 'impact' and parsed_impact is None) or
                (outcome != 'impact' and impact is not None) or
                parsed_distance is None or
                parsed_loss is None or parsed_factor is None or
                not isinstance(splash, list) or
                len(splash) > MAX_PROJECTILE_BATCH):
            return False
        parsed_direct = None
        if direct is not None:
            parsed_direct = _strict_projectile_effect(direct)
            if parsed_direct is None:
                return False
        parsed_splash = []
        targets = set()
        if parsed_direct is not None:
            targets.add((parsed_direct['target_kind'],
                         parsed_direct['target_id']))
        for effect in splash:
            parsed = _strict_projectile_effect(effect)
            if parsed is None:
                return False
            target = (parsed['target_kind'], parsed['target_id'])
            if target in targets:
                return False
            targets.add(target)
            parsed_splash.append(parsed)
        if (outcome != 'impact' and
                (parsed_direct is not None or parsed_splash)):
            return False
        return self._send({
            'type': 'projectile_resolve',
            'round_id': self.round_id,
            'authority_epoch': parsed_epoch,
            'projectile_id': parsed_projectile_id,
            'base_checked_ms': parsed_base,
            'outcome': outcome,
            'resolved_time_ms': parsed_time,
            'checked_distance': parsed_distance,
            'piercing_loss': parsed_loss,
            'penetration_factor': parsed_factor,
            'impact': parsed_impact,
            'direct': parsed_direct,
            'splash': parsed_splash,
            'destructibles': parsed_destructibles,
        })

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

    def has_projectile_ledger(self):
        return PROJECTILE_LEDGER_CAPABILITY in self.capabilities

    def send_bot_manifest(self, bots):
        if not self.is_bot_authority():
            return False
        return self._send({'type': 'bot_manifest',
                           'round_id': self.round_id,
                           'bots': list(bots or ())[:30]})

    def send_bot_state(self, bots):
        if not self.is_bot_authority():
            return False
        projected = []
        for state in list(bots or ())[:30]:
            state = _project_bot_state(state)
            if state is None:
                return False
            projected.append(state)
        return self._send({'type': 'bot_state', 'round_id': self.round_id,
                           'bots': projected})

    def send_bot_observation(self, contacts, affordances=None):
        if not self.is_bot_authority():
            return False
        return self._send({'type': 'bot_observation',
                           'round_id': self.round_id,
                           'contacts': list(contacts or ())[:64],
                           'affordances': list(affordances or ())[:16]})

    def send_descriptor_catalog(self, vehicles):
        if not self.ready:
            return False
        return self._send({'type': 'descriptor_catalog',
                           'vehicles': list(vehicles or ())[:600]})

    def send_descriptor_bundle(self, projections, requested=None,
                               failures=None, complete=True):
        if not self.ready:
            return False
        projections = dict(projections or {})
        if requested is None:
            requested = sorted(projections)
        return self._send({'type': 'descriptor_bundle',
                           'round_id': self.round_id,
                           'requested': list(requested or ())[:64],
                           'failures': list(failures or ())[:64],
                           'complete': bool(complete),
                           'projections': projections})

    def send_destructible_map(self, map_name, payload):
        if not self.ready or not isinstance(payload, dict):
            return False
        message = dict(payload)
        message['type'] = 'destructible_map'
        message['round_id'] = self.round_id
        message['map'] = map_name
        return self._send(message)

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

    def _publish_connected_transport(self, sock, generation):
        """Atomically publish one hello-complete transport generation."""
        sender = threading.Thread(
            target=self._sender_worker, args=(sock, generation),
            name='offline-lan-0922-sender')
        sender.setDaemon(True)
        with self._outbound_lock:
            if (generation != self._transport_generation or
                    self._stopping or not self.running or
                    self.sock is not sock):
                return False
            self.connected = True
            self._outbound_accepting = True
            self._sender_thread = sender
            # Starting under the lifecycle lock closes the window where stop
            # could join an assigned-but-not-yet-started sender.
            try:
                sender.start()
            except Exception:
                self.connected = False
                self._outbound_accepting = False
                self._sender_thread = None
                raise
        return True

    def _worker(self, generation=None):
        if generation is None:
            generation = self._transport_generation
        sock = None
        recv_buffer = u''
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect((self.host, self.port))
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            sock.settimeout(0.5)
            with self._outbound_lock:
                if (generation != self._transport_generation or
                        self._stopping or not self.running):
                    return
                self.sock = sock
            # The server requires hello to be the first wire message.  Do not
            # expose the socket to the BigWorld poller until it is on the
            # wire, or the first main-thread ping can win this race.
            hello = {
                'type': 'hello',
                'protocol': PROTOCOL_VERSION,
                'client_build': CLIENT_BUILD,
                'capabilities': list(CLIENT_CAPABILITIES),
                'name': self.name,
                'vehicle': self.vehicle,
                'max_health': self.max_health,
            }
            payload = (json.dumps(
                hello, separators=(',', ':')) + '\n').encode('utf-8')
            with self._send_lock:
                with self._outbound_lock:
                    if (generation != self._transport_generation or
                            self._stopping or not self.running or
                            self.sock is not sock):
                        return
                sock.sendall(payload)
            if not self._publish_connected_transport(sock, generation):
                return
            while (self.running and
                   generation == self._transport_generation):
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    continue
                if generation != self._transport_generation:
                    break
                if not chunk:
                    self._record_transport_error(
                        'server closed the connection', generation, sock)
                    break
                received_time = _monotonic_time()
                try:
                    recv_buffer += chunk.decode('utf-8')
                except UnicodeError:
                    self._record_transport_error(
                        'server sent invalid UTF-8', generation, sock)
                    break
                if len(recv_buffer) > MAX_BUFFER_BYTES:
                    self._record_transport_error(
                        'server message buffer exceeded limit',
                        generation, sock)
                    break
                while u'\n' in recv_buffer:
                    line, recv_buffer = recv_buffer.split(u'\n', 1)
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
                    if generation != self._transport_generation:
                        break
                    self._queue_message(message, generation)
        except Exception as error:
            self._record_transport_error(error, generation)
        finally:
            wake_sender = False
            with self._outbound_lock:
                if (generation == self._transport_generation and
                        self.sock is sock):
                    self.connected = False
                    self.running = False
                    self._outbound_accepting = False
                    self._outbound_queue = []
                    self._outbound_bytes = 0
                    self.sock = None
                    wake_sender = True
            if wake_sender:
                self._outbound_event.set()
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass

    def _queue_message(self, message, generation=None):
        if not isinstance(message, dict):
            return
        with self._pending_lock:
            if (generation is not None and
                    (generation != self._transport_generation or
                     self._stopping or not self.running)):
                return
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

    def _record_transport_error(self, error, generation, sock=None):
        """Record an error only while its transport generation still owns it."""
        with self._outbound_lock:
            if (generation != self._transport_generation or
                    self._stopping or
                    (sock is not None and self.sock is not sock)):
                return False
            self.last_error = str(error)
        return True

    def _abort_outbound(self, error, generation):
        with self._outbound_lock:
            if generation != self._transport_generation:
                return False
            if error:
                self.last_error = str(error)
            sock = self.sock
            self.running = False
            self.connected = False
            self._stopping = True
            self._outbound_accepting = False
            self._outbound_queue = []
            self._outbound_bytes = 0
            self.sock = None
        self._outbound_event.set()
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
        return True

    def _dequeue_outbound(self, generation):
        with self._outbound_lock:
            if (generation != self._transport_generation or
                    not self._outbound_queue):
                return None
            item = self._outbound_queue.pop(0)
            self._outbound_bytes = max(
                0, self._outbound_bytes - item[2])
            return item

    def _send_wire(self, message, sock, generation):
        """Encode and write one queued message from the sender thread."""
        try:
            payload = (json.dumps(
                message, separators=(',', ':')) + '\n').encode('utf-8')
            if len(payload) > MAX_MESSAGE_BYTES:
                if self._record_transport_error(
                        'client message exceeded wire limit',
                        generation, sock):
                    return False
                return None
            with self._send_lock:
                with self._outbound_lock:
                    if (generation != self._transport_generation or
                            self._stopping or not self.running or
                            not self.connected or self.sock is not sock):
                        return None
                sock.sendall(payload)
            return True
        except Exception as error:
            if self._record_transport_error(error, generation, sock):
                return False
            return None

    def _sender_worker(self, sock, generation):
        try:
            while (self.running and self.connected and
                   generation == self._transport_generation):
                item = self._dequeue_outbound(generation)
                if item is None:
                    self._outbound_event.clear()
                    with self._outbound_lock:
                        pending = bool(
                            generation == self._transport_generation and
                            self._outbound_queue)
                    if pending:
                        self._outbound_event.set()
                        continue
                    self._outbound_event.wait(0.1)
                    continue
                send_result = self._send_wire(item[1], sock, generation)
                if send_result is not True:
                    if send_result is False:
                        self._abort_outbound(
                            self.last_error or 'LAN sender stopped',
                            generation)
                    break
        finally:
            with self._outbound_lock:
                if (generation == self._transport_generation and
                        self._sender_thread is threading.current_thread()):
                    self._sender_thread = None

    def _send(self, message):
        """Freeze and enqueue one reliable message without wire I/O."""
        with self._outbound_lock:
            if (not self.connected or self.sock is None or
                    self._stopping or not self.running or
                    not self._outbound_accepting):
                return False
            generation = self._transport_generation
        try:
            frozen, estimated_size = _freeze_outbound(message, [0])
        except Exception:
            return False
        estimated_size += 1
        if estimated_size > MAX_MESSAGE_BYTES:
            return False
        overflow = False
        with self._outbound_lock:
            if (generation != self._transport_generation or
                    not self._outbound_accepting or self._stopping or
                    not self.running or not self.connected):
                return False
            if (len(self._outbound_queue) >= MAX_OUTBOUND_MESSAGES or
                    self._outbound_bytes + estimated_size >
                    MAX_OUTBOUND_BYTES):
                overflow = True
            else:
                self._outbound_seq += 1
                self._outbound_queue.append((
                    self._outbound_seq, frozen, estimated_size))
                self._outbound_bytes += estimated_size
        if overflow:
            self._abort_outbound('LAN outbound queue exceeded limit',
                                 generation)
            return False
        self._outbound_event.set()
        return True

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
        message_round = _exact_int(message.get('round_id'))
        if (message_round is not None and message_round == self.round_id and
                'server_time_ms' in message):
            server_time_ms = _projectile_int_range(
                message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            if (server_time_ms is None or
                    (self.server_time_ms is not None and
                     server_time_ms < self.server_time_ms)):
                self.last_error = 'invalid server time'
                self.stop()
                return
            self.server_time_ms = server_time_ms
        if kind == 'welcome':
            if _safe_text(message.get('client_build'), '') != CLIENT_BUILD:
                self.last_error = 'client build mismatch'
                self.stop()
                return
            capabilities = _strict_capabilities(message.get('capabilities'))
            if (capabilities is None or
                    PROJECTILE_LEDGER_CAPABILITY not in capabilities):
                self.last_error = 'projectile ledger capability mismatch'
                self.stop()
                return
            player_id = _exact_int(message.get('player_id'))
            team = _exact_int(message.get('team'))
            round_id = _exact_int(message.get('round_id'))
            state_revision = _exact_int(message.get('state_revision'))
            host_player_id = _exact_int(message.get('host_player_id'))
            slot = _exact_int(message.get('slot'))
            max_health = _exact_int(message.get('max_health'))
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            welcome_server_time = None
            if 'server_time_ms' in message:
                welcome_server_time = _projectile_int_range(
                    message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            phase = _safe_text(message.get('phase'), '')
            map_name = _safe_text(message.get('map'), '')
            spawn = message.get('spawn')
            if (player_id is None or state_revision is None or
                    state_revision < 0 or host_player_id is None or
                    host_player_id <= 0 or team not in (1, 2) or
                    round_id is None or slot is None or not 0 <= slot < 15 or
                    max_health is None or max_health <= 0 or
                    authority_epoch is None or
                    ('server_time_ms' in message and
                     welcome_server_time is None) or
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
            self.authority_epoch = authority_epoch
            self.capabilities = capabilities
            self.server_time_ms = welcome_server_time
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
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            roster_server_time = None
            if 'server_time_ms' in message:
                roster_server_time = _projectile_int_range(
                    message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            player_ids = set(_exact_int(value.get('id'))
                             for value in players or ())
            ledger_required = self.has_projectile_ledger()
            if (phase not in ('waiting', 'loading', 'battle') or not map_name or
                    players is None or host_player_id not in player_ids or
                    (ledger_required and authority_epoch is None) or
                    (ledger_required and round_id == self.round_id and
                     self.authority_epoch is not None and
                     authority_epoch < self.authority_epoch) or
                    (ledger_required and 'server_time_ms' in message and
                     roster_server_time is None)):
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
                self.server_time_ms = None
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
            if authority_epoch is not None:
                self.authority_epoch = authority_epoch
            if roster_server_time is not None:
                self.server_time_ms = roster_server_time
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
                if self.authority_epoch is not None:
                    message['authority_epoch'] = self.authority_epoch
                if self.server_time_ms is not None:
                    message['server_time_ms'] = self.server_time_ms
            map_name = _safe_text(message.get('map'), '')
            phase = _safe_text(message.get('phase'), '')
            players = _strict_mapping_list(message.get('players'), 64)
            local_ids = set(_exact_int(value.get('id')) for value in players or ())
            host_player_id = _exact_int(message.get('host_player_id'))
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            start_server_time = None
            if 'server_time_ms' in message:
                start_server_time = _projectile_int_range(
                    message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            ledger_required = self.has_projectile_ledger()
            if (phase != 'loading' or
                    not map_name or not players or
                    self.player_id not in local_ids or
                    host_player_id not in local_ids or
                    (ledger_required and authority_epoch is None) or
                    (ledger_required and round_id == self.round_id and
                     self.authority_epoch is not None and
                     authority_epoch < self.authority_epoch) or
                    (ledger_required and 'server_time_ms' in message and
                     start_server_time is None)):
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
                self.server_time_ms = None
            self.phase = phase
            self.map_name = map_name
            self.round_id = round_id
            self.state_revision = state_revision
            self.roster = players
            self.host_player_id = host_player_id
            self._battle_start_round_id = round_id
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
            if authority_epoch is not None:
                self.authority_epoch = authority_epoch
            if start_server_time is not None:
                self.server_time_ms = start_server_time
        elif kind == 'battle_live':
            round_id = _exact_int(message.get('round_id'))
            state_revision = _exact_int(message.get('state_revision'))
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            countdown = _finite_float(
                message.get('countdown_seconds'), -1.0)
            duration = _finite_float(
                message.get('battle_duration_seconds'), -1.0)
            if (round_id is None or round_id != self.round_id or
                    self.phase not in ('loading', 'battle') or
                    state_revision is None or state_revision < 0 or
                    (self.has_projectile_ledger() and
                     authority_epoch is None) or
                    (self.has_projectile_ledger() and
                     self.authority_epoch is not None and
                     authority_epoch < self.authority_epoch) or
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
            if authority_epoch is not None:
                self.authority_epoch = authority_epoch
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
            server_time_ms = _projectile_int_range(
                message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            projectile_revision = _projectile_int_range(
                message.get('projectile_revision'), 0, MAX_PROJECTILE_ID)
            bot_state_revision = _projectile_int_range(
                message.get('bot_state_revision'), 0, MAX_PROJECTILE_ID)
            projectiles = message.get('projectiles')
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
            ledger_required = self.has_projectile_ledger()
            previous_bot_state_revision = None
            if (isinstance(self.last_snapshot, dict) and
                    _exact_int(self.last_snapshot.get('round_id')) ==
                    round_id):
                previous_bot_state_revision = _projectile_int_range(
                    self.last_snapshot.get('bot_state_revision'),
                    0, MAX_PROJECTILE_ID)
            valid_projectiles = (not ledger_required or (
                server_time_ms is not None and authority_epoch is not None and
                projectile_revision is not None and
                (self.authority_epoch is None or
                 authority_epoch >= self.authority_epoch) and
                _valid_active_projectiles(
                    projectiles, authority_epoch, server_time_ms)))
            if (server_tick is None or server_tick < 0 or
                    bot_state_revision is None or
                    (previous_bot_state_revision is not None and
                     bot_state_revision < previous_bot_state_revision) or
                    not valid_projectiles or
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
            if server_time_ms is not None:
                self.server_time_ms = server_time_ms
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
            if authority_epoch is not None:
                self.authority_epoch = authority_epoch
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
            ledger_required = self.has_projectile_ledger()
            events_server_time = _projectile_int_range(
                message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            events_authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            if (server_tick is None or server_tick < 0 or events is None or
                    (ledger_required and events_server_time is None) or
                    (ledger_required and events_authority_epoch is None) or
                    (ledger_required and self.authority_epoch is not None and
                     events_authority_epoch < self.authority_epoch)):
                self.last_error = 'invalid events message'
                self.stop()
                return
            for event in events:
                if event.get('kind') not in ('authority', 'bot_authority'):
                    continue
                event_authority_epoch = _projectile_int_range(
                    event.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
                authority_id = event.get('player_id')
                if authority_id is not None:
                    authority_id = _projectile_int_range(
                        authority_id, 1, MAX_PROJECTILE_ID)
                if (event_authority_epoch is None or
                        (self.authority_epoch is not None and
                         event_authority_epoch < self.authority_epoch) or
                        (ledger_required and
                         event_authority_epoch > events_authority_epoch)):
                    self.last_error = 'invalid bot authority event'
                    self.stop()
                    return
                self.bot_authority_id = authority_id
                self.authority_epoch = event_authority_epoch
            if events_authority_epoch is not None:
                self.authority_epoch = events_authority_epoch
        elif kind == 'bot_observation':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None:
                self.last_error = 'invalid bot observation message'
                self.stop()
                return
            if round_id != self.round_id:
                return
            contacts = _strict_mapping_list(message.get('contacts'), 64)
            valid_contacts = contacts is not None and all(
                _exact_int(contact.get('observing_team')) in (1, 2) and
                _exact_int(contact.get('target_team')) in (1, 2) and
                _exact_int(contact.get('observing_team')) !=
                _exact_int(contact.get('target_team')) and
                _exact_int(contact.get('target_id')) is not None and
                _exact_int(contact.get('target_id')) > 0 and
                _safe_text(contact.get('target_kind'), '') in
                ('human', 'bot') and
                isinstance(contact.get('visible'), bool)
                for contact in (contacts or ()))
            if self.phase != 'battle' or not valid_contacts:
                self.last_error = 'invalid bot observation message'
                self.stop()
                return
            message = dict(message)
            message['contacts'] = contacts
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
