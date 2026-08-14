from pathlib import Path
import math
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import vehicle_physics


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class VehiclePhysicsDescriptorTests(unittest.TestCase):

    def test_rotation_speed_reads_native_1513_chassis_attribute(self):
        descriptor = types.SimpleNamespace(
            physics={},
            chassis=_Strict1513Component(rotationSpeed=0.75))

        params = vehicle_physics.derive_params(descriptor)

        self.assertEqual(0.75, params['rotSpd'])

    def test_zero_native_brake_force_keeps_track_grip_fallback(self):
        descriptor = types.SimpleNamespace(
            physics={'weight': 21000.0, 'brakeForce': 0.0},
            chassis=_Strict1513Component(rotationSpeed=0.75))

        params = vehicle_physics.derive_params(descriptor)

        self.assertEqual(
            vehicle_physics.COHESION * vehicle_physics.GRAVITY,
            params['brakeDecel'])


class VehiclePhysicsCoastTests(unittest.TestCase):

    def setUp(self):
        self.params = dict(vehicle_physics._DEFAULTS)
        # Exact #1513 Type 62 values used by the current copied integrator.
        self.params.update({
            'mass': 21000.0,
            'speedFwd': 60.0 / 3.6,
            'terrainResist': (0.5, 0.6, 1.3),
            'specificFriction': 0.6867,
        })

    def _coast(self, speed, slope_degrees, dt):
        return vehicle_physics.longitudinal_step(
            self.params, speed, 0.0, False,
            math.radians(slope_degrees), dt)

    def _flat_stop(self, frame_rate):
        speed = self.params['speedFwd']
        dt = 1.0 / frame_rate
        distance = 0.0
        elapsed = 0.0
        while speed > 0.0 and elapsed < 5.0:
            speed = self._coast(speed, 0.0, dt)
            # BattleRuntime integrates the post-step speed into the pose.
            distance += speed * dt
            elapsed += dt
        return elapsed, distance

    def test_type62_flat_release_stops_in_the_conservative_calibrated_window(self):
        results = [self._flat_stop(rate) for rate in (24, 30, 60, 120)]

        for elapsed, distance in results:
            self.assertGreaterEqual(elapsed, 1.50)
            self.assertLessEqual(elapsed, 1.60)
            self.assertGreaterEqual(distance, 12.5)
            self.assertLessEqual(distance, 12.9)
        self.assertLess(
            max(row[1] for row in results) -
            min(row[1] for row in results),
            0.30)

    def test_moving_tank_accelerates_down_a_fifteen_degree_slope(self):
        speed = 5.0
        dt = 0.1
        actual = self._coast(speed, 15.0, dt)

        self.assertGreater(actual, speed)
        self.assertLess(actual - speed, 0.3)

    def test_static_hold_and_handbrake_are_unchanged(self):
        self.assertEqual(0.0, self._coast(0.0, 25.0, 0.1))
        self.assertGreater(self._coast(0.0, 30.0, 0.1), 0.0)
        self.assertGreater(
            vehicle_physics.brake_force(self.params, True),
            vehicle_physics.brake_force(self.params, False))
        self.assertEqual(0.0, vehicle_physics.longitudinal_step(
            self.params, 0.0, 0.0, False, math.radians(30.0), 0.1,
            handbrake=True))

    def test_downhill_neutral_coast_is_frame_rate_invariant(self):
        results = []
        for frame_rate in (24, 30, 60, 120):
            speed = 5.0
            dt = 1.0 / frame_rate
            for unused in range(frame_rate):
                speed = self._coast(speed, 20.0, dt)
            results.append(speed)

        self.assertGreater(results[0], 7.0)
        self.assertLess(max(results) - min(results), 1e-9)


if __name__ == '__main__':
    unittest.main()
