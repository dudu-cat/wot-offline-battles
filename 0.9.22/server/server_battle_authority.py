"""Server-hosted battle authority for the 0.9.22 LAN line.

Runs the engine-free simulation (BotRuntime, artillery arcs, projectile
flight, combat and critical-damage law) inside the server's fixed tick,
answering world queries from server_world.BakedWorld and vehicle data from
donated descriptor projections. Results enter BattleState through the same
admission methods the elected authority client used, under the reserved
identity SERVER_AUTHORITY_ID, so the wire protocol is unchanged and every
connected client runs in follower mode.
"""

import math
import os
import sys
import types

_SERVER_ROOT = os.path.dirname(os.path.abspath(__file__))
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)

import server_world
from descriptor_projection import DescriptorStore

_CLIENT_SCRIPT_ROOT = server_world._CLIENT_SCRIPT_ROOT

import random

from gui.mods.offline_lan_0922 import combat_rules
from gui.mods.offline_lan_0922 import critical_damage
from gui.mods.offline_lan_0922 import vehicle_physics
from gui.mods.offline_lan_0922.destructibles_sensor import (
    _SHOT_AP_KINDS_1513, _SHOT_THROUGH_MAX_HP_1513,
    _SHOT_THROUGH_MIN_REDUCTION_1513)
from gui.mods.offline_lan_0922.ai import planner as bot_planner
from gui.mods.offline_lan_0922.artillery_controller import ArtilleryController
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
from gui.mods.offline_lan_0922.projectile_manager import InFlightProjectiles
from gui.mods.offline_lan_0922.spawn_planner import SpawnPlanner


SERVER_AUTHORITY_ID = 0
ARTILLERY_ARC_RAYS_PER_TICK = 4
PROJECTILE_CHORDS_PER_TICK = 240
PROJECTILE_MAX_TIME_MS = 30000
PROJECTILE_PROGRESS_BATCH = 30


class Vector3(object):
    """Pure twin of the Math.Vector3 surface the shared law reads."""

    __slots__ = ('x', 'y', 'z')

    def __init__(self, x=0.0, y=0.0, z=0.0):
        if hasattr(x, 'x') and not isinstance(x, (int, float)):
            self.x, self.y, self.z = float(x.x), float(x.y), float(x.z)
        elif isinstance(x, (tuple, list)):
            self.x, self.y, self.z = (float(x[0]), float(x[1]), float(x[2]))
        else:
            self.x, self.y, self.z = float(x), float(y), float(z)

    def __add__(self, other):
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def scale(self, value):
        return Vector3(self.x * value, self.y * value, self.z * value)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y +
                         self.z * self.z)

    @property
    def lengthSquared(self):
        return self.x * self.x + self.y * self.y + self.z * self.z

    def normalise(self):
        length = self.length
        if length > 0.0:
            self.x /= length
            self.y /= length
            self.z /= length

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __iter__(self):
        return iter((self.x, self.y, self.z))


class Matrix(object):
    """Pure twin of the Math.Matrix operations the crit law uses."""

    def __init__(self, source=None):
        if source is None:
            self._rows = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                          [0.0, 0.0, 1.0]]
            self._translation = [0.0, 0.0, 0.0]
        elif isinstance(source, Matrix):
            self._rows = [list(row) for row in source._rows]
            self._translation = list(source._translation)
        else:
            rows = getattr(source, '_rows', None)
            translation = getattr(source, '_translation', None)
            if rows is None or translation is None:
                raise TypeError('unsupported matrix source')
            self._rows = [list(row) for row in rows]
            self._translation = list(translation)

    @classmethod
    def from_yaw_position(cls, yaw, position):
        matrix = cls()
        sine, cosine = math.sin(float(yaw)), math.cos(float(yaw))
        # BigWorld yaw rotates about Y with +Z forward.
        matrix._rows = [[cosine, 0.0, -sine],
                        [0.0, 1.0, 0.0],
                        [sine, 0.0, cosine]]
        matrix._translation = [float(position[0]), float(position[1]),
                               float(position[2])]
        return matrix

    @property
    def translation(self):
        return Vector3(*self._translation)

    def applyVector(self, vector):
        rows = self._rows
        return Vector3(
            rows[0][0] * vector.x + rows[1][0] * vector.y +
            rows[2][0] * vector.z,
            rows[0][1] * vector.x + rows[1][1] * vector.y +
            rows[2][1] * vector.z,
            rows[0][2] * vector.x + rows[1][2] * vector.y +
            rows[2][2] * vector.z)

    def applyPoint(self, point):
        rotated = self.applyVector(point)
        return Vector3(rotated.x + self._translation[0],
                       rotated.y + self._translation[1],
                       rotated.z + self._translation[2])

    def invert(self):
        # Rigid transforms only: transpose the basis, back-rotate the origin.
        rows = self._rows
        transposed = [[rows[column][row] for column in range(3)]
                      for row in range(3)]
        origin = Vector3(*self._translation)
        self._rows = transposed
        back = self.applyVector(origin)
        self._translation = [-back.x, -back.y, -back.z]


def _pure_math_module():
    module = types.ModuleType('Math')
    module.Vector3 = Vector3
    module.Matrix = Matrix
    return module


def _pure_bigworld_module(clock):
    module = types.ModuleType('BigWorld')
    module.time = clock
    module.player = lambda: types.SimpleNamespace(
        vehicleTypeDescriptor=None, playerVehicleID=-1,
        vehicleTypeDescr=None)
    return module


class engine_modules(object):
    """Install pure Math/BigWorld twins while the authority law runs."""

    def __init__(self, clock):
        self._clock = clock
        self._saved = {}

    def __enter__(self):
        for name, module in (('Math', _pure_math_module()),
                             ('BigWorld', _pure_bigworld_module(self._clock))):
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = module
        return self

    def __exit__(self, *unused):
        for name, previous in self._saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        return False


