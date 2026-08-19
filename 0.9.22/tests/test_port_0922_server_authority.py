import copy
import json
import sys
import unittest
from unittest import mock
from pathlib import Path

PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, Player, PREBATTLE_SECONDS,
    PROJECTILE_CAPABILITY, TICK_HZ,
)
import server_battle_authority  # noqa: E402
from server_battle_authority import (  # noqa: E402
    SERVER_AUTHORITY_ID, ServerBattleAuthority, _segment_hull_entry,
)
import server_world  # noqa: E402
from descriptor_projection import DescriptorStore, wrap  # noqa: E402


class _Socket(object):
    def __init__(self):
        self.payloads = []

    def sendall(self, unused_payload):
        self.payloads.append(unused_payload)


def _player(player_id, team=1, x=398.0, z=402.0):
    return Player(
        player_id, _Socket(), ('127.0.0.1', player_id),
        team=team, slot=max(0, player_id - 1), x=x, z=z,
        client_position=True, health=1000, max_health=1000,
        capabilities=(PROJECTILE_CAPABILITY,),
    )


def _projection():
    return {
        'name': 'ussr:R11_MS-1', 'level': 1, 'tags': ('lightTank',),
        'maxHealth': 1000,
        'gun': {
            'shots': [{
                'shell': {'kind': 'ARMOR_PIERCING', 'caliber': 45.0,
                          'damage': [110.0, 110.0], 'effectsIndex': 0},
                'speed': 700.0, 'gravity': 9.81, 'maxDistance': 720.0,
                'piercingPower': [80.0, 60.0],
            }],
            'reloadTime': 2.3, 'clip': (1, 0.0),
            'turretYawLimits': (-3.14159, 3.14159),
            'pitchLimits': {'absolute': (-0.35, 0.15)},
            'rotationSpeed': 0.7, 'shotDispersionAngle': 0.0046,
            'aimingTime': 2.0, 'maxAmmo': 50,
            'maxHealth': 54, 'maxRegenHealth': 27,
        },
        'turret': {'rotationSpeed': 0.7, 'circularVisionRadius': 445.0},
        'physics': {'weight': 8000.0, 'enginePower': 220000.0,
                    'speedLimits': (9.4, 4.0)},
        'chassis': {
            'hitTester': {'bbox': [(-1.5, -0.8, -3.5), (1.5, 0.8, 3.5),
                                   None]},
            'hullPosition': (0.0, 0.6, 0.0), 'rotationSpeed': 0.66,
            'shotDispersionFactors': (0.14, 0.14),
            'maxHealth': 170, 'maxRegenHealth': 130,
        },
        'hull': {
            'hitTester': {'bbox': [(-1.7, -0.2, -3.5), (1.7, 1.4, 3.5),
                                   None]},
            'turretPositions': ((0.0, 1.0, 0.0),),
            'primaryArmor': (18.0, 16.0, 16.0),
        },
    }


