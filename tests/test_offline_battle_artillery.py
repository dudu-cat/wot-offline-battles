import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from lan_battle_server import BattleState
from server_bot_ai import BotPlanner


ROOT = Path(__file__).resolve().parents[1]
BATTLE_SOURCE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
DRIVER_SOURCE = ROOT / "scripts/client/gui/mods/offhangar/bot_ai_driver.py"


def _function_source(name, next_name):
    source = BATTLE_SOURCE.read_text()
    start = source.index("def %s" % name)
    end = source.index("\ndef %s" % next_name, start)
    return source[start:end]


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "bot_ai_driver_for_artillery_test", DRIVER_SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _observed_artillery_battle():
    manifest = [{
        "id": 15,
        "team": 1,
        "slot": 14,
        "health": 560,
        "profile": {
            "class_tag": "SPG",
            "dominant_role": "artillery",
            "desired_range": 520.0,
            "fire_range": 1200.0,
            "roles": {"artillery": 1.0},
            "shells": [{
                "index": 0,
                "kind": "HIGH_EXPLOSIVE",
                "damage": 500,
                "penetration": 50,
            }],
        },
        "route": {
            "id": "rear_arc",
            "waypoints": [
                {"x": 0, "y": 0, "z": -100},
                {"x": 35, "y": 0, "z": -80},
            ],
        },
    }]
    states = [{
        "id": 15,
        "team": 1,
        "alive": True,
        "x": 0,
        "y": 0,
        "z": -100,
    }]
    players = [{
        "id": 1,
        "team": 2,
        "alive": True,
        "x": 0,
        "y": 0,
        "z": -60,
    }]
    contact = {
        "observing_team": 1,
        "target_kind": "human",
        "target_id": 1,
        "target_team": 2,
        "visible": True,
        "shootable_by_bot_ids": [],
        "x": 0,
        "y": 0,
        "z": -60,
        "health": 556,
        "max_health": 880,
        "class_tag": "lightTank",
    }
    return manifest, states, players, contact


