import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts/client/gui/mods/offhangar/spectator_minimap.py"
    spec = importlib.util.spec_from_file_location("spectator_minimap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Provider:
    pass


class FakeMath:
    WGCombinedMP = Provider
    WGTranslationOnlyMP = Provider


class FakeUI:
    def __init__(self):
        self.calls = []
        self.next_handle = 90

    def entryInvoke(self, handle, invocation):
        self.calls.append(("invoke", handle, invocation))

    def entrySetMatrix(self, handle, matrix):
        self.calls.append(("matrix", handle, matrix))

    def delEntry(self, handle):
        self.calls.append(("delete", handle))

    def addEntry(self, matrix, z_index):
        self.calls.append(("add", matrix, z_index))
        self.next_handle += 1
        return self.next_handle


class FakeZIndex:
    def getIndexByName(self, name):
        return {"cameraNormal": 100}[name]


class FakeParent:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))


class FakeMinimap:
    def __init__(self):
        self._Minimap__ownUI = FakeUI()
        self._Minimap__ownEntry = {"handle": 151}
        self._Minimap__entries = {7: {"handle": 107}, 8: {"handle": 108}}
        self._Minimap__observedVehicleId = 7
        self._Minimap__cameraHandle = 100
        self._Minimap__parentUI = FakeParent()
        self.zIndexManager = FakeZIndex()


class SpectatorMinimapTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_switch_moves_native_postmortem_and_camera_markers(self):
        minimap = FakeMinimap()
        target_matrix = object()
        inverse = object()

        changed = self.module.follow_mock_vehicle(
            minimap, 1, 8, target_matrix, object(), inverse, FakeMath,
        )

        self.assertTrue(changed)
        self.assertEqual(8, minimap._Minimap__observedVehicleId)
        calls = minimap._Minimap__ownUI.calls
        self.assertIn(("invoke", 107, ("setPostmortem", [False])), calls)
        self.assertIn(("invoke", 108, ("setPostmortem", [True])), calls)
        self.assertIn(("matrix", 151, target_matrix), calls)
        add = next(call for call in calls if call[0] == "add")
        self.assertIs(target_matrix, add[1].translationSrc.source)
        self.assertIs(inverse, add[1].rotationSrc)


if __name__ == "__main__":
    unittest.main()
