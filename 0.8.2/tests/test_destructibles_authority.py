import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/destructibles_authority.py"
)


class Vector3:
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    @property
    def length(self):
        return math.sqrt(
            self.x * self.x + self.y * self.y + self.z * self.z
        )


class Matrix:
    def __init__(self, scale):
        self.scale = float(scale)

    def applyVector(self, vector):
        return Vector3(
            vector.x * self.scale,
            vector.y * self.scale,
            vector.z * self.scale,
        )


class DestructiblesAuthorityTest(unittest.TestCase):
    TREE = 1
    COLUMN = 2
    FRAGILE = 3
    STRUCTURE = 4

    def setUp(self):
        self.saved_modules = dict(sys.modules)
        self.descriptors = {}
        self.descriptors_by_filename = {}
        self.scales = {}

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_getDestructibleMatrix = (
            lambda space_id, chunk_id, item_index:
            self.scales.get((space_id, chunk_id, item_index))
        )

        math_module = types.ModuleType("Math")
        math_module.Vector3 = Vector3
        math_module.Matrix = lambda value: value

        area = types.ModuleType("AreaDestructibles")
        area.DESTR_TYPE_TREE = self.TREE
        area.DESTR_TYPE_FALLING_ATOM = self.COLUMN
        area.DESTR_TYPE_FRAGILE = self.FRAGILE
        area.DESTR_TYPE_STRUCTURE = self.STRUCTURE
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=1000.0,
            getDestructibleDesc=(
                lambda space_id, chunk_id, item_index:
                self.descriptors.get((space_id, chunk_id, item_index))
            ),
            getDescByFilename=(
                lambda filename: self.descriptors_by_filename.get(filename)
            ),
        )

        cache = types.ModuleType("DestructiblesCache")
        cache.DESTR_TYPE_STRUCTURE = self.STRUCTURE
        cache.scaledDestructibleHealth = (
            lambda scale, health:
            int(math.ceil(float(scale) * float(scale) * int(health)))
        )

        constants = types.ModuleType("constants")
        constants.DESTRUCTIBLE_MATKIND = types.SimpleNamespace(
            NORMAL_MIN=73,
            NORMAL_MAX=86,
        )

        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        sys.modules["AreaDestructibles"] = area
        sys.modules["DestructiblesCache"] = cache
        sys.modules["constants"] = constants

        spec = importlib.util.spec_from_file_location(
            "destructibles_authority_under_test", MODULE_PATH
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def add_item(self, item_index, descriptor, scale=1.0, space_id=7,
                 chunk_id=100):
        key = (space_id, chunk_id, item_index)
        self.descriptors[key] = descriptor
        self.scales[key] = Matrix(scale)
        return key

    def test_collision_health_scales_non_structure_health(self):
        self.add_item(2, {
            "type": self.TREE,
            "health": 10,
        }, scale=1.5)

        self.assertEqual(23, self.module.collision_health(7, 100, 2))

    def test_crushability_uses_retail_mass_speed_and_scaled_health(self):
        descriptor = {
            "type": self.FRAGILE,
            "health": 2,
            "kineticDamageCorrection": 0.0,
        }
        self.descriptors_by_filename["fence.model"] = descriptor
        self.scales[(7, 100, 2)] = Matrix(1.0)
        owner = types.SimpleNamespace(
            typeDescriptor=types.SimpleNamespace(
                physics={"weight": 20000.0}
            )
        )

        self.assertFalse(self.module.can_crush(
            owner, 7, 100, 2, 73, "fence.model", 1.0
        ))
        self.assertTrue(self.module.can_crush(
            owner, 7, 100, 2, 73, "fence.model", 2.0
        ))

    def test_collision_health_returns_complete_structure_module_dict(self):
        self.add_item(3, {
            "type": self.STRUCTURE,
            "modules": {
                73: {"health": 20},
                75: {"health": 7},
            },
            "destroyDepends": {73: set([75])},
        }, scale=2.0)

        health = self.module.collision_health(7, 100, 3)

        self.assertEqual({73: 80, 75: 28}, health)
        self.assertNotIn(86, health)

    def test_structure_rejects_executable_health_slot_overflow_key(self):
        self.add_item(31, {
            "type": self.STRUCTURE,
            "modules": {86: {"health": 10}},
            "destroyDepends": {},
        })

        with self.assertRaises(ValueError):
            self.module.collision_health(7, 100, 31)

    def test_negative_reference_health_remains_unbreakable(self):
        self.add_item(32, {
            "type": self.TREE,
            "health": -2,
        }, scale=1.5)
        self.module.destroy_tree = lambda *args: self.fail(
            "negative health must not dispatch destruction"
        )

        self.assertEqual(-4, self.module.collision_health(7, 100, 32))
        with self.assertRaises(RuntimeError):
            self.module.apply_collision_damage(
                7, 100, 32, 71, 1,
                Vector3(0, 0, 0), 0.0, 1.0
            )

    def test_partial_damage_updates_health_without_visual_destroy(self):
        self.add_item(4, {
            "type": self.TREE,
            "health": 10,
        })
        calls = []
        self.module.destroy_tree = lambda *args: calls.append(args)

        destroyed = self.module.apply_collision_damage(
            7, 100, 4, 71, 4, Vector3(1, 2, 3), 0.5, 6.0
        )

        self.assertFalse(destroyed)
        self.assertEqual(6, self.module.collision_health(7, 100, 4))
        self.assertEqual([], calls)

    def test_lethal_damage_dispatches_each_existing_destroy_api(self):
        cases = (
            (10, self.TREE, "destroy_tree", (7, 100, 10, 0.5, 6.0)),
            (11, self.COLUMN, "destroy_column", (7, 100, 11, 0.5, 6.0)),
            (12, self.FRAGILE, "destroy_fragile", (7, 100, 12)),
        )
        point = Vector3(1, 2, 3)
        calls = []

        for item_index, kind, method_name, expected_prefix in cases:
            self.add_item(item_index, {"type": kind, "health": 5})

            def destroy(*args, _name=method_name):
                calls.append((_name, args))
                return True

            setattr(self.module, method_name, destroy)
            self.assertTrue(self.module.apply_collision_damage(
                7, 100, item_index, 71, 5, point, 0.5, 6.0
            ))
            name, args = calls[-1]
            self.assertEqual(method_name, name)
            self.assertEqual(expected_prefix, args[:-1] if kind != self.FRAGILE
                             else args[:-1])
            self.assertIs(point, args[-1])
            self.assertEqual(
                0, self.module.collision_health(7, 100, item_index)
            )

    def test_structure_destruction_zeroes_transitive_dependencies(self):
        self.add_item(20, {
            "type": self.STRUCTURE,
            "modules": {
                73: {"health": 10},
                74: {"health": 8},
                75: {"health": 6},
            },
            "destroyDepends": {73: set([74, 75])},
        })
        point = Vector3(1, 2, 3)
        calls = []

        def destroy(*args):
            calls.append(args)
            return True

        self.module.destroy_module = destroy

        self.assertTrue(self.module.apply_collision_damage(
            7, 100, 20, 73, 10, point, 0.25, 4.0
        ))

        self.assertEqual(
            (7, 100, 20, 73, point, False), calls[0]
        )
        self.assertEqual(
            {73: 0, 74: 0, 75: 0},
            self.module.collision_health(7, 100, 20),
        )
        self.assertTrue(self.module.is_destroyed(100, 20, 73))
        self.assertTrue(self.module.is_destroyed(100, 20, 74))
        self.assertTrue(self.module.is_destroyed(100, 20, 75))

    def test_terminal_damage_is_idempotent(self):
        self.add_item(30, {"type": self.FRAGILE, "health": 1})
        calls = []

        def destroy(*args):
            calls.append(args)
            return True

        self.module.destroy_fragile = destroy
        args = (7, 100, 30, 71, 1, Vector3(1, 2, 3), 0.0, 1.0)

        self.assertTrue(self.module.apply_collision_damage(*args))
        self.assertTrue(self.module.apply_collision_damage(*args))
        self.assertEqual(1, len(calls))

    def test_reset_and_space_change_clear_partial_health(self):
        descriptor = {"type": self.TREE, "health": 10}
        self.add_item(40, descriptor, space_id=7)
        self.add_item(40, descriptor, space_id=8)

        self.assertFalse(self.module.apply_collision_damage(
            7, 100, 40, 71, 4, Vector3(0, 0, 0), 0.0, 1.0
        ))
        self.assertEqual(6, self.module.collision_health(7, 100, 40))

        self.module.reset(7)
        self.assertEqual(10, self.module.collision_health(7, 100, 40))
        self.assertFalse(self.module.apply_collision_damage(
            7, 100, 40, 71, 3, Vector3(0, 0, 0), 0.0, 1.0
        ))
        self.assertEqual(7, self.module.collision_health(7, 100, 40))
        self.assertEqual(10, self.module.collision_health(8, 100, 40))

    def test_unavailable_descriptor_or_matrix_returns_none_without_state(self):
        self.assertIsNone(self.module.collision_health(7, 100, 50))
        self.descriptors[(7, 100, 50)] = {
            "type": self.TREE,
            "health": 10,
        }
        self.assertIsNone(self.module.collision_health(7, 100, 50))

        self.scales[(7, 100, 50)] = Matrix(1.0)
        self.assertEqual(10, self.module.collision_health(7, 100, 50))

    def test_invalid_structure_material_and_non_finite_data_are_rejected(self):
        self.add_item(60, {
            "type": self.STRUCTURE,
            "modules": {73: {"health": 10}},
            "destroyDepends": {},
        })

        with self.assertRaises(ValueError):
            self.module.apply_collision_damage(
                7, 100, 60, 74, 1, Vector3(0, 0, 0), 0.0, 1.0
            )
        with self.assertRaises(ValueError):
            self.module.apply_collision_damage(
                7, 100, 60, 73, 0, Vector3(0, 0, 0), 0.0, 1.0
            )
        with self.assertRaises(ValueError):
            self.module.apply_collision_damage(
                7, 100, 60, 73, 1,
                Vector3(float("nan"), 0, 0), 0.0, 1.0
            )

    def test_terminal_delivery_failure_is_explicit(self):
        self.add_item(70, {"type": self.TREE, "health": 5})
        self.module.destroy_tree = lambda *args: False

        with self.assertRaises(RuntimeError):
            self.module.apply_collision_damage(
                7, 100, 70, 71, 5,
                Vector3(0, 0, 0), 0.0, 1.0
            )

        self.assertEqual(5, self.module.collision_health(7, 100, 70))

    def test_failed_delivery_does_not_commit_or_retry_ambiguous_order(self):
        class Manager(object):
            fail = True
            calls = []

            def orderDestructibleDestroy(self, *args):
                self.calls.append(args)
                if self.fail:
                    raise RuntimeError("direct delivery failed")

        area = sys.modules["AreaDestructibles"]
        area._DAMAGE_TYPE_TREE = 1
        area._DAMAGE_TYPE_COLUMN = 2
        area._DAMAGE_TYPE_FRAGILE = 3
        area._DAMAGE_TYPE_MODULE = 4
        manager = Manager()
        area.g_destructiblesManager = manager
        # A controller may legitimately still be spawning. In that case the
        # retail manager's direct order queue is the only delivery receipt.
        self.module._ensure_chunk = lambda *unused: None
        point = Vector3(1, 2, 3)

        self.assertFalse(self.module._apply(
            7, 100, point, "fragile", 12, (12, None)
        ))
        self.assertFalse(self.module.is_destroyed(100, 12))
        self.assertEqual(
            [], self.module._state["chunks"][100]["destroyedFragiles"]
        )

        manager.fail = False
        self.assertFalse(self.module._apply(
            7, 100, point, "fragile", 12, (12, None)
        ))
        self.assertFalse(self.module.is_destroyed(100, 12))
        self.assertEqual(
            [], self.module._state["chunks"][100]["destroyedFragiles"]
        )
        self.assertEqual(1, len(manager.calls))

    def test_controller_path_uses_one_native_delivery_without_setter(self):
        class Controller(object):
            def __init__(self):
                self.destroyedFragiles = []
                self._AreaDestructibles__prevDestroyedFragiles = frozenset()

            def set_destroyedFragiles(self, unused):
                self.fail("controller setter must not issue a second order")

        class Manager(object):
            def __init__(self):
                self.orders = []

            def orderDestructibleDestroy(self, *args):
                self.orders.append(args)

        area = sys.modules["AreaDestructibles"]
        area._DAMAGE_TYPE_TREE = 1
        area._DAMAGE_TYPE_COLUMN = 2
        area._DAMAGE_TYPE_FRAGILE = 3
        area._DAMAGE_TYPE_MODULE = 4
        manager = Manager()
        controller = Controller()
        area.g_destructiblesManager = manager
        self.module._ensure_chunk = lambda *unused: controller
        point = Vector3(1, 2, 3)

        self.assertTrue(self.module._apply(
            7, 100, point, "fragile", 12, (12, None)
        ))
        self.assertEqual([(100, 3, 12, True)], manager.orders)
        self.assertEqual([12], controller.destroyedFragiles)
        self.assertEqual(
            frozenset([12]),
            controller._AreaDestructibles__prevDestroyedFragiles,
        )
        self.assertTrue(self.module.is_destroyed(100, 12))


if __name__ == "__main__":
    unittest.main()
