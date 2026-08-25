import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import hull_aiming  # noqa: E402


class HullAiming1513RulesTests(unittest.TestCase):
    def test_static_yaw_uses_destroyed_movement_and_switching_gates(self):
        static = 0.0
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, engine_destroyed=True))
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, track_destroyed=True))
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, overturned=True))
        self.assertTrue(hull_aiming.static_yaw_locked(static, moving=True))
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, siege_state=hull_aiming.SWITCHING_ON))
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, siege_state=hull_aiming.SWITCHING_OFF))
        # A critical (yellow) engine and stationary hull rotation do not set
        # any of the native destroyed/moving inputs.
        self.assertFalse(hull_aiming.static_yaw_locked(static))
        self.assertFalse(hull_aiming.static_yaw_locked(None, moving=True))

    def test_static_pitch_ignores_tracks_and_movement(self):
        static = math.radians(-1.0)
        self.assertFalse(hull_aiming.static_pitch_locked(static))
        self.assertTrue(hull_aiming.static_pitch_locked(
            static, engine_destroyed=True))
        self.assertTrue(hull_aiming.static_pitch_locked(
            static, overturned=True))
        self.assertTrue(hull_aiming.static_pitch_locked(
            static, siege_state=hull_aiming.SWITCHING_ON))

    def test_four_vehicle_flat_combined_envelopes_are_derived(self):
        strv_min, strv_min_ok = hull_aiming.minimal_correction(
            math.radians(-15.0), math.radians(-4.0), math.radians(2.0),
            math.radians(-11.0), math.radians(11.0))
        strv_max, strv_max_ok = hull_aiming.minimal_correction(
            math.radians(13.0), math.radians(-4.0), math.radians(2.0),
            math.radians(-11.0), math.radians(11.0))
        udes_max, udes_max_ok = hull_aiming.minimal_correction(
            math.radians(14.0), math.radians(-20.0), 0.0,
            0.0, math.radians(14.0))

        self.assertTrue(strv_min_ok)
        self.assertTrue(strv_max_ok)
        self.assertTrue(udes_max_ok)
        self.assertAlmostEqual(math.radians(-11.0), strv_min)
        self.assertAlmostEqual(math.radians(11.0), strv_max)
        self.assertAlmostEqual(math.radians(14.0), udes_max)
        unused, reachable = hull_aiming.minimal_correction(
            math.radians(14.1), math.radians(-20.0), 0.0,
            0.0, math.radians(14.0))
        self.assertFalse(reachable)

    def test_slew_uses_descriptor_radians_per_second(self):
        self.assertAlmostEqual(
            math.radians(7.5),
            hull_aiming.slew(
                0.0, math.radians(11.0), math.radians(7.5), 1.0))
        self.assertAlmostEqual(
            math.radians(11.0),
            hull_aiming.slew(
                math.radians(7.5), math.radians(11.0),
                math.radians(7.5), 1.0))

    def test_static_pitch_crossing_doubles_then_obeys_turret_time(self):
        static = math.radians(-1.0)
        current = math.radians(-3.0)
        desired = math.radians(1.0)
        crossed = hull_aiming.gun_pitch_step(
            current, desired, static, math.radians(1.0), 1.0)
        coordinated = hull_aiming.gun_pitch_step(
            current, desired, static, math.radians(10.0), 0.5, 2.0)

        self.assertAlmostEqual(math.radians(-1.0), crossed)
        self.assertAlmostEqual(math.radians(-2.0), coordinated)

    def test_pitch_slew_clamps_raw_target_after_the_native_epsilon_gate(self):
        limits = (math.radians(-4.0), math.radians(2.0))
        self.assertEqual(
            limits[1], hull_aiming.gun_pitch_step(
                math.radians(20.0), math.radians(20.0), None,
                math.radians(1.0), 0.01, angle_limits=limits))
        self.assertEqual(
            math.radians(-3.0), hull_aiming.gun_pitch_step(
                math.radians(-3.0), math.radians(1.0), None,
                0.0, 10.0, angle_limits=limits))


if __name__ == '__main__':
    unittest.main()
