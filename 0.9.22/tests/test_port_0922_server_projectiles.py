import json
import math
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_082, CLIENT_BUILD_0922, ClientHandler,
    MAX_LINE_BYTES,
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY, PREBATTLE_SECONDS,
    HUMAN_RAM_TIMELINE_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    PLAYER_ENVIRONMENT_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY,
    PROJECTILE_CAPABILITY, PROJECTILE_MAX_ACTIVE,
    Player, SimulationWorker, SIMULATION_WORKER_AUTHORITY_ID,
    SIEGE_DISABLED, SIEGE_ENABLED, SIEGE_SWITCHING_OFF,
    SIEGE_SWITCHING_ON, SIEGE_VEHICLE_PARAMS, TICK_HZ,
)
from effective_params_fixture import effective_params


class _Socket(object):
    def sendall(self, unused_payload):
        pass


def _player(player_id, team=1, x=0.0):
    return Player(
        player_id, _Socket(), ('127.0.0.1', player_id), team=team,
        slot=(player_id - 1) % 15, x=x, client_position=True,
        capabilities=(
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            HUMAN_RAM_TIMELINE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
            PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY,
            EFFECTIVE_PARAMS_CAPABILITY),
        effective_params=effective_params())


def _state(players=2):
    state = BattleState(map_name='04_himmelsdorf')
    state.client_build = CLIENT_BUILD_0922
    state.phase = 'battle'
    state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
    for player_id in range(1, players + 1):
        state.players[player_id] = _player(
            player_id, 1 if player_id % 2 else 2,
            float(player_id - 1) * 10.0)
    _attach_worker_authority(state)
    state.authority_epoch = 1
    return state


def _gun_checkpoint(reload_time=0.0, clip=1, clip_size=1,
                    dispersion=0.02, reload_duration=5.0):
    return {
        'reload_time': float(reload_time),
        'reload_duration': float(reload_duration),
        'clip': int(clip), 'clip_size': int(clip_size),
        'dispersion': float(dispersion),
    }


def _attach_worker_authority(state):
    state.simulation_worker = SimulationWorker(
        _Socket(), ('127.0.0.1', 28782), capabilities=(
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            HUMAN_RAM_TIMELINE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
            PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY,
            EFFECTIVE_PARAMS_CAPABILITY))
    state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
    state.simulation_worker.offer_reliable = lambda unused_message: True
    return state.simulation_worker


def _update_player_input(state, player_id, **changes):
    player = state.players[player_id]
    message = {
        'type': 'input', 'round_id': state.round_id,
        'input_seq': player.input_seq + 1,
        'pose_time_us': state._logical_motion_time_us(),
        'forward': 0.0, 'turn': 0.0, 'speed': 0.0,
        'aim_yaw': player.aim_yaw, 'gun_pitch': player.gun_pitch,
        'x': player.x, 'y': player.y, 'z': player.z,
        'yaw': player.yaw, 'pitch': player.pitch, 'roll': player.roll,
        'fire_seq': player.fire_seq, 'shell_index': player.shell_index,
        'next_shell_index': player.next_shell_index,
        'shell_change_pending': player.shell_change_pending,
        'gun_checkpoint': _gun_checkpoint(),
    }
    message.update(changes)
    return state.update_input(player_id, message)


def _fire_intent(state, player_id=1, **changes):
    player = state.players[player_id]
    message = {
        'type': 'fire_intent', 'round_id': state.round_id,
        'intent_seq': player.fire_intent_seq + 1,
        'input_seq': player.input_seq, 'shell_index': player.shell_index,
        'shot_origin': [player.x, player.y + 1.0, player.z],
        'shot_direction': [0.0, 0.0, 1.0],
        'dispersion_angle': 0.01,
    }
    message.update(changes)
    return message


def _source_shot(speed, gravity, maximum, is_he=False, radius=0.0,
                 damage=(390.0, 150.0), deadeye=False):
    return {
        'speed': speed,
        'gravity': gravity,
        'maxDistance': maximum,
        'piercingPower': [220.0, 200.0],
        'deadeye': bool(deadeye),
        'shell': {
            'kind': 'HIGH_EXPLOSIVE' if is_he else 'ARMOR_PIERCING',
            'caliber': 105.0,
            'damage': list(damage),
            'explosionRadius': radius,
        },
    }


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
    message.update(changes)
    if 'source_shot' not in message:
        message['source_shot'] = _source_shot(
            math.sqrt(sum(component * component
                          for component in message['velocity'])),
            message['gravity'], message['max_distance'],
            message['is_he'], message['splash_radius'])
    if shooter_kind == 'bot':
        message['authority_epoch'] = 1
    return message


