import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BATTLE_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
)
PROJECTILE_RUNTIME_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/projectile_runtime.py"
)


class Vector3(object):
    def __init__(self, *args):
        if len(args) == 1:
            value = args[0]
            if hasattr(value, "x"):
                args = (value.x, value.y, value.z)
            else:
                args = (value[0], value[1], value[2])
        self.x = float(args[0])
        self.y = float(args[1])
        self.z = float(args[2])

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __add__(self, other):
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalise(self):
        length = self.length
        if length > 1e-12:
            self.x /= length
            self.y /= length
            self.z /= length

    def scale(self, amount):
        return Vector3(self.x * amount, self.y * amount, self.z * amount)

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z


class CompatDict(dict):
    def iteritems(self):
        return self.items()

    def __bool__(self):
        # Python 2's empty fallback is another dict with iteritems(). Keep that
        # contract when the extracted client helper executes under Python 3.
        return True


class PlaneVehicle(object):
    def __init__(self, entity_id, x, y, visible=True):
        self.id = entity_id
        self.x = float(x)
        self.position = Vector3(x, y, 0.0)
        self.health = 100
        self.isAlive = True
        self._spot_visible = visible
        self.collision_calls = 0

    def collideSegment(self, start, end):
        self.collision_calls += 1
        dx = end.x - start.x
        if abs(dx) <= 1e-12:
            return None
        fraction = (self.x - start.x) / dx
        if not 0.0 <= fraction <= 1.0:
            return None
        distance = (end - start).length * fraction
        return (distance, "hull", 0, 0)