def install_engine_modules(clock):
    """Install the pure twins for the lifetime of a server process."""
    for name, module in (('Math', _pure_math_module()),
                         ('BigWorld', _pure_bigworld_module(clock))):
        if name not in sys.modules:
            sys.modules[name] = module


class _TargetMock(object):
    """Detached target state for the copied crit law's proposal path."""

    def __init__(self, identity, health, descriptor, position, yaw,
                 combat_state):
        self.id = identity
        self.health = int(health)
        self.typeDescriptor = descriptor
        self.position = Vector3(*position)
        self.matrix = Matrix.from_yaw_position(yaw, position)
        critical = combat_state.get('critical') or {}
        self.devices_hp = dict(
            (name, float(value))
            for name, value in (critical.get('devices_hp') or {}).items())
        self._destroyed_devices = set(critical.get('destroyed') or ())
        self._crew_ko = set(critical.get('crew_ko') or ())
        self.is_on_fire = bool(critical.get('fire', False))
        self._offline_proposal_only = True

    def getComponents(self):
        return ()


class ServerBattleAuthority(object):
    """One round's bot, artillery and projectile authority on the server."""

    def __init__(self, state, world, descriptors, vehicle_selector=None,
                 clock=None):
        self.state = state
        self.world = world
        self.descriptors = descriptors
        self._vehicle_selector = vehicle_selector
        self._clock = clock
        self._bots = None
        self._spawn_planner = SpawnPlanner(navigation_graph=world.graph)
        self._artillery = ArtilleryController()
        self._projectiles = None
        self._fire_seen = {}
        self._shot_seq = {}
        self._round_id = None
        self._live = False
        self._started = False
        self._progress_cursors = {}
        self._piercing_loss = {}
        self._shot_receipts = {}
        self._assignments = {}
        self._required_names = ()
        self._last_now = 0.0

    def started(self):
        return self._started

    def required_projections(self):
        return self._required_names

    def prepare_lineup(self, catalog, roster, public_players,
                       requester_vehicle):
        """Port of the client's mirrored 0.8.2 lineup law over the catalog."""
        self._assignments = {}
        human_names = sorted(set(
            str(raw.get('vehicle') or '')
            for raw in (public_players or ()) if raw.get('vehicle')))
        self._required_names = tuple(human_names)
        profiles = {}
        for row in (catalog or ()):
            profiles[row['name']] = {
                'name': row['name'], 'level': int(row['level']),
                'tags': tuple(row.get('tags') or ()),
            }
        requester_profile = profiles.get(str(requester_vehicle))
        if requester_profile is None:
            return False
        tier = int(requester_profile['level'])
        candidates = [
            profile for profile in profiles.values()
            if bot_planner.vehicle_in_battle_tier_band(
                tier, profile['level']) and
            not _vehicle_excluded(profile)]
        if not candidates:
            return False
        bots_by_team = dict((team, sorted(
            (raw for raw in (roster or ()) if isinstance(raw, dict) and
             int(raw.get('team', 0)) == team),
            key=lambda raw: int(raw.get('slot', 0))))
            for team in (1, 2))
        humans_by_team = {1: [], 2: []}
        for raw in (public_players or ()):
            team = int(raw.get('team', 0) or 0)
            profile = profiles.get(str(raw.get('vehicle') or ''))
            if team in humans_by_team and profile is not None:
                humans_by_team[team].append(profile)
        if not humans_by_team[1] and not humans_by_team[2]:
            humans_by_team[1].append(requester_profile)
        available_tiers = sorted(set(
            int(candidate['level']) for candidate in candidates))
        match_tiers = list(bot_planner.choose_match_tiers(
            tier, random.random(), random.random(), available_tiers))
        for team_profiles in humans_by_team.values():
            for profile in team_profiles:
                if profile['level'] not in match_tiers:
                    match_tiers.append(profile['level'])
                if not any(
                        candidate['level'] == profile['level'] and
                        bot_planner.vehicle_match_class(candidate) ==
                        bot_planner.vehicle_match_class(profile)
                        for candidate in candidates):
                    candidates.append(profile)
        match_tiers = tuple(sorted(set(match_tiers)))
        team_size = max(
            len(humans_by_team[team]) + len(bots_by_team[team])
            for team in (1, 2))
        requirements = bot_planner.shared_human_requirements(humans_by_team)
        template = bot_planner.build_match_template(
            candidates, team_size, requester_profile, match_tiers,
            random, requirements)
        assignments = {}
        for team in (1, 2):
            team_bots = bots_by_team[team]
            picked = bot_planner.remaining_match_template(
                template, humans_by_team[team])
            if len(picked) < len(team_bots):
                picked = bot_planner.select_bot_lineup(
                    picked or candidates, len(team_bots), 1, candidates)
            picked = list(picked[:len(team_bots)])
            random.shuffle(picked)
            picked.sort(key=_vehicle_class_order)
            for raw, entry in zip(team_bots, picked):
                assignments[(team, int(raw.get('slot', 0)))] = entry['name']
        self._assignments = assignments
        self._required_names = tuple(sorted(
            set(human_names) | set(assignments.values())))
        return True

    # -- lifecycle -----------------------------------------------------------

    def battle_start(self, message, now):
        """Build the authority BotRuntime and admit its opening manifest."""
        self._round_id = message.get('round_id')
        self._projectiles = InFlightProjectiles(initial_time=float(now))
        self._fire_seen = {}
        self._shot_seq = {}
        self._piercing_loss = {}
        self._shot_receipts = {}
        self._bots = BotRuntime(
            SERVER_AUTHORITY_ID,
            descriptor_resolver=self._resolve_descriptor,
            direction_probe=self.world.direction_probe,
            vehicle_selector=self._select_vehicle,
            visibility_probe=self.world.visibility,
            firing_lane_probe=self._firing_lane,
            ballistic_solution_probe=self._ballistic_solution,
            artillery_launch_probe=self._artillery_launch,
            artillery_launch_cancel=self._artillery_cancel,
            spawn_resolver=self._spawn_planner.pose,
            ground_probe=self.world.navigation_ground,
            physics_ground_probe=self.world.ground_y,
            obstacle_probe=self.world.navigation_obstacle,
            cover_probe=self.world.sample_cover,
            motion_resolver=self._resolve_motion,
            world_receipt_probe=self.world.world_receipt,
            baked_graph=self.world.graph,
            native_motion=False)
        start_message = dict(message)
        start_message.setdefault('bot_authority_id', SERVER_AUTHORITY_ID)
        with engine_modules(lambda: float(now)):
            for outgoing in self._bots.battle_start(start_message):
                self._route(outgoing, now)
        self._started = True

    def set_live(self, live):
        self._live = bool(live)

    def capture_bases(self):
        """Standard-battle base points from the validated navigation graph."""
        bases = self.world.graph.get('objective_bases') or ()
        result = {}
        for team in (1, 2):
            if len(bases) >= team:
                point = bases[team - 1]
                result[team] = [(float(point[0]), float(point[1]))]
        return result

    def apply_snapshot(self, message):
        if self._bots is not None:
            self._bots.apply_snapshot(message)

    # -- probes --------------------------------------------------------------

    def _resolve_descriptor(self, vehicle_name):
        descriptor = self.descriptors.get(vehicle_name)
        if descriptor is None:
            raise RuntimeError(
                'no donated descriptor projection for %r' % (vehicle_name,))
        return descriptor

    def _select_vehicle(self, raw):
        if self._vehicle_selector is not None:
            return self._vehicle_selector(raw)
        requested = raw.get('vehicle')
        if requested:
            return requested
        assigned = self._assignments.get(
            (int(raw.get('team', 1)), int(raw.get('slot', 0))))
        if assigned:
            return assigned
        names = self.descriptors.names()
        if not names:
            raise RuntimeError('no donated descriptor projections')
        return names[(int(raw.get('team', 1)) * 16 +
                      int(raw.get('slot', 0))) % len(names)]

    def _firing_lane(self, source, target):
        profile = source.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        if str(profile.get('class_tag') or '') == 'SPG':
            if self._bots is None:
                return False
            descriptor = self._bots._descriptors.get(int(source.get('id')))
            shell_index = max(0, int(source.get('shell_index', 0) or 0))
            ready, solution = self._artillery.request(
                source, target, descriptor, shell_index, self._now())
            return bool(ready and solution is not None)
        return self.world.firing_lane(source, target)

    def _ballistic_solution(self, source, target, descriptor, shell_index,
                            now):
        if target is None:
            return None
        return self._artillery.solution(
            source, target, descriptor, shell_index, now)

    def _artillery_launch(self, source, target, descriptor, shell_index,
                          fire_seq, shot_yaw, shot_pitch, flight_time, now):
        if not isinstance(source, dict) or not isinstance(target, dict):
            return None
        origin = _muzzle_origin(source, descriptor)
        if origin is None:
            return None
        ready, receipt = self._artillery.request_launch(
            source, target, descriptor, int(shell_index), int(fire_seq),
            origin, float(shot_yaw), float(shot_pitch),
            float(flight_time), float(now))
        return receipt if ready and isinstance(receipt, dict) else None

    def _artillery_cancel(self, source):
        if not isinstance(source, dict):
            return False
        return bool(self._artillery.cancel_launch(source))

    def _resolve_motion(self, bot_id, position, yaw, speed, descriptor,
                        dt, now):
        """Commit-side contact from baked lanes and the crush law."""
        travel_yaw = (float(yaw) if speed >= 0.0 else float(yaw) + math.pi)
        receipt_reusable = getattr(
            self._bots, 'motion_world_receipt_reusable', None)
        contact_clear = None
        if (callable(receipt_reusable) and receipt_reusable(
                bot_id, position, travel_yaw, speed, now, dt)):
            contact_clear = 'clear'
        reach = max(1.0, abs(float(speed)) * float(dt) * 3.0 + 1.5)
        half_width = 1.4
        half_length = 3.2
        try:
            bbox = server_world._descriptor_hull_bbox(descriptor)
            half_width = max(abs(float(bbox[0][0])),
                             abs(float(bbox[1][0])))
            half_length = max(abs(float(bbox[0][2])),
                              abs(float(bbox[1][2])))
        except (ValueError, TypeError, IndexError, AttributeError):
            pass
        if contact_clear is None:
            sine, cosine = math.sin(travel_yaw), math.cos(travel_yaw)
            end = (float(position[0]) + sine * reach, float(position[1]),
                   float(position[2]) + cosine * reach)
            if self.world.navigation_obstacle(position, end, half_width):
                return 'hard'
        if not self.world.has_destructible_identities():
            return 'clear'
        contacts = self.world.hull_destructible_contacts(
            position, travel_yaw, half_width, half_length,
            abs(float(speed)) * float(dt) + 0.3)
        if not contacts:
            return 'clear'
        try:
            params = vehicle_physics.derive_params(descriptor)
            mass = float(params['mass'])
            cap_speed = float(params['speedBwd' if speed < 0.0
                                     else 'speedFwd'])
        except (AttributeError, KeyError, TypeError, ValueError):
            return 'hard'
        crushed = []
        blocked = False
        kinetic_hold = False
        for contact in contacts:
            instance = contact['instance']
            if self.world.crushable(instance, None, mass, speed):
                crushed.append(contact)
            elif self.world.crushable(instance, None, mass, cap_speed):
                kinetic_hold = True
            else:
                blocked = True
        if blocked:
            return 'hard'
        if kinetic_hold:
            return 'soft'
        column_blocked = False
        for contact in crushed:
            instance = contact['instance']
            kind = ('column' if instance.get('destr_type') == 'column'
                    else 'tree' if instance.get('destr_type') == 'tree'
                    else 'fragile')
            self.world.mark_destroyed(contact['signature'])
            self._report_destroyed(
                kind, instance, contact['position'], travel_yaw,
                abs(float(speed)), is_shot=False)
            if kind == 'column':
                column_blocked = True
        if column_blocked:
            return 'hard'
        return 'crushed' if crushed else (contact_clear or 'clear')

    def _report_destroyed(self, kind, instance, position, fall_yaw, speed,
                          is_shot, mat_kind=None):
        wire = instance.get('wire')
        if wire is None:
            return False
        message = {
            'type': 'destructible',
            'round_id': self._round_id,
            'destructible_kind': kind,
            'chunk_id': int(wire[0]),
            'item_index': int(wire[1]),
            'x': float(position[0]),
            'y': float(position[1]),
            'z': float(position[2]),
            'fall_yaw': float(fall_yaw),
            'speed': float(speed),
            'is_shot': bool(is_shot),
        }
        if mat_kind is not None:
            message['mat_kind'] = int(mat_kind)
        return self.state.report_destructible(SERVER_AUTHORITY_ID, message)

    # -- per-tick update -----------------------------------------------------

    def _now(self):
        return self._last_now

    def update(self, dt, now, live):
        """Advance the whole authority one server tick."""
        if not self._started or self._bots is None:
            return
        self._last_now = float(now)
        self._live = bool(live)
        with engine_modules(lambda: float(now)):
            self._artillery.advance(
                now, ARTILLERY_ARC_RAYS_PER_TICK, self.world.arc_probe)
            if self._live:
                outgoing = self._bots.update(
                    dt, now, players=self._players_payload())
                for message in outgoing:
                    self._route(message, now)
            if self._projectiles is not None and len(self._projectiles):
                self._projectiles.advance(
                    now, self._projectile_chord, self._projectile_terminal,
                    maximum_chords=PROJECTILE_CHORDS_PER_TICK)
                self._flush_progress(now)

    def _players_payload(self):
        rows = []
        for player in self.state.players.values():
            if not player.connected or not player.participating:
                continue
            rows.append({
                'id': int(player.player_id),
                'name': player.name,
                'vehicle': player.vehicle,
                'team': int(player.team),
                'x': float(player.x), 'y': float(player.y),
                'z': float(player.z), 'yaw': float(player.yaw),
                'speed': float(player.speed),
                'alive': bool(player.alive),
                'health': int(player.health),
                'max_health': int(player.max_health),
                'aim_yaw': float(player.aim_yaw),
                'gun_pitch': float(player.gun_pitch),
                'world_pose': bool(player.client_position),
            })
        return rows

    # -- outgoing message routing --------------------------------------------

    def _route(self, message, now):
        kind = message.get('type')
        payload = dict(message)
        payload['round_id'] = self._round_id
        if kind == 'bot_manifest':
            self.state.update_bot_manifest(SERVER_AUTHORITY_ID, payload)
        elif kind == 'bot_state':
            self._resolve_bot_fire(message, now)
            self.state.update_bot_states(SERVER_AUTHORITY_ID, payload)
        elif kind == 'bot_observation':
            self.state.update_bot_observation(SERVER_AUTHORITY_ID, payload)
        elif kind == 'bot_ram':
            self.state.report_bot_ram(SERVER_AUTHORITY_ID, payload)
        elif kind == 'bot_human_hit':
            self.state.report_bot_human_hit(SERVER_AUTHORITY_ID, payload)

    # -- bot projectiles -------------------------------------------------------

    def _resolve_bot_fire(self, message, now):
        for state in message.get('bots') or ():
            try:
                bot_id = int(state.get('id'))
                fire_seq = int(state.get('fire_seq', 0))
            except (TypeError, ValueError):
                continue
            previous = self._fire_seen.get(bot_id, 0)
            if (fire_seq > previous and
                    self._launch_bot_projectile(state, fire_seq, now)):
                self._fire_seen[bot_id] = fire_seq

    def _launch_bot_projectile(self, state, shot_seq, now):
        try:
            bot_id = int(state.get('id'))
            shot_yaw = float(state.get('shot_yaw'))
            shot_pitch = float(state.get('shot_pitch'))
        except (TypeError, ValueError):
            return False
        descriptor = self._bots._descriptors.get(bot_id)
        if descriptor is None:
            return False
        shell_index = max(0, int(state.get('shell_index', 0) or 0))
        shot = _descriptor_shot(descriptor, shell_index)
        speed = _number(_field(shot, 'speed'), -1.0)
        gravity = _number(_field(shot, 'gravity'), -1.0)
        maximum = _number(_field(shot, 'maxDistance'), -1.0)
        if speed <= 0.0 or gravity <= 0.0 or maximum <= 0.0:
            return False
        profile = state.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        max_time_ms = PROJECTILE_MAX_TIME_MS
        if str(profile.get('class_tag') or '') == 'SPG':
            try:
                origin = tuple(float(value)
                               for value in state['shot_origin'])
                velocity = tuple(float(value)
                                 for value in state['shot_velocity'])
                gravity = float(state['shot_gravity'])
                maximum = float(state['shot_max_distance'])
                max_time_ms = int(state['shot_max_time_ms'])
            except (KeyError, TypeError, ValueError):
                return False
        else:
            origin = _muzzle_origin(state, descriptor,
                                    shot_yaw=shot_yaw)
            if origin is None:
                return False
            cosine = math.cos(shot_pitch)
            velocity = (math.sin(shot_yaw) * cosine * speed,
                        math.sin(shot_pitch) * speed,
                        math.cos(shot_yaw) * cosine * speed)
        is_he = combat_rules.is_he(shot)
        message = {
            'type': 'projectile_launch',
            'round_id': self._round_id,
            'shooter_kind': 'bot',
            'shooter_id': bot_id,
            'shot_seq': int(shot_seq),
            'shell_index': shell_index,
            'origin': [float(origin[0]), float(origin[1]),
                       float(origin[2])],
            'velocity': [float(velocity[0]), float(velocity[1]),
                         float(velocity[2])],
            'gravity': float(gravity),
            'max_distance': float(maximum),
            'max_time_ms': int(max_time_ms),
            'is_he': bool(is_he),
            'splash_radius': float(combat_rules.he_radius(shot)
                                   if is_he else 0.0),
            'penetration_factor': float(
                combat_rules.sample_penetration_factor()),
            'authority_epoch': int(self.state.authority_epoch),
        }
        accepted = self.state.launch_projectile(SERVER_AUTHORITY_ID, message)
        if not accepted:
            return False
        wire_id = '%d:b:%d:%d' % (int(self._round_id), bot_id,
                                  int(shot_seq))
        self._progress_cursors[wire_id] = 0
        self._projectiles.launch(
            wire_id,
            origin, velocity, (0.0, -float(gravity), 0.0),
            float(now), max_time_ms / 1000.0, float(maximum),
            payload={'shooter_kind': 'bot', 'shooter_id': bot_id,
                     'shot_seq': int(shot_seq),
                     'shell_index': shell_index,
                     'penetration_factor':
                         message['penetration_factor'],
                     'is_he': bool(is_he),
                     'splash_radius': message['splash_radius']})
        return True

    def _projectile_chord(self, state, start, end, absolute_start,
                          absolute_end):
        meta = state.get('payload') or {}
        static_fraction = self.world.segment_hit_fraction(
            start, end, include_destructibles=False)
        nearest = None
        for target in self._chord_targets(meta):
            entry = _segment_hull_entry(start, end, target)
            if entry is None:
                continue
            if nearest is None or entry['fraction'] < nearest['fraction']:
                nearest = entry
        limit = min(value for value in (
            static_fraction,
            nearest['fraction'] if nearest is not None else None,
            1.0) if value is not None)
        stop = self._traverse_shot_destructibles(
            meta, state, start, end, limit)
        if stop is not None:
            return stop
        if static_fraction is not None and (
                nearest is None or static_fraction <= nearest['fraction']):
            return {'outcome': 'impact', 'fraction': static_fraction,
                    'world': True}
        if nearest is None:
            return None
        return {'outcome': 'impact', 'fraction': nearest['fraction'],
                'target': nearest}

    def _traverse_shot_destructibles(self, meta, state, start, end, limit):
        """Destroy and pierce catalog items on one chord, retail-law style."""
        if not self.world.has_destructible_identities():
            return None
        wire_id = self._wire_projectile_id(meta)
        shooter_descriptor = self._bots._descriptors.get(
            int(meta.get('shooter_id', -1)))
        if shooter_descriptor is None:
            return None
        shot = _descriptor_shot(shooter_descriptor,
                                meta.get('shell_index'))
        shell_kind = str(_field(_field(shot, 'shell', {}) or {},
                                'kind', '') or '')
        chord = tuple(float(end[index]) - float(start[index])
                      for index in range(3))
        chord_length = math.sqrt(sum(value * value for value in chord))
        if chord_length <= 1.0e-9:
            return None
        fall_yaw = math.atan2(chord[0], chord[2])
        for hit in self.world.destructibles_on_segment(start, end):
            if hit['fraction'] >= limit:
                break
            instance = hit['instance']
            mat_kind = hit['mat_kind']
            if instance['kind'] == 'structure':
                event_kind = 'module'
                reference = (instance.get('modules') or {}).get(
                    int(mat_kind) if mat_kind is not None else None)
                reference = reference[0] if reference else None
                self.world.mark_destroyed(hit['signature'], mat_kind)
            else:
                event_kind = (
                    'tree' if instance.get('destr_type') == 'tree' else
                    'column' if instance.get('destr_type') == 'column'
                    else 'fragile')
                reference = instance.get('scaled_health')
                self.world.mark_destroyed(hit['signature'])
            entry_point = tuple(
                float(start[index]) + chord[index] * hit['fraction']
                for index in range(3))
            receipt = {
                'destructible_kind': event_kind,
                'chunk_id': int(instance['wire'][0]),
                'item_index': int(instance['wire'][1]),
                'x': float(entry_point[0]),
                'y': float(entry_point[1]),
                'z': float(entry_point[2]),
                'fall_yaw': float(fall_yaw),
                'speed': 12.0,
                'is_shot': True,
            }
            if event_kind == 'module':
                receipt['mat_kind'] = int(mat_kind)
            if instance.get('wire') is not None:
                self._shot_receipts.setdefault(wire_id, []).append(receipt)
            can_continue = (
                shell_kind in _SHOT_AP_KINDS_1513 and
                reference is not None and
                float(reference) <= _SHOT_THROUGH_MAX_HP_1513)
            if not can_continue:
                return {'outcome': 'impact', 'fraction': hit['fraction'],
                        'world': True}
            loss = (self._piercing_loss.get(wire_id, 0.0) +
                    _SHOT_THROUGH_MIN_REDUCTION_1513)
            self._piercing_loss[wire_id] = loss
            entry_distance = (float(state.get('distance', 0.0)) +
                              chord_length * hit['fraction'])
            factor = float(meta.get('penetration_factor', 1.0))
            if combat_rules.sampled_piercing(
                    shot, entry_distance, factor, loss) < 1.0:
                return {'outcome': 'impact', 'fraction': hit['fraction'],
                        'world': True}
        return None

    def _chord_targets(self, meta, include_shooter=False):
        shooter_key = ('%s:%s' % (meta.get('shooter_kind'),
                                  meta.get('shooter_id')))
        if include_shooter:
            shooter_key = None
        for player in self.state.players.values():
            if (not player.connected or not player.participating or
                    not player.alive or not player.client_position):
                continue
            key = 'player:%s' % player.player_id
            if key == shooter_key:
                continue
            descriptor = self.descriptors.get(player.vehicle)
            if descriptor is None:
                continue
            yield {
                'kind': 'player', 'id': int(player.player_id),
                'position': (player.x, player.y, player.z),
                'yaw': float(player.yaw), 'descriptor': descriptor,
                'health': int(player.health),
                'state': {'critical': dict(player.critical or {})},
            }
        for bot_id, state in self.state.bot_states.items():
            if not state.get('alive'):
                continue
            key = 'bot:%s' % bot_id
            if key == shooter_key:
                continue
            descriptor = self._bots._descriptors.get(int(bot_id))
            if descriptor is None:
                continue
            yield {
                'kind': 'bot', 'id': int(bot_id),
                'position': (float(state.get('x', 0.0)),
                             float(state.get('y', 0.0)),
                             float(state.get('z', 0.0))),
                'yaw': float(state.get('yaw', 0.0)),
                'descriptor': descriptor,
                'health': int(state.get('health', 0)),
                'state': state,
            }

    def _projectile_terminal(self, state, terminal):
        meta = state.get('payload') or {}
        if terminal.get('outcome') == 'impact':
            outcome = 'impact'
        elif terminal.get('reason') == 'max_distance':
            outcome = 'miss'
        else:
            outcome = 'expired'
        impact = tuple(state.get('position') or (0.0, 0.0, 0.0))
        direct = None
        target = terminal.get('target')
        if outcome == 'impact' and target is not None:
            direct = self._direct_effect(meta, state, target)
        splash = []
        if outcome == 'impact' and meta.get('is_he'):
            splash = self._splash_effects(meta, impact, direct)
        wire_id = self._wire_projectile_id(meta)
        elapsed_ms = int(round(float(state.get('elapsed', 0.0)) * 1000.0))
        base = int(self._progress_cursors.get(wire_id, 0))
        message = {
            'type': 'projectile_resolve',
            'round_id': self._round_id,
            'authority_epoch': int(self.state.authority_epoch),
            'projectile_id': wire_id,
            'base_checked_ms': base,
            'outcome': outcome,
            'resolved_time_ms': max(base, elapsed_ms),
            'checked_distance': float(state.get('distance', 0.0)),
            'piercing_loss': float(self._piercing_loss.get(wire_id, 0.0)),
            'penetration_factor': float(
                meta.get('penetration_factor', 1.0)),
            'impact': [float(impact[0]), float(impact[1]),
                       float(impact[2])],
            'direct': direct,
            'splash': splash,
            'destructibles': self._shot_receipts.pop(wire_id, []),
        }
        self.state.resolve_projectile(SERVER_AUTHORITY_ID, message)
        self._progress_cursors.pop(wire_id, None)
        self._piercing_loss.pop(wire_id, None)

    def _splash_effects(self, meta, impact, direct):
        """Splash damage from the copied HE law over donated hull armor."""
        shooter_descriptor = self._bots._descriptors.get(
            int(meta.get('shooter_id', -1)))
        if shooter_descriptor is None:
            return []
        shot = _descriptor_shot(shooter_descriptor,
                                meta.get('shell_index'))
        radius = combat_rules.he_radius(shot)
        if radius <= 0.0:
            return []
        shell = (combat_rules.legacy_shot(shot).get('shell') or {})
        effects = []
        for target in self._chord_targets(meta, include_shooter=True):
            if (direct is not None and
                    target['kind'] == direct['target_kind'] and
                    int(target['id']) == int(direct['target_id'])):
                continue
            position = target['position']
            distance = math.sqrt(sum(
                (float(position[index]) - float(impact[index])) ** 2
                for index in range(3)))
            if distance > radius:
                continue
            nominal = combat_rules.he_hull_armor(target['descriptor'])
            damage = combat_rules.he_splash_damage(
                shot, nominal, distance / radius)
            if damage <= 0:
                continue
            hull_damage = damage
            mock = _TargetMock(
                target['id'], target['health'], target['descriptor'],
                position, target['yaw'], target.get('state') or {})
            damage, critical = critical_damage.propose_direct(
                mock, combat_rules.collision_layers(()),
                Vector3(*impact), Vector3(*position), damage, shell,
                int(meta.get('shooter_id', 0)), penetrated=False,
                by_explosion=True)
            effect = {
                'target_kind': target['kind'],
                'target_id': int(target['id']),
                'damage': int(damage),
                'shot_result': 2,
                'x': float(impact[0]), 'y': float(impact[1]),
                'z': float(impact[2]),
            }
            if isinstance(critical, dict):
                contract = self._critical_contract(target)
                if contract is not None:
                    effect['critical'] = critical
                    effect['hull_damage'] = int(hull_damage)
                    effect.update(contract)
            effects.append(effect)
        return effects

    def _wire_projectile_id(self, meta):
        return '%d:b:%d:%d' % (
            int(self._round_id), int(meta.get('shooter_id', 0)),
            int(meta.get('shot_seq', 0)))

    def _direct_effect(self, meta, state, target):
        shooter_descriptor = self._bots._descriptors.get(
            int(meta.get('shooter_id', -1)))
        if shooter_descriptor is None:
            return None
        shot = _descriptor_shot(shooter_descriptor,
                                meta.get('shell_index'))
        collisions = target['collisions']
        distance = float(state.get('distance', 0.0))
        resolved = combat_rules.resolve_hull_hit(
            shot, distance, collisions,
            pierce_loss=float(self._piercing_loss.get(
                self._wire_projectile_id(meta), 0.0)),
            penetration_factor=meta.get('penetration_factor'))
        if resolved is None:
            return None
        result = resolved[0]
        armor = combat_rules.he_nominal_armor(collisions)
        damage = combat_rules.damage(shot, result, armor)
        hull_damage = damage
        mock = _TargetMock(
            target['id'], target['health'], target['descriptor'],
            target['position'], target['yaw'],
            target.get('state') or {})
        shell = (combat_rules.legacy_shot(shot).get('shell') or {})
        damage, critical = critical_damage.propose_direct(
            mock, combat_rules.collision_layers(collisions),
            Vector3(*target['ray_start']), Vector3(*target['ray_end']),
            damage, shell, int(meta.get('shooter_id', 0)),
            penetrated=int(result) == 2)
        effect = {
            'target_kind': target['kind'],
            'target_id': int(target['id']),
            'damage': int(damage),
            'shot_result': int(result),
            'x': float(state['position'][0]),
            'y': float(state['position'][1]),
            'z': float(state['position'][2]),
        }
        if isinstance(critical, dict):
            contract = self._critical_contract(target)
            if contract is not None:
                effect['critical'] = critical
                effect['hull_damage'] = int(hull_damage)
                effect.update(contract)
        return effect

    def _critical_contract(self, target):
        if target['kind'] == 'bot':
            state = self.state.bot_states.get(int(target['id'])) or {}
            base = state.get('combat_base_revision')
            ack = state.get('combat_ack_seq')
        else:
            player = self.state.players.get(int(target['id']))
            if player is None:
                return None
            base = player.critical_report_base_revision
            ack = player.critical_ack_seq
        try:
            return {
                'critical_target_base_revision': int(base),
                'critical_target_ack_seq': int(ack),
            }
        except (TypeError, ValueError):
            return None

    def _flush_progress(self, now):
        if self._projectiles is None:
            return
        cursors = []
        advanced = []
        for state in self._projectiles.snapshot():
            meta = state.get('payload') or {}
            wire_id = self._wire_projectile_id(meta)
            base = int(self._progress_cursors.get(wire_id, 0))
            checked = int(round(float(state.get('elapsed', 0.0)) * 1000.0))
            if checked <= base:
                continue
            cursors.append({
                'projectile_id': wire_id,
                'base_checked_ms': base,
                'checked_through_ms': checked,
                'checked_distance': float(state.get('distance', 0.0)),
                'piercing_loss': float(
                    self._piercing_loss.get(wire_id, 0.0)),
                'penetration_factor': float(
                    meta.get('penetration_factor', 1.0)),
                'destructibles': self._shot_receipts.pop(wire_id, []),
            })
            advanced.append((wire_id, checked))
        for index in range(0, len(cursors), PROJECTILE_PROGRESS_BATCH):
            batch = cursors[index:index + PROJECTILE_PROGRESS_BATCH]
            accepted = self.state.progress_projectiles(SERVER_AUTHORITY_ID, {
                'type': 'projectile_progress',
                'round_id': self._round_id,
                'authority_epoch': int(self.state.authority_epoch),
                'cursors': batch,
            })
            if accepted:
                for wire_id, checked in advanced[
                        index:index + PROJECTILE_PROGRESS_BATCH]:
                    self._progress_cursors[wire_id] = checked


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    try:
        return getattr(value, name)
    except AttributeError:
        try:
            return value[name]
        except (TypeError, KeyError, IndexError):
            return default


