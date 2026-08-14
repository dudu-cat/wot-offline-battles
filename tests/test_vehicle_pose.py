import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/client/gui/mods/offhangar/vehicle_pose.py"


class Vector3:
    def __init__(self, x, y=None, z=None):
        if y is None and z is None:
            if hasattr(x, "x"):
                x, y, z = x.x, x.y, x.z
            else:
                x, y, z = x
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class Matrix:
    def __init__(self):
        self.rotation = None
        self.translation = None

    def setRotateYPR(self, value):
        self.rotation = tuple(value)


class EntityFilter:
    def __init__(self):
        self.calls = []

    def set(self, *args):
        self.calls.append(args)


class Model:
    def __init__(self):
        self.position = None
        self.yaw = None
        self.motors = []

    def addMotor(self, motor):
        self.motors.append(motor)


def load_module():
    math_module = types.ModuleType("Math")
    math_module.Vector3 = Vector3
    sys.modules["Math"] = math_module

    bigworld = types.ModuleType("BigWorld")
    bigworld.time = lambda: 123.0
    bigworld.Servo = lambda matrix: ("servo", matrix)
    sys.modules["BigWorld"] = bigworld

    spec = importlib.util.spec_from_file_location("vehicle_pose_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VehiclePoseTests(unittest.TestCase):
    def setUp(self):
        self.pose = load_module()
        self.vehicle = types.SimpleNamespace(
            position=Vector3(0, 0, 0),
            yaw=0.0,
            pitch=0.0,
            roll=0.0,
            matrix=Matrix(),
            bw_entity=types.SimpleNamespace(id=77, filter=EntityFilter()),
            _chassis_model=Model(),
        )

    def test_commit_updates_every_live_pose_consumer(self):
        result = self.pose.commit_pose(
            self.vehicle, (10.0, 2.0, -4.0), 0.7, 0.1, -0.2,
            space_id=9, timestamp=55.0, prime_model=True,
        )

        self.assertTrue(result)
        self.assertEqual((10.0, 2.0, -4.0), (
            self.vehicle.position.x,
            self.vehicle.position.y,
            self.vehicle.position.z,
        ))
        self.assertEqual((0.7, 0.1, -0.2), self.vehicle.matrix.rotation)
        self.assertEqual(
            (55.0, 9, 77, self.vehicle.position, (-0.2, 0.1, 0.7), 0),
            self.vehicle.bw_entity.filter.calls[-1],
        )
        self.assertEqual(1, len(self.vehicle._chassis_model.motors))
        self.assertTrue(self.vehicle._servo_added)

    def test_servo_becomes_the_only_model_writer_after_priming(self):
        self.pose.commit_pose(
            self.vehicle, (1.0, 2.0, 3.0), 0.2,
            space_id=9, prime_model=True,
        )
        primed_model_position = self.vehicle._chassis_model.position

        self.pose.commit_pose(
            self.vehicle, (8.0, 2.0, 9.0), 0.9,
            space_id=9, prime_model=True,
        )

        self.assertIs(primed_model_position, self.vehicle._chassis_model.position)
        self.assertEqual(1, len(self.vehicle._chassis_model.motors))
        self.assertEqual((8.0, 2.0, 9.0), (
            self.vehicle.matrix.translation.x,
            self.vehicle.matrix.translation.y,
            self.vehicle.matrix.translation.z,
        ))

    def test_silent_servo_add_no_op_stays_retryable_until_readback_succeeds(self):
        model = self.vehicle._chassis_model
        real_add_motor = model.addMotor
        attempts = []

        def silent_no_op(motor):
            attempts.append(motor)

        model.addMotor = silent_no_op
        result = self.pose.commit_pose(
            self.vehicle, (1.0, 2.0, 3.0), 0.2,
            space_id=9, prime_model=True,
        )

        self.assertFalse(result)
        self.assertEqual([], model.motors)
        self.assertFalse(getattr(self.vehicle, "_servo_added", False))
        self.assertIsNone(getattr(self.vehicle, "_pose_servo", None))
        self.assertEqual(1, len(attempts))

        model.addMotor = real_add_motor
        self.assertTrue(self.pose.commit_pose(
            self.vehicle, (4.0, 2.0, 6.0), 0.4,
            space_id=9, prime_model=True,
        ))
        self.assertEqual(1, len(model.motors))
        self.assertIs(self.vehicle._pose_servo, model.motors[0])
        self.assertTrue(self.vehicle._servo_added)

if __name__ == "__main__":
    unittest.main()
