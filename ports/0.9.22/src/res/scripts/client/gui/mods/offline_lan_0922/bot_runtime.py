from __future__ import print_function

"""Authority-side, engine-free bridge from v5 bots to the local AI package."""

import math

from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai import maps as tactical_maps


TICK_SECONDS = 1.0 / 30.0
OBSERVATION_SECONDS = 0.20
HUMAN_TARGET_ID_BASE = 1000000
VISIBILITY_MIN_SECONDS = 0.18
VISIBILITY_JITTER_SECONDS = 0.018


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _position(state):
    return (_number(state.get('x')), _number(state.get('y')),
            _number(state.get('z')))


def _value(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _reload_seconds(descriptor):
    gun = _value(descriptor, 'gun', {}) or {}
    # A minimum delay also protects incomplete/fake descriptors from turning
    # one 30 Hz state tick into one shell.
    return max(0.45, _number(_value(gun, 'reloadTime', 2.5), 2.5))


def _forward_speed(descriptor):
    physics = _value(descriptor, 'physics', {}) or {}
    limits = _value(physics, 'speedLimits', (14.0, 7.0))
    try:
        value = abs(float(limits[0]))
    except (TypeError, ValueError, IndexError):
        value = 14.0
    return max(4.0, min(value, 35.0))


def _distance(first, second):
    dx = _number(first[0]) - _number(second[0])
    dz = _number(first[2]) - _number(second[2])
    return math.sqrt(dx * dx + dz * dz)


def _angle_delta(target, current):
    value = _number(target) - _number(current)
    while value > math.pi:
        value -= math.pi * 2.0
    while value < -math.pi:
        value += math.pi * 2.0
    return value


class BotRuntime(object):
    """Produces v5 ``bot_manifest`` and ``bot_state`` payloads without entities."""

    def __init__(self, local_player_id, descriptor_resolver=None,
                 direction_probe=None, adapter_factory=None,
                 vehicle_selector=None, visibility_probe=None):
        self.local_player_id = local_player_id
        self.descriptor_resolver = descriptor_resolver or (lambda unused: {})
        self.direction_probe = direction_probe or (lambda *unused: True)
        self.adapter_factory = adapter_factory or BotAdapter
        self.vehicle_selector = vehicle_selector or (
            lambda raw: raw.get('vehicle') or 'ussr:R11_MS-1')
        self.visibility_probe = visibility_probe or (
            lambda unused_source, unused_target: True)
        self.adapter = None
        self.authority_id = None
        self.round_id = None
        self.states = {}
        self._accumulator = 0.0
        self._manifest_sent = False
        self._reload_times = {}
        self._next_fire = {}
        self.finished = False
        self._visibility_cache = {}
        self._server_orders = {}
        self._order_revision = -1
        self._next_observation = 0.0

    def is_authority(self):
        return self.authority_id == self.local_player_id

    def _clear(self, position, yaw):
        """Treat collision, excessive slope and water as a failed local ray."""
        try:
            result = self.direction_probe(position, yaw)
        except Exception:
            return False
        if isinstance(result, dict):
            if not result.get('clear', True) or result.get('collision', False):
                return False
            if result.get('water', False):
                return False
            return abs(_number(result.get('slope', 0.0))) <= 0.55
        return bool(result)

    def battle_start(self, message):
        """Build a local authority manifest from the server roster once per round."""
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id')
        if round_id != self.round_id:
            self.round_id = round_id
            self.states = {}
            self._accumulator = 0.0
            self._manifest_sent = False
            self._reload_times = {}
            self._next_fire = {}
            self.adapter = None
            self.finished = False
            self._visibility_cache = {}
            self._server_orders = {}
            self._order_revision = -1
            self._next_observation = 0.0
        self._apply_orders(message)
        if message.get('battle_result') is not None:
            self.finished = True
        previous_authority = self.authority_id
        self.authority_id = message.get('bot_authority_id')
        if previous_authority != self.authority_id:
            self._visibility_cache = {}
            if self.is_authority():
                self._manifest_sent = False
        if not self.is_authority():
            return []
        if self.finished:
            return []
        if self.adapter is None:
            self.adapter = self.adapter_factory(message.get('map', ''),
                                                round_id or 0)
        manifest = message.get('bot_manifest') or message.get('bots') or []
        for raw in manifest:
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            bot_id = int(raw['id'])
            vehicle_name = self.vehicle_selector(raw)
            descriptor = self.descriptor_resolver(vehicle_name)
            self._reload_times[bot_id] = _reload_seconds(descriptor)
            self._next_fire[bot_id] = 0.0
            if isinstance(raw.get('profile'), dict):
                self.adapter.director.register_profile(bot_id, raw.get('team', 1),
                                                       raw['profile'], raw.get('name', 'Bot'))
            else:
                self.adapter.register(bot_id, raw.get('team', 1), descriptor,
                                      raw.get('name', 'Bot'))
            spawn, spawn_yaw = self._spawn(
                int(raw.get('team', 1)), int(raw.get('slot', 0)))
            agents = getattr(self.adapter.director, 'agents', {})
            agent = agents.get(bot_id, {})
            profile = agent.get('profile', {})
            route = agent.get('route') or {}
            max_health = max(1, int(getattr(descriptor, 'maxHealth',
                                            raw.get('max_health', 1000))))
            health = max(0, min(
                int(_number(raw.get('health'), max_health)), max_health))
            self.states.setdefault(bot_id, {
                'id': bot_id, 'team': int(raw.get('team', 1)),
                'slot': int(raw.get('slot', 0)), 'name': raw.get('name', 'Bot'),
                'vehicle': vehicle_name,
                'x': _number(raw.get('x'), spawn[0]),
                'y': _number(raw.get('y'), spawn[1]),
                'z': _number(raw.get('z'), spawn[2]),
                'yaw': _number(raw.get('yaw'), spawn_yaw),
                'aim_yaw': _number(raw.get('yaw'), spawn_yaw),
                'gun_pitch': 0.0,
                'health': health, 'max_health': max_health,
                'alive': bool(raw.get('alive', health > 0)) and health > 0,
                'fire_seq': max(0, int(_number(raw.get('fire_seq'), 0))),
                'shell_index': max(0, min(
                    int(_number(raw.get('shell_index'), 0)), 9)),
                'move_speed': _forward_speed(descriptor),
                'profile': profile, 'route': route,
            })
        if self._manifest_sent:
            return []
        self._manifest_sent = True
        return [{'type': 'bot_manifest', 'bots': [self._manifest_entry(state)
                                                   for state in self.states.values()]}]

    def apply_snapshot(self, message):
        """Apply only server-owned combat state to the authority simulation.

        Bot poses remain locally simulated to avoid feeding a delayed echo back
        into steering.  Health and alive state are server authoritative because
        hits may be reported by other clients or by the authority's collision
        resolver after the local AI tick that fired the shot.
        """
        message = message if isinstance(message, dict) else {}
        self._apply_orders(message)
        if message.get('battle_result') is not None:
            self.finished = True
        for raw in message.get('bots') or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                state = self.states.get(int(raw['id']))
            except (TypeError, ValueError):
                continue
            if state is None:
                continue
            health = max(0, min(
                int(_number(raw.get('health'), state['health'])),
                int(state['max_health'])))
            state['health'] = health
            state['alive'] = bool(raw.get('alive', health > 0)) and health > 0
            state['fire_seq'] = max(
                int(state.get('fire_seq', 0)),
                max(0, int(_number(raw.get('fire_seq'), 0))))
            state['shell_index'] = max(0, min(
                int(_number(raw.get('shell_index'),
                            state.get('shell_index', 0))), 9))
            if not state['alive']:
                state['speed'] = 0.0
                state['target_kind'] = None
                state['target_id'] = None

    def _apply_orders(self, message):
        if not isinstance(message, dict) or 'bot_orders' not in message:
            return False
        orders = message.get('bot_orders')
        if not isinstance(orders, (list, tuple)):
            return False
        try:
            revision = int(message.get('bot_order_revision'))
        except (TypeError, ValueError):
            return False
        if revision < self._order_revision:
            return False
        accepted = {}
        for raw in orders[:30]:
            if not isinstance(raw, dict) or raw.get('id') is None:
                return False
            try:
                bot_id = int(raw.get('id'))
            except (TypeError, ValueError):
                return False
            if bot_id in accepted:
                return False
            accepted[bot_id] = dict(raw)
        self._server_orders = accepted
        self._order_revision = revision
        return True

    def _manifest_entry(self, state):
        keys = ('id', 'team', 'slot', 'name', 'vehicle', 'health',
                'max_health', 'x', 'y', 'z', 'yaw', 'profile')
        result = dict((key, state[key]) for key in keys)
        route = state.get('route') or {}
        result['route'] = {
            'id': route.get('id', 'map_route'),
            'waypoints': [
                {'x': point[0], 'y': 0.0, 'z': point[1],
                 'hold': bool(point[2]) if len(point) > 2 else False}
                for point in route.get('waypoints', ())],
        }
        return result

    def _spawn(self, team, slot):
        map_name = getattr(self.adapter.director, 'map_name', '')
        data = tactical_maps.get_tactical_map(map_name) or {}
        bases = data.get('bases', {})
        base = bases.get(team)
        enemy = bases.get(2 if team == 1 else 1)
        if base is None:
            base = (0.0, -35.0 if team == 1 else 35.0)
        if enemy is None:
            enemy = (base[0], base[1] + (70.0 if team == 1 else -70.0))
        dx, dz = float(enemy[0]) - float(base[0]), float(enemy[1]) - float(base[1])
        length = max(1.0, math.sqrt(dx * dx + dz * dz))
        fx, fz = dx / length, dz / length
        sx, sz = fz, -fx
        column, row = int(slot) % 5 - 2, int(slot) // 5
        return ((float(base[0]) + sx * column * 5.5 - fx * row * 7.0,
                 0.0,
                 float(base[1]) + sz * column * 5.5 - fz * row * 7.0),
                math.atan2(fx, fz))

    def _visible(self, source, target, now):
        target_id = target.get('network_id', target.get('id', 0))
        key = (int(source.get('id', 0)), target.get('kind'), int(target_id))
        cached = self._visibility_cache.get(key)
        ttl = (VISIBILITY_MIN_SECONDS +
               ((key[0] * 31 + key[2] * 17) % 11) *
               VISIBILITY_JITTER_SECONDS)
        if cached is not None and _number(now) - cached[0] < ttl:
            return cached[1]
        try:
            value = bool(self.visibility_probe(source, target))
        except Exception:
            value = False
        self._visibility_cache[key] = (_number(now), value)
        if len(self._visibility_cache) > 1024:
            oldest = sorted(self._visibility_cache.items(),
                            key=lambda item: item[1][0])[:256]
            for old_key, unused_value in oldest:
                self._visibility_cache.pop(old_key, None)
        return value

    @staticmethod
    def _human_planner_id(player_id):
        return HUMAN_TARGET_ID_BASE + int(player_id)

    def _contacts_for(self, source, players, now):
        contacts = []
        lookup = {}
        source_team = int(source.get('team', 0))
        for raw in players or ():
            if (not isinstance(raw, dict) or raw.get('id') is None or
                    not raw.get('alive', True) or
                    int(raw.get('team', 0)) == source_team):
                continue
            target = dict(raw)
            target['kind'] = 'human'
            target['network_id'] = int(raw['id'])
            planner_id = self._human_planner_id(raw['id'])
            target['id'] = planner_id
            target['position'] = _position(raw)
            target['visible'] = self._visible(source, target, now)
            lookup[planner_id] = target
            contacts.append(target)
        for bot_id, raw in self.states.items():
            if (bot_id == source.get('id') or not raw.get('alive', True) or
                    int(raw.get('team', 0)) == source_team):
                continue
            target = dict(raw)
            target['kind'] = 'bot'
            target['network_id'] = int(bot_id)
            target['position'] = _position(raw)
            target['visible'] = self._visible(source, target, now)
            lookup[int(bot_id)] = target
            contacts.append(target)
        return contacts, lookup

    def _neighbours_for(self, source, supplied):
        result = list(supplied or ())
        for bot_id, raw in self.states.items():
            if bot_id == source.get('id') or not raw.get('alive', True):
                continue
            result.append({
                'id': bot_id, 'position': _position(raw),
                'yaw': _number(raw.get('yaw')),
                'velocity': (
                    math.sin(_number(raw.get('yaw'))) * _number(raw.get('speed')),
                    0.0,
                    math.cos(_number(raw.get('yaw'))) * _number(raw.get('speed'))),
            })
        return result

    @staticmethod
    def _player_neighbours(players):
        result = []
        for raw in players or ():
            if (not isinstance(raw, dict) or raw.get('id') is None or
                    not raw.get('alive', True)):
                continue
            yaw = _number(raw.get('yaw'))
            speed = _number(raw.get('speed'))
            result.append({
                'id': HUMAN_TARGET_ID_BASE + int(raw['id']),
                'position': _position(raw), 'yaw': yaw,
                'velocity': (math.sin(yaw) * speed, 0.0,
                             math.cos(yaw) * speed),
            })
        return result

    def update(self, dt, now, players=None, neighbours=None):
        """Advance bots locally and publish state plus periodic observations."""
        if (not self.is_authority() or self.adapter is None or
                self.finished):
            return []
        self._accumulator += max(0.0, _number(dt))
        if self._accumulator < TICK_SECONDS:
            return []
        step = min(self._accumulator, 0.2)
        self._accumulator = 0.0
        players = list(players or [])
        neighbours = list(neighbours or []) + self._player_neighbours(players)
        observations = {}
        for state in self.states.values():
            if not state['alive']:
                continue
            position = _position(state)
            contacts, targets = self._contacts_for(state, players, now)
            for target in contacts:
                key = (int(state.get('team', 0)), target.get('kind'),
                       int(target.get('network_id', 0)))
                previous = observations.get(key)
                profile = target.get('profile')
                profile = profile if isinstance(profile, dict) else {}
                observations[key] = {
                    'observing_team': key[0], 'target_kind': key[1],
                    'target_id': key[2],
                    'target_team': int(target.get('team', 0)),
                    'visible': bool(target.get('visible')) or bool(
                        previous and previous.get('visible')),
                    'x': _number(target.get('x')),
                    'y': _number(target.get('y')),
                    'z': _number(target.get('z')),
                    'health': max(0, int(_number(target.get('health'), 1))),
                    'max_health': max(
                        1, int(_number(target.get('max_health'), 1))),
                    'class_tag': target.get(
                        'class_tag', profile.get('class_tag', 'unknown')),
                    'armor': max(0.0, _number(
                        target.get('armor', profile.get('armor', 0.0)))),
                }
            decision_state = {
                'id': state['id'], 'position': position, 'yaw': state['yaw'],
                'speed': abs(_number(state.get('speed'))), 'dt': step, 'now': now,
                'health': state['health'], 'max_health': state['max_health'],
                'contacts': contacts,
                'neighbours': self._neighbours_for(state, neighbours),
            }
            server_order = self._server_orders.get(state['id'])
            decide_with_order = getattr(self.adapter, 'decide_with_order', None)
            if server_order is not None and callable(decide_with_order):
                server_order = dict(server_order)
                if (server_order.get('target_kind') == 'human' and
                        server_order.get('target_id') is not None):
                    server_order['target_id'] = self._human_planner_id(
                        server_order.get('target_id'))
                command = decide_with_order(
                    decision_state, server_order,
                    lambda yaw: self._clear(position, yaw))
            else:
                command = self.adapter.decide(
                    decision_state, lambda yaw: self._clear(position, yaw))
            # Respect a tank-like hull turn rate.  Teleporting the yaw directly
            # to the selected avoidance ray made groups look synchronized and
            # let them cut across unsafe terrain between probes.
            yaw_delta = _angle_delta(command['target_yaw'], state['yaw'])
            maximum_turn = 0.85 * step
            state['yaw'] += max(-maximum_turn, min(maximum_turn, yaw_delta))
            state['aim_yaw'] = command['target_yaw']
            throttle = max(-1.0, min(1.0, _number(command['throttle'])))
            travel_yaw = state['yaw'] if throttle >= 0.0 else state['yaw'] + math.pi
            if not self._clear(position, travel_yaw):
                throttle = 0.0
                driver = getattr(self.adapter, 'driver', None)
                remember = getattr(driver, 'remember_failure', None)
                if callable(remember):
                    remember(state['id'], travel_yaw)
            speed = state.get('move_speed', 14.0) * throttle
            state['speed'] = speed
            state['x'] += math.sin(state['yaw']) * speed * step
            state['z'] += math.cos(state['yaw']) * speed * step
            state['shell_index'] = command['shell_index']
            target = targets.get(command.get('target_id'))
            state['target_kind'] = (
                target.get('kind') if target is not None else None)
            state['target_id'] = (
                target.get('network_id') if target is not None else None)
            if target is not None:
                target_position = target['position']
                dx = target_position[0] - state['x']
                dy = target_position[1] - state['y']
                dz = target_position[2] - state['z']
                horizontal = math.sqrt(dx * dx + dz * dz)
                state['aim_yaw'] = math.atan2(dx, dz)
                state['gun_pitch'] = math.atan2(dy, max(0.001, horizontal))
            fire_range = max(0.0, _number(command.get('fire_range'), 0.0))
            in_range = (target is not None and
                        (fire_range <= 0.0 or
                         _distance(position, target['position']) <= fire_range))
            next_fire = self._next_fire.get(state['id'], 0.0)
            if next_fire <= 0.0:
                next_fire = _number(now) + 0.35 + (state['id'] % 7) * 0.07
                self._next_fire[state['id']] = next_fire
            if (command['fire_allowed'] and target is not None and
                    target.get('visible') and in_range and
                    _number(now) >= next_fire):
                state['fire_seq'] += 1
                self._next_fire[state['id']] = (
                    _number(now) + self._reload_times.get(state['id'], 2.5))
        outgoing = [{'type': 'bot_state', 'bots': [dict(state)
                                                   for state in self.states.values()]}]
        if _number(now) >= self._next_observation:
            self._next_observation = _number(now) + OBSERVATION_SECONDS
            outgoing.append({
                'type': 'bot_observation',
                'contacts': [observations[key]
                             for key in sorted(observations)],
                'affordances': [],
            })
        return outgoing
