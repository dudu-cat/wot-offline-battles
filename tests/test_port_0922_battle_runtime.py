import contextlib
import io
import math
from pathlib import Path
import pickle
import sys
import types
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.battle_runtime import (
    BattleRuntime, _LANInputSender, _engine_rotation,
    _selected_vehicle_has_sixth_sense)
from gui.mods.offline_lan_0922 import critical_damage, gun_mechanics
from gui.mods.offline_lan_0922.entities.remote_vehicle import \
    RemoteVehicle, RemoteVehicleFactory, _RemoteFilter, \
    collide_vehicle_at_matrix
from gui.mods.offline_lan_0922.entities.bigworld_binding import \
    BigWorldVehicleBinding


class _Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            try:
                x, y, z = x[0], x[1], x[2]
            except (TypeError, IndexError):
                x, y, z = x.x, x.y, x.z
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __add__(self, other):
        return _Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return _Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __neg__(self):
        return _Vector(-self.x, -self.y, -self.z)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def scale(self, value):
        return _Vector(self.x * value, self.y * value, self.z * value)

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


class _ReadOnlyVector(object):
    """Match native #1513 vectors whose components reject assignment."""

    def __init__(self, x=0.0, y=0.0, z=0.0):
        object.__setattr__(self, 'x', float(x))
        object.__setattr__(self, 'y', float(y))
        object.__setattr__(self, 'z', float(z))

    def __setattr__(self, name, unused_value):
        raise RuntimeError('Operation is not allowed')


class _Matrix(object):
    def __init__(self, other=None):
        self.yaw = getattr(other, 'yaw', 0.0)
        self.pitch = getattr(other, 'pitch', 0.0)
        self.roll = getattr(other, 'roll', 0.0)
        self.translation = _Vector(getattr(
            other, 'translation', _Vector()))

    def setIdentity(self):
        self.yaw = self.pitch = self.roll = 0.0
        self.translation = _Vector()

    def setRotateYPR(self, value):
        self.yaw, self.pitch, self.roll = map(float, value)

    def setRotateY(self, value):
        self.yaw = float(value)

    def setRotateX(self, value):
        self.pitch = float(value)

    def setTranslate(self, value):
        self.translation = _Vector(value)

    def postMultiply(self, unused_other):
        return None

    def preMultiply(self, unused_other):
        return None

    def invert(self):
        self.translation = -self.translation

    def applyPoint(self, value):
        value = _Vector(value)
        return value + self.translation


class _YawMatrix(_Matrix):
    """Rigid yaw transform for visible-pose collision regression tests."""

    def invert(self):
        yaw = self.yaw
        translation = self.translation
        self.yaw = -yaw
        cosine = math.cos(self.yaw)
        sine = math.sin(self.yaw)
        x = -translation.x
        z = -translation.z
        self.translation = _Vector(
            cosine * x + sine * z, -translation.y,
            -sine * x + cosine * z)

    def applyPoint(self, value):
        value = _Vector(value)
        cosine = math.cos(self.yaw)
        sine = math.sin(self.yaw)
        return _Vector(
            cosine * value.x + sine * value.z + self.translation.x,
            value.y + self.translation.y,
            -sine * value.x + cosine * value.z + self.translation.z)


class _Model(object):
    _SUPPORTED_ATTRIBUTES = frozenset(('matrix', 'visible', 'node_bindings'))

    def __init__(self):
        self.matrix = None
        self.visible = True
        self.node_bindings = []

    def __setattr__(self, name, value):
        if name not in self._SUPPORTED_ATTRIBUTES:
            raise AttributeError(
                'PyCompoundModel has no %s attribute' % name)
        object.__setattr__(self, name, value)

    def node(self, unused_name, matrix_provider=None):
        if matrix_provider is not None:
            self.node_bindings.append((unused_name, matrix_provider))
        position = getattr(self.matrix, 'translation', _Vector())
        return types.SimpleNamespace(translation=_Vector(
            position.x, position.y + 1.5, position.z))


class _Descriptor(object):
    def __init__(self, name='ussr:R11_MS-1'):
        self.name = name
        shell = types.SimpleNamespace(
            compactDescr=101, damage=(100.0,), caliber=37.0,
            kind='ARMOR_PIERCING', effectsIndex=3)
        shot = types.SimpleNamespace(
            shell=shell, piercingPower=(1000.0, 800.0),
            maxDistance=500.0)
        self.gun = types.SimpleNamespace(
            itemTypeName='vehicleGun',
            pitchLimits={'absolute': (-0.2, 0.4)}, shots=[shot],
            maxAmmo=40, clip=(1,), reloadTime=1.5, rotationSpeed=1.0,
            aimingTime=1.0, burst=(1, 0.1),
            shotDispersionAngle=0.0037,
            shotDispersionFactors={
                'afterShot': 4.0, 'turretRotation': 0.1})
        self.turret = types.SimpleNamespace(
            itemTypeName='vehicleTurret',
            circularVisionRadius=330.0, rotationSpeed=1.0)
        self.radio = types.SimpleNamespace(distance=400.0)
        self.physics = {'speedLimits': (14.0, 7.0)}
        self.type = types.SimpleNamespace(name=name, tags=('lightTank',))
        self.hull = {}
        self.chassis = {
            'itemTypeName': 'vehicleChassis',
            'hullPosition': _Vector(),
            'shotDispersionFactors': (0.14, 0.14)}
        self.hull = {
            'itemTypeName': 'vehicleHull',
            'turretPositions': (_Vector(),)}
        self.maxHealth = 500
        self.activeGunShotIndex = 0

    def makeCompactDescr(self):
        return self.name

    def getHitTesters(self):
        return ()


class _VehicleDescr(object):
    def __new__(cls, typeName=None, compactDescr=None):
        return _Descriptor(typeName or compactDescr or 'ussr:R11_MS-1')


class _Vehicle(object):
    def __init__(self, entity_id, descriptor, position, rotation, properties):
        self.id = entity_id
        self.typeDescriptor = descriptor
        self.position = position
        self.rotation = tuple(rotation)
        self.yaw = float(rotation[2])
        self.matrix = _Matrix()
        self.matrix.setRotateYPR(rotation)
        self.matrix.setTranslate(position)
        self.model = _Model()
        self.model.matrix = self.matrix
        self.appearance = types.SimpleNamespace(
            compoundModel=self.model, turretMatrix=_Matrix(),
            gunMatrix=_Matrix())
        self.health = properties['health']
        self.isCrewActive = True
        self.gunAnglesPacked = properties.get('gunAnglesPacked', 0)
        self.isStarted = True
        self.inWorld = True
        self.teleports = []
        self.speed = 0.0
        self.filter = types.SimpleNamespace(
            longitudinalSpeed=0.0, angularSpeed=0.0,
            notifyInputKeysDown=mock.Mock())
        self.ammo_bay_effects = []

    def teleport(self, position, rotation):
        self.position = position
        self.rotation = tuple(rotation)
        self.yaw = float(rotation[2])
        self.teleports.append((position, rotation))

    def getAimParams(self):
        return (0.0, 0.0)

    def getSpeed(self):
        return self.speed

    def showShooting(self, burst, is_predicted=False):
        self.last_shot = (burst, is_predicted)

    def showAmmoBayEffect(self, mode, fireball_volume,
                          projected_turret_speed):
        self.ammo_bay_effects.append(
            (mode, fireball_volume, projected_turret_speed))

    def set_gunAnglesPacked(self, previous):
        self.previous_gun_angles = previous

    def set_health(self, previous):
        self.previous_health = previous

    def set_isCrewActive(self, previous):
        self.previous_crew_active = previous

    def isAlive(self):
        return self.health > 0 and self.isCrewActive

    def onHealthChanged(self, health, attacker_id, reason_id):
        self.health_change = (health, attacker_id, reason_id)


class _Arena(object):
    def __init__(self, avatar):
        self._avatar = avatar

    def onTeamBasePointsUpdate(self, team, base_id, points, time_left,
                               invaders, capturing_stopped):
        self._avatar.base_points.append((
            team, base_id, points, time_left, invaders,
            capturing_stopped))

    def onTeamBaseCaptured(self, team, base_id):
        self._avatar.base_captured.append((team, base_id))


class _ArenaDataProvider(object):
    def __init__(self, avatar):
        self.avatar = avatar
        self.player_vehicle_id = 0
        self.refreshes = 0

    def isRequiredDataExists(self):
        if self.player_vehicle_id > 0:
            return True
        self.refreshes += 1
        self.player_vehicle_id = int(self.avatar.playerVehicleID)
        return self.player_vehicle_id > 0

    def getPlayerVehicleID(self, forceUpdate=True):
        # Exact #1513 only force-refreshes a None cache. ArenaDP is created
        # before the local Vehicle and therefore normally holds integer 0.
        if forceUpdate and self.player_vehicle_id is None:
            self.refreshes += 1
            self.player_vehicle_id = int(self.avatar.playerVehicleID)
        return self.player_vehicle_id


class _InputHandler(object):
    def __init__(self):
        self.started_periods = []
        self._AvatarInputHandler__ctrlModeName = 'arcade'
        self._AvatarInputHandler__curCtrl = types.SimpleNamespace(
            camera=_ArcadeCamera())
        self.steadyVehicleMatrixCalculator = types.SimpleNamespace(
            _SteadyVehicleMatrixCalculator__outputMProv=
            types.SimpleNamespace(rotationSrc=object(),
                                  translationSrc=object()),
            _SteadyVehicleMatrixCalculator__stabilisedMProv=
            types.SimpleNamespace(target=object()))

    def _AvatarInputHandler__onArenaStarted(self, period):
        self.started_periods.append(period)


class _ArcadeCamera(object):
    def __init__(self):
        self._vehicle_matrix = object()
        self.bindings = []

    @property
    def vehicleMProv(self):
        return self._vehicle_matrix

    @vehicleMProv.setter
    def vehicleMProv(self, value):
        self._vehicle_matrix = value
        self.bindings.append(value)


class _ConsistentMatrices(object):
    def __init__(self):
        self.targets = []

    def _ConsistentMatrices__setTarget(self, matrix, as_static):
        self.targets.append((matrix, as_static))


class _AdaptiveMatrixProvider(object):
    """Strict stand-in for #1513 Math.WGAdaptiveMatrixProvider."""

    def __init__(self, target):
        self._target = None
        self.target = target

    @property
    def target(self):
        return self._target

    @target.setter
    def target(self, value):
        if not isinstance(value, _Matrix):
            raise TypeError('adaptive matrix target must be a Matrix')
        self._target = value


class _Avatar(object):
    def __init__(self):
        self._offlineLANInitComplete = True
        self._offlineLANPlayerReady = True
        self.spaceID = 7
        self.team = 1
        self.playerVehicleID = 0
        self.isGunLocked = True
        self.ownVehicleAuxPhysicsData = 0
        self.ownVehicleGear = 0
        self.arena_updates = []
        self.positions = []
        self.round_finished = []
        self.ammo_updates = []
        self.reload_updates = []
        self.targeting_updates = []
        self.gun_marker_updates = []
        self.damage_info = []
        self.hit_directions = []
        self.shot_results = []
        self.dispersion_queries = []
        self.battle_events = []
        self.misc_statuses = []
        self.filter = object()
        self.base_points = []
        self.base_captured = []
        self.arena = _Arena(self)
        self.inputHandler = _InputHandler()
        self.consistentMatrices = _ConsistentMatrices()
        self._PlayerAvatar__ownVehicleStabMProv = \
            _AdaptiveMatrixProvider(_Matrix())
        self.visual_starts = []
        self.visual_stops = []
        self.gunRotator = types.SimpleNamespace(
            dispersionAngle=0.25,
            turretRotationSpeed=0.5,
            getCurShotPosition=lambda: (
                _Vector(0.0, 2.0, 0.0), _Vector(0.0, 0.0, 1.0)))
        self.terrainEffects = types.SimpleNamespace(addNew=mock.Mock())
        self.arena_dp = _ArenaDataProvider(self)
        self.guiSessionProvider = types.SimpleNamespace(
            invalidateVehicleState=mock.Mock(),
            setVehicleHealth=mock.Mock(),
            getArenaDP=lambda: self.arena_dp)

    def getOwnVehicleShotDispersionAngle(self, turret_rotation_speed,
                                         with_shot=0):
        self.dispersion_queries.append((turret_rotation_speed, with_shot))
        return [0.25, 0.125]

    def set_playerVehicleID(self, previous):
        self.previous_vehicle_id = previous

    def set_isGunLocked(self, previous):
        pass

    def set_ownVehicleAuxPhysicsData(self, previous):
        pass

    def set_ownVehicleGear(self, previous):
        pass

    def onVehicleChanged(self):
        self.vehicle_changed = getattr(self, 'vehicle_changed', 0) + 1

    def updateArena(self, kind, payload):
        self.arena_updates.append((kind, payload))

    def syncVehicleAttrs(self, values):
        self.synced_attrs = values

    def updateOwnVehiclePosition(self, position, direction,
                                 vehicle_speed, vehicle_rotation_speed):
        self.positions.append((position, direction, vehicle_speed,
                               vehicle_rotation_speed))

    def updateVehicleAmmo(self, vehicle_id, compact_descr, quantity,
                          quantity_in_clip, time_remaining):
        self.ammo_updates.append((vehicle_id, compact_descr, quantity,
                                  quantity_in_clip, time_remaining))

    def updateVehicleSetting(self, vehicle_id, code, value):
        self.last_setting = (vehicle_id, code, value)

    def updateTargetingInfo(self, turret_yaw, gun_pitch,
                            max_turret_rotation_speed,
                            max_gun_rotation_speed,
                            shot_disp_multiplier_factor,
                            gun_shot_dispersion_turret_rotation,
                            chassis_shot_dispersion_movement,
                            chassis_shot_dispersion_rotation, aiming_time):
        self.targeting = (
            turret_yaw, gun_pitch, max_turret_rotation_speed,
            max_gun_rotation_speed, shot_disp_multiplier_factor,
            gun_shot_dispersion_turret_rotation,
            chassis_shot_dispersion_movement,
            chassis_shot_dispersion_rotation, aiming_time)
        self.targeting_updates.append(self.targeting)

    def updateGunMarker(self, vehicle_id, shot_position, shot_vector,
                        dispersion_angle):
        self.gun_marker_updates.append((
            vehicle_id, shot_position, shot_vector, dispersion_angle))

    def updateVehicleGunReloadTime(self, vehicle_id, time_left, base_time):
        self.reload = (vehicle_id, time_left, base_time)
        self.reload_updates.append(self.reload)

    def updateVehicleHealth(self, vehicle_id, health, death_reason_id,
                            is_crew_active, is_respawn):
        self.health_update = (vehicle_id, health, death_reason_id,
                              is_crew_active, is_respawn)

    def updateVehicleMiscStatus(self, vehicle_id, code, int_arg, float_args):
        self.misc_status = (vehicle_id, code, int_arg, float_args)
        self.misc_statuses.append(self.misc_status)

    def showVehicleDamageInfo(self, vehicle_id, damage_index, extra_index,
                              attacker_id, equipment_id):
        self.damage_info.append((vehicle_id, damage_index, extra_index,
                                 attacker_id, equipment_id))

    def showOwnVehicleHitDirection(self, hit_yaw, attacker_id, damage,
                                   crits, is_blocked, is_shell_he,
                                   damaged_id):
        self.hit_directions.append((
            hit_yaw, attacker_id, damage, crits, is_blocked,
            is_shell_he, damaged_id))

    def showShotResults(self, results):
        self.shot_results.append(list(results))

    def onBattleEvents(self, events):
        self.battle_events.append(list(events))

    def onRoundFinished(self, winner, reason):
        self.round_finished.append((winner, reason))


class _Compatibility(object):
    def __init__(self):
        self.bridge = None
        self.configured = []
        self.hangar_space = None
        self.bigworld = None
        self.app_loader = None
        self.retired_players = set()
        self.disconnect_calls = 0
        self.network_client = None
        self.pose_overlays = {}
        self.control_mode_listener = None
        self.target_lock_candidate = None
        self.target_lock_validations = []

    def set_battle_network_client(self, client):
        self.network_client = client

    def set_control_mode_listener(self, listener):
        self.control_mode_listener = listener

    def set_target_lock_candidate(self, vehicle):
        self.target_lock_candidate = vehicle
        return True

    def validate_target_lock(self, avatar):
        self.target_lock_validations.append(avatar)
        return False

    def native_vehicle_attribute(self, vehicle, name):
        return getattr(vehicle, name)

    def set_vehicle_pose_overlay(self, vehicle, position, yaw, matrix,
                                 speed=0.0, turn_speed=0.0):
        self.pose_overlays[id(vehicle)] = {
            'position': position, 'yaw': yaw, 'matrix': matrix,
            'speed': speed, 'turn_speed': turn_speed}
        vehicle.position = position
        vehicle.yaw = yaw
        vehicle.matrix = matrix
        return True

    def clear_vehicle_pose_overlay(self, vehicle):
        return self.pose_overlays.pop(id(vehicle), None) is not None

    def bind_vehicle_pose_sources(self, avatar, vehicle):
        matrix = self.pose_overlays[id(vehicle)]['matrix']
        avatar.consistentMatrices._ConsistentMatrices__setTarget(
            matrix, False)
        avatar._PlayerAvatar__ownVehicleStabMProv.target = matrix
        calculator = avatar.inputHandler.steadyVehicleMatrixCalculator
        calculator._SteadyVehicleMatrixCalculator__outputMProv.rotationSrc = \
            matrix
        calculator._SteadyVehicleMatrixCalculator__outputMProv.\
            translationSrc = matrix
        calculator._SteadyVehicleMatrixCalculator__stabilisedMProv.target = \
            matrix
        return True

    def restore_vehicle_pose_sources(self, avatar, vehicle, native_matrix,
                                     native_stabilised_matrix):
        unused_vehicle = vehicle
        avatar.consistentMatrices._ConsistentMatrices__setTarget(
            native_matrix, False)
        avatar._PlayerAvatar__ownVehicleStabMProv.target = \
            native_stabilised_matrix
        return True

    def configure_battle(self, gui_type, bonus_type, player_name=None,
                         player_team=None):
        self.configured.append(
            (gui_type, bonus_type, player_name, player_team))

    def attach_avatar_server(self, avatar, bridge):
        self.bridge = bridge

    def deactivate_map(self):
        self.deactivated = True

    def retire_current_player(self):
        if self.bigworld is None or self.bigworld.player() is None:
            return False
        player = self.bigworld.player()
        if player in self.retired_players:
            return False
        self.retired_players.add(player)
        if (self.hangar_space is not None and
                self.hangar_space.inited and
                self.hangar_space.spaceInited):
            self.bigworld.operations.append(('account_retire',))
            self.hangar_space.destroy()
        else:
            self.bigworld.operations.append(('avatar_retire',))
        return True

    def restore_lobby_account(self):
        self.account_restored = True
        if self.hangar_space is not None:
            self.hangar_space.inited = True
            self.hangar_space.spaceInited = True
        account = _Avatar()
        if self.bigworld is not None:
            self.bigworld.avatar = account
        if self.app_loader is not None:
            self.app_loader.showLobby()
        return account

    def disconnect(self):
        self.disconnect_calls += 1
        if self.bigworld is not None:
            self.bigworld.operations.append(('offline_disconnect',))
            self.bigworld.avatar = None


class _AppLoader(object):
    __slots__ = (
        '__state', '__ctx', '__appFactory',
        'onGUISpaceLeft', 'onGUISpaceEntered', 'space_id',
        'actual_space_id', 'transitions')

    battle_page_calls = mock.Mock(return_value=True)
    battle_loading_calls = mock.Mock(return_value=True)
    lobby_callback = None

    def __init__(self):
        self._AppLoader__state = _AppState(self)
        self._AppLoader__ctx = None
        self._AppLoader__appFactory = None
        self.onGUISpaceLeft = None
        self.onGUISpaceEntered = None
        self.space_id = 4
        self.actual_space_id = 4
        self.transitions = []

    def getSpaceID(self):
        return self.space_id

    def showBattleLoading(self):
        result = type(self).battle_loading_calls()
        self.transitions.append((self.actual_space_id, 5))
        # Match exact changeSpace(): ctx is mutated before the current state
        # accepts or rejects the requested transition.
        self.space_id = 5
        if result:
            self.actual_space_id = 5
        return result

    def showBattlePage(self):
        result = type(self).battle_page_calls()
        self.transitions.append((self.actual_space_id, 6))
        self.space_id = 6
        if result:
            self.actual_space_id = 6
        return result

    def showLobby(self):
        callback = type(self).lobby_callback
        self.transitions.append((self.actual_space_id, 4))
        self.space_id = 4
        self.actual_space_id = 4
        if callable(callback):
            return callback()
        return True


class _AppState(object):
    def __init__(self, loader):
        self.loader = loader

    def getSpaceID(self):
        return self.loader.actual_space_id


_APP_LOADER_SHOW_BATTLE_PAGE = _AppLoader.__dict__['showBattlePage']
_APP_LOADER_SHOW_BATTLE_LOADING = _AppLoader.__dict__['showBattleLoading']
_APP_LOADER_SHOW_LOBBY = _AppLoader.__dict__['showLobby']


class _ArenaLoadController(object):
    def __init__(self, app_loader):
        self.app_loader = app_loader
        self.invalidations = 0

    def invalidateArenaInfo(self):
        self.invalidations += 1
        return self.app_loader.showBattleLoading()


class _OfflineMap(object):
    def __init__(self, bigworld=None, app_loader=None):
        self.active = False
        self.bigworld = bigworld
        self.app_loader = app_loader
        self.viewer_camera_calls = 0

    def create(self, map_name):
        if self.app_loader is not None:
            self.app_loader.showBattlePage()
        if self.bigworld is not None:
            self.bigworld.operations.append(('map_create', map_name))
        self.active = True
        self.map_name = map_name
        if self.bigworld is not None and self.bigworld.avatar is None:
            self.bigworld.avatar = _Avatar()
        if self.bigworld is not None:
            avatar = self.bigworld.avatar
            self.bigworld.avatar.guiSessionProvider = types.SimpleNamespace(
                shared=types.SimpleNamespace(
                    arenaLoad=_ArenaLoadController(self.app_loader)),
                invalidateVehicleState=mock.Mock(),
                setVehicleHealth=mock.Mock(),
                getArenaDP=lambda: avatar.arena_dp,
                startVehicleVisual=lambda proxy, immediate:
                self.bigworld.avatar.visual_starts.append((proxy, immediate)),
                stopVehicleVisual=lambda entity_id, is_player:
                self.bigworld.avatar.visual_stops.append(
                    (entity_id, is_player)))
        self._OfflineMapCreator__setupCamera()

    def _OfflineMapCreator__setupCamera(self):
        self.viewer_camera_calls += 1

    def SetActive(self, active):
        self.active = bool(active)

    def Active(self):
        return self.active

    def destroy(self):
        self.active = False
        if self.bigworld is not None:
            self.bigworld.clearEntitiesAndSpaces()


