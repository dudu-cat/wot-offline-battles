from pathlib import Path
import sys
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from server_bot_ai import BotPlanner  # noqa: E402
from lan_battle_server import BattleState, Player  # noqa: E402
from gui.mods.offline_lan_0922.bot_runtime import (  # noqa: E402
    _overlay_live_target_pose,
)


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


def _route(route_id, points, capacity=None, class_weights=None,
           role_weights=None):
    result = {
        'id': route_id,
        'waypoints': [
            {'x': float(x), 'y': 0.0, 'z': float(z), 'hold': bool(hold)}
            for x, z, hold in points
        ],
    }
    if capacity is not None:
        result['capacity'] = int(capacity)
    if class_weights is not None:
        result['class_weights'] = dict(class_weights)
    if role_weights is not None:
        result['role_weights'] = dict(role_weights)
    return result


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


def _contact(target_id, x, z, observers, class_tag='mediumTank'):
    return {
        'observing_team': 1,
        'target_kind': 'human',
        'target_id': target_id,
        'target_team': 2,
        'visible': True,
        'shootable_by_bot_ids': list(observers),
        'x': float(x),
        'y': 0.0,
        'z': float(z),
        'health': 1000,
        'max_health': 1000,
        'class_tag': class_tag,
    }


def _capture_defense():
    return {
        'capture_bases': {
            '1': [{'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': -100.0}],
            '2': [{'id': '2:0', 'x': 123.5, 'y': 0.0, 'z': 456.25}],
        },
    }