def _load_runtime():
    spec = importlib.util.spec_from_file_location(
        "gun_marker_projectile_runtime", PROJECTILE_RUNTIME_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _world_plane_hit(x):
    x = float(x)

    def collide(start, end):
        start = Vector3(start)
        end = Vector3(end)
        dx = end.x - start.x
        if abs(dx) <= 1e-12:
            return None, 999999.0
        fraction = (x - start.x) / dx
        if not 0.0 <= fraction <= 1.0:
            return None, 999999.0
        segment = end - start
        point = start + segment.scale(fraction)
        return (point, "terrain"), segment.length * fraction

    return collide


class GunMarkerBallisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = _load_runtime()
        source = BATTLE_PATH.read_text()
        start = source.index("def _offh_vec3_tuple(")
        end = source.index("def _offh_live_projectile_tick(", start)
        cls.namespace = {"LOG_CURRENT_EXCEPTION": lambda: None}
        exec(source[start:end], cls.namespace)

        cls.math_module = types.ModuleType("Math")
        cls.math_module.Vector3 = Vector3
        cls.gui_module = types.ModuleType("gui")
        cls.gui_module.__path__ = []
        cls.mods_module = types.ModuleType("gui.mods")
        cls.mods_module.__path__ = []
        cls.offhangar_module = types.ModuleType("gui.mods.offhangar")
        cls.offhangar_module.__path__ = []
        cls.offhangar_module.projectile_runtime = cls.runtime
        cls.pen_module = types.ModuleType(
            "gui.mods.offhangar.pen_indicator"
        )
        cls.pen_module.coll_data_from_collision = (
            lambda vehicle, collision, point, team: {
                "vehicle_id": vehicle.id,
                "team": team,
            }
        )
        cls.offhangar_module.pen_indicator = cls.pen_module

    def setUp(self):
        modules = {
            "Math": self.math_module,
            "gui": self.gui_module,
            "gui.mods": self.mods_module,
            "gui.mods.offhangar": self.offhangar_module,
            "gui.mods.offhangar.projectile_runtime": self.runtime,
            "gui.mods.offhangar.pen_indicator": self.pen_module,
        }
        self.module_patch = mock.patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    def _preview(self, vehicles, world_x, max_time=1.0):
        self.namespace["_offh_live_projectile_world_hit"] = (
            _world_plane_hit(world_x)
        )
        return self.namespace["_offh_player_gun_marker_impact"](
            Vector3(0.0, 10.0, 0.0),
            Vector3(100.0, 0.0, 0.0),
            Vector3(0.0, -10.0, 0.0),
            CompatDict(vehicles), 1, 1, max_time, 100.0,
        )

    def test_marker_follows_gravity_to_the_first_static_impact(self):
        marker = self._preview({}, 50.0)

        self.assertIsNotNone(marker)
        self.assertAlmostEqual(50.0, marker[0].x, places=5)
        # t=0.5: y = 10 + 0*t - 10*t^2/2. A straight barrel ray
        # would incorrectly leave the marker at y=10.
        self.assertAlmostEqual(8.75, marker[0].y, places=5)
        self.assertGreater(marker[2], 50.0)
        self.assertLess(marker[2], 50.1)
        self.assertIsNone(marker[3])

    def test_marker_does_not_ignore_a_wall_one_metre_from_the_gun(self):
        marker = self._preview({}, 1.0)

        self.assertIsNotNone(marker)
        self.assertAlmostEqual(1.0, marker[0].x, places=5)
        self.assertLess(marker[2], 1.01)
        self.assertIsNone(marker[3])

    def test_marker_without_an_impact_clamps_to_shell_range(self):
        marker = self._preview({}, 1000.0)

        self.assertIsNotNone(marker)
        self.assertAlmostEqual(100.0, marker[2], places=5)
        self.assertAlmostEqual(
            100.0,
            (marker[0] - Vector3(0.0, 10.0, 0.0)).length,
            places=5,
        )
        self.assertLess(marker[0].y, 10.0)
        self.assertIsNone(marker[3])

    def test_marker_selects_the_nearest_dynamic_or_static_impact(self):
        vehicle = PlaneVehicle(2, 40.0, 9.2)
        marker = self._preview({2: vehicle}, 60.0)

        self.assertAlmostEqual(40.0, marker[0].x, places=5)
        self.assertAlmostEqual(9.2, marker[0].y, places=5)
        self.assertEqual(2, marker[3]["vehicle_id"])

        vehicle = PlaneVehicle(2, 40.0, 9.2)
        marker = self._preview({2: vehicle}, 30.0)

        self.assertAlmostEqual(30.0, marker[0].x, places=5)
        self.assertIsNone(marker[3])

    def test_hidden_vehicle_does_not_leak_through_marker_preview(self):
        hidden = PlaneVehicle(2, 30.0, 9.55, visible=False)
        marker = self._preview({2: hidden}, 60.0)

        self.assertAlmostEqual(60.0, marker[0].x, places=5)
        self.assertIsNone(marker[3])
        self.assertEqual(0, hidden.collision_calls)

        # Visibility filters presentation only. The authoritative live shell
        # must still collide with an unspotted vehicle in its physical path.
        impact = self.namespace["_offh_projectile_chord_impact"](
            (25.0, 9.7, 0.0), (35.0, 9.4, 0.0),
            CompatDict({2: hidden}), 1,
            {2: (30.0, 9.55, 0.0)}, {2: (30.0, 9.55, 0.0)},
            0.0, 1.0, False,
        )
        self.assertEqual("vehicle", impact["kind"])

    def test_marker_vehicle_envelope_rejects_off_axis_vehicles_once(self):
        vehicles = CompatDict()
        for entity_id in range(2, 31):
            vehicles[entity_id] = PlaneVehicle(
                entity_id, 20.0 + entity_id, 10.0
            )
            vehicles[entity_id].position.z = 100.0
        target = PlaneVehicle(31, 40.0, 9.2)
        vehicles[31] = target

        candidates = self.namespace[
            "_offh_player_gun_marker_vehicle_candidates"
        ](
            Vector3(0.0, 10.0, 0.0),
            Vector3(100.0, 0.0, 0.0),
            Vector3(0.0, -10.0, 0.0),
            1.0,
            vehicles,
            1,
        )

        self.assertEqual([31], sorted(candidates))

    def test_marker_vehicle_envelope_is_executable_equivalent(self):
        vehicles = CompatDict({
            2: PlaneVehicle(2, 40.0, 9.2),
            3: PlaneVehicle(3, 25.0, 9.7, visible=False),
            4: PlaneVehicle(4, 30.0, 50.0),
            5: PlaneVehicle(5, 55.0, 8.5),
        })
        vehicles[4].position.z = 100.0
        original_filter = self.namespace[
            "_offh_player_gun_marker_vehicle_candidates"
        ]

        def marker_signature(marker):
            return (
                tuple(round(marker[0][index], 8) for index in range(3)),
                tuple(round(marker[1][index], 8) for index in range(3)),
                round(marker[2], 8),
                marker[3],
            )

        filtered = marker_signature(self._preview(vehicles, 60.0))
        self.namespace["_offh_player_gun_marker_vehicle_candidates"] = (
            lambda start, velocity, gravity, max_time, all_vehicles, shooter_id,
            profile_candidates=False:
            all_vehicles
        )
        try:
            unfiltered = marker_signature(self._preview(vehicles, 60.0))
        finally:
            self.namespace[
                "_offh_player_gun_marker_vehicle_candidates"
            ] = original_filter

        self.assertEqual(unfiltered, filtered)

    def test_live_projectile_keeps_nearest_hit_order_after_refactor(self):
        def run(world_x, vehicle_x):
            vehicle_t = vehicle_x / 100.0
            vehicle = PlaneVehicle(
                2, vehicle_x, 10.0 - 5.0 * vehicle_t * vehicle_t
            )
            vehicles = CompatDict({2: vehicle})
            events = []
            state = {
                "start": (0.0, 10.0, 0.0),
                "velocity": (100.0, 0.0, 0.0),
                "gravity": (0.0, -10.0, 0.0),
                "last_t": 0.0,
                "max_time": 1.0,
                "travelled": 0.0,
                "vehicles": vehicles,
                "shooter_id": 1,
                "target_positions": {2: tuple(vehicle.position[i] for i in range(3))},
                "shot_id": None,
                "on_vehicle_hit": lambda *args: events.append(
                    ("vehicle", args[0].id)
                ),
                "on_world_hit": lambda *args: events.append(("world", None)),
            }
            runtime_id = "nearest-order"
            self.namespace["g_offh_live_projectiles"] = {runtime_id: state}
            self.namespace["_offh_live_projectile_world_hit"] = (
                _world_plane_hit(world_x)
            )
            active = self.namespace["_offh_live_projectile_advance"](
                runtime_id, state, 0.75
            )
            self.assertFalse(active)
            return events

        self.assertEqual([("vehicle", 2)], run(30.0, 20.0))
        self.assertEqual([("world", None)], run(15.0, 20.0))

    def test_live_projectile_does_not_ignore_a_wall_one_metre_from_the_gun(self):
        events = []
        state = {
            "start": (0.0, 10.0, 0.0),
            "velocity": (100.0, 0.0, 0.0),
            "gravity": (0.0, -10.0, 0.0),
            "last_t": 0.0,
            "max_time": 1.0,
            "travelled": 0.0,
            "vehicles": CompatDict(),
            "shooter_id": 1,
            "target_positions": {},
            "shot_id": None,
            "on_vehicle_hit": lambda *args: events.append("vehicle"),
            "on_world_hit": lambda *args: events.append("world"),
        }
        runtime_id = "near-muzzle-world"
        self.namespace["g_offh_live_projectiles"] = {runtime_id: state}
        self.namespace["_offh_live_projectile_world_hit"] = (
            _world_plane_hit(1.0)
        )

        active = self.namespace["_offh_live_projectile_advance"](
            runtime_id, state, 0.025
        )

        self.assertFalse(active)
        self.assertEqual(["world"], events)


if __name__ == "__main__":
    unittest.main()