class _HangarSpace(object):
    def __init__(self, operations):
        self.inited = True
        self.spaceInited = True
        self.operations = operations

    def destroy(self):
        self.operations.append(('hangar_destroy',))
        self.inited = False
        self.spaceInited = False


class _BigWorld(object):
    def __init__(self, avatar, compatibility):
        self.avatar = avatar
        self.compatibility = compatibility
        self.entities = {}
        self.callbacks = []
        self.operations = []
        self.now = 10.0
        self.space_status = 1.0
        self.next_id = 100
        self.defer_vehicle_entry = False
        self.reenter_vehicle_during_create = False
        self.pending_entities = {}
        self.created_offline_entities = []
        self.edge_adds = []
        self.edge_removes = []

    def player(self):
        return self.avatar

    def time(self):
        return self.now

    def serverTime(self):
        return self.now

    def wg_getMatInfoNearPoint(self, unused_space_id, unused_start,
                               unused_end, unused_hit_point,
                               unused_filter):
        return None

    def callback(self, delay, function):
        if self.pending_entities and not self.defer_vehicle_entry:
            original = function

            def enter_pending_then_invoke():
                # Model the normal BigWorld lifecycle: createEntity returns
                # first, then Vehicle.onEnterWorld runs on an engine tick.
                for entity_id in list(self.pending_entities):
                    if entity_id in self.pending_entities:
                        self.enter_pending_vehicle(entity_id)
                return original()

            function = enter_pending_then_invoke
        self.callbacks.append(function)
        return len(self.callbacks)

    def cancelCallback(self, callback_id):
        pass

    def spaceLoadStatus(self):
        return self.space_status

    def createEntity(self, name, space_id, vehicle_id, position, rotation,
                     properties):
        self.next_id += 1
        if name == 'OfflineEntity':
            entity = types.SimpleNamespace(
                id=self.next_id, model=None, inWorld=True)
            self.entities[entity.id] = entity
            self.created_offline_entities.append({
                'id': entity.id, 'space_id': space_id,
                'position': position, 'rotation': rotation})
            return entity.id
        descriptor = _VehicleDescr(
            compactDescr=properties['publicInfo']['compDescr'])
        entity = _Vehicle(
            self.next_id, descriptor, position, rotation, properties)
        if self.reenter_vehicle_during_create:
            self._enter_vehicle(entity)
        else:
            self.pending_entities[entity.id] = entity
        return entity.id

    def _enter_vehicle(self, entity):
        bridge = self.compatibility.bridge
        if bridge is not None:
            bridge.prepareVehicleEnter(entity)
            bridge.acceptVehicleEnter(entity.id)
            bridge.setClientReady()
            bridge.completeVehicleEnter(entity.id)
        # Match #1513: BigWorld.entity(id) becomes visible only after the
        # native vehicle_onEnterWorld callback has returned.
        self.entities[entity.id] = entity

    def enter_pending_vehicle(self, entity_id):
        entity = self.pending_entities.pop(entity_id)
        self._enter_vehicle(entity)

    def destroyEntity(self, entity_id):
        self.entities.pop(entity_id, None)

    def entity(self, entity_id):
        return self.entities.get(entity_id)

    def clearEntitiesAndSpaces(self):
        self.operations.append(('clear_entities_spaces',))
        self.entities.clear()
        self.pending_entities.clear()
        self.avatar = None

    def loadResourceListBG(self, assemblers, callback):
        descriptor = assemblers[0]
        callback({descriptor.name: _Model()})

    def setWatcher(self, name, enabled):
        self.operations.append(('watcher', name, enabled))

    def clearAllSpaces(self):
        self.clearEntitiesAndSpaces()

    def wg_collideSegment(self, space_id, start, end, mask):
        if start.y > end.y and abs(start.x - end.x) < 0.001 and abs(start.z - end.z) < 0.001:
            return (_Vector(start.x, 0.0, start.z),)
        return None

    def wgAddEdgeDetectEntity(self, entity, color, group, behind):
        self.edge_adds.append((entity, color, group, behind))

    def wgDelEdgeDetectEntity(self, entity):
        self.edge_removes.append(entity)


class _Client(object):
    def __init__(self):
        self.player_id = 1
        self.name = 'Player'
        self.vehicle = 'ussr:R11_MS-1'
        self.team = 1
        self.slot = 0
        self.max_health = 500
        self.sent = []

    def send_bot_manifest(self, bots):
        self.sent.append(('manifest', bots))
        return True

    def send_bot_state(self, bots):
        self.sent.append(('state', bots))
        return True

    def send_input(self, *values, **kwargs):
        self.sent.append(('input', values, kwargs))
        return True

    def send_fire(self, *values):
        self.sent.append(('fire', values))
        return 1