class ServerBotTacticsTests(unittest.TestCase):
    def setUp(self):
        self.route = _route('lane', [
            (0, -100, False), (0, 100, False), (0, 500, False),
        ])
        self.manifest = [_bot(
            11, 1, 0, self.route, 'mediumTank', {'support': 1.0})]
        self.manifest[0]['profile'].update({
            'armor': 120.0,
            'dominant_role': 'support',
            'desired_range': 180.0,
            'fire_range': 520.0,
        })
        self.states = [_state(11, 1, 0, 0)]

    def _report(self, planner, contacts):
        players = [
            {'id': raw['target_id'], 'team': 2, 'alive': True}
            for raw in contacts
        ]
        self.assertEqual(len(contacts), planner.report_contacts(
            contacts, planner.known_targets(self.states, players), 1.0))
        return players

    def test_recent_attacker_preempts_target_lease_and_withdraws(self):
        planner = BotPlanner()
        contacts = [
            _contact(2, 0, 260, [11]),
            _contact(3, 0, 35, [11]),
        ]
        players = self._report(planner, contacts)
        first = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]
        self.assertEqual(3, first['target_id'])

        self.assertTrue(planner.report_damage(
            11, 'player', 2, 240, 1.1))
        reaction = planner.build_orders(
            self.manifest, self.states, players, 1.1)['orders'][0]

        self.assertEqual(2, reaction['target_id'])
        self.assertEqual('under_fire_withdraw', reaction['combat_mode'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': -100.0},
                         reaction['move_position'])

    def test_recent_hit_holds_cover_until_exposure_window_expires(self):
        planner = BotPlanner()
        contacts = [_contact(2, 0, 260, [11])]
        players = self._report(planner, contacts)
        known_bots = planner.known_bots(self.manifest, self.states)
        known_targets = planner.known_targets(self.states, players)
        self.assertEqual(1, planner.report_affordances([{
            'bot_id': 11,
            'target_kind': 'human',
            'target_id': 2,
            'candidates': [{
                'id': 'rock',
                'position': {'x': 12.0, 'y': 0.0, 'z': 0.0},
                'peek_position': {'x': 18.0, 'y': 0.0, 'z': 4.0},
                'travel_distance': 12.0,
                'route_alignment': 0.8,
                'enemy_occlusion': 0.9,
                'exposure': 0.1,
                'slope': 1.0,
                'water': 0.0,
                'ally_congestion': 0.0,
                'peek_feasible': True,
                'escape_feasible': True,
            }],
        }], known_bots, known_targets, 1.0))
        planner.report_damage(11, 'player', 2, 200, 1.1)

        approach = planner.build_orders(
            self.manifest, self.states, players, 1.1)['orders'][0]
        self.assertEqual('take_cover', approach['combat_mode'])
        self.states[0]['x'] = 12.0
        holding = planner.build_orders(
            self.manifest, self.states, players, 1.2)['orders'][0]
        self.assertEqual('cover_hold', holding['combat_mode'])
        still_holding = planner.build_orders(
            self.manifest, self.states, players, 5.0)['orders'][0]
        self.assertEqual('cover_hold', still_holding['combat_mode'])
        peeking = planner.build_orders(
            self.manifest, self.states, players, 7.2)['orders'][0]
        self.assertEqual('cover_peek', peeking['combat_mode'])

    def test_low_health_vehicle_retreats_without_waiting_for_a_hit(self):
        planner = BotPlanner()
        contacts = [_contact(2, 0, 150, [11])]
        players = self._report(planner, contacts)
        self.states[0]['health'] = 200

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]

        self.assertEqual('low_health_retreat', order['combat_mode'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': -100.0},
                         order['move_position'])

    def test_contact_free_capturer_follows_lane_before_enemy_base(self):
        planner = BotPlanner()
        defense = _capture_defense()

        approach = planner.build_orders(
            self.manifest, self.states, [], 1.0, defense)['orders'][0]

        self.assertEqual('route', approach['combat_mode'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 100.0},
                         approach['move_position'])
        self.assertNotIn('capture_base_id', approach)

        self.states[0]['z'] = 100.0
        order = planner.build_orders(
            self.manifest, self.states, [], 2.0, defense)['orders'][0]

        self.assertEqual('base_capture', order['combat_mode'])
        self.assertEqual('2:0', order['capture_base_id'])
        self.assertEqual({'x': 123.5, 'y': 0.0, 'z': 456.25},
                         order['move_position'])
        self.assertEqual(order['move_position'], order['aim_position'])
        self.assertEqual(order['move_position'], order['face_position'])

    def test_capture_squad_is_capped_while_other_bots_keep_routes(self):
        planner = BotPlanner()
        manifest = [
            _bot(100 + index, 1, index, self.route, 'mediumTank')
            for index in range(15)
        ]
        states = [
            _state(bot['id'], 1, 0, index * 2)
            for index, bot in enumerate(manifest)
        ]

        approach = planner.build_orders(
            manifest, states, [], 1.0, _capture_defense())['orders']
        selected_ids = set(planner._base_capture[1]['bot_ids'])
        self.assertEqual(3, len(selected_ids))
        self.assertFalse(any(order['combat_mode'] == 'base_capture'
                             for order in approach))

        for state in states:
            state['z'] = 100.0
        orders = planner.build_orders(
            manifest, states, [], 2.0, _capture_defense())['orders']
        capture = [order for order in orders
                   if order['combat_mode'] == 'base_capture']
        screen = [order for order in orders
                  if order['combat_mode'] == 'base_screen']

        self.assertEqual(3, len(capture))
        self.assertEqual(selected_ids, {order['id'] for order in capture})
        self.assertEqual(12, len(screen))
        self.assertTrue(all(order['route_id'] == 'lane' for order in screen))
        self.assertTrue(all('capture_base_id' not in order
                            for order in screen))

    def test_spgs_yield_capture_slots_to_regular_vehicles(self):
        planner = BotPlanner()
        manifest = [
            _bot(201, 1, 0, self.route, 'mediumTank'),
            _bot(202, 1, 1, self.route, 'heavyTank'),
            _bot(203, 1, 2, self.route, 'SPG'),
            _bot(204, 1, 3, self.route, 'SPG'),
            _bot(205, 1, 4, self.route, 'SPG'),
        ]
        states = [_state(bot['id'], 1, 0, 0) for bot in manifest]

        planner.build_orders(
            manifest, states, [], 1.0, _capture_defense())['orders']
        capture_ids = set(planner._base_capture[1]['bot_ids'])

        self.assertEqual({201, 202}, capture_ids)

    def test_capture_squad_is_stable_across_a_contact_cycle(self):
        planner = BotPlanner()
        manifest = [
            _bot(300 + index, 1, index, self.route, 'mediumTank')
            for index in range(5)
        ]
        states = [
            _state(bot['id'], 1, index * 25, 0)
            for index, bot in enumerate(manifest)
        ]
        defense = _capture_defense()
        planner.build_orders(manifest, states, [], 1.0, defense)
        initial_ids = set(planner._base_capture[1]['bot_ids'])
        self.assertEqual(3, len(initial_ids))

        contact = _contact(900, 0, 400, [])
        players = [{'id': 900, 'team': 2, 'alive': True}]
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(states, players), 2.0))
        engaged = planner.build_orders(
            manifest, states, players, 2.0, defense)['orders']
        self.assertFalse(any(order['combat_mode'] == 'base_capture'
                             for order in engaged))

        for state in states:
            state['z'] = (-900.0 if state['id'] in initial_ids else 450.0)
        planner.build_orders(manifest, states, players, 10.1, defense)
        resumed_ids = set(planner._base_capture[1]['bot_ids'])
        self.assertEqual(initial_ids, resumed_ids)

    def test_capture_squad_replaces_only_a_dead_member(self):
        planner = BotPlanner()
        manifest = [
            _bot(400 + index, 1, index, self.route, 'mediumTank')
            for index in range(5)
        ]
        states = [
            _state(bot['id'], 1, index * 20, 0)
            for index, bot in enumerate(manifest)
        ]
        defense = _capture_defense()
        planner.build_orders(manifest, states, [], 1.0, defense)
        initial_ids = set(planner._base_capture[1]['bot_ids'])
        lost_id = min(initial_ids)
        for state in states:
            if state['id'] == lost_id:
                state['alive'] = False
                break

        planner.build_orders(manifest, states, [], 2.0, defense)
        updated_ids = set(planner._base_capture[1]['bot_ids'])

        self.assertEqual(3, len(updated_ids))
        self.assertEqual(initial_ids - {lost_id},
                         updated_ids.intersection(initial_ids))
        self.assertNotIn(lost_id, updated_ids)

    def test_known_enemy_keeps_the_route_instead_of_starting_base_capture(self):
        planner = BotPlanner()
        contact = _contact(2, 0, 400, [])
        players = self._report(planner, [contact])
        defense = _capture_defense()

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0, defense)['orders'][0]

        self.assertEqual('route', order['combat_mode'])
        self.assertNotIn('capture_base_id', order)

    def test_two_wide_enemy_lanes_trigger_crossfire_withdrawal(self):
        planner = BotPlanner()
        contacts = [
            _contact(2, -130, 130, [11]),
            _contact(3, 130, 130, [11]),
        ]
        players = self._report(planner, contacts)

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]

        live_bot = planner._alive_bots(self.manifest, self.states)[0]
        self.assertGreaterEqual(planner._crossfire_risk(
            live_bot, list(planner._contacts[1].values())), 0.35)
        self.assertEqual('crossfire_withdraw', order['combat_mode'])

    def test_nearby_ally_changes_cautious_support_advance_score(self):
        contact = _contact(2, 0, 380, [12, 13], 'heavyTank')
        player = {'id': 2, 'team': 2, 'alive': True}
        cautious = _bot(
            12, 1, 0, self.route, 'mediumTank', {'support': 1.0})
        cautious['profile'].update({
            'dominant_role': 'support', 'desired_range': 200.0,
            'fire_range': 520.0,
        })
        cautious_state = _state(12, 1, 0, 0)

        solo = BotPlanner()
        self.assertEqual(1, solo.report_contacts(
            [contact], solo.known_targets([cautious_state], [player]), 1.0))
        solo_order = solo.build_orders(
            [cautious], [cautious_state], [player], 1.0)['orders'][0]
        self.assertEqual('support_hold', solo_order['combat_mode'])

        ally = _bot(13, 1, 1, self.route, 'mediumTank', {'support': 1.0})
        ally_state = _state(13, 1, 5, 0)
        supported = BotPlanner()
        self.assertEqual(1, supported.report_contacts(
            [contact], supported.known_targets(
                [cautious_state, ally_state], [player]), 1.0))
        orders = dict((value['id'], value) for value in
                      supported.build_orders(
                          [cautious, ally], [cautious_state, ally_state],
                          [player], 1.0)['orders'])
        self.assertEqual('advance_contact', orders[12]['combat_mode'])
        live_bots = supported._alive_bots(
            [cautious, ally], [cautious_state, ally_state])
        self.assertGreater(supported._ally_support_score(
            live_bots[0], live_bots,
            supported._contacts[1][('human', 2)]), 0.5)

    def test_stationary_armored_turreted_vehicle_angles_without_moving(self):
        planner = BotPlanner()
        contacts = [_contact(2, 0, 150, [11])]
        players = self._report(planner, contacts)

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]

        self.assertEqual('engage', order['combat_mode'])
        self.assertEqual(0.0, order['throttle_override'])
        self.assertGreaterEqual(abs(order['hull_angle_degrees']), 12.0)
        self.assertLessEqual(abs(order['hull_angle_degrees']), 30.0)
        self.assertNotEqual(order['aim_position'], order['face_position'])

        moved_target = {
            'alive': True, 'visible': True,
            'position': (30.0, 0.0, 170.0),
        }
        live = _overlay_live_target_pose(
            order, moved_target, (0.0, 0.0, 0.0))
        self.assertEqual(moved_target['position'], live['aim_position'])
        self.assertNotEqual(live['aim_position'], live['face_position'])
        with self.assertRaises(ValueError):
            _overlay_live_target_pose(
                dict(order, hull_angle_degrees=90.0), moved_target,
                (0.0, 0.0, 0.0))

        td_manifest = [_bot(
            21, 1, 0, self.route, 'AT-SPG', {'brawler': 1.0})]
        td_manifest[0]['profile'].update({
            'armor': 240.0, 'dominant_role': 'brawler',
            'desired_range': 180.0, 'fire_range': 520.0,
        })
        td_state = [_state(21, 1, 0, 0)]
        td_contact = [_contact(2, 0, 150, [21])]
        td_planner = BotPlanner()
        self.assertEqual(1, td_planner.report_contacts(
            td_contact, td_planner.known_targets(td_state, players), 1.0))
        td_order = td_planner.build_orders(
            td_manifest, td_state, players, 1.0)['orders'][0]
        self.assertNotIn('hull_angle_degrees', td_order)
        self.assertEqual(td_order['aim_position'], td_order['face_position'])

    def test_server_damage_accounting_forwards_hostile_bot_threat(self):
        clock = lambda: 12.5
        state = BattleState(clock=clock)
        state.players[1] = Player(
            1, None, ('test', 0), team=2, connected=True)
        state.bot_states[11] = {'id': 11, 'team': 1}
        received = []
        state.bot_planner.report_damage = lambda *values: received.append(
            values) or True

        state._record_damage(
            ('player', 1), ('bot', 11), 175, {})

        self.assertEqual([(11, 'player', 1, 175, 12.5)], received)


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
            _bot(14, 1, 3, route_a, 'mediumTank', {
                'support': 1.0, 'flanker': 1.0,
            }),
        ]
        states = [
            _state(11, 1, -100, 0),
            _state(12, 1, -100, 0),
            _state(13, 1, 100, 0),
            _state(14, 1, -100, 0),
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
        self.assertEqual('b', orders[13]['route_id'])
        self.assertEqual(1, sum(
            orders[bot_id]['route_id'] == 'b' for bot_id in (12, 14)))

    def test_rebalance_keeps_scouts_off_an_incompatible_heavy_lane(self):
        planner = BotPlanner()
        middle = _route('middle', [
            (0, 0, False), (0, 100, False), (0, 500, False),
        ], capacity=4,
            class_weights={'lightTank': 1.0, 'heavyTank': 0.02},
            role_weights={'scout': 1.0})
        heavy = _route('heavy', [
            (100, 0, False), (100, 100, False), (100, 500, False),
        ], capacity=6,
            class_weights={'lightTank': 0.12, 'heavyTank': 1.0},
            role_weights={'brawler': 1.0})
        manifest = [
            _bot(20 + value, 1, value, middle, 'lightTank', {'scout': 1.0})
            for value in range(4)
        ] + [
            _bot(30 + value, 1, 4 + value, heavy, 'heavyTank',
                 {'brawler': 1.0})
            for value in range(5)
        ]
        states = [
            _state(bot['id'], 1,
                   0 if bot['profile']['class_tag'] == 'lightTank' else 100,
                   0)
            for bot in manifest
        ]
        bots = planner._alive_bots(manifest, states)
        contacts = [{
            'position': {'x': 100.0, 'y': 0.0, 'z': 250.0},
            'health': 1000.0,
            'max_health': 1000.0,
        } for unused in range(4)]

        planner._rebalance_routes(1, bots, contacts, 1.0)

        self.assertTrue(all(
            planner._route_assignments[bot_id]['route']['id'] == 'middle'
            for bot_id in range(20, 24)))

    def test_spg_does_not_fill_frontline_capacity_during_rebalance(self):
        planner = BotPlanner()
        source = _route('source', [
            (-100, 0, False), (-100, 100, False), (-100, 500, False),
        ], capacity=4)
        target = _route('target', [
            (100, 0, False), (100, 100, False), (100, 500, False),
        ], capacity=1)
        manifest = [
            _bot(41, 1, 0, source, 'mediumTank', {'support': 1.0}),
            _bot(42, 1, 1, source, 'mediumTank', {'support': 0.9}),
            _bot(43, 1, 2, target, 'SPG', {'artillery': 1.0}),
        ]
        states = [
            _state(41, 1, -100, 0),
            _state(42, 1, -100, 0),
            _state(43, 1, 100, 0),
        ]
        bots = planner._alive_bots(manifest, states)
        contacts = [{
            'position': {'x': 100.0, 'y': 0.0, 'z': 250.0},
            'health': 1000.0,
            'max_health': 1000.0,
        }]

        planner._rebalance_routes(1, bots, contacts, 1.0)
        donor_id = next(
            bot_id for bot_id in (41, 42)
            if planner._route_assignments[bot_id]['route']['id'] == 'target')
        donor = next(bot for bot in bots if bot['id'] == donor_id)
        planner._route(donor, 1.1)
        planner._route_states[donor_id]['index'] = 1
        planner._rebalance_routes(1, bots, contacts, 5.0)
        planner._rebalance_routes(1, bots, contacts, 9.0)

        assigned = dict((bot_id, value['route']['id'])
                        for bot_id, value in
                        planner._route_assignments.items())
        self.assertEqual('target', assigned[43])
        self.assertEqual(1, sum(
            assigned[bot_id] == 'target' for bot_id in (41, 42)))
        self.assertEqual(1, planner._route_states[donor_id]['index'])
        self.assertGreater(
            planner._route_assignments[donor_id]['until'], 9.0)


if __name__ == '__main__':
    unittest.main()
