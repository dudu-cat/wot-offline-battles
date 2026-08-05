import json
import threading
import time
import unittest

from lan_battle_server import (
    BattleState, CLIENT_BUILD_0922, ClientHandler, Player, PROTOCOL_VERSION,
)
from server_bot_ai import BotPlanner


def _manifest():
    return [
        {"id": 1, "team": 1, "slot": 0, "health": 1000, "profile": {"role": "heavy"}},
        {"id": 2, "team": 1, "slot": 1, "health": 1000},
        {"id": 3, "team": 2, "slot": 0, "health": 1000},
    ]


def _states():
    return [
        {"id": 1, "team": 1, "alive": True, "x": 0, "z": -20},
        {"id": 2, "team": 1, "alive": True, "x": 10, "z": -20},
        {"id": 3, "team": 2, "alive": True, "x": 0, "z": 20},
    ]


def _cover_candidate(candidate_id="rock", x=-8.0, z=-12.0):
    return {
        "id": candidate_id,
        "position": {"x": x, "y": 0.0, "z": z},
        "peek_position": {"x": x + 6.0, "y": 0.0, "z": z + 2.0},
        "travel_distance": 12.0,
        "route_alignment": 0.7,
        "enemy_occlusion": 0.9,
        "exposure": 0.1,
        "slope": 3.0,
        "water": 0.0,
        "ally_congestion": 0.0,
        "peek_feasible": True,
        "escape_feasible": True,
    }


def _wire_manifest(state):
    return [dict(value, vehicle="ussr:R11_MS-1", health=500,
                 max_health=500, x=float(value["slot"]), y=0.0,
                 z=-20.0 if value["team"] == 1 else 20.0,
                 yaw=0.0 if value["team"] == 1 else 3.14)
            for value in state.bot_roster]


def _wire_states(state):
    return [dict(value) for unused_id, value in sorted(state.bot_states.items())]


