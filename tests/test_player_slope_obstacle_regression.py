import math
import importlib.util
import sys
import textwrap
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
COLLISION = ROOT / "scripts/client/gui/mods/offhangar/vehicle_collision.py"


class _Vector3(object):
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __sub__(self, other):
        return _Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)


class PlayerSlopeObstacleRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BATTLE.read_text()

    def _load_drive_pitch(self, collide_segment, material_info):
        helper_start = self.source.index(
            "\t\t\t\tdef _support_drive_pitch("
        )
        helper_end = self.source.index(
            "\n\t\t\t\tdef _check_horizontal_collision(", helper_start
        )
        helper_source = textwrap.dedent(
            self.source[helper_start:helper_end]
        )

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = collide_segment
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        namespace = {
            "_get_destr_authority": lambda: types.SimpleNamespace(
                is_destroyed=lambda *unused: False
            ),
            "_offh_destructible_mat_passable": (
                lambda hit, *unused: hit is not None
            ),
            "_offh_mat_info_for_segment_hit": material_info,
            "_offh_perf_count": lambda *unused: None,
            "math": math,
        }
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            exec(helper_source, namespace)
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math
        return namespace["_drive_pitch"], bigworld, math_module

    def _run_drive_pitch(self, collide_segment, material_info):
        drive_pitch, bigworld, math_module = self._load_drive_pitch(
            collide_segment, material_info
        )
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            return drive_pitch(7, 0.0, 0.0, 0.0, 0.0)
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math

    def _run_horizontal_collision(self, ground_heights, horizontal_hit_z,
                                  horizontal_normal_y=1.0,
                                  repeat_horizontal_contact=False,
                                  horizontal_collision_flags=None,
                                  horizontal_normal_z=None,
                                  hard_wall_z=None,
                                  return_solid_attempts=False,
                                  destructible_delivery=False,
                                  additional_terrain_z=None):
        collision_start = self.source.index(
            "\t\t\t\tdef _check_horizontal_collision("
        )
        collision_end = self.source.index(
            "\n\t\t\t\tdef _offh_land_impact(", collision_start
        )
        collision_source = textwrap.dedent(
            self.source[collision_start:collision_end]
        )

        collision_spec = importlib.util.spec_from_file_location(
            "player_slope_vehicle_collision", COLLISION
        )
        collision_module = importlib.util.module_from_spec(collision_spec)
        collision_spec.loader.exec_module(collision_module)

        def collide_segment(unused_space_id, start, end, unused_mask,
                            collision_filter=None):
            if abs(start.y - end.y) > 0.001:
                if 8 & unused_mask:
                    return None
                if (collision_filter is not None and
                        not collision_filter(0, 8, 0, 0)):
                    return None
                sample_index = int(round((start.z / 3.9) * 6.0))
                sample_index = max(0, min(len(ground_heights) - 1, sample_index))
                return (
                    _Vector3(start.x, ground_heights[sample_index], start.z),
                    _Vector3(0.0, 1.0, 0.0),
                )
            if repeat_horizontal_contact:
                horizontal_hits = (start.z + 0.01,)
            else:
                horizontal_hits = (
                    horizontal_hit_z
                    if isinstance(horizontal_hit_z, (list, tuple))
                    else (horizontal_hit_z,)
                )
            eligible_hits = [
                hit_z for hit_z in horizontal_hits
                if start.z <= hit_z <= end.z
            ]
            if abs(start.y - 0.6) < 0.001:
                collision_flags = horizontal_collision_flags
                if collision_flags is None:
                    collision_flags = 8 if horizontal_normal_y > 0.5 else 0
                normal_z = horizontal_normal_z
                if normal_z is None:
                    normal_z = 1.0 - horizontal_normal_y
                candidates = [
                    (
                        hit_z,
                        _Vector3(0.0, horizontal_normal_y, normal_z),
                        collision_flags,
                    )
                    for hit_z in eligible_hits
                ]
                if (hard_wall_z is not None and
                        start.z <= hard_wall_z <= end.z):
                    candidates.append((
                        hard_wall_z,
                        _Vector3(0.0, 0.0, -1.0),
                        0,
                    ))
                if (additional_terrain_z is not None and
                        start.z <= additional_terrain_z <= end.z):
                    candidates.append((
                        additional_terrain_z,
                        _Vector3(0.0, 1.0, 0.0),
                        8,
                    ))
                for hit_z, normal, candidate_flags in sorted(candidates):
                    if candidate_flags & unused_mask:
                        continue
                    if (collision_filter is not None and
                            not collision_filter(0, candidate_flags, 0, 0)):
                        continue
                    return (_Vector3(start.x, start.y, hit_z), normal)
            return None

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = collide_segment
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        solid_attempts = []

        def try_destroy_solid_hit(*args):
            solid_attempts.append(args[2].z)
            return destructible_delivery

        namespace = {
            "_offh_perf_count": lambda *unused: None,
            "_try_destroy_solid_hit": try_destroy_solid_hit,
            "_VC": collision_module,
            "math": math,
        }
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            exec(collision_source, namespace)
            blocked = namespace["_check_horizontal_collision"](
                object(), 7, _Vector3(0.0, 0.0, 0.0), 0.0, 5.0,
                None, False, 0.04,
            )
            if return_solid_attempts:
                return blocked, solid_attempts
            return blocked
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math

    def _run_uphill_with_optional_thin_wall(
            self, wall_z, wall_normal_y=0.0, engine_error=None,
            terrain_gradient=0.3, wall_top=None,
            destructible_delivery=False, return_solid_attempts=False):
        collision_start = self.source.index(
            "\t\t\t\tdef _check_horizontal_collision("
        )
        collision_end = self.source.index(
            "\n\t\t\t\tdef _offh_land_impact(", collision_start
        )
        collision_source = textwrap.dedent(
            self.source[collision_start:collision_end]
        )

        collision_spec = importlib.util.spec_from_file_location(
            "player_uphill_wall_vehicle_collision", COLLISION
        )
        collision_module = importlib.util.module_from_spec(collision_spec)
        collision_spec.loader.exec_module(collision_module)
        injected_errors = []

        def collide_segment(unused_space_id, start, end, unused_mask,
                            collision_filter=None):
            if engine_error == "mask136" and unused_mask == 136:
                injected_errors.append("mask136")
                raise RuntimeError("mask136 collision query failed")
            if (engine_error == "terrain_callback" and
                    collision_filter is not None):
                injected_errors.append("terrain_callback")
                raise RuntimeError("terrain callback query failed")
            if abs(start.y - end.y) > 0.001:
                if 8 & unused_mask:
                    return None
                if (collision_filter is not None and
                        not collision_filter(0, 8, 0, 0)):
                    return None
                # The wall is an infinitesimally thin vertical face, so a sparse
                # vertical profile sees only the continuous uphill terrain.
                return (
                    _Vector3(
                        start.x,
                        terrain_gradient * start.z,
                        start.z,
                    ),
                    _Vector3(0.0, 1.0, 0.0),
                )
            if end.z <= start.z:
                return None

            candidates = []
            terrain_z = start.y / terrain_gradient
            if start.z <= terrain_z <= end.z:
                candidates.append((
                    terrain_z,
                    _Vector3(0.0, 1.0, -terrain_gradient),
                    8,
                ))
            if (wall_z is not None and start.z <= wall_z <= end.z and
                    (wall_top is None or start.y <= wall_top)):
                wall_normal_z = -math.sqrt(max(
                    0.0, 1.0 - wall_normal_y * wall_normal_y
                ))
                candidates.append((
                    wall_z,
                    _Vector3(0.0, wall_normal_y, wall_normal_z),
                    0,
                ))
            if not candidates:
                return None
            for hit_z, normal, collision_flags in sorted(
                    candidates, key=lambda item: item[0]):
                if collision_flags & unused_mask:
                    continue
                if (collision_filter is None or
                        collision_filter(0, collision_flags, 0, 0)):
                    return (_Vector3(start.x, start.y, hit_z), normal)
            return None

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = collide_segment
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        solid_attempts = []

        def try_destroy_solid_hit(*args):
            solid_attempts.append(args)
            return destructible_delivery

        namespace = {
            "_offh_perf_count": lambda *unused: None,
            "_try_destroy_solid_hit": try_destroy_solid_hit,
            "_VC": collision_module,
            "math": math,
        }
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            exec(collision_source, namespace)
            blocked = namespace["_check_horizontal_collision"](
                object(), 7, _Vector3(0.0, 0.0, 0.0), 0.0, 5.0,
                None, False, 0.04,
            )
            if engine_error is not None:
                self.assertIn(engine_error, injected_errors)
            if return_solid_attempts:
                return blocked, solid_attempts
            return blocked
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math

    def _run_multilane_uphill_contact(
            self, hard_lane=None, vertical_sees_hard_surface=False):
        collision_start = self.source.index(
            "\t\t\t\tdef _check_horizontal_collision("
        )
        collision_end = self.source.index(
            "\n\t\t\t\tdef _offh_land_impact(", collision_start
        )
        collision_source = textwrap.dedent(
            self.source[collision_start:collision_end]
        )

        collision_spec = importlib.util.spec_from_file_location(
            "player_multilane_uphill_vehicle_collision", COLLISION
        )
        collision_module = importlib.util.module_from_spec(collision_spec)
        collision_spec.loader.exec_module(collision_module)

        terrain_gradient = 0.18
        obstacle_z = 1.5

        def lane_base(x):
            if x < -1.0:
                return 0.05
            if x > 1.0:
                return -0.05
            return 0.0

        def collide_segment(unused_space_id, start, end, unused_mask,
                            collision_filter=None):
            if abs(start.y - end.y) > 0.001:
                lane = -1 if start.x < -1.0 else (1 if start.x > 1.0 else 0)
                if (vertical_sees_hard_surface and hard_lane == lane and
                        abs(start.z - obstacle_z) < 0.001):
                    # A top-down engine query first meets the same inclined
                    # hard surface that produced the horizontal contact. Its
                    # matching point and normal do not make it terrain.
                    if (not (0 & unused_mask) and
                            (collision_filter is None or
                             collision_filter(0, 0, 0, 0))):
                        return (
                            _Vector3(start.x, 0.6, start.z),
                            _Vector3(0.0, 0.8, -0.6),
                        )
                if 8 & unused_mask:
                    return None
                if (collision_filter is not None and
                        not collision_filter(0, 8, 0, 0)):
                    return None
                # Every vertical probe returns the real ground for its own lane.
                # A future lane/contact verifier can therefore distinguish the
                # hard side obstacle below from this traversable surface.
                return (
                    _Vector3(
                        start.x,
                        lane_base(start.x) + terrain_gradient * start.z,
                        start.z,
                    ),
                    _Vector3(0.0, 1.0, 0.0),
                )
            if end.z <= start.z:
                return None

            candidates = []
            ground_z = (
                start.y - lane_base(start.x)
            ) / terrain_gradient
            if start.z <= ground_z <= end.z:
                candidates.append((
                    ground_z,
                    _Vector3(0.0, 1.0, -terrain_gradient),
                    "ground",
                ))

            lane = -1 if start.x < -1.0 else (1 if start.x > 1.0 else 0)
            if (hard_lane == lane and
                    start.z <= obstacle_z <= end.z):
                # This is an intact inclined rock face, not the lane's ground.
                # Its upward-facing normal alone must not make it terrain.
                candidates.append((
                    obstacle_z,
                    _Vector3(0.0, 0.8, -0.6),
                    "hard_obstacle",
                ))
            if not candidates:
                return None
            for hit_z, normal, kind in sorted(
                    candidates, key=lambda item: item[0]):
                collision_flags = 8 if kind == "ground" else 0
                if collision_flags & unused_mask:
                    continue
                if (collision_filter is None or
                        collision_filter(0, collision_flags, 0, 0)):
                    return (_Vector3(start.x, start.y, hit_z), normal)
            return None

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = collide_segment
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        solid_attempts = []
        namespace = {
            "_offh_perf_count": lambda *unused: None,
            "_try_destroy_solid_hit": (
                lambda *args: solid_attempts.append(args) or False
            ),
            "_VC": collision_module,
            "math": math,
        }
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            exec(collision_source, namespace)
            blocked = namespace["_check_horizontal_collision"](
                object(), 7, _Vector3(0.0, 0.0, 0.0), 0.0, 5.0,
                None, False, 0.04,
            )
            return blocked, solid_attempts
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math

    def _run_hard_object_chain(self, object_zs, destructible_zs):
        collision_start = self.source.index(
            "\t\t\t\tdef _check_horizontal_collision("
        )
        collision_end = self.source.index(
            "\n\t\t\t\tdef _offh_land_impact(", collision_start
        )
        collision_source = textwrap.dedent(
            self.source[collision_start:collision_end]
        )

        collision_spec = importlib.util.spec_from_file_location(
            "player_hard_object_chain_vehicle_collision", COLLISION
        )
        collision_module = importlib.util.module_from_spec(collision_spec)
        collision_spec.loader.exec_module(collision_module)

        def collide_segment(unused_space_id, start, end, unused_mask,
                            collision_filter=None):
            if abs(start.y - end.y) > 0.001 or end.z <= start.z:
                return None
            for object_z in object_zs:
                if not (start.z <= object_z <= end.z):
                    continue
                if (collision_filter is not None and
                        not collision_filter(0, 0, 0, 0)):
                    continue
                return (
                    _Vector3(start.x, start.y, object_z),
                    _Vector3(0.0, 0.0, -1.0),
                )
            return None

        solid_attempts = []

        def try_destroy_solid_hit(*args):
            hit_z = args[2].z
            solid_attempts.append(hit_z)
            return any(
                abs(hit_z - destructible_z) < 0.001
                for destructible_z in destructible_zs
            )

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = collide_segment
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        namespace = {
            "_offh_perf_count": lambda *unused: None,
            "_try_destroy_solid_hit": try_destroy_solid_hit,
            "_VC": collision_module,
            "math": math,
        }
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            exec(collision_source, namespace)
            blocked = namespace["_check_horizontal_collision"](
                object(), 7, _Vector3(0.0, 0.0, 0.0), 0.0, 30.0,
                None, False, 0.2,
            )
            return blocked, solid_attempts
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math

    def _run_near_coincident_terrain_and_destructible(self):
        collision_start = self.source.index(
            "\t\t\t\tdef _check_horizontal_collision("
        )
        collision_end = self.source.index(
            "\n\t\t\t\tdef _offh_land_impact(", collision_start
        )
        collision_source = textwrap.dedent(
            self.source[collision_start:collision_end]
        )

        collision_spec = importlib.util.spec_from_file_location(
            "player_coincident_terrain_vehicle_collision", COLLISION
        )
        collision_module = importlib.util.module_from_spec(collision_spec)
        collision_spec.loader.exec_module(collision_module)

        terrain_z = 3.0
        destructible_z = 3.0005
        terrain_gradient = 1.5

        def collide_segment(unused_space_id, start, end, unused_mask,
                            collision_filter=None):
            if abs(start.y - end.y) > 0.001:
                if 8 & unused_mask:
                    return None
                if (collision_filter is not None and
                        not collision_filter(0, 8, 0, 0)):
                    return None
                return (
                    _Vector3(
                        start.x,
                        terrain_gradient * start.z,
                        start.z,
                    ),
                    _Vector3(0.0, 1.0, 0.0),
                )
            if end.z <= start.z:
                return None
            candidates = (
                (terrain_z, _Vector3(0.0, 1.0, -terrain_gradient), 8),
                (destructible_z, _Vector3(0.0, 0.0, -1.0), 0),
            )
            for hit_z, normal, collision_flags in candidates:
                if not (start.z <= hit_z <= end.z):
                    continue
                if collision_flags & unused_mask:
                    continue
                if (collision_filter is None or
                        collision_filter(0, collision_flags, 0, 0)):
                    return (_Vector3(start.x, start.y, hit_z), normal)
            return None

        solid_attempts = []

        def try_destroy_solid_hit(*args):
            solid_attempts.append(args[2].z)
            return abs(args[2].z - destructible_z) < 0.0001

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = collide_segment
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        namespace = {
            "_offh_perf_count": lambda *unused: None,
            "_try_destroy_solid_hit": try_destroy_solid_hit,
            "_VC": collision_module,
            "math": math,
        }
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            exec(collision_source, namespace)
            blocked = namespace["_check_horizontal_collision"](
                object(), 7, _Vector3(0.0, 0.0, 0.0), 0.0, 5.0,
                None, False, 0.04,
            )
            return blocked, solid_attempts
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math

    def test_drive_pitch_continues_below_a_low_destructible_top(self):
        calls = []

        def collide_segment(unused_space_id, start, unused_end, unused_mask):
            calls.append((start.y, start.z))
            if start.z > 0.0:
                # The first front probe hits a low pole/fence top. A second ray
                # from below that top reaches the real, gently rising terrain.
                hit_y = 2.0 if start.y > 2.0 else 0.4
            else:
                hit_y = 0.0
            return (
                _Vector3(start.x, hit_y, start.z),
                _Vector3(0.0, 1.0, 0.0),
            )

        def material_info(unused_space_id, hit_point, unused_normal):
            if abs(hit_point.y - 2.0) > 0.001:
                return None
            return (
                hit_point,
                _Vector3(0.0, 1.0, 0.0),
                100,
                7,
                73,
                "content/GatesAndFences/low_fence.model",
            )

        pitch = self._run_drive_pitch(collide_segment, material_info)

        self.assertAlmostEqual(-math.atan2(0.4, 4.0), pitch, places=6)
        self.assertGreaterEqual(
            len([call for call in calls if call[1] > 0.0]), 2
        )

    def test_drive_pitch_keeps_the_first_complete_ground_hit(self):
        calls = []

        def collide_segment(unused_space_id, start, unused_end, unused_mask):
            calls.append((start.y, start.z))
            hit_y = 0.4 if start.z > 0.0 else 0.0
            return (
                _Vector3(start.x, hit_y, start.z),
                _Vector3(0.0, 1.0, 0.0),
            )

        pitch = self._run_drive_pitch(
            collide_segment,
            lambda *unused: None,
        )

        self.assertAlmostEqual(-math.atan2(0.4, 4.0), pitch, places=6)
        self.assertEqual(2, len(calls))

    def test_rounded_bump_is_not_classified_as_a_wall(self):
        blocked = self._run_horizontal_collision(
            [0.0, 0.2, 0.45, 0.65, 0.45, 0.2, 0.0],
            1.8,
        )

        self.assertFalse(blocked)

    def test_rounded_bump_multiple_ground_contacts_remain_drivable(self):
        blocked = self._run_horizontal_collision(
            [0.0, 0.2, 0.45, 0.65, 0.45, 0.2, 0.0],
            (1.8, 2.2),
        )

        self.assertFalse(blocked)

    def test_gradual_downhill_is_not_classified_as_a_wall(self):
        blocked = self._run_horizontal_collision(
            [0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0],
            0.0,
        )

        self.assertFalse(blocked)

    def test_gradual_downhill_does_not_hide_a_vertical_wall_contact(self):
        blocked = self._run_horizontal_collision(
            [0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0],
            0.0,
            horizontal_normal_y=0.0,
        )

        self.assertTrue(blocked)

    def test_smooth_uphill_without_a_wall_remains_drivable(self):
        blocked = self._run_uphill_with_optional_thin_wall(None)

        self.assertFalse(blocked)

    def test_smooth_uphill_first_hit_cannot_hide_a_thin_wall(self):
        # The lower hull ray meets terrain at z=2.0 first. The wall is still
        # inside the same z=3.9 sweep and must not disappear behind that hit.
        blocked = self._run_uphill_with_optional_thin_wall(3.7)

        self.assertTrue(blocked)

    def test_smooth_uphill_cannot_hide_a_later_upward_hard_surface(self):
        blocked = self._run_uphill_with_optional_thin_wall(
            3.7,
            wall_normal_y=0.8,
        )

        self.assertTrue(blocked)

    def test_mask136_engine_error_fails_closed_with_wall_behind_terrain(self):
        blocked = self._run_uphill_with_optional_thin_wall(
            3.7,
            engine_error="mask136",
        )

        self.assertTrue(blocked)

    def test_terrain_callback_engine_error_fails_closed_with_wall_behind_terrain(self):
        blocked = self._run_uphill_with_optional_thin_wall(
            2.0005,
            wall_normal_y=0.8,
            engine_error="terrain_callback",
            destructible_delivery=True,
        )

        self.assertTrue(blocked)

    def test_smooth_uphill_ground_contact_cannot_skip_adjacent_thin_wall(self):
        # Resuming 3 cm after the terrain hit must not jump over a thin wall
        # rooted immediately behind that terrain contact.
        blocked = self._run_uphill_with_optional_thin_wall(2.01)

        self.assertTrue(blocked)

    def test_low_hard_object_before_uphill_requires_exact_delivery(self):
        blocked, solid_attempts = self._run_uphill_with_optional_thin_wall(
            1.0,
            terrain_gradient=0.5,
            wall_top=0.9,
            destructible_delivery=False,
            return_solid_attempts=True,
        )

        self.assertTrue(blocked)
        self.assertGreaterEqual(len(solid_attempts), 1)
        self.assertAlmostEqual(1.0, solid_attempts[0][2].z, places=6)

    def test_low_destructible_before_uphill_passes_after_exact_delivery(self):
        blocked, solid_attempts = self._run_uphill_with_optional_thin_wall(
            1.0,
            terrain_gradient=0.5,
            wall_top=0.9,
            destructible_delivery=True,
            return_solid_attempts=True,
        )

        self.assertFalse(blocked)
        self.assertGreaterEqual(len(solid_attempts), 1)
        self.assertAlmostEqual(1.0, solid_attempts[0][2].z, places=6)

    def test_destroyed_hard_object_cannot_hide_intact_object_in_same_lane(self):
        blocked, solid_attempts = self._run_hard_object_chain(
            (4.0, 5.0),
            (4.0,),
        )

        self.assertTrue(blocked)
        self.assertIn(4.0, solid_attempts)
        self.assertIn(5.0, solid_attempts)

    def test_destroyed_hard_object_cannot_skip_adjacent_intact_object(self):
        blocked, solid_attempts = self._run_hard_object_chain(
            (3.600, 3.610),
            (3.600,),
        )

        self.assertTrue(blocked)
        self.assertIn(3.600, solid_attempts)
        self.assertIn(3.610, solid_attempts)

    def test_two_destroyed_hard_objects_pass_only_after_segment_is_clear(self):
        blocked, solid_attempts = self._run_hard_object_chain(
            (4.0, 5.0),
            (4.0, 5.0),
        )

        self.assertFalse(blocked)
        self.assertIn(4.0, solid_attempts)
        self.assertIn(5.0, solid_attempts)

    def test_destructible_chain_beyond_rescan_budget_fails_closed(self):
        object_zs = tuple(4.0 + 0.1 * index for index in range(8))
        blocked, unused_solid_attempts = self._run_hard_object_chain(
            object_zs,
            object_zs,
        )

        self.assertTrue(blocked)

    def test_near_coincident_destroyed_solid_still_profiles_steep_terrain(self):
        blocked, solid_attempts = (
            self._run_near_coincident_terrain_and_destructible()
        )

        self.assertTrue(blocked)
        self.assertIn(3.0005, solid_attempts)

    def test_side_lane_sloped_hard_obstacle_is_not_centerline_terrain(self):
        for hard_lane in (-1, 1):
            with self.subTest(hard_lane=hard_lane):
                blocked, unused_solid_attempts = (
                    self._run_multilane_uphill_contact(hard_lane)
                )

                self.assertTrue(blocked)

    def test_side_lane_sloped_hard_obstacle_cannot_self_certify_as_ground(self):
        blocked, unused_solid_attempts = self._run_multilane_uphill_contact(
            -1,
            vertical_sees_hard_surface=True,
        )

        self.assertTrue(blocked)

    def test_each_lane_matching_its_real_uphill_ground_remains_drivable(self):
        blocked, solid_attempts = self._run_multilane_uphill_contact()

        self.assertFalse(blocked)
        self.assertEqual([], solid_attempts)

    def test_flat_ground_does_not_hide_a_horizontal_wall(self):
        blocked = self._run_horizontal_collision(
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            1.5,
            horizontal_normal_y=0.0,
        )

        self.assertTrue(blocked)

    def test_flat_terrain_only_contact_does_not_block_player(self):
        blocked = self._run_horizontal_collision(
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            1.5,
        )

        self.assertFalse(blocked)

    def test_flat_terrain_contact_cannot_hide_later_hard_wall(self):
        blocked, solid_attempts = self._run_horizontal_collision(
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            1.5,
            hard_wall_z=3.0,
            return_solid_attempts=True,
        )

        self.assertTrue(blocked)
        self.assertIn(3.0, solid_attempts)

    def test_repeated_terrain_only_contacts_do_not_exhaust_sweep_budget(self):
        blocked = self._run_horizontal_collision(
            [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
            None,
            repeat_horizontal_contact=True,
        )

        self.assertFalse(blocked)

    def test_flat_sparse_profile_cannot_hide_overlimit_terrain_normal(self):
        # A narrow terrain ridge can intersect the exact lower sweep between
        # the seven vertical samples.  Its normalized contact normal still
        # proves a roughly 57-degree face, beyond the 1.28 gradient limit.
        blocked = self._run_horizontal_collision(
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            1.5,
            horizontal_normal_y=0.55,
            horizontal_collision_flags=8,
            horizontal_normal_z=-math.sqrt(1.0 - 0.55 * 0.55),
        )

        self.assertTrue(blocked)

    def test_overlimit_nonterrain_normal_passes_after_exact_destruction(self):
        blocked, solid_attempts = self._run_horizontal_collision(
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            1.5,
            horizontal_normal_y=0.55,
            horizontal_collision_flags=0,
            horizontal_normal_z=-math.sqrt(1.0 - 0.55 * 0.55),
            return_solid_attempts=True,
            destructible_delivery=True,
        )

        self.assertFalse(blocked)
        self.assertIn(1.5, solid_attempts)

    def test_near_flat_terrain_uses_terrain_normal_not_destructible_normal(self):
        blocked, solid_attempts = self._run_horizontal_collision(
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            1.5,
            horizontal_normal_y=0.55,
            horizontal_collision_flags=0,
            horizontal_normal_z=-math.sqrt(1.0 - 0.55 * 0.55),
            return_solid_attempts=True,
            destructible_delivery=True,
            additional_terrain_z=1.5005,
        )

        self.assertFalse(blocked)
        self.assertIn(1.5, solid_attempts)

    def _run_zero_speed_forward_contact(self, crushable):
        block_start = self.source.index("\t\t\t\t# Apply position")
        block_end = self.source.index(
            "\n\t\t\t\t# Tank-vs-tank:", block_start
        )
        movement_block = textwrap.dedent(
            self.source[block_start:block_end]
        )
        probe_inputs = []
        cleared = []

        def exact_sweep(unused_vehicle, unused_space_id, unused_pos,
                        unused_yaw, impact_velocity, *rest):
            sweep_direction = rest[-1]
            probe_inputs.append((impact_velocity, sweep_direction))
            if crushable and impact_velocity > 0.0:
                cleared.append(True)
                return False
            return True

        namespace = {
            "Math": types.SimpleNamespace(Vector3=_Vector3),
            "_check_horizontal_collision": exact_sweep,
            "_fell_trees_near": lambda *unused: None,
            "_offh_bspace": lambda: 7,
            "_veh_airborne": [False],
            "_veh_velocity": [0.0],
            "dt": 1.0 / 60.0,
            "loaded_models": {},
            "math": math,
            "mock_veh": object(),
            "player": types.SimpleNamespace(_offh_grind=0),
            "throttle": 1.0,
            "veh_pos": [0.0, 0.0, 0.0],
            "veh_yaw": [0.0],
        }
        exec(movement_block, namespace)
        return probe_inputs, cleared, namespace["veh_pos"]

    def test_forward_input_sweeps_without_inventing_impact_velocity(self):
        probe_inputs, cleared, unused_position = (
            self._run_zero_speed_forward_contact(True)
        )

        self.assertTrue(probe_inputs)
        self.assertEqual(0.0, probe_inputs[0][0])
        self.assertGreater(probe_inputs[0][1], 0.0)
        self.assertEqual([], cleared)

    def test_zero_speed_forward_sweep_keeps_a_hard_wall_blocking(self):
        probe_inputs, cleared, position = (
            self._run_zero_speed_forward_contact(False)
        )

        self.assertTrue(probe_inputs)
        self.assertEqual(0.0, probe_inputs[0][0])
        self.assertGreater(probe_inputs[0][1], 0.0)
        self.assertEqual([], cleared)
        self.assertEqual([0.0, 0.0, 0.0], position)


if __name__ == "__main__":
    unittest.main()
