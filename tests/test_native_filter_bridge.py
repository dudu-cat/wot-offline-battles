import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/native_filter_bridge.py"
)


class NativeFilterBridgeTest(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        self.logs = []
        for name in ("gui", "gui.mods", "gui.mods.offhangar"):
            sys.modules[name] = types.ModuleType(name)
        logging = types.ModuleType("gui.mods.offhangar.logging")
        logging.LOG_NOTE = lambda message: self.logs.append(("note", message))
        logging.LOG_ERROR = lambda message: self.logs.append(("error", message))
        sys.modules["gui.mods.offhangar.logging"] = logging

        spec = importlib.util.spec_from_file_location(
            "native_filter_bridge_under_test", BRIDGE_PATH
        )
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def install_native_module(self):
        calls = []
        native = types.ModuleType(
            "gui.mods.offhangar.offhangar_native_seed"
        )
        native.seed_filter = lambda *args: calls.append(args)
        sys.modules["gui.mods.offhangar.offhangar_native_seed"] = native
        sys.modules["gui.mods.offhangar"].offhangar_native_seed = native
        return calls

    def test_matching_executable_loads_once_and_marshals_seed(self):
        calls = self.install_native_module()
        hash_calls = []
        self.bridge._world_of_tanks_exe = lambda: "WorldOfTanks.exe"

        def matching_hash(path):
            hash_calls.append(path)
            return self.bridge.EXPECTED_EXE_SHA256

        self.bridge._sha256_file = matching_hash
        vehicle_filter = object()

        self.assertTrue(self.bridge.seed_filter(
            vehicle_filter, 123.5, 7,
            (1.0, 2.0, 3.0), (0.1, 0.2, 0.3),
        ))
        self.assertTrue(self.bridge.seed_filter(
            vehicle_filter, 124.5, 7,
            (4.0, 5.0, 6.0), (0.0, 0.0, 0.4),
        ))

        self.assertEqual(["WorldOfTanks.exe"], hash_calls)
        self.assertEqual(2, len(calls))
        self.assertEqual((vehicle_filter, 123.5, 7, 0,
                          1.0, 2.0, 3.0, 0.1, 0.2, 0.3), calls[0])
        self.assertEqual(1, sum(
            "NATIVE_FILTER_BRIDGE loaded" in message
            for unused_level, message in self.logs
        ))

    def test_executable_hash_mismatch_fails_closed_and_logs_once(self):
        self.install_native_module()
        self.bridge._world_of_tanks_exe = lambda: "WorldOfTanks.exe"
        self.bridge._sha256_file = lambda unused_path: "0" * 64

        self.assertIsNone(self.bridge.load())
        self.assertIsNone(self.bridge.load())
        self.assertEqual(1, sum(
            "SHA-256 mismatch" in message
            for unused_level, message in self.logs
        ))

    def test_native_seed_exception_is_contained(self):
        self.install_native_module()
        self.bridge._world_of_tanks_exe = lambda: "WorldOfTanks.exe"
        self.bridge._sha256_file = (
            lambda unused_path: self.bridge.EXPECTED_EXE_SHA256
        )
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]

        def fail(*unused_args):
            raise RuntimeError("seed refused")

        native.seed_filter = fail
        self.assertFalse(self.bridge.seed_filter(
            object(), 1.0, 2, (4, 5, 6), (0, 0, 0.7)
        ))
        self.assertTrue(any(
            "seed failed: seed refused" in message
            for unused_level, message in self.logs
        ))


if __name__ == "__main__":
    unittest.main()
