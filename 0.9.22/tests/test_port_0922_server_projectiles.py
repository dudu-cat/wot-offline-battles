import json
import math
from pathlib import Path
import sys
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_082, CLIENT_BUILD_0922, MAX_LINE_BYTES,
    PREBATTLE_SECONDS, PROJECTILE_CAPABILITY, PROJECTILE_MAX_ACTIVE,
    Player, TICK_HZ,
)


class _Socket(object):
    def sendall(self, unused_payload):
        pass


def _player(player_id, team=1, x=0.0):
    return Player(
        player_id, _Socket(), ('127.0.0.1', player_id), team=team,
        slot=(player_id - 1) % 15, x=x, client_position=True,
        capabilities=(PROJECTILE_CAPABILITY,))


def _state(players=2):
    state = BattleState(map_name='04_himmelsdorf')
    state.client_build = CLIENT_BUILD_0922
    state.phase = 'battle'
    state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
    for player_id in range(1, players + 1):
        state.players[player_id] = _player(
            player_id, 1 if player_id % 2 else 2,
            float(player_id - 1) * 10.0)
    state.bot_authority_id = 1
    state.authority_epoch = 1
    return state


def _launch(shooter_id=1, shot_seq=1, shooter_kind='player', **changes):
    message = {
        'type': 'projectile_launch', 'round_id': 1,
        'shooter_kind': shooter_kind, 'shooter_id': shooter_id,
        'shot_seq': shot_seq, 'shell_index': 0,
        'origin': [0.0, 1.0, 0.0],
        'velocity': [100.0, 0.0, 0.0], 'gravity': 9.81,
        'max_distance': 1000.0, 'max_time_ms': 10000,
        'is_he': False, 'splash_radius': 0.0,
        'penetration_factor': 1.0,
    }
    if shooter_kind == 'bot':
        message['authority_epoch'] = 1
    message.update(changes)
    return message


def _effect(target_id=2, target_kind='player', damage=100, x=10.0,
            **changes):
    value = {
        'target_kind': target_kind, 'target_id': target_id,
        'damage': damage, 'shot_result': 2,
        'x': x, 'y': 1.0, 'z': 0.0,
    }
    value.update(changes)
    return value


def _resolve(projectile_id, epoch=1, **changes):
    message = {
        'type': 'projectile_resolve', 'round_id': 1,
        'authority_epoch': epoch, 'projectile_id': projectile_id,
        'base_checked_ms': 0, 'outcome': 'impact',
        'resolved_time_ms': 0, 'checked_distance': 10.0,
        'piercing_loss': 0.0, 'penetration_factor': 1.0,
        'impact': [10.0, 1.0, 0.0],
        'direct': _effect(), 'splash': [], 'destructibles': [],
    }
    message.update(changes)
    return message


def _destructible(chunk_id=7, item_index=3, **changes):
    event = {
        'destructible_kind': 'fragile', 'chunk_id': chunk_id,
        'item_index': item_index, 'x': 5.0, 'y': 0.5, 'z': 0.0,
        'fall_yaw': 0.2, 'speed': 12.0, 'is_shot': True,
    }
    event.update(changes)
    return event


