import importlib.util
import math
import sys
import textwrap
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

    def applyPoint(self, point):
        rotated = self.applyVector(point)
        return Vector3(
            rotated.x + self.translation.x,
            rotated.y + self.translation.y,
            rotated.z + self.translation.z,
        )

    def invert(self):
        old_translation = self.translation
        self.yaw = -self.yaw
        inverse_translation = self.applyVector(Vector3(
            -old_translation.x,
            -old_translation.y,
            -old_translation.z,
        ))
        self.translation = inverse_translation
        self.pitch = -self.pitch
        self.roll = -self.roll


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
    instances = []
    install_failure_name = None
    clear_failures_on_create = 0

    def __init__(self):
        object.__setattr__(self, "_callbacks", {})
        self.callback_clear_failures = int(
            VehiclePhysics.clear_failures_on_create
        )
        self.damageDestructibleCb = None
        self.destructibleHealthRequestCb = None
        self.onRammingCb = None
        self.onBecameFrozenCb = None
        self.onStaticDamageCb = None
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
        VehiclePhysics.instances.append(self)

    def __setattr__(self, name, value):
        callback_names = (
            "damageDestructibleCb", "destructibleHealthRequestCb",
            "onRammingCb", "onBecameFrozenCb", "onStaticDamageCb",
        )
        if name in callback_names:
            callbacks = object.__getattribute__(self, "_callbacks")
            install_failure = VehiclePhysics.install_failure_name
            install_write = (
                value is not None or
                (name == "onRammingCb" and
                 callbacks.get("damageDestructibleCb") is not None)
            )
            if name == install_failure and install_write:
                VehiclePhysics.install_failure_name = None
                raise RuntimeError("callback install refused")
            if (value is None and
                    getattr(self, "callback_clear_failures", 0) > 0 and
                    callbacks.get(name) is not None):
                object.__setattr__(
                    self, "callback_clear_failures",
                    self.callback_clear_failures - 1,
                )
                raise RuntimeError("callback clear refused")
            callbacks[name] = value
            return
        object.__setattr__(self, name, value)

    def __getattribute__(self, name):
        if name in (
                "damageDestructibleCb", "destructibleHealthRequestCb",
                "onRammingCb", "onBecameFrozenCb", "onStaticDamageCb"):
            raise AttributeError("native callback attribute is write-only")
        return object.__getattribute__(self, name)

    def callback(self, name):
        return self._callbacks.get(name)

    @property
    def matrix(self):
        matrix = Matrix()
        matrix.translation = Vector3(
            self.body_position.x,
            self.body_position.y,
            self.body_position.z,
        )
        matrix.yaw = float(self.body_yaw)
        matrix.pitch = float(self.body_pitch)
        matrix.roll = float(self.body_roll)
        return matrix

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
    event_sink = None
    callback_hook = None

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
        if self.event_sink is not None:
            self.event_sink.append(("update", float(dt)))
        self.calls.append((float(dt), tuple(vehicles), tuple(bodies)))
        callback_hook = DynamicsSimulator.callback_hook
        if callback_hook is not None:
            for physics in vehicles:
                callback_hook(physics)
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
            # Native C++ integration does not invoke the Python property getter.
            physics.body_yaw += physics._rspeed * float(dt)
            physics.body_position = Vector3(
                physics.body_position.x +
                math.sin(physics.body_yaw) * physics._speed * float(dt),
                physics.body_position.y,
                physics.body_position.z +
                math.cos(physics.body_yaw) * physics._speed * float(dt),
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


class FilterReleaseRetryEntity(Entity):
    def __init__(self, entity_id):
        super().__init__(entity_id)
        self.avatar_filter_failures = 0

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, value):
        if isinstance(value, AvatarFilter) and self.avatar_filter_failures > 0:
            self.avatar_filter_failures -= 1
            raise RuntimeError("filter release refused")
        Entity.filter.fset(self, value)


class SilentReleaseRetryEntity(Entity):
    def __init__(self, entity_id):
        super().__init__(entity_id)
        self.filter_release_no_ops = 0
        self.physics_release_no_ops = 0

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, value):
        if (isinstance(value, AvatarFilter) and
                self.filter_release_no_ops > 0):
            self.filter_release_no_ops -= 1
            return
        Entity.filter.fset(self, value)

    def __setattr__(self, name, value):
        if (name == "wgPhysics" and value is None and
                getattr(self, "physics_release_no_ops", 0) > 0):
            object.__setattr__(
                self, "physics_release_no_ops",
                self.physics_release_no_ops - 1,
            )
            return
        super(SilentReleaseRetryEntity, self).__setattr__(name, value)


class SilentFilterAttachEntity(Entity):
    @Entity.filter.setter
    def filter(self, value):
        if isinstance(value, VehicleFilter):
            return
        Entity.filter.fset(self, value)


class Model(object):
    def __init__(self, motor=None):
        self.motors = [] if motor is None else [motor]
        self.matrix = Matrix()
        self.nodes = {}

    def addMotor(self, motor):
        self.motors.append(motor)

    def delMotor(self, motor):
        self.motors.remove(motor)

    def node(self, name):
        return self.nodes[name]


class FirstFashionDetachRefusalModel(Model):
    def __init__(self, motor=None):
        super(FirstFashionDetachRefusalModel, self).__init__(motor)
        self.fashion_delete_attempts = 0

    def __delattr__(self, name):
        if name == "wg_fashion":
            self.fashion_delete_attempts += 1
            if self.fashion_delete_attempts == 1:
                raise RuntimeError("fashion detach refused")
        super(FirstFashionDetachRefusalModel, self).__delattr__(name)


class ProviderRollbackFailureFashion(object):
    def __init__(self):
        object.__setattr__(self, "placingCompensationMatrix", object())
        object.__setattr__(self, "physicsInfo", object())
        object.__setattr__(self, "movementInfo", object())
        object.__setattr__(self, "placing_writes", 0)

    def __setattr__(self, name, value):
        if name == "placingCompensationMatrix":
            if self.placing_writes:
                raise RuntimeError("placing provider rollback refused")
            object.__setattr__(self, "placing_writes", 1)
        elif name == "physicsInfo":
            raise RuntimeError("physics provider bind refused")
        object.__setattr__(self, name, value)