def _number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) else float(default)


def _descriptor_shot(descriptor, shell_index=None):
    shots = tuple(_field(_field(descriptor, 'gun', {}), 'shots', ()) or ())
    if shell_index is None:
        shell_index = 0
    index = max(0, min(int(shell_index), max(0, len(shots) - 1)))
    return shots[index] if shots else {}


def _muzzle_origin(state, descriptor, shot_yaw=None):
    """Approximate the muzzle from donated mount offsets and the pose."""
    try:
        x = float(state.get('x'))
        y = float(state.get('y'))
        z = float(state.get('z'))
    except (TypeError, ValueError):
        return None
    height = 1.5
    forward = 2.0
    try:
        hull = _field(descriptor, 'hull', {})
        turret_positions = _field(hull, 'turretPositions', None)
        if turret_positions:
            height = float(turret_positions[0][1]) + 0.6
    except (TypeError, ValueError, IndexError):
        pass
    yaw = float(shot_yaw if shot_yaw is not None
                else state.get('yaw', 0.0) or 0.0)
    return (x + math.sin(yaw) * forward, y + height,
            z + math.cos(yaw) * forward)


def _fraction_along(start, end, point):
    dx = float(end[0]) - float(start[0])
    dy = float(end[1]) - float(start[1])
    dz = float(end[2]) - float(start[2])
    length_sq = dx * dx + dy * dy + dz * dz
    if length_sq <= 1.0e-12:
        return 0.0
    px = float(point[0]) - float(start[0])
    py = float(point[1]) - float(start[1])
    pz = float(point[2]) - float(start[2])
    return max(0.0, min(1.0, (px * dx + py * dy + pz * dz) / length_sq))


