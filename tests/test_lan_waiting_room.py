import json
import socket
import threading
import time
import unittest

from lan_battle_server import (
    BattleState, CLIENT_BUILD_082, CLIENT_BUILD_0922, ClientHandler,
    MAP_POOL_082, MAP_POOL_0922, PROTOCOL_VERSION, TICK_HZ,
    PREBATTLE_SECONDS, Player, ThreadedTCPServer,
)


class WireClient:
    def __init__(self, port, name, vehicle="ussr:T-34", max_health=880,
                 client_build=CLIENT_BUILD_0922):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.sock.settimeout(2)
        self.buffer = b""
        hello = {
            "type": "hello",
            "protocol": PROTOCOL_VERSION,
            "name": name,
            "vehicle": vehicle,
            "max_health": max_health,
        }
        if client_build is not None:
            hello["client_build"] = client_build
        self.send(hello)

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


def bot_manifest(started, selected_id=None):
    result = []
    for identity in started["bots"]:
        selected = identity["id"] == selected_id
        result.append(dict(
            identity,
            vehicle="germany:PzIV" if selected else "ussr:R11_MS-1",
            max_health=500,
            health=500,
            x=0.0 if selected else float(identity["slot"]),
            y=0.0,
            z=100.0 if selected else (
                -35.0 if identity["team"] == 1 else 35.0),
            yaw=3.14 if identity["team"] == 2 else 0.0,
        ))
    return result


def bot_states(manifest):
    return [dict(value, alive=True, fire_seq=0, shell_index=0,
                 aim_yaw=value["yaw"], gun_pitch=0.0, critical={},
                 combat_base_revision=0, combat_seq=0,
                 combat_fire_elapsed=0.0, combat_fire_timer=0.0)
            for value in manifest]


