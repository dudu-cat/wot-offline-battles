import sys
import types
import unittest
from pathlib import Path
from unittest import mock


SOURCE = (Path(__file__).resolve().parents[1] /
          "scripts/client/gui/mods/offhangar/offline_battle.py")


def load_arena_helpers():
    source = SOURCE.read_text()
    start = source.index("def _offh_arena_type_matches(")
    end = source.index("\ndef _queue_type_randoms(", start)
    notes = []
    errors = []
    namespace = {
        "LOG_DEBUG": lambda *args: None,
        "LOG_NOTE": lambda message: notes.append(message),
        "LOG_ERROR": lambda message: errors.append(message),
        "LOG_CURRENT_EXCEPTION": lambda: None,
    }
    exec(compile(source[start:end], str(SOURCE), "exec"), namespace)
    return namespace, notes, errors


class OfflineArenaVisibilityTests(unittest.TestCase):
    def _arena_module(self, cache):
        module = types.ModuleType("ArenaType")
        module.g_cache = cache
        module.getGameplayIDForName = lambda name: {
            "ctf": 0, "assault": 1, "domination": 2,
        }[name]
        module.getVisibilityMask = lambda gameplay_id: 1 << gameplay_id
        return module

    def test_resolver_selects_exact_gameplay_instead_of_first_geometry(self):
        helpers, unused_notes, unused_errors = load_arena_helpers()
        ctf = types.SimpleNamespace(
            geometryName="02_malinovka", gameplayName="ctf", gameplayID=0,
            controlPoint=None, teamBasePositions="ctf-bases")
        assault = types.SimpleNamespace(
            geometryName="02_malinovka", gameplayName="assault", gameplayID=1,
            controlPoint="assault-flag", teamBasePositions="assault-bases")
        module = self._arena_module({(1 << 16) | 2: assault, 2: ctf})

        with mock.patch.dict(sys.modules, {"ArenaType": module}):
            selected = helpers["_resolve_real_arena_type"](
                2, "02_malinovka", "ctf")

        self.assertIs(ctf, selected)
        self.assertEqual("assault", assault.gameplayName)
        self.assertEqual("assault-flag", assault.controlPoint)

    def test_resolver_scan_still_requires_matching_gameplay(self):
        helpers, unused_notes, unused_errors = load_arena_helpers()
        assault = types.SimpleNamespace(
            geometryName="spaces/02_malinovka", gameplayName="assault",
            gameplayID=1)
        ctf = types.SimpleNamespace(
            geometryName="spaces/02_malinovka", gameplayName="ctf",
            gameplayID=0)
        module = self._arena_module({100: assault, 101: ctf})

        with mock.patch.dict(sys.modules, {"ArenaType": module}):
            selected = helpers["_resolve_real_arena_type"](
                999, "02_malinovka", "ctf")

        self.assertIs(ctf, selected)

    def test_ctf_visibility_bit_is_written_to_the_mapped_space(self):
        helpers, notes, errors = load_arena_helpers()
        writes = []
        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_setSpaceItemsVisibilityMask = (
            lambda space_id, mask: writes.append((space_id, mask)))
        module = self._arena_module({})
        arena_type = types.SimpleNamespace(gameplayID=0)

        with mock.patch.dict(
                sys.modules, {"BigWorld": bigworld, "ArenaType": module}):
            result = helpers["_offh_apply_space_visibility_mask"](
                17, arena_type, "ctf")

        self.assertTrue(result)
        self.assertEqual([(17, 1)], writes)
        self.assertEqual([], errors)
        self.assertIn("gameplay=ctf id=0 mask=0x1", notes[0])

    def test_space_visibility_is_applied_after_geometry_mapping(self):
        source = SOURCE.read_text()
        mapping = source.index("BigWorld.addSpaceGeometryMapping(")
        visibility = source.index(
            "_offh_apply_space_visibility_mask(", mapping)

        self.assertLess(mapping, visibility)


if __name__ == "__main__":
    unittest.main()
