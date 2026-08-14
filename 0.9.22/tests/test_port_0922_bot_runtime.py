import importlib.util
import json
import math
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import BattleState, CLIENT_BUILD_0922, Player
from server_bot_ai import BotPlanner

PACKAGE_ROOT = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922'


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


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class _HitTester1513(object):
    def __init__(self, minimum, maximum):
        # Exact #1513 bbox exposes min, max and a third derived value.
        self.bbox = (minimum, maximum, None)


def _combat_descriptor(reload_time=0.5, clip=(2, 0.2),
                       turret_yaw_limits=(-math.pi, math.pi),
                       turret_speed=10.0, gun_speed=10.0,
                       dispersion=0.03, max_ammo=None):
    gun = types.SimpleNamespace(
        shots=({'shell': {'effectsIndex': 0}, 'speed': 1000.0,
                'gravity': 10.0, 'maxDistance': 5000.0},),
        reloadTime=reload_time,
        clip=clip, turretYawLimits=turret_yaw_limits,
        pitchLimits={'absolute': (-0.35, 0.15)}, rotationSpeed=gun_speed,
        shotDispersionAngle=dispersion,
        maxHealth=54, maxRegenHealth=27)
    if max_ammo is not None:
        gun.maxAmmo = int(max_ammo)
    chassis = _Strict1513Component(
        hitTester=_HitTester1513(
            (-1.5, -0.8, -3.5), (1.5, 0.8, 3.5)),
        hullPosition=(0.0, 0.6, 0.0), rotationSpeed=0.75,
        shotDispersionFactors=(0.14, 0.14),
        maxHealth=170, maxRegenHealth=130)
    hull = _Strict1513Component(
        hitTester=_HitTester1513(
            (-1.7, -0.2, -3.5), (1.7, 1.4, 3.5)),
        turretPositions=((0.0, 1.0, 0.0),))
    return types.SimpleNamespace(
        gun=gun, turret={'rotationSpeed': turret_speed,
                         'circularVisionRadius': 445.0},
        physics={'speedLimits': (14.0, 7.0)}, chassis=chassis,
        hull=hull, maxHealth=1000)


def _critical_descriptor():
    descriptor = _combat_descriptor()
    descriptor.chassis.maxHealth = 170
    descriptor.chassis.maxRegenHealth = 130
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


class _CaptureSocket(object):
    def __init__(self):
        self.payloads = []

    def sendall(self, payload):
        self.payloads.append(payload)


class ServerReportedHealthTests(unittest.TestCase):
    @staticmethod
    def _server_with_bot():
        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        connection = _CaptureSocket()
        player = Player(
            1, connection, ('127.0.0.1', 1), team=1, slot=0,
            health=1000, max_health=1000)
        server.players[player.player_id] = player
        server.players[2] = Player(
            2, _CaptureSocket(), ('127.0.0.1', 2), team=2, slot=0)
        server.bot_states[28] = {
            'id': 28, 'team': 2, 'alive': True, 'frags': 0}
        return server, player, connection

    @staticmethod
    def _broadcast_health_event(connection):
        messages = [json.loads(payload.decode('utf-8'))
                    for payload in connection.payloads]
        events_message = next(message for message in messages
                              if message.get('type') == 'events')
        return next(event for event in events_message['events']
                    if event.get('kind') == 'health')

    def test_nonfatal_client_simulation_drops_stale_attacker_from_wire(self):
        server, player, connection = self._server_with_bot()

        self.assertTrue(server._apply_reported_health(player, {
            'reported_health': 1000,
            'reported_critical': _critical_payload({
                'name': 'engineHealth', 'hp': 25.0, 'max_hp': 100.0,
                'state': 'critical'}),
            'reported_critical_base_revision': 0,
            'reported_critical_seq': 1,
            'reported_attacker': 2,
        }))
        event = server.pending_events[-1]
        self.assertEqual(0, event['damage'])
        self.assertNotIn('attacker', event)
        self.assertNotIn('attacker_bot', event)
        self.assertTrue(server._validate_combat_event_for_wire(event))
        self.assertEqual(('', 0),
                         (player.death_attacker_kind,
                          player.death_attacker_id))
        self.assertEqual(0, server.players[2].frags)
        self.assertEqual(0, server.bot_states[28]['frags'])

        server.battle_result = {
            'winner': 0, 'reason': 'test fence', 'base_team': 0}
        server.tick_once(1.0 / 30.0)

        wire_event = self._broadcast_health_event(connection)
        self.assertNotIn('attacker', wire_event)
        self.assertNotIn('attacker_bot', wire_event)

    def test_fatal_client_simulation_keeps_death_ledger_only_once(self):
        server, player, connection = self._server_with_bot()
        player.health = 100

        self.assertTrue(server._apply_reported_health(player, {
            'reported_health': 0,
            'reported_reason': 1,
            'reported_attacker_bot': 28,
        }))
        self.assertEqual(('bot', 28),
                         (player.death_attacker_kind,
                          player.death_attacker_id))
        self.assertEqual(1, server.bot_states[28]['frags'])
        health_event = next(event for event in server.pending_events
                            if event.get('kind') == 'health')
        self.assertNotIn('attacker', health_event)
        self.assertNotIn('attacker_bot', health_event)
        self.assertTrue(server._validate_combat_event_for_wire(health_event))

        self.assertFalse(server._apply_reported_health(player, {
            'reported_health': 0,
            'reported_reason': 1,
            'reported_attacker_bot': 28,
        }))
        self.assertEqual(1, server.bot_states[28]['frags'])

        server.battle_result = {
            'winner': 0, 'reason': 'test fence', 'base_team': 0}
        server.tick_once(1.0 / 30.0)

        wire_event = self._broadcast_health_event(connection)
        self.assertNotIn('attacker', wire_event)
        self.assertNotIn('attacker_bot', wire_event)


class ServerBotObservationRelayTests(unittest.TestCase):
    @staticmethod
    def _server():
        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        authority_socket = _CaptureSocket()
        guest_socket = _CaptureSocket()
        server.players[1] = Player(
            1, authority_socket, ('127.0.0.1', 1), team=1, slot=0)
        server.players[2] = Player(
            2, guest_socket, ('127.0.0.1', 2), team=1, slot=1)
        server.bot_authority_id = 1
        server.bot_manifest_authority_id = 1
        return server, authority_socket, guest_socket

    @staticmethod
    def _message(round_id, visible=True, target_id=2):
        return {
            'type': 'bot_observation', 'round_id': round_id,
            'contacts': [{
                'observing_team': 2, 'target_kind': 'human',
                'target_id': target_id, 'target_team': 1,
                'visible': bool(visible),
                'shootable_by_bot_ids': [],
                'x': 10.0, 'y': 0.0, 'z': 20.0,
                'health': 1000, 'max_health': 1000,
            }],
            'affordances': [],
        }

    def test_validated_visibility_is_relayed_once_to_every_participant(self):
        server, authority_socket, guest_socket = self._server()

        relay = server.update_bot_observation(
            1, self._message(server.round_id))

        self.assertIsInstance(relay, dict)
        self.assertEqual({
            'observing_team': 2, 'target_kind': 'human',
            'target_id': 2, 'target_team': 1, 'visible': True,
        }, relay['contacts'][0])
        self.assertTrue(server.broadcast_bot_observation(relay))
        for connection in (authority_socket, guest_socket):
            payloads = [json.loads(value.decode('utf-8'))
                        for value in connection.payloads]
            self.assertEqual([relay], payloads)

    def test_hidden_and_stale_observations_never_relay(self):
        server, authority_socket, guest_socket = self._server()

        self.assertFalse(server.update_bot_observation(
            1, self._message(server.round_id, visible=False)))
        self.assertFalse(server.update_bot_observation(
            1, self._message(server.round_id - 1, visible=True)))

        self.assertEqual([], authority_socket.payloads)
        self.assertEqual([], guest_socket.payloads)