class WaitingRoomTest(unittest.TestCase):
    def setUp(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        self.state = state
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

    def connect(self, name, client_build=CLIENT_BUILD_0922):
        client = WireClient(self.port, name, client_build=client_build)
        self.clients.append(client)
        return client

    @staticmethod
    def critical_fixture(hp, fire, ammo_rack_death=False):
        destroyed = hp <= 0.0
        events = ([{"kind": "ammo_rack", "state": "destroyed",
                    "cause": "shot"}] if ammo_rack_death else [])
        return {
            "devices": [{
                "name": "engineHealth", "hp": float(hp),
                "max_hp": 100.0,
                "state": "destroyed" if destroyed else "critical",
            }],
            "destroyed": ["engineHealth"] if destroyed else [],
            "crew_ko": [], "fire": bool(fire),
            "ammo_rack_death": bool(ammo_rack_death),
            "events": events,
        }

    def modern_critical_hit_state(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.client_build = CLIENT_BUILD_0922
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        canonical = self.critical_fixture(40.0, True)
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), team=1,
                      x=0.0, z=0.0, fire_seq=1,
                      health=500, max_health=500),
            2: Player(2, None, ("127.0.0.1", 2), team=2,
                      x=0.0, z=20.0, health=500, max_health=500,
                      critical=canonical, critical_revision=2,
                      critical_report_base_revision=1,
                      critical_ack_seq=1),
        }
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.bot_states = {
            16: {
                "id": 16, "team": 1, "x": 0.0, "y": 0.0,
                "z": 5.0, "health": 500, "max_health": 500,
                "alive": True, "fire_seq": 1, "shell_index": 0,
                "critical": {}, "combat_revision": 0,
                "combat_base_revision": 0, "combat_ack_seq": 0,
                "combat_fire_elapsed": 0.0, "combat_fire_timer": 0.0,
            },
            17: {
                "id": 17, "team": 2, "x": 0.0, "y": 0.0,
                "z": 20.0, "health": 500, "max_health": 500,
                "alive": True, "fire_seq": 0, "shell_index": 0,
                "critical": canonical, "combat_revision": 2,
                "combat_base_revision": 1, "combat_ack_seq": 1,
                "combat_fire_elapsed": 4.5, "combat_fire_timer": 0.5,
                "fire_attacker_kind": "player", "fire_attacker_id": 1,
            },
        }
        return state

    def modern_critical_hit_cases(self, state):
        return (
            ("human_human", "hit", state.players[2], False,
             state.report_hit, 1, {"target": 2}),
            ("human_bot", "bot_hit", state.bot_states[17], True,
             state.report_bot_hit, 1, {"target": 17}),
            ("bot_human", "bot_human_hit", state.players[2], True,
             state.report_bot_human_hit, 1,
             {"attacker_bot": 16, "target": 2}),
            ("bot_bot", "bot_bot_hit", state.bot_states[17], False,
             state.report_bot_hit, 1,
             {"attacker_bot": 16, "target": 17}),
        )

    def flush_pending_battle_live(self):
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with self.state.lock:
                ready = self.state.pending_live_message is not None
            if ready:
                self.state.tick_once(1.0 / TICK_HZ)
                return
            time.sleep(0.005)
        raise AssertionError("server did not queue battle_live")

    def activate_modern_battle(self, clients, started):
        # The 0.9.22 client cannot declare the round load-complete until the
        # authority has published canonical bot identities and spawn poses.
        clients[0].send({"type": "bot_manifest",
                         "round_id": started["round_id"],
                         "bots": bot_manifest(started)})
        for client in clients:
            client.send({"type": "battle_ready",
                         "round_id": started["round_id"],
                         "bases": {
                             "1": [[-300.0, -300.0]],
                             "2": [[300.0, 300.0]],
                         }})
        self.flush_pending_battle_live()
        for client in clients:
            live = client.receive_type("battle_live")
            self.assertEqual(started["round_id"], live["round_id"])
            self.assertEqual(PREBATTLE_SECONDS,
                             live["countdown_seconds"])
        with self.state.lock:
            self.state.tick = int(PREBATTLE_SECONDS * TICK_HZ)

    def test_server_snapshots_run_at_thirty_hz(self):
        self.assertEqual(30.0, TICK_HZ)

    def test_server_timing_is_derived_from_authoritative_tick(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = 0
        self.assertEqual({
            "phase": "prebattle", "start_in_ms": 15000,
            "remaining_ms": 900000, "duration_ms": 900000,
        }, state._timing_payload())

        state.tick = int((PREBATTLE_SECONDS + 1.0) * TICK_HZ)
        self.assertEqual({
            "phase": "battle", "start_in_ms": 0,
            "remaining_ms": 899000, "duration_ms": 900000,
        }, state._timing_payload())

    def test_battle_live_is_an_own_tick_zero_wire_barrier(self):
        class RecordingConnection:
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                # Sending while the state lock is held makes the validation
                # immediately before this call atomic with respect to a round
                # reset that preserves this Player object.
                self.assert_state_lock_held()
                self.messages.append(json.loads(payload.decode('utf-8')))

            def assert_state_lock_held(self):
                self.test_case.assertTrue(self.state.lock._is_owned())

        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "loading"
        state.client_build = CLIENT_BUILD_0922
        state.bot_roster = []
        connection = RecordingConnection()
        connection.test_case = self
        connection.state = state
        participant = Player(
            1, connection, ("127.0.0.1", 1), name="P", vehicle="v",
            team=1, slot=0, health=100, max_health=100,
            battle_ready_round=state.round_id)
        state.players = {1: participant}

        live = state._activate_battle_if_ready()
        self.assertEqual(0, live["server_tick"])
        self.assertIs(live, state.pending_live_message["message"])
        self.assertEqual(state.round_id,
                         state.pending_live_message["round_id"])
        self.assertEqual((participant,),
                         state.pending_live_message["recipients"])

        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual(["battle_live"], [
            item["type"] for item in connection.messages])
        self.assertEqual(0, state.tick)
        self.assertIsNone(state.pending_live_message)

    def test_stale_pending_live_never_crosses_round_or_recipient_set(self):
        class RecordingConnection:
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode('utf-8')))

        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "loading"
        state.client_build = CLIENT_BUILD_0922
        state.bot_roster = []
        old_connection = RecordingConnection()
        old_player = Player(
            1, old_connection, ("127.0.0.1", 1), name="Old", vehicle="v",
            team=1, slot=0, health=100, max_health=100,
            battle_ready_round=state.round_id)
        state.players = {1: old_player}
        state.host_player_id = 1
        stale = state._activate_battle_if_ready()
        stale_pending = state.pending_live_message

        state._reset_round()
        new_connection = RecordingConnection()
        new_player = Player(
            2, new_connection, ("127.0.0.1", 2), name="New", vehicle="v",
            team=1, slot=0, health=100, max_health=100)
        state.players = {2: new_player}
        state.host_player_id = 2
        state.client_build = CLIENT_BUILD_0922
        # Reinsert the already-extracted old envelope to force the exact
        # reset-between-dequeue-and-send interleaving deterministically.
        state.pending_live_message = stale_pending

        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual(1, stale["round_id"])
        self.assertEqual(2, state.round_id)
        self.assertEqual([], new_connection.messages)
        self.assertEqual([], old_connection.messages)
        self.assertIsNone(state.pending_live_message)

    def test_loading_graceful_leave_rechecks_ready_barrier_atomically(self):
        class RecordingConnection:
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.client_build = CLIENT_BUILD_0922
        state.phase = "loading"
        state.bot_roster = []
        connections = [RecordingConnection() for unused in range(3)]
        players = [
            Player(
                index + 1, connections[index], ("127.0.0.1", index + 1),
                name="P%d" % (index + 1), vehicle="v",
                team=(1 if index != 1 else 2), slot=index,
                health=100, max_health=100,
                battle_ready_round=(state.round_id if index < 2 else 0))
            for index in range(3)
        ]
        state.players = {player.player_id: player for player in players}
        state.host_player_id = 1
        state.bot_authority_id = 1

        self.assertTrue(state.leave_battle_and_publish(
            3, {"round_id": state.round_id}))

        self.assertEqual("battle", state.phase)
        self.assertFalse(players[2].participating)
        self.assertEqual((players[0], players[1]),
                         state.pending_live_message["recipients"])
        self.assertGreater(state.state_revision, 0)
        for connection in connections:
            self.assertEqual("roster", connection.messages[-1]["type"])
            self.assertEqual("battle", connection.messages[-1]["phase"])
            self.assertEqual(state.state_revision,
                             connection.messages[-1]["state_revision"])

    def test_loading_authority_graceful_leave_publishes_new_authority(self):
        class RecordingConnection:
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.client_build = CLIENT_BUILD_0922
        state.phase = "loading"
        first_connection = RecordingConnection()
        second_connection = RecordingConnection()
        first = Player(
            1, first_connection, ("127.0.0.1", 1), team=1, slot=0,
            health=100, max_health=100)
        second = Player(
            2, second_connection, ("127.0.0.1", 2), team=2, slot=0,
            health=100, max_health=100)
        state.players = {1: first, 2: second}
        state.host_player_id = 1
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1

        self.assertTrue(state.leave_battle_and_publish(
            1, {"round_id": state.round_id}))

        self.assertEqual("loading", state.phase)
        self.assertEqual(2, state.bot_authority_id)
        self.assertIsNone(state.bot_manifest_authority_id)
        roster = second_connection.messages[-1]
        self.assertEqual("roster", roster["type"])
        self.assertEqual(2, roster["bot_authority_id"])
        self.assertEqual(state.state_revision, roster["state_revision"])

    def test_all_loading_players_gracefully_leave_into_next_waiting_round(self):
        class RecordingConnection:
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.client_build = CLIENT_BUILD_0922
        state.phase = "loading"
        connection = RecordingConnection()
        player = Player(
            1, connection, ("127.0.0.1", 1), team=1, slot=0,
            health=100, max_health=100)
        state.players = {1: player}
        state.host_player_id = 1
        state.bot_authority_id = 1
        old_round = state.round_id
        old_revision = state.state_revision

        self.assertTrue(state.leave_battle_and_publish(
            1, {"round_id": old_round}))

        self.assertEqual("waiting", state.phase)
        self.assertEqual(old_round + 1, state.round_id)
        self.assertGreater(state.state_revision, old_revision)
        self.assertIn(1, state.players)
        self.assertTrue(player.connected)
        self.assertTrue(player.participating)
        self.assertIsNone(state.pending_live_message)
        roster = connection.messages[-1]
        self.assertEqual("waiting", roster["phase"])
        self.assertEqual(state.round_id, roster["round_id"])
        self.assertEqual(state.state_revision, roster["state_revision"])

    def test_loading_transition_send_failure_repairs_survivor_roster(self):
        class FailingConnection:
            def sendall(self, unused_payload):
                raise OSError("forced transition send failure")

        class RecordingConnection:
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.client_build = CLIENT_BUILD_0922
        survivor_connection = RecordingConnection()
        failed = Player(
            1, FailingConnection(), ("127.0.0.1", 1),
            name="Failed", vehicle="v", team=1, slot=0,
            health=100, max_health=100)
        survivor = Player(
            2, survivor_connection, ("127.0.0.1", 2),
            name="Survivor", vehicle="v", team=2, slot=0,
            health=100, max_health=100)
        state.players = {1: failed, 2: survivor}
        state.host_player_id = 1
        state.next_id = 3
        start, error = state.request_start(1)
        self.assertIsNone(error)

        self.assertTrue(state.broadcast_loading_transition(start))

        self.assertEqual([2], sorted(state.players))
        self.assertEqual(2, state.host_player_id)
        self.assertEqual(2, state.bot_authority_id)
        self.assertEqual(["battle_start", "roster"], [
            message["type"] for message in survivor_connection.messages])
        repaired = survivor_connection.messages[-1]
        self.assertEqual("loading", repaired["phase"])
        self.assertEqual(2, repaired["host_player_id"])
        self.assertEqual(2, repaired["bot_authority_id"])
        self.assertEqual([2], [
            player["id"] for player in repaired["players"]])
        self.assertEqual(state.state_revision,
                         repaired["state_revision"])

    def test_loading_snapshot_send_failure_repairs_authority_before_live(self):
        class FailingConnection:
            def sendall(self, unused_payload):
                raise OSError("forced loading snapshot send failure")

        class RecordingConnection:
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.client_build = CLIENT_BUILD_0922
        state.phase = "loading"
        state.bot_roster = []
        survivor_connection = RecordingConnection()
        failed = Player(
            1, FailingConnection(), ("127.0.0.1", 1),
            team=1, slot=0, health=100, max_health=100)
        survivor = Player(
            2, survivor_connection, ("127.0.0.1", 2),
            team=2, slot=0, health=100, max_health=100)
        state.players = {1: failed, 2: survivor}
        state.host_player_id = 1
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        snapshot = state.loading_snapshot()

        self.assertTrue(state.broadcast_loading_transition(snapshot))

        self.assertEqual("loading", state.phase)
        self.assertEqual(2, state.bot_authority_id)
        self.assertIsNone(state.bot_manifest_authority_id)
        self.assertEqual(["snapshot", "roster"], [
            message["type"] for message in survivor_connection.messages])
        repaired = survivor_connection.messages[-1]
        self.assertEqual(2, repaired["bot_authority_id"])
        self.assertEqual([2], [
            player["id"] for player in repaired["players"]])

    def test_destructible_result_is_validated_deduplicated_and_persisted(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1),
                      health=500, max_health=500),
        }
        report = {
            "round_id": state.round_id,
            "destructible_kind": "module",
            "chunk_id": 1234,
            "item_index": 7,
            "mat_kind": 91,
            "x": 10.0, "y": 2.0, "z": -4.0,
            "fall_yaw": 0.25, "speed": 12.0,
            "is_shot": True,
        }

        self.assertTrue(state.report_destructible(1, report))
        self.assertTrue(state.report_destructible(1, report))
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(1, len(state.destructibles))
        event = state.pending_events[-1]
        self.assertEqual("destructible", event["kind"])
        self.assertEqual("module", event["destructible_kind"])
        self.assertEqual(1, event["reported_by"])

        malformed = dict(report, item_index=-1)
        self.assertFalse(state.report_destructible(1, malformed))
        state._reset_round()
        self.assertEqual({}, state.destructibles)
        self.assertEqual(0, state.destructible_revision)

    def test_room_is_pinned_to_one_client_build_and_its_exact_map_pool(self):
        modern = self.connect("Modern")
        welcome = modern.receive_type("welcome")
        modern.receive_type("roster")

        self.assertEqual(CLIENT_BUILD_0922, welcome["client_build"])
        self.assertEqual(list(MAP_POOL_0922), welcome["map_pool"])
        self.assertNotIn("03_campania", welcome["map_pool"])

        legacy = self.connect("Legacy", client_build=None)
        rejected = legacy.receive_type("error")
        self.assertEqual("incompatible_client_build", rejected["code"])

    def test_legacy_hello_needs_no_new_field_and_receives_082_maps(self):
        legacy = self.connect("Legacy", client_build=None)
        welcome = legacy.receive_type("welcome")

        self.assertEqual(CLIENT_BUILD_082, welcome["client_build"])
        self.assertEqual(list(MAP_POOL_082), welcome["map_pool"])
        self.assertIn("03_campania", welcome["map_pool"])
        self.assertNotIn("59_asia_great_wall", welcome["map_pool"])

    def test_unknown_explicit_client_build_is_rejected(self):
        client = self.connect("Unknown", client_build="wot-unknown")
        rejected = client.receive_type("error")
        self.assertEqual("unsupported_client_build", rejected["code"])

    def test_empty_room_can_be_reused_by_the_other_client_build(self):
        modern = self.connect("Modern")
        modern.receive_type("welcome")
        modern.close()
        self.clients.remove(modern)
        deadline = time.time() + 2
        while self.state.client_build is not None and time.time() < deadline:
            time.sleep(0.01)
        self.assertIsNone(self.state.host_player_id)

        legacy = self.connect("Legacy", client_build=None)
        welcome = legacy.receive_type("welcome")
        self.assertEqual(CLIENT_BUILD_082, welcome["client_build"])
        self.assertEqual(welcome["player_id"], welcome["host_player_id"])

    def test_fixed_map_unavailable_to_the_joining_build_is_rejected(self):
        state = BattleState(map_name="03_campania", max_players=30)
        server = ThreadedTCPServer(("127.0.0.1", 0), ClientHandler)
        server.game_server = type("GameServer", (), {"state": state})()
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        client = WireClient(
            server.server_address[1], "Modern", client_build=CLIENT_BUILD_0922)
        try:
            rejected = client.receive_type("error")
            self.assertEqual("map_not_available_for_client", rejected["code"])
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_one_player_can_start_from_clickable_panel_request(self):
        client = self.connect("Solo")
        welcome = client.receive_type("welcome")
        roster = client.receive_type("roster")

        client.send({"type": "start_battle"})
        started = client.receive_type("battle_start")

        self.assertEqual("04_himmelsdorf", welcome["map"])
        self.assertEqual(welcome["map"], started["map"])
        self.assertEqual(welcome["player_id"], welcome["host_player_id"])
        self.assertEqual(welcome["player_id"], roster["host_player_id"])
        self.assertEqual(welcome["player_id"], started["host_player_id"])
        self.assertEqual(welcome["state_revision"],
                         roster["state_revision"])
        self.assertGreater(started["state_revision"],
                           roster["state_revision"])
        self.assertEqual(1, len(started["players"]))

    def test_modern_guest_cannot_start_or_change_the_host_map(self):
        host = self.connect("Host")
        host_welcome = host.receive_type("welcome")
        host.receive_type("roster")
        guest = self.connect("Guest")
        guest_welcome = guest.receive_type("welcome")
        host_roster = host.receive_type("roster")
        guest_roster = guest.receive_type("roster")
        original_map = self.state.map_name

        guest.send({"type": "start_battle", "map": "31_airfield"})
        denied = guest.receive_type("start_denied")

        self.assertEqual("host_only", denied["code"])
        self.assertEqual("waiting", self.state.phase)
        self.assertEqual(original_map, self.state.map_name)
        self.assertEqual(host_welcome["player_id"], guest_welcome["host_player_id"])
        self.assertEqual(host_welcome["player_id"], host_roster["host_player_id"])
        self.assertEqual(host_welcome["player_id"], guest_roster["host_player_id"])

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

        first.send({"type": "start_battle"})
        first_start = first.receive_type("battle_start")
        second_start = second.receive_type("battle_start")

        self.assertEqual(2, len(first_roster["players"]))
        self.assertEqual(2, len(second_roster["players"]))
        self.assertEqual(first_welcome["map"], second_welcome["map"])
        self.assertEqual(first_welcome["map"], first_start["map"])
        self.assertEqual(first_start["map"], second_start["map"])
        self.assertEqual(first_welcome["player_id"], first_start["host_player_id"])
        self.assertEqual(first_start["host_player_id"], second_start["host_player_id"])
        self.assertEqual(2, len(first_start["players"]))
        self.assertEqual(first_start["bots"], second_start["bots"])
        self.assertEqual(28, len(first_start["bots"]))
        self.assertEqual(28, len({bot["name"] for bot in first_start["bots"]}))
        participants = [
            (value["team"], value["slot"])
            for value in first_start["players"] + first_start["bots"]]
        self.assertEqual(30, len(participants))
        self.assertEqual(30, len(set(participants)))
        self.assertEqual(first_welcome["player_id"], first_start["bot_authority_id"])

    def test_modern_clients_share_one_load_barrier_and_countdown(self):
        first = self.connect("Alpha")
        first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")

        first.send({"type": "start_battle"})
        started = first.receive_type("battle_start")
        second.receive_type("battle_start")
        self.assertEqual("loading", started["phase"])
        self.assertEqual("loading", self.state.phase)

        first.send({"type": "battle_ready",
                    "round_id": started["round_id"],
                    "bases": {
                        "1": [[-300.0, -300.0]],
                        "2": [[300.0, 300.0]],
                    }})
        time.sleep(0.05)
        self.assertEqual("loading", self.state.phase)

        second.send({"type": "battle_ready",
                     "round_id": started["round_id"]})
        time.sleep(0.05)
        self.assertEqual("loading", self.state.phase)

        first.send({"type": "bot_manifest",
                    "round_id": started["round_id"],
                    "bots": bot_manifest(started)})
        self.flush_pending_battle_live()
        first_live = first.receive_type("battle_live")
        second_live = second.receive_type("battle_live")

        self.assertEqual("battle", self.state.phase)
        self.assertEqual(0, self.state.tick)
        self.assertEqual(15.0, first_live["countdown_seconds"])
        self.assertEqual(0, first_live["server_tick"])
        self.assertEqual({
            "phase": "prebattle", "start_in_ms": 15000,
            "remaining_ms": 900000, "duration_ms": 900000,
        }, first_live["timing"])
        self.assertEqual(first_live, second_live)
        self.assertEqual({
            1: [(-300.0, -300.0)],
            2: [(300.0, 300.0)],
        }, self.state.capture_bases)

    def test_modern_manifest_loading_snapshot_has_complete_combat_contract(self):
        from gui.mods.offline_lan_0922.lan_client import LANClient

        wire = self.connect("Alpha")
        wire.receive_type("welcome")
        wire.receive_type("roster")
        wire.send({"type": "start_battle"})
        started = wire.receive_type("battle_start")
        wire.send({"type": "bot_manifest",
                   "round_id": started["round_id"],
                   "bots": bot_manifest(started)})
        snapshot = wire.receive_type("snapshot")

        self.assertTrue(snapshot["bots"])
        self.assertTrue(all(
            isinstance(bot.get("critical"), dict) and
            bot.get("combat_fire_elapsed") == 0.0 and
            bot.get("combat_fire_timer") == 0.0
            for bot in snapshot["bots"]))
        client = LANClient("127.0.0.1", self.port, "P", "ussr:MS-1")
        client.running = True
        client.phase = "loading"
        client.round_id = started["round_id"]
        client._send = lambda unused: True
        client._handle_message(snapshot)
        self.assertIsNone(client.last_error)
        self.assertEqual(snapshot, client.last_snapshot)

    def test_loading_authority_disconnect_promotes_guest_and_unblocks_room(self):
        first = self.connect("Alpha")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second_welcome = second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")

        first.send({"type": "start_battle"})
        started = first.receive_type("battle_start")
        second.receive_type("battle_start")
        self.assertEqual(first_welcome["player_id"],
                         started["bot_authority_id"])

        first.close()
        self.clients.remove(first)
        roster = second.receive_type("roster")
        self.assertEqual("loading", roster["phase"])
        self.assertEqual(second_welcome["player_id"],
                         roster["bot_authority_id"])

        second.send({"type": "bot_manifest",
                     "round_id": started["round_id"],
                     "bots": bot_manifest(started)})
        second.send({"type": "battle_ready",
                     "round_id": started["round_id"]})
        self.flush_pending_battle_live()
        live = second.receive_type("battle_live")

        self.assertEqual(started["round_id"], live["round_id"])
        self.assertEqual("battle", self.state.phase)

    def test_standard_capture_uses_the_082_server_owned_law(self):
        client = self.connect("Alpha")
        welcome = client.receive_type("welcome")
        client.receive_type("roster")
        client.send({"type": "start_battle"})
        started = client.receive_type("battle_start")
        self.activate_modern_battle((client,), started)

        with self.state.lock:
            player = self.state.players[welcome["player_id"]]
            player.x = 300.7
            player.z = 300.9
            self.state.tick = int(PREBATTLE_SECONDS * TICK_HZ) + int(TICK_HZ)
            self.state._update_capture()
            first_base = dict(self.state.rules_state["bases"]["2"])
            for second in range(2, 101):
                self.state.tick = int(PREBATTLE_SECONDS * TICK_HZ) + (
                    second * int(TICK_HZ))
                self.state._update_capture()

            base = self.state.rules_state["bases"]["2"]
            result = self.state.battle_result

        self.assertEqual({
            "points": 1,
            "time_left": 99.0,
            "invaders": 1,
            "stopped": False,
        }, first_base)
        self.assertEqual(100, base["points"])
        self.assertEqual(1, result["winner"])
        self.assertEqual(2, result["base_team"])
        self.assertEqual("base captured", result["reason"])

    def test_waiting_host_leave_transfers_host_to_lowest_connected_id(self):
        first = self.connect("Alpha")
        first_welcome = first.receive_type("welcome")
        first_initial_roster = first.receive_type("roster")
        second = self.connect("Bravo")
        second_welcome = second.receive_type("welcome")
        first_second_roster = first.receive_type("roster")
        second_initial_roster = second.receive_type("roster")
        third = self.connect("Charlie")
        third_welcome = third.receive_type("welcome")
        first_third_roster = first.receive_type("roster")
        second_third_roster = second.receive_type("roster")
        third_initial_roster = third.receive_type("roster")

        first.close()
        self.clients.remove(first)
        second_roster = second.receive_type("roster")
        third_roster = third.receive_type("roster")

        self.assertEqual(first_welcome["player_id"], first_welcome["host_player_id"])
        self.assertEqual(second_welcome["player_id"], self.state.host_player_id)
        self.assertLess(second_welcome["player_id"], third_welcome["player_id"])
        self.assertEqual(second_welcome["player_id"], second_roster["host_player_id"])
        self.assertEqual(second_welcome["player_id"], third_roster["host_player_id"])
        self.assertEqual(first_welcome["state_revision"],
                         first_initial_roster["state_revision"])
        self.assertEqual(second_welcome["state_revision"],
                         first_second_roster["state_revision"])
        self.assertEqual(second_welcome["state_revision"],
                         second_initial_roster["state_revision"])
        self.assertEqual(third_welcome["state_revision"],
                         first_third_roster["state_revision"])
        self.assertEqual(third_welcome["state_revision"],
                         second_third_roster["state_revision"])
        self.assertEqual(third_welcome["state_revision"],
                         third_initial_roster["state_revision"])
        self.assertGreater(second_roster["state_revision"],
                           third_welcome["state_revision"])
        self.assertEqual(second_roster["state_revision"],
                         third_roster["state_revision"])

    def test_legacy_guest_can_still_select_the_map_and_start(self):
        first = self.connect("LegacyHost", client_build=None)
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("LegacyGuest", client_build=None)
        second_welcome = second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")

        second.send({"type": "start_battle", "map": "31_airfield"})
        first_start = first.receive_type("battle_start")
        second_start = second.receive_type("battle_start")

        self.assertEqual("31_airfield", first_start["map"])
        self.assertEqual(second_welcome["player_id"], first_start["requested_by"])
        self.assertEqual(first_welcome["player_id"], first_start["host_player_id"])
        self.assertEqual(first_start["host_player_id"], second_start["host_player_id"])

    def test_authority_owns_bot_rules_and_result_state(self):
        first = self.connect("Alpha")
        first_welcome = first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")
        first.send({"type": "start_battle"})
        started = first.receive_type("battle_start")
        second.receive_type("battle_start")
        self.activate_modern_battle((first, second), started)
        identity = next(value for value in started["bots"]
                        if value["team"] == 2)
        manifest = bot_manifest(started, identity["id"])
        bot = next(value for value in manifest
                   if value["id"] == identity["id"])

        second.send({"type": "bot_manifest", "bots": manifest})
        first.send({"type": "bot_manifest", "bots": manifest})
        time.sleep(0.05)
        self.assertEqual(len(started["bots"]), len(self.state.bot_manifest))
        first_state = bot_states(manifest)
        target_state = next(value for value in first_state
                            if value["id"] == bot["id"])
        target_state.update(x=4.0, z=92.0, aim_yaw=3.0, gun_pitch=-0.1,
                            fire_seq=1, shell_index=1)
        first.send({"type": "bot_state", "bots": first_state})
        second_state = [dict(value) for value in first_state]
        next(value for value in second_state
             if value["id"] == bot["id"])["fire_seq"] = 2
        first.send({"type": "bot_state", "bots": second_state})
        first.send({"type": "input", "fire_seq": 1, "shell_index": 0})
        first.send({
            "type": "bot_hit_report", "target": bot["id"], "shot_seq": 1,
            "damage": 125, "shot_result": 2, "x": 4.0, "y": 1.0, "z": 92.0,
        })
        bot_human_hit = {
            "type": "bot_human_hit", "attacker_bot": bot["id"],
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
        shared_bot = next(value for value in snapshot["bots"]
                          if value["id"] == bot["id"])
        self.assertEqual(first_welcome["player_id"], snapshot["bot_authority_id"])
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
        started = first.receive_type("battle_start")
        second.receive_type("battle_start")
        self.activate_modern_battle((first, second), started)
        identity = next(value for value in started["bots"]
                        if value["team"] == 2)
        manifest = bot_manifest(started, identity["id"])
        bot = next(value for value in manifest
                   if value["id"] == identity["id"])
        bot["profile"] = {
            "class_tag": "mediumTank", "dominant_role": "support",
            "roles": ["support"], "desired_range": 220,
            "fire_range": 600}
        first.send({"type": "bot_manifest", "bots": manifest})
        first.send({"type": "bot_state", "bots": bot_states(manifest)})
        first.send({"type": "bot_observation", "contacts": [{
            "observing_team": 2, "target_kind": "human",
            "target_id": first_welcome["player_id"], "target_team": 1,
            "visible": True, "x": 17.0, "y": 2.0, "z": -31.0,
            "health": 880, "max_health": 880, "class_tag": "mediumTank",
            "shootable_by_bot_ids": [identity["id"]],
        }]})
        time.sleep(0.05)
        self.state.tick_once(0.05)
        snapshot = second.receive_type("snapshot")
        self.assertGreater(snapshot["bot_order_revision"], 0)
        targeted = [value for value in snapshot["bot_orders"]
                    if value["team"] == 2 and
                    value["target_id"] == first_welcome["player_id"]]
        self.assertEqual(1, len(targeted))
        order = targeted[0]
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
        self.clients.remove(first)
        deadline = time.time() + 2
        while time.time() < deadline and self.state.bot_authority_id != second_welcome["player_id"]:
            time.sleep(0.02)
        roster = second.receive_type("roster")
        self.assertEqual(second_welcome["player_id"], self.state.bot_authority_id)
        self.assertEqual(second_welcome["player_id"], self.state.host_player_id)
        self.assertEqual(second_welcome["player_id"], roster["host_player_id"])
        self.assertEqual({1: {}, 2: {}}, self.state.bot_planner._contacts)
        self.assertEqual({}, self.state.bot_planner._affordances)
        self.assertEqual({}, self.state.bot_planner._cover_states)
        self.assertEqual({}, self.state.bot_planner._engage_anchors)

    def test_late_player_is_rejected_until_the_next_waiting_round(self):
        first = self.connect("Alpha")
        first.receive_type("welcome")
        first.receive_type("roster")
        first.send({"type": "start_battle"})
        first.receive_type("battle_start")

        late = self.connect("LateBravo")
        error = late.receive_type("error")

        self.assertEqual("battle_in_progress", error["code"])
        self.assertEqual(1, len(self.state.players))

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
        self.assertEqual((1, 1), (third_welcome["team"], third_welcome["slot"]))
        third_state = [player for player in third_roster["players"] if player["id"] == 3][0]
        self.assertEqual(12.0, third_state["spawn_x"])
        self.assertEqual(-35.0, third_state["spawn_z"])

    def test_client_resolved_shot_and_hit_are_broadcast(self):
        first = self.connect("Alpha")
        first.receive_type("welcome")
        first.receive_type("roster")
        second = self.connect("Bravo")
        second.receive_type("welcome")
        first.receive_type("roster")
        second.receive_type("roster")
        self.state.phase = "battle"
        self.state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
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
            "hull_damage": 125,
            "shot_result": 2,
            "critical_target_base_revision": 0,
            "critical_target_ack_seq": 0,
            "x": 0.0,
            "y": 1.0,
            "z": 70.0,
            "critical": {
                "devices": [{"name": "leftTrackHealth", "hp": 0.0,
                             "max_hp": 100.0, "state": "destroyed"}],
                "destroyed": ["leftTrackHealth"], "crew_ko": [],
                "fire": False, "ammo_rack_death": False,
                "events": [{"kind": "device",
                            "name": "leftTrackHealth",
                            "old_state": "normal", "state": "destroyed",
                            "cause": "shot"}],
            },
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
        self.assertTrue(hit["critical_accepted"])
        self.assertEqual("leftTrackHealth",
                         hit["critical"]["devices"][0]["name"])
        self.assertEqual("shot",
                         hit["critical"]["events"][0]["cause"])
        self.assertEqual(755, target["health"])
        self.assertEqual(
            "leftTrackHealth", target["critical"]["devices"][0]["name"])
        self.assertEqual([], target["critical"]["events"])
        self.assertEqual(1, len([
            event for event in event_message["events"] if event["kind"] == "hit"
        ]))

    def test_modern_stale_critical_proposals_apply_only_hull_damage(self):
        invalid_mutations = (
            lambda message: message.pop("hull_damage"),
            lambda message: message.update(
                critical_target_base_revision=True),
            lambda message: message.update(critical_target_ack_seq=0.5),
            lambda message: message.update(hull_damage="25"),
        )
        for index, name in enumerate((
                "human_human", "human_bot", "bot_human", "bot_bot")):
            with self.subTest(path=name):
                state = self.modern_critical_hit_state()
                case = self.modern_critical_hit_cases(state)[index]
                unused_name, event_kind, target, splash, report, sender, ids = case
                message = dict({
                    "round_id": state.round_id, "shot_seq": 1,
                    "damage": 500, "hull_damage": 25,
                    "shot_result": 2, "splash": splash,
                    "critical_target_base_revision": 1,
                    "critical_target_ack_seq": 0,
                    "critical": self.critical_fixture(
                        0.0, False, ammo_rack_death=True),
                }, **ids)
                invalid = dict(message)
                invalid_mutations[index](invalid)

                self.assertFalse(report(sender, invalid))
                self.assertEqual(500, target.health if isinstance(
                    target, Player) else target["health"])
                self.assertTrue((target.critical if isinstance(
                    target, Player) else target["critical"])["fire"])

                self.assertTrue(report(sender, message))
                health = target.health if isinstance(
                    target, Player) else target["health"]
                critical = target.critical if isinstance(
                    target, Player) else target["critical"]
                self.assertEqual(475, health)
                self.assertTrue(critical["fire"])
                self.assertEqual(40.0, critical["devices"][0]["hp"])
                event = [item for item in state.pending_events
                         if item.get("kind") == event_kind][-1]
                self.assertEqual(25, event["damage"])
                self.assertFalse(event["critical_accepted"])
                self.assertEqual("stale_target_state", event[
                    "critical_reject_reason"])
                self.assertNotIn("critical", event)
                if not isinstance(target, Player):
                    self.assertEqual(4.5, target[
                        "combat_fire_elapsed"])
                    self.assertEqual(0.5, target[
                        "combat_fire_timer"])

    def test_modern_matching_critical_proposals_apply_final_damage(self):
        for index, name in enumerate((
                "human_human", "human_bot", "bot_human", "bot_bot")):
            with self.subTest(path=name):
                state = self.modern_critical_hit_state()
                case = self.modern_critical_hit_cases(state)[index]
                unused_name, event_kind, target, splash, report, sender, ids = case
                message = dict({
                    "round_id": state.round_id, "shot_seq": 1,
                    "damage": 500, "hull_damage": 25,
                    "shot_result": 2, "splash": splash,
                    "critical_target_base_revision": 1,
                    "critical_target_ack_seq": 1,
                    "critical": self.critical_fixture(
                        0.0, False, ammo_rack_death=True),
                }, **ids)

                self.assertTrue(report(sender, message))
                health = target.health if isinstance(
                    target, Player) else target["health"]
                critical = target.critical if isinstance(
                    target, Player) else target["critical"]
                self.assertEqual(0, health)
                self.assertTrue(critical["ammo_rack_death"])
                self.assertFalse(critical["fire"])
                event = [item for item in state.pending_events
                         if item.get("kind") == event_kind][-1]
                self.assertEqual(500, event["damage"])
                self.assertTrue(event["critical_accepted"])
                self.assertTrue(event["critical"]["ammo_rack_death"])

    def test_modern_stale_pre_repair_proposal_cannot_undo_bot_repair(self):
        state = self.modern_critical_hit_state()
        target = state.bot_states[17]
        target["critical"] = self.critical_fixture(40.0, False)
        target["combat_fire_elapsed"] = 0.0
        target["combat_fire_timer"] = 0.0
        target["fire_attacker_kind"] = ""
        target["fire_attacker_id"] = 0

        self.assertTrue(state.report_bot_hit(1, {
            "round_id": state.round_id, "target": 17,
            "shot_seq": 1, "damage": 500, "hull_damage": 25,
            "shot_result": 2,
            "critical_target_base_revision": 1,
            "critical_target_ack_seq": 0,
            "critical": self.critical_fixture(0.0, True),
        }))

        self.assertEqual(475, target["health"])
        self.assertFalse(target["critical"]["fire"])
        self.assertEqual(40.0, target["critical"]["devices"][0]["hp"])
        self.assertEqual(0.0, target["combat_fire_elapsed"])
        self.assertEqual("", target["fire_attacker_kind"])

    def test_one_he_shot_accepts_one_report_per_distinct_target(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), x=0.0, z=0.0,
                      fire_seq=1, health=500, max_health=500),
            2: Player(2, None, ("127.0.0.1", 2), x=0.0, z=8.0,
                      team=2, health=500, max_health=500),
            3: Player(3, None, ("127.0.0.1", 3), x=0.0, z=12.0,
                      team=2, health=500, max_health=500),
        }

        first = {"round_id": state.round_id, "target": 2,
                 "shot_seq": 1, "damage": 100, "splash": True}
        second = {"round_id": state.round_id, "target": 3,
                  "shot_seq": 1, "damage": 75, "splash": True}

        self.assertTrue(state.report_hit(1, first))
        self.assertFalse(state.report_hit(1, first))
        self.assertTrue(state.report_hit(1, second))
        self.assertEqual(400, state.players[2].health)
        self.assertEqual(425, state.players[3].health)
        self.assertEqual(2, len([
            event for event in state.pending_events
            if event.get("kind") == "hit"]))

    def test_he_splash_may_damage_its_firing_vehicle_once(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), x=0.0, z=0.0,
                      fire_seq=1, health=500, max_health=500),
        }
        report = {"round_id": state.round_id, "target": 1,
                  "shot_seq": 1, "damage": 50, "splash": True}

        self.assertTrue(state.report_hit(1, report))
        self.assertFalse(state.report_hit(1, report))
        self.assertEqual(450, state.players[1].health)

    def test_enemy_frag_is_server_owned_and_published_once(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), team=1, fire_seq=1,
                      health=500, max_health=500),
            2: Player(2, None, ("127.0.0.1", 2), team=2,
                      health=100, max_health=100),
        }
        report = {"round_id": state.round_id, "target": 2,
                  "shot_seq": 1, "damage": 100}

        self.assertTrue(state.report_hit(1, report))
        self.assertFalse(state.report_hit(1, report))
        self.assertEqual(1, state.players[1].frags)
        self.assertEqual("player", state.players[2].death_attacker_kind)
        self.assertEqual(1, state.players[2].death_attacker_id)
        statistics = [event for event in state.pending_events
                      if event.get("kind") == "vehicle_statistics"]
        self.assertEqual([{
            "kind": "vehicle_statistics", "actor_kind": "player",
            "actor_id": 1, "frags": 1, "team_killer": False,
        }], statistics)
        self.assertEqual(1, state._public_player(state.players[1])["frags"])

    def test_public_player_carries_native_filter_input(self):
        player = Player(
            1, None, ("127.0.0.1", 1), forward=0.75, turn=-0.5)

        public = BattleState._public_player(player)

        self.assertEqual(0.75, public["forward"])
        self.assertEqual(-0.5, public["turn"])

    def test_team_kill_subtracts_frag_and_marks_attacker(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), team=1, fire_seq=1,
                      health=500, max_health=500),
            2: Player(2, None, ("127.0.0.1", 2), team=1,
                      health=100, max_health=100),
            3: Player(3, None, ("127.0.0.1", 3), team=2,
                      health=500, max_health=500),
        }

        self.assertTrue(state.report_hit(1, {
            "round_id": state.round_id, "target": 2,
            "shot_seq": 1, "damage": 100}))
        self.assertEqual(-1, state.players[1].frags)
        self.assertTrue(state.players[1].team_killer)
        self.assertEqual({
            "kind": "vehicle_statistics", "actor_kind": "player",
            "actor_id": 1, "frags": -1, "team_killer": True,
        }, state.pending_events[-1])

        state._reset_round()
        self.assertEqual(0, state.players[1].frags)
        self.assertFalse(state.players[1].team_killer)

    def test_bot_friendly_kill_only_subtracts_frag_like_082(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.bot_states = {
            1: {"id": 1, "team": 1, "frags": 0,
                "team_killer": False},
        }

        self.assertTrue(state._record_frag("bot", 1, 1, "player", 2))
        self.assertEqual(-1, state.bot_states[1]["frags"])
        self.assertFalse(state.bot_states[1]["team_killer"])
        self.assertFalse(state.pending_events[-1]["team_killer"])

    def test_player_bot_and_bot_bot_kills_publish_durable_frags(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), team=1, fire_seq=1,
                      health=500, max_health=500),
        }
        state.bot_states = {
            16: {"id": 16, "team": 2, "x": 0.0, "y": 0.0,
                 "z": 20.0, "health": 100, "alive": True,
                 "fire_seq": 1, "shell_index": 0, "frags": 0,
                 "team_killer": False},
            17: {"id": 17, "team": 1, "x": 0.0, "y": 0.0,
                 "z": 10.0, "health": 100, "alive": True,
                 "fire_seq": 1, "shell_index": 0, "frags": 0,
                 "team_killer": False},
        }

        self.assertTrue(state.report_bot_hit(1, {
            "round_id": state.round_id, "target": 16,
            "shot_seq": 1, "damage": 100}))
        self.assertEqual(1, state.players[1].frags)
        self.assertEqual("player", state.bot_states[16][
            "death_attacker_kind"])

        state.bot_states[16].update(
            health=100, alive=True, death_attacker_kind="",
            death_attacker_id=0)
        self.assertTrue(state.report_bot_hit(1, {
            "round_id": state.round_id, "attacker_bot": 17,
            "target": 16, "shot_seq": 1, "damage": 100}))
        self.assertEqual(1, state.bot_states[17]["frags"])
        self.assertEqual("bot", state.bot_states[16][
            "death_attacker_kind"])
        self.assertEqual(17, state.bot_states[16]["death_attacker_id"])

    def test_bot_human_kill_and_environment_death_attribution(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), team=1,
                      health=100, max_health=100),
            2: Player(2, None, ("127.0.0.1", 2), team=2,
                      health=100, max_health=100),
        }
        state.bot_states = {
            16: {"id": 16, "team": 2, "x": 0.0, "y": 0.0,
                 "z": 20.0, "health": 100, "alive": True,
                 "fire_seq": 1, "shell_index": 0, "frags": 0,
                 "team_killer": False},
        }

        self.assertTrue(state.report_bot_human_hit(1, {
            "round_id": state.round_id, "attacker_bot": 16,
            "target": 1, "shot_seq": 1, "damage": 100}))
        self.assertEqual(1, state.bot_states[16]["frags"])
        self.assertEqual("bot", state.players[1].death_attacker_kind)
        self.assertEqual(16, state.players[1].death_attacker_id)

        self.assertIsNone(state.update_input(2, {
            "round_id": state.round_id, "reported_health": 0,
            "reported_reason": 5}))
        self.assertEqual("", state.players[2].death_attacker_kind)
        self.assertEqual(0, state.players[2].death_attacker_id)

    def test_self_splash_death_does_not_award_a_frag(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), team=1, fire_seq=1,
                      health=50, max_health=50),
        }

        self.assertTrue(state.report_hit(1, {
            "round_id": state.round_id, "target": 1,
            "shot_seq": 1, "damage": 50, "splash": True}))
        self.assertEqual(0, state.players[1].frags)
        self.assertEqual([], [event for event in state.pending_events
                              if event.get("kind") == "vehicle_statistics"])

    def test_authority_bot_snapshot_cannot_forge_server_owned_frags(self):
        identity = {"id": 16, "team": 2, "slot": 0, "name": "Bot",
                    "vehicle": "ussr:R11_MS-1", "max_health": 100}
        previous = {
            "id": 16, "team": 2, "frags": 3, "team_killer": False,
            "health": 100, "fire_seq": 0, "death_attacker_kind": "",
            "death_attacker_id": 0,
        }
        forged = dict(previous, frags=99, team_killer=True,
                      x=0.0, y=0.0, z=0.0, yaw=0.0, alive=True)

        sanitized = BattleState._sanitize_bot_state(
            forged, identity, previous)

        self.assertEqual(3, sanitized["frags"])
        self.assertFalse(sanitized["team_killer"])

    def test_bot_state_carries_clamped_native_filter_input(self):
        identity = {"id": 16, "team": 2, "slot": 0, "name": "Bot",
                    "vehicle": "ussr:R11_MS-1", "max_health": 100}
        raw = {
            "id": 16, "health": 100, "alive": True, "fire_seq": 0,
            "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0,
            "movement_dir": 9, "rotation_dir": -4,
        }

        sanitized = BattleState._sanitize_bot_state(raw, identity, None)

        self.assertEqual(1, sanitized["movement_dir"])
        self.assertEqual(-1, sanitized["rotation_dir"])

    def test_ammo_rack_death_transition_is_accepted_by_server(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        state.players = {
            1: Player(1, None, ("127.0.0.1", 1), team=1, fire_seq=1,
                      health=500, max_health=500),
            2: Player(2, None, ("127.0.0.1", 2), team=2,
                      health=100, max_health=100),
        }
        critical = {
            "devices": [], "destroyed": [], "crew_ko": [],
            "fire": False, "ammo_rack_death": True,
            "events": [{"kind": "ammo_rack", "state": "destroyed",
                        "cause": "shot"}],
        }

        self.assertTrue(state.report_hit(1, {
            "round_id": state.round_id, "target": 2,
            "shot_seq": 1, "damage": 100, "critical": critical}))
        hit = [event for event in state.pending_events
               if event.get("kind") == "hit"][0]
        self.assertTrue(hit["critical"]["ammo_rack_death"])
        self.assertEqual("ammo_rack", hit["critical"]["events"][0]["kind"])

    def test_local_simulation_health_is_relayed_downward(self):
        first = self.connect("Alpha")
        first.receive_type("welcome")
        first.receive_type("roster")
        self.state.phase = "battle"
        self.state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        first.send({"type": "input", "reported_health": 700})
        time.sleep(0.05)
        self.state.tick_once(0.05)

        event_message = first.receive_type("events")
        health = [event for event in event_message["events"] if event["kind"] == "health"][0]
        self.assertEqual(180, health["damage"])
        self.assertEqual(700, health["health"])

    def test_local_critical_and_drowning_metadata_are_durable(self):
        first = self.connect("Alpha")
        welcome = first.receive_type("welcome")
        first.receive_type("roster")
        self.state.phase = "battle"
        self.state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        critical = {
            "devices": [{"name": "fuelTankHealth", "hp": 0.0,
                         "max_hp": 100.0, "state": "destroyed"}],
            "destroyed": ["fuelTankHealth"], "crew_ko": [],
            "fire": False, "ammo_rack_death": False,
            "events": [{"kind": "fire", "state": False,
                        "cause": "drowning"}],
        }
        first.send({
            "type": "input", "reported_health": 0,
            "reported_critical": critical, "reported_reason": 5,
            "reported_display_health": 880,
            "reported_critical_base_revision": 0,
            "reported_critical_seq": 1})
        time.sleep(0.05)
        self.state.tick_once(0.05)

        events = first.receive_type("events")["events"]
        snapshot = first.receive_type("snapshot")
        event = [value for value in events
                 if value.get("kind") == "health"][0]
        player = [value for value in snapshot["players"]
                  if value["id"] == welcome["player_id"]][0]
        self.assertEqual(5, event["death_reason"])
        self.assertEqual(880, event["display_health"])
        self.assertEqual("drowning", event["critical"]["events"][0]["cause"])
        self.assertEqual(5, player["death_reason"])
        self.assertEqual(880, player["display_health"])
        self.assertEqual([], player["critical"]["events"])

    def test_repair_hp_checkpoint_is_durable_without_event_flood(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        player = Player(
            1, None, ("127.0.0.1", 1), team=1,
            health=500, max_health=500)
        state.players = {1: player}

        def critical(hp, phase="destroyed", events=None):
            return {
                "devices": [{"name": "engineHealth", "hp": hp,
                             "max_hp": 100.0, "state": phase}],
                "destroyed": (["engineHealth"]
                              if phase == "destroyed" else []),
                "crew_ko": [], "fire": False,
                "ammo_rack_death": False, "events": list(events or ())}

        self.assertTrue(state._apply_reported_health(player, {
            "reported_health": 500,
            "reported_critical": critical(10.0)}))
        self.assertEqual(1, len(state.pending_events))
        state.pending_events[:] = []

        self.assertTrue(state._apply_reported_health(player, {
            "reported_health": 500,
            "reported_critical": critical(25.0)}))
        self.assertEqual([], state.pending_events)
        self.assertEqual(25.0, player.critical["devices"][0]["hp"])

        transition = [{"kind": "device", "name": "engineHealth",
                       "state": "critical", "old_state": "destroyed",
                       "cause": "repair"}]
        self.assertTrue(state._apply_reported_health(player, {
            "reported_health": 500,
            "reported_critical": critical(
                40.0, phase="critical", events=transition)}))
        self.assertEqual(1, len(state.pending_events))
        self.assertEqual("critical", state.pending_events[0][
            "critical"]["events"][0]["state"])

    def test_0922_repair_reports_are_revisioned_and_stale_lineages_fail(self):
        state = BattleState(map_name="04_himmelsdorf", max_players=30)
        state.client_build = CLIENT_BUILD_0922
        state.phase = "battle"
        state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
        player = Player(
            1, None, ("127.0.0.1", 1), team=1,
            health=500, max_health=500)
        state.players = {1: player}

        def critical(hp):
            return {
                "devices": [{"name": "engineHealth", "hp": hp,
                             "max_hp": 100.0, "state": "destroyed"}],
                "destroyed": ["engineHealth"], "crew_ko": [],
                "fire": False, "ammo_rack_death": False, "events": []}

        self.assertTrue(state._apply_reported_health(player, {
            "reported_health": 500,
            "reported_critical": critical(10.0),
            "reported_critical_base_revision": 0,
            "reported_critical_seq": 1}))
        self.assertEqual((1, 0, 1), (
            player.critical_revision,
            player.critical_report_base_revision,
            player.critical_ack_seq))

        self.assertTrue(state._apply_reported_health(player, {
            "reported_health": 500,
            "reported_critical": critical(25.0),
            "reported_critical_base_revision": 0,
            "reported_critical_seq": 3}))
        self.assertEqual((2, 0, 3), (
            player.critical_revision,
            player.critical_report_base_revision,
            player.critical_ack_seq))

        commit = state._commit_external_player_critical(
            player, critical(0.0))
        self.assertEqual((3, 3, 0), (
            commit["critical_revision"],
            commit["critical_base_revision"],
            commit["critical_ack_seq"]))
        self.assertFalse(state._apply_reported_health(player, {
            "reported_health": 500,
            "reported_critical": critical(40.0),
            "reported_critical_base_revision": 0,
            "reported_critical_seq": 4}))
        self.assertEqual(0.0, player.critical["devices"][0]["hp"])

    def test_dead_player_pose_is_frozen_at_the_wreck_position(self):
        client = self.connect("Alpha")
        welcome = client.receive_type("welcome")
        client.receive_type("roster")
        self.state.phase = "battle"
        self.state.tick = int(PREBATTLE_SECONDS * TICK_HZ)
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