def _launch_authority(state, message):
    """Admit a player trigger, then let only worker -1 launch it."""
    if message.get('shooter_kind') == 'bot':
        return state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, message)
    if 'fire_intent_seq' not in message:
        player_id = int(message['shooter_id'])
        player = state.players[player_id]
        input_seq = player.input_seq + 1
        self_time = state._logical_motion_time_us()
        if not state.update_input(player_id, {
                'type': 'input', 'round_id': state.round_id,
                'input_seq': input_seq, 'pose_time_us': self_time,
                'forward': 0.0, 'turn': 0.0, 'speed': 0.0,
                'aim_yaw': player.aim_yaw, 'gun_pitch': player.gun_pitch,
                'x': player.x, 'y': player.y, 'z': player.z,
                'yaw': player.yaw, 'pitch': player.pitch,
                'roll': player.roll, 'fire_seq': player.fire_seq,
                'shell_index': message['shell_index'],
                'next_shell_index': message['shell_index'],
                'shell_change_pending': False,
                'gun_checkpoint': _gun_checkpoint()}):
            return False
        intent_seq = player.fire_intent_seq + 1
        launch_speed = math.sqrt(sum(
            component * component for component in message['velocity']))
        if not state.submit_fire_intent(player_id, _fire_intent(
                state, player_id, intent_seq=intent_seq,
                input_seq=input_seq,
                shell_index=message['shell_index'],
                shot_origin=list(message['origin']),
                shot_direction=[
                    component / launch_speed
                    for component in message['velocity']],
                dispersion_angle=0.0)):
            return False
        relay = player.pending_fire_intents[intent_seq]
        message.update({
            'authority_epoch': state.authority_epoch,
            'shot_seq': relay['shot_seq'],
            'fire_intent_seq': intent_seq,
            'fire_input_seq': input_seq,
        })
    return state.launch_projectile(
        SIMULATION_WORKER_AUTHORITY_ID, message)


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
    def test_1513_siege_transition_is_server_owned_and_caps_speed(self):
        state = _state()
        player = state.players[1]
        player.vehicle = 'sweden:S21_UDES_03'

        _update_player_input(
            state, 1, siege_enabled=True, speed=99.0)

        self.assertEqual(SIEGE_SWITCHING_ON, player.siege_state)
        self.assertEqual(60, player.siege_transition_ticks)
        self.assertEqual(2000, state._public_player(
            player)['siege_time_left_ms'])
        for unused_tick in range(59):
            state._advance_siege_states()
        self.assertEqual(SIEGE_SWITCHING_ON, player.siege_state)
        state._advance_siege_states()
        self.assertEqual(SIEGE_ENABLED, player.siege_state)

        _update_player_input(state, 1, speed=99.0)
        self.assertAlmostEqual(5.0 / 3.6, player.speed)
        _update_player_input(state, 1, siege_enabled=False)
        self.assertEqual(SIEGE_SWITCHING_OFF, player.siege_state)
        for unused_tick in range(60):
            state._advance_siege_states()
        self.assertEqual(SIEGE_DISABLED, player.siege_state)
        self.assertEqual(0, state._public_player(
            player)['siege_time_left_ms'])

    def test_1513_siege_vehicle_table_matches_pinned_xml(self):
        self.assertEqual(
            (2.0, 1.3, 10.0 / 3.6, 2.0),
            SIEGE_VEHICLE_PARAMS['sweden:S10_Strv_103_0_Series'])
        self.assertEqual(
            (2.0, 1.3, 10.0 / 3.6, 2.0),
            SIEGE_VEHICLE_PARAMS['sweden:S11_Strv_103B'])
        self.assertEqual(
            (2.0, 2.0, 5.0 / 3.6, 2.0),
            SIEGE_VEHICLE_PARAMS['sweden:S21_UDES_03'])
        self.assertEqual(
            (2.0, 1.3, 8.0 / 3.6, 2.0),
            SIEGE_VEHICLE_PARAMS['sweden:S22_Strv_S1'])

    def test_siege_request_rejects_non_bool_and_destroyed_engine(self):
        state = _state()
        player = state.players[1]
        player.vehicle = 'sweden:S11_Strv_103B'

        _update_player_input(state, 1, siege_enabled=1)
        self.assertEqual(SIEGE_DISABLED, player.siege_state)
        player.critical = {
            'destroyed': ['engineHealth'], 'devices': []}
        _update_player_input(state, 1, siege_enabled=True)
        self.assertEqual(SIEGE_DISABLED, player.siege_state)

    def test_damaged_engine_uses_pinned_siege_transition_coefficient(self):
        state = _state()
        player = state.players[1]
        player.vehicle = 'sweden:S11_Strv_103B'
        player.critical = {
            'destroyed': [],
            'devices': [{
                'name': 'engineHealth', 'state': 'critical',
                'hp': 20.0, 'max_hp': 100.0,
            }],
        }

        _update_player_input(state, 1, siege_enabled=True)

        self.assertEqual(SIEGE_SWITCHING_ON, player.siege_state)
        self.assertEqual(120, player.siege_transition_ticks)

    def test_player_projectile_is_rejected_while_siege_mode_switches(self):
        state = _state()
        player = state.players[1]
        player.vehicle = 'sweden:S22_Strv_S1'
        player.siege_state = SIEGE_SWITCHING_ON

        self.assertFalse(_launch_authority(state, _launch()))
        enabled = _state()
        enabled.players[1].vehicle = 'sweden:S22_Strv_S1'
        enabled.players[1].siege_state = SIEGE_ENABLED
        self.assertTrue(_launch_authority(enabled, _launch()))

    def test_modern_player_launch_is_atomic_and_idempotent(self):
        state = _state()
        message = _launch()

        self.assertTrue(_launch_authority(state, message))
        self.assertEqual(1, state.players[1].fire_seq)
        self.assertEqual(['1:p:1:1'], sorted(state.projectiles))
        shot = state.pending_events[-1]
        self.assertEqual('ussr:R11_MS-1', shot['source_vehicle'])
        self.assertEqual(message['source_shot'], shot['source_shot'])
        self.assertEqual(
            'ussr:R11_MS-1',
            state._projectile_snapshot()[0]['source_vehicle'])
        self.assertEqual(
            message['source_shot'],
            state._projectile_snapshot()[0]['source_shot'])
        self.assertEqual('shot', shot['kind'])
        self.assertEqual([0.0, 1.0, 0.0], shot['origin'])
        self.assertEqual([100.0, 0.0, 0.0], shot['velocity'])
        self.assertEqual(1.570796, shot['shot_yaw'])
        self.assertEqual(state._server_time_ms(),
                         shot['launch_server_time_ms'])

        event_count = len(state.pending_events)
        self.assertTrue(_launch_authority(state, dict(message)))
        self.assertEqual(event_count, len(state.pending_events))
        self.assertFalse(state.launch_projectile(
            1, dict(message, velocity=[101.0, 0.0, 0.0])))
        self.assertFalse(state.launch_projectile(
            1, _launch(shot_seq=3)))
        self.assertEqual(1, state.players[1].fire_seq)

    def test_player_fire_intent_freezes_admitted_input_and_retries_exactly(self):
        state = _state()
        player = state.players[1]
        relayed = []
        state.simulation_worker.offer_reliable = lambda message: (
            relayed.append(dict(message)) or True)
        self.assertTrue(_update_player_input(
            state, 1, x=3.25, y=1.5, z=-4.75, yaw=0.25,
            aim_yaw=-0.5, gun_pitch=0.125))
        message = _fire_intent(
            state, 1, shot_origin=[3.25, 2.5, -4.75])

        self.assertTrue(state.submit_fire_intent(1, message))
        self.assertEqual(1, len(relayed))
        relay = relayed[0]
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.bot_authority_id)
        self.assertEqual(1, relay['intent_seq'])
        self.assertEqual(1, relay['shot_seq'])
        self.assertEqual(player.input_seq, relay['input_seq'])
        self.assertEqual(player.pose_time_us, relay['pose_time_us'])
        self.assertEqual((3.25, 1.5, -4.75),
                         (relay['x'], relay['y'], relay['z']))
        self.assertEqual((-0.5, 0.125),
                         (relay['aim_yaw'], relay['gun_pitch']))
        self.assertEqual([3.25, 2.5, -4.75], relay['shot_origin'])
        self.assertEqual([0.0, 0.0, 1.0], relay['shot_direction'])
        self.assertEqual(0.01, relay['dispersion_angle'])
        self.assertEqual(0, relay['next_shell_index'])
        self.assertFalse(relay['shell_change_pending'])
        self.assertEqual(player.input_seq, relay['gun_checkpoint_seq'])
        self.assertEqual(_gun_checkpoint(), relay['gun_checkpoint'])
        self.assertNotIn('deadline_server_time_ms', relay)

        self.assertTrue(state.submit_fire_intent(1, dict(message)))
        self.assertEqual(1, len(relayed))
        self.assertFalse(state.submit_fire_intent(
            1, dict(message, shell_index=1)))
        self.assertFalse(state.submit_fire_intent(
            1, dict(message, intent_seq=3)))
        self.assertFalse(state.submit_fire_intent(
            1, dict(message, shot_direction=[1.0, 0.0, 0.0])))
        self.assertEqual([1], list(player.pending_fire_intents))
        launch = _launch(
            origin=list(relay['shot_origin']), velocity=[100.0, 0.0, 0.0],
            authority_epoch=state.authority_epoch,
            fire_intent_seq=relay['intent_seq'],
            fire_input_seq=relay['input_seq'])
        self.assertFalse(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, launch))

    def test_player_fire_intent_survives_worker_stall_beyond_five_seconds(self):
        state = _state(players=1)
        now_ms = [1000]
        state._server_time_ms = lambda: now_ms[0]
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        player = state.players[1]
        relay = player.pending_fire_intents[1]

        now_ms[0] += 6001
        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _launch(
                origin=list(relay['shot_origin']),
                velocity=[0.0, 0.0, 100.0],
                authority_epoch=state.authority_epoch,
                fire_intent_seq=relay['intent_seq'],
                fire_input_seq=relay['input_seq'])))

        projectile_id = state._projectile_id(1, 'player', 1, 1)
        self.assertNotIn(1, player.pending_fire_intents)
        self.assertEqual(
            (True, projectile_id), player.fire_intent_results[1])
        self.assertIn(projectile_id, state.projectiles)

    def test_late_next_gun_checkpoint_is_kept_and_old_retry_cannot_replace_it(self):
        state = _state(players=1)
        player = state.players[1]
        first = _gun_checkpoint(reload_time=1.0, clip=0)
        second = _gun_checkpoint(reload_time=0.0, clip=1)

        self.assertTrue(_update_player_input(
            state, 1, gun_checkpoint=first))
        first_message = json.loads(player.input_fingerprints[1])
        # The fingerprint is JSON, so retry the original semantic input
        # through the helper's frozen server record instead of reusing it.
        self.assertTrue(_update_player_input(
            state, 1, gun_checkpoint=second))
        self.assertEqual(2, player.gun_checkpoint_seq)
        self.assertEqual(second, player.gun_checkpoint)
        self.assertEqual(first, player.gun_checkpoints[1])
        self.assertEqual(second, player.gun_checkpoints[2])

        # An exact retry of input 1 remains idempotent but cannot roll the
        # public checkpoint back from the already-admitted input 2.
        self.assertTrue(state.update_input(1, first_message))
        self.assertEqual(2, player.gun_checkpoint_seq)
        self.assertEqual(second, player.gun_checkpoint)
        self.assertFalse(state.update_input(1, {
            'type': 'input', 'round_id': state.round_id,
            'input_seq': 3, 'pose_time_us': state._logical_motion_time_us(),
            'shell_index': 0, 'next_shell_index': 0,
            'shell_change_pending': False,
        }))
        self.assertEqual(2, player.input_seq)

    def test_player_queued_shell_is_promoted_by_the_canonical_shot(self):
        state = _state()
        player = state.players[1]
        self.assertTrue(_update_player_input(
            state, 1, shell_index=0, next_shell_index=1,
            shell_change_pending=True))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state, shot_direction=[1.0, 0.0, 0.0])))
        relay = player.pending_fire_intents[1]
        self.assertEqual(1, relay['next_shell_index'])
        self.assertTrue(relay['shell_change_pending'])

        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, _launch(
                authority_epoch=state.authority_epoch,
                fire_intent_seq=relay['intent_seq'],
                fire_input_seq=relay['input_seq'])))

        self.assertEqual(1, player.shell_index)
        self.assertEqual(1, player.next_shell_index)
        self.assertFalse(player.shell_change_pending)
        public = state._public_player(player)
        self.assertEqual(1, public['shell_index'])
        self.assertEqual(1, public['next_shell_index'])
        self.assertFalse(public['shell_change_pending'])

    def test_player_fire_intent_rejects_untrusted_trigger_rays(self):
        state = _state()
        self.assertTrue(_update_player_input(state, 1))
        invalid = (
            {'shot_origin': (0.0, 1.0, 0.0)},
            {'shot_origin': [100.0, 1.0, 0.0]},
            {'shot_direction': [0.0, 0.0, 0.0]},
            {'shot_direction': [0.0, 0.0, 0.5]},
            {'shot_direction': [float('nan'), 0.0, 1.0]},
            {'dispersion_angle': float('inf')},
            {'dispersion_angle': 0.5001},
        )

        for changes in invalid:
            self.assertFalse(state.submit_fire_intent(
                1, _fire_intent(state, **changes)), changes)
        self.assertEqual(0, state.players[1].fire_intent_seq)

    def test_worker_fire_rejection_is_committed_after_visible_delivery(self):
        state = _state()
        player = state.players[1]
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(
            1, _fire_intent(state)))
        delivered = []
        player.offer_reliable = lambda message: (
            delivered.append(dict(message)) or True)
        rejection = {
            'type': 'fire_intent_result', 'round_id': state.round_id,
            'authority_epoch': state.authority_epoch, 'player_id': 1,
            'intent_seq': 1, 'accepted': False, 'reason': 'gun_not_ready',
        }

        self.assertTrue(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID, rejection))
        self.assertEqual([{
            'type': 'fire_intent_result', 'round_id': state.round_id,
            'intent_seq': 1, 'accepted': False, 'reason': 'gun_not_ready',
        }], delivered)
        self.assertNotIn(1, player.pending_fire_intents)
        self.assertEqual((False, 'gun_not_ready'),
                         player.fire_intent_results[1])
        self.assertTrue(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID, dict(rejection)))
        self.assertEqual(1, len(delivered))
        self.assertFalse(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(rejection, reason='different')))

    def test_failed_fire_rejection_delivery_disconnects_without_commit(self):
        state = _state()
        player = state.players[1]
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(
            1, _fire_intent(state)))
        player.offer_reliable = lambda unused_message: False

        self.assertFalse(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'fire_intent_result',
                'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'player_id': 1, 'intent_seq': 1,
                'accepted': False, 'reason': 'gun_not_ready',
            }))
        self.assertNotIn(1, state.players)
        self.assertEqual({}, player.fire_intent_results)

    def test_rejected_worker_player_launch_terminates_both_endpoints(self):
        state = _state()
        player = state.players[1]
        worker = state.simulation_worker
        player_messages = []
        worker_messages = []
        player.offer_reliable = lambda message: (
            player_messages.append(dict(message)) or True)
        worker.offer_reliable = lambda message: (
            worker_messages.append(dict(message)) or True)
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(
            1, _fire_intent(state)))
        relay = worker_messages.pop()
        launch = _launch(origin=[100.0, 1.0, 0.0])
        launch.update({
            'authority_epoch': state.authority_epoch,
            'shot_seq': relay['shot_seq'],
            'fire_intent_seq': relay['intent_seq'],
            'fire_input_seq': relay['input_seq'],
        })
        handler = object.__new__(ClientHandler)

        self.assertFalse(handler._dispatch_simulation_worker_message(
            types.SimpleNamespace(state=state), worker, launch))

        terminal = {
            'type': 'fire_intent_result', 'round_id': state.round_id,
            'player_id': 1, 'intent_seq': 1, 'accepted': False,
            'reason': 'projectile_launch_rejected',
        }
        self.assertEqual([terminal], player_messages)
        self.assertEqual([terminal], worker_messages)
        self.assertNotIn(1, player.pending_fire_intents)
        self.assertEqual(
            (False, 'projectile_launch_rejected'),
            player.fire_intent_results[1])
        self.assertFalse(state.projectiles)

    def test_launch_rejects_malformed_or_inconsistent_source_shot(self):
        valid = _launch()
        invalid = []
        for mutate in (
                lambda shot: shot.update(speed=101.0),
                lambda shot: shot.update(gravity='9.81'),
                lambda shot: shot.update(extra=1),
                lambda shot: shot['shell'].update(damage=[390.0]),
                lambda shot: shot['shell'].update(kind='HIGH_EXPLOSIVE'),
                lambda shot: shot['shell'].update(explosionRadius=4.0),
                lambda shot: shot['shell'].update(caliber=True)):
            candidate = json.loads(json.dumps(valid))
            mutate(candidate['source_shot'])
            invalid.append(candidate)
        missing = json.loads(json.dumps(valid))
        missing.pop('source_shot')
        invalid.append(missing)

        for message in invalid:
            with self.subTest(message=message):
                state = _state()
                self.assertFalse(_launch_authority(state, message))
                self.assertEqual(0, state.players[1].fire_seq)
                self.assertFalse(state.projectiles)

    def test_retail_spg_gravity_is_admitted_and_snapshotted(self):
        state = _state()

        self.assertTrue(_launch_authority(
            state, _launch(gravity=143.0)))
        self.assertEqual(143.0,
                         state.projectiles['1:p:1:1']['gravity'])
        self.assertEqual(143.0,
                         state._projectile_snapshot()[0]['gravity'])

    def test_bot_state_edge_waits_for_authorized_launch(self):
        state = _state()
        state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_roster = [{
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Bot',
        }]
        manifest = [{
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'health': 1000,
            'max_health': 1000, 'x': 20.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'world_pose': True, 'profile': {},
            'reload_time': 0.0, 'reload_duration': 1.5,
            'route': {'id': 'test', 'waypoints': []},
        }]
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID, {
            'round_id': 1, 'bots': manifest,
            'player_collision_profiles': [
                {
                    'id': player.player_id, 'vehicle': player.vehicle,
                    'mass': player.effective_params['physics']['mass'],
                    'shape': [3.0, 6.0, -1.0, 2.0],
                    'ram_profile': {
                        'spall_coefficient': player.effective_params[
                            'ramming']['spall_coefficient'],
                        'ramming_bonus': player.effective_params[
                            'ramming']['ramming_bonus'],
                    },
                }
                for player in state.players.values()
            ]}))
        state.pending_events[:] = []
        publication = {
            'id': 16, 'x': 20.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'health': 1000, 'alive': True, 'fire_seq': 1,
            'reload_time': 0.0, 'reload_duration': 1.5,
            'critical': {}, 'combat_base_revision': 0, 'combat_seq': 0,
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        }
        self.assertTrue(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
            'round_id': 1, 'bots': [publication]}),
            state.last_bot_state_reject)
        self.assertFalse(any(event.get('kind') == 'bot_shot'
                             for event in state.pending_events))

        launch = _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=1,
            origin=[20.0, 1.0, 0.0])
        self.assertFalse(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(launch, authority_epoch=0)))
        self.assertTrue(_launch_authority(state, launch))
        self.assertEqual('bot_shot', state.pending_events[-1]['kind'])
        self.assertEqual('1:b:16:1',
                         state.pending_events[-1]['projectile_id'])
        self.assertTrue(_launch_authority(state, dict(launch)))
        self.assertFalse(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(launch, gravity=9.9)))

    def test_progress_uses_batch_cas_epoch_and_exact_retry(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        projectile_id = '1:p:1:1'
        cursor = {
            'projectile_id': projectile_id, 'base_checked_ms': 0,
            'checked_through_ms': 200, 'checked_distance': 20.0,
            'piercing_loss': 3.0, 'penetration_factor': 1.0,
            'destructibles': [],
        }
        message = {
            'type': 'projectile_progress', 'round_id': 1,
            'authority_epoch': 1, 'cursors': [cursor],
        }
        self.assertFalse(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(message, authority_epoch=0)))
        self.assertTrue(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertTrue(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, dict(message)))
        record = state.projectiles[projectile_id]
        self.assertEqual(200, record['checked_through_ms'])
        self.assertEqual(20.0, record['checked_distance'])
        self.assertEqual(3.0, record['piercing_loss'])
        self.assertEqual(1.0, record['penetration_factor'])
        stale = dict(cursor, checked_through_ms=201)
        self.assertFalse(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(message, cursors=[stale])))
        self.assertFalse(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, dict(message, cursors=[dict(
                cursor, base_checked_ms=200, checked_through_ms=200,
                penetration_factor=0.999999)])))

    def test_terminal_overtaking_exact_progress_retry_does_not_poison_batch(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(_launch_authority(
            state, _launch(shooter_id=2, shot_seq=1)))
        retired = {
            'projectile_id': '1:p:1:1', 'base_checked_ms': 0,
            'checked_through_ms': 100, 'checked_distance': 10.0,
            'piercing_loss': 1.0, 'penetration_factor': 1.0,
            'destructibles': [],
        }
        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'projectile_progress', 'round_id': 1,
                'authority_epoch': 1, 'cursors': [retired]}))
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, _resolve(
                '1:p:1:1', base_checked_ms=100,
                resolved_time_ms=100, checked_distance=10.0,
                piercing_loss=1.0)))
        active = {
            'projectile_id': '1:p:2:1', 'base_checked_ms': 0,
            'checked_through_ms': 120, 'checked_distance': 12.0,
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'destructibles': [],
        }

        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'projectile_progress', 'round_id': 1,
                'authority_epoch': 1, 'cursors': [retired, active]}))

        self.assertEqual(
            120, state.projectiles['1:p:2:1']['checked_through_ms'])

    def test_progress_destructibles_are_atomic_and_idempotent(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        cursor = {
            'projectile_id': '1:p:1:1', 'base_checked_ms': 0,
            'checked_through_ms': 100, 'checked_distance': 10.0,
            'piercing_loss': 1.0, 'penetration_factor': 1.0,
            'destructibles': [_destructible()],
        }
        message = {
            'type': 'projectile_progress', 'round_id': 1,
            'authority_epoch': 1, 'cursors': [cursor],
        }
        invalid = dict(message, cursors=[dict(
            cursor, destructibles=[_destructible(is_shot=False)])])
        before_revision = state.projectile_revision
        self.assertFalse(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, invalid))
        self.assertEqual(0,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(before_revision, state.projectile_revision)
        self.assertEqual(0, state.destructible_revision)
        self.assertFalse(state.destructibles)

        self.assertTrue(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(100,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(1, len(state.destructibles))
        events = len(state.pending_events)
        self.assertTrue(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, dict(message)))
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(events, len(state.pending_events))

    def test_progress_destructible_total_batch_cap_is_sixty_four(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(_launch_authority(
            state, _launch(shooter_id=2, shot_seq=1)))
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
        self.assertFalse(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(0, state.destructible_revision)
        self.assertEqual(0,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(0,
                         state.projectiles['1:p:2:1']['checked_through_ms'])

    def test_resolve_destructibles_validate_before_any_terminal_change(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        message = _resolve(
            '1:p:1:1', destructibles=[_destructible()])
        invalid = dict(message, destructibles=[_destructible(
            destructible_kind='unknown')])
        before_revision = state.projectile_revision
        before_events = len(state.pending_events)
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, invalid))
        self.assertEqual(1000, state.players[2].health)
        self.assertIn('1:p:1:1', state.projectiles)
        self.assertEqual(before_revision, state.projectile_revision)
        self.assertEqual(before_events, len(state.pending_events))
        self.assertEqual(0, state.destructible_revision)

        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(900, state.players[2].health)
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(1, len(state.destructibles))
        events = len(state.pending_events)
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, dict(message)))
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(events, len(state.pending_events))

    def test_player_disconnect_keeps_bot_projectile_under_worker_epoch(self):
        state = _state()
        _attach_worker_authority(state)
        state.bot_states[16] = {
            'id': 16, 'team': 2, 'alive': True, 'fire_seq': 1,
            'shell_index': 0, 'health': 1000, 'max_health': 1000,
            'vehicle': 'ussr:R11_MS-1',
            'x': 20.0, 'y': 0.0, 'z': 0.0,
        }
        state.bot_pending_projectile_launches.add((16, 1))
        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _launch(shooter_id=16, shooter_kind='bot', shot_seq=1)))

        state.remove_player(1)
        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, state.bot_authority_id)
        self.assertEqual(1, state.authority_epoch)
        self.assertIn('1:b:16:1', state.projectiles)
        snapshot = state._projectile_snapshot()
        self.assertEqual(1, snapshot[0]['authority_epoch'])
        self.assertIn('launch_server_time_ms', snapshot[0])
        self.assertIn('checked_distance', snapshot[0])
        self.assertIn('piercing_loss', snapshot[0])

        miss = _resolve(
            '1:b:16:1', epoch=1, outcome='miss', impact=None,
            direct=None, splash=[], checked_distance=5.0)
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, miss))

    def test_resolve_is_atomic_idempotent_and_preserves_hit_contract(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        message = _resolve('1:p:1:1')

        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(900, state.players[2].health)
        self.assertNotIn('1:p:1:1', state.projectiles)
        self.assertEqual('impact',
                         state.projectile_tombstones['1:p:1:1']['outcome'])
        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_impact', 'hit'],
                         [event['kind'] for event in events])
        self.assertTrue(events[1]['hit_vehicle'])
        self.assertEqual('shot', events[-1]['source'])
        outgoing = state.vehicle_interactions[
            ('player', 1)]['player:2']
        incoming = state.vehicle_interactions[
            ('player', 2)]['player:1']
        self.assertEqual(1, outgoing['direct_hits'])
        self.assertEqual(1, outgoing['piercings'])
        self.assertEqual(100, outgoing['damage'])
        self.assertEqual(100, incoming['damage_received'])

        event_count = len(state.pending_events)
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, dict(message)))
        self.assertEqual(900, state.players[2].health)
        self.assertEqual(event_count, len(state.pending_events))
        self.assertEqual(100, outgoing['damage'])
        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(message, checked_distance=11.0)))

    def test_internal_projectile_stun_is_durable_expires_and_assists(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch()))
        now = state._server_time_ms()
        stun_end = now + 1500
        message = _resolve(
            '1:p:1:1', direct=_effect(
                stun_end_server_time_ms=stun_end))

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, message))

        target = state.players[2]
        self.assertEqual(stun_end, target.stun_end_server_time_ms)
        self.assertEqual(('player', 1), (
            target.stun_attacker_kind, target.stun_attacker_id))
        public = state._public_player(target)
        self.assertEqual(stun_end, public['stun_end_server_time_ms'])
        self.assertEqual('player', public['stun_attacker_kind'])
        stun_events = [event for event in state.pending_events
                       if event.get('kind') == 'stun']
        self.assertEqual([True], [event['active'] for event in stun_events])

        state._record_damage(
            ('player', 3), ('player', 2), 240, target.critical)
        self.assertEqual(
            240, state._statistics_row('player', 1)[
                'damage_assisted_stun'])
        self.assertEqual(
            240, state.vehicle_interactions[
                ('player', 1)]['player:2']['assist_stun'])
        assist = [event for event in state.pending_events
                  if event.get('kind') == 'assist'][-1]
        self.assertEqual('stun', assist['category'])

        state.tick += int(round(1.5 * TICK_HZ))
        self.assertEqual(1, state._expire_stuns())
        self.assertEqual(0, target.stun_end_server_time_ms)
        self.assertEqual('', target.stun_attacker_kind)
        self.assertEqual(0, target.stun_attacker_id)
        self.assertFalse([
            event for event in state.pending_events
            if event.get('kind') == 'stun'][-1]['active'])

    def test_stun_batch_uses_one_frozen_resolution_clock(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=20.0)))
        message = _resolve(
            '1:p:1:1',
            direct=_effect(stun_end_server_time_ms=101),
            splash=[_effect(
                target_id=3, damage=50, x=20.0,
                stun_end_server_time_ms=101)])

        with mock.patch.object(
                state, '_server_time_ms', side_effect=[100]) as clock:
            self.assertTrue(state.resolve_projectile(
                SIMULATION_WORKER_AUTHORITY_ID, message))

        self.assertEqual(1, clock.call_count)
        self.assertEqual(101, state.players[2].stun_end_server_time_ms)
        self.assertEqual(101, state.players[3].stun_end_server_time_ms)

    def test_visible_projectile_authority_cannot_supply_stun_state(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        state.bot_authority_id = 1
        message = _resolve(
            '1:p:1:1', direct=_effect(
                stun_end_server_time_ms=state._server_time_ms() + 1000))

        self.assertFalse(state.resolve_projectile(1, message))
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(0, state.players[2].stun_end_server_time_ms)
        self.assertIn('1:p:1:1', state.projectiles)

    def test_resolve_cannot_lower_the_launch_penetration_roll(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))

        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', penetration_factor=0.75)))

        self.assertIn('1:p:1:1', state.projectiles)
        self.assertEqual(1000, state.players[2].health)

    def test_wreck_terminal_can_have_no_damage_but_still_hit_a_vehicle(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        message = _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True)

        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))

        event = next(
            value for value in state.pending_events
            if value.get('kind') == 'projectile_impact')
        self.assertTrue(event['hit_vehicle'])
        self.assertEqual(1000, state.players[2].health)

    def test_wreck_impact_relays_identity_without_combat_statistics(self):
        state = _state()
        state.players[2].health = 0
        state.players[2].alive = False
        self.assertTrue(_launch_authority(state, _launch()))
        message = _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True,
            wreck_hit={'target_kind': 'player', 'target_id': 2})

        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))

        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_impact'],
                         [event['kind'] for event in events])
        self.assertEqual(
            {'target_kind': 'player', 'target_id': 2},
            events[-1]['wreck_hit'])
        self.assertEqual({}, state.vehicle_interactions)

    def test_wreck_impact_contract_rejects_live_or_damage_targets(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        wreck_hit = {'target_kind': 'player', 'target_id': 2}

        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True,
            wreck_hit=wreck_hit)))
        state.players[2].health = 0
        state.players[2].alive = False
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', hit_vehicle=True, wreck_hit=wreck_hit)))
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True, wreck_hit=None)))
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True,
            wreck_hit={'target_kind': 'player', 'target_id': 99})))
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True,
            wreck_hit=wreck_hit)))

    def test_hit_event_reports_only_damage_the_target_had_left(self):
        state = _state()
        state.players[2].health = 200
        self.assertTrue(_launch_authority(state, _launch()))

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', direct=_effect(damage=400))))

        event = next(
            value for value in state.pending_events
            if value.get('kind') == 'hit')
        self.assertEqual(200, event['damage'])
        self.assertEqual(0, event['health'])
        interaction = state.vehicle_interactions[
            ('player', 1)]['player:2']
        self.assertEqual(200, interaction['damage'])
        self.assertEqual(1, interaction['target_kills'])
        self.assertEqual(0, interaction['death_reason'])

    def test_he_direct_target_cannot_repeat_in_splash(self):
        state = _state(players=3)
        launch = _launch(
            is_he=True, splash_radius=15.0,
            penetration_factor=0.0)
        self.assertTrue(_launch_authority(state, launch))
        message = _resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=_effect(target_id=2, damage=50),
            splash=[_effect(target_id=2, damage=40, x=10.0)])
        before = [state.players[index].health for index in (2, 3)]
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(before,
                         [state.players[index].health for index in (2, 3)])
        self.assertIn('1:p:1:1', state.projectiles)

        message['splash'] = [_effect(target_id=3, damage=40, x=20.0)]
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual([950, 960],
                         [state.players[index].health for index in (2, 3)])

    def test_invalid_nth_effect_is_atomic_even_when_targets_are_distinct(self):
        state = _state(players=3)
        state.players[3].x = 12.0
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=15.0, penetration_factor=0.0)))
        message = _resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=_effect(target_id=2, damage=50),
            splash=[_effect(target_id=3, damage=40, x=12.0),
                    _effect(target_id=999, damage=30)])

        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(1000, state.players[3].health)
        self.assertIn('1:p:1:1', state.projectiles)

    def test_expiration_result_and_reset_lifecycle(self):
        state = _state()
        self.assertTrue(_launch_authority(
            state, _launch(max_time_ms=100)))
        state.tick += 3
        self.assertEqual(1, state._expire_projectiles())
        self.assertNotIn('1:p:1:1', state.projectiles)
        self.assertEqual('expired',
                         state.projectile_tombstones['1:p:1:1']['outcome'])

        self.assertTrue(_launch_authority(state, _launch(
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
        self.assertTrue(_launch_authority(disconnected, _launch(
            shooter_id=2, shot_seq=1)))
        disconnected.remove_player(2)
        self.assertIn('1:p:2:1', disconnected.projectiles)
        self.assertTrue(disconnected.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:2:1', direct=None, outcome='miss',
                        impact=None, checked_distance=1.0)))

        left = _state()
        self.assertTrue(_launch_authority(left, _launch(
            shooter_id=2, shot_seq=1)))
        self.assertTrue(left.leave_battle(2, {
            'round_id': left.round_id}))
        self.assertIn('1:p:2:1', left.projectiles)
        self.assertTrue(left.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:2:1', direct=None, outcome='miss',
                        impact=None, checked_distance=1.0)))

    def test_disconnected_shooter_projectile_still_applies_damage(self):
        state = _state()
        _attach_worker_authority(state)
        self.assertTrue(_launch_authority(state, _launch()))

        state.remove_player(1)

        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, state.bot_authority_id)
        self.assertEqual(1, state.authority_epoch)
        self.assertIn('1:p:1:1', state.projectiles)
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', epoch=1)))
        self.assertEqual(900, state.players[2].health)
        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_impact', 'hit'],
                         [event['kind'] for event in events])

    def test_disconnected_shooter_projectile_keeps_stun_attribution(self):
        state = _state()
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))
        stun_end = state._server_time_ms() + 1500

        state.remove_player(1)

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve(
                '1:p:1:1', direct=_effect(
                    stun_end_server_time_ms=stun_end))))
        target = state.players[2]
        self.assertEqual(stun_end, target.stun_end_server_time_ms)
        self.assertEqual(
            ('player', 1),
            (target.stun_attacker_kind, target.stun_attacker_id))

    def test_disconnected_shooter_enemy_frag_uses_frozen_launch_identity(self):
        state = _state()
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))

        state.remove_player(1)

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', direct=_effect(damage=1000))))
        attacker = state.vehicle_statistics[('player', 1)]
        target = state.vehicle_statistics[('player', 2)]
        self.assertEqual(1, attacker['team'])
        self.assertEqual(1000, attacker['damage_dealt'])
        self.assertEqual(1, attacker['kills'])
        self.assertEqual(1000, target['damage_received'])
        self.assertEqual(1, state.round_participants['player-1']['frags'])
        self.assertFalse(
            state.round_participants['player-1']['team_killer'])
        statistics = [event for event in state.pending_events
                      if event.get('kind') == 'vehicle_statistics']
        self.assertEqual(1, statistics[-1]['frags'])

    def test_disconnected_shooter_friendly_frag_keeps_enemy_stats_zero(self):
        state = _state(players=3)
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))

        state.remove_player(1)

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve(
                '1:p:1:1', impact=[20.0, 1.0, 0.0],
                checked_distance=20.0,
                direct=_effect(target_id=3, damage=1000, x=20.0))))
        attacker = state.vehicle_statistics[('player', 1)]
        target = state.vehicle_statistics[('player', 3)]
        self.assertEqual(1, attacker['team'])
        self.assertEqual(0, attacker['damage_dealt'])
        self.assertEqual(0, attacker['kills'])
        self.assertEqual(1000, target['damage_received'])
        self.assertEqual(-1, state.round_participants['player-1']['frags'])
        self.assertTrue(state.round_participants['player-1']['team_killer'])
        statistics = [event for event in state.pending_events
                      if event.get('kind') == 'vehicle_statistics']
        self.assertEqual(-1, statistics[-1]['frags'])
        self.assertTrue(statistics[-1]['team_killer'])

    def test_modern_legacy_hits_reject_but_082_remains_compatible(self):
        modern = _state()
        modern.players[1].fire_seq = 1
        hit = {'round_id': 1, 'target': 2, 'shot_seq': 1, 'damage': 1}
        self.assertFalse(modern.report_hit(1, hit))
        self.assertFalse(modern.report_bot_hit(1, hit))

        legacy = _state()
        legacy.client_build = CLIENT_BUILD_082
        legacy.players[1].capabilities = ()
        legacy.players[1].fire_seq = 1
        self.assertTrue(legacy.report_hit(1, hit))
        self.assertEqual(999, legacy.players[2].health)
        legacy.update_input(1, {'round_id': 1, 'fire_seq': 2})
        self.assertEqual(2, legacy.players[1].fire_seq)
        self.assertEqual('shot', legacy.pending_events[-1]['kind'])

    def test_capability_and_active_snapshot_wire_bound(self):
        self.assertEqual('projectile_ledger_v2', PROJECTILE_CAPABILITY)
        modern = BattleState(map_name='04_himmelsdorf')
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P'})
        self.assertIsNone(player)
        self.assertEqual('unsupported_capabilities', error)
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P',
            'capabilities': [
                'projectile_ledger_v1',
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY]})
        self.assertIsNone(player)
        self.assertEqual('unsupported_capabilities', error)
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P',
            'capabilities': [PROJECTILE_CAPABILITY]})
        self.assertIsNone(player)
        self.assertEqual('unsupported_capabilities', error)
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P',
            'capabilities': [
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY,
                EFFECTIVE_PARAMS_CAPABILITY],
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': effective_params()})
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
                self.assertTrue(_launch_authority(
                    state, _launch(
                        shooter_id=shooter_id, shot_seq=shot_seq,
                        origin=[state.players[shooter_id].x, 1.0, 0.0])))
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
        self.assertTrue(_launch_authority(state, _launch()))

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

        self.assertTrue(_launch_authority(
            state, _launch(
                velocity=[0.0, 100.0, 425.0], gravity=143.0)))
        event = state.pending_events[-1]
        self.assertGreater(event['shot_pitch'], 0.0)
        self.assertAlmostEqual(
            math.atan2(100.0, 425.0), event['shot_pitch'], places=6)

    def test_modern_events_and_snapshot_share_current_tick_time_and_epoch(self):
        state = _state(players=1)
        self.assertTrue(_launch_authority(state, _launch()))
        broadcasts = []
        snapshots = []

        def offer_reliable(message):
            target = (snapshots if message.get('type') == 'snapshot'
                      else broadcasts)
            target.append(message)
            return True

        state.players[1].offer_reliable = offer_reliable
        state.players[1].offer_snapshot = (
            lambda message: snapshots.append(message) or True)
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

    def test_event_extraction_and_leave_are_one_ordered_state_transaction(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        entered_delivery = threading.Event()
        release_delivery = threading.Event()
        mutation_started = threading.Event()
        mutation_done = threading.Event()
        delivered = []

        def offer_reliable(message):
            if message.get('type') == 'events':
                delivered.append(message)
                if not entered_delivery.is_set():
                    entered_delivery.set()
                    self.assertTrue(release_delivery.wait(2.0))
            return True

        state.players[1].offer_reliable = offer_reliable
        state.players[1].offer_snapshot = lambda unused_message: True
        tick_thread = threading.Thread(
            target=state.tick_once, args=(1.0 / TICK_HZ,))
        tick_thread.start()
        self.assertTrue(entered_delivery.wait(2.0))

        def leave_player():
            mutation_started.set()
            state.leave_battle(2, {'round_id': state.round_id})
            mutation_done.set()

        leave_thread = threading.Thread(target=leave_player)
        leave_thread.start()
        self.assertTrue(mutation_started.wait(2.0))
        self.assertFalse(mutation_done.wait(0.05))
        release_delivery.set()
        tick_thread.join(2.0)
        leave_thread.join(2.0)
        self.assertFalse(tick_thread.is_alive())
        self.assertFalse(leave_thread.is_alive())
        self.assertTrue(mutation_done.is_set())

        state.tick_once(1.0 / TICK_HZ)

        events = [event for message in delivered
                  for event in message['events']]
        event_ids = [event['event_id'] for event in events]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.assertEqual(1, sum(event.get('kind') == 'shot'
                                for event in events))
        self.assertEqual(1, sum(event.get('source') == 'player_left'
                                for event in events))

    def test_legacy_events_envelope_remains_082_compatible(self):
        state = _state(players=1)
        state.client_build = CLIENT_BUILD_082
        state.players[1].capabilities = ()
        state.update_input(1, {
            'round_id': state.round_id, 'fire_seq': 1,
        })
        broadcasts = []
        state.players[1].offer_reliable = (
            lambda message: broadcasts.append(message) or True)
        state.players[1].offer_snapshot = lambda unused_message: True

        state.tick_once(1.0 / TICK_HZ)

        events = [message for message in broadcasts
                  if message.get('type') == 'events']
        self.assertEqual(1, len(events))
        self.assertNotIn('server_time_ms', events[0])
        self.assertNotIn('authority_epoch', events[0])


if __name__ == '__main__':
    unittest.main()
