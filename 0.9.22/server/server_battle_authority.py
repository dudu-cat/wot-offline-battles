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
import time
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
PROJECTILE_MAX_TIME_MS = 20000
PROJECTILE_PROGRESS_BATCH = 30
RAM_BOT_HISTORY_SAMPLES = 512


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

    @classmethod
    def from_pose(cls, yaw, pitch, roll, position):
        matrix = cls()
        matrix._rows = [list(axis) for axis in _pose_axes(
            float(yaw), float(pitch), float(roll))]
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


class _ComponentTransform(object):
    """Vehicle-local to one donated component's current local space."""

    __slots__ = ('_hull', '_turret', '_gun', '_turret_yaw', '_gun_pitch',
                 '_stage')

    def __init__(self, hull, turret, gun, turret_yaw, gun_pitch, stage):
        self._hull = hull
        self._turret = turret
        self._gun = gun
        self._turret_yaw = float(turret_yaw)
        self._gun_pitch = float(gun_pitch)
        self._stage = str(stage)

    @staticmethod
    def _subtract(point, offset):
        return Vector3(point.x - offset.x, point.y - offset.y,
                       point.z - offset.z)

    @staticmethod
    def _rotate_y_inverse(point, angle):
        sine, cosine = math.sin(angle), math.cos(angle)
        return Vector3(cosine * point.x - sine * point.z, point.y,
                       sine * point.x + cosine * point.z)

    @staticmethod
    def _rotate_x_inverse(point, angle):
        sine, cosine = math.sin(angle), math.cos(angle)
        return Vector3(point.x, cosine * point.y + sine * point.z,
                       -sine * point.y + cosine * point.z)

    def applyPoint(self, point):
        point = Vector3(point)
        if self._stage == 'chassis':
            return point
        point = self._subtract(point, self._hull)
        if self._stage == 'hull':
            return point
        point = self._subtract(point, self._turret)
        point = self._rotate_y_inverse(point, self._turret_yaw)
        if self._stage == 'turret':
            return point
        point = self._subtract(point, self._gun)
        return self._rotate_x_inverse(point, self._gun_pitch)


def _component_offset(value):
    try:
        return Vector3(value)
    except (TypeError, ValueError, IndexError):
        return Vector3()


def _target_components(descriptor, hull_yaw, aim_yaw, gun_pitch):
    """Rebuild the four #1513 parent transforms from donated mount offsets.

    These transforms place the adopted profile boxes. They deliberately do
    not claim or synthesize native armor/material collision layers.
    """
    chassis = _field(descriptor, 'chassis', None)
    hull = _field(descriptor, 'hull', None)
    turret = _field(descriptor, 'turret', None)
    gun = _field(descriptor, 'gun', None)
    hull_offset = _component_offset(
        _field(chassis, 'hullPosition', (0.0, 0.0, 0.0)))
    turret_positions = _field(hull, 'turretPositions', ()) or ()
    turret_offset = _component_offset(
        turret_positions[0] if turret_positions else (0.0, 0.0, 0.0))
    gun_offset = _component_offset(
        _field(turret, 'gunPosition', (0.0, 0.0, 0.0)))
    relative_yaw = ((float(aim_yaw) - float(hull_yaw) + math.pi) %
                    (2.0 * math.pi)) - math.pi
    result = []
    for name, component in (('chassis', chassis), ('hull', hull),
                            ('turret', turret), ('gun', gun)):
        if component is None:
            continue
        result.append((component, _ComponentTransform(
            hull_offset, turret_offset, gun_offset, relative_yaw,
            gun_pitch, name)))
    return tuple(result)


