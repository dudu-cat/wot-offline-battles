import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922'

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

class BotRuntimeTests(unittest.TestCase):
    def setUp(self):
        self._modules = dict((key, value) for key, value in sys.modules.items()
                             if key == 'gui' or key.startswith('gui.'))
        self.module = _load(); self.adapters = []
        def factory(*args):
            adapter = _Adapter(*args); self.adapters.append(adapter); return adapter
        self.runtime = self.module.BotRuntime(1, adapter_factory=factory,
            direction_probe=lambda position, yaw: {'clear': True, 'slope': .2})
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

    def test_30hz_state_updates_pose_input_and_fire_sequence(self):
        self.runtime.battle_start(self.start)
        self.assertEqual([], self.runtime.update(.02, 1.0))
        result = self.runtime.update(.02, 1.02, players=[{'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5}])
        self.assertEqual(0, result[0]['bots'][0]['fire_seq'])
        result = self.runtime.update(.04, 1.80, players=[{'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5}])
        bot = result[0]['bots'][0]
        self.assertEqual('bot_state', result[0]['type'])
        self.assertGreater(bot['z'], 0.0); self.assertEqual(1, bot['fire_seq'])
        self.assertEqual(2, bot['shell_index'])

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
            {'id': 11, 'health': 0, 'alive': False}]})

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
            'bots': [{'id': 11, 'health': 1000, 'alive': True}]})

        self.assertTrue(self.runtime.finished)
        self.assertEqual([], self.runtime.update(.1, 2.0))

    def test_visibility_probes_are_cached_and_staggered(self):
        calls = []
        self.runtime.visibility_probe = lambda source, target: (
            calls.append((source['id'], target['network_id'])) or True)
        self.runtime.battle_start(self.start)
        players = [{'id': 2, 'team': 1, 'x': 5, 'y': 0, 'z': 5}]

        self.runtime.update(.04, 1.0, players=players)
        self.runtime.update(.04, 1.04, players=players)

        self.assertEqual([(11, 2)], calls)

    def test_authority_failover_resumes_server_fire_sequence(self):
        waiting = dict(self.start, bot_authority_id=2)
        self.assertEqual([], self.runtime.battle_start(waiting))
        snapshot_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=1, y=0, z=2, yaw=0.5,
            fire_seq=7, shell_index=1)
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
            'bots': [{'id': 11, 'health': 0, 'alive': False}]})
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

    def test_probe_rejects_water_collision_and_steep_slope(self):
        for probe in ({'clear': True, 'water': True}, {'clear': True, 'collision': True}, {'clear': True, 'slope': .7}):
            runtime = self.module.BotRuntime(1, direction_probe=lambda *unused, value=probe: value)
            self.assertFalse(runtime._clear((0, 0, 0), 0.0))
