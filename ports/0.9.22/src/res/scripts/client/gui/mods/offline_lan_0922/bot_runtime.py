from __future__ import print_function

"""Authority-side, engine-free bridge from v5 bots to the local AI package."""

import math
import random

from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai import maps as tactical_maps
from gui.mods.offline_lan_0922.ai import driver as ai_driver
from gui.mods.offline_lan_0922.ai.navigation import TerrainNavigator
from gui.mods.offline_lan_0922 import critical_damage
from gui.mods.offline_lan_0922 import device_damage
from gui.mods.offline_lan_0922 import prebaked_navigation
from gui.mods.offline_lan_0922 import tank_collision
from gui.mods.offline_lan_0922 import vehicle_physics


TICK_SECONDS = 1.0 / 30.0
OBSERVATION_SECONDS = 0.20
HUMAN_TARGET_ID_BASE = 1000000
VISIBILITY_MIN_SECONDS = 0.18
VISIBILITY_JITTER_SECONDS = 0.018
DECISION_SECONDS = 0.0975
FIRE_DURATION_SECONDS = 10.0
FIRE_TICK_SECONDS = 1.0


def _cache_deadline(now, entity_id, interval, salt=0, stagger=False):
    """Spread only the first expiry, then retain the requested cadence."""
    interval = max(0.001, float(interval))
    deadline = float(now) + interval
    if not stagger:
        return deadline
    phase = (((abs(int(entity_id)) * 17 + int(salt) * 11) % 29) /
             29.0) * interval
    return deadline + phase


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


def _forward_speed(descriptor):
    physics = _value(descriptor, 'physics', {}) or {}
    limits = _value(physics, 'speedLimits', (14.0, 7.0))
    try:
        value = abs(float(limits[0]))
    except (TypeError, ValueError, IndexError):
        value = 14.0
    return max(4.0, min(value, 35.0))


def _view_range(descriptor):
    turret = _value(descriptor, 'turret', {}) or {}
    return max(50.0, min(445.0, _number(
        _value(turret, 'circularVisionRadius', 330.0), 330.0)))


def _hull_dimensions(descriptor):
    """Copy 0.8.2 conservative OBB half dimensions from the hull tester."""
    half_length = 3.5
    half_width = 1.7
    try:
        hull = _value(descriptor, 'hull', {}) or {}
        hit_tester = _value(hull, 'hitTester')
        bbox = hit_tester.bbox
        half_width = max(0.8, abs(float(bbox[0][0])),
                         abs(float(bbox[1][0])))
        half_length = max(1.5, abs(float(bbox[0][2])),
                          abs(float(bbox[1][2])))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        pass
    return half_length, half_width


def _collision_shape(descriptor):
    """Return the current 0.8.2 chassis hit-tester body."""
    return tank_collision.chassis_shape(descriptor)


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


def _wrapped(value):
    return _angle_delta(value, 0.0)


def _point(value, fallback=(0.0, 0.0, 0.0)):
    if isinstance(value, dict):
        return (_number(value.get('x'), fallback[0]),
                _number(value.get('y'), fallback[1]),
                _number(value.get('z'), fallback[2]))
    try:
        return (_number(value[0], fallback[0]),
                _number(value[1], fallback[1]),
                _number(value[2], fallback[2]))
    except (TypeError, IndexError):
        return fallback


def _slew(current, desired, maximum_step):
    difference = float(desired) - float(current)
    step = max(0.0, float(maximum_step))
    if difference > step:
        return float(current) + step
    if difference < -step:
        return float(current) - step
    return float(desired)


def _rotation_speed(component, default):
    return max(0.0, _number(_value(component, 'rotationSpeed', default),
                            default))


