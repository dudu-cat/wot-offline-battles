import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_PATH = ROOT / "scripts/client/gui/mods/offhangar/bot_ai.py"
MAPS_PATH = ROOT / "scripts/client/gui/mods/offhangar/bot_ai_maps.py"


def load_ai_modules():
    for name in ("gui", "gui.mods", "gui.mods.offhangar"):
        package = types.ModuleType(name)
        package.__path__ = (
            [str(AI_PATH.parent)] if name == "gui.mods.offhangar" else []
        )
        sys.modules[name] = package

    maps_spec = importlib.util.spec_from_file_location(
        "gui.mods.offhangar.bot_ai_maps", MAPS_PATH
    )
    maps = importlib.util.module_from_spec(maps_spec)
    sys.modules[maps_spec.name] = maps
    maps_spec.loader.exec_module(maps)
    sys.modules["gui.mods.offhangar"].bot_ai_maps = maps

    ai_spec = importlib.util.spec_from_file_location(
        "gui.mods.offhangar.bot_ai", AI_PATH
    )
    ai = importlib.util.module_from_spec(ai_spec)
    sys.modules[ai_spec.name] = ai
    ai_spec.loader.exec_module(ai)
    return ai, maps


def descriptor(class_tag, speed=12.0, armor=80.0, name=None):
    return types.SimpleNamespace(
        type=types.SimpleNamespace(
            tags={class_tag}, name=name or "test:%s" % class_tag
        ),
        physics={"speedLimits": (speed, 5.0)},
        hull={"primaryArmor": armor},
        turret={"primaryArmor": armor, "circularVisionRadius": 400.0},
    )


class BotAITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ai, cls.maps = load_ai_modules()

    def test_personality_is_stable_but_varies_between_bots(self):
        first = self.ai.make_personality(self.ai.stable_seed(7, "Alpha"))
        second = self.ai.make_personality(self.ai.stable_seed(7, "Alpha"))
        self.assertEqual(first, second)

        personalities = {
            tuple(round(value, 4) for key, value in sorted(
                self.ai.make_personality(self.ai.stable_seed(7, bot_id)).items()
            ))
            for bot_id in range(12)
        }
        self.assertGreaterEqual(len(personalities), 10)

    def test_matchmaking_uses_a_three_tier_band(self):
        allowed = {
            tier for tier in range(1, 11)
            if self.ai.vehicle_in_battle_tier_band(7, tier)
        }

        self.assertEqual({6, 7, 8}, allowed)

    def test_lineup_limits_exact_spg_but_not_tank_destroyers(self):
        vehicles = [
            {"name": "spg-a", "tags": {"SPG"}},
            {"name": "spg-b", "tags": {"SPG"}},
            {"name": "td", "tags": {"AT-SPG"}},
            {"name": "medium", "tags": {"mediumTank"}},
        ]

        lineup = self.ai.select_bot_lineup(vehicles, 12, 1, vehicles)
        no_bot_spg = self.ai.select_bot_lineup(vehicles, 12, 0, vehicles)

        self.assertEqual(12, len(lineup))
        self.assertLessEqual(sum("SPG" in item["tags"] for item in lineup), 1)
        self.assertEqual(0, sum("SPG" in item["tags"] for item in no_bot_spg))
        self.assertTrue(any("AT-SPG" in item["tags"] for item in lineup))

    def test_enemy_is_hidden_until_spotted_but_ally_is_visible(self):
        self.assertFalse(self.ai.bot_initially_visible(2, 1, True))
        self.assertTrue(self.ai.bot_initially_visible(1, 1, True))
        self.assertTrue(self.ai.bot_initially_visible(2, 1, False))
        self.assertFalse(self.ai.entity_visible_to_minimap(
            types.SimpleNamespace(_spot_visible=False)
        ))
        self.assertTrue(self.ai.entity_visible_to_minimap(
            types.SimpleNamespace(_spot_visible=True)
        ))

        battle_source = (
            ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
        ).read_text()
        self.assertGreaterEqual(battle_source.count("vehicle_in_battle_tier_band"), 3)
        self.assertIn("e_mock._spot_visible = bot_initially_visible(", battle_source)
        self.assertIn("if not _offh_minimap_visible(value):", battle_source)

    def test_sight_segment_excludes_shooter_and_target_hulls(self):
        segment = self.ai.trimmed_sight_segment(
            (0.0, 10.0, 0.0), (0.0, 12.0, 100.0)
        )

        self.assertEqual((0.0, 12.5, 4.0), segment[0])
        self.assertEqual((0.0, 13.5, 96.0), segment[1])
        self.assertIsNone(self.ai.trimmed_sight_segment(
            (0.0, 0.0, 0.0), (0.0, 0.0, 8.0)
        ))
        self.assertEqual((), self.ai.trimmed_sight_segment(
            ("bad", 0.0, 0.0), (0.0, 0.0, 100.0)
        ))

    def test_lakeville_lane_capacities_force_a_balanced_split(self):
        director = self.ai.BattleDirector("07_lakeville", "lane-capacity")
        for bot_id in range(1, 15):
            director.register(
                bot_id, 1, descriptor("heavyTank", armor=200.0),
                "Heavy %s" % bot_id,
            )
        counts = {}
        for agent in director.agents.values():
            route_id = agent["route"]["id"]
            counts[route_id] = counts.get(route_id, 0) + 1

        self.assertEqual(
            {"east_town": 5, "lake_road": 4, "west_valley": 5}, counts
        )

    def test_lakeville_routes_use_three_real_corridors(self):
        tactical_map = self.maps.get_tactical_map("07_lakeville")
        routes = {
            route["id"]: route["waypoints"]
            for route in tactical_map["routes"][1]
        }

        self.assertLess(min(point[0] for point in routes["west_valley"]), -320)
        self.assertTrue(all(
            -180 <= point[0] <= -50
            for point in routes["lake_road"]
        ))
        self.assertGreater(max(point[0] for point in routes["east_town"]), 300)

        # Near the map's middle latitude the lanes must still be separated.
        # This catches the old data where the west route sat on the mountain
        # and the town route ended in the lake instead of reaching the city.
        lane_x = {}
        for route_id, points in routes.items():
            lane_x[route_id] = min(points, key=lambda point: abs(point[1]))[0]
        self.assertLess(lane_x["west_valley"], -280)
        self.assertTrue(-130 < lane_x["lake_road"] < -40)
        self.assertGreater(lane_x["east_town"], 260)

    def test_routes_start_at_own_flag_and_finish_at_enemy_flag(self):
        for map_name, tactical_map in self.maps.TACTICAL_MAPS.items():
            for team in (1, 2):
                own = tactical_map["bases"][team]
                enemy = tactical_map["bases"][3 - team]
                for source_route in tactical_map["routes"][team]:
                    points = self.ai.route_toward_enemy(
                        source_route, team, tactical_map["bases"]
                    )["waypoints"]
                    if len(points) < 2:
                        continue
                    self.assertEqual((float(own[0]), float(own[1]), 0), points[0])
                    self.assertEqual(
                        (float(enemy[0]), float(enemy[1]), 0), points[-1]
                    )

                director = self.ai.BattleDirector(map_name, "flag-goal")
                agent = director.register(
                    8000 + team, team, descriptor("mediumTank"), "Route check"
                )
                points = agent["route"]["waypoints"]
                if len(points) < 2:
                    continue
                order = director.order_for(
                    agent["id"], (own[0] + 15.0, 0.0, own[1] + 15.0),
                    0.0, 1000, 1000, 0.0,
                )
                self.assertGreaterEqual(order["route_index"], 1)

    def test_python26_source_loader_orders_ai_dependencies_before_battle(self):
        bootstrap = (
            ROOT / "scripts/client/gui/mods/mod_offhangar.py"
        ).read_text()
        group_index = bootstrap.index("'bot_ai_maps_group_a'")
        maps_index = bootstrap.index("'bot_ai_maps',")
        ai_index = bootstrap.index("'bot_ai',")
        prebaked_index = bootstrap.index("'prebaked_navigation'")
        navigation_index = bootstrap.index("'bot_ai_navigation'")
        cover_index = bootstrap.index("'bot_ai_cover'")
        driver_index = bootstrap.index("'bot_ai_driver'")
        battle_index = bootstrap.index("'offline_battle',")

        self.assertLess(group_index, maps_index)
        self.assertLess(maps_index, ai_index)
        self.assertLess(ai_index, prebaked_index)
        self.assertLess(prebaked_index, navigation_index)
        self.assertLess(navigation_index, cover_index)
        self.assertLess(cover_index, driver_index)
        self.assertLess(driver_index, battle_index)

    def test_vehicle_stats_produce_distinct_tactical_profiles(self):
        heavy = self.ai.build_vehicle_profile(
            descriptor("heavyTank", speed=9.0, armor=160.0)
        )
        light = self.ai.build_vehicle_profile(
            descriptor("lightTank", speed=20.0, armor=30.0)
        )
        tank_destroyer = self.ai.build_vehicle_profile(
            descriptor("AT-SPG", speed=10.0, armor=60.0)
        )

        self.assertEqual("brawler", heavy["dominant_role"])
        self.assertEqual("scout", light["dominant_role"])
        self.assertEqual("sniper", tank_destroyer["dominant_role"])
        self.assertLess(heavy["desired_range"], light["desired_range"])
        self.assertLess(light["desired_range"], tank_destroyer["desired_range"])

    def test_himmelsdorf_routes_are_bounded_and_bidirectional(self):
        tactical_map = self.maps.get_tactical_map("spaces/04_himmelsdorf")
        self.assertIsNotNone(tactical_map)
        min_x, min_z, max_x, max_z = tactical_map["bounds"]
        for team in (1, 2):
            route_ids = {route["id"] for route in tactical_map["routes"][team]}
            self.assertEqual(
                {"banana", "hill", "rail", "center", "rear_guard"}, route_ids
            )
            for route in tactical_map["routes"][team]:
                for x, z, hold in route["waypoints"]:
                    self.assertLessEqual(min_x, x)
                    self.assertLessEqual(x, max_x)
                    self.assertLessEqual(min_z, z)
                    self.assertLessEqual(z, max_z)
                    self.assertIn(hold, (0, 1))

        south_banana = next(
            route for route in tactical_map["routes"][1] if route["id"] == "banana"
        )
        north_banana = next(
            route for route in tactical_map["routes"][2] if route["id"] == "banana"
        )
        self.assertEqual(south_banana["waypoints"][0], north_banana["waypoints"][-1])
        self.assertEqual(south_banana["waypoints"][-1], north_banana["waypoints"][0])

    def test_all_stock_standard_battle_maps_have_tactical_data(self):
        expected = {
            "01_karelia", "02_malinovka", "03_campania", "04_himmelsdorf",
            "05_prohorovka", "06_ensk", "07_lakeville", "08_ruinberg",
            "10_hills", "11_murovanka", "13_erlenberg", "14_siegfried_line",
            "15_komarin", "17_munchen", "18_cliff", "19_monastery",
            "22_slough", "23_westfeld", "28_desert", "29_el_hallouf",
            "31_airfield", "33_fjord", "34_redshire", "35_steppes",
            "36_fishing_bay", "37_caucasus", "38_mannerheim_line",
            "39_crimea", "42_north_america", "44_north_america",
            "45_north_america", "47_canada_a", "51_asia",
        }
        self.assertEqual(expected, set(self.maps.TACTICAL_MAPS))

        for map_name, tactical_map in self.maps.TACTICAL_MAPS.items():
            min_x, min_z, max_x, max_z = tactical_map["bounds"]
            self.assertTrue(tactical_map["routes"][1], map_name)
            self.assertEqual(
                len(tactical_map["routes"][1]),
                len(tactical_map["routes"][2]),
                map_name,
            )
            for team in (1, 2):
                for route in tactical_map["routes"][team]:
                    for x, z, hold in route["waypoints"]:
                        self.assertLessEqual(min_x, x, map_name)
                        self.assertLessEqual(x, max_x, map_name)
                        self.assertLessEqual(min_z, z, map_name)
                        self.assertLessEqual(z, max_z, map_name)
                        self.assertIn(hold, (0, 1), map_name)

            director = self.ai.BattleDirector(map_name, "schema-check")
            classes = ("heavyTank", "mediumTank", "lightTank", "AT-SPG", "SPG")
            for index, class_tag in enumerate(classes):
                agent = director.register(
                    1000 + index, 1, descriptor(class_tag), class_tag
                )
                self.assertIsNotNone(agent["route"], map_name)
                base = tactical_map["bases"][1]
                order = director.order_for(
                    1000 + index, (base[0], 0.0, base[1]), 0.0,
                    1000, 1000, 0.0,
                )
                self.assertIn(order["route_id"], {
                    route["id"] for route in tactical_map["routes"][1]
                })

    def test_tactical_routes_have_no_hairpins_or_self_intersections(self):
        def orientation(first, second, third):
            return ((second[0] - first[0]) * (third[1] - first[1]) -
                    (second[1] - first[1]) * (third[0] - first[0]))

        def intersects(first, second, third, fourth):
            return (
                orientation(first, second, third) *
                orientation(first, second, fourth) < 0.0 and
                orientation(third, fourth, first) *
                orientation(third, fourth, second) < 0.0
            )

        for map_name, tactical_map in self.maps.TACTICAL_MAPS.items():
            for team in (1, 2):
                for route in tactical_map["routes"][team]:
                    points = [(float(point[0]), float(point[1]))
                              for point in route["waypoints"]]
                    context = (map_name, team, route["id"])
                    for first, second in zip(points, points[1:]):
                        self.assertGreater(
                            math.hypot(second[0] - first[0],
                                       second[1] - first[1]),
                            1.0,
                            context,
                        )
                    for first, second, third in zip(
                            points, points[1:], points[2:]):
                        first_heading = math.atan2(
                            second[1] - first[1], second[0] - first[0])
                        second_heading = math.atan2(
                            third[1] - second[1], third[0] - second[0])
                        turn = abs(self.ai._angle_delta(
                            second_heading, first_heading))
                        self.assertLess(turn, math.pi * 0.5, context)
                    segments = list(zip(points, points[1:]))
                    for first_index, first_segment in enumerate(segments):
                        for second_index in range(
                                first_index + 2, len(segments)):
                            self.assertFalse(
                                intersects(
                                    first_segment[0], first_segment[1],
                                    segments[second_index][0],
                                    segments[second_index][1],
                                ),
                                context + (first_index, second_index),
                            )

    def test_route_assignment_is_deterministic_and_capacity_aware(self):
        heavy = descriptor("heavyTank", speed=9.0, armor=160.0)
        first = self.ai.BattleDirector("04_himmelsdorf", "round-9")
        second = self.ai.BattleDirector("04_himmelsdorf", "round-9")
        first_routes = []
        second_routes = []
        for bot_id in range(1, 16):
            first_routes.append(
                first.register(bot_id, 1, heavy, "Bot %s" % bot_id)["route"]["id"]
            )
            second_routes.append(
                second.register(bot_id, 1, heavy, "Bot %s" % bot_id)["route"]["id"]
            )

        self.assertEqual(first_routes, second_routes)
        self.assertGreaterEqual(len(set(first_routes)), 3)
        self.assertLess(first_routes.count("banana"), len(first_routes))

    def test_unseen_enemy_is_unknown_then_becomes_last_known_without_fire(self):
        director = self.ai.BattleDirector("04_himmelsdorf", 12)
        director.register(101, 1, descriptor("heavyTank", armor=160.0), "Guard")
        bot_position = (185.0, 0.0, -82.0)

        unknown = director.order_for(101, bot_position, 0.0, 1000, 1000, 0.0)
        self.assertIsNone(unknown["target_id"])
        self.assertFalse(unknown["fire_allowed"])

        target_position = (185.0, 0.0, 10.0)
        director.update_contact(
            1, 202, 2, target_position, 500, 1000, "mediumTank", True, 1.0
        )
        spotted = director.order_for(101, bot_position, 0.0, 1000, 1000, 1.0)
        self.assertEqual(202, spotted["target_id"])
        self.assertTrue(spotted["fire_allowed"])

        director.update_contact(
            1, 202, 2, (300.0, 0.0, 300.0), 500, 1000,
            "mediumTank", False, 2.0
        )
        remembered = director.order_for(101, bot_position, 0.0, 1000, 1000, 2.0)
        self.assertEqual(target_position, remembered["aim_position"])
        self.assertFalse(remembered["fire_allowed"])
        self.assertEqual("investigate", remembered["combat_mode"])

        expired = director.order_for(101, bot_position, 0.0, 1000, 1000, 8.1)
        self.assertIsNone(expired["target_id"])
        self.assertFalse(expired["fire_allowed"])

    def test_armoured_bot_angles_and_patient_bot_cycles_back_to_cover(self):
        director = self.ai.BattleDirector("04_himmelsdorf", 33)
        agent = director.register(
            301, 1, descriptor("heavyTank", armor=180.0), "Patient"
        )
        agent["personality"]["caution"] = 0.85
        agent["personality"]["patience"] = 0.85
        agent["personality"]["aggression"] = 0.25
        agent["personality"]["jiggle"] = 0.0
        position = (185.0, 0.0, -82.0)
        target = (185.0, 0.0, 8.0)
        director.update_contact(
            1, 302, 2, target, 1000, 1000, "heavyTank", True, 0.0
        )

        modes = []
        angled = None
        for tick in range(60):
            now = tick * 0.2
            director.update_contact(
                1, 302, 2, target, 1000, 1000, "heavyTank", True, now
            )
            order = director.order_for(301, position, 0.0, 1000, 1000, now)
            modes.append(order["combat_mode"])
            angled = order["face_position"]

        self.assertIn("engage", modes)
        self.assertIn("withdraw", modes)
        self.assertNotEqual(target, angled)
        direct_bearing = math.atan2(target[0] - position[0], target[2] - position[2])
        angled_bearing = math.atan2(
            angled[0] - position[0], angled[2] - position[2]
        )
        self.assertGreater(abs(angled_bearing - direct_bearing), math.radians(10.0))

    def test_jiggle_is_an_individual_armoured_driver_habit(self):
        director = self.ai.BattleDirector("04_himmelsdorf", 45)
        agent = director.register(
            401, 1, descriptor("heavyTank", armor=180.0), "Jiggler"
        )
        agent["personality"].update({
            "caution": 0.2,
            "patience": 0.2,
            "aggression": 0.3,
            "jiggle": 0.95,
        })
        position = (185.0, 0.0, -82.0)
        target = (185.0, 0.0, -22.0)
        modes = set()
        throttle_values = set()

        for tick in range(50):
            now = tick * 0.2
            director.update_contact(
                1, 402, 2, target, 1000, 1000, "heavyTank", True, now
            )
            order = director.order_for(401, position, 0.0, 1000, 1000, now)
            modes.add(order["combat_mode"])
            throttle_values.add(order["throttle_override"])

        self.assertIn("jiggle_forward", modes)
        self.assertIn("jiggle_back", modes)
        self.assertTrue(any(value is not None and value > 0 for value in throttle_values))
        self.assertTrue(any(value is not None and value < 0 for value in throttle_values))

        light_director = self.ai.BattleDirector("04_himmelsdorf", 45)
        light_agent = light_director.register(
            403, 1, descriptor("lightTank", speed=20.0, armor=30.0), "Scout"
        )
        light_agent["personality"]["jiggle"] = 1.0
        light_director.update_contact(
            1, 404, 2, target, 1000, 1000, "heavyTank", True, 0.0
        )
        light_order = light_director.order_for(
            403, position, 0.0, 1000, 1000, 0.0
        )
        self.assertIsNone(light_order["throttle_override"])

    def test_serialized_profile_can_be_registered_by_a_remote_planner(self):
        profile = self.ai.build_vehicle_profile(
            descriptor("mediumTank", speed=17.0, armor=70.0)
        )
        director = self.ai.BattleDirector("04_himmelsdorf", "server-round")
        agent = director.register_profile(701, 1, profile, "Remote medium")

        self.assertEqual("mediumTank", agent["profile"]["class_tag"])
        self.assertIsNotNone(agent["route"])
        self.assertEqual(profile["roles"], agent["profile"]["roles"])

    def test_team_target_assignment_spreads_fire_after_reservation(self):
        director = self.ai.BattleDirector("04_himmelsdorf", "focus-fire")
        for bot_id in range(801, 805):
            director.register(
                bot_id, 1, descriptor("mediumTank", speed=16.0),
                "Medium %s" % bot_id,
            )
        for target_id, x in ((901, 150.0), (902, 220.0)):
            director.update_contact(
                1, target_id, 2, (x, 0.0, 20.0), 600, 600,
                "mediumTank", True, 1.0, armor=70.0,
            )

        assignments = [
            director.order_for(
                bot_id, (185.0, 0.0, -82.0), 0.0, 800, 800, 1.0
            )["target_id"]
            for bot_id in range(801, 805)
        ]

        assigned = [target_id for target_id in assignments
                    if target_id is not None]
        self.assertEqual({901, 902}, set(assigned))
        self.assertEqual(2, assignments.count(None))
        self.assertEqual(1, assigned.count(901))
        self.assertEqual(1, assigned.count(902))

    def test_mobile_vehicle_uses_force_aware_flanking_position(self):
        director = self.ai.BattleDirector("04_himmelsdorf", "flank")
        agent = director.register(
            1001, 1, descriptor("mediumTank", speed=18.0), "Flanker"
        )
        agent["personality"].update({
            "initiative": 0.9,
            "caution": 0.35,
            "aggression": 0.65,
        })
        position = (185.0, 0.0, -82.0)
        target = (185.0, 0.0, 35.0)
        director.update_contact(
            1, 1002, 2, target, 800, 800, "heavyTank", True, 2.0,
            armor=140.0,
        )

        order = director.order_for(1001, position, 0.0, 800, 800, 2.0)

        self.assertEqual("flank", order["combat_mode"])
        self.assertNotEqual(target, order["move_position"])
        self.assertNotEqual(position, order["move_position"])

    def test_mobile_vehicle_approaches_a_distant_contact_before_flanking(self):
        director = self.ai.BattleDirector("04_himmelsdorf", "far-flank")
        agent = director.register(
            1003, 1, descriptor("mediumTank", speed=18.0), "Far flanker"
        )
        agent["personality"].update({
            "initiative": 0.9,
            "caution": 0.35,
            "aggression": 0.65,
        })
        position = (0.0, 0.0, -200.0)
        target = (0.0, 0.0, 200.0)
        director.update_contact(
            1, 1004, 2, target, 800, 800, "heavyTank", True, 2.0,
            armor=140.0,
        )

        order = director.order_for(1003, position, 0.0, 800, 800, 2.0)

        self.assertEqual("advance_contact", order["combat_mode"])
        self.assertEqual(target, order["move_position"])

    def test_shell_selection_uses_armor_instead_of_always_slot_zero(self):
        profile = {
            "shells": (
                {"index": 0, "kind": "ARMOR_PIERCING", "penetration": 105,
                 "damage": 220},
                {"index": 1, "kind": "HOLLOW_CHARGE", "penetration": 210,
                 "damage": 200},
                {"index": 2, "kind": "HIGH_EXPLOSIVE", "penetration": 45,
                 "damage": 330},
            )
        }

        shell_index = self.ai.select_shell_index(
            profile,
            {"armor": 170, "health": 900, "distance": 180},
            {"aggression": 0.55},
        )

        self.assertEqual(1, shell_index)


if __name__ == "__main__":
    unittest.main()
