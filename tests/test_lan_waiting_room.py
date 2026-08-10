import json
import socket
import threading
import time
import unittest

from lan_battle_server import (
    BattleState, CLIENT_BUILD, ClientHandler, PROTOCOL_VERSION, TICK_HZ,
    ThreadedTCPServer,
)


class WireClient:
    def __init__(self, port, name, vehicle="ussr:T-34", max_health=880,
                 client_build=CLIENT_BUILD):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.sock.settimeout(2)
        self.buffer = b""
        self.send({
            "type": "hello",
            "protocol": PROTOCOL_VERSION,
            "client_build": client_build,
            "name": name,
            "vehicle": vehicle,
            "max_health": max_health,
        })

    def send(self, message):
        payload = (json.dumps(message) + "\n").encode("utf-8")
        self.sock.sendall(payload)

    def receive_type(self, wanted):
        deadline = time.time() + 3
        while time.time() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line:
                    continue
                message = json.loads(line.decode("utf-8"))
                if message.get("type") == wanted:
                    return message
            self.buffer += self.sock.recv(8192)
        raise AssertionError("did not receive %s" % wanted)

    def close(self):
        self.sock.close()


class WaitingRoomTest(unittest.TestCase):
    def setUp(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        self.state = state
        # Most integration assertions use a stable team order. A separate test
        # below exercises the randomized first-player side explicitly.
        self.state.next_balanced_team = 1
        self.server = ThreadedTCPServer(("127.0.0.1", 0), ClientHandler)
        self.server.game_server = type("GameServer", (), {"state": state})()
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.port = self.server.server_address[1]
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_server_rejects_a_stale_client_build_before_join(self):
        stale = WireClient(
            self.server.server_address[1], "OldClient",
            client_build="1.8.16-test-20260809",
        )
        try:
            error = stale.receive_type("error")
        finally:
            stale.close()

        self.assertEqual("build", error["code"])
        self.assertIn(CLIENT_BUILD, error["message"])
        self.assertEqual({}, self.state.players)

    def connect(self, name):
        client = WireClient(self.port, name)
        self.clients.append(client)
        return client

    def test_server_snapshots_run_at_thirty_hz(self):
        self.assertEqual(30.0, TICK_HZ)

    def test_one_player_can_start_from_clickable_panel_request(self):
        client = self.connect("Solo")
        welcome = client.receive_type("welcome")
        client.receive_type("roster")

        client.send({"type": "start_battle"})
        started = client.receive_type("battle_start")

        self.assertEqual("04_himmelsdorf", welcome["map"])
        self.assertEqual(welcome["map"], started["map"])
        self.assertEqual(1, len(started["players"]))
        self.assertEqual("prebattle", started["timing"]["phase"])
        self.assertGreaterEqual(started["timing"]["start_in_ms"], 29000)
        self.assertEqual(900000, started["timing"]["duration_ms"])

        self.state.tick_once(1.0 / TICK_HZ)
        snapshot = client.receive_type("snapshot")
        self.assertEqual("prebattle", snapshot["timing"]["phase"])
        self.assertLessEqual(
            snapshot["timing"]["start_in_ms"], started["timing"]["start_in_ms"]
        )

    def test_first_player_side_can_start_on_team_two_and_remains_balanced(self):
        self.state.next_balanced_team = 2
        first = self.connect("NorthOrSouth")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("BalancedPeer")
        second_welcome = second.receive_type("welcome")

        self.assertEqual((2, 0), (first_welcome["team"], first_welcome["slot"]))
        self.assertEqual((1, 0), (second_welcome["team"], second_welcome["slot"]))

    def test_start_request_selects_and_validates_the_map(self):
        client = self.connect("MapPicker")
        client.receive_type("welcome")
        roster = client.receive_type("roster")

        self.assertIn("31_airfield", roster["map_pool"])
        client.send({"type": "start_battle", "map": "31_airfield"})
        started = client.receive_type("battle_start")

        self.assertEqual("31_airfield", started["map"])

    def test_invalid_map_is_rejected_without_starting(self):
        client = self.connect("MapPicker")
        client.receive_type("welcome")
        client.receive_type("roster")

        client.send({"type": "start_battle", "map": "not_a_real_map"})
        denied = client.receive_type("start_denied")

        self.assertEqual("invalid_map", denied["code"])
        self.assertEqual("waiting", self.state.phase)

    def test_start_is_broadcast_to_every_waiting_player(self):
        first = self.connect("Alpha")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second_welcome = second.receive_type("welcome")
        first_roster = first.receive_type("roster")
        second_roster = second.receive_type("roster")

        second.send({"type": "start_battle"})
        first_start = first.receive_type("battle_start")
        second_start = second.receive_type("battle_start")

        self.assertEqual(2, len(first_roster["players"]))
        self.assertEqual(2, len(second_roster["players"]))
        self.assertEqual(first_welcome["map"], second_welcome["map"])
        self.assertEqual(first_welcome["map"], first_start["map"])
        self.assertEqual(first_start["map"], second_start["map"])
        self.assertEqual(2, len(first_start["players"]))
        self.assertEqual(first_start["bots"], second_start["bots"])
        self.assertEqual(30, len(first_start["bots"]))
        self.assertEqual(30, len({bot["name"] for bot in first_start["bots"]}))
        self.assertEqual(first_welcome["player_id"], first_start["bot_authority_id"])

    def test_authority_owns_bot_rules_and_result_state(self):
        first = self.connect("Alpha")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")
        first.send({"type": "start_battle"})
        first.receive_type("battle_start")
        second.receive_type("battle_start")
        bot = {
            "id": 16, "team": 2, "slot": 0, "name": "ignored",
            "vehicle": "germany:PzIV", "max_health": 500, "health": 500,
            "x": 0.0, "y": 0.0, "z": 100.0, "yaw": 3.14,
        }

        second.send({"type": "bot_manifest", "bots": [bot]})
        first.send({"type": "bot_manifest", "bots": [bot]})
        time.sleep(0.05)
        self.assertEqual(1, len(self.state.bot_manifest))
        first.send({"type": "bot_state", "bots": [dict(
            bot, x=4.0, z=92.0, aim_yaw=3.0, gun_pitch=-0.1,
            fire_seq=2, shell_index=1,
        )]})
        first.send({"type": "input", "fire_seq": 1, "shell_index": 0})
        first.send({
            "type": "bot_hit_report", "target": 16, "shot_seq": 1,
            "damage": 125, "shot_result": 2, "x": 4.0, "y": 1.0, "z": 92.0,
        })
        bot_human_hit = {
            "type": "bot_human_hit", "attacker_bot": 16,
            "target": first_welcome["player_id"], "shot_seq": 2,
            "damage": 50, "shot_result": 2,
        }
        first.send(bot_human_hit)
        first.send(bot_human_hit)
        second.send({
            "type": "rules_state",
            "rules": {"bases": {"1": {"points": 99}, "2": {"points": 99}}},
        })
        first.send({
            "type": "rules_state",
            "rules": {"bases": {"1": {"points": 42}, "2": {"points": 0}}},
        })
        second.send({"type": "battle_result", "winner": 1, "reason": "invalid"})
        first.send({
            "type": "battle_result", "winner": 2,
            "reason": "base captured", "base_team": 1,
        })
        time.sleep(0.05)
        self.state.tick_once(0.05)

        snapshot = second.receive_type("snapshot")
        shared_bot = snapshot["bots"][0]
        self.assertEqual(first_welcome["player_id"], snapshot["bot_authority_id"])
        self.assertGreater(snapshot["bot_state_revision"], 0)
        self.assertEqual("germany:PzIV", shared_bot["vehicle"])
        self.assertEqual(4.0, shared_bot["x"])
        self.assertEqual(375, shared_bot["health"])
        authority_player = next(
            player for player in snapshot["players"]
            if player["id"] == first_welcome["player_id"]
        )
        self.assertEqual(830, authority_player["health"])
        self.assertEqual(42, snapshot["rules"]["bases"]["1"]["points"])
        self.assertEqual(2, snapshot["battle_result"]["winner"])
        self.assertEqual(1, snapshot["battle_result"]["base_team"])

    def test_authority_observation_produces_non_omniscient_server_bot_order(self):
        first = self.connect("Alpha")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")
        first.send({"type": "start_battle"})
        first.receive_type("battle_start")
        second.receive_type("battle_start")
        bot = {"id": 16, "team": 2, "slot": 0, "vehicle": "germany:PzIV",
               "max_health": 500, "health": 500, "x": 0.0, "z": 100.0,
               "profile": {"class_tag": "mediumTank", "dominant_role": "support",
                           "roles": ["support"], "desired_range": 220, "fire_range": 600}}
        first.send({"type": "bot_manifest", "bots": [bot]})
        first.send({"type": "bot_state", "bots": [bot]})
        first.send({"type": "bot_observation", "contacts": [{
            "observing_team": 2, "target_id": first_welcome["player_id"], "target_team": 1,
            "visible": True, "x": 17.0, "y": 2.0, "z": -31.0,
            "health": 880, "max_health": 880, "class_tag": "mediumTank",
        }]})
        time.sleep(0.05)
        self.state.tick_once(0.05)
        snapshot = second.receive_type("snapshot")
        self.assertGreater(snapshot["bot_order_revision"], 0)
        order = snapshot["bot_orders"][0]
        self.assertEqual(16, order["id"])
        self.assertEqual(first_welcome["player_id"], order["target_id"])
        self.assertEqual({"x": 17.0, "y": 2.0, "z": -31.0}, order["aim_position"])
        self.assertTrue(order["fire_allowed"])

    def test_ping_echo_and_authority_failover(self):
        first = self.connect("Alpha")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second_welcome = second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")
        first.send({"type": "start_battle"})
        first.receive_type("battle_start")
        second.receive_type("battle_start")

        second.send({"type": "ping", "seq": 7, "client_time": 123.5})
        pong = second.receive_type("pong")
        self.assertEqual(7, pong["seq"])
        self.assertEqual(123.5, pong["client_time"])

        self.state.bot_planner._contacts[1][("human", 99)] = {"id": 99}
        self.state.bot_planner._affordances[16] = {"target": ("human", 99)}
        self.state.bot_planner._cover_states[16] = {"target": ("human", 99)}
        self.state.bot_planner._engage_anchors[16] = {"x": 0.0}

        first.close()
        deadline = time.time() + 2
        while time.time() < deadline and self.state.bot_authority_id != second_welcome["player_id"]:
            time.sleep(0.02)
        self.assertEqual(second_welcome["player_id"], self.state.bot_authority_id)
        self.assertEqual({1: {}, 2: {}}, self.state.bot_planner._contacts)
        self.assertEqual({}, self.state.bot_planner._affordances)
        self.assertEqual({}, self.state.bot_planner._cover_states)
        self.assertEqual({}, self.state.bot_planner._engage_anchors)

    def test_late_player_joins_the_current_battle(self):
        first = self.connect("Alpha")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        first.send({"type": "start_battle"})
        first_start = first.receive_type("battle_start")

        late = self.connect("LateBravo")
        late_welcome = late.receive_type("welcome")
        late_start = late.receive_type("battle_start")

        self.assertEqual("battle", late_welcome["phase"])
        self.assertTrue(late_start["late_join"])
        self.assertEqual(first_welcome["map"], first_start["map"])
        self.assertEqual(first_start["map"], late_start["map"])
        self.assertEqual(2, len(late_start["players"]))
        self.assertLessEqual(
            late_start["timing"]["start_in_ms"], first_start["timing"]["start_in_ms"]
        )

    def test_default_names_are_unique_and_vehicle_identity_is_preserved(self):
        first = self.connect("Defaultplayer")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Defaultplayer")
        second_welcome = second.receive_type("welcome")
        third = self.connect("Defaultplayer")
        third_welcome = third.receive_type("welcome")
        third_roster = third.receive_type("roster")

        self.assertNotEqual(first_welcome["name"], second_welcome["name"])
        self.assertEqual(3, len({
            first_welcome["name"], second_welcome["name"], third_welcome["name"]
        }))
        self.assertEqual("ussr:T-34", first_welcome["vehicle"])
        self.assertEqual(880, first_welcome["max_health"])
        self.assertEqual((1, 2), (first_welcome["team"], second_welcome["team"]))
        self.assertEqual((0, 0), (first_welcome["slot"], second_welcome["slot"]))
        self.assertIn(third_welcome["team"], (1, 2))
        self.assertEqual(1, third_welcome["slot"])
        third_state = [player for player in third_roster["players"] if player["id"] == 3][0]
        self.assertEqual(12.0, third_state["spawn_x"])
        self.assertEqual(
            -35.0 if third_welcome["team"] == 1 else 35.0,
            third_state["spawn_z"],
        )

    def test_client_resolved_shot_and_hit_are_broadcast(self):
        first = self.connect("Alpha")
        first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")
        self.state.phase = "battle"
        first.send({
            "type": "input",
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "yaw": 0.0,
            "aim_yaw": 0.0,
            "fire_seq": 1,
            "shell_index": 1,
        })
        second.send({"type": "input", "x": 0.0, "y": 0.0, "z": 70.0, "yaw": 3.14159265359})
        hit_report = {
            "type": "hit_report",
            "target": 2,
            "shot_seq": 1,
            "damage": 125,
            "shot_result": 2,
            "x": 0.0,
            "y": 1.0,
            "z": 70.0,
        }
        first.send(hit_report)
        first.send(hit_report)
        time.sleep(0.05)
        self.state.tick_once(0.05)

        event_message = first.receive_type("events")
        snapshot = second.receive_type("snapshot")

        shot = [event for event in event_message["events"] if event["kind"] == "shot"][0]
        hit = [event for event in event_message["events"] if event["kind"] == "hit"][0]
        target = [player for player in snapshot["players"] if player["id"] == 2][0]
        self.assertEqual(1, shot["shot_seq"])
        self.assertEqual(1, shot["shell_index"])
        self.assertEqual(2, hit["shot_result"])
        self.assertEqual(125, hit["damage"])
        self.assertEqual(755, target["health"])
        self.assertEqual(1, len([
            event for event in event_message["events"] if event["kind"] == "hit"
        ]))

    def test_local_simulation_health_is_relayed_downward(self):
        first = self.connect("Alpha")
        first.receive_type("welcome")
        first.receive_type("roster")
        self.state.phase = "battle"
        first.send({"type": "input", "reported_health": 700})
        time.sleep(0.05)
        self.state.tick_once(0.05)

        event_message = first.receive_type("events")
        health = [event for event in event_message["events"] if event["kind"] == "health"][0]
        self.assertEqual(180, health["damage"])
        self.assertEqual(700, health["health"])

    def test_dead_player_pose_is_frozen_at_the_wreck_position(self):
        client = self.connect("Alpha")
        welcome = client.receive_type("welcome")
        client.receive_type("roster")
        self.state.phase = "battle"
        client.send({
            "type": "input", "x": 10.0, "y": 2.0, "z": 30.0,
            "yaw": 0.5, "forward": 1.0,
        })
        time.sleep(0.05)
        client.send({"type": "input", "reported_health": 0})
        time.sleep(0.05)
        client.send({
            "type": "input", "x": 500.0, "y": 100.0, "z": 700.0,
            "yaw": 2.0, "forward": 1.0, "turn": 1.0, "fire_seq": 1,
        })
        time.sleep(0.05)

        with self.state.lock:
            player = self.state.players[welcome["player_id"]]
            self.assertFalse(player.alive)
            self.assertEqual((10.0, 2.0, 30.0), (player.x, player.y, player.z))
            self.assertEqual(0.5, player.yaw)
            self.assertEqual(0, player.fire_seq)
            self.assertEqual((0.0, 0.0), (player.forward, player.turn))


if __name__ == "__main__":
    unittest.main()
