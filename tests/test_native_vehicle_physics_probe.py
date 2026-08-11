import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/native_vehicle_physics_probe.py"
)
LOADER_PATH = ROOT / "scripts/client/gui/mods/mod_offhangar.py"


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
        point = getattr(source, "translation", Vector3(0, 0, 0))
        self.translation = Vector3(point.x, point.y, point.z)
        self.yaw = float(getattr(source, "yaw", 0.0))

    def applyVector(self, vector):
        return Vector3(
            vector.x * math.cos(self.yaw) + vector.z * math.sin(self.yaw),
            vector.y,
            -vector.x * math.sin(self.yaw) + vector.z * math.cos(self.yaw),
        )


class DumbFilter(object):
    def __init__(self, position, yaw, valid):
        self.bodyMatrix = Matrix()
        self.export_valid = bool(valid)
        if self.export_valid:
            self.bodyMatrix.translation = Vector3(
                position.x, position.y, position.z
            )
            self.bodyMatrix.yaw = float(yaw)


class AvatarFilter(object):
    def __init__(self, source=None):
        if source is not None and not isinstance(source, AvatarFilter):
            raise TypeError("expected AvatarFilter")
        self.bodyMatrix = Matrix(
            source.bodyMatrix if source is not None else None
        )
        self.export_valid = bool(
            source is not None and getattr(source, "export_valid", False)
        )


class LegacyVehicleFilter(AvatarFilter):
    def setPosition(self, x, z):
        # The pinned ABI only accepts x/z. It cannot establish full y/yaw.
        self.bodyMatrix.translation = Vector3(x, 0.0, z)
        self.bodyMatrix.yaw = 0.0
        self.export_valid = True


class VehicleFilter(AvatarFilter):
    def __init__(self, source=None):
        AvatarFilter.__init__(self, source)
        self.longitudinalSpeed = 0.0
        self.strafeSpeed = 0.0
        self.angularSpeed = 0.0
        self.numLeftTrackContacts = 4
        self.numRightTrackContacts = 4
        self.speedInfo = types.SimpleNamespace(value=[0.0])
        self.triangles = []
        self.physics = None

    def addTriangle(self, first, second, third):
        self.triangles.append((first, second, third))

    def setVehiclePhysics(self, physics):
        self.physics = physics

    def syncGunAngles(self, unused_yaw, unused_pitch):
        pass

    def notifyInputKeysDown(self, movement, unused_rotation):
        if movement > 0:
            self.longitudinalSpeed = 4.0
            self.speedInfo.value[0] = 4.0
            self.bodyMatrix.translation = Vector3(
                self.bodyMatrix.translation.x,
                self.bodyMatrix.translation.y,
                self.bodyMatrix.translation.z + 2.0,
            )


class VehiclePhysics(object):
    def setArenaBounds(self, minimum, maximum):
        self.bounds = (minimum, maximum)


class Entity(object):
    def __init__(self, entity_id, position, yaw, initial_filter_valid):
        self.id = entity_id
        self.position = position
        self._filter = DumbFilter(position, yaw, initial_filter_valid)

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, replacement):
        # Entity.filter only migrates a pose when the old filter can export a
        # timestamped state. A fresh client-created DumbFilter cannot.
        if getattr(self._filter, "export_valid", False):
            replacement.bodyMatrix = Matrix(self._filter.bodyMatrix)
            replacement.export_valid = True
        self._filter = replacement


