from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))


_bigworld_stub = types.ModuleType('BigWorld')
_math_stub = types.ModuleType('Math')
_math_stub.Vector3 = lambda value: value
with mock.patch.dict(
        sys.modules, {'BigWorld': _bigworld_stub, 'Math': _math_stub}):
    from gui.mods.offline_lan_0922 import destructibles_authority

from gui.mods.offline_lan_0922 import destructibles_compat
from gui.mods.offline_lan_0922 import destructibles_sensor


class _Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            x, y, z = x
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __add__(self, other):
        return _Vector(self.x + other.x, self.y + other.y,
                       self.z + other.z)

    def __sub__(self, other):
        return _Vector(self.x - other.x, self.y - other.y,
                       self.z - other.z)

    @property
    def length(self):
        return (self.x * self.x + self.y * self.y +
                self.z * self.z) ** 0.5

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length

    def scale(self, value):
        return _Vector(self.x * value, self.y * value, self.z * value)


def _mat_info_1513(collided=False, point=None, normal=None, mat_kind=0,
                   filename='', chunk_id=0, item_index=0):
    return (bool(collided), point or _Vector(), normal or _Vector(),
            int(mat_kind), filename, int(chunk_id), int(item_index))


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class _Manager(object):
    def __init__(self):
        self.space_id = None
        self.orders = []
        self.attempts = []
        self.fail = False
        self.controller = None

    def getSpaceID(self):
        return self.space_id

    def startSpace(self, space_id):
        self.space_id = space_id

    def isChunkLoaded(self, unused_chunk_id):
        return True

    def getController(self, unused_chunk_id):
        return self.controller

    def orderDestructibleDestroy(self, *args):
        self.attempts.append(args)
        if self.fail:
            raise RuntimeError('native destroy failed')
        self.orders.append(args)


def _authority_environment(manager):
    area = types.ModuleType('AreaDestructibles')
    area.g_destructiblesManager = manager
    area._DAMAGE_TYPE_TREE = 1
    area._DAMAGE_TYPE_COLUMN = 2
    area._DAMAGE_TYPE_FRAGILE = 3
    area._DAMAGE_TYPE_MODULE = 4
    area.encodeFragile = lambda item, shot: (int(item) << 8) | int(bool(shot))
    area.encodeFallenTree = lambda *args: ('tree',) + args
    area.encodeFallenColumn = lambda *args: ('column',) + args
    area.encodeDestructibleModule = lambda *args: ('module',) + args
    bigworld = types.SimpleNamespace(
        createEntity=lambda *unused: 900,
        wg_getChunkDestrFilenames=lambda *unused: (),
        wg_getDestructibleFallPitchConstr=lambda *unused: (None, 0))
    math_module = types.SimpleNamespace(Vector3=_Vector)
    destructibles_authority.BigWorld = bigworld
    destructibles_authority.Math = math_module
    destructibles_authority.reset()
    return area


