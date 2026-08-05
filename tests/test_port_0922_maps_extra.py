import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922' / 'ai'

def _load():
    for name, path in (('gui', BASE), ('gui.mods', BASE), ('gui.mods.offline_lan_0922', BASE), ('gui.mods.offline_lan_0922.ai', BASE)):
        if name not in sys.modules:
            module = types.ModuleType(name); module.__path__ = [str(path)]; sys.modules[name] = module
    name = 'gui.mods.offline_lan_0922.ai.maps_0922_extra'; sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, BASE / 'maps_0922_extra.py')
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return module

class Maps0922ExtraTests(unittest.TestCase):
    def test_all_new_arena_defs_have_coarse_two_team_routes(self):
        maps = _load().TACTICAL_MAPS_0922_EXTRA
        self.assertEqual(14, len(maps))
        for name, data in maps.items():
            self.assertEqual(name, data['name'])
            self.assertEqual('coarse-minimap-bounds', data['annotation_confidence'])
            self.assertEqual(2, len(data['bases']))
            self.assertGreaterEqual(len(data['routes'][1]), 3)
            self.assertGreaterEqual(len(data['routes'][2]), 3)
            for route in data['routes'][1] + data['routes'][2]:
                self.assertGreaterEqual(len(route['waypoints']), 3)
