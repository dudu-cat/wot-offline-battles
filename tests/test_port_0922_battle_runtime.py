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

from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime, _LANInputSender


class _Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __add__(self, other):
        return _Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return _Vector(self.x - other.x, self.y - other.y, self.z - other.z)

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


class _Descriptor(object):
    def __init__(self, name='ussr:R11_MS-1'):
        self.name = name
        shell = types.SimpleNamespace(
            compactDescr=101, damage=(100.0,), piercingPower=(1000.0,),
            kind='ARMOR_PIERCING')
        self.gun = types.SimpleNamespace(
            pitchLimits={'absolute': (-0.2, 0.4)}, shots=[{'shell': shell}],
            maxAmmo=40, clip=(1,), reloadTime=1.5, rotationSpeed=1.0,
            aimingTime=1.0, burst=(1, 0.1))
        self.turret = types.SimpleNamespace(
            circularVisionRadius=330.0, rotationSpeed=1.0)
        self.radio = types.SimpleNamespace(distance=400.0)
        self.physics = {'speedLimits': (14.0, 7.0)}
        self.type = types.SimpleNamespace(name=name, tags=('lightTank',))
        self.hull = {}
        self.maxHealth = 500
        self.activeGunShotIndex = 0

    def makeCompactDescr(self):
        return self.name


class _VehicleDescr(object):
    def __new__(cls, typeName=None, compactDescr=None):
        return _Descriptor(typeName or compactDescr or 'ussr:R11_MS-1')


class _Vehicle(object):
    def __init__(self, entity_id, descriptor, position, rotation, properties):
        self.id = entity_id
        self.typeDescriptor = descriptor
        self.position = position
        self.yaw = float(rotation[0])
        self.health = properties['health']
        self.isCrewActive = True
        self.gunAnglesPacked = properties.get('gunAnglesPacked', 0)
        self.isStarted = True
        self.inWorld = True
        self.teleports = []

    def teleport(self, position, rotation):
        self.position = position
        self.yaw = float(rotation[0])
        self.teleports.append((position, rotation))

    def getAimParams(self):
        return (0.0, 0.0)

    def showShooting(self, burst, is_predicted=False):
        self.last_shot = (burst, is_predicted)

    def set_gunAnglesPacked(self, previous):
        self.previous_gun_angles = previous

    def set_health(self, previous):
        self.previous_health = previous

    def isAlive(self):
        return self.health > 0 and self.isCrewActive

    def onHealthChanged(self, health, attacker_id, reason_id):
        self.health_change = (health, attacker_id, reason_id)


class _Avatar(object):
    def __init__(self):
        self._offlineLANInitComplete = True
        self._offlineLANPlayerReady = True
        self.spaceID = 7
        self.playerVehicleID = 0
        self.isGunLocked = True
        self.ownVehicleAuxPhysicsData = 0
        self.ownVehicleGear = 0
        self.arena_updates = []
        self.positions = []
        self.round_finished = []
        self.ammo_updates = []
        self.filter = object()

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

    def updateVehicleGunReloadTime(self, vehicle_id, time_left, base_time):
        self.reload = (vehicle_id, time_left, base_time)

    def updateVehicleHealth(self, vehicle_id, health, death_reason_id,
                            is_crew_active, is_respawn):
        self.health_update = (vehicle_id, health, death_reason_id,
                              is_crew_active, is_respawn)

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

    def set_battle_network_client(self, client):
        self.network_client = client

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
            self.bigworld.avatar.guiSessionProvider = types.SimpleNamespace(
                shared=types.SimpleNamespace(
                    arenaLoad=_ArenaLoadController(self.app_loader)))
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

    def player(self):
        return self.avatar

    def time(self):
        return self.now

    def serverTime(self):
        return self.now

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

    def setWatcher(self, name, enabled):
        self.operations.append(('watcher', name, enabled))

    def clearAllSpaces(self):
        self.clearEntitiesAndSpaces()

    def wg_collideSegment(self, space_id, start, end, mask):
        if start.y > end.y and abs(start.x - end.x) < 0.001 and abs(start.z - end.z) < 0.001:
            return (_Vector(start.x, 0.0, start.z),)
        return None


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

    def send_input(self, *values):
        self.sent.append(('input', values))
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
            VEHICLE_ADDED=1, VEHICLE_KILLED=5,
            AVATAR_READY=3, PERIOD=4),
        ARENA_PERIOD=types.SimpleNamespace(PREBATTLE=2, BATTLE=5),
        VEHICLE_PHYSICS_MODE=types.SimpleNamespace(STANDARD=0),
        VEHICLE_SIEGE_STATE=types.SimpleNamespace(DISABLED=0),
        VEHICLE_SETTING=types.SimpleNamespace(CURRENT_SHELLS=1),
        FINISH_REASON=types.SimpleNamespace(
            UNKNOWN=0, EXTERMINATION=1, BASE=2, TIMEOUT=3))
    arena = types.SimpleNamespace(
        geometryName='01_karelia', gameplayName='ctf')
    return types.SimpleNamespace(
        account_commands=types.SimpleNamespace(
            CMD_GET_AVATAR_SYNC=1, CMD_ADD_INT_USER_SETTINGS=2,
            CMD_DEL_INT_USER_SETTINGS=3),
        arena_cache={1: arena}, bigworld=bigworld,
        app_loader=app_loader,
        compatibility=compatibility, constants=constants,
        encode_gun_angles=lambda *unused: 0,
        game=types.SimpleNamespace(abort=mock.Mock()),
        gui_global_space_id=types.SimpleNamespace(
            LOBBY=4, BATTLE_LOADING=5, BATTLE=6),
        hangar_space=types.SimpleNamespace(
            g_hangarSpace=hangar_space),
        math=types.SimpleNamespace(Vector3=_Vector),
        offline_map_creator=_OfflineMap(bigworld, app_loader),
        vehicles=types.SimpleNamespace(VehicleDescr=_VehicleDescr))


