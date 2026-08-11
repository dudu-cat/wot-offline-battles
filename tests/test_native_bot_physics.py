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

    def addTriangle(self, first, second, third):
        self.triangles.append((first, second, third))

    def setVehiclePhysics(self, physics):
        self.physics = physics

    def syncGunAngles(self, unused_yaw, unused_pitch):
        raise AssertionError("offline native bots must not call syncGunAngles")

    def notifyInputKeysDown(self, movement, rotation):
        self.input = (movement, rotation)
        if movement:
            self.longitudinalSpeed = 8.0 * movement
            self.bodyMatrix.translation = Vector3(
                self.bodyMatrix.translation.x,
                self.bodyMatrix.translation.y,
                self.bodyMatrix.translation.z + movement,
            )
        if rotation:
            self.angularSpeed = 0.2 * rotation
            self.bodyMatrix.yaw += 0.05 * rotation


class VehiclePhysics(object):
    def setArenaBounds(self, minimum, maximum):
        self.bounds = (minimum, maximum)


class Entity(object):
    def __init__(self, entity_id):
        self.id = entity_id
        self.filter = AvatarFilter()
        self.wgPhysics = None
        self.matrix = types.SimpleNamespace(notModel=False)


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

        bigworld = types.ModuleType("BigWorld")
        bigworld.time = lambda: self.now
        bigworld.WGVehicleFilter2 = VehicleFilter
        bigworld.WGVehiclePhysics2 = VehiclePhysics
        bigworld.AvatarFilter = AvatarFilter
        bigworld.Servo = lambda provider: ("servo", provider)
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
            self.bridge_calls.append((timestamp, space_id))
            if not self.bridge_ok:
                return False
            vehicle_filter.bodyMatrix.translation = Vector3(position)
            vehicle_filter.bodyMatrix.yaw = float(direction[2])
            vehicle_filter.bodyMatrix.pitch = float(direction[1])
            vehicle_filter.bodyMatrix.roll = float(direction[0])
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
            _chassis_model=Model(self.old_servo),
            _pose_servo=self.old_servo,
            _servo_added=True,
        )
        self.descriptor = types.SimpleNamespace(
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

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def activate(self):
        self.assertTrue(self.native.prepare(
            self.player, self.mock, self.descriptor, 7, self.now
        ))
        self.now += self.native.SEED_CHECK_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.now += self.native.SETTLE_SECONDS + 0.01
        return self.native.step(
            self.player, self.mock, self.descriptor, 1, 1, 7, self.now
        )

    def test_authority_body_stages_then_returns_native_pose(self):
        result = self.activate()

        self.assertTrue(self.native.is_active(self.mock))
        self.assertFalse(self.entity.isStarted)
        self.assertEqual((1, 1), self.entity.filter.input)
        self.assertEqual(8.0, result["velocity"])
        self.assertEqual(0.2, result["turn_velocity"])
        self.assertAlmostEqual(-7.0, result["position"][2])
        self.assertEqual(2, len(self.bridge_calls))
        self.assertEqual(500.0, self.entity.wgPhysics.enginePower)

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

    def test_implausible_active_jump_freezes_without_second_pose_owner(self):
        result = self.activate()
        self.assertIsNotNone(result)
        native_filter = self.entity.filter
        self.entity.filter.bodyMatrix.translation = Vector3(500, 0, 500)

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
        self.assertTrue(getattr(
            self.mock, self.native.STATE_ATTR
        )["freeze_reseed"])
        self.assertEqual((12.0, 3.0, -7.0), (
            self.entity.filter.bodyMatrix.translation.x,
            self.entity.filter.bodyMatrix.translation.y,
            self.entity.filter.bodyMatrix.translation.z,
        ))

    def test_long_frame_allows_physically_plausible_native_displacement(self):
        self.assertIsNotNone(self.activate())
        self.entity.filter.bodyMatrix.translation.z += 30.0

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
        self.assertEqual([], self.mock._chassis_model.motors)
        self.assertFalse(hasattr(self.mock._chassis_model, "wg_fashion"))
        self.assertIsNone(self.mock._fashion)
        self.assertIsInstance(self.entity.filter, AvatarFilter)

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
        self.assertTrue(self.entity.wgPhysics.staticMode)
        self.assertIsNone(other_entity.wgPhysics)

        self.now += 0.01
        self.native.step(
            self.player, other, self.descriptor, 0, 0, 7, self.now
        )
        self.assertIsNotNone(other_entity.wgPhysics)

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
        self.now += self.native.WARMUP_SECONDS + 0.01
        self.assertTrue(self.native.step(
            self.player, self.mock, self.descriptor, 0, 0, 7, self.now
        )["staging"])
        self.now += self.native.SETTLE_SECONDS + 0.01

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
