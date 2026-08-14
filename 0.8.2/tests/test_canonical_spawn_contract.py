import json
import math
import re
import unittest
from pathlib import Path

from lan_battle_server import BattleState


ROOT = Path(__file__).resolve().parents[1]
NORTH_AMERICA_GRAPH = (
    ROOT / "scripts/client/gui/mods/offhangar/navgraphs/42_north_america.json"
)
AIRFIELD_GRAPH = (
    ROOT / "scripts/client/gui/mods/offhangar/navgraphs/31_airfield.json"
)
OFFLINE_BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
NETWORK_BATTLE = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"


# Collision footprints recovered from the retail 42_north_america package.
# The legacy procedural team-1 slots 10 and 12 intersect these soft props.
SOFT_SPAWN_OBSTACLES = {
    10: (
        (-183.786860, -283.154218),
        (-183.378258, -289.521204),
        (-179.078323, -289.466117),
        (-178.961577, -281.930007),
        (-179.199116, -280.037315),
        (-183.499051, -280.092401),
    ),
    12: (
        (-195.943526, -270.607344),
        (-195.815751, -277.420762),
        (-192.165605, -277.352309),
        (-192.293379, -270.538891),
    ),
}
LEGACY_SLOT_POSES = {
    10: (-181.1127667, -281.7297868),
    12: (-191.7989617, -272.6851620),
}


def _vehicle_obb(x, z, yaw, half_width, half_length):
    forward = (math.sin(yaw), math.cos(yaw))
    right = (math.cos(yaw), -math.sin(yaw))
    return tuple(
        (
            x + forward[0] * along * half_length
            + right[0] * across * half_width,
            z + forward[1] * along * half_length
            + right[1] * across * half_width,
        )
        for along, across in ((1, 1), (1, -1), (-1, -1), (-1, 1))
    )


def _polygons_overlap(first, second):
    for polygon in (first, second):
        for index, point in enumerate(polygon):
            other = polygon[(index + 1) % len(polygon)]
            axis = (-(other[1] - point[1]), other[0] - point[0])
            first_projection = tuple(
                value[0] * axis[0] + value[1] * axis[1] for value in first
            )
            second_projection = tuple(
                value[0] * axis[0] + value[1] * axis[1] for value in second
            )
            if (max(first_projection) < min(second_projection) or
                    max(second_projection) < min(first_projection)):
                return False
    return True


def _terrain_footprint_clear(graph, pose, half_width, half_length):
    x, ground_y, z, yaw = (float(value) for value in pose)
    forward = (math.sin(yaw), math.cos(yaw))
    right = (math.cos(yaw), -math.sin(yaw))
    origin_x, origin_z = (float(value) for value in graph["origin"])
    cell_size = float(graph["cell_size"])
    width = int(graph["width"])
    height = int(graph["height"])
    maximum_grade = float(graph["bake"]["max_grade"])
    for along, across in (
            (0, 0), (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1)):
        sample_x = (x + forward[0] * along * half_length +
                    right[0] * across * half_width)
        sample_z = (z + forward[1] * along * half_length +
                    right[1] * across * half_width)
        cell_x = int(round((sample_x - origin_x) / cell_size))
        cell_z = int(round((sample_z - origin_z) / cell_size))
        if not (0 <= cell_x < width and 0 <= cell_z < height):
            return False
        index = cell_z * width + cell_x
        sample_height = graph["heights_mm"][index]
        if int(graph["hazards"][index]) & 3:
            return False
        horizontal = math.hypot(sample_x - x, sample_z - z)
        # Ordinary height=None cells may be conservative structure raster near
        # an otherwise safe terrain footprint. Raw terrain grade/support is
        # proven by the baker and recorded in validation metadata; only a
        # present graph height can be independently cross-checked here.
        if (sample_height is not None and horizontal > 0.0 and
                abs(float(sample_height) / 1000.0 - ground_y) >
                horizontal * maximum_grade):
            return False
    return True


