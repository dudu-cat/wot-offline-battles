import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/native_navmesh_probe.py"
)


class Vector3(object):
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class NativeNavmeshProbeTest(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        self.logs = []
        self.messages = []
        self.navigation_calls = []

        bigworld = types.ModuleType("BigWorld")

        def navigate(source, destination, distance, girth):
            self.navigation_calls.append((source, destination, distance, girth))
            return [source, destination]

        bigworld.navigatePathPoints = navigate
        math_module = types.ModuleType("Math")
        math_module.Vector3 = Vector3
        logging = types.ModuleType("gui.mods.offhangar.logging")
        logging.LOG_NOTE = lambda message: self.logs.append(("note", message))
        logging.LOG_ERROR = lambda message: self.logs.append(("error", message))
        messages = types.ModuleType("gui.SystemMessages")
        messages.SM_TYPE = types.SimpleNamespace(
            Information="information", Warning="warning", Error="error"
        )
        messages.pushMessage = lambda text, kind: self.messages.append((text, kind))

        for name in ("gui", "gui.mods", "gui.mods.offhangar"):
            sys.modules[name] = types.ModuleType(name)
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        sys.modules["gui.mods.offhangar.logging"] = logging
        sys.modules["gui.SystemMessages"] = messages

        spec = importlib.util.spec_from_file_location(
            "native_navmesh_probe_under_test", PROBE_PATH
        )
        self.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.probe)

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    @staticmethod
    def player():
        return types.SimpleNamespace(
            arena=types.SimpleNamespace(period=3),
            _offh_spawn_fixed=True,
        )

    def test_lakeville_probe_calls_native_pathfinder_once_per_battle(self):
        player = self.player()
        position = Vector3(-169.5, 12.0, 299.4)

        first = self.probe.maybe_run(
            player, "spaces/07_lakeville", position, 7
        )
        second = self.probe.maybe_run(
            player, "spaces/07_lakeville", position, 7
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, len(self.navigation_calls))
        source, destination, distance, girth = self.navigation_calls[0]
        self.assertEqual((-169.5, 299.4), (source.x, source.z))
        self.assertEqual((-161.5, 299.4), (destination.x, destination.z))
        self.assertEqual((100.0, 0.5), (distance, girth))
        self.assertIn("NAVMESH_PROBE PASS", self.logs[0][1])
        self.assertEqual("information", self.messages[0][1])

    def test_probe_waits_for_live_period_and_spawn_correction(self):
        player = self.player()
        player.arena.period = 2
        position = Vector3(-169.5, 12.0, 299.4)

        self.assertFalse(self.probe.maybe_run(
            player, "07_lakeville", position, 1
        ))
        player.arena.period = 3
        player._offh_spawn_fixed = False
        self.assertFalse(self.probe.maybe_run(
            player, "07_lakeville", position, 1
        ))

        self.assertEqual([], self.navigation_calls)
        self.assertEqual([], self.messages)

    def test_other_maps_do_not_run_the_probe(self):
        self.assertFalse(self.probe.maybe_run(
            self.player(), "18_cliff", Vector3(0.0, 0.0, 0.0), 1
        ))
        self.assertEqual([], self.navigation_calls)


if __name__ == "__main__":
    unittest.main()
