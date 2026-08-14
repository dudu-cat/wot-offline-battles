import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COVER_PATH = ROOT / "scripts/client/gui/mods/offhangar/bot_ai_cover.py"


def load_cover():
    spec = importlib.util.spec_from_file_location("bot_ai_cover_under_test", COVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoverScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cover = load_cover()

    def test_normalization_is_json_safe_and_clamps_bad_sampler_values(self):
        result = self.cover.normalize_candidate({
            'id': 'wall', 'position': (1, 'bad', 3), 'travel_distance': -1,
            'route_alignment': 3, 'enemy_occlusion': -2, 'exposure': 'nan',
            'slope': -5, 'water': 2, 'ally_congestion': -1,
        })
        self.assertIsNone(result['position'])
        self.assertEqual(0.0, result['travel_distance'])
        self.assertEqual(1.0, result['route_alignment'])
        self.assertEqual(0.0, result['enemy_occlusion'])
        self.assertEqual(1.0, result['exposure'])
        self.assertEqual(1.0, result['water'])

    def test_malformed_positions_and_truthy_strings_never_become_valid_cover(self):
        result = self.cover.normalize_candidate({
            'id': '\u5ca9:1',
            'position': {'x': 1, 'z': 3},
            'peek_position': {'x': 1, 'y': 0, 'z': 2},
            'peek_feasible': 'false',
            'escape_feasible': 'true',
        })

        self.assertEqual(':1', result['id'])
        self.assertIsNone(result['position'])
        self.assertFalse(result['peek_feasible'])
        self.assertFalse(result['escape_feasible'])

    def test_safe_cover_outranks_exposed_water_candidate_with_breakdown(self):
        ranked = self.cover.score_candidates([
            {'id': 'water', 'travel_distance': 10, 'route_alignment': 1,
             'enemy_occlusion': 0, 'exposure': 1, 'slope': 2, 'water': 1,
             'ally_congestion': 0, 'peek_feasible': False, 'escape_feasible': False},
            {'id': 'rock', 'travel_distance': 25, 'route_alignment': 0.8,
             'enemy_occlusion': 0.9, 'exposure': 0.1, 'slope': 4, 'water': 0,
             'ally_congestion': 0.1, 'peek_feasible': True, 'escape_feasible': True},
        ])
        self.assertEqual('rock', ranked[0]['id'])
        self.assertEqual(1, ranked[0]['rank'])
        self.assertGreater(ranked[0]['breakdown']['enemy_occlusion'], 0)
        self.assertIn('water_risk', ranked[1]['reasons'])

    def test_equal_scores_have_stable_distance_then_id_tie_break(self):
        ranked = self.cover.score_candidates([
            {'id': 'z', 'travel_distance': 20},
            {'id': 'a', 'travel_distance': 20},
            {'id': 'near', 'travel_distance': 10},
        ], weights={'travel_distance': 0, 'exposure': 0})
        self.assertEqual(['near', 'a', 'z'], [item['id'] for item in ranked])

    def test_weight_overrides_are_explicit(self):
        base = self.cover.score_candidate({'id': 'x', 'enemy_occlusion': 1})
        changed = self.cover.score_candidate({'id': 'x', 'enemy_occlusion': 1},
                                             {'enemy_occlusion': 3})
        self.assertNotEqual(base['score'], changed['score'])
        self.assertEqual(3.0, changed['breakdown']['enemy_occlusion'])


if __name__ == '__main__':
    unittest.main()
