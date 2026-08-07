import importlib.util
import math
import unittest
from pathlib import Path

from server_bot_ai import BotPlanner


ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "scripts/client/gui/mods/offhangar/bot_ai_driver.py"
PHYSICS_PATH = ROOT / "scripts/client/gui/mods/offhangar/physics.py"


def load_driver():
    spec = importlib.util.spec_from_file_location("bot_ai_driver_under_test", DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_physics():
    spec = importlib.util.spec_from_file_location("physics_under_test", PHYSICS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotAIDriverTest(unittest.TestCase):
    def setUp(self):
        self.module = load_driver()

    def test_route_without_spotted_target_does_not_become_idle_hold(self):
        driver = self.module.LocalDriver()
        current = (0.0, 0.0, 0.0)
        route = (0.0, 0.0, 80.0)

        aim, move, face, should_stop = driver.resolve_order_positions(
            current, None, route, None
        )

        self.assertFalse(should_stop)
        self.assertEqual(route, aim)
        self.assertEqual(route, move)
        self.assertEqual(route, face)

    def test_real_server_advance_order_is_not_braked_by_client(self):
        order = BotPlanner().build_orders(
            [{"id": 1, "team": 1, "slot": 0, "health": 1000}],
            [{"id": 1, "team": 1, "alive": True, "x": 0.0, "z": -20.0}],
            [],
            0.0,
        )["orders"][0]

        aim, move, face, should_stop = self.module.LocalDriver.resolve_order_positions(
            (0.0, 0.0, -20.0),
            order["aim_position"],
            order["move_position"],
            order["face_position"],
        )

        self.assertEqual("route", order["combat_mode"])
        self.assertIsNone(order["aim_position"])
        self.assertFalse(should_stop)
        self.assertEqual(order["move_position"], aim)
        self.assertEqual(move, face)

    def test_missing_route_and_target_remains_an_idle_hold(self):
        driver = self.module.LocalDriver()
        current = (12.0, 3.0, -7.0)

        aim, move, face, should_stop = driver.resolve_order_positions(
            current, None, None, None
        )

        self.assertTrue(should_stop)
        self.assertEqual(current, aim)
        self.assertEqual(current, move)
        self.assertEqual(current, face)

    def test_limited_traverse_gun_turns_hull_before_physics(self):
        minimum, maximum, limited = self.module.gun_yaw_limits({
            "gun": {"turretYawLimits": (-10.0, 10.0)},
            "turret": {},
        })
        turn, throttle, aiming = self.module.combat_hull_aim(
            0.0, math.pi / 2.0, minimum, maximum,
            -0.3, 0.8, "drive", True,
        )

        self.assertTrue(limited)
        self.assertTrue(aiming)
        self.assertEqual(1.0, turn)
        self.assertEqual(0.0, throttle)

    def test_missing_installed_yaw_limits_means_full_rotation(self):
        minimum, maximum, limited = self.module.gun_yaw_limits({
            "gun": {},
            "turret": {},
        })

        self.assertFalse(limited)
        self.assertAlmostEqual(-math.pi, minimum)
        self.assertAlmostEqual(math.pi, maximum)

    def test_installed_turret_yaw_limits_are_used_per_vehicle(self):
        minimum, maximum, limited = self.module.gun_yaw_limits({
            "gun": {},
            "turret": {"yawLimits": (-0.2, 0.3)},
        })

        self.assertTrue(limited)
        self.assertAlmostEqual(-0.2, minimum)
        self.assertAlmostEqual(0.3, maximum)

    def test_installed_gun_yaw_limits_override_turret_limits(self):
        minimum, maximum, limited = self.module.gun_yaw_limits({
            "gun": {"turretYawLimits": (-0.1, 0.1)},
            "turret": {"yawLimits": (-math.pi, math.pi)},
        })

        self.assertTrue(limited)
        self.assertAlmostEqual(-0.1, minimum)
        self.assertAlmostEqual(0.1, maximum)

    def test_obstacle_recovery_outranks_limited_traverse_hull_aim(self):
        turn, throttle, aiming = self.module.combat_hull_aim(
            0.0, math.pi / 2.0, -0.2, 0.2,
            -1.0, -0.72, "reverse_turn", True,
        )

        self.assertFalse(aiming)
        self.assertEqual(-1.0, turn)
        self.assertEqual(-0.72, throttle)

    def test_fire_waits_for_visible_yaw_and_pitch_alignment(self):
        self.assertFalse(self.module.gun_aligned(
            0.0, 0.0, 0.0, -0.20, -0.05
        ))
        self.assertFalse(self.module.gun_aligned(
            0.20, 0.0, 0.0, -0.10, -0.10
        ))
        self.assertTrue(self.module.gun_aligned(
            0.20, 0.10, 0.08, -0.10, -0.08
        ))

    def test_projectile_direction_follows_rendered_barrel_pitch(self):
        direction = self.module.barrel_direction(0.0, -0.25)

        self.assertAlmostEqual(0.0, direction[0], places=6)
        self.assertGreater(direction[1], 0.0)
        self.assertAlmostEqual(1.0, math.sqrt(sum(v * v for v in direction)), places=6)

    def test_battle_loop_integrates_prebattle_freeze_and_physical_aim(self):
        source = (
            ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
        ).read_text()

        hull_aim = source.index(".combat_hull_aim(")
        direct_turn = source.find("_PHY.traverse_step(", hull_aim)
        profiled_turn = source.find(
            "'kinematics', _PHY.traverse_step,", hull_aim
        )
        physics_turn = max(direct_turn, profiled_turn)
        self.assertGreaterEqual(physics_turn, 0)
        self.assertLess(hull_aim, physics_turn)
        self.assertIn("if _battle_active and hasattr(m_veh, '_t_mat'):", source)
        self.assertIn("_ai_gun_aligned = _offh_ai_driver().gun_aligned(", source)
        self.assertIn("_offh_ai_driver().barrel_direction(", source)

    def test_flat_ground_drives_straight_to_target(self):
        driver = self.module.LocalDriver()
        order = driver.drive(1, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                             (0.0, 0.0, 50.0), (), lambda angle: True)
        self.assertEqual("drive", order["recovery_mode"])
        self.assertGreater(order["throttle"], 0.9)
        self.assertAlmostEqual(0.0, order["turn"], places=5)
        self.assertAlmostEqual(0.0, order["target_yaw"], places=5)

    def test_normal_route_turn_keeps_full_throttle(self):
        driver = self.module.LocalDriver()
        target_yaw = 0.9
        order = driver.drive(
            2, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
            (math.sin(target_yaw) * 50.0, 0.0,
             math.cos(target_yaw) * 50.0),
            (), lambda angle: True,
        )

        self.assertEqual("drive", order["recovery_mode"])
        self.assertEqual(1.0, order["throttle"])
        self.assertGreater(order["turn"], 0.9)

    def test_reverse_steering_yaws_opposite_to_forward_steering(self):
        physics = load_physics()
        params = {
            "speedFwd": 12.0,
            "rotSpd": 1.0,
            "terrainResist": (1.0, 1.0, 1.0),
        }

        forward = physics.traverse_step(params, 0.0, -1.0, 2.0, 0.1)
        reverse = physics.traverse_step(params, 0.0, -1.0, -2.0, 0.1)

        self.assertLess(forward, 0.0)
        self.assertGreater(reverse, 0.0)

    def test_reverse_recovery_command_compensates_for_reverse_physics(self):
        driver = self.module.LocalDriver(stuck_seconds=0.4, recovery_seconds=0.8)
        order = None
        for unused in range(10):
            order = driver.drive(
                303, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                (0.0, 0.0, 50.0), (), lambda angle: True,
            )
            if order["recovery_mode"] == "reverse_turn":
                break
        order = driver.drive(
            303, (0.0, 0.0, 0.0), 0.0, -2.0, 0.1,
            (0.0, 0.0, 50.0), (), lambda angle: True,
        )

        self.assertEqual("reverse_turn", order["recovery_mode"])
        self.assertLess(order["turn"] * order["target_yaw"], 0.0)

    def test_target_yaw_controller_compensates_while_rolling_backwards(self):
        driver = self.module.LocalDriver()
        order = driver.drive(
            304, (0.0, 0.0, 0.0), 0.0, -2.0, 0.1,
            (50.0, 0.0, 0.0), (), lambda angle: True,
        )

        self.assertGreater(order["target_yaw"], 0.0)
        self.assertLess(order["turn"], 0.0)

    def test_reaching_a_target_never_turns_zero_vector_into_full_throttle(self):
        driver = self.module.LocalDriver()

        order = driver.drive(
            101, (4.0, 12.0, -8.0), 1.2, 0.0, 0.1,
            (4.0, 0.0, -8.0), (), lambda angle: True,
        )

        self.assertEqual("arrived", order["recovery_mode"])
        self.assertEqual(0.0, order["throttle"])
        self.assertEqual(0.0, order["turn"])

    def test_blocked_forward_ray_chooses_open_side(self):
        driver = self.module.LocalDriver()

        def clear(angle):
            return angle > 0.2 and angle < 0.65

        order = driver.drive(2, (0.0, 0.0, 0.0), 0.0, 3.0, 0.1,
                             (0.0, 0.0, 40.0), (), clear)
        self.assertEqual("avoid", order["recovery_mode"])
        self.assertGreater(order["turn"], 0.0)
        self.assertGreater(order["target_yaw"], 0.2)

    def test_stuck_time_triggers_reverse_turn_without_frame_counter(self):
        driver = self.module.LocalDriver(stuck_seconds=0.6, recovery_seconds=0.5)
        order = None
        for unused in range(12):
            order = driver.drive(9, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                                 (0.0, 0.0, 50.0), (), lambda angle: True)
            if order["recovery_mode"] == "reverse_turn":
                break
        self.assertEqual("reverse_turn", order["recovery_mode"])
        self.assertLess(order["throttle"], 0.0)
        first_turn = order["turn"]
        # Let this recovery finish, stay blocked again, then ensure the next
        # recovery turns to the other side.
        for unused in range(30):
            order = driver.drive(9, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                                 (0.0, 0.0, 50.0), (), lambda angle: True)
            if order["recovery_mode"] == "reverse_turn" and order["turn"] != first_turn:
                break
        self.assertEqual("reverse_turn", order["recovery_mode"])
        self.assertNotEqual(first_turn, order["turn"])

    def test_recovery_never_reverses_into_an_unsafe_corridor(self):
        driver = self.module.LocalDriver(stuck_seconds=0.4, recovery_seconds=0.5)
        order = None

        def only_forward_is_clear(angle):
            return abs(math.atan2(math.sin(angle), math.cos(angle))) < 1.0

        for unused in range(10):
            order = driver.drive(
                102, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                (0.0, 0.0, 50.0), (), only_forward_is_clear,
            )
            if order["recovery_mode"] == "pivot_recovery":
                break

        self.assertEqual("pivot_recovery", order["recovery_mode"])
        self.assertEqual(0.0, order["throttle"])
        self.assertNotEqual(0.0, order["turn"])

    def test_commanded_hold_never_accumulates_stuck_recovery(self):
        driver = self.module.LocalDriver(stuck_seconds=0.4, recovery_seconds=0.5)
        orders = []
        for unused in range(30):
            orders.append(driver.drive(
                103, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                (0.0, 0.0, 4.0), (), lambda angle: True,
                movement_intent=False,
            ))

        self.assertTrue(all(order["recovery_mode"] == "arrived" for order in orders))
        self.assertTrue(all(order["throttle"] == 0.0 for order in orders))

        moving = None
        for unused in range(10):
            moving = driver.drive(
                103, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                (0.0, 0.0, 40.0), (), lambda angle: True,
            )
        self.assertIn(moving["recovery_mode"], ("reverse_turn", "pivot_recovery"))

    def test_non_overlapping_side_traffic_does_not_override_route(self):
        driver = self.module.LocalDriver(stuck_seconds=0.6)
        # A harmless vehicle beside the route is not an emergency. The moving
        # OBB predictor will still reject this heading if the paths converge.
        crowded = driver.drive("west", (0.0, 0.0, 0.0), 0.0, 2.0, 0.1,
                               (0.0, 0.0, 40.0), [(8.0, 0.0, 0.0)],
                               lambda angle: True)
        self.assertAlmostEqual(0.0, crowded["target_yaw"], places=5)

    def test_identity_phase_staggers_recovery(self):
        driver = self.module.LocalDriver(stuck_seconds=0.6)

        states = []
        for bot_id in ("alpha", "left"):
            active = 0.0
            for unused in range(20):
                active += 0.1
                order = driver.drive(bot_id, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                                     (0.0, 0.0, 40.0), (), lambda angle: True)
                if order["recovery_mode"] == "reverse_turn":
                    break
            states.append(active)
        self.assertNotEqual(states[0], states[1])

    def test_short_horizon_obb_prediction_rejects_a_clear_head_on_path(self):
        driver = self.module.LocalDriver()
        # The ray itself is clear, but at 0.75s the two oriented hulls overlap.
        clear = driver._prediction_clear(
            (0.0, 0.0, 0.0), 0.0, 8.0, None,
            [{"position": (0.0, 0.0, 10.0), "yaw": 0.0,
              "velocity": (0.0, 0.0, -6.0),
              "half_length": 3.5, "half_width": 1.7}], 3.5, 1.7
        )
        self.assertFalse(clear)

        stacked = driver._prediction_clear(
            (0.0, 0.0, 0.0), 0.0, 8.0, None,
            [{"position": (0.0, 8.0, 10.0), "yaw": 0.0,
              "velocity": (0.0, 0.0, -6.0),
              "half_length": 3.5, "half_width": 1.7}], 3.5, 1.7
        )
        self.assertTrue(stacked)

    def test_spawn_overlap_uses_separation_instead_of_deadlocking(self):
        driver = self.module.LocalDriver()
        close_leader = [{
            "position": (0.0, 0.0, 5.0),
            "yaw": 0.0,
            "velocity": (0.0, 0.0, 0.0),
            "half_length": 3.5,
            "half_width": 1.7,
        }]

        order = driver.drive(
            88, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
            (0.0, 0.0, 50.0), close_leader, lambda angle: True,
            None, 3.5, 1.7,
        )

        self.assertEqual("avoid", order["recovery_mode"])
        self.assertGreater(order["throttle"], 0.0)
        self.assertGreater(abs(order["turn"]), 0.5)

    def test_dense_spawn_turns_with_enough_throttle_to_leave_the_lineup(self):
        driver = self.module.LocalDriver()
        positions = [
            ((column - 2) * 9.0, 0.0, row * 9.0)
            for row in range(3)
            for column in range(5)
        ]
        orders = []
        for bot_id, position in enumerate(positions):
            neighbours = [
                {
                    "position": other,
                    "yaw": 0.0,
                    "velocity": (0.0, 0.0, 0.0),
                    "half_length": 3.5,
                    "half_width": 1.7,
                }
                for other in positions
                if other != position
            ]
            orders.append(driver.drive(
                bot_id, position, 0.0, 0.0, 0.1,
                (position[0], 0.0, position[2] + 100.0),
                neighbours, lambda angle: True,
                None, 3.5, 1.7,
            ))

        self.assertEqual(15, len(orders))
        self.assertTrue(all(order["throttle"] >= 0.70 for order in orders))
        self.assertTrue(all(order["recovery_mode"] in ("drive", "avoid") for order in orders))

    def test_target_behind_hull_pivots_before_driving(self):
        driver = self.module.LocalDriver()

        order = driver.drive(
            99, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
            (0.0, 0.0, -100.0), (), lambda angle: True,
            None, 3.5, 1.7,
        )

        self.assertEqual(0.0, order["throttle"])
        self.assertGreater(abs(order["turn"]), 0.9)

    def test_walking_pace_does_not_deadlock_on_neighbour_prediction(self):
        driver = self.module.LocalDriver()
        clear = driver._prediction_clear(
            (0.0, 0.0, 0.0), 0.0, 0.7, None,
            [{"position": (0.0, 0.0, 8.0), "yaw": 3.1415926535,
              "velocity": (0.0, 0.0, -4.0),
              "half_length": 3.5, "half_width": 1.7}],
            3.5, 1.7,
        )
        self.assertTrue(clear)

    def test_failed_direction_is_penalized_until_its_ttl_expires(self):
        driver = self.module.LocalDriver(failure_ttl=0.5)
        initial = driver.drive(55, (0.0, 0.0, 0.0), 0.0, 2.0, 0.1,
                               (0.0, 0.0, 40.0), (), lambda angle: True)
        self.assertAlmostEqual(0.0, initial["target_yaw"], places=5)
        driver.remember_failure(55, 0.0)
        diverted = driver.drive(55, (0.0, 0.0, 0.2), 0.0, 2.0, 0.1,
                                (0.0, 0.0, 40.0), (), lambda angle: True)
        self.assertEqual("avoid", diverted["recovery_mode"])
        self.assertNotAlmostEqual(0.0, diverted["target_yaw"], places=3)
        driver.drive(55, (0.0, 0.0, 1.0), 0.0, 2.0, 0.35,
                     (0.0, 0.0, 40.0), (), lambda angle: True)
        expired = driver.drive(55, (0.0, 0.0, 2.0), 0.0, 2.0, 0.35,
                               (0.0, 0.0, 40.0), (), lambda angle: True)
        self.assertAlmostEqual(0.0, expired["target_yaw"], places=5)

    def test_recent_plan_uses_one_hard_probe_and_keeps_dynamic_prediction(self):
        driver = self.module.LocalDriver()
        probes = []

        def clear(angle):
            probes.append(angle)
            return True

        driver.drive(77, (0.0, 0.0, 0.0), 0.0, 4.0, 0.01,
                     (0.0, 0.0, 50.0), (), clear)
        first_count = len(probes)
        driver.drive(77, (0.0, 0.0, 0.1), 0.0, 4.0, 0.05,
                     (0.0, 0.0, 50.0), (), clear)

        self.assertEqual(1, first_count)
        self.assertEqual(first_count + 1, len(probes))

        blocked = driver.drive(
            77, (0.0, 0.0, 0.2), 0.0, 4.0, 0.05,
            (0.0, 0.0, 50.0), (), lambda angle: False,
        )
        self.assertEqual("blocked", blocked["recovery_mode"])

    def test_transient_blockers_do_not_fill_failure_memory(self):
        driver = self.module.LocalDriver()
        for unused in range(40):
            driver.drive(71, (0.0, 0.0, 0.0), 0.0, 3.0, 0.1,
                         (0.0, 0.0, 40.0), (), lambda angle: False)
        self.assertEqual({}, driver.states[71]["failed_yaws"])

        for unused in range(60):
            driver.remember_failure(71, unused * 0.12, ttl=20.0)
            driver.drive(71, (float(unused), 0.0, 0.0), 0.0, 3.0, 0.1,
                         (0.0, 0.0, 100.0), (), lambda angle: True)
        self.assertLessEqual(len(driver.states[71]["failed_yaws"]), 32)


if __name__ == "__main__":
    unittest.main()
