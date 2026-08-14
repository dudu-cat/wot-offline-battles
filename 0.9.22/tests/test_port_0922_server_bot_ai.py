from pathlib import Path
import sys
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from server_bot_ai import BotPlanner  # noqa: E402


def _profile(class_tag='SPG', roles=None):
    return {
        'class_tag': class_tag,
        'speed': 12.0,
        'dominant_role': ('artillery' if class_tag == 'SPG' else 'support'),
        'desired_range': 650.0 if class_tag == 'SPG' else 180.0,
        'fire_range': 1250.0 if class_tag == 'SPG' else 520.0,
        'roles': dict(roles or {}),
        'shells': [],
    }


def _route(route_id, points):
    return {
        'id': route_id,
        'waypoints': [
            {'x': float(x), 'y': 0.0, 'z': float(z), 'hold': bool(hold)}
            for x, z, hold in points
        ],
    }


def _bot(bot_id, team, slot, route, class_tag='SPG', roles=None):
    return {
        'id': bot_id,
        'team': team,
        'slot': slot,
        'health': 1000,
        'profile': _profile(class_tag, roles),
        'route': route,
    }


def _state(bot_id, team, x, z):
    return {
        'id': bot_id,
        'team': team,
        'alive': True,
        'world_pose': True,
        'x': float(x),
        'y': 0.0,
        'z': float(z),
        'yaw': 0.0,
        'health': 1000,
        'max_health': 1000,
        'critical': {},
    }


