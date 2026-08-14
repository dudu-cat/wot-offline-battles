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
        self.assertEqual(["b"], state["active_contributors"])
        self.assertEqual(1, state["invaders"])

    def test_leaving_the_circle_drops_only_the_leaver(self):
        state = self.capture_rules.new_state()
        for _ in range(3):
            self.capture_rules.advance(state, ["a", "b"])

        result = self.capture_rules.advance(state, ["b"])

        self.assertEqual({"a": 3}, result["dropped"])
        self.assertEqual(4, state["points"])
        self.assertEqual({"b": 4}, state["contributors"])

    def test_owner_presence_does_not_pause_standard_ctf_capture(self):
        state = self.capture_rules.new_state()
        self.capture_rules.advance(state, ["a"])

        result = self.capture_rules.advance(
            state, ["a"], defenders_present=True
        )

        # The exact 0.8.2 standard CTF control point declares
        # ownerStopsCapturing=false: an owner merely standing in its base must
        # not pause capture.  Damage remains the mechanism that resets points.
        self.assertFalse(result["stopped"])
        self.assertEqual(2, state["points"])
        self.assertEqual({"a": 2}, state["contributors"])

    def test_team_two_player_inside_team_one_cliff_base_emits_progress_with_owner_present(self):
        # Exact spaces/18_cliff team-1 ControlPoint world position and radius.
        base_xz = (-287.414, -436.601)
        radius_sq = 50.0 * 50.0
        player_team = 2
        base_team = 1
        player_xz = (base_xz[0] + 49.0, base_xz[1])
        defender_xz = base_xz

        def inside(position):
            dx = position[0] - base_xz[0]
            dz = position[1] - base_xz[1]
            return dx * dx + dz * dz <= radius_sq

        invading_team = 2 if base_team == 1 else 1
        invader_ids = []
        if player_team == invading_team and inside(player_xz):
            invader_ids.append("player")
        defenders_present = inside(defender_xz)

        state = self.capture_rules.new_state()
        old_points = state["points"]
        old_stopped = state["stopped"]
        result = self.capture_rules.advance(
            state, invader_ids, defenders_present=defenders_present
        )

        ui_updates = []
        if (
            state["points"] != old_points
            or state["stopped"] != old_stopped
            or invader_ids
        ):
            ui_updates.append({
                "base_team": base_team,
                "points": state["points"],
                "stopped": result["stopped"],
            })

        self.assertEqual(["player"], invader_ids)
        self.assertTrue(defenders_present)
        self.assertEqual(1, state["points"])
        self.assertFalse(result["stopped"])
        self.assertEqual(
            [{"base_team": 1, "points": 1, "stopped": False}],
            ui_updates,
        )

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
