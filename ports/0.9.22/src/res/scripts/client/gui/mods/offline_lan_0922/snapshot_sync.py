from __future__ import print_function

"""Engine-free translation of LAN v5 snapshots into entity lifecycle data."""

import math


PREDICTION_SECONDS = 0.05
SNAP_DISTANCE = 25.0
MAX_VELOCITY = 80.0


def _number(value, default=0.0):
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return float(default)
        return value
    except (TypeError, ValueError):
        return float(default)


def _angle_delta(current, target):
    """Return the shortest signed delta between two yaw angles."""
    return (target - current + math.pi) % (2.0 * math.pi) - math.pi


def _pose(state):
    if not isinstance(state, dict):
        return None
    if not all(key in state for key in ('x', 'y', 'z')):
        return None
    return {
        'x': _number(state['x']), 'y': _number(state['y']),
        'z': _number(state['z']), 'yaw': _number(state.get('yaw')),
        'aim_yaw': _number(state.get('aim_yaw', state.get('yaw'))),
        'gun_pitch': _number(state.get('gun_pitch')),
    }


def _entity_key(kind, state):
    if not isinstance(state, dict) or state.get('id') is None:
        return None
    return '%s:%s' % (kind, state['id'])


def _copy_state(state):
    return dict(state) if isinstance(state, dict) else {}