class DestructiblesCompatibilityTests(unittest.TestCase):

    def tearDown(self):
        destructibles_sensor.set_event_sink(None)
        for name in ('g_offh_destr_seen', 'g_offh_destr_nodesc',
                     'g_offh_tree_state', 'g_offh_destr_ordered',
                     'g_offh_destr_chunks'):
            destructibles_sensor.__dict__.pop(name, None)
        destructibles_authority.reset()

    def test_restores_only_names_moved_by_1513(self):
        area = types.ModuleType('AreaDestructibles')
        cache = types.ModuleType('DestructiblesCache')
        cache.chunkIDFromPosition = object()
        cache.encodeFallenTree = object()
        cache.encodeFallenColumn = object()
        cache.encodeFragile = object()
        cache.encodeDestructibleModule = object()
        cache.DESTR_TYPE_TREE = 1
        cache.DESTR_TYPE_FALLING_ATOM = 2
        cache.DESTR_TYPE_FRAGILE = 3
        cache.DESTR_TYPE_STRUCTURE = 4

        destructibles_compat._INSTALLED = False
        with mock.patch.dict(
                sys.modules,
                {'AreaDestructibles': area, 'DestructiblesCache': cache}):
            self.assertTrue(destructibles_compat.install())

        self.assertIs(cache.encodeFallenTree, area.encodeFallenTree)
        self.assertIs(cache.chunkIDFromPosition, area.chunkIDFromPosition)
        self.assertIs(cache.encodeFallenColumn, area.encodeFallenColumn)
        self.assertIs(cache.encodeFragile, area.encodeFragile)
        self.assertIs(
            cache.encodeDestructibleModule,
            area.encodeDestructibleModule)
        self.assertEqual(1, area._DAMAGE_TYPE_TREE)
        self.assertEqual(2, area._DAMAGE_TYPE_COLUMN)
        self.assertEqual(3, area._DAMAGE_TYPE_FRAGILE)
        self.assertEqual(4, area._DAMAGE_TYPE_MODULE)
        self.assertEqual(1, area.DESTR_TYPE_TREE)
        self.assertEqual(2, area.DESTR_TYPE_FALLING_ATOM)
        self.assertEqual(3, area.DESTR_TYPE_FRAGILE)
        self.assertEqual(4, area.DESTR_TYPE_STRUCTURE)

    def test_does_not_replace_an_existing_client_name(self):
        original = object()
        area = types.ModuleType('AreaDestructibles')
        area.encodeFallenTree = original
        cache = types.ModuleType('DestructiblesCache')
        cache.chunkIDFromPosition = object()
        cache.encodeFallenTree = object()
        cache.encodeFallenColumn = object()
        cache.encodeFragile = object()
        cache.encodeDestructibleModule = object()
        cache.DESTR_TYPE_TREE = 1
        cache.DESTR_TYPE_FALLING_ATOM = 2
        cache.DESTR_TYPE_FRAGILE = 3
        cache.DESTR_TYPE_STRUCTURE = 4

        destructibles_compat._INSTALLED = False
        with mock.patch.dict(
                sys.modules,
                {'AreaDestructibles': area, 'DestructiblesCache': cache}):
            destructibles_compat.install()

        self.assertIs(original, area.encodeFallenTree)

    def test_sensor_publishes_normalized_client_report(self):
        events = []
        destructibles_sensor.set_event_sink(
            lambda event: events.append(event) or True)

        self.assertTrue(destructibles_sensor._publish_destroyed(
            'tree', 12, 3, (1, 2, 4), 0.75, 8.0,
            isShotDamage=True))

        self.assertEqual({
            'destructible_kind': 'tree', 'chunk_id': 12,
            'item_index': 3, 'x': 1.0, 'y': 2.0, 'z': 4.0,
            'fall_yaw': 0.75, 'speed': 8.0, 'is_shot': True,
        }, events[0])

    def test_sensor_does_not_silently_accept_failed_lan_report(self):
        destructibles_sensor.set_event_sink(lambda unused_event: False)

        with self.assertRaisesRegex(RuntimeError, 'not admitted'):
            destructibles_sensor._publish_destroyed(
                'fragile', 12, 3, (1, 2, 4))

    def test_fragile_uses_exact_1513_encoding_and_shot_sync_flag(self):
        manager = _Manager()
        area = _authority_environment(manager)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertTrue(destructibles_authority.destroy_fragile(
                1, 22, 37, (10.0, 2.0, 20.0), True))

        encoded = (37 << 8) | 1
        self.assertEqual([(22, 3, encoded, True, True)], manager.orders)
        chunk = destructibles_authority._state['chunks'][22]
        self.assertEqual([encoded], chunk['destroyedFragiles'])
        self.assertIn((37, None), chunk['keys'])

    def test_native_failure_does_not_commit_and_can_be_retried(self):
        manager = _Manager()
        manager.fail = True
        area = _authority_environment(manager)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            with self.assertRaisesRegex(RuntimeError, 'native destroy failed'):
                destructibles_authority.destroy_fragile(
                    1, 22, 37, (10.0, 2.0, 20.0), False)
            chunk = destructibles_authority._state['chunks'][22]
            self.assertEqual([], chunk['destroyedFragiles'])
            self.assertEqual(set(), chunk['keys'])

            manager.fail = False
            self.assertTrue(destructibles_authority.destroy_fragile(
                1, 22, 37, (10.0, 2.0, 20.0), False))

        self.assertEqual(2, len(manager.attempts))
        self.assertEqual(1, len(manager.orders))
        self.assertTrue(destructibles_authority.is_destroyed(22, 37))

    def test_controller_setter_failure_rolls_back_only_its_append(self):
        class Controller(object):
            def __init__(self):
                self.destroyedFragiles = []
                self._AreaDestructibles__prevDestroyedFragiles = frozenset()
                self.fail = True

            def set_destroyedFragiles(self, unused_previous):
                self._AreaDestructibles__prevDestroyedFragiles = frozenset(
                    self.destroyedFragiles)
                if self.fail:
                    raise RuntimeError('setter failed')

        manager = _Manager()
        manager.controller = Controller()
        area = _authority_environment(manager)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            with self.assertRaisesRegex(RuntimeError, 'setter failed'):
                destructibles_authority.destroy_fragile(
                    1, 22, 37, (10.0, 2.0, 20.0), False)
            chunk = destructibles_authority._state['chunks'][22]
            self.assertEqual([], manager.controller.destroyedFragiles)
            self.assertEqual(
                frozenset(),
                manager.controller._AreaDestructibles__prevDestroyedFragiles)
            self.assertEqual([], chunk['destroyedFragiles'])
            self.assertEqual(set(), chunk['keys'])

            manager.controller.fail = False
            self.assertTrue(destructibles_authority.destroy_fragile(
                1, 22, 37, (10.0, 2.0, 20.0), False))

        self.assertEqual([(37 << 8)],
                         manager.controller.destroyedFragiles)
        self.assertTrue(destructibles_authority.is_destroyed(22, 37))

    def test_failed_entity_creation_is_not_permanently_deduplicated(self):
        manager = _Manager()
        area = _authority_environment(manager)
        results = [None, 900]
        destructibles_authority.BigWorld.createEntity = (
            lambda *unused: results.pop(0))

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            with self.assertRaisesRegex(RuntimeError, 'was not created'):
                destructibles_authority.destroy_fragile(
                    1, 22, 37, (10.0, 2.0, 20.0), False)
            self.assertNotIn(22, destructibles_authority._state['entities'])
            self.assertTrue(destructibles_authority.destroy_fragile(
                1, 22, 37, (10.0, 2.0, 20.0), False))

        self.assertIn(22, destructibles_authority._state['entities'])

    def test_identified_destructible_native_rejection_is_not_passable(self):
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {'type': 3, 'health': 1})
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: False)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            with self.assertRaisesRegex(
                RuntimeError, 'native destructible destroy'):
                destructibles_sensor._try_destroy_destructible(
                    1, _mat_info_1513(
                        True, _Vector(), _Vector(0, 1, 0), 75,
                        'fence', 22, 37),
                    0.0, 6.0)

    def test_exact_1513_hit_preserves_chunk_and_item_order(self):
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda filename: (
                {'type': 3, 'health': 1} if filename == 'fence' else None))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)
        hit_point = _Vector(10, 2, 20)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(
                    True, hit_point, _Vector(0, 1, 0), 75,
                    'fence', 22, 37),
                0.25, 6.0, True))

        self.assertEqual(1, len(calls))
        self.assertEqual((1, 22, 37), calls[0][:3])
        self.assertIs(hit_point, calls[0][3])
        self.assertTrue(calls[0][4])

    def test_exact_1513_miss_does_not_touch_destructibles_runtime(self):
        with mock.patch.dict(sys.modules, {'AreaDestructibles': None}):
            self.assertFalse(destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(False), 0.0, 6.0))

    def test_material_hit_payload_rejects_legacy_or_unknown_width(self):
        for payload in (
                (_Vector(), _Vector(), 22, 37, 75, 'fence'),
                (True, _Vector(), _Vector(), 75, 'fence', 22, 37,
                 'extra')):
            with self.assertRaisesRegex(
                    RuntimeError, 'must contain 7 items'):
                destructibles_sensor._try_destroy_destructible(
                    1, payload, 0.0, 6.0)

        with self.assertRaisesRegex(RuntimeError, 'must be a tuple'):
            destructibles_sensor._try_destroy_destructible(
                1, list(_mat_info_1513(False)), 0.0, 6.0)

        payload = list(_mat_info_1513(False))
        payload[0] = 0
        with self.assertRaisesRegex(RuntimeError, 'collided flag must be bool'):
            destructibles_sensor._try_destroy_destructible(
                1, tuple(payload), 0.0, 6.0)

    def test_solid_probe_uses_1513_miss_sentinel(self):
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = (
            lambda *unused: _mat_info_1513(False))

        with mock.patch.dict(sys.modules, {'BigWorld': bigworld}):
            self.assertFalse(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(), _Vector(0, 0, 2), 0.0, 6.0))

    def test_shot_probe_uses_1513_miss_sentinel(self):
        start = _Vector()
        impact = _Vector(0, 0, 5)
        calls = []

        def collide(*unused):
            calls.append(unused)
            return (impact,)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=(
                lambda *unused: _mat_info_1513(False)))

        distance = destructibles_sensor.shot_world_distance(
            bigworld, 1, start, _Vector(0, 0, 20), _Vector(0, 0, 1))

        self.assertEqual(5.0, distance)
        self.assertEqual(1, len(calls))

    def test_fall_pitch_payload_is_strictly_two_items(self):
        manager = _Manager()
        area = _authority_environment(manager)
        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            for payload in (None, (None,), (None, 0, 'extra')):
                destructibles_authority.BigWorld.wg_getDestructibleFallPitchConstr = (
                    lambda *unused, **unused_kwargs: payload)
                with self.assertRaisesRegex(
                        RuntimeError,
                        'fall-pitch payload must contain 2 items'):
                    destructibles_authority.destroy_tree(
                        1, 22, 37, 0.0, 6.0, (10.0, 2.0, 20.0))

    def test_proximity_failure_is_retryable_and_uses_object_world_position(self):
        descriptor = {'type': 1, 'health': 10, 'mass': 20}
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6),
                    (1.6, 1.0, 3.6), None))))
        manager = _Manager()
        manager.space_id = 1
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: descriptor)
        chunk_matrix = types.SimpleNamespace(translation=_Vector(100, 5, 200))
        item_matrix = types.SimpleNamespace(translation=_Vector(2, 3, 4))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ('tree',)
        bigworld.wg_getChunkMatrix = lambda *unused: chunk_matrix
        bigworld.wg_getDestructibleMatrix = lambda *unused: item_matrix
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        calls = []
        accepted = [False, True]

        def destroy_tree(*args):
            calls.append(args)
            return accepted.pop(0)

        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_tree=destroy_tree)
        events = []
        destructibles_sensor.xrange = range
        destructibles_sensor.set_event_sink(
            lambda event: events.append(event) or True)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            with self.assertRaisesRegex(
                    RuntimeError, 'native proximity destroy'):
                destructibles_sensor._fell_trees_near(
                    1, _Vector(102, 8, 200), 0.0, 6.0,
                    type_descriptor)
            self.assertNotIn(
                (22, 0),
                destructibles_sensor.g_offh_tree_state['felled'])

            destructibles_sensor._fell_trees_near(
                1, _Vector(102, 8, 200), 0.0, 6.0,
                type_descriptor)

        object_position = calls[-1][-1]
        self.assertEqual((102.0, 8.0, 204.0),
                         (object_position.x, object_position.y,
                          object_position.z))
        self.assertIn((22, 0), destructibles_sensor.g_offh_tree_state['felled'])
        self.assertEqual((102.0, 8.0, 204.0),
                         (events[0]['x'], events[0]['y'], events[0]['z']))


if __name__ == '__main__':
    unittest.main()
