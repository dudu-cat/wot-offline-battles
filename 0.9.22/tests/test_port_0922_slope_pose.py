import math
from pathlib import Path
import sys
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(PORT_ROOT / 'server'))
sys.path.insert(0, str(CLIENT_ROOT))

import descriptor_projection  # noqa: E402
import server_battle_authority  # noqa: E402
from lan_battle_server import BattleState  # noqa: E402
from gui.mods.offline_lan_0922 import bot_runtime  # noqa: E402


def _plane_probe(slope_z):
    def probe(unused_x, z, unused_hint):
        return z * slope_z
    return probe


class SlopePoseTests(unittest.TestCase):
    def test_uphill_plane_pitches_the_nose_up(self):
        pitch, roll = bot_runtime.slope_pose(
            _plane_probe(0.2), (0.0, 0.0, 0.0), 0.0, 3.5, 1.7)
        expected = -math.atan2(0.2 * 7.0, 7.0) * 0.9 * 0.5
        self.assertAlmostEqual(expected, pitch)
        self.assertAlmostEqual(0.0, roll)

    def test_smoothing_converges_toward_the_sampled_pose(self):
        target = -math.atan2(0.2 * 7.0, 7.0) * 0.9
        pitch, roll = 0.0, 0.0
        for unused in range(12):
            pitch, roll = bot_runtime.slope_pose(
                _plane_probe(0.2), (0.0, 0.0, 0.0), 0.0, 3.5, 1.7,
                pitch, roll)
        self.assertAlmostEqual(target, pitch, places=3)

    def test_extreme_slope_is_clamped_to_the_copied_limit(self):
        pitch, roll = bot_runtime.slope_pose(
            _plane_probe(5.0), (0.0, 0.0, 0.0), 0.0, 3.5, 1.7,
            last_pitch=-0.61, last_roll=0.0)
        self.assertLessEqual(math.sqrt(pitch * pitch + roll * roll),
                             0.61 + 1.0e-9)

    def test_missing_sample_keeps_the_last_pose(self):
        pitch, roll = bot_runtime.slope_pose(
            lambda x, z, hint: None, (0.0, 0.0, 0.0), 0.0, 3.5, 1.7,
            last_pitch=-0.3, last_roll=0.1)
        self.assertEqual((-0.3, 0.1), (pitch, roll))

    def test_side_slope_rolls_without_pitch(self):
        def probe(x, unused_z, unused_hint):
            return x * 0.2

        pitch, roll = bot_runtime.slope_pose(
            probe, (0.0, 0.0, 0.0), 0.0, 3.5, 1.7)
        self.assertAlmostEqual(0.0, pitch)
        self.assertAlmostEqual(math.atan2(0.2 * 3.4, 3.4) * 0.9 * 0.5, roll)


class PoseAxesTests(unittest.TestCase):
    def test_yaw_only_matches_the_previous_flat_axes(self):
        yaw = 0.7
        axes = server_battle_authority._pose_axes(yaw, 0.0, 0.0)
        sine, cosine = math.sin(yaw), math.cos(yaw)
        for actual, expected in zip(axes, ((cosine, 0.0, -sine),
                                           (0.0, 1.0, 0.0),
                                           (sine, 0.0, cosine))):
            for a_value, e_value in zip(actual, expected):
                self.assertAlmostEqual(e_value, a_value)

    def test_negative_pitch_raises_the_forward_axis(self):
        axes = server_battle_authority._pose_axes(0.0, -0.3, 0.0)
        self.assertAlmostEqual(math.sin(0.3), axes[2][1])
        self.assertAlmostEqual(math.cos(0.3), axes[2][2])


class PitchedHullEntryTests(unittest.TestCase):
    def _target(self, pitch=0.0, roll=0.0):
        descriptor = descriptor_projection.wrap({
            'hull': {'hitTester': {
                'bbox': [(-1.7, -0.2, -3.5), (1.7, 1.4, 3.5), None]}},
        })
        return {
            'kind': 'bot', 'id': 7, 'health': 900,
            'position': (0.0, 10.0, 0.0), 'yaw': 0.0,
            'pitch': pitch, 'roll': roll,
            'descriptor': descriptor, 'state': {},
        }

    def test_flat_hull_misses_a_chord_above_its_roof(self):
        start, end = (0.0, 11.8, -10.0), (0.0, 11.8, 10.0)
        self.assertIsNone(server_battle_authority._segment_hull_entry(
            start, end, self._target()))

    def test_nose_up_hull_catches_the_same_chord(self):
        start, end = (0.0, 11.8, -10.0), (0.0, 11.8, 10.0)
        entry = server_battle_authority._segment_hull_entry(
            start, end, self._target(pitch=-0.4))
        self.assertIsNotNone(entry)
        self.assertEqual('bot', entry['kind'])
        self.assertEqual(7, entry['id'])

    def test_hull_face_armor_is_still_resolved_on_the_pitched_box(self):
        descriptor = descriptor_projection.wrap({
            'hull': {
                'hitTester': {
                    'bbox': [(-1.7, -0.2, -3.5), (1.7, 1.4, 3.5), None]},
                'primaryArmor': (18.0, 16.0, 14.0),
            },
        })
        target = self._target(pitch=-0.2)
        target['descriptor'] = descriptor
        entry = server_battle_authority._segment_hull_entry(
            (0.0, 10.5, -10.0), (0.0, 10.5, 10.0), target)
        self.assertIsNotNone(entry)
        collision = entry['collisions'][0]
        self.assertGreater(float(collision.matInfo.armor), 0.0)


class BotStateWireTests(unittest.TestCase):
    def test_wire_rows_carry_the_clamped_hull_pose(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
        }
        row = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'pitch': -0.2, 'roll': 5.0,
        }, identity, None)
        self.assertAlmostEqual(-0.2, row['pitch'])
        self.assertAlmostEqual(0.61, row['roll'])

    def test_wire_rows_default_to_a_flat_pose(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
        }
        row = BattleState._sanitize_bot_state(
            {'id': 11, 'health': 1000, 'alive': True}, identity, None)
        self.assertEqual(0.0, row['pitch'])
        self.assertEqual(0.0, row['roll'])


if __name__ == '__main__':
    unittest.main()
