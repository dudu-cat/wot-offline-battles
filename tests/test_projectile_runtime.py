import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "scripts" / "client" / "gui" / "mods" / "offhangar" /
    "projectile_runtime.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("projectile_runtime_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectileRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = _load_module()

    def test_trajectory_uses_real_velocity_and_gravity(self):
        position = self.runtime.trajectory_position(
            (1.0, 2.0, 3.0), (100.0, 10.0, -20.0),
            (0.0, -10.0, 0.0), 2.0,
        )

        self.assertEqual((201.0, 2.0, -37.0), position)

    def test_relative_sweep_meets_target_at_its_interpolated_pose(self):
        adjusted_start, adjusted_end = (
            self.runtime.compensate_segment_for_moving_target(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                (5.0, 0.0, 0.0), (7.0, 0.0, 0.0),
            )
        )

        self.assertEqual((2.0, 0.0, 0.0), adjusted_start)
        self.assertEqual((10.0, 0.0, 0.0), adjusted_end)
        # In the target's current collision frame (x=7), the crossing occurs at
        # t=0.625. In world space the shell and target are both at x=6.25 then.
        fraction = (7.0 - adjusted_start[0]) / (
            adjusted_end[0] - adjusted_start[0]
        )
        self.assertAlmostEqual(0.625, fraction)
        self.assertAlmostEqual(6.25, 10.0 * fraction)
        self.assertAlmostEqual(6.25, 5.0 + 2.0 * fraction)

    def test_broadphase_distance_is_clamped_to_segment(self):
        self.assertEqual(
            4.0,
            self.runtime.point_segment_distance_sq(
                (5.0, 2.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)
            ),
        )
        self.assertEqual(
            29.0,
            self.runtime.point_segment_distance_sq(
                (15.0, 2.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)
            ),
        )

    def test_slow_frame_is_fully_covered_by_bounded_substeps(self):
        steps = list(self.runtime.substep_boundaries(0.10, 0.21, 0.025))

        self.assertEqual(0.10, steps[0][0])
        self.assertAlmostEqual(0.21, steps[-1][1])
        self.assertTrue(all(end > start for start, end in steps))
        self.assertTrue(all(end - start <= 0.0250001 for start, end in steps))
        self.assertAlmostEqual(0.11, sum(end - start for start, end in steps))


if __name__ == "__main__":
    unittest.main()
