import sys
import unittest
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
    def sendall(self, unused_payload):
        pass


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


def _state_with_authority():
    state = BattleState(map_name='01_karelia')
    state.client_build = CLIENT_BUILD_0922
    state.descriptor_store.add('ussr:R11_MS-1', _projection())
    player = _player(1)
    state.players[1] = player
    state._elect_room_host()
    return state


class ServerAuthorityElectionTest(unittest.TestCase):
    def test_server_owns_bot_authority_after_start(self):
        state = _state_with_authority()
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
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

    def test_capture_bases_come_from_the_navigation_graph(self):
        state = _state_with_authority()
        unused_message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertIn(1, state.capture_bases)
        self.assertIn(2, state.capture_bases)

    def test_without_donation_falls_back_to_client_authority(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.players[1] = _player(1)
        state._elect_room_host()
        unused_message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertIsNone(state.server_authority)
        self.assertEqual(1, state.bot_authority_id)

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
