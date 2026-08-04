import importlib.util
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "scripts/client/gui/mods/offhangar/bot_ai_driver.py"


def load_driver():
    spec = importlib.util.spec_from_file_location("bot_ai_driver_under_test", DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotAIDriverTest(unittest.TestCase):
    def setUp(self):
        self.module = load_driver()

    def test_flat_ground_drives_straight_to_target(self):
        driver = self.module.LocalDriver()
        order = driver.drive(1, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                             (0.0, 0.0, 50.0), (), lambda angle: True)
        self.assertEqual("drive", order["recovery_mode"])
        self.assertGreater(order["throttle"], 0.9)
        self.assertAlmostEqual(0.0, order["turn"], places=5)
        self.assertAlmostEqual(0.0, order["target_yaw"], places=5)

    def test_blocked_forward_ray_chooses_open_side(self):
        driver = self.module.LocalDriver()

        def clear(angle):
            return angle > 0.2 and angle < 0.65

        order = driver.drive(2, (0.0, 0.0, 0.0), 0.0, 3.0, 0.1,
                             (0.0, 0.0, 40.0), (), clear)
        self.assertEqual("avoid", order["recovery_mode"])
        self.assertGreater(order["turn"], 0.0)
        self.assertGreater(order["target_yaw"], 0.2)

    def test_stuck_time_triggers_reverse_turn_without_frame_counter(self):
        driver = self.module.LocalDriver(stuck_seconds=0.6, recovery_seconds=0.5)
        order = None
        for unused in range(12):
            order = driver.drive(9, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                                 (0.0, 0.0, 50.0), (), lambda angle: True)
            if order["recovery_mode"] == "reverse_turn":
                break
        self.assertEqual("reverse_turn", order["recovery_mode"])
        self.assertLess(order["throttle"], 0.0)
        first_turn = order["turn"]
        # Let this recovery finish, stay blocked again, then ensure the next
        # recovery turns to the other side.
        for unused in range(30):
            order = driver.drive(9, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                                 (0.0, 0.0, 50.0), (), lambda angle: True)
            if order["recovery_mode"] == "reverse_turn" and order["turn"] != first_turn:
                break
        self.assertEqual("reverse_turn", order["recovery_mode"])
        self.assertNotEqual(first_turn, order["turn"])

    def test_neighbours_and_identity_phase_stagger_bots(self):
        driver = self.module.LocalDriver(stuck_seconds=0.6)
        # A close neighbour on the east makes an otherwise clear driver choose
        # the westward candidate, proving separation is considered.
        crowded = driver.drive("west", (0.0, 0.0, 0.0), 0.0, 2.0, 0.1,
                               (0.0, 0.0, 40.0), [(3.0, 0.0, 1.0)],
                               lambda angle: True)
        self.assertLess(crowded["target_yaw"], 0.0)

        states = []
        for bot_id in ("alpha", "left"):
            active = 0.0
            for unused in range(20):
                active += 0.1
                order = driver.drive(bot_id, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                                     (0.0, 0.0, 40.0), (), lambda angle: True)
                if order["recovery_mode"] == "reverse_turn":
                    break
            states.append(active)
        self.assertNotEqual(states[0], states[1])


if __name__ == "__main__":
    unittest.main()