class BattleRuntimeContractTests(unittest.TestCase):
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
            runtime.constants.ARENA_UPDATE.VEHICLE_ADDED,
            runtime.bigworld.avatar.arena_updates[0][0])
        self.assertIsNone(runtime.bigworld.entity(battle._server.vehicle_id))
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
        battle.on_snapshot({
            'round_id': 1, 'server_tick': 3,
            'players': [player], 'bots': manifests[0]})
        self.assertFalse(battle._sync._entities['bot:11']['dead'])
        self.assertFalse(battle._sync._entities['bot:12']['dead'])

        battle._frame()
        self.assertIn('bot:11', battle._records)
        self.assertNotIn('bot:12', battle._records)
        runtime.bigworld.now += 0.19
        battle._frame()
        self.assertNotIn('bot:12', battle._records)
        runtime.bigworld.now += 0.02
        battle._frame()
        self.assertIn('bot:12', battle._records)

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
        self.assertEqual([(2, 25.0, 15.0, [])], periods)
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
        self.assertEqual((5, 925.0, 900.0, []), periods[-1])

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

    def test_local_input_moves_vehicle_and_publishes_pose(self):
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
            forward=1.0, turn=0.0,
            send_current=lambda: client.send_input('current'))

        battle._drive_local(0.1)

        self.assertGreater(entity.position.z, 0.0)
        self.assertTrue(runtime.bigworld.avatar.positions)
        self.assertTrue(client.sent)

    def test_relative_gun_tracking_uses_delta_and_stop_uses_hull_yaw(self):
        owner = types.SimpleNamespace(
            local_pose=lambda: ((100.0, 5.0, 200.0), 0.5),
            client=types.SimpleNamespace(send_input=mock.Mock(return_value=True)))
        owner.shoot = mock.Mock(return_value=True)
        sender = _LANInputSender(owner)

        sender.send_avatar_input(1, 'track_relative', {
            'point': _Vector(10.0, 2.0, 20.0)})
        self.assertAlmostEqual(math.atan2(10.0, 20.0), sender.aim_yaw)
        self.assertAlmostEqual(math.atan2(2.0, math.sqrt(500.0)),
                               sender.gun_pitch)

        sender.send_avatar_input(1, 'stop_tracking', {
            'turret_yaw': 0.25, 'gun_pitch': -0.1})
        self.assertAlmostEqual(0.75, sender.aim_yaw)
        self.assertAlmostEqual(-0.1, sender.gun_pitch)

    def test_local_snapshot_only_corrects_large_divergence(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'
        battle._binding = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(10, 0, 10), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'state': {'health': 500},
                         'kind': 'player', 'network_id': 1, 'local': True}}

        battle._update_entity({
            'entity': 'player:1', 'kind': 'player', 'id': 1,
            'pose': {'x': 12.0, 'y': 0.0, 'z': 10.0, 'yaw': 0.1},
            'state': {'health': 500}})
        battle._binding.update_vehicle.assert_not_called()

        battle._update_entity({
            'entity': 'player:1', 'kind': 'player', 'id': 1,
            'pose': {'x': 20.0, 'y': 0.0, 'z': 10.0, 'yaw': 0.2},
            'state': {'health': 500}})
        battle._binding.update_vehicle.assert_called_once()

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
        battle._show_shot({'attacker': 2})

        self.assertEqual((1, False), local.last_shot)
        self.assertEqual((1, False), remote.last_shot)

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

        battle._binding.update_vehicle.assert_not_called()
        battle._binding.update_vehicle_aim.assert_not_called()
        self.assertNotIn(11, battle._last_health)

        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._flush_pending_entities(runtime.bigworld.now)

        self.assertTrue(battle._records['player:2']['ready'])
        battle._binding.update_vehicle.assert_called_once()
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

    def test_pending_remote_destroy_catches_late_world_entry(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._config = {
            'map': '01_karelia',
            'vehicle': 'ussr:R11_MS-1',
            'startupTimeoutSeconds': 30.0}
        battle._server = types.SimpleNamespace(
            vehicleEnterStatus=lambda unused_id: ('completed', None))
        battle._binding = mock.Mock()
        battle._binding.is_vehicle_ready.side_effect = lambda entity_id: (
            runtime.bigworld.entity(entity_id) is not None)
        battle._binding.create_vehicle.side_effect = (
            lambda properties, position, rotation:
            runtime.bigworld.createEntity(
                'Vehicle', 7, 0, position, rotation, properties))
        battle._binding.properties_from_compact_descr.return_value = {
            'publicInfo': {'compDescr': 'ussr:R11_MS-1'},
            'health': 500}
        runtime.bigworld.defer_vehicle_entry = True

        battle._create_remote({
            'type': 'create', 'entity': 'bot:2', 'kind': 'bot', 'id': 2,
            'state': {
                'team': 2, 'slot': 0, 'x': 5.0, 'z': 5.0,
                'vehicle': 'ussr:R11_MS-1', 'health': 500}})
        record = battle._records['bot:2']
        vehicle_id = record['engine_id']
        battle._binding.destroy_entity.side_effect = \
            runtime.bigworld.destroyEntity

        battle._destroy_entity({'entity': 'bot:2'})
        self.assertTrue(record['tombstone'])
        self.assertIn(vehicle_id, runtime.bigworld.pending_entities)
        original_state = dict(record['state'])
        battle._update_entity({
            'entity': 'bot:2',
            'pose': {'x': 99.0, 'y': 0.0, 'z': 99.0},
            'state': {'health': 1}})
        self.assertEqual(original_state, record['state'])
        self.assertNotIn('pending_pose', record)

        runtime.bigworld.enter_pending_vehicle(vehicle_id)
        self.assertIsNotNone(runtime.bigworld.entity(vehicle_id))
        battle._flush_pending_entities(runtime.bigworld.now)

        self.assertIsNone(runtime.bigworld.entity(vehicle_id))
        self.assertTrue(record['visible_destroy_requested'])

    def test_terminal_result_notifies_native_hud_once_with_finish_reason(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'

        battle.on_events({'events': [{
            'kind': 'battle_result', 'winner': 2,
            'reason': 'team_eliminated'}]})
        battle.on_snapshot({'battle_result': {
            'winner': 2, 'reason': 'team_eliminated'}})

        self.assertEqual(
            [(2, runtime.constants.FINISH_REASON.EXTERMINATION)],
            runtime.bigworld.avatar.round_finished)
        self.assertTrue(battle._round_finished_notified)

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
        self.assertEqual(255, update[3])
        self.assertEqual(2, update[4])

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
            matInfo=types.SimpleNamespace(armor=10.0))]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'state': {'team': 1},
                         'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11, 'state': {'team': 2},
                      'kind': 'bot', 'network_id': 2, 'local': False}}
        get_shot = mock.Mock(return_value=(
            _Vector(0, 2, 0), _Vector(0, 0, 1)))
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=get_shot)
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock(return_value=True))

        battle._resolve_hit(7, 0.0, 0.0)

        get_shot.assert_called_once_with()
        battle.client.send_bot_hit.assert_called_once()

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
            matInfo=types.SimpleNamespace(armor=10.0))
        target.collideSegmentExt = lambda start, end: [collision]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 1}},
            'bot:2': {'engine_id': 11, 'state': {'team': 2}}}
        battle.client = types.SimpleNamespace(
            send_bot_bot_hit=mock.Mock(return_value=True))

        self.assertTrue(battle._resolve_bot_shot({
            'id': 1, 'target_kind': 'bot', 'target_id': 2,
            'shell_index': 0}, 3))
        battle.client.send_bot_bot_hit.assert_called_once()

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
            'kind': 'authority', 'round_id': 4, 'player_id': 1}]})

        self.assertEqual(9, battle._bot_fire_seen[11])
        battle._bots.apply_snapshot.assert_called_once_with(
            battle._last_snapshot)

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
            'bots': [{'id': 11, 'fire_seq': 9,
                      'health': 500, 'alive': True}]}

        battle.on_snapshot(snapshot)

        bots.battle_start.assert_called_once()
        battle._send_bot_message.assert_called_once_with({
            'type': 'bot_manifest', 'bots': [{'id': 11}]})
        bots.apply_snapshot.assert_called_once_with(snapshot)
        self.assertEqual(9, battle._bot_fire_seen[11])


if __name__ == '__main__':
    unittest.main()