class ServerProjectileLedgerTests(unittest.TestCase):
    def test_modern_player_launch_is_atomic_and_idempotent(self):
        state = _state()
        message = _launch()

        self.assertTrue(state.launch_projectile(1, message))
        self.assertEqual(1, state.players[1].fire_seq)
        self.assertEqual(['1:p:1:1'], sorted(state.projectiles))
        shot = state.pending_events[-1]
        self.assertEqual('ussr:R11_MS-1', shot['source_vehicle'])
        self.assertEqual(
            'ussr:R11_MS-1',
            state._projectile_snapshot()[0]['source_vehicle'])
        self.assertEqual('shot', shot['kind'])
        self.assertEqual([0.0, 1.0, 0.0], shot['origin'])
        self.assertEqual([100.0, 0.0, 0.0], shot['velocity'])
        self.assertEqual(1.570796, shot['shot_yaw'])
        self.assertEqual(state._server_time_ms(),
                         shot['launch_server_time_ms'])

        event_count = len(state.pending_events)
        self.assertTrue(state.launch_projectile(1, dict(message)))
        self.assertEqual(event_count, len(state.pending_events))
        self.assertFalse(state.launch_projectile(
            1, dict(message, velocity=[101.0, 0.0, 0.0])))
        self.assertFalse(state.launch_projectile(
            1, _launch(shot_seq=3)))
        self.assertEqual(1, state.players[1].fire_seq)

    def test_retail_spg_gravity_is_admitted_and_snapshotted(self):
        state = _state()

        self.assertTrue(state.launch_projectile(
            1, _launch(gravity=143.0)))
        self.assertEqual(143.0,
                         state.projectiles['1:p:1:1']['gravity'])
        self.assertEqual(143.0,
                         state._projectile_snapshot()[0]['gravity'])

    def test_bot_state_edge_waits_for_authorized_launch(self):
        state = _state()
        state.bot_manifest_authority_id = 1
        state.bot_roster = [{
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Bot',
        }]
        manifest = [{
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'health': 1000,
            'max_health': 1000, 'x': 20.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'world_pose': True, 'profile': {},
            'route': {'id': 'test', 'waypoints': []},
        }]
        self.assertTrue(state.update_bot_manifest(1, {
            'round_id': 1, 'bots': manifest}))
        state.pending_events[:] = []
        publication = {
            'id': 16, 'x': 20.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'health': 1000, 'alive': True, 'fire_seq': 1,
            'critical': {}, 'combat_base_revision': 0, 'combat_seq': 0,
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        }
        self.assertTrue(state.update_bot_states(1, {
            'round_id': 1, 'bots': [publication]}),
            state.last_bot_state_reject)
        self.assertFalse(any(event.get('kind') == 'bot_shot'
                             for event in state.pending_events))

        launch = _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=1,
            origin=[20.0, 1.0, 0.0])
        self.assertFalse(state.launch_projectile(
            1, dict(launch, authority_epoch=0)))
        self.assertTrue(state.launch_projectile(1, launch))
        self.assertEqual('bot_shot', state.pending_events[-1]['kind'])
        self.assertEqual('1:b:16:1',
                         state.pending_events[-1]['projectile_id'])
        self.assertTrue(state.launch_projectile(1, dict(launch)))
        self.assertFalse(state.launch_projectile(
            1, dict(launch, gravity=9.9)))

    def test_progress_uses_batch_cas_epoch_and_exact_retry(self):
        state = _state()
        self.assertTrue(state.launch_projectile(1, _launch()))
        projectile_id = '1:p:1:1'
        cursor = {
            'projectile_id': projectile_id, 'base_checked_ms': 0,
            'checked_through_ms': 200, 'checked_distance': 20.0,
            'piercing_loss': 3.0, 'penetration_factor': 0.8,
            'destructibles': [],
        }
        message = {
            'type': 'projectile_progress', 'round_id': 1,
            'authority_epoch': 1, 'cursors': [cursor],
        }
        self.assertFalse(state.progress_projectiles(
            1, dict(message, authority_epoch=0)))
        self.assertTrue(state.progress_projectiles(1, message))
        self.assertTrue(state.progress_projectiles(1, dict(message)))
        record = state.projectiles[projectile_id]
        self.assertEqual(200, record['checked_through_ms'])
        self.assertEqual(20.0, record['checked_distance'])
        self.assertEqual(3.0, record['piercing_loss'])
        self.assertEqual(0.8, record['penetration_factor'])
        stale = dict(cursor, checked_through_ms=201)
        self.assertFalse(state.progress_projectiles(
            1, dict(message, cursors=[stale])))
        self.assertFalse(state.progress_projectiles(
            1, dict(message, cursors=[dict(
                cursor, base_checked_ms=200, checked_through_ms=200,
                penetration_factor=1.0)])))

    def test_progress_destructibles_are_atomic_and_idempotent(self):
        state = _state()
        self.assertTrue(state.launch_projectile(1, _launch()))
        cursor = {
            'projectile_id': '1:p:1:1', 'base_checked_ms': 0,
            'checked_through_ms': 100, 'checked_distance': 10.0,
            'piercing_loss': 1.0, 'penetration_factor': 0.9,
            'destructibles': [_destructible()],
        }
        message = {
            'type': 'projectile_progress', 'round_id': 1,
            'authority_epoch': 1, 'cursors': [cursor],
        }
        invalid = dict(message, cursors=[dict(
            cursor, destructibles=[_destructible(is_shot=False)])])
        before_revision = state.projectile_revision
        self.assertFalse(state.progress_projectiles(1, invalid))
        self.assertEqual(0,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(before_revision, state.projectile_revision)
        self.assertEqual(0, state.destructible_revision)
        self.assertFalse(state.destructibles)

        self.assertTrue(state.progress_projectiles(1, message))
        self.assertEqual(100,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(1, len(state.destructibles))
        events = len(state.pending_events)
        self.assertTrue(state.progress_projectiles(1, dict(message)))
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(events, len(state.pending_events))

    def test_progress_destructible_total_batch_cap_is_sixty_four(self):
        state = _state(players=3)
        self.assertTrue(state.launch_projectile(1, _launch()))
        self.assertTrue(state.launch_projectile(
            2, _launch(shooter_id=2, shot_seq=1)))
        first = {
            'projectile_id': '1:p:1:1', 'base_checked_ms': 0,
            'checked_through_ms': 1, 'checked_distance': 1.0,
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'destructibles': [_destructible(index, 1)
                              for index in range(33)],
        }
        second = {
            'projectile_id': '1:p:2:1', 'base_checked_ms': 0,
            'checked_through_ms': 1, 'checked_distance': 1.0,
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'destructibles': [_destructible(index + 100, 1)
                              for index in range(32)],
        }
        message = {
            'type': 'projectile_progress', 'round_id': 1,
            'authority_epoch': 1, 'cursors': [first, second],
        }
        self.assertFalse(state.progress_projectiles(1, message))
        self.assertEqual(0, state.destructible_revision)
        self.assertEqual(0,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(0,
                         state.projectiles['1:p:2:1']['checked_through_ms'])

    def test_resolve_destructibles_validate_before_any_terminal_change(self):
        state = _state()
        self.assertTrue(state.launch_projectile(1, _launch()))
        message = _resolve(
            '1:p:1:1', destructibles=[_destructible()])
        invalid = dict(message, destructibles=[_destructible(
            destructible_kind='unknown')])
        before_revision = state.projectile_revision
        before_events = len(state.pending_events)
        self.assertFalse(state.resolve_projectile(1, invalid))
        self.assertEqual(1000, state.players[2].health)
        self.assertIn('1:p:1:1', state.projectiles)
        self.assertEqual(before_revision, state.projectile_revision)
        self.assertEqual(before_events, len(state.pending_events))
        self.assertEqual(0, state.destructible_revision)

        self.assertTrue(state.resolve_projectile(1, message))
        self.assertEqual(900, state.players[2].health)
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(1, len(state.destructibles))
        events = len(state.pending_events)
        self.assertTrue(state.resolve_projectile(1, dict(message)))
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(events, len(state.pending_events))

    def test_authority_handoff_keeps_bot_projectile_for_new_epoch(self):
        state = _state()
        state.bot_states[16] = {
            'id': 16, 'team': 2, 'alive': True, 'fire_seq': 1,
            'shell_index': 0, 'health': 1000, 'max_health': 1000,
            'vehicle': 'ussr:R11_MS-1',
            'x': 20.0, 'y': 0.0, 'z': 0.0,
        }
        state.bot_pending_projectile_launches.add((16, 1))
        self.assertTrue(state.launch_projectile(
            1, _launch(shooter_id=16, shooter_kind='bot', shot_seq=1)))

        state.remove_player(1)
        self.assertEqual(2, state.bot_authority_id)
        self.assertEqual(2, state.authority_epoch)
        self.assertIn('1:b:16:1', state.projectiles)
        snapshot = state._projectile_snapshot()
        self.assertEqual(2, snapshot[0]['authority_epoch'])
        self.assertIn('launch_server_time_ms', snapshot[0])
        self.assertIn('checked_distance', snapshot[0])
        self.assertIn('piercing_loss', snapshot[0])

        miss = _resolve(
            '1:b:16:1', epoch=2, outcome='miss', impact=None,
            direct=None, splash=[], checked_distance=5.0)
        self.assertTrue(state.resolve_projectile(2, miss))

    def test_resolve_is_atomic_idempotent_and_preserves_hit_contract(self):
        state = _state()
        self.assertTrue(state.launch_projectile(1, _launch()))
        message = _resolve('1:p:1:1')

        self.assertTrue(state.resolve_projectile(1, message))
        self.assertEqual(900, state.players[2].health)
        self.assertNotIn('1:p:1:1', state.projectiles)
        self.assertEqual('impact',
                         state.projectile_tombstones['1:p:1:1']['outcome'])
        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_impact', 'hit'],
                         [event['kind'] for event in events])
        self.assertEqual('shot', events[-1]['source'])

        event_count = len(state.pending_events)
        self.assertTrue(state.resolve_projectile(1, dict(message)))
        self.assertEqual(900, state.players[2].health)
        self.assertEqual(event_count, len(state.pending_events))
        self.assertFalse(state.resolve_projectile(
            1, dict(message, checked_distance=11.0)))

    def test_he_direct_target_cannot_repeat_in_splash(self):
        state = _state(players=3)
        launch = _launch(
            is_he=True, splash_radius=15.0,
            penetration_factor=0.0)
        self.assertTrue(state.launch_projectile(1, launch))
        message = _resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=_effect(target_id=2, damage=50),
            splash=[_effect(target_id=2, damage=40, x=10.0)])
        before = [state.players[index].health for index in (2, 3)]
        self.assertFalse(state.resolve_projectile(1, message))
        self.assertEqual(before,
                         [state.players[index].health for index in (2, 3)])
        self.assertIn('1:p:1:1', state.projectiles)

        message['splash'] = [_effect(target_id=3, damage=40, x=20.0)]
        self.assertTrue(state.resolve_projectile(1, message))
        self.assertEqual([950, 960],
                         [state.players[index].health for index in (2, 3)])

    def test_invalid_nth_effect_is_atomic_even_when_targets_are_distinct(self):
        state = _state(players=3)
        state.players[3].x = 12.0
        self.assertTrue(state.launch_projectile(1, _launch(
            is_he=True, splash_radius=15.0, penetration_factor=0.0)))
        message = _resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=_effect(target_id=2, damage=50),
            splash=[_effect(target_id=3, damage=40, x=12.0),
                    _effect(target_id=999, damage=30)])

        self.assertFalse(state.resolve_projectile(1, message))
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(1000, state.players[3].health)
        self.assertIn('1:p:1:1', state.projectiles)

    def test_expiration_result_and_reset_lifecycle(self):
        state = _state()
        self.assertTrue(state.launch_projectile(
            1, _launch(max_time_ms=100)))
        state.tick += 3
        self.assertEqual(1, state._expire_projectiles())
        self.assertNotIn('1:p:1:1', state.projectiles)
        self.assertEqual('expired',
                         state.projectile_tombstones['1:p:1:1']['outcome'])

        self.assertTrue(state.launch_projectile(2, _launch(
            shooter_id=2, shot_seq=1, max_time_ms=10000)))
        self.assertTrue(state._finish_battle(1, 'test'))
        self.assertFalse(state.projectiles)
        self.assertEqual('battle_finished',
                         state.projectile_tombstones['1:p:2:1']['outcome'])
        state._reset_round()
        self.assertFalse(state.projectiles)
        self.assertFalse(state.projectile_tombstones)
        self.assertEqual(0, state.projectile_revision)

    def test_player_disconnect_and_leave_do_not_cancel_fired_projectile(self):
        disconnected = _state()
        self.assertTrue(disconnected.launch_projectile(2, _launch(
            shooter_id=2, shot_seq=1)))
        disconnected.remove_player(2)
        self.assertIn('1:p:2:1', disconnected.projectiles)
        self.assertTrue(disconnected.resolve_projectile(
            1, _resolve('1:p:2:1', direct=None, outcome='miss',
                        impact=None, checked_distance=1.0)))

        left = _state()
        self.assertTrue(left.launch_projectile(2, _launch(
            shooter_id=2, shot_seq=1)))
        self.assertTrue(left.leave_battle(2, {
            'round_id': left.round_id}))
        self.assertIn('1:p:2:1', left.projectiles)
        self.assertTrue(left.resolve_projectile(
            1, _resolve('1:p:2:1', direct=None, outcome='miss',
                        impact=None, checked_distance=1.0)))

    def test_disconnected_shooter_projectile_still_applies_damage(self):
        state = _state()
        self.assertTrue(state.launch_projectile(1, _launch()))

        state.remove_player(1)

        self.assertEqual(2, state.bot_authority_id)
        self.assertEqual(2, state.authority_epoch)
        self.assertIn('1:p:1:1', state.projectiles)
        self.assertTrue(state.resolve_projectile(
            2, _resolve('1:p:1:1', epoch=2)))
        self.assertEqual(900, state.players[2].health)
        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_impact', 'hit'],
                         [event['kind'] for event in events])

    def test_modern_legacy_hits_reject_but_082_remains_compatible(self):
        modern = _state()
        modern.players[1].fire_seq = 1
        hit = {'round_id': 1, 'target': 2, 'shot_seq': 1, 'damage': 1}
        self.assertFalse(modern.report_hit(1, hit))
        self.assertFalse(modern.report_bot_hit(1, hit))

        legacy = _state()
        legacy.client_build = CLIENT_BUILD_082
        legacy.players[1].fire_seq = 1
        self.assertTrue(legacy.report_hit(1, hit))
        self.assertEqual(999, legacy.players[2].health)
        legacy.update_input(1, {'round_id': 1, 'fire_seq': 2})
        self.assertEqual(2, legacy.players[1].fire_seq)
        self.assertEqual('shot', legacy.pending_events[-1]['kind'])

    def test_capability_and_active_snapshot_wire_bound(self):
        modern = BattleState(map_name='04_himmelsdorf')
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P'})
        self.assertIsNone(player)
        self.assertEqual('unsupported_capabilities', error)
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P',
            'capabilities': [PROJECTILE_CAPABILITY]})
        self.assertIsNotNone(player)
        self.assertIsNone(error)

        legacy = BattleState(map_name='04_himmelsdorf')
        player, error = legacy.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_082, 'name': 'P'})
        self.assertIsNotNone(player)
        self.assertIsNone(error)

        shooter_count = PROJECTILE_MAX_ACTIVE // 32
        state = _state(players=shooter_count)
        for shooter_id in range(1, shooter_count + 1):
            for shot_seq in range(1, 33):
                self.assertTrue(state.launch_projectile(
                    shooter_id, _launch(
                        shooter_id=shooter_id, shot_seq=shot_seq,
                        origin=[float(shooter_id), 1.0, 0.0])))
        self.assertEqual(PROJECTILE_MAX_ACTIVE, len(state.projectiles))
        snapshot = {
            'type': 'snapshot', 'protocol': 5, 'server_tick': state.tick,
            'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'server_time_ms': state._server_time_ms(),
            'projectile_revision': state.projectile_revision,
            'projectiles': state._projectile_snapshot(),
        }
        payload = (json.dumps(snapshot, separators=(',', ':')) + '\n').encode()
        self.assertLessEqual(len(payload), MAX_LINE_BYTES)

    def test_current_battle_message_includes_modern_authority_ledger(self):
        state = _state(players=1)
        state.phase = 'battle'
        state.authority_status = 'failed'
        state.authority_fallback_reason = 'world_data_unavailable'
        self.assertTrue(state.launch_projectile(1, _launch()))

        message = state.current_battle_message()

        self.assertEqual(state.authority_epoch, message['authority_epoch'])
        self.assertEqual(state.projectile_revision,
                         message['projectile_revision'])
        self.assertEqual(state._projectile_snapshot(), message['projectiles'])
        self.assertEqual('failed', message['authority_status'])
        self.assertEqual('world_data_unavailable',
                         message['authority_fallback_reason'])

    def test_launch_event_pitch_uses_physical_positive_up_convention(self):
        state = _state(players=1)

        self.assertTrue(state.launch_projectile(
            1, _launch(velocity=[0.0, 100.0, 425.0], gravity=143.0)))
        event = state.pending_events[-1]
        self.assertGreater(event['shot_pitch'], 0.0)
        self.assertAlmostEqual(
            math.atan2(100.0, 425.0), event['shot_pitch'], places=6)

    def test_modern_events_and_snapshot_share_current_tick_time_and_epoch(self):
        state = _state(players=1)
        self.assertTrue(state.launch_projectile(1, _launch()))
        broadcasts = []
        snapshots = []
        state.broadcast = lambda message: broadcasts.append(message)
        state.players[1].send = lambda message: snapshots.append(message) or True
        samples = iter((40000, 40017))
        state._server_time_ms = lambda: next(samples)

        state.tick_once(1.0 / TICK_HZ)

        events = [message for message in broadcasts
                  if message.get('type') == 'events']
        self.assertEqual(1, len(events))
        self.assertEqual(40017, events[0]['server_time_ms'])
        self.assertEqual(state.authority_epoch,
                         events[0]['authority_epoch'])
        self.assertEqual(40017, snapshots[-1]['server_time_ms'])
        self.assertEqual(events[0]['server_time_ms'],
                         snapshots[-1]['server_time_ms'])
        self.assertEqual(events[0]['authority_epoch'],
                         snapshots[-1]['authority_epoch'])

    def test_legacy_events_envelope_remains_082_compatible(self):
        state = _state(players=1)
        state.client_build = CLIENT_BUILD_082
        state.update_input(1, {
            'round_id': state.round_id, 'fire_seq': 1,
        })
        broadcasts = []
        state.broadcast = lambda message: broadcasts.append(message)
        state.players[1].send = lambda unused_message: True

        state.tick_once(1.0 / TICK_HZ)

        events = [message for message in broadcasts
                  if message.get('type') == 'events']
        self.assertEqual(1, len(events))
        self.assertNotIn('server_time_ms', events[0])
        self.assertNotIn('authority_epoch', events[0])


if __name__ == '__main__':
    unittest.main()