class SnapshotSync(object):
    """Keeps protocol ordering and remote smoothing outside BigWorld.

    ``on_event`` receives plain dictionaries and can be wired to a later entity
    binding.  No import in this module has an engine side effect.
    """

    def __init__(self, local_player_id=None, on_event=None, clock=None):
        self.local_player_id = local_player_id
        self.on_event = on_event
        self._clock = clock
        self.round_id = None
        self._last_sequence = None
        self._last_order_revision = None
        self._entities = {}
        self._last_advance = None

    def _now(self):
        if self._clock is not None:
            return float(self._clock())
        import time
        return time.time()

    def _emit(self, event, output):
        output.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _reset_round(self, round_id):
        self.round_id = round_id
        self._last_sequence = None
        self._last_order_revision = None
        self._entities = {}
        self._last_advance = None

    def manifest(self, message):
        """Consume a battle_start or roster message and emit missing creates."""
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id')
        if round_id is not None and round_id != self.round_id:
            self._reset_round(round_id)
        output = []
        for kind, field in (('player', 'players'), ('bot', 'bots')):
            states = message.get(field) or []
            for state in states:
                key = _entity_key(kind, state)
                if key is None:
                    continue
                record = self._entities.get(key)
                if record is None:
                    record = {'kind': kind, 'id': state['id'], 'dead': False,
                              'current': None, 'target': None, 'velocity': (0.0, 0.0, 0.0),
                              'target_time': None}
                    self._entities[key] = record
                    self._emit({'type': 'create', 'entity': key, 'kind': kind,
                                'id': state['id'], 'state': _copy_state(state)}, output)
                else:
                    record['manifest'] = _copy_state(state)
        return output

    def _sequence(self, message):
        return message.get('sequence', message.get('server_tick'))

    def _set_remote_target(self, record, pose, now):
        previous = record['target']
        previous_time = record['target_time']
        velocity = (0.0, 0.0, 0.0)
        if previous is not None and previous_time is not None and now > previous_time:
            delta = max(0.01, min(now - previous_time, 0.25))
            velocity = ((pose['x'] - previous['x']) / delta,
                        (pose['y'] - previous['y']) / delta,
                        (pose['z'] - previous['z']) / delta)
            speed = math.sqrt(sum(part * part for part in velocity))
            if speed > MAX_VELOCITY:
                scale = MAX_VELOCITY / speed
                velocity = tuple(part * scale for part in velocity)
        record['target'] = pose
        record['velocity'] = velocity
        record['target_time'] = now
        if record['current'] is None:
            record['current'] = dict(pose)
            return True
        return False

    def _upsert(self, kind, state, now, output):
        key = _entity_key(kind, state)
        if key is None:
            return
        record = self._entities.get(key)
        if record is None:
            record = {'kind': kind, 'id': state['id'], 'dead': False,
                      'current': None, 'target': None,
                      'velocity': (0.0, 0.0, 0.0), 'target_time': None}
            self._entities[key] = record
            self._emit({'type': 'create', 'entity': key, 'kind': kind,
                        'id': state['id'], 'state': _copy_state(state)}, output)
        alive = bool(state.get('alive', True))
        pose = _pose(state)
        local = kind == 'player' and state.get('id') == self.local_player_id
        if not alive:
            if not record['dead']:
                record['dead'] = True
                if pose is not None:
                    record['current'] = dict(pose)
                self._emit({'type': 'destroy', 'entity': key, 'kind': kind,
                            'id': state['id'], 'reason': 'dead',
                            'keep_corpse': True, 'state': _copy_state(state)}, output)
            return
        if record['dead']:
            return
        if local:
            if pose is not None:
                record['current'] = dict(pose)
            self._emit({'type': 'update', 'entity': key, 'kind': kind,
                        'id': state['id'], 'state': _copy_state(state),
                        'pose': pose, 'correction': True}, output)
            return
        snapped = self._set_remote_target(record, pose, now) if pose is not None else False
        self._emit({'type': 'update', 'entity': key, 'kind': kind,
                    'id': state['id'], 'state': _copy_state(state),
                    'pose': dict(record['current']) if record['current'] else None,
                    'target': pose, 'remote': True, 'snap': snapped}, output)

    def snapshot(self, message):
        """Translate one full snapshot, dropping stale rounds/sequences."""
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id', self.round_id)
        if self.round_id is not None and round_id != self.round_id:
            return []
        sequence = self._sequence(message)
        if sequence is not None and self._last_sequence is not None and sequence <= self._last_sequence:
            return []
        if sequence is not None:
            self._last_sequence = sequence
        now = self._now()
        output = []
        seen = set()
        for kind, field in (('player', 'players'), ('bot', 'bots')):
            for state in message.get(field) or []:
                key = _entity_key(kind, state)
                if key is not None:
                    seen.add(key)
                self._upsert(kind, state, now, output)
        for key, record in list(self._entities.items()):
            if key not in seen and not record['dead']:
                record['dead'] = True
                self._emit({'type': 'destroy', 'entity': key,
                            'kind': record['kind'], 'id': record['id'],
                            'reason': 'missing', 'keep_corpse': False}, output)
        revision = message.get('bot_order_revision')
        orders = message.get('bot_orders')
        if orders is not None and (self._last_order_revision is None or
                                   revision is None or revision > self._last_order_revision):
            self._last_order_revision = revision
            for order in orders:
                if isinstance(order, dict):
                    self._emit({'type': 'order', 'order': _copy_state(order),
                                'revision': revision}, output)
        return output

    def advance(self, now=None):
        """Return interpolated/predicted remote poses for one render frame."""
        now = self._now() if now is None else float(now)
        delta = 0.016 if self._last_advance is None else max(0.001, min(now - self._last_advance, 0.1))
        self._last_advance = now
        alpha = 1.0 - math.exp(-20.0 * delta)
        output = []
        for key, record in self._entities.items():
            if record['dead'] or record['target'] is None or record['current'] is None:
                continue
            target = record['target']
            predict = max(0.0, min(now - record['target_time'], PREDICTION_SECONDS))
            velocity = record['velocity']
            desired = dict(target)
            desired['x'] += velocity[0] * predict
            desired['y'] += velocity[1] * predict
            desired['z'] += velocity[2] * predict
            current = record['current']
            dx, dy, dz = (desired['x'] - current['x'], desired['y'] - current['y'], desired['z'] - current['z'])
            if dx * dx + dy * dy + dz * dz > SNAP_DISTANCE * SNAP_DISTANCE:
                current = desired
                snapped = True
            else:
                current = dict(current)
                for axis in ('x', 'y', 'z'):
                    current[axis] += (desired[axis] - current[axis]) * alpha
                for axis in ('yaw', 'aim_yaw'):
                    current[axis] += _angle_delta(
                        current[axis], desired[axis]) * alpha
                current['gun_pitch'] += (
                    desired['gun_pitch'] - current['gun_pitch']) * alpha
                snapped = False
            record['current'] = current
            self._emit({'type': 'update', 'entity': key, 'kind': record['kind'],
                        'id': record['id'], 'pose': dict(current), 'remote': True,
                        'interpolated': True, 'snap': snapped}, output)
        return output
