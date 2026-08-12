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
        self.placingCompensationMatrix = Matrix()
        self.physicsInfo = object()
        self.movementInfo = object()
        self.owner = None

    def addTriangle(self, first, second, third):
        self.triangles.append((first, second, third))

    def setVehiclePhysics(self, physics):
        self.physics = physics
        physics.vehicle_filter = self
        physics.body_position = Vector3(
            self.bodyMatrix.translation.x,
            self.bodyMatrix.translation.y,
            self.bodyMatrix.translation.z,
        )
        physics.body_yaw = float(self.bodyMatrix.yaw)
        physics.body_pitch = float(self.bodyMatrix.pitch)
        physics.body_roll = float(self.bodyMatrix.roll)
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

    def publishPhysics(self):
        if self.physics is None:
            return
        physics = self.physics
        self.bodyMatrix.translation = Vector3(
            physics.body_position.x,
            physics.body_position.y,
            physics.body_position.z,
        )
        self.bodyMatrix.yaw = float(physics.body_yaw)
        self.bodyMatrix.pitch = float(physics.body_pitch)
        self.bodyMatrix.roll = float(physics.body_roll)
        self.longitudinalSpeed = float(physics.speed)
        self.angularSpeed = float(physics.rspeed)
        if self.owner is not None:
            self.owner.matrix.translation = Vector3(
                physics.body_position.x,
                physics.body_position.y,
                physics.body_position.z,
            )
            self.owner.matrix.yaw = float(physics.body_yaw)
            self.owner.matrix.pitch = float(physics.body_pitch)
            self.owner.matrix.roll = float(physics.body_roll)


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
        self.gotTracksContact = False
        self.gotCarcassContact = False
        self.isFrozenDuringFrame = False
        self._speed = 0.0
        self._rspeed = 0.0
        self.refuse_speed_read = False
        self.body_position = Vector3(0.0, 0.0, 0.0)
        self.body_yaw = 0.0
        self.body_pitch = 0.0
        self.body_roll = 0.0

    @property
    def speed(self):
        if self.refuse_speed_read:
            raise RuntimeError("physics speed unavailable")
        return self._speed

    @speed.setter
    def speed(self, value):
        self._speed = float(value)

    @property
    def rspeed(self):
        if self.refuse_speed_read:
            raise RuntimeError("physics rspeed unavailable")
        return self._rspeed

    @rspeed.setter
    def rspeed(self, value):
        self._rspeed = float(value)

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