class _SyntheticMatInfo(object):
    __slots__ = ('armor', 'vehicleDamageFactor')

    def __init__(self, armor):
        self.armor = float(armor)
        self.vehicleDamageFactor = 1.0


class _SyntheticCollision(object):
    __slots__ = ('dist', 'hitAngleCos', 'matInfo', 'compName')

    def __init__(self, dist, hit_angle_cos, armor, comp_name):
        self.dist = float(dist)
        self.hitAngleCos = float(hit_angle_cos)
        self.matInfo = _SyntheticMatInfo(armor)
        self.compName = comp_name


def _segment_hull_entry(start, end, target):
    """Pure narrow-phase: chord versus the target's hull box and armor."""
    descriptor = target['descriptor']
    try:
        bbox = server_world._descriptor_hull_bbox(descriptor)
        minimum, maximum = bbox[0], bbox[1]
    except (ValueError, TypeError, IndexError, AttributeError):
        return None
    position = target['position']
    yaw = float(target['yaw'])
    sine, cosine = math.sin(yaw), math.cos(yaw)
    center_local = ((float(minimum[0]) + float(maximum[0])) * 0.5,
                    (float(minimum[1]) + float(maximum[1])) * 0.5,
                    (float(minimum[2]) + float(maximum[2])) * 0.5)
    half = ((float(maximum[0]) - float(minimum[0])) * 0.5,
            (float(maximum[1]) - float(minimum[1])) * 0.5,
            (float(maximum[2]) - float(minimum[2])) * 0.5)
    center_world = (
        float(position[0]) + cosine * center_local[0] + sine * center_local[2],
        float(position[1]) + center_local[1],
        float(position[2]) - sine * center_local[0] + cosine * center_local[2])
    axes = (
        ((cosine, 0.0, -sine)),
        ((0.0, 1.0, 0.0)),
        ((sine, 0.0, cosine)))
    world_box = (center_world,
                 tuple(tuple(axis[index] * half[row] for index in range(3))
                       for row, axis in enumerate(axes)),
                 None)
    fraction = server_world._segment_obb_entry(start, end, world_box)
    if fraction is None or fraction >= 1.0:
        return None
    hit = tuple(float(start[index]) +
                (float(end[index]) - float(start[index])) * fraction
                for index in range(3))
    local = _world_to_local(hit, position, yaw)
    face, normal_local = _dominant_face(local, half, center_local)
    armor = _armor_for_face(descriptor, face)
    direction = tuple(float(end[index]) - float(start[index])
                      for index in range(3))
    length = math.sqrt(sum(value * value for value in direction))
    if length <= 1.0e-9:
        return None
    direction = tuple(value / length for value in direction)
    normal_world = _local_vector_to_world(normal_local, yaw)
    hit_angle_cos = abs(sum(direction[index] * normal_world[index]
                            for index in range(3)))
    collision = _SyntheticCollision(
        fraction * length, hit_angle_cos, armor, 'hull')
    return {
        'fraction': fraction,
        'kind': target['kind'],
        'id': target['id'],
        'health': target['health'],
        'descriptor': descriptor,
        'position': position,
        'yaw': yaw,
        'state': target.get('state') or {},
        'collisions': (collision,),
        'ray_start': tuple(float(value) for value in start),
        'ray_end': tuple(float(value) for value in end),
    }


