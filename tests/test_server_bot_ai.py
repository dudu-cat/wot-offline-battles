import json
import contextlib
import io
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from lan_battle_server import (
    BOT_ORDER_WIRE_FIELDS, BattleState, CLIENT_BUILD, ClientHandler, Player,
    PROTOCOL_VERSION, SERVER_SEND_BUFFER_BYTES,
)
from server_bot_ai import BotPlanner


ROOT = Path(__file__).resolve().parents[1]


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


def _artillery_manifest_and_states():
    manifest = [
        {
            "id": 1, "team": 1, "slot": 0, "health": 500,
            "profile": {
                "class_tag": "SPG", "dominant_role": "artillery",
                "desired_range": 520.0, "fire_range": 1200.0,
                "roles": {"artillery": 1.0},
                "shells": [{"index": 0, "kind": "HIGH_EXPLOSIVE",
                            "damage": 500, "penetration": 50}],
            },
            "route": {
                "id": "rear_arc", "waypoints": [
                    {"x": 0, "y": 0, "z": -100},
                    {"x": 35, "y": 0, "z": -80},
                    {"x": -45, "y": 0, "z": -55},
                    {"x": 0, "y": 0, "z": 500},
                ],
            },
        },
        {"id": 3, "team": 2, "slot": 0, "health": 1000,
         "profile": {"class_tag": "heavyTank"}},
    ]
    states = [
        {"id": 1, "team": 1, "alive": True,
         "x": 0, "y": 0, "z": -100},
        {"id": 3, "team": 2, "alive": True,
         "x": 0, "y": 0, "z": 650},
    ]
    return manifest, states


