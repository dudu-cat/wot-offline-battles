import copy
import json
import math
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

import lan_battle_server  # noqa: E402
from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, ClientHandler, CRITICAL_DEVICE_NAMES,
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY, Player, PREBATTLE_SECONDS,
    MAX_PLAYER_DESTRUCTIBLE_REJECTIONS,
    PLAYER_ENVIRONMENT_CAPABILITY, PLAYER_ENVIRONMENT_STALE_TICKS,
    PLAYER_FIRE_INTENT_CAPABILITY,
    PROJECTILE_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY,
    SIMULATION_WORKER_CAPABILITY, TICK_HZ,
    SIMULATION_WORKER_AUTHORITY_ID, SimulationWorker, _critical_payload,
    _run_tick_loop,
)
from effective_params_fixture import effective_params  # noqa: E402
import server_battle_authority  # noqa: E402
from server_battle_authority import (  # noqa: E402
    SERVER_AUTHORITY_ID, ServerBattleAuthority, _segment_hull_entry,
)
import server_world  # noqa: E402
from descriptor_projection import DescriptorStore, wrap  # noqa: E402
from gui.mods.offline_lan_0922 import equipment_mechanics  # noqa: E402


class _Socket(object):
    def __init__(self):
        self.payloads = []

    def sendall(self, unused_payload):
        self.payloads.append(unused_payload)


class TickLoopTimeTest(unittest.TestCase):

    def test_one_second_scheduler_stall_runs_all_thirty_due_steps(self):
        clock = [0.0]

        class State(object):
            running = True

            def __init__(self):
                self.steps = []

            def tick_once(self, dt):
                self.steps.append(dt)

        state = State()
        sleeps = []

        def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 1:
                clock[0] = 1.0
            else:
                state.running = False

        _run_tick_loop(state, lambda: clock[0], sleep)

        self.assertEqual(30, len(state.steps))
        self.assertAlmostEqual(1.0, sum(state.steps))
        self.assertTrue(all(
            abs(step - 1.0 / TICK_HZ) < 1e-12
            for step in state.steps))


def _player(player_id, team=1, x=398.0, z=402.0):
    return Player(
        player_id, _Socket(), ('127.0.0.1', player_id),
        team=team, slot=max(0, player_id - 1), x=x, z=z,
        client_position=True, health=1000, max_health=1000,
        capabilities=(
            PROJECTILE_CAPABILITY, HUMAN_RAM_TIMELINE_CAPABILITY,
            RAM_CONTACT_LEDGER_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY,
            EFFECTIVE_PARAMS_CAPABILITY),
        effective_params=effective_params(),
    )


def _gun_checkpoint(reload_time=0.0, clip=1):
    return {
        'reload_time': float(reload_time), 'reload_duration': 5.0,
        'clip': int(clip), 'clip_size': 1, 'dispersion': 0.02,
    }