def _world_to_local(point, position, yaw):
    dx = float(point[0]) - float(position[0])
    dy = float(point[1]) - float(position[1])
    dz = float(point[2]) - float(position[2])
    sine, cosine = math.sin(yaw), math.cos(yaw)
    return (cosine * dx - sine * dz, dy, sine * dx + cosine * dz)


def _local_vector_to_world(vector, yaw):
    sine, cosine = math.sin(yaw), math.cos(yaw)
    return (cosine * vector[0] + sine * vector[2],
            vector[1],
            -sine * vector[0] + cosine * vector[2])


def _dominant_face(local, half, center_local):
    offsets = (
        (local[0] - center_local[0]) / max(half[0], 1.0e-6),
        (local[1] - center_local[1]) / max(half[1], 1.0e-6),
        (local[2] - center_local[2]) / max(half[2], 1.0e-6))
    axis = max(range(3), key=lambda index: abs(offsets[index]))
    sign = 1.0 if offsets[axis] >= 0.0 else -1.0
    if axis == 2:
        face = 'front' if sign > 0.0 else 'rear'
    elif axis == 0:
        face = 'side'
    else:
        face = 'top'
    normal = [0.0, 0.0, 0.0]
    normal[axis] = sign
    return face, tuple(normal)


def _vehicle_excluded(profile):
    tags = profile.get('tags', ()) or ()
    if 'secret' in tags:
        return True
    return profile.get('name') == 'usa:T23'


def _vehicle_class_order(profile):
    tags = profile.get('tags', ()) or ()
    for tag, order in (('heavyTank', 0), ('mediumTank', 1),
                       ('AT-SPG', 2), ('lightTank', 3), ('SPG', 4)):
        if tag in tags:
            return order
    return 1


def _armor_for_face(descriptor, face):
    armor = _field(descriptor, 'primaryArmor', None)
    if armor is None:
        armor = _field(_field(descriptor, 'hull', {}), 'primaryArmor',
                       (40.0, 30.0, 20.0))
    try:
        front, side, rear = (float(armor[0]), float(armor[1]),
                             float(armor[2]))
    except (TypeError, ValueError, IndexError):
        front = side = rear = 30.0
    if face == 'front':
        return front
    if face == 'rear':
        return rear
    if face == 'top':
        return min(side, rear) * 0.5
    return side