def _prime_destructible_map(state):
    world = server_world.load_world(state.map_name)
    instances = {}
    for index, signature in enumerate(sorted(world._instances)):
        instances[signature] = [
            list(signature), 7 + index // 1000, index % 1000, None, None]
    state.destructible_maps[state.map_name] = {
        'unit_vehicle_mass': 8000.0,
        'resources': {},
        'instances': instances,
        'parts_seen': {0},
        'parts': 1,
    }


def _state_with_authority(ready_world=True, clock=None):
    state = BattleState(map_name='01_karelia', clock=clock,
                        authority_mode='server')
    state.client_build = CLIENT_BUILD_0922
    state.descriptor_store.add('ussr:R11_MS-1', _projection())
    player = _player(1)
    state.players[1] = player
    state._elect_room_host()
    state.vehicle_catalogs[1] = ({
        'name': 'ussr:R11_MS-1', 'level': 1,
        'tags': ('lightTank',),
    },)
    if ready_world:
        _prime_destructible_map(state)
    return state


class ServerAuthorityElectionTest(unittest.TestCase):
    def test_client_mode_elects_the_lowest_connected_player(self):
        state = _state_with_authority()
        state.authority_mode = 'client'

        message, error = state.request_start(1, '01_karelia')

        self.assertIsNone(error)
        self.assertEqual('battle_start', message['type'])
        self.assertIsNone(state.server_authority)
        self.assertEqual(1, state.bot_authority_id)
        self.assertEqual(1, message['bot_authority_id'])
        self.assertEqual('loading', state.phase)

    def test_client_mode_round_goes_live_on_manifest_and_readiness(self):
        state = _state_with_authority()
        state.authority_mode = 'client'
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)

        manifest = [dict(entry, vehicle='ussr:R11_MS-1', health=350,
                         max_health=350, x=float(index), y=0.0,
                         z=0.0, yaw=0.0)
                    for index, entry in enumerate(state.bot_roster)]
        self.assertTrue(state.update_bot_manifest(1, {
            'round_id': state.round_id, 'bots': manifest}))
        live = state.mark_battle_ready(1, {'round_id': state.round_id})

        self.assertIsNotNone(live)
        self.assertEqual('battle_live', live['type'])
        self.assertEqual('battle', state.phase)
        self.assertEqual(1, state.bot_authority_id)

    def test_server_owns_bot_authority_after_start(self):
        state = _state_with_authority()
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertEqual('battle_start', message['type'])
        self.assertIsNotNone(state.server_authority)
        self.assertEqual(SERVER_AUTHORITY_ID, state.bot_authority_id)
        self.assertEqual(SERVER_AUTHORITY_ID,
                         state.bot_manifest_authority_id)
        self.assertEqual(SERVER_AUTHORITY_ID,
                         message['bot_authority_id'])
        self.assertTrue(state.bot_manifest)
        self.assertEqual(len(state.bot_roster), len(state.bot_manifest))
        for entry in state.bot_manifest:
            self.assertEqual('ussr:R11_MS-1', entry['vehicle'])

    def test_loading_snapshot_returns_the_canonical_lineup(self):
        state = _state_with_authority()
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertEqual('battle_start', message['type'])
        snapshot = state.loading_snapshot()
        self.assertIsInstance(snapshot, dict)
        self.assertEqual('snapshot', snapshot['type'])
        self.assertEqual(SERVER_AUTHORITY_ID,
                         snapshot['bot_authority_id'])
        self.assertEqual(state.bot_manifest, snapshot['bot_manifest'])
        self.assertEqual(
            sorted(state.bot_states),
            sorted(value['id'] for value in snapshot['bots']))

    def test_capture_bases_come_from_the_navigation_graph(self):
        state = _state_with_authority()
        unused_message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertIn(1, state.capture_bases)
        self.assertIn(2, state.capture_bases)

    def test_without_donation_refuses_the_start(self):
        state = BattleState(map_name='01_karelia',
                            authority_mode='server')
        state.client_build = CLIENT_BUILD_0922
        state.players[1] = _player(1)
        state._elect_room_host()
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(message)
        self.assertEqual('vehicle_catalog_unavailable', error)
        self.assertIsNone(state.server_authority)
        self.assertEqual('waiting', state.phase)
        self.assertEqual('failed', state.authority_status)
        self.assertEqual('vehicle_catalog_unavailable',
                         state.authority_fallback_reason)

    def test_reset_round_drops_the_authority(self):
        state = _state_with_authority()
        state.request_start(1, '01_karelia')
        state._reset_round()
        self.assertIsNone(state.server_authority)


class ServerAuthorityBattleTest(unittest.TestCase):
    def _live_state(self):
        state = _state_with_authority()
        unused_message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        state.mark_battle_ready(1, {'round_id': state.round_id})
        self.assertEqual('battle', state.phase)
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.pending_live_message = None
        return state

    def test_bots_publish_states_and_move_on_server_ticks(self):
        state = self._live_state()
        self.assertTrue(state.bot_states)
        initial = {
            bot_id: (value.get('x'), value.get('z'))
            for bot_id, value in state.bot_states.items()}
        for unused in range(90):
            state.tick_once(1.0 / TICK_HZ)
        self.assertIsNone(state.battle_result)
        self.assertGreater(state.bot_state_revision, 0)
        moved = sum(
            1 for bot_id, value in state.bot_states.items()
            if abs(float(value.get('x', 0.0)) - float(initial[bot_id][0])) +
            abs(float(value.get('z', 0.0)) - float(initial[bot_id][1])) >
            0.05)
        self.assertGreater(moved, 0)

    def test_bot_states_stay_inside_the_baked_grid(self):
        state = self._live_state()
        world = state.server_authority.world
        for unused in range(60):
            state.tick_once(1.0 / TICK_HZ)
        for value in state.bot_states.values():
            if not value.get('alive'):
                continue
            self.assertIsNotNone(world.ground_height(
                float(value.get('x', 0.0)), float(value.get('z', 0.0))))

    def test_validated_server_observation_is_broadcast_once_outside_lock(self):
        state = self._live_state()
        authority = state.server_authority
        state.players[2] = _player(2, team=1, x=410.0, z=410.0)
        for player in state.players.values():
            player.conn.payloads = []
        authority._bots.update = lambda unused_dt, unused_now, players=None: [{
            'type': 'bot_observation',
            'contacts': [{
                'observing_team': 2,
                'target_kind': 'human',
                'target_id': 1,
                'target_team': 1,
                'visible': True,
                'shootable_by_bot_ids': [],
                'x': state.players[1].x,
                'y': state.players[1].y,
                'z': state.players[1].z,
                'health': state.players[1].health,
                'max_health': state.players[1].max_health,
            }],
            'affordances': [],
        }]
        lock_ownership = []
        original_broadcast = state.broadcast_bot_observation

        def checked_broadcast(message):
            lock_ownership.append(state.lock._is_owned())
            return original_broadcast(message)

        state.broadcast_bot_observation = checked_broadcast
        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual([False], lock_ownership)
        expected_contacts = [{
            'observing_team': 2,
            'target_kind': 'human',
            'target_id': 1,
            'target_team': 1,
            'visible': True,
        }]
        for player in state.players.values():
            decoded = [json.loads(payload.decode('utf-8'))
                       for payload in player.conn.payloads]
            relays = [message for message in decoded
                      if message.get('type') == 'bot_observation']
            self.assertEqual(1, len(relays))
            self.assertEqual(expected_contacts, relays[0]['contacts'])