class DynamicsSimulator(object):
    instances = []
    produce_contacts = True
    corrupt_first_solve_roll = None

    def __init__(self):
        self.numSubsteps = 1
        self.numIterations = 10
        self.frictionRatio = 1.0
        self.restitution = 0.5
        self.allowedPenetration = 0.01
        self.midSolvingIterations = 4
        self.calls = []
        self.fail_update = False
        self.mutate_before_failure = False
        self._updated = False
        DynamicsSimulator.instances.append(self)

    def update(self, dt, vehicles, bodies):
        self.calls.append((float(dt), tuple(vehicles), tuple(bodies)))
        if self.fail_update:
            if self.mutate_before_failure:
                for physics in vehicles:
                    physics.vehicle_filter.owner.matrix.translation.x += 100.0
            raise RuntimeError("simulator update failed")
        for physics in vehicles:
            physics.gotTracksContact = bool(self.produce_contacts)
            physics.gotCarcassContact = bool(self.produce_contacts)
            physics.isFrozenDuringFrame = bool(physics.isFrozen)
            if physics.staticMode or physics.isFrozen:
                physics.speed = 0.0
                physics.rspeed = 0.0
                continue
            signals = int(physics.movementSignals)
            movement = int(bool(signals & 1)) - int(bool(signals & 2))
            rotation = int(bool(signals & 8)) - int(bool(signals & 4))
            physics.speed = 8.0 * movement
            physics.rspeed = 0.2 * rotation
            physics.body_yaw += physics.rspeed * float(dt)
            physics.body_position = Vector3(
                physics.body_position.x +
                math.sin(physics.body_yaw) * physics.speed * float(dt),
                physics.body_position.y,
                physics.body_position.z +
                math.cos(physics.body_yaw) * physics.speed * float(dt),
            )
            if (not self._updated and
                    self.corrupt_first_solve_roll is not None):
                physics.body_roll = float(self.corrupt_first_solve_roll)
        self._updated = True


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
        DynamicsSimulator.instances = []
        DynamicsSimulator.produce_contacts = True
        DynamicsSimulator.corrupt_first_solve_roll = None

        bigworld = types.ModuleType("BigWorld")
        bigworld.time = lambda: self.now
        bigworld.WGVehicleFilter2 = VehicleFilter
        bigworld.WGVehiclePhysics2 = VehiclePhysics
        bigworld.WGDynamicsSimulator = DynamicsSimulator
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
            if vehicle_filter.physics is not None:
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
        physics_shared.IS_CLIENT = True
        physics_shared.ROLLER_MODE = False

        def init_vehicle_physics(physics, descriptor):
            physics.enginePower = (
                float(descriptor.physics["enginePower"]) * 0.00125
            )
            physics.normalEnginePower = physics.enginePower

        physics_shared.initVehiclePhysics = init_vehicle_physics
        physics_shared.NUM_SUBSTEPS = 2
        physics_shared.NUM_ITERATIONS = 10
        physics_shared.FRICTION_RATIO = 1.0
        physics_shared.RESTITUTION = 0.5
        physics_shared.ALLOWED_PENETRATION = 0.01
        physics_shared.MID_SOLVING_ITERATIONS = 4
        self.physics_shared = physics_shared

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

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def simulate(self, mocks=None, dt=0.02, timestamp=None):
        if mocks is None:
            mocks = {self.mock.id: self.mock}
        if timestamp is None:
            self.now += dt
            timestamp = self.now
        return self.native.simulate_frame(mocks, dt, timestamp)

    def publish_filters(self, mocks=None):
        if mocks is None:
            mocks = {self.mock.id: self.mock}
        for mock in mocks.values():
            vehicle_filter = getattr(
                getattr(mock, "bw_entity", None), "filter", None
            )
            if isinstance(vehicle_filter, VehicleFilter):
                vehicle_filter.publishPhysics()

    def activate(self, throttle=1, turn=1):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())
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
        self.assertEqual(0.0, result["velocity"])
        self.assertEqual(0.0, result["turn_velocity"])
        self.assertAlmostEqual(-8.0, result["position"][2])
        self.assertEqual(1, len(self.bridge_calls))
        self.assertEqual(625.0, self.entity.wgPhysics.enginePower)
        self.assertTrue(any(
            "NATIVE_BOT_PHYSICS drive active" in message and
            "signals=9 frozen=False" in message
            for level, message in self.logs if level == "note"
        ))

    def test_batch_caches_pose_and_physics_speeds_before_solver_update(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        state = getattr(self.mock, self.native.STATE_ATTR)
        before = (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        )

        self.assertEqual(1, self.simulate(dt=0.10))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7, self.now,
        )
        self.assertEqual(before, result["position"])
        self.assertEqual(0.0, result["velocity"])
        self.assertEqual(0.0, result["turn_velocity"])
        self.assertEqual(8.0, physics.speed)
        self.assertEqual(0.2, physics.rspeed)
        self.assertEqual(0.0, self.entity.filter.longitudinalSpeed)
        self.assertEqual(0.0, self.entity.filter.angularSpeed)
        self.assertEqual(0.0, state["frame_speed"])
        self.assertEqual(0.0, state["frame_turn_speed"])

    def test_next_filter_tick_publishes_previous_solver_pose_and_speeds(self):
        self.assertIsNotNone(self.activate())
        self.assertEqual(1, self.simulate(dt=0.10))
        solved_position = (
            self.entity.wgPhysics.body_position.x,
            self.entity.wgPhysics.body_position.y,
            self.entity.wgPhysics.body_position.z,
        )

        self.publish_filters()
        self.assertEqual(1, self.simulate(dt=0.10))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7, self.now,
        )
        self.assertEqual(solved_position, result["position"])
        self.assertEqual(8.0, result["velocity"])
        self.assertEqual(0.2, result["turn_velocity"])

    def test_batch_initialization_uses_server_hull_spring_contract(self):
        calls = []

        def init_vehicle_physics(physics, descriptor):
            full_mass = 48.0
            suspension_mass = 8.0
            clearance = 0.5
            compression = 0.25
            tracks_penetration = 0.1
            if self.physics_shared.IS_CLIENT:
                hull_mass = full_mass
                spring_length = clearance / compression
            else:
                hull_mass = full_mass - suspension_mass
                spring_length = (
                    clearance + tracks_penetration
                ) / compression
            physics.hullMass = hull_mass
            physics.springLength = spring_length
            physics.rollerMode = self.physics_shared.ROLLER_MODE
            physics.enginePower = (
                float(descriptor.physics["enginePower"]) * 0.00125
            )
            physics.normalEnginePower = physics.enginePower
            calls.append((
                self.physics_shared.IS_CLIENT,
                self.physics_shared.ROLLER_MODE,
                hull_mass,
                spring_length,
            ))

        self.physics_shared.initVehiclePhysics = init_vehicle_physics

        result = self.activate()

        self.assertIsNotNone(result)
        self.assertEqual(1, len(calls))
        self.assertEqual((False, True, 40.0), calls[0][:3])
        self.assertAlmostEqual(2.4, calls[0][3])
        self.assertIs(True, self.entity.wgPhysics.rollerMode)
        self.assertEqual(40.0, self.entity.wgPhysics.hullMass)
        self.assertAlmostEqual(2.4, self.entity.wgPhysics.springLength)
        self.assertEqual(625.0, self.entity.wgPhysics.enginePower)
        self.assertEqual(
            625.0,
            getattr(self.mock, self.native.STATE_ATTR)["base_engine_power"],
        )
        self.assertIs(True, self.physics_shared.IS_CLIENT)
        self.assertIs(False, self.physics_shared.ROLLER_MODE)

    def test_batch_initialization_restores_globals_after_exception(self):
        calls = []

        def fail_init(unused_physics, unused_descriptor):
            calls.append((
                self.physics_shared.IS_CLIENT,
                self.physics_shared.ROLLER_MODE,
            ))
            raise RuntimeError("server physics init failed")

        self.physics_shared.initVehiclePhysics = fail_init
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNone(result)
        self.assertEqual([(False, True)], calls)
        self.assertIs(True, self.physics_shared.IS_CLIENT)
        self.assertIs(False, self.physics_shared.ROLLER_MODE)
        self.assertEqual(
            "failed", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertTrue(any(
            "server physics init failed" in message
            for level, message in self.logs if level == "error"
        ))

    def test_shared_dynamics_simulator_batches_bodies_once_in_id_order(self):
        other, other_entity = self.make_mock(10, 902, 20.0)
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

        mocks = {self.mock.id: self.mock, other.id: other}
        solve_at = attach_at + 0.02
        self.assertEqual(2, self.native.simulate_frame(
            mocks, 0.02, solve_at
        ))
        self.assertEqual(0, self.native.simulate_frame(
            mocks, 0.02, solve_at
        ))

        self.assertEqual(1, len(DynamicsSimulator.instances))
        simulator = DynamicsSimulator.instances[0]
        self.assertEqual(2, simulator.numSubsteps)
        self.assertEqual(10, simulator.numIterations)
        self.assertEqual(1.0, simulator.frictionRatio)
        self.assertEqual(0.5, simulator.restitution)
        self.assertEqual(0.01, simulator.allowedPenetration)
        self.assertEqual(4, simulator.midSolvingIterations)
        self.assertEqual(1, len(simulator.calls))
        self.assertEqual((
            other_entity.wgPhysics, self.entity.wgPhysics,
        ), simulator.calls[0][1])
        self.assertEqual((), simulator.calls[0][2])

    def test_bad_speed_getter_excludes_only_that_body_from_batch(self):
        other, other_entity = self.make_mock(10, 902, 20.0)
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
        self.entity.wgPhysics.refuse_speed_read = True

        result = self.native.simulate_frame(
            {self.mock.id: self.mock, other.id: other},
            0.02, attach_at + 0.02,
        )

        self.assertEqual(1, result)
        self.assertEqual("failed", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertEqual("warmup", getattr(
            other, self.native.STATE_ATTR
        )["phase"])
        self.assertEqual(1, len(DynamicsSimulator.instances))
        self.assertEqual(
            (other_entity.wgPhysics,),
            DynamicsSimulator.instances[0].calls[0][1],
        )
        self.assertFalse(self.native._SIMULATION_FAILED[0])

    def test_invalid_pose_excludes_only_that_body_from_batch(self):
        other, other_entity = self.make_mock(10, 902, 20.0)
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
        self.entity.matrix.translation.x = float("nan")

        result = self.native.simulate_frame(
            {self.mock.id: self.mock, other.id: other},
            0.02, attach_at + 0.02,
        )

        self.assertEqual(1, result)
        self.assertEqual("failed", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertEqual("warmup", getattr(
            other, self.native.STATE_ATTR
        )["phase"])
        self.assertEqual(
            (other_entity.wgPhysics,),
            DynamicsSimulator.instances[0].calls[0][1],
        )
        self.assertFalse(self.native._SIMULATION_FAILED[0])

    def test_warmup_without_batch_simulation_falls_back(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNone(result)
        self.assertEqual("failed", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertTrue(any(
            "dynamics simulator did not advance" in message
            for level, message in self.logs if level == "error"
        ))

    def test_warmup_does_not_require_unreliable_contact_output_flags(self):
        DynamicsSimulator.produce_contacts = False
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNotNone(result)
        self.assertEqual("active", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])

    def test_warmup_rejects_a_finite_pose_buried_below_real_support(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_position.y = 2.2
        self.publish_filters()
        self.assertEqual(0, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNone(result)
        self.assertEqual("failed", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertTrue(any(
            "sank below ground support" in message
            for level, message in self.logs if level == "error"
        ))

    def test_warmup_rejects_a_root_tilted_past_forty_five_degrees(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_pitch = math.radians(45.1)
        self.publish_filters()
        self.assertEqual(0, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNone(result)
        self.assertEqual("failed", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertTrue(any(
            "lost an upright spawn pose" in message and
            "tilt_deg=45.1" in message and "simulated_frames=1" in message
            for level, message in self.logs if level == "error"
        ))

    def test_warmup_accepts_combined_tilt_below_forty_five_degrees(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_pitch = math.radians(25.0)
        self.entity.wgPhysics.body_roll = math.radians(25.0)
        self.publish_filters()
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNotNone(result)
        self.assertEqual("active", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])

    def test_warmup_tilt_is_rejected_on_the_first_bad_solver_frame(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_roll = math.radians(80.0)
        self.publish_filters()

        result = self.simulate()

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual(0, result)
        self.assertEqual("failed", state["phase"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertIsNone(self.entity.wgPhysics)
        self.assertEqual([self.mock._pose_servo],
                         self.mock._chassis_model.motors)
        self.assertTrue(any(
            "lost an upright spawn pose" in message and
            "tilt_deg=80.0" in message
            for level, message in self.logs if level == "error"
        ))

    def test_bad_first_solve_never_swaps_before_engine_output_publish(self):
        DynamicsSimulator.corrupt_first_solve_roll = math.radians(80.0)
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])

        self.assertEqual(1, self.simulate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.now += self.native.WARMUP_SECONDS + 1.0
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertTrue(result["staging"])
        self.assertEqual("warmup", state["phase"])
        self.assertEqual(0, state["frame_output_generation"])
        self.assertIsNone(state["native_servo"])
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

        self.publish_filters()
        self.assertEqual(0, self.simulate())

        self.assertEqual("failed", state["phase"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsNone(state["native_servo"])
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertTrue(any(
            "lost an upright spawn pose" in message and
            "tilt_deg=80.0" in message
            for level, message in self.logs if level == "error"
        ))

    def test_countdown_keeps_hidden_native_body_on_python_servo(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now, active=False,
        )["staging"])

        for unused_index in range(80):
            self.publish_filters()
            self.assertEqual(1, self.simulate(dt=0.10))
            result = self.native.step(
                self.player, self.mock, self.descriptor, 0, 0, 7,
                self.now, active=False,
            )
            self.assertTrue(result["staging"])

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("warmup", state["phase"])
        self.assertIsNone(state["native_servo"])
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)

    def test_late_countdown_tilt_falls_back_before_model_handoff(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now, active=False,
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 1.0
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now, active=False,
        )["staging"])

        self.entity.wgPhysics.body_pitch = math.radians(80.0)
        self.publish_filters()
        self.assertEqual(0, self.simulate(dt=0.02))

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("failed", state["phase"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsNone(state["native_servo"])
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)

    def test_first_battle_frame_performs_the_only_model_handoff(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now, active=False,
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now, active=False,
        )["staging"])
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            self.now + 0.01, active=True,
        )

        self.assertIsNotNone(result)
        self.assertEqual("active", state["phase"])
        self.assertEqual([state["native_servo"]],
                         self.mock._chassis_model.motors)
        self.assertIsNone(self.mock._pose_servo)
        self.assertEqual(1, self.native._COUNTERS["active"])

    def test_batch_failure_faults_live_bodies_and_latches_the_battle(self):
        self.assertIsNotNone(self.activate())
        simulator = DynamicsSimulator.instances[0]
        simulator.fail_update = True
        simulator.mutate_before_failure = True
        state = getattr(self.mock, self.native.STATE_ATTR)
        validated_pose = state["last_pose"]

        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, self.now + 0.02
        ))

        self.assertEqual("faulted", state["phase"])
        self.assertEqual(validated_pose, state["last_pose"])
        self.assertTrue(self.entity.wgPhysics.staticMode)
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)
        other, unused_entity = self.make_mock(12, 902, 20.0)
        self.assertFalse(self.native.prepare(
            self.player, other, self.descriptor, 7, self.now + 0.03
        ))

    def test_batch_failure_rejects_an_existing_seed_wait_body_before_attach(self):
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
        simulator = DynamicsSimulator()
        simulator.fail_update = True
        self.native._DYNAMICS_SIMULATOR[0] = simulator
        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock, other.id: other},
            0.02, attach_at + 0.02,
        ))

        result = self.native.step(
            self.player, other, self.descriptor, 0, 0, 7, attach_at + 0.03
        )

        self.assertIsNone(result)
        self.assertEqual("failed", getattr(
            other, self.native.STATE_ATTR
        )["phase"])
        self.assertIsNone(other_entity.wgPhysics)
        self.assertFalse(any(
            event[0] == "setVehiclePhysics" for event in other_entity.events
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

    def test_active_native_owner_does_not_self_seed_filter(self):
        self.assertIsNotNone(self.activate())
        self.entity.matrix.translation = Vector3(14.0, 4.0, -6.0)
        self.entity.matrix.yaw = 0.8
        self.entity.matrix.pitch = 0.2
        self.entity.matrix.roll = -0.1

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + 0.11,
        )

        self.assertEqual((14.0, 4.0, -6.0), result["position"])
        self.assertEqual(1, len(self.bridge_calls))

    def test_canary_logs_one_next_frame_drive_diagnostic_after_repair(self):
        self.mock.id = self.native.DRIVE_DIAGNOSTIC_IDS[0]
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        vehicle_filter = self.entity.filter
        physics.enginePower = 500.0
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
        self.assertIn("id=1000 signals_before=0", diagnostic)
        self.assertIn("signals_before=0 signals=9 repaired=True", diagnostic)
        self.assertIn("engine_power=500.0 normal_engine_power=500.0", diagnostic)
        self.assertIn(
            "engine_power_mode=2 frozen=False frozen_during_frame=False "
            "static_mode=False",
            diagnostic,
        )
        self.assertIn("tracks_contact=True allow_tracks=True", diagnostic)
        self.assertIn("carcass_contact=True allow_carcass=True", diagnostic)
        self.assertIn(
            "seed_y=3.000 entity_y=3.000 body_y=3.000 "
            "placing_y=0.000 root_motors=1",
            diagnostic,
        )
        self.assertIn("left_contacts=4 right_contacts=5", diagnostic)
        self.assertIn("force=(1.000,2.000,3.000)", diagnostic)
        self.assertIn("torque=(4.000,5.000,6.000)", diagnostic)
        self.assertIn("speed=0.0 longitudinal_speed=0.0", diagnostic)

    def test_active_filter_has_no_periodic_self_samples(self):
        self.assertIsNotNone(self.activate())
        activated_at = self.now

        for sample in range(1, 62):
            self.native.step(
                self.player, self.mock, self.descriptor, 0, 0, 7,
                activated_at + sample * 0.11,
            )

        self.assertEqual(1, len(self.bridge_calls))
        self.assertTrue(self.native.is_active(self.mock))

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
        self.assertEqual(1, self.simulate())
        self.entity.filter.bodyMatrix.translation = Vector3(400, 0, 400)
        self.publish_filters()
        self.assertEqual(1, self.simulate())
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
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_position.x += 3.0
        self.publish_filters()
        self.assertEqual(0, self.simulate())
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
        self.assertIn("body=(15.000,3.000,-8.000", error)
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
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_yaw += self.native.POSE_YAW_TOLERANCE + 0.01
        self.publish_filters()
        self.assertEqual(0, self.simulate())
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
        self.assertIsNone(state["native_servo"])
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

    def test_activation_rejects_nonfinite_root_pose(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_position.x = float("nan")
        self.publish_filters()
        self.assertEqual(0, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        self.assertIsNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        self.assertTrue(any(
            "native filter tick returned an invalid pose" in message
            for level, message in self.logs if level == "error"
        ))

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
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)

    def test_activation_swap_detach_failure_keeps_python_owner(self):
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

        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())
        model.refuse_detach = True
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertIsNone(result)
        self.assertEqual("failed", state["phase"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([self.old_servo], model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)
        self.assertIsNone(state["native_servo"])
        self.assertEqual([True], collision_installs)

    def test_model_provider_switches_after_hidden_warmup_and_only_once(self):
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
        self.assertNotIn("addMotor", names)
        self.assertNotIn("delMotor", names)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        names = [event[0] for event in events]
        self.assertLess(names.index("setVehiclePhysics"),
                        names.index("delMotor"))
        self.assertLess(names.index("delMotor"), names.index("addMotor"))
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual([state["native_servo"]],
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
        self.assertEqual((500.0, 0.0, 500.0), (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        ))

    def test_guard_fault_freezes_current_entity_pose_without_filter_input(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_filter = self.entity.filter
        native_physics = self.entity.wgPhysics
        native_servo = state["native_servo"]
        self.entity.matrix.translation = Vector3(13.0, 3.5, -6.0)
        self.entity.matrix.yaw = 0.9
        calls_before = len(self.bridge_calls)

        self.assertTrue(self.native.guard_fault(
            self.mock, "water edge guard"
        ))

        self.assertEqual("faulted", state["phase"])
        self.assertEqual((13.0, 3.5, -6.0), state["last_pose"][:3])
        self.assertAlmostEqual(0.9, state["last_pose"][3])
        self.assertEqual(calls_before, len(self.bridge_calls))
        self.assertIs(native_filter, self.entity.filter)
        self.assertIs(native_physics, self.entity.wgPhysics)
        self.assertIs(native_servo, state["native_servo"])
        self.assertEqual([native_servo], self.mock._chassis_model.motors)
        self.assertEqual(0, native_physics.movementSignals)
        self.assertTrue(native_physics.staticMode)
        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 0.1,
        )
        self.assertTrue(result["faulted"])
        self.assertEqual((13.0, 3.5, -6.0), result["position"])

    def test_guard_fault_rejects_preactive_body(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))

        self.assertFalse(self.native.guard_fault(
            self.mock, "not active"
        ))

        self.assertEqual("seed_wait", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertEqual(1, len(self.bridge_calls))

    def test_faulted_body_stays_static_without_repeating_live_read_failure(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        physics = self.entity.wgPhysics
        physics.refuse_speed_read = True

        first_at = self.now + 0.10
        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, first_at,
        ))
        self.assertEqual("faulted", state["phase"])
        failed = self.native._COUNTERS["failed"]
        errors = len([level for level, unused in self.logs if level == "error"])

        self.assertEqual(1, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, first_at + 0.02,
        ))
        self.assertEqual("faulted", state["phase"])
        self.assertEqual(failed, self.native._COUNTERS["failed"])
        self.assertEqual(
            errors,
            len([level for level, unused in self.logs if level == "error"]),
        )
        self.assertTrue(physics.staticMode)
        self.assertEqual(0.0, state["frame_speed"])
        self.assertEqual(0.0, state["frame_turn_speed"])

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

    def test_stop_all_then_next_battle_restarts_drive_without_self_samples(self):
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
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, -1, -1, 7, self.now
        ))

        self.assertIsNot(first_physics, second_physics)
        self.assertEqual(6, second_physics.movementSignals)
        self.assertFalse(second_physics.isFrozen)
        calls_before_idle = len(self.bridge_calls)
        self.now += 6.10
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        ))
        self.assertEqual(calls_before_idle, len(self.bridge_calls))

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

        lineup = {mock.id: mock for mock in mocks}
        self.assertEqual(29, self.native.simulate_frame(
            lineup, 0.02, attach_at + 0.030
        ))
        self.publish_filters(lineup)
        self.assertEqual(29, self.native.simulate_frame(
            lineup, 0.02, attach_at + 0.050
        ))

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
        self.assertEqual(2, self.native.simulate_frame(
            {self.mock.id: self.mock, other.id: other},
            0.02, attach_at + 0.02,
        ))
        lineup = {self.mock.id: self.mock, other.id: other}
        self.publish_filters(lineup)
        self.assertEqual(2, self.native.simulate_frame(
            lineup, 0.02, attach_at + 0.04,
        ))
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
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())

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
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())

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
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.publish_filters()
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
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

        self.assertIn("'native_simulation'", source[ordered:report])
        self.assertIn("'native_physics'", source[ordered:report])

    def test_client_only_native_path_avoids_server_connection_clock(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("state['filter'].syncGunAngles(", source)


if __name__ == "__main__":
    unittest.main()