class OfflineBattleArtilleryTests(unittest.TestCase):
    def test_contact_arc_candidates_are_built_only_after_queue_admission(self):
        source = BATTLE_SOURCE.read_text()
        contact_start = source.index("def _offh_ai_refresh_contacts")
        contact_end = source.index("def _offh_battle_sweep", contact_start)
        contacts = source[contact_start:contact_end]
        artillery_start = contacts.index(
            "for observer in artillery_by_team.get(observing_team, ()):"
        )
        artillery_end = contacts.index(
            "if target['id'] == player_id:", artillery_start
        )
        artillery_lane = contacts[artillery_start:artillery_end]

        lazy_request = artillery_lane.index("arc_queue.request_lazy(")
        candidate_factory = artillery_lane.index(
            "lambda: _offh_ai_artillery_candidates(", lazy_request
        )
        self.assertLess(lazy_request, candidate_factory)
        self.assertNotIn("arc_queue.request(", artillery_lane)

    def test_obj212_close_range_candidate_owns_its_math_dependency(self):
        namespace = {}
        exec(compile(
            _function_source(
                "_offh_ai_artillery_candidates",
                "_offh_ai_artillery_solution",
            ),
            str(BATTLE_SOURCE),
            "exec",
        ), namespace)
        self.assertNotIn("math", namespace)

        namespace.update({
            "_offh_ai_artillery_shot": lambda _vehicle, _shell_index: {
                "speed": 460.0,
                "gravity": 160.81,
            },
            "_offh_ai_gun_fire_position": lambda _vehicle: (0.0, 1.5, 0.0),
            "_offh_ai_artillery_pitch_limits": (
                lambda _vehicle, _relative_yaw: (
                    -0.7853981633974483,
                    0.06981317007977318,
                )
            ),
        })

        driver = _load_driver()
        gui = types.ModuleType("gui")
        mods = types.ModuleType("gui.mods")
        offhangar = types.ModuleType("gui.mods.offhangar")
        gui.mods = mods
        mods.offhangar = offhangar
        offhangar.bot_ai_driver = driver
        vehicle = types.SimpleNamespace(yaw=0.0)

        with mock.patch.dict(sys.modules, {
            "gui": gui,
            "gui.mods": mods,
            "gui.mods.offhangar": offhangar,
        }):
            candidates = namespace["_offh_ai_artillery_candidates"](
                vehicle, (0.0, 0.0, 40.0)
            )

        self.assertEqual(1, len(candidates))
        self.assertGreaterEqual(candidates[0]["pitch"], -0.7853981633974483)
        self.assertLessEqual(candidates[0]["pitch"], 0.06981317007977318)
        self.assertGreaterEqual(len(candidates[0]["path"]), 2)

    def test_direct_los_never_grants_an_spg_server_fire_proof(self):
        source = BATTLE_SOURCE.read_text()
        contact_start = source.index("def _offh_ai_refresh_contacts")
        contact_end = source.index("def _offh_battle_sweep", contact_start)
        contacts = source[contact_start:contact_end]

        direct_start = contacts.index("for distance_sq, observer in candidates:")
        direct_end = contacts.index("if visible:", direct_start)
        direct_lane = contacts[direct_start:direct_end]
        visibility = direct_lane.index("visible = True")
        spg_guard = direct_lane.index(
            "if observer.get('class_tag') == 'SPG':"
        )
        guard_reject = direct_lane.index("continue", spg_guard)
        direct_server_proof = direct_lane.index(
            "shootable_by_bot_ids.append(observer_id)"
        )
        self.assertLess(visibility, spg_guard)
        self.assertLess(spg_guard, guard_reject)
        self.assertLess(guard_reject, direct_server_proof)

        artillery_start = contacts.index(
            "for observer in artillery_by_team.get(observing_team, ()):",
            direct_end,
        )
        artillery_lane = contacts[artillery_start:]
        solution_gate = artillery_lane.index("if solution is None:")
        artillery_server_proof = artillery_lane.index(
            "shootable_by_bot_ids.append(observer_id)", solution_gate
        )
        self.assertLess(solution_gate, artillery_server_proof)

    def test_local_battle_receives_entity_arc_proof_without_server_ids(self):
        source = BATTLE_SOURCE.read_text()
        contact_start = source.index("def _offh_ai_refresh_contacts")
        contact_end = source.index("def _offh_battle_sweep", contact_start)
        contacts = source[contact_start:contact_end]
        artillery_start = contacts.index(
            "for observer in artillery_by_team.get(observing_team, ()):"
        )
        artillery_end = contacts.index("if target['id'] == player_id:", artillery_start)
        artillery_lane = contacts[artillery_start:artillery_end]

        solution_gate = artillery_lane.index("if solution is None:")
        entity_proof = artillery_lane.index(
            "shootable_by_entity_ids.append(observer_entity_id)",
            solution_gate,
        )
        network_guard = artillery_lane.index(
            "if observer.get('server_id') is not None:", entity_proof
        )
        network_proof = artillery_lane.index(
            "shootable_by_bot_ids.append(observer_id)", network_guard
        )
        local_contact = contacts.index("director.update_contact(", artillery_end)
        local_proof_argument = contacts.index(
            "shootable_by_entity_ids)", local_contact
        )

        self.assertLess(solution_gate, entity_proof)
        self.assertLess(entity_proof, network_guard)
        self.assertLess(network_guard, network_proof)
        self.assertLess(artillery_end, local_contact)
        self.assertLess(local_contact, local_proof_argument)

    def test_server_id_15_needs_arc_proof_for_artillery_fire_order(self):
        planner = BotPlanner()
        manifest, states, players, contact = _observed_artillery_battle()
        known = planner.known_targets(states, players)
        planner.report_contacts([contact], known, 1.0)

        blocked = planner.build_orders(
            manifest, states, players, 1.0
        )["orders"][0]
        self.assertEqual("artillery_hold", blocked["combat_mode"])
        self.assertIsNone(blocked["target_id"])
        self.assertFalse(blocked["fire_allowed"])

        proved = dict(contact, shootable_by_bot_ids=[15])
        planner.report_contacts([proved], known, 1.1)
        firing = planner.build_orders(
            manifest, states, players, 1.1
        )["orders"][0]
        self.assertEqual("artillery_fire", firing["combat_mode"])
        self.assertEqual(1, firing["target_id"])
        self.assertTrue(firing["fire_allowed"])
        self.assertEqual(0.0, firing["throttle_override"])

    def test_artillery_order_rechecks_solution_before_fire_sequence(self):
        source = BATTLE_SOURCE.read_text()
        order = source.index(
            "_is_artillery_order = (_tactical_mode == 'artillery_fire')"
        )
        solve = source.index("_offh_ai_artillery_solution(", order)
        reject = source.index("_ai_fire_allowed = False", solve)
        ready = source.index(
            "m_veh._ai_shoot_timer > bot_reload and _ai_fire_allowed",
            reject,
        )
        fire = source.index("m_veh._network_bot_fire_seq = ", ready)

        self.assertLess(order, solve)
        self.assertLess(solve, reject)
        self.assertLess(reject, ready)
        self.assertLess(ready, fire)

    def test_last_roster_slots_have_stable_server_ids_for_battle_logs(self):
        roster = BattleState._new_bot_roster()

        self.assertEqual(
            [(15, 1, 14), (30, 2, 14)],
            [
                (entry["id"], entry["team"], entry["slot"])
                for entry in roster
                if entry["slot"] == 14
            ],
        )


if __name__ == "__main__":
    unittest.main()