class ServerAuthorityProjectileTest(unittest.TestCase):
    def _live_state(self):
        state = _state_with_authority()
        human = _projection()
        human['name'] = 'test:human'
        human['gun']['shots'][0]['shell']['damage'] = [400.0, 400.0]
        state.descriptor_store.add('test:human', human)
        state.players[1].vehicle = 'test:human'
        state.vehicle_catalogs[1] = state.vehicle_catalogs[1] + ({
            'name': 'test:human', 'level': 1, 'tags': ('lightTank',),
        },)
        state.players[1].x = 0.0
        state.players[1].y = 0.0
        state.players[1].z = 0.0
        state.players[2] = _player(2, team=2, x=0.0, z=20.0)
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertEqual('battle_start', message['type'])
        self.assertIsNone(state.mark_battle_ready(
            1, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            2, {'round_id': state.round_id}))
        self.assertEqual('battle', state.phase)
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.pending_live_message = None
        authority = state.server_authority
        authority.world.segment_hit_fraction = (
            lambda unused_start, unused_end,
            include_destructibles=True: None)
        authority._bots.update = (
            lambda unused_dt, unused_now, players=None: [])
        return state, authority

    def _launch_human(self, state, velocity=(0.0, 0.0, 100.0),
                      max_time_ms=2000, shot_seq=1):
        message = {
            'type': 'projectile_launch',
            'round_id': state.round_id,
            'shooter_kind': 'player',
            'shooter_id': 1,
            'shot_seq': shot_seq,
            'shell_index': 0,
            'origin': [0.0, 1.0, 0.0],
            'velocity': list(velocity),
            'gravity': 9.81,
            'max_distance': 200.0,
            'max_time_ms': max_time_ms,
            'is_he': False,
            'splash_radius': 0.0,
            'penetration_factor': 1.0,
        }
        self.assertTrue(state.launch_projectile(1, message))
        return '%d:p:1:%d' % (state.round_id, shot_seq)

    def _tick_until_terminal(self, state, projectile_id, limit=30):
        for unused in range(limit):
            state.tick_once(1.0 / TICK_HZ)
            if projectile_id in state.projectile_tombstones:
                return
        self.fail('projectile did not reach a terminal')

    def test_human_ledger_projectile_uses_its_frozen_source_descriptor(self):
        state, unused_authority = self._live_state()
        projectile_id = self._launch_human(state)
        self._tick_until_terminal(state, projectile_id)
        self.assertLess(state.players[2].health, 850)
        self.assertNotIn(projectile_id, state.projectiles)
        self.assertEqual(
            'impact', state.projectile_tombstones[projectile_id]['outcome'])

    def test_disconnected_shooter_projectile_still_resolves(self):
        state, unused_authority = self._live_state()
        projectile_id = self._launch_human(state)
        removed, reset = state.remove_player(1)
        self.assertIsNotNone(removed)
        self.assertFalse(reset)
        self._tick_until_terminal(state, projectile_id)
        self.assertLess(state.players[2].health, 850)

    def test_progress_uses_the_canonical_epoch_and_cursor_base(self):
        state, authority = self._live_state()
        state.players[2].x = 100.0
        state.players[2].z = 100.0
        projectile_id = self._launch_human(
            state, velocity=(10.0, 0.0, 0.0))
        state.tick_once(1.0 / TICK_HZ)
        record = state.projectiles[projectile_id]
        snapshot = dict((row['projectile_id'], row)
                        for row in state._projectile_snapshot())
        self.assertEqual(state.authority_epoch,
                         snapshot[projectile_id]['authority_epoch'])
        self.assertGreater(record['checked_through_ms'], 0)
        self.assertEqual(record['checked_through_ms'],
                         authority._progress_cursors[projectile_id])
        managed = authority._projectiles.get(projectile_id)
        self.assertIsNotNone(managed)
        self.assertAlmostEqual(
            record['launch_server_time_ms'] / 1000.0,
            managed['launch_time'])

    def test_terminal_retry_is_exact_and_idempotent(self):
        state, unused_authority = self._live_state()
        original = state.resolve_projectile
        attempts = []

        def flaky_resolve(player_id, message):
            attempts.append(copy.deepcopy(message))
            if len(attempts) == 1:
                return False
            return original(player_id, message)

        state.resolve_projectile = flaky_resolve
        projectile_id = self._launch_human(state)
        self._tick_until_terminal(state, projectile_id)
        self.assertGreaterEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        health = state.players[2].health
        self.assertTrue(original(SERVER_AUTHORITY_ID, attempts[1]))
        self.assertEqual(health, state.players[2].health)

    def test_last_lifetime_subsegment_can_still_hit(self):
        state, authority = self._live_state()
        state.players[2].x = 100.0
        state.players[2].z = 100.0

        def final_segment_hit(start, end, include_destructibles=True):
            plane = 33.75
            if start[2] < plane <= end[2]:
                return (plane - start[2]) / (end[2] - start[2])
            return None

        authority.world.segment_hit_fraction = final_segment_hit
        projectile_id = self._launch_human(
            state, velocity=(0.0, 0.0, 1000.0), max_time_ms=34)

        state.tick_once(1.0 / TICK_HZ)
        self.assertIn(projectile_id, state.projectiles)
        state.tick_once(1.0 / TICK_HZ)

        self.assertNotIn(projectile_id, state.projectiles)
        self.assertEqual(
            'impact', state.projectile_tombstones[projectile_id]['outcome'])

    def test_chord_debt_is_not_preempted_by_canonical_expiry(self):
        state, authority = self._live_state()
        state.players[2].x = 100.0
        state.players[2].z = 100.0
        first = self._launch_human(state, max_time_ms=34, shot_seq=1)
        second = self._launch_human(state, max_time_ms=34, shot_seq=2)

        with mock.patch.object(
                server_battle_authority, 'PROJECTILE_CHORDS_PER_TICK', 1):
            state.tick_once(1.0 / TICK_HZ)
            state.tick_once(1.0 / TICK_HZ)
            self.assertEqual(1, len(state.projectiles))
            self.assertEqual(1, len(state.projectile_tombstones))
            self.assertEqual(
                'expired', next(iter(
                    state.projectile_tombstones.values()))['outcome'])
            state.tick_once(1.0 / TICK_HZ)

        self.assertNotIn(first, state.projectiles)
        self.assertNotIn(second, state.projectiles)
        self.assertEqual(
            {'expired'}, set(value['outcome']
                             for value in state.projectile_tombstones.values()))

    def test_bot_launch_is_admitted_then_restored_from_the_same_ledger(self):
        state, authority = self._live_state()
        bot_id = sorted(state.bot_states)[0]
        bot = dict(state.bot_states[bot_id])
        bot.update({'fire_seq': 1, 'shot_yaw': 0.0, 'shot_pitch': 0.0})
        state.bot_states[bot_id].update(bot)
        state.bot_pending_projectile_launches.add((bot_id, 1))
        now = float(state.tick) / TICK_HZ
        authority._projectiles.advance(
            now, authority._projectile_chord,
            authority._projectile_terminal, maximum_chords=0)
        self.assertTrue(authority._launch_bot_projectile(bot, 1, now))
        projectile_id = '%d:b:%d:1' % (state.round_id, bot_id)
        self.assertIn(projectile_id, state.projectiles)
        self.assertEqual(20000,
                         state.projectiles[projectile_id]['max_time_ms'])
        authority._reconcile_projectiles(state._projectile_snapshot())
        self.assertTrue(authority._projectiles.contains(projectile_id))


