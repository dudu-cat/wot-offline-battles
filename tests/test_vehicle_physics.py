import importlib.util
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PHYSICS = ROOT / "scripts/client/gui/mods/offhangar/physics.py"


def load_physics():
    spec = importlib.util.spec_from_file_location("offhangar_vehicle_physics", PHYSICS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VehiclePhysicsTests(unittest.TestCase):
    def setUp(self):
        self.physics = load_physics()
        self.params = {
            "mass": 21500.0,
            "powerW": 430.0 * 735.49875,
            "speedFwd": 60.0 / 3.6,
            "speedBwd": 20.0 / 3.6,
            "rotSpd": math.radians(54.0),
            "terrainResist": (0.6, 0.7, 1.4),
            "specificFriction": 0.6867,
            "brakeDecel": 12.0,
            "trackCenter": 1.4,
            "minPlaneNormalY": math.cos(math.radians(25.0)),
        }

    def test_stock_drivable_grade_still_accelerates_uphill(self):
        pitch = -math.radians(24.0)
        velocity = 0.0
        for unused in range(60):
            velocity = self.physics.longitudinal_step(
                self.params, velocity, 1.0, False, pitch, 1.0 / 60.0)

        self.assertGreater(velocity, 0.1)

    def test_grade_beyond_stock_rise_limit_does_not_power_uphill(self):
        pitch = -math.radians(27.0)
        velocity = 0.0
        for unused in range(60):
            velocity = self.physics.longitudinal_step(
                self.params, velocity, 1.0, False, pitch, 1.0 / 60.0)

        self.assertLessEqual(velocity, 0.0)

    def test_neutral_backward_slide_keeps_forward_steering_convention(self):
        omega = self.physics.traverse_step(
            self.params, 0.0, -1.0, -2.0, 0.1, drive_intent=0.0)

        self.assertLess(omega, 0.0)


if __name__ == "__main__":
    unittest.main()
