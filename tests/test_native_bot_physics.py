import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/native_bot_physics.py"
)
LOADER_PATH = ROOT / "scripts/client/gui/mods/mod_offhangar.py"
BATTLE_PATH = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"


class Vector3(object):
    def __init__(self, x, y=None, z=None):
        if y is None and z is None:
            x, y, z = x
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class Matrix(object):
    def __init__(self, source=None):
        source = source or types.SimpleNamespace()
        position = getattr(source, "translation", Vector3(0, 0, 0))
        self.translation = Vector3(position.x, position.y, position.z)
        self.yaw = float(getattr(source, "yaw", 0.0))
        self.pitch = float(getattr(source, "pitch", 0.0))
        self.roll = float(getattr(source, "roll", 0.0))

    def applyVector(self, vector):
        return Vector3(
            vector.x * math.cos(self.yaw) + vector.z * math.sin(self.yaw),
            vector.y,
            -vector.x * math.sin(self.yaw) + vector.z * math.cos(self.yaw),
        )


class EntityMatrix(Matrix):
    def __init__(self, entity):
        super().__init__()
        self.entity = entity
        self._not_model = False

    @property
    def notModel(self):
        return self._not_model

    @notModel.setter
    def notModel(self, value):
        self._not_model = bool(value)
        self.entity.events.append(("notModel", bool(value)))


class AvatarFilter(object):
    pass


class VehicleFilter(object):
    def __init__(self):
        self.bodyMatrix = Matrix()
        self.longitudinalSpeed = 0.0
        self.angularSpeed = 0.0
        self.triangles = []
        self.input = None
        self.physics = None
        self.placingCompensationMatrix = object()
        self.physicsInfo = object()
        self.movementInfo = object()
        self.owner = None

    def addTriangle(self, first, second, third):
        self.triangles.append((first, second, third))

    def setVehiclePhysics(self, physics):
        self.physics = physics
        self.contacts_at_attach = (
            physics.allowTracksContacts,
            physics.allowCarcassContacts,
        )
        if self.owner is not None:
            self.owner.events.append(("setVehiclePhysics", physics))

    def syncGunAngles(self, unused_yaw, unused_pitch):
        raise AssertionError("offline native bots must not call syncGunAngles")

    def notifyInputKeysDown(self, movement, rotation):
        self.input = (movement, rotation)
        if self.physics is not None:
            self.physics.drive_history.append((
                "keys", (movement, rotation)
            ))
        if movement:
            self.longitudinalSpeed = 8.0 * movement
            self.bodyMatrix.translation = Vector3(
                self.bodyMatrix.translation.x,
                self.bodyMatrix.translation.y,
                self.bodyMatrix.translation.z + movement,
            )
            if self.owner is not None:
                self.owner.matrix.translation = Vector3(
                    self.owner.matrix.translation.x,
                    self.owner.matrix.translation.y,
                    self.owner.matrix.translation.z + movement,
                )
        if rotation:
            self.angularSpeed = 0.2 * rotation
            self.bodyMatrix.yaw += 0.05 * rotation
            if self.owner is not None:
                self.owner.matrix.yaw += 0.05 * rotation


class VehiclePhysics(object):
    def __init__(self):
        self._static_mode = False
        self._movement_signals = 0
        self._is_frozen = False
        self.refuse_wake = False
        self.refuse_tracks_contacts = False
        self.refuse_carcass_contacts = False
        self._allow_tracks_contacts = False
        self._allow_carcass_contacts = False
        self.static_history = []
        self.drive_history = []

    @property
    def staticMode(self):
        return self._static_mode

    @staticMode.setter
    def staticMode(self, value):
        self._static_mode = bool(value)
        self.static_history.append(bool(value))

    @property
    def movementSignals(self):
        return self._movement_signals

    @movementSignals.setter
    def movementSignals(self, value):
        self._movement_signals = int(value)
        self.drive_history.append(("signals", int(value)))

    @property
    def isFrozen(self):
        return self._is_frozen

    @isFrozen.setter
    def isFrozen(self, value):
        value = bool(value)
        if not (self.refuse_wake and not value):
            self._is_frozen = value
        self.drive_history.append(("frozen", value))

    @property
    def allowTracksContacts(self):
        return self._allow_tracks_contacts

    @allowTracksContacts.setter
    def allowTracksContacts(self, value):
        if not self.refuse_tracks_contacts:
            self._allow_tracks_contacts = bool(value)

    @property
    def allowCarcassContacts(self):
        return self._allow_carcass_contacts

    @allowCarcassContacts.setter
    def allowCarcassContacts(self, value):
        if not self.refuse_carcass_contacts:
            self._allow_carcass_contacts = bool(value)

    def setArenaBounds(self, minimum, maximum):
        self.bounds = (minimum, maximum)


class Entity(object):
    def __init__(self, entity_id):
        self.id = entity_id
        self.events = []
        self._filter = AvatarFilter()
        self.wgPhysics = None
        self.matrix = EntityMatrix(self)

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, value):
        self._filter = value
        if isinstance(value, VehicleFilter):
            value.owner = self


class Model(object):
    def __init__(self, motor=None):
        self.motors = [] if motor is None else [motor]

    def addMotor(self, motor):
        self.motors.append(motor)

    def delMotor(self, motor):
        self.motors.remove(motor)