def _runtime():
    avatar = _Avatar()
    compatibility = _Compatibility()
    bigworld = _BigWorld(avatar, compatibility)
    compatibility.bigworld = bigworld
    hangar_space = _HangarSpace(bigworld.operations)
    compatibility.hangar_space = hangar_space
    _AppLoader.showBattlePage = _APP_LOADER_SHOW_BATTLE_PAGE
    _AppLoader.showBattleLoading = _APP_LOADER_SHOW_BATTLE_LOADING
    _AppLoader.showLobby = _APP_LOADER_SHOW_LOBBY
    app_loader = _AppLoader()
    compatibility.app_loader = app_loader
    _AppLoader.battle_page_calls = mock.Mock(return_value=True)
    _AppLoader.battle_loading_calls = mock.Mock(return_value=True)
    _AppLoader.lobby_callback = None
    constants = types.SimpleNamespace(
        ARENA_GUI_TYPE=types.SimpleNamespace(RANDOM=1),
        ARENA_BONUS_TYPE=types.SimpleNamespace(REGULAR=2),
        ARENA_UPDATE=types.SimpleNamespace(
            VEHICLE_ADDED=2, PERIOD=3, VEHICLE_STATISTICS=5,
            VEHICLE_KILLED=6, AVATAR_READY=7, TEAM_KILLER=10),
        ARENA_PERIOD=types.SimpleNamespace(PREBATTLE=2, BATTLE=3),
        VEHICLE_PHYSICS_MODE=types.SimpleNamespace(STANDARD=0),
        VEHICLE_SIEGE_STATE=types.SimpleNamespace(DISABLED=0),
        VEHICLE_SETTING=types.SimpleNamespace(
            CURRENT_SHELLS=0, ACTIVATE_EQUIPMENT=16),
        VEHICLE_MISC_STATUS=types.SimpleNamespace(
            VEHICLE_DROWN_WARNING=4),
        DROWN_WARNING_LEVEL=types.SimpleNamespace(
            SAFE=0, CAUTION=1, DANGER=2),
        ATTACK_REASON=types.SimpleNamespace(
            SHOT='shot', FIRE='fire', RAM='ram',
            WORLD_COLLISION='world_collision',
            DEATH_ZONE='death_zone', DROWNING='drowning'),
        ATTACK_REASON_INDICES={
            'shot': 0, 'fire': 1, 'ram': 2,
            'world_collision': 3, 'death_zone': 4, 'drowning': 5},
        AMMOBAY_DESTRUCTION_MODE=types.SimpleNamespace(
            POWDER_BURN_OFF=0, POWDER_EXPLOSION=1, HE_DETONATION=2),
        DAMAGE_INFO_INDICES={
            'DEVICE_DESTROYED_AT_FIRE': 10,
            'DEVICE_CRITICAL_AT_WORLD_COLLISION': 11,
            'TANKMAN_HIT_AT_DROWNING': 12,
            'FIRE_STOPPED': 13,
        },
        VEHICLE_HIT_FLAGS=types.SimpleNamespace(
            ATTACK_IS_DIRECT_PROJECTILE=1,
            MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE=2,
            MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE=4,
            RICOCHET=8, VEHICLE_KILLED=16),
        FINISH_REASON=types.SimpleNamespace(
            UNKNOWN=0, EXTERMINATION=1, BASE=2, TIMEOUT=3))
    arena = types.SimpleNamespace(
        geometryName='01_karelia', gameplayName='ctf')
    width = 61
    graph_template = {
        'format': 'offline-lan-0922-navgraph', 'version': 2,
        'game_version': '0.9.22.0.1-cn-1513',
        'origin': (-120.0, -120.0), 'cell_size': 4.0,
        'bounds': (-120.0, -120.0, 120.0, 120.0),
        'width': width, 'height': width,
        'heights_mm': tuple([0] * (width * width)),
        'links': tuple([0] * (width * width)),
        'hazards': tuple([0] * (width * width)),
        'spawn_anchors': ((0.0, -40.0), (0.0, 40.0)),
        'objective_bases': ((0.0, 40.0), (0.0, -40.0)),
        'spawn_formations': {
            '1': tuple(((slot % 5 - 2) * 12.0, 0.0,
                        -80.0 + (slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple(((slot % 5 - 2) * 12.0, 0.0,
                        80.0 - (slot // 5) * 12.0, math.pi)
                       for slot in range(15)),
        },
        'routes': {
            '1': ({'id': 'test', 'capacity': 15, 'risk': 0.0,
                   'role_weights': {},
                   'waypoints': ((0.0, -40.0, False),
                                 (0.0, 40.0, False))},),
            '2': ({'id': 'test', 'capacity': 15, 'risk': 0.0,
                   'role_weights': {},
                   'waypoints': ((0.0, 40.0, False),
                                 (0.0, -40.0, False))},),
        },
    }

    def navigation_graph_loader(map_name):
        graph = dict(graph_template)
        graph['map'] = str(map_name)
        return graph

    def setup_turret_rotations(appearance):
        appearance.compoundModel.node('turret', appearance.turretMatrix)
        appearance.compoundModel.node(
            'gun_inclination', appearance.gunMatrix)

    return types.SimpleNamespace(
        account_commands=types.SimpleNamespace(
            CMD_GET_AVATAR_SYNC=1, CMD_ADD_INT_USER_SETTINGS=2,
            CMD_DEL_INT_USER_SETTINGS=3),
        arena_cache={1: arena}, bigworld=bigworld,
        avatar_input_handler=types.SimpleNamespace(
            _CTRL_MODE=types.SimpleNamespace(
                ARCADE='arcade', SNIPER='sniper')),
        app_loader=app_loader,
        compatibility=compatibility, constants=constants,
        battle_feedback_common=types.SimpleNamespace(
            BATTLE_EVENT_TYPE=types.SimpleNamespace(
                CRIT=6, DAMAGE=7, KILL=8, RECEIVED_CRIT=9,
                RECEIVED_DAMAGE=10,
                packDamage=lambda damage, reason: (
                    (int(damage) << 16) | (int(reason) << 9)),
                packCrits=lambda count, reason: (
                    (int(count) << 16) | (int(reason) << 8)))),
        encode_gun_angles=lambda *unused: 0,
        game=types.SimpleNamespace(abort=mock.Mock()),
        gui_global_space_id=types.SimpleNamespace(
            LOBBY=4, BATTLE_LOADING=5, BATTLE=6),
        hangar_space=types.SimpleNamespace(
            g_hangarSpace=hangar_space),
        math=types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix),
        model_assembler=types.SimpleNamespace(
            prepareCompoundAssembler=lambda descriptor, state, space, flag:
            descriptor,
            setupTurretRotations=setup_turret_rotations),
        offline_map_creator=_OfflineMap(bigworld, app_loader),
        navigation_graph_loader=navigation_graph_loader,
        vehicle_view_state=types.SimpleNamespace(RPM='rpm'),
        vehicles=types.SimpleNamespace(
            VehicleDescr=_VehicleDescr,
            g_cache=types.SimpleNamespace(shotEffects={
                3: {
                    'armorRicochet': ('ricochetStages', 'ricochetFx', None),
                    'armorResisted': ('resistedStages', 'resistedFx', None),
                    'armorHit': ('hitStages', 'hitFx', None),
                }})))


class RemoteVehicleFactoryTests(unittest.TestCase):
    def test_pose_collider_uses_visible_matrix_not_stale_native_pose(self):
        descriptor = _Descriptor()
        material = types.SimpleNamespace(armor=75.0)
        hit_tester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[
                (20.0, None, 1.0, 7)]))
        descriptor.hull['hitTester'] = hit_tester
        descriptor.hull['materials'] = {7: material}
        vehicle = _Vehicle(
            11, descriptor, _Vector(0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), {'health': 500})
        visible_matrix = _Matrix()
        visible_matrix.translation = _Vector(0.0, 0.0, 20.0)

        collisions = collide_vehicle_at_matrix(
            vehicle, visible_matrix, _Vector(0.0, 1.0, 0.0),
            _Vector(0.0, 1.0, 100.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))

        self.assertEqual(1, len(collisions))
        self.assertEqual(20.0, collisions[0].dist)
        self.assertIs(material, collisions[0].matInfo)
        self.assertEqual('vehicleHull', collisions[0].compName)
        self.assertEqual(4, len(collisions[0]))
        local_start, local_end = hit_tester.localHitTest.call_args[0]
        self.assertEqual(-20.0, local_start.z)
        self.assertEqual(80.0, local_end.z)

    def test_remote_collision_preserves_ext_shape_across_ticks_and_skip_gun(self):
        descriptor = _Descriptor()
        gun_material = types.SimpleNamespace(armor=25.0)
        hull_material = types.SimpleNamespace(armor=75.0)
        descriptor.gun.hitTester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[
                (4.0, None, 0.8, 3)]))
        descriptor.gun.materials = {3: gun_material}
        descriptor.hull['hitTester'] = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[
                (12.0, None, 0.9, 7)]))
        descriptor.hull['materials'] = {7: hull_material}
        vehicle = RemoteVehicle(
            1000, descriptor, {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        start = _Vector(0.0, 1.0, -20.0)
        end = _Vector(0.0, 1.0, 80.0)

        for unused_tick in range(5):
            collisions = vehicle.collideSegmentExt(start, end)
            self.assertEqual(
                ['vehicleGun', 'vehicleHull'],
                [collision.compName for collision in collisions])
            self.assertTrue(all(len(collision) == 4
                                for collision in collisions))
            self.assertEqual(
                [gun_material, hull_material],
                [collision.matInfo for collision in collisions])

        nearest = vehicle.collideSegment(start, end, skipGun=True)
        self.assertEqual(12.0, nearest.dist)
        self.assertEqual(0.9, nearest.hitAngleCos)
        self.assertEqual(75.0, nearest.armor)

    def test_pose_collider_rotates_ray_with_visible_hull_yaw(self):
        descriptor = _Descriptor()
        hit_tester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[
                (10.0, None, 1.0, 7)]))
        descriptor.hull['hitTester'] = hit_tester
        descriptor.hull['materials'] = {
            7: types.SimpleNamespace(armor=75.0)}
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        visible_matrix = _YawMatrix()
        visible_matrix.setRotateYPR((math.pi / 2.0, 0.0, 0.0))
        visible_matrix.translation = _Vector(10.0, 0.0, 20.0)

        collisions = collide_vehicle_at_matrix(
            vehicle, visible_matrix, _Vector(0.0, 1.0, 20.0),
            _Vector(20.0, 1.0, 20.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_YawMatrix))

        self.assertEqual(1, len(collisions))
        local_start, local_end = hit_tester.localHitTest.call_args[0]
        self.assertAlmostEqual(-10.0, local_start.z)
        self.assertAlmostEqual(10.0, local_end.z)
        self.assertAlmostEqual(0.0, local_start.x)
        self.assertAlmostEqual(0.0, local_end.x)

    def test_remote_engine_audition_uses_exact_1513_sound_object(self):
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        vehicle.model = _Model()
        sound_object = mock.Mock()
        sound_group = types.SimpleNamespace(
            WWgetSoundObject=mock.Mock(return_value=sound_object))
        sound_module = types.ModuleType('SoundGroups')
        sound_module.g_instance = sound_group

        with mock.patch.dict(sys.modules, {'SoundGroups': sound_module}):
            first = vehicle.appearance.engineAudition.getSoundObject(3)
            second = vehicle.appearance.engineAudition.getSoundObject(3)

        self.assertIs(sound_object, first)
        self.assertIs(first, second)
        sound_group.WWgetSoundObject.assert_called_once()
        name, node = sound_group.WWgetSoundObject.call_args[0]
        self.assertEqual('offline_lan_vehicle_1000_sound_3', name)
        self.assertIsNotNone(node)

    def test_remote_shot_effect_contract_failure_is_not_hidden(self):
        class BrokenExtra(object):
            def stopFor(self, unused_vehicle):
                return None

            def startFor(self, unused_vehicle, unused_burst):
                raise RuntimeError('shot sound contract failed')

        descriptor = _Descriptor()
        descriptor.extrasDict = {'shoot': BrokenExtra()}
        vehicle = RemoteVehicle(
            1000, descriptor, {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        vehicle.model = _Model()
        vehicle.isStarted = True
        vehicle.inWorld = True

        with self.assertRaisesRegex(
                RuntimeError, 'shot sound contract failed'):
            vehicle.showShooting(1, False)

    def test_remote_filter_implements_1513_three_argument_broad_phase(self):
        math_module = types.SimpleNamespace(Vector3=_Vector)
        remote_filter = _RemoteFilter(
            math_module, _Vector(0.0, 0.0, 0.0))

        self.assertTrue(remote_filter.segmentMayHitEntity(
            _Vector(-30.0, 0.0, 0.0), _Vector(30.0, 0.0, 0.0), True))
        self.assertFalse(remote_filter.segmentMayHitEntity(
            _Vector(-30.0, 50.0, 0.0), _Vector(30.0, 50.0, 0.0), False))

    def test_remote_collision_returns_exact_1513_nearest_tuple(self):
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        material = types.SimpleNamespace(armor=120.0)
        collision = types.SimpleNamespace(
            dist=0.25, hitAngleCos=0.75, matInfo=material,
            compName='vehicleHull')
        vehicle.collideSegmentExt = lambda start, end: [collision]

        result = vehicle.collideSegment(_Vector(), _Vector(1.0, 0.0, 0.0))

        self.assertEqual(0.25, result[0])
        self.assertEqual(0.25, result.dist)
        self.assertEqual(0.75, result.hitAngleCos)
        self.assertEqual(120.0, result.armor)
        self.assertEqual(3, len(result))

    def test_remote_shot_uses_stock_extra_recoil_and_1513_tracer(self):
        runtime = _runtime()
        original_entity = runtime.bigworld.entity
        runtime.bigworld.PyModelObstacle = mock.Mock(
            side_effect=AssertionError(
                'remote presentation must not create a second collider'))
        recoil = mock.Mock()
        assemble_recoil = mock.Mock(side_effect=lambda appearance, unused_lod:
                                    setattr(appearance, 'gunRecoil', recoil))
        setup_rotations = mock.Mock(side_effect=lambda appearance: (
            appearance.compoundModel.node(
                'turret', appearance.turretMatrix),
            appearance.compoundModel.node(
                'gun_inclination', appearance.gunMatrix)))
        runtime.model_assembler.assembleRecoil = assemble_recoil
        runtime.model_assembler.setupTurretRotations = setup_rotations
        runtime.bigworld.camera = lambda: types.SimpleNamespace(
            position=_Vector(50.0, 20.0, 30.0))
        projectiles = []

        class ProjectileMover(object):
            def __init__(self):
                self.add = mock.Mock()
                self.destroy = mock.Mock()
                projectiles.append(self)

        class ShootExtra(object):
            def __init__(self):
                self.started = []
                self.stopped = []

            def stopFor(self, entity):
                self.stopped.append(entity.id)
                entity.extras.pop('shoot-test', None)

            def startFor(self, entity, burst_count):
                self.started.append((entity.id, burst_count))
                entity.extras['shoot-test'] = True
                entity.appearance.recoil()

        descriptor = _Descriptor()
        descriptor.hull['models'] = {'undamaged': 'hull.model'}
        descriptor.turret.models = {'undamaged': 'turret.model'}
        shoot_extra = ShootExtra()
        descriptor.extrasDict = {'shoot': shoot_extra}
        descriptor.gun.burst = (3, 0.1)
        descriptor.gun.shots[0].speed = 950.0
        descriptor.gun.shots[0].gravity = 9.81
        descriptor.gun.shots[0].shell.effectsIndex = 7
        alternate_shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(effectsIndex=8),
            speed=1100.0, gravity=4.0, maxDistance=720.0)
        descriptor.gun.shots.append(alternate_shot)
        items_module = types.ModuleType('items')
        items_module.vehicles = types.SimpleNamespace(
            g_cache=types.SimpleNamespace(
                shotEffects={
                    7: {'projectile': 'ap-tracer'},
                    8: {'projectile': 'he-tracer'}}))
        projectile_module = types.ModuleType('ProjectileMover')
        projectile_module.ProjectileMover = ProjectileMover

        with mock.patch.dict(sys.modules, {
                'items': items_module,
                'ProjectileMover': projectile_module}):
            factory = RemoteVehicleFactory(
                runtime.bigworld, runtime.math, runtime.model_assembler, 7)
            vehicle_id = factory.create(descriptor, {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0}, _Vector(10.0, 2.0, 20.0),
                (0.0, 0.0, 0.0))
            vehicle = factory.get(vehicle_id)
            vehicle.set_aim(0.0, math.pi / 2.0, -0.1)
            vehicle._offlineLANShotIndex = 1

            battle = BattleRuntime(runtime)
            battle._remote_factory = factory
            battle._records = {
                'bot:11': {'engine_id': vehicle_id, 'local': False}}
            battle._show_shot({
                'kind': 'bot_shot', 'attacker_bot': 11,
                'shell_index': 1, 'shot_yaw': math.pi / 2.0,
                'shot_pitch': 0.1})

            self.assertEqual((3, False), vehicle.last_shot)
            self.assertEqual((True, True), vehicle.last_shot_effect)
            self.assertEqual([(vehicle_id, 3)], shoot_extra.started)
            assemble_recoil.assert_called_once_with(
                vehicle.appearance, None)
            setup_rotations.assert_called_once_with(vehicle.appearance)
            self.assertEqual([
                ('turret', vehicle.appearance.turretMatrix),
                ('gun_inclination', vehicle.appearance.gunMatrix),
            ], vehicle.model.node_bindings)
            recoil.recoil.assert_called_once_with()
            self.assertEqual(1, len(projectiles))
            projectile_args = projectiles[0].add.call_args[0]
            self.assertEqual(9, len(projectile_args))
            self.assertEqual({'projectile': 'he-tracer'}, projectile_args[1])
            self.assertEqual(4.0, projectile_args[2])
            self.assertAlmostEqual(math.cos(0.1) * 1100.0,
                                   projectile_args[4].x)
            self.assertAlmostEqual(math.sin(0.1) * 1100.0,
                                   projectile_args[4].y)
            self.assertAlmostEqual(0.0, projectile_args[4].z, places=5)
            self.assertEqual(720.0, projectile_args[6])
            self.assertEqual(vehicle_id, projectile_args[7])
            self.assertFalse(hasattr(vehicle, '_offlineLANShotYaw'))
            self.assertFalse(hasattr(vehicle, '_offlineLANShotPitch'))
            runtime.bigworld.PyModelObstacle.assert_not_called()
            self.assertIsNone(vehicle._collision_obstacle)

            factory.destroy_all()

            self.assertFalse(vehicle.showShooting(1, False))
            projectiles[0].add.assert_called_once_with(*projectile_args)

        self.assertGreaterEqual(shoot_extra.stopped.count(vehicle_id), 2)
        self.assertEqual({}, vehicle.extras)
        self.assertIsNone(vehicle._collision_obstacle)
        projectiles[0].destroy.assert_called_once_with()
        self.assertEqual(runtime.bigworld.entity, original_entity)

    def test_remote_shot_cleanup_failure_still_restores_entity_owners(self):
        runtime = _runtime()
        original_entity = runtime.bigworld.entity
        original_entities = runtime.bigworld.entities
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        factory._shot_presenter._mover = types.SimpleNamespace(
            destroy=mock.Mock(side_effect=RuntimeError('mover failed')))

        with self.assertRaisesRegex(RuntimeError, 'mover failed'):
            factory.destroy_all()

        self.assertEqual({}, factory._vehicles)
        self.assertEqual(runtime.bigworld.entity, original_entity)
        self.assertIs(runtime.bigworld.entities, original_entities)

    def test_compound_model_uses_separate_synthetic_vehicle_identity(self):
        runtime = _runtime()
        bigworld = runtime.bigworld
        original_entity = bigworld.entity
        original_entities = bigworld.entities
        factory = RemoteVehicleFactory(
            bigworld, runtime.math, runtime.model_assembler, 7)
        descriptor = _Descriptor()
        properties = {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}

        vehicle_id = factory.create(
            descriptor, properties, _Vector(10.0, 2.0, 30.0),
            (0.0, 0.0, 0.5))
        vehicle = factory.get(vehicle_id)

        self.assertEqual(1000, vehicle_id)
        self.assertTrue(factory.is_ready(vehicle_id))
        self.assertIsNone(bigworld.entity(vehicle_id))
        self.assertTrue(vehicle._offlineLANPresentation)
        self.assertEqual('Vehicle', vehicle.__class__.__name__)
        self.assertNotEqual(vehicle_id, vehicle.bw_entity_id)
        self.assertNotIn(vehicle_id, bigworld.entities)
        self.assertIsNone(bigworld.entities.get(vehicle_id))
        vehicle._spot_visible = True
        self.assertIs(vehicle, bigworld.entity(vehicle_id))
        self.assertIs(vehicle, bigworld.entities[vehicle_id])
        self.assertIs(vehicle, bigworld.entities.get(vehicle_id))
        vehicle._spot_visible = False
        self.assertIsNone(bigworld.entity(vehicle_id))
        self.assertNotIn(vehicle_id, bigworld.entities)
        self.assertIsNone(bigworld.entities.get(vehicle_id))
        vehicle._spot_visible = True
        vehicle.health = 0
        vehicle.isAlive.value = False
        self.assertIsNone(bigworld.entity(vehicle_id))
        self.assertNotIn(vehicle_id, bigworld.entities)
        vehicle.health = 500
        vehicle.isAlive.value = True
        self.assertIs(vehicle, bigworld.entity(vehicle_id))
        self.assertIs(vehicle.matrix, vehicle.model.matrix)
        self.assertEqual(
            (10.0, 2.0, 30.0), tuple(vehicle.matrix.translation))
        self.assertEqual(0.5, vehicle.matrix.yaw)
        self.assertEqual(
            (0.0, 0.0, 0.5),
            bigworld.created_offline_entities[-1]['rotation'])

        vehicle.set_pose(_Vector(20.0, 3.0, 40.0), (0.0, 0.0, 1.0))
        self.assertEqual((20.0, 3.0, 40.0), tuple(vehicle.position))
        self.assertIs(vehicle.matrix, vehicle.model.matrix)
        self.assertEqual(
            (20.0, 3.0, 40.0), tuple(vehicle.model.matrix.translation))
        self.assertEqual(1.0, vehicle.model.matrix.yaw)

        visual_id = vehicle.bw_entity_id
        visual = bigworld.entity(visual_id)
        model = vehicle.model
        factory.destroy_all()
        self.assertIsNone(bigworld.entity(visual_id))
        self.assertNotIn(vehicle_id, bigworld.entities)
        self.assertIs(bigworld.entities, original_entities)
        self.assertEqual(original_entity, bigworld.entity)
        self.assertIsNone(visual.model)
        self.assertIsNot(model.matrix, vehicle.matrix)
        self.assertEqual((0.0, 0.0, 0.0), tuple(model.matrix.translation))

    def test_manual_target_outline_uses_exact_1513_edge_api(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 1, 'name': 'Ally'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=20.0),)
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True}}

        battle._update_target_outline(1.0)

        self.assertEqual(
            [(vehicle.bw_entity, 2, 0, False)],
            runtime.bigworld.edge_adds)
        battle._clear_target_outline()
        self.assertEqual([vehicle.bw_entity], runtime.bigworld.edge_removes)
        factory.destroy_all()

    def test_remote_visual_cleanup_survives_destroy_entity_failure(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(10.0, 2.0, 30.0),
            (0.25, -0.1, 0.5))
        vehicle = factory.get(vehicle_id)
        visual = runtime.bigworld.entity(vehicle.bw_entity_id)
        model = vehicle.model
        runtime.bigworld.destroyEntity = mock.Mock(
            side_effect=RuntimeError('destroy failed'))

        with self.assertRaisesRegex(RuntimeError, 'destroy failed'):
            factory.destroy(vehicle_id)

        self.assertIsNone(visual.model)
        self.assertIsNot(model.matrix, vehicle.matrix)
        self.assertIsNone(vehicle.model)
        self.assertIsNone(vehicle.bw_entity)
        self.assertFalse(vehicle.inWorld)
        factory.restore()

    def test_destroy_before_resource_callback_prevents_late_visual(self):
        runtime = _runtime()
        callbacks = []
        runtime.bigworld.loadResourceListBG = (
            lambda assemblers, callback: callbacks.append(
                (assemblers, callback)))
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        descriptor = _Descriptor()
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 1}, 'health': 500,
            'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))

        self.assertTrue(factory.destroy(vehicle_id))
        callbacks[0][1]({descriptor.name: _Model()})

        self.assertIsNone(factory.get(vehicle_id))
        self.assertFalse(any(
            getattr(entity, 'model', None) is not None
            for entity in runtime.bigworld.entities.values()))
        factory.destroy_all()

    def test_factory_releases_each_unique_hit_tester_once(self):
        runtime = _runtime()
        tester = types.SimpleNamespace(
            loadBspModel=mock.Mock(), releaseBspModel=mock.Mock())
        descriptor = _Descriptor()
        descriptor.getHitTesters = lambda: (tester, tester)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        properties = {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0}

        factory.create(
            descriptor, properties, _Vector(), (0.0, 0.0, 0.0))
        factory.create(
            descriptor, properties, _Vector(), (0.0, 0.0, 0.0))
        factory.destroy_all()

        tester.loadBspModel.assert_called_once_with()
        tester.releaseBspModel.assert_called_once_with()

    def test_destroy_all_restores_every_owner_after_one_destroy_fails(self):
        runtime = _runtime()
        original_entity = runtime.bigworld.entity
        original_entities = runtime.bigworld.entities
        tester = types.SimpleNamespace(
            loadBspModel=mock.Mock(), releaseBspModel=mock.Mock())
        descriptor = _Descriptor()
        descriptor.getHitTesters = lambda: (tester,)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        properties = {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0}
        first = factory.create(
            descriptor, properties, _Vector(), (0.0, 0.0, 0.0))
        second = factory.create(
            descriptor, properties, _Vector(), (0.0, 0.0, 0.0))
        visual_ids = [factory.get(first).bw_entity_id,
                      factory.get(second).bw_entity_id]
        destroy = runtime.bigworld.destroyEntity
        attempted = []

        def fail_first(entity_id):
            attempted.append(entity_id)
            if len(attempted) == 1:
                raise RuntimeError('first visual failed')
            destroy(entity_id)

        runtime.bigworld.destroyEntity = fail_first
        with self.assertRaisesRegex(RuntimeError, 'first visual failed'):
            factory.destroy_all()

        self.assertEqual(visual_ids, attempted)
        self.assertEqual({}, factory._vehicles)
        tester.releaseBspModel.assert_called_once_with()
        self.assertEqual(runtime.bigworld.entity, original_entity)
        self.assertIs(runtime.bigworld.entities, original_entities)

    def test_failed_post_create_attach_destroys_orphan_visual(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        descriptor = _Descriptor()

        with mock.patch.object(
                RemoteVehicle, 'attach_visual',
                side_effect=RuntimeError('attach failed')):
            vehicle_id = factory.create(descriptor, {
                'publicInfo': {'team': 1}, 'health': 500,
                'isCrewActive': True, 'gunAnglesPacked': 0},
                _Vector(), (0.0, 0.0, 0.0))

        self.assertIn('attach failed', str(factory.error(vehicle_id)))
        self.assertFalse(any(
            getattr(entity, 'model', None) is not None
            for entity in runtime.bigworld.entities.values()))
        factory.destroy_all()


class BattleRuntimeContractTests(unittest.TestCase):
    def test_network_deadlines_remove_main_thread_delay_from_periods(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = False
        battle._config = {
            'prebattleCountdownSeconds': 15.0,
            'battleDurationSeconds': 900.0}
        battle.client = _Client()
        battle.client.combat_deadline = 110.0
        battle.client.combat_end_deadline = 1010.0
        battle.client.combat_duration = 900.0
        battle._binding = types.SimpleNamespace(arena_period=mock.Mock())
        battle._enable_prebattle_camera_controls = mock.Mock()
        battle._clock = mock.Mock(return_value=50.0)

        module = sys.modules[BattleRuntime.__module__]
        with mock.patch.object(
                module, '_monotonic_time', return_value=100.0):
            self.assertTrue(battle.on_battle_live({
                'countdown_seconds': 15.0,
                'battle_duration_seconds': 900.0}))

        battle._binding.arena_period.assert_called_once_with(
            'prebattle', 10.0)
        self.assertEqual(60.0, battle._prebattle_deadline)

        with mock.patch.object(
                module, '_monotonic_time', return_value=110.0):
            self.assertTrue(battle._begin_battle())
        self.assertEqual(
            mock.call('battle', 900.0),
            battle._binding.arena_period.call_args_list[-1])

    def test_native_shot_ray_is_copied_before_normalise_or_scatter(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        native_start = _ReadOnlyVector(1.0, 2.0, 3.0)
        native_direction = _ReadOnlyVector(0.0, 0.0, 4.0)
        battle._avatar.gunRotator.getCurShotPosition = lambda: (
            native_start, native_direction)

        start, direction = battle._mutable_shot_ray()
        direction.x = 0.25

        self.assertEqual((1.0, 2.0, 3.0),
                         (start.x, start.y, start.z))
        self.assertEqual((0.0, 0.0, 4.0),
                         (native_direction.x, native_direction.y,
                          native_direction.z))

    def test_local_tank_contact_uses_copied_separation_and_impulse(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(0.0, 0.0, 6.5),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = remote
        battle._records = {'player:2': {
            'engine_id': 11, 'network_id': 2, 'kind': 'player',
            'local': False, 'ready': True, 'tombstone': False,
            'state': {'x': 0.0, 'y': 0.0, 'z': 6.5, 'yaw': 0.0,
                      'speed': 0.0, 'alive': True}}}
        battle._local_physics = {'mass': 25000.0}
        battle._local_speed = 5.0
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._baked_pose_safe = mock.Mock(return_value=True)

        position = battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertLess(position[2], 0.0)
        self.assertLess(battle._local_speed, 5.0)
        battle._motion_is_clear.assert_called_once()
        battle._baked_pose_safe.assert_called_once()

    def test_local_tank_contact_cannot_push_hull_through_world_geometry(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(0.0, 0.0, 6.5),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = remote
        battle._records = {'player:2': {
            'engine_id': 11, 'network_id': 2, 'kind': 'player',
            'local': False, 'ready': True, 'tombstone': False,
            'state': {'x': 0.0, 'y': 0.0, 'z': 6.5, 'yaw': 0.0,
                      'speed': 0.0, 'alive': True}}}
        battle._local_physics = {'mass': 25000.0}
        battle._local_speed = 5.0
        battle._motion_is_clear = mock.Mock(return_value=False)
        battle._baked_pose_safe = mock.Mock(return_value=True)

        position = battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertEqual(0.0, battle._local_push_x)
        self.assertEqual(0.0, battle._local_push_z)
        battle._baked_pose_safe.assert_not_called()

    def test_active_exception_prints_original_traceback_before_cleanup(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._cleanup = lambda: None
        runtime.compatibility.restore_lobby_account = lambda: None
        battle.client = types.SimpleNamespace(on_event=None)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            try:
                raise RuntimeError('native operation denied')
            except RuntimeError as error:
                battle._fail(error)

        rendered = output.getvalue()
        self.assertIn('battle failed: native operation denied', rendered)
        self.assertIn('battle traceback:', rendered)
        self.assertIn('RuntimeError: native operation denied', rendered)
        self.assertIn(
            'test_active_exception_prints_original_traceback_before_cleanup',
            rendered)

    def test_selected_commander_sixth_sense_is_read_before_lobby_retire(self):
        tankman = types.SimpleNamespace(skills=(
            types.SimpleNamespace(name='commander_sixthSense'),))
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            item=types.SimpleNamespace(crew=((0, tankman),)))

        with mock.patch.dict(sys.modules, {
                'CurrentVehicle': current_vehicle}):
            self.assertTrue(_selected_vehicle_has_sixth_sense())

    def test_bot_observation_drives_only_enemy_visibility_feedback(self):
        battle = BattleRuntime(_runtime())
        observed = []
        battle.client = types.SimpleNamespace(player_id=7, team=1)
        battle._sixth_sense = types.SimpleNamespace(
            observe=lambda visible, now: observed.append((visible, now)))
        message = {'type': 'bot_observation', 'contacts': [
            {'target_kind': 'human', 'target_id': 7,
             'observing_team': 1, 'visible': True},
            {'target_kind': 'human', 'target_id': 7,
             'observing_team': 2, 'visible': True},
        ]}

        self.assertTrue(battle._observe_local_vehicle(message, 12.5))
        self.assertEqual([(True, 12.5)], observed)

    def test_bigworld_entity_rotation_keeps_yaw_out_of_roll(self):
        prohorovka_team_one_yaw = 2.947

        self.assertEqual(
            (0.0, 0.0, prohorovka_team_one_yaw),
            _engine_rotation(prohorovka_team_one_yaw))
        self.assertEqual(
            (-0.25, 0.125, prohorovka_team_one_yaw),
            _engine_rotation(prohorovka_team_one_yaw, 0.125, -0.25))

    def test_standard_arena_matches_space_prefixed_geometry_name(self):
        runtime = _runtime()
        arena = types.SimpleNamespace(
            geometryName='spaces/31_airfield', gameplayName='ctf')
        runtime.arena_cache = {7: arena}
        battle = BattleRuntime(runtime)

        self.assertIs(arena, battle._standard_arena('31_airfield'))

    def test_baked_formation_slot_is_reused_without_runtime_nudging(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._config = {'map': '31_airfield'}
        battle._navigation_graph = runtime.navigation_graph_loader(
            '31_airfield')

        first = battle._formation_pose(1, 3)
        second = battle._formation_pose(1, 3)

        self.assertEqual(first, second)
        self.assertEqual(((12.0, 0.0, -80.0), 0.0), first)

    def test_missing_baked_formation_fails_instead_of_searching_locally(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._config = {'map': '01_karelia'}
        battle._navigation_graph = {'map': '01_karelia'}

        with self.assertRaisesRegex(ValueError, 'spawn formations are missing'):
            battle._formation_pose(1, 0)

    def test_bot_roster_uses_complete_catalog_without_persistent_pool(self):
        runtime = _runtime()
        runtime.nations = types.SimpleNamespace(
            AVAILABLE_NAMES=('ussr',), INDICES={'ussr': 0})
        entries = {
            1: types.SimpleNamespace(
                level=8, tags=frozenset(('heavyTank',)),
                name='ussr:heavy'),
            2: types.SimpleNamespace(
                level=8, tags=frozenset(('mediumTank',)),
                name='ussr:medium'),
        }
        runtime.vehicles.g_list = types.SimpleNamespace(
            getList=lambda unused_nation_id: entries)
        descriptor = _Descriptor('china:Ch22_113P')
        descriptor.type.level = 8
        battle = BattleRuntime(runtime)
        battle._config = {'vehicle': descriptor.name}
        battle._start_message = {'players': [
            {'id': 1, 'team': 1, 'vehicle': descriptor.name},
        ], 'bots': [
            {'team': 1, 'slot': 0}, {'team': 1, 'slot': 1},
            {'team': 2, 'slot': 0}, {'team': 2, 'slot': 1},
        ]}
        battle.client = types.SimpleNamespace(team=1, player_id=1)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.random.random',
                side_effect=(0.0, 0.0)), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.random.shuffle',
                    side_effect=lambda values: None):
            self.assertTrue(
                battle._prepare_bot_vehicle_assignments(descriptor))

        assignments = battle._bot_vehicle_assignments
        self.assertEqual(4, len(assignments))
        self.assertIn('ussr:heavy', assignments.values())
        self.assertIn('ussr:medium', assignments.values())
        self.assertIn(descriptor.name, assignments.values())
        self.assertNotIn('_BOT_POOL_BY_TIER', vars(sys.modules[
            'gui.mods.offline_lan_0922.battle_runtime']))

    def test_game_abort_is_rejected_and_original_is_restored(self):
        runtime = _runtime()
        original_abort = runtime.game.abort
        normal_create = runtime.offline_map_creator.create

        def create_then_abort(unused_map_name):
            runtime.game.abort()

        runtime.offline_map_creator.create = create_then_abort
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {'round_id': 3}, _Client()))

        self.assertIs(original_abort, runtime.game.abort)
        original_abort.assert_not_called()
        self.assertEqual('failed', battle.state)
        self.assertIn('game.abort', battle.error)

        runtime.offline_map_creator.create = normal_create
        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {'round_id': 4}, _Client()))

    def test_game_abort_patch_does_not_overwrite_a_newer_patch(self):
        runtime = _runtime()

        def newer_abort():
            return 'newer'

        def replace_during_create(unused_map_name):
            runtime.game.abort = newer_abort

        runtime.offline_map_creator.create = replace_during_create
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {'round_id': 3}, _Client()))

        self.assertIs(newer_abort, runtime.game.abort)
        self.assertEqual('newer', runtime.game.abort())

    def test_lobby_is_retired_before_native_map_without_viewer_camera(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))

        self.assertEqual(
            [('account_retire',), ('hangar_destroy',),
             ('clear_entities_spaces',),
             ('map_create', '01_karelia'),
             ('watcher', 'Visibility/GUI', True)],
            runtime.bigworld.operations)
        self.assertEqual(0, runtime.offline_map_creator.viewer_camera_calls)
        self.assertFalse(hasattr(runtime.app_loader, '__dict__'))
        type(runtime.app_loader).battle_page_calls.assert_not_called()
        self.assertFalse(runtime.offline_map_creator.Active())
        self.assertTrue(runtime.bigworld.avatar._offlineLANPlayerReady)

        runtime.app_loader.showBattlePage()
        type(runtime.app_loader).battle_page_calls.assert_called_once_with()

    def test_incomplete_hangar_fails_before_native_clear(self):
        runtime = _runtime()
        hangar = runtime.hangar_space.g_hangarSpace
        hangar.spaceInited = False
        runtime.offline_map_creator.create = mock.Mock()
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        self.assertEqual([], runtime.bigworld.operations)
        runtime.offline_map_creator.create.assert_not_called()
        self.assertEqual('failed', battle.state)
        self.assertIn('hangar space is not ready', battle.error)

    def test_incomplete_hangar_destroy_fails_before_native_clear(self):
        runtime = _runtime()
        hangar = runtime.hangar_space.g_hangarSpace

        def incomplete_destroy():
            runtime.bigworld.operations.append(('hangar_destroy',))
            hangar.inited = False

        hangar.destroy = incomplete_destroy
        runtime.offline_map_creator.create = mock.Mock()
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        self.assertEqual(
            [('account_retire',), ('hangar_destroy',),
             ('clear_entities_spaces',)],
            runtime.bigworld.operations)
        runtime.offline_map_creator.create.assert_not_called()
        self.assertEqual('failed', battle.state)
        self.assertIn(
            'Account retirement did not destroy the hangar space',
            battle.error)

    def test_failed_lobby_clear_uses_second_boundary_before_restore(self):
        runtime = _runtime()

        def failing_clear():
            runtime.bigworld.operations.append(('clear_failed',))
            raise RuntimeError('first clear failed')

        def fallback_clear():
            runtime.bigworld.operations.append(('clear_all_spaces',))
            runtime.bigworld.avatar = None

        runtime.bigworld.clearEntitiesAndSpaces = failing_clear
        runtime.bigworld.clearAllSpaces = fallback_clear
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        self.assertEqual([
            ('account_retire',), ('hangar_destroy',), ('clear_failed',),
            ('clear_failed',), ('clear_all_spaces',),
            ('offline_disconnect',),
        ], runtime.bigworld.operations)
        self.assertFalse(getattr(
            runtime.compatibility, 'account_restored', False))
        self.assertEqual(1, runtime.compatibility.disconnect_calls)
        self.assertEqual('failed', battle.state)
        self.assertIn('first clear failed', battle.error)

    def test_retained_lobby_account_is_forced_out_before_restore(self):
        runtime = _runtime()

        def retaining_clear():
            runtime.bigworld.operations.append(('clear_retained',))

        def fallback_clear():
            runtime.bigworld.operations.append(('clear_all_spaces',))
            runtime.bigworld.avatar = None

        runtime.bigworld.clearEntitiesAndSpaces = retaining_clear
        runtime.bigworld.clearAllSpaces = fallback_clear
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        self.assertEqual([
            ('account_retire',), ('hangar_destroy',), ('clear_retained',),
            ('clear_retained',), ('clear_all_spaces',),
        ], runtime.bigworld.operations)
        self.assertTrue(runtime.compatibility.account_restored)
        self.assertEqual('failed', battle.state)
        self.assertIn('lobby Account survived', battle.error)

    def test_missing_viewer_camera_boundary_fails_closed_and_restores_lobby(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        calls = []
        runtime.offline_map_creator._OfflineMapCreator__setupCamera = None
        runtime.offline_map_creator.create = mock.Mock()

        def destroy():
            calls.append('destroy')
            runtime.bigworld.avatar = None

        def restore():
            self.assertIsNone(runtime.bigworld.avatar)
            calls.append('restore')

        runtime.offline_map_creator.destroy = destroy
        runtime.compatibility.restore_lobby_account = restore

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        runtime.offline_map_creator.create.assert_not_called()
        self.assertEqual(['destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)
        self.assertFalse(battle._map_create_attempted)

    def test_missing_battle_page_boundary_fails_closed_and_restores_lobby(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        calls = []
        type(runtime.app_loader).showBattlePage = None
        runtime.offline_map_creator.create = mock.Mock()

        def destroy():
            calls.append('destroy')
            runtime.bigworld.avatar = None

        def restore():
            self.assertIsNone(runtime.bigworld.avatar)
            calls.append('restore')

        runtime.offline_map_creator.destroy = destroy
        runtime.compatibility.restore_lobby_account = restore

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        runtime.offline_map_creator.create.assert_not_called()
        self.assertEqual(['restore'], calls)
        self.assertEqual('failed', battle.state)
        self.assertFalse(battle._map_create_attempted)

    def test_battle_page_patch_does_not_overwrite_a_newer_class_patch(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        def newer_show_battle_page(unused_loader):
            return 'newer'

        def replace_during_create(unused_map_name):
            runtime.app_loader.showBattlePage()
            type(runtime.app_loader).showBattlePage = \
                newer_show_battle_page
            runtime.offline_map_creator._OfflineMapCreator__setupCamera()

        runtime.offline_map_creator.create = replace_during_create

        battle._create_native_battle_map('01_karelia')

        self.assertIs(
            newer_show_battle_page,
            type(runtime.app_loader).__dict__['showBattlePage'])
        self.assertEqual('newer', runtime.app_loader.showBattlePage())

    def test_map_to_native_vehicle_to_ready_lifecycle(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))

        self.assertEqual('loading_entities', battle.state)
        self.assertIsNotNone(battle._server.vehicle_id)
        self.assertEqual(
            battle._server.vehicle_id,
            runtime.bigworld.avatar.playerVehicleID)
        self.assertEqual(
            battle._server.vehicle_id,
            runtime.bigworld.avatar.arena_dp.player_vehicle_id)
        self.assertEqual(1, runtime.bigworld.avatar.arena_dp.refreshes)
        self.assertEqual(
            runtime.constants.ARENA_UPDATE.VEHICLE_ADDED,
            runtime.bigworld.avatar.arena_updates[0][0])
        self.assertIsNone(runtime.bigworld.entity(battle._server.vehicle_id))
        pending = runtime.bigworld.pending_entities[battle._server.vehicle_id]
        self.assertEqual(0.0, pending.rotation[0])
        self.assertEqual(0.0, pending.rotation[1])
        self.assertEqual(battle._local_yaw, pending.rotation[2])
        self.assertEqual([(4, 5)], runtime.app_loader.transitions)

        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        self.assertEqual('loading_entities', battle.state)
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('running', battle.state)
        self.assertEqual(1, runtime.bigworld.avatar.vehicle_changed)
        self.assertFalse(battle._server.setClientReady())
        self.assertEqual(500, runtime.bigworld.entity(
            battle._server.vehicle_id).health)

    def test_player_identity_sync_rejects_arena_dp_mismatch(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._avatar.guiSessionProvider.getArenaDP = lambda: (
            types.SimpleNamespace(
                isRequiredDataExists=lambda: True,
                getPlayerVehicleID=lambda forceUpdate=True: 11))

        with self.assertRaisesRegex(RuntimeError, 'refresh mismatch'):
            battle._synchronise_player_identity(10)

    def test_player_identity_sync_refreshes_exact_1513_zero_cache(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        arena_dp = battle._avatar.arena_dp

        self.assertEqual(0, arena_dp.getPlayerVehicleID(True))
        self.assertEqual(0, arena_dp.refreshes)
        self.assertTrue(battle._synchronise_player_identity(10))
        self.assertEqual(10, arena_dp.getPlayerVehicleID(False))
        self.assertEqual(1, arena_dp.refreshes)

    def test_player_identity_sync_requires_current_bound_avatar(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        arena_dp = battle._avatar.arena_dp
        runtime.bigworld.avatar = _Avatar()
        runtime.bigworld.avatar.playerVehicleID = 10

        with self.assertRaisesRegex(RuntimeError, 'BigWorld player changed'):
            battle._synchronise_player_identity(10)
        self.assertEqual(0, arena_dp.refreshes)

    def test_player_identity_sync_requires_avatar_id_before_arena_refresh(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 9
        arena_dp = battle._avatar.arena_dp

        with self.assertRaisesRegex(
                RuntimeError, 'Avatar player identity mismatch'):
            battle._synchronise_player_identity(10)
        self.assertEqual(0, arena_dp.refreshes)

    def test_player_identity_sync_requires_team_before_arena_refresh(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._avatar.team = 0
        arena_dp = battle._avatar.arena_dp

        with self.assertRaisesRegex(RuntimeError, 'Avatar team is invalid'):
            battle._synchronise_player_identity(10)
        self.assertEqual(0, arena_dp.refreshes)

    def test_local_feedback_rejects_player_identity_drift(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 12
        battle._avatar.arena_dp.player_vehicle_id = 12
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}

        with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
            battle._present_combat_feedback({
                'kind': 'bot_hit', 'damage': 50, 'shot_result': 2,
                'dead': False, 'attack_reason': 0, 'death_reason': 0,
                'source': 'shot'}, target, attacker)

    def test_empty_loading_snapshot_cannot_tombstone_authority_bots(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        player = {
            'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
            'vehicle': 'ussr:R11_MS-1', 'health': 500}
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [player],
            # The server start barrier reserves identities but intentionally
            # has no canonical pose until the authority publishes a manifest.
            'bots': [{
                'id': 11, 'team': 2, 'slot': 0, 'name': 'Enemy 1'}, {
                'id': 12, 'team': 2, 'slot': 1, 'name': 'Enemy 2'}]}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        battle.on_snapshot({
            'round_id': 1, 'server_tick': 1,
            'players': [player], 'bots': []})
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        self.assertIn('bot:11', battle._pending_bot_creates)
        self.assertIn('bot:12', battle._pending_bot_creates)
        manifests = [value[1] for value in client.sent
                     if value[0] == 'manifest']
        self.assertEqual(1, len(manifests))

        # A second empty snapshot can race with the outbound authority
        # manifest. It must not register/tombstone the local lineup either.
        battle.on_snapshot({
            'round_id': 1, 'server_tick': 2,
            'players': [player], 'bots': []})
        self.assertNotIn('bot:11', battle._sync._entities)
        canonical_bots = [dict(
            value, critical={}, combat_revision=0,
            combat_base_revision=0, combat_ack_seq=0,
            combat_fire_elapsed=0.0, combat_fire_timer=0.0)
            for value in manifests[0]]
        battle.on_snapshot({
            'round_id': 1, 'server_tick': 3,
            'players': [player], 'bots': canonical_bots})
        self.assertFalse(battle._sync._entities['bot:11']['dead'])
        self.assertFalse(battle._sync._entities['bot:12']['dead'])

        battle._frame()
        self.assertIn('bot:11', battle._records)
        self.assertNotIn('bot:12', battle._records)
        runtime.bigworld.now += 0.29
        battle._frame()
        self.assertNotIn('bot:12', battle._records)
        runtime.bigworld.now += 0.02
        battle._frame()
        self.assertIn('bot:12', battle._records)

    def test_human_readiness_starts_countdown_while_bots_materialize(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        client.send_battle_ready = mock.Mock(return_value=True)
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': [{
                'id': 11, 'team': 2, 'slot': 0, 'name': 'Enemy 1'}, {
                'id': 12, 'team': 2, 'slot': 1, 'name': 'Enemy 2'}]}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual(2, len(battle._pending_bot_create_order))
        self.assertFalse(battle._ready_sent)
        battle._frame()

        client.send_battle_ready.assert_called_once()
        self.assertTrue(battle._ready_sent)
        self.assertEqual(1, len(battle._pending_bot_create_order))
        self.assertFalse(battle._battle_live)
        # Enemy models may finish loading during the countdown, but their
        # marker/minimap visual is not registered before the first real spot.
        self.assertEqual(0, len(runtime.bigworld.avatar.visual_starts))
        enemy = battle._records['bot:11']
        self.assertFalse(enemy['spot_visible'])
        remote = battle._remote_factory.get(enemy['engine_id'])
        self.assertFalse(remote.model.visible)
        self.assertIsNone(runtime.bigworld.entity(enemy['engine_id']))
        self.assertIsNone(runtime.bigworld.entities.get(enemy['engine_id']))
        self.assertNotIn(enemy['engine_id'], runtime.bigworld.entities)

        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 11, 'alive': True, 'x': 17.0, 'y': 2.0, 'z': 19.0,
            'yaw': 0.75, 'aim_yaw': 0.9, 'gun_pitch': -0.1}]))

        self.assertEqual((17.0, 2.0, 19.0), tuple(remote.position))
        self.assertAlmostEqual(0.75, remote.yaw)
        self.assertAlmostEqual(0.9, remote._aim_yaw)
        self.assertAlmostEqual(-0.1, remote._gun_pitch)
        self.assertIsNone(runtime.bigworld.entity(enemy['engine_id']))
        self.assertIsNone(runtime.bigworld.entities.get(enemy['engine_id']))

        battle._apply_health(enemy, {'health': 125, 'alive': True})

        self.assertEqual(125, remote.health)
        self.assertIsNone(runtime.bigworld.entity(enemy['engine_id']))
        self.assertNotIn(enemy['engine_id'], runtime.bigworld.entities)

        battle._destroy_entity({'entity': 'bot:11'})

        self.assertNotIn('bot:11', battle._records)
        self.assertIsNone(battle._remote_factory.get(enemy['engine_id']))
        self.assertIsNone(runtime.bigworld.entity(enemy['engine_id']))

    def test_direction_probe_copies_dual_distance_three_lane_corridor(self):
        runtime = _runtime()
        rays = []
        original = runtime.bigworld.wg_collideSegment

        def collide(space_id, start, end, mask):
            rays.append((start, end))
            return original(space_id, start, end, mask)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        result = battle._direction_probe((0.0, 0.0, 0.0), 0.0, 6.0)

        self.assertTrue(result['clear'])
        horizontal = [(start, end) for start, end in rays
                      if abs(start.y - end.y) < 0.001]
        self.assertEqual(6, len(horizontal))
        self.assertEqual(20.0, max(end.z for unused, end in horizontal))
        self.assertEqual({-2.2, 0.0, 2.2},
                         {round(end.x, 1) for unused, end in horizontal})

    def test_bot_firing_lane_trims_hulls_and_tries_two_target_heights(self):
        runtime = _runtime()
        rays = []

        def collide(unused_space_id, start, end, unused_mask):
            rays.append((start, end))
            return (_Vector(start.x + 1.0, start.y, start.z),)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {'position': (0.0, 0.0, 100.0)}

        self.assertFalse(battle._bot_firing_lane(source, target))

        self.assertEqual(2, len(rays))
        self.assertEqual({1.5, 2.2}, {round(end.y, 1)
                                     for unused, end in rays})
        self.assertTrue(all(round(start.z, 1) == 4.0
                            for start, unused in rays))
        self.assertTrue(all(round(end.z, 1) == 96.0
                            for unused, end in rays))

        runtime.bigworld.wg_collideSegment = lambda *unused: None
        self.assertTrue(battle._bot_firing_lane(source, target))

    def test_bot_firing_lane_probes_close_targets_instead_of_assuming_clear(self):
        runtime = _runtime()
        rays = []

        def wall(unused_space_id, start, end, unused_mask):
            rays.append((start, end))
            return (_Vector(start.x, start.y, start.z + 0.25),)

        runtime.bigworld.wg_collideSegment = wall
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {'position': (0.0, 0.0, 8.0)}

        self.assertFalse(battle._bot_firing_lane(source, target))
        self.assertEqual(2, len(rays))
        self.assertTrue(all(start.z < end.z for start, end in rays))

        runtime.bigworld.wg_collideSegment = lambda *unused: None
        self.assertTrue(battle._bot_firing_lane(source, target))

    def test_direction_and_graph_probes_reject_drowning_depth_water(self):
        runtime = _runtime()
        runtime.bigworld.wg_collideWater = lambda *unused: 18.5
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        result = battle._direction_probe((0.0, 0.0, 0.0), 0.0)

        self.assertTrue(result['water'])
        self.assertFalse(result['clear'])
        self.assertIsNone(battle._navigation_ground(0.0, 8.0, 0.0))

    def test_local_drowning_uses_native_warning_and_preserves_display_hp(self):
        runtime = _runtime()
        runtime.bigworld.serverTime = lambda: 750.0
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._server = types.SimpleNamespace(vehicle_id=10)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True}}
        battle._water_depth = lambda unused: 2.0
        battle._local_last_attacker = ('player', 9)
        battle._present_critical = mock.Mock(return_value=True)
        critical = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False,
            'events': []}

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.apply_drowning', return_value=critical):
            self.assertFalse(battle._tick_drowning(0.3, 1.0))
            self.assertEqual((10, 4, 2, (750.0, 10.0)),
                             battle._avatar.misc_status)
            for index in range(34):
                battle._tick_drowning(0.3, 1.3 + index * 0.3)

        self.assertEqual(500, entity.health)
        self.assertFalse(entity.isCrewActive)
        self.assertTrue(entity.previous_crew_active)
        self.assertTrue(entity._drowned)
        self.assertEqual((10, 500, 5, False, False),
                         battle._avatar.health_update)
        self.assertEqual(5, battle._local_damage_report['reason'])
        self.assertEqual(500,
                         battle._local_damage_report['display_health'])
        self.assertNotIn('attacker', battle._local_damage_report)
        self.assertNotIn('attacker_bot', battle._local_damage_report)

    def test_socket_write_does_not_acknowledge_local_critical_report(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._server = types.SimpleNamespace(vehicle_id=10)
        runtime.bigworld.entities[10] = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 450})
        battle._sender = _LANInputSender(battle)
        battle._local_damage_report = {
            'critical': {'events': []}, 'reason': 2,
            'critical_base_revision': 0, 'critical_seq': 1}

        self.assertTrue(battle._sender.send_current())

        self.assertIsNotNone(battle._local_damage_report)
        kwargs = battle.client.sent[-1][2]
        self.assertEqual(450, kwargs['reported_health'])
        self.assertEqual(2, kwargs['reported_reason'])
        self.assertEqual({'events': []}, kwargs['reported_critical'])
        self.assertEqual(0, kwargs['reported_critical_base_revision'])
        self.assertEqual(1, kwargs['reported_critical_seq'])
        self.assertTrue(battle.acknowledge_local_damage_report(0, 1, 1))
        self.assertIsNone(battle._local_damage_report)

    def test_snapshot_critical_state_recovers_missed_native_hud_events(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'], 'crew_ko': ['driver'],
            'fire': True, 'ammo_rack_death': False, 'events': []}
        battle._present_critical = mock.Mock(return_value=True)

        bigworld_module = types.ModuleType('BigWorld')
        bigworld_module.player = runtime.bigworld.player
        with mock.patch.dict(sys.modules, {'BigWorld': bigworld_module}):
            self.assertTrue(battle._apply_critical_state(record, payload))

        events = battle._present_critical.call_args.args[1]
        self.assertEqual(
            set([('device', 'destroyed'), ('crew', 'destroyed'),
                 ('fire', True)]),
            set((event['kind'], event['state']) for event in events))
        self.assertTrue(all(event['cause'] == 'shot' for event in events))
        with mock.patch.dict(sys.modules, {'BigWorld': bigworld_module}):
            self.assertFalse(battle._apply_critical_state(record, payload))
        self.assertEqual(1, battle._present_critical.call_count)

    def test_local_critical_echo_does_not_replay_native_hud_event(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        canonical = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {
                'health': 500, 'alive': True, 'critical': canonical},
            'critical_state': canonical,
            'kind': 'player', 'network_id': 1, 'local': True}}
        battle._present_critical = mock.Mock(return_value=True)

        self.assertTrue(battle._apply_combat_event({
            'kind': 'health', 'target': 1, 'health': 499,
            'critical': dict(canonical), 'critical_revision': 1,
            'critical_base_revision': 0, 'critical_ack_seq': 1,
            'source': 'client_simulation', 'attack_reason': 0,
            'death_reason': 0}))

        battle._present_critical.assert_not_called()

    def test_local_repair_snapshot_ack_does_not_rewind_live_progress(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        live = {
            'devices': [{'name': 'engineHealth', 'hp': 35.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        echoed = dict(live)
        echoed['devices'] = [dict(live['devices'][0], hp=10.0)]
        entity.devices_hp = {'engineHealth': 35.0}
        record = {
            'engine_id': 10, 'state': {
                'health': 500, 'alive': True, 'critical': live},
            'critical_state': live, 'critical_revision': 0,
            'kind': 'player', 'network_id': 1, 'local': True}
        battle._local_critical_owned = True
        battle._local_critical_base_revision = 0
        battle._local_critical_next_seq = 1
        battle._local_damage_report = {
            'critical': live, 'critical_base_revision': 0,
            'critical_seq': 1}

        self.assertFalse(battle._apply_critical_state(record, echoed, {
            'critical_revision': 1, 'critical_base_revision': 0,
            'critical_ack_seq': 1}))

        self.assertEqual(35.0, entity.devices_hp['engineHealth'])
        self.assertEqual(live, record['state']['critical'])
        self.assertIsNone(battle._local_damage_report)

    def test_duplicate_ordered_event_is_presented_once(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'
        message = {'events': [{
            'event_id': '1:7:0', 'kind': 'battle_result', 'winner': 2,
            'reason': 'team_eliminated'}]}

        battle.on_events(message)
        battle.on_events(message)

        self.assertEqual(1, len(runtime.bigworld.avatar.round_finished))

    def test_pending_shot_is_accepted_then_applied_once_when_ready(self):
        battle = BattleRuntime(_runtime())
        battle._pending_bot_creates = {
            'bot:11': {'state': {'health': 500, 'alive': True}}}
        battle._pending_bot_create_order = ['bot:11']
        battle._show_shot = mock.Mock(return_value=True)
        message = {'events': [{
            'event_id': '1:7:0', 'kind': 'bot_shot',
            'attacker_bot': 11, 'shell_index': 0}]}

        self.assertTrue(battle.on_events(message))
        self.assertTrue(battle.on_events(message))
        self.assertIn('1:7:0', battle._accepted_event_ids)
        self.assertNotIn('1:7:0', battle._applied_event_ids)
        self.assertEqual(1, len(battle._event_journal))
        battle._show_shot.assert_not_called()

        pending = battle._pending_bot_creates.pop('bot:11')
        battle._pending_bot_create_order = []
        battle._records['bot:11'] = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 11,
            'state': pending['state'], 'ready': True, 'local': False}

        self.assertTrue(battle._drain_event_journal())
        self.assertIn('1:7:0', battle._applied_event_ids)
        self.assertEqual([], battle._event_journal)
        battle._show_shot.assert_called_once_with(
            message['events'][0], update_state=False)

    def test_pending_combat_merges_state_before_native_presentation(self):
        battle = BattleRuntime(_runtime())
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True,
            'ready': True}}
        battle._pending_bot_creates = {
            'bot:11': {'state': {'health': 500, 'alive': True}}}
        battle._pending_bot_create_order = ['bot:11']
        battle._apply_combat_event = mock.Mock(return_value=True)
        event = {
            'event_id': '1:8:0', 'kind': 'bot_hit', 'attacker': 1,
            'target_bot': 11, 'health': 0, 'dead': True,
            'source': 'shot', 'attack_reason': 0, 'death_reason': 3}

        self.assertTrue(battle.on_events({'events': [event]}))
        pending = battle._pending_bot_creates['bot:11']
        self.assertEqual(0, pending['state']['health'])
        self.assertFalse(pending['state']['alive'])
        self.assertEqual('player', pending['state']['death_attacker_kind'])
        self.assertEqual(1, pending['state']['death_attacker_id'])
        self.assertIn('1:8:0', battle._accepted_event_ids)
        self.assertNotIn('1:8:0', battle._applied_event_ids)
        battle._apply_combat_event.assert_not_called()

        battle._pending_bot_creates.pop('bot:11')
        battle._pending_bot_create_order = []
        battle._records['bot:11'] = {
            'engine_id': 1000, 'state': pending['state'],
            'kind': 'bot', 'network_id': 11, 'local': False,
            'ready': False}
        self.assertFalse(battle._drain_event_journal())
        battle._records['bot:11']['ready'] = True
        self.assertTrue(battle._drain_event_journal())

        battle._apply_combat_event.assert_called_once_with(
            event, update_state=False)
        self.assertIn('1:8:0', battle._applied_event_ids)

    def test_pending_combat_blocks_snapshot_native_reconciliation(self):
        battle = BattleRuntime(_runtime())
        battle._binding = mock.Mock()
        target = {
            'engine_id': 1000, 'state': {'health': 500, 'alive': True},
            'kind': 'bot', 'network_id': 11, 'local': False,
            'ready': True}
        battle._records = {'bot:11': target}
        battle._pending_bot_creates = {
            'bot:12': {'state': {'health': 500, 'alive': True}}}
        battle._pending_bot_create_order = ['bot:12']
        battle._apply_health = mock.Mock()
        event = {
            'event_id': '1:9:0', 'kind': 'bot_bot_hit',
            'attacker_bot': 12, 'target_bot': 11,
            'health': 250, 'dead': False,
            'source': 'shot', 'attack_reason': 0, 'death_reason': 0}

        self.assertTrue(battle.on_events({'events': [event]}))
        self.assertTrue(battle._materialize_record(target))

        self.assertEqual(250, target['state']['health'])
        battle._apply_health.assert_not_called()
        self.assertNotIn('1:9:0', battle._applied_event_ids)

    def test_keep_corpse_preserves_pending_create_and_live_initial_state(self):
        battle = BattleRuntime(_runtime())
        battle._queue_bot_create({
            'type': 'create', 'entity': 'bot:11', 'kind': 'bot', 'id': 11,
            'state': {'team': 2, 'slot': 0, 'x': 1.0, 'z': 2.0,
                      'health': 500, 'alive': True}})

        battle._destroy_entity({
            'entity': 'bot:11', 'keep_corpse': True,
            'state': {'health': 0, 'alive': False, 'death_reason': 3}})

        pending = battle._pending_bot_creates['bot:11']
        self.assertIn('bot:11', battle._pending_bot_create_order)
        self.assertEqual(500, pending['initial_state']['health'])
        self.assertEqual(0, pending['state']['health'])
        self.assertFalse(pending['state']['alive'])
        self.assertEqual(3, pending['state']['death_reason'])

    def test_unknown_ordered_entity_fails_battle_before_acceptance(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._fail = mock.Mock()

        self.assertFalse(battle.on_events({'events': [{
            'event_id': '1:10:0', 'kind': 'bot_shot',
            'attacker_bot': 99}]}))

        self.assertNotIn('1:10:0', battle._accepted_event_ids)
        error = battle._fail.call_args.args[0]
        self.assertIn('unknown entity bot:99', str(error))

    def test_unknown_ordered_event_kind_fails_before_acceptance(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._fail = mock.Mock()

        self.assertFalse(battle.on_events({'events': [{
            'event_id': '1:10:1', 'kind': 'future_magic'}]}))

        self.assertNotIn('1:10:1', battle._accepted_event_ids)
        error = battle._fail.call_args.args[0]
        self.assertIn('kind is unsupported: future_magic', str(error))

    def test_ordered_native_exception_fails_without_marking_applied(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True,
            'ready': True}}
        battle._show_shot = mock.Mock(
            side_effect=RuntimeError('native shot failed'))
        battle._fail = mock.Mock()

        self.assertFalse(battle.on_events({'events': [{
            'event_id': '1:11:0', 'kind': 'shot', 'attacker': 1}]}))

        self.assertIn('1:11:0', battle._accepted_event_ids)
        self.assertNotIn('1:11:0', battle._applied_event_ids)
        self.assertEqual(1, len(battle._event_journal))
        error = battle._fail.call_args.args[0]
        self.assertEqual('native shot failed', str(error))

    def test_repair_hp_reports_only_on_transition_or_checkpoint(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = types.SimpleNamespace(player_id=1)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}}
        battle._present_critical = mock.Mock(return_value=False)
        battle._present_repair_progress = mock.Mock(return_value=True)
        clock = [100.0]
        battle._clock = lambda: clock[0]
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 20.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}

        with mock.patch.object(
                critical_damage, 'tick_repair', return_value=payload), \
                mock.patch.object(
                    critical_damage, 'tick_fire', return_value=(0, None)):
            battle._tick_critical_states(0.1)
            self.assertIsNotNone(battle._local_damage_report)
            battle._local_damage_report = None
            clock[0] = 100.2
            battle._tick_critical_states(0.1)
            self.assertIsNone(battle._local_damage_report)
            clock[0] = 101.1
            battle._tick_critical_states(0.1)
            self.assertIsNotNone(battle._local_damage_report)
            battle._local_damage_report = None
            payload['events'] = [{
                'kind': 'device', 'name': 'engineHealth',
                'state': 'critical', 'cause': 'repair'}]
            clock[0] = 101.2
            battle._tick_critical_states(0.1)

        self.assertIsNotNone(battle._local_damage_report)

    def test_repair_progress_closes_with_zero_seconds_once(self):
        runtime = _runtime()
        runtime.constants.VEHICLE_MISC_STATUS.\
            DESTROYED_DEVICE_IS_REPAIRING = 17
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        extra = types.SimpleNamespace(name='leftTrackHealth')
        descriptor.extrasDict = {'leftTrackHealth': extra}
        descriptor.extras = {3: extra}
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.devices_hp = {'leftTrackHealth': 25.0}
        entity._destroyed_devices = set(['leftTrackHealth'])

        with mock.patch.object(
                critical_damage._device_damage, 'device_regen_hp',
                return_value=50.0), mock.patch.object(
                    critical_damage._device_damage, 'repair_seconds',
                    return_value=10.0):
            self.assertTrue(battle._present_repair_progress(entity))
            entity._destroyed_devices.clear()
            self.assertTrue(battle._present_repair_progress(entity))
            self.assertTrue(battle._present_repair_progress(entity))

        self.assertEqual([
            (10, 17, 3 | (50 << 8), (5.0,)),
            (10, 17, 3, (0.0,)),
        ], battle._avatar.misc_statuses)

    def test_local_victim_gets_native_hit_direction_and_world_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        target = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        attacker = _Vehicle(11, _Descriptor(), _Vector(10, 0, 0),
                            (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: target, 11: attacker})
        battle._local_position = (0.0, 0.0, 0.0)
        target_record = {
            'engine_id': 10, 'state': {'health': 500},
            'kind': 'player', 'network_id': 1, 'local': True}
        attacker_record = {
            'engine_id': 11,
            'state': {'health': 500, 'x': 10.0, 'y': 0.0, 'z': 0.0},
            'kind': 'bot', 'network_id': 2, 'local': False}
        event = {
            'kind': 'bot_human_hit', 'world_pose': True,
            'x': 0.5, 'y': 1.0, 'z': 0.0, 'shell_index': 0,
            'shot_result': 2, 'damage': 144, 'source': 'shot'}

        self.assertTrue(battle._present_combat_hit(
            event, target_record, attacker_record, 11))

        direction = battle._avatar.hit_directions[-1]
        self.assertEqual((11, 144, 10),
                         (direction[1], direction[2], direction[6]))
        self.assertAlmostEqual(-math.pi / 2.0, direction[0])
        effect = battle._avatar.terrainEffects.addNew.call_args
        self.assertEqual(('hitFx', 'hitStages'),
                         (effect.args[1], effect.args[2]))
        self.assertTrue(effect.kwargs['showShockWave'])
        self.assertTrue(effect.kwargs['showFlashBang'])

    def test_critical_presentation_uses_exact_causes_and_ammo_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {'engine_id': 10, 'local': True}
        events = [
            {'kind': 'device', 'name': 'engineHealth',
             'state': 'destroyed', 'cause': 'fire'},
            {'kind': 'device', 'name': 'leftTrackHealth',
             'state': 'critical', 'cause': 'world_collision'},
            {'kind': 'crew', 'name': 'driver',
             'state': 'destroyed', 'cause': 'drowning'},
            {'kind': 'fire', 'state': False, 'cause': 'repair'},
            {'kind': 'ammo_rack', 'state': 'destroyed', 'cause': 'shot'},
        ]

        with mock.patch.object(
                battle, '_critical_extra_index', return_value=7):
            self.assertTrue(battle._present_critical(record, events, 99))

        self.assertEqual([
            (10, 10, 7, 99, 0), (10, 11, 7, 99, 0),
            (10, 12, 7, 99, 0), (10, 13, 0, 99, 0)],
            battle._avatar.damage_info)
        self.assertEqual([(2, 0.0, 0.0)], entity.ammo_bay_effects)

    def test_server_hit_uses_stock_shot_result_and_battle_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._avatar.arena_dp.isRequiredDataExists()
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        event = {
            'kind': 'bot_hit', 'damage': 144, 'shot_result': 2,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot',
            'critical': {'events': [{
                'kind': 'device', 'name': 'engineHealth',
                'state': 'critical', 'cause': 'shot'}]}}

        self.assertTrue(battle._present_combat_feedback(
            event, target, attacker))

        self.assertEqual(1, len(battle._avatar.shot_results))
        packed = battle._avatar.shot_results[0][0]
        self.assertEqual(11, packed & 0xffffffff)
        self.assertEqual(3, packed >> 32)
        self.assertEqual([7, 6], [
            value['eventType']
            for value in battle._avatar.battle_events[0]])

    def test_local_ram_of_ally_updates_health_without_projectile_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        battle._avatar.arena_dp.isRequiredDataExists()
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        ally = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1), (0, 0, 0),
                        {'health': 500})
        runtime.bigworld.entities.update({10: attacker, 11: ally})
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'team': 1, 'health': 500},
                'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {
                'engine_id': 11, 'state': {'team': 1, 'health': 500},
                'kind': 'bot', 'network_id': 2, 'local': False,
                'presentation': True},
        }

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'damage': 50, 'health': 450, 'dead': False,
            'attack_reason': 2, 'death_reason': 0, 'source': 'ram'}))

        self.assertEqual((450, 10, 2), ally.health_change)
        present_health = battle._avatar.guiSessionProvider.setVehicleHealth
        present_health.assert_called_once_with(False, 11, 450, 10, 2)
        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)
        battle._avatar.terrainEffects.addNew.assert_not_called()

    def test_local_ram_of_enemy_uses_ram_efficiency_without_shot_results(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._avatar.arena_dp.isRequiredDataExists()
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}

        self.assertTrue(battle._present_combat_feedback({
            'kind': 'bot_hit', 'damage': 50, 'dead': False,
            'attack_reason': 2, 'death_reason': 0, 'source': 'ram'},
            target, attacker))

        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([7], [
            value['eventType']
            for value in battle._avatar.battle_events[0]])
        self.assertEqual((50 << 16) | (2 << 9),
                         battle._avatar.battle_events[0][0]['details'])

    def test_local_projectile_at_ally_keeps_stock_ally_hit_only(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._avatar.arena_dp.isRequiredDataExists()
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 1}}

        self.assertFalse(battle._present_combat_feedback({
            'kind': 'bot_hit', 'damage': 50, 'shot_result': 2,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot'}, target, attacker))

        self.assertEqual(1, len(battle._avatar.shot_results))
        self.assertEqual(11,
                         battle._avatar.shot_results[0][0] & 0xffffffff)
        self.assertEqual([], battle._avatar.battle_events)

    def test_received_friendly_projectile_has_no_enemy_efficiency_event(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        attacker = {
            'engine_id': 11, 'local': False, 'kind': 'player',
            'network_id': 2, 'state': {'team': 1}}
        target = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}

        self.assertFalse(battle._present_combat_feedback({
            'kind': 'hit', 'damage': 50, 'shot_result': 2,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot'}, target, attacker))

        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)

    def test_fire_feedback_never_uses_projectile_result_or_impact_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._avatar.arena_dp.isRequiredDataExists()
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}
        event = {
            'kind': 'bot_hit', 'damage': 10, 'dead': False,
            'attack_reason': 1, 'death_reason': 0, 'source': 'fire'}

        self.assertFalse(battle._present_combat_hit(
            event, target, attacker, 10))
        self.assertTrue(battle._present_combat_feedback(
            event, target, attacker))

        self.assertEqual([], battle._avatar.shot_results)
        battle._avatar.terrainEffects.addNew.assert_not_called()
        self.assertEqual((10 << 16) | (1 << 9),
                         battle._avatar.battle_events[0][0]['details'])

    def test_combat_attack_reason_is_mandatory_and_matches_source(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        with self.assertRaisesRegex(RuntimeError, 'no attack_reason'):
            battle._combat_attack_reason({'source': 'ram'})
        with self.assertRaisesRegex(RuntimeError, 'no source'):
            battle._combat_attack_reason({'attack_reason': 0})
        with self.assertRaisesRegex(RuntimeError, 'does not match source'):
            battle._combat_attack_reason({
                'source': 'ram', 'attack_reason': 0})
        self.assertEqual(2, battle._combat_attack_reason({
            'source': 'ram', 'attack_reason': 2, 'death_reason': 0}))
        self.assertIsNone(battle._combat_attack_reason({
            'source': 'player_left', 'attack_reason': None,
            'death_reason': 0}))
        with self.assertRaisesRegex(RuntimeError, 'null attack_reason'):
            battle._combat_attack_reason({
                'source': 'player_left', 'attack_reason': 0,
                'death_reason': 0})

    def test_combat_source_contract_rejects_implicit_or_mixed_causes(self):
        battle = BattleRuntime(_runtime())

        with self.assertRaisesRegex(RuntimeError, 'no source'):
            battle._validate_combat_event_contract({
                'kind': 'bot_hit', 'attacker': 1,
                'attack_reason': 0, 'death_reason': 0})
        with self.assertRaisesRegex(RuntimeError, 'does not allow kind'):
            battle._validate_combat_event_contract({
                'kind': 'health', 'attacker': 1, 'source': 'shot',
                'attack_reason': 0, 'death_reason': 0})
        with self.assertRaisesRegex(RuntimeError, 'must not have an attacker'):
            battle._validate_combat_event_contract({
                'kind': 'health', 'attacker': 1,
                'source': 'client_simulation', 'attack_reason': 0,
                'death_reason': 0})
        with self.assertRaisesRegex(RuntimeError, 'must not have an attacker'):
            battle._validate_combat_event_contract({
                'kind': 'health', 'attacker': 1,
                'source': 'player_left', 'attack_reason': None,
                'death_reason': 0})

    def test_player_left_is_nonattack_health_cause_without_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._records = {'player:2': {
            'engine_id': 11, 'state': {'health': 500, 'team': 2},
            'kind': 'player', 'network_id': 2, 'local': False,
            'presentation': True}}

        with mock.patch.object(
                critical_damage, 'apply_death', return_value=None):
            self.assertTrue(battle._apply_combat_event({
                'kind': 'health', 'target': 2, 'damage': 500,
                'health': 0, 'dead': True, 'source': 'player_left',
                'attack_reason': None, 'death_reason': 0}))

        self.assertEqual((0, 0, 0), entity.health_change)
        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)

    def test_prebattle_freezes_input_and_publishes_battle_after_countdown(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'prebattleCountdownSeconds': 15.0,
            'battleDurationSeconds': 900.0}, start, client))
        self.assertIs(client, runtime.compatibility.network_client)
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        periods = [pickle.loads(zlib.decompress(payload))
                   for kind, payload in runtime.bigworld.avatar.arena_updates
                   if kind == runtime.constants.ARENA_UPDATE.PERIOD]
        self.assertEqual(
            [(2, 25.0, 15.0, []), (2, 25.0, 15.0, [])], periods)
        self.assertEqual(
            [runtime.constants.ARENA_PERIOD.BATTLE,
             runtime.constants.ARENA_PERIOD.BATTLE],
            runtime.bigworld.avatar.inputHandler.started_periods)
        self.assertFalse(battle._battle_live)
        battle._sender.forward = 1.0
        self.assertFalse(battle.shoot(0.0, 0.0))
        local = runtime.bigworld.entity(battle._server.vehicle_id)

        runtime.bigworld.now = 24.9
        battle._frame()
        self.assertEqual([], local.teleports)
        self.assertFalse(battle._battle_live)

        runtime.bigworld.now = 25.0
        battle._frame()
        self.assertTrue(battle._battle_live)
        periods = [pickle.loads(zlib.decompress(payload))
                   for kind, payload in runtime.bigworld.avatar.arena_updates
                   if kind == runtime.constants.ARENA_UPDATE.PERIOD]
        self.assertEqual((3, 925.0, 900.0, []), periods[-1])

    def test_reentrant_vehicle_enter_fails_before_roster_publication(self):
        runtime = _runtime()
        runtime.bigworld.reenter_vehicle_during_create = True
        created_avatars = []
        original_create = runtime.offline_map_creator.create

        def record_created_avatar(map_name):
            original_create(map_name)
            created_avatars.append(runtime.bigworld.avatar)

        runtime.offline_map_creator.create = record_created_avatar
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))

        self.assertEqual('failed', battle.state)
        self.assertIn(
            'Vehicle entered before createEntity returned', battle.error)
        self.assertEqual(1, len(created_avatars))
        self.assertEqual([], created_avatars[0].arena_updates)

    def test_local_vehicle_ready_timeout_recovers_lobby(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'startupTimeoutSeconds': 0.5}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.now = battle._vehicle_ready_deadline
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('failed', battle.state)
        self.assertIn('did not enter world', battle.error)
        self.assertTrue(runtime.compatibility.account_restored)

    def test_vehicle_ready_gets_a_fresh_timeout_after_slow_map_load(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        runtime.bigworld.space_status = 0.0
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'startupTimeoutSeconds': 30.0}, start, client))
        map_deadline = battle._deadline
        runtime.bigworld.now = map_deadline - 0.1
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('loading_entities', battle.state)
        self.assertEqual(0.0, battle._vehicle_ready_deadline)
        runtime.bigworld.space_status = 1.0
        runtime.bigworld.callbacks.pop(0)()
        self.assertGreater(battle._vehicle_ready_deadline, map_deadline)

    def test_initial_ammo_failure_does_not_leave_a_frame_callback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.avatar.updateVehicleAmmo = mock.Mock(
            side_effect=RuntimeError('ammo failed'))
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('failed', battle.state)
        self.assertIsNone(battle._callback_id)
        self.assertIsNone(battle._ammo_callback_id)
        self.assertEqual([], runtime.bigworld.callbacks)

    def test_gui_guard_orders_fast_page_and_ignores_late_loading(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        battle._install_battle_gui_guard()
        runtime.app_loader.showBattlePage()
        runtime.app_loader.showBattleLoading()

        self.assertEqual([(4, 5), (5, 6)], runtime.app_loader.transitions)
        type(runtime.app_loader).battle_loading_calls.assert_called_once_with()
        type(runtime.app_loader).battle_page_calls.assert_called_once_with()
        battle._restore_battle_gui_guard()
        self.assertIs(
            _APP_LOADER_SHOW_BATTLE_LOADING,
            type(runtime.app_loader).__dict__['showBattleLoading'])
        self.assertIs(
            _APP_LOADER_SHOW_BATTLE_PAGE,
            type(runtime.app_loader).__dict__['showBattlePage'])

    def test_gui_guard_does_not_trust_ctx_after_rejected_loading(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        type(runtime.app_loader).battle_loading_calls.return_value = False

        battle._install_battle_gui_guard()
        runtime.app_loader.showBattlePage()

        # Exact changeSpace() has already polluted __ctx.guiSpaceID, which is
        # what public getSpaceID() returns, but LobbyState rejected the change.
        self.assertEqual(5, runtime.app_loader.getSpaceID())
        self.assertEqual(4, runtime.app_loader.actual_space_id)
        self.assertEqual([(4, 5)], runtime.app_loader.transitions)
        type(runtime.app_loader).battle_page_calls.assert_not_called()

    def test_gui_guard_never_enters_loading_from_waiting(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        runtime.app_loader.space_id = 7
        runtime.app_loader.actual_space_id = 7

        with self.assertRaisesRegex(
                RuntimeError, 'not in the lobby state'):
            battle._install_battle_gui_guard()

        type(runtime.app_loader).battle_loading_calls.assert_not_called()

    def test_stale_callback_cannot_clear_a_new_generation_handle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._generation = 1
        old_call = mock.Mock()
        new_call = mock.Mock()

        battle._schedule(0.0, old_call)
        old_wrapper = runtime.bigworld.callbacks.pop(0)
        battle._generation = 2
        battle._schedule(0.0, new_call)
        new_handle = battle._callback_id

        old_wrapper()

        self.assertEqual(new_handle, battle._callback_id)
        self.assertFalse(old_call.called)
        runtime.bigworld.callbacks.pop(0)()
        self.assertIsNone(battle._callback_id)
        new_call.assert_called_once_with()

    def test_local_vehicle_enter_failure_never_publishes_ready(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        vehicle_id = battle._server.vehicle_id
        avatar = battle._avatar
        battle._server.acceptVehicleEnter(vehicle_id)
        battle._server.failVehicleEnter(
            vehicle_id, RuntimeError('native enter failed'))
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('failed', battle.state)
        self.assertIn('native enter failed', battle.error)
        self.assertFalse(any(
            update[0] == runtime.constants.ARENA_UPDATE.AVATAR_READY
            for update in avatar.arena_updates))

    def test_copied_player_physics_pose_is_published_to_lan(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        entity.speed = 7.5
        entity.filter.angularSpeed = 0.25
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        battle._drive_local(0.1)

        self.assertGreater(battle._local_position[2], 4.0)
        self.assertGreater(battle._local_speed, 0.0)
        self.assertEqual(0.0, battle._local_turn_speed)
        self.assertEqual([], entity.teleports)
        self.assertIs(entity.model.matrix, battle._local_matrix)
        self.assertEqual(
            battle._local_position, tuple(entity.model.matrix.translation))
        self.assertTrue(runtime.bigworld.avatar.positions)
        self.assertTrue(client.sent)

    def test_local_motion_notifies_destructibles_before_collision_probe(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._destructibles = mock.Mock()

        def motion_is_clear(*unused_args):
            battle._destructibles._fell_trees_near.assert_called_once()
            return True

        battle._motion_is_clear = mock.Mock(side_effect=motion_is_clear)

        battle._drive_local(0.1)

        call = battle._destructibles._fell_trees_near.call_args[0]
        self.assertEqual(7, call[0])
        self.assertEqual((2.0, 3.0, 4.0), tuple(call[1]))
        self.assertEqual(0.0, call[2])
        self.assertGreater(call[3], 0.0)
        self.assertIs(entity.typeDescriptor, call[4])

    def test_destroyed_track_locks_drive_and_turn_through_brake_path(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.is_tracked = True
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._local_speed = 6.0
        battle._local_turn_speed = 0.7

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=0.0) as drive, \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0) as turn:
            battle._drive_local(0.1)

        self.assertEqual(0.0, drive.call_args[0][2])
        self.assertTrue(drive.call_args[0][8])
        self.assertEqual(0.0, turn.call_args[0][2])
        self.assertEqual(0.0, turn.call_args[0][1])
        self.assertEqual(0.0, turn.call_args.kwargs['drive_intent'])

    def test_dead_engine_coasts_without_locking_tracks(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.is_engine_dead = True
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=0.0) as drive, \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0) as turn:
            battle._drive_local(0.1)

        self.assertEqual(0.0, drive.call_args[0][2])
        self.assertFalse(drive.call_args[0][8])
        self.assertEqual(0.0, turn.call_args[0][2])

    def test_damaged_modules_scale_throttle_and_traverse(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        def factor(unused_entity, stat):
            return {'mobility': 0.5, 'traverse': 0.4}[stat]

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.stat_factor', side_effect=factor), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.longitudinal_step', return_value=0.0) as drive, \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=2.0) as turn:
            battle._drive_local(0.1)

        self.assertEqual(0.5, drive.call_args[0][2])
        self.assertEqual(1.0, turn.call_args[0][2])
        self.assertEqual(0.5, turn.call_args.kwargs['drive_intent'])
        self.assertAlmostEqual(0.8, battle._local_turn_speed)

    def test_existing_arcade_camera_tracks_copied_player_matrix(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        camera = battle._avatar.inputHandler.\
            _AvatarInputHandler__curCtrl.camera
        stale_matrix = camera.vehicleMProv

        battle._attach_local_presentation()
        battle._bind_local_arcade_camera()
        battle._drive_local(0.1)

        self.assertIsNot(stale_matrix, camera.vehicleMProv)
        self.assertIs(battle._local_matrix, camera.vehicleMProv)
        calculator = battle._avatar.inputHandler.\
            steadyVehicleMatrixCalculator
        output = calculator.\
            _SteadyVehicleMatrixCalculator__outputMProv
        stabilised = calculator.\
            _SteadyVehicleMatrixCalculator__stabilisedMProv
        self.assertIs(battle._local_matrix, output.rotationSrc)
        self.assertIs(battle._local_matrix, output.translationSrc)
        self.assertIs(battle._local_matrix, stabilised.target)
        self.assertEqual(
            battle._local_position,
            tuple(camera.vehicleMProv.translation))

    def test_sniper_transition_rejects_stale_steady_sources(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        handler = battle._avatar.inputHandler
        calculator = handler.steadyVehicleMatrixCalculator
        output = calculator.\
            _SteadyVehicleMatrixCalculator__outputMProv
        stabilised = calculator.\
            _SteadyVehicleMatrixCalculator__stabilisedMProv
        output.rotationSrc = object()
        output.translationSrc = object()
        stabilised.target = object()
        handler._AvatarInputHandler__ctrlModeName = 'sniper'

        with self.assertRaisesRegex(
                RuntimeError, 'captured a stale vehicle pose'):
            battle._on_control_mode_changed(handler, 'sniper')

        output.rotationSrc = battle._local_matrix
        output.translationSrc = battle._local_matrix
        stabilised.target = battle._local_matrix
        self.assertTrue(battle._on_control_mode_changed(handler, 'sniper'))

    def test_local_rpm_uses_native_vehicle_state_channel(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()

        self.assertTrue(battle._publish_rpm(10.0, force=True))
        battle._avatar.guiSessionProvider.invalidateVehicleState.\
            assert_called_once_with('rpm', 0.0)

        battle._local_speed = 7.0
        self.assertTrue(battle._publish_rpm(10.1))
        state, value = battle._avatar.guiSessionProvider.\
            invalidateVehicleState.call_args.args
        self.assertEqual('rpm', state)
        self.assertGreater(value, 0.3)
        self.assertLessEqual(value, 1.0)
        self.assertFalse(battle._publish_rpm(10.15))

    def test_native_gun_stabilised_provider_tracks_copied_player_matrix(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4),
                          (0, 0, 0), {'health': 500})
        native_matrix = entity.matrix
        entity.filter.stabilisedMatrix = native_matrix
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (2.0, 3.0, 4.0)

        battle._attach_local_presentation()
        provider = battle._avatar._PlayerAvatar__ownVehicleStabMProv

        self.assertIs(battle._local_matrix, provider.target)
        battle._local_yaw = 1.25
        battle._local_position = (5.0, 3.0, 9.0)
        battle._update_local_presentation(entity)
        self.assertAlmostEqual(1.25, provider.target.yaw)
        self.assertEqual((5.0, 3.0, 9.0),
                         tuple(provider.target.translation))

        battle._detach_local_presentation()

        self.assertIs(native_matrix, provider.target)

    def test_native_reverse_sample_preserves_yaw_component_order(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        yaw = 2.947
        entity = _Vehicle(
            10, _Descriptor(),
            _Vector(-math.sin(yaw) * 2.0, 0.0,
                    -math.cos(yaw) * 2.0),
            _engine_rotation(yaw), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=-1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_position = (0.0, 0.0, 0.0)
        battle._local_yaw = yaw
        battle._local_speed = -3.0
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        battle._drive_local(0.1)

        displacement_along_forward = (
            math.sin(yaw) * battle._local_position[0] +
            math.cos(yaw) * battle._local_position[2])
        self.assertLess(battle._local_speed, 0.0)
        self.assertLess(displacement_along_forward, 0.0)
        direction = runtime.bigworld.avatar.positions[-1][1]
        self.assertAlmostEqual(0.0, direction.x)
        self.assertAlmostEqual(0.0, direction.y)
        self.assertAlmostEqual(yaw, direction.z)

    def test_local_pose_tracks_successive_copied_physics_steps(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})

        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        battle._drive_local(0.02)
        first_z = battle._local_position[2]
        battle._drive_local(0.02)

        self.assertGreater(battle._local_position[2], first_z)
        self.assertEqual([], entity.teleports)

    def test_copied_integrator_owns_player_collision_and_vertical_motion(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(4, -5, 8), (0, 0, 0),
                          {'health': 500})
        entity.speed = 4.0
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_position = (4.0, -5.0, 8.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._ground_y = mock.Mock(return_value=0.0)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=4.0) as step:
            battle._drive_local(0.02)

        step.assert_called_once()
        battle._motion_is_clear.assert_called()
        battle._ground_y.assert_called()
        self.assertGreater(battle._local_position[2], 8.0)
        self.assertEqual(4.0, battle._local_speed)

    def test_first_streamed_ground_snaps_spawn_without_fall_damage(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 100, 0),
                          (0, 0, 0), {'health': 500})

        position = battle._update_vertical_motion(
            entity, (0.0, 100.0, 0.0), 0.0, 0.04)

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertTrue(battle._local_fall_armed)
        self.assertFalse(battle._local_airborne)
        self.assertEqual(500, entity.health)

    def test_armed_ledge_fall_uses_copied_damage_and_reason(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 20, 0),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True}}
        battle._local_fall_armed = True
        position = (0.0, 20.0, 0.0)

        for unused in range(30):
            position = battle._update_vertical_motion(
                entity, position, 0.0, 0.1)
            if not battle._local_airborne:
                break

        self.assertEqual(0.0, position[1])
        self.assertFalse(battle._local_airborne)
        self.assertLess(entity.health, 500)
        self.assertEqual(3, entity.health_change[2])
        self.assertEqual(3, battle._local_damage_report['reason'])
        self.assertNotIn('attacker', battle._local_damage_report)
        self.assertNotIn('attacker_bot', battle._local_damage_report)
        self.assertEqual(entity.health,
                         battle._records['player:1']['state']['health'])

    def test_cross_heading_steep_slope_uses_copied_slide_law(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        battle._ground_y = lambda x, unused_z, unused_hint=0.0, **unused: -0.6 * x

        battle._ground_pitch((0.0, 0.0, 0.0), 0.0, descriptor)
        position = battle._apply_slope_slide(
            (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertGreater(battle._local_slope_tangent, 0.44)
        self.assertGreater(battle._local_slide_speed, 0.0)
        self.assertGreater(position[0], 0.0)
        self.assertLess(position[1], 0.0)

    def test_airborne_slope_drift_is_carried_without_new_ground_slide(self):
        battle = BattleRuntime(_runtime())
        battle._local_airborne = True
        battle._local_slide_speed = 4.0
        battle._local_air_lateral = (2.0, -1.0)

        position = battle._apply_slope_slide(
            (0.0, 10.0, 0.0), 0.0, 0.1)

        self.assertEqual((0.2, 10.0, -0.1), position)
        self.assertEqual(0.0, battle._local_slide_speed)
        self.assertEqual((1.99, -0.995), battle._local_air_lateral)

    def test_drive_pitch_skips_bridge_deck_above_the_hull(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        def collision(unused_space, start, unused_end, unused_mask):
            # First see an overhead bridge; the copied probe restarts below it
            # and then reaches the drivable terrain.
            if start.y > 7.5:
                return (_Vector(start.x, 8.0, start.z),)
            return (_Vector(start.x, 1.0 if start.z > 0 else 0.0,
                            start.z),)

        runtime.bigworld.wg_collideSegment = collision

        self.assertAlmostEqual(
            -math.atan2(1.0, 4.0),
            battle._drive_pitch((0.0, 0.0, 0.0), 0.0))

    def test_drive_pitch_median_rejects_one_frame_geometry_spike(self):
        battle = BattleRuntime(_runtime())
        readings = iter((0.2, 0.2, 0.9, 0.2, 0.2))
        battle._drive_pitch = lambda *unused: next(readings)

        values = [battle._smoothed_drive_pitch((0, 0, 0), 0.0)
                  for unused in range(5)]

        self.assertLess(max(values), 0.2)
        self.assertAlmostEqual(0.19375, values[-1])

    def test_landing_combines_lateral_impact_and_retains_skid(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}}
        battle._local_air_lateral = (9.0, 0.0)

        damage = battle._apply_landing_impact(entity, 6.0)

        self.assertGreater(damage, 0)
        self.assertEqual((0.0, 0.0), battle._local_air_lateral)
        self.assertEqual(9.0, battle._local_slide_speed)

    def test_relative_gun_tracking_uses_delta_and_stop_uses_hull_yaw(self):
        owner = types.SimpleNamespace(
            local_pose=lambda: ((100.0, 5.0, 200.0), 0.5),
            client=types.SimpleNamespace(send_input=mock.Mock(return_value=True)))
        owner.shoot = mock.Mock(return_value=True)
        owner._echo_local_gun_angles = mock.Mock(return_value=True)
        sender = _LANInputSender(owner)

        sender.send_avatar_input(1, 'track_relative', {
            'point': _Vector(10.0, 2.0, 20.0)})
        self.assertAlmostEqual(math.atan2(10.0, 20.0), sender.aim_yaw)
        self.assertAlmostEqual(math.atan2(2.0, math.sqrt(500.0)),
                               sender.gun_pitch)
        owner._echo_local_gun_angles.assert_called_once_with()

        sender.send_avatar_input(1, 'stop_tracking', {
            'turret_yaw': 0.25, 'gun_pitch': -0.1})
        self.assertAlmostEqual(0.75, sender.aim_yaw)
        self.assertAlmostEqual(-0.1, sender.gun_pitch)
        self.assertEqual(
            [mock.call(), mock.call(0.25, -0.1)],
            owner._echo_local_gun_angles.call_args_list)

    def test_native_cruise_flags_preserve_r_f_throttle_presets(self):
        send_input = mock.Mock(return_value=True)
        owner = types.SimpleNamespace(
            local_pose=lambda: ((0.0, 0.0, 0.0), 0.0),
            client=types.SimpleNamespace(send_input=send_input))
        sender = _LANInputSender(owner)

        # Exact #1513 emits full manual W first. If R was armed while W was
        # held, releasing W then emits FORWARD | CRUISE_CONTROL25; the native
        # PlayerAvatar and HUD retain ownership of that pending preset.
        sender.send_avatar_input(1, 'move', {'flags': 1})
        sender.send_avatar_input(1, 'move', {'flags': 1 | 32})
        self.assertEqual(
            [1.0, 0.25],
            [call.args[0] for call in send_input.call_args_list])

        for flags, throttle in (
                (1 | 16, 0.5), (1, 1.0),
                (2 | 16, -0.5), (2, -1.0), (0, 0.0)):
            sender.send_avatar_input(1, 'move', {'flags': flags})
            self.assertEqual(throttle, sender.forward)

    def test_cruise_mode_fallback_matches_native_mode_values(self):
        owner = types.SimpleNamespace(
            local_pose=lambda: ((0.0, 0.0, 0.0), 0.0),
            client=types.SimpleNamespace(
                send_input=mock.Mock(return_value=True)))
        sender = _LANInputSender(owner)

        for mode, throttle in (
                (-2, -1.0), (-1, -0.5), (0, 0.0),
                (1, 0.25), (2, 0.5), (3, 1.0)):
            sender.send_avatar_input(1, 'cruise', {'mode': mode})
            self.assertEqual(throttle, sender.forward)

    def test_local_gun_echo_updates_packed_server_angle_from_native_rotator(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.gunRotator = types.SimpleNamespace(
            turretYaw=0.35, gunPitch=-0.08)
        battle._binding = mock.Mock()
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_yaw = -0.2

        self.assertTrue(battle._echo_local_gun_angles())

        args = battle._binding.update_vehicle_aim.call_args[0]
        self.assertEqual(10, args[0])
        self.assertAlmostEqual(-0.2, args[1])
        self.assertAlmostEqual(0.15, args[2])
        self.assertAlmostEqual(-0.08, args[3])

    def test_local_snapshot_never_rewinds_native_vehicle_physics(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'
        battle._binding = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(10, 0, 10), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (10.0, 0.0, 10.0)
        battle._local_yaw = 0.0
        battle._records = {
            'player:1': {'engine_id': 10, 'state': {'health': 500},
                         'kind': 'player', 'network_id': 1, 'local': True}}

        battle._update_entity({
            'entity': 'player:1', 'kind': 'player', 'id': 1,
            'pose': {'x': 12.0, 'y': 0.0, 'z': 10.0, 'yaw': 0.1},
            'state': {'health': 500}})
        battle._binding.drive_vehicle.assert_not_called()

        battle._update_entity({
            'entity': 'player:1', 'kind': 'player', 'id': 1,
            'pose': {'x': 20.0, 'y': 0.0, 'z': 10.0, 'yaw': 0.2},
            'state': {'health': 500}})
        battle._binding.drive_vehicle.assert_not_called()

    def test_authority_applies_copied_bot_pose_to_remote_filter(self):
        battle = BattleRuntime(_runtime())
        battle._binding = mock.Mock()
        battle._records = {
            'bot:17': {'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                       'ready': True, 'tombstone': False}}

        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 17, 'alive': True, 'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'aim_yaw': 0.9, 'gun_pitch': -0.1}]))

        pose_call = battle._binding.set_vehicle_pose.call_args
        self.assertEqual(11, pose_call[0][0])
        self.assertEqual((7.0, 2.0, 9.0), tuple(pose_call[0][1]))
        self.assertEqual((0.0, 0.0, 0.75), pose_call[0][2])
        battle._binding.update_vehicle_aim.assert_called_once_with(
            11, 0.75, 0.9, -0.1)

    def test_authority_bot_motion_notifies_destructibles_before_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._destructibles = mock.Mock()
        descriptor = _Descriptor()
        entity = _Vehicle(11, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        battle._server_entity = mock.Mock(return_value=entity)
        battle._records = {
            'bot:17': {'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                       'ready': True, 'tombstone': False}}

        def set_vehicle_pose(*unused_args):
            battle._destructibles._fell_trees_near.assert_called_once()

        battle._binding.set_vehicle_pose.side_effect = set_vehicle_pose

        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 17, 'alive': True, 'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'speed': 6.5,
            'aim_yaw': 0.9, 'gun_pitch': -0.1}]))

        call = battle._destructibles._fell_trees_near.call_args[0]
        self.assertEqual(7, call[0])
        self.assertEqual((7.0, 2.0, 9.0), tuple(call[1]))
        self.assertEqual(0.75, call[2])
        self.assertEqual(6.5, call[3])
        self.assertIs(descriptor, call[4])

    def test_canonical_fragile_preserves_shot_damage_bit(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = object()
        event = {
            'destructible_kind': 'fragile',
            'chunk_id': 3, 'item_index': 9,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.0, 'speed': 0.0,
            'is_shot': True}
        authority = types.ModuleType(
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority.is_destroyed = mock.Mock(return_value=False)
        authority.destroy_fragile = mock.Mock(return_value=True)
        package = sys.modules['gui.mods.offline_lan_0922']

        with mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.destructibles_authority':
                authority}), mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True):
            self.assertTrue(battle._apply_destructible_event(event))

        args = authority.destroy_fragile.call_args[0]
        self.assertEqual((7, 3, 9), args[:3])
        self.assertEqual((1.0, 2.0, 3.0), tuple(args[3]))
        self.assertIs(True, args[4])

        invalid = dict(event)
        del invalid['is_shot']
        with mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.destructibles_authority':
                authority}), mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True), self.assertRaisesRegex(
                    RuntimeError, 'shot flag is invalid'):
            battle._apply_destructible_event(invalid)

    def test_authority_updates_hidden_remote_through_private_registry(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        battle._remote_factory = factory
        battle._binding = BigWorldVehicleBinding(
            runtime.bigworld, runtime.bigworld.avatar, runtime.constants,
            runtime.vehicles.VehicleDescr, runtime.encode_gun_angles,
            outfit_provider=lambda unused_descriptor: '',
            authority_entity_resolver=battle._server_entity)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Hidden Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        battle._records = {
            'bot:17': {
                'engine_id': vehicle_id, 'kind': 'bot', 'network_id': 17,
                'ready': True, 'tombstone': False}}

        try:
            self.assertIsNone(runtime.bigworld.entity(vehicle_id))
            self.assertTrue(battle._apply_authority_bot_poses([{
                'id': 17, 'alive': True, 'x': 7.0, 'y': 2.0, 'z': 9.0,
                'yaw': 0.75, 'aim_yaw': 0.9, 'gun_pitch': -0.1}]))

            self.assertEqual((7.0, 2.0, 9.0), tuple(vehicle.position))
            self.assertAlmostEqual(0.75, vehicle.yaw)
            self.assertAlmostEqual(0.9, vehicle._aim_yaw)
            self.assertAlmostEqual(-0.1, vehicle._gun_pitch)
            self.assertIsNone(runtime.bigworld.entity(vehicle_id))
        finally:
            factory.destroy_all()

    def test_authority_server_echo_cannot_rewind_presented_bot_pose(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._binding = mock.Mock()
        battle._bots = types.SimpleNamespace(is_authority=lambda: True)
        battle._records = {
            'bot:17': {
                'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                'ready': True, 'local': False, 'tombstone': False,
                'state': {'team': 2, 'yaw': 0.75}}}

        battle._apply_sync_event({
            'type': 'update', 'entity': 'bot:17', 'kind': 'bot', 'id': 17,
            'state': {'team': 2, 'yaw': 0.60},
            'pose': {'x': 7.0, 'y': 2.0, 'z': 9.0, 'yaw': 0.60,
                     'aim_yaw': 0.8, 'gun_pitch': -0.1},
            'remote': True, 'interpolated': True})

        battle._binding.set_vehicle_pose.assert_not_called()
        self.assertEqual(0.60, battle._records['bot:17']['state']['yaw'])

    def test_enemy_spotting_controls_model_marker_and_five_second_memory(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = local
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._binding = mock.Mock()
        enemy = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Enemy'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(100.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        enemy.model = _Model()
        enemy.model.visible = False
        enemy.appearance.attach(enemy.model)
        enemy.isStarted = True
        enemy.inWorld = True
        runtime.bigworld.entities[1000] = enemy
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        battle._records = {'bot:17': {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'arena_added': True,
            'visual_started': False, 'spot_visible': False,
            'spot_until': 0.0, 'spot_next': 0.0,
            'state': {'team': 2, 'health': 500, 'alive': True}}}

        self.assertTrue(battle._update_spotting(10.0))
        self.assertTrue(enemy.model.visible)
        battle._binding.start_vehicle_visual.assert_called_once_with(
            1000, True)

        runtime.bigworld.wg_collideSegment = lambda *unused: (_Vector(),)
        self.assertFalse(battle._update_spotting(10.6))
        self.assertTrue(enemy.model.visible)
        self.assertTrue(battle._update_spotting(15.1))
        self.assertFalse(enemy.model.visible)
        battle._binding.stop_vehicle_visual.assert_called_once_with(
            1000, False)

    def test_spotting_uses_descriptor_camouflage_and_shot_factor(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        observer = _Descriptor()
        target = _Descriptor()
        crew_factors = []

        def base_invisibility(crew_factor, camouflage_id):
            crew_factors.append((crew_factor, camouflage_id))
            return (0.50, 0.50)

        target.computeBaseInvisibility = base_invisibility
        target.gun.invisibilityFactorAtShot = 0.25
        sight = ((0.0, 0.0, 0.0), observer, None)
        target_position = (285.0, 0.0, 0.0)

        self.assertFalse(battle._spot_line_of_sight(
            sight, target_position, target, False, False))
        self.assertTrue(battle._spot_line_of_sight(
            sight, target_position, target, False, True))
        self.assertAlmostEqual(4.0 / 7.0, crew_factors[0][0])
        self.assertIsNone(crew_factors[0][1])

    def test_damaged_optics_and_crew_reduce_observer_view_range(self):
        battle = BattleRuntime(_runtime())
        descriptor = _Descriptor()
        observer = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 500})

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.stat_factor', return_value=0.5):
            damaged = battle._vision_radius(descriptor, observer)

        healthy = battle._vision_radius(descriptor)
        self.assertAlmostEqual(healthy * 0.5, damaged)

    def test_spotting_applies_pair_foliage_and_near_bush_shot_rule(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        observer = _Descriptor()
        target = _Descriptor()
        target.computeBaseInvisibility = lambda *unused: (0.0, 0.0)
        calls = []

        def foliage_bonus(unused_observer, unused_target, fired_recently):
            calls.append(fired_recently)
            return 0.0 if fired_recently else 0.60

        battle._foliage = types.SimpleNamespace(
            camouflage_bonus=foliage_bonus)
        sight = ((0.0, 0.0, 0.0), observer, None)
        target_position = (250.0, 0.0, 0.0)

        self.assertFalse(battle._spot_line_of_sight(
            sight, target_position, target, False, False))
        self.assertTrue(battle._spot_line_of_sight(
            sight, target_position, target, False, True))
        self.assertEqual([False, True], calls)

    def test_dead_local_vehicle_cannot_move_or_fire(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 0})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0,
            send_current=lambda: client.send_input('current'))
        battle._local_speed = 5.0

        battle._drive_local(0.1)

        self.assertEqual(0.0, battle._local_speed)
        self.assertEqual(0.0, battle._sender.forward)
        self.assertEqual(0.0, battle._sender.turn)
        self.assertFalse(battle.shoot(0.0, 0.0))
        self.assertFalse(any(kind == 'fire' for kind, unused in client.sent))

    def test_accepted_shot_enters_native_1513_bloom_once(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = 1
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()
        battle._resolve_hit = mock.Mock()

        self.assertTrue(battle.shoot(0.2, -0.1))

        self.assertEqual([(0.5, 1)], battle._avatar.dispersion_queries)
        battle._resolve_hit.assert_called_once_with(
            1, 0.2, -0.1, 0, 0.25)

    def test_accepted_shot_seeds_stateful_native_convergence(self):
        class StockLikeDispersion(object):
            def __init__(self):
                self.factor = 1.0
                self.calls = []

            def __call__(self, turret_speed, with_shot=0):
                self.calls.append((turret_speed, with_shot))
                if with_shot == 1:
                    self.factor = math.sqrt(self.factor ** 2 + 4.0 ** 2)
                else:
                    self.factor = 1.0 + (self.factor - 1.0) * 0.75
                return [0.1 * self.factor,
                        0.1 * (math.sqrt(17.0) if with_shot else 1.0)]

        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        producer = StockLikeDispersion()
        runtime.bigworld.avatar.getOwnVehicleShotDispersionAngle = producer
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = 1
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()
        battle._resolve_hit = mock.Mock()

        self.assertTrue(battle.shoot(0.2, -0.1))
        shot_angle = producer.factor
        first_tick = producer(0.5, 0)[0]
        second_tick = producer(0.5, 0)[0]

        self.assertEqual((0.5, 1), producer.calls[0])
        self.assertGreater(shot_angle, 1.0)
        self.assertGreater(0.1 * shot_angle, first_tick)
        self.assertGreater(first_tick, second_tick)
        self.assertGreater(second_tick, 0.1)

    def test_rejected_shot_does_not_enter_native_1513_bloom(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        client.send_fire = mock.Mock(return_value=0)
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = 1

        self.assertFalse(battle.shoot(0.2, -0.1))
        self.assertEqual([], battle._avatar.dispersion_queries)

    def test_native_shot_bloom_rejects_non_1513_result_shape(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.getOwnVehicleShotDispersionAngle = (
            lambda unused_speed, unused_with_shot: (0.25, 0.125))

        with self.assertRaisesRegex(
                RuntimeError, 'invalid shape'):
            battle._apply_native_shot_bloom()

    def test_server_shot_event_confirms_local_after_mailbox_returns(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities.update({10: local, 11: remote})
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True},
            'player:2': {'engine_id': 11, 'local': False}}

        battle._show_shot({'attacker': 1})
        battle._show_shot({'attacker': 2, 'shell_index': 2})

        self.assertEqual((1, False), local.last_shot)
        self.assertEqual((1, False), remote.last_shot)
        self.assertEqual(2, remote._offlineLANShotIndex)
        self.assertEqual(10.75,
                         battle._records['player:1']['shot_penalty_until'])
        self.assertEqual(10.75,
                         battle._records['player:2']['shot_penalty_until'])

    def test_bot_shot_camouflage_penalty_does_not_mark_same_id_player(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        player = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        bot = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                       {'health': 500})
        runtime.bigworld.entities.update({10: player, 11: bot})
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True},
            'bot:1': {'engine_id': 11, 'local': False}}

        battle._show_shot({'attacker_bot': 1})

        self.assertEqual((1, False), bot.last_shot)
        self.assertFalse(hasattr(player, 'last_shot'))
        self.assertNotIn('shot_penalty_until', battle._records['player:1'])
        self.assertEqual(10.75,
                         battle._records['bot:1']['shot_penalty_until'])

    def test_server_shot_uses_finite_descriptor_burst(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.typeDescriptor.gun.burst = (3, 0.05)
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True}}

        battle._show_shot({'attacker': 1})

        self.assertEqual((3, False), entity.last_shot)

    def test_invalid_server_shot_burst_falls_back_to_one(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.typeDescriptor.gun.burst = (0,)
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True}}

        battle._show_shot({'attacker': 1})

        self.assertEqual((1, False), entity.last_shot)

    def test_remote_pose_updates_exact_packed_gun_angles(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._records = {
            'player:2': {'engine_id': 11, 'state': {'health': 500},
                         'kind': 'player', 'network_id': 2, 'local': False}}

        battle._update_entity({
            'entity': 'player:2', 'kind': 'player', 'id': 2,
            'pose': {'x': 4.0, 'y': 0.0, 'z': 8.0, 'yaw': 3.0,
                     'aim_yaw': -3.0, 'gun_pitch': -0.15},
            'state': {'health': 500}})

        battle._binding.update_vehicle_aim.assert_called_once_with(
            11, 3.0, -3.0, -0.15)
        pose_call = battle._binding.set_vehicle_pose.call_args
        self.assertEqual(11, pose_call[0][0])
        self.assertEqual((4.0, 0.0, 8.0), tuple(pose_call[0][1]))
        self.assertEqual((0.0, 0.0, 3.0), pose_call[0][2])

    def test_remote_update_is_coalesced_until_vehicle_materializes(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace()
        battle._binding = mock.Mock()
        battle._binding.is_vehicle_ready.side_effect = lambda entity_id: (
            runtime.bigworld.entity(entity_id) is not None)
        battle._records = {
            'player:2': {
                'engine_id': 11, 'state': {'health': 500},
                'kind': 'player', 'network_id': 2, 'local': False,
                'ready': False, 'ready_deadline': runtime.bigworld.now + 5.0}}

        battle._update_entity({
            'entity': 'player:2', 'kind': 'player', 'id': 2,
            'pose': {'x': 4.0, 'y': 0.0, 'z': 8.0, 'yaw': 0.5,
                     'aim_yaw': 0.7, 'gun_pitch': -0.1},
            'state': {'health': 125}})

        battle._binding.set_vehicle_pose.assert_not_called()
        battle._binding.update_vehicle_aim.assert_not_called()
        self.assertNotIn(11, battle._last_health)

        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._flush_pending_entities(runtime.bigworld.now)

        self.assertTrue(battle._records['player:2']['ready'])
        pose_call = battle._binding.set_vehicle_pose.call_args
        self.assertEqual(11, pose_call[0][0])
        self.assertEqual((4.0, 0.0, 8.0), tuple(pose_call[0][1]))
        battle._binding.update_vehicle_aim.assert_called_once_with(
            11, 0.5, 0.7, -0.1)
        self.assertEqual(125, entity.health)
        self.assertEqual((125, 0, 0), entity.health_change)

    def test_pending_remote_death_materializes_as_corpse(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._server = types.SimpleNamespace()
        battle._binding = mock.Mock()
        battle._binding.is_vehicle_ready.side_effect = lambda entity_id: (
            runtime.bigworld.entity(entity_id) is not None)
        battle._records = {
            'bot:2': {
                'engine_id': 11, 'state': {'health': 500, 'alive': True},
                'kind': 'bot', 'network_id': 2, 'local': False,
                'ready': False, 'ready_deadline': runtime.bigworld.now + 5.0}}

        battle._destroy_entity({
            'entity': 'bot:2', 'keep_corpse': True,
            'state': {'health': 0, 'alive': False}})
        self.assertFalse(battle._records['bot:2']['ready'])

        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._flush_pending_entities(runtime.bigworld.now)

        self.assertEqual(0, entity.health)
        self.assertEqual((0, 0, 0), entity.health_change)
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            11, 0, 0)

    def test_pending_remote_presentation_destroy_cancels_late_load(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._config = {
            'map': '01_karelia',
            'vehicle': 'ussr:R11_MS-1',
            'startupTimeoutSeconds': 30.0}
        battle._server = types.SimpleNamespace()
        battle._binding = mock.Mock()
        battle._binding.properties_from_compact_descr.return_value = {
            'publicInfo': {'compDescr': 'ussr:R11_MS-1'},
            'health': 500}
        battle._remote_factory = mock.Mock()
        battle._remote_factory.create.return_value = 1000
        battle._remote_factory.error.return_value = None
        battle._remote_factory.is_ready.return_value = False

        battle._create_remote({
            'type': 'create', 'entity': 'bot:2', 'kind': 'bot', 'id': 2,
        'state': {
            'team': 2, 'slot': 0, 'x': 5.0, 'y': 0.0, 'z': 5.0,
            'world_pose': True,
            'vehicle': 'ussr:R11_MS-1', 'health': 500}})
        record = battle._records['bot:2']
        self.assertEqual(1000, record['engine_id'])
        self.assertTrue(record['presentation'])
        self.assertFalse(record['ready'])

        battle._destroy_entity({'entity': 'bot:2'})
        self.assertNotIn('bot:2', battle._records)
        battle._remote_factory.destroy.assert_called_once_with(1000)
        battle._binding.destroy_entity.assert_not_called()

    def test_terminal_result_notifies_native_hud_once_with_finish_reason(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'

        battle.on_events({'events': [{
            'event_id': '1:1:0',
            'kind': 'battle_result', 'winner': 2,
            'reason': 'team_eliminated'}]})
        battle.on_snapshot({'battle_result': {
            'winner': 2, 'reason': 'team_eliminated'}})

        self.assertEqual(
            [(2, runtime.constants.FINISH_REASON.EXTERMINATION)],
            runtime.bigworld.avatar.round_finished)
        self.assertTrue(battle._round_finished_notified)

    def test_base_capture_uses_exact_1513_event_shapes(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'

        self.assertTrue(battle._apply_rules({'bases': {
            '1': {'points': 42, 'time_left': 29.0,
                  'invaders': 2, 'stopped': True},
            '2': {'points': 0, 'time_left': 0.0,
                  'invaders': 0, 'stopped': False}}}))
        self.assertEqual([
            (1, 0, 42, 29.0, 2, True),
            (2, 0, 0, 0.0, 0, False),
        ], runtime.bigworld.avatar.base_points)

        self.assertTrue(battle._apply_rules({'bases': {
            '1': {'points': 42, 'time_left': 27.5,
                  'invaders': 3, 'stopped': True},
            '2': {'points': 0, 'time_left': 0.0,
                  'invaders': 0, 'stopped': False}}}))
        self.assertEqual(
            (1, 0, 42, 27.5, 3, True),
            runtime.bigworld.avatar.base_points[-1])
        update_count = len(runtime.bigworld.avatar.base_points)
        self.assertFalse(battle._apply_rules({'bases': {
            '1': {'points': 42, 'time_left': 27.5,
                  'invaders': 3, 'stopped': True},
            '2': {'points': 0, 'time_left': 0.0,
                  'invaders': 0, 'stopped': False}}}))
        self.assertEqual(update_count,
                         len(runtime.bigworld.avatar.base_points))

        self.assertTrue(battle._apply_battle_result({
            'winner': 2, 'reason': 'base captured', 'base_team': 1}))
        self.assertEqual([(1, 0)], runtime.bigworld.avatar.base_captured)
        self.assertEqual([
            (2, runtime.constants.FINISH_REASON.BASE),
        ], runtime.bigworld.avatar.round_finished)

    def test_ammo_hud_producer_obeys_exact_integer_wire_ranges(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        descriptor.gun.maxAmmo = 999999
        descriptor.gun.clip = (999,)
        descriptor.gun.reloadTime = 1.5
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()

        update = runtime.bigworld.avatar.ammo_updates[0]
        self.assertEqual(5, len(update))
        self.assertTrue(all(isinstance(value, int) for value in update))
        self.assertEqual(65535, update[2])
        # Exact 0.8.2 starts with an empty breech/magazine and begins the
        # first reload only after the battle period becomes live.
        self.assertEqual(0, update[3])
        self.assertEqual(0, update[4])

        battle._gun_last_tick -= 2.0
        battle._ammo_tick()
        loaded = runtime.bigworld.avatar.ammo_updates[-1]
        self.assertEqual(255, loaded[3])
        self.assertEqual(0, loaded[4])

    def test_reload_hud_receives_edges_and_interpolates_between_them(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = False
        battle._config = {}
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()
        battle._ammo_tick()
        self.assertEqual(1, len(runtime.bigworld.avatar.reload_updates))
        self.assertEqual(0.0, runtime.bigworld.avatar.reload_updates[0][1])

        battle._begin_battle()
        self.assertEqual(2, len(runtime.bigworld.avatar.reload_updates))
        self.assertGreater(runtime.bigworld.avatar.reload_updates[1][1], 0.0)

        runtime.bigworld.now += 0.5
        battle._ammo_tick()
        self.assertEqual(2, len(runtime.bigworld.avatar.reload_updates))
        runtime.bigworld.now += 2.0
        battle._ammo_tick()
        self.assertEqual(3, len(runtime.bigworld.avatar.reload_updates))
        self.assertEqual(0.0, runtime.bigworld.avatar.reload_updates[-1][1])

    def test_client_ready_does_not_start_a_second_ammo_timer(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = mock.Mock()
        runtime.bigworld.entities[10] = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})

        battle._ammo_tick()
        callbacks = len(runtime.bigworld.callbacks)
        battle._on_client_ready()

        self.assertEqual(callbacks, len(runtime.bigworld.callbacks))
        battle._sender.send_current.assert_called_once_with()

    def test_ammo_tick_never_writes_read_only_1513_dispersion_property(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        class ReadOnlyGunRotator(object):
            @property
            def dispersionAngle(self):
                return 0.25

        runtime.bigworld.avatar.gunRotator = ReadOnlyGunRotator()

        battle._ammo_tick()

        self.assertEqual('running', battle.state)
        self.assertIsNone(battle.error)

    def test_ammo_tick_feeds_native_1513_dispersion_parameters(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()

        targeting = runtime.bigworld.avatar.targeting
        crew_multiplier = 1.0 / (0.5 + 0.005 * 110.0)
        self.assertAlmostEqual(crew_multiplier, targeting[4])
        self.assertEqual(0.1, targeting[5])
        self.assertEqual(0.14, targeting[6])
        self.assertEqual(0.14, targeting[7])
        self.assertAlmostEqual(crew_multiplier, targeting[8])

    def test_damaged_turret_rotator_scales_native_traverse_speed(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        def factor(unused_entity, stat):
            return 0.5 if stat == 'turret_speed' else 1.0

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.stat_factor', side_effect=factor):
            battle._ammo_tick()

        self.assertAlmostEqual(
            descriptor.turret.rotationSpeed * 0.5,
            runtime.bigworld.avatar.targeting[2])

    def test_parsed_1513_light_tank_bloom_uses_raw_descriptor_factor(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        # #1513 converts XML 0.14 to per-m/s and per-rad/s runtime values.
        descriptor.chassis['shotDispersionFactors'] = (0.504, 8.02)
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()

        targeting = runtime.bigworld.avatar.targeting
        movement_factor = targeting[6]
        full_speed_multiplier = math.sqrt(
            1.0 + (16.67 * movement_factor) ** 2)
        self.assertAlmostEqual(0.504, movement_factor)
        self.assertAlmostEqual(
            math.sqrt(1.0 + (16.67 * 0.504) ** 2),
            full_speed_multiplier)

    def test_ammo_tick_does_not_restart_native_gun_rotator_each_frame(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()
        battle._ammo_tick()

        self.assertEqual(1, len(runtime.bigworld.avatar.targeting_updates))

        descriptor.gun.rotationSpeed = 0.75
        battle._ammo_tick()
        self.assertEqual(2, len(runtime.bigworld.avatar.targeting_updates))
        self.assertEqual(0.75, runtime.bigworld.avatar.targeting_updates[-1][3])

    def test_ammo_tick_keeps_enabled_server_marker_on_the_client_angle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        shot_position = _Vector(1.0, 2.0, 3.0)
        shot_vector = _Vector(0.0, 0.0, 250.0)
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            showServerMarker=True,
            dispersionAngle=0.0375,
            getCurShotPosition=mock.Mock(
                return_value=(shot_position, shot_vector)))

        battle._ammo_tick()

        self.assertEqual([
            (10, shot_position, shot_vector, 0.0375)
        ], runtime.bigworld.avatar.gun_marker_updates)

    def test_native_dispersion_uses_read_only_rotator_without_class_patch(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        avatar_type = type(runtime.bigworld.avatar)
        original = avatar_type.__dict__[
            'getOwnVehicleShotDispersionAngle']
        battle._avatar.gunRotator = types.SimpleNamespace(
            dispersionAngle=0.0375)

        self.assertAlmostEqual(0.0375, battle._native_dispersion_angle())
        self.assertIs(
            original,
            avatar_type.__dict__['getOwnVehicleShotDispersionAngle'])

    def test_invalid_native_dispersion_fails_without_fallback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.gunRotator = types.SimpleNamespace(
            dispersionAngle=float('nan'))

        with self.assertRaisesRegex(RuntimeError, 'angle is invalid'):
            battle._native_dispersion_angle()

    def test_equipment_activation_decodes_exact_extra_and_consumes_once(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        battle.client = types.SimpleNamespace(player_id=1)
        descriptor = _Descriptor()
        extra = types.SimpleNamespace(name='engineHealth')
        descriptor.extras = {7: extra}
        descriptor.extrasDict = {'engineHealth': extra}
        descriptor.engine = {'maxHealth': 100, 'maxRegenHealth': 50}
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.devices_hp = {'engineHealth': 0.0}
        entity._destroyed_devices = set(['engineHealth'])
        entity._crew_ko = set()
        entity.is_on_fire = False
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}}
        battle._equipment_state = [{
            'id': 41, 'compact_descr': 401,
            'name': 'smallrepairkit', 'kind': 'repairkit',
            'used': False}]
        battle._present_critical = mock.Mock(return_value=True)

        activation_code = (7 << 16) | 41
        self.assertTrue(battle.change_vehicle_setting(
            runtime.constants.VEHICLE_SETTING.ACTIVATE_EQUIPMENT,
            activation_code))

        self.assertEqual(100.0, entity.devices_hp['engineHealth'])
        self.assertTrue(battle._equipment_state[0]['used'])
        # The activation is accepted once; the stock controller receives a
        # zero quantity immediately, so a second click cannot reuse the kit.
        self.assertEqual((10, 401, 0, 0, 0),
                         runtime.bigworld.avatar.ammo_updates[-1])
        self.assertEqual([], battle._records[
            'player:1']['critical_state']['destroyed'])
        self.assertIsNotNone(battle._local_damage_report)
        self.assertFalse(battle.change_vehicle_setting(
            runtime.constants.VEHICLE_SETTING.ACTIVATE_EQUIPMENT,
            activation_code))

    def test_hit_resolution_uses_public_1513_gun_rotator_api(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._start_message = {'players': [{'id': 1, 'team': 1}]}
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=20.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'state': {'team': 1},
                         'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11,
                      'state': {'team': 2, 'combat_base_revision': 7,
                                'combat_ack_seq': 3},
                      'kind': 'bot', 'network_id': 2, 'local': False}}
        get_shot = mock.Mock(return_value=(
            _Vector(0, 2, 0), _Vector(0, 0, 1)))
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=get_shot)
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(120, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            500, {'events': []})

        battle._resolve_hit(7, 0.0, 0.0)

        get_shot.assert_called_once_with()
        battle.client.send_bot_hit.assert_called_once()
        sent = battle.client.send_bot_hit.call_args
        self.assertEqual(500, sent.args[2])
        self.assertEqual(120, sent.kwargs['hull_damage'])
        self.assertEqual(7, sent.kwargs[
            'critical_target_base_revision'])
        self.assertEqual(3, sent.kwargs['critical_target_ack_seq'])

    def test_player_shot_collision_contract_failure_is_not_a_silent_miss(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = mock.Mock(
            side_effect=RuntimeError('remote collision failed'))
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11, 'kind': 'bot',
                      'network_id': 2, 'local': False}}

        with self.assertRaisesRegex(RuntimeError, 'remote collision failed'):
            battle._resolve_hit(7, 0.0, 0.0)

    def test_he_splash_uses_vehicle_ray_and_skips_direct_target(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        shell = descriptor.gun.shots[0].shell
        shell.kind = 'HIGH_EXPLOSIVE'
        shell.damage = (400.0,)
        shell.explosionRadius = 10.0
        source = _Vehicle(10, descriptor, _Vector(100, 0, 100),
                          (0, 0, 0), {'health': 500})
        direct = _Vehicle(11, _Descriptor(), _Vector(1, 0, 0),
                          (0, 0, 0), {'health': 500})
        splash = _Vehicle(12, _Descriptor(), _Vector(5, 0, 0),
                          (0, 0, 0), {'health': 500})
        far = _Vehicle(13, _Descriptor(), _Vector(20, 0, 0),
                       (0, 0, 0), {'health': 500})
        material = types.SimpleNamespace(
            armor=20.0, vehicleDamageFactor=1.0,
            chanceToHitByExplosion=1.0)
        splash.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=1.0, hitAngleCos=1.0, matInfo=material,
            compName='vehicleHull')]
        runtime.bigworld.entities.update({
            10: source, 11: direct, 12: splash, 13: far})
        direct_record = {
            'engine_id': 11, 'state': {'health': 500}, 'kind': 'bot',
            'network_id': 1, 'local': False}
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500}, 'kind': 'player',
                'network_id': 1, 'local': True},
            'bot:1': direct_record,
            'bot:2': {
                'engine_id': 12,
                'state': {'health': 500, 'combat_base_revision': 7,
                          'combat_ack_seq': 3}, 'kind': 'bot',
                'network_id': 2, 'local': False},
            'bot:3': {
                'engine_id': 13, 'state': {'health': 500}, 'kind': 'bot',
                'network_id': 3, 'local': False},
        }
        battle.client = types.SimpleNamespace(
            send_bot_hit=mock.Mock(return_value=True))

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.propose_direct',
                side_effect=lambda *args, **kwargs: (
                    args[4] + 111, {'events': []})) as apply_critical:
            count = battle._he_splash(
                _Vector(0, 0, 0), descriptor.gun.shots[0], 7,
                direct_record, 'player', 1, 10)

        self.assertEqual(1, count)
        sent = battle.client.send_bot_hit.call_args
        self.assertEqual(2, sent.args[0])
        self.assertEqual(
            sent.kwargs['hull_damage'] + 111, sent.args[2])
        self.assertTrue(sent.kwargs['splash'])
        self.assertGreater(sent.kwargs['hull_damage'], 0)
        self.assertEqual(7, sent.kwargs[
            'critical_target_base_revision'])
        self.assertEqual(3, sent.kwargs['critical_target_ack_seq'])
        self.assertTrue(apply_critical.call_args.kwargs['by_explosion'])

    def test_health_transition_calls_native_vehicle_death_path(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {'engine_id': 10, 'local': False}

        battle._apply_health(record, {'health': 0})

        self.assertEqual((0, 0, 0), entity.health_change)

    def test_ram_death_preserves_ramming_critical_cause(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {'engine_id': 10, 'local': False}

        with mock.patch.object(
                critical_damage, 'apply_death', return_value=None) as death:
            battle._apply_health(record, {'health': 0}, reason_id=2)

        death.assert_called_once_with(entity, 'ramming')

    def test_combat_event_separates_attack_and_death_reason(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities.update({10: attacker, 11: target})
        battle._records = {
            'player:1': {
                'engine_id': 10,
                'state': {'health': 500, 'team': 1},
                'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {
                'engine_id': 11,
                'state': {'health': 500, 'team': 2},
                'kind': 'bot', 'network_id': 2, 'local': False,
                'presentation': True},
        }
        battle._avatar.playerVehicleID = 10
        battle._avatar.arena_dp.isRequiredDataExists()

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'health': 0, 'dead': True, 'attack_reason': 0,
            'death_reason': 3, 'source': 'shot',
            'world_pose': True, 'x': 0.0, 'y': 0.0, 'z': 1.0,
            'shell_index': 0, 'shot_result': 2, 'damage': 500}))

        self.assertEqual((0, 10, 0), target.health_change)
        present_health = battle._avatar.guiSessionProvider.setVehicleHealth
        present_health.assert_called_once_with(False, 11, 0, 10, 0)
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            11, 10, 3)

    def test_server_owned_frag_and_team_killer_updates_use_native_arena(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._binding = mock.Mock()
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'frags': 0},
                'kind': 'player', 'network_id': 1, 'local': True},
        }

        event = {
            'kind': 'vehicle_statistics', 'actor_kind': 'player',
            'actor_id': 1, 'frags': -1, 'team_killer': True}
        self.assertTrue(battle._apply_vehicle_statistics_event(event))
        battle._binding.arena_vehicle_statistics.assert_called_once_with(
            10, -1)
        battle._binding.arena_team_killer.assert_called_once_with(10)

        self.assertFalse(battle._apply_vehicle_statistics_event(event))
        battle._binding.arena_vehicle_statistics.assert_called_once_with(
            10, -1)
        battle._binding.arena_team_killer.assert_called_once_with(10)

    def test_death_snapshot_resolves_durable_attacker_before_health(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._server = types.SimpleNamespace()
        battle._binding = mock.Mock()
        battle._binding.is_vehicle_ready.return_value = True
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        victim = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities.update({10: attacker, 11: victim})
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500}, 'ready': True,
                'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {
                'engine_id': 11,
                'state': {
                    'health': 0, 'alive': False, 'death_reason': 3,
                    'death_attacker_kind': 'player',
                    'death_attacker_id': 1},
                'ready': True, 'kind': 'bot', 'network_id': 2,
                'local': False},
        }

        self.assertTrue(battle._materialize_record(battle._records['bot:2']))

        self.assertEqual((0, 10, 3), victim.health_change)
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            11, 10, 3)

    def test_stop_restores_account_and_native_sync_owns_lobby_transition(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        calls = []
        runtime.offline_map_creator.destroy = lambda: calls.append('destroy')
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))
        type(runtime.app_loader).lobby_callback = lambda: calls.append(
            'lobby')

        battle.stop(show_login=False)

        self.assertEqual(['destroy', 'restore'], calls)
        self.assertEqual('stopped', battle.state)

    def test_cleanup_leaves_vehicle_teardown_to_native_avatar_then_map(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._map_create_attempted = True
        binding = mock.Mock()
        server = mock.Mock()
        battle._binding = binding
        battle._server = server
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True},
            'bot:2': {'engine_id': 11, 'local': False}}
        runtime.bigworld.entities[11] = _Vehicle(
            11, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        calls = []

        def retire():
            calls.append('retire')

        def destroy():
            calls.append('destroy')
            runtime.bigworld.clearEntitiesAndSpaces()

        runtime.compatibility.retire_current_player = retire
        runtime.offline_map_creator.destroy = destroy

        battle._cleanup()

        self.assertEqual(['retire', 'destroy'], calls)
        binding.destroy_entity.assert_not_called()
        binding.arena_vehicle_removed.assert_not_called()
        server.destroy.assert_not_called()
        self.assertIsNone(battle._binding)

    def test_cleanup_destroys_remote_presentations_before_native_space(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._map_create_attempted = True
        calls = []

        class _FailingRemoteFactory(object):
            def destroy_all(self):
                calls.append('remote')
                raise RuntimeError('remote cleanup failed')

        battle._remote_factory = _FailingRemoteFactory()

        def retire():
            calls.append('retire')

        def destroy():
            calls.append('destroy')
            runtime.bigworld.avatar = None

        runtime.compatibility.retire_current_player = retire
        runtime.offline_map_creator.destroy = destroy

        with self.assertRaisesRegex(RuntimeError, 'remote cleanup failed'):
            battle._cleanup()

        self.assertEqual(['remote', 'retire', 'destroy'], calls)
        self.assertIsNone(battle._remote_factory)

    def test_cleanup_releases_space_id_lost_by_stock_destroy_failure(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._map_create_attempted = True
        creator = runtime.offline_map_creator
        creator._OfflineMapCreator__spaceId = 7
        creator._OfflineMapCreator__spaceMappingId = 3
        client_spaces = set([7])
        calls = []

        runtime.compatibility.retire_current_player = (
            lambda: calls.append(('retire',)))
        runtime.bigworld.isClientSpace = (
            lambda space_id: space_id in client_spaces)
        runtime.bigworld.delSpaceGeometryMapping = (
            lambda space_id, mapping_id:
            calls.append(('mapping', space_id, mapping_id)))
        runtime.bigworld.clearSpace = (
            lambda space_id: calls.append(('clear', space_id)))

        def release(space_id):
            calls.append(('release', space_id))
            client_spaces.discard(space_id)

        runtime.bigworld.releaseSpace = release

        def lossy_destroy():
            calls.append(('destroy',))
            runtime.bigworld.avatar = None
            creator._OfflineMapCreator__spaceId = 0
            creator._OfflineMapCreator__spaceMappingId = 0

        creator.destroy = lossy_destroy

        battle._cleanup()

        self.assertEqual([
            ('retire',), ('destroy',), ('mapping', 7, 3),
            ('clear', 7), ('release', 7)], calls)
        self.assertNotIn(7, client_spaces)

    def test_lan_disconnect_still_restores_fake_account(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        calls = []
        runtime.offline_map_creator.destroy = lambda: calls.append('destroy')
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))

        battle.stop(show_login=True)

        self.assertEqual(['destroy', 'restore'], calls)
        self.assertEqual('stopped', battle.state)

    def test_global_shutdown_cleans_battle_without_recreating_account(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        calls = []
        runtime.offline_map_creator.destroy = lambda: calls.append('destroy')
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))

        battle.stop(show_login=False, restore_account=False)

        self.assertEqual(['destroy'], calls)
        self.assertEqual('stopped', battle.state)

    def test_failed_account_restore_does_not_leave_runtime_running(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        runtime.compatibility.restore_lobby_account = mock.Mock(
            side_effect=RuntimeError('restore failed'))

        with self.assertRaisesRegex(RuntimeError, 'restore failed'):
            battle.stop()

        self.assertEqual('stopped', battle.state)
        self.assertIsNone(battle._avatar)
        self.assertIsNone(battle._server)
        battle.stop()

    def test_dirty_stock_teardown_never_restores_account_over_zombie_avatar(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        runtime.offline_map_creator.destroy = lambda: None
        runtime.bigworld.clearEntitiesAndSpaces = lambda: None
        runtime.bigworld.clearAllSpaces = lambda: None
        runtime.compatibility.restore_lobby_account = mock.Mock()

        with self.assertRaisesRegex(RuntimeError,
                                    'retained the Avatar'):
            battle.stop()

        self.assertEqual('stopped', battle.state)
        runtime.compatibility.restore_lobby_account.assert_not_called()

    def test_rejected_map_attempt_runs_full_destroy_before_account_restore(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        calls = []
        original_clear = runtime.bigworld.clearEntitiesAndSpaces

        def clear_lobby():
            calls.append('clear')
            original_clear()

        def partial_create(unused_map_name):
            runtime.bigworld.avatar = object()
            runtime.offline_map_creator.active = False

        def full_destroy():
            calls.append('destroy')
            runtime.bigworld.avatar = None

        def restore():
            self.assertIsNone(runtime.bigworld.avatar)
            calls.append('restore')

        runtime.offline_map_creator.create = partial_create
        runtime.offline_map_creator.destroy = full_destroy
        runtime.compatibility.restore_lobby_account = restore
        runtime.bigworld.clearEntitiesAndSpaces = clear_lobby

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        self.assertEqual(['clear', 'destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)
        self.assertFalse(battle._map_create_attempted)

    def test_partial_avatar_is_rejected_and_fully_destroyed(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        calls = []
        original_clear = runtime.bigworld.clearEntitiesAndSpaces

        def clear_lobby():
            calls.append('clear')
            original_clear()

        def create_partial(unused_map_name):
            runtime.offline_map_creator.active = True
            runtime.bigworld.avatar = object()

        def destroy_partial():
            calls.append('destroy')
            runtime.offline_map_creator.active = False
            runtime.bigworld.avatar = None

        runtime.offline_map_creator.create = create_partial
        runtime.offline_map_creator.destroy = destroy_partial
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))
        runtime.bigworld.clearEntitiesAndSpaces = clear_lobby

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        self.assertEqual(['clear', 'destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)

    def test_avatar_leave_defers_destroy_until_mailbox_returns(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}
        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.callbacks.pop(0)()
        self.assertEqual('running', battle.state)
        server = battle._server

        server.leaveArena({})

        self.assertEqual('running', battle.state)
        self.assertIs(server, battle._server)
        runtime.bigworld.callbacks.pop()()
        self.assertEqual('stopped', battle.state)
        self.assertIsNone(battle._server)

    def test_avatar_leave_delegates_session_ownership_after_mailbox_returns(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        local_leave = mock.Mock()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}
        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client,
            on_local_leave=local_leave))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.callbacks.pop(0)()
        server = battle._server

        server.leaveArena({})

        local_leave.assert_not_called()
        runtime.bigworld.callbacks.pop()()
        local_leave.assert_called_once_with()
        self.assertEqual('running', battle.state)
        self.assertIs(server, battle._server)

    def test_same_runtime_can_cleanly_create_a_second_round(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()

        for round_id in (1, 2):
            start = {
                'round_id': round_id, 'map': '01_karelia',
                'bot_authority_id': 1,
                'players': [{
                    'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                    'vehicle': 'ussr:R11_MS-1', 'health': 500}],
                'bots': []}
            self.assertTrue(battle.start({
                'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
                'name': 'Player'}, start, client))
            runtime.bigworld.callbacks.pop(0)()
            runtime.bigworld.callbacks.pop(0)()
            self.assertEqual('running', battle.state)
            self.assertEqual(round_id, battle._sync.round_id)
            self.assertFalse(battle._round_finished_notified)
            if round_id == 1:
                battle.stop(show_login=False)
                self.assertEqual('stopped', battle.state)
                runtime.bigworld.callbacks[:] = []

    def test_async_failure_recovers_lobby_and_notifies_session(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'loading_entities'
        battle._map_create_attempted = True
        calls = []
        runtime.offline_map_creator.destroy = lambda: calls.append('destroy')
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))
        type(runtime.app_loader).lobby_callback = lambda: calls.append(
            'lobby')
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))

        self.assertEqual(['destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)
        callback.assert_called_once_with(
            'battle_failed', {
                'message': 'entity loading failed',
                'round_id': None,
                'lobby_restored': True,
            })

    def test_failed_lobby_restore_is_reported_without_transport_error(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'loading_entities'
        battle._start_message = {'round_id': 9}
        battle._map_create_attempted = True
        runtime.offline_map_creator.destroy = lambda: None
        runtime.bigworld.avatar = None
        runtime.compatibility.restore_lobby_account = mock.Mock(
            side_effect=RuntimeError('replacement Account failed'))
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))

        self.assertEqual('failed', battle.state)
        self.assertIn('entity loading failed', battle.error)
        self.assertIn('replacement Account failed', battle.error)
        self.assertEqual(1, runtime.compatibility.disconnect_calls)
        self.assertIsNone(runtime.bigworld.player())
        callback.assert_called_once_with('battle_failed', {
            'message': battle.error,
            'round_id': 9,
            'lobby_restored': False,
        })

    def test_retirement_failure_does_not_skip_map_destroy_or_disconnect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'loading_entities'
        battle._start_message = {'round_id': 11}
        battle._map_create_attempted = True
        runtime.compatibility.retire_current_player = mock.Mock(
            side_effect=RuntimeError('native retirement failed'))
        runtime.offline_map_creator.destroy = mock.Mock(
            side_effect=runtime.bigworld.clearEntitiesAndSpaces)
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))

        runtime.offline_map_creator.destroy.assert_called_once_with()
        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual(1, runtime.compatibility.disconnect_calls)
        self.assertIn('native retirement failed', battle.error)
        callback.assert_called_once_with('battle_failed', {
            'message': battle.error,
            'round_id': 11,
            'lobby_restored': False,
        })

    def test_force_clear_runs_after_native_retirement_failure(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        runtime.compatibility.retire_current_player = mock.Mock(
            side_effect=RuntimeError('native retirement failed'))

        error = battle._force_clear_engine_player(
            'engine retained its player')

        self.assertIsNone(runtime.bigworld.player())
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual('native retirement failed', str(error))
        self.assertIn(('clear_entities_spaces',), runtime.bigworld.operations)

    def test_failure_notification_exception_never_replaces_first_error(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'loading_entities'
        battle._start_message = {'round_id': 9}
        battle._map_create_attempted = True
        runtime.offline_map_creator.destroy = lambda: None
        runtime.bigworld.avatar = None
        runtime.compatibility.restore_lobby_account = lambda: object()

        def fail_callback(kind, message):
            raise RuntimeError('notification failed')

        battle.client = types.SimpleNamespace(on_event=fail_callback)

        battle._fail(RuntimeError('first native failure'))

        self.assertEqual('failed', battle.state)
        self.assertEqual('first native failure', battle.error)

    def test_bot_to_bot_collision_uses_authority_report(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        collision = types.SimpleNamespace(
            dist=20.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')
        target.collideSegmentExt = lambda start, end: [collision]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 1},
                      'kind': 'bot', 'network_id': 1},
            'bot:2': {'engine_id': 11,
                      'state': {'team': 2, 'combat_base_revision': 6,
                                'combat_ack_seq': 2},
                      'kind': 'bot', 'network_id': 2}}
        battle.client = types.SimpleNamespace(
            send_bot_bot_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(80, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            400, {'events': []})

        self.assertTrue(battle._resolve_bot_shot({
            'id': 1, 'target_kind': 'bot', 'target_id': 2,
            'shell_index': 0}, 3))
        battle.client.send_bot_bot_hit.assert_called_once()
        sent = battle.client.send_bot_bot_hit.call_args
        self.assertEqual(400, sent.args[3])
        self.assertEqual(80, sent.kwargs['hull_damage'])
        self.assertEqual(6, sent.kwargs[
            'critical_target_base_revision'])
        self.assertEqual(2, sent.kwargs['critical_target_ack_seq'])

    def test_bot_shot_resolver_uses_dispersed_barrel_ray(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        segments = []
        collision = types.SimpleNamespace(
            dist=20.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')

        def collide(start, end):
            segments.append((start, end))
            return [collision]

        target.collideSegmentExt = collide
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 1},
                      'kind': 'bot', 'network_id': 1},
            'bot:2': {'engine_id': 11, 'state': {'team': 2},
                      'kind': 'bot', 'network_id': 2}}
        battle.client = types.SimpleNamespace(
            send_bot_bot_hit=mock.Mock(return_value=True))
        battle._critical_hit = lambda *args, **kwargs: (args[5], None)

        self.assertTrue(battle._resolve_bot_shot({
            'id': 1, 'target_kind': 'bot', 'target_id': 2,
            'shell_index': 0, 'shot_yaw': math.pi / 2.0,
            'shot_pitch': 0.1}, 3))

        start, end = segments[0]
        direction = end - start
        self.assertAlmostEqual(500.0, direction.length, places=4)
        direction.normalise()
        self.assertAlmostEqual(math.cos(0.1), direction.x, places=5)
        self.assertAlmostEqual(math.sin(0.1), direction.y, places=5)
        self.assertAlmostEqual(0.0, direction.z, places=5)

    def test_bot_shot_resolver_reports_damage_to_local_human(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        collision = types.SimpleNamespace(
            dist=20.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(
                armor=10.0, vehicleDamageFactor=1.0),
            compName='vehicleHull')
        target.collideSegmentExt = mock.Mock(side_effect=AssertionError(
            'native collision uses the stale retail vehicle filter'))
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 2},
                      'kind': 'bot', 'network_id': 1},
            'player:7': {'engine_id': 11,
                         'state': {'team': 1,
                                   'critical_base_revision': 4,
                                   'critical_ack_seq': 1},
                         'kind': 'player', 'network_id': 7, 'local': True}}
        battle._local_matrix = _Matrix(target.matrix)
        battle.client = types.SimpleNamespace(
            send_bot_human_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(90, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            450, {'events': []})

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'collide_vehicle_at_matrix',
                return_value=[collision]) as collide_at_matrix:
            self.assertTrue(battle._resolve_bot_shot({
                'id': 1, 'target_kind': 'human', 'target_id': 7,
                'shell_index': 0, 'shot_yaw': 0.0,
                'shot_pitch': 0.0}, 3))

        args = battle.client.send_bot_human_hit.call_args[0]
        self.assertEqual((1, 7, 3), args[:3])
        self.assertEqual(450, args[3])
        kwargs = battle.client.send_bot_human_hit.call_args.kwargs
        self.assertEqual(90, kwargs['hull_damage'])
        self.assertEqual(4, kwargs['critical_target_base_revision'])
        self.assertEqual(1, kwargs['critical_target_ack_seq'])
        collide_at_matrix.assert_called_once()
        self.assertIs(target, collide_at_matrix.call_args[0][0])
        self.assertIs(battle._local_matrix,
                      collide_at_matrix.call_args[0][1])
        target.collideSegmentExt.assert_not_called()

    def test_bot_shot_collision_contract_failure_is_not_a_silent_miss(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        target.collideSegmentExt = mock.Mock(
            side_effect=RuntimeError('native collision failed'))
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 2},
                      'kind': 'bot', 'network_id': 1},
            'player:7': {'engine_id': 11, 'state': {'team': 1},
                         'kind': 'player', 'network_id': 7}}

        with self.assertRaisesRegex(RuntimeError, 'native collision failed'):
            battle._resolve_bot_shot({
                'id': 1, 'target_kind': 'human', 'target_id': 7,
                'shell_index': 0, 'shot_yaw': 0.0,
                'shot_pitch': 0.0}, 3)

    def test_bot_ram_message_uses_authority_client_contract(self):
        battle = BattleRuntime(_runtime())
        battle.client = types.SimpleNamespace(
            send_bot_ram=mock.Mock(return_value=True))

        self.assertTrue(battle._send_bot_message({
            'type': 'bot_ram', 'bot_id': 11, 'target_kind': 'human',
            'target_id': 2, 'ram_seq': 4, 'damage_to_bot': 20,
            'damage_to_target': 40}))

        battle.client.send_bot_ram.assert_called_once_with(
            11, 'human', 2, 4, 20, 40)

    def test_snapshot_health_is_forwarded_to_authority_runtime(self):
        battle = BattleRuntime(_runtime())
        battle._bots = types.SimpleNamespace(apply_snapshot=mock.Mock())
        battle._sync = types.SimpleNamespace(snapshot=mock.Mock())
        snapshot = {'server_tick': 8, 'bots': [
            {'id': 2, 'health': 0, 'alive': False}]}

        battle.on_snapshot(snapshot)

        battle._bots.apply_snapshot.assert_called_once_with(snapshot)
        battle._sync.snapshot.assert_called_once_with(snapshot)

    def test_dead_player_and_terminal_battle_cannot_keep_driving(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 0})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0,
            send_current=lambda: client.send_input('current'))

        battle._drive_local(0.1)
        self.assertEqual([], entity.teleports)

        entity.health = 500
        battle._battle_result = {'winner': 1}
        battle._drive_local(0.1)
        self.assertEqual([], entity.teleports)

    def test_authority_takeover_primes_seen_fire_sequences(self):
        battle = BattleRuntime(_runtime())
        battle._start_message = {'round_id': 4, 'bot_authority_id': 2}
        battle._last_snapshot = {'bots': [
            {'id': 11, 'fire_seq': 9, 'health': 500, 'alive': True}]}
        battle._bots = types.SimpleNamespace(
            battle_start=mock.Mock(return_value=[]),
            apply_snapshot=mock.Mock(), is_authority=lambda: True)

        battle.on_events({'events': [{
            'event_id': '4:1:0',
            'kind': 'authority', 'round_id': 4, 'player_id': 1}]})

        self.assertEqual(9, battle._bot_fire_seen[11])
        battle._bots.apply_snapshot.assert_called_once_with(
            battle._last_snapshot)

    def test_loading_roster_updates_authority_before_bots_exist(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'loading'
        battle._start_message = {
            'round_id': 4, 'bot_authority_id': 2,
            'bots': [{'id': 11, 'team': 1, 'slot': 0}]}

        self.assertTrue(battle.on_roster({
            'round_id': 4, 'phase': 'loading',
            'bot_authority_id': 1}))

        self.assertEqual(1, battle._start_message['bot_authority_id'])

    def test_snapshot_recovers_authority_takeover_without_event(self):
        battle = BattleRuntime(_runtime())
        battle._start_message = {'round_id': 4, 'bot_authority_id': 2}
        battle._send_bot_message = mock.Mock(return_value=True)
        bots = types.SimpleNamespace(
            authority_id=2,
            battle_start=mock.Mock(return_value=[{
                'type': 'bot_manifest', 'bots': [{'id': 11}]}]),
            apply_snapshot=mock.Mock(), is_authority=lambda: True)
        battle._bots = bots
        snapshot = {
            'round_id': 4, 'bot_authority_id': 1,
            'bot_manifest': [{
                'id': 11,
                'profile': {'dominant_role': 'sniper'},
                'route': {'id': 'ridge', 'waypoints': []}}],
            'bots': [{'id': 11, 'fire_seq': 9,
                      'health': 500, 'alive': True,
                      'x': 123.0, 'y': 4.0, 'z': -87.0, 'yaw': 1.25}]}

        battle.on_snapshot(snapshot)

        bots.battle_start.assert_called_once()
        takeover = bots.battle_start.call_args[0][0]
        self.assertEqual('sniper',
                         takeover['bot_manifest'][0]['profile']['dominant_role'])
        self.assertEqual('ridge',
                         takeover['bot_manifest'][0]['route']['id'])
        self.assertEqual(9, takeover['bot_manifest'][0]['fire_seq'])
        self.assertEqual(500, takeover['bot_manifest'][0]['health'])
        self.assertEqual((123.0, 4.0, -87.0, 1.25), (
            takeover['bot_manifest'][0]['x'],
            takeover['bot_manifest'][0]['y'],
            takeover['bot_manifest'][0]['z'],
            takeover['bot_manifest'][0]['yaw']))
        battle._send_bot_message.assert_called_once_with({
            'type': 'bot_manifest', 'bots': [{'id': 11}]})
        bots.apply_snapshot.assert_called_once_with(snapshot)
        self.assertEqual(9, battle._bot_fire_seen[11])


if __name__ == '__main__':
    unittest.main()