class BotRuntimeTests(unittest.TestCase):
    def setUp(self):
        self._modules = dict((key, value) for key, value in sys.modules.items()
                             if key == 'gui' or key.startswith('gui.'))
        self.module = _load(); self.adapters = []
        def factory(*args):
            adapter = _Adapter(*args); self.adapters.append(adapter); return adapter
        self.runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=factory,
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

    def test_probe_totals_count_only_real_query_seams_and_are_pull_only(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            direction_probe=lambda *unused: {'clear': True},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            physics_ground_probe=lambda *unused: 0.0)
        source = {'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                  'view_range': 500.0}
        target = {'id': 12, 'network_id': 12, 'kind': 'bot', 'team': 2,
                  'x': 100.0, 'y': 0.0, 'z': 0.0,
                  'position': (100.0, 0.0, 0.0), 'fire_seq': 0,
                  'speed': 0.0}
        before = runtime.probe_totals()

        runtime._probe_direction((0.0, 0.0, 0.0), 0.0)
        self.assertTrue(runtime._visible(source, target, 1.0))
        self.assertTrue(runtime._visible(source, target, 1.01))
        self.assertTrue(runtime._shot_clear(source, target, 1.0))
        self.assertTrue(runtime._shot_clear(source, target, 1.01))
        runtime._terrain_support({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'half_length': 3.0})

        after = runtime.probe_totals()
        self.assertEqual(after, runtime.probe_totals())
        self.assertEqual(
            {'visibility': 1, 'lane': 1, 'cover': 0,
             'ground': 1, 'motion': 1},
            dict(zip(self.module.PROBE_KINDS,
                     (after[index] - before[index]
                      for index in range(len(after))))))

    def test_direction_probe_receives_speed_and_descriptor_contract(self):
        calls = []
        descriptor = _combat_descriptor()

        def direction(position, yaw, speed, type_descriptor):
            calls.append((position, yaw, speed, type_descriptor))
            return {'clear': True, 'collision': False, 'slope': 0.0}

        runtime = self.module.BotRuntime(1, direction_probe=direction)

        result = runtime._probe_direction(
            (1.0, 2.0, 3.0), 0.25, 7.5, descriptor)

        self.assertTrue(result['clear'])
        self.assertEqual(1, len(calls))
        self.assertEqual(((1.0, 2.0, 3.0), 0.25, 7.5), calls[0][:3])
        self.assertIs(descriptor, calls[0][3])

    def test_direction_probe_body_type_error_is_not_retried_as_old_arity(self):
        calls = []

        def direction(position, yaw, speed, descriptor):
            calls.append((position, yaw, speed, descriptor))
            raise TypeError('probe body failed after native work')

        runtime = self.module.BotRuntime(1, direction_probe=direction)

        result = runtime._probe_direction(
            (1.0, 2.0, 3.0), 0.25, 7.5, _combat_descriptor())

        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])
        self.assertEqual(1, len(calls))

    def test_final_world_receipt_runs_once_after_seven_planning_candidates(self):
        descriptor = _combat_descriptor()
        planning_yaws = tuple(index * 0.17 for index in range(7))
        direction_calls = []
        receipt_calls = []
        command = {
            'target_yaw': 0.00004, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        adapter = _FixedAdapter(command)

        def direction(position, yaw, speed, type_descriptor):
            direction_calls.append((position, yaw, speed, type_descriptor))
            return {'clear': True, 'collision': False, 'slope': 0.0}

        def receipt(position, yaw, speed, type_descriptor):
            receipt_calls.append((position, yaw, speed, type_descriptor))
            return {
                'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **unused_kwargs: adapter,
            direction_probe=direction, world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(yaw=0.00004, speed=4.0, grounded_once=True)

        def decide(unused_state, clear):
            for candidate in planning_yaws:
                self.assertTrue(clear(candidate))
            return dict(command)

        adapter.decide = decide
        runtime.update(.04, 1.0)

        # yaw=0.00004 shares the planner's round(yaw, 4) key for yaw=0,
        # but the exact proof belongs to the final unrounded travel heading.
        self.assertEqual(7, len(direction_calls))
        self.assertEqual(1, len(receipt_calls))
        self.assertAlmostEqual(0.00004, receipt_calls[0][1])
        self.assertIs(descriptor, receipt_calls[0][3])
        cached = runtime._motion_probe_cache[11]['result']
        self.assertAlmostEqual(
            0.00004, cached['world_receipt']['yaw'])

    def test_reverse_final_world_receipt_receives_exact_travel_heading(self):
        command = {
            'target_yaw': 0.0, 'throttle': -1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'reverse_turn', 'movement_intent': True,
        }
        receipt_calls = []

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_calls.append((tuple(position), yaw, speed))
            return {
                'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.states[11].update(
            yaw=0.0, speed=-4.0, grounded_once=True)

        runtime.update(.04, 1.0)

        self.assertEqual(1, len(receipt_calls))
        self.assertAlmostEqual(math.pi, receipt_calls[0][1])
        self.assertLess(receipt_calls[0][2], 0.0)
        cached = runtime._motion_probe_cache[11]['result']['world_receipt']
        self.assertAlmostEqual(math.pi, cached['yaw'])
        self.assertEqual(-1, cached['direction'])

    def test_deferred_final_world_receipt_is_not_cached_and_retries(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        receipt_calls = []

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_calls.append((tuple(position), yaw, speed))
            if len(receipt_calls) == 1:
                return 'deferred'
            return {
                'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)

        runtime.update(.04, 1.0)
        self.assertNotIn(11, runtime._motion_probe_cache)

        runtime.update(.04, 1.04)
        self.assertEqual(2, len(receipt_calls))
        self.assertIn(11, runtime._motion_probe_cache)
        self.assertIn(
            'world_receipt', runtime._motion_probe_cache[11]['result'])

    def test_hard_final_world_receipt_blocks_the_selected_motion(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=lambda *unused: False,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)

        runtime.update(.04, 1.0)

        state = runtime.states[11]
        self.assertEqual(0, state['movement_dir'])
        cached = runtime._motion_probe_cache[11]['result']
        self.assertFalse(runtime._probe_is_clear(cached))
        self.assertNotIn('world_receipt', cached)

    def test_full_roster_world_receipts_are_bounded_and_drain_in_three_frames(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Receipt-%d' % index}
            for index in range(29)
        ]
        frame = [0]
        receipt_counts = [0, 0, 0]

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_counts[frame[0]] += 1
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))

        expected_cached = (13, 26, 29)
        for frame_index, now in enumerate((1.0, 1.04, 1.08)):
            frame[0] = frame_index
            runtime.update(.04, now)
            self.assertLessEqual(
                receipt_counts[frame_index],
                self.module.MAX_WORLD_RECEIPTS_PER_FRAME)
            self.assertEqual(
                expected_cached[frame_index],
                len(runtime._motion_probe_cache))
            uncached = set(runtime.states).difference(
                runtime._motion_probe_cache)
            self.assertTrue(all(
                runtime.states[bot_id]['movement_dir'] == 0
                for bot_id in uncached))
            self.assertTrue(all(
                'world_receipt' in cached['result']
                for cached in runtime._motion_probe_cache.values()))

        self.assertEqual([13, 13, 3], receipt_counts)

    def test_ineligible_receiptless_bots_do_not_block_receipt_refreshes(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }

        class MixedAdapter(_FixedAdapter):
            def decide(self, state, clear):
                result = _FixedAdapter.decide(self, state, clear)
                if state['id'] == 11:
                    result['throttle'] = 0.0
                return result

        descriptors = {}

        def descriptor(vehicle_name):
            if vehicle_name not in descriptors:
                value = _combat_descriptor()
                value.test_role = vehicle_name
                descriptors[vehicle_name] = value
            return descriptors[vehicle_name]

        def direction(position, yaw, speed, type_descriptor):
            if type_descriptor.test_role == 'receipt-hard':
                return {'clear': False, 'collision': True, 'slope': 0.0}
            return {'clear': True, 'collision': False, 'slope': 0.0}

        frame = [0]
        receipt_counts = [0, 0, 0]

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_counts[frame[0]] += 1
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Idle',
             'vehicle': 'receipt-idle'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Hard',
             'vehicle': 'receipt-hard'},
        ] + [
            {'id': 13 + index, 'team': 1, 'slot': 2 + index,
             'name': 'Moving-%d' % index, 'vehicle': 'receipt-moving'}
            for index in range(13)
        ]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=descriptor,
            adapter_factory=lambda *unused, **kwargs: MixedAdapter(command),
            direction_probe=direction, world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))

        expected_receipts = (13, 0, 0)
        for frame_index, now in enumerate((1.0, 1.2, 1.4)):
            frame[0] = frame_index
            runtime.update(.04, now)
            self.assertEqual(
                expected_receipts[frame_index],
                receipt_counts[frame_index])
            self.assertLessEqual(
                receipt_counts[frame_index],
                self.module.MAX_WORLD_RECEIPTS_PER_FRAME)
            self.assertTrue(all(
                runtime.states[bot_id]['movement_dir'] == 1
                for bot_id in range(13, 26)))

        for bot_id in (11, 12):
            result = runtime._motion_probe_cache[bot_id]['result']
            self.assertNotIn('world_receipt', result)

    def test_persistent_deferred_receipts_rotate_across_full_roster(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }

        class FailureDriver(object):
            def __init__(self):
                self.calls = []

            def remember_failure(self, *args):
                self.calls.append(args)

        failure_driver = FailureDriver()
        adapter = _FixedAdapter(command)
        adapter.driver = failure_driver
        descriptors = {}

        def descriptor(vehicle_name):
            if vehicle_name not in descriptors:
                value = _combat_descriptor()
                value.test_bot_id = int(vehicle_name.rsplit('-', 1)[1])
                descriptors[vehicle_name] = value
            return descriptors[vehicle_name]

        receipt_attempts = []

        def receipt(position, yaw, speed, type_descriptor):
            receipt_attempts.append(type_descriptor.test_bot_id)
            return 'deferred'

        resolver_calls = []
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Deferred-%d' % index,
             'vehicle': 'deferred-%d' % (11 + index)}
            for index in range(29)
        ]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=descriptor,
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            motion_resolver=lambda *args: resolver_calls.append(args))
        runtime.battle_start(dict(self.start, bots=roster))
        poses = {}
        for bot_id, state in runtime.states.items():
            state.update(speed=4.0, grounded_once=True)
            poses[bot_id] = (state['x'], state['y'], state['z'])

        cohorts = []
        for now in (1.0, 1.04, 1.08):
            before = len(receipt_attempts)
            runtime.update(.04, now)
            cohort = set(receipt_attempts[before:])
            cohorts.append(cohort)
            self.assertTrue(cohort)
            self.assertLessEqual(
                len(cohort), self.module.MAX_WORLD_RECEIPTS_PER_FRAME)
            self.assertEqual({}, runtime._motion_probe_cache)
            self.assertEqual([], resolver_calls)
            self.assertEqual([], failure_driver.calls)
            for bot_id, state in runtime.states.items():
                self.assertEqual(poses[bot_id],
                                 (state['x'], state['y'], state['z']))
                self.assertAlmostEqual(4.0, state['speed'])
                self.assertEqual(0, state['movement_dir'])

        self.assertNotEqual(cohorts[0], cohorts[1])
        self.assertEqual(set(range(11, 40)), set().union(*cohorts))

        service_counts = dict((bot_id, 0) for bot_id in range(11, 40))
        for bot_id in receipt_attempts:
            service_counts[bot_id] += 1
        for frame_index in range(3, 60):
            before = len(receipt_attempts)
            runtime.update(.04, 1.0 + frame_index * .04)
            frame_attempts = receipt_attempts[before:]
            self.assertLessEqual(
                len(frame_attempts),
                self.module.MAX_WORLD_RECEIPTS_PER_FRAME)
            for bot_id in frame_attempts:
                service_counts[bot_id] += 1
        self.assertLessEqual(
            max(service_counts.values()) - min(service_counts.values()), 1)

    def test_persistent_deferred_bot_does_not_starve_receipt_refresh(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        descriptors = {}

        def descriptor(vehicle_name):
            if vehicle_name not in descriptors:
                value = _combat_descriptor()
                value.test_role = vehicle_name
                descriptors[vehicle_name] = value
            return descriptors[vehicle_name]

        attempts = []

        def receipt(position, yaw, speed, type_descriptor):
            attempts.append(type_descriptor.test_role)
            if type_descriptor.test_role == 'receipt-stuck':
                return 'deferred'
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Stuck',
             'vehicle': 'receipt-stuck'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Moving',
             'vehicle': 'receipt-moving'},
        ]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))

        runtime.update(.04, 1.0)
        initial_moving_attempts = attempts.count('receipt-moving')
        self.assertEqual(1, initial_moving_attempts)
        self.assertEqual(1, runtime.states[12]['movement_dir'])
        for frame_index in range(1, 8):
            runtime.update(.04, 1.0 + frame_index * .04)
        self.assertGreater(attempts.count('receipt-stuck'), 1)
        self.assertEqual(
            initial_moving_attempts,
            attempts.count('receipt-moving'))
        self.assertEqual(1, runtime.states[12]['movement_dir'])

    def test_initial_motion_deadlines_fill_one_cycle_without_exceeding_it(self):
        now = 10.0
        interval = self.module.MOTION_PROBE_SECONDS
        offsets = sorted(
            self.module._motion_probe_deadline(now, bot_id, True) - now
            for bot_id in range(11, 40))

        self.assertEqual(29, len(set(round(value, 12)
                                     for value in offsets)))
        for index, offset in enumerate(offsets, 1):
            self.assertAlmostEqual(
                interval * index / 29.0, offset, places=12)
            self.assertGreater(offset, 0.0)
            self.assertLessEqual(offset, interval + 1e-12)

    def test_deferred_motion_probe_is_not_cached_and_retries_next_frame(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        results = [
            {'clear': True, 'collision': False, 'slope': 0.0,
             'deferred': True,
             'world_receipt': {
                 'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                 'origin': (0.0, 0.0, 0.0), 'yaw': 0.0,
                 'direction': 1}},
            {'clear': True, 'collision': False, 'slope': 0.0},
        ]
        calls = []

        def direction(*unused):
            calls.append(len(calls) + 1)
            return dict(results[min(len(calls) - 1, len(results) - 1)])

        adapter = _FixedAdapter(command)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=direction,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)

        runtime.update(.04, 1.0)
        self.assertEqual([1], calls)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertIn(11, runtime._decision_cache)

        runtime.update(.04, 1.04)
        self.assertEqual([1, 2], calls)
        self.assertIn(11, runtime._motion_probe_cache)
        self.assertIn(11, runtime._decision_cache)
        self.assertEqual(1, len(adapter.calls))
        self.assertFalse(runtime._motion_probe_cache[11]['result'].get(
            'deferred', False))

    def test_fixed_bot_order_cannot_starve_deferred_probe_cohort(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        frame_budget = [24]
        frame_recasts = [0]

        def direction(*unused):
            # Model one full-width soft obstacle: six native recasts cover the
            # dual-height three-lane corridor. Four Bots fit the frame cap;
            # the fifth must defer and become the first uncached retry.
            if frame_budget[0] < 6:
                return {'clear': True, 'collision': False, 'slope': 0.0,
                        'deferred': True}
            frame_budget[0] -= 6
            frame_recasts[0] += 6
            return {'clear': True, 'collision': False, 'slope': 0.0}

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=direction,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        roster = [
            {'id': 11 + index, 'team': 2, 'slot': index,
             'name': 'Soft-%d' % index}
            for index in range(5)]
        runtime.battle_start(dict(self.start, bots=roster))

        runtime.update(.04, 1.0)
        self.assertLessEqual(frame_recasts[0], 24)
        self.assertEqual(4, len(runtime._motion_probe_cache))
        self.assertNotIn(15, runtime._motion_probe_cache)

        frame_budget[0] = 24
        frame_recasts[0] = 0
        runtime.update(.04, 1.04)
        self.assertLessEqual(frame_recasts[0], 24)
        self.assertEqual({11, 12, 13, 14, 15},
                         set(runtime._motion_probe_cache))

    def test_bot_soft_motion_contact_preserves_speed_without_moving(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        calls = []

        def resolver(*args):
            calls.append(args)
            return 'soft'

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            motion_resolver=resolver,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)
        before_position = (state['x'], state['y'], state['z'])

        runtime.update(.04, 1.0)

        self.assertEqual(1, len(calls))
        self.assertEqual(11, calls[0][0])
        self.assertEqual(before_position,
                         (state['x'], state['y'], state['z']))
        self.assertGreater(state['speed'], 4.0)
        soft_speed = state['speed']

        runtime.update(.04, 1.04)

        self.assertEqual(before_position,
                         (state['x'], state['y'], state['z']))
        self.assertAlmostEqual(soft_speed, state['speed'])
        self.assertEqual(2, len(calls))

        hard_calls = []

        def hard_resolver(*args):
            hard_calls.append(args)
            return 'hard'

        hard_runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            motion_resolver=hard_resolver,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        hard_runtime.battle_start(self.start)
        hard_state = hard_runtime.states[11]
        hard_state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                          grounded_once=True)

        hard_runtime.update(.04, 1.0)

        self.assertEqual(1, len(hard_calls))
        self.assertEqual(before_position,
                         (hard_state['x'], hard_state['y'], hard_state['z']))
        self.assertAlmostEqual(soft_speed * 0.2, hard_state['speed'])

    def test_realised_hard_contact_invalidates_cached_command_and_probe(self):
        attempted_yaw = 0.25
        aim = (math.sin(attempted_yaw) * 200.0, 0.0,
               math.cos(attempted_yaw) * 200.0)
        command = {
            'target_yaw': attempted_yaw, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': aim, 'face_position': aim,
            'move_position': aim,
            'recovery_mode': 'drive', 'movement_intent': True,
        }

        class FailureDriver(object):
            def __init__(self):
                self.calls = []

            def remember_failure(self, *args):
                self.calls.append(args)

        adapter = _FixedAdapter(command)
        adapter.driver = FailureDriver()
        status = ['hard']
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            motion_resolver=lambda *unused: status[0],
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=attempted_yaw,
                     speed=4.0, grounded_once=True)

        runtime.update(.04, 1.0)

        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertEqual([(11, attempted_yaw, 5.0)],
                         adapter.driver.calls)
        self.assertEqual(1, len(adapter.calls))

        status[0] = 'clear'
        runtime.update(.04, 1.04)

        self.assertEqual(2, len(adapter.calls))
        self.assertIn(11, runtime._decision_cache)
        self.assertIn(11, runtime._motion_probe_cache)

    def test_nonhard_realised_contacts_keep_cached_command_and_probe(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }

        class FailureDriver(object):
            def __init__(self):
                self.calls = []

            def remember_failure(self, *args):
                self.calls.append(args)

        for motion_status in ('soft', 'cap_crushed'):
            with self.subTest(motion_status=motion_status):
                adapter = _FixedAdapter(command)
                adapter.driver = FailureDriver()
                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: adapter,
                    direction_probe=lambda *unused: {
                        'clear': True, 'collision': False, 'slope': 0.0},
                    motion_resolver=lambda *unused, value=motion_status: value,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, baked_graph=_graph())
                runtime.battle_start(self.start)
                runtime.states[11].update(
                    x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                    grounded_once=True)

                runtime.update(.04, 1.0)

                self.assertIn(11, runtime._decision_cache)
                self.assertIn(11, runtime._motion_probe_cache)
                self.assertEqual([], adapter.driver.calls)

    def test_bot_cap_crush_keeps_real_speed_then_moves_next_tick(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        statuses = iter(('cap_crushed', 'crushed'))
        calls = []

        def resolver(*args):
            calls.append(args)
            return next(statuses)

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            motion_resolver=resolver,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)
        before_position = (state['x'], state['y'], state['z'])

        runtime.update(.04, 1.0)
        first_speed = state['speed']
        first_position = (state['x'], state['y'], state['z'])
        runtime.update(.04, 1.04)

        self.assertEqual(before_position, first_position)
        self.assertEqual(4.0, first_speed)
        self.assertGreater(state['speed'], first_speed)
        self.assertGreater(state['z'], before_position[2])
        self.assertEqual(2, len(calls))
        self.assertNotIn('destructible_contact_speed', state)

    def test_probe_duration_totals_measure_queries_without_driving_work(self):
        clock_value = [0.0]

        def probe_clock():
            clock_value[0] += 0.001
            return clock_value[0]

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            direction_probe=lambda *unused: {'clear': True},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            physics_ground_probe=lambda *unused: 0.0,
            probe_clock=probe_clock)
        source = {'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                  'view_range': 500.0}
        target = {'id': 12, 'network_id': 12, 'kind': 'bot', 'team': 2,
                  'x': 100.0, 'y': 0.0, 'z': 0.0,
                  'position': (100.0, 0.0, 0.0), 'fire_seq': 0,
                  'speed': 0.0}
        before = runtime.probe_duration_totals()

        runtime._probe_direction((0.0, 0.0, 0.0), 0.0)
        self.assertTrue(runtime._visible(source, target, 1.0))
        self.assertTrue(runtime._shot_clear(source, target, 1.0))
        runtime._terrain_support({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'half_length': 3.0})

        after = runtime.probe_duration_totals()
        self.assertEqual(after, runtime.probe_duration_totals())
        elapsed = dict(zip(
            self.module.PROBE_KINDS,
            (after[index] - before[index]
             for index in range(len(after)))))
        for name in ('visibility', 'lane', 'ground', 'motion'):
            self.assertAlmostEqual(0.001, elapsed[name])
        self.assertEqual(0.0, elapsed['cover'])

    def test_disabling_probe_clock_preserves_wire_state_and_probe_sequence(self):
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Clock-A'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Clock-B'},
        ]

        def exercise(probe_clock):
            probes = []

            def direction(position, yaw, speed=0.0):
                probes.append(('motion', tuple(position), yaw, speed))
                return {'clear': True, 'collision': False,
                        'water': False, 'slope': 0.0}

            def visibility(source, target, fired_recently=False):
                probes.append(('visibility', source['id'],
                               target['network_id'], fired_recently))
                return True

            def firing_lane(source, target):
                probes.append(('lane', source['id'],
                               target['network_id']))
                return True

            def ground(x, z, hint):
                probes.append(('ground', x, z, hint))
                return 0.0

            runtime = self.module.BotRuntime(
                1, descriptor_resolver=lambda unused: _combat_descriptor(),
                adapter_factory=lambda *args: _Adapter(*args),
                direction_probe=direction,
                visibility_probe=visibility,
                firing_lane_probe=firing_lane,
                ground_probe=ground, physics_ground_probe=ground,
                spawn_resolver=_spawn_resolver, baked_graph=_graph(),
                probe_clock=probe_clock)
            wire = list(runtime.battle_start(
                dict(self.start, bots=roster)))
            for frame in range(20):
                wire.extend(runtime.update(
                    0.05, 1.0 + frame * 0.05, players=[]))
            return (wire, runtime.presentation_states(), probes,
                    runtime.probe_totals(),
                    runtime.probe_duration_totals())

        clock_value = [0.0]

        def clock():
            clock_value[0] += 0.0001
            return clock_value[0]

        untimed = exercise(None)
        timed = exercise(clock)

        self.assertEqual(untimed[:4], timed[:4])
        self.assertTrue(any(untimed[3]))
        self.assertEqual((0.0,) * len(self.module.PROBE_KINDS), untimed[4])
        self.assertTrue(any(value > 0.0 for value in timed[4]))

    def test_ground_support_samples_ends_only_when_centre_is_missing(self):
        calls = []

        def ground(unused_x, z, unused_y):
            calls.append(z)
            if abs(z) < 1e-9:
                return None
            return 2.0 if z > 0.0 else 1.0

        runtime = self.module.BotRuntime(
            1, physics_ground_probe=ground)

        self.assertEqual((2.0, None), runtime._terrain_support({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'half_length': 3.0}))
        self.assertEqual([0.0, 3.0, -3.0], calls)
        self.assertEqual(3, dict(zip(
            self.module.PROBE_KINDS, runtime.probe_totals()))['ground'])

    def test_vertical_motion_uses_centre_without_sampling_higher_ends(self):
        calls = []

        def ground(x, z, unused_y):
            calls.append((x, z))
            return 2.0 if abs(x) < 1e-9 and abs(z) < 1e-9 else 20.0

        runtime = self.module.BotRuntime(
            1, physics_ground_probe=ground)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 0.0, 'half_length': 3.0,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': False, 'last_drive_pitch': 0.0,
        }

        support_blocked = runtime._update_vertical_motion(state, 0.1)

        self.assertEqual([(0.0, 0.0)], calls)
        self.assertEqual(2.0, state['y'])
        self.assertFalse(state['airborne'])
        self.assertFalse(support_blocked)

    def test_grounded_bot_rejects_raised_centre_support_and_recovers(self):
        failures = []
        driver = types.SimpleNamespace(
            remember_failure=lambda *args: failures.append(args))
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda *unused: 1.4)
        runtime.adapter = types.SimpleNamespace(driver=driver)
        runtime._turn_speeds[11] = 0.5
        runtime._decision_cache[11] = object()
        runtime._motion_probe_cache[11] = object()
        state = {
            'id': 11, 'x': 2.0, 'y': 0.0, 'z': 3.0, 'yaw': 0.25,
            'speed': 4.0, 'half_length': 3.0,
            'movement_dir': 1, 'rotation_dir': 1,
            'push_x': 0.4, 'push_z': -0.3,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': True, 'last_drive_pitch': 0.0,
            'destructible_contact_speed': 4.0,
        }

        support_blocked = runtime._update_vertical_motion(
            state, 0.1, (0.0, 0.0, 0.0), 0.75)

        self.assertTrue(support_blocked)
        self.assertEqual((0.0, 0.0, 0.0),
                         (state['x'], state['y'], state['z']))
        self.assertEqual((0.0, 0, 0, 0.0, 0.0), (
            state['speed'], state['movement_dir'], state['rotation_dir'],
            state['push_x'], state['push_z']))
        self.assertEqual(0.0, runtime._turn_speeds[11])
        self.assertFalse(state['airborne'])
        self.assertNotIn('destructible_contact_speed', state)
        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertEqual([(11, 0.75, 5.0)], failures)

    def test_vertical_motion_uses_edge_fallback_or_ballistic_fall(self):
        def edge_ground(unused_x, z, unused_y):
            if abs(z) < 1e-9:
                return None
            return 3.0 if z > 0.0 else 1.0

        runtime = self.module.BotRuntime(
            1, physics_ground_probe=edge_ground)
        supported = {
            'id': 11, 'x': 0.0, 'y': 8.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 0.0, 'half_length': 3.0,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': False, 'last_drive_pitch': 0.0,
        }

        runtime._update_vertical_motion(supported, 0.1)

        self.assertEqual(3.0, supported['y'])
        self.assertFalse(supported['airborne'])

        falling_runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda *unused: None)
        falling = dict(supported)
        falling.update(y=3.0, vertical_speed=0.0, airborne=False,
                       grounded_once=True)
        falling_runtime._update_vertical_motion(falling, 0.1)
        self.assertTrue(falling['airborne'])
        self.assertLess(falling['vertical_speed'], 0.0)
        self.assertLess(falling['y'], 3.0)

    def test_probe_clock_failure_never_changes_probe_result_or_call_count(self):
        calls = []

        def fail_clock():
            raise RuntimeError('diagnostic clock failed')

        runtime = self.module.BotRuntime(
            1,
            direction_probe=lambda *unused: calls.append(1) or {
                'clear': True},
            probe_clock=fail_clock)

        self.assertTrue(runtime._probe_is_clear(
            runtime._probe_direction((0.0, 0.0, 0.0), 0.0)))
        self.assertTrue(runtime._probe_is_clear(
            runtime._probe_direction((0.0, 0.0, 0.0), 0.0)))
        self.assertEqual([1, 1], calls)
        self.assertEqual((0.0,) * len(self.module.PROBE_KINDS),
                         runtime.probe_duration_totals())

        clock_calls = [0]

        def fail_on_finish():
            clock_calls[0] += 1
            if clock_calls[0] == 2:
                raise RuntimeError('diagnostic finish clock failed')
            return 1.0

        finish_calls = []
        runtime = self.module.BotRuntime(
            1,
            direction_probe=lambda *unused: finish_calls.append(1) or {
                'clear': True},
            probe_clock=fail_on_finish)
        self.assertTrue(runtime._probe_is_clear(
            runtime._probe_direction((0.0, 0.0, 0.0), 0.0)))
        self.assertEqual([1], finish_calls)
        self.assertEqual((0.0,) * len(self.module.PROBE_KINDS),
                         runtime.probe_duration_totals())

    def test_flat_full_roster_uses_one_ground_query_per_bot_frame(self):
        calls = []
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Ground-%d' % index}
            for index in range(29)
        ]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *args: calls.append(args) or 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))

        runtime.update(0.04, 1.0)

        self.assertEqual(29, len(calls))
        self.assertEqual(29, dict(zip(
            self.module.PROBE_KINDS, runtime.probe_totals()))['ground'])

    def test_friendly_crossing_traffic_has_one_right_of_way_winner(self):
        lower = {
            'id': 21, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 5.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        higher = {
            'id': 23, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 8.0,
            'yaw': math.pi, 'speed': 5.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        lower_command = {'throttle': 1.0, 'target_yaw': 0.0}
        higher_command = {'throttle': 1.0, 'target_yaw': math.pi}

        traffic_throttle = self.module.BotRuntime._traffic_throttle
        lower_throttle, lower_waiting = traffic_throttle(
            lower, lower_command, [dict(
                higher, position=(higher['x'], higher['y'], higher['z']),
                velocity=(0.0, 0.0, -5.0))])
        higher_throttle, higher_waiting = traffic_throttle(
            higher, higher_command, [dict(
                lower, position=(lower['x'], lower['y'], lower['z']),
                velocity=(0.0, 0.0, 5.0))])

        self.assertEqual((1.0, False),
                         (lower_throttle, lower_waiting))
        self.assertEqual((0.0, True),
                         (higher_throttle, higher_waiting))

    def test_traffic_yield_is_friendly_only_and_humans_have_priority(self):
        source = {
            'id': 21, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 5.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        command = {'throttle': 1.0, 'target_yaw': 0.0}
        traffic = {
            'position': (0.0, 0.0, 8.0), 'yaw': 0.0,
            'velocity': (0.0, 0.0, 0.0),
            'half_length': 3.5, 'half_width': 1.7,
        }

        traffic_throttle = self.module.BotRuntime._traffic_throttle
        self.assertEqual((1.0, False), traffic_throttle(
            source, command, [dict(traffic, id=22, team=1)]))
        self.assertEqual((0.0, True), traffic_throttle(
            source, command, [dict(
                traffic, id=self.module.HUMAN_TARGET_ID_BASE + 1,
                team=2)]))

    def test_traffic_wait_does_not_enter_reverse_recovery(self):
        from gui.mods.offline_lan_0922.ai.driver import LocalDriver
        driver = LocalDriver()
        driver_state = driver._state(11, (0.0, 0.0, 0.0))
        driver_state['stuck_time'] = 10.0
        driver_state['recovery_time'] = 0.5

        self.assertTrue(driver.wait_for_traffic(11))
        self.assertEqual((0.0, 0.0), (
            driver_state['stuck_time'], driver_state['recovery_time']))

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
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=factory, baked_graph=graph,
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
                1, descriptor_resolver=lambda unused: _combat_descriptor(),
                adapter_factory=lambda *args, **unused: _Adapter(*args),
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

    def test_render_frames_update_pose_before_30hz_network_throttle(self):
        self.runtime.descriptor_resolver = lambda unused: _combat_descriptor(
            reload_time=0.45, clip=(1,))
        self.runtime.battle_start(self.start)
        first = self.runtime.update(.02, 1.0)
        first_pose = self.runtime.presentation_states()[0]
        second = self.runtime.update(.02, 1.02, players=[{
            'id': 2, 'team': 1, 'alive': True,
            'x': 5, 'y': 0, 'z': 5}])
        second_pose = self.runtime.presentation_states()[0]
        self.assertEqual('bot_state', first[0]['type'])
        self.assertEqual([], second)
        self.assertNotEqual(first_pose['z'], second_pose['z'])
        self.assertEqual(0, second_pose['fire_seq'])
        player = [{'id': 2, 'team': 1, 'alive': True,
                   'x': 5, 'y': 0, 'z': 5}]
        self.runtime.update(.20, 1.22, players=player)
        self.runtime.update(.20, 1.42, players=player)
        result = self.runtime.update(.04, 1.46, players=player)
        bot = result[0]['bots'][0]
        self.assertEqual('bot_state', result[0]['type'])
        self.assertGreater(bot['z'], 0.0); self.assertEqual(1, bot['fire_seq'])
        self.assertEqual(0, bot['shell_index'])

    def test_authority_publication_and_server_ack_remain_live_for_two_minutes(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.5, clip=(1,), max_ammo=300),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Shooter-%d' % index}
            for index in range(29)
        ]
        start = dict(self.start, bots=roster)
        manifest_message = runtime.battle_start(start)[0]
        for state in runtime.states.values():
            state.update(x=0.0, y=0.0, z=0.0,
                         yaw=0.0, aim_yaw=0.0)

        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        server.players[1] = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        server.bot_authority_id = 1
        server.bot_roster = list(roster)
        self.assertTrue(server.update_bot_manifest(
            1, {'round_id': server.round_id,
                'bots': manifest_message['bots']}))

        player = {
            'id': 1, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }
        published = 0
        accepted = 0
        fps = 24
        frame_count = fps * 120
        for frame in range(frame_count):
            now = 1.0 + frame / float(fps)
            outgoing = runtime.update(
                1.0 / float(fps), now, players=[player])
            bot_states = [message for message in outgoing
                          if message['type'] == 'bot_state']
            self.assertLessEqual(len(bot_states), 1)
            for bot_state in bot_states:
                self.assertEqual(29, len(bot_state['bots']))
                published += 1
                self.assertTrue(server.update_bot_states(1, {
                    'round_id': server.round_id,
                    'bots': bot_state['bots'],
                }))
                accepted += 1
                for published_bot in bot_state['bots']:
                    server_bot = server.bot_states[published_bot['id']]
                    self.assertEqual(published_bot['combat_seq'],
                                     server_bot['combat_ack_seq'])
                runtime.apply_snapshot({
                    'server_tick': frame,
                    'bots': [dict(server.bot_states[bot_id])
                             for bot_id in sorted(server.bot_states)],
                })
                for bot_id, server_bot in server.bot_states.items():
                    self.assertEqual(
                        server_bot['combat_ack_seq'],
                        runtime.states[bot_id]['combat_ack_seq'])

        self.assertEqual((frame_count, frame_count), (published, accepted))
        enemy_ids = {entry['id'] for entry in roster if entry['team'] == 2}
        friendly_ids = {entry['id'] for entry in roster if entry['team'] == 1}
        self.assertTrue(all(runtime.states[bot_id]['fire_seq'] > 100
                            for bot_id in enemy_ids))
        self.assertTrue(all(runtime.states[bot_id]['fire_seq'] == 0
                            for bot_id in friendly_ids))
        self.assertEqual(
            {bot_id: state['fire_seq']
             for bot_id, state in runtime.states.items()},
            {bot_id: state['fire_seq']
             for bot_id, state in server.bot_states.items()})

    def test_rapid_clip_never_skips_server_fire_sequence_at_120_fps(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 0.0, 100.0),
            'face_position': (0.0, 0.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.5, clip=(30, 0.01)),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Autoloader-%d' % index}
            for index in range(29)
        ]
        manifest = runtime.battle_start(
            dict(self.start, bots=roster))[0]['bots']
        for state in runtime.states.values():
            state.update(
                x=0.0, y=0.0, z=0.0, yaw=0.0, aim_yaw=0.0)

        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        server.players[1] = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        server.bot_authority_id = 1
        server.bot_roster = list(roster)
        self.assertTrue(server.update_bot_manifest(1, {
            'round_id': server.round_id, 'bots': manifest}))

        player = {
            'id': 1, 'team': 1, 'alive': True,
            'vehicle': 'ussr:R11_MS-1',
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }
        previous_fire = dict((entry['id'], 0) for entry in roster)
        publications = 0
        for frame in range(240):
            outgoing = runtime.update(
                1.0 / 120.0, 1.0 + frame / 120.0,
                players=[player])
            for message in outgoing:
                if message['type'] != 'bot_state':
                    continue
                publications += 1
                for bot in message['bots']:
                    current_fire = bot['fire_seq']
                    self.assertLessEqual(
                        current_fire - previous_fire[bot['id']], 1)
                    previous_fire[bot['id']] = current_fire
                self.assertTrue(server.update_bot_states(1, {
                    'round_id': server.round_id,
                    'bots': message['bots'],
                }))
                runtime.apply_snapshot({
                    'server_tick': frame,
                    'bots': [dict(server.bot_states[bot_id])
                             for bot_id in sorted(server.bot_states)],
                })

        self.assertGreater(publications, 50)
        enemy_ids = {entry['id'] for entry in roster if entry['team'] == 2}
        friendly_ids = {entry['id'] for entry in roster if entry['team'] == 1}
        self.assertTrue(all(previous_fire[bot_id] > 20
                            for bot_id in enemy_ids))
        self.assertTrue(all(previous_fire[bot_id] == 0
                            for bot_id in friendly_ids))

    def test_render_rate_presentation_and_publication_sequence_are_separate(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)

        for fps in (20, 24, 30, 40, 60, 120):
            with self.subTest(fps=fps):
                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _critical_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        command),
                    direction_probe=lambda *unused: {
                        'clear': True, 'slope': 0.0},
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver,
                    baked_graph=_graph())
                roster = [dict(self.start['bots'][0])]
                start = dict(self.start, bots=roster)
                manifest = runtime.battle_start(start)[0]
                runtime.states[11]['critical'] = dict(burning)

                server = BattleState(map_name='04_himmelsdorf')
                server.client_build = CLIENT_BUILD_0922
                server.phase = 'battle'
                server.tick = 100000
                server.players[1] = Player(
                    1, object(), ('127.0.0.1', 1), team=1, slot=0)
                server.bot_authority_id = 1
                server.bot_roster = list(roster)
                self.assertTrue(server.update_bot_manifest(1, {
                    'round_id': server.round_id,
                    'bots': manifest['bots'],
                }))

                dt = 1.0 / float(fps)
                previous_pose = None
                changed_frames = 0
                publications = 0
                last_ack = 0
                for frame in range(fps * 2):
                    now = 10.0 + (frame + 1) * dt
                    outgoing = runtime.update(dt, now)
                    pose = runtime.presentation_states()[0]
                    current_pose = (pose['x'], pose['y'], pose['z'],
                                    pose['yaw'])
                    if previous_pose is not None:
                        self.assertNotEqual(previous_pose, current_pose)
                        changed_frames += 1
                    previous_pose = current_pose

                    publications_now = [
                        message for message in outgoing
                        if message.get('type') == 'bot_state']
                    self.assertLessEqual(len(publications_now), 1)
                    for publication in publications_now:
                        publications += 1
                        published = publication['bots'][0]
                        self.assertEqual(last_ack + 1,
                                         published['combat_seq'])
                        self.assertTrue(server.update_bot_states(1, {
                            'round_id': server.round_id,
                            'bots': publication['bots'],
                        }))
                        canonical = server.bot_states[11]
                        self.assertEqual(published['combat_seq'],
                                         canonical['combat_ack_seq'])
                        last_ack = canonical['combat_ack_seq']
                        runtime.apply_snapshot({
                            'server_tick': frame,
                            'bots': [dict(canonical)],
                        })

                self.assertEqual(fps * 2 - 1, changed_frames)
                expected_publications = min(fps, 30) * 2
                self.assertGreaterEqual(
                    publications, expected_publications - 1)
                self.assertLessEqual(
                    publications, expected_publications + 1)
                self.assertEqual(publications, last_ack)

    def test_29_bot_sensing_budget_is_render_rate_independent(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Mover-%d' % index}
            for index in range(29)
        ]
        probe_totals = {}
        for fps in (40, 60, 120):
            with self.subTest(fps=fps):
                frame_number = [0]
                probe_frames = []

                def direction_probe(*unused):
                    probe_frames.append(frame_number[0])
                    return {'clear': True, 'slope': 0.0}

                runtime = self.module.BotRuntime(
                    1, descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        command),
                    direction_probe=direction_probe,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, baked_graph=_graph())
                runtime.battle_start(dict(self.start, bots=roster))
                previous = None
                changed = 0
                publications = []
                dt = 1.0 / float(fps)
                for frame in range(fps * 2):
                    frame_number[0] = frame
                    outgoing = runtime.update(
                        dt, 10.0 + (frame + 1) * dt)
                    poses = tuple(
                        (state['id'], state['x'], state['y'], state['z'],
                         state['yaw'])
                        for state in runtime.presentation_states())
                    if previous is not None:
                        self.assertTrue(all(
                            poses[index] != previous[index]
                            for index in range(29)))
                        changed += 29
                    previous = poses
                    publications.extend(
                        message for message in outgoing
                        if message.get('type') == 'bot_state')

                self.assertEqual(29 * (fps * 2 - 1), changed)
                self.assertGreaterEqual(len(publications), 59)
                self.assertLessEqual(len(publications), 61)
                self.assertTrue(all(
                    len(message['bots']) == 29
                    for message in publications))
                # Planner clear(state.yaw) and copied physics consume one raw
                # sample when they select the same heading in the same frame.
                self.assertEqual(29, probe_frames.count(0))
                later_counts = [probe_frames.count(frame)
                                for frame in range(1, fps * 2)]
                self.assertLess(max(later_counts), 29)
                maximum_per_bot = (
                    4 + 2 * int(math.ceil(
                        2.0 / self.module.DECISION_SECONDS)))
                self.assertLessEqual(
                    len(probe_frames), 29 * maximum_per_bot)
                probe_totals[fps] = len(probe_frames)

        # More render callbacks integrate more poses, not more native sensing.
        self.assertLessEqual(
            max(probe_totals.values()) - min(probe_totals.values()), 29 * 4)

    def test_cover_jobs_are_phased_and_publish_complete_fair_batches(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 0.0),
            'face_position': (0.0, 1.0, 0.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        roster = [
            {'id': 11 + index, 'team': 2, 'slot': index,
             'name': 'Cover-%d' % index}
            for index in range(6)
        ]
        player = {
            'id': 1, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }
        for fps in (24, 40, 60, 120):
            with self.subTest(fps=fps):
                frame_number = [0]
                calls = []

                def cover_probe(source, target, unused_route, allies,
                                unused_segment_clear):
                    calls.append((frame_number[0], source['id'],
                                  frame_number[0] / float(fps)))
                    self.assertEqual(6, len(allies))
                    return ({'source_id': source['id'],
                             'target_id': target['network_id']},)

                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        command),
                    direction_probe=lambda *unused: {
                        'clear': True, 'slope': 0.0},
                    visibility_probe=lambda *unused: True,
                    firing_lane_probe=lambda *unused: True,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, cover_probe=cover_probe,
                    baked_graph=_graph())
                runtime.battle_start(dict(self.start, bots=roster))

                observations = []
                observation_frames = []
                dt = 1.0 / float(fps)
                for frame in range(fps * 2):
                    frame_number[0] = frame
                    outgoing = runtime.update(
                        dt, 1.0 + frame * dt, players=[player])
                    published = [
                        message for message in outgoing
                        if message.get('type') == 'bot_observation']
                    observations.extend(published)
                    observation_frames.extend(
                        [frame] * len(published))
                    if len(observations) >= 3:
                        break

                self.assertEqual(3, len(observations))
                # The immediate observation primes the future window.  Every
                # later observation carries the complete three-job batch
                # selected by its predecessor.
                self.assertEqual([], observations[0]['affordances'])
                result_batches = [
                    [value['candidates'][0]['source_id']
                     for value in observation['affordances']]
                    for observation in observations[1:3]
                ]
                self.assertEqual([[11, 12, 13], [14, 15, 16]],
                                 result_batches)
                self.assertEqual([11, 12, 13, 14, 15, 16],
                                 [value[1] for value in calls[:6]])
                self.assertEqual(11, calls[6][1])
                self.assertEqual(observation_frames[2], calls[6][0])
                # The next batch may start on the publication frame, but its
                # first result is retained for the *following* observation;
                # it must never leak into the message just formed above.
                self.assertEqual([11], [
                    value['bot_id'] for value in runtime._cover_results])
                self.assertEqual([12, 13], [
                    value[1] for value in runtime._cover_queue])
                frame_counts = {}
                for frame, unused_bot_id, unused_offset in calls:
                    frame_counts[frame] = frame_counts.get(frame, 0) + 1
                self.assertEqual(1, max(frame_counts.values()))
                self.assertEqual(7, len(calls))

                # Cover owns the first half-window and firing-lane refreshes
                # own the second.  Even at 24 FPS all three cover jobs finish
                # before the final 0.10-second lane-refresh window begins.
                first_offsets = [value[2] for value in calls[:3]]
                self.assertEqual(0.0, first_offsets[0])
                self.assertLess(first_offsets[-1],
                                self.module.COVER_JOB_WINDOW_SECONDS + 1e-9)

    def test_cached_motion_probe_has_explicit_corridor_safety_bounds(self):
        cached = {
            'result': {'clear': True, 'slope': 0.0},
            'position': (0.0, 0.0, 0.0), 'yaw': 0.0,
            'deadline': 1.0975,
        }
        reusable = self.module.BotRuntime._motion_probe_reusable

        self.assertTrue(reusable(
            cached, (0.0, 0.0, 3.4), 0.0, 35.0, 1.09))
        self.assertFalse(reusable(
            cached, (0.0, 0.0, 3.51), 0.0, 35.0, 1.09))
        self.assertFalse(reusable(
            cached, (1.01, 0.0, 0.0), 0.0, 0.0, 1.09))
        self.assertFalse(reusable(
            cached, (0.0, 0.0, 0.0),
            math.asin(1.01 / 15.0), 0.0, 1.09))
        self.assertFalse(reusable(
            cached, (0.0, 0.0, 0.0), 0.0, 0.0, 1.0975))

    def test_typed_world_receipt_contains_only_the_actual_motion_step(self):
        runtime = self.module.BotRuntime(1)
        runtime._motion_probe_cache[11] = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                'world_receipt': {
                    'distance': 8.0,
                    'half_width': 1.6,
                    'leading': 3.5,
                    'origin': (0.0, 0.0, 0.0),
                    'yaw': 0.0,
                    'direction': 1,
                },
            },
            'position': (0.0, 0.0, 0.0),
            'yaw': 0.0,
            'deadline': 1.10,
        }

        self.assertTrue(runtime.motion_world_receipt_reusable(
            11, (0.0, 0.0, 3.4), 0.0, 35.0,
            now=1.09, dt=0.02))
        self.assertFalse(runtime.motion_world_receipt_reusable(
            11, (0.0, 0.0, 3.4), 0.0, 35.0,
            now=1.09, dt=0.04))

    def test_typed_world_receipt_rejects_pose_heading_expiry_and_defer(self):
        runtime = self.module.BotRuntime(1)
        cached = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                'world_receipt': {
                    'distance': 8.0,
                    'half_width': 1.6,
                    'leading': 3.5,
                    'origin': (0.0, 0.0, 0.0),
                    'yaw': 0.0,
                    'direction': 1,
                },
            },
            'position': (0.0, 0.0, 0.0),
            'yaw': 0.0,
            'deadline': 1.10,
        }
        runtime._motion_probe_cache[11] = cached

        reusable = runtime.motion_world_receipt_reusable
        self.assertTrue(reusable(
            11, (0.0, 0.0, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0002, 0.0, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0002, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.04, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, -0.0002), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, -0.05), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.0), 0.00002, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.0), 0.0, 4.0,
            now=1.10, dt=0.04))

        cached['result']['deferred'] = True
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))

    def test_expired_plan_refresh_carries_only_a_contained_world_receipt(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        direction_calls = []
        receipt_calls = []

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_calls.append((tuple(position), yaw, speed))
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: direction_calls.append(1) or {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(self.start)

        runtime.update(.04, 1.0)
        first_receipt = runtime._motion_probe_cache[11][
            'result']['world_receipt']
        self.assertEqual(1, len(direction_calls))
        self.assertEqual(1, len(receipt_calls))

        # The generic planning sample expires, but the exact hull is still at
        # the receipt origin. Refresh slope/steering without another 3x3 proof.
        runtime.update(.04, 1.20)
        self.assertEqual(2, len(direction_calls))
        self.assertEqual(1, len(receipt_calls))
        self.assertIs(
            first_receipt,
            runtime._motion_probe_cache[11]['result']['world_receipt'])
        self.assertEqual(1, runtime.states[11]['movement_dir'])

        # Even sub-millimetre lateral drift is outside the exact typed lanes;
        # the next expired planning sample must acquire a new native receipt.
        runtime.states[11]['x'] += 0.0002
        runtime.update(.04, 1.40)
        self.assertEqual(3, len(direction_calls))
        self.assertEqual(2, len(receipt_calls))
        self.assertIsNot(
            first_receipt,
            runtime._motion_probe_cache[11]['result']['world_receipt'])

    def test_typed_receipt_owns_origin_yaw_and_direction_over_cache_key(self):
        runtime = self.module.BotRuntime(1)
        receipt = {
            'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
            'origin': (0.0, 0.0, 0.0), 'yaw': 0.0, 'direction': 1,
        }
        runtime._motion_probe_cache[11] = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                'world_receipt': receipt,
            },
            # Model two planner requests that collided under round(yaw, 4):
            # the cache metadata names the later request, while the receipt
            # was actually sampled at the exact origin and yaw above.
            'position': (0.0, 0.0, 0.00004),
            'yaw': 0.00004,
            'deadline': 1.10,
        }

        reusable = runtime.motion_world_receipt_reusable
        self.assertTrue(reusable(
            11, (0.0, 0.0, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.00004), 0.00004, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.0), 0.0, -4.0,
            now=1.09, dt=0.04))

    def test_reverse_typed_receipt_reuses_exact_travel_yaw(self):
        runtime = self.module.BotRuntime(1)
        runtime._motion_probe_cache[11] = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                'world_receipt': {
                    'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                    'origin': (0.0, 0.0, 0.0), 'yaw': math.pi,
                    'direction': -1,
                },
            },
            'position': (0.0, 0.0, 0.0),
            'yaw': math.pi,
            'deadline': 1.10,
        }

        self.assertTrue(runtime.motion_world_receipt_reusable(
            11, (0.0, 0.0, 0.0), math.pi, -4.0,
            now=1.09, dt=0.04))
        self.assertFalse(runtime.motion_world_receipt_reusable(
            11, (0.0, 0.0, 0.0), 0.0, -4.0,
            now=1.09, dt=0.04))

    def test_settled_motion_reuses_only_an_unchanged_pose_and_heading(self):
        reusable = self.module.BotRuntime._motion_probe_reusable
        cached = {
            'result': {'clear': True, 'slope': 0.2},
            'position': (10.0, 2.0, 20.0),
            'yaw': 0.5, 'deadline': 1.0,
        }

        self.assertTrue(reusable(
            cached, (10.0, 2.0, 20.0), 0.5, 0.0, 10.0, True))
        self.assertFalse(reusable(
            cached, (10.1, 2.0, 20.0), 0.5, 0.0, 10.0, True))
        self.assertFalse(reusable(
            cached, (10.0, 2.1, 20.0), 0.5, 0.0, 10.0, True))
        self.assertFalse(reusable(
            cached, (10.0, 2.0, 20.0), 0.51, 0.0, 10.0, True))
        self.assertFalse(reusable(
            cached, (10.0, 2.0, 20.0), 0.5, 0.0, 10.0, False))

    def test_settled_full_roster_keeps_slope_without_periodic_motion_probes(self):
        command = self._stationary_command()
        calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: calls.append(1) or {
                'clear': True, 'slope': 0.2},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Settled-%d' % index}
            for index in range(29)
        ]
        runtime.battle_start(dict(self.start, bots=roster))
        # Isolate the continuous-motion seam from LocalDriver's decision
        # probes; an intentional hold returns before direction_clear in the
        # production adapter.
        runtime.adapter.decide = lambda unused_state, unused_clear: dict(command)

        self.assertTrue(all(
            not state['grounded_once'] for state in runtime.states.values()))
        runtime.update(.04, 1.0)
        self.assertEqual(29, len(calls))
        self.assertTrue(all(
            state['grounded_once'] for state in runtime.states.values()))
        runtime.update(.20, 2.0)
        self.assertEqual(29, len(calls))
        self.assertTrue(all(
            abs(state['last_drive_pitch'] + math.atan(0.2)) < 1e-9
            for state in runtime.states.values()))

        command['throttle'] = 1.0
        command['movement_intent'] = True
        runtime.update(.20, 2.2)
        self.assertEqual(58, len(calls))

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
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
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
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
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
            {'id': 2, 'team': 1, 'alive': True,
             'x': 100.0, 'y': 0.5, 'z': 0.0}
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
            artillery_launch_probe=lambda *unused: (_ for _ in ()).throw(
                AssertionError('ordinary tank requested SPG proof')),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        # This test isolates the firing clock. Observation-lane publication is
        # covered separately and intentionally uses the same cached probe.
        runtime._next_observation = 100.0
        state = runtime.states[11]
        state['x'], state['y'], state['z'], state['yaw'] = 0.0, 0.0, 0.0, 0.0
        player = {'id': 2, 'team': 1, 'alive': True,
                  'x': 0.0, 'y': 10.5, 'z': 100.0}

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

    def test_ballistic_aim_leads_a_moving_target_and_matches_barrel_pitch(self):
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(1)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'profile': {'class_tag': 'mediumTank'},
        }
        target = {
            'position': (0.0, 0.0, 300.0),
            'yaw': math.pi * 0.5, 'speed': 20.0,
        }

        solution = runtime._local_ballistic_solution(
            state, target, descriptor, 0)

        self.assertIsNotNone(solution)
        self.assertGreater(solution['aim_position'][0], 1.0)
        self.assertGreater(solution['yaw'], 0.0)
        self.assertGreaterEqual(solution['pitch'], -0.35)
        self.assertLessEqual(solution['pitch'], 0.15)
        self.assertGreater(solution['flight_time'], 0.25)

    def test_spg_requires_a_client_proved_arc_and_accepts_low_root_fallback(self):
        descriptor = _combat_descriptor()
        target = {
            'position': (0.0, 0.0, 500.0),
            'yaw': 0.0, 'speed': 0.0,
        }
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'profile': {'class_tag': 'SPG'},
        }
        calls = []
        runtime = self.module.BotRuntime(
            1, ballistic_solution_probe=lambda *args: calls.append(args) or {
                'aim_position': (0.0, 1.0, 500.0),
                'yaw': 0.0, 'pitch': -0.10, 'flight_time': 0.5,
                'arc': 'low',
            })

        solution = runtime._ballistic_solution(
            state, target, descriptor, 0, 2.0)

        self.assertEqual('low', solution['arc'])
        self.assertEqual(-0.10, solution['pitch'])
        self.assertEqual(1, len(calls))
        blocked = self.module.BotRuntime(1)
        self.assertIsNone(blocked._ballistic_solution(
            state, target, descriptor, 0, 2.0))

    def test_invalid_spg_solution_cannot_be_clamped_into_a_fake_hit(self):
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(
            1, ballistic_solution_probe=lambda *unused: {
                'aim_position': (0.0, 1.0, 500.0),
                'yaw': 0.0, 'pitch': -1.2, 'flight_time': 3.0,
                'arc': 'high',
            })
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'profile': {'class_tag': 'SPG'},
        }

        self.assertIsNone(runtime._ballistic_solution(
            state, {'position': (0.0, 0.0, 500.0)}, descriptor, 0, 3.0))

    def test_spg_solution_beyond_projectile_lifetime_is_rejected(self):
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(
            1, ballistic_solution_probe=lambda *unused: {
                'aim_position': (0.0, 1.0, 500.0),
                'yaw': 0.0, 'pitch': -0.1, 'flight_time': 20.001,
                'arc': 'high',
            })
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'profile': {'class_tag': 'SPG'},
        }

        self.assertIsNone(runtime._ballistic_solution(
            state, {'position': (0.0, 0.0, 500.0)}, descriptor, 0, 3.0))

    def test_spg_final_proof_pending_does_not_consume_fire_sequence(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        descriptor.gun.shots = descriptor.gun.shots * 2
        descriptor.gun.maxAmmo = 60
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 1, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 1000.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        calls = []
        proofs = []

        def final_proof(state, target, unused_descriptor, shell_index,
                        fire_seq, shot_yaw, shot_pitch, flight_time, now):
            calls.append((
                state, target, shell_index, fire_seq, shot_yaw,
                shot_pitch, flight_time, now))
            if len(calls) == 1:
                return None
            speed = 1000.0
            horizontal = math.cos(shot_pitch)
            velocity = (
                math.sin(shot_yaw) * horizontal * speed,
                math.sin(shot_pitch) * speed,
                math.cos(shot_yaw) * horizontal * speed,
            )
            muzzle = (
                target['position'][0] - velocity[0] * flight_time,
                target['position'][1] + 1.0 -
                velocity[1] * flight_time +
                0.5 * 10.0 * flight_time * flight_time,
                target['position'][2] - velocity[2] * flight_time,
            )
            receipt = {
                'proof_key': ('exact', fire_seq, muzzle,
                              shot_yaw, shot_pitch),
                'fire_seq': fire_seq, 'shell_index': shell_index,
                'origin': muzzle,
                'velocity': velocity,
                'shot_yaw': shot_yaw, 'shot_pitch': shot_pitch,
                'gravity': 10.0, 'max_distance': 5000.0,
                'max_time_ms': 20000, 'flight_time': flight_time,
            }
            proofs.append(receipt)
            return receipt

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ballistic_solution_probe=lambda *unused: {
                'aim_position': (0.0, 1.0, 100.0),
                'yaw': 0.0, 'pitch': -0.1, 'flight_time': 0.5,
                'arc': 'low',
            },
            artillery_launch_probe=final_proof,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'turret_yaw': 0.0,
            'gun_pitch': -0.1, 'desired_gun_pitch': -0.1,
            'profile': {'class_tag': 'SPG'},
        })
        runtime._gun_states[11].elapsed = 1.0
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }

        pending = runtime.update(
            0.04, 1.0, players=[player])[0]['bots'][0]
        self.assertEqual(0, pending['fire_seq'])
        self.assertEqual((0, 1), (
            pending['shell_index'], pending['next_shell_index']))
        self.assertNotIn('shot_origin', pending)
        self.assertEqual(1, calls[0][3])
        self.assertEqual(0, calls[0][2])

        fired = runtime.update(
            0.04, 1.04, players=[player])[0]['bots'][0]
        self.assertEqual(1, fired['fire_seq'])
        self.assertEqual((0, 1), (
            fired['shell_index'], fired['next_shell_index']))
        self.assertEqual(1, calls[1][3])
        self.assertEqual(0, calls[1][2])
        self.assertNotEqual(0.0, fired['shot_yaw'])
        self.assertNotEqual(0.1, fired['shot_pitch'])
        self.assertEqual(proofs[0]['origin'], fired['shot_origin'])
        self.assertEqual(proofs[0]['velocity'], fired['shot_velocity'])
        self.assertEqual(20000, fired['shot_max_time_ms'])

    def test_spg_final_proof_waits_for_exact_nominal_alignment(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        calls = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=lambda *args: calls.append(args))
        runtime.round_id = 7
        gun_state = self.module._BotGunState(descriptor)
        state = {
            'id': 11, 'fire_seq': 0, 'aim_yaw': 0.000001,
            'gun_pitch': -0.1, 'gun_aligned': True,
            'critical': {},
        }
        solution = {
            'yaw': 0.0, 'pitch': -0.1, 'flight_time': 0.5,
            'aim_position': (0.0, 1.0, 100.0),
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        self.assertEqual([], calls)

        state['aim_yaw'] = 0.0
        state['gun_pitch'] = -0.100001
        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        self.assertEqual([], calls)

        state['gun_pitch'] = -0.1
        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        self.assertEqual(1, len(calls))

    def test_spg_malformed_final_proof_fails_closed(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        runtime = self.module.BotRuntime(1)
        receipt = {
            'proof_key': ('exact',),
            'fire_seq': 'not-an-integer', 'shell_index': 0,
            'origin': (0.0, 0.0, 0.0),
            'velocity': (0.0, 0.0, 1000.0),
            'shot_yaw': 0.0, 'shot_pitch': 0.0,
            'gravity': 10.0, 'max_distance': 5000.0,
            'max_time_ms': 20000, 'flight_time': 0.5,
        }

        self.assertIsNone(runtime._validated_artillery_receipt(
            receipt, descriptor, 0, 1, 0.0, 0.0, 0.5))

    def test_spg_receipt_is_rejected_when_target_stops_or_reverses(self):
        descriptor = _combat_descriptor()
        receipt = {
            'origin': (0.0, 6.0, 0.0),
            'velocity': (10.0, 0.0, 99.498743710662),
            'gravity': 10.0, 'flight_time': 1.0,
        }
        stopped = {
            'position': (0.0, 0.0, 99.498743710662),
            'yaw': 0.0, 'speed': 0.0,
        }
        reversed_target = dict(
            stopped, yaw=-math.pi * 0.5, speed=8.0)

        runtime = self.module.BotRuntime(1)
        stopped_error = runtime._artillery_receipt_impact_error(
            receipt, stopped)[0]
        reversed_error = runtime._artillery_receipt_impact_error(
            receipt, reversed_target)[0]

        self.assertAlmostEqual(10.0, stopped_error, 6)
        self.assertAlmostEqual(18.0, reversed_error, 6)
        self.assertGreater(
            stopped_error, self.module.ARTILLERY_IMPACT_ERROR_METRES)
        self.assertGreater(
            reversed_error, self.module.ARTILLERY_IMPACT_ERROR_METRES)
        for target in (stopped, reversed_target):
            target = dict(
                target, kind='human', network_id=2, alive=True)
            state = {
                'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                'yaw': 0.0, 'fire_seq': 0,
            }
            physical = self.module._shot_ballistics(descriptor, 0)
            reproof = {
                'source': {'id': 11},
                'source_pose': (0.0, 0.0, 0.0, 0.0),
                'target_identity': ('human', 2),
                'shell_index': 0, 'fire_seq': 1,
                'physical': physical,
                'compensation_offset': (0.0, 0.0, 0.0),
                'attempts': 0, 'created': 1.0,
                'deadline': 61.0, 'absolute_deadline': 121.0,
            }
            intent = {
                'source': {'id': 11}, 'created': 1.0,
                'solution': {
                    'arc': 'low',
                    'aim_position': (10.0, 1.0, 99.498743710662),
                },
            }
            runtime._artillery_reproofs[11] = reproof
            runtime._artillery_intents[11] = intent

            self.assertTrue(runtime._reject_stale_artillery_receipt(
                state, target, descriptor, 0, intent, receipt, 2.0))
            self.assertNotIn(11, runtime._artillery_intents)
            self.assertEqual(1, runtime._artillery_reproofs[11]['attempts'])
            solution = runtime._artillery_reproof_solution(
                state, target, descriptor, 0,
                runtime._artillery_reproofs[11])
            self.assertIsNotNone(solution)
            if target['speed'] == 0.0:
                self.assertAlmostEqual(0.0, solution['aim_position'][0], 6)
            else:
                self.assertLess(solution['aim_position'][0], -1.0)

    def test_spg_receipt_is_rejected_when_target_height_changes(self):
        receipt = {
            'origin': (0.0, 11.0, 0.0),
            'velocity': (0.0, 0.0, 100.0),
            'gravity': 10.0, 'flight_time': 1.0,
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
            'yaw': 0.0, 'speed': 0.0,
        }
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(1)
        distance, error = runtime._artillery_receipt_impact_error(
            receipt, target)
        self.assertAlmostEqual(5.0, distance, 6)
        self.assertEqual((0.0, -5.0, 0.0), error)

        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'fire_seq': 0,
        }
        reproof = {
            'source': {'id': 11},
            'source_pose': (0.0, 0.0, 0.0, 0.0),
            'target_identity': ('human', 2),
            'shell_index': 0, 'fire_seq': 1,
            'physical': self.module._shot_ballistics(descriptor, 0),
            'compensation_offset': (0.0, 0.0, 0.0),
            'attempts': 0, 'created': 1.0, 'deadline': 61.0,
            'absolute_deadline': 121.0,
        }
        intent = {
            'source': {'id': 11}, 'created': 1.0,
            'solution': {
                'arc': 'low', 'aim_position': (0.0, 6.0, 100.0),
            },
        }
        runtime._artillery_reproofs[11] = reproof
        runtime._artillery_intents[11] = intent

        self.assertTrue(runtime._reject_stale_artillery_receipt(
            state, target, descriptor, 0, intent, receipt, 2.0))
        self.assertNotIn(11, runtime._artillery_intents)
        self.assertEqual(1, runtime._artillery_reproofs[11]['attempts'])
        solution = runtime._artillery_reproof_solution(
            state, target, descriptor, 0,
            runtime._artillery_reproofs[11])
        self.assertIsNotNone(solution)
        self.assertAlmostEqual(1.0, solution['aim_position'][1], 6)

    def test_spg_pinned_receipt_hold_rechecks_motion_and_closest_frame(self):
        runtime = self.module.BotRuntime(1)
        receipt = {
            'origin': (0.0, 6.0, 0.0),
            'velocity': (20.0, 0.0, 100.0),
            'gravity': 10.0, 'flight_time': 1.0,
        }

        def reproof(velocity):
            return {'held_receipt': {
                'receipt': receipt, 'velocity': velocity,
                'target_identity': ('human', 2), 'deadline': 20.0,
            }}

        target = {
            'kind': 'human', 'network_id': 2,
            'position': (10.0, 0.0, 100.0),
            'yaw': math.pi * 0.5, 'speed': 10.0,
        }
        held = reproof((10.0, 0.0, 0.0))
        value, waiting = runtime._held_artillery_receipt(held, target, 1.0)
        self.assertIs(receipt, value)
        self.assertTrue(waiting)

        for changed in (
                dict(target, speed=0.0),
                dict(target, yaw=-math.pi * 0.5),
                dict(target, velocity=(10.0, 1.0, 0.0)),
                dict(target, network_id=3)):
            held = reproof((10.0, 0.0, 0.0))
            value, waiting = runtime._held_artillery_receipt(
                held, changed, 1.0)
            self.assertIsNone(value)
            self.assertFalse(waiting)
            self.assertIsNone(held['held_receipt'])

        held = reproof((10.0, 0.0, 0.0))
        before = dict(target, position=(8.0, 0.0, 100.0))
        value, waiting = runtime._held_artillery_receipt(held, before, 1.0)
        self.assertIsNone(value)
        self.assertTrue(waiting)
        after = dict(target, position=(10.0, 0.0, 100.0))
        value, waiting = runtime._held_artillery_receipt(held, after, 1.04)
        self.assertIs(receipt, value)
        self.assertTrue(waiting)

    def test_spg_pending_intent_freezes_moving_target_angles_and_sequence(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        launch_calls = []
        cancel_calls = []
        strategic_calls = []
        fresh = {
            'aim_position': (8.0, 1.0, 100.0),
            'yaw': 0.08, 'pitch': -0.12,
            'flight_time': 0.6, 'arc': 'low',
        }
        runtime = self.module.BotRuntime(
            1,
            ballistic_solution_probe=lambda *unused: (
                strategic_calls.append(True) or fresh),
            artillery_launch_probe=lambda *args: (
                launch_calls.append(args) or None),
            artillery_launch_cancel=lambda source: cancel_calls.append(source))
        runtime.round_id = 9
        gun_state = self.module._BotGunState(descriptor)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
            'yaw': math.pi * 0.5, 'speed': 8.0,
        }
        proved = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, proved, 1.0))
        intent = runtime._artillery_intents[11]
        first_angles = (
            intent['shot_yaw'], intent['shot_pitch'],
            intent['solution']['flight_time'])
        moved = dict(target, position=(8.0, 0.0, 100.0))
        frozen = runtime._ballistic_solution(
            state, moved, descriptor, 0, 1.04)
        self.assertEqual(proved['yaw'], frozen['yaw'])
        self.assertEqual(proved['pitch'], frozen['pitch'])
        self.assertEqual(0, len(strategic_calls))

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, moved, descriptor, 0, gun_state, frozen, 1.04))
        second_angles = (
            launch_calls[1][5], launch_calls[1][6], launch_calls[1][7])
        self.assertEqual(first_angles, second_angles)
        self.assertEqual(1, launch_calls[0][4])
        self.assertEqual(1, launch_calls[1][4])

        changed_target = dict(moved, network_id=3)
        self.assertEqual(fresh, runtime._ballistic_solution(
            state, changed_target, descriptor, 0, 1.08))
        self.assertNotIn(11, runtime._artillery_intents)
        self.assertEqual([{'id': 11}], cancel_calls)

    def test_spg_pending_intent_invalidates_on_shell_seq_pose_and_deadline(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        cancelled = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=lambda *unused: None,
            artillery_launch_cancel=lambda source: cancelled.append(source))
        runtime.round_id = 10
        gun_state = self.module._BotGunState(descriptor)
        base_state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
        }
        solution = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }
        variants = (
            ('missing_target', lambda state, now: (
                state, 0, now, None)),
            ('dead_target', lambda state, now: (
                state, 0, now, dict(target, health=0))),
            ('shell', lambda state, now: (state, 1, now)),
            ('sequence', lambda state, now: (
                dict(state, fire_seq=1), 0, now)),
            ('pose', lambda state, now: (
                dict(state, x=0.051), 0, now)),
            ('deadline', lambda state, now: (
                state, 0, now + self.module.ARTILLERY_INTENT_SECONDS + 0.01)),
        )
        for name, mutate in variants:
            with self.subTest(name=name):
                state = dict(base_state)
                self.assertIsNone(runtime._artillery_launch_receipt(
                    state, target, descriptor, 0, gun_state,
                    solution, 1.0))
                values = mutate(state, 1.0)
                changed, shell_index, now = values[:3]
                current_target = values[3] if len(values) > 3 else target
                self.assertIsNone(runtime._active_artillery_intent(
                    changed, current_target, descriptor, shell_index, now))
                self.assertNotIn(11, runtime._artillery_intents)
                self.assertNotIn(11, runtime._artillery_reproofs)

        self.assertEqual(6, len(cancelled))

    def test_spg_pending_intent_clears_on_authority_change(self):
        cancelled = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_cancel=lambda source: cancelled.append(source))
        runtime.round_id = 5
        runtime.authority_id = 1
        runtime._artillery_intents[11] = {'source': {'id': 11}}
        runtime._artillery_reproofs[11] = {'source': {'id': 11}}
        handoff = dict(self.start, bot_authority_id=2)

        self.assertEqual([], runtime.battle_start(handoff))
        self.assertEqual({}, runtime._artillery_intents)
        self.assertEqual({}, runtime._artillery_reproofs)
        self.assertEqual([{'id': 11}], cancelled)

    def test_spg_intent_timeout_clears_and_allows_a_fresh_restart(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        cancelled = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=lambda *unused: None,
            artillery_launch_cancel=lambda source: cancelled.append(source))
        runtime.round_id = 12
        gun_state = self.module._BotGunState(descriptor)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
        }
        solution = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        expired_at = 1.0 + self.module.ARTILLERY_INTENT_SECONDS + 0.01
        self.assertIsNone(runtime._active_artillery_intent(
            state, target, descriptor, 0, expired_at))
        self.assertNotIn(11, runtime._artillery_reproofs)

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, expired_at))
        self.assertIn(11, runtime._artillery_intents)
        self.assertGreater(
            runtime._artillery_reproofs[11]['deadline'], expired_at)
        self.assertEqual([{'id': 11}], cancelled)

    def test_spg_reproof_attempts_never_extend_absolute_lifetime(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        cancelled = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=lambda *unused: None,
            artillery_launch_cancel=lambda source: cancelled.append(source))
        runtime.round_id = 12
        gun_state = self.module._BotGunState(descriptor)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
        }
        solution = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        reproof = runtime._artillery_reproofs[11]
        absolute = 1.0 + self.module.ARTILLERY_TOTAL_PROOF_SECONDS
        self.assertEqual(absolute, reproof['absolute_deadline'])

        reproof['attempts'] = 4
        for now in (30.0, 80.0, 120.0):
            runtime._artillery_intents.pop(11, None)
            self.assertIsNotNone(runtime._create_artillery_intent(
                state, target, descriptor, 0, gun_state, solution, now))
            self.assertLessEqual(reproof['deadline'], absolute)
        runtime._artillery_intents.pop(11, None)
        self.assertIsNone(runtime._active_artillery_reproof(
            state, target, descriptor, 0, absolute + 0.01))
        self.assertNotIn(11, runtime._artillery_reproofs)

    def test_spg_update_rejects_a_stale_frozen_receipt_without_firing(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 1000.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        strategic_calls = []
        launch_calls = []
        lane_calls = []
        muzzle = (3.0, 4.0, -2.0)

        def strategic(state, target, unused_descriptor,
                      unused_shell, unused_now):
            strategic_calls.append(target['position'])
            yaw = math.atan2(target['position'][0], target['position'][2])
            return {
                'aim_position': target['position'], 'yaw': yaw,
                'pitch': -0.1, 'flight_time': 0.5, 'arc': 'low',
            }

        def final_proof(state, target, unused_descriptor, shell_index,
                        fire_seq, shot_yaw, shot_pitch, flight_time, now):
            launch_calls.append((
                fire_seq, shot_yaw, shot_pitch, flight_time,
                target['position'], now))
            if len(launch_calls) < 5:
                return None
            speed = 1000.0
            velocity = (
                math.sin(shot_yaw) * math.cos(shot_pitch) * speed,
                math.sin(shot_pitch) * speed,
                math.cos(shot_yaw) * math.cos(shot_pitch) * speed,
            )
            return {
                'proof_key': ('exact', fire_seq),
                'fire_seq': fire_seq, 'shell_index': shell_index,
                'origin': muzzle, 'velocity': velocity,
                'shot_yaw': shot_yaw, 'shot_pitch': shot_pitch,
                'gravity': 10.0, 'max_distance': 5000.0,
                'max_time_ms': 20000, 'flight_time': flight_time,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: lane_calls.append(True) or True,
            ballistic_solution_probe=strategic,
            artillery_launch_probe=final_proof,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'turret_yaw': 0.0,
            'gun_pitch': -0.1, 'desired_gun_pitch': -0.1,
            'profile': {'class_tag': 'SPG'},
        })
        runtime._gun_states[11].elapsed = 1.0

        publication = None
        for frame in range(5):
            publication = runtime.update(0.04, 1.0 + frame * 0.04, players=[{
                'id': 2, 'team': 1, 'alive': True,
                'x': float(frame) * 8.0, 'y': 0.0, 'z': 100.0,
                'yaw': math.pi * 0.5, 'speed': 8.0,
            }])[0]['bots'][0]

        self.assertEqual(0, publication['fire_seq'])
        self.assertEqual(1, len(strategic_calls))
        self.assertEqual(1, len(lane_calls))
        frozen = [call[0:4] for call in launch_calls]
        self.assertTrue(all(value == frozen[0] for value in frozen))
        self.assertNotIn('shot_origin', publication)
        self.assertEqual(1, runtime._artillery_reproofs[11]['attempts'])
        self.assertGreater(
            runtime._artillery_reproofs[11]['last_impact_error'],
            self.module.ARTILLERY_IMPACT_ERROR_METRES)

    def test_moving_spg_reproofs_converge_at_20_and_24_fps(self):
        from gui.mods.offline_lan_0922.artillery_controller import (
            ArtilleryController)

        for fps in (20, 24):
            for count in (1, 2):
                with self.subTest(fps=fps, count=count):
                    controller = ArtilleryController(maximum_step=0.12)
                    descriptor = _combat_descriptor(dispersion=0.03)
                    descriptor.gun.shots = ({
                        'shell': {'effectsIndex': 0},
                        'speed': 425.0, 'gravity': 143.0,
                        'maxDistance': 10000.0,
                    },)
                    descriptor.gun.pitchLimits = {
                        'absolute': (-0.8, 0.15)}

                    def strategic(source, target, installed, shell, now):
                        return controller.solution(
                            source, target, installed, shell, now)

                    def exact_launch(
                            source, target, installed, shell, fire_seq,
                            shot_yaw, shot_pitch, flight_time, now):
                        origin = (
                            source['x'] + 0.3, source['y'] + 1.7,
                            source['z'] - 0.2)
                        unused_ready, receipt = controller.request_launch(
                            source, target, installed, shell, fire_seq,
                            origin, shot_yaw, shot_pitch, flight_time, now)
                        return receipt

                    runtime = self.module.BotRuntime(
                        1, ballistic_solution_probe=strategic,
                        artillery_launch_probe=exact_launch,
                        artillery_launch_cancel=controller.cancel_launch)
                    runtime.round_id = 77
                    states = []
                    guns = []
                    for index in range(count):
                        states.append({
                            'id': 11 + index,
                            'x': float(index), 'y': 0.0, 'z': 0.0,
                            'yaw': 0.0, 'speed': 0.0, 'fire_seq': 0,
                            'aim_yaw': 0.0, 'gun_pitch': 0.0,
                            'gun_aligned': True, 'critical': {},
                            'profile': {'class_tag': 'SPG'},
                        })
                        gun = self.module._BotGunState(descriptor)
                        gun.elapsed = 100.0
                        guns.append(gun)

                    fired = {}
                    rejected = dict((state['id'], 0) for state in states)
                    for frame in range(1, fps * 5 + 1):
                        now = frame / float(fps)
                        probe_calls = [0]

                        def clear_probe(unused_start, unused_end):
                            probe_calls[0] += 1
                            return None

                        used = controller.advance(now, 4, clear_probe)
                        self.assertEqual(probe_calls[0], used)
                        self.assertLessEqual(used, 4)
                        for index, (state, gun) in enumerate(
                                zip(states, guns)):
                            if state['id'] in fired:
                                continue
                            target = {
                                'kind': 'human',
                                'network_id': 2 + index,
                                'alive': True,
                                'position': (8.0 * now, 0.0, 560.0),
                                'yaw': math.pi * 0.5, 'speed': 8.0,
                            }
                            solution = runtime._ballistic_solution(
                                state, target, descriptor, 0, now)
                            if solution is None:
                                continue
                            state['aim_yaw'] = solution['yaw']
                            state['gun_pitch'] = solution['pitch']
                            state['gun_aligned'] = True
                            before = runtime._artillery_reproofs.get(
                                state['id'], {}).get('attempts', 0)
                            receipt = runtime._artillery_launch_receipt(
                                state, target, descriptor, 0, gun,
                                solution, now)
                            after = runtime._artillery_reproofs.get(
                                state['id'], {}).get('attempts', 0)
                            if after > before:
                                rejected[state['id']] += 1
                            self.assertEqual(0, state['fire_seq'])
                            if receipt is None:
                                continue
                            impact_error = (
                                runtime._artillery_receipt_impact_error(
                                    receipt, target)[0])
                            self.assertLessEqual(
                                impact_error,
                                self.module.ARTILLERY_IMPACT_ERROR_METRES)
                            self.assertTrue(runtime._fire(
                                state, gun, 1.0, descriptor,
                                launch_receipt=receipt))
                            self.assertEqual(
                                receipt['origin'], state['shot_origin'])
                            self.assertEqual(
                                receipt['velocity'], state['shot_velocity'])
                            fired[state['id']] = (now, impact_error, after)
                            runtime._cancel_artillery_intent(state['id'])
                        if len(fired) == count:
                            break

                    self.assertEqual(count, len(fired))
                    self.assertLessEqual(now, 5.0)
                    self.assertTrue(all(
                        rejected[state['id']] >= 1
                        for state in states))

    def _run_catalog_max_spg_proof_case(self, fps, direction):
        from gui.mods.offline_lan_0922.artillery_controller import (
            ArtilleryController)

        controller = ArtilleryController(maximum_step=0.12)
        descriptor = _combat_descriptor(dispersion=0.0001)
        # The pinned #1513 non-secret SPG catalog has shell speeds 265..510,
        # gravity 125..190 and maxDistance 10000.  This FV3805/FV206 5.5-inch
        # shell takes about 5.66 seconds on the flat high arc used here.  The
        # moving contact uses the catalogue's 79 km/h maximum plus the copied
        # 1.05 downhill overspeed, about 23.04 m/s.  The old speed=100,
        # gravity=1, 19-second fixture is not assignable by this client.
        descriptor.gun.shots = ({
            'shell': {'effectsIndex': 0},
            'speed': 440.0, 'gravity': 146.0,
            'maxDistance': 10000.0,
        },)
        descriptor.gun.pitchLimits = {
            'absolute': (-math.radians(70.0), math.radians(5.0))}

        strategic_calls = {}

        def strategic(source, target, installed, shell, now):
            source_id = int(source['id'])
            strategic_calls[source_id] = (
                strategic_calls.get(source_id, 0) + 1)
            return controller.solution(
                source, target, installed, shell, now)

        def exact_launch(
                source, target, installed, shell, fire_seq,
                shot_yaw, shot_pitch, flight_time, now):
            unused_ready, receipt = controller.request_launch(
                source, target, installed, shell, fire_seq,
                (source['x'], source['y'] + 1.5, source['z']),
                shot_yaw, shot_pitch, flight_time, now)
            return receipt

        runtime = self.module.BotRuntime(
            1, ballistic_solution_probe=strategic,
            artillery_launch_probe=exact_launch,
            artillery_launch_cancel=controller.cancel_launch)
        runtime.round_id = 5
        states = []
        guns = []
        for index in range(8):
            states.append({
                'id': 11 + index,
                'x': float(index), 'y': 0.0, 'z': 0.0,
                'yaw': 0.0, 'speed': 0.0, 'fire_seq': 0,
                'aim_yaw': 0.0, 'gun_pitch': 0.0,
                'gun_aligned': True, 'critical': {},
                'profile': {'class_tag': 'SPG'},
            })
            gun = self.module._BotGunState(descriptor)
            gun.elapsed = 100.0
            guns.append(gun)

        fired = {}
        rejected = dict((state['id'], 0) for state in states)
        strategic_calls_at_first_reproof = {}
        catalog_max_speed = direction * 79.0 / 3.6 * 1.05
        for frame in range(1, fps * 20 + 1):
            now = frame / float(fps)
            probe_calls = [0]

            def low_arc_wall(start, end):
                probe_calls[0] += 1
                wall_z = 400.0
                if ((start[2] - wall_z) * (end[2] - wall_z) <= 0.0 and
                        abs(end[2] - start[2]) > 1e-9):
                    fraction = ((wall_z - start[2]) /
                                (end[2] - start[2]))
                    height = start[1] + (end[1] - start[1]) * fraction
                    if height < 300.0:
                        return (0.0, height, wall_z)
                return None

            used = controller.advance(now, 4, low_arc_wall)
            self.assertEqual(probe_calls[0], used)
            self.assertLessEqual(used, 4)
            for index, (state, gun) in enumerate(zip(states, guns)):
                if state['id'] in fired:
                    continue
                target = {
                    'kind': 'human', 'network_id': 2 + index,
                    'alive': True,
                    'position': (catalog_max_speed * now, 0.0, 853.0),
                    'yaw': direction * math.pi * 0.5,
                    'speed': abs(catalog_max_speed),
                }
                solution = runtime._ballistic_solution(
                    state, target, descriptor, 0, now)
                if solution is None:
                    continue
                self.assertEqual('high', solution['arc'])
                self.assertGreater(solution['flight_time'], 5.0)
                self.assertLess(solution['flight_time'], 6.0)
                state['aim_yaw'] = solution['yaw']
                state['gun_pitch'] = solution['pitch']
                state['gun_aligned'] = True
                before = runtime._artillery_reproofs.get(
                    state['id'], {}).get('attempts', 0)
                receipt = runtime._artillery_launch_receipt(
                    state, target, descriptor, 0, gun, solution, now)
                after = runtime._artillery_reproofs.get(
                    state['id'], {}).get('attempts', 0)
                if after > before:
                    rejected[state['id']] += 1
                    if before == 0:
                        strategic_calls_at_first_reproof[state['id']] = (
                            strategic_calls[state['id']])
                self.assertEqual(0, state['fire_seq'])
                if receipt is None:
                    continue
                impact_error = runtime._artillery_receipt_impact_error(
                    receipt, target)[0]
                self.assertLessEqual(
                    impact_error,
                    self.module.ARTILLERY_IMPACT_ERROR_METRES)
                self.assertTrue(runtime._fire(
                    state, gun, 1.0, descriptor,
                    launch_receipt=receipt))
                self.assertEqual('exact_launch', receipt['arc'])
                fired[state['id']] = (now, impact_error, after)
                runtime._cancel_artillery_intent(state['id'])
            if len(fired) == 8:
                break

        self.assertEqual(8, len(fired))
        self.assertLessEqual(now, 20.0)
        self.assertTrue(all(value >= 1 for value in rejected.values()))
        self.assertEqual(
            strategic_calls_at_first_reproof, strategic_calls)
        self.assertEqual({}, runtime._artillery_intents)
        self.assertEqual({}, runtime._artillery_reproofs)
        return fired, rejected

    def test_eight_catalog_max_flight_reproofs_finish_with_shared_budget(self):
        fired, rejected = self._run_catalog_max_spg_proof_case(24, 1.0)

        self.assertEqual(8, len(fired))
        self.assertTrue(all(value >= 1 for value in rejected.values()))

    def test_catalog_max_spg_pinned_receipts_converge_across_frame_rates(self):
        for fps in (20, 30, 60):
            for direction in (-1.0, 1.0):
                with self.subTest(fps=fps, direction=direction):
                    fired, rejected = self._run_catalog_max_spg_proof_case(
                        fps, direction)
                    self.assertEqual(8, len(fired))
                    self.assertTrue(all(
                        value >= 1 for value in rejected.values()))

    def test_installed_gun_dispersion_and_critical_factors_set_exact_sigma(self):
        descriptor = _combat_descriptor(dispersion=0.012)
        gun_state = self.module._BotGunState(descriptor)
        critical = {
            'devices': [{
                'name': 'gunHealth', 'hp': 10.0, 'max_hp': 54.0,
                'state': 'critical',
            }],
            'destroyed': [], 'crew_ko': ['gunner1'],
        }
        state = {
            'id': 11, 'fire_seq': 0, 'aim_yaw': 0.4,
            'gun_pitch': -0.1, 'critical': critical,
        }

        # Installed 0.012 rad x gunner 2.0 x damaged gun 2.0 = 0.048.
        self.assertAlmostEqual(0.012, gun_state.fully_aimed_dispersion)
        self.assertAlmostEqual(
            0.048, self.module._effective_shot_dispersion(
                gun_state, state, descriptor))

        sigmas = []

        class RecordingRandom(object):
            def __init__(self, unused_seed):
                pass

            def gauss(self, mean, sigma):
                sigmas.append((mean, sigma))
                return 0.0

        original_random = self.module.random.Random
        self.module.random.Random = RecordingRandom
        gun_state.elapsed = 10.0
        runtime = self.module.BotRuntime(1)
        runtime.round_id = 5
        try:
            self.assertTrue(runtime._fire(
                state, gun_state, 1.0, descriptor))
        finally:
            self.module.random.Random = original_random

        self.assertEqual(3, len(sigmas))
        for mean, sigma in sigmas:
            self.assertEqual(0.0, mean)
            self.assertAlmostEqual(0.016, sigma)
        self.assertAlmostEqual(0.4, state['shot_yaw'])
        self.assertAlmostEqual(0.1, state['shot_pitch'])

    def test_firing_lane_probe_failure_is_not_hidden_as_blocked_los(self):
        def broken_lane(unused_source, unused_target):
            raise RuntimeError('native lane probe failed')

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=broken_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)

        with self.assertRaisesRegex(RuntimeError, 'native lane probe failed'):
            runtime.update(.04, 1.0, players=[{
                'id': 2, 'team': 1, 'alive': True,
                'x': 0.0, 'y': 0.0, 'z': 100.0,
            }])

    def test_missing_or_nonpositive_installed_gun_dispersion_is_rejected(self):
        missing = _combat_descriptor()
        del missing.gun.shotDispersionAngle
        with self.assertRaisesRegex(
                ValueError, 'shotDispersionAngle is unavailable'):
            self.module._BotGunState(missing)

        zero = _combat_descriptor(dispersion=0.0)
        with self.assertRaisesRegex(
                ValueError, 'shotDispersionAngle must be positive'):
            self.module._BotGunState(zero)

    def test_cached_server_order_tracks_current_visible_target_pose(self):
        stale = (-40.0, 1.0, 80.0)
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'advance_contact',
            'aim_position': stale, 'face_position': stale,
            'move_position': stale,
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        adapter = _FixedAdapter(command)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.states[11].update(
            x=0.0, y=0.0, z=0.0, yaw=0.0, aim_yaw=0.0)
        runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'target_kind': 'human', 'target_id': 2,
                'fire_allowed': True, 'shell_index': 0,
                'fire_range': 500.0, 'combat_mode': 'advance_contact',
                'aim_position': stale, 'face_position': stale,
                'move_position': stale,
            }],
            'bots': [],
        })

        seen_commands = []
        original_update_aim = runtime._update_gun_aim

        def record_update_aim(state, live_command, target, step):
            seen_commands.append(dict(live_command))
            return original_update_aim(state, live_command, target, step)

        runtime._update_gun_aim = record_update_aim
        first_pose = (0.0, 1.0, 100.0)
        moved_pose = (80.0, 2.0, 20.0)
        try:
            runtime.update(.04, 1.0, players=[{
                'id': 2, 'team': 1, 'alive': True,
                'x': first_pose[0], 'y': first_pose[1], 'z': first_pose[2],
            }])
            runtime.update(.04, 1.04, players=[{
                'id': 2, 'team': 1, 'alive': True,
                'x': moved_pose[0], 'y': moved_pose[1], 'z': moved_pose[2],
            }])
        finally:
            runtime._update_gun_aim = original_update_aim

        # The second frame reuses the cached decision but not its stale pose.
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(first_pose, adapter.server_orders[0]['aim_position'])
        self.assertEqual(moved_pose, seen_commands[-1]['aim_position'])
        self.assertEqual(moved_pose, seen_commands[-1]['face_position'])
        self.assertEqual(moved_pose, seen_commands[-1]['move_position'])
        self.assertGreater(runtime.states[11]['aim_yaw'], 0.3)

        missing = self.module._overlay_live_target_pose(command, None)
        team_spotted = self.module._overlay_live_target_pose(
            command, {'alive': True, 'visible': False,
                      'position': moved_pose})
        self.assertFalse(missing['fire_allowed'])
        self.assertTrue(team_spotted['fire_allowed'])
        self.assertEqual(moved_pose, team_spotted['aim_position'])
        self.assertEqual(stale, missing['aim_position'])

        with self.assertRaisesRegex(
                ValueError, 'alive flag is invalid'):
            self.module._overlay_live_target_pose(command, {
                'visible': True, 'position': moved_pose})
        with self.assertRaisesRegex(
                ValueError, 'position is unavailable'):
            self.module._overlay_live_target_pose(command, {
                'alive': True, 'visible': True})
        with self.assertRaisesRegex(
                ValueError, 'position must be finite'):
            self.module._overlay_live_target_pose(command, {
                'alive': True, 'visible': True,
                'position': (0.0, float('nan'), 1.0)})

    def test_cached_selected_bot_refreshes_only_live_pose_and_death(self):
        stale = (0.0, 1.0, 100.0)
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True, 'target_id': 12,
            'fire_range': 500.0, 'combat_mode': 'advance_contact',
            'aim_position': stale, 'face_position': stale,
            'move_position': stale,
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        adapter = _FixedAdapter(command)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.01, clip=(1,)),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Shooter'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Target'},
        ]))
        runtime.states[11].update(
            x=0.0, y=0.0, z=0.0, yaw=0.0, aim_yaw=0.0)
        runtime.states[12].update(
            x=stale[0], y=stale[1], z=stale[2], yaw=0.0,
            health=900, max_health=900, alive=True)
        # Keep all three frames outside both observation and lane-refresh
        # windows so only the selected target may be copied.
        runtime._next_observation = 100.0
        full_refreshes = []
        original_refresh = runtime._refresh_target_poses

        def record_full_refresh(*args, **kwargs):
            full_refreshes.append(1)
            return original_refresh(*args, **kwargs)

        seen = []
        original_update_aim = runtime._update_gun_aim

        def record_update_aim(state, live_command, target, step):
            if state['id'] == 11:
                seen.append((dict(live_command), dict(target)))
            return original_update_aim(state, live_command, target, step)

        runtime._refresh_target_poses = record_full_refresh
        runtime._update_gun_aim = record_update_aim
        try:
            runtime.update(.04, 1.0)
            cached_target = runtime._decision_cache[11][5][12]
            cached_snapshot = dict(cached_target)

            moved = (60.0, 2.0, 80.0)
            runtime.states[12].update(
                x=moved[0], y=moved[1], z=moved[2], health=321)
            runtime.update(.04, 1.04)
            fire_before_death = runtime.states[11]['fire_seq']

            runtime.states[12].update(alive=False, health=0)
            runtime.update(.04, 1.08)
        finally:
            runtime._refresh_target_poses = original_refresh
            runtime._update_gun_aim = original_update_aim

        self.assertEqual([], full_refreshes)
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual(moved, seen[1][0]['aim_position'])
        self.assertEqual(moved, seen[1][0]['face_position'])
        self.assertEqual(moved, seen[1][0]['move_position'])
        self.assertEqual(321, seen[1][1]['health'])
        self.assertFalse(seen[2][1]['alive'])
        self.assertEqual(0, seen[2][1]['health'])
        self.assertFalse(seen[2][0]['fire_allowed'])
        self.assertEqual(fire_before_death, runtime.states[11]['fire_seq'])
        self.assertIs(cached_target, runtime._decision_cache[11][5][12])
        self.assertEqual(cached_snapshot, cached_target)

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
        player = {'id': 2, 'team': 1, 'alive': True,
                  'x': 0.0, 'y': 0.5, 'z': 100.0}

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
        manifest_message = runtime.battle_start(self.start)[0]
        server = BattleState(map_name='01_karelia')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        server.round_id = self.start['round_id']
        server.players[1] = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        server.bot_authority_id = 1
        server.bot_roster = list(self.start['bots'])
        self.assertTrue(server.update_bot_manifest(1, {
            'round_id': server.round_id,
            'bots': manifest_message['bots'],
        }))
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
        # Mirror the external hit as the server's canonical combat base.  The
        # next full-state wire must be accepted directly against this ack=0
        # state; accepting only a locally inspected sequence number would not
        # protect the complete #1513 reconciliation contract.
        server.bot_states[11].update(
            health=800, display_health=800, alive=True,
            critical=dict(burning), combat_revision=2,
            combat_base_revision=2, combat_ack_seq=0,
            combat_fire_elapsed=0.0, combat_fire_timer=0.0)

        self.assertEqual(750, runtime.states[11]['health'])
        self.assertEqual((2, 0), (
            runtime.states[11]['combat_base_revision'],
            runtime.states[11]['combat_seq']))
        sync = runtime._combat_sync[11]
        self.assertEqual([], sync['pending'])
        self.assertEqual(5, len(sync['unpublished_steps']))

        next_publication = runtime.update(.20, 1.20)[0]['bots'][0]

        # The five replayed slices plus this render slice's fire-clock advance
        # are one full-state publication on the new base.  The current slice
        # does not cross another one-second damage boundary, so the replayed
        # 50 health loss remains exact.  No invisible replay proposal may
        # consume sequence 1 and make the wire jump directly to sequence 2.
        self.assertEqual(750, next_publication['health'])
        self.assertAlmostEqual(
            1.2, next_publication['combat_fire_elapsed'])
        self.assertAlmostEqual(.2, next_publication['combat_fire_timer'])
        self.assertEqual(1, next_publication['combat_seq'])
        self.assertEqual([1], [entry['seq'] for entry in sync['pending']])
        self.assertEqual([], sync['unpublished_steps'])

        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id,
            'bots': [next_publication],
        }))
        self.assertEqual(1, server.bot_states[11]['combat_ack_seq'])

        # Acknowledged snapshots and later fire-clock publications stay
        # contiguous too; the fix must not merely make the first rebase packet
        # acceptable and then reopen the same gap on the following tick.
        for index in range(4):
            runtime.apply_snapshot({
                'server_tick': 3 + index,
                'bots': [dict(server.bot_states[11])],
            })
            server_ack = server.bot_states[11]['combat_ack_seq']
            outgoing = runtime.update(.20, 1.40 + index * .20)
            publication_message = next(
                message for message in outgoing
                if message['type'] == 'bot_state')
            published = publication_message['bots'][0]
            self.assertEqual(server_ack + 1, published['combat_seq'])
            self.assertTrue(server.update_bot_states(1, {
                'round_id': server.round_id,
                'bots': publication_message['bots'],
            }))
            self.assertEqual(
                published['combat_seq'],
                server.bot_states[11]['combat_ack_seq'])
        self.assertEqual(700, server.bot_states[11]['health'])

    def test_external_base_replay_waits_for_wire_before_reserving_sequence(self):
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

        first = runtime.update(.20, .20)[0]['bots'][0]
        self.assertEqual(1, first['combat_seq'])
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=900, critical=burning,
                revision=2, base_revision=2, ack_seq=0)]})

        sync = runtime._combat_sync[11]
        self.assertEqual(0, sync['next_seq'])
        self.assertEqual([], sync['pending'])
        self.assertEqual(1, len(sync['unpublished_steps']))
        self.assertEqual(0, runtime.states[11]['combat_seq'])

        publication = runtime.update(.20, .40)[0]['bots'][0]

        self.assertEqual(1, publication['combat_seq'])
        self.assertEqual(1, sync['next_seq'])
        self.assertEqual([1], [entry['seq'] for entry in sync['pending']])
        self.assertEqual([], sync['unpublished_steps'])

    def test_second_external_base_replays_unpublished_lineage_without_gap(self):
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
        runtime.update(.20, .20)
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=900, critical=burning,
                revision=2, base_revision=2, ack_seq=0)]})
        first_replay = list(runtime._combat_sync[11]['unpublished_steps'])

        runtime.apply_snapshot({
            'server_tick': 3,
            'bots': [_snapshot_bot(
                health=850, critical=burning,
                revision=3, base_revision=3, ack_seq=0)]})

        sync = runtime._combat_sync[11]
        self.assertEqual(first_replay, sync['unpublished_steps'])
        self.assertEqual(0, sync['next_seq'])
        self.assertEqual([], sync['pending'])
        publication = runtime.update(.20, .40)[0]['bots'][0]
        self.assertEqual(1, publication['combat_seq'])

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
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *args: _Adapter(*args),
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
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
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
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
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
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
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
            {'id': 2, 'team': 1, 'alive': True,
             'x': 4, 'y': 0, 'z': 4}])
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
            {'id': 2, 'team': 1, 'alive': True,
             'x': 5, 'y': 0, 'z': 5}])

        self.runtime.apply_snapshot({'bots': [
            _snapshot_bot(health=0, alive=False,
                          revision=1, base_revision=1)]})

        self.assertFalse(self.runtime.states[11]['alive'])
        self.assertEqual(0.0, self.runtime.states[11]['speed'])
        final = self.runtime.update(.1, 2.0, players=[
            {'id': 2, 'team': 1, 'alive': True,
             'x': 5, 'y': 0, 'z': 5}])
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
        players = [{'id': 2, 'team': 1, 'alive': True,
                    'x': state['x'] + 100,
                    'y': state['y'], 'z': state['z']}]

        self.runtime.update(.04, 1.0, players=players)
        self.runtime.update(.04, 1.04, players=players)

        self.assertEqual([(11, 2)], calls)

    def test_visibility_upper_bound_skips_only_impossible_native_probes(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.30, 0.40))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        probes = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            visibility_probe=lambda source, target, fired=False: (
                probes.append(target['network_id']) or True))
        source = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }

        targets = [
            {'id': self.module.HUMAN_TARGET_ID_BASE + target_id,
             'kind': 'human', 'network_id': target_id,
             'vehicle': 'ussr:R11_MS-1',
             'position': (0.0, 0.0, distance),
             'speed': 0.0, 'fire_seq': 0}
            for target_id, distance in ((2, 300.0), (3, 400.0),
                                        (4, 325.0))
        ]

        visible = [runtime._visible(source, target, 1.0)
                   for target in targets]

        self.assertEqual([True, False, True], visible)
        # The baseline probe order is 2, 3, 4. The fast path removes only the
        # impossible middle ray and leaves the remaining order unchanged.
        self.assertEqual([2, 4], probes)
        self.assertEqual(2, runtime.probe_totals()[0])

    def test_visibility_upper_bound_retains_24_fps_cache_and_shot_refresh(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.30, 0.40))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        probes = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            visibility_probe=lambda source, target, fired=False: (
                probes.append((target['network_id'], bool(fired))) or True))
        source = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        target = {
            'id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'kind': 'human', 'network_id': 2,
            'vehicle': 'ussr:R11_MS-1',
            'position': (0.0, 0.0, 400.0),
            'speed': 0.0, 'fire_seq': 0,
        }

        for frame in range(12):
            self.assertFalse(runtime._visible(
                source, target, 1.0 + frame / 24.0))
        self.assertEqual([], probes)
        target['fire_seq'] = 1
        self.assertTrue(runtime._visible(source, target, 1.5))
        self.assertTrue(runtime._visible(source, target, 1.5 + 1.0 / 24.0))
        self.assertEqual([(2, True)], probes)

    def test_visibility_upper_bound_preserves_descriptor_failure_fallback(self):
        descriptor_calls = []
        probes = []

        def descriptor_resolver(vehicle_name):
            descriptor_calls.append(vehicle_name)
            raise RuntimeError('descriptor unavailable')

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=descriptor_resolver,
            visibility_probe=lambda source, target, fired=False: (
                probes.append(target['network_id']) or True))
        source = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        target = {
            'id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'kind': 'human', 'network_id': 2,
            'vehicle': 'missing:vehicle',
            'position': (0.0, 0.0, 400.0),
            'speed': 0.0, 'fire_seq': 0,
        }

        self.assertTrue(runtime._visible(source, target, 1.0))
        self.assertEqual(['missing:vehicle'], descriptor_calls)
        self.assertEqual([2], probes)

    def test_bot_spotting_applies_target_camouflage_and_shot_penalty(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.30, 0.40))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        source = runtime.states[11]
        source.update(x=0.0, y=0.0, z=0.0)
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'vehicle': 'ussr:R11_MS-1',
            'x': 0.0, 'y': 0.0, 'z': 400.0,
            'speed': 0.0, 'fire_seq': 0,
        }

        contacts, unused_lookup = runtime._contacts_for(
            source, [player], 1.0)
        self.assertFalse(contacts[0]['visible'])

        player['fire_seq'] = 1
        contacts, unused_lookup = runtime._contacts_for(
            source, [player], 1.01)
        self.assertTrue(contacts[0]['visible'])

        contacts, unused_lookup = runtime._contacts_for(
            source, [player], 1.80)
        self.assertFalse(contacts[0]['visible'])

        player.update(z=365.0, speed=10.0)
        contacts, unused_lookup = runtime._contacts_for(
            source, [player], 2.10)
        self.assertTrue(contacts[0]['visible'])

    def test_shot_camouflage_invalidates_every_observer_cache(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.30, 0.40))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        probes = []

        def visibility(source, target, fired_recently=False):
            probes.append((source['id'], target['network_id'],
                           bool(fired_recently)))
            return True

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            visibility_probe=visibility)
        sources = [
            {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
             'view_range': 445.0},
            {'id': 12, 'x': 0.0, 'y': 0.0, 'z': 0.0,
             'view_range': 445.0},
        ]
        target = {
            'id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'kind': 'human', 'network_id': 2,
            'vehicle': 'ussr:R11_MS-1',
            'position': (0.0, 0.0, 400.0),
            'speed': 0.0, 'fire_seq': 0,
        }

        self.assertEqual(
            [False, False],
            [runtime._visible(source, target, 1.0) for source in sources])
        target['fire_seq'] = 1
        self.assertEqual(
            [True, True],
            [runtime._visible(source, target, 1.01) for source in sources])
        # The stationary target is mathematically undetectable before firing,
        # so those two native rays are skipped. The fire-sequence change still
        # invalidates both observer caches and admits the same two shot probes.
        self.assertEqual([(11, 2, True), (12, 2, True)], probes)

        self.assertEqual(
            [True, True],
            [runtime._visible(source, target, 1.02) for source in sources])
        self.assertEqual(2, len(probes))

    def test_bot_spotting_applies_foliage_without_breaking_proximity(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.0, 0.0))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        calls = []

        def visibility(unused_source, target, fired_recently=False):
            target_id = target['network_id']
            calls.append((target_id, bool(fired_recently)))
            foliage_bonus = (
                0.60 if target_id == 3 and not fired_recently else 0.0)
            return {
                'line_of_sight': target_id != 4,
                'foliage_bonus': foliage_bonus,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=visibility,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        source = runtime.states[11]
        source.update(x=0.0, y=0.0, z=0.0)
        players = [
            {'id': 2, 'team': 1, 'alive': True,
             'vehicle': 'ussr:R11_MS-1',
             'x': 0.0, 'y': 0.0, 'z': 300.0,
             'speed': 0.0, 'fire_seq': 0},
            {'id': 3, 'team': 1, 'alive': True,
             'vehicle': 'ussr:R11_MS-1',
             'x': 0.0, 'y': 0.0, 'z': 300.0,
             'speed': 0.0, 'fire_seq': 0},
            {'id': 4, 'team': 1, 'alive': True,
             'vehicle': 'ussr:R11_MS-1',
             'x': 0.0, 'y': 0.0, 'z': 40.0,
             'speed': 0.0, 'fire_seq': 0},
        ]

        contacts, unused_lookup = runtime._contacts_for(
            source, players, 1.0)
        visible = dict((contact['network_id'], contact['visible'])
                       for contact in contacts)
        self.assertEqual({2: True, 3: False, 4: True}, visible)
        self.assertFalse(any(target_id == 4
                             for target_id, unused_fired in calls))

        players[1]['fire_seq'] = 1
        contacts, unused_lookup = runtime._contacts_for(
            source, players, 1.01)
        visible = dict((contact['network_id'], contact['visible'])
                       for contact in contacts)
        self.assertTrue(visible[3])
        self.assertIn((3, True), calls)

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

    def test_server_order_revision_invalidates_only_semantically_changed_bot(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter({
                'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
                'shell_index': 0, 'fire_allowed': False,
                'target_id': None, 'fire_range': 500.0,
                'combat_mode': 'route',
                'aim_position': (0.0, 0.0, 100.0),
                'face_position': (0.0, 0.0, 100.0),
                'move_position': (0.0, 0.0, 100.0),
                'recovery_mode': 'drive', 'movement_intent': True,
            }),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Changed'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Moving target'},
        ]
        runtime.battle_start(dict(self.start, bots=roster))

        def order(bot_id, mode='advance_contact', aim=(0.0, 1.0, 80.0)):
            return {
                'id': bot_id, 'target_kind': 'human', 'target_id': 2,
                'fire_allowed': True, 'shell_index': 0,
                'fire_range': 500.0, 'combat_mode': mode,
                'aim_position': aim, 'face_position': aim,
                'move_position': aim,
            }

        self.assertTrue(runtime._apply_orders({
            'bot_order_revision': 1,
            'bot_orders': [order(11), order(12)],
        }))
        player = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 80.0,
        }
        runtime.update(.04, 1.0, players=[player])
        self.assertEqual({11, 12}, set(runtime._decision_cache))
        self.assertEqual({11, 12}, set(runtime._motion_probe_cache))
        token_11 = runtime._server_order_tokens[11]
        token_12 = runtime._server_order_tokens[12]

        moved = (20.0, 1.0, 70.0)
        self.assertTrue(runtime._apply_orders({
            'bot_order_revision': 2,
            'bot_orders': [order(11, mode='engage'), order(12, aim=moved)],
        }))

        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertIn(12, runtime._decision_cache)
        self.assertIn(12, runtime._motion_probe_cache)
        self.assertEqual(token_11 + 1, runtime._server_order_tokens[11])
        self.assertEqual(token_12, runtime._server_order_tokens[12])
        self.assertEqual(moved, runtime._server_orders[12]['aim_position'])

    def test_distant_firing_lane_is_ready_without_native_probe_or_budget(self):
        calls = []
        runtime = self.module.BotRuntime(
            1, firing_lane_probe=lambda *unused: calls.append(1) or True)
        source = {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {
            'id': 12, 'network_id': 12, 'kind': 'bot',
            'x': 0.0, 'y': 0.0,
            'z': self.module.SHOT_LANE_QUERY_DISTANCE + 1.0,
        }
        budget = [7]

        self.assertFalse(runtime._shot_clear(
            source, target, 1.0, force=True, probe_budget=budget))
        self.assertEqual([], calls)
        self.assertEqual([7], budget)
        self.assertEqual(
            (1.0, False),
            runtime._shot_los_cache[(11, 'bot', 12)])

        target['z'] = self.module.SHOT_LANE_QUERY_DISTANCE
        self.assertTrue(runtime._shot_clear(
            source, target, 1.1, force=True, probe_budget=budget))
        self.assertEqual([1], calls)
        self.assertEqual([6], budget)

    def test_selected_target_keeps_the_independent_lane_freshness_gate(self):
        calls = []
        runtime = self.module.BotRuntime(
            1, firing_lane_probe=lambda *unused: calls.append(1) or True)
        source = {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {
            'id': 12, 'network_id': 12, 'kind': 'bot',
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }

        self.assertTrue(runtime._shot_clear(source, target, 1.0))
        self.assertTrue(runtime._shot_clear(source, target, 1.2))
        self.assertEqual(1, len(calls))
        self.assertTrue(runtime._shot_clear(source, target, 1.200001))
        self.assertEqual(2, len(calls))

    def test_render_frame_reuses_probe_geometry_by_target_pose_phase(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'B'},
            {'id': 13, 'team': 2, 'slot': 0, 'name': 'C'},
        ]))
        player = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 20.0, 'y': 0.0, 'z': 30.0,
            'health': 800, 'max_health': 900,
        }
        probe_geometries = []
        original_probe_pose = runtime._probe_target_pose

        def record_probe_pose(planner_id, cached, live_players,
                              probe_targets, processed_bot_ids):
            result = original_probe_pose(
                planner_id, cached, live_players,
                probe_targets, processed_bot_ids)
            probe_geometries.append((
                cached.get('kind'), cached.get('network_id'), id(result)))
            return result

        runtime._probe_target_pose = record_probe_pose
        try:
            runtime.update(.04, 1.0, players=[player])
        finally:
            runtime._probe_target_pose = original_probe_pose

        human_probe_ids = [value[2] for value in probe_geometries
                           if value[:2] == ('human', 2)]
        bot_probe_ids = [value[2] for value in probe_geometries
                         if value[:2] == ('bot', 13)]
        self.assertGreaterEqual(len(human_probe_ids), 2)
        self.assertEqual(1, len(set(human_probe_ids)))
        self.assertGreaterEqual(len(bot_probe_ids), 2)
        self.assertEqual(1, len(set(bot_probe_ids)))

    def test_due_observation_builds_one_lane_key_per_pair(self):
        lane_calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda source, target: lane_calls.append(
                (source['id'], target['network_id'])) or True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'B'},
        ]))
        key_calls = []
        original_key = runtime._shot_los_key

        def counted_key(source, target):
            key_calls.append((source['id'], target['network_id']))
            return original_key(source, target)

        runtime._shot_los_key = counted_key
        outgoing = runtime.update(.04, 1.0)

        self.assertEqual([(11, 12), (12, 11)], key_calls)
        self.assertEqual(key_calls, lane_calls)
        observation = next(message for message in outgoing
                           if message['type'] == 'bot_observation')
        self.assertEqual(2, len(observation['contacts']))

    def test_due_observation_deduplicates_pair_serialisation(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'B'},
            {'id': 13, 'team': 2, 'slot': 0, 'name': 'C'},
        ]))
        calls = []

        class CountingDict(dict):
            def get(self, name, default=None):
                if name == 'profile':
                    calls.append(name)
                return dict.get(self, name, default)

        original_refresh = runtime._refresh_target_pose

        def counted_refresh(planner_id, cached, live_players):
            return CountingDict(original_refresh(
                planner_id, cached, live_players))

        runtime._refresh_target_pose = counted_refresh
        outgoing = runtime.update(.04, 1.0)

        observation = next(message for message in outgoing
                           if message['type'] == 'bot_observation')
        self.assertEqual(3, len(observation['contacts']))
        # Three enemy pairs collapse to one payload record per team target:
        # team 1->bot 13 and team 2->bot 11/bot 12.
        self.assertEqual(3, len(calls))

    def test_fire_range_uses_one_target_distance_per_cached_frame(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(dict(
                self._stationary_command(), target_id=12,
                fire_allowed=False, fire_range=500.0)),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'B'},
        ]))
        runtime.states[11].update(x=0.0, y=0.0, z=0.0)
        runtime.states[12].update(x=0.0, y=0.0, z=100.0)
        runtime.update(.04, 1.0)
        runtime._next_observation = 100.0
        distance_calls = []
        original_distance = self.module._distance

        def counted_distance(first, second):
            distance_calls.append((first, second))
            return original_distance(first, second)

        self.module._distance = counted_distance
        try:
            runtime.update(.04, 1.04)
        finally:
            self.module._distance = original_distance

        self.assertEqual(1, len(distance_calls))

    def test_gun_yaw_limits_are_derived_once_per_manifest_bot(self):
        calls = []
        original_limits = self.module.ai_driver.gun_yaw_limits

        def counted_limits(descriptor):
            calls.append(descriptor)
            return original_limits(descriptor)

        self.module.ai_driver.gun_yaw_limits = counted_limits
        try:
            runtime = self.module.BotRuntime(
                1, descriptor_resolver=lambda unused: _combat_descriptor(),
                adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                    self._stationary_command()),
                direction_probe=lambda *unused: {
                    'clear': True, 'slope': 0.0},
                visibility_probe=lambda *unused: True,
                firing_lane_probe=lambda *unused: True,
                ground_probe=lambda *unused: 0.0,
                physics_ground_probe=lambda *unused: 0.0,
                spawn_resolver=_spawn_resolver, native_motion=True,
                baked_graph=_graph())
            runtime.battle_start(dict(self.start, bots=[
                {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
                {'id': 12, 'team': 2, 'slot': 0, 'name': 'B'},
            ]))
            self.assertEqual(2, len(calls))
            for frame in range(5):
                runtime.update(.04, 1.0 + frame * .04)
            self.assertEqual(2, len(calls))
        finally:
            self.module.ai_driver.gun_yaw_limits = original_limits

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
            fire_seq=7, shell_index=0, next_shell_index=0,
            ammo_remaining=[38], ammo_reload_pending=False, critical={},
            combat_revision=0, combat_base_revision=0,
            combat_ack_seq=0, combat_fire_elapsed=0.0,
            combat_fire_timer=0.0)
        takeover = dict(
            self.start, bot_authority_id=1,
            bot_manifest=[snapshot_bot])

        outgoing = self.runtime.battle_start(takeover)

        self.assertEqual(7, self.runtime.states[11]['fire_seq'])
        self.assertEqual(0, self.runtime.states[11]['shell_index'])
        self.assertEqual('bot_manifest', outgoing[0]['type'])
        self.runtime.apply_snapshot({'bots': [dict(
            snapshot_bot, fire_seq=8, shell_index=0,
            ammo_remaining=[37], ammo_reload_pending=True)]})
        self.assertEqual(8, self.runtime.states[11]['fire_seq'])
        self.assertEqual(0, self.runtime.states[11]['shell_index'])
        self.assertEqual([37], self.runtime.states[11]['ammo_remaining'])
        self.assertTrue(
            self.runtime.states[11]['ammo_reload_pending'])

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

    def test_authority_handback_rebases_canonical_pose_aim_and_motion(self):
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        sync = self.runtime._combat_sync[11]
        state.update({
            'x': 50.0, 'y': 1.0, 'z': 60.0, 'yaw': 2.5,
            'aim_yaw': 2.7, 'turret_yaw': 0.2, 'gun_pitch': -0.1,
            'desired_gun_pitch': -0.1, 'gun_aligned': True,
            'hull_aiming': True, 'speed': 8.0,
            'movement_dir': 1, 'rotation_dir': -1,
            'push_x': 3.0, 'push_z': -2.0,
            'vertical_speed': -4.0, 'airborne': True,
            'grounded_once': True, 'last_drive_pitch': 0.3,
            'health': 777,
        })
        self.runtime._turn_speeds[11] = 0.8
        self.assertEqual([], self.runtime.battle_start(dict(
            self.start, bot_authority_id=2)))
        takeover = dict(
            self.start['bots'][0], x=200.0, y=4.0, z=300.0,
            yaw=1.0, aim_yaw=1.4, gun_pitch=-0.25,
            movement_dir=-1, rotation_dir=1, health=900,
            max_health=1000, alive=True)

        resumed = self.runtime.battle_start(dict(
            self.start, bot_manifest=[takeover]))

        state = self.runtime.states[11]
        self.assertEqual((200.0, 4.0, 300.0, 1.0), (
            state['x'], state['y'], state['z'], state['yaw']))
        self.assertAlmostEqual(1.4, state['aim_yaw'])
        self.assertAlmostEqual(0.4, state['turret_yaw'])
        self.assertEqual((-0.25, -0.25), (
            state['gun_pitch'], state['desired_gun_pitch']))
        self.assertEqual((0.0, -1, 1), (
            state['speed'], state['movement_dir'], state['rotation_dir']))
        self.assertEqual((0.0, 0.0, 0.0, False, False, 0.0), (
            state['push_x'], state['push_z'], state['vertical_speed'],
            state['airborne'], state['grounded_once'],
            state['last_drive_pitch']))
        self.assertFalse(state['gun_aligned'])
        self.assertFalse(state['hull_aiming'])
        self.assertEqual(0.0, self.runtime._turn_speeds[11])
        self.assertEqual(777, state['health'])
        self.assertIs(sync, self.runtime._combat_sync[11])
        self.assertTrue(sync['authority_handoff_pending'])
        self.assertEqual((200.0, 4.0, 300.0, 1.0), (
            resumed[0]['bots'][0]['x'], resumed[0]['bots'][0]['y'],
            resumed[0]['bots'][0]['z'], resumed[0]['bots'][0]['yaw']))

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
            {'id': 2, 'team': 1, 'alive': True,
             'x': 5, 'y': 0, 'z': 5}])

        order = self.adapters[0].server_orders[-1]
        self.assertEqual(self.module.HUMAN_TARGET_ID_BASE + 2,
                         order['target_id'])
        self.assertEqual((5.0, 0.0, 5.0), order['aim_position'])
        self.assertEqual((5.0, 0.0, 5.0), order['face_position'])
        self.assertEqual('human', self.runtime.states[11]['target_kind'])
        self.assertEqual(2, self.runtime.states[11]['target_id'])

    def test_spawn_route_join_paths_are_scoped_to_each_bot(self):
        calls = []

        class Navigator(object):
            def next_target(self, bot_id, position, goal, path_key, now,
                            anchor, avoid):
                calls.append((bot_id, path_key, anchor))
                return goal

        runtime = self.module.BotRuntime(1)
        runtime.navigator = Navigator()
        runtime.states = {
            11: {'team': 2},
            12: {'team': 2},
        }
        strategic = {
            'combat_mode': 'route', 'route_id': 'forest', 'route_index': 1,
            'route_join': True,
        }
        runtime._navigation_target(
            11, (-12.0, 0.0, 0.0), (0.0, 0.0, 80.0),
            dict(strategic, route_anchor=(-12.0, 0.0, 0.0)),
            {'now': 1.0, 'neighbours': ()})
        runtime._navigation_target(
            12, (12.0, 0.0, 0.0), (0.0, 0.0, 80.0),
            dict(strategic, route_anchor=(12.0, 0.0, 0.0)),
            {'now': 1.0, 'neighbours': ()})

        self.assertEqual(('route_join', 11, 2, 'forest', 1), calls[0][1])
        self.assertEqual(('route_join', 12, 2, 'forest', 1), calls[1][1])
        self.assertNotEqual(calls[0][1], calls[1][1])
        self.assertNotEqual(calls[0][2], calls[1][2])

        runtime._navigation_target(
            11, (0.0, 0.0, 40.0), (0.0, 0.0, 80.0),
            dict(strategic, route_join=False,
                 route_anchor=(0.0, 0.0, 20.0)),
            {'now': 2.0, 'neighbours': ()})
        self.assertEqual(('route', 2, 'forest', 1), calls[2][1])
        self.assertIsNone(calls[2][2])

    def test_base_defense_navigation_key_ignores_combat_target_changes(self):
        calls = []

        class Navigator(object):
            def next_target(self, bot_id, position, goal, path_key, now,
                            anchor, avoid):
                calls.append(path_key)
                return goal

        runtime = self.module.BotRuntime(1)
        runtime.navigator = Navigator()
        runtime.states = {11: {'team': 1}}
        for target_id in (21, 22, None):
            runtime._navigation_target(
                11, (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), {
                    'combat_mode': 'base_defense',
                    'defense_base_id': '1:0',
                    'target_id': target_id,
                }, {'now': 1.0, 'neighbours': ()})

        self.assertEqual([
            ('local', 11, 'base_defense', '1:0'),
            ('local', 11, 'base_defense', '1:0'),
            ('local', 11, 'base_defense', '1:0'),
        ], calls)

    def test_planner_selects_near_fast_stable_base_defenders(self):
        planner = BotPlanner()
        manifest = []
        states = []
        for bot_id, x, speed in (
                (11, 90.0, 10.0), (12, 180.0, 22.0),
                (13, 60.0, 5.0), (14, 400.0, 22.0)):
            manifest.append({
                'id': bot_id, 'team': 1, 'slot': bot_id - 11,
                'health': 1000,
                'profile': {
                    'speed': speed, 'class_tag': 'mediumTank',
                    'dominant_role': 'support', 'roles': {},
                },
                'route': {'id': 'lane-%s' % bot_id, 'waypoints': [
                    {'x': x, 'y': 0.0, 'z': 0.0},
                    {'x': x, 'y': 0.0, 'z': 300.0},
                ]},
            })
            states.append({
                'id': bot_id, 'team': 1, 'alive': True,
                'world_pose': True, 'x': x, 'y': 0.0, 'z': 0.0,
                'health': 1000, 'max_health': 1000,
            })
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 20, 'time_left': 40.0,
                'invaders': 2, 'stopped': False}},
            'contributors': {'1': []},
        }

        orders = planner.build_orders(
            manifest, states, [], 1.0, defense)['orders']
        defenders = [order for order in orders
                     if order['combat_mode'] == 'base_defense']

        self.assertEqual([11, 12], sorted(
            order['id'] for order in defenders))
        self.assertTrue(all(
            order['move_position'] == {'x': 0.0, 'y': 0.0, 'z': 0.0}
            for order in defenders))
        self.assertTrue(all(order['defense_base_id'] == '1:0'
                            for order in defenders))

        # Small ETA changes and fewer invaders do not churn an active group.
        states[2]['x'] = 5.0
        defense['states']['1']['invaders'] = 1
        again = planner.build_orders(
            manifest, states, [], 2.0, defense)['orders']
        self.assertEqual([11, 12], sorted(
            order['id'] for order in again
            if order['combat_mode'] == 'base_defense'))

    def test_base_defense_stays_through_stopped_and_clears_after_grace(self):
        planner = BotPlanner()
        manifest = [{
            'id': 11, 'team': 1, 'slot': 0, 'health': 1000,
            'profile': {'speed': 16.0, 'class_tag': 'lightTank'},
            'route': {'id': 'lane', 'waypoints': [
                {'x': 0.0, 'y': 0.0, 'z': 0.0},
                {'x': 0.0, 'y': 0.0, 'z': 300.0}]},
        }]
        states = [{
            'id': 11, 'team': 1, 'alive': True, 'world_pose': True,
            'x': 100.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }]
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 30, 'time_left': 70.0,
                'invaders': 1, 'stopped': True}},
        }
        self.assertEqual('base_defense', planner.build_orders(
            manifest, states, [], 1.0, defense)['orders'][0]['combat_mode'])

        defense['states']['1']['invaders'] = 0
        self.assertEqual('base_defense', planner.build_orders(
            manifest, states, [], 2.0, defense)['orders'][0]['combat_mode'])
        self.assertEqual('base_defense', planner.build_orders(
            manifest, states, [], 4.9, defense)['orders'][0]['combat_mode'])
        self.assertEqual('route', planner.build_orders(
            manifest, states, [], 5.0, defense)['orders'][0]['combat_mode'])

    def test_base_defender_keeps_visible_target_and_moving_order(self):
        planner = BotPlanner()
        manifest = [{
            'id': 11, 'team': 1, 'slot': 0, 'health': 1000,
            'profile': {
                'speed': 16.0, 'class_tag': 'mediumTank',
                'dominant_role': 'support', 'desired_range': 180.0,
                'fire_range': 500.0, 'roles': {}},
            'route': {'id': 'lane', 'waypoints': [
                {'x': 100.0, 'y': 0.0, 'z': 0.0},
                {'x': 100.0, 'y': 0.0, 'z': 300.0}]},
        }, {
            'id': 12, 'team': 1, 'slot': 1, 'health': 1000,
            'profile': {'speed': 8.0, 'class_tag': 'heavyTank'},
            'route': {'id': 'other', 'waypoints': [
                {'x': 400.0, 'y': 0.0, 'z': 0.0},
                {'x': 400.0, 'y': 0.0, 'z': 300.0}]},
        }]
        states = [{
            'id': 11, 'team': 1, 'alive': True, 'world_pose': True,
            'x': 100.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }, {
            'id': 12, 'team': 1, 'alive': True, 'world_pose': True,
            'x': 400.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1, 'target_kind': 'human',
            'target_id': 2, 'target_team': 2, 'visible': True,
            'shootable_by_bot_ids': [11],
            'x': 120.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }], planner.known_targets(states, [enemy]), 1.0))
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 10, 'time_left': 90.0,
                'invaders': 1, 'stopped': False}},
            'contributors': {'1': [{'kind': 'human', 'id': 2}]},
        }

        order = next(order for order in planner.build_orders(
            manifest, states, [enemy], 1.0, defense)['orders']
                     if order['id'] == 11)

        self.assertEqual('base_defense', order['combat_mode'])
        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 0.0},
                         order['move_position'])
        self.assertIsNone(order['throttle_override'])

    def test_base_defense_leaves_one_attacker_and_replaces_crippled_responder(self):
        planner = BotPlanner()
        manifest = []
        states = []
        for bot_id, x in ((11, 30.0), (12, 60.0),
                          (13, 90.0), (14, 120.0), (15, 150.0)):
            manifest.append({
                'id': bot_id, 'team': 1, 'slot': bot_id - 11,
                'health': 1000,
                'profile': {
                    'speed': 18.0, 'class_tag': 'mediumTank',
                    'dominant_role': 'support', 'roles': {},
                },
                'route': {'id': 'lane-%s' % bot_id, 'waypoints': [
                    {'x': x, 'y': 0.0, 'z': 0.0},
                    {'x': x, 'y': 0.0, 'z': 300.0},
                ]},
            })
            states.append({
                'id': bot_id, 'team': 1, 'alive': True,
                'world_pose': True, 'x': x, 'y': 0.0, 'z': 0.0,
                'health': 1000, 'max_health': 1000, 'critical': {},
            })
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 30, 'time_left': 25.0,
                'invaders': 3, 'stopped': False}},
            'contributors': {'1': []},
        }

        first = planner.build_orders(
            manifest, states, [], 1.0, defense)
        self.assertEqual([11, 12, 13], sorted(
            order['id'] for order in first['orders']
            if order['combat_mode'] == 'base_defense'))
        self.assertGreaterEqual(sum(
            order['combat_mode'] != 'base_defense'
            for order in first['orders']), 1)

        # An authority failover clears observations, not the server-owned
        # capture incident or its stable responder leases.
        planner.clear_observations()
        unchanged = planner.build_orders(
            manifest, states, [], 2.0, defense)
        self.assertEqual(first['revision'], unchanged['revision'])
        self.assertEqual([11, 12, 13], sorted(
            order['id'] for order in unchanged['orders']
            if order['combat_mode'] == 'base_defense'))

        # If the two bots left on attack are lost, one of the three existing
        # leases is released deterministically so the last mobile trio does
        # not all abandon the rest of the map.
        states[3]['alive'] = False
        states[4]['alive'] = False
        reduced = planner.build_orders(
            manifest, states, [], 2.5, defense)
        self.assertEqual([11, 12], sorted(
            order['id'] for order in reduced['orders']
            if order['combat_mode'] == 'base_defense'))

        # The same reserve invariant applies during the three-second clear
        # grace; debouncing the capture signal must not recall the last Bot.
        defense['states']['1']['invaders'] = 0
        grace = planner.build_orders(
            manifest, states, [], 2.6, defense)
        self.assertEqual([11, 12], sorted(
            order['id'] for order in grace['orders']
            if order['combat_mode'] == 'base_defense'))

        # The local driver cannot move with either track fully destroyed.
        # That responder must release its lease and be replaced without
        # recalling every bot deliberately left ahead.
        states[3]['alive'] = True
        states[4]['alive'] = True
        defense['states']['1']['invaders'] = 3
        states[0]['critical'] = {'destroyed': ['leftTrackHealth']}
        replaced = planner.build_orders(
            manifest, states, [], 3.0, defense)
        self.assertEqual([12, 13, 14], sorted(
            order['id'] for order in replaced['orders']
            if order['combat_mode'] == 'base_defense'))

    def test_base_defenders_spread_visible_capture_contributors(self):
        planner = BotPlanner()
        manifest = []
        states = []
        for bot_id, x in ((11, 30.0), (12, 40.0), (13, 300.0)):
            manifest.append({
                'id': bot_id, 'team': 1, 'slot': bot_id - 11,
                'health': 1000,
                'profile': {
                    'speed': 18.0, 'class_tag': 'mediumTank',
                    'dominant_role': 'support', 'desired_range': 180.0,
                    'fire_range': 500.0, 'roles': {},
                },
                'route': {'id': 'lane-%s' % bot_id, 'waypoints': [
                    {'x': x, 'y': 0.0, 'z': 0.0},
                    {'x': x, 'y': 0.0, 'z': 300.0},
                ]},
            })
            states.append({
                'id': bot_id, 'team': 1, 'alive': True,
                'world_pose': True, 'x': x, 'y': 0.0, 'z': 0.0,
                'health': 1000, 'max_health': 1000,
            })
        enemies = [
            {'id': 2, 'team': 2, 'alive': True},
            {'id': 3, 'team': 2, 'alive': True},
        ]
        known = planner.known_targets(states, enemies)
        for enemy_id, z in ((2, 15.0), (3, -15.0)):
            self.assertEqual(1, planner.report_contacts([{
                'observing_team': 1, 'target_kind': 'human',
                'target_id': enemy_id, 'target_team': 2,
                'visible': True, 'shootable_by_bot_ids': [11, 12],
                'x': 0.0, 'y': 0.0, 'z': z,
                'health': 1000, 'max_health': 1000,
            }], known, 1.0))
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 30, 'time_left': 35.0,
                'invaders': 2, 'stopped': False}},
            'contributors': {'1': [
                {'kind': 'human', 'id': 2},
                {'kind': 'human', 'id': 3},
            ]},
        }

        orders = planner.build_orders(
            manifest, states, enemies, 1.0, defense)['orders']
        defenders = [order for order in orders
                     if order['combat_mode'] == 'base_defense']

        self.assertEqual([11, 12], sorted(
            order['id'] for order in defenders))
        self.assertEqual({2, 3}, set(
            order['target_id'] for order in defenders))
        self.assertTrue(all(order['fire_allowed'] for order in defenders))
        self.assertTrue(all(
            order['move_position'] == {'x': 0.0, 'y': 0.0, 'z': 0.0}
            for order in defenders))

    def test_json_route_anchor_is_normalized_before_terrain_navigation(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
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
                'route_join': True,
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
            {'id': 2, 'team': 1, 'alive': True,
             'x': 5, 'y': 0, 'z': 5,
             'health': 100, 'max_health': 100}])

        observation = [value for value in outgoing
                       if value['type'] == 'bot_observation'][0]
        self.assertEqual(1, len(observation['contacts']))
        self.assertEqual('human', observation['contacts'][0]['target_kind'])
        self.assertEqual(2, observation['contacts'][0]['target_id'])

    def test_human_vehicle_profiles_drive_server_shell_selection_once(self):
        descriptor_calls = []

        def target_descriptor(vehicle_name):
            descriptor_calls.append(vehicle_name)
            descriptor = _combat_descriptor()
            armor = 40.0 if vehicle_name == 'test:soft' else 240.0
            class_tag = ('lightTank' if vehicle_name == 'test:soft' else
                         'heavyTank')
            descriptor.hull.primaryArmor = (armor, armor, armor)
            descriptor.type = types.SimpleNamespace(
                tags=(class_tag,), name=vehicle_name)
            return descriptor

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=target_descriptor,
            visibility_probe=lambda *unused: True)
        source = {
            'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        players = [
            {'id': 2, 'team': 2, 'alive': True,
             'vehicle': 'test:soft', 'x': 10.0, 'y': 0.0, 'z': 100.0,
             'health': 1000, 'max_health': 1000},
            {'id': 3, 'team': 2, 'alive': True,
             'vehicle': 'test:hard', 'x': -10.0, 'y': 0.0, 'z': 100.0,
             'health': 1000, 'max_health': 1000},
        ]
        contacts, unused_lookup = runtime._contacts_for(
            source, players, 1.0)
        runtime._contacts_for(source, players, 1.1)
        by_network_id = dict((contact['network_id'], contact)
                             for contact in contacts)

        self.assertEqual((40.0, 'lightTank'), (
            by_network_id[2]['armor'], by_network_id[2]['class_tag']))
        self.assertEqual((240.0, 'heavyTank'), (
            by_network_id[3]['armor'], by_network_id[3]['class_tag']))
        self.assertEqual(['test:soft', 'test:hard'], descriptor_calls)

        aggregate = {}
        for target_id, target in by_network_id.items():
            aggregate[(1, 'human', target_id)] = (True, set((11,)), target)
        observations = runtime._pack_observations(aggregate)
        planner = BotPlanner()
        known = planner.known_targets([], players)
        self.assertEqual(2, planner.report_contacts(
            observations, known, 1.0))
        weapon_descriptor = _combat_descriptor()
        weapon_descriptor.gun.shots = (
            {'shell': {'kind': 'ARMOR_PIERCING',
                       'piercingPower': 180.0, 'damage': 300.0},
             'speed': 900.0},
            {'shell': {'kind': 'ARMOR_PIERCING_CR',
                       'piercingPower': 260.0, 'damage': 300.0},
             'speed': 1100.0},
            {'shell': {'kind': 'HIGH_EXPLOSIVE',
                       'piercingPower': 60.0, 'damage': 420.0},
             'speed': 700.0},
        )
        weapon_profile = self.module.ai_planner.build_vehicle_profile(
            weapon_descriptor)
        weapon_profile = BattleState._sanitize_bot_profile(weapon_profile)
        personality = {'aggression': 0.5}
        remaining = {'ammo_remaining': [30, 20, 10]}
        self.assertEqual(2, planner._shell_index(
            weapon_profile, planner._contacts[1][('human', 2)],
            personality, remaining))
        self.assertEqual(1, planner._shell_index(
            weapon_profile, planner._contacts[1][('human', 3)],
            personality, remaining))

    def test_cached_observation_uses_current_bot_pose_and_health(self):
        adapter = _FixedAdapter(self._stationary_command())
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Observer'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Target'},
        ]))
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(
            x=0.0, y=1.0, z=100.0, yaw=0.0,
            health=900, max_health=900, alive=True)

        runtime.update(.04, 1.0)
        cached_target = runtime._decision_cache[11][5][12]
        cached_snapshot = dict(cached_target)
        moved = (45.0, 2.0, 70.0)
        runtime.states[12].update(
            x=moved[0], y=moved[1], z=moved[2],
            health=321, max_health=900)
        # Force the next publish frame to collect an observation while both
        # bots still reuse their first decision/perception cache.
        runtime._next_observation = 1.04
        indexed_players = []
        full_refreshes = []
        original_index = runtime._index_live_players
        original_refresh = runtime._refresh_target_poses

        def record_index(players):
            indexed_players.append(1)
            return original_index(players)

        def record_full_refresh(*args, **kwargs):
            full_refreshes.append(1)
            return original_refresh(*args, **kwargs)

        runtime._index_live_players = record_index
        runtime._refresh_target_poses = record_full_refresh
        try:
            outgoing = runtime.update(.04, 1.04)
        finally:
            runtime._index_live_players = original_index
            runtime._refresh_target_poses = original_refresh

        observation = next(message for message in outgoing
                           if message['type'] == 'bot_observation')
        contact = next(
            value for value in observation['contacts']
            if value['observing_team'] == 1 and
            value['target_kind'] == 'bot' and value['target_id'] == 12)
        self.assertEqual(moved, (contact['x'], contact['y'], contact['z']))
        self.assertEqual(321, contact['health'])
        self.assertEqual(900, contact['max_health'])
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual([1], indexed_players)
        self.assertEqual([1, 1], full_refreshes)
        self.assertIs(cached_target, runtime._decision_cache[11][5][12])
        self.assertEqual(cached_snapshot, cached_target)

    def test_team_spot_does_not_pull_blocked_bot_off_route(self):
        lane_calls = []

        def firing_lane(source, target):
            lane_calls.append((source['id'], target['network_id']))
            return source['id'] == 12

        def visibility(source, unused_target):
            return source['id'] == 11

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.05, clip=(1,)),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            # Bot 11 spots for the team, while only bot 12 owns the clear
            # barrel lane. Team visibility and per-bot shootability are
            # deliberately different facts.
            visibility_probe=visibility,
            firing_lane_probe=firing_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Clear'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Blocked'},
        ]
        manifest = runtime.battle_start(
            dict(self.start, bots=roster))[0]['bots']
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(x=10.0, y=0.0, z=0.0, yaw=0.0)
        enemy = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
            'health': 1000, 'max_health': 1000,
        }

        outgoing = runtime.update(.04, 1.0, players=[enemy])
        bot_states = next(message['bots'] for message in outgoing
                          if message['type'] == 'bot_state')
        observation = next(message for message in outgoing
                           if message['type'] == 'bot_observation')
        self.assertEqual(1, len(observation['contacts']))
        contact = observation['contacts'][0]
        self.assertTrue(contact['visible'])
        self.assertEqual([12], contact['shootable_by_bot_ids'])
        self.assertEqual([(11, 2), (12, 2)], lane_calls)

        planner = BotPlanner()
        known = planner.known_targets(bot_states, [enemy])
        self.assertEqual(1, planner.report_contacts(
            observation['contacts'], known, 1.0))
        orders = dict((order['id'], order) for order in
                      planner.build_orders(
                          manifest, bot_states, [enemy], 1.0)['orders'])
        self.assertIsNone(orders[11]['target_id'])
        self.assertEqual('route', orders[11]['combat_mode'])
        self.assertFalse(orders[11]['fire_allowed'])
        self.assertEqual(2, orders[12]['target_id'])
        self.assertEqual('human', orders[12]['target_kind'])
        self.assertTrue(orders[12]['fire_allowed'])

        payload = planner.build_orders(
            manifest, bot_states, [enemy], 1.0)
        self.assertTrue(runtime._apply_orders({
            'bot_order_revision': payload['revision'],
            'bot_orders': payload['orders'],
        }))
        # Team spotting does not pull bot 11 off its route. Bot 12 owns the
        # current firing lane and remains the only assigned shooter.
        for index in range(4):
            runtime.update(.06, 1.21 + index * .21, players=[enemy])
        self.assertEqual(0, runtime.states[11]['fire_seq'])
        self.assertGreater(runtime.states[12]['fire_seq'], 0)

    def test_close_support_withdraws_without_losing_limited_traverse_fire(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=8.0, clip=(1,),
                turret_yaw_limits=(-0.1, 0.1)),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        profile = {
            'dominant_role': 'support', 'desired_range': 200.0,
            'fire_range': 500.0, 'roles': {'support': 1.0},
        }
        manifest = runtime.battle_start(dict(self.start, bots=[{
            'id': 11, 'team': 1, 'slot': 0, 'name': 'Limited TD',
            'profile': profile,
        }]))[0]['bots']
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0,
                     aim_yaw=0.0, speed=0.0)
        manifest[0]['route'] = {
            'id': 'support_lane', 'waypoints': [
                {'x': 0.0, 'y': 0.0, 'z': -120.0},
                {'x': 0.0, 'y': 0.0, 'z': 300.0},
            ],
        }
        enemy = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 20.0,
            'health': 1000, 'max_health': 1000,
        }
        planner = BotPlanner()
        bot_states = [dict(state)]
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1, 'target_kind': 'human',
            'target_id': 2, 'target_team': 2,
            'visible': True, 'shootable_by_bot_ids': [11],
            'x': 0.0, 'y': 0.0, 'z': 20.0,
            'health': 1000, 'max_health': 1000,
        }], planner.known_targets(bot_states, [enemy]), 1.0))
        payload = planner.build_orders(
            manifest, bot_states, [enemy], 1.0)
        order = payload['orders'][0]

        self.assertEqual('withdraw', order['combat_mode'])
        self.assertIsNone(order['throttle_override'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 0.0},
                         order['move_position'])
        runtime.apply_snapshot({
            'bot_order_revision': payload['revision'],
            'bot_orders': payload['orders'], 'bots': [],
        })

        yaws = []
        turns = []
        for frame in range(750):
            runtime.update(0.02, 1.0 + frame * 0.02,
                           players=[enemy])
            yaws.append(runtime.states[11]['yaw'])
            turns.append(runtime.states[11]['rotation_dir'])

        self.assertEqual({0}, set(turns))
        self.assertLess(max(abs(value) for value in yaws), 0.001)
        self.assertTrue(runtime.states[11]['gun_aligned'])
        self.assertGreaterEqual(runtime.states[11]['fire_seq'], 1)

    def test_non_close_support_target_still_uses_engage_hold(self):
        planner = BotPlanner()
        profile = {
            'dominant_role': 'support', 'desired_range': 200.0,
            'fire_range': 500.0, 'roles': {'support': 1.0},
        }
        manifest = [{
            'id': 11, 'team': 1, 'slot': 0, 'name': 'Support',
            'health': 1000, 'profile': profile,
            'route': {
                'id': 'support_lane', 'waypoints': [
                    {'x': 0.0, 'y': 0.0, 'z': -120.0},
                    {'x': 0.0, 'y': 0.0, 'z': 300.0},
                ],
            },
        }]
        bot_states = [{
            'id': 11, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
        }]
        enemy = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 150.0,
        }
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1, 'target_kind': 'human',
            'target_id': 2, 'target_team': 2,
            'visible': True, 'shootable_by_bot_ids': [11],
            'x': 0.0, 'y': 0.0, 'z': 150.0,
            'health': 1000, 'max_health': 1000,
        }], planner.known_targets(bot_states, [enemy]), 1.0))

        order = planner.build_orders(
            manifest, bot_states, [enemy], 1.0)['orders'][0]

        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertEqual('engage', order['combat_mode'])
        self.assertEqual(0.0, order['throttle_override'])

    def test_proximity_spot_through_wall_publishes_explicit_unshootable_list(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: False,
            firing_lane_probe=lambda *unused: False,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Blocked-A'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Blocked-B'},
        ]
        manifest = runtime.battle_start(
            dict(self.start, bots=roster))[0]['bots']
        runtime.states[11].update(x=0.0, y=0.0, z=0.0)
        runtime.states[12].update(x=5.0, y=0.0, z=0.0)
        enemy = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 30.0,
            'health': 1000, 'max_health': 1000,
        }

        outgoing = runtime.update(.04, 1.0, players=[enemy])
        bot_states = next(message['bots'] for message in outgoing
                          if message['type'] == 'bot_state')
        contact = next(message for message in outgoing
                       if message['type'] == 'bot_observation')['contacts'][0]
        self.assertTrue(contact['visible'])
        self.assertEqual([], contact['shootable_by_bot_ids'])

        planner = BotPlanner()
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(bot_states, [enemy]), 1.0))
        orders = planner.build_orders(
            manifest, bot_states, [enemy], 1.0)['orders']
        self.assertTrue(all(order['target_id'] is None for order in orders))
        self.assertTrue(all(order['combat_mode'] == 'route'
                            for order in orders))
        self.assertTrue(all(not order['fire_allowed'] for order in orders))

    def test_full_roster_firing_lane_refresh_is_staggered_and_complete(self):
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Observer-%d' % index}
            for index in range(29)
        ]
        for fps in (24, 40, 60, 120):
            with self.subTest(fps=fps):
                frame_time = [0.0]
                sample_times = {}

                def firing_lane(source, target):
                    key = (source['id'], target['kind'],
                           target['network_id'])
                    sample_times.setdefault(key, []).append(frame_time[0])
                    clear_id = 11 if source['team'] == 1 else 25
                    return source['id'] == clear_id

                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        self._stationary_command()),
                    direction_probe=lambda *unused: {
                        'clear': True, 'slope': 0.0},
                    visibility_probe=lambda *unused: True,
                    firing_lane_probe=firing_lane,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, native_motion=True,
                    baked_graph=_graph())
                runtime.battle_start(dict(self.start, bots=roster))
                player = {
                    'id': 1, 'team': 1, 'alive': True,
                    'x': 0.0, 'y': 0.0, 'z': 100.0}
                per_frame = []
                observations = []
                observation_ages = []
                observation_delays = []
                for frame in range(int(fps * 0.6) + 1):
                    now = 1.0 + frame / float(fps)
                    frame_time[0] = now
                    observation_deadline = runtime._next_observation
                    before = sum(len(values)
                                 for values in sample_times.values())
                    outgoing = runtime.update(
                        1.0 / fps, now, players=[player])
                    after = sum(len(values)
                                for values in sample_times.values())
                    per_frame.append(after - before)
                    for message in outgoing:
                        if message['type'] != 'bot_observation':
                            continue
                        observations.append(message)
                        self.assertEqual(
                            435, len(runtime._shot_los_cache))
                        self.assertEqual(435, sum(
                            1 for deadline in
                            runtime._shot_los_deadlines.values()
                            if deadline == observation_deadline))
                        observation_ages.append(max(
                            now - sample[0]
                            for sample in runtime._shot_los_cache.values()))
                        if observation_deadline > 0.0:
                            observation_delays.append(
                                now - observation_deadline)

                # Initial warm-up and every later refresh obey one global
                # render-frame budget. A due observation waits for the whole
                # 435-pair set instead of publishing a partial firing view.
                self.assertLessEqual(
                    max(per_frame),
                    self.module.MAX_SHOT_LANE_PAIRS_PER_FRAME)
                self.assertEqual(435, len(sample_times))
                self.assertTrue(observations)
                self.assertTrue(all(
                    len(values) >= len(observations)
                    for values in sample_times.values()))
                self.assertLessEqual(
                    max(observation_ages),
                    self.module.SHOT_LANE_SECONDS + 1e-6)
                if observation_delays:
                    self.assertLessEqual(
                        max(observation_delays),
                        1.0 / fps + self.module.PUBLICATION_SECONDS + 1e-6)
                for observation in observations:
                    self.assertEqual(30, len(observation['contacts']))
                    for contact in observation['contacts']:
                        expected = (
                            [11] if contact['target_team'] == 2 else [25])
                        self.assertEqual(
                            expected, contact['shootable_by_bot_ids'])

    def test_full_roster_observation_and_server_planner_stay_live_for_two_minutes(self):
        lane_probes = [0]
        clear_observer = {1: 11, 2: 25}

        def firing_lane(source, unused_target):
            lane_probes[0] += 1
            return source['id'] == clear_observer[source['team']]

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.5, clip=(1,)),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=firing_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        refresh_lane_probes = [0]
        refresh_shot_clear = runtime._refresh_shot_clear

        def counted_refresh(*args, **kwargs):
            before = lane_probes[0]
            result = refresh_shot_clear(*args, **kwargs)
            refresh_lane_probes[0] += lane_probes[0] - before
            return result

        runtime._refresh_shot_clear = counted_refresh
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Observer-%d' % index}
            for index in range(29)
        ]
        manifest = runtime.battle_start(
            dict(self.start, bots=roster))[0]['bots']
        planner = BotPlanner()
        observation_batches = 0
        observation_times = []
        per_frame_lane_probes = []

        # Drive the runtime at 50 FPS for 120 seconds. Periodic firing-lane
        # refreshes and current-fire safety checks share one frame budget.
        for frame in range(6000):
            now = 1.0 + frame * 0.02
            observation_deadline = runtime._next_observation
            before_lane_probes = lane_probes[0]
            outgoing = runtime.update(.02, now)
            per_frame_lane_probes.append(
                lane_probes[0] - before_lane_probes)
            if not any(message['type'] == 'bot_observation'
                       for message in outgoing):
                continue
            bot_states = next(message['bots'] for message in outgoing
                              if message['type'] == 'bot_state')
            observation = next(
                message for message in outgoing
                if message['type'] == 'bot_observation')
            observation_batches += 1
            observation_times.append(now)
            self.assertEqual(420, sum(
                1 for deadline in runtime._shot_los_deadlines.values()
                if deadline == observation_deadline))
            contacts = observation['contacts']
            self.assertEqual(29, len(contacts))
            self.assertTrue(all(
                'shootable_by_bot_ids' in contact for contact in contacts))
            known = planner.known_targets(bot_states, [])
            self.assertEqual(29, planner.report_contacts(
                contacts, known, now))
            orders = planner.build_orders(
                manifest, bot_states, [], now)['orders']
            contact_by_target = dict(
                ((contact['target_kind'], contact['target_id']), contact)
                for contact in contacts)
            targeted = 0
            firing = 0
            for order in orders:
                if order['target_id'] is None:
                    self.assertFalse(order['fire_allowed'])
                    self.assertEqual('route', order['combat_mode'])
                    continue
                targeted += 1
                contact = contact_by_target[
                    (order['target_kind'], order['target_id'])]
                if order['fire_allowed']:
                    firing += 1
                    self.assertIn(order['id'],
                                  contact['shootable_by_bot_ids'])
            self.assertEqual(2, targeted)
            self.assertEqual(2, firing)

        self.assertTrue(observation_times)
        initial_probe_frames = int(math.ceil(
            420.0 / self.module.MAX_SHOT_LANE_PAIRS_PER_FRAME))
        self.assertLessEqual(
            observation_times[0],
            1.0 + (initial_probe_frames - 1) * 0.02 +
            self.module.PUBLICATION_SECONDS + 1e-6)
        self.assertGreaterEqual(observation_times[-1], 120.75)
        self.assertLessEqual(max(
            later - earlier for earlier, later in zip(
                observation_times, observation_times[1:])),
            self.module.OBSERVATION_SECONDS + 0.02 +
            self.module.PUBLICATION_SECONDS + 1e-6)
        self.assertLessEqual(observation_batches, 300)
        # 14x15 plus 15x14 enemy pairs refresh once per observation. The run
        # may end with at most one unpublished refresh in flight; live-fire
        # safety checks are measured separately instead of widening that law.
        self.assertLessEqual(
            max(per_frame_lane_probes),
            self.module.MAX_SHOT_LANE_PAIRS_PER_FRAME)
        self.assertLessEqual(
            refresh_lane_probes[0], 420 * (observation_batches + 1))
        self.assertLessEqual(sum(
            1 for deadline in runtime._shot_los_deadlines.values()
            if deadline == runtime._next_observation), 420)
        live_fire_lane_probes = lane_probes[0] - refresh_lane_probes[0]
        # Tactical observations are deliberately slower than the selected
        # target's final-fire gate.  Between two 0.40-second observations,
        # each Bot may therefore refresh its own 0.20-second lane once.
        maximum_live_fire_cycles = int(math.ceil(
            120.0 / self.module.SHOT_LANE_SECONDS)) + 1
        self.assertLessEqual(
            live_fire_lane_probes,
            len(roster) * maximum_live_fire_cycles)

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
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            baked_graph=graph,
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
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: _Adapter(),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        failures = []
        runtime.adapter.driver = types.SimpleNamespace(
            remember_failure=lambda *args: failures.append(args))
        runtime._decision_cache[11] = object()
        runtime._motion_probe_cache[11] = object()
        state = runtime.states[11]
        state.update(x=4.0, y=-1.0, z=0.0, speed=3.0,
                     vertical_speed=-2.0, airborne=True)

        guarded = runtime._guard_realised_pose(
            state, (0.0, 0.0, 0.0), True, 0.25)

        self.assertTrue(guarded)
        self.assertEqual((0.0, 0.0, 0.0),
                         (state['x'], state['y'], state['z']))
        self.assertEqual(0.0, state['vertical_speed'])
        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertEqual([(11, 0.25, 5.0)], failures)

    def test_driver_receives_native_collision_dimensions_and_velocity(self):
        descriptor = _combat_descriptor()
        descriptor.chassis.hitTester = _HitTester1513(
            (-2.1, -1.0, -4.2), (2.3, 1.0, 3.8))
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