class NativeBotPhysicsTest(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        self.now = 100.0
        self.logs = []
        self.bridge_ok = True
        self.bridge_calls = []
        self.reject_post_attach_seed = False
        self.defer_post_attach_seed = False
        self.deferred_seed = None

        bigworld = types.ModuleType("BigWorld")
        bigworld.time = lambda: self.now
        bigworld.WGVehicleFilter2 = VehicleFilter
        bigworld.WGVehiclePhysics2 = VehiclePhysics
        bigworld.AvatarFilter = AvatarFilter
        bigworld.Servo = lambda provider: ("servo", provider)
        self.ground_supported = True
        self.ground_probe_calls = []

        def collide_segment(space_id, start, end, mask):
            self.ground_probe_calls.append((space_id, start, end, mask))
            if not self.ground_supported:
                return None
            return (Vector3(start.x, 3.0, start.z),)

        bigworld.wg_collideSegment = collide_segment
        self.bigworld = bigworld

        math_module = types.ModuleType("Math")
        math_module.Vector3 = Vector3
        math_module.Matrix = Matrix

        logging = types.ModuleType("gui.mods.offhangar.logging")
        logging.LOG_NOTE = lambda message: self.logs.append(("note", message))
        logging.LOG_ERROR = lambda message: self.logs.append(("error", message))

        constants = types.ModuleType("gui.mods.offhangar._constants")
        constants.CONFIG_OPTIONS = {
            "experimental_native_bot_physics": True,
            "network_mode": False,
        }

        network = types.ModuleType("gui.mods.offhangar.network_battle")
        network.network_is_authority = lambda player: bool(player.authority)

        bridge = types.ModuleType("gui.mods.offhangar.native_filter_bridge")

        def seed_filter(vehicle_filter, timestamp, space_id, position,
                        direction):
            self.bridge_calls.append((
                timestamp, space_id, tuple(position), tuple(direction)
            ))
            if not self.bridge_ok:
                return False
            if self.reject_post_attach_seed and vehicle_filter.physics is not None:
                raise AssertionError("post-attach seed is not allowed")
            if self.defer_post_attach_seed and vehicle_filter.physics is not None:
                self.deferred_seed = (vehicle_filter, position, direction)
                return True
            vehicle_filter.bodyMatrix.translation = Vector3(position)
            vehicle_filter.bodyMatrix.yaw = float(direction[2])
            vehicle_filter.bodyMatrix.pitch = float(direction[1])
            vehicle_filter.bodyMatrix.roll = float(direction[0])
            if vehicle_filter.owner is not None:
                vehicle_filter.owner.matrix.translation = Vector3(position)
                vehicle_filter.owner.matrix.yaw = float(direction[2])
                vehicle_filter.owner.matrix.pitch = float(direction[1])
                vehicle_filter.owner.matrix.roll = float(direction[0])
            return True

        bridge.seed_filter = seed_filter

        physics_shared = types.ModuleType("physics_shared")
        physics_shared.initVehiclePhysics = lambda physics, descriptor: None

        arena_type = types.ModuleType("ArenaType")
        arena_type.getVisibilityMask = lambda unused_gameplay: 3

        offline_entity = types.ModuleType("OfflineEntity")
        offline_entity.install_native_destructible_callback_adapter = (
            lambda: True
        )
        offline_entity.restore_native_destructible_callback_adapter = (
            lambda: True
        )

        gui = types.ModuleType("gui")
        mods = types.ModuleType("gui.mods")
        package = types.ModuleType("gui.mods.offhangar")
        gui.mods = mods
        mods.offhangar = package
        package.native_filter_bridge = bridge

        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        sys.modules["physics_shared"] = physics_shared
        sys.modules["ArenaType"] = arena_type
        sys.modules["OfflineEntity"] = offline_entity
        sys.modules["gui"] = gui
        sys.modules["gui.mods"] = mods
        sys.modules["gui.mods.offhangar"] = package
        sys.modules["gui.mods.offhangar.logging"] = logging
        sys.modules["gui.mods.offhangar._constants"] = constants
        sys.modules["gui.mods.offhangar.network_battle"] = network
        sys.modules["gui.mods.offhangar.native_filter_bridge"] = bridge

        spec = importlib.util.spec_from_file_location(
            "native_bot_physics_under_test", MODULE_PATH
        )
        self.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.native)

        self.player = types.SimpleNamespace(
            arenaTypeID=0,
            authority=True,
            _offhangar_network_client=None,
        )
        self.entity = Entity(901)
        self.old_servo = object()
        self.mock = types.SimpleNamespace(
            id=11,
            position=Vector3(12.0, 3.0, -8.0),
            yaw=0.7,
            pitch=0.0,
            roll=0.0,
            _veh_velocity=0.0,
            _veh_turn_velocity=0.0,
            bw_entity=self.entity,
            matrix=Matrix(),
            _chassis_model=Model(self.old_servo),
            _pose_servo=self.old_servo,
            _servo_added=True,
        )
        self.descriptor = types.SimpleNamespace(
            name="test:vehicle",
            chassis={"topRightCarryingPoint": (1.6, 0.0)},
            physics={
                "speedLimits": (20.0, 8.0),
                "minPlaneNormalY": 0.55,
                "carryingTriangles": (
                    ((-1.0, -2.0), (1.0, -2.0), (0.0, 2.0)),
                ),
                "enginePower": 500000.0,
            },
        )

    def make_mock(self, mock_id, entity_id, x=12.0):
        entity = Entity(entity_id)
        old_servo = object()
        mock = types.SimpleNamespace(
            id=mock_id,
            position=Vector3(x, 3.0, -8.0),
            yaw=0.7,
            pitch=0.0,
            roll=0.0,
            _veh_velocity=0.0,
            _veh_turn_velocity=0.0,
            bw_entity=entity,
            matrix=Matrix(),
            _chassis_model=Model(old_servo),
            _pose_servo=old_servo,
            _servo_added=True,
        )
        return mock, entity

    def apply_deferred_seed(self):
        vehicle_filter, position, direction = self.deferred_seed
        vehicle_filter.bodyMatrix.translation = Vector3(position)
        vehicle_filter.bodyMatrix.yaw = float(direction[2])
        vehicle_filter.owner.matrix.translation = Vector3(position)
        vehicle_filter.owner.matrix.yaw = float(direction[2])
        self.deferred_seed = None

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def activate(self, throttle=1, turn=1):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.now += self.native.WARMUP_SECONDS + 0.01
        return self.native.step(
            self.player, self.mock, self.descriptor,
            throttle, turn, 7, self.now
        )

    def test_authority_body_stages_then_returns_native_pose(self):
        result = self.activate()

        self.assertTrue(self.native.is_active(self.mock))
        self.assertFalse(self.entity.isStarted)
        self.assertEqual((1, 1), self.entity.filter.input)
        self.assertEqual(9, self.entity.wgPhysics.movementSignals)
        self.assertEqual(8.0, result["velocity"])
        self.assertEqual(0.2, result["turn_velocity"])
        self.assertAlmostEqual(-7.0, result["position"][2])
        self.assertEqual(1, len(self.bridge_calls))
        self.assertEqual(500.0, self.entity.wgPhysics.enginePower)
        self.assertTrue(any(
            "NATIVE_BOT_PHYSICS drive active" in message and
            "signals=9 frozen=False" in message
            for level, message in self.logs if level == "note"
        ))

    def test_missing_real_ground_support_falls_back_before_native_attach(self):
        self.ground_supported = False
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNone(result)
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("failed", state["phase"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertTrue(any(
            "native ground support is unavailable" in message
            for level, message in self.logs if level == "error"
        ))

    def test_drive_signs_map_to_retail_physics_signal_bits(self):
        self.assertIsNotNone(self.activate())

        self.native.step(
            self.player, self.mock, self.descriptor, -1, -1, 7,
            self.now + 0.10,
        )

        self.assertEqual((-1, -1), self.entity.filter.input)
        self.assertEqual(6, self.entity.wgPhysics.movementSignals)

        self.assertTrue(self.native.hold(self.mock))
        self.assertEqual((0, 0), self.entity.filter.input)
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)

    def test_nonzero_input_wakes_a_body_frozen_during_countdown(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        self.assertTrue(self.native.hold(self.mock))
        physics.isFrozen = True
        physics.drive_history = []

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 0.10,
        )

        self.assertIsNotNone(result)
        self.assertFalse(physics.isFrozen)
        self.assertEqual(9, physics.movementSignals)
        self.assertEqual((1, 1), self.entity.filter.input)
        self.assertEqual([
            ("signals", 9),
            ("frozen", False),
            ("keys", (1, 1)),
        ], physics.drive_history)

    def test_unchanged_drive_repairs_cross_frame_signal_overwrite(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        physics._movement_signals = 0
        physics._is_frozen = True
        physics.drive_history = []

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 0.05,
        )

        self.assertIsNotNone(result)
        self.assertEqual(9, physics.movementSignals)
        self.assertFalse(physics.isFrozen)
        self.assertEqual([
            ("signals", 9),
            ("frozen", False),
            ("keys", (1, 1)),
        ], physics.drive_history)

    def test_zero_input_does_not_disable_native_auto_freeze(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        physics.isFrozen = True
        physics.drive_history = []

        self.assertTrue(self.native.hold(self.mock))

        self.assertTrue(physics.isFrozen)
        self.assertEqual([
            ("signals", 0),
            ("keys", (0, 0)),
        ], physics.drive_history)

    def test_wake_readback_failure_faults_instead_of_claiming_drive(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        self.assertTrue(self.native.hold(self.mock))
        physics.isFrozen = True
        physics.refuse_wake = True

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            self.now + 0.10,
        )

        self.assertTrue(result["faulted"])
        self.assertEqual("faulted", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertEqual(0, physics.movementSignals)
        self.assertTrue(any(
            "native rigid body wake readback mismatch" in message
            for level, message in self.logs if level == "error"
        ))

    def test_active_filter_heartbeat_uses_post_physics_entity_pose(self):
        self.assertIsNotNone(self.activate())
        self.entity.matrix.translation = Vector3(14.0, 4.0, -6.0)
        self.entity.matrix.yaw = 0.8
        self.entity.matrix.pitch = 0.2
        self.entity.matrix.roll = -0.1

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + self.native.FILTER_HEARTBEAT_SECONDS + 0.01,
        )

        self.assertEqual((14.0, 4.0, -6.0), result["position"])
        self.assertEqual(2, len(self.bridge_calls))
        self.assertEqual((14.0, 4.0, -6.0), self.bridge_calls[-1][2])
        self.assertEqual((-0.1, 0.2, 0.8), self.bridge_calls[-1][3])
        self.assertTrue(any(
            "NATIVE_BOT_PHYSICS heartbeat active" in message
            for level, message in self.logs if level == "note"
        ))

    def test_heartbeat_pause_canary_stays_fresh_during_long_countdown(self):
        self.mock.id = self.native.HEARTBEAT_PAUSE_CANARY_ID
        self.assertIsNotNone(self.activate(0, 0))
        activated_at = self.now

        for sample in range(1, 62):
            self.native.step(
                self.player, self.mock, self.descriptor, 0, 0, 7,
                activated_at + sample * 0.11,
            )

        self.assertGreater(self.bridge_calls[-1][0] - activated_at, 6.0)
        self.assertTrue(any(
            "heartbeat_canary id=1000 heartbeat=on pause_window_ms=3000"
            in message
            for level, message in self.logs if level == "note"
        ))

    def test_heartbeat_pause_canary_resumes_after_bounded_drive_window(self):
        self.mock.id = self.native.HEARTBEAT_PAUSE_CANARY_ID
        self.assertIsNotNone(self.activate(0, 0))
        drive_at = self.now + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7, drive_at,
        )
        calls_before_pause = len(self.bridge_calls)

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            drive_at + 2.99,
        )
        self.assertEqual(calls_before_pause, len(self.bridge_calls))

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            drive_at + self.native.HEARTBEAT_CANARY_PAUSE_SECONDS + 0.01,
        )

        self.assertEqual(calls_before_pause + 1, len(self.bridge_calls))
        self.assertTrue(any(
            "heartbeat_canary id=1000 heartbeat=paused "
            "pause_elapsed_ms=0 pause_window_ms=3000" in message
            for level, message in self.logs if level == "note"
        ))

    def test_heartbeat_pause_does_not_block_safety_corrections(self):
        self.mock.id = self.native.HEARTBEAT_PAUSE_CANARY_ID
        self.defer_post_attach_seed = True
        self.assertIsNotNone(self.activate(0, 0))
        drive_at = self.now + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7, drive_at,
        )
        self.assertTrue(self.native.reseed(
            self.mock, (10.0, 3.0, -7.0), 0.7, 7,
            drive_at + 0.01, safety=True,
        ))
        calls_before_correction = len(self.bridge_calls)

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            drive_at + 0.11,
        )
        self.assertEqual(calls_before_correction + 1, len(self.bridge_calls))
        self.assertEqual((10.0, 3.0, -7.0), self.bridge_calls[-1][2])

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            drive_at + 0.22,
        )

        self.assertEqual(calls_before_correction + 2, len(self.bridge_calls))
        self.assertEqual((10.0, 3.0, -7.0), self.bridge_calls[-1][2])

    def test_heartbeat_on_canary_keeps_regular_samples(self):
        self.mock.id = self.native.HEARTBEAT_ON_CANARY_ID
        self.assertIsNotNone(self.activate())

        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + self.native.FILTER_HEARTBEAT_SECONDS + 0.01,
        )

        self.assertEqual(2, len(self.bridge_calls))
        self.assertTrue(any(
            "heartbeat_canary id=1001 heartbeat=on pause_window_ms=0"
            in message
            for level, message in self.logs if level == "note"
        ))

    def test_canary_logs_one_next_frame_drive_diagnostic_after_repair(self):
        self.mock.id = self.native.HEARTBEAT_PAUSE_CANARY_ID
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        vehicle_filter = self.entity.filter
        physics.normalEnginePower = 500.0
        physics.enginePowerMode = 2
        physics.isFrozenDuringFrame = False
        physics.gotTracksContact = True
        physics.allowTracksContacts = True
        physics.gotCarcassContact = True
        physics.allowCarcassContacts = True
        physics.groundType = 1
        physics.forceApplied = Vector3(1.0, 2.0, 3.0)
        physics.torqueApplied = Vector3(4.0, 5.0, 6.0)
        physics.speed = 0.0
        vehicle_filter.numLeftTrackContacts = 4
        vehicle_filter.numRightTrackContacts = 5
        physics._movement_signals = 0
        physics._is_frozen = True

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 0.05,
        )
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 0.06,
        )

        diagnostics = [
            message for level, message in self.logs
            if level == "note" and "drive_diagnostic" in message
        ]
        self.assertEqual(1, len(diagnostics))
        diagnostic = diagnostics[0]
        self.assertIn(
            "id=1000 heartbeat=paused pause_elapsed_ms=50 "
            "pause_window_ms=3000",
            diagnostic,
        )
        self.assertIn("signals_before=0 signals=9 repaired=True", diagnostic)
        self.assertIn("engine_power=500.0 normal_engine_power=500.0", diagnostic)
        self.assertIn(
            "engine_power_mode=2 frozen=False frozen_during_frame=False "
            "static_mode=False",
            diagnostic,
        )
        self.assertIn("tracks_contact=True allow_tracks=True", diagnostic)
        self.assertIn("carcass_contact=True allow_carcass=True", diagnostic)
        self.assertIn("left_contacts=4 right_contacts=5", diagnostic)
        self.assertIn("force=(1.000,2.000,3.000)", diagnostic)
        self.assertIn("torque=(4.000,5.000,6.000)", diagnostic)
        self.assertIn("speed=0.0 longitudinal_speed=8.0", diagnostic)

    def test_pending_correction_pauses_regular_filter_heartbeat(self):
        self.defer_post_attach_seed = True
        self.assertIsNotNone(self.activate())
        self.assertTrue(self.native.reseed(
            self.mock, (13.0, 3.0, -7.0), 0.7, 7,
            self.now + 0.01,
        ))
        self.assertEqual(1, len(self.bridge_calls))

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            self.now + self.native.FILTER_HEARTBEAT_SECONDS + 0.20,
        )
        correction_call_count = len(self.bridge_calls)
        self.assertEqual(2, correction_call_count)

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            self.now + self.native.FILTER_HEARTBEAT_SECONDS + 0.40,
        )

        self.assertEqual(correction_call_count, len(self.bridge_calls))

    def test_filter_heartbeat_stays_fresh_beyond_retail_stale_window(self):
        self.assertIsNotNone(self.activate())
        activated_at = self.now

        for sample in range(1, 62):
            self.native.step(
                self.player, self.mock, self.descriptor, 0, 0, 7,
                activated_at + sample * 0.11,
            )

        heartbeat_times = [call[0] for call in self.bridge_calls[1:]]
        self.assertGreater(heartbeat_times[-1] - activated_at, 6.0)
        self.assertTrue(all(
            later - earlier < 0.20
            for earlier, later in zip(heartbeat_times, heartbeat_times[1:])
        ))
        self.assertTrue(self.native.is_active(self.mock))

    def test_same_frame_inputs_coalesce_to_one_next_frame_safety_sample(self):
        self.defer_post_attach_seed = True
        self.assertIsNotNone(self.activate())
        frame_time = self.now + self.native.FILTER_HEARTBEAT_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, frame_time,
        )
        frame_call_count = len(self.bridge_calls)
        self.assertTrue(self.native.reseed(
            self.mock, (13.0, 3.0, -7.0), 0.7, 7, frame_time,
        ))
        self.assertTrue(self.native.reseed(
            self.mock, (12.0, 3.0, -7.0), 0.7, 7, frame_time,
            safety=True,
        ))

        self.assertEqual(frame_call_count, len(self.bridge_calls))
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertIsNone(state["pending_correction"])
        self.assertTrue(state["queued_correction"]["safety"])
        self.assertEqual(
            (12.0, 3.0, -7.0),
            state["queued_correction"]["position"],
        )
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7, frame_time,
        )
        self.assertEqual(frame_call_count, len(self.bridge_calls))
        self.assertIsNotNone(state["queued_correction"])

        next_frame = frame_time + 0.05
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7, next_frame,
        )

        self.assertEqual(frame_call_count + 1, len(self.bridge_calls))
        self.assertEqual((12.0, 3.0, -7.0), self.bridge_calls[-1][2])
        self.assertEqual((0.0, 0.0, 0.7), self.bridge_calls[-1][3])
        self.assertLess(self.bridge_calls[-2][0], self.bridge_calls[-1][0])
        self.assertIsNone(state["queued_correction"])
        self.assertTrue(state["pending_correction"]["safety"])
        self.assertEqual(next_frame, state["pending_correction"]["submitted_at"])
        self.assertEqual((0, 0), self.entity.filter.input)

    def test_submitted_contact_is_replaced_by_safety_on_next_real_tick(self):
        self.defer_post_attach_seed = True
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        contact_time = self.now + 0.20
        self.assertTrue(self.native.reseed(
            self.mock, (13.0, 3.0, -7.0), 0.7, 7,
            self.now + 0.01,
        ))
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            contact_time,
        )
        contact = state["pending_correction"]
        self.assertFalse(contact["safety"])
        call_count = len(self.bridge_calls)

        self.assertTrue(self.native.reseed(
            self.mock, (10.0, 3.0, -7.0), 0.7, 7,
            contact_time, safety=True,
        ))
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            contact_time,
        )
        self.assertEqual(call_count, len(self.bridge_calls))
        self.assertIs(contact, state["pending_correction"])
        self.assertTrue(state["queued_correction"]["safety"])
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)

        safety_time = contact_time + 0.05
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            safety_time,
        )
        self.assertEqual(call_count + 1, len(self.bridge_calls))
        self.assertIsNone(state["queued_correction"])
        self.assertTrue(state["pending_correction"]["safety"])
        self.assertEqual((10.0, 3.0, -7.0),
                         state["pending_correction"]["position"])
        self.assertEqual(safety_time,
                         state["pending_correction"]["submitted_at"])

    def test_retail_order_seeds_once_and_stays_dynamic_through_activation(self):
        self.reject_post_attach_seed = True

        self.assertIsNotNone(self.activate())

        event_names = [event[0] for event in self.entity.events]
        self.assertLess(
            event_names.index("notModel"),
            event_names.index("setVehiclePhysics"),
        )
        self.assertEqual([False], self.entity.wgPhysics.static_history)
        self.assertEqual((True, True), self.entity.filter.contacts_at_attach)
        self.assertEqual(1, len(self.bridge_calls))

    def test_track_contact_enable_readback_failure_falls_back_before_attach(self):
        physics = VehiclePhysics()
        physics.refuse_tracks_contacts = True
        self.bigworld.WGVehiclePhysics2 = lambda: physics

        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNone(result)
        self.assertEqual("failed", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual(1, len(self.mock._chassis_model.motors))
        self.assertIs(
            self.mock._pose_servo, self.mock._chassis_model.motors[0]
        )
        self.assertIsNone(getattr(
            self.mock, self.native.STATE_ATTR
        )["native_servo"])
        self.assertTrue(any(
            "native contact enable readback mismatch" in message
            for level, message in self.logs if level == "error"
        ))

    def test_carcass_contact_enable_readback_failure_falls_back_before_attach(self):
        physics = VehiclePhysics()
        physics.refuse_carcass_contacts = True
        self.bigworld.WGVehiclePhysics2 = lambda: physics

        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNone(result)
        self.assertEqual("failed", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual(1, len(self.mock._chassis_model.motors))
        self.assertIs(
            self.mock._pose_servo, self.mock._chassis_model.motors[0]
        )
        self.assertIsNone(getattr(
            self.mock, self.native.STATE_ATTR
        )["native_servo"])
        self.assertTrue(any(
            "native contact enable readback mismatch" in message
            for level, message in self.logs if level == "error"
        ))

    def test_body_matrix_is_diagnostic_not_the_root_pose_owner(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.entity.filter.bodyMatrix.translation = Vector3(400, 0, 400)
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNotNone(result)
        self.assertTrue(self.native.is_active(self.mock))
        self.assertEqual((12.0, 3.0, -8.0), result["position"])

    def test_activation_root_mismatch_logs_actionable_pose_deltas(self):
        installed = []
        self.mock._offh_install_collision_obstacle = (
            lambda: installed.append(True)
        )
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.entity.matrix.translation.x += 3.0
        self.now += self.native.WARMUP_SECONDS + 0.01

        self.assertIsNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))

        error = next(
            message for level, message in self.logs
            if level == "error" and "stage=warmup" in message
        )
        self.assertIn("vehicle=test:vehicle", error)
        self.assertIn("expected=(12.000,3.000,-8.000 yaw=0.7000)", error)
        self.assertIn("delta=(3.000,0.000,0.000)", error)
        self.assertIn("distance=3.000", error)
        self.assertIn("yaw_delta=0.0000", error)
        self.assertIn("body=(12.000,3.000,-8.000", error)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([True], installed)

    def test_activation_rejects_out_of_tolerance_yaw(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.entity.matrix.yaw += self.native.POSE_YAW_TOLERANCE + 0.01
        self.now += self.native.WARMUP_SECONDS + 0.01

        self.assertIsNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        self.assertTrue(any(
            "yaw_delta=0.3600" in message
            for level, message in self.logs if level == "error"
        ))

    def test_model_handoff_rejects_a_visible_prephysics_yaw_jump(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.entity.matrix.yaw += 0.10
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        self.assertIsNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))

        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertTrue(any(
            "stage=model_handoff" in message and "yaw_delta=0.1000" in message
            for level, message in self.logs if level == "error"
        ))

    def test_model_handoff_allows_subthreshold_filter_settle(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.entity.matrix.yaw += 0.04
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertTrue(result["staging"])
        self.assertIsNotNone(self.entity.wgPhysics)
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual([state["native_servo"]],
                         self.mock._chassis_model.motors)

    def test_activation_rejects_nonfinite_root_pose(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.entity.matrix.translation.x = float("nan")
        self.now += self.native.WARMUP_SECONDS + 0.01

        self.assertIsNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        self.assertTrue(any(
            "stage=warmup" in message and "actual=invalid" in message and
            "delta=invalid" in message
            for level, message in self.logs if level == "error"
        ))

    def test_async_correction_is_published_only_after_entity_readback(self):
        self.defer_post_attach_seed = True
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        old_pose = state["last_pose"]
        target = (13.0, 3.0, -7.0)

        self.assertTrue(self.native.reseed(
            self.mock, target, 0.7, 7, self.now + 0.01
        ))
        queued = state["queued_correction"]
        self.assertIsNone(state["pending_correction"])
        self.assertEqual(old_pose, state["last_pose"])
        self.assertTrue(self.native.reseed(
            self.mock, (14.0, 3.0, -7.0), 0.7, 7, self.now + 0.02
        ))
        self.assertIs(queued, state["queued_correction"])
        self.assertEqual(1, len(self.bridge_calls))

        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + 0.20
        ))
        first_submitted = state["pending_correction"]["submitted_at"]
        self.assertEqual(self.now + 0.20, first_submitted)
        self.assertEqual(2, len(self.bridge_calls))
        self.assertIsNotNone(state["pending_correction"])
        self.assertTrue(self.native.reseed(
            self.mock, (14.0, 3.0, -7.0), 0.7, 7, self.now + 0.21
        ))
        self.assertEqual(first_submitted,
                         state["pending_correction"]["submitted_at"])
        self.apply_deferred_seed()
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + 0.30
        )

        self.assertIsNone(state["pending_correction"])
        self.assertEqual(target, result["position"])

    def test_unacknowledged_correction_logs_without_claiming_or_freezing(self):
        self.defer_post_attach_seed = True
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertTrue(self.native.reseed(
            self.mock, (13.0, 3.0, -7.0), 0.7, 7, self.now + 0.01
        ))

        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + 0.20
        )

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + 1.00
        )

        self.assertNotIn("faulted", result)
        self.assertEqual("active", state["phase"])
        self.assertIsNone(state["pending_correction"])
        self.assertTrue(any(
            "correction_unconfirmed" in message and
            "stage=correction_ack" in message and "distance=1.000" in message
            for level, message in self.logs if level == "error"
        ))

    def test_safety_correction_replaces_contact_but_not_an_existing_safety_target(self):
        self.defer_post_attach_seed = True
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertTrue(self.native.reseed(
            self.mock, (13.0, 3.0, -7.0), 0.7, 7, self.now + 0.01
        ))

        self.assertTrue(self.native.reseed(
            self.mock, (10.0, 3.0, -7.0), 0.7, 7, self.now + 0.02,
            safety=True,
        ))
        queued_target = state["queued_correction"]
        self.assertTrue(queued_target["safety"])
        self.assertEqual((10.0, 3.0, -7.0), queued_target["position"])
        self.assertEqual(1, len(self.bridge_calls))
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)

        self.assertTrue(self.native.reseed(
            self.mock, (11.0, 3.0, -7.0), 0.7, 7, self.now + 0.03,
            safety=True,
        ))
        self.assertIs(queued_target, state["queued_correction"])
        self.assertEqual(1, len(self.bridge_calls))

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 0.20
        )
        safety_target = state["pending_correction"]
        submitted_at = safety_target["submitted_at"]
        self.assertTrue(safety_target["safety"])
        self.assertEqual((10.0, 3.0, -7.0), safety_target["position"])
        self.assertEqual(2, len(self.bridge_calls))
        self.assertEqual((0, 0), self.entity.filter.input)

        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 1.00
        )
        self.assertIs(safety_target, state["pending_correction"])
        self.assertTrue(safety_target["timeout_logged"])
        self.assertEqual(submitted_at, safety_target["submitted_at"])
        self.assertEqual((0, 0), self.entity.filter.input)
        self.assertTrue(self.native.reseed(
            self.mock, (50.0, 3.0, -7.0), 0.7, 7, self.now + 0.81,
            safety=True,
        ))
        self.assertIs(safety_target, state["pending_correction"])
        self.assertEqual((10.0, 3.0, -7.0), safety_target["position"])
        self.assertEqual(3, len(self.bridge_calls))
        self.assertEqual((12.0, 3.0, -7.0), (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        ))

    def test_unconfirmed_safety_target_stays_fresh_beyond_stale_window(self):
        self.defer_post_attach_seed = True
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        target = (10.0, 3.0, -7.0)
        self.assertTrue(self.native.reseed(
            self.mock, target, 0.7, 7, self.now + 0.01, safety=True,
        ))
        submitted_at = self.now + 0.11
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            submitted_at,
        )
        pending = state["pending_correction"]
        self.assertEqual(submitted_at, pending["submitted_at"])

        for sample in range(1, 62):
            self.native.step(
                self.player, self.mock, self.descriptor, 1, 1, 7,
                submitted_at + sample * 0.11,
            )

        safety_calls = self.bridge_calls[1:]
        self.assertGreater(safety_calls[-1][0] - submitted_at, 6.0)
        self.assertTrue(all(call[2] == target for call in safety_calls))
        self.assertTrue(all(call[3] == (0.0, 0.0, 0.7)
                            for call in safety_calls))
        self.assertTrue(all(
            0.0 < later[0] - earlier[0] < 0.20
            for earlier, later in zip(safety_calls, safety_calls[1:])
        ))
        self.assertIs(pending, state["pending_correction"])
        self.assertEqual(submitted_at, pending["submitted_at"])
        self.assertTrue(pending["timeout_logged"])
        self.assertEqual((0, 0), self.entity.filter.input)
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)

    def test_queued_filter_input_failure_freezes_and_clears_corrections(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertTrue(self.native.reseed(
            self.mock, (13.0, 3.0, -7.0), 0.7, 7,
            self.now + 0.01,
        ))
        self.bridge_ok = False

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            self.now + 0.20,
        )

        self.assertTrue(result["faulted"])
        self.assertEqual("faulted", state["phase"])
        self.assertIsNone(state["queued_correction"])
        self.assertIsNone(state["pending_correction"])
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)
        self.assertTrue(self.entity.wgPhysics.staticMode)

    def test_staged_body_never_enters_retail_vehicle_ui_lifecycle(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))

        self.assertFalse(self.entity.isStarted)
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.assertFalse(self.entity.isStarted)

    def test_replica_never_prepares_native_body(self):
        self.player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        self.player.authority = False

        self.assertFalse(self.native.enabled_for(self.player))
        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.assertIsInstance(self.entity.filter, AvatarFilter)

    def test_unready_lan_client_never_prepares_native_body(self):
        self.player._offhangar_network_client = types.SimpleNamespace(
            ready=False, phase="waiting"
        )

        self.assertFalse(self.native.enabled_for(self.player))
        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.assertIsInstance(self.entity.filter, AvatarFilter)

    def test_explicit_lan_fallback_is_treated_as_local_offline(self):
        self.player._offhangar_network_client = types.SimpleNamespace(
            ready=False, phase="waiting"
        )
        self.player._offhangar_network_fallback_local = True

        self.assertTrue(self.native.enabled_for(self.player))

    def test_authority_never_attaches_physics_to_remote_human_proxy(self):
        self.mock._network_remote = True
        self.mock._network_shared_bot = False

        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([], self.bridge_calls)

    def test_seed_failure_restores_avatar_filter(self):
        self.bridge_ok = False

        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.assertEqual(
            "failed", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertTrue(any(
            "NATIVE_BOT_PHYSICS FAIL" in message
            for kind, message in self.logs if kind == "error"
        ))

    def test_destructible_adapter_failure_falls_back_before_activation(self):
        sys.modules[
            "OfflineEntity"
        ].install_native_destructible_callback_adapter = lambda: False

        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        self.assertIsNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        self.assertEqual(
            "failed", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual(
            [("servo", self.mock.matrix)],
            self.mock._chassis_model.motors,
        )

    def test_startup_rollback_detach_failure_keeps_one_native_owner(self):
        class RefuseDetachModel(Model):
            refuse_detach = False

            def delMotor(inner_self, motor):
                if inner_self.refuse_detach:
                    raise RuntimeError("detach refused")
                super(RefuseDetachModel, inner_self).delMotor(motor)

        model = RefuseDetachModel(self.old_servo)
        self.mock._chassis_model = model
        collision_installs = []
        self.mock._offh_install_collision_obstacle = (
            lambda: collision_installs.append(True)
        )

        def reject_adapter_after_model_handoff():
            model.refuse_detach = True
            return False

        sys.modules[
            "OfflineEntity"
        ].install_native_destructible_callback_adapter = (
            reject_adapter_after_model_handoff
        )
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        native_filter = self.entity.filter
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertTrue(result["faulted"])
        self.assertEqual("faulted", state["phase"])
        self.assertIs(self.entity.filter, native_filter)
        self.assertEqual([state["native_servo"]], model.motors)
        self.assertIsNone(self.mock._pose_servo)
        self.assertEqual([], collision_installs)
        self.assertTrue(any(
            "Python Servo restore failed" in message
            for level, message in self.logs if level == "error"
        ))

        model.refuse_detach = False
        self.assertTrue(self.native.stop_mock(self.mock))
        self.assertEqual([], model.motors)
        self.assertIsNone(state["native_servo"])

    def test_model_provider_switches_before_dynamic_physics_and_only_once(self):
        events = self.entity.events

        class OrderedModel(Model):
            def addMotor(inner_self, motor):
                events.append(("addMotor", motor))
                super(OrderedModel, inner_self).addMotor(motor)

            def delMotor(inner_self, motor):
                events.append(("delMotor", motor))
                super(OrderedModel, inner_self).delMotor(motor)

        self.mock._chassis_model = OrderedModel(self.old_servo)
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])

        names = [event[0] for event in events]
        self.assertLess(names.index("addMotor"),
                        names.index("setVehiclePhysics"))
        self.assertLess(names.index("delMotor"),
                        names.index("setVehiclePhysics"))
        motors_after_attach = list(self.mock._chassis_model.motors)
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        self.assertEqual(motors_after_attach,
                         self.mock._chassis_model.motors)

    def test_implausible_active_jump_freezes_without_second_pose_owner(self):
        result = self.activate()
        self.assertIsNotNone(result)
        native_filter = self.entity.filter
        self.entity.matrix.translation = Vector3(500, 0, 500)

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now + 0.1
        )

        self.assertIsNotNone(result)
        self.assertIs(self.entity.filter, native_filter)
        self.assertEqual(0.0, result["velocity"])
        self.assertEqual(
            "faulted", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertTrue(self.entity.wgPhysics.staticMode)
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)
        self.assertFalse(getattr(
            self.mock, self.native.STATE_ATTR
        )["freeze_reseed"])
        self.assertEqual((500.0, 0.0, 500.0), (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        ))

    def test_long_frame_allows_physically_plausible_native_displacement(self):
        self.assertIsNotNone(self.activate())
        self.entity.matrix.translation.z += 30.0

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + 1.0
        )

        self.assertIsNotNone(result)
        self.assertTrue(self.native.is_active(self.mock))
        self.assertNotIn("faulted", result)

    def test_cleanup_detaches_native_physics(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        fashion = object()
        self.mock._fashion = fashion
        self.mock._chassis_model.wg_fashion = fashion

        self.assertTrue(self.native.stop_mock(self.mock))

        self.assertIsNone(self.entity.wgPhysics)
        self.assertFalse(self.entity.isStarted)
        self.assertIsNone(self.entity.typeDescriptor)
        self.assertEqual("stopped", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertIsNone(physics.damageDestructibleCb)
        self.assertEqual(0, physics.movementSignals)
        self.assertEqual([], self.mock._chassis_model.motors)
        self.assertFalse(hasattr(self.mock._chassis_model, "wg_fashion"))
        self.assertIsNone(self.mock._fashion)
        self.assertIsInstance(self.entity.filter, AvatarFilter)

    def test_cleanup_logs_and_retains_servo_for_a_later_detach_retry(self):
        class RetryModel(Model):
            fail_detach = False

            def delMotor(self, motor):
                if self.fail_detach:
                    raise RuntimeError("detach refused")
                super().delMotor(motor)

        model = RetryModel(self.old_servo)
        self.mock._chassis_model = model
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_servo = state["native_servo"]
        model.fail_detach = True

        self.assertTrue(self.native.stop_mock(self.mock))

        self.assertIs(native_servo, state["native_servo"])
        self.assertIs(native_servo, self.mock._native_pose_servo)
        self.assertTrue(any(
            "servo detach failed" in message and "detach refused" in message
            for level, message in self.logs if level == "error"
        ))

        model.fail_detach = False
        self.assertTrue(self.native.stop_mock(self.mock))
        self.assertIsNone(state["native_servo"])
        self.assertEqual([], model.motors)

    def test_reuse_cannot_discard_a_servo_that_failed_to_detach(self):
        class RetryModel(Model):
            fail_detach = False

            def delMotor(self, motor):
                if self.fail_detach:
                    raise RuntimeError("detach refused")
                super().delMotor(motor)

        model = RetryModel(self.old_servo)
        self.mock._chassis_model = model
        self.assertIsNotNone(self.activate())
        old_state = getattr(self.mock, self.native.STATE_ATTR)
        native_servo = old_state["native_servo"]
        model.fail_detach = True
        self.assertTrue(self.native.stop_mock(self.mock))

        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now + 1.0
        ))
        self.assertIs(old_state, getattr(self.mock, self.native.STATE_ATTR))
        self.assertIs(native_servo, old_state["native_servo"])
        self.assertEqual([native_servo], model.motors)
        self.assertEqual(1, len([
            message for level, message in self.logs
            if level == "error" and "reuse blocked" in message
        ]))

        model.fail_detach = False
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now + 2.0
        ))
        self.assertIsNot(old_state, getattr(self.mock, self.native.STATE_ATTR))
        self.assertEqual([], model.motors)

    def test_stopped_mock_builds_a_fresh_native_state(self):
        self.assertIsNotNone(self.activate())
        old_state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertTrue(self.native.stop_mock(self.mock))

        self.now += 1.0
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))

        new_state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertIsNot(old_state, new_state)
        self.assertEqual("seed_wait", new_state["phase"])

    def test_stop_all_then_next_battle_restarts_drive_and_heartbeat(self):
        self.assertIsNotNone(self.activate())
        first_physics = self.entity.wgPhysics

        self.assertEqual(1, self.native.stop_all({self.mock.id: self.mock}))
        self.assertEqual(0, first_physics.movementSignals)

        self.now += 1.0
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        second_physics = self.entity.wgPhysics
        second_physics.isFrozen = True
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, -1, -1, 7, self.now
        ))

        self.assertIsNot(first_physics, second_physics)
        self.assertEqual(6, second_physics.movementSignals)
        self.assertFalse(second_physics.isFrozen)
        calls_before_heartbeat = len(self.bridge_calls)
        self.now += self.native.FILTER_HEARTBEAT_SECONDS + 0.01
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        self.assertEqual(calls_before_heartbeat + 1, len(self.bridge_calls))

    def test_preactive_stop_completes_startup_with_an_explicit_stopped_count(self):
        self.player._offhangar_network_bot_manifest = [object()]
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))

        self.assertTrue(self.native.stop_mock(self.mock))

        summaries = [
            message for level, message in self.logs
            if level == "note" and "startup_complete" in message
        ]
        self.assertEqual(1, len(summaries))
        self.assertIn(
            "expected=1 attempted=1 prepared=1 active=0 failed=0 stopped=1",
            summaries[0],
        )

    def test_only_one_native_body_attaches_for_the_same_frame_time(self):
        other_entity = Entity(902)
        other_servo = object()
        other = types.SimpleNamespace(
            id=12,
            position=Vector3(20.0, 3.0, -8.0),
            yaw=0.7,
            pitch=0.0,
            roll=0.0,
            _veh_velocity=0.0,
            _veh_turn_velocity=0.0,
            bw_entity=other_entity,
            _chassis_model=Model(other_servo),
            _pose_servo=other_servo,
            _servo_added=True,
        )
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.assertTrue(self.native.prepare(
            self.player, other, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        first = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        second = self.native.step(
            self.player, other, self.descriptor, 0, 0, 7, self.now
        )

        self.assertTrue(first["staging"])
        self.assertTrue(second["staging"])
        self.assertIsNotNone(self.entity.wgPhysics)
        self.assertFalse(self.entity.wgPhysics.staticMode)
        self.assertIsNone(other_entity.wgPhysics)

        self.now += 0.01
        self.native.step(
            self.player, other, self.descriptor, 0, 0, 7, self.now
        )
        self.assertIsNotNone(other_entity.wgPhysics)

    def test_full_staggered_lineup_reaches_one_terminal_startup_summary(self):
        mocks = []
        entities = []
        self.player._offhangar_network_bot_manifest = [object()] * 29
        for index in range(29):
            mock, entity = self.make_mock(
                1000 + index, 2000 + index, 20.0 + index
            )
            mocks.append(mock)
            entities.append(entity)
            self.assertTrue(self.native.prepare(
                self.player, mock, self.descriptor, 7, self.now
            ))

        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        for index, mock in enumerate(mocks):
            self.assertTrue(self.native.step(
                self.player, mock, self.descriptor, 0, 0, 7,
                attach_at + index * 0.001
            )["staging"])

        activate_at = attach_at + 0.028 + self.native.WARMUP_SECONDS + 0.01
        for mock in mocks:
            self.assertIsNotNone(self.native.step(
                self.player, mock, self.descriptor, 0, 0, 7, activate_at
            ))

        self.assertTrue(all(self.native.is_active(mock) for mock in mocks))
        self.assertEqual(29, len(self.bridge_calls))
        self.assertTrue(all(
            entity.wgPhysics.static_history == [False]
            for entity in entities
        ))
        summaries = [
            message for level, message in self.logs
            if level == "note" and "startup_complete" in message
        ]
        self.assertEqual(1, len(summaries))
        self.assertIn(
            "expected=29 attempted=29 prepared=29 active=29 failed=0 stopped=0",
            summaries[0],
        )

    def test_runtime_fault_before_lineup_completion_is_not_reported_green(self):
        self.player._offhangar_network_bot_manifest = [object(), object()]
        other, other_entity = self.make_mock(12, 902, 20.0)
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.assertTrue(self.native.prepare(
            self.player, other, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )
        self.native.step(
            self.player, other, self.descriptor, 0, 0, 7, attach_at + 0.01
        )
        first_active_at = attach_at + self.native.WARMUP_SECONDS + 0.02
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            first_active_at,
        ))
        self.entity.matrix.translation = Vector3(500.0, 0.0, 500.0)
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            first_active_at + 0.10,
        )["faulted"])
        self.assertIsNotNone(self.native.step(
            self.player, other, self.descriptor, 0, 0, 7,
            first_active_at + 0.20,
        ))
        self.assertTrue(self.native.is_active(other))
        self.assertIs(other_entity.wgPhysics,
                      getattr(other, self.native.STATE_ATTR)["physics"])

        summaries = [
            message for level, message in self.logs
            if level == "note" and "startup_complete" in message
        ]
        self.assertEqual(1, len(summaries))
        self.assertIn(
            "expected=2 attempted=2 prepared=2 active=1 failed=1 stopped=0",
            summaries[0],
        )

    def test_first_active_sample_failure_cannot_emit_a_green_summary(self):
        self.player._offhangar_network_bot_manifest = [object()]
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        def reject_input(unused_movement, unused_rotation):
            raise RuntimeError("input readback failed")

        self.entity.filter.notifyInputKeysDown = reject_input
        self.now += self.native.WARMUP_SECONDS + 0.01
        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7, self.now
        )

        self.assertTrue(result["faulted"])
        self.assertFalse(any(
            level == "note" and
            "NATIVE_BOT_PHYSICS active=1 prepared=1 failed=0" in message
            for level, message in self.logs
        ))
        summaries = [
            message for level, message in self.logs
            if level == "note" and "startup_complete" in message
        ]
        self.assertEqual(1, len(summaries))
        self.assertIn(
            "expected=1 attempted=1 prepared=1 active=0 failed=1 stopped=0",
            summaries[0],
        )

    def test_active_body_rebinds_model_and_fashion_to_native_providers(self):
        old_servo = object()
        self.mock._chassis_model = Model(old_servo)
        self.mock._pose_servo = old_servo
        self.mock._servo_added = True

        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_servo = ("servo", self.entity.matrix)
        self.assertEqual([native_servo], self.mock._chassis_model.motors)
        self.assertEqual(native_servo, state["native_servo"])
        self.assertTrue(self.entity.matrix.notModel)

        fashion = types.SimpleNamespace()
        self.assertTrue(self.native.bind_fashion(self.mock, fashion))
        self.assertIs(fashion.placingCompensationMatrix,
                      self.entity.filter.placingCompensationMatrix)
        self.assertIs(fashion.physicsInfo, self.entity.filter.physicsInfo)
        self.assertIs(fashion.movementInfo, self.entity.filter.movementInfo)

    def test_staged_fashion_binds_native_providers_only_after_activation(self):
        movement = object()
        physics = object()
        placing = object()
        fashion = types.SimpleNamespace(
            movementInfo=movement,
            physicsInfo=physics,
            placingCompensationMatrix=placing,
        )
        self.mock._fashion = fashion
        self.mock._chassis_model.wg_fashion = fashion
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertTrue(self.native.bind_fashion(self.mock, fashion))
        self.assertIs(movement, fashion.movementInfo)
        self.assertIs(physics, fashion.physicsInfo)
        self.assertIs(placing, fashion.placingCompensationMatrix)
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))

        self.assertIs(fashion.placingCompensationMatrix,
                      self.entity.filter.placingCompensationMatrix)
        self.assertIs(fashion.physicsInfo, self.entity.filter.physicsInfo)
        self.assertIs(fashion.movementInfo, self.entity.filter.movementInfo)
        self.assertIsNone(getattr(
            self.mock, self.native.STATE_ATTR
        )["pending_fashion"])

    def test_staged_fashion_does_not_retain_native_body_after_fallback(self):
        movement = object()
        physics = object()
        placing = object()
        fashion = types.SimpleNamespace(
            movementInfo=movement,
            physicsInfo=physics,
            placingCompensationMatrix=placing,
        )
        chassis = self.mock._chassis_model
        self.mock._fashion = fashion
        chassis.wg_fashion = fashion
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertTrue(self.native.bind_fashion(self.mock, fashion))
        self.entity.matrix.translation.x += 3.0
        self.now += self.native.WARMUP_SECONDS + 0.01

        self.assertIsNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))

        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsNone(state["filter"])
        self.assertIsNone(state["physics"])
        self.assertIsNone(state["pending_fashion"])
        self.assertIs(chassis.wg_fashion, fashion)
        self.assertIs(movement, fashion.movementInfo)
        self.assertIs(physics, fashion.physicsInfo)
        self.assertIs(placing, fashion.placingCompensationMatrix)

    def test_native_fashion_provider_failure_is_logged_once(self):
        self.assertIsNotNone(self.activate())

        class BrokenFashion(object):
            def __setattr__(self, name, value):
                raise RuntimeError("provider rejected")

        self.assertFalse(self.native.bind_fashion(
            self.mock, BrokenFashion()
        ))
        self.assertFalse(self.native.bind_fashion(
            self.mock, BrokenFashion()
        ))
        errors = [
            message for kind, message in self.logs
            if kind == "error" and "fashion bind failed" in message
        ]
        self.assertEqual(1, len(errors))

    def test_provider_failure_falls_back_before_native_activation(self):
        installed = []
        self.mock._chassis_model = None
        self.mock.model = None
        self.mock._offh_install_collision_obstacle = (
            lambda: installed.append(True)
        )

        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertIsNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        self.assertEqual(
            "failed", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([True], installed)

    def test_loader_injects_manager_before_offline_battle(self):
        source = LOADER_PATH.read_text(encoding="utf-8")
        manager = source.index("'native_bot_physics'")
        battle = source.index("'offline_battle'", manager)
        self.assertLess(manager, battle)

    def test_live_pymodel_obstacle_is_only_installed_for_fallback(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        spawn = source.index("def _install_live_collision_obstacle")
        native_prepare = source.index("_native_body_prepared =", spawn)
        fallback = source.index("if not _native_body_prepared:", native_prepare)
        obstacle_call = source.index(
            "_install_live_collision_obstacle()", fallback
        )
        native_branch = source.index("else:", obstacle_call)
        clear_proxy = source.index(
            "_e_mock._collision_obstacle = None", native_branch
        )

        self.assertLess(fallback, obstacle_call)
        self.assertLess(obstacle_call, native_branch)
        self.assertLess(native_branch, clear_proxy)

    def test_central_kill_path_stops_native_body_before_wreck_swap(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        wrapper = source.index("class _KillEventWrapper(object):")
        dead = source.index("_offh_set_alive(_mv, False)", wrapper)
        stop = source.index("_stop_dead_native_bot(_mv, False)", dead)
        wreck = source.index("# Fire deaths (reason 2)", stop)

        self.assertLess(dead, stop)
        self.assertLess(stop, wreck)

    def test_perf_report_includes_native_physics_cost(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        ordered = source.index("ordered = (")
        report = source.index("parts = []", ordered)

        self.assertIn("'native_physics'", source[ordered:report])

    def test_client_only_native_path_avoids_server_connection_clock(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("state['filter'].syncGunAngles(", source)


if __name__ == "__main__":
    unittest.main()
