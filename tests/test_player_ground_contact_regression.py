import importlib.util
import math
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
OFFLINE_BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
PHYSICS = ROOT / "scripts/client/gui/mods/offhangar/physics.py"
VEHICLE_COLLISION = (
    ROOT / "scripts/client/gui/mods/offhangar/vehicle_collision.py"
)
CLIENT_SCRIPTS = ROOT / "scripts/client"


def load_physics():
    spec = importlib.util.spec_from_file_location(
        "offhangar_player_ground_contact_physics", PHYSICS
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_vehicle_collision():
    spec = importlib.util.spec_from_file_location(
        "offhangar_player_ground_contact_collision", VEHICLE_COLLISION
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Player(object):
    pass


class _Vector3(object):
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class _LayeredCollision(object):
    def __init__(self, heights, normal_ys=None):
        self.heights = tuple(float(height) for height in heights)
        if normal_ys is None:
            normal_ys = (1.0,) * len(self.heights)
        self.normal_ys = tuple(float(normal_y) for normal_y in normal_ys)

    def collide(self, unused_space_id, start, end, unused_mask):
        for height, normal_y in zip(self.heights, self.normal_ys):
            if start.y >= height >= end.y:
                return (
                    _Vector3(start.x, height, start.z),
                    _Vector3(0.0, normal_y, 0.0),
                )
        return None


class PlayerGroundContactRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = OFFLINE_BATTLE.read_text()
        marker = source.index("\t\t\t\t# --- Ground contact:")
        start = source.index("\t\t\t\ttry:", marker)
        end = source.index("\n\t\t\t\t# --- Drowning:", start)
        cls.ground_contact_block = textwrap.dedent(source[start:end]).replace(
            "from gui.mods.offhangar import vehicle_collision as _VC",
            "_VC = _test_vehicle_collision",
        )
        support_start = source.index("\t\t\t\tdef _terrain_support")
        support_end = source.index(
            "\n\t\t\t\tdef _try_destroy_destructible", support_start
        )
        cls.terrain_support_function = textwrap.dedent(
            source[support_start:support_end]
        )
        cls.physics = load_physics()
        cls.vehicle_collision = load_vehicle_collision()
        client_scripts = str(CLIENT_SCRIPTS)
        if client_scripts not in sys.path:
            sys.path.insert(0, client_scripts)

    def _run_ground_contact(
        self,
        support,
        body_y=0.0,
        fall_armed=True,
        airborne=False,
        vertical_velocity=0.0,
        ticks=1,
    ):
        player = _Player()
        player._offh_buried = 0
        terrain_support = support if callable(support) else lambda *unused: support
        namespace = {
            "loaded_models": {},
            "_terrain_support": terrain_support,
            "_offh_bspace": lambda: 1,
            "veh_pos": [0.0, float(body_y), 0.0],
            "veh_yaw": [0.0],
            "player": player,
            "_veh_velocity": [0.0],
            "dt": 0.05,
            "_veh_fall_armed": [bool(fall_armed)],
            "_veh_vert_vel": [float(vertical_velocity)],
            "_veh_airborne": [bool(airborne)],
            "_phys_gravity": 12.2625,
            "_offh_land_impact": lambda *unused: None,
            "_test_vehicle_collision": self.vehicle_collision,
            "LOG_DEBUG": lambda *unused: None,
            "math": math,
        }
        for unused in range(ticks):
            exec(self.ground_contact_block, namespace)
        return namespace

    def _layered_support(self, upper, lower):
        maximum_y_calls = []

        def terrain_support(*args, **kwargs):
            maximum_y = kwargs.get("maximum_y")
            if len(args) >= 7:
                maximum_y = args[6]
            maximum_y_calls.append(maximum_y)
            return upper if maximum_y is None else lower

        return terrain_support, maximum_y_calls

    def _run_real_support_probe(self, heights, maximum_y, normal_ys=None):
        collision = _LayeredCollision(heights, normal_ys)
        big_world = types.ModuleType("BigWorld")
        big_world.wg_collideSegment = collision.collide
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        namespace = {
            "math": math,
            "_offh_perf_count": lambda *unused: None,
            "_offh_mat_info_for_segment_hit": lambda *unused: None,
        }
        exec(self.terrain_support_function, namespace)
        with mock.patch.dict(
            sys.modules, {"BigWorld": big_world, "Math": math_module}
        ):
            try:
                return namespace["_terrain_support"](
                    1, 0.0, 0.0, 0.0, 0.0, 2.5, maximum_y=maximum_y
                )
            except TypeError as error:
                self.fail(
                    "_terrain_support must accept maximum_y: %s" % error
                )

    def test_support_probe_skips_intact_upper_surface_above_maximum_y(self):
        support = self._run_real_support_probe((0.7, 0.0), maximum_y=0.62)

        self.assertEqual((0.0, 0.0, 0.0, 0.0), support)

    def test_support_probe_returns_none_below_rejected_upper_surface_over_void(self):
        support = self._run_real_support_probe((0.7,), maximum_y=0.62)

        self.assertEqual((None, None, None, None), support)

    def test_support_probe_rejects_downward_underside_over_void(self):
        support = self._run_real_support_probe(
            (0.7, 0.3),
            maximum_y=0.62,
            normal_ys=(1.0, -1.0),
        )

        self.assertEqual((None, None, None, None), support)

    def test_support_probe_continues_below_downward_underside_to_ground(self):
        support = self._run_real_support_probe(
            (0.7, 0.3, 0.0),
            maximum_y=0.62,
            normal_ys=(1.0, -1.0, 1.0),
        )

        self.assertEqual((0.0, 0.0, 0.0, 0.0), support)

    def test_rejected_upper_surface_uses_real_lower_ground(self):
        support, maximum_y_calls = self._layered_support(
            (0.7, 0.7, 0.7, 0.7),
            (0.0, 0.0, 0.0, 0.0),
        )
        state = self._run_ground_contact(support)

        self.assertFalse(state["_veh_airborne"][0])
        self.assertEqual(0.0, state["veh_pos"][1])
        self.assertEqual(0.0, state["_veh_vert_vel"][0])
        self.assertEqual(2, len(maximum_y_calls))
        self.assertIsNone(maximum_y_calls[0])
        self.assertIsNotNone(maximum_y_calls[1])

    def test_real_lower_ground_restores_forward_and_reverse_traction(self):
        support, maximum_y_calls = self._layered_support(
            (0.7, 0.7, 0.7, 0.7),
            (0.0, 0.0, 0.0, 0.0),
        )
        state = self._run_ground_contact(support)
        params = dict(self.physics._DEFAULTS)

        forward = self.physics.longitudinal_step(
            params, 0.0, 1.0, False, 0.0, 0.05,
            state["_veh_airborne"][0]
        )
        reverse = self.physics.longitudinal_step(
            params, 0.0, -1.0, False, 0.0, 0.05,
            state["_veh_airborne"][0]
        )

        self.assertGreater(forward, 0.0)
        self.assertLess(reverse, 0.0)
        self.assertEqual(2, len(maximum_y_calls))
        self.assertIsNotNone(maximum_y_calls[1])

    def test_rejected_upper_surface_without_lower_ground_enters_airborne(self):
        support, maximum_y_calls = self._layered_support(
            (0.7, 0.7, 0.7, 0.7),
            (None, None, None, None),
        )
        state = self._run_ground_contact(support)

        self.assertEqual(2, len(maximum_y_calls))
        self.assertTrue(state["_veh_airborne"][0])
        self.assertLess(state["veh_pos"][1], 0.0)
        self.assertLess(state["_veh_vert_vel"][0], 0.0)

    def test_repeated_rejected_upper_surface_over_void_cannot_hover(self):
        support, maximum_y_calls = self._layered_support(
            (0.7, 0.7, 0.7, 0.7),
            (None, None, None, None),
        )
        state = self._run_ground_contact(support, ticks=100)

        self.assertTrue(any(
            maximum_y is not None for maximum_y in maximum_y_calls
        ))
        self.assertTrue(state["_veh_airborne"][0])
        self.assertLess(state["veh_pos"][1], -1.0)
        self.assertLess(state["_veh_vert_vel"][0], 0.0)

    def test_spawn_unarmed_still_holds_when_no_live_support_exists(self):
        support, unused_calls = self._layered_support(
            (100.7, 100.7, 100.7, 100.7),
            (None, None, None, None),
        )
        state = self._run_ground_contact(
            support, body_y=100.0, fall_armed=False
        )

        self.assertEqual(100.0, state["veh_pos"][1])
        self.assertEqual(0.0, state["_veh_vert_vel"][0])
        self.assertFalse(state["_veh_airborne"][0])
        self.assertFalse(state["_veh_fall_armed"][0])

    def test_already_airborne_tank_keeps_falling_without_lower_support(self):
        support, unused_calls = self._layered_support(
            (0.7, 0.7, 0.7, 0.7),
            (None, None, None, None),
        )
        state = self._run_ground_contact(
            support, airborne=True, vertical_velocity=-2.0
        )

        self.assertTrue(state["_veh_airborne"][0])
        self.assertLess(state["veh_pos"][1], 0.0)
        self.assertLess(state["_veh_vert_vel"][0], -2.0)

    def test_missing_centre_support_still_enters_airborne_state(self):
        state = self._run_ground_contact((None, None, None, None))

        self.assertTrue(state["_veh_airborne"][0])
        self.assertLess(state["veh_pos"][1], 0.0)
        self.assertLess(state["_veh_vert_vel"][0], 0.0)


if __name__ == "__main__":
    unittest.main()