def _gun_pitch_limits(descriptor):
    gun = _value(descriptor, 'gun', {}) or {}
    limits = _value(gun, 'pitchLimits')
    if isinstance(limits, dict):
        limits = limits.get('absolute', limits)
    try:
        return float(limits[0]), float(limits[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return -0.35, 0.15


def _critical_parts(state):
    critical = state.get('critical')
    if not isinstance(critical, dict):
        return {}, set(), set()
    devices = {}
    for record in critical.get('devices') or ():
        if isinstance(record, dict) and record.get('name'):
            devices[str(record['name'])] = _number(record.get('hp'))
    destroyed = set(str(name) for name in
                    (critical.get('destroyed') or ()))
    crew_ko = set(str(name) for name in (critical.get('crew_ko') or ()))
    return devices, destroyed, crew_ko


def _critical_factor(state, descriptor, stat):
    devices, destroyed, crew_ko = _critical_parts(state)
    return (device_damage.crew_stat_factor(crew_ko, stat) *
            device_damage.module_stat_factor(
                devices, destroyed, descriptor, stat))


def _critical_signature(payload):
    """Match the server's durable, three-decimal critical-state boundary."""
    if not isinstance(payload, dict) or not payload:
        return ()
    devices = []
    for record in payload.get('devices') or ():
        if not isinstance(record, dict) or not record.get('name'):
            raise ValueError('bot critical device is malformed')
        devices.append((
            str(record['name']), round(_number(record.get('hp')), 3),
            round(_number(record.get('max_hp'), 1.0), 3),
            str(record.get('state', ''))))
    signature = (
        tuple(sorted(devices)),
        tuple(sorted(str(name) for name in
                     (payload.get('destroyed') or ()))),
        tuple(sorted(str(name) for name in
                     (payload.get('crew_ko') or ()))),
        bool(payload.get('fire', False)),
        bool(payload.get('ammo_rack_death', False)))
    if signature == ((), (), (), False, False):
        return ()
    return signature


def _canonical_critical(payload):
    """Emit exactly the durable shape returned by server ``_critical_state``."""
    if not isinstance(payload, dict) or not payload:
        return {}
    devices = []
    for record in payload.get('devices') or ():
        if (not isinstance(record, dict) or not record.get('name') or
                'hp' not in record or 'max_hp' not in record or
                record.get('state') not in (
                    'normal', 'critical', 'destroyed')):
            raise ValueError('bot critical device is malformed')
        maximum = max(1.0, round(float(record['max_hp']), 3))
        hp = max(0.0, min(round(float(record['hp']), 3), maximum))
        devices.append({
            'name': str(record['name']), 'hp': hp, 'max_hp': maximum,
            'state': str(record['state'])})
    devices.sort(key=lambda record: record['name'])
    return {
        'devices': devices,
        'destroyed': sorted(str(name) for name in
                            (payload.get('destroyed') or ())),
        'crew_ko': sorted(str(name) for name in
                          (payload.get('crew_ko') or ())),
        'fire': bool(payload.get('fire', False)),
        'ammo_rack_death': bool(payload.get('ammo_rack_death', False)),
        'events': [],
    }


def _combat_signature(state):
    return (
        max(0, int(_number(state.get('health')))),
        bool(state.get('alive', False)),
        _critical_signature(state.get('critical')),
        round(_number(state.get('combat_fire_elapsed')), 6),
        round(_number(state.get('combat_fire_timer')), 6))


def _combat_record(state):
    return {
        'health': max(0, int(_number(state.get('health')))),
        'alive': bool(state.get('alive', False)),
        'critical': _canonical_critical(state.get('critical')),
        'combat_fire_elapsed': round(
            _number(state.get('combat_fire_elapsed')), 6),
        'combat_fire_timer': round(
            _number(state.get('combat_fire_timer')), 6),
    }


def _apply_combat_record(state, record):
    state['health'] = max(0, min(
        int(_number(record.get('health'))), int(state['max_health'])))
    state['alive'] = bool(record.get('alive')) and state['health'] > 0
    state['critical'] = _canonical_critical(record.get('critical'))
    state['combat_fire_elapsed'] = round(
        _number(record.get('combat_fire_elapsed')), 6)
    state['combat_fire_timer'] = round(
        _number(record.get('combat_fire_timer')), 6)
    state['display_health'] = state['health']
    if not state['alive']:
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        state['target_kind'] = None
        state['target_id'] = None


class _BotCriticalVehicle(object):
    """Detached adapter for the copied 0.8.2 repair and fire functions."""

    def __init__(self, state, descriptor, fire_started, fire_timer):
        payload = state.get('critical') or {}
        devices = {}
        for record in payload.get('devices') or ():
            if not isinstance(record, dict) or not record.get('name'):
                raise ValueError('bot critical device is malformed')
            devices[str(record['name'])] = max(
                0.0, float(record.get('hp', 0.0)))
        self.id = int(state['id'])
        self.health = max(0, int(_number(state.get('health'))))
        self.maxHealth = max(1, int(_number(
            state.get('max_health'), self.health or 1)))
        self.typeDescriptor = descriptor
        self.devices_hp = devices
        self._destroyed_devices = set(
            str(name) for name in (payload.get('destroyed') or ()))
        self._crew_ko = set(
            str(name) for name in (payload.get('crew_ko') or ()))
        self._crew_impaired = frozenset()
        self.is_on_fire = bool(payload.get('fire', False))
        self._ammo_rack_death = bool(
            payload.get('ammo_rack_death', False))
        self._fire_started = fire_started
        self._fire_timer = max(0.0, float(fire_timer or 0.0))
        self._offline_proposal_only = True
        self.is_tracked = False
        self.is_engine_dead = False
        self.is_gun_destroyed = False
        self.is_turret_locked = False


class _BotGunState(object):
    """The final 0.8.2 bot reload/clip clock, kept engine-free.

    Bots intentionally have the original offline implementation's unlimited
    reserve ammunition.  A clip starts full; rounds inside it use ``clip[1]``
    and an empty clip is immediately reset but held for the full reload time.
    """

    def __init__(self, descriptor, fire_seq=0):
        gun = _value(descriptor, 'gun', {}) or {}
        self.reload_full = max(
            0.01, _number(_value(gun, 'reloadTime', 3.0), 3.0))
        self.clip_size = 1
        self.reload_intra = 0.0
        clip = _value(gun, 'clip')
        try:
            if len(clip) == 2:
                self.clip_size = max(1, int(clip[0]))
                self.reload_intra = max(0.01, float(clip[1]))
        except (TypeError, ValueError, IndexError):
            pass
        shots = _value(gun, 'shots', ()) or ()
        try:
            self.shell_count = max(1, len(shots))
        except TypeError:
            self.shell_count = 1
        self.clip = self.clip_size
        self.elapsed = 0.0
        self.reload_duration = self.reload_full
        self.restore_fire_seq(fire_seq)

    def restore_fire_seq(self, fire_seq):
        fire_seq = max(0, int(_number(fire_seq)))
        if self.clip_size > 1:
            used = fire_seq % self.clip_size
            self.clip = self.clip_size - used if used else self.clip_size
            self.reload_duration = (
                self.reload_intra if used else self.reload_full)
        else:
            self.clip = 1
            self.reload_duration = self.reload_full
        # A takeover does not know the previous authority's sub-frame clock.
        # Waiting one complete interval is safe and prevents a duplicate shot.
        self.elapsed = 0.0

    def tick(self, dt):
        self.elapsed += max(0.0, float(dt))

    def ready(self, reload_factor=1.0):
        return self.elapsed > (
            self.reload_duration * max(0.0, float(reload_factor)))

    def shell_index(self, requested):
        return max(0, min(int(_number(requested)), self.shell_count - 1))

    def fire(self, reload_factor=1.0):
        if not self.ready(reload_factor):
            return False
        self.elapsed = 0.0
        if self.clip_size > 1:
            self.clip -= 1
            if self.clip <= 0:
                self.clip = self.clip_size
                self.reload_duration = self.reload_full
            else:
                self.reload_duration = self.reload_intra
        else:
            self.reload_duration = self.reload_full
        return True

    def remaining(self, reload_factor=1.0):
        duration = self.reload_duration * max(
            0.0, float(reload_factor))
        return max(0.0, duration - self.elapsed)


def _dispersed_barrel_angles(bot_id, round_id, fire_seq, yaw, pitch):
    """Return the actual physical shot ray used by the battle resolver.

    The 0.8.2 presentation uses negative pitch for a raised barrel.  Protocol
    ``shot_pitch`` is a physical vector elevation (positive is up), matching
    the #1513 projectile/raycast boundary.  A per-shot seed makes authority
    takeover deterministic without sharing ``random`` module state.
    """
    direction = list(ai_driver.barrel_direction(yaw, pitch))
    seed = ((int(_number(round_id)) & 0xffff) * 1000003 +
            (int(bot_id) & 0xffff) * 9176 +
            (int(fire_seq) & 0x7fffffff) * 6113) & 0x7fffffff
    generator = random.Random(seed)
    sigma = 0.03 / 3.0
    direction[0] += generator.gauss(0.0, sigma)
    direction[1] += generator.gauss(0.0, sigma)
    direction[2] += generator.gauss(0.0, sigma)
    length = math.sqrt(sum(value * value for value in direction))
    if length <= 1e-9:
        direction = [0.0, 0.0, 1.0]
    else:
        direction = [value / length for value in direction]
    horizontal = math.sqrt(direction[0] * direction[0] +
                           direction[2] * direction[2])
    return (math.atan2(direction[0], direction[2]),
            math.atan2(direction[1], max(1e-9, horizontal)))


class BotRuntime(object):
    """Produces v5 ``bot_manifest`` and ``bot_state`` payloads without entities."""

    def __init__(self, local_player_id, descriptor_resolver=None,
                 direction_probe=None, adapter_factory=None,
                 vehicle_selector=None, visibility_probe=None,
                 firing_lane_probe=None,
                 spawn_resolver=None, ground_probe=None,
                 physics_ground_probe=None,
                 obstacle_probe=None, bounds=None, cover_probe=None,
                 native_motion=False, baked_graph=None):
        self.local_player_id = local_player_id
        self.descriptor_resolver = descriptor_resolver or (lambda unused: {})
        self.direction_probe = direction_probe or (lambda *unused: True)
        self.adapter_factory = adapter_factory or BotAdapter
        self.vehicle_selector = vehicle_selector or (
            lambda raw: raw.get('vehicle') or 'ussr:R11_MS-1')
        self.visibility_probe = visibility_probe or (
            lambda unused_source, unused_target: True)
        # The production #1513 adapter uses the same static collision ray for
        # spotting and shooting, but they are separate decisions and caches.
        # Keeping an explicit seam also makes it impossible for a stale team
        # spot to stand in for a current clear barrel lane.
        self.firing_lane_probe = firing_lane_probe or self.visibility_probe
        self.spawn_resolver = spawn_resolver
        self._injected_baked_graph = baked_graph
        self.baked_graph = None
        self._navigation_map_name = None
        self._navigation_error = None
        self._ground_probe = ground_probe
        self._physics_ground_probe = physics_ground_probe
        self._obstacle_probe = obstacle_probe
        self._navigation_bounds = bounds
        self.navigator = (TerrainNavigator(
            ground_probe, obstacle_probe, bounds, 18.0)
            if callable(ground_probe) else None)
        self.cover_probe = cover_probe
        self.native_motion = bool(native_motion)
        self.adapter = None
        self.authority_id = None
        self.round_id = None
        self.states = {}
        self._accumulator = 0.0
        self._manifest_sent = False
        self._descriptors = {}
        self._gun_states = {}
        self._shot_los_cache = {}
        self._physics_params = {}
        self._player_collision_profiles = {}
        self._turn_speeds = {}
        self._ram_cooldowns = {}
        self._ram_seq = 0
        self.finished = False
        self._visibility_cache = {}
        self._server_orders = {}
        self._order_revision = -1
        self._next_observation = 0.0
        self._cover_cursor = 0
        self._decision_cache = {}
        self._combat_sync = {}
        self._server_tick = -1

    def is_authority(self):
        return self.authority_id == self.local_player_id

    def _ensure_navigation_graph(self, map_name):
        """Install a matching immutable graph after the battle map is known."""
        map_name = tactical_maps.normalize_map_name(map_name)
        if not map_name:
            raise ValueError('battle map name is unavailable')
        if self._navigation_map_name == map_name:
            return
        if map_name not in prebaked_navigation.SUPPORTED_MAPS:
            raise ValueError(
                'standard battle map is not supported: %s' % map_name)
        graph = None
        injected = self._injected_baked_graph
        if isinstance(injected, dict):
            injected_name = tactical_maps.normalize_map_name(
                injected.get('map'))
            if injected_name == map_name:
                graph = injected
        if graph is None:
            graph = prebaked_navigation.load_graph(map_name)
        if graph is None:
            raise ValueError(
                'required navigation graph is missing for %s' % map_name)
        prebaked_navigation._validate(graph, map_name)
        self._validated_baked_routes(graph)
        if not callable(self._ground_probe):
            raise ValueError(
                'navigation ground probe is unavailable for %s' % map_name)
        if not callable(self._physics_ground_probe):
            raise ValueError(
                'physics ground probe is unavailable for %s' % map_name)
        try:
            navigator = TerrainNavigator(
                self._ground_probe, self._obstacle_probe,
                self._navigation_bounds, 18.0, baked_graph=graph)
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError(
                'required navigation graph cannot be installed for %s: %s' %
                (map_name, error))
        self._navigation_map_name = map_name
        self._navigation_error = None
        self.baked_graph = graph
        self.navigator = navigator

    @staticmethod
    def _validated_baked_routes(graph):
        routes = graph.get('routes') if isinstance(graph, dict) else None
        if not isinstance(routes, dict):
            raise ValueError('navigation graph routes are missing')
        for team in (1, 2):
            values = routes.get(str(team), routes.get(team))
            if not isinstance(values, (list, tuple)) or not values:
                raise ValueError(
                    'navigation graph routes are missing for team %d' % team)
            for route in values:
                if not isinstance(route, dict):
                    raise ValueError('navigation graph route is invalid')
                waypoints = route.get('waypoints')
                if (not isinstance(waypoints, (list, tuple)) or
                        not waypoints or len(waypoints) > 16):
                    raise ValueError(
                        'navigation graph route waypoint count is invalid')
                for point in waypoints:
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
                    try:
                        x = float(point[0])
                        z = float(point[1])
                    except (TypeError, ValueError, IndexError):
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
                    if (x != x or z != z or abs(x) == float('inf') or
                            abs(z) == float('inf')):
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
        return routes

    def _new_adapter(self, map_name, round_id):
        """Keep custom two-argument factories compatible with the graph seam."""
        baked_routes = (self.baked_graph or {}).get('routes')
        if (not isinstance(baked_routes, dict) or not any(
                baked_routes.get(key) for key in (1, 2, '1', '2'))):
            return self.adapter_factory(map_name, round_id)
        if self.adapter_factory is BotAdapter:
            return self.adapter_factory(map_name, round_id,
                                        baked_routes=baked_routes)
        try:
            return self.adapter_factory(map_name, round_id,
                                        baked_routes=baked_routes)
        except TypeError:
            return self.adapter_factory(map_name, round_id)

    def _clear(self, position, yaw, speed=0.0):
        """Treat collision, excessive slope and water as a failed local ray."""
        try:
            try:
                result = self.direction_probe(position, yaw, speed)
            except TypeError:
                # Keep the engine-free two-argument test/probe contract usable.
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
            self._descriptors = {}
            self._gun_states = {}
            self._shot_los_cache = {}
            self._physics_params = {}
            self._player_collision_profiles = {}
            self._turn_speeds = {}
            self._ram_cooldowns = {}
            self._ram_seq = 0
            self.adapter = None
            self.finished = False
            self._visibility_cache = {}
            self._server_orders = {}
            self._order_revision = -1
            self._next_observation = 0.0
            self._cover_cursor = 0
            self._decision_cache = {}
            self._combat_sync = {}
            self._server_tick = -1
        self._apply_orders(message)
        if message.get('battle_result') is not None:
            self.finished = True
        previous_authority = self.authority_id
        self.authority_id = message.get('bot_authority_id')
        authority_handoff = (
            previous_authority is not None and
            previous_authority != self.authority_id and
            self.is_authority() and
            isinstance(message.get('bot_manifest'), (list, tuple)))
        if previous_authority != self.authority_id:
            self._visibility_cache = {}
            self._decision_cache = {}
            if self.is_authority():
                self._manifest_sent = False
        if authority_handoff:
            # The takeover manifest is an explicit server-authority boundary.
            # Existing combat sync entries may still be based on an older
            # snapshot than publications already accepted from the previous
            # authority, so keep a per-bot handoff window until one canonical
            # acknowledgement resolves that overlap.
            for sync in self._combat_sync.values():
                sync['authority_handoff_pending'] = True
        if not self.is_authority():
            return []
        if self.finished:
            return []
        if self.adapter is None:
            self._ensure_navigation_graph(message.get('map', ''))
            self.adapter = self._new_adapter(message.get('map', ''),
                                             round_id or 0)
            if self.navigator is not None:
                self.adapter.navigation_target = self._navigation_target
        manifest = message.get('bot_manifest') or message.get('bots') or []
        for raw in manifest:
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            bot_id = int(raw['id'])
            vehicle_name = self.vehicle_selector(raw)
            descriptor = self.descriptor_resolver(vehicle_name)
            half_length, half_width = _hull_dimensions(descriptor)
            self._descriptors.setdefault(bot_id, descriptor)
            if bot_id not in self._gun_states:
                self._gun_states[bot_id] = _BotGunState(
                    descriptor, raw.get('fire_seq', 0))
            self._physics_params[bot_id] = vehicle_physics.derive_params(
                descriptor)
            self._turn_speeds[bot_id] = 0.0
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
                'turret_yaw': 0.0, 'gun_pitch': 0.0,
                'desired_gun_pitch': 0.0,
                'gun_aligned': False, 'hull_aiming': False,
                'health': health, 'max_health': max_health,
                'alive': bool(raw.get('alive', health > 0)) and health > 0,
                'fire_seq': max(0, int(_number(raw.get('fire_seq'), 0))),
                'shell_index': max(0, min(
                    int(_number(raw.get('shell_index'), 0)), 9)),
                'speed': _number(raw.get('speed')),
                'movement_dir': 0, 'rotation_dir': 0,
                'move_speed': _forward_speed(descriptor),
                'view_range': _view_range(descriptor),
                'half_length': half_length, 'half_width': half_width,
                'collision_shape': _collision_shape(descriptor),
                'mass': self._physics_params[bot_id]['mass'],
                'push_x': 0.0, 'push_z': 0.0,
                'vertical_speed': 0.0, 'airborne': False,
                'grounded_once': False, 'last_drive_pitch': 0.0,
                'critical': (dict(raw.get('critical'))
                             if isinstance(raw.get('critical'), dict) else {}),
                'combat_revision': max(0, int(_number(
                    raw.get('combat_revision'), 0))),
                'combat_base_revision': max(0, int(_number(
                    raw.get('combat_base_revision'), 0))),
                'combat_ack_seq': max(0, int(_number(
                    raw.get('combat_ack_seq'), 0))),
                'combat_fire_elapsed': round(max(0.0, min(
                    FIRE_DURATION_SECONDS, _number(
                        raw.get('combat_fire_elapsed'), 0.0))), 6),
                'combat_fire_timer': round(max(0.0, min(
                    FIRE_TICK_SECONDS - 0.000001, _number(
                        raw.get('combat_fire_timer'), 0.0))), 6),
                'profile': profile, 'route': route,
            })
            gun_state = self._gun_states[bot_id]
            state = self.states[bot_id]
            sync = self._combat_sync_state(state)
            if authority_handoff:
                sync['authority_handoff_pending'] = True
            reload_factor = _critical_factor(state, descriptor, 'reload')
            state['clip_size'] = gun_state.clip_size
            state['clip'] = gun_state.clip
            state['reload_time'] = gun_state.remaining(reload_factor)
            state['reload_duration'] = (
                gun_state.reload_duration * reload_factor)
        if self._manifest_sent:
            return []
        bots = [self._manifest_entry(state)
                for state in self._ordered_states()]
        self._manifest_sent = True
        return [{'type': 'bot_manifest', 'bots': bots}]

    def _combat_sync_state(self, state):
        bot_id = int(state['id'])
        sync = self._combat_sync.get(bot_id)
        if sync is None:
            signature = _combat_signature(state)
            revision = max(0, int(_number(
                state.get('combat_revision'), 0)))
            base_revision = max(0, int(_number(
                state.get('combat_base_revision'), 0)))
            acked_seq = max(0, int(_number(
                state.get('combat_ack_seq'), 0)))
            sync = {
                'server_signature': signature,
                'server_combat': _combat_record(state),
                'published_signature': signature,
                'pending': [],
                'next_seq': acked_seq,
                'acked_seq': acked_seq,
                'combat_revision': revision,
                'base_revision': base_revision,
                'server_tick': -1,
                'unpublished_steps': [],
                'authority_handoff_pending': False,
            }
            self._combat_sync[bot_id] = sync
        state['combat_revision'] = sync['combat_revision']
        state['combat_base_revision'] = sync['base_revision']
        state['combat_ack_seq'] = sync['acked_seq']
        state['combat_seq'] = sync['next_seq']
        return sync

    def _mark_combat_publication(self, state):
        sync = self._combat_sync_state(state)
        signature = _combat_signature(state)
        if signature == sync['published_signature']:
            return False
        sync['next_seq'] += 1
        sync['pending'].append({
            'seq': sync['next_seq'],
            'signature': signature,
            'combat': _combat_record(state),
            'steps': list(sync['unpublished_steps']),
        })
        sync['unpublished_steps'] = []
        sync['published_signature'] = signature
        state['combat_seq'] = sync['next_seq']
        return True

    def _apply_server_combat_state(self, state, raw, server_tick):
        """Reconcile an explicit server base/revision/ack boundary.

        Signatures validate an acknowledged publication; they never decide
        whether the server consumed it.  ``combat_ack_seq`` is the sole answer
        to that question.  When an external hit opens a new base, only the
        unacknowledged repair/fire time slices are replayed on the new canonical
        state.
        """
        sync = self._combat_sync_state(state)
        if server_tick is not None and server_tick < sync['server_tick']:
            return False
        candidate = dict(state)
        candidate['health'] = max(0, min(
            int(_number(raw.get('health'), state['health'])),
            int(state['max_health'])))
        candidate['alive'] = (
            bool(raw.get('alive', candidate['health'] > 0)) and
            candidate['health'] > 0)
        contract = ('critical', 'combat_revision', 'combat_base_revision',
                    'combat_ack_seq', 'combat_fire_elapsed',
                    'combat_fire_timer')
        if not all(name in raw for name in contract):
            raise ValueError('modern bot snapshot combat contract is missing')
        if not isinstance(raw['critical'], dict):
            raise ValueError('modern bot snapshot critical state is invalid')
        try:
            revision = int(raw['combat_revision'])
            base_revision = int(raw['combat_base_revision'])
            acked_seq = int(raw['combat_ack_seq'])
            fire_elapsed = float(raw['combat_fire_elapsed'])
            fire_timer = float(raw['combat_fire_timer'])
            exact = (
                not isinstance(raw['combat_revision'], bool) and
                not isinstance(raw['combat_base_revision'], bool) and
                not isinstance(raw['combat_ack_seq'], bool) and
                not isinstance(raw['combat_fire_elapsed'], bool) and
                not isinstance(raw['combat_fire_timer'], bool) and
                float(raw['combat_revision']) == revision and
                float(raw['combat_base_revision']) == base_revision and
                float(raw['combat_ack_seq']) == acked_seq and
                not math.isnan(fire_elapsed) and
                not math.isinf(fire_elapsed) and
                not math.isnan(fire_timer) and
                not math.isinf(fire_timer))
        except (TypeError, ValueError, OverflowError):
            exact = False
        if (not exact or revision < 0 or base_revision < 0 or
                acked_seq < 0 or base_revision > revision or
                fire_elapsed < 0.0 or
                fire_elapsed > FIRE_DURATION_SECONDS or
                fire_timer < 0.0 or fire_timer >= FIRE_TICK_SECONDS):
            raise ValueError('modern bot snapshot combat contract is invalid')
        candidate['critical'] = _canonical_critical(raw['critical'])
        if (not candidate['critical'].get('fire', False) and
                (fire_elapsed != 0.0 or fire_timer != 0.0)):
            raise ValueError('inactive bot fire has a non-zero clock')
        candidate['combat_fire_elapsed'] = round(fire_elapsed, 6)
        candidate['combat_fire_timer'] = round(fire_timer, 6)
        candidate_record = _combat_record(candidate)
        signature = _combat_signature(candidate)

        acknowledged = None
        if acked_seq > sync['acked_seq']:
            for pending in sync['pending']:
                if pending['seq'] == acked_seq:
                    acknowledged = pending
                    break
        handoff_pending = sync.get('authority_handoff_pending', False)
        handoff_new_base = (
            handoff_pending and
            base_revision > sync['base_revision'])
        handoff_same_base_overlap = (
            handoff_pending and
            base_revision == sync['base_revision'] and
            acked_seq > sync['acked_seq'] and
            (acked_seq > sync['next_seq'] or
             (acknowledged is not None and
              acknowledged['signature'] != signature)))
        handoff_canonical_reset = (
            handoff_new_base or handoff_same_base_overlap)
        if (revision < sync['combat_revision'] or
                base_revision < sync['base_revision'] or
                acked_seq < sync['acked_seq'] or
                (acked_seq > sync['next_seq'] and
                 not handoff_canonical_reset)):
            raise ValueError('server bot combat revision moved backwards')

        if handoff_canonical_reset:
            if revision <= sync['combat_revision']:
                raise ValueError('server bot combat handoff ack is inconsistent')
            # A promoted authority may start from the last snapshot it consumed
            # while the server has already accepted later publications from the
            # old authority.  Those sequence numbers overlap any work started
            # locally during the promotion window.  A new base also makes every
            # local pending step a derivative of the superseded baseline, so no
            # step can be identified safely for replay.  Treat either case as
            # an explicit canonical handoff reset and resume after the server
            # ack.
            _apply_combat_record(state, candidate_record)
            sync['server_signature'] = signature
            sync['server_combat'] = candidate_record
            sync['published_signature'] = signature
            sync['pending'] = []
            sync['unpublished_steps'] = []
            sync['next_seq'] = acked_seq
            sync['acked_seq'] = acked_seq
            sync['combat_revision'] = revision
            sync['base_revision'] = base_revision
            sync['authority_handoff_pending'] = False
            state['combat_revision'] = revision
            state['combat_base_revision'] = base_revision
            state['combat_ack_seq'] = acked_seq
            state['combat_seq'] = acked_seq
            if server_tick is not None:
                sync['server_tick'] = server_tick
            return True

        if base_revision == sync['base_revision']:
            if acked_seq == sync['acked_seq']:
                if (revision != sync['combat_revision'] or
                        signature != sync['server_signature']):
                    raise ValueError(
                        'server changed bot combat without a publication ack')
            else:
                if (acknowledged is None or
                        acknowledged['signature'] != signature or
                        revision <= sync['combat_revision']):
                    raise ValueError('server bot combat ack is inconsistent')
                sync['pending'] = [
                    pending for pending in sync['pending']
                    if pending['seq'] > acked_seq]
                # A matching publication from this authority is an ordered
                # barrier: no accepted state from the previous authority can
                # remain unresolved after it.
                sync['authority_handoff_pending'] = False
            sync['server_signature'] = signature
            sync['server_combat'] = candidate_record
            sync['acked_seq'] = acked_seq
            sync['combat_revision'] = revision
            state['combat_revision'] = revision
            state['combat_base_revision'] = base_revision
            state['combat_ack_seq'] = acked_seq
            state['combat_seq'] = sync['next_seq']
            if server_tick is not None:
                sync['server_tick'] = server_tick
            return False

        if revision < base_revision or revision <= sync['combat_revision']:
            raise ValueError('new bot combat base has no canonical revision')
        boundary = None
        if acked_seq == sync['acked_seq']:
            boundary_record = sync['server_combat']
        else:
            for pending in sync['pending']:
                if pending['seq'] == acked_seq:
                    boundary = pending
                    break
            if boundary is None:
                raise ValueError('new bot combat base has an unknown ack')
            boundary_record = boundary['combat']

        replay_steps = []
        for pending in sync['pending']:
            if pending['seq'] > acked_seq:
                replay_steps.extend(pending['steps'])
        replay_steps.extend(sync['unpublished_steps'])

        _apply_combat_record(state, candidate_record)
        sync['server_signature'] = signature
        sync['server_combat'] = candidate_record
        sync['published_signature'] = signature
        sync['pending'] = []
        sync['unpublished_steps'] = []
        sync['next_seq'] = acked_seq
        sync['acked_seq'] = acked_seq
        sync['combat_revision'] = revision
        sync['base_revision'] = base_revision
        sync['authority_handoff_pending'] = False

        for replay_step, replay_now, replay_fire in replay_steps:
            self._advance_bot_critical(
                state, replay_step, replay_now, record_step=False,
                advance_fire=replay_fire)
        replayed_signature = _combat_signature(state)
        if replayed_signature != signature:
            sync['next_seq'] = acked_seq + 1
            sync['pending'] = [{
                'seq': sync['next_seq'],
                'signature': replayed_signature,
                'combat': _combat_record(state),
                'steps': list(replay_steps),
            }]
            sync['published_signature'] = replayed_signature
        else:
            # The explicit fire clock is part of the durable signature.  A
            # replay slice that changed neither combat state nor clock is a
            # completed no-op and must not leak into a later lineage.
            sync['unpublished_steps'] = []
        state['combat_revision'] = revision
        state['combat_base_revision'] = base_revision
        state['combat_ack_seq'] = acked_seq
        state['combat_seq'] = sync['next_seq']
        if server_tick is not None:
            sync['server_tick'] = server_tick
        return True

    def _advance_bot_critical(self, state, step, now, record_step=True,
                              advance_fire=True):
        payload = state.get('critical')
        if not isinstance(payload, dict) or not payload:
            return False
        before_signature = _combat_signature(state)
        was_on_fire = bool(payload.get('fire', False))
        sync = self._combat_sync_state(state)
        descriptor = self._descriptors.get(state['id'], {})
        fire_elapsed = round(max(0.0, min(
            FIRE_DURATION_SECONDS,
            _number(state.get('combat_fire_elapsed')))), 6)
        fire_timer = round(max(0.0, min(
            FIRE_TICK_SECONDS - 0.000001,
            _number(state.get('combat_fire_timer')))), 6)
        fire_started = (
            float(now) - min(
                FIRE_DURATION_SECONDS, fire_elapsed + float(step))
            if was_on_fire and advance_fire else None)
        shadow = _BotCriticalVehicle(
            state, descriptor, fire_started, fire_timer)
        repair_payload = critical_damage.tick_repair(shadow, step)
        fire_damage = 0
        fire_payload = None
        if advance_fire:
            fire_damage, fire_payload = critical_damage.tick_fire(
                shadow, step, now=now)
        if was_on_fire and advance_fire and shadow.is_on_fire:
            state['combat_fire_elapsed'] = round(min(
                FIRE_DURATION_SECONDS, fire_elapsed + float(step)), 6)
            state['combat_fire_timer'] = round(
                max(0.0, min(FIRE_TICK_SECONDS - 0.000001,
                             shadow._fire_timer)), 6)
        elif not shadow.is_on_fire:
            state['combat_fire_elapsed'] = 0.0
            state['combat_fire_timer'] = 0.0
        durable = fire_payload or repair_payload
        if durable is not None:
            state['critical'] = _canonical_critical(durable)
        if fire_damage > 0:
            state['health'] = max(
                0, int(state['health']) - int(fire_damage))
            state['alive'] = state['health'] > 0
            state['display_health'] = state['health']
            if not state['alive']:
                state['death_reason'] = 1
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
                state['target_kind'] = None
                state['target_id'] = None
        changed = _combat_signature(state) != before_signature
        if (record_step and
                (changed or was_on_fire or
                 bool((state.get('critical') or {}).get('fire', False)))):
            sync['unpublished_steps'].append(
                (float(step), float(now), bool(was_on_fire)))
        return changed

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
        server_tick = message.get('server_tick')
        if server_tick is not None:
            try:
                server_tick = int(server_tick)
            except (TypeError, ValueError):
                raise ValueError('bot snapshot server_tick is invalid')
            if server_tick < self._server_tick:
                return
            self._server_tick = server_tick
        for raw in message.get('bots') or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                state = self.states.get(int(raw['id']))
            except (TypeError, ValueError):
                continue
            if state is None:
                continue
            self._apply_server_combat_state(state, raw, server_tick)
            previous_fire_seq = int(state.get('fire_seq', 0))
            incoming_fire_seq = max(
                0, int(_number(raw.get('fire_seq'), 0)))
            state['fire_seq'] = max(previous_fire_seq, incoming_fire_seq)
            if incoming_fire_seq > previous_fire_seq:
                gun_state = self._gun_states.get(state['id'])
                if gun_state is not None:
                    gun_state.restore_fire_seq(incoming_fire_seq)
                    descriptor = self._descriptors.get(state['id'], {})
                    reload_factor = _critical_factor(
                        state, descriptor, 'reload')
                    state['clip'] = gun_state.clip
                    state['reload_time'] = gun_state.remaining(reload_factor)
                    state['reload_duration'] = (
                        gun_state.reload_duration * reload_factor)
            state['shell_index'] = max(0, min(
                int(_number(raw.get('shell_index'),
                            state.get('shell_index', 0))), 9))
            if not state['alive']:
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
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
            order = dict(raw)
            # Match the mature 0.8.2 network boundary: JSON point records are
            # converted before any planner, navigator, or driver sees them.
            # Leaving route_anchor as a dict makes TerrainNavigator execute
            # tuple(dict), then float('x'/'y'/'z') on the first live tick.
            for name in ('aim_position', 'face_position', 'move_position',
                         'route_anchor'):
                point = order.get(name)
                if isinstance(point, dict):
                    order[name] = _position(point)
            accepted[bot_id] = order
        if revision != self._order_revision or accepted != self._server_orders:
            self._decision_cache = {}
        self._server_orders = accepted
        self._order_revision = revision
        return True

    def _manifest_entry(self, state):
        keys = ('id', 'team', 'slot', 'name', 'vehicle', 'health',
                'max_health', 'x', 'y', 'z', 'yaw', 'profile')
        result = dict((key, state[key]) for key in keys)
        # These coordinates were resolved against the loaded retail map by
        # the authority.  Consumers must not run the formation resolver a
        # second time and nudge the same slot away from its canonical pose.
        result['world_pose'] = True
        route = state.get('route') or {}
        waypoints = route.get('waypoints', ()) or ()
        if len(waypoints) > 16:
            raise ValueError(
                'bot route exceeds the 16-waypoint LAN protocol limit')
        result['route'] = {
            'id': route.get('id', 'map_route'),
            'waypoints': [
                {'x': point[0], 'y': 0.0, 'z': point[1],
                 'hold': bool(point[2]) if len(point) > 2 else False}
                for point in waypoints],
        }
        return result

    def _ordered_states(self):
        return sorted(self.states.values(), key=lambda state: (
            int(state.get('slot', 0)), int(state.get('team', 1))))

    def _spawn(self, team, slot):
        if callable(self.spawn_resolver):
            return self.spawn_resolver(team, slot)
        raise ValueError(
            'validated spawn resolver is missing for team %s slot %s' %
            (team, slot))

    def _visible(self, source, target, now):
        target_id = target.get('network_id', target.get('id', 0))
        key = (int(source.get('id', 0)), target.get('kind'), int(target_id))
        cached = self._visibility_cache.get(key)
        ttl = (VISIBILITY_MIN_SECONDS +
               ((key[0] * 31 + key[2] * 17) % 11) *
               VISIBILITY_JITTER_SECONDS)
        if cached is not None and _number(now) - cached[0] < ttl:
            return cached[1]
        distance = _distance(_position(source), target.get('position') or
                             _position(target))
        if distance > _number(source.get('view_range'), 330.0):
            value = False
        elif distance <= 50.0:
            value = True
        else:
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

    def _neighbours_for(self, source, supplied, spatial_index=None,
                        traffic_bodies=None):
        if spatial_index is not None and traffic_bodies is not None:
            position = _position(source)
            result = []
            for body_id in tank_collision.nearby_ids(
                    spatial_index, position[0], position[2]):
                if body_id == source.get('id'):
                    continue
                body = traffic_bodies.get(body_id)
                if body is not None:
                    result.append(body)
            return result
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
                'half_length': _number(raw.get('half_length'), 3.5),
                'half_width': _number(raw.get('half_width'), 1.7),
            })
        return result

    def _traffic_snapshot(self, supplied):
        """Build one immutable local-traffic snapshot for this authority tick."""
        bodies = {}
        for raw in supplied or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            body = dict(raw)
            body['position'] = _position(raw)
            bodies[raw['id']] = body
        for bot_id, raw in self.states.items():
            if not raw.get('alive', True):
                continue
            yaw = _number(raw.get('yaw'))
            speed = _number(raw.get('speed'))
            bodies[bot_id] = {
                'id': bot_id, 'position': _position(raw), 'yaw': yaw,
                'velocity': (math.sin(yaw) * speed, 0.0,
                             math.cos(yaw) * speed),
                'half_length': _number(raw.get('half_length'), 3.5),
                'half_width': _number(raw.get('half_width'), 1.7),
            }
        return bodies, tank_collision.build_spatial_index(bodies)

    def apply_native_pose(self, bot_id, position, yaw, speed):
        """Feed a sampled #1513 WGVehiclePhysics pose back to the AI law."""
        try:
            state = self.states.get(int(bot_id))
            if state is None:
                return False
            if isinstance(position, dict):
                point = _position(position)
            else:
                point = (_number(position[0]), _number(position[1]),
                         _number(position[2]))
            state['x'], state['y'], state['z'] = point
            state['yaw'] = _number(yaw, state.get('yaw', 0.0))
            state['speed'] = _number(speed, state.get('speed', 0.0))
            return True
        except (TypeError, ValueError):
            return False

    def _terrain_support(self, state):
        """Copy the 0.8.2 front/centre/back support boundary for one bot."""
        position = _position(state)
        yaw = _number(state.get('yaw'))
        half_length = max(1.5, _number(state.get('half_length'), 3.5))
        sine, cosine = math.sin(yaw), math.cos(yaw)
        highest = None
        centre = None
        for distance in (half_length, 0.0, -half_length):
            x = position[0] + sine * distance
            z = position[2] + cosine * distance
            value = self._physics_ground_probe(x, z, position[1])
            if value is None:
                continue
            value = float(value)
            if highest is None or value > highest:
                highest = value
            if distance == 0.0:
                centre = value
        return highest, centre

    def _update_vertical_motion(self, state, step):
        """Run the same grounded, ledge and ballistic phases as 0.8.2 bots."""
        highest, centre = self._terrain_support(state)
        ground = centre if centre is not None else highest
        if ground is not None:
            speed = abs(_number(state.get('speed')))
            snap_gap = max(0.8, min(2.5, speed * step * 2.0 + 0.6))
            max_climb = max(0.6, speed * step * 2.5)
            com_gap = snap_gap if centre is None else state['y'] - centre
            land_y = ground if centre is None else centre
            if not state.get('grounded_once', False):
                state['y'] = land_y
                state['vertical_speed'] = 0.0
                state['airborne'] = False
                state['grounded_once'] = True
            elif (state['y'] <= ground or
                  (com_gap <= snap_gap and not state.get('airborne', False))):
                if state['y'] < ground:
                    rise = ground - state['y']
                    state['y'] += min(rise, max_climb)
                else:
                    state['y'] += ((ground - state['y']) *
                                   min(1.0, step * 15.0))
                    state['y'] = min(state['y'], ground + 0.12)
                state['vertical_speed'] = 0.0
                state['airborne'] = False
            else:
                if not state.get('airborne', False):
                    pitch = _number(state.get('last_drive_pitch'))
                    state['vertical_speed'] = (
                        _number(state.get('speed')) * math.sin(-pitch)
                        if pitch < 0.0 else 0.0)
                state['airborne'] = True
                substeps = min(8, max(
                    1, int(abs(_number(state.get('vertical_speed')) * step) /
                           0.5) + 1))
                sub_step = step / float(substeps)
                for unused_step in range(substeps):
                    state['vertical_speed'] -= (
                        vehicle_physics.GRAVITY * sub_step)
                    state['y'] += state['vertical_speed'] * sub_step
                    if state['y'] <= land_y:
                        state['y'] = land_y
                        state['vertical_speed'] = 0.0
                        state['airborne'] = False
                        break
        elif state.get('grounded_once', False):
            state['airborne'] = True
            state['vertical_speed'] -= vehicle_physics.GRAVITY * step
            state['y'] += state['vertical_speed'] * step
        else:
            # Terrain streaming owns the first placement. A missing first hit
            # must not turn map loading into a fictitious fall from altitude.
            state['vertical_speed'] = 0.0
            state['airborne'] = False

    def _guard_realised_pose(self, state, tick_pose, tick_was_safe,
                             attempted_yaw):
        """Cancel only this tick's newly unsafe motion; never rewind history."""
        if (not tick_was_safe or
                prebaked_navigation.pose_is_safe(
                    self.baked_graph, _position(state), shoulder_cells=0)):
            return False
        state['x'], state['y'], state['z'] = tick_pose
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        state['push_x'] = 0.0
        state['push_z'] = 0.0
        state['vertical_speed'] = 0.0
        state['airborne'] = False
        driver = getattr(self.adapter, 'driver', None)
        remember = getattr(driver, 'remember_failure', None)
        if callable(remember):
            remember(state['id'], attempted_yaw, 5.0)
        return True

    def _navigation_target(self, bot_id, position, goal, strategic, state):
        if self.navigator is None or _distance(position, goal) <= 15.0:
            return goal
        mode = strategic.get('combat_mode', 'route')
        route_index = int(_number(strategic.get('route_index'), 0))
        if mode in ('route', 'advance', 'hold'):
            path_key = ('route', int(self.states[bot_id].get('team', 0)),
                        strategic.get('route_id', 'direct'), route_index)
            anchor = strategic.get('route_anchor') if route_index > 0 else None
        else:
            path_key = ('local', int(bot_id), mode,
                        strategic.get('target_id'))
            anchor = None
        avoid = []
        for neighbour in state.get('neighbours') or ():
            other = (neighbour.get('position') if isinstance(neighbour, dict)
                     else neighbour)
            if other is not None:
                avoid.append(other)
        return self.navigator.next_target(
            bot_id, position, goal, path_key, state.get('now', 0.0),
            anchor, avoid)

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

    def _player_collision_profile(self, raw):
        vehicle_name = raw.get('vehicle') or 'ussr:R11_MS-1'
        cached = self._player_collision_profiles.get(vehicle_name)
        if cached is not None:
            return cached
        try:
            descriptor = self.descriptor_resolver(vehicle_name)
        except Exception:
            descriptor = {}
        params = vehicle_physics.derive_params(descriptor)
        cached = {
            'mass': params.get('mass', 25000.0),
            'shape': _collision_shape(descriptor),
        }
        self._player_collision_profiles[vehicle_name] = cached
        return cached

    def _resolve_tank_contacts(self, players, now, step):
        """Apply current 0.8.2 chassis OBB response and report rams."""
        if self.native_motion:
            return []
        tanks = []
        for state in self._ordered_states():
            if not state.get('alive', True):
                continue
            yaw = _number(state.get('yaw'))
            speed = _number(state.get('speed'))
            tanks.append({
                'id': int(state['id']), 'alive': True,
                'x': _number(state.get('x')), 'y': _number(state.get('y')),
                'z': _number(state.get('z')), 'yaw': yaw,
                'mass': _number(state.get('mass'), 25000.0),
                'shape': state.get('collision_shape'),
                'vx': (math.sin(yaw) * speed +
                       _number(state.get('push_x'))),
                'vz': (math.cos(yaw) * speed +
                       _number(state.get('push_z'))),
            })
        for raw in players or ():
            if (not isinstance(raw, dict) or raw.get('id') is None or
                    not raw.get('alive', True)):
                continue
            try:
                player_id = HUMAN_TARGET_ID_BASE + int(raw['id'])
            except (TypeError, ValueError):
                continue
            profile = self._player_collision_profile(raw)
            yaw = _number(raw.get('yaw'))
            speed = _number(raw.get('speed'))
            tanks.append({
                'id': player_id, 'alive': True,
                'x': _number(raw.get('x')), 'y': _number(raw.get('y')),
                'z': _number(raw.get('z')), 'yaw': yaw,
                'mass': profile['mass'], 'shape': profile['shape'],
                'vx': math.sin(yaw) * speed,
                'vz': math.cos(yaw) * speed,
            })

        by_id = dict((tank['id'], tank) for tank in tanks)
        collision_bodies = {}
        maximum_radius = 4.0
        for tank in tanks:
            shape = tank.get('shape') or tank_collision.DEFAULT_SHAPE
            radius = math.sqrt(
                _number(shape[0]) * _number(shape[0]) +
                _number(shape[1]) * _number(shape[1]))
            maximum_radius = max(maximum_radius, radius)
            collision_bodies[tank['id']] = {
                'position': (tank['x'], tank['y'], tank['z'])}
        collision_index = tank_collision.build_spatial_index(
            collision_bodies, maximum_radius * 2.0 + 4.0)
        reports = []
        for state in self._ordered_states():
            if not state.get('alive', True):
                continue
            own = by_id.get(int(state['id']))
            if own is None:
                continue
            candidate_ids = tank_collision.nearby_ids(
                collision_index, own['x'], own['z'])
            result = tank_collision.resolve_tank(
                own, (by_id[tank_id] for tank_id in candidate_ids
                      if tank_id != own['id'] and tank_id in by_id),
                now=now, ram_cooldowns=self._ram_cooldowns)
            self._ram_cooldowns = result['cooldowns']
            delta_x, delta_z = result['delta_velocity']
            yaw = _number(state.get('yaw'))
            speed = _number(state.get('speed'))
            forward_impulse = (delta_x * math.sin(yaw) +
                               delta_z * math.cos(yaw))
            applied_forward = 0.0
            if forward_impulse * speed < 0.0:
                applied_forward = (-speed if
                                   abs(forward_impulse) >= abs(speed)
                                   else forward_impulse)
                state['speed'] = speed + applied_forward
            push_x = (_number(state.get('push_x')) + delta_x -
                      applied_forward * math.sin(yaw))
            push_z = (_number(state.get('push_z')) + delta_z -
                      applied_forward * math.cos(yaw))
            correction_x, correction_z = result['correction']
            move_x = correction_x + push_x * step
            move_z = correction_z + push_z * step
            move_distance = math.sqrt(move_x * move_x + move_z * move_z)
            if move_distance > 0.0001:
                contact_yaw = math.atan2(move_x, move_z)
                contact_speed = move_distance / max(float(step), 1.0 / 120.0)
                if not self._clear(_position(state), contact_yaw,
                                   contact_speed):
                    # Tank separation is not permission to cross static world
                    # geometry. Let the other hull take its own inverse-mass
                    # share instead of pushing this hull through a wall.
                    move_x = 0.0
                    move_z = 0.0
                    push_x = 0.0
                    push_z = 0.0
            state['x'] += move_x
            state['z'] += move_z
            state['push_x'] = push_x * 0.90
            state['push_z'] = push_z * 0.90
            for event in result['ram_events']:
                other_id = int(event['other_id'])
                target_kind = ('human' if
                               other_id >= HUMAN_TARGET_ID_BASE else 'bot')
                if target_kind == 'human':
                    other_id -= HUMAN_TARGET_ID_BASE
                self._ram_seq += 1
                reports.append({
                    'type': 'bot_ram', 'bot_id': int(state['id']),
                    'target_kind': target_kind, 'target_id': other_id,
                    'ram_seq': self._ram_seq,
                    'damage_to_bot': event['damage_to_self'],
                    'damage_to_target': event['damage_to_other'],
                })
        return reports

    def _update_gun_aim(self, state, command, target, step):
        """Slew the rendered turret and barrel through the 0.8.2 limits."""
        descriptor = self._descriptors.get(state['id'], {})
        fallback = (target.get('position') if target is not None
                    else _position(state))
        aim_position = _point(command.get('aim_position'), fallback)
        dx = aim_position[0] - _number(state.get('x'))
        dz = aim_position[2] - _number(state.get('z'))
        horizontal = math.sqrt(dx * dx + dz * dz)
        desired_yaw = (math.atan2(dx, dz) if horizontal > 0.1
                       else _number(state.get('yaw')))
        minimum_yaw, maximum_yaw, limited = ai_driver.gun_yaw_limits(
            descriptor)
        desired_relative = _angle_delta(desired_yaw, state['yaw'])
        if limited:
            desired_relative = max(
                minimum_yaw, min(maximum_yaw, desired_relative))
        turret = _value(descriptor, 'turret', {}) or {}
        turret_step = (_rotation_speed(turret, 0.5) * step *
                       _critical_factor(state, descriptor, 'turret_speed'))
        current_relative = _number(state.get('turret_yaw'))
        turret_difference = _angle_delta(desired_relative, current_relative)
        current_relative = _wrapped(
            current_relative + max(-turret_step,
                                   min(turret_step, turret_difference)))
        if limited:
            current_relative = max(
                minimum_yaw, min(maximum_yaw, current_relative))
        state['turret_yaw'] = current_relative
        state['aim_yaw'] = _wrapped(state['yaw'] + current_relative)

        # BigWorld's rendered gun convention is negative pitch for a raised
        # barrel.  The offsets are the mature implementation's target and
        # muzzle heights, rather than aiming the model roots at each other.
        desired_pitch = -math.atan2(
            (aim_position[1] + 1.0) - (_number(state.get('y')) + 1.5),
            max(0.5, horizontal))
        minimum_pitch, maximum_pitch = _gun_pitch_limits(descriptor)
        desired_pitch = max(
            minimum_pitch, min(maximum_pitch, desired_pitch))
        gun = _value(descriptor, 'gun', {}) or {}
        state['gun_pitch'] = _slew(
            _number(state.get('gun_pitch')), desired_pitch,
            _rotation_speed(gun, 0.35) * step)
        state['desired_gun_pitch'] = desired_pitch
        state['gun_aligned'] = bool(
            target is not None and ai_driver.gun_aligned(
                desired_yaw, state['yaw'], state['turret_yaw'],
                desired_pitch, state['gun_pitch']))
        return desired_yaw, horizontal

    def _shot_clear(self, source, target, now):
        """Probe a current static firing lane independently from team spotting."""
        target_id = target.get('network_id', target.get('id', 0))
        key = (int(source.get('id', 0)), target.get('kind'), int(target_id))
        cached = self._shot_los_cache.get(key)
        if cached is not None and _number(now) - cached[0] < 0.20:
            return cached[1]
        try:
            value = bool(self.firing_lane_probe(source, target))
        except Exception:
            value = False
        self._shot_los_cache[key] = (_number(now), value)
        if len(self._shot_los_cache) > 1024:
            oldest = sorted(self._shot_los_cache.items(),
                            key=lambda item: item[1][0])[:256]
            for old_key, unused_value in oldest:
                self._shot_los_cache.pop(old_key, None)
        return value

    def _fire(self, state, gun_state, reload_factor):
        if not gun_state.fire(reload_factor):
            return False
        state['fire_seq'] += 1
        state['shot_yaw'], state['shot_pitch'] = _dispersed_barrel_angles(
            state['id'], self.round_id, state['fire_seq'],
            state['aim_yaw'], state['gun_pitch'])
        state['clip'] = gun_state.clip
        state['reload_time'] = gun_state.remaining(reload_factor)
        state['reload_duration'] = (
            gun_state.reload_duration * reload_factor)
        return True

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
        traffic_bodies, traffic_index = self._traffic_snapshot(neighbours)
        observations = {}
        cover_jobs = []
        tick_poses = {}
        tick_safe = {}
        attempted_yaws = {}
        for state in self.states.values():
            if not state['alive']:
                continue
            self._advance_bot_critical(state, step, now)
            if not state['alive']:
                continue
            position = _position(state)
            tick_poses[state['id']] = position
            tick_safe[state['id']] = prebaked_navigation.pose_is_safe(
                self.baked_graph, position, shoulder_cells=0)
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
            server_order = self._server_orders.get(state['id'])
            decide_with_order = getattr(self.adapter, 'decide_with_order', None)
            cache_key = (('server', self._order_revision)
                         if server_order is not None else ('local',))
            decision_cache = self._decision_cache.get(state['id'])
            if (decision_cache is not None and
                    decision_cache[0] == cache_key and
                    _number(now) < decision_cache[1]):
                command = dict(decision_cache[3])
            else:
                decision_step = step
                if decision_cache is not None:
                    decision_step = max(
                        step, min(0.35, _number(now) - decision_cache[2]))
                decision_state = {
                    'id': state['id'], 'position': position,
                    'yaw': state['yaw'],
                    'speed': abs(_number(state.get('speed'))),
                    'dt': decision_step, 'now': now,
                    'health': state['health'],
                    'max_health': state['max_health'],
                    'contacts': contacts,
                    'neighbours': self._neighbours_for(
                        state, neighbours, traffic_index, traffic_bodies),
                    'velocity': (
                        math.sin(_number(state.get('yaw'))) *
                        _number(state.get('speed')), 0.0,
                        math.cos(_number(state.get('yaw'))) *
                        _number(state.get('speed'))),
                    'half_length': _number(state.get('half_length'), 3.5),
                    'half_width': _number(state.get('half_width'), 1.7),
                }
                if server_order is not None and callable(decide_with_order):
                    server_order = dict(server_order)
                    if (server_order.get('target_kind') == 'human' and
                            server_order.get('target_id') is not None):
                        server_order['target_id'] = self._human_planner_id(
                            server_order.get('target_id'))
                    command = decide_with_order(
                        decision_state, server_order,
                        lambda yaw: self._clear(
                            position, yaw, state.get('speed', 0.0)))
                else:
                    command = self.adapter.decide(
                        decision_state, lambda yaw: self._clear(
                            position, yaw, state.get('speed', 0.0)))
                self._decision_cache[state['id']] = (
                    cache_key,
                    _cache_deadline(
                        now, state['id'], DECISION_SECONDS, 3,
                        decision_cache is None),
                    _number(now), dict(command))
            target = targets.get(command.get('target_id'))
            state['target_kind'] = (
                target.get('kind') if target is not None else None)
            state['target_id'] = (
                target.get('network_id') if target is not None else None)
            throttle = max(-1.0, min(1.0, _number(command['throttle'])))
            turn = max(-1.0, min(1.0, _number(command.get('turn'))))
            aim_fallback = (target.get('position') if target is not None
                            else _position(state))
            aim_position = _point(command.get('aim_position'), aim_fallback)
            aim_dx = aim_position[0] - _number(state.get('x'))
            aim_dz = aim_position[2] - _number(state.get('z'))
            aim_distance = math.sqrt(aim_dx * aim_dx + aim_dz * aim_dz)
            desired_aim_yaw = (
                math.atan2(aim_dx, aim_dz) if aim_distance > 0.1
                else _number(state.get('yaw')))
            descriptor = self._descriptors.get(state['id'], {})
            minimum_yaw, maximum_yaw, unused_limited = (
                ai_driver.gun_yaw_limits(descriptor))
            turn, throttle, hull_aiming = ai_driver.combat_hull_aim(
                state['yaw'], desired_aim_yaw, minimum_yaw, maximum_yaw,
                turn, throttle, command.get('recovery_mode', 'drive'),
                target is not None)
            state['hull_aiming'] = bool(hull_aiming)
            unused_devices, destroyed_devices, unused_crew = (
                _critical_parts(state))
            if destroyed_devices.intersection((
                    'engineHealth', 'leftTrackHealth',
                    'rightTrackHealth')):
                throttle = 0.0
                turn = 0.0
            elif abs(throttle) > 0.01:
                throttle *= _critical_factor(
                    state, descriptor, 'mobility')
            travel_yaw = (state['yaw'] if throttle >= 0.0
                          else state['yaw'] + math.pi)
            attempted_yaws[state['id']] = travel_yaw
            path_clear = (True if (abs(throttle) <= 0.01 or
                                   state.get('airborne', False)) else
                          self._clear(
                              position, travel_yaw,
                              state.get('speed', 0.0)))
            if not path_clear:
                throttle = 0.0
                driver = getattr(self.adapter, 'driver', None)
                remember = getattr(driver, 'remember_failure', None)
                if callable(remember):
                    remember(state['id'], travel_yaw)
            steer_dir = 0
            if abs(turn) > 0.01:
                # LocalDriver already inverts reverse recovery steering for the
                # copied traverse law.  Re-deriving this sign from target_yaw
                # discards that command and recreates the stationary spin.
                steer_dir = 1 if turn > 0.0 else -1
            state['movement_dir'] = (
                1 if throttle > 0.01 else (-1 if throttle < -0.01 else 0))
            state['rotation_dir'] = steer_dir
            if not self.native_motion:
                params = self._physics_params.get(state['id'])
                if params is None:
                    params = vehicle_physics.derive_params({})
                    self._physics_params[state['id']] = params
                try:
                    try:
                        slope_probe = self.direction_probe(
                            position, state['yaw'], state.get('speed', 0.0))
                    except TypeError:
                        slope_probe = self.direction_probe(
                            position, state['yaw'])
                except Exception:
                    slope_probe = {}
                slope = (_number(slope_probe.get('slope'))
                         if isinstance(slope_probe, dict) else 0.0)
                slope_pitch = -math.atan(slope)
                turn_speed = vehicle_physics.traverse_step(
                    params, self._turn_speeds.get(state['id'], 0.0),
                    turn, _number(state.get('speed')), step,
                    drive_intent=throttle)
                self._turn_speeds[state['id']] = turn_speed
                state['yaw'] += turn_speed * step
                while state['yaw'] > math.pi:
                    state['yaw'] -= math.pi * 2.0
                while state['yaw'] < -math.pi:
                    state['yaw'] += math.pi * 2.0
                speed = vehicle_physics.longitudinal_step(
                    params, _number(state.get('speed')), throttle,
                    steer_dir != 0, slope_pitch, step,
                    bool(state.get('airborne', False)), 0, False)
                state['last_drive_pitch'] = slope_pitch
                if not path_clear:
                    speed *= 0.2
                state['speed'] = speed
                if path_clear:
                    state['x'] += math.sin(state['yaw']) * speed * step
                    state['z'] += math.cos(state['yaw']) * speed * step
            gun_state = self._gun_states.get(state['id'])
            if gun_state is None:
                gun_state = _BotGunState(
                    descriptor, state.get('fire_seq', 0))
                self._gun_states[state['id']] = gun_state
            state['shell_index'] = gun_state.shell_index(
                command.get('shell_index', 0))
            unused_desired_yaw, unused_horizontal = self._update_gun_aim(
                state, command, target, step)
            gun_state.tick(step)
            reload_factor = _critical_factor(
                state, descriptor, 'reload')
            state['clip_size'] = gun_state.clip_size
            state['clip'] = gun_state.clip
            state['reload_time'] = gun_state.remaining(reload_factor)
            state['reload_duration'] = (
                gun_state.reload_duration * reload_factor)
            fire_range = max(0.0, _number(command.get('fire_range'), 0.0))
            in_range = (target is not None and
                        _distance(_position(state), target['position']) > 1.0 and
                        (fire_range <= 0.0 or
                         _distance(_position(state), target['position']) < fire_range))
            if (command['fire_allowed'] and target is not None and
                    target.get('visible') and in_range and
                    'gunHealth' not in destroyed_devices and
                    state.get('gun_aligned') and
                    gun_state.ready(reload_factor) and
                    self._shot_clear(state, target, now)):
                self._fire(state, gun_state, reload_factor)
            mode = command.get('combat_mode')
            if (target is not None and target.get('visible') and
                    callable(self.cover_probe) and
                    (mode in ('take_cover', 'cover_hold', 'cover_peek',
                              'cover_return') or
                     (command.get('fire_allowed') and mode in (
                         'engage', 'advance_contact', 'jiggle_forward',
                         'jiggle_back')))):
                cover_jobs.append((state['id'], dict(state), dict(target),
                                   command.get('move_position', position)))
        affordances = []
        cover_jobs.sort(key=lambda value: value[0])
        if cover_jobs:
            cursor = self._cover_cursor % len(cover_jobs)
            ordered = cover_jobs[cursor:] + cover_jobs[:cursor]
            self._cover_cursor = (cursor + 3) % len(cover_jobs)
            ally_positions = dict((team, [
                _position(value) for value in self.states.values()
                if value.get('alive') and value.get('team') == team])
                for team in (1, 2))
            for bot_id, source, target, route in ordered[:3]:
                try:
                    candidates = self.cover_probe(
                        source, target, route,
                        ally_positions.get(source.get('team'), ()),
                        (self.navigator.grid.segment_clear
                         if self.navigator is not None else None))
                except Exception:
                    candidates = ()
                if candidates:
                    affordances.append({
                        'bot_id': int(bot_id),
                        'target_id': int(target.get('network_id')),
                        'target_kind': target.get('kind', 'human'),
                        'candidates': list(candidates),
                    })
        ram_reports = self._resolve_tank_contacts(players, now, step)
        for state in self._ordered_states():
            if state.get('alive', True):
                self._update_vertical_motion(state, step)
                self._guard_realised_pose(
                    state, tick_poses[state['id']], tick_safe[state['id']],
                    attempted_yaws.get(state['id'], state.get('yaw', 0.0)))
            self._mark_combat_publication(state)
        outgoing = [{'type': 'bot_state', 'bots': [dict(state)
                                                   for state in self._ordered_states()]}]
        # The server validates ram proximity against its latest authority pose.
        # Publish state first, then the cooldown-gated damage reports.
        outgoing.extend(ram_reports)
        if _number(now) >= self._next_observation:
            self._next_observation = _number(now) + OBSERVATION_SECONDS
            outgoing.append({
                'type': 'bot_observation',
                'contacts': [observations[key]
                             for key in sorted(observations)],
                'affordances': affordances,
            })
        return outgoing