class NativeVehiclePhysicsProbeTest(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        self.now = 100.0
        self.logs = []
        self.messages = []
        self.entities = {}
        self.destroyed = []
        self.physics_init_calls = []
        self.initial_filter_valid = True
        self.bridge_available = True
        self.bridge_calls = []

        bigworld = types.ModuleType("BigWorld")
        bigworld.Entity = object
        bigworld.time = lambda: self.now

        def collide(unused_space, first, second, unused_mask):
            if first.x == second.x and first.z == second.z:
                return (Vector3(first.x, 0.0, first.z), None)
            return None

        def create_entity(unused_type, unused_space, unused_vehicle_id,
                          position, direction, unused_properties):
            entity_id = 900 + len(self.destroyed) + len(self.entities)
            self.entities[entity_id] = Entity(
                entity_id, position, direction[2], self.initial_filter_valid
            )
            return entity_id

        bigworld.wg_collideSegment = collide
        bigworld.createEntity = create_entity
        bigworld.entity = lambda entity_id: self.entities.get(entity_id)

        def destroy_entity(entity_id):
            self.destroyed.append(entity_id)
            self.entities.pop(entity_id, None)

        bigworld.destroyEntity = destroy_entity
        bigworld.AvatarFilter = AvatarFilter
        bigworld.WGVehicleFilter = LegacyVehicleFilter
        bigworld.WGVehicleFilter2 = VehicleFilter
        bigworld.WGVehiclePhysics2 = VehiclePhysics

        math_module = types.ModuleType("Math")
        math_module.Vector3 = Vector3
        math_module.Matrix = Matrix

        logging = types.ModuleType("gui.mods.offhangar.logging")
        logging.LOG_NOTE = lambda message: self.logs.append(("note", message))
        logging.LOG_ERROR = lambda message: self.logs.append(("error", message))

        messages = types.ModuleType("gui.SystemMessages")
        messages.SM_TYPE = types.SimpleNamespace(
            Information="information", Warning="warning", Error="error"
        )
        messages.pushMessage = lambda text, kind: self.messages.append((text, kind))

        physics_shared = types.ModuleType("physics_shared")
        physics_shared.initVehiclePhysics = lambda physics, descriptor: (
            self.physics_init_calls.append((physics, descriptor))
        )

        arena_type = types.ModuleType("ArenaType")
        arena_type.getVisibilityMask = lambda unused_gameplay: 1

        for name in ("gui", "gui.mods", "gui.mods.offhangar"):
            sys.modules[name] = types.ModuleType(name)

        native_bridge = types.ModuleType(
            "gui.mods.offhangar.native_filter_bridge"
        )

        def seed_filter(vehicle_filter, timestamp, space_id, entity_id,
                        position, direction):
            position_tuple = (position.x, position.y, position.z)
            self.bridge_calls.append((
                vehicle_filter, timestamp, space_id, entity_id,
                position_tuple, tuple(direction),
            ))
            if not self.bridge_available:
                return False
            vehicle_filter.bodyMatrix.translation = Vector3(position_tuple)
            vehicle_filter.bodyMatrix.yaw = float(direction[2])
            vehicle_filter.export_valid = True
            return True

        native_bridge.seed_filter = seed_filter
        sys.modules["gui.mods.offhangar.native_filter_bridge"] = native_bridge
        sys.modules["gui.mods.offhangar"].native_filter_bridge = native_bridge
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        sys.modules["physics_shared"] = physics_shared
        sys.modules["ArenaType"] = arena_type
        sys.modules["gui.mods.offhangar.logging"] = logging
        sys.modules["gui.SystemMessages"] = messages

        spec = importlib.util.spec_from_file_location(
            "native_vehicle_physics_probe_under_test", PROBE_PATH
        )
        self.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.probe)

        self.player = types.SimpleNamespace(
            arena=types.SimpleNamespace(period=3),
            arenaTypeID=0,
        )
        self.descriptor = types.SimpleNamespace(
            chassis={
                "topRightCarryingPoint": (1.7, 0.0),
            },
            physics={
                "speedLimits": (20.0, 8.0),
                "minPlaneNormalY": 0.6,
                "carryingTriangles": (
                    ((-1.0, -2.0), (1.0, -2.0), (0.0, 2.0)),
                ),
                "enginePower": 500000.0,
            },
        )
        self.position = Vector3(0.0, 0.0, 0.0)

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def advance_probe(self):
        return self.probe.maybe_run(
            self.player, self.descriptor, self.position, 0.0, 7, 11
        )

    def run_until_done(self, max_steps=100):
        saw_pass = False
        for unused in range(max_steps):
            saw_pass = self.advance_probe() or saw_pass
            state = getattr(self.player, self.probe._STATE_ATTR, None)
            if (state is not None and state.get("phase") == "done" and
                    not self.probe.is_requested()):
                return state, saw_pass
            self.now += 1.0
        self.fail("probe did not finish")

    def test_f6_request_runs_staged_retail_physics_and_cleans_up(self):
        self.probe.request()
        state, saw_pass = self.run_until_done()

        self.assertTrue(saw_pass)
        self.assertTrue(state["passed"])
        self.assertEqual("retail_order", state["candidate"])
        self.assertEqual(1, len(self.physics_init_calls))
        self.assertEqual([900], self.destroyed)
        self.assertTrue(any(
            "NATIVE_PHYSICS_PROBE PASS" in message
            for unused_level, message in self.logs
        ))
        self.assertTrue(any(
            "stage=T6_physics_callback" in message and "valid=True" in message
            for unused_level, message in self.logs
        ))
        self.assertFalse(hasattr(VehicleFilter(), "set"))
        self.assertEqual("information", self.messages[-1][1])

        self.assertFalse(self.advance_probe())
        self.assertEqual([900], self.destroyed)

    def test_real_client_created_semantics_use_native_bridge_and_pass(self):
        self.initial_filter_valid = False
        self.probe.request()
        state, saw_pass = self.run_until_done()

        self.assertTrue(saw_pass)
        self.assertTrue(state["passed"])
        self.assertEqual("native_bridge", state["candidate"])
        self.assertEqual(4, len(self.destroyed))
        self.assertEqual(2, len(self.physics_init_calls))
        self.assertEqual(3, len(state["candidate_failures"]))
        self.assertEqual(1, len(self.bridge_calls))
        self.assertEqual(7, self.bridge_calls[0][2])
        self.assertTrue(any(
            "CANDIDATE FAIL candidate=retail_order" in message
            for unused_level, message in self.logs
        ))
        self.assertTrue(any(
            "CANDIDATE FAIL candidate=avatar_copy" in message
            for unused_level, message in self.logs
        ))
        self.assertTrue(any(
            "CANDIDATE FAIL candidate=legacy_set_position" in message
            for unused_level, message in self.logs
        ))
        self.assertTrue(any(
            "candidate=native_bridge" in message and "PASS" in message
            for unused_level, message in self.logs
        ))
        self.assertEqual("information", self.messages[-1][1])

    def test_native_bridge_failure_exhausts_candidates_and_fails_closed(self):
        self.initial_filter_valid = False
        self.bridge_available = False
        self.probe.request()
        state, saw_pass = self.run_until_done()

        self.assertFalse(saw_pass)
        self.assertFalse(state["passed"])
        self.assertEqual(4, len(self.destroyed))
        self.assertEqual(1, len(self.physics_init_calls))
        self.assertEqual(4, len(state["candidate_failures"]))
        self.assertEqual(1, len(self.bridge_calls))
        self.assertTrue(any(
            "CANDIDATE FAIL candidate=native_bridge" in message
            for unused_level, message in self.logs
        ))
        self.assertTrue(any(
            "stage=pose_seed" in message
            for unused_level, message in self.logs
        ))
        self.assertEqual("error", self.messages[-1][1])

    def test_probe_is_inert_until_explicitly_requested(self):
        self.assertFalse(self.advance_probe())
        self.assertEqual({}, self.entities)
        self.assertEqual([], self.logs)

    def test_python26_source_loader_preloads_probe_before_battle_module(self):
        source = LOADER_PATH.read_text(encoding="utf-8")
        probe_index = source.index("'native_vehicle_physics_probe'")
        battle_index = source.index("'offline_battle'", probe_index)
        self.assertLess(probe_index, battle_index)

    def test_missing_native_physics_fails_closed_and_keeps_no_bot_state(self):
        del sys.modules["BigWorld"].WGVehiclePhysics2
        self.probe.request()
        state, saw_pass = self.run_until_done()

        self.assertFalse(saw_pass)
        self.assertFalse(state["passed"])
        self.assertTrue(any(
            "NATIVE_PHYSICS_PROBE FAIL stage=physics_attach" in message
            for unused_level, message in self.logs
        ))
        self.assertEqual([900], self.destroyed)


if __name__ == "__main__":
    unittest.main()