_TRACK_CRITICAL = {
    'devices': [{'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 100.0,
                 'state': 'destroyed'}],
    'destroyed': ['leftTrackHealth'], 'crew_ko': [],
    'fire': False, 'ammo_rack_death': False,
    'events': [{'kind': 'device', 'name': 'leftTrackHealth',
                'state': 'destroyed', 'cause': 'shot'}],
}


class VehicleStatisticsTest(unittest.TestCase):
    def _live_state(self):
        state = _state_with_authority()
        state.players[1].x = 0.0
        state.players[1].y = 0.0
        state.players[1].z = 0.0
        state.players[2] = _player(2, team=2, x=0.0, z=20.0)
        state.players[3] = _player(3, team=1, x=0.0, z=40.0)
        state.players[4] = _player(4, team=1, x=0.0, z=60.0)
        self._start_round(state)
        return state

    def _start_round(self, state):
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertEqual('battle_start', message['type'])
        pending = list(state.pending_descriptor_names)
        if pending:
            state.donate_descriptors(1, {
                'type': 'descriptor_bundle', 'round_id': state.round_id,
                'requested': list(state.descriptor_requested_names),
                'failures': [], 'complete': True,
                'projections': dict((name, _projection())
                                    for name in pending)})
        for player_id in sorted(state.players):
            state.mark_battle_ready(player_id, {'round_id': state.round_id})
        self.assertEqual('battle', state.phase)
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.pending_live_message = None
        state.server_authority._bots.update = (
            lambda unused_dt, unused_now, players=None: [])
        state.server_authority.world.segment_hit_fraction = (
            lambda unused_start, unused_end, include_destructibles=True: None)

    def _launch(self, state, shooter_id, shot_seq=1):
        launch = {
            'type': 'projectile_launch',
            'round_id': state.round_id,
            'shooter_kind': 'player',
            'shooter_id': shooter_id,
            'shot_seq': shot_seq,
            'shell_index': 0,
            'origin': [0.0, 1.0, 0.0],
            'velocity': [0.0, 0.0, 100.0],
            'gravity': 9.81,
            'max_distance': 200.0,
            'max_time_ms': 2000,
            'is_he': False,
            'splash_radius': 0.0,
            'penetration_factor': 1.0,
        }
        self.assertTrue(state.launch_projectile(shooter_id, launch))
        return '%d:p:%d:%d' % (state.round_id, shooter_id, shot_seq)

    def _shoot(self, state, shooter_id, target_id, damage, shot_seq=1,
               critical=None, shot_result=2, potential_damage=None):
        projectile_id = self._launch(state, shooter_id, shot_seq)
        record = state.projectiles[projectile_id]
        target = state.players[target_id]
        direct = {
            'target_kind': 'player', 'target_id': target_id,
            'damage': damage, 'shot_result': shot_result,
            'x': 0.0, 'y': 1.0, 'z': 20.0,
        }
        if potential_damage is not None:
            direct['potential_damage'] = potential_damage
        if critical is not None:
            direct.update({
                'critical': critical,
                'critical_target_base_revision':
                    target.critical_report_base_revision,
                'critical_target_ack_seq': target.critical_ack_seq,
                'hull_damage': damage,
            })
        self.assertTrue(state.resolve_projectile(SERVER_AUTHORITY_ID, {
            'type': 'projectile_resolve',
            'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'projectile_id': projectile_id,
            'base_checked_ms': record['checked_through_ms'],
            'outcome': 'impact',
            'resolved_time_ms': record['checked_through_ms'],
            'checked_distance': record['checked_distance'],
            'piercing_loss': record['piercing_loss'],
            'penetration_factor': record['penetration_factor'],
            'impact': [0.0, 1.0, 20.0],
            'direct': direct,
            'splash': [],
            'destructibles': [],
        }))

    def _assists(self, state):
        return [event for event in state.pending_events
                if event.get('kind') == 'assist']

    def test_track_assist_credits_the_immobiliser_not_the_shooter(self):
        state = self._live_state()
        self._shoot(state, 1, 2, 50, critical=_TRACK_CRITICAL)
        state.pending_events = []
        self._shoot(state, 3, 2, 200)

        assists = self._assists(state)
        self.assertEqual(1, len(assists))
        self.assertEqual({
            'kind': 'assist', 'category': 'track',
            'assister_kind': 'player', 'assister_id': 1,
            'attacker_kind': 'player', 'attacker_id': 3,
            'target_kind': 'player', 'target_id': 2,
            'damage': 200,
        }, assists[0])
        self.assertEqual(
            200, state.vehicle_statistics[('player', 1)][
                'damage_assisted_track'])
        self.assertEqual(
            0, state.vehicle_statistics[('player', 3)][
                'damage_assisted_track'])
        self.assertEqual(
            200, state.vehicle_statistics[('player', 3)]['damage_dealt'])
        self.assertEqual(
            250, state.vehicle_statistics[('player', 2)]['damage_received'])

    def test_the_immobiliser_earns_no_assist_from_its_own_damage(self):
        state = self._live_state()
        self._shoot(state, 1, 2, 50, critical=_TRACK_CRITICAL)
        state.pending_events = []
        self._shoot(state, 1, 2, 120, shot_seq=2)

        self.assertEqual([], self._assists(state))
        self.assertEqual(
            0, state.vehicle_statistics[('player', 1)][
                'damage_assisted_track'])
        self.assertEqual(
            170, state.vehicle_statistics[('player', 1)]['damage_dealt'])

    def test_repaired_tracks_stop_earning_assist(self):
        state = self._live_state()
        self._shoot(state, 1, 2, 50, critical=_TRACK_CRITICAL)
        state.players[2].critical = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 100.0,
                         'max_hp': 100.0, 'state': 'normal'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': []}
        state.pending_events = []
        self._shoot(state, 3, 2, 200)

        self.assertEqual([], self._assists(state))
        self.assertEqual(
            0, state.vehicle_statistics[('player', 1)][
                'damage_assisted_track'])

    def test_round_end_result_carries_the_accumulated_totals(self):
        state = self._live_state()
        self._shoot(state, 1, 2, 50, critical=_TRACK_CRITICAL)
        self._shoot(state, 3, 2, 200)
        self._shoot(state, 1, 2, 0, shot_seq=2, shot_result=0,
                    potential_damage=320)
        state._finish_battle(1, 'test_finished')

        rows = dict((row['actor_id'], row) for row
                    in state.battle_result['vehicle_statistics']
                    if row['actor_kind'] == 'player')
        self.assertEqual({
            'actor_kind': 'player', 'actor_id': 1, 'team': 1,
            'shots_fired': 2, 'shots_hit': 2, 'shots_penetrated': 1,
            'damage_dealt': 50, 'damage_received': 0, 'damage_blocked': 0,
            'damage_assisted_track': 200, 'damage_assisted_radio': 0,
            'kills': 0,
        }, rows[1])
        self.assertEqual(200, rows[3]['damage_dealt'])
        self.assertEqual(250, rows[2]['damage_received'])
        self.assertEqual(320, rows[2]['damage_blocked'])
        self.assertEqual(2, rows[2]['team'])
        published = [event for event in state.pending_events
                     if event.get('kind') == 'battle_result']
        self.assertEqual(
            state.battle_result['vehicle_statistics'],
            published[0]['vehicle_statistics'])

    def test_damage_blocked_counts_only_the_unpenetrated_remainder(self):
        state = self._live_state()
        self._shoot(state, 1, 2, 40, shot_result=1, potential_damage=300)
        self._shoot(state, 1, 2, 250, shot_seq=2, shot_result=2,
                    potential_damage=250)

        row = state.vehicle_statistics[('player', 2)]
        self.assertEqual(260, row['damage_blocked'])
        self.assertEqual(290, row['damage_received'])
        self.assertEqual(
            2, state.vehicle_statistics[('player', 1)]['shots_hit'])
        self.assertEqual(
            1, state.vehicle_statistics[('player', 1)]['shots_penetrated'])

    def test_a_second_round_starts_from_zero(self):
        state = self._live_state()
        self._shoot(state, 1, 2, 50, critical=_TRACK_CRITICAL)
        self._shoot(state, 3, 2, 200)
        state._finish_battle(1, 'test_finished')
        state.tick = state.result_reset_tick - 1
        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual('waiting', state.phase)
        self.assertEqual({}, state.vehicle_statistics)
        self.assertEqual({}, state.track_immobilisers)
        self.assertEqual({}, state.player_spotted)

        self._start_round(state)
        self._shoot(state, 3, 2, 120)

        self.assertEqual([], self._assists(state))
        self.assertNotIn(('player', 1), state.vehicle_statistics)
        self.assertEqual({
            'actor_kind': 'player', 'actor_id': 3, 'team': 1,
            'shots_fired': 1, 'shots_hit': 1, 'shots_penetrated': 1,
            'damage_dealt': 120, 'damage_received': 0, 'damage_blocked': 0,
            'damage_assisted_track': 0, 'damage_assisted_radio': 0,
            'kills': 0,
        }, state.vehicle_statistics[('player', 3)])

    def _report_spotted(self, state, player_id, targets):
        return state.update_spotted_targets(player_id, {
            'type': 'spotted_report',
            'round_id': state.round_id,
            'targets': [{'target_kind': kind, 'target_id': target_id}
                        for kind, target_id in targets],
        })

    def test_radio_assist_goes_to_the_reporter_not_the_shooter(self):
        state = self._live_state()
        self.assertTrue(self._report_spotted(state, 3, [('player', 2)]))
        self._shoot(state, 1, 2, 180)

        assists = self._assists(state)
        self.assertEqual(1, len(assists))
        self.assertEqual({
            'kind': 'assist', 'category': 'radio',
            'assister_kind': 'player', 'assister_id': 3,
            'attacker_kind': 'player', 'attacker_id': 1,
            'target_kind': 'player', 'target_id': 2,
            'damage': 180,
        }, assists[0])
        self.assertEqual(
            180, state.vehicle_statistics[('player', 3)][
                'damage_assisted_radio'])
        self.assertEqual(
            0, state.vehicle_statistics[('player', 1)][
                'damage_assisted_radio'])

    def test_a_reporter_earns_no_radio_assist_from_its_own_damage(self):
        state = self._live_state()
        self.assertTrue(self._report_spotted(state, 1, [('player', 2)]))
        self._shoot(state, 1, 2, 180)

        self.assertEqual([], self._assists(state))
        self.assertEqual(
            0, state.vehicle_statistics[('player', 1)][
                'damage_assisted_radio'])

    def test_an_empty_report_clears_the_previous_spotted_set(self):
        state = self._live_state()
        self.assertTrue(self._report_spotted(state, 3, [('player', 2)]))
        self.assertTrue(self._report_spotted(state, 3, []))
        self._shoot(state, 1, 2, 180)

        self.assertEqual([], self._assists(state))

    def test_a_dead_reporter_stops_earning_radio_assist(self):
        state = self._live_state()
        self.assertTrue(self._report_spotted(state, 3, [('player', 2)]))
        state.players[3].alive = False
        self._shoot(state, 1, 2, 180)

        self.assertEqual([], self._assists(state))

    def test_spotted_report_refuses_an_invalid_claim_entirely(self):
        state = self._live_state()
        enemy_bot_id = min(bot_id for bot_id, bot in state.bot_states.items()
                           if int(bot['team']) == 2)
        self.assertTrue(self._report_spotted(state, 3, [('player', 2)]))

        self.assertFalse(self._report_spotted(state, 3, [('player', 99)]))
        self.assertFalse(self._report_spotted(state, 3, [('player', 1)]))
        self.assertFalse(self._report_spotted(state, 3, [('crew', 2)]))
        self.assertFalse(state.update_spotted_targets(3, {
            'type': 'spotted_report', 'round_id': state.round_id,
            'targets': [{'target_kind': 'player', 'target_id': 2},
                        {'target_kind': 'player', 'target_id': 99}]}))
        self.assertFalse(state.update_spotted_targets(3, {
            'type': 'spotted_report', 'round_id': state.round_id + 5,
            'targets': []}))
        self.assertEqual(frozenset([('player', 2)]), state.player_spotted[3])
        self.assertTrue(self._report_spotted(
            state, 3, [('player', 2), ('bot', enemy_bot_id)]))

    def test_track_and_radio_assist_are_both_credited(self):
        state = self._live_state()
        self._shoot(state, 1, 2, 50, critical=_TRACK_CRITICAL)
        self.assertTrue(self._report_spotted(state, 3, [('player', 2)]))
        state.pending_events = []
        self._shoot(state, 4, 2, 200)

        self.assertEqual(
            [('track', 1), ('radio', 3)],
            [(event['category'], event['assister_id'])
             for event in self._assists(state)])
        self.assertEqual(
            200, state.vehicle_statistics[('player', 1)][
                'damage_assisted_track'])
        self.assertEqual(
            200, state.vehicle_statistics[('player', 3)][
                'damage_assisted_radio'])
        self.assertEqual(
            200, state.vehicle_statistics[('player', 4)]['damage_dealt'])

    def test_a_resolved_shot_publishes_its_rolled_potential_damage(self):
        state = self._live_state()
        captured = []
        original = state.resolve_projectile

        def capture(player_id, message):
            captured.append(copy.deepcopy(message))
            return original(player_id, message)

        state.resolve_projectile = capture
        projectile_id = self._launch(state, 1)
        for unused in range(30):
            state.tick_once(1.0 / TICK_HZ)
            if projectile_id in state.projectile_tombstones:
                break
        else:
            self.fail('projectile did not reach a terminal')

        direct = captured[-1]['direct']
        self.assertIsNotNone(direct)
        self.assertGreater(direct['potential_damage'], 0)
        self.assertGreaterEqual(direct['potential_damage'], direct['damage'])
        self.assertEqual(
            1, state.vehicle_statistics[('player', 1)]['shots_hit'])

    def test_a_kill_is_counted_once_for_the_attacker(self):
        state = self._live_state()
        self._shoot(state, 3, 2, 1000)

        self.assertFalse(state.players[2].alive)
        self.assertEqual(1, state.vehicle_statistics[('player', 3)]['kills'])
        self.assertEqual(
            1000, state.vehicle_statistics[('player', 3)]['damage_dealt'])


