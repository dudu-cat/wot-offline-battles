import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/client/gui/mods/offhangar/battle_feedback.py"


def load_module():
    spec = importlib.util.spec_from_file_location("battle_feedback_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BattleFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.stats = self.module.new_stats(10.0)
        self.module.mark_started(self.stats, 20.0)

    def test_results_use_observed_combat_values(self):
        self.module.record_shot(self.stats)
        self.module.record_outgoing_hit(self.stats, 42, 317, 2, True)
        self.module.record_incoming_hit(self.stats, 121)
        self.module.record_spotted(self.stats, 42)
        self.module.record_assist(self.stats, 43, 88)
        self.module.record_capture(self.stats, 3)

        result = self.module.result_values(self.stats, 75.0)

        self.assertEqual(1, result["shots"])
        self.assertEqual(1, result["hits"])
        self.assertEqual(317, result["damageDealt"])
        self.assertEqual(88, result["damageAssisted"])
        self.assertEqual(121, result["damageReceived"])
        self.assertEqual(1, result["shotsReceived"])
        self.assertEqual(1, result["spotted"])
        self.assertEqual(1, result["kills"])
        self.assertEqual(3, result["capturePoints"])
        self.assertEqual(55, result["lifeTime"])

    def test_spotting_and_kills_are_idempotent_per_target(self):
        self.assertTrue(self.module.record_spotted(self.stats, 7))
        self.assertFalse(self.module.record_spotted(self.stats, 7))
        self.module.record_outgoing_hit(self.stats, 7, 100, 2, True)
        self.module.record_outgoing_hit(self.stats, 7, 0, 1, True)

        result = self.module.result_values(self.stats, 30.0)
        self.assertEqual(1, result["spotted"])
        self.assertEqual(1, result["kills"])
        self.assertEqual(2, result["hits"])

    def test_mileage_ignores_spawn_or_network_teleports(self):
        self.module.record_position(self.stats, (0.0, 0.0, 0.0))
        self.module.record_position(self.stats, (3.0, 0.0, 4.0))
        self.module.record_position(self.stats, (300.0, 0.0, 400.0))

        self.assertEqual(5, self.module.result_values(self.stats, 30.0)["mileage"])
