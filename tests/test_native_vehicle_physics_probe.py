import importlib.util
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
        self.translation = getattr(source, "translation", Vector3(0, 0, 0))


class DumbFilter(object):
    def __init__(self, position):
        self.position = Vector3(position.x, position.y, position.z)


class VehicleFilter(object):
    def __init__(self):
        self.bodyMatrix = Matrix()
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
    def __init__(self, entity_id, position):
        self.id = entity_id
        self.position = position
        self._filter = DumbFilter(position)

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, replacement):
        # The retail Entity.filter setter transfers the timestamped pose from
        # the engine-created DumbFilter before attaching the replacement.
        old_position = self._filter.position
        replacement.bodyMatrix.translation = Vector3(
            old_position.x, old_position.y, old_position.z
        )
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

        bigworld = types.ModuleType("BigWorld")
        bigworld.Entity = object
        bigworld.time = lambda: self.now

        def collide(unused_space, first, second, unused_mask):
            if first.x == second.x and first.z == second.z:
                return (Vector3(first.x, 0.0, first.z), None)
            return None

        def create_entity(unused_type, unused_space, unused_vehicle_id,
                          position, unused_direction, unused_properties):
            entity_id = 900 + len(self.entities)
            self.entities[entity_id] = Entity(entity_id, position)
            return entity_id

        bigworld.wg_collideSegment = collide
        bigworld.createEntity = create_entity
        bigworld.entity = lambda entity_id: self.entities.get(entity_id)
        bigworld.destroyEntity = lambda entity_id: (
            self.destroyed.append(entity_id), self.entities.pop(entity_id, None)
        )
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

    def test_f6_request_runs_staged_retail_physics_and_cleans_up(self):
        self.probe.request()
        self.assertFalse(self.advance_probe())
        self.now += self.probe.INITIAL_DELAY
        self.assertFalse(self.advance_probe())  # create
        self.assertFalse(self.advance_probe())  # bind entity/filter
        self.assertFalse(self.advance_probe())  # bind physics
        self.now += self.probe.SETTLE_SECONDS
        self.assertFalse(self.advance_probe())  # begin forward input
        self.now += self.probe.DRIVE_SECONDS
        self.assertTrue(self.advance_probe())   # observe PASS
        self.now += self.probe.CLEANUP_DELAY
        self.assertTrue(self.advance_probe())   # destroy temporary entity

        self.assertEqual(1, len(self.physics_init_calls))
        self.assertEqual([900], self.destroyed)
        self.assertTrue(any(
            "NATIVE_PHYSICS_PROBE PASS" in message
            for unused_level, message in self.logs
        ))
        self.assertTrue(any(
            "filter_transfer source=DumbFilter" in message
            for unused_level, message in self.logs
        ))
        self.assertFalse(hasattr(VehicleFilter(), "set"))
        self.assertEqual("information", self.messages[-1][1])
        self.assertFalse(self.probe.is_requested())

        # A completed request must not silently run again in a later battle.
        self.assertFalse(self.advance_probe())
        self.assertEqual([900], self.destroyed)

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
        self.assertFalse(self.advance_probe())
        self.now += self.probe.INITIAL_DELAY
        self.assertFalse(self.advance_probe())
        self.assertFalse(self.advance_probe())
        self.assertFalse(self.advance_probe())

        state = getattr(self.player, self.probe._STATE_ATTR)
        self.assertEqual("cleanup", state["phase"])
        self.assertTrue(any(
            "NATIVE_PHYSICS_PROBE FAIL stage=physics" in message
            for unused_level, message in self.logs
        ))
        self.now += self.probe.CLEANUP_DELAY
        self.assertFalse(self.advance_probe())
        self.assertEqual([900], self.destroyed)


if __name__ == "__main__":
    unittest.main()