class ServerBotArtilleryTests(unittest.TestCase):
    def test_spg_keeps_a_client_proved_target_beyond_direct_fire_range(self):
        planner = BotPlanner()
        route = _route('field', [
            (0, 0, False), (0, 60, False), (0, 1200, False),
        ])
        manifest = [_bot(11, 1, 0, route)]
        states = [_state(11, 1, 0, 60)]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1,
            'target_kind': 'human',
            'target_id': 2,
            'target_team': 2,
            'visible': True,
            'shootable_by_bot_ids': [11],
            'x': 0.0,
            'y': 0.0,
            'z': 1050.0,
            'health': 1000,
            'max_health': 1000,
        }], planner.known_targets(states, [enemy]), 1.0))

        order = planner.build_orders(
            manifest, states, [enemy], 1.0)['orders'][0]

        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertEqual('artillery_hold', order['combat_mode'])
        self.assertEqual(1250.0, order['fire_range'])

    def test_mirrored_spgs_choose_stable_rear_non_hold_anchors(self):
        planner = BotPlanner()
        manifest = [
            _bot(11, 1, 0, _route('field', [
                (0, 0, False), (0, 60, False),
                (0, 160, True), (0, 600, False),
            ])),
            _bot(26, 2, 0, _route('field', [
                (0, 600, False), (0, 540, False),
                (0, 440, True), (0, 0, False),
            ])),
        ]
        states = [_state(11, 1, 0, 0), _state(26, 2, 0, 600)]

        deploying = dict((order['id'], order) for order in
                         planner.build_orders(
                             manifest, states, [], 1.0)['orders'])

        self.assertEqual('artillery_deploy', deploying[11]['combat_mode'])
        self.assertEqual('artillery_deploy', deploying[26]['combat_mode'])
        self.assertEqual(1, deploying[11]['route_index'])
        self.assertEqual(1, deploying[26]['route_index'])
        self.assertEqual(60.0, deploying[11]['move_position']['z'])
        self.assertEqual(540.0, deploying[26]['move_position']['z'])
        self.assertEqual(
            600.0,
            deploying[11]['move_position']['z'] +
            deploying[26]['move_position']['z'])
        self.assertFalse(manifest[0]['route']['waypoints'][1]['hold'])
        self.assertFalse(manifest[1]['route']['waypoints'][1]['hold'])

        states[0]['z'] = 60.0
        states[1]['z'] = 540.0
        holding = dict((order['id'], order) for order in
                       planner.build_orders(
                           manifest, states, [], 2.0)['orders'])

        self.assertEqual('artillery_hold', holding[11]['combat_mode'])
        self.assertEqual('artillery_hold', holding[26]['combat_mode'])
        self.assertEqual(0.0, holding[11]['throttle_override'])
        self.assertEqual(0.0, holding[26]['throttle_override'])
        self.assertEqual(deploying[11]['move_position'],
                         holding[11]['move_position'])
        self.assertEqual(deploying[26]['move_position'],
                         holding[26]['move_position'])

    def test_target_does_not_pull_spg_off_anchor(self):
        planner = BotPlanner()
        route = _route('field', [
            (0, 0, False), (0, 60, False),
            (0, 160, False), (0, 600, False),
        ])
        manifest = [_bot(11, 1, 0, route)]
        states = [_state(11, 1, 0, 60)]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1,
            'target_kind': 'human',
            'target_id': 2,
            'target_team': 2,
            'visible': True,
            'shootable_by_bot_ids': [11],
            'x': 0.0,
            'y': 0.0,
            'z': 500.0,
            'health': 1000,
            'max_health': 1000,
        }], planner.known_targets(states, [enemy]), 1.0))

        order = planner.build_orders(
            manifest, states, [enemy], 1.0)['orders'][0]

        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertEqual('artillery_hold', order['combat_mode'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 60.0},
                         order['move_position'])
        self.assertNotIn(order['combat_mode'], (
            'advance_contact', 'take_cover', 'cover_hold',
            'cover_peek', 'cover_return', 'flank'))

    def test_base_defense_preempts_artillery_hold(self):
        planner = BotPlanner()
        manifest = [_bot(11, 1, 0, _route('field', [
            (0, 0, False), (0, 60, False), (0, 600, False),
        ]))]
        states = [_state(11, 1, 0, 60)]
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0},
            ]},
            'states': {'1': {
                'points': 20,
                'time_left': 30.0,
                'invaders': 1,
                'stopped': False,
            }},
            'contributors': {'1': []},
        }

        order = planner.build_orders(
            manifest, states, [], 1.0, defense)['orders'][0]

        self.assertEqual('base_defense', order['combat_mode'])
        self.assertEqual('1:0', order['defense_base_id'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 0.0},
                         order['move_position'])

    def test_spg_is_never_a_pressured_route_donor(self):
        planner = BotPlanner()
        route_a = _route('a', [
            (-100, 0, False), (-100, 80, False),
            (-100, 500, False),
        ])
        route_b = _route('b', [
            (100, 0, False), (100, 80, False),
            (100, 500, False),
        ])
        manifest = [
            _bot(11, 1, 0, route_a, 'SPG', {
                'support': 1.0, 'flanker': 1.0, 'scout': 1.0,
            }),
            _bot(12, 1, 1, route_a, 'mediumTank', {
                'support': 0.0, 'flanker': 0.0, 'scout': 0.0,
                'brawler': 1.0,
            }),
            _bot(13, 1, 2, route_b, 'mediumTank', {
                'support': 0.5,
            }),
        ]
        states = [
            _state(11, 1, -100, 0),
            _state(12, 1, -100, 0),
            _state(13, 1, 100, 0),
        ]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1,
            'target_kind': 'human',
            'target_id': 2,
            'target_team': 2,
            'visible': True,
            'shootable_by_bot_ids': [],
            'x': 100.0,
            'y': 0.0,
            'z': 250.0,
            'health': 1000,
            'max_health': 1000,
        }], planner.known_targets(states, [enemy]), 1.0))

        orders = dict((order['id'], order) for order in
                      planner.build_orders(
                          manifest, states, [enemy], 1.0)['orders'])

        self.assertEqual('a', orders[11]['route_id'])
        self.assertEqual('b', orders[12]['route_id'])
        self.assertEqual('b', orders[13]['route_id'])


if __name__ == '__main__':
    unittest.main()
