import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922'


def _graph(map_name='01_karelia', waypoint_count=2):
    waypoints = tuple((float(index * 4), 0.0, False)
                      for index in range(waypoint_count))
    reverse = tuple(reversed(waypoints))
    return {
        'format': 'offline-lan-0922-navgraph', 'version': 2,
        'game_version': '0.9.22.0.1-cn-1513', 'map': map_name,
        'cell_size': 4.0, 'origin': (0.0, 0.0), 'bounds': (0, 0, 8, 0),
        'width': 3, 'height': 1, 'heights_mm': (0, 0, 0),
        'links': (1 << 4, (1 << 3) | (1 << 4), 1 << 3),
        'hazards': (0, 0, 0),
        'spawn_anchors': ((0.0, 0.0), (8.0, 0.0)),
        'objective_bases': ((8.0, 0.0), (0.0, 0.0)),
        'spawn_formations': {
            '1': tuple((float(slot % 5) * 12.0, 0.0,
                        -100.0 + float(slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple((float(slot % 5) * 12.0, 0.0,
                        100.0 - float(slot // 5) * 12.0, 3.14159)
                       for slot in range(15)),
        },
        'routes': {
            '1': ({'id': 'safe-1', 'waypoints': waypoints},),
            '2': ({'id': 'safe-2', 'waypoints': reverse},),
        },
        'bake': {'max_grade': 0.30},
    }


def _spawn_resolver(team, slot):
    point = _graph()['spawn_formations'][str(int(team))][int(slot)]
    return ((point[0], point[1], point[2]), point[3])


def _load():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name); module.__path__ = [str(PACKAGE_ROOT)]; sys.modules[name] = module
    for name in ('gui.mods.offline_lan_0922.ai',):
        if name not in sys.modules:
            module = types.ModuleType(name); module.__path__ = [str(PACKAGE_ROOT / 'ai')]; sys.modules[name] = module
    name = 'gui.mods.offline_lan_0922.bot_runtime'; sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, PACKAGE_ROOT / 'bot_runtime.py')
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return module

class _Director(object):
    def __init__(self): self.registered = []
    def register_profile(self, *args): self.registered.append(args)

class _Adapter(object):
    def __init__(self, *unused):
        self.director = _Director(); self.calls = []; self.server_orders = []
    def register(self, *args): self.director.registered.append(args)
    def decide(self, state, clear):
        self.calls.append((state, clear(state['yaw'])))
        target_id = (state['contacts'][0]['id']
                     if state.get('contacts') else None)
        return {'target_yaw': 0.0, 'throttle': 1.0, 'shell_index': 2,
                'fire_allowed': True, 'target_id': target_id,
                'fire_range': 500.0}
    def decide_with_order(self, state, strategic, clear):
        self.server_orders.append(dict(strategic))
        command = self.decide(state, clear)
        command.update({
            'target_id': strategic.get('target_id'),
            'fire_allowed': bool(strategic.get('fire_allowed')),
            'shell_index': int(strategic.get('shell_index', 0)),
            'fire_range': float(strategic.get('fire_range', 0.0)),
        })
        return command


class _FixedAdapter(_Adapter):
    def __init__(self, command):
        super().__init__()
        self.command = dict(command)

    def decide(self, state, clear):
        self.calls.append((state, clear(state['yaw'])))
        return dict(self.command)

    def decide_with_order(self, state, strategic, clear):
        self.server_orders.append(dict(strategic))
        return self.decide(state, clear)


def _combat_descriptor(reload_time=0.5, clip=(2, 0.2),
                       turret_yaw_limits=(-math.pi, math.pi),
                       turret_speed=10.0, gun_speed=10.0):
    gun = types.SimpleNamespace(
        shots=({'shell': {'effectsIndex': 0}},), reloadTime=reload_time,
        clip=clip, turretYawLimits=turret_yaw_limits,
        pitchLimits={'absolute': (-0.35, 0.15)}, rotationSpeed=gun_speed)
    return types.SimpleNamespace(
        gun=gun, turret={'rotationSpeed': turret_speed,
                         'circularVisionRadius': 445.0},
        physics={'speedLimits': (14.0, 7.0)}, hull={}, maxHealth=1000)


def _critical_descriptor():
    descriptor = _combat_descriptor()
    descriptor.chassis = {'maxHealth': 170, 'maxRegenHealth': 130}
    descriptor.fuelTank = {'maxHealth': 100, 'maxRegenHealth': 40}
    descriptor.miscAttrs = {}
    return descriptor


def _critical_payload(*records, **values):
    return {
        'devices': [dict(record) for record in records],
        'destroyed': list(values.get('destroyed', ())),
        'crew_ko': list(values.get('crew_ko', ())),
        'fire': bool(values.get('fire', False)),
        'ammo_rack_death': False, 'events': [],
    }


def _snapshot_bot(bot_id=11, health=1000, alive=True, critical=None,
                  revision=0, base_revision=0, ack_seq=0,
                  fire_elapsed=0.0, fire_timer=0.0, **values):
    result = {
        'id': bot_id, 'health': health, 'alive': alive,
        'critical': dict(critical or {}),
        'combat_revision': revision,
        'combat_base_revision': base_revision,
        'combat_ack_seq': ack_seq,
        'combat_fire_elapsed': fire_elapsed,
        'combat_fire_timer': fire_timer,
    }
    result.update(values)
    return result

class BotRuntimeTests(unittest.TestCase):
    def setUp(self):
        self._modules = dict((key, value) for key, value in sys.modules.items()
                             if key == 'gui' or key.startswith('gui.'))
        self.module = _load(); self.adapters = []
        def factory(*args):
            adapter = _Adapter(*args); self.adapters.append(adapter); return adapter
        self.runtime = self.module.BotRuntime(1, adapter_factory=factory,
            direction_probe=lambda position, yaw: {'clear': True, 'slope': .2},
            ground_probe=lambda unused_x, unused_z, unused_hint: 0.0,
            physics_ground_probe=lambda unused_x, unused_z, unused_hint: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        self.start = {'round_id': 5, 'map': '01_karelia', 'bot_authority_id': 1,
                      'bots': [{'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot'}]}

    def tearDown(self):
        for key in list(sys.modules):
            if key == 'gui' or key.startswith('gui.'):
                sys.modules.pop(key, None)
        sys.modules.update(self._modules)

    def test_authority_builds_manifest_once_from_battle_roster(self):
        first = self.runtime.battle_start(self.start)
        self.assertEqual('bot_manifest', first[0]['type'])
        self.assertEqual(11, first[0]['bots'][0]['id'])
        self.assertEqual([], self.runtime.battle_start(self.start))

    def test_injected_baked_graph_replaces_runtime_grid_and_passes_routes(self):
        graph = _graph()
        seen = []
        def factory(*args, **kwargs):
            seen.append((args, kwargs))
            return _Adapter(*args)
        runtime = self.module.BotRuntime(
            1, adapter_factory=factory, baked_graph=graph,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0})
        runtime.battle_start(self.start)
        self.assertTrue(runtime.navigator.grid.prebaked)
        self.assertEqual(graph['routes'], seen[0][1]['baked_routes'])

    def test_supported_map_with_empty_baked_routes_fails_closed(self):
        graph = _graph()
        graph['routes'] = {'1': (), '2': ()}
        seen = []

        def factory(*args, **kwargs):
            seen.append((args, kwargs))
            return _Adapter(*args)

        runtime = self.module.BotRuntime(
            1, adapter_factory=factory, baked_graph=graph,
            ground_probe=lambda *unused: 0.0)
        with self.assertRaisesRegex(
                ValueError, 'navigation graph routes are missing'):
            runtime.battle_start(self.start)

        self.assertEqual([], seen)
        self.assertIsNone(runtime.baked_graph)

    def test_unknown_developer_map_is_rejected_without_runtime_navigation(self):
        seen = []

        def factory(*args, **kwargs):
            seen.append((args, kwargs))
            return _Adapter(*args)

        runtime = self.module.BotRuntime(
            1, adapter_factory=factory,
            ground_probe=lambda unused_x, unused_z, unused_hint: 0.0)
        start = dict(self.start, map='dev_test_map')

        with self.assertRaisesRegex(ValueError, 'map is not supported'):
            runtime.battle_start(start)

        self.assertEqual([], seen)
        self.assertIsNone(runtime.baked_graph)

    def test_supported_map_without_graph_fails_closed(self):
        original = self.module.prebaked_navigation.load_graph
        self.module.prebaked_navigation.load_graph = lambda unused: None
        try:
            runtime = self.module.BotRuntime(
                1, adapter_factory=lambda *args: _Adapter(*args))
            with self.assertRaisesRegex(
                    ValueError, 'required navigation graph is missing'):
                runtime.battle_start(self.start)
        finally:
            self.module.prebaked_navigation.load_graph = original

    def test_new_map_replaces_previous_navigation_graph(self):
        first_graph = _graph('01_karelia')
        second_graph = _graph('02_malinovka')
        loaded = []
        original = self.module.prebaked_navigation.load_graph
        self.module.prebaked_navigation.load_graph = lambda name: (
            loaded.append(name) or second_graph)
        try:
            runtime = self.module.BotRuntime(
                1, adapter_factory=lambda *args, **unused: _Adapter(*args),
                baked_graph=first_graph,
                ground_probe=lambda *unused: 0.0,
                physics_ground_probe=lambda *unused: 0.0,
                spawn_resolver=_spawn_resolver)
            runtime.battle_start(self.start)
            runtime.battle_start(dict(
                self.start, round_id=6, map='02_malinovka'))
        finally:
            self.module.prebaked_navigation.load_graph = original

        self.assertEqual(['02_malinovka'], loaded)
        self.assertIs(second_graph, runtime.baked_graph)
        self.assertEqual('02_malinovka', runtime._navigation_map_name)

    def test_30hz_state_updates_pose_input_and_fire_sequence(self):
        self.runtime.descriptor_resolver = lambda unused: _combat_descriptor(
            reload_time=0.45, clip=(1,))
        self.runtime.battle_start(self.start)
        self.assertEqual([], self.runtime.update(.02, 1.0))
        result = self.runtime.update(.02, 1.02, players=[{'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5}])
        self.assertEqual(0, result[0]['bots'][0]['fire_seq'])
        player = [{'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5}]
        self.runtime.update(.20, 1.22, players=player)
        self.runtime.update(.20, 1.42, players=player)
        result = self.runtime.update(.04, 1.46, players=player)
        bot = result[0]['bots'][0]
        self.assertEqual('bot_state', result[0]['type'])
        self.assertGreater(bot['z'], 0.0); self.assertEqual(1, bot['fire_seq'])
        self.assertEqual(0, bot['shell_index'])

    def test_reverse_recovery_uses_driver_turn_sign_not_target_bearing(self):
        command = {
            'target_yaw': 1.0, 'throttle': -0.72, 'turn': -1.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 20.0),
            'face_position': (0.0, 0.0, 20.0),
            'move_position': (0.0, 0.0, 20.0),
            'recovery_mode': 'reverse_turn', 'movement_intent': True,
        }
        runtime = self.module.BotRuntime(
            1, adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.states[11]['speed'] = -1.0

        state = runtime.update(.04, 1.0)[0]['bots'][0]

        self.assertEqual(-1, state['rotation_dir'])
        # Reverse motion flips track steering inside the copied physics law, so
        # a negative input produces the requested positive hull-yaw recovery.
        self.assertGreater(runtime._turn_speeds[11], 0.0)

    def test_driver_proportional_turn_is_not_collapsed_to_keyboard_sign(self):
        command = {
            'target_yaw': 0.2, 'throttle': 1.0, 'turn': 0.2,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 20.0),
            'face_position': (0.0, 0.0, 20.0),
            'move_position': (0.0, 0.0, 20.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        runtime = self.module.BotRuntime(
            1, adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)

        state = runtime.update(.04, 1.0)[0]['bots'][0]

        self.assertEqual(1, state['rotation_dir'])
        self.assertAlmostEqual(
            runtime._physics_params[11]['rotSpd'] * 0.2,
            runtime._turn_speeds[11])

    def test_limited_traverse_tank_turns_hull_before_advancing_or_firing(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (100.0, 0.5, 0.0),
            'face_position': (100.0, 0.5, 0.0),
            'move_position': (100.0, 0.0, 0.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                turret_yaw_limits=(-0.1, 0.1)),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.states[11]['yaw'] = 0.0

        state = runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'x': 100.0, 'y': 0.5, 'z': 0.0}
        ])[0]['bots'][0]

        self.assertEqual(0, state['movement_dir'])
        self.assertEqual(1, state['rotation_dir'])
        self.assertTrue(state['hull_aiming'])
        self.assertEqual(0, state['fire_seq'])

    def test_bot_fire_uses_turret_pitch_los_reload_clip_and_barrel_scatter(self):
        lane_probes = []

        def firing_lane(source, target):
            lane_probes.append((source['id'], target['network_id']))
            # Deliberately block the first otherwise-ready shot.
            return len(lane_probes) != 1

        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 10.5, 100.0),
            'face_position': (0.0, 10.5, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                gun_speed=0.25),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=firing_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state['x'], state['y'], state['z'], state['yaw'] = 0.0, 0.0, 0.0, 0.0
        player = {'id': 2, 'team': 1, 'x': 0.0, 'y': 10.5, 'z': 100.0}

        # The gun first slews to the visible elevated target; it cannot fire
        # merely because the strategic order says fire_allowed.
        first = runtime.update(.20, 1.0, players=[player])[0]['bots'][0]
        self.assertEqual(0, first['fire_seq'])
        self.assertNotIn('shot_yaw', first)
        self.assertNotIn('shot_pitch', first)
        self.assertLess(first['gun_pitch'], 0.0)
        self.assertFalse(first['gun_aligned'])

        # The second slew tick aligns, but the full reload is not complete.
        aligned = runtime.update(.20, 1.2, players=[player])[0]['bots'][0]
        self.assertEqual(0, aligned['fire_seq'])
        self.assertTrue(aligned['gun_aligned'])
        self.assertEqual(0, len(lane_probes))

        # Once aligned and reloaded, a fresh static-lane probe still blocks.
        blocked = runtime.update(.11, 1.31, players=[player])[0]['bots'][0]
        self.assertEqual(0, blocked['fire_seq'])
        self.assertNotIn('shot_yaw', blocked)
        self.assertNotIn('shot_pitch', blocked)
        self.assertTrue(blocked['gun_aligned'])
        self.assertEqual(1, len(lane_probes))

        # The next fresh lane is clear. The emitted shot angles are the actual
        # dispersed barrel ray and the clip selects the intra-clip delay.
        fired = runtime.update(.20, 1.52, players=[player])[0]['bots'][0]
        self.assertEqual(1, fired['fire_seq'])
        self.assertIn('shot_yaw', fired)
        self.assertIn('shot_pitch', fired)
        self.assertAlmostEqual(0.0, fired['aim_yaw'], places=6)
        self.assertGreater(fired['shot_pitch'], 0.0)
        self.assertEqual(1, fired['clip'])
        self.assertAlmostEqual(0.2, fired['reload_duration'])

        runtime.update(.20, 1.72, players=[player])
        second = runtime.update(.04, 1.76, players=[player])[0]['bots'][0]
        self.assertEqual(2, second['fire_seq'])
        self.assertEqual(2, second['clip'])
        self.assertAlmostEqual(0.5, second['reload_duration'])

        runtime.update(.20, 1.94, players=[player])
        runtime.update(.20, 2.14, players=[player])
        early = runtime.update(.09, 2.23, players=[player])[0]['bots'][0]
        self.assertEqual(2, early['fire_seq'])
        third = runtime.update(.04, 2.27, players=[player])[0]['bots'][0]
        self.assertEqual(3, third['fire_seq'])

    def test_bot_critical_state_preserves_loader_reload_and_gun_gate(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 0.5, 100.0),
            'face_position': (0.0, 0.5, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.5, clip=(1,)),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0,
                     critical={'crew_ko': ['loader1'], 'destroyed': [],
                               'devices': []})
        player = {'id': 2, 'team': 1, 'x': 0.0, 'y': 0.5, 'z': 100.0}

        last = None
        for index in range(6):
            last = runtime.update(.20, 1.0 + index * .20,
                                  players=[player])[0]['bots'][0]
        self.assertEqual(0, last['fire_seq'])
        self.assertAlmostEqual(1.25, last['reload_duration'])
        fired = runtime.update(.20, 2.2, players=[player])[0]['bots'][0]
        self.assertEqual(1, fired['fire_seq'])

        runtime.states[11]['critical'] = {
            'crew_ko': [], 'devices': [], 'destroyed': ['gunHealth']}
        for index in range(5):
            blocked = runtime.update(
                .20, 2.4 + index * .20, players=[player])[0]['bots'][0]
        self.assertEqual(1, blocked['fire_seq'])

    def test_destroyed_bot_track_repairs_to_regen_cap(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        broken = _critical_payload({
            'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
            'state': 'destroyed'}, destroyed=['leftTrackHealth'])
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                critical=broken, revision=1, base_revision=1)]})

        outgoing = None
        for index in range(25):
            outgoing = runtime.update(.20, 1.0 + index * .20)[0]['bots'][0]

        device = outgoing['critical']['devices'][0]
        self.assertEqual('leftTrackHealth', device['name'])
        self.assertEqual(130.0, device['hp'])
        self.assertEqual('critical', device['state'])
        self.assertNotIn('leftTrackHealth', outgoing['critical']['destroyed'])

    def test_bot_fire_burns_five_percent_per_second_and_ends_at_ten_seconds(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                critical=burning, revision=1, base_revision=1)]})

        outgoing = None
        for index in range(49):
            outgoing = runtime.update(.20, index * .20)[0]['bots'][0]
        self.assertEqual(550, outgoing['health'])
        self.assertTrue(outgoing['critical']['fire'])

        outgoing = runtime.update(.20, 9.8)[0]['bots'][0]
        fuel = outgoing['critical']['devices'][0]
        self.assertEqual(500, outgoing['health'])
        self.assertFalse(outgoing['critical']['fire'])
        self.assertEqual(0.0, outgoing['combat_fire_elapsed'])
        self.assertEqual(0.0, outgoing['combat_fire_timer'])
        self.assertEqual(40.0, fuel['hp'])
        self.assertEqual('critical', fuel['state'])
        self.assertNotIn('fuelTankHealth', outgoing['critical']['destroyed'])

    def test_delayed_server_echo_cannot_rewind_bot_fire_or_repair_publication(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        baseline = _critical_payload(
            {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
             'state': 'destroyed'},
            {'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
             'state': 'destroyed'},
            destroyed=['leftTrackHealth', 'fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                critical=baseline, revision=1, base_revision=1)]})

        first = None
        for index in range(5):
            first = runtime.update(.20, index * .20)[0]['bots'][0]
        first_seq = runtime._combat_sync[11]['next_seq']
        first_track = dict((record['name'], record['hp'])
                           for record in first['critical']['devices'])[
                               'leftTrackHealth']
        self.assertEqual(950, first['health'])

        # The server has not consumed the publication yet and repeats its last
        # canonical state.  This is an echo, not a new critical hit.
        runtime.apply_snapshot({
            'server_tick': 11,
            'bots': [_snapshot_bot(
                critical=baseline, revision=1, base_revision=1)]})
        self.assertEqual(950, runtime.states[11]['health'])
        self.assertEqual(
            first_track, dict((record['name'], record['hp']) for record in
                              runtime.states[11]['critical']['devices'])[
                                  'leftTrackHealth'])

        second = None
        for index in range(5, 10):
            second = runtime.update(.20, index * .20)[0]['bots'][0]
        second_track = dict((record['name'], record['hp'])
                            for record in second['critical']['devices'])[
                                'leftTrackHealth']
        self.assertEqual(900, second['health'])
        self.assertGreater(second_track, first_track)

        # A later snapshot acknowledges the earlier exact publication.  It
        # advances the local revision boundary but cannot overwrite revision 2.
        runtime.apply_snapshot({
            'server_tick': 12,
            'bots': [_snapshot_bot(
                health=first['health'], critical=first['critical'],
                revision=1 + first_seq, base_revision=1,
                ack_seq=first_seq,
                fire_elapsed=first['combat_fire_elapsed'],
                fire_timer=first['combat_fire_timer'])]})
        self.assertEqual(900, runtime.states[11]['health'])
        self.assertEqual(
            second_track, dict((record['name'], record['hp']) for record in
                               runtime.states[11]['critical']['devices'])[
                                   'leftTrackHealth'])
        sync = runtime._combat_sync[11]
        self.assertEqual(first_seq, sync['acked_seq'])
        self.assertEqual(
            sync['next_seq'] - first_seq, len(sync['pending']))

    def test_external_hit_before_publication_ack_replays_unacked_fire_once(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                health=950, critical=burning,
                revision=1, base_revision=1)]})

        publication = None
        for index in range(5):
            publication = runtime.update(
                .20, .20 + index * .20)[0]['bots'][0]
        self.assertEqual((900, 5),
                         (publication['health'], publication['combat_seq']))
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=800, critical=burning,
                revision=2, base_revision=2, ack_seq=0)]})

        self.assertEqual(750, runtime.states[11]['health'])
        self.assertEqual((2, 1), (
            runtime.states[11]['combat_base_revision'],
            runtime.states[11]['combat_seq']))

    def test_external_hit_after_publication_ack_does_not_double_apply_fire(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                health=950, critical=burning,
                revision=1, base_revision=1)]})

        publication = None
        for index in range(5):
            publication = runtime.update(
                .20, .20 + index * .20)[0]['bots'][0]
        self.assertEqual(900, publication['health'])
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=800, critical=burning,
                revision=7, base_revision=7, ack_seq=5,
                fire_elapsed=1.0, fire_timer=0.0)]})

        self.assertEqual(800, runtime.states[11]['health'])
        self.assertEqual([], runtime._combat_sync[11]['pending'])

    def test_external_ignition_does_not_replay_pre_hit_time_as_fire(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        repairing = _critical_payload({
            'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
            'state': 'destroyed'}, destroyed=['leftTrackHealth'])
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                critical=repairing, revision=1, base_revision=1)]})
        for index in range(5):
            runtime.update(.20, .20 + index * .20)

        ignited = _critical_payload(
            {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
             'state': 'destroyed'},
            {'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
             'state': 'destroyed'},
            destroyed=['leftTrackHealth', 'fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=900, critical=ignited,
                revision=2, base_revision=2, ack_seq=0)]})

        self.assertEqual(900, runtime.states[11]['health'])
        self.assertEqual(0.0, runtime.states[11]['combat_fire_elapsed'])
        self.assertEqual(0.0, runtime.states[11]['combat_fire_timer'])

    def test_authority_handoff_preserves_fire_duration_and_tick_phase(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=800, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, critical=burning,
            combat_revision=22, combat_base_revision=1,
            combat_ack_seq=21, combat_fire_elapsed=4.4,
            combat_fire_timer=0.4)
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))

        for index in range(3):
            outgoing = runtime.update(
                .20, 100.2 + index * .20)[0]['bots'][0]
        self.assertEqual(750, outgoing['health'])
        self.assertEqual(5.0, outgoing['combat_fire_elapsed'])
        self.assertEqual(0.0, outgoing['combat_fire_timer'])

        for index in range(25):
            outgoing = runtime.update(
                .20, 100.8 + index * .20)[0]['bots'][0]
        self.assertEqual(500, outgoing['health'])
        self.assertFalse(outgoing['critical']['fire'])
        self.assertEqual(0.0, outgoing['combat_fire_elapsed'])
        self.assertEqual(0.0, outgoing['combat_fire_timer'])

    def test_authority_handoff_resets_to_same_base_server_ack_ahead(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, critical=burning,
            combat_revision=4, combat_base_revision=1,
            combat_ack_seq=3, combat_fire_elapsed=2.0,
            combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bot_authority_id=2))
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))

        local = runtime.update(.20, 100.2)[0]['bots'][0]
        self.assertEqual(4, local['combat_seq'])
        self.assertEqual(2.2, local['combat_fire_elapsed'])

        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                health=800, critical=burning,
                revision=6, base_revision=1, ack_seq=5,
                fire_elapsed=3.0, fire_timer=0.0)]})

        state = runtime.states[11]
        sync = runtime._combat_sync[11]
        self.assertEqual(800, state['health'])
        self.assertEqual(3.0, state['combat_fire_elapsed'])
        self.assertEqual((5, 5, 5), (
            state['combat_ack_seq'], state['combat_seq'], sync['next_seq']))
        self.assertEqual([], sync['pending'])
        self.assertEqual([], sync['unpublished_steps'])

        outgoing = runtime.update(.20, 100.4)[0]['bots'][0]
        self.assertEqual(800, outgoing['health'])
        self.assertEqual(3.2, outgoing['combat_fire_elapsed'])
        self.assertEqual(6, outgoing['combat_seq'])

    def test_authority_handoff_resets_same_sequence_signature_collision(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, critical=burning,
            combat_revision=4, combat_base_revision=1,
            combat_ack_seq=3, combat_fire_elapsed=2.0,
            combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bot_authority_id=2))
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))

        local = runtime.update(.20, 100.2)[0]['bots'][0]
        self.assertEqual(4, local['combat_seq'])
        self.assertEqual(2.2, local['combat_fire_elapsed'])

        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                health=800, critical=burning,
                revision=5, base_revision=1, ack_seq=4,
                fire_elapsed=3.0, fire_timer=0.0)]})

        state = runtime.states[11]
        sync = runtime._combat_sync[11]
        self.assertEqual(800, state['health'])
        self.assertEqual(3.0, state['combat_fire_elapsed'])
        self.assertEqual((4, 4, 4), (
            state['combat_ack_seq'], state['combat_seq'], sync['next_seq']))
        self.assertEqual([], sync['pending'])
        self.assertFalse(sync['authority_handoff_pending'])

        outgoing = runtime.update(.20, 100.4)[0]['bots'][0]
        self.assertEqual(800, outgoing['health'])
        self.assertEqual(3.2, outgoing['combat_fire_elapsed'])
        self.assertEqual(5, outgoing['combat_seq'])

    def test_authority_handoff_new_base_ack_ahead_drops_old_lineage(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, critical=burning,
            combat_revision=3, combat_base_revision=1,
            combat_ack_seq=3, combat_fire_elapsed=2.0,
            combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bot_authority_id=2))
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))
        runtime.update(.20, 100.2)

        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                health=700, critical=burning,
                revision=6, base_revision=6, ack_seq=5,
                fire_elapsed=3.0, fire_timer=0.0)]})

        state = runtime.states[11]
        sync = runtime._combat_sync[11]
        self.assertEqual((700, 3.0), (
            state['health'], state['combat_fire_elapsed']))
        self.assertEqual((6, 6, 5, 5), (
            state['combat_revision'], state['combat_base_revision'],
            state['combat_ack_seq'], state['combat_seq']))
        self.assertEqual([], sync['pending'])
        self.assertEqual([], sync['unpublished_steps'])
        self.assertFalse(sync['authority_handoff_pending'])

        outgoing = runtime.update(.20, 100.4)[0]['bots'][0]
        self.assertEqual((700, 3.2, 6), (
            outgoing['health'], outgoing['combat_fire_elapsed'],
            outgoing['combat_seq']))

    def test_authority_handoff_new_base_same_sequence_does_not_replay(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, critical=burning,
            combat_revision=3, combat_base_revision=1,
            combat_ack_seq=3, combat_fire_elapsed=2.0,
            combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bot_authority_id=2))
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))
        local = runtime.update(.20, 100.2)[0]['bots'][0]
        self.assertEqual((4, 2.2), (
            local['combat_seq'], local['combat_fire_elapsed']))

        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                health=750, critical=burning,
                revision=5, base_revision=5, ack_seq=4,
                fire_elapsed=3.0, fire_timer=0.0)]})

        state = runtime.states[11]
        sync = runtime._combat_sync[11]
        self.assertEqual((750, 3.0), (
            state['health'], state['combat_fire_elapsed']))
        self.assertEqual((5, 5, 4, 4), (
            state['combat_revision'], state['combat_base_revision'],
            state['combat_ack_seq'], state['combat_seq']))
        self.assertEqual([], sync['pending'])
        self.assertFalse(sync['authority_handoff_pending'])

        outgoing = runtime.update(.20, 100.4)[0]['bots'][0]
        self.assertEqual((750, 3.2, 5), (
            outgoing['health'], outgoing['combat_fire_elapsed'],
            outgoing['combat_seq']))

    def test_same_authority_same_sequence_signature_mismatch_raises(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        initial = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, critical=burning, combat_revision=4,
            combat_base_revision=1, combat_ack_seq=3,
            combat_fire_elapsed=2.0, combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bots=[initial]))
        runtime.update(.20, 100.2)

        with self.assertRaisesRegex(
                ValueError, 'server bot combat ack is inconsistent'):
            runtime.apply_snapshot({
                'server_tick': 10,
                'bots': [_snapshot_bot(
                    health=800, critical=burning,
                    revision=5, base_revision=1, ack_seq=4,
                    fire_elapsed=3.0, fire_timer=0.0)]})

    def test_bot_snapshot_without_explicit_combat_contract_raises(self):
        self.runtime.battle_start(self.start)
        with self.assertRaises(ValueError):
            self.runtime.apply_snapshot({'bots': [
                {'id': 11, 'health': 1000, 'alive': True,
                 'critical': {}}]})

    def test_bot_snapshot_non_object_critical_raises_without_clearing_state(self):
        self.runtime.battle_start(self.start)
        before = dict(self.runtime.states[11])
        malformed = _snapshot_bot(critical={})
        malformed['critical'] = []

        with self.assertRaises(ValueError):
            self.runtime.apply_snapshot({'bots': [malformed]})

        self.assertEqual(before['critical'], self.runtime.states[11]['critical'])

    def test_static_critical_state_does_not_accumulate_replay_steps(self):
        self.runtime.battle_start(self.start)
        static = _critical_payload(crew_ko=['commander'])
        self.runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                critical=static, revision=1, base_revision=1)]})

        for index in range(1000):
            self.runtime.update(.04, 1.0 + index * .04)

        sync = self.runtime._combat_sync[11]
        self.assertEqual([], sync['unpublished_steps'])
        self.assertEqual([], sync['pending'])

    def test_1513_native_motion_emits_input_without_integrating_pose(self):
        runtime = self.module.BotRuntime(
            1, adapter_factory=lambda *args: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            native_motion=True, baked_graph=_graph())
        runtime.battle_start(self.start)
        before = dict(runtime.states[11])

        outgoing = runtime.update(.04, 1.0)
        state = outgoing[0]['bots'][0]

        self.assertEqual(1, state['movement_dir'])
        self.assertEqual(0, state['rotation_dir'])
        self.assertEqual((before['x'], before['y'], before['z'],
                          before['yaw']),
                         (state['x'], state['y'], state['z'], state['yaw']))
        self.assertTrue(runtime.apply_native_pose(
            11, (7.0, 2.0, 9.0), 0.75, 4.5))
        self.assertEqual((7.0, 2.0, 9.0, 0.75, 4.5), (
            runtime.states[11]['x'], runtime.states[11]['y'],
            runtime.states[11]['z'], runtime.states[11]['yaw'],
            runtime.states[11]['speed']))

    @staticmethod
    def _stationary_command():
        return {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'hold',
            'aim_position': (0.0, 0.0, 10.0),
            'face_position': (0.0, 0.0, 10.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }

    def test_overlapping_bots_are_separated_without_spawn_deadlock(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull['hitTester'] = types.SimpleNamespace(
            bbox=((-1.5, -1.0, -3.5), (1.5, 1.0, 3.5)))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        start = dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Second'},
        ])
        runtime.battle_start(start)
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(x=0.8, y=0.0, z=0.0, yaw=0.0)

        outgoing = runtime.update(.04, 1.0)

        self.assertEqual('bot_state', outgoing[0]['type'])
        self.assertLess(runtime.states[11]['x'], 0.0)
        self.assertGreater(runtime.states[12]['x'], 0.8)
        self.assertEqual(0, runtime.states[11]['movement_dir'])
        self.assertEqual(0, runtime.states[12]['movement_dir'])

    def test_tank_separation_does_not_push_bots_through_world_geometry(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull['hitTester'] = types.SimpleNamespace(
            bbox=((-1.5, -1.0, -3.5), (1.5, 1.0, 3.5)))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        start = dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Second'},
        ])
        runtime.battle_start(start)
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(x=0.8, y=0.0, z=0.0, yaw=0.0)

        runtime.update(.04, 1.0)

        self.assertEqual((0.0, 0.8), (
            runtime.states[11]['x'], runtime.states[12]['x']))
        self.assertEqual((0.0, 0.0), (
            runtime.states[11]['push_x'], runtime.states[12]['push_x']))

    def test_ram_report_follows_pose_and_is_cooldown_gated(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull['hitTester'] = types.SimpleNamespace(
            bbox=((-1.5, -1.0, -3.5), (1.5, 1.0, 3.5)))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
                     speed=10.0)
        player = {
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            # End-to-end OBB contact. A deeply interpenetrating parallel pair
            # correctly chooses the shorter sideways escape axis instead.
            'x': 6.5, 'y': 0.0, 'z': 0.0, 'yaw': math.pi / 2.0,
            'speed': 0.0,
            'alive': True,
        }

        outgoing = runtime.update(.04, 10.0, players=[player])

        self.assertEqual(['bot_state', 'bot_ram', 'bot_observation'],
                         [message['type'] for message in outgoing])
        report = outgoing[1]
        self.assertEqual((11, 'human', 2), (
            report['bot_id'], report['target_kind'], report['target_id']))
        self.assertGreater(report['damage_to_bot'], 0)
        self.assertGreater(report['damage_to_target'], 0)

        state.update(x=0.0, z=0.0, yaw=math.pi / 2.0, speed=10.0,
                     push_x=0.0, push_z=0.0)
        repeated = runtime.update(.04, 10.2, players=[player])
        self.assertNotIn('bot_ram', [message['type'] for message in repeated])

    def test_enemy_bots_and_humans_have_distinct_target_ids(self):
        self.start['bots'].append(
            {'id': 2, 'team': 1, 'slot': 0, 'name': 'OtherBot'})
        self.runtime.battle_start(self.start)
        self.runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'x': 4, 'y': 0, 'z': 4}])
        contacts = self.adapters[0].calls[0][0]['contacts']
        by_kind = dict((item['kind'], item) for item in contacts)
        self.assertEqual(2, by_kind['bot']['id'])
        self.assertEqual(self.module.HUMAN_TARGET_ID_BASE + 2,
                         by_kind['human']['id'])

    def test_non_authority_does_not_emit_or_construct_manifest(self):
        self.start['bot_authority_id'] = 2
        self.assertEqual([], self.runtime.battle_start(self.start))
        self.assertEqual([], self.runtime.update(.1, 1.0))

    def test_server_snapshot_kills_local_bot_and_stops_future_fire(self):
        self.runtime.battle_start(self.start)
        self.runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5}])

        self.runtime.apply_snapshot({'bots': [
            _snapshot_bot(health=0, alive=False,
                          revision=1, base_revision=1)]})

        self.assertFalse(self.runtime.states[11]['alive'])
        self.assertEqual(0.0, self.runtime.states[11]['speed'])
        final = self.runtime.update(.1, 2.0, players=[
            {'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5}])
        self.assertFalse(final[0]['bots'][0]['alive'])
        self.assertEqual(0, self.runtime.states[11]['fire_seq'])

    def test_terminal_snapshot_freezes_all_bot_updates(self):
        self.runtime.battle_start(self.start)
        self.runtime.apply_snapshot({
            'battle_result': {'winner': 2, 'reason': 'team_eliminated'},
            'bots': [_snapshot_bot()]})

        self.assertTrue(self.runtime.finished)
        self.assertEqual([], self.runtime.update(.1, 2.0))

    def test_visibility_probes_are_cached_and_staggered(self):
        calls = []
        self.runtime.visibility_probe = lambda source, target: (
            calls.append((source['id'], target['network_id'])) or True)
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        players = [{'id': 2, 'team': 1, 'x': state['x'] + 100,
                    'y': state['y'], 'z': state['z']}]

        self.runtime.update(.04, 1.0, players=players)
        self.runtime.update(.04, 1.04, players=players)

        self.assertEqual([(11, 2)], calls)

    def test_driver_decisions_are_cached_and_staggered_but_physics_ticks(self):
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        start_z = state['z']

        self.runtime.update(.04, 1.00)
        self.runtime.update(.04, 1.04)
        self.runtime.update(.04, 1.08)

        self.assertEqual(1, len(self.adapters[0].calls))
        self.assertNotEqual(start_z, state['z'])

        self.runtime.update(.04, 1.20)
        self.assertEqual(2, len(self.adapters[0].calls))
        self.assertGreater(
            self.adapters[0].calls[-1][0]['dt'],
            self.adapters[0].calls[0][0]['dt'])

    def test_new_server_order_revision_invalidates_decision_cache(self):
        self.runtime.battle_start(self.start)
        self.runtime.update(.04, 1.00)
        self.assertEqual(1, len(self.adapters[0].calls))

        self.runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'move_position': {'x': 8, 'y': 0, 'z': 8},
                'fire_allowed': False, 'shell_index': 0,
                'fire_range': 0}],
            'bots': []})
        self.runtime.update(.04, 1.04)

        self.assertEqual(2, len(self.adapters[0].calls))

    def test_collision_broad_phase_skips_distant_all_pairs(self):
        self.runtime.battle_start(self.start)
        template = dict(self.runtime.states[11])
        for index in range(1, 29):
            state = dict(template)
            state.update(
                id=100 + index, slot=index, x=float(index * 100),
                z=float(index * 100))
            self.runtime.states[state['id']] = state

        candidate_counts = []
        original = self.module.tank_collision.resolve_tank

        def resolve(unused_own, others, now=None, ram_cooldowns=None):
            candidate_counts.append(len(list(others)))
            return {
                'correction': (0.0, 0.0),
                'delta_velocity': (0.0, 0.0),
                'ram_events': (),
                'cooldowns': dict(ram_cooldowns or {}),
            }

        self.module.tank_collision.resolve_tank = resolve
        try:
            self.runtime._resolve_tank_contacts([], 1.0, .04)
        finally:
            self.module.tank_collision.resolve_tank = original

        self.assertEqual(29, len(candidate_counts))
        self.assertEqual({0}, set(candidate_counts))

    def test_authority_failover_resumes_server_fire_sequence(self):
        waiting = dict(self.start, bot_authority_id=2)
        self.assertEqual([], self.runtime.battle_start(waiting))
        snapshot_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=1, y=0, z=2, yaw=0.5,
            fire_seq=7, shell_index=1, critical={},
            combat_revision=0, combat_base_revision=0,
            combat_ack_seq=0, combat_fire_elapsed=0.0,
            combat_fire_timer=0.0)
        takeover = dict(
            self.start, bot_authority_id=1,
            bot_manifest=[snapshot_bot])

        outgoing = self.runtime.battle_start(takeover)

        self.assertEqual(7, self.runtime.states[11]['fire_seq'])
        self.assertEqual(1, self.runtime.states[11]['shell_index'])
        self.assertEqual('bot_manifest', outgoing[0]['type'])
        self.runtime.apply_snapshot({'bots': [dict(
            snapshot_bot, fire_seq=8, shell_index=2)]})
        self.assertEqual(8, self.runtime.states[11]['fire_seq'])
        self.assertEqual(2, self.runtime.states[11]['shell_index'])

    def test_new_round_discards_previous_bot_and_terminal_state(self):
        self.runtime.battle_start(self.start)
        self.runtime.apply_snapshot({
            'battle_result': {'winner': 1},
            'bots': [_snapshot_bot(health=0, alive=False,
                                   revision=1, base_revision=1)]})
        next_round = dict(
            self.start, round_id=6, battle_result=None,
            bots=[{'id': 12, 'team': 1, 'slot': 0, 'name': 'Next'}])

        outgoing = self.runtime.battle_start(next_round)

        self.assertFalse(self.runtime.finished)
        self.assertEqual({12}, set(self.runtime.states))
        self.assertEqual('bot_manifest', outgoing[0]['type'])

    def test_authority_handback_resends_manifest_in_same_round(self):
        first = self.runtime.battle_start(self.start)
        self.assertEqual('bot_manifest', first[0]['type'])
        self.assertEqual([], self.runtime.battle_start(dict(
            self.start, bot_authority_id=2)))

        resumed = self.runtime.battle_start(self.start)

        self.assertEqual('bot_manifest', resumed[0]['type'])

    def test_server_macro_order_drives_local_adapter_with_human_id_mapping(self):
        self.runtime.battle_start(self.start)
        self.runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'target_kind': 'human', 'target_id': 2,
                'move_position': {'x': 8, 'y': 0, 'z': 8},
                'fire_allowed': False, 'shell_index': 1,
                'fire_range': 400}],
            'bots': []})

        self.runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5}])

        order = self.adapters[0].server_orders[-1]
        self.assertEqual(self.module.HUMAN_TARGET_ID_BASE + 2,
                         order['target_id'])
        self.assertEqual('human', self.runtime.states[11]['target_kind'])
        self.assertEqual(2, self.runtime.states[11]['target_id'])

    def test_json_route_anchor_is_normalized_before_terrain_navigation(self):
        runtime = self.module.BotRuntime(
            1,
            ground_probe=lambda unused_x, unused_z, unused_hint: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            obstacle_probe=lambda *unused: False,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph(),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0})
        runtime.battle_start(self.start)
        runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'team': 2,
                'route_id': 'server_route', 'route_index': 1,
                # Keep y first so the unfixed tuple(dict) path reproduces the
                # exact legacy-client error: could not convert string to float: y.
                'route_anchor': {'y': 0.0, 'x': 0.0, 'z': 0.0},
                'move_position': {'x': 8.0, 'y': 0.0, 'z': 0.0},
                'aim_position': {'x': 6.0, 'y': 1.0, 'z': 0.0},
                'face_position': {'x': 7.0, 'y': 0.0, 'z': 0.0},
                'target_id': None, 'target_kind': None,
                'combat_mode': 'route', 'fire_allowed': False,
                'shell_index': 0, 'fire_range': 400.0,
            }],
            'bots': [],
        })

        outgoing = runtime.update(0.04, 1.0)

        self.assertEqual('bot_state', outgoing[0]['type'])
        path = list(runtime.navigator.paths.values())[0]
        self.assertEqual((0.0, 0.0, 0.0),
                         path[0])
        order = runtime._server_orders[11]
        self.assertEqual((6.0, 1.0, 0.0), order['aim_position'])
        self.assertEqual((7.0, 0.0, 0.0), order['face_position'])
        self.assertEqual((8.0, 0.0, 0.0), order['move_position'])
        self.assertEqual((0.0, 0.0, 0.0), order['route_anchor'])

    def test_authority_publishes_deduplicated_visibility_observations(self):
        self.runtime.battle_start(self.start)

        outgoing = self.runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5,
             'health': 100, 'max_health': 100}])

        observation = [value for value in outgoing
                       if value['type'] == 'bot_observation'][0]
        self.assertEqual(1, len(observation['contacts']))
        self.assertEqual('human', observation['contacts'][0]['target_kind'])
        self.assertEqual(2, observation['contacts'][0]['target_id'])

    def test_malformed_new_server_order_batch_does_not_replace_last_good(self):
        self.runtime.battle_start(self.start)
        self.assertTrue(self.runtime._apply_orders({
            'bot_order_revision': 1,
            'bot_orders': [{'id': 11, 'move_position': {'x': 1}}]}))

        self.assertFalse(self.runtime._apply_orders({
            'bot_order_revision': 2, 'bot_orders': {'id': 11}}))
        self.assertEqual(1, self.runtime._order_revision)
        self.assertEqual({11}, set(self.runtime._server_orders))

    def test_manifest_rejects_route_above_protocol_limit(self):
        self.runtime.battle_start(self.start)
        state = dict(self.runtime.states[11])
        state['route'] = {
            'id': 'too-long',
            'waypoints': tuple((float(value), 0.0, False)
                               for value in range(17)),
        }

        with self.assertRaisesRegex(ValueError, '16-waypoint'):
            self.runtime._manifest_entry(state)

    def test_probe_rejects_water_collision_and_steep_slope(self):
        for probe in ({'clear': True, 'water': True}, {'clear': True, 'collision': True}, {'clear': True, 'slope': .7}):
            runtime = self.module.BotRuntime(1, direction_probe=lambda *unused, value=probe: value)
            self.assertFalse(runtime._clear((0, 0, 0), 0.0))

    def test_bot_leaving_support_enters_ballistic_fall(self):
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda x, unused_z, unused_y: (
                0.0 if x < 1.0 else -20.0))
        state = {
            'id': 11, 'x': 2.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 8.0, 'half_length': 1.5,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': True, 'last_drive_pitch': 0.0,
        }

        runtime._update_vertical_motion(state, 0.1)

        self.assertTrue(state['airborne'])
        self.assertLess(state['vertical_speed'], 0.0)
        self.assertLess(state['y'], 0.0)

    def test_realised_hazard_guard_never_rewinds_an_already_fallen_bot(self):
        graph = _graph()
        graph['hazards'] = (0, 2, 0)
        runtime = self.module.BotRuntime(
            1, baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: _Adapter(),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=4.0, y=-6.0, z=0.0, speed=3.0,
                     vertical_speed=-8.0, airborne=True)

        guarded = runtime._guard_realised_pose(
            state, (0.0, 0.0, 0.0), False, 0.0)

        self.assertFalse(guarded)
        self.assertEqual((4.0, -6.0, 0.0),
                         (state['x'], state['y'], state['z']))
        self.assertEqual(-8.0, state['vertical_speed'])

    def test_realised_hazard_guard_cancels_only_current_safe_tick(self):
        graph = _graph()
        graph['hazards'] = (0, 2, 0)
        runtime = self.module.BotRuntime(
            1, baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: _Adapter(),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=4.0, y=-1.0, z=0.0, speed=3.0,
                     vertical_speed=-2.0, airborne=True)

        guarded = runtime._guard_realised_pose(
            state, (0.0, 0.0, 0.0), True, 0.0)

        self.assertTrue(guarded)
        self.assertEqual((0.0, 0.0, 0.0),
                         (state['x'], state['y'], state['z']))
        self.assertEqual(0.0, state['vertical_speed'])

    def test_driver_receives_native_hull_dimensions_and_velocity(self):
        hit_tester = types.SimpleNamespace(
            bbox=((-2.1, -1.0, -4.2), (2.3, 1.0, 3.8)))
        descriptor = {
            'hull': {'hitTester': hit_tester},
            'physics': {'speedLimits': (14.0, 7.0)},
        }
        self.runtime.descriptor_resolver = lambda unused: descriptor
        self.start['bots'].append(
            {'id': 12, 'team': 2, 'slot': 1, 'name': 'Wingman'})
        self.runtime.battle_start(self.start)
        self.runtime.states[11]['speed'] = 6.0

        self.runtime.update(.04, 1.0)

        decision = self.adapters[0].calls[0][0]
        self.assertEqual(4.2, decision['half_length'])
        self.assertEqual(2.3, decision['half_width'])
        expected_velocity = (
            math.sin(self.runtime.states[11]['yaw']) * 6.0, 0.0,
            math.cos(self.runtime.states[11]['yaw']) * 6.0)
        self.assertEqual(expected_velocity, decision['velocity'])
        neighbour = decision['neighbours'][0]
        self.assertEqual(4.2, neighbour['half_length'])
        self.assertEqual(2.3, neighbour['half_width'])