class CanonicalSpawnFormationContractTest(unittest.TestCase):
    def test_airfield_team1_slot14_moves_off_the_unsafe_legacy_pose(self):
        graph = json.loads(AIRFIELD_GRAPH.read_text())
        validation = graph["validation"]
        half_width = float(validation["spawn_vehicle_half_width_metres"])
        half_length = float(validation["spawn_vehicle_half_length_metres"])
        legacy = (306.0, 11.4, -190.0, -1.602537)

        self.assertFalse(
            _terrain_footprint_clear(graph, legacy, half_width, half_length),
            "regression fixture no longer proves the legacy Obj.212 pose unsafe",
        )
        actual = graph["spawn_formations"]["1"][14]
        self.assertNotEqual((306.0, -190.0),
                            (float(actual[0]), float(actual[2])))
        self.assertTrue(
            _terrain_footprint_clear(graph, actual, half_width, half_length)
        )

    def test_airfield_all_spawn_footprints_have_terrain_clearance(self):
        graph = json.loads(AIRFIELD_GRAPH.read_text())
        validation = graph["validation"]
        self.assertIs(
            True, validation.get("spawn_terrain_footprint_clearance"),
            "31_airfield must be baked with full terrain-footprint clearance",
        )
        half_width = float(validation["spawn_vehicle_half_width_metres"])
        half_length = float(validation["spawn_vehicle_half_length_metres"])

        for team in ("1", "2"):
            for slot, pose in enumerate(graph["spawn_formations"][team]):
                with self.subTest(team=team, slot=slot, pose=pose):
                    self.assertTrue(
                        _terrain_footprint_clear(
                            graph, pose, half_width, half_length
                        )
                    )

    def test_north_america_slots_10_and_12_are_baked_clear_of_soft_props(self):
        graph = json.loads(NORTH_AMERICA_GRAPH.read_text())
        validation = graph.get("validation") or {}
        required_validation = {
            "spawn_slots_per_team",
            "spawn_compiled_bsp_obb_clearance",
            "spawn_pairwise_obb_clearance",
            "spawn_vehicle_half_width_metres",
            "spawn_vehicle_half_length_metres",
            "spawn_vehicle_resources_scanned",
        }
        missing = sorted(required_validation.difference(validation))
        self.assertIsInstance(
            graph.get("spawn_formations"),
            dict,
            "42_north_america must ship canonical poses; runtime projection "
            "cannot repair legacy team-1 slots 10/12",
        )
        self.assertEqual([], missing, "incomplete spawn bake validation: %s" % missing)
        self.assertEqual(15, validation["spawn_slots_per_team"])
        self.assertIs(True, validation["spawn_compiled_bsp_obb_clearance"])
        self.assertIs(True, validation["spawn_pairwise_obb_clearance"])
        self.assertGreater(validation["spawn_vehicle_resources_scanned"], 0)

        formations = graph["spawn_formations"]
        self.assertEqual({"1", "2"}, set(formations))
        self.assertEqual(15, len(formations["1"]))
        self.assertEqual(15, len(formations["2"]))
        self.assertTrue(all(
            len(pose) == 4 and all(math.isfinite(float(value)) for value in pose)
            for team in formations.values() for pose in team
        ))

        half_width = float(validation["spawn_vehicle_half_width_metres"])
        half_length = float(validation["spawn_vehicle_half_length_metres"])
        self.assertGreater(half_width, 0.0)
        self.assertGreater(half_length, 0.0)
        home, enemy = graph["bases"]
        legacy_yaw = math.atan2(enemy[0] - home[0], enemy[1] - home[1])
        for slot, obstacle in SOFT_SPAWN_OBSTACLES.items():
            legacy_x, legacy_z = LEGACY_SLOT_POSES[slot]
            self.assertTrue(
                _polygons_overlap(
                    _vehicle_obb(
                        legacy_x, legacy_z, legacy_yaw,
                        half_width, half_length
                    ),
                    obstacle,
                ),
                "regression fixture no longer proves legacy slot %d is blocked" % slot,
            )
            x, unused_y, z, yaw = formations["1"][slot]
            self.assertFalse(
                _polygons_overlap(
                    _vehicle_obb(
                        float(x), float(z), float(yaw), half_width, half_length
                    ),
                    obstacle,
                ),
                "team-1 slot %d still overlaps a retail soft obstacle" % slot,
            )
            cell_size = float(graph["cell_size"])
            cell_x = int(round((float(x) - graph["origin"][0]) / cell_size))
            cell_z = int(round((float(z) - graph["origin"][1]) / cell_size))
            index = cell_z * int(graph["width"]) + cell_x
            self.assertAlmostEqual(
                graph["origin"][0] + cell_x * cell_size, float(x), places=6
            )
            self.assertAlmostEqual(
                graph["origin"][1] + cell_z * cell_size, float(z), places=6
            )
            self.assertAlmostEqual(
                float(graph["heights_mm"][index]) / 1000.0,
                float(unused_y),
                places=6,
            )
            self.assertEqual(0, int(graph["hazards"][index]))
            self.assertGreaterEqual(bin(int(graph["links"][index])).count("1"), 3)

        source = OFFLINE_BATTLE.read_text()
        start = source.index("\t\t\tdef _formation_slot")
        end = source.index("globals()['g_offline_formation_slot']", start)
        consumer = source[start:end]
        self.assertTrue(
            any(token in consumer for token in (
                "spawn_formations", "SpawnPlanner", "spawn_pose",
            )),
            "stock-map formation slots must consume the baked pose unchanged",
        )