class NativeBotPhysicsTest(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        self.now = 100.0
        self.logs = []
        self.bridge_ok = True
        self.bridge_calls = []
        self.output_calls = []
        self.output_fail_ids = set()
        self.output_invalid_ids = set()
        self.native_events = []
        self.module_bridge = None
        self.reject_post_attach_seed = False
        DynamicsSimulator.instances = []
        DynamicsSimulator.produce_contacts = True
        DynamicsSimulator.corrupt_first_solve_roll = None
        DynamicsSimulator.event_sink = self.native_events
        DynamicsSimulator.callback_hook = None
        VehiclePhysics.instances = []
        VehiclePhysics.install_failure_name = None
        VehiclePhysics.clear_failures_on_create = 0

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
            vehicle_filter.bodyMatrix.yaw = float(direction[0])
            vehicle_filter.bodyMatrix.pitch = float(direction[1])
            vehicle_filter.bodyMatrix.roll = float(direction[2])
            if vehicle_filter.owner is not None:
                vehicle_filter.owner.matrix.translation = Vector3(position)
                vehicle_filter.owner.matrix.yaw = float(direction[0])
                vehicle_filter.owner.matrix.pitch = float(direction[1])
                vehicle_filter.owner.matrix.roll = float(direction[2])
            return True

        bridge.seed_filter = seed_filter

        def output_filter(vehicle_filter, timestamp):
            owner_id = getattr(getattr(vehicle_filter, "owner", None), "id", None)
            self.output_calls.append((owner_id, float(timestamp)))
            self.native_events.append(("output", owner_id, float(timestamp)))
            if owner_id in self.output_fail_ids:
                return False
            # Exact WGVehicleFilter2.output does not copy the attached rigid root.
            # Keep this legacy entry point deliberately inert in the fake so tests
            # cannot manufacture the production handoff that 1.8.42 was missing.
            if owner_id in self.output_invalid_ids:
                vehicle_filter.owner.matrix.translation.x = float("nan")
            return True

        bridge.output_filter = output_filter

        def publish_physics_root(vehicle_filter, vehicle_physics, timestamp,
                                 space_id):
            owner_id = getattr(getattr(vehicle_filter, "owner", None),
                               "id", None)
            self.output_calls.append((owner_id, float(timestamp)))
            self.native_events.append(("output", owner_id, float(timestamp)))
            if owner_id in self.output_fail_ids:
                return False
            # The real bridge reads the solved WGVehiclePhysics2 root matrix,
            # inserts that pose into Filter::input and then calls output. Keep
            # these steps explicit: WGVehicleFilter2.output alone does not copy
            # the rigid body transform.
            vehicle_filter.bodyMatrix.translation = Vector3(
                vehicle_physics.body_position.x,
                vehicle_physics.body_position.y,
                vehicle_physics.body_position.z,
            )
            vehicle_filter.bodyMatrix.yaw = float(vehicle_physics.body_yaw)
            vehicle_filter.bodyMatrix.pitch = float(vehicle_physics.body_pitch)
            vehicle_filter.bodyMatrix.roll = float(vehicle_physics.body_roll)
            if vehicle_filter.owner is not None:
                vehicle_filter.owner.matrix.translation = Vector3(
                    vehicle_physics.body_position.x,
                    vehicle_physics.body_position.y,
                    vehicle_physics.body_position.z,
                )
                vehicle_filter.owner.matrix.yaw = float(
                    vehicle_physics.body_yaw
                )
                vehicle_filter.owner.matrix.pitch = float(
                    vehicle_physics.body_pitch
                )
                vehicle_filter.owner.matrix.roll = float(
                    vehicle_physics.body_roll
                )
            if owner_id in self.output_invalid_ids:
                vehicle_filter.owner.matrix.translation.x = float("nan")
            return True

        bridge.publish_physics_root = publish_physics_root

        def filter_has_physics(vehicle_filter, vehicle_physics):
            return vehicle_filter.physics is vehicle_physics

        bridge.filter_has_physics = filter_has_physics

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

        self.destructible_health = 10
        self.destructible_health_error = None
        self.destructible_health_calls = []
        self.destructible_damage_calls = []
        self.destructible_damage_error = None
        destructibles_authority = types.ModuleType(
            "gui.mods.offhangar.destructibles_authority"
        )

        def collision_health(space_id, chunk_id, item_index):
            self.destructible_health_calls.append(
                (space_id, chunk_id, item_index)
            )
            if self.destructible_health_error is not None:
                raise self.destructible_health_error
            return self.destructible_health

        def apply_collision_damage(space_id, chunk_id, item_index,
                                   mat_kind, damage, point, fall_yaw,
                                   impact_speed):
            event = (
                space_id, chunk_id, item_index, mat_kind, damage,
                tuple(point), fall_yaw, impact_speed,
            )
            self.native_events.append(("damage", event))
            self.destructible_damage_calls.append(event)
            if self.destructible_damage_error is not None:
                raise self.destructible_damage_error
            return True

        destructibles_authority.collision_health = collision_health
        destructibles_authority.apply_collision_damage = (
            apply_collision_damage
        )

        gui = types.ModuleType("gui")
        mods = types.ModuleType("gui.mods")
        package = types.ModuleType("gui.mods.offhangar")
        gui.mods = mods
        mods.offhangar = package
        package.native_filter_bridge = bridge
        self.module_bridge = bridge
        package.destructibles_authority = destructibles_authority

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
        sys.modules[
            "gui.mods.offhangar.destructibles_authority"
        ] = destructibles_authority

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
            _offh_native_model_root_ready=True,
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
            _offh_native_model_root_ready=True,
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

    def activate(self, throttle=1, turn=1):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))
        return self.native.step(
            self.player, self.mock, self.descriptor,
            throttle, turn, 7, self.now
        )

    def assert_failed_static(self, result, position=None):
        self.assertIsNotNone(result)
        self.assertTrue(result["failed"])
        self.assertEqual(0.0, result["velocity"])
        self.assertEqual(0.0, result["turn_velocity"])
        if position is not None:
            self.assertEqual(position, result["position"])

    def test_required_body_waits_for_entity_binding_without_failing_closed(self):
        position_before = (
            self.mock.position.x,
            self.mock.position.y,
            self.mock.position.z,
        )
        collision_installs = []
        self.mock.bw_entity = None
        self.mock._collision_obstacle = None
        self.mock._offh_install_collision_obstacle = (
            lambda: collision_installs.append(True)
        )

        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.assertTrue(self.native.requires_native(self.player, self.mock))
        self.assertFalse(self.native.is_prepared(self.mock))

        pending = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 0.01,
        )

        self.assertIsNotNone(pending)
        self.assertTrue(pending["staging"])
        self.assertNotIn("failed", pending)
        self.assertEqual(0.0, pending["velocity"])
        self.assertEqual(0.0, pending["turn_velocity"])
        self.assertFalse(self.native.is_prepared(self.mock))
        self.assertEqual([], collision_installs)
        self.assertIsNone(self.mock._collision_obstacle)
        self.assertEqual(position_before, (
            self.mock.position.x,
            self.mock.position.y,
            self.mock.position.z,
        ))
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

        self.mock.bw_entity = self.entity
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now + 0.02
        ))
        self.assertEqual(
            "seed_wait", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

    def test_required_body_waits_for_spawn_root_handoff_before_native_attach(self):
        self.mock._offh_native_model_root_ready = False

        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            self.now + 0.01,
        )

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        self.assertFalse(hasattr(self.mock, self.native.STATE_ATTR))
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertFalse(hasattr(self.mock, "_native_pose_servo"))

        self.mock._offh_native_model_root_ready = True
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now + 0.02
        ))
        self.assertEqual(
            "seed_wait", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

    def test_prepare_rejects_silent_native_filter_attach_no_op(self):
        entity = SilentFilterAttachEntity(901)
        self.entity = entity
        self.mock.bw_entity = entity

        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("failed", state["phase"])
        self.assertFalse(self.native.owns_filter(self.mock))
        self.assertIsNone(state["filter"])
        self.assertIsNone(state["physics"])
        self.assertIsNone(state["entity_provider"])
        self.assertIsInstance(entity.filter, AvatarFilter)
        self.assertNotIsInstance(entity.filter, VehicleFilter)
        self.assertIsNone(entity.wgPhysics)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertFalse(hasattr(self.mock, "_native_pose_servo"))
        self.assertTrue(any(
            "WGVehicleFilter2 attach readback mismatch" in message
            for level, message in self.logs if level == "error"
        ))

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

    def test_batch_publishes_post_solver_pose_and_physics_speeds(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual(1, self.simulate(dt=0.10))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7, self.now,
        )
        solved = (
            physics.body_position.x,
            physics.body_position.y,
            physics.body_position.z,
        )
        self.assertEqual(solved, result["position"])
        self.assertEqual(8.0, result["velocity"])
        self.assertEqual(0.2, result["turn_velocity"])
        self.assertEqual(8.0, physics.speed)
        self.assertEqual(0.2, physics.rspeed)
        # These are filter-history estimates, not WGVehiclePhysics2 velocity.
        # The native owner must feed AI from physics.speed/rspeed instead.
        self.assertEqual(0.0, self.entity.filter.longitudinalSpeed)
        self.assertEqual(0.0, self.entity.filter.angularSpeed)
        self.assertEqual(8.0, state["frame_speed"])
        self.assertEqual(0.2, state["frame_turn_speed"])

    def test_filter_output_alone_cannot_publish_the_rigid_root(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        before = (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        )
        physics.body_position = Vector3(30.0, 4.0, -40.0)

        self.assertTrue(self.module_bridge.output_filter(
            self.entity.filter, self.now + 0.01
        ))

        self.assertEqual(before, (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        ))

        self.assertTrue(self.module_bridge.publish_physics_root(
            self.entity.filter, physics, self.now + 0.02, 7
        ))
        self.assertEqual((30.0, 4.0, -40.0), (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        ))

    def test_frame_cache_uses_physics_root_not_delayed_filter_pose(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        entity_before = (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        )

        def delayed_publish(vehicle_filter, vehicle_physics, timestamp,
                            space_id):
            return True

        self.module_bridge.publish_physics_root = delayed_publish
        physics.body_position = Vector3(15.0, 3.0, -5.0)
        physics.body_yaw = 0.8
        at = self.now + 0.10

        self.assertEqual(1, self.simulate(timestamp=at))
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, at,
        )

        expected = self.native._physics_pose(physics)
        self.assertEqual(expected[:3], result["position"])
        self.assertAlmostEqual(expected[3], result["yaw"])
        self.assertEqual(entity_before, (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        ))

    def test_active_chassis_and_gameplay_share_canonical_physics_root_provider(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics

        # mock.matrix is the established mutable MatrixProvider which
        # offline_battle updates before the authority publishes mock.position.
        # Delaying Filter::output must therefore not leave the visible chassis
        # on the stale entity provider.
        self.assertIsNot(physics.matrix, self.mock.matrix)

        def delayed_publish(vehicle_filter, vehicle_physics, timestamp,
                            space_id):
            return True

        self.module_bridge.publish_physics_root = delayed_publish
        physics.body_position = Vector3(15.0, 3.0, -5.0)
        physics.body_yaw = 0.8
        at = self.now + 0.10

        self.assertEqual(1, self.simulate(timestamp=at))
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, at,
        )
        expected = self.native._physics_pose(physics)

        # Mirror offline_battle's same-frame canonical state/pose commit before
        # publish_authoritative_bots reads mock.position.
        self.mock.position = Vector3(result["position"])
        self.mock.yaw = result["yaw"]
        self.mock.pitch = result["pitch"]
        self.mock.roll = result["roll"]
        self.mock.matrix.translation = self.mock.position
        self.mock.matrix.yaw = self.mock.yaw
        self.mock.matrix.pitch = self.mock.pitch
        self.mock.matrix.roll = self.mock.roll

        # Gameplay and the authority packet source are both mock.position after
        # this commit; the root Servo must consume the same canonical provider.
        self.assertEqual(expected[:3], (
            self.mock.position.x,
            self.mock.position.y,
            self.mock.position.z,
        ))
        self.assertEqual(expected[:3], (
            self.mock.matrix.translation.x,
            self.mock.matrix.translation.y,
            self.mock.matrix.translation.z,
        ))
        self.assertEqual(
            ("servo", self.mock.matrix),
            self.mock._chassis_model.motors[0],
        )

    def test_same_timestamp_does_not_solve_or_publish_twice(self):
        self.assertIsNotNone(self.activate())
        at = self.now + 0.10
        self.assertEqual(1, self.simulate(dt=0.10, timestamp=at))
        outputs = list(self.output_calls)
        calls = len(DynamicsSimulator.instances[0].calls)

        self.assertEqual(0, self.simulate(dt=0.10, timestamp=at))
        self.assertEqual(outputs, self.output_calls)
        self.assertEqual(calls, len(DynamicsSimulator.instances[0].calls))

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

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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

    def test_warmup_failure_keeps_refs_when_entity_release_silently_no_ops(self):
        entity = SilentReleaseRetryEntity(901)
        self.entity = entity
        self.mock.bw_entity = entity
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_filter = state["filter"]
        native_physics = state["physics"]
        entity.physics_release_no_ops = 1
        entity.filter_release_no_ops = 1
        native_physics.refuse_speed_read = True

        self.assertEqual(0, self.simulate())

        self.assertEqual("faulted", state["phase"])
        self.assertIs(native_filter, state["filter"])
        self.assertIs(native_physics, state["physics"])
        self.assertIs(state["entity_provider"], entity.matrix)
        self.assertIs(native_filter, entity.filter)
        self.assertIs(native_physics, entity.wgPhysics)
        self.assertIsInstance(self.mock._pose_servo, object)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

        # The first transaction proved the physics setter was a no-op and stopped
        # before attempting the filter setter. Let the retry exercise both writes.
        entity.filter_release_no_ops = 0
        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual("stopped", state["phase"])
        self.assertIsNone(state["filter"])
        self.assertIsNone(state["physics"])
        self.assertIsNone(state["entity_provider"])
        self.assertIsNone(entity.wgPhysics)
        self.assertIsInstance(entity.filter, AvatarFilter)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

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
        self.assertEqual([
            ("update", 0.02),
            ("output", other_entity.id, solve_at),
            ("output", self.entity.id, solve_at),
        ], self.native_events)

    def test_destructible_callbacks_install_with_exact_sync_health_contract(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )
        physics = self.entity.wgPhysics

        damage_callback = physics.callback("damageDestructibleCb")
        health_callback = physics.callback("destructibleHealthRequestCb")
        self.assertIsNotNone(damage_callback)
        self.assertIsNotNone(health_callback)
        self.assertIsNone(physics.callback("onRammingCb"))
        with self.assertRaises(AttributeError):
            getattr(physics, "damageDestructibleCb")
        self.assertEqual(
            10, health_callback(100, 2)
        )
        self.assertEqual([(7, 100, 2)], self.destructible_health_calls)
        with self.assertRaises(TypeError):
            health_callback(7, 100, 2)
        with self.assertRaises(TypeError):
            damage_callback(
                7, 100, 2, 73, 1, (1.0, 2.0, 3.0), 4.0
            )

    def _attach_with_callback_install_failure(self, callback_name,
                                              clear_failures=0):
        VehiclePhysics.install_failure_name = callback_name
        VehiclePhysics.clear_failures_on_create = clear_failures
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )

        self.assertEqual(1, len(VehiclePhysics.instances))
        return (
            result,
            getattr(self.mock, self.native.STATE_ATTR),
            VehiclePhysics.instances[0],
        )

    def _assert_callback_install_rollback(self, callback_name):
        result, state, physics = self._attach_with_callback_install_failure(
            callback_name
        )

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        self.assertEqual("failed", state["phase"])
        self.assertIsNone(state["physics"])
        self.assertIsNone(state["filter"])
        self.assertIsNone(state["destructible_health_callback"])
        self.assertIsNone(state["destructible_damage_callback"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        for name in (
                "damageDestructibleCb", "destructibleHealthRequestCb",
                "onRammingCb", "onBecameFrozenCb", "onStaticDamageCb"):
            self.assertIsNone(physics.callback(name), name)

    def test_damage_callback_install_failure_clears_partial_health_callback(self):
        self._assert_callback_install_rollback("damageDestructibleCb")

    def test_on_ramming_callback_install_failure_clears_both_callbacks(self):
        self._assert_callback_install_rollback("onRammingCb")

    def test_callback_install_and_clear_failure_retains_retryable_owner(self):
        result, state, physics = self._attach_with_callback_install_failure(
            "damageDestructibleCb", clear_failures=1
        )
        native_filter = state["filter"]

        self.assertTrue(result["faulted"])
        self.assertEqual("faulted", state["phase"])
        self.assertIs(physics, state["physics"])
        self.assertIs(native_filter, self.entity.filter)
        self.assertIs(physics, self.entity.wgPhysics)
        self.assertIs(state["entity_provider"], self.entity.matrix)
        self.assertIsNotNone(
            physics.callback("destructibleHealthRequestCb")
        )
        self.assertIsNone(physics.callback("damageDestructibleCb"))
        self.assertTrue(physics.staticMode)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual("stopped", state["phase"])
        self.assertIsNone(physics.callback("destructibleHealthRequestCb"))
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

    def test_zero_destructible_damage_is_noop_during_warmup(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )
        state = getattr(self.mock, self.native.STATE_ATTR)
        callback_observations = []

        def emit_zero_damage(physics):
            physics.callback("damageDestructibleCb")(
                100, 2, 73, 0, Vector3(1.0, 2.0, 3.0), 6.0
            )
            callback_observations.append((
                state["destructible_callback_error"],
                list(state["destructible_events"]),
            ))

        DynamicsSimulator.callback_hook = emit_zero_damage

        result = self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, attach_at + 0.02
        )

        self.assertEqual([(None, [])], callback_observations)
        self.assertEqual(1, result)
        self.assertEqual("warmup", state["phase"])
        self.assertIsNone(state["destructible_callback_error"])
        self.assertEqual([], state["destructible_events"])
        self.assertEqual([], state["destructible_pending"])
        self.assertEqual([], self.destructible_damage_calls)

    def test_zero_destructible_damage_is_noop_for_active_body(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("active", state["phase"])
        callback_observations = []

        def emit_zero_damage(physics):
            physics.callback("damageDestructibleCb")(
                100, 2, 73, 0, Vector3(1.0, 2.0, 3.0), 6.0
            )
            callback_observations.append((
                state["destructible_callback_error"],
                list(state["destructible_events"]),
            ))

        DynamicsSimulator.callback_hook = emit_zero_damage

        result = self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, self.now + 0.02
        )

        self.assertEqual([(None, [])], callback_observations)
        self.assertEqual(1, result)
        self.assertEqual("active", state["phase"])
        self.assertTrue(self.native.is_active(self.mock))
        self.assertIsNone(state["destructible_callback_error"])
        self.assertEqual([], state["destructible_events"])
        self.assertEqual([], state["destructible_pending"])
        self.assertEqual([], self.destructible_damage_calls)

    def test_zero_destructible_damage_is_noop_when_batch_is_full(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        callback_observations = []

        def emit_zero_damage(physics):
            sentinel = object()
            events = state["destructible_events"]
            events.extend(
                [sentinel] * self.native.DESTRUCTIBLE_BATCH_EVENT_LIMIT
            )
            physics.callback("damageDestructibleCb")(
                100, 2, 73, 0, Vector3(1.0, 2.0, 3.0), 6.0
            )
            callback_observations.append((
                state["destructible_callback_error"],
                len(events),
                all(event is sentinel for event in events),
            ))
            del events[:]

        DynamicsSimulator.callback_hook = emit_zero_damage

        result = self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, self.now + 0.02
        )

        self.assertEqual([
            (None, self.native.DESTRUCTIBLE_BATCH_EVENT_LIMIT, True)
        ], callback_observations)
        self.assertEqual(1, result)
        self.assertEqual("active", state["phase"])
        self.assertIsNone(state["destructible_callback_error"])
        self.assertEqual([], state["destructible_events"])
        self.assertEqual([], state["destructible_pending"])
        self.assertEqual([], self.destructible_damage_calls)

    def test_negative_destructible_damage_fails_closed_during_warmup(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )
        state = getattr(self.mock, self.native.STATE_ATTR)
        callback_errors = []

        def emit_negative_damage(physics):
            physics.callback("damageDestructibleCb")(
                100, 2, 73, -1, Vector3(1.0, 2.0, 3.0), 6.0
            )
            callback_errors.append(state["destructible_callback_error"])

        DynamicsSimulator.callback_hook = emit_negative_damage
        solve_at = attach_at + 0.02

        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, solve_at
        ))

        self.assertEqual(1, len(callback_errors))
        self.assertIsNotNone(callback_errors[0])
        self.assertEqual("failed", state["phase"])
        self.assertIn(
            "native destructible callback failed", state["reason"]
        )
        self.assertEqual([(self.entity.id, solve_at)], self.output_calls)
        self.assertEqual([], state["destructible_events"])
        self.assertEqual([], state["destructible_pending"])
        self.assertEqual([], self.destructible_damage_calls)

    def test_active_destructible_damage_drains_after_all_outputs_in_body_order(self):
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
            self.player, other, self.descriptor, 0, 0, 7,
            attach_at + 0.01,
        )
        lineup = {self.mock.id: self.mock, other.id: other}
        self.native.simulate_frame(lineup, 0.02, attach_at + 0.02)
        self.native.simulate_frame(lineup, 0.02, attach_at + 0.04)
        active_at = attach_at + self.native.WARMUP_SECONDS + 0.03
        self.native.simulate_frame(lineup, 0.02, active_at)
        self.native.step(
            self.player, other, self.descriptor, 0, 0, 7, active_at
        )
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, active_at
        )
        self.assertTrue(self.native.is_active(other))
        self.assertTrue(self.native.is_active(self.mock))

        def emit_damage(physics):
            owner_id = physics.vehicle_filter.owner.id
            physics.callback("damageDestructibleCb")(
                owner_id, owner_id + 100, 73, 4,
                Vector3(owner_id, 2.0, 3.0), 6.0,
            )

        DynamicsSimulator.callback_hook = emit_damage
        self.native_events[:] = []
        solve_at = active_at + 0.02

        self.assertEqual(2, self.native.simulate_frame(
            lineup, 0.02, solve_at
        ))

        self.assertEqual("update", self.native_events[0][0])
        self.assertEqual([
            ("output", other_entity.id, solve_at),
            ("output", self.entity.id, solve_at),
        ], self.native_events[1:3])
        self.assertEqual([other_entity.id, self.entity.id], [
            event[1][1] for event in self.native_events[3:]
            if event[0] == "damage"
        ])
        self.assertEqual([other_entity.id, self.entity.id], [
            event[1] for event in self.destructible_damage_calls
        ])

    def test_warmup_damage_is_bounded_merged_and_deferred_until_active(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )

        def emit_duplicate_damage(physics):
            damage_callback = physics.callback("damageDestructibleCb")
            damage_callback(
                100, 2, 73, 3, Vector3(1.0, 2.0, 3.0), 5.0
            )
            damage_callback(
                100, 2, 73, 4, Vector3(4.0, 5.0, 6.0), 7.0
            )

        DynamicsSimulator.callback_hook = emit_duplicate_damage
        self.assertEqual(1, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, attach_at + 0.02
        ))
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual([], self.destructible_damage_calls)
        self.assertEqual(1, len(state["destructible_pending"]))
        self.assertEqual(7, state["destructible_pending"][0][3])

        active_at = attach_at + self.native.WARMUP_SECONDS + 0.02
        self.assertEqual(1, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, active_at
        ))
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, active_at
        )
        self.assertEqual([], self.destructible_damage_calls)
        DynamicsSimulator.callback_hook = None

        self.assertEqual(1, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, active_at + 0.02
        ))

        self.assertEqual(1, len(self.destructible_damage_calls))
        damage = self.destructible_damage_calls[0]
        self.assertEqual((7, 100, 2, 73, 14), damage[:5])
        self.assertEqual((4.0, 5.0, 6.0), damage[5])
        self.assertEqual([], state["destructible_pending"])

    def test_warmup_damage_overflow_fails_after_filter_output(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )
        self.native.DESTRUCTIBLE_WARMUP_EVENT_LIMIT = 1

        def emit_distinct_damage(physics):
            damage_callback = physics.callback("damageDestructibleCb")
            damage_callback(
                100, 2, 73, 1, Vector3(1.0, 2.0, 3.0), 5.0
            )
            damage_callback(
                100, 3, 73, 1, Vector3(1.0, 2.0, 3.0), 5.0
            )

        DynamicsSimulator.callback_hook = emit_distinct_damage
        solve_at = attach_at + 0.02

        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, solve_at
        ))

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("failed", state["phase"])
        self.assertEqual([(self.entity.id, solve_at)], self.output_calls)
        self.assertEqual([], self.destructible_damage_calls)

    def test_batch_exception_discards_generated_destructible_damage(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )

        def emit_damage(physics):
            physics.callback("damageDestructibleCb")(
                100, 2, 73, 5, Vector3(1.0, 2.0, 3.0), 6.0
            )

        DynamicsSimulator.callback_hook = emit_damage
        simulator = DynamicsSimulator()
        simulator.fail_update = True
        self.native._DYNAMICS_SIMULATOR[0] = simulator

        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, attach_at + 0.02
        ))

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual([], self.destructible_damage_calls)
        self.assertEqual([], state["destructible_events"])
        self.assertEqual([], state["destructible_pending"])

    def test_health_callback_error_faults_only_after_batch_output(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        attach_at = self.now + self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, attach_at
        )
        results = []
        self.destructible_health_error = RuntimeError("health unavailable")

        def request_health(physics):
            results.append(
                physics.callback("destructibleHealthRequestCb")(100, 2)
            )

        DynamicsSimulator.callback_hook = request_health
        solve_at = attach_at + 0.02

        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, solve_at
        ))

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual([None], results)
        self.assertEqual([(self.entity.id, solve_at)], self.output_calls)
        self.assertEqual("failed", state["phase"])
        self.assertIn("health unavailable", state["reason"])

    def test_callback_clear_failure_keeps_retryable_native_owner(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        physics = self.entity.wgPhysics
        native_filter = state["filter"]
        native_servo = state["native_servo"]
        physics.callback_clear_failures = 1

        self.assertFalse(self.native.stop_mock(self.mock, True))

        self.assertEqual("faulted", state["phase"])
        self.assertIs(physics, state["physics"])
        self.assertIs(native_filter, state["filter"])
        self.assertIs(native_servo, state["native_servo"])
        self.assertIs(physics, self.entity.wgPhysics)
        self.assertIs(native_filter, self.entity.filter)
        self.assertIsNotNone(physics.callback("damageDestructibleCb"))

        self.assertTrue(self.native.stop_mock(self.mock, True))
        self.assertIsNone(physics.callback("damageDestructibleCb"))
        self.assertIsNone(physics.callback("destructibleHealthRequestCb"))
        self.assertEqual("stopped", state["phase"])

    def test_callback_fault_clear_failure_retains_owner_until_cleanup_retry(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        physics = self.entity.wgPhysics
        native_filter = state["filter"]
        native_servo = state["native_servo"]
        physics.callback_clear_failures = 1
        self.destructible_damage_error = RuntimeError("damage sink failed")

        def emit_damage(body):
            body.callback("damageDestructibleCb")(
                100, 2, 73, 5, Vector3(1.0, 2.0, 3.0), 6.0
            )

        DynamicsSimulator.callback_hook = emit_damage
        solve_at = self.now + 0.02

        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, solve_at
        ))

        self.assertEqual("faulted", state["phase"])
        self.assertIs(physics, state["physics"])
        self.assertIs(native_filter, state["filter"])
        self.assertIs(native_servo, state["native_servo"])
        self.assertIsNotNone(physics.callback("damageDestructibleCb"))
        self.assertTrue(physics.staticMode)

        DynamicsSimulator.callback_hook = None
        self.assertTrue(self.native.stop_mock(self.mock, True))
        self.assertEqual("stopped", state["phase"])
        self.assertIsNone(physics.callback("damageDestructibleCb"))

    def test_stop_all_retries_callback_release_before_global_reset(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        physics = self.entity.wgPhysics
        simulator = self.native._DYNAMICS_SIMULATOR[0]
        physics.callback_clear_failures = 1

        self.assertEqual(-1, self.native.stop_all(
            {self.mock.id: self.mock}
        ))

        self.assertEqual("faulted", state["phase"])
        self.assertIs(simulator, self.native._DYNAMICS_SIMULATOR[0])
        self.assertIsNotNone(physics.callback("damageDestructibleCb"))

        self.assertEqual(1, self.native.stop_all(
            {self.mock.id: self.mock}
        ))
        self.assertIsNone(physics.callback("damageDestructibleCb"))
        self.assertIsNone(physics.callback("destructibleHealthRequestCb"))
        self.assertIsNone(self.native._DYNAMICS_SIMULATOR[0])
        self.assertEqual("stopped", state["phase"])

    def test_stop_all_retries_destructible_adapter_restore_before_global_reset(self):
        offline_entity = sys.modules["OfflineEntity"]
        adapter_active = [False]
        restore_results = [False, True]
        restore_calls = []

        def install_adapter():
            adapter_active[0] = True
            return True

        def restore_adapter():
            restore_calls.append(True)
            if not restore_results.pop(0):
                return False
            adapter_active[0] = False
            return True

        offline_entity.install_native_destructible_callback_adapter = (
            install_adapter
        )
        offline_entity.restore_native_destructible_callback_adapter = (
            restore_adapter
        )
        self.assertIsNotNone(self.activate())
        simulator = self.native._DYNAMICS_SIMULATOR[0]

        self.assertEqual(-1, self.native.stop_all(
            {self.mock.id: self.mock}
        ))

        counters_after_release = dict(self.native._COUNTERS)
        self.assertEqual("stopped", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])
        self.assertEqual(1, counters_after_release["stopped"])
        self.assertIs(simulator, self.native._DYNAMICS_SIMULATOR[0])
        self.assertTrue(adapter_active[0])
        self.assertEqual([True], restore_calls)

        self.assertEqual(1, self.native.stop_all(
            {self.mock.id: self.mock}
        ))

        self.assertEqual([True, True], restore_calls)
        self.assertFalse(adapter_active[0])
        self.assertIsNone(self.native._DYNAMICS_SIMULATOR[0])
        self.assertTrue(all(
            value == 0 for value in self.native._COUNTERS.values()
        ))

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
        bad_physics = self.entity.wgPhysics
        bad_physics.refuse_speed_read = True

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
            (other_entity.wgPhysics, bad_physics),
            DynamicsSimulator.instances[0].calls[0][1],
        )
        self.assertEqual([
            (other_entity.id, attach_at + 0.02),
            (self.entity.id, attach_at + 0.02),
        ], self.output_calls)
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
        bad_physics = self.entity.wgPhysics
        self.output_invalid_ids.add(self.entity.id)

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
            (other_entity.wgPhysics, bad_physics),
            DynamicsSimulator.instances[0].calls[0][1],
        )
        self.assertFalse(self.native._SIMULATION_FAILED[0])

    def test_output_failure_isolates_one_body_after_shared_update(self):
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
        self.output_fail_ids.add(self.entity.id)
        bad_physics = self.entity.wgPhysics

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
            (other_entity.wgPhysics, bad_physics),
            DynamicsSimulator.instances[0].calls[0][1],
        )
        self.assertEqual([
            (other_entity.id, attach_at + 0.02),
            (self.entity.id, attach_at + 0.02),
        ], self.output_calls)
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

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))

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
        self.assertEqual(0, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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
        self.assertEqual(0, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNotNone(result)
        self.assertEqual("active", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])

    def test_warmup_accepts_small_solver_yaw_settle(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_yaw += 0.0501
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertIsNotNone(result)
        self.assertEqual("active", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])

    def test_warmup_rejects_large_solver_yaw_change(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertEqual(1, self.simulate())
        self.entity.wgPhysics.body_yaw += 0.10

        result = self.simulate()

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual(0, result)
        self.assertEqual("failed", state["phase"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertTrue(any(
            "stage=warmup" in message and "yaw_delta=0.1000" in message
            for level, message in self.logs if level == "error"
        ))

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

        self.assertEqual(0, self.simulate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.now += self.native.WARMUP_SECONDS + 1.0
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertEqual("failed", state["phase"])
        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 1.0
        self.assertEqual(1, self.simulate(timestamp=self.now))
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now, active=False,
        )["staging"])

        self.entity.wgPhysics.body_pitch = math.radians(80.0)
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
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now, active=False,
        )["staging"])
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

        self.assertEqual(1, self.simulate(timestamp=self.now + 0.01))
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
        outputs = len(self.output_calls)
        state = getattr(self.mock, self.native.STATE_ATTR)
        validated_pose = state["last_pose"]

        self.assertEqual(0, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, self.now + 0.02
        ))

        self.assertEqual("faulted", state["phase"])
        self.assertEqual(outputs, len(self.output_calls))
        self.assertEqual(validated_pose, state["last_pose"])
        self.assertTrue(self.entity.wgPhysics.staticMode)
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)
        other, unused_entity = self.make_mock(12, 902, 20.0)
        self.assertFalse(self.native.prepare(
            self.player, other, self.descriptor, 7, self.now + 0.03
        ))

    def test_attached_filter_input_only_sets_drive_and_does_not_publish_pose(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        physics = self.entity.wgPhysics
        before = (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        )
        physics.body_position.x += 4.0

        self.entity.filter.notifyInputKeysDown(1, 0)

        self.assertEqual(before, (
            self.entity.matrix.translation.x,
            self.entity.matrix.translation.y,
            self.entity.matrix.translation.z,
        ))
        self.assertEqual([], self.output_calls)
        self.assertEqual((1, 0), self.entity.filter.input)

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

        self.assert_failed_static(result, (20.0, 3.0, -8.0))
        self.assertEqual("failed", getattr(
            other, self.native.STATE_ATTR
        )["phase"])
        self.assertIsNone(other_entity.wgPhysics)
        self.assertFalse(any(
            event[0] == "setVehiclePhysics" for event in other_entity.events
        ))

    def test_missing_live_ground_support_waits_then_attaches_exactly_once(self):
        self.ground_supported = False
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        first = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assertTrue(first["staging"])
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("seed_wait", state["phase"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIs(self.entity.filter, state["filter"])
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertFalse(first.get("failed", False))
        self.assertFalse(any(
            event[0] == "setVehiclePhysics" for event in self.entity.events
        ))

        self.now += self.native.GROUND_SUPPORT_RETRY_SECONDS + 0.01
        second = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertTrue(second["staging"])
        self.assertEqual("seed_wait", state["phase"])
        self.assertIsNone(self.entity.wgPhysics)

        self.ground_supported = True
        self.now += self.native.GROUND_SUPPORT_RETRY_SECONDS + 0.01
        attached = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assertTrue(attached["staging"])
        self.assertEqual("warmup", state["phase"])
        self.assertEqual((12.0, 3.0, -8.0), state["ground_support"])
        self.assertEqual("collision", state["ground_support_source"])
        self.assertIs(self.entity.wgPhysics, state["physics"])
        self.assertEqual(1, len([
            event for event in self.entity.events
            if event[0] == "setVehiclePhysics"
        ]))

        self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            self.now + 0.01,
        )
        self.assertEqual(1, len([
            event for event in self.entity.events
            if event[0] == "setVehiclePhysics"
        ]))

    def test_ground_support_probe_exception_fails_visible_native_contract(self):
        def fail_probe(*unused_args):
            raise RuntimeError("collision API rejected the probe")

        self.bigworld.wg_collideSegment = fail_probe
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("failed", state["phase"])
        self.assertEqual("error", state["ground_support_source"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertTrue(any(
            "ground support probe failed" in message
            for level, message in self.logs if level == "error"
        ))

    def test_ground_support_height_mismatch_fails_visible_native_contract(self):
        self.bigworld.wg_collideSegment = lambda *args: (
            Vector3(12.0, 7.0, -8.0),
        )
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("failed", state["phase"])
        self.assertEqual("mismatch", state["ground_support_source"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertTrue(any(
            "ground support height differs" in message
            for level, message in self.logs if level == "error"
        ))

    def test_nonfinite_ground_support_fails_visible_without_native_attach(self):
        self.bigworld.wg_collideSegment = lambda *args: (
            Vector3(12.0, float("nan"), -8.0),
        )
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("failed", state["phase"])
        self.assertEqual("invalid", state["ground_support_source"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertFalse(any(
            event[0] == "setVehiclePhysics" for event in self.entity.events
        ))
        self.assertTrue(any(
            "ground support" in message and "invalid" in message
            for level, message in self.logs if level == "error"
        ))

    def test_drive_signs_map_to_retail_physics_signal_bits(self):
        self.assertIsNotNone(self.activate())

        at = self.now + 0.10
        self.assertEqual(1, self.simulate(timestamp=at))
        self.native.step(
            self.player, self.mock, self.descriptor, -1, -1, 7,
            at,
        )

        self.assertEqual((-1, -1), self.entity.filter.input)
        self.assertEqual(6, self.entity.wgPhysics.movementSignals)

        self.assertTrue(self.native.hold(self.mock))
        self.assertEqual((0, 0), self.entity.filter.input)
        self.assertEqual(0, self.entity.wgPhysics.movementSignals)

    def test_small_heading_noise_does_not_toggle_digital_track_turn(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics

        at = self.now + 0.10
        self.assertEqual(1, self.simulate(timestamp=at))
        self.native.step(
            self.player, self.mock, self.descriptor, 1.0, 0.09, 7, at,
        )
        self.assertEqual(1, physics.movementSignals)

        at += 0.10
        self.assertEqual(1, self.simulate(timestamp=at))
        self.native.step(
            self.player, self.mock, self.descriptor, 1.0, -0.09, 7, at,
        )
        self.assertEqual(1, physics.movementSignals)

        at += 0.10
        self.assertEqual(1, self.simulate(timestamp=at))
        self.native.step(
            self.player, self.mock, self.descriptor, 1.0, 0.11, 7, at,
        )
        self.assertEqual(9, physics.movementSignals)

    def test_nonzero_input_wakes_a_body_frozen_during_countdown(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        self.assertTrue(self.native.hold(self.mock))
        physics.isFrozen = True
        physics.drive_history = []

        at = self.now + 0.10
        self.assertEqual(1, self.simulate(timestamp=at))
        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            at,
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

        at = self.now + 0.05
        self.assertEqual(1, self.simulate(timestamp=at))
        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            at,
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

        at = self.now + 0.10
        self.assertEqual(1, self.simulate(timestamp=at))
        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7,
            at,
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

    def test_active_native_owner_consumes_explicit_post_solve_output(self):
        self.assertIsNotNone(self.activate())
        self.entity.wgPhysics.body_position = Vector3(14.0, 4.0, -6.0)
        self.entity.wgPhysics.body_yaw = 0.8
        self.entity.wgPhysics.body_pitch = 0.2
        self.entity.wgPhysics.body_roll = -0.1
        self.entity.wgPhysics.movementSignals = 0
        at = self.now + 0.11
        self.assertEqual(1, self.simulate(timestamp=at))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            at,
        )

        self.assertEqual((14.0, 4.0, -6.0), result["position"])
        self.assertEqual(1, len(self.bridge_calls))

    def test_active_native_owner_fails_closed_on_mismatched_output_timestamp(self):
        self.assertIsNotNone(self.activate())
        output_at = self.now + 0.10
        self.assertEqual(1, self.simulate(timestamp=output_at))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            output_at + 0.01,
        )

        self.assertTrue(result["faulted"])
        self.assertEqual(
            "faulted", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertTrue(any(
            "entity matrix returned an invalid pose" in message
            for level, message in self.logs if level == "error"
        ))

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

        first_at = self.now + 0.05
        self.assertEqual(1, self.simulate(timestamp=first_at))
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            first_at,
        )
        second_at = self.now + 0.06
        self.assertEqual(1, self.simulate(timestamp=second_at))
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            second_at,
        )
        followup_at = self.now + 1.07
        self.assertEqual(1, self.simulate(timestamp=followup_at))
        self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7,
            followup_at,
        )

        diagnostics = [
            message for level, message in self.logs
            if level == "note" and "drive_diagnostic" in message
        ]
        self.assertEqual(1, len(diagnostics))
        diagnostic = diagnostics[0]
        self.assertIn("id=1000 signals_before=9", diagnostic)
        self.assertIn("signals_before=9 signals=9 repaired=False", diagnostic)
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
        self.assertIn("physics_root=", diagnostic)
        self.assertIn("entity_pose=", diagnostic)
        self.assertIn("root_delta=xz=", diagnostic)
        self.assertIn("entity_delta=xz=", diagnostic)
        self.assertIn("root_entity_gap=xz=", diagnostic)
        self.assertIn("sample_seconds=1.070", diagnostic)
        self.assertIn("left_contacts=4 right_contacts=5", diagnostic)
        self.assertIn("force=(1.000,2.000,3.000)", diagnostic)
        self.assertIn("torque=(4.000,5.000,6.000)", diagnostic)
        self.assertIn("speed=8.0 rspeed=0.2 longitudinal_speed=0.0", diagnostic)

    def test_active_filter_has_no_periodic_self_samples(self):
        self.assertIsNotNone(self.activate())
        activated_at = self.now

        for sample in range(1, 62):
            at = activated_at + sample * 0.11
            self.assertEqual(1, self.simulate(timestamp=at))
            self.native.step(
                self.player, self.mock, self.descriptor, 0, 0, 7,
                at,
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

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))

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
        self.assertEqual(0, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assert_failed_static(result, (12.0, 3.0, -8.0))

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
        self.assertEqual([], installed)

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
        self.assertEqual(0, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assert_failed_static(result, (12.0, 3.0, -8.0))

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
        self.output_invalid_ids.add(self.entity.id)
        self.assertEqual(0, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        self.assertTrue(any(
            "native Filter::output returned an invalid pose" in message
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

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assert_failed_static(result, (12.0, 3.0, -8.0))
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
        self.assertEqual(1, self.simulate())
        model.refuse_detach = True
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        self.assertEqual("failed", state["phase"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([self.old_servo], model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)
        self.assertIsNone(state["native_servo"])
        self.assertEqual([], collision_installs)

    def test_activation_rejects_silent_python_servo_detach_no_op(self):
        class SilentNoOpDetachModel(Model):
            def __init__(inner_self, motor=None):
                super(SilentNoOpDetachModel, inner_self).__init__(motor)
                inner_self.add_calls = []

            def addMotor(inner_self, motor):
                inner_self.add_calls.append(motor)
                super(SilentNoOpDetachModel, inner_self).addMotor(motor)

            def delMotor(inner_self, motor):
                return None

        model = SilentNoOpDetachModel(self.old_servo)
        self.mock._chassis_model = model

        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        self.assertFalse(self.native.is_active(self.mock))
        self.assertEqual("failed", state["phase"])
        self.assertIsNone(state["native_servo"])
        self.assertEqual([], model.add_calls)
        self.assertEqual([self.old_servo], model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)

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
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))
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
        self.entity.wgPhysics.body_position = Vector3(500, 0, 500)
        self.entity.wgPhysics.movementSignals = 0
        at = self.now + 0.1
        self.assertEqual(1, self.simulate(timestamp=at))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, at
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
        outputs = len(self.output_calls)

        self.assertEqual(1, self.native.simulate_frame(
            {self.mock.id: self.mock}, 0.02, first_at + 0.02,
        ))
        self.assertEqual(outputs, len(self.output_calls))
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
        self.entity.wgPhysics.body_position.z += 30.0
        at = self.now + 1.0
        self.assertEqual(1, self.simulate(timestamp=at))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            at
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
        self.assertIsNone(physics.callback("damageDestructibleCb"))
        self.assertEqual(0, physics.movementSignals)
        self.assertEqual([], self.mock._chassis_model.motors)
        self.assertFalse(hasattr(self.mock._chassis_model, "wg_fashion"))
        self.assertIsNone(self.mock._fashion)
        self.assertIsInstance(self.entity.filter, AvatarFilter)

    def test_successful_stop_clears_native_hazard_recovery_state(self):
        self.assertIsNotNone(self.activate())
        self.mock._offh_native_hazard_recovering = True
        self.mock._offh_native_hazard_anchor = (12.0, 3.0, -8.0)
        self.mock._offh_native_hazard_escape_endpoint = (20.0, 3.0, -8.0)
        self.mock._offh_native_hazard_safe_since = 17.5

        self.assertTrue(self.native.stop_mock(self.mock))

        self.assertFalse(getattr(
            self.mock, "_offh_native_hazard_recovering", False
        ))
        self.assertIsNone(getattr(
            self.mock, "_offh_native_hazard_anchor", None
        ))
        self.assertIsNone(getattr(
            self.mock, "_offh_native_hazard_escape_endpoint", None
        ))
        self.assertIsNone(getattr(
            self.mock, "_offh_native_hazard_safe_since", None
        ))

    def test_no_state_stop_clears_native_hazard_recovery_state(self):
        mock = types.SimpleNamespace(
            _offh_native_hazard_recovering=True,
            _offh_native_hazard_anchor=(4.0, 2.0, 8.0),
            _offh_native_hazard_escape_endpoint=(4.0, 2.0, -4.0),
            _offh_native_hazard_safe_since=17.5,
        )

        self.assertTrue(self.native.stop_mock(mock))

        self.assertFalse(getattr(
            mock, "_offh_native_hazard_recovering", False
        ))
        self.assertIsNone(getattr(
            mock, "_offh_native_hazard_anchor", None
        ))
        self.assertIsNone(getattr(
            mock, "_offh_native_hazard_escape_endpoint", None
        ))
        self.assertIsNone(getattr(
            mock, "_offh_native_hazard_safe_since", None
        ))

    def test_stopped_state_reuse_clears_stale_native_hazard_recovery_state(self):
        self.assertIsNotNone(self.activate())
        self.assertTrue(self.native.stop_mock(self.mock))
        old_state = getattr(self.mock, self.native.STATE_ATTR)
        self.mock._offh_native_hazard_recovering = True
        self.mock._offh_native_hazard_anchor = (12.0, 3.0, -8.0)
        self.mock._offh_native_hazard_escape_endpoint = (20.0, 3.0, -8.0)
        self.mock._offh_native_hazard_safe_since = 17.5
        self.now += 1.0

        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))

        self.assertIsNot(old_state, getattr(
            self.mock, self.native.STATE_ATTR
        ))
        self.assertFalse(getattr(
            self.mock, "_offh_native_hazard_recovering", False
        ))
        self.assertIsNone(getattr(
            self.mock, "_offh_native_hazard_anchor", None
        ))
        self.assertIsNone(getattr(
            self.mock, "_offh_native_hazard_escape_endpoint", None
        ))
        self.assertIsNone(getattr(
            self.mock, "_offh_native_hazard_safe_since", None
        ))

    def test_failed_stop_retains_native_hazard_recovery_state_for_retry(self):
        self.assertIsNotNone(self.activate())
        physics = self.entity.wgPhysics
        anchor = (12.0, 3.0, -8.0)
        endpoint = (20.0, 3.0, -8.0)
        safe_since = 17.5
        self.mock._offh_native_hazard_recovering = True
        self.mock._offh_native_hazard_anchor = anchor
        self.mock._offh_native_hazard_escape_endpoint = endpoint
        self.mock._offh_native_hazard_safe_since = safe_since
        physics.callback_clear_failures = 1

        self.assertFalse(self.native.stop_mock(self.mock, True))

        self.assertTrue(self.mock._offh_native_hazard_recovering)
        self.assertIs(anchor, self.mock._offh_native_hazard_anchor)
        self.assertIs(
            endpoint, self.mock._offh_native_hazard_escape_endpoint
        )
        self.assertEqual(
            safe_since, self.mock._offh_native_hazard_safe_since
        )
        self.assertEqual("faulted", getattr(
            self.mock, self.native.STATE_ATTR
        )["phase"])

    def test_seed_wait_demotion_reuses_the_existing_python_servo(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("seed_wait", state["phase"])
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)
        self.assertTrue(self.mock._servo_added)

    def test_warmup_demotion_reuses_the_existing_python_servo(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("warmup", state["phase"])
        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual([self.old_servo], self.mock._chassis_model.motors)
        self.assertIs(self.old_servo, self.mock._pose_servo)
        self.assertTrue(self.mock._servo_added)

    def test_active_demotion_replaces_native_servo_with_one_python_servo(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_servo = state["native_servo"]
        self.assertEqual([native_servo], self.mock._chassis_model.motors)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual(1, len(self.mock._chassis_model.motors))
        self.assertIs(
            self.mock._pose_servo,
            self.mock._chassis_model.motors[0],
        )
        self.assertIsNot(native_servo, self.mock._pose_servo)
        self.assertIsNone(state["native_servo"])
        self.assertTrue(self.mock._servo_added)

    def test_active_demotion_rejects_silent_native_servo_detach_no_op(self):
        class SilentNoOpDetachModel(Model):
            no_op_detach = False

            def __init__(inner_self, motor=None):
                super(SilentNoOpDetachModel, inner_self).__init__(motor)
                inner_self.add_calls = []

            def addMotor(inner_self, motor):
                inner_self.add_calls.append(motor)
                super(SilentNoOpDetachModel, inner_self).addMotor(motor)

            def delMotor(inner_self, motor):
                if inner_self.no_op_detach:
                    return None
                super(SilentNoOpDetachModel, inner_self).delMotor(motor)

        model = SilentNoOpDetachModel(self.old_servo)
        self.mock._chassis_model = model
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_servo = state["native_servo"]
        native_filter = state["filter"]
        native_physics = state["physics"]
        add_count_before_demotion = len(model.add_calls)
        model.no_op_detach = True

        self.assertFalse(self.native.stop_mock(self.mock, True))

        self.assertEqual("faulted", state["phase"])
        self.assertIs(native_servo, state["native_servo"])
        self.assertIs(native_servo, self.mock._native_pose_servo)
        self.assertIs(native_filter, state["filter"])
        self.assertIs(native_physics, state["physics"])
        self.assertIs(native_filter, self.entity.filter)
        self.assertIs(native_physics, self.entity.wgPhysics)
        self.assertEqual([native_servo], model.motors)
        self.assertEqual(add_count_before_demotion, len(model.add_calls))
        self.assertIsNone(self.mock._pose_servo)

    def test_fashion_detach_failure_retains_native_owners_for_demotion_retry(self):
        class FashionReleaseRetryModel(Model):
            def __init__(inner_self, motor=None):
                super(FashionReleaseRetryModel, inner_self).__init__(motor)
                inner_self.fashion_delete_failures = 1
                inner_self.fashion_delete_attempts = 0

            def __delattr__(inner_self, name):
                if name == "wg_fashion":
                    inner_self.fashion_delete_attempts += 1
                    if inner_self.fashion_delete_failures > 0:
                        inner_self.fashion_delete_failures -= 1
                        raise RuntimeError("fashion detach refused")
                super(FashionReleaseRetryModel, inner_self).__delattr__(name)

        model = FashionReleaseRetryModel(self.old_servo)
        self.mock._chassis_model = model
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_filter = state["filter"]
        native_physics = state["physics"]
        fashion = object()
        self.mock._fashion = fashion
        model.wg_fashion = fashion

        self.assertFalse(self.native.stop_mock(self.mock, True))

        self.assertEqual("faulted", state["phase"])
        self.assertIs(fashion, self.mock._fashion)
        self.assertIs(fashion, model.wg_fashion)
        self.assertIs(native_filter, state["filter"])
        self.assertIs(native_physics, state["physics"])
        self.assertIs(native_filter, self.entity.filter)
        self.assertIs(native_physics, self.entity.wgPhysics)
        self.assertEqual(1, model.fashion_delete_attempts)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual("stopped", state["phase"])
        self.assertIsNone(self.mock._fashion)
        self.assertFalse(hasattr(model, "wg_fashion"))
        self.assertIsNone(state["filter"])
        self.assertIsNone(state["physics"])
        self.assertIsNone(state["entity_provider"])
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual(2, model.fashion_delete_attempts)

    def test_filter_release_retry_does_not_add_a_second_python_servo(self):
        class CountingModel(Model):
            def __init__(inner_self, motor=None):
                super(CountingModel, inner_self).__init__(motor)
                inner_self.add_calls = []

            def addMotor(inner_self, motor):
                inner_self.add_calls.append(motor)
                super(CountingModel, inner_self).addMotor(motor)

        entity = FilterReleaseRetryEntity(901)
        model = CountingModel(self.old_servo)
        self.entity = entity
        self.mock.bw_entity = entity
        self.mock._chassis_model = model
        self.assertIsNotNone(self.activate())
        entity.avatar_filter_failures = 1

        self.assertFalse(self.native.stop_mock(self.mock, True))

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("faulted", state["phase"])
        self.assertIsNone(state["native_servo"])
        self.assertEqual(1, len(model.motors))
        self.assertIs(self.mock._pose_servo, model.motors[0])
        add_count_after_failure = len(model.add_calls)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual(add_count_after_failure, len(model.add_calls))
        self.assertEqual(1, len(model.motors))
        self.assertIs(self.mock._pose_servo, model.motors[0])
        self.assertTrue(self.mock._servo_added)
        self.assertEqual("stopped", state["phase"])

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

        self.assertFalse(self.native.stop_mock(self.mock))

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
        self.assertFalse(self.native.stop_mock(self.mock))

        self.assertFalse(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now + 1.0
        ))
        self.assertIs(old_state, getattr(self.mock, self.native.STATE_ATTR))
        self.assertIs(native_servo, old_state["native_servo"])
        self.assertEqual([native_servo], model.motors)
        self.assertEqual("faulted", old_state["phase"])

        model.fail_detach = False
        self.assertTrue(self.native.stop_mock(self.mock))
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

    def test_stopped_mock_step_rebuilds_native_state_after_promotion(self):
        self.assertIsNotNone(self.activate())
        old_state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.now += 1.0
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        new_state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertIsNot(old_state, new_state)
        self.assertEqual("seed_wait", new_state["phase"])
        self.assertTrue(result["staging"])

    def test_promoted_relay_stages_existing_fashion_for_new_filter(self):
        fashion = types.SimpleNamespace(
            movementInfo=object(), physicsInfo=object(),
            placingCompensationMatrix=object(),
        )
        self.mock._fashion = fashion
        self.mock._chassis_model.wg_fashion = fashion

        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))

        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertIs(fashion, state["pending_fashion"])

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
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, -1, -1, 7, self.now
        ))

        self.assertIsNot(first_physics, second_physics)
        self.assertEqual(6, second_physics.movementSignals)
        self.assertFalse(second_physics.isFrozen)

    def test_stop_all_retains_global_owner_until_every_body_releases(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        simulator = self.native._DYNAMICS_SIMULATOR[0]
        counters = dict(self.native._COUNTERS)
        original_stop = self.native.stop_mock
        attempts = []

        def fail_once(mock, restore_filter=False):
            attempts.append(mock)
            if len(attempts) == 1:
                state["phase"] = "faulted"
                return False
            return original_stop(mock, restore_filter)

        self.native.stop_mock = fail_once

        self.assertEqual(-1, self.native.stop_all({self.mock.id: self.mock}))

        self.assertIs(simulator, self.native._DYNAMICS_SIMULATOR[0])
        self.assertEqual(counters, self.native._COUNTERS)
        self.assertIsNotNone(state["filter"])
        self.assertIsNotNone(state["physics"])

        self.assertEqual(1, self.native.stop_all({self.mock.id: self.mock}))
        self.assertIsNone(self.native._DYNAMICS_SIMULATOR[0])

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
            _offh_native_model_root_ready=True,
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
        self.assertEqual(29, self.native.simulate_frame(
            lineup, 0.02, attach_at + 0.050
        ))

        activate_at = attach_at + 0.028 + self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(29, self.native.simulate_frame(
            lineup, 0.02, activate_at
        ))
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
        self.assertEqual(2, self.native.simulate_frame(
            lineup, 0.02, attach_at + 0.04,
        ))
        first_active_at = attach_at + self.native.WARMUP_SECONDS + 0.02
        self.assertEqual(2, self.native.simulate_frame(
            lineup, 0.02, first_active_at,
        ))
        self.assertIsNotNone(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            first_active_at,
        ))
        fault_at = first_active_at + 0.10
        self.entity.wgPhysics.body_position = Vector3(500.0, 0.0, 500.0)
        self.assertEqual(2, self.native.simulate_frame(
            lineup, 0.02, fault_at,
        ))
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7,
            fault_at,
        )["faulted"])
        second_active_at = first_active_at + 0.20
        self.assertEqual(2, self.native.simulate_frame(
            lineup, 0.02, second_active_at,
        ))
        self.assertIsNotNone(self.native.step(
            self.player, other, self.descriptor, 0, 0, 7,
            second_active_at,
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
        self.assertEqual(1, self.simulate())

        def reject_input(unused_movement, unused_rotation):
            raise RuntimeError("input readback failed")

        self.entity.filter.notifyInputKeysDown = reject_input
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))
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
        native_servo = ("servo", self.mock.matrix)
        self.assertEqual([native_servo], self.mock._chassis_model.motors)
        self.assertEqual(native_servo, state["native_servo"])
        self.assertTrue(self.entity.matrix.notModel)

        fashion = types.SimpleNamespace()
        self.assertTrue(self.native.bind_fashion(self.mock, fashion))
        self.assertIs(fashion.placingCompensationMatrix,
                      self.entity.filter.placingCompensationMatrix)
        self.assertIs(fashion.physicsInfo, self.entity.filter.physicsInfo)
        self.assertIs(fashion.movementInfo, self.entity.filter.movementInfo)

    def test_silent_fashion_provider_no_op_fails_and_preserves_old_providers(self):
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_servo = state["native_servo"]
        native_filter = state["filter"]
        native_physics = state["physics"]
        old_placing = object()
        old_physics = object()
        old_movement = object()

        class SilentNoOpFashion(object):
            def __init__(inner_self):
                object.__setattr__(
                    inner_self, "placingCompensationMatrix", old_placing
                )
                object.__setattr__(inner_self, "physicsInfo", old_physics)
                object.__setattr__(inner_self, "movementInfo", old_movement)

            def __setattr__(inner_self, name, value):
                if name in (
                    "placingCompensationMatrix", "physicsInfo", "movementInfo"
                ):
                    return
                object.__setattr__(inner_self, name, value)

        fashion = SilentNoOpFashion()
        self.mock._fashion = fashion
        self.mock._chassis_model.wg_fashion = fashion

        self.assertFalse(self.native.bind_fashion(self.mock, fashion))

        self.assertEqual("faulted", state["phase"])
        self.assertFalse(state["fashion_detach_blocked"])
        self.assertIs(old_placing, fashion.placingCompensationMatrix)
        self.assertIs(old_physics, fashion.physicsInfo)
        self.assertIs(old_movement, fashion.movementInfo)
        self.assertIs(native_servo, state["native_servo"])
        self.assertEqual([native_servo], self.mock._chassis_model.motors)
        self.assertIs(native_filter, self.entity.filter)
        self.assertIs(native_physics, self.entity.wgPhysics)

    def test_silent_fashion_rollback_with_detach_refusal_keeps_retryable_owner(self):
        model = FirstFashionDetachRefusalModel(self.old_servo)
        self.mock._chassis_model = model
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        native_servo = state["native_servo"]
        native_filter = state["filter"]
        native_physics = state["physics"]
        old_placing = object()
        old_physics = object()
        old_movement = object()

        class PartialSilentFashion(object):
            def __init__(inner_self):
                object.__setattr__(
                    inner_self, "placingCompensationMatrix", old_placing
                )
                object.__setattr__(inner_self, "physicsInfo", old_physics)
                object.__setattr__(inner_self, "movementInfo", old_movement)
                object.__setattr__(inner_self, "placing_bound", False)

            def __setattr__(inner_self, name, value):
                if name == "placingCompensationMatrix":
                    if inner_self.placing_bound:
                        return
                    object.__setattr__(inner_self, name, value)
                    object.__setattr__(inner_self, "placing_bound", True)
                    return
                if name == "physicsInfo":
                    return
                object.__setattr__(inner_self, name, value)

        fashion = PartialSilentFashion()
        self.mock._fashion = fashion
        model.wg_fashion = fashion

        self.assertFalse(self.native.bind_fashion(self.mock, fashion))

        self.assertEqual("faulted", state["phase"])
        self.assertTrue(state["fashion_detach_blocked"])
        self.assertIs(fashion, self.mock._fashion)
        self.assertIs(fashion, model.wg_fashion)
        self.assertIs(
            native_filter.placingCompensationMatrix,
            fashion.placingCompensationMatrix,
        )
        self.assertIs(old_physics, fashion.physicsInfo)
        self.assertIs(old_movement, fashion.movementInfo)
        self.assertIs(native_servo, state["native_servo"])
        self.assertEqual([native_servo], model.motors)
        self.assertIs(native_filter, self.entity.filter)
        self.assertIs(native_physics, self.entity.wgPhysics)
        self.assertEqual(1, model.fashion_delete_attempts)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual("stopped", state["phase"])
        self.assertFalse(hasattr(model, "wg_fashion"))
        self.assertIsNone(self.mock._fashion)
        self.assertIsNone(state["native_servo"])
        self.assertIsNone(state["filter"])
        self.assertIsNone(state["physics"])
        self.assertEqual(2, model.fashion_delete_attempts)
        self.assertEqual(1, len(model.motors))
        self.assertIs(self.mock._pose_servo, model.motors[0])

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
        self.assertEqual(1, self.simulate())

        self.assertTrue(self.native.bind_fashion(self.mock, fashion))
        self.assertIs(movement, fashion.movementInfo)
        self.assertIs(physics, fashion.physicsInfo)
        self.assertIs(placing, fashion.placingCompensationMatrix)
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))
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

    def test_pending_broken_fashion_never_reports_native_activation(self):
        class BrokenFashion(object):
            def __setattr__(self, name, value):
                raise RuntimeError("provider rejected")

        fashion = BrokenFashion()
        self.mock._fashion = fashion
        self.mock._chassis_model.wg_fashion = fashion
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 1, 0, 7, self.now
        )

        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        state = getattr(self.mock, self.native.STATE_ATTR)
        self.assertEqual("failed", state["phase"])
        self.assertFalse(state["counted_active"])
        self.assertEqual(0, self.native._COUNTERS["activated"])
        self.assertEqual(0, self.native._COUNTERS["active"])
        self.assertEqual(1, self.native._COUNTERS["failed"])
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertIsNone(self.entity.wgPhysics)
        self.assertIsNone(state["native_servo"])
        self.assertEqual(
            [self.mock._pose_servo], self.mock._chassis_model.motors
        )
        self.assertIsNot(self.old_servo, self.mock._pose_servo)
        self.assertTrue(any(
            "fashion bind failed" in message
            for level, message in self.logs if level == "error"
        ))

    def test_pending_fashion_rollback_and_detach_failure_keeps_retryable_owner(self):
        model = FirstFashionDetachRefusalModel(self.old_servo)
        fashion = ProviderRollbackFailureFashion()
        self.mock._chassis_model = model
        self.mock._fashion = fashion
        model.wg_fashion = fashion
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.assertEqual(1, self.simulate())
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )

        state = getattr(self.mock, self.native.STATE_ATTR)
        native_servo = state["native_servo"]
        native_filter = state["filter"]
        native_physics = state["physics"]
        self.assertTrue(result["faulted"])
        self.assertEqual(0.0, result["velocity"])
        self.assertEqual(0.0, result["turn_velocity"])
        self.assertEqual((12.0, 3.0, -8.0), result["position"])
        self.assertEqual("faulted", state["phase"])
        self.assertTrue(state["fashion_detach_blocked"])
        self.assertIs(fashion, state["pending_fashion"])
        self.assertIs(fashion, self.mock._fashion)
        self.assertIs(fashion, model.wg_fashion)
        self.assertIs(native_servo, state["native_servo"])
        self.assertEqual([native_servo], model.motors)
        self.assertIs(native_filter, self.entity.filter)
        self.assertIs(native_physics, self.entity.wgPhysics)
        self.assertEqual(1, model.fashion_delete_attempts)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual("stopped", state["phase"])
        self.assertFalse(hasattr(model, "wg_fashion"))
        self.assertIsNone(self.mock._fashion)
        self.assertIsNone(state["native_servo"])
        self.assertIsNone(state["filter"])
        self.assertIsNone(state["physics"])
        self.assertEqual(2, model.fashion_delete_attempts)
        self.assertEqual(1, len(model.motors))
        self.assertIs(self.mock._pose_servo, model.motors[0])

    def test_active_fashion_rollback_and_detach_failure_keeps_retryable_owner(self):
        model = FirstFashionDetachRefusalModel(self.old_servo)
        self.mock._chassis_model = model
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        fashion = ProviderRollbackFailureFashion()
        self.mock._fashion = fashion
        model.wg_fashion = fashion

        self.assertFalse(self.native.bind_fashion(self.mock, fashion))

        native_servo = state["native_servo"]
        native_filter = state["filter"]
        native_physics = state["physics"]
        self.assertEqual("faulted", state["phase"])
        self.assertTrue(state["fashion_detach_blocked"])
        self.assertIs(fashion, self.mock._fashion)
        self.assertIs(fashion, model.wg_fashion)
        self.assertIs(native_servo, state["native_servo"])
        self.assertEqual([native_servo], model.motors)
        self.assertIs(native_filter, self.entity.filter)
        self.assertIs(native_physics, self.entity.wgPhysics)
        self.assertEqual(1, model.fashion_delete_attempts)

        self.assertTrue(self.native.stop_mock(self.mock, True))

        self.assertEqual("stopped", state["phase"])
        self.assertFalse(hasattr(model, "wg_fashion"))
        self.assertIsNone(self.mock._fashion)
        self.assertIsNone(state["native_servo"])
        self.assertIsNone(state["filter"])
        self.assertIsNone(state["physics"])
        self.assertEqual(2, model.fashion_delete_attempts)
        self.assertEqual(1, len(model.motors))
        self.assertIs(self.mock._pose_servo, model.motors[0])

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
        self.entity.wgPhysics.body_position.x += 3.0
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(0, self.simulate(timestamp=self.now))

        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assert_failed_static(result, (12.0, 3.0, -8.0))

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
        self.assertEqual(1, self.simulate())
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertEqual(1, self.simulate(timestamp=self.now))
        result = self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )
        self.assert_failed_static(result, (12.0, 3.0, -8.0))
        self.assertEqual(
            "failed", getattr(self.mock, self.native.STATE_ATTR)["phase"]
        )
        self.assertIsInstance(self.entity.filter, AvatarFilter)
        self.assertEqual([], installed)

    def test_loader_injects_manager_before_offline_battle(self):
        source = LOADER_PATH.read_text(encoding="utf-8")
        manager = source.index("'native_bot_physics'")
        battle = source.index("'offline_battle'", manager)
        self.assertLess(manager, battle)

    def test_live_pymodel_obstacle_is_only_installed_for_fallback(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        spawn = source.index("def _install_live_collision_obstacle")
        required = source.index("_native_body_required =", spawn)
        native_prepare = source.index("_native_body_prepared =", required)
        fallback = source.index(
            "if not _native_body_prepared and not _native_body_required:",
            native_prepare,
        )
        obstacle_call = source.index(
            "_install_live_collision_obstacle()", fallback
        )
        native_branch = source.index("else:", obstacle_call)
        clear_proxy = source.index(
            "_e_mock._collision_obstacle = None", native_branch
        )

        self.assertLess(required, fallback)
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

    def test_central_kill_waits_for_native_owner_release_before_wreck_swap(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        wrapper_start = source.index("class _KillEventWrapper(object):")
        wrapper_end = source.index(
            "player.arena.onVehicleKilled = _KillEventWrapper", wrapper_start
        )
        namespace = {}
        retry_start = source.index("def _offh_wreck_release_or_retry(")
        retry_end = source.index(
            "\n\ndef _offh_native_failed_pose", retry_start
        )
        exec(source[retry_start:retry_end], namespace)
        exec(textwrap.dedent(source[wrapper_start:wrapper_end]), namespace)

        callbacks = []
        created_models = []
        added_models = []
        release_calls = []

        class PoseMatrix(object):
            def __init__(self):
                self.translation = None
                self.rotation = None

            def setRotateYPR(self, rotation):
                self.rotation = rotation

        class Position(object):
            def __init__(self, x, y=None, z=None):
                if y is None and z is None:
                    x, y, z = x.x, x.y, x.z
                self.x = float(x)
                self.y = float(y)
                self.z = float(z)

        class Node(object):
            def attach(self, model):
                pass

        class WreckModel(object):
            def __init__(self, path):
                self.path = path
                self.loaded = True
                self.visible = True
                self.visibleAttachments = True

            def addMotor(self, motor):
                self.motor = motor

            def node(self, name, matrix=None):
                return Node()

        old_chassis = WreckModel("live")
        old_chassis.position = Position(4.0, 2.0, 8.0)
        old_chassis.yaw = 0.25
        old_chassis.pitch = 0.05
        old_chassis.roll = -0.03
        descriptor = types.SimpleNamespace(
            chassis={"models": {"destroyed": "wreck/chassis"}},
            hull={"models": {"destroyed": "wreck/hull"}},
            turret={"models": {"destroyed": "wreck/turret"}},
            gun={"models": {"destroyed": "wreck/gun"}},
        )
        mock = types.SimpleNamespace(
            id=1016,
            health=500,
            isAlive=True,
            position=old_chassis.position,
            matrix=PoseMatrix(),
            _chassis_model=old_chassis,
            typeDescriptor=descriptor,
            bw_entity=None,
            _turret_yaw=0.1,
            _gun_pitch=-0.05,
        )
        arena = types.SimpleNamespace(vehicles={1016: {"isAlive": True}})
        player = types.SimpleNamespace(arena=arena, playerVehicleID=1)

        bigworld = types.ModuleType("BigWorld")
        bigworld.player = lambda: player
        bigworld.Model = lambda path: (
            created_models.append(WreckModel(path)) or created_models[-1]
        )
        bigworld.Servo = lambda provider: ("servo", provider)
        bigworld.delModel = lambda model: None
        bigworld.wg_collideSegment = lambda *args: None
        math_module = types.ModuleType("Math")
        math_module.Vector3 = Position
        math_module.Matrix = PoseMatrix
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module

        def release_native_for_wreck(target):
            release_calls.append(target)
            return len(release_calls) > 1

        namespace.update({
            "G_MOCK_VEHICLES": {1016: mock},
            "_offh_release_native_for_wreck": release_native_for_wreck,
            "_offh_set_alive": lambda target, alive: setattr(
                target, "isAlive", alive
            ),
            "_offh_refresh_team_score": lambda unused_player: None,
            "_offh_battle_callback": lambda delay, callback: callbacks.append(
                (delay, callback)
            ),
            "_offh_hp_display": lambda target: target.health,
            "_offh_bspace": lambda: 7,
            "_add_model": lambda model: added_models.append(model),
            "_play_death_effect": lambda *args: None,
            "LOG_DEBUG": lambda *args: None,
            "LOG_ERROR": lambda *args: None,
        })
        wrapper = namespace["_KillEventWrapper"](None)

        wrapper(1016, 1, 2)

        self.assertEqual([mock], release_calls)
        self.assertFalse(getattr(mock, "_wreck_done", False))
        self.assertEqual(0, len(created_models))
        self.assertEqual([], added_models)
        self.assertIs(old_chassis, mock._chassis_model)
        retries = [
            callback for delay, callback in callbacks
            if (abs(delay - 0.1) < 0.0001 and
                callback.__name__ == "_fire_wreck_swap")
        ]
        self.assertEqual(1, len(retries))

        callbacks[:] = [
            item for item in callbacks if item[1] is not retries[0]
        ]
        retries[0]()

        load_callbacks = [
            callback for delay, callback in callbacks
            if (abs(delay - 0.1) < 0.0001 and
                callback.__name__ == "_fire_wreck_swap")
        ]
        self.assertEqual([mock, mock], release_calls)
        self.assertTrue(mock._wreck_done)
        self.assertEqual(4, len(created_models))
        self.assertEqual([], added_models)
        self.assertIs(old_chassis, mock._chassis_model)
        self.assertEqual(1, len(load_callbacks))

        callbacks[:] = [
            item for item in callbacks if item[1] is not load_callbacks[0]
        ]
        load_callbacks[0]()

        self.assertEqual([mock, mock], release_calls)
        self.assertIs(created_models[0], mock._chassis_model)
        self.assertEqual([created_models[0]], added_models)

        wrapper(1016, 1, 2)

        self.assertEqual(4, len(created_models))
        self.assertEqual([created_models[0]], added_models)

    def test_drowned_native_bot_releases_owner_before_preserving_live_model(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        wrapper_start = source.index("class _KillEventWrapper(object):")
        wrapper_end = source.index(
            "player.arena.onVehicleKilled = _KillEventWrapper", wrapper_start
        )
        retry_start = source.index("def _offh_wreck_release_or_retry(")
        retry_end = source.index(
            "\n\ndef _offh_native_failed_pose", retry_start
        )
        namespace = {}
        exec(source[retry_start:retry_end], namespace)
        exec(textwrap.dedent(source[wrapper_start:wrapper_end]), namespace)

        callbacks = []
        created_models = []
        release_calls = []
        chassis = types.SimpleNamespace(
            position=Vector3(4.0, 2.0, 8.0),
            yaw=0.25,
            pitch=0.05,
            roll=-0.03,
        )
        descriptor = types.SimpleNamespace(
            chassis={"models": {"destroyed": "wreck/chassis"}},
            hull={"models": {"destroyed": "wreck/hull"}},
            turret={"models": {"destroyed": "wreck/turret"}},
            gun={"models": {"destroyed": "wreck/gun"}},
        )
        mock = types.SimpleNamespace(
            id=1016,
            health=500,
            isAlive=True,
            _drowned=True,
            position=chassis.position,
            _chassis_model=chassis,
            typeDescriptor=descriptor,
        )
        arena = types.SimpleNamespace(vehicles={1016: {"isAlive": True}})
        player = types.SimpleNamespace(arena=arena, playerVehicleID=1)
        bigworld = types.ModuleType("BigWorld")
        bigworld.player = lambda: player
        bigworld.Model = lambda path: (
            created_models.append(path) or types.SimpleNamespace(path=path)
        )
        sys.modules["BigWorld"] = bigworld

        def release_native(target):
            release_calls.append(target)
            return len(release_calls) > 1

        namespace.update({
            "G_MOCK_VEHICLES": {1016: mock},
            "_offh_release_native_for_wreck": release_native,
            "_offh_set_alive": lambda target, alive: setattr(
                target, "isAlive", alive
            ),
            "_offh_refresh_team_score": lambda unused_player: None,
            "_offh_battle_callback": lambda delay, callback: callbacks.append(
                (delay, callback)
            ),
            "_offh_hp_display": lambda target: target.health,
            "_play_death_effect": lambda *args: None,
            "LOG_DEBUG": lambda *args: None,
            "LOG_ERROR": lambda *args: None,
        })
        wrapper = namespace["_KillEventWrapper"](None)

        wrapper(1016, -1, 5)

        self.assertEqual([mock], release_calls)
        self.assertFalse(getattr(mock, "_wreck_done", False))
        self.assertIs(chassis, mock._chassis_model)
        self.assertEqual([], created_models)
        retries = [
            callback for delay, callback in callbacks
            if abs(delay - 0.1) < 0.0001
        ]
        self.assertEqual(1, len(retries))

        callbacks[:] = [
            item for item in callbacks if item[1] is not retries[0]
        ]
        retries[0]()

        self.assertEqual([mock, mock], release_calls)
        self.assertTrue(mock._wreck_done)
        self.assertIs(chassis, mock._chassis_model)
        self.assertEqual([], created_models)
        self.assertEqual([], [
            callback for delay, callback in callbacks
            if abs(delay - 0.1) < 0.0001
        ])

    def test_native_destructibles_bypass_legacy_proximity_tree_scan(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        native_start = source.index("if _native_body_pose is not None:")
        legacy_start = source.index(
            "elif _python_movement_allowed:", native_start
        )
        native_branch = source[native_start:legacy_start]
        pose_commit = source.index(
            "if _native_body_pose is not None:", legacy_start
        )
        legacy_branch = source[legacy_start:pose_commit]
        player_start = source.index("# Apply position")
        player_end = source.index("# Tank-vs-tank:", player_start)
        player_movement = source[player_start:player_end]

        self.assertNotIn("_fell_trees_near", native_branch)
        self.assertIn("_fell_trees_near", legacy_branch)
        self.assertIn("_fell_trees_near", player_movement)

    def test_kill_ui_failure_cannot_skip_canonical_event_or_native_release(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        wrapper_start = source.index("class _KillEventWrapper(object):")
        wrapper_end = source.index(
            "player.arena.onVehicleKilled = _KillEventWrapper", wrapper_start
        )
        retry_start = source.index("def _offh_wreck_release_or_retry(")
        retry_end = source.index(
            "\n\ndef _offh_native_failed_pose", retry_start
        )
        namespace = {}
        exec(source[retry_start:retry_end], namespace)
        exec(textwrap.dedent(source[wrapper_start:wrapper_end]), namespace)

        original_events = []
        release_calls = []
        callbacks = []
        position = types.SimpleNamespace(x=4.0, y=2.0, z=8.0)
        chassis = types.SimpleNamespace(
            position=position, yaw=0.25, pitch=0.0, roll=0.0
        )
        descriptor = types.SimpleNamespace(
            chassis={"models": {"destroyed": "wreck/chassis"}},
            hull={"models": {"destroyed": "wreck/hull"}},
            turret={"models": {"destroyed": "wreck/turret"}},
            gun={"models": {"destroyed": "wreck/gun"}},
        )
        mock = types.SimpleNamespace(
            id=1016,
            health=500,
            isAlive=True,
            _chassis_model=chassis,
            typeDescriptor=descriptor,
            position=position,
            _turret_yaw=0.0,
            _gun_pitch=0.0,
        )
        arena = types.SimpleNamespace(vehicles={1016: {"isAlive": True}})
        player = types.SimpleNamespace(arena=arena, playerVehicleID=1)
        bigworld = types.ModuleType("BigWorld")
        bigworld.player = lambda: player
        bigworld.Model = lambda path: types.SimpleNamespace(
            path=path, loaded=True
        )
        sys.modules["BigWorld"] = bigworld

        def release_native(target):
            release_calls.append(target)
            return True

        def fail_score_ui(unused_player):
            raise RuntimeError("score UI unavailable")

        namespace.update({
            "G_MOCK_VEHICLES": {1016: mock},
            "_offh_release_native_for_wreck": release_native,
            "_offh_set_alive": lambda target, alive: setattr(
                target, "isAlive", alive
            ),
            "_offh_refresh_team_score": fail_score_ui,
            "_offh_battle_callback": lambda delay, callback: callbacks.append(
                (delay, callback)
            ),
            "_offh_hp_display": lambda target: target.health,
            "_play_death_effect": lambda *args: None,
            "LOG_DEBUG": lambda *args: None,
        })
        wrapper = namespace["_KillEventWrapper"](
            lambda *args: original_events.append(args)
        )

        wrapper(1016, 1, 2)

        self.assertEqual([(1016, 1, 2)], original_events)
        self.assertEqual([mock], release_calls)
        self.assertTrue(mock._wreck_done)
        self.assertEqual(1, len([
            callback for delay, callback in callbacks
            if (abs(delay - 0.1) < 0.0001 and
                callback.__name__ == "_fire_wreck_swap")
        ]))

    def test_central_kill_transaction_is_idempotent_and_owns_frag_credit(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        wrapper_start = source.index("class _KillEventWrapper(object):")
        wrapper_end = source.index(
            "player.arena.onVehicleKilled = _KillEventWrapper", wrapper_start
        )
        namespace = {}
        exec(textwrap.dedent(source[wrapper_start:wrapper_end]), namespace)

        original_events = []
        score_calls = []
        callbacks = []
        stats_updates = []
        team_killers = []
        vehicles = {
            1: {"team": 1, "isAlive": True, "frags": 99, "name": "Killer"},
            101: {"team": 2, "isAlive": True, "name": "Enemy A"},
            102: {"team": 2, "isAlive": True, "name": "Enemy B"},
            103: {"team": 1, "isAlive": True, "name": "Ally"},
        }
        arena = types.SimpleNamespace(
            vehicles=vehicles,
            statistics={1: {"frags": 99}},
            onVehicleStatisticsUpdate=lambda vehicle_id: stats_updates.append(
                vehicle_id
            ),
            onTeamKiller=lambda vehicle_id: team_killers.append(vehicle_id),
        )
        player = types.SimpleNamespace(
            arena=arena, playerVehicleID=1, _offhangar_team=1,
        )
        mocks = {}
        for vehicle_id in (101, 102, 103):
            mocks[vehicle_id] = types.SimpleNamespace(
                id=vehicle_id, health=500, isAlive=True,
                _chassis_model=None,
            )
        bigworld = types.ModuleType("BigWorld")
        bigworld.player = lambda: player
        sys.modules["BigWorld"] = bigworld
        namespace.update({
            "G_MOCK_VEHICLES": mocks,
            "_offh_set_alive": lambda target, alive: setattr(
                target, "isAlive", alive
            ),
            "_offh_refresh_team_score": lambda unused: score_calls.append(
                tuple(info["isAlive"] for info in vehicles.values())
            ),
            "_offh_battle_callback": lambda delay, callback: callbacks.append(
                (delay, callback)
            ),
            "_offh_hp_display": lambda target: target.health,
            "LOG_DEBUG": lambda *args: None,
        })
        wrapper = namespace["_KillEventWrapper"](
            lambda *args: original_events.append(args)
        )

        wrapper(101, 1, 0)
        wrapper(101, 1, 0)
        wrapper(102, 1, 0)
        wrapper(103, 1, 0)

        self.assertEqual([
            (101, 1, 0), (102, 1, 0), (103, 1, 0),
        ], original_events)
        self.assertEqual(1, vehicles[1]["frags"])
        self.assertEqual(1, arena.statistics[1]["frags"])
        self.assertEqual([1, 1, 1], stats_updates)
        self.assertEqual([1], team_killers)
        self.assertEqual(3, len(score_calls))
        self.assertEqual(3, len([
            item for item in callbacks if abs(item[0]) < 0.0001
        ]))
        for mock in mocks.values():
            self.assertTrue(mock._network_death_notified)

    def test_weapon_paths_do_not_mutate_frags_outside_central_wrapper(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        wrapper = source.index("class _KillEventWrapper(object):")
        weapon_start = source.index("def _resolve_bot_projectile_hit(")
        weapon_end = source.index("def _mock_shoot()", weapon_start)
        player_death = source[source.index("LOG_DEBUG('Player is dead."):weapon_start]
        weapon_paths = source[weapon_start:weapon_end]

        self.assertNotIn("['frags'] =", player_death)
        self.assertNotIn("['frags'] =", weapon_paths)
        self.assertNotIn("player.onVehicleKilled", weapon_paths)
        self.assertNotIn("updateFrags", weapon_paths)
        self.assertIn("_offh_canonical_frags", source[wrapper:])

    def test_team_score_uses_canonical_retail_frag_totals(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        start = source.index("def _offh_team_score(player):")
        end = source.index("\n\ndef _offh_refresh_team_score", start)
        namespace = {}
        exec(source[start:end], namespace)
        vehicles = {}
        statistics = {}
        for vehicle_id in range(1, 31):
            team = 1 if vehicle_id <= 15 else 2
            vehicles[vehicle_id] = {
                "team": team,
                "isAlive": True,
                "frags": 9 if vehicle_id == 16 else 0,
            }
            statistics[vehicle_id] = {"frags": 0}
        vehicles[1]["isAlive"] = False
        vehicles[2]["isAlive"] = False
        vehicles[16]["isAlive"] = False
        vehicles[17]["isAlive"] = False
        statistics[3]["frags"] = 1
        statistics[4]["frags"] = 1
        statistics[18]["frags"] = 1
        statistics[19]["frags"] = 2
        statistics[20]["frags"] = -1
        player = types.SimpleNamespace(
            arena=types.SimpleNamespace(
                vehicles=vehicles, statistics=statistics,
            ),
            _offhangar_team=1,
        )

        self.assertEqual((2, 2), namespace["_offh_team_score"](player))

    def test_bot_projectile_kill_delegates_wreck_swap_to_central_wrapper(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        bot_start = source.index("def _resolve_bot_projectile_hit(")
        player_start = source.index(
            "def _resolve_player_projectile_hit(", bot_start
        )
        weapon_path = source[bot_start:player_start]

        self.assertIn("arena.onVehicleKilled", weapon_path)
        self.assertNotIn("_wreck_done = True", weapon_path)
        self.assertNotIn("def _swap_destroyed_model_bot", weapon_path)
        self.assertNotIn(
            "_offh_battle_callback(0.1, _swap_destroyed_model_bot",
            weapon_path,
        )

    def test_player_projectile_kill_delegates_wreck_swap_to_central_wrapper(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        player_start = source.index("def _resolve_player_projectile_hit(")
        shoot_start = source.index("def _mock_shoot():", player_start)
        weapon_path = source[player_start:shoot_start]

        self.assertIn("arena.onVehicleKilled", weapon_path)
        self.assertNotIn("_wreck_done = True", weapon_path)
        self.assertNotIn("def _swap_destroyed_model(", weapon_path)
        self.assertNotIn(
            "_offh_battle_callback(0.0, _swap_destroyed_model",
            weapon_path,
        )

    def test_perf_report_includes_native_physics_cost(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        ordered = source.index("ordered = (")
        report = source.index("parts = []", ordered)

        self.assertIn("'native_simulation'", source[ordered:report])
        self.assertIn("'native_physics'", source[ordered:report])

    def test_presentation_jump_classifier_separates_root_model_and_hp_pulses(self):
        base = {
            "physics": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            "mock": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            "chassis": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            "hp": (0.0, 4.0, 0.0, 0.0, 0.0, 0.0),
            "hp_local": (0.0, 4.0, 0.0),
            "placing": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        }
        canonical = dict(base)
        for key in ("physics", "mock", "chassis"):
            canonical[key] = (5.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        canonical["hp"] = (5.0, 4.0, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(
            ["canonical_root"],
            self.native._presentation_jump_reasons(
                base, canonical, speed=2.0, sample_gap=0.05,
            ),
        )

        hp_only = dict(base)
        hp_only["hp_local"] = (3.0, 4.0, 0.0)
        self.assertEqual(
            ["hp_gui_split"],
            self.native._presentation_jump_reasons(
                base, hp_only, speed=0.0, sample_gap=0.05,
            ),
        )

        chassis_only = dict(base)
        chassis_only["chassis"] = (2.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(
            ["chassis_root_split"],
            self.native._presentation_jump_reasons(
                base, chassis_only, speed=0.0, sample_gap=0.05,
            ),
        )

        one_frame_turn_lag = dict(base)
        one_frame_turn_lag["chassis"] = (
            0.0, 0.0, 0.0, 0.04, 0.0, 0.0,
        )
        self.assertEqual(
            [],
            self.native._presentation_jump_reasons(
                base, one_frame_turn_lag, speed=0.0,
                sample_gap=0.05, turn_speed=0.4,
            ),
        )

    def test_ferdinand_presentation_observer_reads_model_matrix_and_hp_local(self):
        self.descriptor.name = "germany:G37_Ferdinand"
        self.assertIsNotNone(self.activate())
        state = getattr(self.mock, self.native.STATE_ATTR)
        physics = state["physics"]
        physics.body_position = Vector3(0.0, 0.0, 0.0)
        physics.body_yaw = 0.0
        physics.body_pitch = 0.0
        physics.body_roll = 0.0
        self.mock.matrix = Matrix()
        self.mock._chassis_model.matrix = Matrix()
        hp_provider = Matrix()
        hp_provider.translation = Vector3(0.0, 4.0, 0.0)
        self.mock._chassis_model.nodes["HP_gui"] = hp_provider
        self.mock.publicInfo = {"name": "Mantis-40"}
        self.mock._network_bot_id = 19
        self.mock._network_bot_slot = 3
        self.mock._bot_team = 2

        self.assertFalse(
            self.native.observe_presentation(self.mock, self.now + 0.01)
        )
        first = state["presentation_sample"]
        self.assertEqual((0.0, 4.0, 0.0), first["hp_local"])

        hp_provider.translation = Vector3(2.0, 4.0, 0.0)
        self.assertTrue(
            self.native.observe_presentation(self.mock, self.now + 0.06)
        )
        messages = [message for level, message in self.logs if level == "note"]
        self.assertTrue(any(
            "reasons=hp_gui_split" in message and
            "vehicle=germany:G37_Ferdinand" in message
            for message in messages
        ))

        sampled = state["presentation_sample"]
        state["presentation_log_count"] = (
            self.native.PRESENTATION_DIAGNOSTIC_LOG_LIMIT
        )
        hp_provider.translation = Vector3(5.0, 4.0, 0.0)
        self.assertFalse(
            self.native.observe_presentation(self.mock, self.now + 0.11)
        )
        self.assertIs(sampled, state["presentation_sample"])

    def test_presentation_observation_runs_after_canonical_pose_commit(self):
        source = BATTLE_PATH.read_text(encoding="utf-8")
        commit = source.index("'pose_commit', _VP.commit_pose")
        observe = source.index(
            "_native_body_manager.observe_presentation(", commit
        )
        visibility = source.index("_perf_visibility =", observe)

        self.assertLess(commit, observe)
        self.assertLess(observe, visibility)
        self.assertIn(
            "if (_native_body_pose is not None and",
            source[commit:observe],
        )

    def test_client_only_native_path_avoids_server_connection_clock(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("state['filter'].syncGunAngles(", source)


if __name__ == "__main__":
    unittest.main()