class SplashEffectsTest(unittest.TestCase):
    def _he_projection(self):
        projection = _projection()
        projection['gun']['shots'] = [{
            'shell': {'kind': 'HIGH_EXPLOSIVE', 'caliber': 122.0,
                      'damage': [450.0, 450.0], 'explosionRadius': 3.5},
            'speed': 500.0, 'gravity': 9.81, 'maxDistance': 720.0,
            'piercingPower': [60.0, 60.0],
        }]
        return projection

    def test_he_terminal_splashes_nearby_bots(self):
        state = _state_with_authority()
        state.request_start(1, '01_karelia')
        state.mark_battle_ready(1, {'round_id': state.round_id})
        authority = state.server_authority
        authority.descriptors.add('he:test', self._he_projection())
        import types
        authority._bots._descriptors[99] = authority.descriptors.get(
            'he:test')
        victim_id = sorted(state.bot_states)[0]
        victim = state.bot_states[victim_id]
        impact = (float(victim['x']) + 2.0, float(victim['y']),
                  float(victim['z']))
        with server_battle_authority.engine_modules(lambda: 0.0):
            effects = authority._splash_effects(
                {'shooter_kind': 'bot', 'shooter_id': 99,
                 'shell_index': 0, 'is_he': True}, impact, None)
        self.assertTrue(any(
            effect['target_id'] == victim_id and effect['damage'] > 0
            for effect in effects))
        for effect in effects:
            self.assertEqual(2, effect['shot_result'])

    def test_direct_target_is_excluded_from_splash(self):
        state = _state_with_authority()
        state.request_start(1, '01_karelia')
        state.mark_battle_ready(1, {'round_id': state.round_id})
        authority = state.server_authority
        authority.descriptors.add('he:test', self._he_projection())
        authority._bots._descriptors[99] = authority.descriptors.get(
            'he:test')
        victim_id = sorted(state.bot_states)[0]
        victim = state.bot_states[victim_id]
        impact = (float(victim['x']), float(victim['y']),
                  float(victim['z']))
        with server_battle_authority.engine_modules(lambda: 0.0):
            effects = authority._splash_effects(
                {'shooter_kind': 'bot', 'shooter_id': 99,
                 'shell_index': 0, 'is_he': True}, impact,
                {'target_kind': 'bot', 'target_id': victim_id})
        self.assertFalse(any(
            effect['target_id'] == victim_id for effect in effects))


