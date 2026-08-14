import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
    name = 'gui.mods.offline_lan_0922.snapshot_sync'
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, PACKAGE_ROOT / 'snapshot_sync.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def player(identifier, x=0, alive=True):
    return {'id': identifier, 'name': 'P%s' % identifier, 'vehicle': 'ussr:T-34',
            'team': 1, 'slot': 0, 'x': x, 'y': 0, 'z': 0, 'yaw': 0,
            'health': 100, 'max_health': 100, 'alive': alive}


class SnapshotSyncTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.now = [0.0]
        self.callback = []
        self.sync = self.module.SnapshotSync(1, self.callback.append,
                                              clock=lambda: self.now[0])

    def test_manifest_creates_players_and_bots_once(self):
        message = {'round_id': 3, 'players': [player(1), player(2)],
                   'bots': [{'id': 7, 'vehicle': 'germany:PzI', 'team': 2}]}
        events = self.sync.manifest(message) + self.sync.manifest(message)

        self.assertEqual(['player:1', 'player:2', 'bot:7'],
                         [event['entity'] for event in events])
        self.assertEqual(events, self.callback)

    def test_local_is_server_correction_remote_interpolates_and_predicts_50ms(self):
        self.sync.manifest({'round_id': 1, 'players': [player(1), player(2)]})
        first = self.sync.snapshot({'round_id': 1, 'server_tick': 1,
                                    'players': [player(1, 3), player(2, 2)], 'bots': []})
        self.assertTrue([event for event in first if event.get('correction')])
        self.assertEqual(2.0, [event for event in first if event['entity'] == 'player:2'][0]['pose']['x'])

        self.now[0] = 0.1
        self.sync.snapshot({'round_id': 1, 'server_tick': 2,
                            'players': [player(1, 4), player(2, 10)], 'bots': []})
        self.now[0] = 0.15
        event = self.sync.advance()[0]
        self.assertTrue(event['interpolated'])
        self.assertGreater(event['pose']['x'], 2.0)
        self.assertLessEqual(event['pose']['x'], 14.0)

    def test_large_remote_gap_snaps_at_25_metres(self):
        self.sync.manifest({'round_id': 1, 'players': [player(2)]})
        self.sync.snapshot({'round_id': 1, 'server_tick': 1, 'players': [player(2, 0)]})
        self.now[0] = 0.1
        self.sync.snapshot({'round_id': 1, 'server_tick': 2, 'players': [player(2, 40)]})
        events = self.sync.advance(0.1)
        self.assertTrue(events[0]['snap'])
        self.assertEqual(40.0, events[0]['pose']['x'])

    def test_remote_bot_prediction_does_not_cross_a_baked_hazard(self):
        sync = self.module.SnapshotSync(
            1, clock=lambda: self.now[0],
            pose_safe=lambda pose: pose[0] < 5.0)
        bot = player(7, 4.0)
        sync.manifest({'round_id': 1, 'bots': [bot]})
        sync.snapshot({'round_id': 1, 'server_tick': 1, 'bots': [bot]})
        self.now[0] = 0.1
        moved = player(7, 4.8)
        sync.snapshot({'round_id': 1, 'server_tick': 2, 'bots': [moved]})

        event = sync.advance(0.15)[0]

        self.assertLessEqual(event['pose']['x'], 4.8)

    def test_authoritative_fallen_bot_pose_is_not_rewound(self):
        sync = self.module.SnapshotSync(
            1, clock=lambda: self.now[0],
            pose_safe=lambda pose: pose[0] < 5.0)
        initial = player(7, 4.0)
        fallen = player(7, 6.0)
        fallen['y'] = -8.0
        sync.manifest({'round_id': 1, 'bots': [initial]})
        sync.snapshot({'round_id': 1, 'server_tick': 1,
                       'bots': [initial]})
        self.now[0] = 0.1
        sync.snapshot({'round_id': 1, 'server_tick': 2,
                       'bots': [fallen]})

        event = sync.advance(0.1)[0]

        self.assertGreater(event['pose']['x'], initial['x'])
        self.assertLess(event['pose']['y'], initial['y'])

    def test_pose_safety_error_is_not_silently_ignored(self):
        def fail(unused_pose):
            raise ValueError('broken graph')

        sync = self.module.SnapshotSync(
            1, clock=lambda: self.now[0], pose_safe=fail)
        first = player(7, 1.0)
        second = player(7, 2.0)
        sync.manifest({'round_id': 1, 'bots': [first]})
        sync.snapshot({'round_id': 1, 'server_tick': 1, 'bots': [first]})
        self.now[0] = 0.1
        sync.snapshot({'round_id': 1, 'server_tick': 2, 'bots': [second]})

        with self.assertRaisesRegex(ValueError, 'broken graph'):
            sync.advance(0.15)

    def test_remote_angles_take_short_path_across_pi_and_aim_is_smoothed(self):
        initial = player(2)
        initial.update(yaw=math.pi - 0.05, aim_yaw=math.pi - 0.10,
                       gun_pitch=-0.2)
        target = player(2)
        target.update(yaw=-math.pi + 0.05, aim_yaw=-math.pi + 0.10,
                      gun_pitch=0.2)
        self.sync.manifest({'round_id': 1, 'players': [initial]})
        self.sync.snapshot({'round_id': 1, 'server_tick': 1,
                            'players': [initial]})
        self.now[0] = 0.1
        self.sync.snapshot({'round_id': 1, 'server_tick': 2,
                            'players': [target]})

        pose = self.sync.advance(0.116)[0]['pose']

        self.assertLess(abs(self.module._angle_delta(initial['yaw'],
                                                      pose['yaw'])), 0.1)
        self.assertLess(abs(self.module._angle_delta(initial['aim_yaw'],
                                                      pose['aim_yaw'])), 0.2)
        self.assertGreater(pose['gun_pitch'], initial['gun_pitch'])

    def test_non_finite_snapshot_numbers_fall_back_to_safe_zero(self):
        malformed = player(2, float('nan'))
        malformed.update(yaw=float('inf'), aim_yaw=float('-inf'),
                         gun_pitch=float('nan'))

        event = self.sync.snapshot({
            'server_tick': 1, 'players': [malformed]})[-1]

        self.assertEqual(
            {'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
             'aim_yaw': 0.0, 'gun_pitch': 0.0}, event['target'])

    def test_stale_round_or_sequence_has_no_events(self):
        self.sync.manifest({'round_id': 4, 'players': [player(1)]})
        self.assertTrue(self.sync.snapshot({'round_id': 4, 'server_tick': 3,
                                            'players': [player(1)]}))
        self.assertEqual([], self.sync.snapshot({'round_id': 4, 'server_tick': 3,
                                                 'players': [player(1)]}))
        self.assertEqual([], self.sync.snapshot({'round_id': 3, 'server_tick': 4,
                                                 'players': [player(1)]}))

    def test_late_join_snapshot_creates_unknown_entity_and_orders_once_per_revision(self):
        events = self.sync.snapshot({'server_tick': 1, 'players': [player(9)],
                                     'bots': [], 'bot_order_revision': 2,
                                     'bot_orders': [{'id': 7, 'target_id': 9}]})
        self.assertEqual(['create', 'update', 'order'], [event['type'] for event in events])
        repeat = self.sync.snapshot({'server_tick': 2, 'players': [player(9)],
                                     'bots': [], 'bot_order_revision': 2,
                                     'bot_orders': [{'id': 7, 'target_id': 1}]})
        self.assertFalse([event for event in repeat if event['type'] == 'order'])

    def test_death_and_missing_corpse_are_idempotent(self):
        self.sync.manifest({'round_id': 1, 'players': [player(2)]})
        dead = self.sync.snapshot({'round_id': 1, 'server_tick': 1,
                                   'players': [player(2, alive=False)]})
        self.assertEqual(['destroy'], [event['type'] for event in dead])
        self.assertTrue(dead[0]['keep_corpse'])
        self.assertEqual([], self.sync.snapshot({'round_id': 1, 'server_tick': 2,
                                                 'players': [player(2, alive=False)]}))
        self.assertEqual([], self.sync.snapshot({'round_id': 1, 'server_tick': 3,
                                                 'players': []}))
