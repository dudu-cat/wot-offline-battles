import json
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from lan_battle_server import BattleState, ClientHandler, Player, PROTOCOL_VERSION
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
        }

        self.assertTrue(state.report_bot_human_hit(7, {
            "attacker_bot": 16, "target": 8, "shot_seq": 1,
            "damage": 100,
        }))
        self.assertEqual("bot", victim.killer_kind)
        self.assertEqual(16, victim.killer_id)

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
                           "server_wait": 2, "water_guard": 2},
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
            "driver=moving:9,drive:8,avoid:3,blocked:6,recovery:7,arrived:5,wait:2",
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
        self.assertNotIn("bot_orders", connection.messages[1])

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

        at_cover = _states()
        at_cover[0] = dict(at_cover[0], x=-8.0, z=-12.0)
        hold = planner.build_orders(manifest, at_cover, players, 1.1)
        hold_order = next(value for value in hold["orders"] if value["id"] == 1)
        self.assertEqual("cover_hold", hold_order["combat_mode"])

        peek = planner.build_orders(manifest, at_cover, players, 3.0)
        peek_order = next(value for value in peek["orders"] if value["id"] == 1)
        self.assertEqual("cover_peek", peek_order["combat_mode"])
        self.assertTrue(peek_order["fire_allowed"])

        at_peek = _states()
        at_peek[0] = dict(at_peek[0], x=-2.0, z=-10.0)
        firing = planner.build_orders(manifest, at_peek, players, 3.1)
        firing_order = next(value for value in firing["orders"] if value["id"] == 1)
        self.assertEqual("cover_peek", firing_order["combat_mode"])
        self.assertTrue(firing_order["fire_allowed"])

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