class _TargetMock(object):
    """Detached target state for the copied crit law's proposal path."""

    def __init__(self, identity, health, descriptor, position, yaw,
                 combat_state, aim_yaw=None, gun_pitch=0.0, pitch=0.0,
                 roll=0.0):
        self.id = identity
        self.health = int(health)
        self.typeDescriptor = descriptor
        self.position = Vector3(*position)
        self.matrix = Matrix.from_pose(yaw, pitch, roll, position)
        critical = combat_state.get('critical') or {}
        self.devices_hp = {}
        self._destroyed_devices = set(
            str(name) for name in critical.get('destroyed') or ())
        self._critical_devices = set()
        for record in critical.get('devices') or ():
            if not isinstance(record, dict) or not record.get('name'):
                continue
            name = str(record['name'])
            try:
                self.devices_hp[name] = max(
                    0.0, float(record.get('hp', 0.0)))
            except (TypeError, ValueError):
                continue
            state = str(record.get('state') or '')
            if state == 'destroyed':
                self._destroyed_devices.add(name)
            elif state == 'critical':
                self._critical_devices.add(name)
        self._critical_devices.difference_update(self._destroyed_devices)
        self._crew_ko = set(critical.get('crew_ko') or ())
        self.is_on_fire = bool(critical.get('fire', False))
        self._ammo_rack_death = bool(
            critical.get('ammo_rack_death', False))
        self._offline_proposal_only = True
        self._components = _target_components(
            descriptor, yaw, yaw if aim_yaw is None else aim_yaw,
            gun_pitch)

    def getComponents(self):
        return self._components


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
        self._projectile_launches = {}
        self._pending_resolutions = {}
        self._assignments = {}
        self._required_names = ()
        self._last_now = 0.0
        self._ram_bot_history = {}
        self._ram_bot_history_order = []
        self._ram_bot_history_times = {}

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
        self._projectile_launches = {}
        self._pending_resolutions = {}
        self._ram_bot_history = {}
        self._ram_bot_history_order = []
        self._ram_bot_history_times = {}
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

    def launch_player_intent(self, intent):
        """Turn one admitted player trigger into a server-owned projectile."""
        if not self._started or not isinstance(intent, dict):
            return False
        try:
            player_id = int(intent['player_id'])
            intent_seq = int(intent['intent_seq'])
            input_seq = int(intent['input_seq'])
            shot_seq = int(intent['shot_seq'])
            shell_index = int(intent['shell_index'])
            aim_yaw = float(intent['aim_yaw'])
            # BigWorld gun pitch is negative-up. The projectile protocol is
            # positive-up, matching the hidden native worker's launch vector.
            shot_pitch = -float(intent['gun_pitch'])
            player = self.state.players[player_id]
            descriptor = self.descriptors.get(player.vehicle)
            shot = _descriptor_shot(descriptor, shell_index)
            speed = _number(_field(shot, 'speed'), -1.0)
            gravity = _number(_field(shot, 'gravity'), -1.0)
            maximum = _number(_field(shot, 'maxDistance'), -1.0)
            origin = _muzzle_origin(intent, descriptor, aim_yaw)
            if (descriptor is None or origin is None or speed <= 0.0 or
                    gravity <= 0.0 or maximum <= 0.0 or
                    not all(math.isfinite(value) for value in (
                        aim_yaw, shot_pitch))):
                return False
            horizontal = math.cos(shot_pitch)
            velocity = (
                math.sin(aim_yaw) * horizontal * speed,
                math.sin(shot_pitch) * speed,
                math.cos(aim_yaw) * horizontal * speed,
            )
            is_he = combat_rules.is_he(shot)
            message = {
                'type': 'projectile_launch',
                'round_id': int(self._round_id),
                'shooter_kind': 'player',
                'shooter_id': player_id,
                'shot_seq': shot_seq,
                'shell_index': shell_index,
                'origin': list(origin),
                'velocity': list(velocity),
                'gravity': gravity,
                'max_distance': maximum,
                'max_time_ms': PROJECTILE_MAX_TIME_MS,
                'is_he': bool(is_he),
                'splash_radius': float(
                    combat_rules.he_radius(shot) if is_he else 0.0),
                'penetration_factor': float(
                    combat_rules.sample_penetration_factor()),
                'source_shot': _source_shot_from_descriptor(shot),
                'authority_epoch': int(self.state.authority_epoch),
                'fire_intent_seq': intent_seq,
                'fire_input_seq': input_seq,
            }
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        return bool(self.state.launch_projectile(
            SERVER_AUTHORITY_ID, message))

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
            self._remember_ram_bot_snapshot(message)
        if self._projectiles is None or not isinstance(message, dict):
            return
        rows = message.get('projectiles')
        if not isinstance(rows, (list, tuple)):
            return
        try:
            now = float(message.get('server_time_ms', 0)) / 1000.0
        except (TypeError, ValueError, OverflowError):
            return
        if now >= self._projectiles.now:
            self._projectiles.advance(
                now, self._projectile_chord, self._projectile_terminal,
                maximum_chords=0)
        self._reconcile_projectiles(rows)

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
            return ()
        self._last_now = float(now)
        self._live = bool(live)
        observation_relays = []
        with engine_modules(lambda: float(now)):
            if self._projectiles is not None:
                # Move the manager clock before restoring launches admitted at
                # this server tick. InFlightProjectiles rejects future launch
                # times even when no projectile is active yet.
                self._projectiles.advance(
                    now, self._projectile_chord, self._projectile_terminal,
                    maximum_chords=0)
                self._reconcile_projectiles(
                    self.state._projectile_snapshot())
                self._flush_pending_resolutions()
            self._artillery.advance(
                now, ARTILLERY_ARC_RAYS_PER_TICK, self.world.arc_probe)
            if self._live:
                outgoing = self._bots.update(
                    dt, now, players=self._players_payload())
                for message in outgoing:
                    relay = self._route(message, now)
                    if isinstance(relay, dict):
                        observation_relays.append(relay)
            if self._projectiles is not None:
                # Bot launches enter the same canonical BattleState ledger as
                # human launches. Reconcile again after BotRuntime publishes
                # this tick's fire edges so both shooter kinds share one path.
                self._reconcile_projectiles(
                    self.state._projectile_snapshot())
            if self._projectiles is not None and len(self._projectiles):
                self._projectiles.advance(
                    now, self._projectile_chord, self._projectile_terminal,
                    maximum_chords=PROJECTILE_CHORDS_PER_TICK)
                self._flush_progress(now)
            self._flush_pending_resolutions()
        return tuple(observation_relays)

    def _players_payload(self):
        rows = []
        for player in self.state.players.values():
            if not player.connected or not player.participating:
                continue
            public = self.state._public_player(
                player, include_outfits=False)
            rows.append(self._decorate_ram_contacts(public))
        return rows

    def _remember_ram_bot_snapshot(self, snapshot):
        """Retain canonical bot bodies referenced by human contact proofs."""
        if not isinstance(snapshot, dict) or self._bots is None:
            return False
        try:
            revision = int(snapshot.get('bot_state_revision'))
            sample_time_us = int(snapshot.get('bot_state_time_us'))
        except (TypeError, ValueError, OverflowError):
            return False
        if revision < 0 or sample_time_us < 0:
            return False
        states = {}
        current = getattr(self._bots, 'states', {}) or {}
        for raw in snapshot.get('bots') or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                bot_id = int(raw['id'])
            except (TypeError, ValueError, OverflowError):
                continue
            state = {}
            current_state = current.get(bot_id)
            if isinstance(current_state, dict):
                for name in ('mass', 'collision_shape', 'vehicle', 'team'):
                    if name in current_state:
                        state[name] = current_state[name]
            state.update(raw)
            states[bot_id] = state
        if revision not in self._ram_bot_history:
            self._ram_bot_history_order.append(revision)
        self._ram_bot_history[revision] = states
        self._ram_bot_history_times[revision] = sample_time_us
        while len(self._ram_bot_history_order) > RAM_BOT_HISTORY_SAMPLES:
            expired = self._ram_bot_history_order.pop(0)
            self._ram_bot_history.pop(expired, None)
            self._ram_bot_history_times.pop(expired, None)
        return True

    def _ram_bot_state_at(self, bot_id, revision, sample_time_us):
        """Interpolate one presented bot pose from canonical wire samples."""
        try:
            bot_id = int(bot_id)
            revision = int(revision)
            sample_time_us = int(sample_time_us)
        except (TypeError, ValueError, OverflowError):
            return None
        samples = []
        for candidate_revision in self._ram_bot_history_order:
            candidate_time = self._ram_bot_history_times.get(
                candidate_revision)
            candidate_states = self._ram_bot_history.get(
                candidate_revision, {})
            state = candidate_states.get(bot_id)
            if candidate_time is None or not isinstance(state, dict):
                continue
            samples.append((candidate_time, candidate_revision, state))
        samples.sort(key=lambda value: (value[0], value[1]))
        if not samples:
            return None
        left = right = None
        for candidate in samples:
            if (candidate[0] <= sample_time_us and
                    candidate[1] <= revision):
                left = candidate
            if (candidate[0] >= sample_time_us and
                    candidate[1] >= revision):
                right = candidate
                break
        if left is None or right is None:
            return None
        left_time, unused_left_revision, left_state = left
        right_time, unused_right_revision, right_state = right
        if left_time == right_time:
            result = dict(left_state)
            result['ram_vx'] = 0.0
            result['ram_vz'] = 0.0
            index = samples.index(left)
            if len(samples) >= 2:
                before, after = ((samples[index - 1], left) if index > 0
                                 else (left, samples[index + 1]))
                span = float(after[0] - before[0]) / 1000000.0
                if span > 0.0:
                    result['ram_vx'] = (
                        _number(after[2].get('x')) -
                        _number(before[2].get('x'))) / span
                    result['ram_vz'] = (
                        _number(after[2].get('z')) -
                        _number(before[2].get('z'))) / span
            return result
        span_us = float(right_time - left_time)
        if span_us <= 0.0:
            return None
        progress = max(0.0, min(
            (sample_time_us - left_time) / span_us, 1.0))
        result = dict(left_state)
        for name in ('x', 'y', 'z', 'pitch', 'roll', 'aim_yaw',
                     'gun_pitch'):
            if name in left_state and name in right_state:
                result[name] = (_number(left_state.get(name)) +
                                (_number(right_state.get(name)) -
                                 _number(left_state.get(name))) * progress)
        if 'yaw' in left_state and 'yaw' in right_state:
            left_yaw = _number(left_state.get('yaw'))
            right_yaw = _number(right_state.get('yaw'))
            delta = math.atan2(
                math.sin(right_yaw - left_yaw),
                math.cos(right_yaw - left_yaw))
            result['yaw'] = left_yaw + delta * progress
        if progress >= 1.0:
            result['alive'] = bool(right_state.get('alive', True))
        result['ram_vx'] = (
            _number(right_state.get('x')) -
            _number(left_state.get('x'))) * 1000000.0 / span_us
        result['ram_vz'] = (
            _number(right_state.get('z')) -
            _number(left_state.get('z'))) * 1000000.0 / span_us
        return result

    def _decorate_ram_contacts(self, state):
        """Attach historical bot bodies to pending human contact proofs."""
        state = dict(state or {})
        contacts = state.get('ram_contacts')
        if not isinstance(contacts, list):
            legacy = state.get('ram_contact')
            contacts = [legacy] if isinstance(legacy, dict) else []
        decorated = []
        for raw_receipt in contacts:
            if not isinstance(raw_receipt, dict):
                continue
            receipt = dict(raw_receipt)
            try:
                revision = int(receipt.get('bot_state_revision'))
                bot_id = int(receipt.get('bot_id'))
                presentation_time_us = int(
                    receipt.get('presentation_time_us'))
            except (TypeError, ValueError, OverflowError):
                revision = bot_id = presentation_time_us = None
            bot_state = self._ram_bot_state_at(
                bot_id, revision, presentation_time_us)
            if bot_state is not None:
                receipt['_ram_contact_bot_state'] = dict(bot_state)
            decorated.append(receipt)
        state['ram_contacts'] = decorated
        return state

    # -- outgoing message routing --------------------------------------------

    def _route(self, message, now):
        kind = message.get('type')
        payload = dict(message)
        payload['round_id'] = self._round_id
        if kind == 'bot_manifest':
            self.state.update_bot_manifest(SERVER_AUTHORITY_ID, payload)
        elif kind == 'bot_state':
            # The canonical ledger admits a bot projectile only after this
            # exact fire edge has entered ``bot_pending_projectile_launches``.
            # Resolving first left rapid clips perpetually one sequence behind:
            # every next round replaced the compact launch before its pending
            # edge could be consumed.
            if self.state.update_bot_states(SERVER_AUTHORITY_ID, payload):
                self._resolve_bot_fire(message, now)
        elif kind == 'bot_observation':
            relay = self.state.update_bot_observation(
                SERVER_AUTHORITY_ID, payload)
            return relay if isinstance(relay, dict) else None
        elif kind == 'bot_ram':
            self.state.report_bot_ram(SERVER_AUTHORITY_ID, payload)
        elif kind == 'bot_human_hit':
            self.state.report_bot_human_hit(SERVER_AUTHORITY_ID, payload)
        return None

    # -- bot projectiles -------------------------------------------------------

    def _resolve_bot_fire(self, message, now):
        for state in (message.get('launches') or message.get('bots') or ()):
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
        class_tag = state.get('class_tag', profile.get('class_tag'))
        max_time_ms = PROJECTILE_MAX_TIME_MS
        if str(class_tag or '') == 'SPG':
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
            try:
                origin = tuple(float(value)
                               for value in state['shot_origin'])
            except (KeyError, TypeError, ValueError, OverflowError):
                return False
            if (len(origin) != 3 or
                    any(math.isnan(value) or math.isinf(value)
                        for value in origin)):
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
            'source_shot': _source_shot_from_descriptor(shot),
            'authority_epoch': int(self.state.authority_epoch),
        }
        accepted = self.state.launch_projectile(SERVER_AUTHORITY_ID, message)
        return bool(accepted)

    @staticmethod
    def _projectile_launch_signature(meta):
        return (
            meta['projectile_id'], meta['shooter_kind'],
            meta['shooter_id'], meta['source_vehicle'], meta['shot_seq'],
            meta['shell_index'], _source_shot_signature(meta['source_shot']),
            meta['team'], meta['origin'],
            meta['velocity'], meta['gravity'], meta['max_distance'],
            meta['max_time_ms'], meta['is_he'], meta['splash_radius'],
            meta['penetration_factor'], meta['launch_server_time_ms'],
            meta['authority_epoch'])

    def _normalize_ledger_projectile(self, raw):
        if not isinstance(raw, dict):
            return None
        try:
            projectile_id = str(raw['projectile_id'])
            shooter_kind = str(raw['shooter_kind'])
            shooter_id = int(raw['shooter_id'])
            source_vehicle = str(raw['source_vehicle'])
            source_shot = _normalize_source_shot(raw['source_shot'])
            shot_seq = int(raw['shot_seq'])
            shell_index = int(raw['shell_index'])
            team = int(raw['team'])
            origin = tuple(float(value) for value in raw['origin'])
            velocity = tuple(float(value) for value in raw['velocity'])
            gravity = float(raw['gravity'])
            maximum = float(raw['max_distance'])
            max_time_ms = int(raw['max_time_ms'])
            splash_radius = float(raw['splash_radius'])
            penetration_factor = float(raw['penetration_factor'])
            launch_server_time_ms = int(raw['launch_server_time_ms'])
            checked_through_ms = int(raw.get('checked_through_ms', 0))
            checked_distance = float(raw.get('checked_distance', 0.0))
            piercing_loss = float(raw.get('piercing_loss', 0.0))
            authority_epoch = int(raw.get(
                'authority_epoch', self.state.authority_epoch))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        numbers = (origin + velocity + (
            gravity, maximum, splash_radius, penetration_factor,
            checked_distance, piercing_loss))
        if (not projectile_id or shooter_kind not in ('player', 'bot') or
                shooter_id <= 0 or not source_vehicle or shot_seq <= 0 or
                shell_index < 0 or team not in (1, 2) or
                len(origin) != 3 or len(velocity) != 3 or
                any(not math.isfinite(value) for value in numbers) or
                gravity <= 0.0 or maximum <= 0.0 or max_time_ms <= 0 or
                max_time_ms > PROJECTILE_MAX_TIME_MS or
                launch_server_time_ms < 0 or checked_through_ms < 0 or
                checked_through_ms > max_time_ms or checked_distance < 0.0 or
                checked_distance > maximum or piercing_loss < 0.0 or
                splash_radius < 0.0 or penetration_factor < 0.0 or
                authority_epoch != int(self.state.authority_epoch) or
                not isinstance(raw.get('is_he'), bool)):
            return None
        if not _source_shot_matches_launch(
                source_shot, velocity, gravity, maximum,
                raw['is_he'], splash_radius):
            return None
        return {
            'projectile_id': projectile_id,
            'shooter_kind': shooter_kind,
            'shooter_id': shooter_id,
            'source_vehicle': source_vehicle,
            'source_shot': source_shot,
            'shot_seq': shot_seq,
            'shell_index': shell_index,
            'team': team,
            'origin': origin,
            'velocity': velocity,
            'gravity': gravity,
            'max_distance': maximum,
            'max_time_ms': max_time_ms,
            'is_he': bool(raw['is_he']),
            'splash_radius': splash_radius,
            'penetration_factor': penetration_factor,
            'launch_server_time_ms': launch_server_time_ms,
            'checked_through_ms': checked_through_ms,
            'checked_distance': checked_distance,
            'piercing_loss': piercing_loss,
            'authority_epoch': authority_epoch,
        }

    def _reconcile_projectiles(self, rows):
        """Install both human and bot shots from the canonical ledger."""
        if self._projectiles is None:
            return False
        active_ids = set()
        for raw in rows or ():
            meta = self._normalize_ledger_projectile(raw)
            if meta is None:
                raise RuntimeError('canonical projectile snapshot is malformed')
            wire_id = meta['projectile_id']
            if wire_id in active_ids:
                raise RuntimeError('canonical projectile snapshot is duplicated')
            active_ids.add(wire_id)
            signature = self._projectile_launch_signature(meta)
            previous = self._projectile_launches.get(wire_id)
            if previous is not None and previous != signature:
                raise RuntimeError('canonical projectile launch changed')
            self._projectile_launches[wire_id] = signature
            if wire_id in self._pending_resolutions:
                continue
            base = int(meta['checked_through_ms'])
            if self._projectiles.contains(wire_id):
                self._progress_cursors[wire_id] = base
                self._piercing_loss[wire_id] = max(
                    float(meta['piercing_loss']),
                    float(self._piercing_loss.get(wire_id, 0.0)))
                continue
            launch_time = float(meta['launch_server_time_ms']) / 1000.0
            if launch_time > self._projectiles.now + 1.0e-9:
                continue
            cursor_time = min(
                self._projectiles.now,
                launch_time + float(base) / 1000.0)
            restored = self._projectiles.restore({
                'key': wire_id,
                'start': meta['origin'],
                'velocity': meta['velocity'],
                'gravity': (0.0, -float(meta['gravity']), 0.0),
                'launch_time': launch_time,
                'max_time': float(meta['max_time_ms']) / 1000.0,
                'max_distance': float(meta['max_distance']),
                'payload': dict(meta),
                'cursor_time': max(launch_time, cursor_time),
                'distance': float(meta['checked_distance']),
            })
            if not restored:
                raise RuntimeError('canonical projectile restore failed')
            self._progress_cursors[wire_id] = base
            self._piercing_loss[wire_id] = float(meta['piercing_loss'])

        for state in tuple(self._projectiles.snapshot()):
            wire_id = state.get('key')
            if wire_id not in active_ids:
                self._projectiles.remove(wire_id)
        for wire_id in tuple(self._projectile_launches):
            if wire_id not in active_ids:
                self._forget_projectile(wire_id)
        return True

    def _source_descriptor(self, meta):
        descriptor = self.descriptors.get(meta.get('source_vehicle', ''))
        if descriptor is not None:
            return descriptor
        if self._bots is not None and meta.get('shooter_kind') == 'bot':
            try:
                return self._bots._descriptors.get(
                    int(meta.get('shooter_id', -1)))
            except (TypeError, ValueError):
                return None
        return None

    def _forget_projectile(self, wire_id):
        self._projectile_launches.pop(wire_id, None)
        self._pending_resolutions.pop(wire_id, None)
        self._progress_cursors.pop(wire_id, None)
        self._piercing_loss.pop(wire_id, None)
        self._shot_receipts.pop(wire_id, None)

    def _flush_pending_resolutions(self):
        changed = False
        for wire_id in sorted(tuple(self._pending_resolutions)):
            message = self._pending_resolutions.get(wire_id)
            if (message is not None and
                    self.state.resolve_projectile(
                        SERVER_AUTHORITY_ID, message)):
                self._forget_projectile(wire_id)
                changed = True
        return changed

    def _projectile_chord(self, state, start, end, absolute_start,
                          absolute_end):
        meta = state.get('payload') or {}
        static_fraction = self.world.segment_hit_fraction(
            start, end, include_destructibles=False)
        nearest = None
        for target in self._chord_targets(meta, include_wrecks=True):
            entry = _segment_hull_entry(start, end, target)
            if entry is None:
                continue
            if nearest is None or entry['fraction'] < nearest['fraction']:
                nearest = entry
        limit = min(value for value in (
            static_fraction,
            nearest['fraction'] if nearest is not None else None,
            1.0) if value is not None)
        is_player_shot = str(meta.get('shooter_kind') or '') == 'player'
        stop = self._traverse_shot_destructibles(
            meta, state, start, end, limit)
        if stop is not None:
            if is_player_shot:
                stop.setdefault(
                    'near', self._nearest_target_note(meta, start, end))
            return stop
        if static_fraction is not None and (
                nearest is None or static_fraction <= nearest['fraction']):
            return {'outcome': 'impact', 'fraction': static_fraction,
                    'world': True, 'hit_kind': 'world',
                    'near': (self._nearest_target_note(meta, start, end)
                             if is_player_shot else None)}
        if nearest is None:
            return None
        return {'outcome': 'impact', 'fraction': nearest['fraction'],
                'target': nearest}

    def _traverse_shot_destructibles(self, meta, state, start, end, limit):
        """Destroy and pierce catalog items on one chord, retail-law style."""
        if not self.world.has_destructible_identities():
            return None
        wire_id = self._wire_projectile_id(meta)
        shot = meta.get('source_shot') or {}
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
                        'world': True, 'hit_kind': 'destructible'}
            loss = (self._piercing_loss.get(wire_id, 0.0) +
                    _SHOT_THROUGH_MIN_REDUCTION_1513)
            self._piercing_loss[wire_id] = loss
            entry_distance = (float(state.get('distance', 0.0)) +
                              chord_length * hit['fraction'])
            factor = float(meta.get('penetration_factor', 1.0))
            if combat_rules.sampled_piercing(
                    shot, entry_distance, factor, loss) < 1.0:
                return {'outcome': 'impact', 'fraction': hit['fraction'],
                        'world': True, 'hit_kind': 'destructible'}
        return None

    def _chord_targets(self, meta, include_shooter=False,
                       include_wrecks=False):
        shooter_key = ('%s:%s' % (meta.get('shooter_kind'),
                                  meta.get('shooter_id')))
        if include_shooter:
            shooter_key = None
        for player in self.state.players.values():
            if (not player.connected or not player.participating or
                    not player.client_position or
                    (not player.alive and not include_wrecks)):
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
                'pitch': float(player.pitch),
                'roll': float(player.roll),
                'aim_yaw': float(player.aim_yaw),
                'gun_pitch': float(player.gun_pitch),
                'health': int(player.health),
                'wreck': not bool(player.alive),
                'state': {'critical': dict(player.critical or {})},
            }
        for bot_id, state in self.state.bot_states.items():
            if not state.get('alive') and not include_wrecks:
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
                'pitch': float(state.get('pitch', 0.0) or 0.0),
                'roll': float(state.get('roll', 0.0) or 0.0),
                'aim_yaw': float(state.get(
                    'aim_yaw', state.get('yaw', 0.0)) or 0.0),
                'gun_pitch': float(state.get('gun_pitch', 0.0) or 0.0),
                'descriptor': descriptor,
                'health': int(state.get('health', 0)),
                'wreck': not bool(state.get('alive')),
                'state': state,
            }

    def _nearest_target_note(self, meta, start, end):
        """Distance from this chord to the closest live hull, for diagnosis."""
        chord = tuple(float(end[index]) - float(start[index])
                      for index in range(3))
        chord_sq = sum(value * value for value in chord)
        best = None
        for target in self._chord_targets(meta):
            position = target['position']
            try:
                bbox = server_world._descriptor_hull_bbox(
                    target['descriptor'])
                center_y = (float(bbox[0][1]) + float(bbox[1][1])) * 0.5
                half_height = (float(bbox[1][1]) - float(bbox[0][1])) * 0.5
            except (ValueError, TypeError, IndexError, AttributeError):
                center_y, half_height = 1.0, 1.0
            center = (float(position[0]), float(position[1]) + center_y,
                      float(position[2]))
            if chord_sq <= 1.0e-12:
                fraction = 0.0
            else:
                fraction = max(0.0, min(1.0, sum(
                    (center[index] - float(start[index])) * chord[index]
                    for index in range(3)) / chord_sq))
            closest = tuple(float(start[index]) + chord[index] * fraction
                            for index in range(3))
            distance = math.sqrt(sum(
                (closest[index] - center[index]) ** 2 for index in range(3)))
            if best is None or distance < best['distance']:
                best = {
                    'kind': target['kind'], 'id': target['id'],
                    'distance': distance,
                    'dy': closest[1] - center[1],
                    'half_height': half_height,
                }
        return best

    def _log_player_terminal(self, meta, state, terminal, outcome, direct):
        if str(meta.get('shooter_kind') or '') != 'player':
            return
        impact = tuple(state.get('position') or (0.0, 0.0, 0.0))
        if outcome != 'impact':
            hit = outcome
        else:
            target = terminal.get('target')
            if target is not None:
                hit = '%s:%s' % (target.get('kind'), target.get('id'))
            else:
                hit = str(terminal.get('hit_kind') or 'world')
        parts = [
            'PROJECTILE DETAIL id=%s hit=%s' % (
                self._wire_projectile_id(meta), hit),
            'impact=(%.2f,%.2f,%.2f)' % (impact[0], impact[1], impact[2]),
        ]
        try:
            ground = self.world.ground_height(impact[0], impact[2])
        except Exception:
            ground = None
        if ground is not None:
            parts.append('ground_dy=%.2f' % (impact[1] - float(ground)))
        if direct is not None:
            parts.append('damage=%s' % direct.get('damage'))
        near = terminal.get('near')
        if isinstance(near, dict):
            parts.append(
                'near=%s:%s miss_distance=%.2f dy=%+.2f half_height=%.2f' % (
                    near['kind'], near['id'], near['distance'], near['dy'],
                    near['half_height']))
        _diag_log(' '.join(parts))

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
        if (outcome == 'impact' and target is not None and
                not target.get('wreck')):
            direct = self._direct_effect(meta, state, target)
        splash = []
        if outcome == 'impact' and meta.get('is_he'):
            splash = self._splash_effects(meta, impact, direct)
        try:
            self._log_player_terminal(meta, state, terminal, outcome, direct)
        except Exception:
            # Diagnostics must never change or terminate combat resolution.
            pass
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
            'impact': ([float(impact[0]), float(impact[1]),
                        float(impact[2])]
                       if outcome == 'impact' else None),
            'hit_vehicle': bool(outcome == 'impact' and target is not None),
            'direct': direct,
            'splash': splash,
            'destructibles': self._shot_receipts.pop(wire_id, []),
        }
        if (outcome == 'impact' and target is not None and
                target.get('wreck')):
            message['wreck_hit'] = {
                'target_kind': target['kind'],
                'target_id': int(target['id']),
            }
        if self.state.resolve_projectile(SERVER_AUTHORITY_ID, message):
            self._forget_projectile(wire_id)
        else:
            # Keep this exact terminal proposal for retry. BattleState's
            # tombstone fingerprint makes an accepted replay idempotent.
            self._pending_resolutions[wire_id] = message

    def _splash_effects(self, meta, impact, direct):
        """Splash damage from the copied HE law over donated hull armor."""
        shot = meta.get('source_shot') or {}
        if not shot:
            return []
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
                position, target['yaw'], target.get('state') or {},
                target.get('aim_yaw', target['yaw']),
                target.get('gun_pitch', 0.0),
                target.get('pitch', 0.0), target.get('roll', 0.0))
            direction = Vector3(
                float(position[0]) - float(impact[0]),
                float(position[1]) - float(impact[1]),
                float(position[2]) - float(impact[2]))
            damage, critical = critical_damage.propose_explosion(
                mock, combat_rules.collision_layers(()),
                Vector3(*impact), direction, damage, shell,
                int(meta.get('shooter_id', 0)),
                deadeye=bool(shot.get('deadeye', False)))
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
        if meta.get('projectile_id'):
            return str(meta['projectile_id'])
        prefix = 'p' if meta.get('shooter_kind') == 'player' else 'b'
        return '%d:%s:%d:%d' % (
            int(self._round_id), prefix, int(meta.get('shooter_id', 0)),
            int(meta.get('shot_seq', 0)))

    def _direct_effect(self, meta, state, target):
        shot = meta.get('source_shot') or {}
        if not shot:
            return None
        collisions, trace_start, trace_end = _critical_vehicle_trace(
            shot, target['ray_start'], target['ray_end'],
            target['collisions'])
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
        rolls = []

        def roll(low, high):
            rolls.append(random.uniform(low, high))
            return rolls[-1]

        damage = combat_rules.damage(shot, result, armor, random_uniform=roll)
        hull_damage = damage
        mock = _TargetMock(
            target['id'], target['health'], target['descriptor'],
            target['position'], target['yaw'],
            target.get('state') or {},
            target.get('aim_yaw', target['yaw']),
            target.get('gun_pitch', 0.0),
            target.get('pitch', 0.0), target.get('roll', 0.0))
        shell = (combat_rules.legacy_shot(shot).get('shell') or {})
        critical_collisions = combat_rules.collision_layers(collisions)
        if str(shell.get('kind') or '') == 'HIGH_EXPLOSIVE':
            ray_start = target['ray_start']
            ray_end = target['ray_end']
            direction = Vector3(
                float(ray_end[0]) - float(ray_start[0]),
                float(ray_end[1]) - float(ray_start[1]),
                float(ray_end[2]) - float(ray_start[2]))
            damage, critical = critical_damage.propose_explosion(
                mock, critical_collisions, Vector3(*state['position']),
                direction, damage, shell, int(meta.get('shooter_id', 0)),
                deadeye=bool(shot.get('deadeye', False)))
        else:
            damage, critical = critical_damage.propose_direct(
                mock, critical_collisions,
                trace_start, trace_end, damage, shell,
                int(meta.get('shooter_id', 0)),
                penetrated=int(result) == 2,
                deadeye=bool(shot.get('deadeye', False)))
        effect = {
            'target_kind': target['kind'],
            'target_id': int(target['id']),
            'damage': int(damage),
            # The same roll the damage law used, before armor and modules.
            'potential_damage': int(rolls[0]) if rolls else 0,
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


_PROJECTILE_SHELL_KINDS = frozenset((
    'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE', 'ARMOR_PIERCING',
    'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR'))


def _source_number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('source shot requires a plain number')
    parsed = float(value)
    if (not math.isfinite(parsed) or parsed < minimum or parsed > maximum):
        raise ValueError('source shot number is outside bounds')
    return parsed


def _normalize_source_shot(value):
    """Validate the immutable mounted-gun law from the canonical ledger."""
    if not isinstance(value, dict) or set(value) != {
            'speed', 'gravity', 'maxDistance', 'piercingPower', 'deadeye',
            'shell'}:
        raise ValueError('invalid source shot shape')
    shell = value.get('shell')
    if not isinstance(shell, dict) or set(shell) != {
            'kind', 'caliber', 'damage', 'explosionRadius'}:
        raise ValueError('invalid source shell shape')
    kind = shell.get('kind')
    piercing = value.get('piercingPower')
    damage = shell.get('damage')
    deadeye = value.get('deadeye')
    if (not isinstance(kind, str) or kind not in _PROJECTILE_SHELL_KINDS or
            not isinstance(deadeye, bool) or
            not isinstance(piercing, list) or len(piercing) != 2 or
            not isinstance(damage, list) or len(damage) != 2):
        raise ValueError('invalid source shell data')
    return {
        'speed': _source_number(value.get('speed'), 0.000001, 3000.0),
        'gravity': _source_number(value.get('gravity'), 0.000001, 500.0),
        'maxDistance': _source_number(
            value.get('maxDistance'), 0.000001, 10000.0),
        'piercingPower': [
            _source_number(component, 0.0, 10000.0)
            for component in piercing],
        'deadeye': deadeye,
        'shell': {
            'kind': kind,
            'caliber': _source_number(
                shell.get('caliber'), 0.000001, 1000.0),
            'damage': [
                _source_number(damage[0], 0.000001, 10000.0),
                _source_number(damage[1], 0.0, 10000.0),
            ],
            'explosionRadius': _source_number(
                shell.get('explosionRadius'), 0.0, 100.0),
        },
    }


def _source_shot_from_descriptor(shot):
    shell = _field(shot, 'shell', {}) or {}
    shell_type = _field(shell, 'type', {}) or {}
    kind = _field(shell, 'kind', None)
    if not kind:
        kind = _field(shell_type, 'name', None)
    radius = _field(shell, 'explosionRadius', None)
    if radius is None:
        radius = _field(shell_type, 'explosionRadius', 0.0)
    return _normalize_source_shot({
        'speed': _field(shot, 'speed'),
        'gravity': _field(shot, 'gravity'),
        'maxDistance': _field(shot, 'maxDistance'),
        'piercingPower': list(_field(shot, 'piercingPower', ()) or ()),
        'deadeye': False,
        'shell': {
            'kind': kind,
            'caliber': _field(shell, 'caliber'),
            'damage': list(_field(shell, 'damage', ()) or ()),
            'explosionRadius': radius,
        },
    })


def _source_shot_signature(shot):
    shell = shot['shell']
    return (
        shot['speed'], shot['gravity'], shot['maxDistance'],
        tuple(shot['piercingPower']), shot['deadeye'],
        shell['kind'], shell['caliber'],
        tuple(shell['damage']), shell['explosionRadius'])


def _source_shot_matches_launch(
        shot, velocity, gravity, maximum, is_he, splash_radius):
    def close(left, right):
        return abs(float(left) - float(right)) <= max(
            0.001, abs(float(right)) * 0.000001)

    speed = math.sqrt(sum(component * component for component in velocity))
    shell = shot['shell']
    return (
        close(speed, shot['speed']) and close(gravity, shot['gravity']) and
        close(maximum, shot['maxDistance']) and
        bool(is_he) == (shell['kind'] == 'HIGH_EXPLOSIVE') and
        close(splash_radius, shell['explosionRadius']))


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


def _critical_vehicle_trace(shot, query_start, query_end, collisions):
    """Cap one vehicle's armor/module trace at ten shell calibres.

    The distance budget starts at the first vehicle collision, including a
    future donated track or spaced-armor layer. The server currently supplies
    only one hull OBB face, but keeping this rule generic prevents a full
    projectile substep from becoming an internal-module ray.
    """
    collisions = tuple(collisions or ())
    start = Vector3(*query_start)
    end = Vector3(*query_end)
    delta = end - start
    length = float(delta.length)
    if not collisions or length <= 0.000001:
        return collisions, start, start
    try:
        first = min(float(collision.dist) for collision in collisions)
    except (AttributeError, TypeError, ValueError):
        raise TypeError('server collision contains an invalid distance')
    first = max(0.0, min(length, first))
    legacy = combat_rules.legacy_shot(shot)
    caliber = _number((legacy.get('shell') or {}).get('caliber'), 0.0)
    trace_distance = first + max(0.0, caliber) / 100.0
    limited = tuple(
        collision for collision in collisions
        if float(collision.dist) <= trace_distance + 0.000001)
    delta.normalise()
    return limited, start, start + delta.scale(trace_distance)


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


def _diag_log(message):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def _pose_axes(yaw, pitch, roll):
    """Local basis vectors in world space for BigWorld's yaw/pitch/roll."""
    sy, cy = math.sin(yaw), math.cos(yaw)
    sp, cp = math.sin(pitch), math.cos(pitch)
    sr, cr = math.sin(roll), math.cos(roll)

    def rotate(vector):
        x, y, z = vector
        y, z = cp * y - sp * z, sp * y + cp * z
        return (cy * x + sy * z, y, -sy * x + cy * z)

    return (rotate((cr, sr, 0.0)),
            rotate((-sr, cr, 0.0)),
            rotate((0.0, 0.0, 1.0)))


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
    axes = _pose_axes(yaw, float(target.get('pitch', 0.0) or 0.0),
                      float(target.get('roll', 0.0) or 0.0))
    center_local = ((float(minimum[0]) + float(maximum[0])) * 0.5,
                    (float(minimum[1]) + float(maximum[1])) * 0.5,
                    (float(minimum[2]) + float(maximum[2])) * 0.5)
    half = ((float(maximum[0]) - float(minimum[0])) * 0.5,
            (float(maximum[1]) - float(minimum[1])) * 0.5,
            (float(maximum[2]) - float(minimum[2])) * 0.5)
    center_world = tuple(
        float(position[index]) +
        sum(axes[row][index] * center_local[row] for row in range(3))
        for index in range(3))
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
    local = _world_to_local(hit, position, axes)
    face, normal_local = _dominant_face(local, half, center_local)
    armor = _armor_for_face(descriptor, face)
    direction = tuple(float(end[index]) - float(start[index])
                      for index in range(3))
    length = math.sqrt(sum(value * value for value in direction))
    if length <= 1.0e-9:
        return None
    direction = tuple(value / length for value in direction)
    normal_world = _local_vector_to_world(normal_local, axes)
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
        'pitch': float(target.get('pitch', 0.0) or 0.0),
        'roll': float(target.get('roll', 0.0) or 0.0),
        'aim_yaw': float(target.get('aim_yaw', yaw)),
        'gun_pitch': float(target.get('gun_pitch', 0.0) or 0.0),
        'state': target.get('state') or {},
        'wreck': bool(target.get('wreck')),
        'collisions': (collision,),
        'ray_start': tuple(float(value) for value in start),
        'ray_end': tuple(float(value) for value in end),
    }


def _world_to_local(point, position, axes):
    delta = (float(point[0]) - float(position[0]),
             float(point[1]) - float(position[1]),
             float(point[2]) - float(position[2]))
    return tuple(sum(axis[index] * delta[index] for index in range(3))
                 for axis in axes)


def _local_vector_to_world(vector, axes):
    return tuple(sum(float(vector[row]) * axes[row][index]
                     for row in range(3))
                 for index in range(3))


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
