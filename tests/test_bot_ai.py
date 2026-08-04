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

    def test_python26_source_loader_orders_ai_dependencies_before_battle(self):
        bootstrap = (
            ROOT / "scripts/client/gui/mods/mod_offhangar.py"
        ).read_text()
        group_index = bootstrap.index("'bot_ai_maps_group_a'")
        maps_index = bootstrap.index("'bot_ai_maps',")
        ai_index = bootstrap.index("'bot_ai',")
        battle_index = bootstrap.index("'offline_battle',")

        self.assertLess(group_index, maps_index)
        self.assertLess(maps_index, ai_index)
        self.assertLess(ai_index, battle_index)

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


if __name__ == "__main__":
    unittest.main()
