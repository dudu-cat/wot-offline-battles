from pathlib import Path
import sys
import unittest
from unittest import mock


SERVER_ROOT = Path(__file__).resolve().parents[1] / 'server'
sys.path.insert(0, str(SERVER_ROOT))

from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    PROJECTILE_CAPABILITY,
)


class _Connection(object):
    def __init__(self):
        self.messages = []

    def sendall(self, payload):
        self.messages.append(payload)


def _hello(index):
    return {
        'client_build': CLIENT_BUILD_0922,
        'capabilities': [
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY],
        'name': 'Player-%d' % index,
        'vehicle': 'ussr:R11_MS-1',
        'max_health': 90,
    }


class ServerTeamSizeTests(unittest.TestCase):
    def test_default_roster_still_has_fifteen_tanks_per_team(self):
        state = BattleState()

        self.assertEqual(15, state.team_size)
        self.assertEqual(30, len(state.bot_roster))

    def test_configured_roster_uses_only_the_selected_team_slots(self):
        state = BattleState(team_size=4)

        self.assertEqual(8, len(state.bot_roster))
        self.assertEqual(
            {(team, slot) for team in (1, 2) for slot in range(4)},
            {(bot['team'], bot['slot']) for bot in state.bot_roster})

    def test_humans_occupy_selected_slots_in_both_rounds(self):
        state = BattleState(team_size=4, authority_mode='client')
        players = []
        for index in range(3):
            player, error = state.add_player(
                _Connection(), ('10.0.0.%d' % (index + 1), 1000 + index),
                _hello(index))
            self.assertIsNone(error)
            players.append(player)

        start, error = state.request_start(players[0].player_id)

        self.assertIsNone(error)
        self.assertEqual(4, start['team_size'])
        occupied = {(player.team, player.slot) for player in players}
        self.assertFalse(occupied & {
            (bot['team'], bot['slot']) for bot in state.bot_roster})
        self.assertEqual(8 - len(players), len(state.bot_roster))

        state._reset_round()

        self.assertEqual('waiting', state.phase)
        self.assertFalse(occupied & {
            (bot['team'], bot['slot']) for bot in state.bot_roster})
        self.assertEqual(8 - len(players), len(state.bot_roster))
        self.assertEqual(4, state.lobby_message()['team_size'])

    def test_humans_cannot_expand_a_selected_four_tank_team(self):
        state = BattleState(team_size=4)
        for index in range(8):
            player, error = state.add_player(
                _Connection(), ('10.0.0.%d' % (index + 1), 1000 + index),
                _hello(index))
            self.assertIsNotNone(player)
            self.assertIsNone(error)

        player, error = state.add_player(
            _Connection(), ('10.0.0.9', 1009), _hello(9))

        self.assertIsNone(player)
        self.assertEqual('full', error)

    def test_invalid_team_sizes_are_rejected(self):
        for value in (0, 16, 'invalid', 1.5, True):
            with self.assertRaises((TypeError, ValueError), msg=value):
                BattleState(team_size=value)

    def test_random_start_chooses_from_the_active_client_map_pool(self):
        state = BattleState(
            map_name='01_karelia', team_size=1, authority_mode='client')
        player, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1))
        self.assertIsNone(error)

        with mock.patch(
                'lan_battle_server.random.choice',
                return_value='05_prohorovka') as choose:
            start, error = state.request_start(
                player.player_id, 'server_random')

        self.assertIsNone(error)
        self.assertEqual('05_prohorovka', state.map_name)
        self.assertEqual('05_prohorovka', start['map'])
        choose.assert_any_call(tuple(state._active_map_pool()))

    def test_unknown_start_map_remains_fail_closed(self):
        state = BattleState(
            map_name='01_karelia', team_size=1, authority_mode='client')
        player, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1))
        self.assertIsNone(error)

        start, error = state.request_start(player.player_id, '99_missing')

        self.assertIsNone(start)
        self.assertEqual('invalid_map', error)
        self.assertEqual('01_karelia', state.map_name)


if __name__ == '__main__':
    unittest.main()
