import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/client/OfflineEntity.py"
PYC_PATH = ROOT / "scripts/client/OfflineEntity.pyc"


class OfflineEntityNativePhysicsTest(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)

        bigworld = types.ModuleType("BigWorld")
        bigworld.Entity = type("Entity", (object,), {})

        class RetailVehicle(object):
            def _isDestructibleMayBeBroken(self, *args):
                raise AssertionError("the retail unbound method was used")

        vehicle = types.ModuleType("Vehicle")
        vehicle.Vehicle = RetailVehicle

        self.desc = {
            "type": 1,
            "health": 20.0,
            "kineticDamageCorrection": 1.0,
            "modules": {},
        }
        cache = types.SimpleNamespace(
            getDescByFilename=lambda filename: self.desc,
            unitVehicleMass=1000.0,
        )
        controller = types.SimpleNamespace(
            isDestructibleBroken=lambda item, material, kind: False
        )
        area = types.ModuleType("AreaDestructibles")
        area.g_cache = cache
        area.g_destructiblesManager = types.SimpleNamespace(
            getController=lambda chunk: controller
        )

        destructibles = types.ModuleType("DestructiblesCache")
        destructibles.DESTR_TYPE_STRUCTURE = 2
        destructibles.scaledDestructibleHealth = (
            lambda scale, health: float(scale) * float(health)
        )

        sys.modules["BigWorld"] = bigworld
        sys.modules["Vehicle"] = vehicle
        sys.modules["AreaDestructibles"] = area
        sys.modules["DestructiblesCache"] = destructibles

        spec = importlib.util.spec_from_file_location(
            "OfflineEntity_under_test", MODULE_PATH
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.vehicle_class = RetailVehicle

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def test_adapter_accepts_offline_owner_and_preserves_retail_formula(self):
        owner = types.SimpleNamespace(
            typeDescriptor=types.SimpleNamespace(
                physics={"weight": 40000.0}
            )
        )

        self.assertTrue(
            self.module.install_native_destructible_callback_adapter()
        )
        callback = self.vehicle_class._isDestructibleMayBeBroken

        self.assertTrue(callback(
            owner, "chunk", 7, 3, "tree.model", 1.0, 20.0
        ))
        self.assertTrue(getattr(
            callback, "_offh_offline_entity_adapter", False
        ))

    def test_adapter_is_idempotent(self):
        self.assertTrue(
            self.module.install_native_destructible_callback_adapter()
        )
        callback = self.vehicle_class._isDestructibleMayBeBroken

        self.assertTrue(
            self.module.install_native_destructible_callback_adapter()
        )
        self.assertIs(
            callback, self.vehicle_class._isDestructibleMayBeBroken
        )

    def test_adapter_restores_original_class_descriptor(self):
        original = self.vehicle_class.__dict__["_isDestructibleMayBeBroken"]
        self.assertTrue(
            self.module.install_native_destructible_callback_adapter()
        )

        self.assertTrue(
            self.module.restore_native_destructible_callback_adapter()
        )
        self.assertIs(
            original,
            self.vehicle_class.__dict__["_isDestructibleMayBeBroken"],
        )
        self.assertTrue(
            self.module.restore_native_destructible_callback_adapter()
        )

    def test_shipped_python26_entrypoint_contains_native_adapter(self):
        payload = PYC_PATH.read_bytes()

        self.assertEqual(b"\xd1\xf2\r\n", payload[:4])
        self.assertIn(
            b"install_native_destructible_callback_adapter", payload
        )
        self.assertIn(
            b"restore_native_destructible_callback_adapter", payload
        )


if __name__ == "__main__":
    unittest.main()
