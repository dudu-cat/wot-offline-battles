from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/client/gui/mods/offhangar/capture_rules.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "capture_rules_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaptureRulesTests(unittest.TestCase):
    def setUp(self):
        self.capture_rules = load_module()

    def test_damage_drops_only_the_damaged_vehicle_contribution(self):
        state = self.capture_rules.new_state()
        for _ in range(4):
            self.capture_rules.advance(state, ["a", "b"])

        self.assertEqual(8, state["points"])
        self.assertEqual(4, self.capture_rules.drop_vehicle(state, "a"))
        self.assertEqual(4, state["points"])
        self.assertEqual({"b": 4}, state["contributors"])

    def test_leaving_the_circle_drops_only_the_leaver(self):
        state = self.capture_rules.new_state()
        for _ in range(3):
            self.capture_rules.advance(state, ["a", "b"])

        result = self.capture_rules.advance(state, ["b"])

        self.assertEqual({"a": 3}, result["dropped"])
        self.assertEqual(4, state["points"])
        self.assertEqual({"b": 4}, state["contributors"])

    def test_defender_pauses_without_erasing_existing_points(self):
        state = self.capture_rules.new_state()
        self.capture_rules.advance(state, ["a"])

        result = self.capture_rules.advance(
            state, ["a"], defenders_present=True
        )

        self.assertTrue(result["stopped"])
        self.assertEqual(1, state["points"])
        self.assertEqual({"a": 1}, state["contributors"])

    def test_rate_is_capped_and_rotated_across_four_invaders(self):
        state = self.capture_rules.new_state()
        first = self.capture_rules.advance(state, ["a", "b", "c", "d"])
        second = self.capture_rules.advance(state, ["a", "b", "c", "d"])

        self.assertEqual(3, sum(first["gained"].values()))
        self.assertEqual(3, sum(second["gained"].values()))
        self.assertEqual(6, state["points"])
        self.assertTrue(all(state["contributors"][key] > 0
                            for key in ("a", "b", "c", "d")))

    def test_empty_circle_clears_capture(self):
        state = self.capture_rules.new_state()
        self.capture_rules.advance(state, ["a"])

        result = self.capture_rules.advance(state, [])

        self.assertEqual({"a": 1}, result["dropped"])
        self.assertEqual(0, state["points"])
        self.assertFalse(state["stopped"])


if __name__ == "__main__":
    unittest.main()