def _projection():
    return {
        'name': 'ussr:R11_MS-1', 'level': 1, 'tags': ('lightTank',),
        'type': {
            'name': 'ussr:R11_MS-1', 'level': 1,
            'tags': ('lightTank',),
            'crewRoles': (
                ('commander', 'gunner', 'radioman', 'loader'),
                ('driver',)),
        },
        'maxHealth': 1000,
        'gun': {
            'hitTester': {'bbox': [(-0.25, -0.25, -1.2),
                                   (0.25, 0.25, 1.2), None]},
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
        'turret': {
            'hitTester': {'bbox': [(-0.9, -0.3, -0.9),
                                   (0.9, 0.8, 0.9), None]},
            'rotationSpeed': 0.7, 'circularVisionRadius': 445.0,
            'yawLimits': (-3.14159, 3.14159),
            'gunPosition': (0.0, 0.25, 0.15),
        },
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


def _mounted_source_shot(state, shooter_id, velocity, gravity, maximum):
    descriptor = state.descriptor_store.get(
        state.players[shooter_id].vehicle)
    shot = descriptor.gun.shots[0]
    shell = shot.shell
    return {
        'speed': sum(float(value) ** 2 for value in velocity) ** 0.5,
        'gravity': float(gravity),
        'maxDistance': float(maximum),
        'piercingPower': [float(value) for value in shot.piercingPower],
        'deadeye': False,
        'shell': {
            'kind': str(shell.kind),
            'caliber': float(shell.caliber),
            'damage': [float(value) for value in shell.damage],
            'explosionRadius': float(getattr(
                shell, 'explosionRadius', 0.0) or 0.0),
        },
    }


def _launch_player_as_authority(state, authority_id, message):
    """Prepare the trusted half of one already-admitted player trigger."""
    player = state.players[int(message['shooter_id'])]
    input_seq = player.input_seq + 1
    intent_seq = player.fire_intent_seq + 1
    player.input_seq = input_seq
    player.fire_intent_seq = intent_seq
    speed = math.sqrt(sum(
        float(component) ** 2 for component in message['velocity']))
    player.pending_fire_intents[intent_seq] = {
        'shot_seq': int(message['shot_seq']),
        'input_seq': input_seq,
        'shell_index': int(message['shell_index']),
        'x': float(player.x), 'y': float(player.y), 'z': float(player.z),
        'shot_origin': list(message['origin']),
        'shot_direction': [
            float(component) / speed for component in message['velocity']],
        'dispersion_angle': 0.0,
    }
    message.update({
        'authority_epoch': state.authority_epoch,
        'fire_intent_seq': intent_seq,
        'fire_input_seq': input_seq,
    })
    return state.launch_projectile(authority_id, message)


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


def _valid_ram_receipt(state, bot_id, seq=1, **values):
    """Build one native-contact receipt accepted by the #1513 server."""
    player = state.players[1]
    presentation_time_us = int(values.pop(
        'presentation_time_us', state.bot_state_time_us))
    result = {
        'seq': seq,
        'bot_id': bot_id,
        'bot_state_revision': int(values.pop(
            'bot_state_revision', state.bot_state_revision)),
        'presentation_time_us': presentation_time_us,
        'native_contact_time_us': int(values.pop(
            'native_contact_time_us', presentation_time_us)),
        'contact_x': float(values.get('x', player.x)),
        'contact_y': float(values.get('y', player.y)),
        'contact_z': float(values.get('z', player.z)),
        'contact_armor_player': 40.0,
        'contact_armor_bot': 40.0,
        'contact_spall_player': 1.0,
        'contact_bonus_player': 0.0,
        'contact_screened_player': False,
        'contact_screened_bot': False,
        'x': float(player.x), 'y': float(player.y), 'z': float(player.z),
        'yaw': float(player.yaw), 'pitch': 0.0, 'roll': 0.0,
        'vx': 0.0, 'vy': 0.0, 'vz': 0.0,
        'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
    }
    result.update(values)
    return result


class ServerCriticalProposalStateTest(unittest.TestCase):
    def test_destroyed_list_must_match_device_states_exactly(self):
        normal = {
            'devices': [{
                'name': 'engineHealth', 'hp': 100.0, 'max_hp': 100.0,
                'state': 'normal'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        destroyed = copy.deepcopy(normal)
        destroyed['devices'][0].update(hp=0.0, state='destroyed')

        with self.assertRaisesRegex(ValueError, 'destroyed state'):
            _critical_payload(dict(destroyed, destroyed=[]))
        with self.assertRaisesRegex(ValueError, 'destroyed state'):
            _critical_payload(dict(
                normal, destroyed=['engineHealth']))

    def test_protocol_device_hp_and_phase_survive_successive_proposals(self):
        descriptor = wrap(_projection())
        target = _player(2, team=2, x=0.0, z=20.0)

        first = {
            'devices': [{
                'name': 'engineHealth', 'hp': 70.0, 'max_hp': 100.0,
                'state': 'normal'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        BattleState._commit_external_player_critical(target, first)
        after_first = server_battle_authority._TargetMock(
            target.player_id, target.health, descriptor,
            (target.x, target.y, target.z), target.yaw,
            {'critical': target.critical})

        self.assertEqual(70.0, after_first.devices_hp['engineHealth'])
        self.assertNotIn('engineHealth', after_first._critical_devices)

        second = {
            'devices': [{
                'name': 'engineHealth', 'hp': 40.0, 'max_hp': 100.0,
                'state': 'critical'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        BattleState._commit_external_player_critical(target, second)
        after_second = server_battle_authority._TargetMock(
            target.player_id, target.health, descriptor,
            (target.x, target.y, target.z), target.yaw,
            {'critical': target.critical})

        self.assertEqual(40.0, after_second.devices_hp['engineHealth'])
        self.assertEqual(
            {'engineHealth'}, after_second._critical_devices)

    def test_rotating_turret_profile_parent_uses_current_aim(self):
        descriptor = wrap(_projection())
        hull_yaw = 0.25
        target = server_battle_authority._TargetMock(
            2, 1000, descriptor, (0.0, 0.0, 0.0), hull_yaw,
            {'critical': {}}, aim_yaw=hull_yaw + 0.5 * math.pi,
            gun_pitch=0.0)
        components = dict(
            (name, matrix) for component, matrix in target.getComponents()
            for name in ('chassis', 'hull', 'turret', 'gun')
            if component is getattr(descriptor, name))

        self.assertEqual(
            {'chassis', 'hull', 'turret', 'gun'}, set(components))
        point = server_battle_authority.Vector3(0.0, 1.6, 1.0)
        hull_point = components['hull'].applyPoint(point)
        turret_point = components['turret'].applyPoint(point)
        self.assertAlmostEqual(1.0, hull_point.y)
        self.assertAlmostEqual(1.0, hull_point.z)
        self.assertAlmostEqual(-1.0, turret_point.x)
        self.assertAlmostEqual(0.0, turret_point.y)
        self.assertAlmostEqual(0.0, turret_point.z)


class PlayerDrowningAuthorityTest(unittest.TestCase):
    def _worker_state(self):
        state = BattleState(
            map_name='01_karelia', authority_mode='worker')
        state.client_build = CLIENT_BUILD_0922
        state.descriptor_store.add('ussr:R11_MS-1', _projection())
        player = _player(1)
        player.input_seq = 7
        state.players[1] = player
        state.simulation_worker = SimulationWorker(
            _Socket(), ('127.0.0.1', 1000), capabilities=(
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                SIMULATION_WORKER_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY,
                EFFECTIVE_PARAMS_CAPABILITY))
        state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.authority_epoch = 3
        state.phase = 'battle'
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        return state, player

    @staticmethod
    def _environment(state, sample_seq, level, input_seq=7):
        return {
            'type': 'player_environment',
            'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'sample_seq': sample_seq,
            'observations': [{
                'player_id': 1, 'input_seq': input_seq, 'level': level,
            }],
        }

    def test_worker_observation_drives_server_owned_drowning_death(self):
        state, player = self._worker_state()

        for sample_seq in range(1, 102):
            state.tick += 3
            self.assertTrue(state.update_player_environment(
                SIMULATION_WORKER_AUTHORITY_ID,
                self._environment(state, sample_seq, 2)))
            state._tick_player_drowning(0.1)

        self.assertFalse(player.alive)
        self.assertEqual(0, player.health)
        self.assertEqual(1000, player.display_health)
        self.assertEqual(5, player.death_reason)
        self.assertEqual(1, player.critical_revision)
        self.assertEqual(
            set(CRITICAL_DEVICE_NAMES), set(player.critical['destroyed']))
        event = next(
            event for event in state.pending_events
            if event.get('source') == 'environment')
        self.assertEqual('health', event['kind'])
        self.assertEqual(5, event['death_reason'])
        self.assertNotIn('attacker', event)
        self.assertTrue(state._validate_combat_event_for_wire(event))

    def test_surfacing_resets_server_drowning_countdown(self):
        state, player = self._worker_state()
        for sample_seq in range(1, 51):
            state.tick += 3
            self.assertTrue(state.update_player_environment(
                SIMULATION_WORKER_AUTHORITY_ID,
                self._environment(state, sample_seq, 2)))
            state._tick_player_drowning(0.1)
        self.assertAlmostEqual(5.0, state.player_drowning_seconds[1])

        state.tick += 3
        self.assertTrue(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._environment(state, 51, 1)))
        state._tick_player_drowning(0.1)
        self.assertNotIn(1, state.player_drowning_seconds)

        for sample_seq in range(52, 112):
            state.tick += 3
            self.assertTrue(state.update_player_environment(
                SIMULATION_WORKER_AUTHORITY_ID,
                self._environment(state, sample_seq, 2)))
            state._tick_player_drowning(0.1)
        self.assertTrue(player.alive)
        self.assertEqual(1000, player.health)

    def test_stale_environment_pauses_without_discarding_drowning_time(self):
        state, player = self._worker_state()
        for sample_seq in range(1, 51):
            state.tick += 3
            self.assertTrue(state.update_player_environment(
                SIMULATION_WORKER_AUTHORITY_ID,
                self._environment(state, sample_seq, 2)))
            state._tick_player_drowning(0.1)
        self.assertAlmostEqual(5.0, state.player_drowning_seconds[1])

        state.tick += PLAYER_ENVIRONMENT_STALE_TICKS + 1
        state._tick_player_drowning(1.0)
        self.assertAlmostEqual(5.0, state.player_drowning_seconds[1])
        self.assertTrue(player.alive)

        state.tick += 1
        self.assertTrue(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._environment(state, 51, 1)))
        state._tick_player_drowning(0.1)
        self.assertNotIn(1, state.player_drowning_seconds)

    def test_visible_or_future_input_observation_is_rejected(self):
        state, player = self._worker_state()
        message = self._environment(state, 1, 2)
        self.assertFalse(state.update_player_environment(1, message))
        message['observations'][0]['input_seq'] = player.input_seq + 1
        self.assertFalse(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertTrue(player.alive)


class ServerAuthorityElectionTest(unittest.TestCase):
    @staticmethod
    def _install_worker(state):
        worker = SimulationWorker(
            _Socket(), ('127.0.0.1', 1000), capabilities=(
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                SIMULATION_WORKER_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY,
                EFFECTIVE_PARAMS_CAPABILITY))
        state.simulation_worker = worker
        state._elect_bot_authority()
        return worker

    def test_client_mode_requires_worker_and_never_elects_player(self):
        state = _state_with_authority()
        state.authority_mode = 'client'

        message, error = state.request_start(1, '01_karelia')

        self.assertIsNone(message)
        self.assertEqual('simulation_worker_required', error)
        self.assertIsNone(state.server_authority)
        self.assertIsNone(state.bot_authority_id)
        self.assertEqual('waiting', state.phase)

    def test_client_mode_round_goes_live_on_manifest_and_readiness(self):
        state = _state_with_authority()
        state.authority_mode = 'client'
        self._install_worker(state)
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)

        manifest = [dict(entry, vehicle='ussr:R11_MS-1', health=350,
                         max_health=350, x=float(index), y=0.0,
                         z=0.0, yaw=0.0, reload_time=0.0,
                         reload_duration=1.5)
                    for index, entry in enumerate(state.bot_roster)]
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID, {
            'round_id': state.round_id, 'bots': manifest,
            'player_collision_profiles': [{
                'id': participant.player_id,
                'vehicle': participant.vehicle,
                'mass': participant.effective_params[
                    'physics']['mass'],
                'shape': [1.5, 3.5, -0.8, 2.0],
                'ram_profile': {
                    'spall_coefficient': participant.effective_params[
                        'ramming']['spall_coefficient'],
                    'ramming_bonus': participant.effective_params[
                        'ramming']['ramming_bonus'],
                },
            } for participant in state.players.values()
             if participant.connected and participant.participating]}))
        self.assertIsNone(state.mark_battle_ready(
            1, {'round_id': state.round_id}))
        live = state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id})

        self.assertIsNotNone(live)
        self.assertEqual('battle_live', live['type'])
        self.assertEqual('battle', state.phase)
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.bot_authority_id)

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
        self.assertEqual(SERVER_AUTHORITY_ID,
                         state.human_collision_profile_authority_id)
        self.assertEqual({1}, set(state.human_collision_profiles))
        self.assertEqual(
            state.players[1].vehicle,
            state.human_collision_profiles[1]['vehicle'])
        for entry in state.bot_manifest:
            self.assertEqual('ussr:R11_MS-1', entry['vehicle'])

    def test_server_mode_player_projectiles_require_internal_authority(self):
        state = _state_with_authority()
        unused_message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        state.mark_battle_ready(1, {'round_id': state.round_id})
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.pending_live_message = None
        launch = {
            'type': 'projectile_launch',
            'round_id': state.round_id,
            'shooter_kind': 'player',
            'shooter_id': 1,
            'shot_seq': 1,
            'shell_index': 0,
            'origin': [state.players[1].x, state.players[1].y + 1.0,
                       state.players[1].z],
            'velocity': [0.0, 0.0, 100.0],
            'gravity': 9.81,
            'max_distance': 200.0,
            'max_time_ms': 2000,
            'is_he': False,
            'splash_radius': 0.0,
            'penetration_factor': 1.0,
            'source_shot': _mounted_source_shot(
                state, 1, (0.0, 0.0, 100.0), 9.81, 200.0),
        }

        self.assertFalse(_launch_player_as_authority(
            state, state.players[1].player_id, dict(launch)))
        state.players[1].pending_fire_intents.clear()
        state.players[1].fire_intent_seq = 0
        self.assertTrue(_launch_player_as_authority(
            state, SERVER_AUTHORITY_ID, dict(launch)))

    def test_server_mode_fire_intent_becomes_internal_projectile(self):
        state = _state_with_authority()
        unused_message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        state.mark_battle_ready(1, {'round_id': state.round_id})
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.pending_live_message = None
        player = state.players[1]
        self.assertTrue(state.update_input(1, {
            'type': 'input', 'round_id': state.round_id,
            'input_seq': 1,
            'pose_time_us': state._logical_motion_time_us(),
            'forward': 0.0, 'turn': 0.0, 'speed': 0.0,
            'x': player.x, 'y': player.y, 'z': player.z,
            'yaw': player.yaw, 'pitch': player.pitch,
            'roll': player.roll, 'shell_index': 0,
            'next_shell_index': 0, 'shell_change_pending': False,
            'gun_checkpoint': _gun_checkpoint(),
        }))

        self.assertTrue(state.submit_fire_intent(1, {
            'type': 'fire_intent', 'round_id': state.round_id,
            'intent_seq': 1, 'input_seq': 1, 'shell_index': 0,
            'shot_origin': [player.x, player.y + 1.0, player.z],
            'shot_direction': [0.0, 0.0, 1.0],
            'dispersion_angle': 0.0,
        }))

        projectile_id = '%d:p:1:1' % state.round_id
        self.assertIn(projectile_id, state.projectiles)
        launch = state.projectiles[projectile_id]
        self.assertEqual(1, launch['fire_intent_seq'])
        self.assertEqual(1, launch['fire_input_seq'])
        self.assertEqual(SERVER_AUTHORITY_ID, state.bot_authority_id)
        self.assertEqual(
            [player.x, player.y + 1.0, player.z], launch['origin'])
        self.assertAlmostEqual(0.0, launch['velocity'][0])
        self.assertAlmostEqual(0.0, launch['velocity'][1])
        self.assertGreater(launch['velocity'][2], 0.0)

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


class CanonicalPlayerEquipmentTest(unittest.TestCase):
    @staticmethod
    def _contract(name, equipment_id, **values):
        raw = {
            'name': name, 'id': (0, equipment_id),
            'compactDescr': 400 + equipment_id, 'tags': (),
            'reuseCount': 0, 'cooldownSeconds': 0.0,
        }
        raw.update(values)
        return equipment_mechanics.project_equipment(raw)

    @staticmethod
    def _critical(engine_hp=100.0, engine_state='normal', fire=False):
        return _critical_payload({
            'devices': [
                {'name': 'engineHealth', 'hp': float(engine_hp),
                 'max_hp': 100.0, 'state': engine_state},
                {'name': 'leftTrackHealth', 'hp': 0.0,
                 'max_hp': 100.0, 'state': 'destroyed'},
            ],
            'destroyed': ([
                'leftTrackHealth'] + ([
                    'engineHealth'] if engine_state == 'destroyed' else [])),
            'crew_ko': [], 'fire': bool(fire),
            'ammo_rack_death': False, 'events': [],
        })

    def _state(self, contracts, critical=None):
        state = BattleState(map_name='01_karelia', authority_mode='client')
        state.client_build = CLIENT_BUILD_0922
        player = _player(1)
        params = effective_params()
        params['equipment'] = list(contracts)
        params['critical'] = {'devices': [
            {'name': 'engineHealth', 'max_hp': 100.0, 'regen_hp': 50.0},
            {'name': 'leftTrackHealth', 'max_hp': 100.0,
             'regen_hp': 50.0},
        ], 'activation_targets': [
            {'index': 1, 'name': 'commander'},
            {'index': 4, 'name': 'engineHealth'},
            {'index': 5, 'name': 'leftTrackHealth'},
        ], 'crew_roster': ['commander', 'driver']}
        player.effective_params = params
        player.critical = critical or self._critical()
        state.players = {1: player}
        state.phase = 'battle'
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        self.assertTrue(state._install_player_equipments(player))
        return state, player

    @staticmethod
    def _intent(state, sequence, equipment_id, selected=None,
                requested_active=None):
        equipment = next(value for value in
                         state.players[1].equipment_states
                         if value.contract['id'] == equipment_id)
        if equipment.contract.get('repairAll', False):
            extra_index = 1
        elif equipment.contract.get('kind') == 'rpm_limiter':
            extra_index = int(bool(requested_active))
        elif selected is not None:
            extra_index = next(
                row['index'] for row in
                state.players[1].effective_params['critical'][
                    'activation_targets']
                if row['name'] == selected)
        else:
            extra_index = 0
        return state.submit_equipment_intent(1, {
            'type': 'equipment_intent', 'round_id': state.round_id,
            'intent_seq': sequence, 'equipment_id': equipment_id,
            'activation_code': (extra_index << 16) | equipment_id,
            'selected': selected, 'requested_active': requested_active,
        })

    def test_inventory_cooldown_and_fingerprint_are_idempotent(self):
        repair = self._contract(
            'smallRepairkit', 41, tags=('repairkit',), reuseCount=1,
            cooldownSeconds=5.0, repairAll=False)
        state, player = self._state(
            [repair], self._critical(0.0, 'destroyed'))

        self.assertTrue(self._intent(
            state, 1, 41, selected='engineHealth'))
        equipment = player.equipment_states[0]
        revision = player.equipment_revision
        self.assertEqual(1, equipment.uses_left)
        self.assertTrue(self._intent(
            state, 1, 41, selected='engineHealth'))
        self.assertEqual((1, revision), (
            equipment.uses_left, player.equipment_revision))
        self.assertFalse(self._intent(
            state, 1, 41, selected='leftTrackHealth'))
        self.assertFalse(self._intent(
            state, 3, 41, selected='engineHealth'))

        player.critical = self._critical(0.0, 'destroyed')
        state.tick += int(4.0 * TICK_HZ)
        self.assertTrue(self._intent(
            state, 2, 41, selected='engineHealth'))
        self.assertEqual(
            'equipment_ineligible',
            player.equipment_intent_result['reason'])
        self.assertEqual(1, equipment.uses_left)
        state.tick += int(1.0 * TICK_HZ)
        self.assertTrue(self._intent(
            state, 3, 41, selected='engineHealth'))
        self.assertEqual(0, equipment.uses_left)

    def test_malformed_equipment_identifiers_fail_closed(self):
        state, player = self._state([])
        base = {
            'type': 'equipment_intent', 'round_id': state.round_id,
            'intent_seq': 1, 'equipment_id': 41,
            'activation_code': 41,
            'selected': None, 'requested_active': None,
        }
        for field, value in (
                ('intent_seq', []), ('intent_seq', '1'),
                ('equipment_id', {}), ('equipment_id', 1.5)):
            message = dict(base)
            message[field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(
                    state.submit_equipment_intent(1, message))
                self.assertEqual(0, player.equipment_intent_seq)

    def test_rpm_and_non_track_repair_are_server_owned(self):
        limiter = self._contract(
            'removedRpmLimiter', 12, reuseCount=-1,
            enginePowerFactor=1.1, engineHpLossPerSecond=1.5)
        state, player = self._state([limiter])
        self.assertTrue(self._intent(
            state, 1, 12, requested_active=True))
        self.assertEqual(1, state._tick_player_critical(2.0))
        engine = next(value for value in player.critical['devices']
                      if value['name'] == 'engineHealth')
        track = next(value for value in player.critical['devices']
                     if value['name'] == 'leftTrackHealth')
        self.assertEqual(97.0, engine['hp'])
        self.assertEqual(0.0, track['hp'])

        repair_state, repair_player = self._state(
            [], self._critical(0.0, 'destroyed'))
        self.assertEqual(1, repair_state._tick_player_critical(1.0))
        engine = next(value for value in repair_player.critical['devices']
                      if value['name'] == 'engineHealth')
        track = next(value for value in repair_player.critical['devices']
                     if value['name'] == 'leftTrackHealth')
        self.assertGreater(engine['hp'], 0.0)
        self.assertEqual(0.0, track['hp'])

    def test_medkit_clears_stun_without_a_knocked_out_crew_member(self):
        medkit = self._contract(
            'smallMedkit', 42, tags=('medkit',), repairAll=False)
        state, player = self._state([medkit])
        player.stun_end_server_time_ms = state._server_time_ms() + 5000
        player.stun_attacker_kind = 'bot'
        player.stun_attacker_id = 7

        self.assertTrue(self._intent(
            state, 1, 42, selected='commander'))
        self.assertTrue(player.equipment_intent_result['accepted'])
        self.assertEqual(0, player.stun_end_server_time_ms)
        self.assertEqual('', player.stun_attacker_kind)
        self.assertEqual(0, player.stun_attacker_id)
        self.assertEqual(0, player.equipment_states[0].uses_left)
        self.assertEqual('stun', state.pending_events[-1]['kind'])
        self.assertFalse(state.pending_events[-1]['active'])

    def test_continuous_progress_does_not_starve_track_owner_cas(self):
        limiter = self._contract(
            'removedRpmLimiter', 12, reuseCount=-1,
            enginePowerFactor=1.1, engineHpLossPerSecond=1.5)
        critical = self._critical()
        state, player = self._state([limiter], critical)
        player.critical = {}
        initial = state._commit_external_player_critical(player, critical)
        base_revision = initial['critical_base_revision']
        self.assertGreater(base_revision, 0)
        self.assertTrue(self._intent(
            state, 1, 12, requested_active=True))

        for unused_index in range(3):
            state.tick += TICK_HZ
            self.assertEqual(1, state._tick_player_critical(1.0))
            self.assertEqual(
                base_revision, player.critical_report_base_revision)
            self.assertEqual(0, player.critical_ack_seq)

        revision = player.critical_revision
        unchanged = state._commit_player_critical_progress(
            player, player.critical)
        self.assertEqual(revision, unchanged['critical_revision'])

        self.assertTrue(state.report_track_repair(1, {
            'type': 'track_repair', 'round_id': state.round_id,
            'critical_base_revision': base_revision, 'repair_seq': 1,
            'tracks': [{
                'name': 'leftTrackHealth', 'hp': 25.0,
                'max_hp': 100.0, 'state': 'destroyed',
            }],
        }))
        self.assertEqual(1, player.critical_ack_seq)
        engine_before = next(
            value['hp'] for value in player.critical['devices']
            if value['name'] == 'engineHealth')

        state.tick += TICK_HZ
        self.assertEqual(1, state._tick_player_critical(1.0))
        self.assertEqual((base_revision, 1), (
            player.critical_report_base_revision,
            player.critical_ack_seq))
        track = next(value for value in player.critical['devices']
                     if value['name'] == 'leftTrackHealth')
        engine = next(value for value in player.critical['devices']
                      if value['name'] == 'engineHealth')
        self.assertEqual(25.0, track['hp'])
        self.assertLess(engine['hp'], engine_before)

        hull_damage, accepted = (
            lan_battle_server._critical_proposal_admission({
                'critical_target_base_revision': base_revision,
                'critical_target_ack_seq': 1,
                'hull_damage': 120,
            }, player.critical_report_base_revision,
                player.critical_ack_seq))
        self.assertEqual(120, hull_damage)
        self.assertTrue(accepted)

    def test_damage_delta_preserves_unrelated_repair_and_fire_progress(self):
        critical = _critical_payload({
            'devices': [
                {'name': 'engineHealth', 'hp': 0.0, 'max_hp': 100.0,
                 'state': 'destroyed'},
                {'name': 'leftTrackHealth', 'hp': 100.0,
                 'max_hp': 100.0, 'state': 'normal'},
            ],
            'destroyed': ['engineHealth'], 'crew_ko': [],
            'crew_roster': ['commander', 'driver'],
            'fire': False, 'ammo_rack_death': False, 'events': [],
        })
        state, player = self._state([], critical)
        state.tick += TICK_HZ
        self.assertEqual(1, state._tick_player_critical(1.0))
        repaired_engine = next(
            row['hp'] for row in player.critical['devices']
            if row['name'] == 'engineHealth')
        self.assertGreater(repaired_engine, 0.0)

        stale_full = _critical_payload({
            'devices': [
                {'name': 'engineHealth', 'hp': 0.0, 'max_hp': 100.0,
                 'state': 'destroyed'},
                {'name': 'leftTrackHealth', 'hp': 50.0,
                 'max_hp': 100.0, 'state': 'critical'},
            ],
            'destroyed': ['engineHealth'], 'crew_ko': [],
            'crew_roster': ['commander', 'driver'],
            'fire': False, 'ammo_rack_death': False, 'events': [],
        })
        merged = state._merge_player_critical_damage(
            player, stale_full, {
                'devices': [{'name': 'leftTrackHealth', 'hp_loss': 50.0}],
                'crew_ko': [], 'ignite': False,
            })
        self.assertEqual(repaired_engine, next(
            row['hp'] for row in merged['devices']
            if row['name'] == 'engineHealth'))
        self.assertEqual(50.0, next(
            row['hp'] for row in merged['devices']
            if row['name'] == 'leftTrackHealth'))

        player.critical = copy.deepcopy(merged)
        player.critical['fire'] = False
        stale_burning = copy.deepcopy(merged)
        stale_burning['fire'] = True
        stale_burning['events'] = []
        not_reignited = state._merge_player_critical_damage(
            player, stale_burning, {
                'devices': [{'name': 'leftTrackHealth', 'hp_loss': 1.0}],
                'crew_ko': [], 'ignite': False,
            })
        self.assertFalse(not_reignited['fire'])

        player.critical = copy.deepcopy(not_reignited)
        player.critical['fire'] = True
        stale_clear = copy.deepcopy(not_reignited)
        stale_clear['fire'] = False
        still_burning = state._merge_player_critical_damage(
            player, stale_clear, {
                'devices': [{'name': 'leftTrackHealth', 'hp_loss': 1.0}],
                'crew_ko': [], 'ignite': False,
            })
        self.assertTrue(still_burning['fire'])

    def test_snapshot_restores_inventory_and_cooldown_exactly(self):
        repair = self._contract(
            'smallRepairkit', 41, tags=('repairkit',), reuseCount=1,
            cooldownSeconds=10.0, repairAll=False)
        state, player = self._state(
            [repair], self._critical(0.0, 'destroyed'))
        self.assertTrue(self._intent(
            state, 1, 41, selected='engineHealth'))
        state.tick += int(3.0 * TICK_HZ)
        state._tick_player_critical(0.0)
        snapshot = BattleState._public_player(player)

        self.assertEqual(1, snapshot['equipment_intent_seq'])
        self.assertAlmostEqual(
            7.0, snapshot['equipment_states'][0]['cooldownTimeLeft'])
        restored = equipment_mechanics.restore_equipment_states(
            snapshot['equipment_states'], now=200.0)
        self.assertEqual(1, restored[0].uses_left)
        self.assertAlmostEqual(207.0, restored[0].ready_at)

    def test_disconnected_human_igniter_keeps_damage_and_frag_credit(self):
        state = BattleState(map_name='01_karelia', authority_mode='client')
        state.client_build = CLIENT_BUILD_0922
        attacker = _player(1, team=1)
        victim = _player(2, team=2)
        attacker.account_key = 'fire-attacker'
        victim.account_key = 'fire-victim'
        victim.health = 40
        victim.critical = self._critical(fire=True)
        victim.fire_attacker_kind = 'player'
        victim.fire_attacker_id = 1
        state.players = {1: attacker, 2: victim}
        state.phase = 'battle'
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.bot_manifest = [{
            'id': 10, 'team': 1, 'name': 'Bot',
            'vehicle': attacker.vehicle, 'max_health': 1000}]
        state.bot_states = {10: {
            'id': 10, 'team': 1, 'alive': True,
            'health': 1000, 'max_health': 1000}}
        state._freeze_round_participants((attacker, victim))
        state.remove_player(1, expected=attacker)

        self.assertEqual(1, state._tick_player_fire(1.0))

        self.assertFalse(victim.alive)
        event = state.pending_events[-2]
        self.assertEqual(('hit', 'fire', 1), (
            event['kind'], event['source'], event['attacker']))
        self.assertEqual(
            40, state._statistics_row('player', 1)['damage_dealt'])
        self.assertEqual(1, state._statistics_row('player', 1)['kills'])


class HumanRamTimelineTest(unittest.TestCase):
    def _state(self, health=1000):
        clock = [0.0]
        state = BattleState(
            map_name='01_karelia', authority_mode='client',
            team1_size=1, team2_size=1, clock=lambda: clock[0])
        state.client_build = CLIENT_BUILD_0922
        capabilities = (
            PROJECTILE_CAPABILITY, HUMAN_RAM_TIMELINE_CAPABILITY,
            RAM_CONTACT_LEDGER_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY)
        first = _player(1, team=1, x=0.0, z=-1.6)
        second = _player(2, team=2, x=0.0, z=6.5)
        first.capabilities = capabilities
        second.capabilities = capabilities
        first.health = first.max_health = health
        second.health = second.max_health = health
        state.players = {1: first, 2: second}
        worker = SimulationWorker(
            _Socket(), ('127.0.0.1', 1000), capabilities=(
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                SIMULATION_WORKER_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY))
        state.simulation_worker = worker
        state._elect_room_host()
        state._elect_bot_authority()
        state.bot_roster = []
        state.roster_finalized = True
        state.phase = 'loading'
        profiles = [{
            'id': player.player_id, 'vehicle': player.vehicle,
            'mass': 8000.0, 'shape': [1.5, 3.5, -0.8, 2.0],
            'ram_profile': {
                'spall_coefficient': 1.0,
                'ramming_bonus': 0.0,
            },
        } for player in (first, second)]
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': state.round_id, 'bots': [],
                'player_collision_profiles': profiles,
            }))
        state.phase = 'battle'
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        return state, clock

    @staticmethod
    def _input(state, clock, player_id, sequence, sample_time_us,
               z, speed, yaw, arrival_delay_us=100000):
        clock[0] = max(
            clock[0],
            float(sample_time_us + arrival_delay_us) / 1000000.0)
        return state.update_input(player_id, {
            'round_id': state.round_id, 'input_seq': sequence,
            'pose_time_us': sample_time_us,
            'x': 0.0, 'y': 0.0, 'z': z, 'yaw': yaw,
            'speed': speed,
        })

    def test_profiles_must_match_the_exact_player_roster(self):
        state, unused_clock = self._state()
        state.phase = 'loading'
        state.human_collision_profiles = {}
        state.human_collision_profile_authority_id = None
        state.human_collision_manifest_fingerprint = None

        self.assertFalse(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': state.round_id, 'bots': [],
                'player_collision_profiles': [{
                    'id': 1, 'vehicle': state.players[1].vehicle,
                    'mass': 8000.0,
                    'shape': [1.5, 3.5, -0.8, 2.0],
                    'ram_profile': {
                        'spall_coefficient': 1.0,
                        'ramming_bonus': 0.0,
                    },
                }],
            }))
        self.assertEqual(
            'human_collision_profiles',
            state.last_bot_manifest_reject_code)
        self.assertIsNone(state.human_collision_profile_authority_id)

    def test_profile_manifest_exact_retry_folds_and_conflict_fails(self):
        state, unused_clock = self._state()
        manifest = {
            'round_id': state.round_id, 'bots': [],
            'player_collision_profiles': [{
                'id': player.player_id, 'vehicle': player.vehicle,
                'mass': 8000.0, 'shape': [1.5, 3.5, -0.8, 2.0],
                'ram_profile': {
                    'spall_coefficient': 1.0,
                    'ramming_bonus': 0.0,
                },
            } for player in (state.players[1], state.players[2])],
        }
        previous = copy.deepcopy(state.human_collision_profiles)

        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID, copy.deepcopy(manifest)))
        self.assertEqual(previous, state.human_collision_profiles)
        conflict = copy.deepcopy(manifest)
        conflict['player_collision_profiles'][0]['mass'] = 9000.0
        self.assertFalse(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID, conflict))
        self.assertEqual(
            'human_collision_manifest_conflict',
            state.last_bot_manifest_reject_code)
        self.assertEqual(previous, state.human_collision_profiles)

    def test_common_frontier_without_contact_armor_fails_closed(self):
        state, clock = self._state()
        self._input(state, clock, 1, 1, 100000, -1.6, 16.0, 0.0)
        self._input(state, clock, 2, 1, 100000, 6.5, 0.0, math.pi)
        self._input(state, clock, 1, 2, 200000, 0.0, 16.0, 0.0)

        self.assertEqual(0, state._resolve_human_rams())
        self._input(state, clock, 2, 2, 200000, 6.5, 0.0, math.pi)
        self.assertEqual(0, state._resolve_human_rams())

        self.assertEqual((1000, 1000), (
            state.players[1].health, state.players[2].health))
        hits = [event for event in state.pending_events
                if event.get('kind') == 'hit']
        self.assertEqual([], hits)

    def test_native_armor_response_replays_the_exact_pending_substep(self):
        state, clock = self._state(health=100000)
        for player_id, z, speed, yaw in (
                (1, -1.6, 16.0, 0.0),
                (2, 6.5, 0.0, math.pi)):
            self._input(state, clock, player_id, 1, 100000, z, speed, yaw)
        self.assertEqual(0, state._resolve_human_rams())
        for player_id, z, speed, yaw in (
                (1, 0.0, 16.0, 0.0),
                (2, 6.5, 0.0, math.pi)):
            self._input(state, clock, player_id, 2, 200000, z, speed, yaw)

        self.assertEqual(0, state._resolve_human_rams())
        request = state._human_ram_probe_snapshot()
        self.assertEqual(1, len(request))
        self.assertEqual(100000, state.human_ram_pair_frontiers[(1, 2)])
        self.assertEqual((1, 2), (
            request[0]['first']['id'], request[0]['second']['id']))
        self.assertEqual((0.0, 6.5), (
            request[0]['first']['z'], request[0]['second']['z']))

        response = {
            'seq': request[0]['seq'], 'first_id': 1, 'second_id': 2,
            'available': True, 'armor_first': 45.0,
            'armor_second': 80.0,
        }
        message = {
            'round_id': state.round_id, 'bots': [],
            'human_ram_armors': [response],
        }
        # A publication which omits the result is an asynchronous retry, not
        # an unavailable verdict and not permission to advance the pair.
        self.assertTrue(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': state.round_id, 'bots': [],
            }))
        self.assertEqual(0, state._resolve_human_rams())
        self.assertEqual(request, state._human_ram_probe_snapshot())
        self.assertFalse(state.update_bot_states(
            1, copy.deepcopy(message)))
        self.assertEqual('authority', state.last_bot_state_reject_code)
        self.assertEqual(request, state._human_ram_probe_snapshot())
        self.assertTrue(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, copy.deepcopy(message)))

        # Entity startup may delay the worker past the bounded pose-history
        # window. The accepted response must still replay the frozen request,
        # not depend on samples that happened to survive until this tick.
        state.players[1].pose_history.clear()
        state.players[2].pose_history.clear()

        self.assertEqual(1, state._resolve_human_rams())
        self.assertEqual(200000, state.human_ram_pair_frontiers[(1, 2)])
        self.assertEqual([], state._human_ram_probe_snapshot())
        self.assertLess(state.players[1].health, 100000)
        self.assertLess(state.players[2].health, 100000)

        # An exact transport retry folds after the request is consumed. A
        # changed payload with the same sequence is a protocol conflict.
        self.assertTrue(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, copy.deepcopy(message)))
        before = (state.players[1].health, state.players[2].health)
        conflict = copy.deepcopy(message)
        conflict['human_ram_armors'][0]['armor_first'] = 46.0
        self.assertFalse(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, conflict))
        self.assertEqual('human_ram_armors',
                         state.last_bot_state_reject_code)
        self.assertEqual(before, (
            state.players[1].health, state.players[2].health))

    def test_explicit_unavailable_native_armor_advances_without_damage(self):
        state, clock = self._state()
        for player_id, z, speed, yaw in (
                (1, -1.6, 16.0, 0.0),
                (2, 6.5, 0.0, math.pi)):
            self._input(state, clock, player_id, 1, 100000, z, speed, yaw)
        for player_id, z, speed, yaw in (
                (1, 0.0, 16.0, 0.0),
                (2, 6.5, 0.0, math.pi)):
            self._input(state, clock, player_id, 2, 200000, z, speed, yaw)
        self.assertEqual(0, state._resolve_human_rams())
        request = state._human_ram_probe_snapshot()[0]

        self.assertTrue(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': state.round_id, 'bots': [],
                'human_ram_armors': [{
                    'seq': request['seq'], 'first_id': 1, 'second_id': 2,
                    'available': False,
                }],
            }))
        self.assertEqual(0, state._resolve_human_rams())

        self.assertEqual(200000, state.human_ram_pair_frontiers[(1, 2)])
        self.assertEqual((1000, 1000), (
            state.players[1].health, state.players[2].health))
        self.assertEqual([], state._human_ram_probe_snapshot())

    def test_input_retry_is_idempotent_and_identity_conflict_is_rejected(self):
        state, clock = self._state()
        message = {
            'round_id': state.round_id, 'input_seq': 1,
            'pose_time_us': 100000,
            'x': 0.0, 'y': 0.0, 'z': -1.6, 'yaw': 0.0,
            'speed': 16.0,
        }
        clock[0] = 0.2

        self.assertTrue(state.update_input(1, dict(message)))
        self.assertTrue(state.update_input(1, dict(message)))
        self.assertEqual(1, len(state.players[1].pose_history))
        self.assertFalse(state.update_input(
            1, dict(message, z=20.0)))
        self.assertEqual(-1.6, state.players[1].z)
        self.assertFalse(state.update_input(
            1, dict(message, input_seq=0)))
        self.assertFalse(state.update_input(
            1, dict(message, input_seq=3, pose_time_us=200000)))
        self.assertEqual(1, state.players[1].input_seq)

    def test_player_destructible_contact_is_bound_to_an_admitted_pose(self):
        state, clock = self._state()
        clock[0] = 0.2
        relays = []
        visible_results = []
        state.simulation_worker.offer_reliable = lambda message: (
            relays.append(copy.deepcopy(message)) or True)
        state.players[1].offer_reliable = lambda message: (
            visible_results.append(copy.deepcopy(message)) or True)
        contact = {
            'seq': 1, 'x': 0.0, 'y': 0.0, 'z': -1.6,
            'yaw': 0.0, 'speed': 16.0, 'dt': 0.04,
            'token': [[22, 3, None]],
        }

        self.assertTrue(state.update_input(1, {
            'round_id': state.round_id, 'input_seq': 1,
            'pose_time_us': 100000,
            'x': 0.0, 'y': 0.0, 'z': -1.6, 'yaw': 0.0,
            'forward': 1.0, 'speed': 16.0,
            'destructible_contacts': [contact],
        }))

        player = state.players[1]
        self.assertEqual([1], list(player.destructible_contacts))
        admitted = player.destructible_contacts[1]
        self.assertEqual(1, admitted['input_seq'])
        self.assertEqual(100000, admitted['pose_time_us'])
        self.assertEqual(
            [1], [row['seq'] for row in
                  state._public_player(player)['destructible_contacts']])
        self.assertEqual({}, state.destructibles)
        self.assertEqual(1, len(relays))
        self.assertEqual('player_destructible_contact', relays[0]['type'])
        self.assertEqual(state.round_id, relays[0]['round_id'])
        self.assertEqual(state.authority_epoch,
                         relays[0]['authority_epoch'])
        self.assertEqual({
            'id', 'vehicle', 'vehicle_compact_descr',
            'destructible_contacts'}, set(relays[0]['player']))
        self.assertEqual(admitted,
                         relays[0]['player']['destructible_contacts'][0])

        self.assertTrue(state.report_player_destructible_contact_result(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'player_destructible_contact_result',
                'round_id': state.round_id, 'player_id': 1,
                'contact_seq': 1, 'accepted': False,
                'token': [[22, 3, None]],
            }))
        self.assertEqual([1], list(
            player.destructible_contact_rejections))
        self.assertEqual([1], state._public_player(player)[
            'destructible_contact_rejected_seqs'])
        self.assertEqual({
            'type': 'player_destructible_contact_result',
            'round_id': state.round_id, 'contact_seq': 1,
            'accepted': False,
            'x': admitted['x'], 'y': admitted['y'],
            'z': admitted['z'], 'yaw': admitted['yaw'],
        }, visible_results[-1])

        # A contact body that does not match an admitted pose is terminally
        # rejected instead of being relayed to the hidden worker.
        self.assertTrue(state.update_input(1, {
            'round_id': state.round_id, 'input_seq': 2,
            'pose_time_us': 200000,
            'x': 0.0, 'y': 0.0, 'z': -1.6, 'yaw': 0.0,
            'forward': 1.0, 'speed': 16.0,
            'destructible_contacts': [dict(contact, seq=2, x=5.0)],
        }))
        self.assertEqual([], list(player.destructible_contacts))
        self.assertEqual(2, player.destructible_contact_seq)
        self.assertEqual(2, player.destructible_contact_resolved_seq)
        self.assertEqual([1, 2], state._public_player(player)[
            'destructible_contact_rejected_seqs'])
        self.assertEqual({
            'type': 'player_destructible_contact_result',
            'round_id': state.round_id, 'contact_seq': 2,
            'accepted': False,
        }, visible_results[-1])

    def test_player_destructible_rejection_history_is_bounded(self):
        state, unused_clock = self._state()
        player = state.players[1]
        player.offer_reliable = lambda unused_message: True

        for seq in range(1, MAX_PLAYER_DESTRUCTIBLE_REJECTIONS + 3):
            state._reject_player_destructible_contact(player, seq)

        self.assertEqual(MAX_PLAYER_DESTRUCTIBLE_REJECTIONS, len(
            player.destructible_contact_rejections))
        self.assertEqual(
            list(range(3, MAX_PLAYER_DESTRUCTIBLE_REJECTIONS + 3)),
            list(player.destructible_contact_rejections))

    def test_player_destructible_relay_failure_keeps_snapshot_retry(self):
        state, clock = self._state()
        clock[0] = 0.2
        state.simulation_worker.offer_reliable = lambda unused_message: False

        self.assertTrue(state.update_input(1, {
            'round_id': state.round_id, 'input_seq': 1,
            'pose_time_us': 100000,
            'x': 0.0, 'y': 0.0, 'z': -1.6, 'yaw': 0.0,
            'forward': 1.0, 'speed': 16.0,
            'destructible_contacts': [{
                'seq': 1, 'x': 0.0, 'y': 0.0, 'z': -1.6,
                'yaw': 0.0, 'speed': 16.0, 'dt': 0.04,
                'token': [[22, 3, None]],
            }],
        }))

        player = state.players[1]
        self.assertEqual([1], list(player.destructible_contacts))
        self.assertEqual(
            [1], [row['seq'] for row in
                  state._public_player(player)['destructible_contacts']])

    def test_only_hidden_worker_can_resolve_player_destructible_contact(self):
        state, clock = self._state()
        clock[0] = 0.2
        token = [[22, 3, None]]
        self.assertTrue(state.update_input(1, {
            'round_id': state.round_id, 'input_seq': 1,
            'pose_time_us': 100000,
            'x': 0.0, 'y': 0.0, 'z': -1.6, 'yaw': 0.0,
            'forward': 1.0, 'speed': 16.0,
            'destructible_contacts': [{
                'seq': 1, 'x': 0.0, 'y': 0.0, 'z': -1.6,
                'yaw': 0.0, 'speed': 16.0, 'dt': 0.04,
                'token': token,
            }],
        }))
        message = {
            'type': 'player_destructible_contact_result',
            'round_id': state.round_id, 'player_id': 1,
            'contact_seq': 1, 'accepted': True, 'token': token,
        }

        self.assertFalse(state.report_player_destructible_contact_result(
            1, message))
        self.assertFalse(state.report_player_destructible_contact_result(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(message, token=[[22, 4, None]])))
        self.assertFalse(state.report_player_destructible_contact_result(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual([1], list(
            state.players[1].destructible_contacts))

        state.destructibles[('fragile', 22, 3, None)] = {
            'kind': 'destructible', 'destructible_kind': 'fragile',
            'chunk_id': 22, 'item_index': 3,
        }
        self.assertTrue(state.report_player_destructible_contact_result(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual([], list(
            state.players[1].destructible_contacts))
        self.assertEqual(
            1, state.players[1].destructible_contact_resolved_seq)
        self.assertTrue(state.report_player_destructible_contact_result(
            SIMULATION_WORKER_AUTHORITY_ID, message))

    def test_visible_input_cannot_submit_health_or_critical_verdicts(self):
        state, unused_clock = self._state()
        player = state.players[1]
        before = (
            player.health, player.alive, copy.deepcopy(player.critical),
            len(state.pending_events), player.input_seq)

        self.assertFalse(state.update_input(1, {
            'round_id': state.round_id,
            'input_seq': 1, 'reported_health': 0,
            'reported_reason': 2, 'reported_critical': {'events': []},
        }))

        self.assertEqual(before, (
            player.health, player.alive, player.critical,
            len(state.pending_events), player.input_seq))
        self.assertFalse(state.report_bot_ram(1, {
            'round_id': state.round_id,
            'bot_id': 1, 'target_kind': 'human', 'target_id': 2,
            'ram_seq': 1, 'damage_to_bot': 100,
            'damage_to_target': 100,
        }))

    def test_missing_timeline_capability_refuses_start_before_mutation(self):
        state = BattleState(
            map_name='01_karelia', authority_mode='client', team_size=1)
        state.client_build = CLIENT_BUILD_0922
        player = _player(1)
        player.capabilities = (PROJECTILE_CAPABILITY,)
        state.players = {1: player}
        state.simulation_worker = SimulationWorker(
            _Socket(), ('127.0.0.1', 1000), capabilities=(
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                SIMULATION_WORKER_CAPABILITY))
        state._elect_room_host()
        state._elect_bot_authority()

        message, error = state.request_start(1)

        self.assertIsNone(message)
        self.assertEqual('missing_human_ram_timeline_capability', error)
        self.assertEqual('waiting', state.phase)
        self.assertFalse(state.roster_finalized)
        self.assertEqual({}, state.round_participants)

    def test_manifest_identity_conflict_fences_the_worker(self):
        state, unused_clock = self._state()
        worker = state.simulation_worker
        profiles = [dict(state.human_collision_profiles[player_id])
                    for player_id in sorted(state.human_collision_profiles)]
        for profile in profiles:
            profile['shape'] = list(profile['shape'])
        profiles[0]['mass'] = 9000.0
        handler = object.__new__(ClientHandler)

        result = handler._dispatch_simulation_worker_message(
            types.SimpleNamespace(state=state), worker, {
                'type': 'bot_manifest', 'round_id': state.round_id,
                'bots': [], 'player_collision_profiles': profiles,
            })

        self.assertEqual('close', result)
        self.assertFalse(worker.connected)
        self.assertIsNone(state.simulation_worker)
        self.assertIsNone(state.bot_authority_id)
        self.assertEqual(
            'human_collision_manifest_conflict',
            state.worker_failure_reason)
        self.assertEqual(
            'human_collision_manifest', state.battle_result['reason'])

    def test_sustained_overlap_without_contact_armor_never_damages(self):
        state, clock = self._state()
        sequence = {1: 0, 2: 0}

        def pair(sample_time_us, first_z, first_speed):
            for player_id, z, speed, yaw in (
                    (1, first_z, first_speed, 0.0),
                    (2, 6.5, 0.0, math.pi)):
                sequence[player_id] += 1
                self._input(
                    state, clock, player_id, sequence[player_id],
                    sample_time_us, z, speed, yaw)
            return state._resolve_human_rams()

        self.assertEqual(0, pair(100000, -1.6, 16.0))
        self.assertEqual(0, pair(200000, 0.0, 16.0))
        first_health = state.players[1].health
        for sample_time in (400000, 600000, 800000, 1000000):
            self.assertEqual(0, pair(sample_time, 0.0, 16.0))
        self.assertEqual(first_health, state.players[1].health)

        self.assertEqual(0, pair(1200000, -5.0, 0.0))
        self.assertNotIn((1, 2), state.human_ram_contacts)
        self.assertEqual(0, pair(1400000, 0.0, 16.0))
        self.assertEqual(first_health, state.players[1].health)

    def test_history_gap_cannot_mint_damage_without_contact_armor(self):
        state, clock = self._state()
        sequence = {1: 0, 2: 0}

        def pair(sample_time_us, first_z, first_speed):
            for player_id, z, speed, yaw in (
                    (1, first_z, first_speed, 0.0),
                    (2, 6.5, 0.0, math.pi)):
                sequence[player_id] += 1
                self._input(
                    state, clock, player_id, sequence[player_id],
                    sample_time_us, z, speed, yaw)
            return state._resolve_human_rams()

        self.assertEqual(0, pair(100000, -1.6, 16.0))
        self.assertEqual(0, pair(200000, 0.0, 16.0))
        first_health = state.players[1].health
        self.assertNotIn((1, 2), state.human_ram_contacts)

        # A 400 ms source-time hole is fully replayed, but missing native
        # contact armour still fails closed and cannot mint damage.
        self.assertEqual(0, pair(600000, 0.0, 16.0))
        self.assertEqual(0, pair(800000, 0.0, 16.0))
        self.assertEqual(first_health, state.players[1].health)
        self.assertNotIn((1, 2), state.human_ram_contacts)
        self.assertEqual(0, pair(1000000, -5.0, 0.0))
        self.assertNotIn((1, 2), state.human_ram_contacts)

    def test_one_second_gap_resolves_intermediate_contact_before_frontier(self):
        state, clock = self._state(health=100000)
        for player_id, z, speed, yaw in (
                (1, -10.0, 16.0, 0.0),
                (2, 0.0, 0.0, math.pi)):
            self._input(state, clock, player_id, 1, 100000, z, speed, yaw)
        self.assertEqual(0, state._resolve_human_rams())

        real_resolve = lan_battle_server.tank_collision.resolve_tank
        resolved_times = []

        def resolve_with_proved_armor(tank, others, **kwargs):
            resolved_times.append(float(kwargs['now']))
            tank = dict(tank, contact_armor=0.0)
            others = tuple(
                dict(other, contact_armor=0.0) for other in others)
            return real_resolve(tank, others, **kwargs)

        with mock.patch.object(
                lan_battle_server.tank_collision, 'resolve_tank',
                side_effect=resolve_with_proved_armor):
            for player_id, z, speed, yaw in (
                    (1, 10.0, 16.0, 0.0),
                    (2, 0.0, 0.0, math.pi)):
                self._input(
                    state, clock, player_id, 2, 1100000, z, speed, yaw)
            self.assertEqual(1, state._resolve_human_rams())

        self.assertEqual(1100000, state.human_ram_pair_frontiers[(1, 2)])
        self.assertEqual(11, len(resolved_times))
        self.assertAlmostEqual(1.0, resolved_times[-1] - resolved_times[0])
        self.assertTrue(all(
            later - earlier <= 0.1000001
            for earlier, later in zip(resolved_times, resolved_times[1:])))
        self.assertLess(state.players[1].health, 100000)
        self.assertLess(state.players[2].health, 100000)
        self.assertNotIn((1, 2), state.human_ram_contacts)
        self.assertEqual(2, len(state.players[1].pose_history))
        self.assertEqual(2, len(state.players[2].pose_history))

    def test_low_health_humans_survive_without_contact_armor(self):
        state, clock = self._state(health=100)
        for player_id, z, speed, yaw in (
                (1, -1.6, 16.0, 0.0),
                (2, 6.5, 0.0, math.pi)):
            self._input(state, clock, player_id, 1, 100000,
                        z, speed, yaw)
        for player_id, z, speed, yaw in (
                (1, 0.0, 16.0, 0.0),
                (2, 6.5, 0.0, math.pi)):
            self._input(state, clock, player_id, 2, 200000,
                        z, speed, yaw)

        self.assertEqual(0, state._resolve_human_rams())

        self.assertEqual((100, 100), (
            state.players[1].health, state.players[2].health))
        self.assertEqual((True, True), (
            state.players[1].alive, state.players[2].alive))
        self.assertIsNone(state.battle_result)
        self.assertEqual(0, state.players[1].death_attacker_id)
        self.assertEqual(0, state.players[2].death_attacker_id)


class ServerAuthorityBattleTest(unittest.TestCase):
    def _live_state(self):
        state = _state_with_authority()
        unused_message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        state.mark_battle_ready(1, {'round_id': state.round_id})
        self.assertEqual('battle', state.phase)
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.pending_live_message = None
        # This class exercises the server-authority ram receipt seam rather
        # than the separately covered source-timed human timeline protocol.
        for player in state.players.values():
            player.capabilities = tuple(
                capability for capability in player.capabilities
                if capability != HUMAN_RAM_TIMELINE_CAPABILITY)
        return state

    def test_human_hull_pitch_and_roll_reach_server_narrow_phase(self):
        state = self._live_state()

        state.update_input(1, {
            'round_id': state.round_id,
            'x': 1.0, 'y': 2.0, 'z': 3.0, 'yaw': 0.4,
            'pitch': 0.25, 'roll': -0.3,
        })
        authority = state.server_authority
        public = state._public_player(state.players[1])
        published = authority._players_payload()[0]
        target = next(authority._chord_targets({
            'shooter_kind': 'bot', 'shooter_id': 99,
        }))

        self.assertEqual(0.25, public['pitch'])
        self.assertEqual(-0.3, public['roll'])
        self.assertEqual(0.25, published['pitch'])
        self.assertEqual(-0.3, published['roll'])
        self.assertEqual(0.25, target['pitch'])
        self.assertEqual(-0.3, target['roll'])

    def test_ram_history_brackets_a_coalesced_player_revision(self):
        state = self._live_state()
        authority = state.server_authority
        player = state.players[1]
        player.capabilities = (
            PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY)
        bot_id = min(state.bot_states)

        def remember(server_tick, revision, sample_time_us, z):
            bot = dict(state.bot_states[bot_id])
            bot.update(id=bot_id, x=0.0, y=0.0, z=z, yaw=math.pi,
                       health=1000, alive=True, fire_seq=0)
            authority.apply_snapshot({
                'server_tick': server_tick,
                'bot_state_revision': revision,
                'bot_state_time_us': sample_time_us,
                'server_time_ms': sample_time_us // 1000,
                'bots': [bot], 'projectiles': [],
            })

        remember(10, 10, 100000, 7.0)
        remember(12, 12, 120000, 6.0)
        state.bot_state_revision = 12
        state.bot_state_time_us = 120000
        state.update_input(1, {
            'round_id': state.round_id,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 16.0,
            'ram_contacts': [_valid_ram_receipt(
                state, bot_id, bot_state_revision=11,
                presentation_time_us=110000,
                x=0.0, y=0.0, z=0.0, yaw=0.0,
                vx=0.0, vy=0.0, vz=16.0,
                contact_z=3.25)],
        })

        published = authority._players_payload()[0]
        receipt = published['ram_contacts'][0]
        historical = receipt['_ram_contact_bot_state']

        self.assertEqual(1, published['ram_contact_admitted_seq'])
        self.assertAlmostEqual(6.5, historical['z'])
        self.assertAlmostEqual(-50.0, historical['ram_vz'])
        self.assertIn('mass', historical)
        self.assertIn('collision_shape', historical)

    def test_server_authority_resolves_human_ram_receipt_end_to_end(self):
        state = self._live_state()
        authority = state.server_authority
        player = state.players[1]
        player.capabilities = (
            PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY)
        bot_id = min(state.bot_states)
        runtime_bot = authority._bots.states[bot_id]
        runtime_bot.update(
            x=0.0, y=0.0, z=6.5, yaw=math.pi, speed=0.0,
            push_x=0.0, push_z=0.0, health=1000, alive=True)
        state.bot_states[bot_id].update(
            x=0.0, y=0.0, z=6.5, yaw=math.pi,
            health=1000, alive=True)

        for server_tick, revision, sample_time_us in (
                (20, 20, 200000), (22, 22, 220000)):
            bot = dict(state.bot_states[bot_id])
            authority.apply_snapshot({
                'server_tick': server_tick,
                'bot_state_revision': revision,
                'bot_state_time_us': sample_time_us,
                'server_time_ms': sample_time_us // 1000,
                'bots': [bot], 'projectiles': [],
            })
        state.bot_state_revision = 22
        state.bot_state_time_us = 220000
        pitch, roll = 0.08, -0.04
        shape = state.human_collision_profiles[player.player_id]['shape']
        axes = server_battle_authority._pose_axes(0.0, pitch, roll)
        local_midpoint = (
            0.0, (shape[2] + shape[3]) * 0.5, 3.2)
        world_midpoint = tuple(
            sum(local_midpoint[row] * axes[row][index]
                for row in range(3))
            for index in range(3))
        state.update_input(1, {
            'round_id': state.round_id,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'pitch': pitch, 'roll': roll,
            'speed': 16.0,
            'ram_contacts': [_valid_ram_receipt(
                state, bot_id, bot_state_revision=21,
                presentation_time_us=210000,
                x=0.0, y=0.0, z=0.0, yaw=0.0,
                pitch=pitch, roll=roll,
                vx=0.0, vy=0.0, vz=16.0,
                contact_x=world_midpoint[0],
                contact_y=world_midpoint[1],
                contact_z=world_midpoint[2])],
        })

        admitted = player.ram_contacts[1]
        self.assertEqual((pitch, roll), (
            admitted['pitch'], admitted['roll']))
        self.assertEqual(
            tuple(round(value, 4) for value in world_midpoint),
            tuple(admitted[key] for key in (
                'contact_x', 'contact_y', 'contact_z')))

        reports = authority._bots._resolve_human_ram_receipts(
            authority._players_payload(), 1.0,
            step=1.0 / TICK_HZ, processed_pairs=set())
        before = (player.health, state.bot_states[bot_id]['health'])
        for report in reports:
            authority._route(report, 1.0)

        self.assertEqual(1, len(reports))
        self.assertEqual((1, 1), (
            reports[0]['ram_contact_player_id'],
            reports[0]['ram_contact_seq']))
        self.assertGreater(reports[0]['damage_to_target'], 0)
        self.assertGreater(reports[0]['damage_to_bot'], 0)
        self.assertLess(player.health, before[0])
        self.assertLess(state.bot_states[bot_id]['health'], before[1])
        self.assertEqual([], list(player.ram_contacts))

    def test_human_ram_keeps_pre_separation_body_from_contact_receipt(self):
        state = self._live_state()
        authority = state.server_authority
        player = state.players[1]
        player.capabilities = (
            PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY)
        bot_id = min(state.bot_states)
        runtime_bot = authority._bots.states[bot_id]
        runtime_bot.update(
            x=0.0, y=0.0, z=6.5, yaw=math.pi, speed=0.0,
            push_x=0.0, push_z=0.0, health=1000, alive=True)
        state.bot_states[bot_id].update(
            x=0.0, y=0.0, z=6.5, yaw=math.pi,
            health=1000, alive=True)

        for server_tick, revision, sample_time_us in (
                (20, 20, 200000), (22, 22, 220000)):
            bot = dict(state.bot_states[bot_id])
            authority.apply_snapshot({
                'server_tick': server_tick,
                'bot_state_revision': revision,
                'bot_state_time_us': sample_time_us,
                'server_time_ms': sample_time_us // 1000,
                'bots': [bot], 'projectiles': [],
            })
        state.bot_state_revision = 22
        state.bot_state_time_us = 220000
        state.update_input(1, {
            'round_id': state.round_id,
            # The local collision response has already moved this ordinary
            # input pose outside the two 3.5 m half-lengths.
            'x': 0.0, 'y': 0.0, 'z': -0.75, 'yaw': 0.0,
            'speed': 8.0,
            'ram_contacts': [_valid_ram_receipt(
                state, bot_id, bot_state_revision=21,
                presentation_time_us=210000,
                # The receipt freezes the pre-separation overlap and speed.
                x=0.0, y=0.0, z=0.0, yaw=0.0,
                vx=0.0, vy=0.0, vz=16.0,
                contact_z=3.25)],
        })

        admitted = state.players[1].ram_contacts[1]
        self.assertEqual((0.0, 16.0), (
            admitted['z'], admitted['vz']))
        reports = authority._bots._resolve_human_ram_receipts(
            authority._players_payload(), 1.0,
            step=1.0 / TICK_HZ, processed_pairs=set())

        self.assertEqual(1, len(reports))
        self.assertGreater(reports[0]['damage_to_target'], 0)
        self.assertGreater(reports[0]['damage_to_bot'], 0)

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

    def test_player_ram_contact_is_validated_and_relayed_by_revision(self):
        state = self._live_state()
        bot_id = min(state.bot_states)
        state.bot_state_revision = 300
        state.bot_state_time_us = 3000000
        valid = _valid_ram_receipt(
            state, bot_id, bot_state_revision=299,
            presentation_time_us=2900000,
            x=1.25, y=0.0, z=-2.5, yaw=0.25,
            vx=0.0, vy=0.0, vz=16.5)

        state.update_input(1, {
            'round_id': state.round_id,
            'x': 1.25, 'y': 0.0, 'z': -2.5, 'yaw': 0.25,
            'speed': 16.5, 'ram_contacts': [valid]})

        accepted = state._public_player(
            state.players[1])['ram_contacts'][0]
        self.assertEqual(
            {key: valid[key] for key in (
                'seq', 'bot_id', 'bot_state_revision',
                'presentation_time_us')},
            {key: accepted[key] for key in (
                'seq', 'bot_id', 'bot_state_revision',
                'presentation_time_us')})
        self.assertEqual((1.25, 0.0, -2.5, 0.25), tuple(
            accepted[key] for key in ('x', 'y', 'z', 'yaw')))
        self.assertEqual((0.0, 0.0, 16.5), (
            accepted['vx'], accepted['vy'], accepted['vz']))
        self.assertEqual(0, accepted['input_seq'])
        for rejected in (
                dict(valid, seq=2, bot_state_revision=301),
                dict(valid, seq=3, bot_state_revision=44),
                dict(valid, seq=4, vx=float('nan')),
                dict(valid, seq=5, bot_id=31),
                dict(valid, seq=6, presentation_time_us=3000001),
                dict(valid, seq=7, presentation_time_us=1.5),
                dict(valid, seq=0)):
            state.update_input(1, {
                'round_id': state.round_id, 'ram_contacts': [rejected]})
            self.assertEqual([1], list(state.players[1].ram_contacts))
        state.update_input(1, {
            'round_id': state.round_id + 1,
            'ram_contacts': [dict(valid, seq=8)]})
        self.assertEqual([1], list(state.players[1].ram_contacts))

    def test_native_ram_receipt_binds_contact_point_armor_and_time(self):
        state = self._live_state()
        player = state.players[1]
        bot_id = min(state.bot_states)
        state.bot_state_revision = 300
        state.bot_state_time_us = 3000000
        self.assertIn(player.player_id, state.human_collision_profiles)
        receipt = _valid_ram_receipt(
            state, bot_id, bot_state_revision=299,
            presentation_time_us=2900000,
            native_contact_time_us=2950000,
            contact_x=0.0, contact_y=0.0, contact_z=3.5,
            contact_armor_player=73.0, contact_armor_bot=45.0,
            contact_screened_player=False, contact_screened_bot=False,
            x=0.0, y=0.0, z=0.0, yaw=0.0,
            vx=0.0, vy=-3.0, vz=16.0)

        accepted = state._validated_ram_contact(player, receipt)

        self.assertEqual(73.0, accepted['contact_armor_player'])
        self.assertEqual(45.0, accepted['contact_armor_bot'])
        self.assertFalse(accepted['contact_screened_player'])
        self.assertEqual(-3.0, accepted['vy'])
        self.assertIsNone(state._validated_ram_contact(
            player, dict(receipt, contact_z=3.6)))
        self.assertIsNone(state._validated_ram_contact(
            player, dict(receipt, native_contact_time_us=1)))
        self.assertIsNone(state._validated_ram_contact(
            player, dict(receipt, contact_screened_player=True)))

    def test_native_ram_receipt_validates_contact_in_pitched_frozen_body(self):
        state = self._live_state()
        player = state.players[1]
        bot_id = min(state.bot_states)
        state.bot_state_revision = 300
        state.bot_state_time_us = 3000000
        shape = state.human_collision_profiles[player.player_id]['shape']
        pitch, roll = 0.30, -0.12
        axes = server_battle_authority._pose_axes(
            player.yaw, pitch, roll)
        local = (0.0, (shape[2] + shape[3]) * 0.5, shape[1])
        point = tuple(
            origin + sum(
                local[row] * axes[row][index] for row in range(3))
            for index, origin in enumerate((player.x, player.y, player.z)))
        receipt = _valid_ram_receipt(
            state, bot_id, bot_state_revision=299,
            presentation_time_us=2900000,
            pitch=pitch, roll=roll,
            contact_x=point[0], contact_y=point[1],
            contact_z=point[2], vx=0.0, vy=0.0, vz=16.5)

        accepted = state._validated_ram_contact(player, receipt)

        self.assertIsNotNone(accepted)
        self.assertEqual((pitch, roll), (
            accepted['pitch'], accepted['roll']))
        self.assertIsNone(state._validated_ram_contact(
            player, dict(receipt, pitch=0.0, roll=0.0)))

    def test_outside_body_ram_receipt_is_terminal_with_stable_reason(self):
        state = self._live_state()
        player = state.players[1]
        bot_id = min(state.bot_states)
        state.bot_state_revision = 300
        state.bot_state_time_us = 3000000
        shape = state.human_collision_profiles[player.player_id]['shape']
        receipt = _valid_ram_receipt(
            state, bot_id, bot_state_revision=299,
            presentation_time_us=2900000,
            x=0.0, y=0.0, z=0.0, yaw=0.0,
            contact_x=float(shape[0]) + 1.0,
            contact_y=(float(shape[2]) + float(shape[3])) * 0.5,
            contact_z=0.0)

        state.update_input(1, {
            'round_id': state.round_id, 'ram_contacts': [receipt]})

        self.assertEqual(1, player.ram_contact_seq)
        self.assertEqual(1, player.ram_contact_resolved_seq)
        self.assertEqual([], list(player.ram_contacts))
        self.assertEqual(
            'contact_outside_player_body',
            player.ram_contact_rejections[1])
        public = state._public_player(player)
        self.assertEqual(1, public['ram_contact_admitted_seq'])
        self.assertEqual(1, public['ram_contact_resolved_seq'])

        state.update_input(1, {
            'round_id': state.round_id, 'ram_contacts': [receipt]})
        self.assertEqual(
            {1: 'contact_outside_player_body'},
            dict(player.ram_contact_rejections))

    def test_accepted_ram_receipt_cannot_reappear_after_authority_change(self):
        state = self._live_state()
        player = state.players[1]
        bot_id = min(state.bot_states)
        bot = state.bot_states[bot_id]
        bot.update(x=player.x, z=player.z, health=1000, alive=True)
        state.bot_state_revision = 300
        state.bot_state_time_us = 3000000
        receipt = _valid_ram_receipt(
            state, bot_id, seq=9, bot_state_revision=299,
            presentation_time_us=2900000,
            x=player.x, y=player.y, z=player.z,
            yaw=0.0, vx=0.0, vy=0.0, vz=16.0)
        player.ram_contact_seq = 8
        state.update_input(1, {
            'round_id': state.round_id,
            'input_seq': 1, 'pose_time_us': 2900000,
            'speed': 16.0, 'ram_contacts': [receipt]})

        self.assertTrue(state.report_bot_ram(SERVER_AUTHORITY_ID, {
            'round_id': state.round_id,
            'bot_id': bot_id, 'target_kind': 'human', 'target_id': 1,
            'ram_seq': 1, 'damage_to_bot': 40,
            'damage_to_target': 80,
            'ram_contact_player_id': 1, 'ram_contact_seq': 9,
        }))

        self.assertEqual([], list(player.ram_contacts))
        self.assertEqual(9, player.ram_contact_seq)
        self.assertEqual(
            [], state._public_player(player).get('ram_contacts', []))
        # The input sender repeats its latest receipt until a newer contact.
        # Keeping the monotonic sequence after clearing prevents that old
        # payload from becoming visible to a takeover authority again.
        state.update_input(1, {
            'round_id': state.round_id,
            'input_seq': 2, 'pose_time_us': 2900000,
            'ram_contacts': [receipt]})
        self.assertEqual([], list(player.ram_contacts))

    def test_ram_contact_ledger_preserves_batch_and_is_idempotent(self):
        state = self._live_state()
        player = state.players[1]
        player.capabilities = (
            PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY)
        bot_id = min(state.bot_states)
        bot = state.bot_states[bot_id]
        bot.update(x=30.0, z=30.0, health=1000, alive=True)
        state.bot_state_revision = 300
        state.bot_state_time_us = 3000000

        def receipt(seq, presentation_time_us):
            return _valid_ram_receipt(
                state, bot_id, seq=seq, bot_state_revision=300,
                presentation_time_us=presentation_time_us,
                x=0.0, y=0.0, z=0.0, yaw=0.0,
                vx=0.0, vy=0.0, vz=16.0)

        state.update_input(1, {
            'round_id': state.round_id,
            'ram_contacts': [receipt(1, 1000000),
                             receipt(2, 2000000)],
        })
        self.assertEqual([1, 2], list(player.ram_contacts))
        self.assertEqual(2, player.ram_contact_seq)
        self.assertEqual(
            [1, 2], [value['seq'] for value in
                     state._public_player(player)['ram_contacts']])

        first = {
            'round_id': state.round_id,
            'bot_id': bot_id, 'target_kind': 'human', 'target_id': 1,
            'ram_seq': 11, 'damage_to_bot': 10,
            'damage_to_target': 20,
            'ram_contact_player_id': 1, 'ram_contact_seq': 1,
        }
        second = dict(
            first, ram_seq=12, damage_to_bot=11,
            damage_to_target=21, ram_contact_seq=2)
        self.assertFalse(state.report_bot_ram(
            SERVER_AUTHORITY_ID, second))
        self.assertEqual([1, 2], list(player.ram_contacts))
        self.assertTrue(state.report_bot_ram(SERVER_AUTHORITY_ID, first))
        self.assertEqual(1, player.ram_contact_resolved_seq)
        self.assertTrue(state.report_bot_ram(SERVER_AUTHORITY_ID, second))
        self.assertEqual([], list(player.ram_contacts))
        self.assertEqual(2, player.ram_contact_resolved_seq)
        self.assertEqual(
            2, state._public_player(player)['ram_contact_resolved_seq'])
        self.assertEqual(959, player.health)
        self.assertEqual(979, bot['health'])

        # Exact retransmission is success without a second HP mutation;
        # reusing the stable contact identity for other damage conflicts.
        self.assertTrue(state.report_bot_ram(SERVER_AUTHORITY_ID, second))
        self.assertFalse(state.report_bot_ram(
            SERVER_AUTHORITY_ID, dict(second, damage_to_target=22)))
        self.assertEqual((959, 979), (player.health, bot['health']))

    def test_receipt_terminal_noop_and_direct_human_report_policy(self):
        state = self._live_state()
        player = state.players[1]
        player.capabilities = (
            PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY)
        bot_id = min(state.bot_states)
        state.bot_states[bot_id].update(health=1000, alive=True)
        state.bot_state_revision = 50
        state.bot_state_time_us = 500000
        receipt = _valid_ram_receipt(
            state, bot_id, bot_state_revision=50,
            presentation_time_us=400000,
            x=0.0, y=0.0, z=0.0, yaw=0.0)
        state.update_input(1, {
            'round_id': state.round_id, 'ram_contacts': [receipt]})
        terminal = {
            'round_id': state.round_id,
            'bot_id': bot_id, 'target_kind': 'human', 'target_id': 1,
            'ram_seq': 7, 'damage_to_bot': 0, 'damage_to_target': 0,
            'ram_contact_player_id': 1, 'ram_contact_seq': 1,
        }

        self.assertTrue(state.report_bot_ram(
            SERVER_AUTHORITY_ID, terminal))
        self.assertEqual([], list(player.ram_contacts))
        self.assertEqual(1, player.ram_contact_resolved_seq)
        self.assertTrue(state.report_bot_ram(
            SERVER_AUTHORITY_ID, terminal))
        self.assertFalse(state.report_bot_ram(SERVER_AUTHORITY_ID, {
            'round_id': state.round_id,
            'bot_id': bot_id, 'target_kind': 'human', 'target_id': 1,
            'ram_seq': 8, 'damage_to_bot': 10, 'damage_to_target': 10,
        }))

    def test_invalid_or_conflicting_receipt_head_gets_input_decision(self):
        state = self._live_state()
        player = state.players[1]
        player.capabilities = (
            PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY)
        bot_id = min(state.bot_states)
        state.bot_state_revision = 10
        state.bot_state_time_us = 100000

        def receipt(seq, **values):
            fields = {
                'bot_state_revision': 10,
                'presentation_time_us': 100000,
                'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            }
            fields.update(values)
            return _valid_ram_receipt(
                state, bot_id, seq=seq, **fields)

        state.update_input(1, {
            'round_id': state.round_id,
            'ram_contacts': [
                receipt(1, bot_state_revision=11), receipt(2)],
        })

        self.assertEqual(2, player.ram_contact_seq)
        self.assertEqual([2], list(player.ram_contacts))

        first_three = receipt(3, x=1.0)
        other_three = receipt(3, x=2.0)
        state.update_input(1, {
            'round_id': state.round_id,
            'ram_contacts': [first_three, other_three, receipt(4)],
        })

        self.assertEqual(4, player.ram_contact_seq)
        self.assertEqual([2, 4], list(player.ram_contacts))

    def test_invalid_ram_receipt_waits_for_prior_terminal_frontier(self):
        state = self._live_state()
        player = state.players[1]
        player.capabilities = (
            PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY)
        bot_id = min(state.bot_states)
        state.bot_state_revision = 10
        state.bot_state_time_us = 100000
        shape = state.human_collision_profiles[player.player_id]['shape']
        first = _valid_ram_receipt(
            state, bot_id, seq=1, bot_state_revision=10,
            presentation_time_us=100000,
            x=0.0, y=0.0, z=0.0, yaw=0.0)
        invalid = _valid_ram_receipt(
            state, bot_id, seq=2, bot_state_revision=10,
            presentation_time_us=100000,
            x=0.0, y=0.0, z=0.0, yaw=0.0,
            contact_x=float(shape[0]) + 1.0)

        state.update_input(1, {
            'round_id': state.round_id,
            'ram_contacts': [first, invalid],
        })

        self.assertEqual(2, player.ram_contact_seq)
        self.assertEqual(0, player.ram_contact_resolved_seq)
        self.assertEqual([1], list(player.ram_contacts))
        self.assertEqual(
            'contact_outside_player_body',
            player.ram_contact_rejections[2])

        self.assertTrue(state.report_bot_ram(SERVER_AUTHORITY_ID, {
            'round_id': state.round_id,
            'bot_id': bot_id, 'target_kind': 'human', 'target_id': 1,
            'ram_seq': 1, 'damage_to_bot': 0, 'damage_to_target': 0,
            'ram_contact_player_id': 1, 'ram_contact_seq': 1,
        }))
        self.assertEqual(2, player.ram_contact_resolved_seq)
        self.assertEqual([], list(player.ram_contacts))

    def test_full_ram_ledger_does_not_advance_admission(self):
        state = self._live_state()
        player = state.players[1]
        player.capabilities = (
            PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY)
        bot_id = min(state.bot_states)
        state.bot_state_revision = 10
        state.bot_state_time_us = 100000
        for seq in range(100, 132):
            player.ram_contacts[seq] = {'seq': seq, 'bot_id': bot_id}
        receipt = _valid_ram_receipt(
            state, bot_id, bot_state_revision=10,
            presentation_time_us=100000,
            x=0.0, y=0.0, z=0.0, yaw=0.0)

        state.update_input(1, {
            'round_id': state.round_id, 'ram_contacts': [receipt]})
        self.assertEqual(0, player.ram_contact_seq)
        player.ram_contacts.pop(100)
        state.update_input(1, {
            'round_id': state.round_id, 'ram_contacts': [receipt]})
        self.assertEqual(1, player.ram_contact_seq)
        self.assertIn(1, player.ram_contacts)

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
                'visible_by_player_ids': [],
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
                      max_time_ms=2000, shot_seq=1, source_shot=None):
        message = {
            'type': 'projectile_launch',
            'round_id': state.round_id,
            'shooter_kind': 'player',
            'shooter_id': 1,
            'shot_seq': shot_seq,
            'shell_index': 0,
            'origin': [state.players[1].x, state.players[1].y + 1.0,
                       state.players[1].z],
            'velocity': list(velocity),
            'gravity': 9.81,
            'max_distance': 200.0,
            'max_time_ms': max_time_ms,
            'is_he': False,
            'splash_radius': 0.0,
            'penetration_factor': 1.0,
            'source_shot': (source_shot or _mounted_source_shot(
                state, 1, velocity, 9.81, 200.0)),
        }
        self.assertTrue(_launch_player_as_authority(
            state, SERVER_AUTHORITY_ID, message))
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

    def test_e50_mounted_105mm_alpha_survives_stock_75mm_descriptor(self):
        state, authority = self._live_state()
        stock = _projection()
        stock['name'] = 'germany:G54_E-50'
        stock['gun']['shots'][0]['shell'].update(
            caliber=75.0, damage=[135.0, 100.0])
        state.descriptor_store.add('germany:G54_E-50', stock)
        state.players[1].vehicle = 'germany:G54_E-50'
        frozen = _mounted_source_shot(
            state, 1, (0.0, 0.0, 100.0), 9.81, 200.0)
        frozen['shell'].update(caliber=105.0, damage=[390.0, 150.0])
        captured = []
        original = state.resolve_projectile

        def capture(player_id, message):
            captured.append(copy.deepcopy(message))
            return original(player_id, message)

        state.resolve_projectile = capture
        with mock.patch.object(
                server_battle_authority.random, 'uniform',
                side_effect=lambda low, high: (low + high) / 2.0):
            projectile_id = self._launch_human(
                state, source_shot=frozen)
            ledger_source_shot = copy.deepcopy(
                state.projectiles[projectile_id]['source_shot'])
            self._tick_until_terminal(state, projectile_id)

        stock_shot = authority._source_descriptor({
            'source_vehicle': 'germany:G54_E-50'}).gun.shots[0]
        self.assertEqual(135.0, stock_shot.shell.damage[0])
        self.assertEqual([390.0, 150.0],
                         ledger_source_shot['shell']['damage'])
        self.assertEqual(390, captured[-1]['direct']['potential_damage'])
        self.assertEqual(390, captured[-1]['direct']['damage'])

    def test_dead_vehicle_blocks_server_projectile_without_taking_damage(self):
        state, unused_authority = self._live_state()
        wreck = state.players[2]
        wreck.health = 0
        wreck.alive = False
        state.players[3] = _player(3, team=2, x=0.0, z=40.0)
        captured = []
        original = state.resolve_projectile

        def capture(player_id, message):
            captured.append(copy.deepcopy(message))
            return original(player_id, message)

        state.resolve_projectile = capture
        projectile_id = self._launch_human(state)
        self._tick_until_terminal(state, projectile_id)

        self.assertEqual(1000, state.players[3].health)
        self.assertTrue(captured[-1]['hit_vehicle'])
        self.assertIsNone(captured[-1]['direct'])
        self.assertEqual(
            {'target_kind': 'player', 'target_id': 2},
            captured[-1]['wreck_hit'])
        self.assertLess(captured[-1]['impact'][2], wreck.z)

    def test_dead_bot_blocks_server_projectile_without_taking_damage(self):
        state, authority = self._live_state()
        authority._bots.apply_snapshot = lambda unused_message: None
        wreck_id = min(state.bot_states)
        for bot_id, bot in state.bot_states.items():
            bot.update(x=200.0, y=0.0, z=200.0)
        wreck = state.bot_states[wreck_id]
        wreck.update(x=0.0, y=0.0, z=20.0, health=0, alive=False)
        state.players[2].z = 40.0
        captured = []
        original = state.resolve_projectile

        def capture(player_id, message):
            captured.append(copy.deepcopy(message))
            return original(player_id, message)

        state.resolve_projectile = capture
        projectile_id = self._launch_human(state)
        self._tick_until_terminal(state, projectile_id)

        self.assertEqual(1000, state.players[2].health)
        self.assertTrue(captured[-1]['hit_vehicle'])
        self.assertIsNone(captured[-1]['direct'])
        self.assertEqual(
            {'target_kind': 'bot', 'target_id': wreck_id},
            captured[-1]['wreck_hit'])
        self.assertLess(captured[-1]['impact'][2], wreck['z'])

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

    def test_canonical_expiry_advances_every_due_chord_in_the_same_tick(self):
        state, authority = self._live_state()
        state.players[2].x = 100.0
        state.players[2].z = 100.0
        first = self._launch_human(state, max_time_ms=34, shot_seq=1)
        second = self._launch_human(state, max_time_ms=34, shot_seq=2)

        state.tick_once(1.0 / TICK_HZ)
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
        frozen_origin = (7.0, 8.0, 9.0)
        bot.update({
            'fire_seq': 1, 'shot_yaw': 0.0, 'shot_pitch': 0.0,
            'shot_origin': frozen_origin,
        })
        state.bot_states[bot_id].update(bot)
        state.bot_pending_projectile_launches.add((bot_id, 1))
        now = float(state.tick) / TICK_HZ
        authority._projectiles.advance(
            now, authority._projectile_chord,
            authority._projectile_terminal, maximum_chords=0)
        self.assertTrue(authority._launch_bot_projectile(bot, 1, now))
        projectile_id = '%d:b:%d:1' % (state.round_id, bot_id)
        self.assertIn(projectile_id, state.projectiles)
        self.assertEqual(list(frozen_origin),
                         state.projectiles[projectile_id]['origin'])
        self.assertEqual(20000,
                         state.projectiles[projectile_id]['max_time_ms'])
        authority._reconcile_projectiles(state._projectile_snapshot())
        self.assertTrue(authority._projectiles.contains(projectile_id))

    def test_rapid_bot_clip_registers_each_edge_before_launching_it(self):
        state, authority = self._live_state()
        bot_id = next(bot_id for bot_id, bot in state.bot_states.items()
                      if int(bot['team']) == 1)
        for other_id, other in state.bot_states.items():
            other.update(x=200.0, y=0.0, z=200.0)
        shooter = state.bot_states[bot_id]
        shooter.update(x=0.0, y=0.0, z=0.0, alive=True, health=1000)
        state.players[1].x = 100.0
        state.players[1].z = 100.0
        state.players[2].x = 0.0
        state.players[2].y = 0.0
        state.players[2].z = 20.0
        state.players[2].health = 5000
        state.players[2].max_health = 5000
        original_update = state.update_bot_states

        def admit_edge(unused_authority, payload):
            launch = payload['launches'][0]
            sequence = int(launch['fire_seq'])
            shooter['fire_seq'] = sequence
            shooter['shell_index'] = int(launch['shell_index'])
            state.bot_pending_projectile_launches.add((bot_id, sequence))
            return True

        state.update_bot_states = admit_edge
        try:
            for sequence in range(1, 5):
                authority._route({
                    'type': 'bot_state',
                    'bots': [{'id': bot_id, 'fire_seq': sequence}],
                    'launches': [{
                        'id': bot_id, 'fire_seq': sequence,
                        'shell_index': 0, 'class_tag': 'lightTank',
                        'shot_yaw': 0.0, 'shot_pitch': 0.0,
                        'shot_origin': (0.0, 1.0, 0.0),
                    }],
                }, float(state.tick) / TICK_HZ)
        finally:
            state.update_bot_states = original_update

        projectile_ids = ['%d:b:%d:%d' % (
            state.round_id, bot_id, sequence) for sequence in range(1, 5)]
        self.assertTrue(all(projectile_id in state.projectiles
                            for projectile_id in projectile_ids))
        self.assertEqual(4, state._statistics_row('bot', bot_id)[
            'shots_fired'])
        self.assertEqual(set(), state.bot_pending_projectile_launches)
        for unused in range(30):
            state.tick_once(1.0 / TICK_HZ)
            if all(projectile_id in state.projectile_tombstones
                   for projectile_id in projectile_ids):
                break
        self.assertTrue(all(
            state.projectile_tombstones.get(projectile_id, {}).get(
                'outcome') == 'impact'
            for projectile_id in projectile_ids))
        self.assertLess(state.players[2].health, 5000)


_TRACK_CRITICAL = {
    'devices': [{'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 100.0,
                 'state': 'destroyed'}],
    'destroyed': ['leftTrackHealth'], 'crew_ko': [],
    'fire': False, 'ammo_rack_death': False,
    'events': [{'kind': 'device', 'name': 'leftTrackHealth',
                'state': 'destroyed', 'cause': 'shot'}],
}

_AMMO_RACK_CRITICAL = {
    'devices': [{'name': 'ammoBayHealth', 'hp': 0.0, 'max_hp': 100.0,
                 'state': 'destroyed'}],
    'destroyed': ['ammoBayHealth'], 'crew_ko': [],
    'fire': False, 'ammo_rack_death': True,
    'events': [
        {'kind': 'device', 'name': 'ammoBayHealth',
         'old_state': 'normal', 'state': 'destroyed', 'cause': 'shot'},
        {'kind': 'ammo_rack', 'state': 'destroyed', 'cause': 'shot'},
    ],
}

_WHOLE_CREW_CRITICAL = {
    'devices': [], 'destroyed': [],
    'crew_roster': ['commander', 'driver'],
    'crew_ko': ['commander', 'driver'],
    'fire': False, 'ammo_rack_death': False,
    'events': [{'kind': 'crew', 'name': 'driver',
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
            'origin': [state.players[shooter_id].x,
                       state.players[shooter_id].y + 1.0,
                       state.players[shooter_id].z],
            'velocity': [0.0, 0.0, 100.0],
            'gravity': 9.81,
            'max_distance': 200.0,
            'max_time_ms': 2000,
            'is_he': False,
            'splash_radius': 0.0,
            'penetration_factor': 1.0,
            'source_shot': _mounted_source_shot(
                state, shooter_id, (0.0, 0.0, 100.0), 9.81, 200.0),
        }
        self.assertTrue(_launch_player_as_authority(
            state, SERVER_AUTHORITY_ID, launch))
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
            profile = state.players[target_id].effective_params['critical'][
                'devices']
            known = set(row['name'] for row in profile)
            for row in critical.get('devices') or ():
                if row['name'] not in known:
                    profile.append({
                        'name': row['name'], 'max_hp': row['max_hp'],
                        'regen_hp': row['max_hp'] * 0.5,
                    })
                    known.add(row['name'])
            delta = {
                'devices': [
                    {'name': row['name'],
                     'hp_loss': max(0.001, row['max_hp'] - row['hp'])}
                    for row in critical.get('devices') or ()
                ],
                'crew_ko': list(critical.get('crew_ko') or ()),
                'ignite': bool(critical.get('fire', False)),
            }
            direct.update({
                'critical': critical,
                'critical_delta': delta,
                'critical_target_base_revision':
                    target.critical_report_base_revision,
                'critical_target_ack_seq': target.critical_ack_seq,
                'hull_damage': damage,
            })
        resolution = {
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
        }
        self.assertTrue(state.resolve_projectile(
            SERVER_AUTHORITY_ID, resolution))
        return resolution

    def test_same_baseline_projectile_deltas_both_apply_once(self):
        state = self._live_state()
        target = state.players[2]
        critical = {
            'devices': [{'name': 'engineHealth', 'hp': 90.0,
                         'max_hp': 100.0, 'state': 'normal'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        base = target.critical_report_base_revision
        ack = target.critical_ack_seq

        def resolution(shot_seq):
            projectile_id = self._launch(state, 1, shot_seq)
            record = state.projectiles[projectile_id]
            return {
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
                'direct': {
                    'target_kind': 'player', 'target_id': 2,
                    'damage': 0, 'shot_result': 2,
                    'x': 0.0, 'y': 1.0, 'z': 20.0,
                    'critical': critical,
                    'critical_delta': {
                        'devices': [{
                            'name': 'engineHealth', 'hp_loss': 10.0}],
                        'crew_ko': [], 'ignite': False,
                    },
                    'critical_target_base_revision': base,
                    'critical_target_ack_seq': ack,
                    'hull_damage': 0,
                },
                'splash': [], 'destructibles': [],
            }

        first = resolution(1)
        second = resolution(2)
        self.assertTrue(state.resolve_projectile(
            SERVER_AUTHORITY_ID, first))
        self.assertTrue(state.resolve_projectile(
            SERVER_AUTHORITY_ID, second))
        self.assertEqual(80.0, next(
            row['hp'] for row in target.critical['devices']
            if row['name'] == 'engineHealth'))
        revision = target.critical_revision
        self.assertTrue(state.resolve_projectile(
            SERVER_AUTHORITY_ID, second))
        self.assertEqual(revision, target.critical_revision)
        self.assertEqual(80.0, next(
            row['hp'] for row in target.critical['devices']
            if row['name'] == 'engineHealth'))

    def test_plain_ap_without_critical_delta_still_applies_hull_damage(self):
        state = self._live_state()
        target = state.players[2]
        revision = target.critical_revision

        self._shoot(state, 1, 2, 115, critical={
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': [],
        })

        self.assertEqual(885, target.health)
        self.assertEqual(revision, target.critical_revision)

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

    def test_visible_owner_repair_verdict_is_rejected(self):
        state = self._live_state()
        target = state.players[2]
        self._shoot(state, 1, 2, 0, critical=_TRACK_CRITICAL)
        first_base = target.critical_report_base_revision
        before = (
            copy.deepcopy(target.critical), target.critical_ack_seq,
            target.critical_report_base_revision)

        repaired = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 50.0,
                         'max_hp': 100.0, 'state': 'critical'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False,
            'events': [{'kind': 'device', 'name': 'leftTrackHealth',
                        'old_state': 'destroyed', 'state': 'critical',
                        'cause': 'repair'}],
        }
        self.assertFalse(state._apply_reported_health(target, {
            'reported_health': target.health,
            'reported_critical': repaired,
            'reported_critical_base_revision': first_base,
            'reported_critical_seq': 1,
        }))
        self.assertEqual(before, (
            target.critical, target.critical_ack_seq,
            target.critical_report_base_revision))

    def test_versioned_track_repair_accepts_only_monotonic_red_to_yellow(self):
        state = self._live_state()
        target = state.players[2]
        self._shoot(state, 1, 2, 0, critical=_TRACK_CRITICAL)
        base = target.critical_report_base_revision

        def report(seq, hp, phase='destroyed', revision=base,
                   name='leftTrackHealth'):
            return {
                'type': 'track_repair', 'round_id': state.round_id,
                'critical_base_revision': revision, 'repair_seq': seq,
                'tracks': [{
                    'name': name, 'hp': hp, 'max_hp': 100.0,
                    'state': phase,
                }],
            }

        self.assertTrue(state.report_track_repair(
            2, report(1, 25.0)))
        self.assertEqual(1, target.critical_ack_seq)
        self.assertEqual(25.0, target.critical['devices'][0]['hp'])
        self.assertEqual(['leftTrackHealth'], target.critical['destroyed'])

        # An exact retry is idempotent; a same-sequence mutation is not.
        before = copy.deepcopy(target.critical)
        self.assertTrue(state.report_track_repair(
            2, report(1, 25.0)))
        self.assertFalse(state.report_track_repair(
            2, report(1, 26.0)))
        self.assertEqual(before, target.critical)

        # Stale lineage, decreasing HP and a non-track device all fail closed.
        self.assertFalse(state.report_track_repair(
            2, report(2, 30.0, revision=base - 1)))
        self.assertFalse(state.report_track_repair(
            2, report(2, 24.0)))
        self.assertFalse(state.report_track_repair(
            2, report(2, 30.0, name='engineHealth')))
        self.assertEqual(before, target.critical)
        self.assertEqual(1, target.critical_ack_seq)

        self.assertTrue(state.report_track_repair(
            2, report(2, 50.0, phase='critical')))
        self.assertEqual(2, target.critical_ack_seq)
        self.assertEqual([], target.critical['destroyed'])
        self.assertEqual('critical', target.critical['devices'][0]['state'])
        self.assertEqual([], target.critical['events'])

        # Yellow is functional but remains damaged; the owner cannot promote
        # it to normal or continue rewriting it after the red->yellow edge.
        self.assertFalse(state.report_track_repair(
            2, report(3, 100.0, phase='normal')))
        self.assertFalse(state.report_track_repair(
            2, report(3, 60.0, phase='critical')))
        self.assertEqual(2, target.critical_ack_seq)

        # A later hit opens a new lineage; an old in-flight repair cannot
        # overwrite that newer canonical damage.
        self._shoot(state, 1, 2, 0, shot_seq=2,
                    critical=_TRACK_CRITICAL)
        newer = copy.deepcopy(target.critical)
        self.assertGreater(target.critical_report_base_revision, base)
        self.assertFalse(state.report_track_repair(
            2, report(3, 75.0, phase='critical', revision=base)))
        self.assertEqual(newer, target.critical)

    def test_ammo_rack_death_clamps_health_and_terminal_retry_is_idempotent(self):
        state = self._live_state()
        target = state.players[2]
        target.health = 880
        target.max_health = 880
        target.display_health = 880

        resolution = self._shoot(
            state, 1, 2, 890, critical=_AMMO_RACK_CRITICAL)
        self.assertTrue(state.resolve_projectile(
            SERVER_AUTHORITY_ID, resolution))

        self.assertEqual(0, target.health)
        self.assertFalse(target.alive)
        self.assertEqual(
            880, state.vehicle_statistics[('player', 1)]['damage_dealt'])
        self.assertEqual(
            1, state.vehicle_statistics[('player', 1)]['kills'])
        hits = [event for event in state.pending_events
                if event.get('kind') == 'hit']
        self.assertEqual(1, len(hits))
        self.assertEqual(880, hits[0]['damage'])
        self.assertTrue(hits[0]['critical']['ammo_rack_death'])

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
            'damage_assisted_stun': 0,
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
        combat = [event for event in state.pending_events
                  if event.get('kind') == 'hit']
        self.assertEqual([260, 0], [
            event['blocked_damage'] for event in combat])

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
            'damage_assisted_stun': 0,
            'kills': 0,
        }, state.vehicle_statistics[('player', 3)])

    def _report_spotted(self, state, player_id, targets):
        return state.update_spotted_targets(player_id, {
            'type': 'spotted_report',
            'round_id': state.round_id,
            'targets': [{'target_kind': kind, 'target_id': target_id}
                        for kind, target_id in targets],
        })

    def test_visible_spotted_report_cannot_mint_radio_assist(self):
        state = self._live_state()
        self.assertFalse(self._report_spotted(state, 3, [('player', 2)]))
        self._shoot(state, 1, 2, 180)

        self.assertEqual([], self._assists(state))
        self.assertNotIn(('player', 3), state.vehicle_statistics)
        self.assertEqual(
            0, state.vehicle_statistics[('player', 1)][
                'damage_assisted_radio'])

    def test_a_reporter_earns_no_radio_assist_from_its_own_damage(self):
        state = self._live_state()
        self.assertFalse(self._report_spotted(state, 1, [('player', 2)]))
        self._shoot(state, 1, 2, 180)

        self.assertEqual([], self._assists(state))
        self.assertEqual(
            0, state.vehicle_statistics[('player', 1)][
                'damage_assisted_radio'])

    def test_an_empty_report_clears_the_previous_spotted_set(self):
        state = self._live_state()
        self.assertFalse(self._report_spotted(state, 3, [('player', 2)]))
        self.assertFalse(self._report_spotted(state, 3, []))
        self._shoot(state, 1, 2, 180)

        self.assertEqual([], self._assists(state))

    def test_visible_spotted_reports_do_not_create_statistics(self):
        state = self._live_state()
        enemy_bot_id = min(
            bot_id for bot_id, bot in state.bot_states.items()
            if int(bot['team']) == 2)

        self.assertFalse(self._report_spotted(state, 3, [('player', 2)]))
        self.assertFalse(self._report_spotted(state, 3, []))
        self.assertFalse(self._report_spotted(
            state, 3, [('player', 2), ('bot', enemy_bot_id)]))

        self.assertNotIn(('player', 3), state.vehicle_statistics)
        self.assertNotIn(('player', 3), state.vehicle_interactions)
        self.assertNotIn(3, state.player_spotted)

    def test_visible_spotted_report_is_rejected_in_every_phase(self):
        state = _state_with_authority()
        self.assertFalse(self._report_spotted(state, 1, []))
        unused_message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertEqual('loading', state.phase)
        self.assertFalse(self._report_spotted(state, 1, []))
        live = state.mark_battle_ready(
            1, {'round_id': state.round_id})

        self.assertIsNotNone(live)
        self.assertEqual('battle', state.phase)
        self.assertEqual(0, state.tick)
        self.assertFalse(self._report_spotted(state, 1, []))
        self.assertNotIn(1, state.player_spotted)

    def test_a_dead_reporter_stops_earning_radio_assist(self):
        state = self._live_state()
        self.assertFalse(self._report_spotted(state, 3, [('player', 2)]))
        state.players[3].alive = False
        self._shoot(state, 1, 2, 180)

        self.assertEqual([], self._assists(state))

    def test_visible_spotted_report_refuses_every_claim_entirely(self):
        state = self._live_state()
        enemy_bot_id = min(bot_id for bot_id, bot in state.bot_states.items()
                           if int(bot['team']) == 2)
        self.assertFalse(self._report_spotted(state, 3, [('player', 2)]))

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
        self.assertNotIn(3, state.player_spotted)
        self.assertFalse(self._report_spotted(
            state, 3, [('player', 2), ('bot', enemy_bot_id)]))

    def test_visible_spot_cannot_join_canonical_track_assist(self):
        state = self._live_state()
        self._shoot(state, 1, 2, 50, critical=_TRACK_CRITICAL)
        self.assertFalse(self._report_spotted(state, 3, [('player', 2)]))
        state.pending_events = []
        self._shoot(state, 4, 2, 200)

        self.assertEqual(
            [('track', 1)],
            [(event['category'], event['assister_id'])
             for event in self._assists(state)])
        self.assertEqual(
            200, state.vehicle_statistics[('player', 1)][
                'damage_assisted_track'])
        self.assertNotIn(('player', 3), state.vehicle_statistics)
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

    def test_full_health_400_alpha_penetration_never_reports_200_damage(self):
        state = self._live_state()
        alpha = _projection()
        alpha['name'] = 'test:400-alpha'
        alpha['gun']['shots'][0]['shell']['damage'] = [400.0, 165.0]
        state.descriptor_store.add('test:400-alpha', alpha)
        state.server_authority.descriptors.add('test:400-alpha', alpha)
        state.players[1].vehicle = 'test:400-alpha'
        state.players[2].health = 880
        state.players[2].max_health = 880
        captured = []
        original = state.resolve_projectile

        def capture(player_id, message):
            captured.append(copy.deepcopy(message))
            return original(player_id, message)

        state.resolve_projectile = capture

        with mock.patch.object(
                server_battle_authority.random, 'uniform',
                side_effect=lambda low, unused_high: low):
            projectile_id = self._launch(state, 1)
            for unused in range(30):
                state.tick_once(1.0 / TICK_HZ)
                if projectile_id in state.projectile_tombstones:
                    break
            else:
                self.fail('projectile did not reach a terminal')

        self.assertEqual(300, captured[-1]['direct']['potential_damage'])
        self.assertEqual(300, captured[-1]['direct']['damage'])
        self.assertEqual(580, state.players[2].health)
        self.assertEqual(
            300, state.vehicle_statistics[('player', 1)]['damage_dealt'])

    def test_a_kill_is_counted_once_for_the_attacker(self):
        state = self._live_state()
        self._shoot(state, 3, 2, 1000)

        self.assertFalse(state.players[2].alive)
        self.assertEqual(1, state.vehicle_statistics[('player', 3)]['kills'])
        self.assertEqual(
            1000, state.vehicle_statistics[('player', 3)]['damage_dealt'])

    def test_whole_crew_knockout_kills_and_preserves_remaining_hull_hp(self):
        state = self._live_state()

        self._shoot(
            state, 3, 2, 25, critical=_WHOLE_CREW_CRITICAL)

        target = state.players[2]
        self.assertFalse(target.alive)
        self.assertEqual(975, target.health)
        self.assertEqual(975, target.display_health)
        self.assertEqual(0, target.death_reason)
        self.assertEqual('player', target.death_attacker_kind)
        self.assertEqual(3, target.death_attacker_id)
        self.assertEqual(1, state.players[3].frags)
        self.assertEqual(
            1, state.vehicle_statistics[('player', 3)]['kills'])
        hit = [event for event in state.pending_events
               if event.get('kind') == 'hit'][-1]
        self.assertTrue(hit['dead'])
        self.assertEqual(975, hit['health'])
        self.assertEqual(0, hit['death_reason'])

    def test_partial_crew_knockout_does_not_kill(self):
        state = self._live_state()
        critical = copy.deepcopy(_WHOLE_CREW_CRITICAL)
        critical['crew_ko'] = ['commander']

        self._shoot(state, 3, 2, 25, critical=critical)

        self.assertTrue(state.players[2].alive)
        self.assertEqual(975, state.players[2].health)
        self.assertEqual(0, state.players[3].frags)

    def test_crew_knockout_cannot_claim_a_name_outside_exact_roster(self):
        critical = copy.deepcopy(_WHOLE_CREW_CRITICAL)
        critical['crew_roster'] = ['commander']

        with self.assertRaisesRegex(ValueError, 'outside roster'):
            _critical_payload(critical)


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
        with server_battle_authority.engine_modules(lambda: 0.0), \
                mock.patch.object(
                    server_battle_authority.critical_damage,
                    'propose_explosion', wraps=(
                        server_battle_authority.critical_damage.
                        propose_explosion)) as explosion:
            effects = authority._splash_effects(
                {'shooter_kind': 'bot', 'shooter_id': 99,
                 'shell_index': 0, 'is_he': True,
                 'source_shot':
                    server_battle_authority._source_shot_from_descriptor(
                        authority._bots._descriptors[99].gun.shots[0])},
                impact, None)
        self.assertGreater(explosion.call_count, 0)
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
                 'shell_index': 0, 'is_he': True,
                 'source_shot':
                    server_battle_authority._source_shot_from_descriptor(
                        authority._bots._descriptors[99].gun.shots[0])}, impact,
                {'target_kind': 'bot', 'target_id': victim_id})
        self.assertFalse(any(
            effect['target_id'] == victim_id for effect in effects))

    def test_direct_he_uses_the_explosion_cone_not_the_solid_ray(self):
        state = _state_with_authority()
        state.request_start(1, '01_karelia')
        state.mark_battle_ready(1, {'round_id': state.round_id})
        authority = state.server_authority
        authority.descriptors.add('he:test', self._he_projection())
        descriptor = authority.descriptors.get('he:test')
        shot = server_battle_authority._source_shot_from_descriptor(
            descriptor.gun.shots[0])
        target = {
            'kind': 'player', 'id': 1, 'health': 1000,
            'descriptor': descriptor,
            'position': (0.0, 0.0, 20.0), 'yaw': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0, 'state': {
                'critical': {},
            },
            'collisions': (), 'ray_start': (0.0, 1.0, 10.0),
            'ray_end': (0.0, 1.0, 30.0),
        }
        meta = {
            'shooter_kind': 'bot', 'shooter_id': 99, 'shot_seq': 1,
            'source_shot': shot, 'penetration_factor': 1.0,
        }
        projectile = {'distance': 20.0, 'position': (0.0, 1.0, 19.0)}

        with mock.patch.object(
                server_battle_authority.combat_rules, 'resolve_hull_hit',
                return_value=(2,)), mock.patch.object(
                    server_battle_authority.combat_rules, 'damage',
                    return_value=120), mock.patch.object(
                        server_battle_authority.critical_damage,
                        'propose_explosion',
                        return_value=(120, None, None)) as cone, \
                mock.patch.object(
                    server_battle_authority.critical_damage,
                    'propose_direct', side_effect=AssertionError(
                        'HE must not use a solid internal ray')):
            effect = authority._direct_effect(meta, projectile, target)

        self.assertEqual(120, effect['damage'])
        self.assertEqual(1, cone.call_count)
        direction = cone.call_args[0][3]
        self.assertGreater(direction.z, 0.0)

    def test_donated_descriptor_produces_a_real_he_internal_cone_hit(self):
        descriptor = wrap(self._he_projection())
        target = server_battle_authority._TargetMock(
            2, 1000, descriptor, (0.0, 0.0, 0.0), 0.0,
            {'critical': {}}, aim_yaw=0.0, gun_pitch=0.0)
        shell = server_battle_authority._source_shot_from_descriptor(
            descriptor.gun.shots[0])['shell']

        with server_battle_authority.engine_modules(lambda: 0.0):
            layout = (server_battle_authority.critical_damage.
                      _offh_internal_layout(descriptor))
            self.assertIsNotNone(layout)
            self.assertTrue(layout['valid'])
            engine = next(record for record in layout['targets']
                          if record['entity'] == 'engine')
            center = engine['center']
            # Hull-local -> vehicle/world at yaw zero. Place the burst half a
            # metre behind the engine so the 122 mm shell's 1.22 m cone
            # crosses the actual donated profile volume.
            burst = server_battle_authority.Vector3(
                center[0], center[1] + 0.6, center[2] - 0.5)
            direction = server_battle_authority.Vector3(0.0, 0.0, 1.0)
            with mock.patch('random.uniform', return_value=450.0), \
                    mock.patch('random.random', return_value=0.0):
                unused_damage, payload = (
                    server_battle_authority.critical_damage.propose_explosion(
                        target, (), burst, direction, 0, shell, 99))

        self.assertIsNotNone(payload)
        self.assertIn(
            'engineHealth',
            set(record['name'] for record in payload['devices']))


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
        # Server authority intentionally owns only one donated hull OBB face;
        # turret plates, spaced armor and native material flags are not part
        # of this projection and must not be synthesized here.
        self.assertEqual(1, len(entry['collisions']))
        collision = entry['collisions'][0]
        self.assertEqual('hull', collision.compName)
        self.assertFalse(hasattr(collision.matInfo, 'extra'))
        self.assertAlmostEqual(18.0, collision.matInfo.armor)
        self.assertGreater(collision.hitAngleCos, 0.9)

    def test_module_trace_stops_ten_calibres_after_first_collision(self):
        shot = server_battle_authority._source_shot_from_descriptor(
            wrap(_projection()).gun.shots[0])
        shot['shell']['caliber'] = 100.0
        collisions = (
            server_battle_authority._SyntheticCollision(
                5.0, 1.0, 20.0, 'hull'),
            server_battle_authority._SyntheticCollision(
                5.99, 1.0, 0.0, 'inside'),
            server_battle_authority._SyntheticCollision(
                6.01, 1.0, 0.0, 'tooFar'),
        )

        limited, trace_start, trace_end = (
            server_battle_authority._critical_vehicle_trace(
                shot, (0.0, 0.0, 0.0), (0.0, 0.0, 10.0), collisions))

        self.assertEqual([5.0, 5.99], [item.dist for item in limited])
        self.assertAlmostEqual(0.0, trace_start.z)
        self.assertAlmostEqual(6.0, trace_end.z)

    def test_module_trace_extends_full_budget_past_a_late_chord_hit(self):
        shot = server_battle_authority._source_shot_from_descriptor(
            wrap(_projection()).gun.shots[0])
        shot['shell']['caliber'] = 100.0
        collisions = (
            server_battle_authority._SyntheticCollision(
                9.80, 1.0, 20.0, 'hull'),
            server_battle_authority._SyntheticCollision(
                10.79, 1.0, 0.0, 'inside'),
            server_battle_authority._SyntheticCollision(
                10.81, 1.0, 0.0, 'outside'),
        )

        limited, unused_start, trace_end = (
            server_battle_authority._critical_vehicle_trace(
                shot, (0.0, 0.0, 0.0),
                (10.0, 0.0, 0.0), collisions))

        self.assertEqual([9.80, 10.79], [item.dist for item in limited])
        self.assertAlmostEqual(10.8, trace_end.x)

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
