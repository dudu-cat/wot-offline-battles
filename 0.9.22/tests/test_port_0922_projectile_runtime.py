import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client' /
    'gui' / 'mods' / 'offline_lan_0922' / 'projectile_runtime.py')


def _load_module():
    spec = importlib.util.spec_from_file_location(
        'port_0922_projectile_runtime_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectileRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = _load_module()

    def test_trajectory_uses_signed_gravity_vector(self):
        position = self.runtime.trajectory_position(
            (1.0, 2.0, 3.0), (100.0, 10.0, -20.0),
            (0.0, -10.0, 2.0), 2.0)

        self.assertEqual((201.0, 2.0, -33.0), position)

    def test_absolute_trajectory_and_substeps_are_frame_rate_invariant(self):
        final_positions = []
        for frames_per_second in (30, 60, 120):
            chords = []
            for frame in range(frames_per_second):
                start = float(frame) / float(frames_per_second)
                end = float(frame + 1) / float(frames_per_second)
                chords.extend(self.runtime.substep_boundaries(start, end))

            self.assertAlmostEqual(0.0, chords[0][0])
            self.assertAlmostEqual(1.0, chords[-1][1])
            self.assertAlmostEqual(
                1.0, sum(end - start for start, end in chords))
            self.assertTrue(all(
                0.0 < end - start <=
                self.runtime.PROJECTILE_MAX_SUBSTEP_SECONDS + 1e-9
                for start, end in chords))
            final_positions.append(self.runtime.trajectory_position(
                (3.0, 7.0, -2.0), (80.0, 15.0, 12.0),
                (0.0, -9.81, 0.0), chords[-1][1]))

        for position in final_positions:
            self.assertEqual(final_positions[0], position)

    def test_relative_sweep_hit_is_invariant_at_30_60_and_120_fps(self):
        # Projectile x=100t crosses target x=50+20t at t=0.625. The target hit
        # tester is available only in each rendered frame's current matrix.
        for frames_per_second in (30, 60, 120):
            hit = False
            frame_time = 1.0 / float(frames_per_second)
            for frame in range(frames_per_second):
                frame_start = float(frame) * frame_time
                frame_end = float(frame + 1) * frame_time
                target_previous = (50.0 + 20.0 * frame_start, 0.0, 0.0)
                target_current = (50.0 + 20.0 * frame_end, 0.0, 0.0)
                for start_time, end_time in self.runtime.substep_boundaries(
                        frame_start, frame_end):
                    interval_start = (
                        (start_time - frame_start) / frame_time)
                    interval_end = ((end_time - frame_start) / frame_time)
                    adjusted_start, adjusted_end = (
                        self.runtime.compensate_segment_for_moving_target(
                            (100.0 * start_time, 0.0, 0.0),
                            (100.0 * end_time, 0.0, 0.0),
                            target_previous, target_current,
                            interval_start, interval_end))
                    if self.runtime.point_segment_distance_sq(
                            target_current, adjusted_start,
                            adjusted_end) <= 1e-12:
                        hit = True
                        break
                if hit:
                    break
            self.assertTrue(hit, '%s FPS missed relative sweep' %
                            frames_per_second)

    def test_slow_frame_is_fully_covered_by_bounded_substeps(self):
        chords = list(self.runtime.substep_boundaries(0.10, 0.21, 0.025))

        self.assertEqual(0.10, chords[0][0])
        self.assertAlmostEqual(0.21, chords[-1][1])
        self.assertTrue(all(end > start for start, end in chords))
        self.assertTrue(all(
            end - start <= 0.0250001 for start, end in chords))
        self.assertAlmostEqual(
            0.11, sum(end - start for start, end in chords))

    def test_relative_sweep_crosses_target_in_current_collision_frame(self):
        adjusted_start, adjusted_end = (
            self.runtime.compensate_segment_for_moving_target(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                (5.0, 0.0, 0.0), (7.0, 0.0, 0.0)))

        self.assertEqual((2.0, 0.0, 0.0), adjusted_start)
        self.assertEqual((10.0, 0.0, 0.0), adjusted_end)
        fraction = ((7.0 - adjusted_start[0]) /
                    (adjusted_end[0] - adjusted_start[0]))
        self.assertAlmostEqual(0.625, fraction)
        self.assertAlmostEqual(6.25, 10.0 * fraction)
        self.assertAlmostEqual(6.25, 5.0 + 2.0 * fraction)

    def test_relative_sweep_honours_partial_frame_interval(self):
        adjusted_start, adjusted_end = (
            self.runtime.compensate_segment_for_moving_target(
                (20.0, 0.0, 0.0), (30.0, 0.0, 0.0),
                (0.0, 0.0, 0.0), (8.0, 0.0, 0.0), 0.25, 0.75))

        self.assertEqual((26.0, 0.0, 0.0), adjusted_start)
        self.assertEqual((32.0, 0.0, 0.0), adjusted_end)

    def test_broadphase_distance_clamps_to_finite_segment(self):
        self.assertEqual(4.0, self.runtime.point_segment_distance_sq(
            (5.0, 2.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))
        self.assertEqual(29.0, self.runtime.point_segment_distance_sq(
            (15.0, 2.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))


if __name__ == '__main__':
    unittest.main()
