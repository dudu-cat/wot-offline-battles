import importlib.util
import inspect
from pathlib import Path
import pickle
import types
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = (ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922' /
                'entities' / 'runtime.py')
BINDING_PATH = RUNTIME_PATH.parent / 'bigworld_binding.py'
AVATAR_BRIDGE_PATH = RUNTIME_PATH.parent / 'avatar_server.py'


def _runtime_module():
    spec = importlib.util.spec_from_file_location('port0922_entities',
                                                  RUNTIME_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _binding_module():
    spec = importlib.util.spec_from_file_location('port0922_binding',
                                                  BINDING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _avatar_bridge_module():
    spec = importlib.util.spec_from_file_location('port0922_avatar_bridge',
                                                  AVATAR_BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(**overrides):
    value = {
        'properties': {'typeCompDescr': 17, 'team': 1},
        'position': (1.0, 2.0, 3.0),
        'rotation': (0.0, 0.0, 0.0),
        'period': 'battle',
    }
    value.update(overrides)
    return value


class _ArenaUpdate(object):
    VEHICLE_ADDED = 1
    AVATAR_READY = 3
    PERIOD = 4
    VEHICLE_KILLED = 5


class _ArenaPeriod(object):
    PREBATTLE = 2
    BATTLE = 5


class _SiegeState(object):
    DISABLED = 6


class _PhysicsMode(object):
    STANDARD = 0


class _Constants(object):
    ARENA_UPDATE = _ArenaUpdate
    ARENA_PERIOD = _ArenaPeriod
    VEHICLE_PHYSICS_MODE = _PhysicsMode
    VEHICLE_SIEGE_STATE = _SiegeState


class _Turret(object):
    circularVisionRadius = 445


class _EntityDescriptor(object):
    turret = _Turret()

    class gun(object):
        pitchLimits = {'absolute': (-1, 1)}


class _Entity(object):
    typeDescriptor = _EntityDescriptor()

    def __init__(self):
        self.teleports = []
        self.gunAnglesPacked = 0
        self.isStarted = True

    def teleport(self, position, rotation):
        self.teleports.append((position, rotation))

    def set_gunAnglesPacked(self, previous):
        self.previous_gun_angles = previous


class _BigWorld(object):
    def __init__(self):
        self.entity_value = _Entity()
        self.created = []
        self.destroyed = []

    def createEntity(self, kind, space_id, flags, position, rotation, properties):
        self.created.append((kind, space_id, flags, position, rotation, properties))
        return 91

    def destroyEntity(self, entity_id):
        self.destroyed.append(entity_id)

    def entity(self, entity_id):
        return self.entity_value if entity_id == 91 else None

    def serverTime(self):
        return 100.0


class _Avatar(object):
    spaceID = 7
    playerVehicleID = 0
    isGunLocked = True
    ownVehicleAuxPhysicsData = 9
    ownVehicleGear = 9

    def __init__(self):
        self.updates = []
        self.synced = []
        self.changed = 0

    def updateArena(self, update_type, payload):
        self.updates.append((update_type, payload))

    def syncVehicleAttrs(self, attrs):
        self.synced.append(attrs)

    def onVehicleChanged(self):
        self.changed += 1


class _VehicleDescr(object):
    maxHealth = 1000
    turret = _Turret()

    class radio(object):
        distance = 730

    class gun(object):
        pitchLimits = {'absolute': (-1, 1)}

    def __init__(self, compactDescr):
        self.compact_descr = compactDescr

    def makeCompactDescr(self):
        return str(self.compact_descr)


class BigWorldBindingTests(unittest.TestCase):
    def test_json_unicode_names_are_encoded_for_bigworld_string(self):
        module = _binding_module()

        class LegacyUnicode(str):
            pass

        # Python 3 has no distinct unicode/str pair.  Substitute the legacy
        # text class here so this test exercises the embedded Python 2 branch.
        module._unicode_types = (LegacyUnicode,)
        binding = module.BigWorldVehicleBinding(
            _BigWorld(), _Avatar(), _Constants, _VehicleDescr,
            lambda yaw, pitch, limits: 321,
            outfit_provider=lambda descriptor: LegacyUnicode('verified'))

        properties = binding.properties_from_compact_descr(
            17, 1, LegacyUnicode('Player-250'))

        self.assertEqual(b'Player-250', properties['publicInfo']['name'])
        self.assertEqual(b'verified', properties['publicInfo']['outfit'])
        self.assertEqual(str, type(properties['publicInfo']['compDescr']))

    def test_exact_property_contract_and_lifecycle_operations(self):
        module = _binding_module()
        bigworld = _BigWorld()
        avatar = _Avatar()
        sent = []
        binding = module.BigWorldVehicleBinding(
            bigworld, avatar, _Constants, _VehicleDescr,
            lambda yaw, pitch, limits: 321,
            lambda entity_id, command: sent.append((entity_id, command)),
            lambda descriptor: 'verified')

        properties = binding.properties_from_compact_descr(17, 1, 'Alpha')
        expected_properties = {
            'publicInfo', 'gunAnglesPacked', 'health', 'isCrewActive',
            'steeringAngle', 'isStrafing', 'physicsMode', 'siegeState',
            'engineMode', 'damageStickers', 'publicStateModifiers',
            'stunInfo'}
        self.assertEqual(expected_properties,
                         set(module.BigWorldVehicleBinding.PROPERTY_NAMES))
        self.assertEqual(expected_properties, set(properties))
        self.assertEqual(0, properties['physicsMode'])
        self.assertEqual(0.0, properties['stunInfo'])
        self.assertNotIn('circularVisionRadius', properties)
        self.assertNotIn('radioDistance', properties)
        self.assertEqual(91, binding.create_vehicle(
            properties, (1, 2, 3), (0, 0, 0)))
        snapshot = _snapshot(properties=properties)
        binding.arena_vehicle_added(91, snapshot)
        binding.avatar_select_vehicle(91)
        binding.avatar_vehicle_entered()
        binding.avatar_client_ready()
        binding.avatar_ready()
        binding.arena_period('battle')
        binding.update_vehicle(91, (4, 5, 6), (0, 1, 0))
        binding.update_vehicle_aim(91, 3.1, -3.1, 0.2)
        binding.send_vehicle_input(91, {'throttle': 1})
        binding.arena_vehicle_killed(91, 23, 7)
        binding.arena_vehicle_removed(91)
        binding.destroy_entity(91)

        self.assertEqual('Vehicle', bigworld.created[0][0])
        self.assertEqual(91, avatar.playerVehicleID)
        self.assertEqual(1, avatar.changed)
        self.assertEqual([{'circularVisionRadius': 445}], avatar.synced)
        self.assertEqual([((4, 5, 6), (0, 1, 0))],
                         bigworld.entity_value.teleports)
        self.assertEqual(321, bigworld.entity_value.gunAnglesPacked)
        self.assertEqual(0, bigworld.entity_value.previous_gun_angles)
        self.assertEqual([(91, {'throttle': 1})], sent)
        self.assertEqual([1, 3, 4, 5],
                         [item[0] for item in avatar.updates])
        vehicle_added = pickle.loads(zlib.decompress(avatar.updates[0][1]))
        self.assertEqual(18, len(vehicle_added))
        self.assertEqual([91, '17', 'Alpha', 1], vehicle_added[:4])
        self.assertEqual(91, pickle.loads(avatar.updates[1][1]))
        self.assertEqual((5, 0, 0, []),
                         pickle.loads(zlib.decompress(avatar.updates[2][1])))
        self.assertEqual((91, 23, 0, 7),
                         pickle.loads(avatar.updates[3][1]))
        self.assertEqual([91], bigworld.destroyed)

    def test_prebattle_period_carries_native_countdown_deadline(self):
        module = _binding_module()
        bigworld = _BigWorld()
        avatar = _Avatar()
        binding = module.BigWorldVehicleBinding(
            bigworld, avatar, _Constants, _VehicleDescr,
            lambda yaw, pitch, limits: 321,
            outfit_provider=lambda descriptor: '')

        binding.arena_period('prebattle', 15.0)

        self.assertEqual(
            (2, 115.0, 15.0, []),
            pickle.loads(zlib.decompress(avatar.updates[0][1])))

    def test_exact_property_schema_rejects_jointly_wrong_producer_values(self):
        module = _binding_module()
        binding = module.BigWorldVehicleBinding(
            _BigWorld(), _Avatar(), _Constants, _VehicleDescr,
            lambda yaw, pitch, limits: 321,
            outfit_provider=lambda descriptor: '')
        properties = binding.properties_from_compact_descr(17, 1, 'Alpha')

        invalid = dict(properties)
        invalid['gunAnglesPacked'] = (0.0, 0.0)
        with self.assertRaisesRegex(module.CapabilityError,
                                    'gunAnglesPacked'):
            binding.create_vehicle(invalid, (0, 0, 0), (0, 0, 0))

        invalid = dict(properties)
        invalid['engineMode'] = (0,)
        with self.assertRaisesRegex(module.CapabilityError, 'engineMode'):
            binding.create_vehicle(invalid, (0, 0, 0), (0, 0, 0))

        invalid = dict(properties)
        invalid['publicInfo'] = dict(properties['publicInfo'], outfit={})
        with self.assertRaisesRegex(module.CapabilityError,
                                    'publicInfo.outfit'):
            binding.create_vehicle(invalid, (0, 0, 0), (0, 0, 0))

    def test_capability_check_rejects_missing_target_methods(self):
        module = _binding_module()
        binding = module.BigWorldVehicleBinding(
            object(), _Avatar(), _Constants, _VehicleDescr, lambda *args: args,
            outfit_provider=lambda descriptor: None)
        with self.assertRaisesRegex(module.CapabilityError, 'createEntity'):
            binding.self_check()

    def test_1513_does_not_require_nonexistent_vehicle_removed_update(self):
        module = _binding_module()
        constants = types.SimpleNamespace(
            ARENA_UPDATE=types.SimpleNamespace(
                VEHICLE_ADDED=1, VEHICLE_KILLED=2,
                AVATAR_READY=3, PERIOD=4),
            ARENA_PERIOD=_ArenaPeriod,
            VEHICLE_PHYSICS_MODE=_PhysicsMode,
            VEHICLE_SIEGE_STATE=_SiegeState)
        binding = module.BigWorldVehicleBinding(
            _BigWorld(), _Avatar(), constants, _VehicleDescr,
            lambda *args: args, outfit_provider=lambda descriptor: '')

        self.assertTrue(binding.self_check())
        self.assertIsNone(binding.arena_vehicle_removed(91))

    def test_vehicle_ready_waits_for_registry_world_start_and_descriptor(self):
        module = _binding_module()
        bigworld = _BigWorld()
        avatar = _Avatar()
        binding = module.BigWorldVehicleBinding(
            bigworld, avatar, _Constants, _VehicleDescr,
            lambda *args: 0, outfit_provider=lambda descriptor: '')

        self.assertFalse(binding.is_vehicle_ready(90))
        bigworld.entity_value.inWorld = False
        self.assertFalse(binding.is_vehicle_ready(91))
        bigworld.entity_value.inWorld = True
        descriptor = bigworld.entity_value.typeDescriptor
        bigworld.entity_value.typeDescriptor = None
        self.assertFalse(binding.is_vehicle_ready(91))
        bigworld.entity_value.typeDescriptor = descriptor
        bigworld.entity_value.isStarted = False
        self.assertFalse(binding.is_vehicle_ready(91))
        bigworld.entity_value.isStarted = True
        self.assertTrue(binding.is_vehicle_ready(91))


class _BridgeBinding(object):
    def __init__(self):
        self.events = []
        self.ready = True

    def create_vehicle(self, properties, position, rotation):
        self.events.append(('create', properties, position, rotation))
        return 91

    def arena_vehicle_added(self, vehicle_id, snapshot):
        self.events.append(('added', vehicle_id))

    def avatar_select_vehicle(self, vehicle_id):
        self.events.append(('select', vehicle_id))

    def avatar_vehicle_entered(self):
        self.events.append(('entered',))

    def is_vehicle_ready(self, vehicle_id):
        return self.ready

    def avatar_client_ready(self):
        self.events.append(('client_ready',))

    def avatar_ready(self):
        self.events.append(('avatar_ready',))

    def arena_period(self, period, duration=0.0):
        self.events.append(('period', period, duration))

    def arena_vehicle_removed(self, vehicle_id):
        self.events.append(('removed', vehicle_id))

    def destroy_entity(self, vehicle_id):
        self.events.append(('destroy', vehicle_id))


class _ReentrantBridgeBinding(_BridgeBinding):
    def __init__(self):
        _BridgeBinding.__init__(self)
        self.bridge = None
        self.in_native_enter = False
        self.native_matrices_initialized = False

    def avatar_select_vehicle(self, vehicle_id):
        if self.in_native_enter and not self.native_matrices_initialized:
            raise AssertionError(
                'player id notifier ran before native matrix initialization')
        _BridgeBinding.avatar_select_vehicle(self, vehicle_id)

    def create_vehicle(self, properties, position, rotation):
        self.events.append(('create', properties, position, rotation))
        self.in_native_enter = True
        try:
            self.bridge.acceptVehicleEnter(91)
            # playerVehicleID is still zero here, so exact
            # PlayerAvatar.vehicle_onEnterWorld treats this entity as remote
            # and never initializes the own-vehicle matrix providers.
            self.bridge.setClientReady()
            self.bridge.completeVehicleEnter(91)
        finally:
            self.in_native_enter = False
        return 91


class _NativeEnterOrderBinding(_BridgeBinding):
    def __init__(self):
        _BridgeBinding.__init__(self)
        self.in_native_enter = False
        self.native_matrices_initialized = False

    def avatar_select_vehicle(self, vehicle_id):
        if self.in_native_enter and not self.native_matrices_initialized:
            raise AssertionError(
                'player id notifier ran before native matrix initialization')
        _BridgeBinding.avatar_select_vehicle(self, vehicle_id)


class _FailingSelectBinding(_BridgeBinding):
    def avatar_select_vehicle(self, vehicle_id):
        _BridgeBinding.avatar_select_vehicle(self, vehicle_id)
        if vehicle_id:
            raise RuntimeError('player Vehicle selection failed')


class _BridgeAvatar(object):
    def __init__(self):
        self.synced = []
        self.settings = []
        self.responses = []
        self.tokens = []
        self.account_stats = []
        self.viewpoint_switches = []

    def syncVehicleAttrs(self, attrs):
        self.synced.append(attrs)

    def updateVehicleSetting(self, vehicle_id, code, value):
        self.settings.append((vehicle_id, code, value))

    def onCmdResponse(self, request_id, result, text):
        self.responses.append((request_id, result, text))

    def onTokenReceived(self, request_id, token_type, data):
        self.tokens.append((request_id, token_type, data))

    def receiveAccountStats(self, request_id, data):
        self.account_stats.append((request_id, pickle.loads(data)))

    def onSwitchViewpoint(self, vehicle_id, position):
        self.viewpoint_switches.append((vehicle_id, position))


class _Sender(object):
    def __init__(self):
        self.events = []

    def send_avatar_input(self, vehicle_id, kind, payload):
        self.events.append((vehicle_id, kind, payload))


class AvatarServerBridgeTests(unittest.TestCase):
    def test_deferred_server_replays_synchronous_native_ready_barrier(self):
        module = _avatar_bridge_module()
        deferred = module.DeferredAvatarServer()
        target = mock.Mock()

        deferred.setClientReady()
        deferred.autoAim(0)
        deferred.attach(target)

        self.assertEqual(
            [mock.call.setClientReady(), mock.call.autoAim(0)],
            target.mock_calls)

    def test_exact_voip_queries_are_explicitly_disabled(self):
        module = _avatar_bridge_module()
        deferred = module.DeferredAvatarServer()
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), _BridgeBinding(),
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender())

        for server in (deferred, bridge):
            self.assertIs(server.voipController, server)
            self.assertFalse(server.isReady())
            self.assertFalse(server.isVOIPEnabled())
            self.assertFalse(server.isVivox())
            self.assertFalse(server.isYY())
            self.assertFalse(server.isPlayerSpeaking(12345))

    def test_stock_sequence_forwards_known_mailbox_and_lan_inputs(self):
        module = _avatar_bridge_module()
        binding = _BridgeBinding()
        avatar = _BridgeAvatar()
        sender = _Sender()
        builder = _runtime_module().EntityPropertyBuilder(('typeCompDescr', 'team'))
        bridge = module.AvatarServerBridge(avatar, binding, builder, sender,
                                           account_commands=('sync',))

        self.assertEqual(91, bridge.addVehicleToArena(_snapshot()))
        bridge.acceptVehicleEnter(91)
        self.assertTrue(bridge.setClientReady())
        bridge.completeVehicleEnter(91)
        self.assertTrue(bridge.flushClientReady())
        self.assertFalse(bridge.setClientReady())
        bridge.syncVehicleAttrs({'radioDistance': 730})
        bridge.vehicle_moveWith(7)
        bridge.trackRelativePointWithGun((1, 2, 3))
        bridge.vehicle_changeSetting(3, 1)
        bridge.vehicle_shoot()
        self.assertIsNone(bridge.switchObserverFPVControlMode(2))
        self.assertIsNone(bridge.setRemoteCamera({'zoom': 1.0}))
        self.assertIsNone(bridge.activateEquipment(101))
        self.assertIsNone(bridge.setMicrophoneMute(True))
        bridge.setDevelopmentFeature('pickup', 0, 'straight')
        bridge.requestToken(5, 2)
        bridge.sendAccountStats(6, ('wins', 'losses'))
        bridge.doCmdStr(4, 'sync', '')
        self.assertTrue(bridge.destroy())

        self.assertEqual(['create', 'added', 'select', 'entered',
                          'client_ready', 'avatar_ready', 'period', 'removed',
                          'destroy'],
                         [event[0] for event in binding.events])
        self.assertEqual([(91, 'move', {'flags': 7}),
                          (91, 'track_relative', {'point': (1, 2, 3)}),
                          (91, 'shoot', {}),
                          (91, 'development', {'name': 'pickup',
                                               'args': (0, 'straight')})],
                         sender.events)
        self.assertEqual([(91, 3, 1)], avatar.settings)
        self.assertEqual([(4, 0, '')], avatar.responses)
        self.assertEqual([(5, 2, '')], avatar.tokens)
        self.assertEqual([(6, {'wins': 0, 'losses': 0})],
                         avatar.account_stats)

    def test_period_reentry_accepts_stock_initial_move(self):
        module = _avatar_bridge_module()
        binding = _BridgeBinding()
        avatar = _BridgeAvatar()
        sender = _Sender()
        bridge = module.AvatarServerBridge(
            avatar, binding,
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            sender)
        original_arena_period = binding.arena_period

        def arena_period(period, duration=0.0):
            original_arena_period(period, duration)
            bridge.vehicle_moveWith(0)

        binding.arena_period = arena_period
        self.assertEqual(91, bridge.addVehicleToArena(_snapshot()))
        self.assertTrue(bridge.acceptVehicleEnter(91))
        self.assertTrue(bridge.setClientReady())
        self.assertTrue(bridge.completeVehicleEnter(91))

        self.assertTrue(bridge.flushClientReady())
        self.assertEqual([(91, 'move', {'flags': 0})], sender.events)

    def test_reentrant_vehicle_enter_fails_closed_before_registration(self):
        module = _avatar_bridge_module()
        binding = _ReentrantBridgeBinding()
        ready = []
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), binding,
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender(), on_ready=lambda: ready.append(True))
        binding.bridge = bridge
        binding.ready = False

        with self.assertRaisesRegex(
                module.AvatarBridgeError,
                'before createEntity returned'):
            bridge.addVehicleToArena(_snapshot())

        self.assertEqual(
            ['create', 'destroy'],
            [event[0] for event in binding.events])
        self.assertEqual([], ready)
        self.assertIsNone(bridge.vehicle_id)
        self.assertFalse(bridge.flushClientReady())

    def test_native_vehicle_enter_does_not_repeat_player_id_notifier(self):
        module = _avatar_bridge_module()
        binding = _NativeEnterOrderBinding()
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), binding,
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender())

        self.assertEqual(91, bridge.addVehicleToArena(_snapshot()))
        self.assertEqual(1, [event[0] for event in binding.events].count(
            'select'))

        binding.in_native_enter = True
        self.assertTrue(bridge.acceptVehicleEnter(91))
        # Exact PlayerAvatar.vehicle_onEnterWorld must initialize its own
        # matrices before it records VEHICLE_ENTERED.  Repeating the notifier
        # from the wrapper would record that step and start visuals too early.
        self.assertEqual(1, [event[0] for event in binding.events].count(
            'select'))
        binding.native_matrices_initialized = True
        binding.in_native_enter = False
        self.assertTrue(bridge.completeVehicleEnter(91))

    def test_failed_player_selection_resets_partial_avatar_binding(self):
        module = _avatar_bridge_module()
        binding = _FailingSelectBinding()
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), binding,
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender())

        with self.assertRaisesRegex(
                RuntimeError, 'player Vehicle selection failed'):
            bridge.addVehicleToArena(_snapshot())

        self.assertEqual(
            [('create', {'typeCompDescr': 17, 'team': 1},
              (1.0, 2.0, 3.0), (0.0, 0.0, 0.0)),
             ('added', 91), ('select', 91), ('destroy', 91),
             ('select', 0)],
            binding.events)
        self.assertIsNone(bridge.vehicle_id)
        self.assertFalse(bridge.flushClientReady())

    def test_early_ready_waits_for_vehicle_bind_and_arena_registration(self):
        module = _avatar_bridge_module()
        binding = _BridgeBinding()
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), binding,
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender())

        self.assertTrue(bridge.setClientReady())
        self.assertEqual(91, bridge.addVehicleToArena(_snapshot()))
        self.assertNotIn('client_ready', [event[0] for event in binding.events])
        self.assertTrue(bridge.acceptVehicleEnter(91))
        self.assertTrue(bridge.completeVehicleEnter(91))
        self.assertTrue(bridge.flushClientReady())
        self.assertEqual(
            ['create', 'added', 'select', 'entered', 'client_ready',
             'avatar_ready', 'period'],
            [event[0] for event in binding.events])

    def test_destroy_rejects_late_vehicle_enter_callback(self):
        module = _avatar_bridge_module()
        binding = _BridgeBinding()
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), binding,
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender())

        self.assertEqual(91, bridge.addVehicleToArena(_snapshot()))
        self.assertTrue(bridge.destroy())
        self.assertFalse(bridge.acceptVehicleEnter(91))
        self.assertFalse(bridge.completeVehicleEnter(91))
        self.assertFalse(bridge.setClientReady())
        self.assertEqual(
            ['create', 'added', 'select', 'removed', 'destroy'],
            [event[0] for event in binding.events])

    def test_duplicate_leave_mailbox_is_delivered_once(self):
        module = _avatar_bridge_module()
        leaves = []
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), _BridgeBinding(),
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender(), on_leave=lambda: leaves.append(True))

        self.assertTrue(bridge.leaveArena({}))
        self.assertFalse(bridge.leaveArena({}))
        self.assertEqual([True], leaves)

    def test_failed_leave_registration_can_be_retried(self):
        module = _avatar_bridge_module()
        leaves = []

        def leave():
            leaves.append(True)
            if len(leaves) == 1:
                raise RuntimeError('callback registration failed')

        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), _BridgeBinding(),
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender(), on_leave=leave)

        with self.assertRaisesRegex(
                RuntimeError, 'callback registration failed'):
            bridge.leaveArena({})
        self.assertTrue(bridge.leaveArena({}))
        self.assertEqual([True, True], leaves)

    def test_partial_ready_publish_is_latched_and_never_replayed(self):
        module = _avatar_bridge_module()
        binding = _BridgeBinding()

        def fail_client_ready():
            binding.events.append(('client_ready_failed',))
            raise RuntimeError('client ready failed')

        binding.avatar_client_ready = fail_client_ready
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), binding,
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            _Sender())
        self.assertEqual(91, bridge.addVehicleToArena(_snapshot()))
        self.assertTrue(bridge.acceptVehicleEnter(91))
        self.assertTrue(bridge.setClientReady())
        self.assertTrue(bridge.completeVehicleEnter(91))

        with self.assertRaisesRegex(RuntimeError, 'client ready failed'):
            bridge.flushClientReady()
        with self.assertRaisesRegex(
                module.AvatarBridgeError, 'Vehicle is not ready'):
            bridge.vehicle_moveWith(0)
        with self.assertRaisesRegex(
                module.AvatarBridgeError, 'client ready failed'):
            bridge.flushClientReady()

        names = [event[0] for event in binding.events]
        self.assertEqual(1, names.count('entered'))
        self.assertEqual(1, names.count('client_ready_failed'))
        self.assertNotIn('avatar_ready', names)

    def test_exact_1513_mailbox_arity_is_explicit(self):
        module = _avatar_bridge_module()
        expected_wire_args = {
            'setClientReady': 0,
            'leaveArena': 1,
            'confirmBattleResultsReceiving': 0,
            'makeDenunciation': 3,
            'banUnbanUser': 5,
            'requestToken': 2,
            'sendAccountStats': 2,
            'setClientCtx': 1,
            'vehicle_moveWith': 1,
            'vehicle_shoot': 0,
            'vehicle_trackWorldPointWithGun': 1,
            'vehicle_trackRelativePointWithGun': 1,
            'vehicle_stopTrackingWithGun': 2,
            'vehicle_changeSetting': 2,
            'vehicle_teleport': 2,
            'vehicle_replenishAmmo': 0,
            'setVehicleDevelopmentFeature': 4,
            'setDevelopmentFeature': 3,
            'logStreamCorruption': 5,
            'autoAim': 1,
            'monitorVehicleDamagedDevices': 1,
            'activateEquipment': 1,
            'switchObserverFPV': 1,
            'switchViewPointOrBindToVehicle': 2,
            'trackRelativePointWithGun': 1,
            'setRemoteCamera': 1,
            'switchObserverFPVControlMode': 1,
            'sendStateToOwnClient': 0,
        }

        for name, wire_count in expected_wire_args.items():
            signature = inspect.signature(
                getattr(module.AvatarServerBridge, name))
            parameters = list(signature.parameters.values())
            self.assertFalse(any(
                value.kind in (inspect.Parameter.VAR_POSITIONAL,
                               inspect.Parameter.VAR_KEYWORD)
                for value in parameters), name)
            self.assertEqual(wire_count + 1, len(parameters), name)

    def test_spectator_switch_is_explicitly_rejected_without_client_desync(self):
        module = _avatar_bridge_module()
        avatar = _BridgeAvatar()
        binding = _BridgeBinding()
        sender = _Sender()
        bridge = module.AvatarServerBridge(
            avatar, binding,
            _runtime_module().EntityPropertyBuilder(
                ('typeCompDescr', 'team')),
            sender)

        self.assertFalse(bridge.switchViewPointOrBindToVehicle(False, 92))
        self.assertFalse(bridge.switchViewPointOrBindToVehicle(True, 7))

        self.assertEqual([], avatar.viewpoint_switches)
        self.assertEqual([], binding.events)
        self.assertEqual([], sender.events)

    def test_unknown_mailbox_interface_is_not_silently_accepted(self):
        module = _avatar_bridge_module()
        bridge = module.AvatarServerBridge(
            _BridgeAvatar(), _BridgeBinding(),
            _runtime_module().EntityPropertyBuilder(('typeCompDescr', 'team')),
            _Sender(), account_commands=('sync',))
        with self.assertRaises(AttributeError):
            bridge.unknownNativeMethod()
        with self.assertRaises(AttributeError):
            bridge.doCmdIntArr(1, 'other', [])


if __name__ == '__main__':
    unittest.main()