class ServerBotPlannerTests(unittest.TestCase):
    def test_bot_state_preserves_relayed_killer_identity(self):
        identity = {
            "id": 16, "team": 2, "slot": 0, "name": "Victim",
            "vehicle": "ussr:T-34", "max_health": 500,
        }
        state = BattleState._sanitize_bot_state({
            "health": 0, "alive": False, "killer_bot_id": 3,
        }, identity, None)

        self.assertEqual(3, state["killer_bot_id"])
        self.assertEqual("bot", state["killer_kind"])
        self.assertEqual(3, state["killer_id"])

    def test_human_killer_identity_survives_later_authority_state(self):
        identity = {
            "id": 16, "team": 2, "slot": 0, "name": "Victim",
            "vehicle": "ussr:T-34", "max_health": 500,
        }
        previous = BattleState._sanitize_bot_state({
            "health": 0, "alive": False,
            "killer_kind": "human", "killer_id": 7,
        }, identity, None)
        state = BattleState._sanitize_bot_state({
            "health": 0, "alive": False,
        }, identity, previous)

        self.assertEqual("human", state["killer_kind"])
        self.assertEqual(7, state["killer_id"])

    def test_lethal_human_bot_hit_sets_snapshot_killer(self):
        state = BattleState()
        state.phase = "battle"
        attacker = Player(7, None, ("127.0.0.1", 1), team=1, fire_seq=4)
        state.players[7] = attacker
        state.bot_states[16] = {
            "id": 16, "team": 2, "health": 100, "alive": True,
            "x": 0.0, "y": 0.0, "z": 0.0,
        }

        self.assertTrue(state.report_bot_hit(7, {
            "target": 16, "shot_seq": 4, "damage": 100,
        }))
        self.assertEqual("human", state.bot_states[16]["killer_kind"])
        self.assertEqual(7, state.bot_states[16]["killer_id"])

    def test_bot_killer_can_complete_an_earlier_unknown_health_death(self):
        state = BattleState()
        state.phase = "battle"
        state.bot_authority_id = 7
        authority = Player(7, None, ("127.0.0.1", 1), team=1)
        victim = Player(8, None, ("127.0.0.1", 2), team=2,
                        health=0, max_health=100, alive=False)
        state.players = {7: authority, 8: victim}
        state.bot_states[16] = {
            "id": 16, "team": 1, "health": 100, "alive": True,
            "fire_seq": 1,
        }

        self.assertTrue(state.report_bot_human_hit(7, {
            "attacker_bot": 16, "target": 8, "shot_seq": 1,
            "damage": 100,
        }))
        self.assertEqual("bot", victim.killer_kind)
        self.assertEqual(16, victim.killer_id)

    def test_human_shell_can_hit_bot_after_shooter_is_destroyed(self):
        state = BattleState()
        state.phase = "battle"
        attacker = Player(
            7, None, ("127.0.0.1", 1), team=1,
            fire_seq=3, health=0, alive=False,
        )
        state.players[7] = attacker
        state.bot_states[16] = {
            "id": 16, "team": 2, "health": 500, "alive": True,
            "x": 0.0, "y": 0.0, "z": 0.0,
        }

        self.assertTrue(state.report_bot_hit(7, {
            "target": 16, "shot_seq": 3, "damage": 120,
        }))
        self.assertEqual(380, state.bot_states[16]["health"])

    def test_human_shell_can_hit_human_after_shooter_is_destroyed(self):
        state = BattleState()
        state.phase = "battle"
        attacker = Player(
            7, None, ("127.0.0.1", 1), team=1,
            fire_seq=4, health=0, alive=False, x=0.0, z=0.0,
        )
        target = Player(
            8, None, ("127.0.0.1", 2), team=2,
            health=500, max_health=500, x=100.0, z=0.0,
        )
        state.players = {7: attacker, 8: target}

        self.assertTrue(state.report_hit(7, {
            "target": 8, "shot_seq": 4, "damage": 150,
        }))
        self.assertEqual(350, target.health)

    def test_bot_shell_can_land_after_bot_dies_but_not_before_it_fires(self):
        state = BattleState()
        state.phase = "battle"
        state.bot_authority_id = 7
        authority = Player(7, None, ("127.0.0.1", 1), team=1)
        first_target = Player(
            8, None, ("127.0.0.1", 2), team=2,
            health=500, max_health=500,
        )
        second_target = Player(
            9, None, ("127.0.0.1", 3), team=2,
            health=500, max_health=500,
        )
        state.players = {7: authority, 8: first_target, 9: second_target}
        state.bot_states[16] = {
            "id": 16, "team": 1, "health": 0, "alive": False,
            "fire_seq": 3,
        }

        self.assertTrue(state.report_bot_human_hit(7, {
            "attacker_bot": 16, "target": 8, "shot_seq": 3,
            "damage": 90,
        }))
        self.assertFalse(state.report_bot_human_hit(7, {
            "attacker_bot": 16, "target": 9, "shot_seq": 4,
            "damage": 90,
        }))
        self.assertEqual(410, first_target.health)
        self.assertEqual(500, second_target.health)

    def test_navigation_fallback_diagnostics_are_bounded_and_rate_limited(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1
        state.update_bot_observation(1, {
            "contacts": [],
            "navigation": {
                "graph": {"source": "baked", "cell_mm": 4000, "nodes": 16808},
                "total": {"safe_direct": 2, "safe_local": 3, "reactive": 200000},
                "active": {"safe_direct": 0, "safe_local": 1, "reactive": 2},
                "recovered": 4,
                "search": {"pending": 13, "completed": 4, "failed": 2,
                           "oldest_ms": 12345, "tick_age_ms": 17},
                "aim": {"alive": 29, "targeted": 15, "aligned": 4,
                        "traversing": 11, "limited": 7},
                "driver": {"moving": 9, "drive": 8, "avoid": 3,
                           "blocked": 6, "recovery": 7, "arrived": 5,
                           "server_wait": 2, "water_guard": 2, "full": 12,
                           "cruise": 10, "speed_pct": 73, "slow": 3},
                "safety": {"water_guard_total": 11, "water_guard_active": 2,
                           "edge_guard_total": 7, "edge_guard_active": 1,
                           "veto_water": 4, "veto_terrain": 3,
                           "veto_obstacle": 2, "veto_error": 1},
            },
        })

        self.assertEqual(100000, state.bot_navigation_stats["total"]["reactive"])
        self.assertEqual(1, state.bot_navigation_stats["active"]["safe_local"])
        self.assertEqual(4, state.bot_navigation_stats["recovered"])
        self.assertEqual(13, state.bot_navigation_stats["search"]["pending"])
        self.assertEqual(15, state.bot_navigation_stats["aim"]["targeted"])
        self.assertEqual(9, state.bot_navigation_stats["driver"]["moving"])
        self.assertEqual(12, state.bot_navigation_stats["driver"]["full"])
        self.assertEqual(73, state.bot_navigation_stats["driver"]["speed_pct"])
        self.assertEqual(11, state.bot_navigation_stats["safety"]["water_guard_total"])
        self.assertEqual("baked", state.bot_navigation_stats["graph"]["source"])
        self.assertEqual(16808, state.bot_navigation_stats["graph"]["nodes"])
        before = dict(state.bot_navigation_stats)
        state.update_bot_observation(2, {
            "navigation": {"total": {"reactive": 0}}
        })
        self.assertEqual(before, state.bot_navigation_stats)

        state.bot_manifest = _manifest()
        state.bot_states = {entry["id"]: entry for entry in _states()}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            state.tick_once(0.05)
            state.tick_once(0.05)
        text = output.getvalue()
        self.assertEqual(1, text.count("BOT AI reports="))
        self.assertIn(
            "nav_total=direct:2,local:3,reactive:100000 recovered:4", text
        )
        self.assertIn("nav=baked,cell:4000mm,nodes:16808", text)
        self.assertIn(
            "astar=pending:13,oldest:12345ms,tick_age:17ms,done:4,failed:2", text
        )
        self.assertIn(
            "aim=targeted:15,aligned:4,traversing:11,limited:7,alive:29", text
        )
        self.assertIn(
            "driver=moving:9,drive:8,avoid:3,blocked:6,recovery:7,arrived:5,wait:2,full:12,cruise:10,speed:73%,slow:3",
            text,
        )
        self.assertIn("safety=water:11/2,edge:7/1,veto:w4,t3,o2,e1", text)

    def test_downloadable_layout_imports_shared_cover_module(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = Path(temp_dir)
            shutil.copyfile(ROOT / "server_bot_ai.py", package_root / "server_bot_ai.py")
            cover_target = (
                package_root / "0.8.2" / "scripts" / "client" / "gui" / "mods" /
                "offhangar" / "bot_ai_cover.py"
            )
            cover_target.parent.mkdir(parents=True)
            shutil.copyfile(
                ROOT / "scripts" / "client" / "gui" / "mods" / "offhangar" /
                "bot_ai_cover.py",
                cover_target,
            )
            result = subprocess.run(
                [sys.executable, "-c", "from server_bot_ai import BotPlanner; print(BotPlanner.__name__)"],
                cwd=str(package_root),
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("BotPlanner", result.stdout.strip())

    def test_orders_are_stable_then_revision_only_changes_for_new_information(self):
        planner = BotPlanner()
        first = planner.build_orders(_manifest(), _states(), [], 0.0)
        second = planner.build_orders(_manifest(), _states(), [], 0.1)
        self.assertEqual(1, first["revision"])
        self.assertEqual(first, second)
        self.assertEqual({"route"}, {order["combat_mode"] for order in first["orders"]})

    def test_wire_orders_keep_only_authority_executable_fields_within_budget(self):
        rich_profile = {
            "class_tag": "mediumTank",
            "roles": {"brawler": 0.7, "support": 0.3},
            "shells": [
                {"kind": "ARMOR_PIERCING", "damage": 320,
                 "penetration": 180, "speed": 900}
                for unused_index in range(8)
            ],
        }
        orders = []
        for bot_id in range(1, 30):
            point = {"x": float(bot_id), "y": 0.0, "z": -80.0}
            orders.append({
                "id": bot_id, "team": 1 if bot_id <= 15 else 2,
                "target_id": None, "target_kind": None,
                "aim_position": point, "face_position": point,
                "move_position": point, "fire_allowed": False,
                "combat_mode": "route", "throttle_override": None,
                "desired_range": 220.0, "fire_range": 600.0,
                "route_id": "west_ridge", "route_index": 2,
                "route_anchor": point, "personality": {
                    "aggression": 0.5, "caution": 0.5, "patience": 0.5,
                },
                "profile": rich_profile, "shell_index": 0,
                "cover_id": "debug-only",
            })
        state = BattleState(map_name="04_himmelsdorf")
        state.bot_orders = {"revision": 7, "orders": orders}

        revision, body, byte_increment = state._wire_order_dispatch()

        expected_keys = set(BOT_ORDER_WIRE_FIELDS)
        self.assertEqual(7, revision)
        self.assertEqual(29, len(body))
        self.assertTrue(all(set(order) == expected_keys for order in body))
        self.assertTrue(all("profile" not in order for order in body))
        self.assertEqual(
            len(b',"bot_orders":') + len(json.dumps(
                body, separators=(",", ":")).encode("utf-8")),
            byte_increment,
        )
        rich_bytes = len(json.dumps(
            orders, separators=(",", ":")).encode("utf-8"))
        self.assertLess(byte_increment, 16 * 1024)
        self.assertLess(byte_increment * 2, rich_bytes)

    def test_only_authority_receives_compact_order_body(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        rich_order = {
            "id": 1, "team": 1, "target_id": None,
            "aim_position": {"x": 0.0, "y": 0.0, "z": 1.0},
            "face_position": {"x": 0.0, "y": 0.0, "z": 1.0},
            "move_position": {"x": 0.0, "y": 0.0, "z": 1.0},
            "fire_allowed": False, "combat_mode": "route",
            "throttle_override": None, "desired_range": 220.0,
            "fire_range": 600.0, "route_id": "middle",
            "route_index": 0,
            "route_anchor": {"x": 0.0, "y": 0.0, "z": 0.0},
            "personality": {"aggression": 0.5},
            "profile": {"shells": [{"damage": 100}]},
            "shell_index": 0,
        }
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        first_connection = CaptureConnection()
        second_connection = CaptureConnection()
        state.players[1] = Player(1, first_connection, ("127.0.0.1", 1))
        state.players[2] = Player(2, second_connection, ("127.0.0.1", 2))
        state.bot_authority_id = 1
        state.bot_planner.build_orders = lambda manifest, states, players, now: {
            "revision": 3, "orders": [rich_order],
        }

        metrics = state.tick_once(0.05)

        authority_snapshot = first_connection.messages[-1]
        replica_snapshot = second_connection.messages[-1]
        self.assertIn("bot_orders", authority_snapshot)
        self.assertNotIn("bot_orders", replica_snapshot)
        self.assertEqual(
            set(BOT_ORDER_WIRE_FIELDS).intersection(rich_order),
            set(authority_snapshot["bot_orders"][0]),
        )
        self.assertNotIn("profile", authority_snapshot["bot_orders"][0])
        authority_base = dict(authority_snapshot)
        authority_base.pop("bot_orders")

        def wire_size(message):
            return len((json.dumps(
                message, separators=(",", ":")) + "\n").encode("utf-8"))

        self.assertEqual(2, metrics["snapshot_messages"])
        self.assertEqual(1, metrics["order_attachments"])
        self.assertEqual(
            wire_size(authority_base) + wire_size(replica_snapshot),
            metrics["snapshot_base_bytes"],
        )
        self.assertEqual(
            wire_size(authority_snapshot) - wire_size(authority_base),
            metrics["snapshot_order_bytes"],
        )

    def test_promoted_authority_ack_is_reset_and_latest_body_is_sent(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        order = {
            "id": 1, "move_position": {"x": 1.0, "y": 0.0, "z": 2.0},
            "fire_allowed": False, "combat_mode": "route",
            "fire_range": 500.0, "route_id": "middle", "route_index": 0,
            "route_anchor": {"x": 0.0, "y": 0.0, "z": 0.0},
            "shell_index": 0,
        }
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        first = Player(1, CaptureConnection(), ("127.0.0.1", 1))
        second_connection = CaptureConnection()
        second = Player(2, second_connection, ("127.0.0.1", 2))
        state.players = {1: first, 2: second}
        state.bot_authority_id = 1
        state.bot_planner.build_orders = lambda manifest, states, players, now: {
            "revision": 9, "orders": [order],
        }
        first.bot_order_revision_ack = 9
        second.bot_order_revision_ack = 9
        second.bot_order_revision_sent = 9

        removed, reset = state.remove_player(1, expected_player=first)

        self.assertIs(removed, first)
        self.assertFalse(reset)
        self.assertEqual(2, state.bot_authority_id)
        self.assertEqual(-1, second.bot_order_revision_ack)
        self.assertEqual(-1, second.bot_order_revision_sent)
        state.tick_once(0.05)
        snapshot = next(
            message for message in reversed(second_connection.messages)
            if message.get("type") == "snapshot"
        )
        self.assertEqual(9, snapshot["bot_order_revision"])
        self.assertEqual([1], [value["id"] for value in snapshot["bot_orders"]])

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
        state.bot_authority_id = 1

        state.tick_once(0.05)
        state.tick_once(0.05)

        self.assertIn("bot_orders", connection.messages[0])
        self.assertNotIn("bot_orders", connection.messages[1])

    def test_health_ack_event_is_sent_before_its_canonical_snapshot(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        connection = CaptureConnection()
        player = Player(
            1, connection, ("127.0.0.1", 0), health=880, max_health=880,
        )
        state.players[1] = player
        state._apply_reported_health(player, 800)

        state.tick_once(0.05)

        self.assertEqual(["events", "snapshot"], [
            message["type"] for message in connection.messages
        ])
        self.assertEqual("client_simulation", connection.messages[0]["events"][0]["source"])
        self.assertEqual(state.round_id, connection.messages[0]["round_id"])
        self.assertEqual(800, connection.messages[1]["players"][0]["health"])
        self.assertEqual(state.round_id, connection.messages[1]["round_id"])

    def test_room_reset_cannot_deliver_old_round_events_to_reused_player_id(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.players[1] = Player(
            1, None, ("127.0.0.1", 0), health=0, max_health=880,
            alive=False,
        )
        state.pending_events = [{
            "kind": "health", "target": 1, "damage": 880,
            "health": 0, "dead": True, "source": "old_round",
        }]

        removed, reset = state.remove_player(1)

        self.assertIsNotNone(removed)
        self.assertTrue(reset)
        self.assertEqual([], state.pending_events)
        self.assertEqual(2, state.round_id)

        connection = CaptureConnection()
        state.players[1] = Player(
            1, connection, ("127.0.0.1", 0), health=880, max_health=880,
        )
        start, error = state.request_start(1, "04_himmelsdorf")
        self.assertIsNone(error)
        self.assertEqual(2, start["round_id"])
        state.tick_once(0.05)

        events = [event for message in connection.messages
                  if message.get("type") == "events"
                  for event in message.get("events", [])]
        self.assertFalse(any(event.get("kind") == "health" for event in events))
        snapshot = next(message for message in connection.messages
                        if message.get("type") == "snapshot")
        self.assertEqual(880, snapshot["players"][0]["health"])

    def test_inflight_old_round_dispatch_cannot_target_or_remove_reused_id(self):
        import lan_battle_server as server_module

        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        old_connection = CaptureConnection()
        old_player = Player(
            1, old_connection, ("127.0.0.1", 1),
            health=0, max_health=880, alive=False,
        )
        state.players[1] = old_player
        state.pending_events = [{
            "kind": "health", "target": 1, "damage": 880,
            "health": 0, "dead": True, "source": "old_round",
        }]
        new_connection = CaptureConnection()
        swapped = []
        original_log = server_module._server_log

        def swap_round_after_capture(message):
            if swapped or not message.startswith("HEALTH target=1"):
                return
            removed, reset = state.remove_player(
                1, expected_player=old_player)
            self.assertIs(removed, old_player)
            self.assertTrue(reset)
            new_player = Player(
                1, new_connection, ("127.0.0.1", 2),
                health=880, max_health=880,
            )
            state.players[1] = new_player
            swapped.append(new_player)

        server_module._server_log = swap_round_after_capture
        try:
            state.tick_once(0.05)
        finally:
            server_module._server_log = original_log

        self.assertEqual(1, len(swapped))
        self.assertIs(swapped[0], state.players.get(1))
        self.assertEqual(2, state.round_id)
        self.assertFalse(any(
            message.get("type") in ("events", "snapshot")
            for message in new_connection.messages
        ))
        self.assertTrue(all(
            message.get("round_id") == 2
            for message in new_connection.messages
        ))

    def test_old_handler_cannot_dispatch_input_or_start_to_reused_player_id(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.round_id = 2
        old_player = Player(
            1, None, ("127.0.0.1", 1), x=1.0, z=1.0,
        )
        new_player = Player(
            1, None, ("127.0.0.1", 2), x=2.0, z=2.0,
        )
        state.players[1] = new_player
        server = type("GameServer", (), {"state": state})()

        self.assertFalse(ClientHandler._dispatch_player_message(
            server, old_player, {
                "type": "input", "x": 99.0, "z": 98.0,
                "reported_health": 0,
            },
        ))
        self.assertFalse(ClientHandler._dispatch_player_message(
            server, old_player, {
                "type": "start_battle", "map": "04_himmelsdorf",
            },
        ))

        self.assertEqual((2.0, 2.0), (new_player.x, new_player.z))
        self.assertEqual(1000, new_player.health)
        self.assertEqual("waiting", state.phase)
        self.assertEqual([], state.pending_events)
        self.assertEqual(2, state.round_id)

    def test_lobby_broadcast_is_atomic_with_battle_start(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf")
        connection = CaptureConnection()
        state.players[1] = Player(1, connection, ("127.0.0.1", 1))
        lobby_entered = threading.Event()
        release_lobby = threading.Event()
        start_finished = threading.Event()
        original_lobby_message = state.lobby_message

        def paused_lobby_message():
            lobby_entered.set()
            release_lobby.wait(2.0)
            return original_lobby_message()

        def start_battle():
            start_message, error = state.request_start(1, "04_himmelsdorf")
            if error is None:
                state.broadcast(start_message)
            start_finished.set()

        state.lobby_message = paused_lobby_message
        lobby_thread = threading.Thread(target=state.broadcast_lobby)
        start_thread = threading.Thread(target=start_battle)
        try:
            lobby_thread.start()
            self.assertTrue(lobby_entered.wait(1.0))
            start_thread.start()
            time.sleep(0.02)
            self.assertFalse(start_finished.is_set())
            release_lobby.set()
            lobby_thread.join(1.0)
            start_thread.join(1.0)
        finally:
            release_lobby.set()
            state.lobby_message = original_lobby_message

        self.assertFalse(lobby_thread.is_alive())
        self.assertFalse(start_thread.is_alive())
        self.assertEqual(["roster", "battle_start"], [
            message["type"] for message in connection.messages
        ])
        self.assertEqual("waiting", connection.messages[0]["phase"])
        self.assertEqual(1, connection.messages[1]["round_id"])

    def test_lobby_broadcast_republishes_after_send_failure(self):
        class CaptureConnection(object):
            def __init__(self, fail=False):
                self.fail = fail
                self.messages = []

            def sendall(self, payload):
                if self.fail:
                    raise OSError("closed")
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf")
        good_connection = CaptureConnection()
        state.players[1] = Player(
            1, good_connection, ("127.0.0.1", 1), name="Good")
        state.players[2] = Player(
            2, CaptureConnection(fail=True), ("127.0.0.1", 2), name="Gone")

        state.broadcast_lobby()

        self.assertEqual([1], list(state.players))
        self.assertEqual([2, 1], [
            len(message["players"]) for message in good_connection.messages
        ])
        self.assertEqual("Good", good_connection.messages[-1]["players"][0]["name"])

    def test_late_join_bootstrap_precedes_snapshot(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []
                self.lock = threading.Lock()

            def sendall(self, payload):
                message = json.loads(payload.decode("utf-8"))
                with self.lock:
                    self.messages.append(message)

        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        connection = CaptureConnection()

        player, error, current_battle = state.add_player(
            connection, ("127.0.0.1", 1), {
                "name": "Late", "vehicle": "ussr:T-34", "max_health": 880,
            })
        self.assertIsNone(error)
        self.assertIsNotNone(current_battle)
        state.tick_once(0.05)

        deadline = time.time() + 1.0
        while time.time() < deadline:
            with connection.lock:
                kinds = [message["type"] for message in connection.messages]
            if "snapshot" in kinds:
                break
            time.sleep(0.01)
        self.assertEqual("welcome", kinds[0])
        self.assertLess(kinds.index("roster"), kinds.index("battle_start"))
        self.assertLess(kinds.index("battle_start"), kinds.index("snapshot"))
        state.remove_player(player.player_id, expected_player=player)

    def test_immediate_start_cannot_overtake_welcome(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []
                self.lock = threading.Lock()

            def sendall(self, payload):
                message = json.loads(payload.decode("utf-8"))
                with self.lock:
                    self.messages.append(message)

        state = BattleState(map_name="04_himmelsdorf")
        connection = CaptureConnection()
        player, error, current_battle = state.add_player(
            connection, ("127.0.0.1", 1), {
                "name": "Starter", "vehicle": "ussr:T-34", "max_health": 880,
            })
        self.assertIsNone(error)
        self.assertIsNone(current_battle)
        start_message, error = state.request_start(1, "04_himmelsdorf")
        self.assertIsNone(error)
        state.broadcast(start_message)

        deadline = time.time() + 1.0
        while time.time() < deadline:
            with connection.lock:
                kinds = [message["type"] for message in connection.messages]
            if "battle_start" in kinds:
                break
            time.sleep(0.01)
        self.assertEqual("welcome", kinds[0])
        self.assertLess(kinds.index("roster"), kinds.index("battle_start"))
        state.remove_player(player.player_id, expected_player=player)

    def test_unacknowledged_orders_are_retried_then_stop_after_ack(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        connection = CaptureConnection()
        player = Player(1, connection, ("127.0.0.1", 0))
        state.players[1] = player
        state.bot_authority_id = 1

        state.tick_once(0.05)
        player.bot_order_sent_at -= 1.0
        state.tick_once(0.05)
        revision = state.bot_orders["revision"]
        self.assertIn("bot_orders", connection.messages[-1])

        self.assertTrue(state.acknowledge_bot_orders(1, {"revision": revision}))
        state.tick_once(0.05)
        self.assertNotIn("bot_orders", connection.messages[-1])

    def test_resync_request_forces_current_order_body(self):
        class CaptureConnection(object):
            def __init__(self):
                self.messages = []

            def sendall(self, payload):
                self.messages.append(json.loads(payload.decode("utf-8")))

        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        connection = CaptureConnection()
        player = Player(1, connection, ("127.0.0.1", 0))
        state.players[1] = player
        state.bot_authority_id = 1
        state.tick_once(0.05)
        revision = state.bot_orders["revision"]
        state.acknowledge_bot_orders(1, {"revision": revision})

        self.assertTrue(state.request_bot_order_resync(1))
        state.tick_once(0.05)

        self.assertIn("bot_orders", connection.messages[-1])
        self.assertEqual(revision, connection.messages[-1]["bot_order_revision"])

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
        targeted = [order for order in team_one if order["target_id"] == 99]
        self.assertEqual(1, len(targeted))
        self.assertEqual(
            {"x": 40.0, "y": 0.0, "z": 30.0},
            targeted[0]["aim_position"],
        )
        self.assertTrue(targeted[0]["fire_allowed"])

    def test_distant_contact_does_not_pull_every_bot_off_its_route(self):
        planner = BotPlanner()
        manifest = _manifest()
        states = _states()
        states[0] = dict(states[0], x=0, z=0)
        states[1] = dict(states[1], x=-650, z=0)
        players = [{"id": 99, "team": 2, "alive": True}]
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True,
            "x": 120, "y": 0, "z": 0, "health": 1000,
            "max_health": 1000,
        }], planner.known_targets(states, players), 1.0)

        result = planner.build_orders(manifest, states, players, 1.0)
        orders = {order["id"]: order for order in result["orders"]}

        self.assertEqual(99, orders[1]["target_id"])
        self.assertIsNone(orders[2]["target_id"])
        self.assertEqual("route", orders[2]["combat_mode"])

    def test_visible_local_threat_interrupts_route_within_340_metres(self):
        planner = BotPlanner()
        manifest = _manifest()
        states = _states()
        states[0] = dict(states[0], x=0, z=0)
        states[1] = dict(states[1], x=-500, z=0)
        players = [{"id": 99, "team": 2, "alive": True}]
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True,
            "x": 300, "y": 0, "z": 0, "health": 1000,
            "max_health": 1000,
        }], planner.known_targets(states, players), 1.0)

        orders = {order["id"]: order for order in planner.build_orders(
            manifest, states, players, 1.0)["orders"]}

        self.assertEqual(99, orders[1]["target_id"])
        self.assertIsNone(orders[2]["target_id"])

    def test_team_spot_without_local_los_does_not_interrupt_routes(self):
        planner = BotPlanner()
        players = [{"id": 99, "team": 2, "alive": True}]
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True,
            "shootable_by_bot_ids": [],
            "x": 0, "y": 0, "z": 40, "health": 1000,
            "max_health": 1000,
        }], planner.known_targets(_states(), players), 1.0)

        orders = [order for order in planner.build_orders(
            _manifest(), _states(), players, 1.0)["orders"]
                  if order["team"] == 1]

        self.assertEqual({"route"}, {order["combat_mode"] for order in orders})
        self.assertTrue(all(order["target_id"] is None for order in orders))
        self.assertTrue(all(not order["fire_allowed"] for order in orders))

    def test_artillery_holds_rear_position_and_fires_a_client_proved_arc(self):
        planner = BotPlanner()
        manifest, states = _artillery_manifest_and_states()
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "bot", "target_id": 3,
            "target_team": 2, "visible": True,
            "shootable_by_bot_ids": [1],
            "x": 0, "y": 0, "z": 650, "health": 1000,
            "max_health": 1000, "class_tag": "heavyTank",
        }], planner.known_targets(states, []), 1.0)

        order = next(value for value in planner.build_orders(
            manifest, states, [], 1.0)["orders"] if value["id"] == 1)

        self.assertEqual("artillery_fire", order["combat_mode"])
        self.assertEqual(3, order["target_id"])
        self.assertTrue(order["fire_allowed"])
        self.assertEqual(
            {"x": 0.0, "y": 0.0, "z": -100.0},
            order["move_position"],
        )
        self.assertEqual(0.0, order["throttle_override"])

    def test_artillery_live_hold_pose_does_not_churn_order_revision(self):
        planner = BotPlanner()
        manifest, states = _artillery_manifest_and_states()

        first = planner.build_orders(manifest, states, [], 1.0)
        second = first
        moved_states = states
        for step in range(1, 31):
            moved_states = [
                dict(states[0], x=step * 0.1, z=-100.0 + step * 0.05),
                states[1],
            ]
            second = planner.build_orders(
                manifest, moved_states, [], 1.0 + step * 0.01)
        first_order = next(
            order for order in first["orders"] if order["id"] == 1)
        second_order = next(
            order for order in second["orders"] if order["id"] == 1)

        self.assertEqual("artillery_hold", first_order["combat_mode"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertNotEqual(
            first_order["route_anchor"], second_order["route_anchor"])

        planner._artillery_states[1]["position"] = {
            "x": 35.0, "y": 0.0, "z": -80.0,
        }
        relocated = planner.build_orders(manifest, moved_states, [], 1.4)
        relocated_order = next(
            order for order in relocated["orders"] if order["id"] == 1)
        self.assertGreater(relocated["revision"], second["revision"])
        self.assertEqual("artillery_relocate", relocated_order["combat_mode"])
        self.assertEqual(
            {"x": 35.0, "y": 0.0, "z": -80.0},
            relocated_order["move_position"],
        )

    def test_artillery_fire_live_pose_is_not_a_semantic_order_change(self):
        planner = BotPlanner()
        manifest, states = _artillery_manifest_and_states()
        report = {
            "observing_team": 1, "target_kind": "bot", "target_id": 3,
            "target_team": 2, "visible": True,
            "shootable_by_bot_ids": [1],
            "x": 0, "y": 0, "z": 650, "health": 1000,
            "max_health": 1000, "class_tag": "heavyTank",
        }
        planner.report_contacts(
            [report], planner.known_targets(states, []), 1.0)
        first = planner.build_orders(manifest, states, [], 1.0)
        moved_states = [dict(states[0], x=0.2, z=-99.9), states[1]]
        second = planner.build_orders(manifest, moved_states, [], 1.1)
        first_order = next(
            order for order in first["orders"] if order["id"] == 1)
        second_order = next(
            order for order in second["orders"] if order["id"] == 1)

        self.assertEqual("artillery_fire", second_order["combat_mode"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertNotEqual(
            first_order["move_position"], second_order["move_position"])

        expired = planner.build_orders(manifest, moved_states, [], 10.1)
        expired_order = next(
            order for order in expired["orders"] if order["id"] == 1)
        self.assertGreater(expired["revision"], second["revision"])
        self.assertEqual("artillery_hold", expired_order["combat_mode"])

    def test_blocked_artillery_relocates_only_between_rear_route_points(self):
        planner = BotPlanner()
        manifest, states = _artillery_manifest_and_states()
        report = {
            "observing_team": 1, "target_kind": "bot", "target_id": 3,
            "target_team": 2, "visible": True,
            "shootable_by_bot_ids": [],
            "x": 0, "y": 0, "z": 650, "health": 1000,
            "max_health": 1000, "class_tag": "heavyTank",
        }
        planner.report_contacts([report], planner.known_targets(states, []), 1.0)
        held = next(value for value in planner.build_orders(
            manifest, states, [], 1.0)["orders"] if value["id"] == 1)
        self.assertEqual("artillery_hold", held["combat_mode"])
        self.assertIsNone(held["target_id"])

        planner.report_contacts([report], planner.known_targets(states, []), 12.5)
        moved_states = [dict(states[0], x=4, z=-96), states[1]]
        relocated = next(value for value in planner.build_orders(
            manifest, moved_states, [], 13.1)["orders"] if value["id"] == 1)

        self.assertEqual("artillery_relocate", relocated["combat_mode"])
        self.assertIn(relocated["move_position"], [
            {"x": 35.0, "y": 0.0, "z": -80.0},
            {"x": -45.0, "y": 0.0, "z": -55.0},
        ])
        self.assertNotEqual(
            {"x": 0.0, "y": 0.0, "z": 500.0},
            relocated["move_position"],
        )

    def test_local_los_and_focus_quota_hard_limit_target_assignments(self):
        planner = BotPlanner()
        manifest = [
            {"id": bot_id, "team": 1, "slot": bot_id, "health": 1000}
            for bot_id in range(1, 6)
        ] + [{"id": 9, "team": 2, "slot": 0, "health": 1000}]
        states = [
            {"id": bot_id, "team": 1, "alive": True, "x": bot_id * 5, "z": 0}
            for bot_id in range(1, 6)
        ] + [{"id": 9, "team": 2, "alive": True, "x": 0, "z": 80}]
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "bot", "target_id": 9,
            "target_team": 2, "visible": True,
            "shootable_by_bot_ids": [1, 2, 3, 4, 5],
            "x": 0, "y": 0, "z": 80, "health": 1000,
            "max_health": 1000, "class_tag": "heavyTank",
        }], planner.known_targets(states, []), 1.0)

        orders = [order for order in planner.build_orders(
            manifest, states, [], 1.0)["orders"] if order["team"] == 1]
        assigned = [order for order in orders if order["target_id"] == 9]

        self.assertEqual(2, len(assigned))
        self.assertTrue(all(order["fire_allowed"] for order in assigned))
        self.assertTrue(all(order["target_id"] is None
                            for order in orders if order not in assigned))

    def test_similar_targets_do_not_flip_on_submetre_position_jitter(self):
        planner = BotPlanner()
        bot = {
            "id": 1, "team": 1, "slot": 0, "profile": {}, "route": {},
            "state": {"x": -0.2, "z": 0.0},
        }
        contacts = [
            {"id": 10, "target_kind": "human", "visible": True,
             "position": {"x": -40.0, "z": 100.0}, "health": 1000,
             "max_health": 1000, "shootable_by_bot_ids": [1]},
            {"id": 11, "target_kind": "human", "visible": True,
             "position": {"x": 40.0, "z": 100.0}, "health": 1000,
             "max_health": 1000, "shootable_by_bot_ids": [1]},
        ]

        first = planner._assign_targets([bot], contacts, 1.0)[1]["id"]
        bot["state"]["x"] = 0.2
        second = planner._assign_targets([bot], contacts, 3.1)[1]["id"]

        self.assertEqual(first, second)

        contacts[0 if first == 10 else 1]["shootable_by_bot_ids"] = []
        switched = planner._assign_targets([bot], contacts, 3.2)[1]["id"]
        self.assertNotEqual(first, switched)

    def test_point_blank_threat_preempts_a_distant_target_lease(self):
        planner = BotPlanner()
        bot = {
            "id": 1, "team": 1, "slot": 0, "profile": {}, "route": {},
            "state": {"x": 0.0, "z": 0.0},
        }
        distant = {
            "id": 10, "target_kind": "bot", "visible": True,
            "position": {"x": 0.0, "z": 220.0}, "health": 100,
            "max_health": 1000, "shootable_by_bot_ids": [1],
        }
        close = {
            "id": 11, "target_kind": "human", "visible": True,
            "position": {"x": 0.0, "z": -20.0}, "health": 1000,
            "max_health": 1000, "shootable_by_bot_ids": [1],
        }

        first = planner._assign_targets([bot], [distant], 1.0)[1]["id"]
        switched = planner._assign_targets([bot], [distant, close], 1.1)[1]["id"]

        self.assertEqual(10, first)
        self.assertEqual(11, switched)

    def test_close_visible_threat_holds_to_fight_without_cover(self):
        planner = BotPlanner()
        manifest = _manifest()
        players = [{"id": 99, "team": 2, "alive": True}]
        known = planner.known_targets(_states(), players)
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True,
            "x": 0, "y": 0, "z": 80, "health": 1000,
            "max_health": 1000,
        }], known, 1.0)

        order = next(order for order in planner.build_orders(
            manifest, _states(), players, 1.0)["orders"] if order["id"] == 1)

        self.assertEqual("engage", order["combat_mode"])
        self.assertEqual(0.0, order["throttle_override"])

    def test_close_visible_threat_makes_ranged_bot_withdraw_while_firing(self):
        planner = BotPlanner()
        manifest = _manifest()
        manifest[0]["profile"] = {
            "dominant_role": "support", "desired_range": 200.0,
            "fire_range": 500.0, "roles": {"support": 1.0},
        }
        manifest[0]["route"] = {
            "id": "support_lane", "waypoints": [
                {"x": 0, "y": 0, "z": -120},
                {"x": 0, "y": 0, "z": 300},
            ],
        }
        states = _states()
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "bot", "target_id": 3,
            "target_team": 2, "visible": True,
            "shootable_by_bot_ids": [1],
            "x": 0, "y": 0, "z": 20, "health": 1000,
            "max_health": 1000,
        }], planner.known_targets(states, []), 1.0)

        order = next(value for value in planner.build_orders(
            manifest, states, [], 1.0)["orders"] if value["id"] == 1)

        self.assertEqual("withdraw", order["combat_mode"])
        self.assertEqual(3, order["target_id"])
        self.assertTrue(order["fire_allowed"])
        self.assertIsNone(order["throttle_override"])

    def test_live_visible_pose_does_not_churn_structural_order_revision(self):
        planner = BotPlanner()
        manifest = _manifest()
        players = [{"id": 99, "team": 2, "alive": True}]
        known = planner.known_targets(_states(), players)
        base = {
            "observing_team": 1, "target_kind": "human", "target_id": 99,
            "target_team": 2, "visible": True, "y": 0,
            "health": 1000, "max_health": 1000,
        }
        planner.report_contacts([dict(base, x=0, z=300)], known, 1.0)
        first = planner.build_orders(manifest, _states(), players, 1.0)
        planner.report_contacts([dict(base, x=3, z=300)], known, 1.1)
        second = planner.build_orders(manifest, _states(), players, 1.1)

        self.assertEqual(first["revision"], second["revision"])
        first_order = next(order for order in first["orders"] if order["id"] == 1)
        second_order = next(order for order in second["orders"] if order["id"] == 1)
        self.assertNotEqual(first_order["aim_position"], second_order["aim_position"])

    def test_snapshot_send_failure_is_logged_and_resets_the_room(self):
        class BrokenConnection(object):
            def sendall(self, payload):
                raise TimeoutError("simulated slow receiver")

        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.players[1] = Player(1, BrokenConnection(), ("127.0.0.1", 0))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            state.tick_once(0.05)

        self.assertFalse(state.players)
        self.assertEqual("waiting", state.phase)
        self.assertIn("SEND DROP id=1", output.getvalue())
        self.assertIn("ROOM RESET", output.getvalue())

    def test_async_sender_coalesces_snapshots_and_preserves_reliable_order(self):
        class PausedConnection(object):
            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()
                self.sent = []

            def sendall(self, payload):
                self.entered.set()
                if not self.release.wait(2.0):
                    raise TimeoutError("test receiver remained paused")
                self.sent.append(json.loads(payload.decode("utf-8")))

        connection = PausedConnection()
        player = Player(1, connection, ("127.0.0.1", 0))
        player.start_sender()
        try:
            self.assertTrue(player.send({
                "type": "snapshot", "server_tick": 1,
                "bot_order_revision": 7,
                "bot_orders": [{"id": 1, "combat_mode": "route"}],
            }))
            self.assertTrue(connection.entered.wait(1.0))
            for tick in range(2, 11):
                self.assertTrue(player.send({
                    "type": "snapshot", "server_tick": tick,
                }))
            self.assertTrue(player.send({"type": "pong", "seq": 7}))
            self.assertTrue(player.send({
                "type": "events", "events": [{"kind": "bot_hit"}],
            }))
            for tick in range(11, 51):
                self.assertTrue(player.send({
                    "type": "snapshot", "server_tick": tick,
                }))
            with player.outbound_lock:
                self.assertEqual(1, len(player.outbound_latest))
                self.assertEqual(2, len(player.outbound_reliable))
                self.assertEqual(48, player.outbound_coalesced)
            self.assertTrue(player.connected)
            blocked = player.outbound_diagnostics()
            self.assertEqual("snapshot_orders", blocked["inflight_type"])
            self.assertGreaterEqual(blocked["inflight_age_ms"], 0.0)
            self.assertEqual({}, blocked["completed_messages"])

            connection.release.set()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                with player.outbound_lock:
                    inflight_type = player.outbound_inflight_type
                if len(connection.sent) >= 4 and not inflight_type:
                    break
                time.sleep(0.01)
            self.assertEqual(["snapshot", "pong", "events", "snapshot"], [
                message["type"] for message in connection.sent
            ])
            self.assertEqual([1, 50], [
                message["server_tick"] for message in connection.sent
                if message["type"] == "snapshot"
            ])
            completed = player.outbound_diagnostics()
            self.assertEqual("", completed["inflight_type"])
            self.assertGreater(completed["send_max_ms"], 0.0)
            self.assertEqual({
                "events": 1, "pong": 1, "snapshot": 1,
                "snapshot_orders": 1,
            }, completed["completed_messages"])
            self.assertEqual(
                set(completed["completed_messages"]),
                set(completed["completed_bytes"]),
            )
            self.assertTrue(all(
                byte_count > 0
                for byte_count in completed["completed_bytes"].values()
            ))
            self.assertTrue(player.connected)
        finally:
            connection.release.set()
            player.stop_sender()

    def test_visible_enemy_overrides_route_until_contact_expires(self):
        planner = BotPlanner()
        manifest = _manifest()
        manifest[0]["profile"] = {
            "dominant_role": "brawler", "desired_range": 40.0,
            "fire_range": 500.0, "roles": {"brawler": 1.0},
        }
        manifest[0]["route"] = {
            "id": "enemy_base", "waypoints": [
                {"x": 0, "y": 0, "z": -20},
                {"x": 0, "y": 0, "z": 300},
            ],
        }
        before = planner.build_orders(manifest, _states(), [], 0.0)
        before_order = next(order for order in before["orders"] if order["id"] == 1)
        self.assertIsNone(before_order["target_id"])
        self.assertEqual("route", before_order["combat_mode"])

        planner.report_contacts([{
            "observing_team": 1, "target_kind": "bot", "target_id": 3,
            "target_team": 2, "visible": True,
            "x": 0, "y": 0, "z": 20, "health": 1000,
            "max_health": 1000,
        }], planner.known_targets(_states(), []), 1.0)
        engaged = planner.build_orders(manifest, _states(), [], 1.0)
        engaged_order = next(order for order in engaged["orders"] if order["id"] == 1)
        self.assertEqual(3, engaged_order["target_id"])
        self.assertEqual({"x": 0.0, "y": 0.0, "z": 20.0}, engaged_order["aim_position"])
        self.assertTrue(engaged_order["fire_allowed"])
        self.assertNotEqual("route", engaged_order["combat_mode"])

        expired = planner.build_orders(manifest, _states(), [], 9.1)
        expired_order = next(order for order in expired["orders"] if order["id"] == 1)
        self.assertIsNone(expired_order["target_id"])
        self.assertEqual("route", expired_order["combat_mode"])

    def test_route_hold_metadata_does_not_pause_travel(self):
        planner = BotPlanner()
        manifest = _manifest()
        manifest[0]["route"] = {
            "id": "held_lane", "waypoints": [
                {"x": 0, "y": 0, "z": -40},
                {"x": 0, "y": 0, "z": -20, "hold": True},
                {"x": 0, "y": 0, "z": 200},
            ],
        }

        result = planner.build_orders(manifest, _states(), [], 1.0)
        order = next(value for value in result["orders"] if value["id"] == 1)

        self.assertEqual(2, order["route_index"])
        self.assertEqual({"x": 0.0, "y": 0.0, "z": 200.0}, order["move_position"])
        self.assertEqual("route", order["combat_mode"])
        self.assertIsNone(order["throttle_override"])

    def test_distant_mobile_bot_approaches_before_attempting_a_flank(self):
        planner = BotPlanner()
        manifest = _manifest()
        manifest[0]["profile"] = {
            "dominant_role": "flanker", "desired_range": 80.0,
            "fire_range": 500.0,
            "roles": {"flanker": 1.0, "support": 0.7},
        }
        states = _states()
        states[2] = dict(states[2], x=0, z=420)
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "bot", "target_id": 3,
            "target_team": 2, "visible": True,
            "x": 0, "y": 0, "z": 420, "health": 1000,
            "max_health": 1000,
        }], planner.known_targets(states, []), 1.0)

        result = planner.build_orders(manifest, states, [], 1.0)
        order = next(value for value in result["orders"] if value["id"] == 1)

        self.assertEqual("advance_contact", order["combat_mode"])
        self.assertEqual(order["aim_position"], order["move_position"])
        self.assertIsNone(order["throttle_override"])

    def test_debug_summary_exposes_contact_order_and_fire_chain(self):
        planner = BotPlanner()
        planner.report_contacts([{
            "observing_team": 1, "target_kind": "bot", "target_id": 3,
            "target_team": 2, "visible": True,
            "x": 0, "y": 0, "z": 20, "health": 1000,
            "max_health": 1000,
        }], planner.known_targets(_states(), []), 1.0)
        planner.build_orders(_manifest(), _states(), [], 1.0)

        summary = planner.debug_summary(1.0)

        self.assertEqual(1, summary["teams"][1]["visible"])
        self.assertGreater(summary["teams"][1]["targeted"], 0)
        self.assertGreater(summary["teams"][1]["fire"], 0)

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

    def test_deployed_bot_does_not_drive_back_to_base_route_anchor(self):
        planner = BotPlanner()
        manifest = _manifest()
        manifest[0]["route"] = {
            "id": "leave_spawn",
            "waypoints": [
                {"x": 0, "y": 0, "z": -40},
                {"x": 0, "y": 0, "z": 80},
            ],
        }
        states = _states()
        states[0] = dict(states[0], x=0, z=-20)

        result = planner.build_orders(manifest, states, [], 0.0)
        order = next(value for value in result["orders"] if value["id"] == 1)

        self.assertEqual(1, order["route_index"])
        self.assertEqual({"x": 0.0, "y": 0.0, "z": 80.0}, order["move_position"])
        self.assertEqual({"x": 0.0, "y": 0.0, "z": -20.0}, order["route_anchor"])

    def test_deployed_bot_skips_a_rear_facing_route_connector(self):
        planner = BotPlanner()
        manifest = _manifest()
        manifest[0]["route"] = {
            "id": "rear_connector",
            "waypoints": [
                {"x": 0, "y": 0, "z": -40},
                {"x": 0, "y": 0, "z": -65},
                {"x": 40, "y": 0, "z": 40},
            ],
        }
        states = _states()
        states[0] = dict(states[0], x=0, z=-20, yaw=0.0)

        result = planner.build_orders(manifest, states, [], 0.0)
        order = next(value for value in result["orders"] if value["id"] == 1)

        self.assertEqual(2, order["route_index"])
        self.assertEqual({"x": 40.0, "y": 0.0, "z": 40.0}, order["move_position"])
        self.assertEqual({"x": 0.0, "y": 0.0, "z": -20.0}, order["route_anchor"])

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
        self.assertTrue(approach_order["fire_allowed"])
        self.assertIsNone(approach_order["throttle_override"])

        at_cover = _states()
        at_cover[0] = dict(at_cover[0], x=-8.0, z=-12.0)
        hold = planner.build_orders(manifest, at_cover, players, 1.1)
        hold_order = next(value for value in hold["orders"] if value["id"] == 1)
        self.assertEqual("cover_hold", hold_order["combat_mode"])

        peek = planner.build_orders(manifest, at_cover, players, 3.0)
        peek_order = next(value for value in peek["orders"] if value["id"] == 1)
        self.assertEqual("cover_peek", peek_order["combat_mode"])
        self.assertTrue(peek_order["fire_allowed"])
        self.assertIsNone(peek_order["throttle_override"])

        at_peek = _states()
        at_peek[0] = dict(at_peek[0], x=-2.0, z=-10.0)
        firing = planner.build_orders(manifest, at_peek, players, 3.1)
        firing_order = next(value for value in firing["orders"] if value["id"] == 1)
        self.assertEqual("cover_peek", firing_order["combat_mode"])
        self.assertTrue(firing_order["fire_allowed"])
        self.assertEqual(0.0, firing_order["throttle_override"])

        returning = planner.build_orders(manifest, at_peek, players, 5.5)
        returning_order = next(value for value in returning["orders"] if value["id"] == 1)
        self.assertEqual("cover_return", returning_order["combat_mode"])
        self.assertTrue(returning_order["fire_allowed"])

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
                        "client_build": CLIENT_BUILD,
                        "name": "Alpha", "vehicle": "ussr:T-34",
                    }) + "\n").encode("utf-8"),
                    b"[]\n",
                    b'{"type":"ping","seq":9,"client_time":1.0}\n',
                    b'{"type":"leave"}\n',
                ]
                self.messages = []
                self.pong_sent = threading.Event()
                self.send_buffer = None

            def setsockopt(self, level, option, value):
                if level == socket.SOL_SOCKET and option == socket.SO_SNDBUF:
                    self.send_buffer = value

            def getsockopt(self, level, option):
                if level == socket.SOL_SOCKET and option == socket.SO_SNDBUF:
                    return self.send_buffer
                return 0

            def settimeout(self, *unused):
                pass

            def recv(self, unused_size):
                if self.chunks and self.chunks[0] == b'{"type":"leave"}\n':
                    self.pong_sent.wait(1.0)
                return self.chunks.pop(0) if self.chunks else b""

            def sendall(self, payload):
                message = json.loads(payload.decode("utf-8"))
                self.messages.append(message)
                if message.get("type") == "pong":
                    self.pong_sent.set()

            def close(self):
                pass

        connection = FakeConnection()
        state = BattleState(map_name="04_himmelsdorf")
        server = type("FakeServer", (), {
            "game_server": type("GameServer", (), {"state": state})(),
        })()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ClientHandler(connection, ("127.0.0.1", 12345), server)

        pong = [message for message in connection.messages
                if message.get("type") == "pong"]
        self.assertEqual(9, pong[0]["seq"])
        self.assertEqual(128 * 1024, SERVER_SEND_BUFFER_BYTES)
        self.assertEqual(SERVER_SEND_BUFFER_BYTES, connection.send_buffer)
        self.assertIn(
            "sndbuf=%dB requested=%dB" % (
                SERVER_SEND_BUFFER_BYTES, SERVER_SEND_BUFFER_BYTES),
            output.getvalue(),
        )

    def test_malformed_bot_collections_are_rejected_without_state_change(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1

        self.assertFalse(state.update_bot_manifest(1, {"bots": {"id": 1}}))
        self.assertFalse(state.update_bot_states(1, {"bots": {"id": 1}}))
        self.assertEqual([], state.bot_manifest)

    def test_bot_state_fire_sequence_creates_a_server_diagnostic_event(self):
        state = BattleState(map_name="04_himmelsdorf")
        state.phase = "battle"
        state.bot_authority_id = 1
        identity = state.bot_roster[0]
        self.assertTrue(state.update_bot_manifest(1, {"bots": [{
            "id": identity["id"], "team": identity["team"],
            "slot": identity["slot"], "max_health": 1000,
            "health": 1000, "fire_seq": 0,
        }]}))
        state.pending_events = []

        self.assertTrue(state.update_bot_states(1, {"bots": [{
            "id": identity["id"], "health": 1000, "alive": True,
            "x": 0, "y": 0, "z": 0, "fire_seq": 1,
            "shell_index": 2,
        }]}))

        events = [event for event in state.pending_events
                  if event.get("kind") == "bot_shot"]
        self.assertEqual(1, len(events))
        self.assertEqual(identity["id"], events[0]["attacker_bot"])
        self.assertEqual(2, events[0]["shell_index"])

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

    def test_same_pressured_route_renews_without_losing_waypoint_state(self):
        planner = BotPlanner()
        profile = {"roles": {"support": 0.9, "flanker": 0.8, "brawler": 0.1}}
        left = {"id": "left", "waypoints": [{"x": -80, "z": 0}]}
        right = {"id": "right", "waypoints": [{"x": 80, "z": 0}]}
        bots = [
            {"id": 1, "team": 1, "slot": 0, "profile": profile,
             "route": left, "state": {"x": -80, "z": 0}},
            {"id": 2, "team": 1, "slot": 1, "profile": profile,
             "route": left, "state": {"x": -70, "z": 0}},
            {"id": 3, "team": 1, "slot": 2, "profile": profile,
             "route": right, "state": {"x": 80, "z": 0}},
        ]
        contacts = [{
            "id": 99, "position": {"x": 80, "z": 20},
            "health": 1000, "max_health": 1000,
        }]

        planner._rebalance_routes(1, bots, contacts, 1.0)
        donor = next(bot_id for bot_id, assignment in planner._route_assignments.items()
                     if assignment["route"]["id"] == "right" and bot_id != 3)
        route_state = {"route_id": "right", "index": 1}
        planner._route_states[donor] = route_state

        planner._rebalance_routes(1, bots, contacts, 5.01)

        self.assertIs(route_state, planner._route_states[donor])
        self.assertGreater(planner._route_assignments[donor]["until"], 5.01)

if __name__ == "__main__":
    unittest.main()
