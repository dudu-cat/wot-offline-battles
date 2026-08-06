import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOADER_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/prebaked_navigation.py"
)


def load_navigation_loader(mod_directory):
    module_names = (
        "gui", "gui.mods", "gui.mods.offhangar",
        "gui.mods.offhangar.paths",
    )
    previous = {name: sys.modules.get(name) for name in module_names}
    gui = types.ModuleType("gui")
    mods = types.ModuleType("gui.mods")
    offhangar = types.ModuleType("gui.mods.offhangar")
    paths = types.ModuleType("gui.mods.offhangar.paths")
    paths.mod_dir = lambda: str(mod_directory)
    sys.modules.update({
        "gui": gui,
        "gui.mods": mods,
        "gui.mods.offhangar": offhangar,
        "gui.mods.offhangar.paths": paths,
    })
    try:
        spec = importlib.util.spec_from_file_location(
            "prebaked_navigation_under_test", LOADER_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


class PrebakedNavigationLoaderTest(unittest.TestCase):
    def graph(self):
        return {
            "format": "offhangar-navgraph",
            "version": 1,
            "game_version": "0.8.2",
            "map": "07_lakeville",
            "cell_size": 4.0,
            "origin": [0.0, 0.0],
            "width": 2,
            "height": 1,
            "heights_mm": [0, 0],
            "links": [16, 8],
        }

    def test_loads_only_a_matching_versioned_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            navgraphs = Path(directory) / "navgraphs"
            navgraphs.mkdir()
            graph_path = navgraphs / "07_lakeville.json"
            graph_path.write_text(json.dumps(self.graph()))
            loader = load_navigation_loader(directory)

            graph = loader.load_graph("spaces/07_lakeville")
            self.assertEqual("07_lakeville", graph["map"])
            self.assertIsNone(loader.load_graph("04_himmelsdorf"))

            invalid = self.graph()
            invalid["game_version"] = "0.9.22"
            graph_path.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):
                loader.load_graph("07_lakeville")


if __name__ == "__main__":
    unittest.main()
