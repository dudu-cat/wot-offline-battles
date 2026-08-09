import copy
import math
import sys
from pathlib import Path
import os
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.ai import cover, maps
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.driver import LocalDriver
from gui.mods.offline_lan_0922.ai.planner import BattleDirector
from gui.mods.offline_lan_0922.ai.navigation import (
    BAKED_SHALLOW_WATER, TerrainGrid, TerrainNavigator,
)
from gui.mods.offline_lan_0922 import prebaked_navigation
from lan_battle_server import MAP_POOL


class BotAiPortTests(unittest.TestCase):
    @staticmethod
    def _formations():
        return {
            '1': tuple((float(slot % 5) * 12.0, 0.0,
                        -100.0 + float(slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple((float(slot % 5) * 12.0, 0.0,
                        100.0 - float(slot // 5) * 12.0, 3.14159)
                       for slot in range(15)),
        }

    @staticmethod
    def _baked_graph(width, height, blocked=()):
        directions = (
            (-1, -1), (0, -1), (1, -1),
            (-1, 0), (1, 0),
            (-1, 1), (0, 1), (1, 1),
        )
        blocked = set(blocked)
        heights = [0 if (x, z) not in blocked else None
                   for z in range(height) for x in range(width)]
        links = [0] * (width * height)
        for z in range(height):
            for x in range(width):
                index = z * width + x
                if heights[index] is None:
                    continue
                for direction_index, (dx, dz) in enumerate(directions):
                    next_x, next_z = x + dx, z + dz
                    if not (0 <= next_x < width and 0 <= next_z < height):
                        continue
                    if heights[next_z * width + next_x] is None:
                        continue
                    if dx and dz and (
                            heights[z * width + next_x] is None or
                            heights[next_z * width + x] is None):
                        continue
                    links[index] |= 1 << direction_index
        return {
            'format': 'offline-lan-0922-navgraph', 'version': 2,
            'game_version': '0.9.22.0.1-cn-1513', 'map': '01_karelia',
            'cell_size': 4.0, 'origin': (10.0, 20.0),
            'bounds': (8.0, 18.0, 10.0 + width * 4.0,
                       20.0 + height * 4.0),
            'width': width, 'height': height,
            'heights_mm': heights, 'links': links,
            'hazards': [0] * (width * height),
            'bake': {'max_grade': 0.30},
        }

    def test_graph_loader_uses_real_mod_config_filesystem(self):
        self.assertEqual(
            os.path.normpath('./mods/configs/offline_lan_0922'),
            os.path.normpath(prebaked_navigation.mod_dir()))

    def test_preserves_annotated_standard_maps(self):
        # The server pool is the exact #1513 standard-mode candidate set.
        # TACTICAL_MAPS also retains older annotations that are useful for
        # other supported clients, so it may be a strict superset.
        self.assertTrue(set(MAP_POOL).issubset(set(maps.TACTICAL_MAPS)))
        karelia = maps.get_tactical_map('spaces/01_karelia')
        self.assertEqual('01_karelia', karelia['name'])
        self.assertTrue(karelia['routes'][1])
        self.assertTrue(karelia['routes'][2])

    def test_cover_contract_is_plain_data_and_deterministic(self):
        result = cover.score_candidates([{
            'id': 'ridge', 'position': (1, 2, 3), 'travel_distance': 5,
            'route_alignment': 1, 'enemy_occlusion': 1, 'exposure': 0,
            'slope': 0, 'water': 0, 'ally_congestion': 0,
            'peek_feasible': True, 'peek_position': (2, 2, 3),
            'escape_feasible': True,
        }])
        self.assertEqual('ridge', result[0]['id'])
        self.assertEqual({'x': 1.0, 'y': 2.0, 'z': 3.0}, result[0]['position'])

    def test_adapter_returns_no_engine_objects(self):
        descriptor = {'type': {'name': 'MS-1', 'tags': ('mediumTank',)},
                      'physics': {'speedLimits': (18.0,)}, 'hull': {},
                      'turret': {}, 'gun': {'shots': ()}}
        adapter = BotAdapter('01_karelia', 7)
        adapter.register(1, 1, descriptor)
        order = adapter.decide({
            'id': 1, 'position': (0, 0, 0), 'yaw': 0, 'speed': 0,
            'dt': 0.05, 'now': 1, 'health': 100, 'max_health': 100,
            'contacts': (), 'neighbours': (),
        }, lambda yaw: True)
        self.assertEqual(1, order['bot_id'])
        self.assertIn('throttle', order)
        self.assertIsInstance(order['move_position'], tuple)

    def test_adapter_preserves_face_and_commanded_hold_semantics(self):
        descriptor = {'type': {'name': 'MS-1', 'tags': ('mediumTank',)},
                      'physics': {'speedLimits': (18.0,)}, 'hull': {},
                      'turret': {}, 'gun': {'shots': ()}}
        adapter = BotAdapter('01_karelia', 7)
        adapter.register(1, 1, descriptor)
        order = adapter.decide_with_order({
            'id': 1, 'position': (0.0, 0.0, 0.0), 'yaw': 0.0,
            'speed': 0.0, 'dt': 0.05, 'now': 1.0,
            'neighbours': (),
        }, {
            'target_id': 2,
            'aim_position': (0.0, 0.0, 50.0),
            'move_position': (0.0, 0.0, 0.0),
            'face_position': (20.0, 0.0, 40.0),
            'fire_allowed': True, 'fire_range': 400.0,
            'combat_mode': 'cover_hold', 'shell_index': 0,
            'throttle_override': 0.0,
        }, lambda unused_yaw: True)

        self.assertEqual((20.0, 0.0, 40.0), order['face_position'])
        self.assertFalse(order['movement_intent'])
        self.assertEqual(0.0, order['throttle'])
        self.assertGreater(order['turn'], 0.0)
        self.assertAlmostEqual(math.atan2(20.0, 40.0), order['target_yaw'])

    def test_local_director_does_not_jiggle_without_confirmed_cover(self):
        descriptor = {
            'type': {'name': 'heavy', 'tags': ('heavyTank',)},
            'physics': {'speedLimits': (12.0,)},
            'hull': {'primaryArmor': 180.0},
            'turret': {'primaryArmor': 180.0,
                       'circularVisionRadius': 400.0},
            'gun': {'shots': ()},
        }
        director = BattleDirector('04_himmelsdorf', 45)
        agent = director.register(401, 1, descriptor, 'Jiggler')
        agent['personality'].update({
            'caution': 0.2, 'patience': 0.2,
            'aggression': 0.3, 'jiggle': 0.95,
        })
        position = (185.0, 0.0, -82.0)
        target = (185.0, 0.0, -22.0)
        modes = set()
        throttle_values = set()

        for tick in range(600):
            now = tick * 0.2
            director.update_contact(
                1, 402, 2, target, 1000, 1000,
                'heavyTank', True, now)
            order = director.order_for(
                401, position, 0.0, 1000, 1000, now)
            modes.add(order['combat_mode'])
            throttle_values.add(order['throttle_override'])

        self.assertNotIn('jiggle_forward', modes)
        self.assertNotIn('jiggle_back', modes)
        self.assertEqual({None}, throttle_values)

    def test_navigation_accepts_caller_probes(self):
        grid = TerrainGrid(lambda x, z, hint_y: 0.0,
                           bounds=(-50, -50, 50, 50))
        path = grid.plan((0, 0, 0), (30, 0, 30))
        self.assertTrue(path)

    def test_baked_graph_uses_immutable_links_without_runtime_probes(self):
        # The 8-way link bits match the shipped graph contract: east is bit 4
        # and west is bit 3. A runtime probe that raises proves the baked
        # geometry rather than an accidental fallback supplies the path.
        graph = {
            'format': 'offline-lan-0922-navgraph', 'version': 2,
            'game_version': '0.9.22.0.1-cn-1513', 'map': '01_karelia',
            'cell_size': 4.0, 'origin': (0.0, 0.0), 'bounds': (0, 0, 8, 0),
            'width': 3, 'height': 1, 'heights_mm': (0, 0, 0),
            'links': (1 << 4, (1 << 3) | (1 << 4), 1 << 3),
            'hazards': (0, 0, 0),
            'spawn_anchors': ((0.0, 0.0), (8.0, 0.0)),
            'objective_bases': ((8.0, 0.0), (0.0, 0.0)),
            'spawn_formations': self._formations(),
            'routes': {
                '1': ({'id': 'lane', 'waypoints': (
                    (0.0, 0.0, False), (8.0, 0.0, False))},),
                '2': ({'id': 'lane', 'waypoints': (
                    (8.0, 0.0, False), (0.0, 0.0, False))},),
            },
            'bake': {'max_grade': 0.30},
        }
        self.assertIs(prebaked_navigation._validate(graph, '01_karelia'), graph)
        grid = TerrainGrid(lambda *unused: (_ for _ in ()).throw(AssertionError()),
                           baked_graph=graph)
        self.assertTrue(grid.prebaked)
        path = grid.plan((0, 0, 0), (8, 0, 0))
        self.assertEqual((0.0, 0.0, 0.0), path[0])
        self.assertEqual((8.0, 0.0, 0.0), path[-1])

    def test_prebaked_shortcuts_do_not_cut_across_shallow_water(self):
        graph = self._baked_graph(5, 3)
        graph['hazards'] = [
            0, 4, 4, 4, 0,
            0, 0, 0, 0, 0,
            0, 0, 0, 0, 0,
        ]
        navigator = TerrainNavigator(lambda *unused: None,
                                     baked_graph=graph)
        start = (10.0, 0.0, 20.0)
        goal = (26.0, 0.0, 20.0)

        unused_key, path = navigator._path(
            ('route', 1, 'dry-detour'), start, goal, 1.0, None)

        self.assertTrue(path)
        self.assertGreater(max(point[2] for point in path), 20.0)
        for first, second in zip(path, path[1:]):
            self.assertFalse(navigator.grid.segment_has_baked_hazard(
                first, second, BAKED_SHALLOW_WATER))

    def test_prebaked_hazard_check_allows_tank_to_leave_shallow_water(self):
        graph = self._baked_graph(3, 1)
        graph['hazards'] = [4, 0, 0]
        grid = TerrainGrid(lambda *unused: None, baked_graph=graph)

        self.assertFalse(grid.segment_has_baked_hazard(
            (10.0, 0.0, 20.0), (18.0, 0.0, 20.0),
            BAKED_SHALLOW_WATER))

    def test_navigation_housekeeping_runs_once_per_second(self):
        navigator = TerrainNavigator(lambda *unused: 0.0)
        calls = []
        navigator.grid.prune_failed_edges = (
            lambda now: calls.append(('prune', now)))
        navigator.grid.trim_caches = lambda: calls.append(('trim', None))

        navigator.tick(1.0)
        navigator.tick(1.0)
        navigator.tick(1.5)
        navigator.tick(2.1)

        self.assertEqual([
            ('prune', 1.0), ('trim', None),
            ('prune', 2.1), ('trim', None),
        ], calls)

    def test_superseded_route_join_search_is_cancelled_for_its_bot(self):
        navigator = TerrainNavigator(lambda *unused: 0.0)
        route_join = (('route_join', 11, 2, 'forest', 1), (4, 5))
        other_bot = (('route_join', 12, 2, 'forest', 1), (4, 5))
        navigator.searches[route_join] = object()
        navigator.searches[other_bot] = object()
        navigator.search_times[route_join] = 1.0
        navigator.search_times[other_bot] = 1.0

        navigator._cancel_bot_searches(11)

        self.assertNotIn(route_join, navigator.searches)
        self.assertNotIn(route_join, navigator.search_times)
        self.assertIn(other_bot, navigator.searches)
        self.assertIn(other_bot, navigator.search_times)

    def test_graph_validation_rejects_incomplete_battle_contract(self):
        graph = {
            'format': 'offline-lan-0922-navgraph', 'version': 2,
            'game_version': '0.9.22.0.1-cn-1513', 'map': '01_karelia',
            'cell_size': 4.0, 'origin': (0.0, 0.0),
            'bounds': (0.0, 0.0, 8.0, 0.0),
            'width': 3, 'height': 1, 'heights_mm': (0, 0, 0),
            'links': (1 << 4, (1 << 3) | (1 << 4), 1 << 3),
            'hazards': (0, 0, 0),
            'spawn_anchors': ((0.0, 0.0), (8.0, 0.0)),
            'objective_bases': ((8.0, 0.0), (0.0, 0.0)),
            'spawn_formations': self._formations(),
            'routes': {
                '1': ({'id': 'lane', 'waypoints': (
                    (0.0, 0.0, False), (8.0, 0.0, False))},),
                '2': ({'id': 'lane', 'waypoints': (
                    (8.0, 0.0, False), (0.0, 0.0, False))},),
            },
        }

        cases = {
            'format': lambda value: value.update(format='wrong'),
            'version': lambda value: value.update(version=1),
            'game_version': lambda value: value.update(game_version='wrong'),
            'map': lambda value: value.update(map='02_malinovka'),
            'grid_array': lambda value: value.update(hazards=(0,)),
            'team_routes': lambda value: value['routes'].update({'2': ()}),
            'route_length': lambda value: value['routes']['1'][0].update(
                waypoints=((0.0, 0.0, False),)),
            'spawn_anchors': lambda value: value.update(spawn_anchors=()),
            'objective_bases': lambda value: value.update(objective_bases=()),
            'spawn_formations': lambda value: value.update(
                spawn_formations={}),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                invalid = copy.deepcopy(graph)
                mutate(invalid)
                with self.assertRaises(ValueError):
                    prebaked_navigation._validate(invalid, '01_karelia')

    def test_baked_routes_keep_validated_team_endpoints_unchanged(self):
        team_one = ((-2.0, -350.0, False),
                    (40.0, -120.0, True),
                    (-2.0, 350.0, False))
        team_two = tuple(reversed(team_one))
        director = BattleDirector('95_lost_city', 7, baked_routes={
            '1': ({'id': 'baked-1', 'waypoints': team_one},),
            '2': ({'id': 'baked-2', 'waypoints': team_two},),
        })

        first = director.register_profile(1, 1, {'roles': {}}, 'First')
        second = director.register_profile(2, 2, {'roles': {}}, 'Second')

        self.assertEqual(team_one, first['route']['waypoints'])
        self.assertEqual(team_two, second['route']['waypoints'])
        self.assertEqual(3, len(first['route']['waypoints']))

    def test_route_hold_metadata_does_not_pause_local_director(self):
        director = BattleDirector('07_lakeville', 'no-route-holds')
        agent = director.register_profile(
            92, 1, {'roles': {}, 'vehicle_name': 'medium'},
            'Continuous traveller')
        agent['route'] = {
            'waypoints': (
                (0.0, -40.0, False),
                (0.0, -20.0, True),
                (0.0, 80.0, False),
            )}
        agent['route_started'] = True
        agent['waypoint_index'] = 1

        target = director._route_position(agent, (0.0, 0.0, -20.0), 3.0)

        self.assertEqual((0.0, 0.0, 80.0), target)
        self.assertEqual(2, agent['waypoint_index'])

    def test_spawn_skips_rear_connector_and_anchors_navigation_at_hull(self):
        director = BattleDirector('07_lakeville', 'forward-join')
        descriptor = {
            'type': {'name': 'medium', 'tags': ('mediumTank',)},
            'physics': {'speedLimits': (18.0,)}, 'hull': {},
            'turret': {}, 'gun': {'shots': ()}}
        agent = director.register(
            93, 1, descriptor, 'Forward join bot')
        agent['route'] = {
            'waypoints': (
                (0.0, -40.0, False),
                (0.0, -65.0, False),
                (40.0, 40.0, False),
            )}
        agent['route_started'] = False

        order = director.order_for(
            93, (0.0, 0.0, -20.0), 0.0, 1000, 1000, 0.0)

        self.assertEqual((40.0, 0.0, 40.0), order['move_position'])
        self.assertEqual(2, agent['waypoint_index'])
        self.assertEqual((0.0, 0.0, -20.0), order['route_anchor'])

    def test_normal_route_turn_keeps_full_throttle(self):
        driver = LocalDriver()
        target_yaw = 0.9
        order = driver.drive(
            2, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
            (math.sin(target_yaw) * 50.0, 0.0,
             math.cos(target_yaw) * 50.0),
            (), lambda unused_angle: True)

        self.assertEqual('drive', order['recovery_mode'])
        self.assertEqual(1.0, order['throttle'])
        self.assertGreater(order['turn'], 0.9)

    def test_wall_avoidance_commits_to_one_clear_branch(self):
        driver = LocalDriver()

        def clear(yaw):
            return abs(yaw) > 0.20

        first = driver.drive(
            81, (0.0, 0.0, 0.0), 0.0, 3.0, 0.05,
            (0.0, 0.0, 50.0), (), clear)
        shifted_yaw = 1.30
        second = driver.drive(
            81, (0.0, 0.0, 0.1), 0.1, 3.0, 0.05,
            (math.sin(shifted_yaw) * 50.0, 0.0,
             math.cos(shifted_yaw) * 50.0), (), clear)

        self.assertEqual('avoid', first['recovery_mode'])
        self.assertAlmostEqual(
            first['target_yaw'], second['target_yaw'], places=6)


if __name__ == '__main__':
    unittest.main()