class CanonicalLANManifestContractTest(unittest.TestCase):
    def test_authority_pose_survives_relay_manifest_and_late_join_payload(self):
        state = BattleState(map_name="42_north_america")
        state.phase = "battle"
        state.bot_authority_id = 91
        state.bot_navigation.configure = lambda *unused: False
        identity = next(
            entry for entry in state.bot_roster
            if entry["team"] == 1 and entry["slot"] == 10
        )
        state.bot_roster = [identity]
        expected_pose = {
            "world_pose": True,
            "x": -17.25,
            "y": 0.625,
            "z": 31.75,
            "yaw": -0.4375,
        }
        bot = dict(expected_pose)
        bot.update({
            "id": identity["id"],
            "team": identity["team"],
            "slot": identity["slot"],
            "vehicle": "usa:T21",
            "max_health": 590,
            "health": 590,
        })

        self.assertTrue(state.update_bot_manifest(91, {
            "bots": [bot],
            "round_id": state.round_id,
            "manifest_nonce": "canonical-pose-1",
            "map_frame": {"origin": [0.0, 0.0], "axis": [0.0, 1.0]},
        }))
        relayed = {
            "server": state.bot_manifest[0],
            "event": state.pending_events[-1]["bots"][0],
            "late_join": state.current_battle_message()["bot_manifest"][0],
        }
        actual = {
            channel: {key: record.get(key) for key in expected_pose}
            for channel, record in relayed.items()
        }
        self.assertEqual(
            {channel: expected_pose for channel in relayed},
            actual,
            "replicas must receive the authority's canonical spawn pose",
        )

    def test_publisher_and_replica_use_one_world_pose_without_reprojection(self):
        network_source = NETWORK_BATTLE.read_text()
        publish_start = network_source.index("def publish_bot_manifest")
        publish_end = network_source.index("\ndef ", publish_start + 1)
        publisher = network_source[publish_start:publish_end]

        offline_source = OFFLINE_BATTLE.read_text()
        setup_start = offline_source.index(
            "from gui.mods.offhangar.network_battle import "
            "network_is_authority, publish_bot_manifest"
        )
        replica_start = offline_source.index("elif _is_network:", setup_start)
        replica_end = offline_source.index(
            "LOG_DEBUG('LAN bot manifest setup failed:'", replica_start
        )
        replica = offline_source[replica_start:replica_end]

        violations = []
        if not re.search(r"['\"]world_pose['\"]\s*:\s*True", publisher):
            violations.append("authority manifest does not mark its pose canonical")
        if re.search(
                r"_server_pose_from_world\(\s*player\s*,\s*world_x\s*,\s*0\.0\s*,",
                publisher):
            violations.append("authority discards the baked y coordinate")
        if "_world_from_server" not in replica:
            violations.append("replica does not consume manifest x/y/z")
        if "_world_yaw_from_server" not in replica:
            violations.append("replica does not consume manifest yaw")
        if "_formation_slot(_jt, _jslot)" in replica:
            violations.append("replica re-runs the formation resolver")
        self.assertEqual([], violations, "; ".join(violations))


if __name__ == "__main__":
    unittest.main()
