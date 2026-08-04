import unittest

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


class ServerBotPlannerTests(unittest.TestCase):
    def test_orders_are_stable_then_revision_only_changes_for_new_information(self):
        planner = BotPlanner()
        first = planner.build_orders(_manifest(), _states(), [], 0.0)
        second = planner.build_orders(_manifest(), _states(), [], 0.1)
        self.assertEqual(1, first["revision"])
        self.assertEqual(first, second)
        self.assertEqual({"advance"}, {order["combat_mode"] for order in first["orders"]})

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


if __name__ == "__main__":
    unittest.main()
