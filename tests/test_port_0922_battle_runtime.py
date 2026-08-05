import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


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
            aimingTime=1.0)
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

    def showShooting(self, burst):
        self.last_shot = burst

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

    def configure_battle(self, gui_type, bonus_type):
        self.configured.append((gui_type, bonus_type))

    def attach_avatar_server(self, avatar, bridge):
        self.bridge = bridge

    def deactivate_map(self):
        self.deactivated = True

    def restore_lobby_account(self):
        self.account_restored = True
        return object()


class _OfflineMap(object):
    def __init__(self, bigworld=None):
        self.active = False
        self.bigworld = bigworld

    def create(self, map_name):
        self.active = True
        self.map_name = map_name
        if self.bigworld is not None and self.bigworld.avatar is None:
            self.bigworld.avatar = _Avatar()

    def Active(self):
        return self.active

    def destroy(self):
        self.active = False
        if self.bigworld is not None:
            self.bigworld.clearEntitiesAndSpaces()


class _BigWorld(object):
    def __init__(self, avatar, compatibility):
        self.avatar = avatar
        self.compatibility = compatibility
        self.entities = {}
        self.callbacks = []
        self.now = 10.0
        self.next_id = 100

    def player(self):
        return self.avatar

    def time(self):
        return self.now

    def callback(self, delay, function):
        self.callbacks.append(function)
        return len(self.callbacks)

    def cancelCallback(self, callback_id):
        pass

    def spaceLoadStatus(self):
        return 1.0

    def createEntity(self, name, space_id, vehicle_id, position, rotation,
                     properties):
        self.next_id += 1
        descriptor = _VehicleDescr(
            compactDescr=properties['publicInfo']['compDescr'])
        entity = _Vehicle(
            self.next_id, descriptor, position, rotation, properties)
        self.entities[entity.id] = entity
        if self.compatibility.bridge is not None:
            self.compatibility.bridge.acceptVehicleEnter(entity.id)
        return entity.id

    def destroyEntity(self, entity_id):
        self.entities.pop(entity_id, None)

    def entity(self, entity_id):
        return self.entities.get(entity_id)

    def clearEntitiesAndSpaces(self):
        self.entities.clear()
        self.avatar = None

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
    constants = types.SimpleNamespace(
        ARENA_GUI_TYPE=types.SimpleNamespace(RANDOM=1),
        ARENA_BONUS_TYPE=types.SimpleNamespace(REGULAR=2),
        ARENA_UPDATE=types.SimpleNamespace(
            VEHICLE_ADDED=1, VEHICLE_KILLED=5,
            AVATAR_READY=3, PERIOD=4),
        ARENA_PERIOD=types.SimpleNamespace(BATTLE=5),
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
        app_loader=types.SimpleNamespace(showLobby=mock.Mock(return_value=True)),
        compatibility=compatibility, constants=constants,
        encode_gun_angles=lambda *unused: 0,
        math=types.SimpleNamespace(Vector3=_Vector),
        offline_map_creator=_OfflineMap(bigworld),
        vehicles=types.SimpleNamespace(VehicleDescr=_VehicleDescr))


class BattleRuntimeContractTests(unittest.TestCase):
    def test_map_to_native_vehicle_to_ready_lifecycle(self):
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

        self.assertEqual('running', battle.state)
        self.assertIsNotNone(battle._server.vehicle_id)
        self.assertEqual(1, runtime.bigworld.avatar.vehicle_changed)
        self.assertTrue(battle._server.setClientReady())
        self.assertFalse(battle._server.setClientReady())
        self.assertEqual(500, runtime.bigworld.entity(
            battle._server.vehicle_id).health)

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

        self.assertEqual(0, local.last_shot)
        self.assertEqual(0, remote.last_shot)

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
        runtime.app_loader.showLobby = lambda: calls.append('lobby')

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

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        self.assertEqual(['destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)
        self.assertFalse(battle._map_create_attempted)

    def test_partial_avatar_is_rejected_and_fully_destroyed(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        calls = []

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

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {}, _Client()))

        self.assertEqual(['destroy', 'restore'], calls)
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
        runtime.app_loader.showLobby = lambda: calls.append('lobby')
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))

        self.assertEqual(['destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)
        callback.assert_called_once_with(
            'error', {'message': 'entity loading failed'})

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