class NarrowPhaseTest(unittest.TestCase):
    def _target(self, x=0.0, z=20.0, yaw=0.0):
        return {
            'kind': 'bot', 'id': 7, 'health': 900,
            'descriptor': wrap(_projection()),
            'position': (x, 0.0, z), 'yaw': yaw,
            'state': {},
        }

    def test_head_on_shot_hits_front_face_armor(self):
        target = self._target()
        entry = _segment_hull_entry((0.0, 1.0, 40.0), (0.0, 1.0, 10.0),
                                    target)
        self.assertIsNotNone(entry)
        collision = entry['collisions'][0]
        self.assertAlmostEqual(18.0, collision.matInfo.armor)
        self.assertGreater(collision.hitAngleCos, 0.9)

    def test_side_shot_hits_side_armor(self):
        target = self._target()
        entry = _segment_hull_entry((30.0, 1.0, 20.0), (-30.0, 1.0, 20.0),
                                    target)
        self.assertIsNotNone(entry)
        self.assertAlmostEqual(16.0, entry['collisions'][0].matInfo.armor)

    def test_miss_returns_none(self):
        target = self._target()
        self.assertIsNone(_segment_hull_entry(
            (30.0, 1.0, 60.0), (-30.0, 1.0, 60.0), target))

    def test_yawed_target_front_face_tracks_hull_yaw(self):
        import math
        target = self._target(yaw=math.pi / 2.0)
        entry = _segment_hull_entry((40.0, 1.0, 20.0), (0.0, 1.0, 20.0),
                                    target)
        self.assertIsNotNone(entry)
        self.assertAlmostEqual(18.0, entry['collisions'][0].matInfo.armor)


if __name__ == '__main__':
    unittest.main()