class ServerBotPlannerTests(unittest.TestCase):
    def _started_state(self, players=1):
        state = BattleState(map_name="04_himmelsdorf")
        for player_id in range(1, players + 1):
            team = 1 if player_id % 2 else 2
            slot = (player_id - 1) // 2
            state.players[player_id] = Player(
                player_id, object(), ("127.0.0.1", player_id),
                team=team, slot=slot)
        state.request_start(1)
        return state

    def test_map_pool_is_unique_and_invalid_fixed_map_fails_early(self):
        from lan_battle_server import MAP_POOL, MAP_POOL_082, MAP_POOL_0922

        self.assertEqual(len(MAP_POOL), len(set(MAP_POOL)))
        self.assertEqual(33, len(MAP_POOL_082))
        self.assertEqual(42, len(MAP_POOL_0922))
        self.assertEqual({
            "03_campania", "15_komarin", "39_crimea",
            "42_north_america", "51_asia",
        }, set(MAP_POOL_082) - set(MAP_POOL_0922))
        self.assertEqual({
            "59_asia_great_wall", "63_tundra", "73_asia_korea",
            "83_kharkiv", "84_winter", "86_himmelsdorf_winter",
            "92_stalingrad", "95_lost_city", "100_thepit", "101_dday",
            "103_ruinberg_winter", "112_eiffel_tower_ctf", "114_czech",
            "217_er_alaska",
        }, set(MAP_POOL_0922) - set(MAP_POOL_082))
        with self.assertRaises(ValueError):
            BattleState(map_name="not_a_standard_map")

    def test_manifest_must_cover_exact_final_roster_atomically(self):
        state = self._started_state(players=1)
        manifest = _wire_manifest(state)

        self.assertFalse(state.update_bot_manifest(1, {
            "bots": manifest[:-1]}))
        self.assertEqual([], state.bot_manifest)
        duplicate = list(manifest)
        duplicate[-1] = dict(duplicate[0])
        self.assertFalse(state.update_bot_manifest(1, {"bots": duplicate}))
        self.assertEqual([], state.bot_manifest)
        self.assertTrue(state.update_bot_manifest(1, {"bots": manifest}))
        self.assertEqual(len(state.bot_roster), len(state.bot_manifest))
        self.assertEqual({value["id"] for value in state.bot_roster},
                         set(state.bot_states))

    def test_empty_full_human_manifest_rejects_injected_bot_atomically(self):
        state = self._started_state(players=30)
        self.assertEqual([], state.bot_roster)

        self.assertFalse(state.update_bot_manifest(1, {"bots": [{"id": 99}]}))
        self.assertIsNone(state.bot_manifest_authority_id)
        self.assertTrue(state.update_bot_manifest(1, {"bots": []}))
        self.assertEqual(1, state.bot_manifest_authority_id)

    def test_modern_round_id_fences_late_battle_messages(self):
        state = self._started_state(players=1)
        stale_round = state.round_id - 1

        self.assertFalse(state.update_input(1, {
            "round_id": stale_round, "fire_seq": 1}))
        self.assertEqual(0, state.players[1].fire_seq)
        self.assertFalse(state.update_bot_manifest(1, {
            "round_id": stale_round, "bots": _wire_manifest(state)}))
        self.assertEqual([], state.bot_manifest)
        self.assertFalse(state.report_battle_result(1, {
            "round_id": stale_round, "winner": 1, "reason": "late"}))
        self.assertIsNone(state.battle_result)

        self.assertTrue(state.update_bot_manifest(1, {
            "round_id": state.round_id, "bots": _wire_manifest(state)}))

    def test_round_leave_keeps_socket_and_transfers_bot_authority(self):
        state = self._started_state(players=2)

        self.assertTrue(state.leave_battle(1, {
            "round_id": state.round_id}))

        self.assertTrue(state.players[1].connected)
        self.assertFalse(state.players[1].participating)
        self.assertFalse(state.players[1].alive)
        self.assertEqual(0, state.players[1].health)
        self.assertEqual(2, state.bot_authority_id)
        self.assertIsNone(state.battle_result)
        self.assertTrue(any(
            event.get("kind") == "authority" and
            event.get("player_id") == 2
            for event in state.pending_events))

    def test_last_round_participant_leave_finishes_draw_and_reset_revives(self):
        state = self._started_state(players=1)

        self.assertTrue(state.leave_battle(1, {
            "round_id": state.round_id}))

        self.assertIsNone(state.bot_authority_id)
        self.assertEqual(0, state.battle_result["winner"])
        self.assertEqual("all_players_left",
                         state.battle_result["reason"])
        state._reset_round()
        self.assertTrue(state.players[1].connected)
        self.assertTrue(state.players[1].participating)
        self.assertTrue(state.players[1].alive)
        self.assertEqual(state.players[1].max_health,
                         state.players[1].health)

    def test_disconnect_of_last_simulator_finishes_round_with_departed_waiter(self):
        state = self._started_state(players=2)
        self.assertTrue(state.leave_battle(1, {
            "round_id": state.round_id}))
        self.assertEqual(2, state.bot_authority_id)

        state.remove_player(2)

        self.assertIn(1, state.players)
        self.assertTrue(state.players[1].connected)
        self.assertFalse(state.players[1].participating)
        self.assertIsNone(state.bot_authority_id)
        self.assertEqual(0, state.battle_result["winner"])
        self.assertEqual("all_players_left",
                         state.battle_result["reason"])

    def test_stale_round_leave_cannot_retire_current_player(self):
        state = self._started_state(players=1)

        self.assertFalse(state.leave_battle(1, {
            "round_id": state.round_id - 1}))

        self.assertTrue(state.players[1].participating)
        self.assertTrue(state.players[1].alive)

    def test_malformed_combat_messages_do_not_mutate_round(self):
        state = self._started_state(players=2)
        state.players[1].fire_seq = 1
        before_health = state.players[2].health

        self.assertFalse(state.report_hit(1, {
            "target": 2, "shot_seq": 1}))
        self.assertFalse(state.report_hit(1, {
            "target": 2, "shot_seq": 1, "damage": "invalid"}))
        self.assertEqual(before_health, state.players[2].health)
        self.assertEqual(set(), state.players[1].reported_hits)
        self.assertFalse(state.update_bot_observation(1, {
            "contacts": {"not": "a list"}}))
        self.assertFalse(state.report_battle_result(1, {"winner": 1}))
        self.assertIsNone(state.battle_result)

    def test_human_fire_edges_must_be_contiguous(self):
        state = self._started_state(players=1)

        state.update_input(1, {"fire_seq": 3})
        self.assertEqual(0, state.players[1].fire_seq)
        self.assertEqual([], [value for value in state.pending_events
                              if value.get("kind") == "shot"])

        state.update_input(1, {"fire_seq": 1})
        self.assertEqual(1, state.players[1].fire_seq)
        self.assertEqual([1], [value["shot_seq"]
                               for value in state.pending_events
                               if value.get("kind") == "shot"])

    def test_bot_state_batch_is_complete_atomic_and_fire_edge_is_contiguous(self):
        state = self._started_state(players=1)
        self.assertTrue(state.update_bot_manifest(
            1, {"bots": _wire_manifest(state)}))
        states = _wire_states(state)
        first_id = states[0]["id"]
        original_x = state.bot_states[first_id]["x"]
        incomplete = [dict(value) for value in states[:-1]]
        incomplete[0]["x"] = 99.0

        self.assertFalse(state.update_bot_states(1, {"bots": incomplete}))
        self.assertEqual(original_x, state.bot_states[first_id]["x"])
        jumped = [dict(value) for value in states]
        jumped[0]["fire_seq"] = 2
        self.assertFalse(state.update_bot_states(1, {"bots": jumped}))
        self.assertEqual(0, state.bot_states[first_id]["fire_seq"])
        malformed = [dict(value) for value in states]
        malformed[0]["x"] = "nan"
        self.assertFalse(state.update_bot_states(1, {"bots": malformed}))
        inconsistent = [dict(value) for value in states]
        inconsistent[0]["alive"] = False
        self.assertFalse(state.update_bot_states(1, {"bots": inconsistent}))
        valid = [dict(value) for value in states]
        valid[0]["fire_seq"] = 1
        self.assertTrue(state.update_bot_states(1, {"bots": valid}))
        shots = [value for value in state.pending_events
                 if value.get("kind") == "bot_shot"]
        self.assertEqual([(first_id, 1)], [
            (value["attacker_bot"], value["shot_seq"])
            for value in shots])

    def test_authority_failover_preserves_bot_fire_sequence_without_replay(self):
        state = self._started_state(players=2)
        manifest = _wire_manifest(state)
        self.assertTrue(state.update_bot_manifest(1, {"bots": manifest}))
        states = _wire_states(state)
        states[0]["fire_seq"] = 1
        self.assertTrue(state.update_bot_states(1, {"bots": states}))
        state.pending_events = []

        state.remove_player(1)

        self.assertEqual(2, state.bot_authority_id)
        self.assertFalse(state.update_bot_states(1, {"bots": states}))
        self.assertFalse(state.update_bot_states(2, {"bots": states}))
        self.assertTrue(state.update_bot_manifest(2, {"bots": manifest}))
        resumed = _wire_states(state)
        resumed[0]["fire_seq"] = 2
        self.assertTrue(state.update_bot_states(2, {"bots": resumed}))
        shots = [value for value in state.pending_events
                 if value.get("kind") == "bot_shot"]
        self.assertEqual([2], [value["shot_seq"] for value in shots])

    def test_disconnect_of_last_live_team_member_finishes_round(self):
        state = self._started_state(players=2)
        self.assertTrue(state.update_bot_manifest(
            1, {"bots": _wire_manifest(state)}))
        for bot in state.bot_states.values():
            if bot["team"] == 1:
                bot["health"] = 0
                bot["alive"] = False

        state.remove_player(1)

        self.assertEqual(2, state.bot_authority_id)
        self.assertEqual(2, state.battle_result["winner"])
        self.assertEqual("team_eliminated", state.battle_result["reason"])

    def test_start_fills_only_unoccupied_team_slots_and_rejects_late_join(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.players[1] = Player(
            1, object(), ("127.0.0.1", 0), team=1, slot=0)
        state.players[2] = Player(
            2, object(), ("127.0.0.1", 0), team=2, slot=0)

        started, error = state.request_start(1)

        self.assertIsNone(error)
        self.assertEqual(28, len(started["bots"]))
        slots = [(value["team"], value["slot"])
                 for value in started["players"] + started["bots"]]
        self.assertEqual(30, len(slots))
        self.assertEqual(30, len(set(slots)))
        late, join_error = state.add_player(
            object(), ("127.0.0.1", 1), {"name": "Late"})
        self.assertIsNone(late)
        self.assertEqual("battle_in_progress", join_error)

    def test_elimination_waits_for_expected_bot_manifest(self):
        state = BattleState(map_name="04_himmelsdorf")
        player = Player(1, object(), ("127.0.0.1", 0), team=1, slot=0)
        state.players[1] = player
        state.request_start(1)

        state.update_input(1, {"reported_health": 0})

        self.assertFalse(player.alive)
        self.assertIsNone(state.battle_result)

    def test_terminal_round_resets_connected_players_and_broadcasts_waiting(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        connection = CaptureConnection()
        state = BattleState(map_name="04_himmelsdorf")
        player = Player(
            1, connection, ("127.0.0.1", 0), team=1, slot=0,
            health=100, max_health=500, alive=True)
        player.fire_seq = 9
        player.client_position = True
        state.players[1] = player
        state.phase = "battle"
        self.assertTrue(state._finish_battle(1, "team_eliminated"))
        state.result_reset_tick = state.tick + 1

        state.tick_once(1.0 / 30.0)

        self.assertEqual("waiting", state.phase)
        self.assertEqual(2, state.round_id)
        self.assertEqual(500, player.health)
        self.assertTrue(player.alive)
        self.assertEqual(0, player.fire_seq)
        self.assertFalse(player.client_position)
        self.assertEqual([], state.bot_manifest)
        self.assertEqual({}, state.bot_states)
        self.assertEqual("roster", connection.messages[-1]["type"])
        self.assertEqual("waiting", connection.messages[-1]["phase"])

        state.update_input(1, {"forward": 1.0, "x": 999, "z": 999})
        self.assertEqual(0.0, player.forward)
        self.assertFalse(player.client_position)

    def test_bot_human_hit_requires_an_observed_bot_fire_sequence(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.players[1] = Player(1, object(), ("127.0.0.1", 0), team=1)
        state.players[2] = Player(2, object(), ("127.0.0.1", 0), team=2)
        state.bot_states[11] = {"id": 11, "team": 1, "alive": True,
                                "health": 1000, "fire_seq": 3,
                                "x": 0.0, "y": 0.0, "z": 0.0}

        self.assertFalse(state.report_bot_human_hit(1, {
            "attacker_bot": 11, "target": 2, "shot_seq": 4,
            "damage": 100, "shot_result": 2}))
        self.assertEqual(1000, state.players[2].health)
        self.assertTrue(state.report_bot_human_hit(1, {
            "attacker_bot": 11, "target": 2, "shot_seq": 3,
            "damage": 100, "shot_result": 2}))
        self.assertEqual(900, state.players[2].health)

    def test_authority_bot_can_damage_enemy_bot_once_per_fire_sequence(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.players[1] = Player(1, object(), ("127.0.0.1", 0), team=1)
        state.bot_states[11] = {
            "id": 11, "team": 1, "alive": True, "health": 1000,
            "fire_seq": 2, "shell_index": 1, "x": 0.0, "y": 0.0, "z": 0.0}
        state.bot_states[12] = {
            "id": 12, "team": 2, "alive": True, "health": 1000,
            "fire_seq": 0, "shell_index": 0, "x": 0.0, "y": 0.0, "z": 20.0}
        report = {"attacker_bot": 11, "target": 12, "shot_seq": 2,
                  "damage": 175, "shot_result": 2}

        self.assertTrue(state.report_bot_hit(1, report))
        self.assertEqual(825, state.bot_states[12]["health"])
        self.assertFalse(state.report_bot_hit(1, report))
        self.assertEqual("bot_bot_hit", state.pending_events[-1]["kind"])

    def test_one_human_shell_cannot_damage_bot_and_human_targets(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.players[1] = Player(
            1, object(), ("127.0.0.1", 0), team=1, fire_seq=1)
        state.players[2] = Player(
            2, object(), ("127.0.0.1", 0), team=2)
        state.bot_states[12] = {
            "id": 12, "team": 2, "alive": True, "health": 500,
            "x": 0.0, "y": 0.0, "z": 20.0}

        self.assertTrue(state.report_bot_hit(1, {
            "target": 12, "shot_seq": 1, "damage": 100}))
        self.assertFalse(state.report_hit(1, {
            "target": 2, "shot_seq": 1, "damage": 100}))
        self.assertEqual(1000, state.players[2].health)

    def test_one_bot_shell_cannot_damage_bot_and_human_targets(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.players[1] = Player(
            1, object(), ("127.0.0.1", 0), team=1)
        state.players[2] = Player(
            2, object(), ("127.0.0.1", 0), team=2)
        state.bot_states[11] = {
            "id": 11, "team": 1, "alive": True, "health": 500,
            "fire_seq": 1, "shell_index": 0,
            "x": 0.0, "y": 0.0, "z": 0.0}
        state.bot_states[12] = {
            "id": 12, "team": 2, "alive": True, "health": 500,
            "fire_seq": 0, "shell_index": 0,
            "x": 0.0, "y": 0.0, "z": 20.0}

        self.assertTrue(state.report_bot_hit(1, {
            "attacker_bot": 11, "target": 12,
            "shot_seq": 1, "damage": 100}))
        self.assertFalse(state.report_bot_human_hit(1, {
            "attacker_bot": 11, "target": 2,
            "shot_seq": 1, "damage": 100}))
        self.assertEqual(1000, state.players[2].health)

    def test_last_kill_finishes_standard_battle_exactly_once(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.players[1] = Player(
            1, object(), ("127.0.0.1", 0), team=1)
        state.bot_manifest = [
            {"id": 11, "team": 1, "slot": 0, "name": "Ally",
             "vehicle": "ussr:R11_MS-1", "max_health": 1000},
            {"id": 12, "team": 2, "slot": 0, "name": "Enemy",
             "vehicle": "ussr:R11_MS-1", "max_health": 100},
        ]
        state.bot_roster = list(state.bot_manifest)
        state.bot_states[11] = {
            "id": 11, "team": 1, "alive": True, "health": 1000,
            "fire_seq": 1, "shell_index": 0,
            "x": 0.0, "y": 0.0, "z": 0.0}
        state.bot_states[12] = {
            "id": 12, "team": 2, "alive": True, "health": 100,
            "fire_seq": 0, "shell_index": 0,
            "x": 0.0, "y": 0.0, "z": 20.0}
        report = {"attacker_bot": 11, "target": 12, "shot_seq": 1,
                  "damage": 100, "shot_result": 2}

        self.assertTrue(state.report_bot_hit(1, report))
        self.assertEqual({"winner": 1, "reason": "team_eliminated",
                          "base_team": 0}, state.battle_result)
        self.assertEqual(1, sum(
            event.get("kind") == "battle_result"
            for event in state.pending_events))
        self.assertFalse(state.report_bot_hit(1, dict(report, shot_seq=2)))
        self.assertFalse(state.report_battle_result(1, {
            "winner": 1, "reason": "duplicate"}))
        self.assertEqual(1, sum(
            event.get("kind") == "battle_result"
            for event in state.pending_events))

    def test_bot_state_fire_edge_creates_visual_shot_event(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.bot_manifest = [{
            "id": 11, "team": 1, "slot": 0, "name": "Bot",
            "vehicle": "ussr:R11_MS-1", "max_health": 1000}]
        state.bot_states[11] = {
            "id": 11, "team": 1, "slot": 0, "name": "Bot",
            "vehicle": "ussr:R11_MS-1", "health": 1000,
            "max_health": 1000, "alive": True, "fire_seq": 0,
            "shell_index": 0, "x": 0.0, "y": 0.0, "z": 0.0,
            "yaw": 0.0, "aim_yaw": 0.0, "gun_pitch": 0.0}

        self.assertTrue(state.update_bot_states(1, {"bots": [{
            "id": 11, "x": 0, "y": 0, "z": 1, "yaw": 0,
            "health": 1000, "alive": True, "fire_seq": 1}]}))
        self.assertEqual("bot_shot", state.pending_events[-1]["kind"])

    def test_dead_bot_cannot_emit_late_visual_shot_edge(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.bot_manifest = [{
            "id": 11, "team": 1, "slot": 0, "name": "Bot",
            "vehicle": "ussr:R11_MS-1", "max_health": 1000}]
        state.bot_states[11] = {
            "id": 11, "team": 1, "slot": 0, "name": "Bot",
            "vehicle": "ussr:R11_MS-1", "health": 0,
            "max_health": 1000, "alive": False, "fire_seq": 1,
            "shell_index": 0, "x": 0.0, "y": 0.0, "z": 0.0,
            "yaw": 0.0, "aim_yaw": 0.0, "gun_pitch": 0.0}

        self.assertTrue(state.update_bot_states(1, {"bots": [{
            "id": 11, "x": 0, "y": 0, "z": 1,
            "yaw": 0, "alive": True, "health": 1000,
            "fire_seq": 2}]}))

        self.assertFalse(state.bot_states[11]["alive"])
        self.assertEqual([], [event for event in state.pending_events
                              if event.get("kind") == "bot_shot"])

    def test_orders_are_stable_then_revision_only_changes_for_new_information(self):
        planner = BotPlanner()
        first = planner.build_orders(_manifest(), _states(), [], 0.0)
        second = planner.build_orders(_manifest(), _states(), [], 0.1)
        self.assertEqual(1, first["revision"])
        self.assertEqual(first, second)
        self.assertEqual({"advance"}, {order["combat_mode"] for order in first["orders"]})

    def test_unchanged_orders_are_omitted_from_following_snapshots(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        connection = CaptureConnection()
        state.players[1] = Player(1, connection, ("127.0.0.1", 0))

        state.tick_once(0.05)
        state.tick_once(0.05)

        self.assertIn("bot_orders", connection.messages[0])
        self.assertEqual(state.round_id, connection.messages[0]["round_id"])
        self.assertEqual(PROTOCOL_VERSION,
                         connection.messages[0]["protocol"])
        self.assertNotIn("bot_orders", connection.messages[1])

    def test_only_reported_enemy_contact_becomes_shared_target(self):
        planner = BotPlanner()
        players = [{"id": 99, "team": 2, "alive": True, "x": 999, "z": 999}]
        planner.build_orders(_manifest(), _states(), players, 0.0)
        accepted = planner.report_contacts([
            {"observing_team": 1, "target_id": 99, "visible": True,
             "x": 40, "y": 0, "z": 30, "health": 200, "max_health": 1000},
        ], planner.known_targets(_states(), players), 1.0)
        self.assertEqual(1, accepted)
        result = planner.build_orders(_manifest(), _states(), players, 1.0)
        team_one = [order for order in result["orders"] if order["team"] == 1]
        self.assertEqual(99, team_one[0]["target_id"])
        self.assertEqual({"x": 40.0, "y": 0.0, "z": 30.0}, team_one[0]["aim_position"])
        self.assertTrue(team_one[0]["fire_allowed"])

    def test_unreported_player_and_stale_contact_do_not_leak_target(self):
        planner = BotPlanner()
        players = [{"id": 99, "team": 2, "alive": True, "x": 999, "z": 999}]
        hidden = planner.build_orders(_manifest(), _states(), players, 0.0)
        self.assertIsNone(hidden["orders"][0]["target_id"])
        planner.report_contacts([{"observing_team": 1, "target_id": 99, "x": 1, "z": 2}],
                                planner.known_targets(_states(), players), 0.0)
        stale = planner.build_orders(_manifest(), _states(), players, 8.1)
        self.assertIsNone([order for order in stale["orders"] if order["team"] == 1][0]["target_id"])

    def test_engage_order_revision_does_not_change_with_every_pose_tick(self):
        planner = BotPlanner()
        manifest = _manifest()
        manifest[0]["profile"] = {
            "dominant_role": "support", "desired_range": 40.0,
        }
        players = [{"id": 99, "team": 2, "alive": True}]
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True, "x": 0, "y": 0, "z": 20,
            "health": 800, "max_health": 800,
        }], planner.known_targets(_states(), players), 1.0)

        first = planner.build_orders(manifest, _states(), players, 1.0)
        moved = _states()
        moved[0] = dict(moved[0], x=3.0, z=-18.0)
        second = planner.build_orders(manifest, moved, players, 1.1)

        first_order = [order for order in first["orders"] if order["id"] == 1][0]
        second_order = [order for order in second["orders"] if order["id"] == 1][0]
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(first_order["move_position"], second_order["move_position"])

    def test_human_and_bot_with_same_numeric_id_are_distinct_contacts(self):
        planner = BotPlanner()
        states = _states() + [{"id": 99, "team": 2, "alive": True, "x": 50, "z": 50}]
        players = [{"id": 99, "team": 2, "alive": True}]
        known = planner.known_targets(states, players)

        accepted = planner.report_contacts([
            {"observing_team": 1, "target_kind": "human", "target_id": 99,
             "target_team": 2, "x": 10, "z": 10},
            {"observing_team": 1, "target_kind": "bot", "target_id": 99,
             "target_team": 2, "x": 100, "z": 100},
        ], known, 1.0)

        self.assertEqual(2, accepted)
        contacts = planner._prune_contacts(known, 1.0)[1]
        self.assertEqual({"human", "bot"}, {value["target_kind"] for value in contacts})

    def test_uploaded_route_advances_after_reaching_waypoint(self):
        planner = BotPlanner()
        manifest = _manifest()
        manifest[0]["route"] = {
            "id": "heavy_city",
            "waypoints": [
                {"x": 0, "y": 0, "z": -20},
                {"x": 60, "y": 0, "z": -20},
            ],
        }

        result = planner.build_orders(manifest, _states(), [], 0.0)
        order = [value for value in result["orders"] if value["id"] == 1][0]

        self.assertEqual("heavy_city", order["route_id"])
        self.assertEqual(1, order["route_index"])
        self.assertEqual({"x": 60.0, "y": 0.0, "z": -20.0}, order["move_position"])

    def test_client_probed_cover_drives_a_hold_peek_return_cycle(self):
        planner = BotPlanner()
        manifest = _manifest()
        players = [{"id": 99, "team": 2, "alive": True}]
        known_targets = planner.known_targets(_states(), players)
        planner.report_contacts([{
            "observing_team": 1,
            "target_kind": "human",
            "target_id": 99,
            "target_team": 2,
            "visible": True,
            "x": 0.0,
            "y": 0.0,
            "z": 20.0,
            "health": 1000,
            "max_health": 1000,
        }], known_targets, 1.0)
        accepted = planner.report_affordances([{
            "bot_id": 1,
            "target_kind": "human",
            "target_id": 99,
            "candidates": [_cover_candidate()],
        }], planner.known_bots(manifest, _states()), known_targets, 1.0)
        self.assertEqual(1, accepted)

        approach = planner.build_orders(manifest, _states(), players, 1.0)
        approach_order = next(value for value in approach["orders"] if value["id"] == 1)
        self.assertEqual("take_cover", approach_order["combat_mode"])
        self.assertFalse(approach_order["fire_allowed"])

        at_cover = _states()
        at_cover[0] = dict(at_cover[0], x=-8.0, z=-12.0)
        hold = planner.build_orders(manifest, at_cover, players, 1.1)
        hold_order = next(value for value in hold["orders"] if value["id"] == 1)
        self.assertEqual("cover_hold", hold_order["combat_mode"])

        peek = planner.build_orders(manifest, at_cover, players, 3.0)
        peek_order = next(value for value in peek["orders"] if value["id"] == 1)
        self.assertEqual("cover_peek", peek_order["combat_mode"])
        self.assertFalse(peek_order["fire_allowed"])

        at_peek = _states()
        at_peek[0] = dict(at_peek[0], x=-2.0, z=-10.0)
        firing = planner.build_orders(manifest, at_peek, players, 3.1)
        firing_order = next(value for value in firing["orders"] if value["id"] == 1)
        self.assertEqual("cover_peek", firing_order["combat_mode"])
        self.assertTrue(firing_order["fire_allowed"])

        returning = planner.build_orders(manifest, at_peek, players, 5.5)
        returning_order = next(value for value in returning["orders"] if value["id"] == 1)
        self.assertEqual("cover_return", returning_order["combat_mode"])
        self.assertFalse(returning_order["fire_allowed"])

    def test_cover_report_requires_an_existing_team_contact(self):
        planner = BotPlanner()
        players = [{"id": 99, "team": 2, "alive": True}]
        accepted = planner.report_affordances([{
            "bot_id": 1,
            "target_kind": "human",
            "target_id": 99,
            "candidates": [_cover_candidate()],
        }], planner.known_bots(_manifest(), _states()),
            planner.known_targets(_states(), players), 1.0)

        self.assertEqual(0, accepted)
        self.assertEqual({}, planner._affordances)

    def test_malformed_observation_payloads_are_ignored(self):
        planner = BotPlanner()
        players = [{"id": 99, "team": 2, "alive": True}]
        known_targets = planner.known_targets(_states(), players)
        known_bots = planner.known_bots(_manifest(), _states())

        self.assertEqual(0, planner.report_contacts({"not": "a list"}, known_targets, 1.0))
        self.assertEqual(0, planner.report_contacts(["not a mapping"], known_targets, 1.0))
        self.assertEqual(0, planner.report_affordances({"not": "a list"}, known_bots, known_targets, 1.0))
        self.assertEqual(0, planner.report_affordances([{
            "bot_id": 1, "target_kind": "human", "target_id": 99,
            "candidates": {"not": "a list"},
        }], known_bots, known_targets, 1.0))

    def test_non_mapping_wire_message_does_not_abort_following_ping(self):
        class FakeConnection(object):
            def __init__(self):
                self.chunks = [
                    (json.dumps({
                        "type": "hello", "protocol": PROTOCOL_VERSION,
                        "client_build": CLIENT_BUILD_0922,
                        "name": "Alpha", "vehicle": "ussr:T-34",
                    }) + "\n").encode("utf-8"),
                    b"[]\n",
                    b'{"type":"ping","seq":9,"client_time":1.0}\n',
                    b'{"type":"leave"}\n',
                ]
                self.messages = []

            def setsockopt(self, *unused):
                pass

            def settimeout(self, *unused):
                pass

            def recv(self, unused_size):
                return self.chunks.pop(0) if self.chunks else b""

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

            def close(self):
                pass

        connection = FakeConnection()
        state = BattleState(map_name="04_himmelsdorf")
        server = type("FakeServer", (), {
            "game_server": type("GameServer", (), {"state": state})(),
        })()

        ClientHandler(connection, ("127.0.0.1", 12345), server)

        pong = [message for message in connection.messages
                if message.get("type") == "pong"]
        self.assertEqual(9, pong[0]["seq"])

    def test_new_membership_cannot_start_before_its_welcome_is_sent(self):
        state = BattleState(map_name="04_himmelsdorf")
        attempted = threading.Event()
        completed = threading.Event()
        request_thread = []
        blocked_during_welcome = []
        testcase = self

        class FakeConnection(object):
            def __init__(self):
                self.chunks = [
                    (json.dumps({
                        "type": "hello", "protocol": PROTOCOL_VERSION,
                        "client_build": CLIENT_BUILD_0922,
                        "name": "Alpha", "vehicle": "ussr:T-34",
                    }) + "\n").encode("utf-8"),
                    b'{"type":"leave"}\n',
                ]
                self.messages = []

            def setsockopt(self, *unused):
                pass

            def settimeout(self, *unused):
                pass

            def recv(self, unused_size):
                return self.chunks.pop(0) if self.chunks else b""

            def sendall(self, payload):
                message = json.loads(payload.decode("utf-8"))
                self.messages.append(message)
                if message.get("type") != "welcome":
                    return

                def request_start():
                    attempted.set()
                    state.request_start(1)
                    completed.set()

                thread = threading.Thread(target=request_start)
                request_thread.append(thread)
                thread.start()
                testcase.assertTrue(attempted.wait(1.0))
                time.sleep(0.01)
                blocked_during_welcome.append(not completed.is_set())

            def close(self):
                pass

        connection = FakeConnection()
        server = type("FakeServer", (), {
            "game_server": type("GameServer", (), {"state": state})(),
        })()

        ClientHandler(connection, ("127.0.0.1", 12345), server)
        request_thread[0].join(timeout=1.0)

        self.assertEqual([True], blocked_during_welcome)
        self.assertTrue(completed.is_set())
        self.assertEqual("welcome", connection.messages[0]["type"])

    def test_malformed_bot_collections_are_rejected_without_state_change(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1

        self.assertFalse(state.update_bot_manifest(1, {"bots": {"id": 1}}))
        self.assertFalse(state.update_bot_states(1, {"bots": {"id": 1}}))
        self.assertEqual([], state.bot_manifest)

    def test_cover_requires_live_contact_and_nearby_server_pose(self):
        planner = BotPlanner()
        players = [{"id": 99, "team": 2, "alive": True}]
        known_targets = planner.known_targets(_states(), players)
        known_bots = planner.known_bots(_manifest(), _states())
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True, "x": 0, "z": 20,
        }], known_targets, 1.0)

        far = _cover_candidate(x=800.0, z=800.0)
        self.assertEqual(0, planner.report_affordances([{
            "bot_id": 1, "target_kind": "human", "target_id": 99,
            "candidates": [far],
        }], known_bots, known_targets, 1.0))
        self.assertEqual(0, planner.report_affordances([{
            "bot_id": 1, "target_kind": "human", "target_id": 99,
            "candidates": [_cover_candidate()],
        }], known_bots, known_targets, 9.1))

    def test_replacing_affordance_replaces_active_cover_candidate(self):
        planner = BotPlanner()
        manifest = _manifest()
        players = [{"id": 99, "team": 2, "alive": True}]
        known_targets = planner.known_targets(_states(), players)
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True, "x": 0, "z": 20,
        }], known_targets, 1.0)
        known_bots = planner.known_bots(manifest, _states())
        planner.report_affordances([{
            "bot_id": 1, "target_kind": "human", "target_id": 99,
            "candidates": [_cover_candidate("old", -8.0, -12.0)],
        }], known_bots, known_targets, 1.0)
        first = planner.build_orders(manifest, _states(), players, 1.0)
        self.assertEqual("old", first["orders"][0]["cover_id"])

        planner.report_affordances([{
            "bot_id": 1, "target_kind": "human", "target_id": 99,
            "candidates": [_cover_candidate("new", 8.0, -12.0)],
        }], known_bots, known_targets, 1.1)
        second = planner.build_orders(manifest, _states(), players, 1.1)

        self.assertEqual("new", second["orders"][0]["cover_id"])
        self.assertEqual("take_cover", second["orders"][0]["combat_mode"])

    def test_tactical_state_is_pruned_for_dead_or_removed_bots(self):
        planner = BotPlanner()
        players = [{"id": 99, "team": 2, "alive": True}]
        known_targets = planner.known_targets(_states(), players)
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True, "x": 0, "z": 20,
        }], known_targets, 1.0)
        planner.report_affordances([{
            "bot_id": 1, "target_kind": "human", "target_id": 99,
            "candidates": [_cover_candidate()],
        }], planner.known_bots(_manifest(), _states()), known_targets, 1.0)
        planner._cover_states[1] = {"target": ("human", 99)}
        planner._route_states[1] = {"route_id": "left"}
        planner._route_assignments[1] = {"route": {"id": "left"}, "until": 0.0}
        planner._engage_anchors[1] = {"x": 0}

        dead = _states()
        dead[0] = dict(dead[0], alive=False)
        planner.build_orders(_manifest(), dead, players, 1.1)

        self.assertNotIn(1, planner._affordances)
        self.assertNotIn(1, planner._cover_states)
        self.assertNotIn(1, planner._route_states)
        self.assertNotIn(1, planner._route_assignments)
        self.assertNotIn(1, planner._engage_anchors)

    def test_team_order_is_stable_across_manifest_permutation(self):
        planner = BotPlanner()
        manifest = _manifest()
        players = [{"id": 99, "team": 2, "alive": True}]
        known_targets = planner.known_targets(_states(), players)
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True, "x": 0, "z": 20,
        }], known_targets, 1.0)
        reports = [{
            "bot_id": bot_id, "target_kind": "human", "target_id": 99,
            "candidates": [_cover_candidate("shared", -8.0, -12.0),
                           _cover_candidate("spare", 14.0, -12.0)],
        } for bot_id in (1, 2)]
        planner.report_affordances(reports, planner.known_bots(manifest, _states()),
                                   known_targets, 1.0)
        first = planner.build_orders(manifest, _states(), players, 1.0)
        planner.reset()
        known_targets = planner.known_targets(_states(), players)
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True, "x": 0, "z": 20,
        }], known_targets, 1.0)
        planner.report_affordances(reports, planner.known_bots(manifest, _states()),
                                   known_targets, 1.0)
        second = planner.build_orders([manifest[1], manifest[0], manifest[2]],
                                      _states(), players, 1.0)

        self.assertEqual(first, second)

    def test_conflicting_duplicate_route_ids_resolve_by_sorted_bot_id(self):
        planner = BotPlanner()
        left = {"id": "shared", "waypoints": [{"x": -50, "y": 0, "z": 0}]}
        right = {"id": "shared", "waypoints": [{"x": 50, "y": 0, "z": 0}]}
        manifest = [
            {"id": 2, "team": 1, "slot": 1, "health": 1000, "route": right},
            {"id": 1, "team": 1, "slot": 0, "health": 1000, "route": left},
        ]
        states = [
            {"id": 1, "team": 1, "alive": True, "x": 0, "z": 0},
            {"id": 2, "team": 1, "alive": True, "x": 0, "z": 0},
        ]

        first = planner.build_orders(manifest, states, [], 1.0)
        planner.reset()
        second = planner.build_orders(list(reversed(manifest)), states, [], 1.0)

        self.assertEqual(first, second)
        self.assertEqual(
            {-50.0}, {order["move_position"]["x"] for order in first["orders"]}
        )

    def test_team_director_reassigns_one_mobile_bot_to_a_pressured_route(self):
        planner = BotPlanner()
        support = {
            "dominant_role": "support",
            "roles": {"support": 0.9, "flanker": 0.7, "brawler": 0.2},
            "desired_range": 80.0,
            "fire_range": 300.0,
        }
        left = {
            "id": "left",
            "waypoints": [
                {"x": -80, "y": 0, "z": -20},
                {"x": -80, "y": 0, "z": 80},
            ],
        }
        right = {
            "id": "right",
            "waypoints": [
                {"x": 80, "y": 0, "z": -20},
                {"x": 80, "y": 0, "z": 80},
            ],
        }
        manifest = [
            {"id": 1, "team": 1, "slot": 0, "health": 1000,
             "profile": support, "route": left},
            {"id": 2, "team": 1, "slot": 1, "health": 1000,
             "profile": support, "route": left},
            {"id": 4, "team": 1, "slot": 2, "health": 1000,
             "profile": support, "route": right},
            {"id": 3, "team": 2, "slot": 0, "health": 1000},
        ]
        states = [
            {"id": 1, "team": 1, "alive": True, "x": -80, "z": -20},
            {"id": 2, "team": 1, "alive": True, "x": -70, "z": -20},
            {"id": 4, "team": 1, "alive": True, "x": 80, "z": -20},
            {"id": 3, "team": 2, "alive": True, "x": 80, "z": 70},
        ]
        players = [{"id": 99, "team": 2, "alive": True}]
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True, "x": 80, "y": 0, "z": 70,
            "health": 1000, "max_health": 1000,
        }], planner.known_targets(states, players), 1.0)

        result = planner.build_orders(manifest, states, players, 1.0)
        team_one = [order for order in result["orders"] if order["team"] == 1]

        self.assertGreaterEqual(
            sum(order["route_id"] == "right" for order in team_one), 2
        )

        changed_manifest = list(manifest)
        changed_manifest[2] = dict(changed_manifest[2], route=left)
        recovered = planner.build_orders(changed_manifest, states, players, 2.0)
        recovered_one = next(value for value in recovered["orders"] if value["id"] == 1)
        self.assertEqual("left", recovered_one["route_id"])

if __name__ == "__main__":
    unittest.main()
