import importlib.util
import math
import pathlib
import sys
import textwrap
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
COLLISION = ROOT / "scripts/client/gui/mods/offhangar/vehicle_collision.py"


class _Vector3(object):
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __sub__(self, other):
        return _Vector3(
            self.x - other.x,
            self.y - other.y,
            self.z - other.z,
        )

    @property
    def length(self):
        return math.sqrt(
            self.x * self.x + self.y * self.y + self.z * self.z
        )


def load_collision_module():
    spec = importlib.util.spec_from_file_location(
        "player_native_vehicle_collision", COLLISION
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def aih_tick_source(source):
    start = source.index("\t\tdef _aih_tick():")
    end = source.index(
        "\n\t\tglobals()['g_offh_aih_callback_id'] = BigWorld.callback(",
        start,
    )
    return source[start:end]


def horizontal_collision_source(source):
    tick = aih_tick_source(source)
    start = tick.index("def _check_horizontal_collision")
    end = tick.index("def _offh_land_impact", start)
    return textwrap.dedent(tick[start:end])


def compile_tick_collision_probe(source, collision, late_local_import=False):
    nested_collision = textwrap.indent(
        horizontal_collision_source(source), "    "
    )
    call = (
        "    blocked = _check_horizontal_collision(\n"
        "        object(), 7, _Vector3(0.0, 0.0, 0.0), 0.0, 5.0,\n"
        "        None, False, 0.04,\n"
        "    )\n"
    )
    if late_local_import:
        # This is the late binding that existed in 2b12b0d. Although it runs
        # after the collision call, Python makes _VC local to the whole tick.
        call += (
            "    from gui.mods.offhangar import vehicle_collision as _VC\n"
        )
    call += "    return blocked\n"
    namespace = {
        "_VC": collision,
        "_Vector3": _Vector3,
        "_offh_perf_count": lambda *unused: None,
        "_try_destroy_solid_hit": lambda *unused: False,
        "math": math,
    }
    exec("def probe():\n" + nested_collision + call, namespace)
    return namespace["probe"]


def run_tick_collision_probe(probe, shallow_terrain):
    bigworld = types.ModuleType("BigWorld")
    math_module = types.ModuleType("Math")
    math_module.Vector3 = _Vector3

    def collide_segment(
        unused_space_id, start, end, collision_mask, collision_filter=None
    ):
        if not shallow_terrain or collision_mask == 136:
            return None
        if abs(start.y - end.y) > 0.001:
            if collision_filter is not None and not collision_filter(
                0, 8, 0, 0
            ):
                return None
            return (
                _Vector3(start.x, 0.30 * start.z, start.z),
                _Vector3(0.0, 1.0, -0.30),
            )
        if abs(start.y - 0.6) < 0.001 and start.z <= 2.0 <= end.z:
            if collision_filter is not None and not collision_filter(
                0, 8, 0, 0
            ):
                return None
            return (
                _Vector3(start.x, start.y, 2.0),
                _Vector3(0.0, 1.0, -0.30),
            )
        return None

    bigworld.wg_collideSegment = collide_segment
    gui = types.ModuleType("gui")
    gui.__path__ = []
    mods = types.ModuleType("gui.mods")
    mods.__path__ = []
    offhangar = types.ModuleType("gui.mods.offhangar")
    offhangar.__path__ = []
    collision = load_collision_module()
    gui.mods = mods
    mods.offhangar = offhangar
    offhangar.vehicle_collision = collision
    module_overrides = {
        "BigWorld": bigworld,
        "Math": math_module,
        "gui": gui,
        "gui.mods": mods,
        "gui.mods.offhangar": offhangar,
        "gui.mods.offhangar.vehicle_collision": collision,
    }
    dependency_errors = []

    def trace(frame, event, argument):
        if event == "exception":
            error_type, error, unused_traceback = argument
            if error_type in (NameError, UnboundLocalError) and "_VC" in str(
                error
            ):
                dependency_errors.append(
                    (frame.f_code.co_name, error_type.__name__, str(error))
                )
        return trace

    with mock.patch.dict(sys.modules, module_overrides):
        previous_trace = sys.gettrace()
        sys.settrace(trace)
        try:
            blocked = probe()
        finally:
            sys.settrace(previous_trace)
    return blocked, dependency_errors


class PlayerNativeCollisionContractTests(unittest.TestCase):
    def test_tick_scope_executes_flat_and_shallow_terrain_contract(self):
        source = BATTLE.read_text(encoding="utf-8")
        collision = load_collision_module()
        baseline_probe = compile_tick_collision_probe(
            source, collision, late_local_import=True
        )
        current_probe = compile_tick_collision_probe(source, collision)

        baseline_flat = run_tick_collision_probe(baseline_probe, False)
        baseline_shallow = run_tick_collision_probe(baseline_probe, True)
        current_flat = run_tick_collision_probe(current_probe, False)
        current_shallow = run_tick_collision_probe(current_probe, True)

        self.assertEqual((False, []), baseline_flat)
        self.assertTrue(baseline_shallow[0])
        self.assertTrue(baseline_shallow[1])
        self.assertEqual("_terrain_profile_overlimit", baseline_shallow[1][0][0])
        self.assertEqual((False, []), current_flat)
        self.assertEqual((False, []), current_shallow)

    def test_aih_tick_does_not_shadow_battle_vehicle_collision_module(self):
        source = BATTLE.read_text(encoding="utf-8")
        tick = aih_tick_source(source)
        resolver_start = tick.index("def _tank_resolve")
        resolver_end = tick.index("def _support_drive_pitch", resolver_start)
        resolver = tick[resolver_start:resolver_end]
        slope_start = tick.index("def _check_horizontal_collision")
        slope_end = tick.index("def _offh_land_impact", slope_start)
        slope_collision = tick[slope_start:slope_end]

        self.assertIn("_VC.chassis_shape", resolver)
        self.assertIn(
            "_VC.TERRAIN_PROFILE_MAXIMUM_GRADIENT",
            slope_collision,
        )
        self.assertIn("_VC.drivable_rising_profile", slope_collision)
        shadowing_imports = [
            line.strip() for line in tick.splitlines()
            if line.strip() ==
            "from gui.mods.offhangar import vehicle_collision as _VC"
        ]
        self.assertEqual(
            [],
            shadowing_imports,
            "a tick-local _VC leaves both the tank resolver and the terrain "
            "slope classifier unbound until the later import executes",
        )

    def test_head_on_player_overlap_resolves_only_the_python_owned_body(self):
        collision = load_collision_module()
        shape = (1.5, 3.0, -0.5, 2.0)
        player_position = [0.0, 5.5]
        native_position = [0.0, 0.0]
        native_before = tuple(native_position)

        contact = collision.obb_contact(
            player_position[0], player_position[1], 0.0, shape,
            native_position[0], native_position[1], 0.0, shape,
        )
        self.assertIsNotNone(contact)
        response = collision.pair_response(
            contact,
            1.0 / 25_000.0,
            0.0,
            (0.0, -8.0),
            (0.0, 0.0),
            slop=0.0,
            percent=1.0,
        )

        player_position[0] += response[0]
        player_position[1] += response[1]
        native_position[0] += response[4]
        native_position[1] += response[5]
        player_velocity_after = (response[2], -8.0 + response[3])

        self.assertEqual(native_before, tuple(native_position))
        self.assertEqual((0.0, 0.0), response[4:6])
        self.assertEqual((0.0, 0.0), response[6:8])
        self.assertAlmostEqual(0.0, player_velocity_after[0])
        self.assertAlmostEqual(0.0, player_velocity_after[1])
        self.assertIsNone(collision.obb_contact(
            player_position[0], player_position[1], 0.0, shape,
            native_position[0], native_position[1], 0.0, shape,
        ))

    def test_player_collision_failure_is_counted_and_logged_once_per_battle(self):
        source = BATTLE.read_text(encoding="utf-8")
        tick = aih_tick_source(source)
        start = tick.index("# Tank-vs-tank: velocity-relative impulse")
        end = tick.index("# --- Hull Rotation", start)
        player_collision = tick[start:end]

        self.assertTrue(
            "except Exception as _player_collision_error:" in player_collision,
            "player collision failures must retain their exception value",
        )
        self.assertTrue(
            "_offh_perf_count('player_collision_error')" in player_collision,
            "sampled diagnostics must count player collision failures",
        )
        self.assertTrue(
            "g_offh_player_collision_error_gen" in player_collision,
            "the error log must be bounded to one report per battle generation",
        )
        self.assertTrue(
            "LOG_ERROR(" in player_collision,
            "a collision subsystem failure must not degrade to pass-through silently",
        )
        self.assertFalse(
            "except Exception:\n\t\t\t\t\tpass" in player_collision,
            "the player collision boundary must not swallow every failure",
        )


if __name__ == "__main__":
    unittest.main()
