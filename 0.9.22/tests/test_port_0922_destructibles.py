from pathlib import Path
import json
import math
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
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
    """Build the exact native #1513 seven-item material payload."""
    return (bool(collided), point or _Vector(), normal or _Vector(),
            int(mat_kind), filename, int(item_index), int(chunk_id))


def _catalog(resources, instances=None, ambiguous_instances=None):
    catalog = {
        'format': 'offline-lan-0922-destructible-catalog',
        'version': 1,
        'game_version': '0.9.22',
        'map': '06_ensk',
        'locator_quantization': 1000,
        'resources': resources,
    }
    if instances is not None:
        catalog['version'] = 4
        catalog['instances'] = instances
        catalog['ambiguous_instances'] = ambiguous_instances or []
    return catalog


class _ItemMatrix(object):
    def __init__(self, translation=None, yaw=0.0, scale=1.0):
        import math
        self.translation = translation or _Vector()
        self._cos = math.cos(yaw)
        self._sin = math.sin(yaw)
        self._scale = float(scale)

    def applyVector(self, point):
        return _Vector(
            (point.x * self._cos + point.z * self._sin) * self._scale,
            point.y * self._scale,
            (-point.x * self._sin + point.z * self._cos) * self._scale)

    def applyPoint(self, point):
        return self.translation + self.applyVector(point)


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
        self._DestructiblesManager__loadedChunkIDs = {}
        self.orders = []
        self.attempts = []
        self.fail = False
        self.controller = None

    def getSpaceID(self):
        return self.space_id

    def startSpace(self, space_id):
        self.space_id = space_id

    def set_chunk_count(self, chunk_id, count):
        self._DestructiblesManager__loadedChunkIDs[int(chunk_id)] = int(count)

    def isChunkLoaded(self, chunk_id):
        return chunk_id in self._DestructiblesManager__loadedChunkIDs

    def onChunkLoad(self, chunk_id, count):
        self._DestructiblesManager__loadedChunkIDs[chunk_id] = count

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
    area.DESTR_TYPE_TREE = 1
    area.g_cache = types.SimpleNamespace(
        getDescByFilename=lambda filename: (
            {'type': 1, 'health': 10} if filename == 'tree' else None))
    area.encodeFragile = lambda item, shot: (int(item) << 8) | int(bool(shot))
    area.encodeFallenTree = lambda *args: ('tree',) + args
    area.encodeFallenColumn = lambda *args: ('column',) + args
    area.encodeDestructibleModule = lambda *args: ('module',) + args
    bigworld = types.SimpleNamespace(
        createEntity=lambda *unused: 900,
        wg_getChunkDestrFilenames=lambda *unused: ('tree',) * 256,
        wg_getDestructibleEffectCategory=lambda *unused: 1,
        wg_getDestructibleFallPitchConstr=lambda *unused: (None, 0))
    math_module = types.SimpleNamespace(Vector3=_Vector)
    destructibles_authority.BigWorld = bigworld
    destructibles_authority.Math = math_module
    destructibles_authority.reset()
    return area


class DestructiblesCompatibilityTests(unittest.TestCase):

    def setUp(self):
        destructibles_sensor.set_diagnostics(False)
        destructibles_compat.reset_safe_descriptor_cache()

    def tearDown(self):
        destructibles_sensor.set_event_sink(None)
        destructibles_sensor.set_diagnostics(False)
        destructibles_sensor.set_catalog(None)
        for name in ('g_offh_destr_seen', 'g_offh_destr_nodesc',
                     'g_offh_tree_state', 'g_offh_destr_ordered',
                     'g_offh_destr_chunks', 'g_offh_destr_instances',
                     'g_offh_destr_contact_bins',
                     'g_offh_destr_pending',
                     'g_offh_destr_speculative',
                     'g_offh_destr_falling_active',
                     'g_offh_destr_item_names',
                     'g_offh_destr_native_name_lists',
                     'g_offh_destr_item_name_budget',
                     'g_offh_destr_item_name_cache_serial',
                     'g_offh_destr_isolated_chunks',
                     'g_offh_destr_isolated_slots',
                     'g_offh_destr_name_unresolved_slots',
                     'g_offh_destr_isolation_logs',
                     'g_offh_destr_isolation_log_capped',
                     'g_offh_destr_diagnostics',
                     'g_offh_destr_diag_last_static',
                     'g_offh_destr_runtime_space'):
            destructibles_sensor.__dict__.pop(name, None)
        destructibles_authority.reset()
        destructibles_compat.reset_safe_descriptor_cache()

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

    def test_tree_descriptor_uses_nullable_chunk_list_not_scalar_wrapper(self):
        direct = mock.Mock(
            side_effect=AssertionError('unsafe scalar wrapper was called'))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getDestructibleFilename = direct
        bigworld.wg_getChunkDestrFilenames = mock.Mock(
            return_value=('tree-a',))
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 1
        logs = []

        class _Cache(object):
            def getDescByFilename(self, filename):
                if filename == 'tree-a':
                    return {'type': 1, 'health': 10}
                return None

        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.ClientDestructiblesCache = _Cache
        area._printErrDescNotAvailable = lambda *unused: None
        area.LOG_ERROR = logs.append
        area.g_destructiblesManager = manager
        instance = _Cache()
        area.g_cache = instance
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
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'BigWorld': bigworld}):
            destructibles_compat.install()
            self.assertEqual(
                10, instance.getDestructibleDesc(1, 22, 0)['health'])
            # The descriptor survives a later chunk unload for the animator's
            # delayed touchdown callback.
            bigworld.wg_getChunkDestrFilenames.return_value = None
            self.assertEqual(
                10, instance.getDestructibleDesc(1, 22, 0)['health'])
            area._printErrDescNotAvailable(1, 22, 9)

        direct.assert_not_called()
        self.assertEqual(1, bigworld.wg_getChunkDestrFilenames.call_count)
        self.assertIn('chunk: 22, id: 9', logs[0])

    def test_late_descriptor_callback_does_not_query_an_old_space(self):
        manager = _Manager()
        manager.space_id = 2
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = mock.Mock()
        bigworld.wg_getDestructibleEffectCategory = mock.Mock()
        cache = types.SimpleNamespace(getDescByFilename=mock.Mock())

        destructibles_sensor._clear_runtime_registry()
        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld}):
            self.assertEqual(
                ('pending', None),
                destructibles_compat.inspect_destructible_desc(
                    cache, 1, 22, 0))

        bigworld.wg_getChunkDestrFilenames.assert_not_called()
        bigworld.wg_getDestructibleEffectCategory.assert_not_called()
        cache.getDescByFilename.assert_not_called()
        self.assertFalse(getattr(
            destructibles_sensor, 'g_offh_destr_isolated_chunks', ()))
        self.assertFalse(getattr(
            destructibles_sensor, 'g_offh_destr_isolated_slots', ()))

    def test_safe_descriptor_cache_cannot_bypass_later_isolation(self):
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        descriptor = {'type': 1, 'health': 10}
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                descriptor if value == 'tree-a' else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ('tree-a',)
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 1

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            for scope in ('slot', 'chunk'):
                destructibles_sensor._clear_runtime_registry()
                destructibles_compat.reset_safe_descriptor_cache()
                self.assertEqual(
                    ('resolved', descriptor),
                    destructibles_compat.inspect_destructible_desc(
                        area.g_cache, 1, 22, 0))
                self.assertIn((22, 0), destructibles_compat._SAFE_DESC_BY_WIRE)
                destructibles_sensor._isolate_destructible_1513(
                    'test_isolation', 22,
                    0 if scope == 'slot' else None)
                self.assertEqual(
                    ('invalid', None),
                    destructibles_compat.inspect_destructible_desc(
                        area.g_cache, 1, 22, 0))
                self.assertNotIn(
                    (22, 0), destructibles_compat._SAFE_DESC_BY_WIRE)

    def test_safe_descriptor_cache_exception_isolates_exact_slot(self):
        filename = 'speedtree/test/oak.spt'
        descriptor = {'type': 1, 'health': 10}
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        lookup = mock.Mock(side_effect=(
            descriptor, RuntimeError('descriptor cache failed')))
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(getDescByFilename=lookup)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (filename,)
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 1

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertFalse(
                destructibles_sensor.validate_tree_identity_1513(
                    1, 22, 0))
            self.assertFalse(
                destructibles_sensor.validate_tree_identity_1513(
                    1, 22, 0))

        self.assertEqual(2, lookup.call_count)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'descriptor_cache'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertNotIn((22, 0), destructibles_compat._SAFE_DESC_BY_WIRE)

    def test_safe_descriptor_cache_rejects_malformed_truthy_payload(self):
        cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: object())

        with mock.patch.object(
                destructibles_sensor, 'resolve_native_item_name_1513',
                return_value=('exact', 'speedtree/test/oak.spt')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertEqual(
                ('invalid', None),
                destructibles_compat.inspect_destructible_desc(
                    cache, 1, 22, 0))

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertNotIn((22, 0), destructibles_compat._SAFE_DESC_BY_WIRE)

    def test_missing_tree_descriptor_stays_solid_before_native_destroy(self):
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = _authority_environment(manager)
        destructibles_authority.BigWorld.wg_getChunkDestrFilenames = (
            lambda *unused: ())
        fall_pitch = mock.Mock(
            side_effect=AssertionError('native fall query was called'))
        destructibles_authority.BigWorld.wg_getDestructibleFallPitchConstr = (
            fall_pitch)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': destructibles_authority.BigWorld}):
            self.assertFalse(destructibles_authority.destroy_tree(
                1, 22, 0, 0.0, 6.0, (10.0, 2.0, 20.0)))

        fall_pitch.assert_not_called()
        self.assertEqual([], manager.attempts)

    def test_loaded_missing_tree_identity_is_isolated_nonfatally(self):
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = _authority_environment(manager)
        destructibles_authority.BigWorld.wg_getChunkDestrFilenames = (
            lambda *unused: ())

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': destructibles_authority.BigWorld}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertFalse(
                destructibles_sensor.validate_tree_identity_1513(1, 22, 0))
            self.assertFalse(
                destructibles_sensor.validate_tree_identity_1513(1, 22, 0))

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'tree_descriptor'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_pending_tree_filename_list_retries_without_isolation(self):
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = _authority_environment(manager)
        destructibles_authority.BigWorld.wg_getChunkDestrFilenames = (
            mock.Mock(return_value=None))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': destructibles_authority.BigWorld}):
            self.assertFalse(
                destructibles_sensor.validate_tree_identity_1513(1, 22, 0))
            self.assertNotIn(
                'g_offh_destr_isolated_slots', destructibles_sensor.__dict__)
            destructibles_authority.BigWorld.wg_getChunkDestrFilenames.return_value = (
                'tree',)
            self.assertTrue(
                destructibles_sensor.validate_tree_identity_1513(1, 22, 0))

        self.assertNotIn(
            'g_offh_destr_isolation_logs', destructibles_sensor.__dict__)

    def test_untypeable_native_name_isolates_the_chunk(self):
        # A name the client cannot type cannot be assigned to any item: the
        # compacted list only says the chunk contains it.  The unknown owner
        # makes the whole compacted alignment unsafe, so no slot is admitted.
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 2)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: None)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = (
            lambda *unused: ('missing-tree',))
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 1
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: _ItemMatrix()
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        self.assertNotIn(
            'g_offh_destr_isolated_slots', destructibles_sensor.__dict__)
        self.assertEqual(
            {22}, destructibles_sensor.g_offh_destr_isolated_chunks)
        self.assertEqual(
            {'name_alignment'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_unresolved_name_item_is_skipped_before_matrix_query(self):
        # The name loop and category probe share one resolver.  A category
        # exception therefore identifies an unresolved, unnamed slot; retain
        # alignment for the other item but quarantine this slot before the
        # scanner can issue any matrix/effect/destruction call for it.
        tree = 'speedtree/test/oak.spt'
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 2)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 1, 'health': 10} if value == tree else None))
        category_calls = []
        matrix_calls = []
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (tree,)
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())

        def effect_category(space, chunk, item, module):
            category_calls.append((space, chunk, item, module))
            if item == 0:
                raise RuntimeError('native item is unresolved')
            return 1

        def item_matrix(space, chunk, item):
            matrix_calls.append((space, chunk, item))
            if item == 0:
                self.fail('unresolved slot reached the matrix query')
            return _ItemMatrix(_Vector(50.0, 0.0, 50.0))

        bigworld.wg_getDestructibleEffectCategory = effect_category
        bigworld.wg_getDestructibleMatrix = item_matrix
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'unresolved slot reached native destruction')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        self.assertEqual([(1, 22, 0, -1), (1, 22, 1, -1)],
                         category_calls)
        self.assertEqual([(1, 22, 1)], matrix_calls)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'name_item_unresolved'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(
            1, destructibles_sensor.g_offh_tree_state['chunks'][22]['count'])

    def test_unbaked_slot_matrix_exception_is_contained_to_that_slot(self):
        tree = 'speedtree/test/oak.spt'
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 1, 'health': 10} if value == tree else None))
        category = mock.Mock(return_value=1)
        matrix_query = mock.Mock(side_effect=RuntimeError('matrix unavailable'))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (tree,)
        bigworld.wg_getDestructibleEffectCategory = category
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = matrix_query
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'isolated matrix slot reached native destruction')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        category.assert_called_once_with(1, 22, 0, -1)
        matrix_query.assert_called_once_with(1, 22, 0)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'native_matrix_query'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(
            0, destructibles_sensor.g_offh_tree_state['chunks'][22]['count'])

    def _scanner_tree_fixture(self):
        tree = 'speedtree/test/scanner-oak.spt'
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 1, 'health': 10, 'mass': 20}
                if value == tree else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = mock.Mock(return_value=(tree,))
        bigworld.wg_getDestructibleEffectCategory = mock.Mock(return_value=1)
        bigworld.wg_getChunkMatrix = mock.Mock(return_value=
            types.SimpleNamespace(translation=_Vector()))
        bigworld.wg_getDestructibleMatrix = mock.Mock(return_value=
            _ItemMatrix(_Vector(50.0, 0.0, 50.0)))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range
        return area, bigworld, math_module, descriptor

    def test_scanner_chunk_matrix_exception_is_chunk_local(self):
        area, bigworld, math_module, descriptor = (
            self._scanner_tree_fixture())
        bigworld.wg_getChunkMatrix.side_effect = RuntimeError(
            'chunk matrix unavailable')

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'chunk-matrix failure reached native destruction')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        bigworld.wg_getChunkMatrix.assert_called_once_with(1, 22)
        bigworld.wg_getDestructibleMatrix.assert_not_called()
        self.assertEqual(
            {22}, destructibles_sensor.g_offh_destr_isolated_chunks)
        self.assertEqual(
            {'native_chunk_matrix'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_scanner_missing_chunk_translation_retries_next_scan(self):
        area, bigworld, math_module, descriptor = (
            self._scanner_tree_fixture())
        bigworld.wg_getChunkMatrix.side_effect = (
            types.SimpleNamespace(translation=None),
            types.SimpleNamespace(translation=_Vector()))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'distant tree reached native destruction')):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            self.assertNotIn(
                22, destructibles_sensor.g_offh_tree_state['chunks'])
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        self.assertEqual(2, bigworld.wg_getChunkMatrix.call_count)
        bigworld.wg_getDestructibleMatrix.assert_called_once_with(1, 22, 0)
        self.assertNotIn(
            'g_offh_destr_isolated_chunks', destructibles_sensor.__dict__)
        self.assertEqual(
            1, destructibles_sensor.g_offh_tree_state['chunks'][22]['count'])

    def test_uncataloged_tree_transform_exception_is_slot_local(self):
        area, bigworld, math_module, descriptor = (
            self._scanner_tree_fixture())
        # The raw named tree is legal without a catalog.  Failure to obtain its
        # local translation still belongs to this slot, not the whole callback.
        bigworld.wg_getDestructibleMatrix.return_value = (
            types.SimpleNamespace(translation=None))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'bad tree transform reached native destruction')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        bigworld.wg_getDestructibleMatrix.assert_called_once_with(1, 22, 0)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'native_matrix_transform'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(
            0, destructibles_sensor.g_offh_tree_state['chunks'][22]['count'])

    def test_malformed_native_name_entry_isolates_the_chunk(self):
        # The engine's own name loop always appends a Python string.  An empty
        # string is legal, but a non-string is a chunk-level ABI violation.
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 2)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {'type': 1, 'health': 10})
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = (
            lambda *unused: ('missing-tree', None))
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 1
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: _ItemMatrix()
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        self.assertEqual(
            {22}, destructibles_sensor.g_offh_destr_isolated_chunks)
        self.assertEqual(
            {'filename_payload'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

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

    def test_fragile_preserves_shot_encoding_without_unmatched_native_sync(self):
        manager = _Manager()
        area = _authority_environment(manager)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertTrue(destructibles_authority.destroy_fragile(
                1, 22, 37, (10.0, 2.0, 20.0), True))

        encoded = (37 << 8) | 1
        self.assertEqual([(22, 3, encoded, True, False)], manager.orders)
        chunk = destructibles_authority._state['chunks'][22]
        self.assertEqual([encoded], chunk['destroyedFragiles'])
        self.assertIn((37, None), chunk['keys'])

    def test_controller_shot_bypasses_unmatched_projectile_sync(self):
        class Controller(object):
            def __init__(self):
                self.destroyedModules = []
                self._AreaDestructibles__prevDestroyedModules = frozenset()
                self.setter_calls = 0

            def set_destroyedModules(self, unused_previous):
                self.setter_calls += 1
                raise AssertionError('shot must bypass the synced setter')

        manager = _Manager()
        manager.controller = Controller()
        area = _authority_environment(manager)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            manager.fail = True
            with self.assertRaisesRegex(RuntimeError, 'native destroy failed'):
                destructibles_authority.destroy_module(
                    1, 22, 37, 73, (10.0, 2.0, 20.0), True)
            self.assertEqual([], manager.controller.destroyedModules)
            self.assertEqual(
                frozenset(),
                manager.controller._AreaDestructibles__prevDestroyedModules)
            self.assertFalse(
                destructibles_authority.is_destroyed(22, 37, 73))
            manager.fail = False
            self.assertTrue(destructibles_authority.destroy_module(
                1, 22, 37, 73, (10.0, 2.0, 20.0), True))

        encoded = ('module', 37, 73, True)
        self.assertEqual([(22, 4, encoded, True, False)], manager.orders)
        self.assertEqual([encoded], manager.controller.destroyedModules)
        self.assertEqual(
            frozenset([encoded]),
            manager.controller._AreaDestructibles__prevDestroyedModules)
        self.assertEqual(0, manager.controller.setter_calls)
        self.assertTrue(destructibles_authority.is_destroyed(22, 37, 73))
        self.assertEqual(2, len(manager.attempts))

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

    def test_controller_contact_uses_one_native_order_without_setter(self):
        class Controller(object):
            def __init__(self):
                self.destroyedFragiles = []
                self._AreaDestructibles__prevDestroyedFragiles = frozenset()
                self.setter_calls = 0

            def set_destroyedFragiles(self, unused_previous):
                self.setter_calls += 1
                raise AssertionError('controller setter must not be called')

        manager = _Manager()
        manager.controller = Controller()
        area = _authority_environment(manager)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertTrue(destructibles_authority.destroy_fragile(
                1, 22, 37, (10.0, 2.0, 20.0), False))

        encoded = 37 << 8
        self.assertEqual([(22, 3, encoded, True, False)], manager.orders)
        self.assertEqual([(37 << 8)],
                         manager.controller.destroyedFragiles)
        self.assertEqual(
            frozenset([encoded]),
            manager.controller._AreaDestructibles__prevDestroyedFragiles)
        self.assertEqual(0, manager.controller.setter_calls)
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
        self.assertEqual(
            {'entityID': 900, 'state': 'pending'},
            destructibles_authority._state['entities'][22])

    def test_pending_controller_request_is_not_committed_or_duplicated(self):
        manager = _Manager()
        area = _authority_environment(manager)
        creates = []
        visible = {}
        destroyed = []

        def create(*unused):
            entity_id = 900 + len(creates)
            creates.append(entity_id)
            return entity_id

        destructibles_authority.BigWorld.createEntity = create
        destructibles_authority.BigWorld.entity = visible.get
        destructibles_authority.BigWorld.destroyEntity = destroyed.append

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertIsNone(destructibles_authority._ensure_chunk(
                1, 22, (10.0, 2.0, 20.0)))
            self.assertIsNone(destructibles_authority._ensure_chunk(
                1, 22, (10.0, 2.0, 20.0)))

        self.assertEqual([900], creates)
        self.assertEqual([], destroyed)
        self.assertEqual('pending',
                         destructibles_authority._state['entities'][22]['state'])

    def test_visible_entity_without_controller_is_replaced_and_then_committed(self):
        manager = _Manager()
        area = _authority_environment(manager)
        creates = []
        visible = {}
        destroyed = []

        def create(*unused):
            entity_id = 900 + len(creates)
            creates.append(entity_id)
            return entity_id

        destructibles_authority.BigWorld.createEntity = create
        destructibles_authority.BigWorld.entity = visible.get
        destructibles_authority.BigWorld.destroyEntity = destroyed.append

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertIsNone(destructibles_authority._ensure_chunk(
                1, 22, (10.0, 2.0, 20.0)))
            visible[900] = object()
            self.assertIsNone(destructibles_authority._ensure_chunk(
                1, 22, (10.0, 2.0, 20.0)))
            controller = object()
            manager.controller = controller
            self.assertIs(controller, destructibles_authority._ensure_chunk(
                1, 22, (10.0, 2.0, 20.0)))

        self.assertEqual([900, 901], creates)
        self.assertEqual([900], destroyed)
        self.assertEqual(
            {'entityID': 901, 'state': 'ready'},
            destructibles_authority._state['entities'][22])

    def test_authority_does_not_replace_native_count_with_name_prefix(self):
        manager = _Manager()
        manager._DestructiblesManager__loadedChunkIDs = {}
        manager.controller = None
        manager.onChunkLoad = mock.Mock(
            side_effect=AssertionError('authority fabricated native count'))
        area = _authority_environment(manager)
        destructibles_authority.BigWorld.wg_getChunkDestrFilenames = mock.Mock(
            return_value=('named-tree-only',))

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertTrue(destructibles_authority.destroy_fragile(
                1, 22, 37, (10.0, 2.0, 20.0), False))

        manager.onChunkLoad.assert_not_called()
        destructibles_authority.BigWorld.wg_getChunkDestrFilenames.assert_not_called()
        self.assertEqual(1, len(manager.orders))

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

    def test_named_material_without_descriptor_isolates_exact_wire(self):
        filename = 'content/test/missing-destructible.model'
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=mock.Mock(return_value=None))
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_fragile=mock.Mock(return_value=True))
        event_sink = mock.Mock(return_value=True)
        destructibles_sensor.set_event_sink(event_sink)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            for unused in range(2):
                self.assertFalse(
                    destructibles_sensor._try_destroy_destructible(
                        1, _mat_info_1513(
                            True, _Vector(), _Vector(0, 1, 0), 75,
                            filename, 22, 37),
                        0.0, 6.0, True))

        area.g_cache.getDescByFilename.assert_called_once_with(filename)
        authority.is_destroyed.assert_not_called()
        authority.destroy_fragile.assert_not_called()
        event_sink.assert_not_called()
        self.assertEqual(
            {(22, 37)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'material_descriptor'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_named_material_descriptor_exception_is_slot_local(self):
        filename = 'content/test/broken-descriptor.model'
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=mock.Mock(
                side_effect=RuntimeError('descriptor cache failed')))
        authority = types.SimpleNamespace(
            destroy_fragile=mock.Mock(return_value=True))

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertFalse(
                destructibles_sensor._try_destroy_destructible(
                    1, _mat_info_1513(
                        True, _Vector(), _Vector(0, 1, 0), 75,
                        filename, 22, 37),
                    0.0, 6.0, True))

        authority.destroy_fragile.assert_not_called()
        self.assertEqual(
            {(22, 37)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'material_descriptor'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_named_material_malformed_descriptor_is_slot_local(self):
        filename = 'content/test/malformed-descriptor.model'
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        payloads = iter((object(), {'type': 4, 'modules': object()}))
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: next(payloads))
        authority = types.SimpleNamespace(
            destroy_module=mock.Mock(return_value=True))

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            for item_index in (37, 38):
                self.assertFalse(
                    destructibles_sensor._try_destroy_destructible(
                        1, _mat_info_1513(
                            True, _Vector(), _Vector(0, 1, 0), 73,
                            filename, 22, item_index),
                        0.0, 6.0, True))

        authority.destroy_module.assert_not_called()
        self.assertEqual(
            {(22, 37), (22, 38)},
            destructibles_sensor.g_offh_destr_isolated_slots)

    def test_anonymous_material_descriptor_exception_stays_retryable(self):
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=mock.Mock(
                side_effect=RuntimeError('anonymous descriptor unavailable')))

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertFalse(
                destructibles_sensor._try_destroy_destructible(
                    1, _mat_info_1513(
                        True, _Vector(), _Vector(0, 1, 0), 75,
                        '', 22, 37),
                    0.0, 6.0, True))

        self.assertNotIn(
            'g_offh_destr_isolated_slots', destructibles_sensor.__dict__)

    def test_v4_direct_hit_rejects_same_kind_different_resource(self):
        expected = 'content/test/expected-fence.model'
        alternate = 'content/test/alternate-fence.model'
        matrix = _ItemMatrix(_Vector(0.0, 0.0, 5.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        resources = {
            expected: {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 2, 1, None]],
            },
            alternate: {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 2, 1, None]],
            },
        }
        destructibles_sensor.set_catalog(_catalog(
            resources,
            [list(signature) + [expected, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        descriptors = {
            expected: {'type': 3, 'health': 15},
            alternate: {'type': 3, 'health': 15},
        }
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=descriptors.get)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (expected,)
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 3
        bigworld.time = lambda: 10.0
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_fragile=mock.Mock(return_value=True))
        event_sink = mock.Mock(return_value=True)
        destructibles_sensor.xrange = range
        destructibles_sensor.set_event_sink(event_sink)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            accepted = destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(
                    True, _Vector(0.0, 0.0, 5.0), _Vector(0, 1, 0),
                    75, alternate, 22, 0),
                0.0, 12.0, True)

        self.assertFalse(accepted)
        authority.is_destroyed.assert_not_called()
        authority.destroy_fragile.assert_not_called()
        event_sink.assert_not_called()
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'filename_identity_conflict'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_v4_direct_structure_hit_requires_exact_catalog_module(self):
        filename = 'content/test/structure-wall.model'
        matrix = _ItemMatrix(_Vector(0.0, 0.0, 5.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [[-1, -1, -1, 1, 2, 1, 73]],
            },
        }, [list(signature) + [filename, None, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        descriptor = {
            'type': 4,
            'modules': {73: {'health': 15}, 74: {'health': 15}},
        }
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.DESTRUCTIBLE_MATKIND = types.SimpleNamespace(
            NORMAL_MIN=73, NORMAL_MAX=86)
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                descriptor if value == filename else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (filename,)
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 4
        bigworld.time = lambda: 10.0
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_module=mock.Mock(return_value=True))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            accepted = destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(
                    True, _Vector(0.0, 0.0, 5.0), _Vector(0, 1, 0),
                    74, filename, 22, 0),
                0.0, 12.0, True)

        self.assertFalse(accepted)
        authority.is_destroyed.assert_not_called()
        authority.destroy_module.assert_not_called()
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'catalog_module_identity'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_health_one_falling_atom_is_not_rejected_as_soft_tree(self):
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda filename: (
                {'type': 2, 'health': 1, 'mass': 10}
                if filename == 'streetlamp' else None))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_column=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            for is_shot in (False, True):
                self.assertTrue(
                    destructibles_sensor._try_destroy_destructible(
                        1, _mat_info_1513(
                            True, _Vector(), _Vector(0, 1, 0), 75,
                            'streetlamp', 22, 37),
                        0.25, 6.0, is_shot))

        self.assertEqual(2, len(calls))
        self.assertEqual([(1, 22, 37), (1, 22, 37)],
                         [call[:3] for call in calls])

    def test_exact_1513_structure_hit_preserves_chunk_and_item_order(self):
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda filename: (
                {'type': 4, 'modules': {73: {'health': 19}}}
                if filename == 'structure-wall' else None))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)
        hit_point = _Vector(10, 2, 20)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(
                    True, hit_point, _Vector(0, 1, 0), 73,
                    'structure-wall', 22, 37),
                0.25, 6.0, False))

        self.assertEqual(1, len(calls))
        self.assertEqual((1, 22, 37, 73), calls[0][:4])
        self.assertIs(hit_point, calls[0][4])
        self.assertFalse(calls[0][5])

    def test_structure_hit_accepts_only_live_normal_descriptor_modules(self):
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 0
        area.DESTR_TYPE_FALLING_ATOM = 1
        area.DESTR_TYPE_FRAGILE = 2
        area.DESTR_TYPE_STRUCTURE = 3
        descriptors = {
            'structure-wall': {
                'type': 3, 'modules': {73: {'health': 19}}},
            'unknown-type': {
                'type': 99, 'modules': {73: {'health': 19}}},
        }
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=descriptors.get)
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_module=mock.Mock(return_value=True))

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            for material in (71, 72, 74, 86, 87, 100, 128):
                self.assertFalse(
                    destructibles_sensor._try_destroy_destructible(
                        1, _mat_info_1513(
                            True, _Vector(), _Vector(0, 1, 0), material,
                            'structure-wall', 22, 37),
                        0.0, 6.0), material)
            self.assertFalse(destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(
                    True, _Vector(), _Vector(0, 1, 0), 73,
                    'unknown-type', 22, 37),
                0.0, 6.0))

        authority.is_destroyed.assert_not_called()
        authority.destroy_module.assert_not_called()

    def test_catalog_rejects_non_normal_structure_material_namespaces(self):
        filename = 'content/Environment/structure/normal/lod0/wall.model'
        for material in (71, 72, 86, 87, 100, 128):
            catalog = _catalog({
                filename: {
                    'kind': 'structure',
                    'boxes': [[-1, 0, -1, 1, 2, 1, material]],
                },
            })
            with self.assertRaisesRegex(
                    ValueError, 'structure catalog material is invalid'):
                destructibles_sensor.set_catalog(catalog)

    def test_destroy_ledger_does_not_claim_native_collision_is_clear(self):
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {'type': 3, 'health': 1})
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: True)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertFalse(destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(
                    True, _Vector(), _Vector(0, 1, 0), 75,
                    'fence', 22, 37), 0.0, 6.0))

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
                1, _Vector(), _Vector(0, 0, 2),
                _Vector(0, 0, -1), 0.0, 6.0))

    def test_solid_probe_uses_native_1513_surface_normal(self):
        bigworld = types.ModuleType('BigWorld')
        calls = []

        def material_probe(unused_space, start, stop, point, unused_cb):
            calls.append((start, stop, point))
            return _mat_info_1513(False)

        bigworld.wg_getMatInfoNearPoint = material_probe
        hit_point = _Vector(10, 2, 20)
        segment_start = _Vector(10, 2, 15)

        with mock.patch.dict(sys.modules, {'BigWorld': bigworld}):
            self.assertFalse(destructibles_sensor._try_destroy_solid_hit(
                1, segment_start, hit_point,
                _Vector(1, 0, 0), 0.0, 6.0))

        self.assertEqual(2, len(calls))
        start, stop, point = calls[0]
        self.assertEqual((7.0, 2.0, 20.0), (start.x, start.y, start.z))
        self.assertEqual((12.0, 2.0, 20.0), (stop.x, stop.y, stop.z))
        self.assertIs(hit_point, point)
        start, stop, point = calls[1]
        self.assertEqual((10.0, 2.0, 23.0), (start.x, start.y, start.z))
        self.assertEqual((10.0, 2.0, 18.0), (stop.x, stop.y, stop.z))
        self.assertIs(hit_point, point)

    def test_solid_probe_rejects_missing_surface_normal(self):
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = (
            lambda *unused: _mat_info_1513(False))

        with mock.patch.dict(sys.modules, {'BigWorld': bigworld}):
            with self.assertRaisesRegex(
                    RuntimeError, 'surface normal is invalid'):
                destructibles_sensor._try_destroy_solid_hit(
                    1, _Vector(0, 0, -1), _Vector(), _Vector(),
                    0.0, 6.0)

    def test_redshire_field_fence_incoming_fallback_destroys_exact_fragile(self):
        # 34_redshire BSMO model 31, BSMI instance 1229 at the north base.
        filename = (
            'content/GatesAndFences/gafBR_002_FieldFence/normal/lod0/'
            'gafBR_002_FieldFence2.model')
        contact = _Vector(-10.171768, 2.622312, 399.907318)
        candidate_hit = _Vector(-9.921768, 2.622312, 399.907318)
        contact_normal = _Vector(1.0, 0.0, 0.0)
        calls = []

        def material_probe(unused_space, start, stop, point, unused_cb):
            calls.append((start, stop, point, unused_cb))
            if abs(start.z - point.z - 3.0) < 0.001:
                return _mat_info_1513(
                    True, candidate_hit, _Vector(-1.0, 0.0, 0.0), 73,
                    filename, 22, 37)
            return _mat_info_1513(False)

        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = material_probe
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda value: (
                {'type': area.DESTR_TYPE_FRAGILE, 'health': 5,
                 'kineticDamageCorrection': 1.0}
                if value == filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: scale * health)
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'filename': filename.lower(), 'kind': 'fragile',
                       'boxes': (), 'item_scale': 1.0},
        }
        descriptor = _Strict1513Component(physics={'weight': 25000.0})
        authority_calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: authority_calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(-10.171768, 2.622312, 393.907318), contact,
                contact_normal, 0.0, 6.0, descriptor))

        self.assertEqual(2, len(calls))
        self.assertEqual((-13.171768, 2.622312, 399.907318),
                         (calls[0][0].x, calls[0][0].y, calls[0][0].z))
        self.assertEqual((-10.171768, 2.622312, 402.907318),
                         (calls[1][0].x, calls[1][0].y, calls[1][0].z))
        self.assertEqual(1, len(authority_calls))
        self.assertEqual((1, 22, 37), authority_calls[0][:3])
        self.assertIs(candidate_hit, authority_calls[0][3])

    def test_redshire_base_normal_probe_destroys_only_exact_module(self):
        # 34_redshire BSMO child model 259, BSMI instance 1245 at north base.
        filename = (
            'content/Buildings/bld000_base/normal/lod0/'
            'bld000_base.model')
        contact = _Vector(-6.952255, 2.676222, 404.814606)
        candidate_hit = _Vector(-6.952255, 2.676222, 405.214606)
        contact_normal = _Vector(0.0, 0.0, -1.0)
        calls = []

        def material_probe(unused_space, start, stop, point, unused_cb):
            calls.append((start, stop, point, unused_cb))
            return _mat_info_1513(
                True, candidate_hit, _Vector(0.0, 0.0, 1.0), 74,
                filename, 22, 37)

        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = material_probe
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda value: (
                {'type': area.DESTR_TYPE_STRUCTURE,
                 'modules': {73: {'health': 15}, 74: {'health': 15},
                             75: {'health': 15}}}
                if value == filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: scale * health)
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'filename': filename.lower(), 'kind': 'structure',
                       'boxes': (), 'item_scale': 1.0},
        }
        descriptor = _Strict1513Component(physics={'weight': 25000.0})
        authority_calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *args: authority_calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(-6.952255, 2.676222, 398.814606), contact,
                contact_normal, 0.0, 6.0, descriptor))

        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(authority_calls))
        self.assertEqual((1, 22, 37, 74), authority_calls[0][:4])
        self.assertIs(candidate_hit, authority_calls[0][4])

    def test_solid_candidate_gate_rejects_neighbour_and_illegal_module(self):
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        descriptors = {
            'nearby-fence': {'type': area.DESTR_TYPE_FRAGILE, 'health': 13},
            'base': {'type': area.DESTR_TYPE_STRUCTURE,
                     'modules': {73: {'health': 15}}},
        }
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: descriptors.get(value))
        contact = _Vector()
        normal = _Vector(0.0, 0.0, 1.0)
        tree = {'type': area.DESTR_TYPE_TREE, 'health': 20}
        descriptors['tree'] = tree

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertFalse(
                destructibles_sensor._solid_destructible_candidate_1513(
                    _mat_info_1513(
                        True, _Vector(0.0, 0.0, 0.5001), normal, 73,
                        'nearby-fence', 22, 1),
                    contact, normal))
            self.assertFalse(
                destructibles_sensor._solid_destructible_candidate_1513(
                    _mat_info_1513(
                        True, _Vector(0.0, 0.0, 0.2), normal, 74,
                        'base', 22, 2),
                    contact, normal))
            self.assertFalse(
                destructibles_sensor._solid_destructible_candidate_1513(
                    _mat_info_1513(
                        True, _Vector(0.0, 0.0, 0.2),
                        _Vector(1.0, 0.0, 0.0), 73,
                        'nearby-fence', 22, 3),
                    contact, normal))
            self.assertFalse(
                destructibles_sensor._solid_destructible_candidate_1513(
                    _mat_info_1513(
                        True, _Vector(0.0, 0.0, 0.2), normal, 70,
                        'nearby-fence', 22, 4),
                    contact, normal))
            self.assertFalse(
                destructibles_sensor._solid_destructible_candidate_1513(
                    _mat_info_1513(
                        True, _Vector(0.0, 0.0, 0.2), normal, 73,
                        'tree', 22, 5),
                    contact, normal))

    def test_solid_contact_descriptor_exception_is_slot_local(self):
        filename = 'content/Environment/broken_fragile.model'
        point = _Vector(0.0, 0.0, 0.2)
        normal = _Vector(0.0, 0.0, 1.0)
        raw = _mat_info_1513(
            True, point, normal, 73, filename, 22, 37)
        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4

        def fail_lookup(unused):
            raise RuntimeError('descriptor cache unavailable')

        area.g_cache = types.SimpleNamespace(
            getDescByFilename=fail_lookup)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = lambda *unused: raw

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertFalse(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(0.0, 0.0, -2.0), point, normal,
                0.0, 6.0))

        self.assertIn(
            (22, 37), destructibles_sensor.g_offh_destr_isolated_slots)

    def test_stock_crushable_second_descriptor_exception_is_slot_local(self):
        filename = 'content/Environment/broken_fragile.model'
        point = _Vector(0.0, 0.0, 0.2)
        normal = _Vector(0.0, 0.0, 1.0)
        raw = _mat_info_1513(
            True, point, normal, 73, filename, 22, 37)
        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        lookups = []

        def lookup(unused):
            lookups.append(True)
            if len(lookups) == 1:
                return {'type': 3, 'health': 5,
                        'kineticDamageCorrection': 0.0}
            raise RuntimeError('descriptor cache became unavailable')

        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lookup, unitVehicleMass=1000.0)
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: health
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = lambda *unused: raw
        type_descriptor = _Strict1513Component(
            physics=_Strict1513Component(weight=25000.0))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'BigWorld': bigworld}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertFalse(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(0.0, 0.0, -2.0), point, normal,
                0.0, 6.0, type_descriptor))

        self.assertEqual(2, len(lookups))
        self.assertIn(
            (22, 37), destructibles_sensor.g_offh_destr_isolated_slots)

    def test_anonymous_solid_descriptor_exception_remains_retryable(self):
        state = {'fail': True}
        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4

        def lookup(unused):
            if state['fail']:
                raise RuntimeError('anonymous descriptor is pending')
            return {'type': 3, 'health': 5}

        area.g_cache = types.SimpleNamespace(getDescByFilename=lookup)
        raw = _mat_info_1513(
            True, _Vector(0.0, 0.0, 0.2), _Vector(0.0, 0.0, 1.0),
            73, '', 22, 37)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertFalse(
                destructibles_sensor._solid_destructible_candidate_1513(
                    raw, _Vector(), _Vector(0.0, 0.0, 1.0)))
            self.assertFalse(destructibles_sensor.is_isolated_1513(22, 37))
            state['fail'] = False
            self.assertTrue(
                destructibles_sensor._solid_destructible_candidate_1513(
                    raw, _Vector(), _Vector(0.0, 0.0, 1.0)))

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

    def test_tree_shot_descriptor_exception_stops_without_callback_escape(self):
        filename = 'speedtree/test/oak.spt'
        impact = _Vector(0.0, 0.0, 5.0)
        raw = _mat_info_1513(
            True, impact, _Vector(0.0, 1.0, 0.0), 73,
            filename, 22, 37)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: (impact,)
        bigworld.wg_getMatInfoNearPoint = lambda *unused: raw
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4

        def fail_lookup(unused):
            raise RuntimeError('tree descriptor cache unavailable')

        area.g_cache = types.SimpleNamespace(
            getDescByFilename=fail_lookup)
        events = []
        destructibles_sensor.set_event_sink(
            lambda event: events.append(event) or True)
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0.0, 0.0, 20.0),
                _Vector(0.0, 0.0, 1.0), shot)

        self.assertEqual(5.0, result['world_distance'])
        self.assertEqual(5.0, result['stop_distance'])
        self.assertEqual([], events)
        self.assertIn(
            (22, 37), destructibles_sensor.g_offh_destr_isolated_slots)

    def test_accepted_native_shot_second_descriptor_failure_is_terminal(self):
        filename = 'content/Environment/fragile.model'
        impact = _Vector(0.0, 0.0, 5.0)
        raw = _mat_info_1513(
            True, impact, _Vector(0.0, 1.0, 0.0), 73,
            filename, 22, 37)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: (impact,)
        bigworld.wg_getMatInfoNearPoint = lambda *unused: raw
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        descriptor = {'type': 3, 'health': 5}
        lookups = []

        def lookup(unused):
            lookups.append(True)
            if len(lookups) <= 2:
                return descriptor
            raise RuntimeError('post-accept descriptor cache unavailable')

        area.g_cache = types.SimpleNamespace(getDescByFilename=lookup)
        destroyed = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: destroyed.append(args) or True)
        events = []
        destructibles_sensor.set_event_sink(
            lambda event: events.append(event) or True)
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0.0, 0.0, 20.0),
                _Vector(0.0, 0.0, 1.0), shot)

        self.assertEqual(3, len(lookups))
        self.assertEqual(1, len(destroyed))
        self.assertEqual(1, len(events))
        self.assertEqual(result['world_distance'], result['stop_distance'])
        self.assertTrue(result['stopped_by_destructible'])
        self.assertIn(
            (22, 37), destructibles_sensor.g_offh_destr_isolated_slots)

    def test_accepted_catalog_shot_second_descriptor_failure_is_terminal(self):
        destructibles_sensor.xrange = range
        filename = 'content/Environment/catalog_fragile.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, 4.0, 1.0, 2.0, 6.0, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'fragile',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 37): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 37), instance)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: None
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        descriptor = {'type': 3, 'health': 5}
        lookups = []

        def lookup(unused):
            lookups.append(True)
            if len(lookups) == 1:
                return descriptor
            raise RuntimeError('post-accept catalog descriptor unavailable')

        area.g_cache = types.SimpleNamespace(getDescByFilename=lookup)
        destroyed = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: destroyed.append(args) or True)
        events = []
        destructibles_sensor.set_event_sink(
            lambda event: events.append(event) or True)
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0.0, 0.0, 20.0),
                _Vector(0.0, 0.0, 1.0), shot)

        self.assertEqual(2, len(lookups))
        self.assertEqual(1, len(destroyed))
        self.assertEqual(1, len(events))
        self.assertEqual(result['world_distance'], result['stop_distance'])
        self.assertTrue(result['stopped_by_destructible'])
        self.assertIn(
            (22, 37), destructibles_sensor.g_offh_destr_isolated_slots)

    def test_malformed_shot_through_descriptor_is_conservative(self):
        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_STRUCTURE = 4

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertIsNone(
                destructibles_sensor._scaled_shot_through_health_1513(
                    {'type': 4, 'modules': {73: object()}}, 73, 1.0))
            self.assertIsNone(
                destructibles_sensor._scaled_shot_through_health_1513(
                    {'type': 3, 'health': object()}, 73, 1.0))

    def test_steppes_mixed_case_structure_uses_exact_descriptor_name(self):
        filename = (
            'content/Buildings/bld000_base/normal/lod0/'
            'bld000_base.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [[-0.085, -1.073, -1.562,
                           6.530, 6.680, 5.386, 73]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(), _Vector(), math_module)
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'structure', 'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(33147, 0): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (33147, 0), instance)

        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=35000.0,
            getDescByFilename=lambda value: (
                {'type': 4, 'modules': {73: {'health': 15}}}
                if value == filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: int(scale * scale * health + 0.999999))
        descriptor = _Strict1513Component(
            physics={'weight': 21000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(destructibles_sensor._catalog_motion_blocked(
                1, _Vector(), 0.0, 4.783, descriptor, 10.0))

        self.assertEqual(1, len(calls))
        self.assertEqual((1, 33147, 0, 73), calls[0][:4])

    def test_shot_recovers_unique_anonymous_structure_from_catalog(self):
        filename = (
            'content/Buildings/bld000_base/normal/lod0/'
            'bld000_base.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [[-1.0, -1.0, 4.0, 1.0, 2.0, 6.0, 73]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(), _Vector(), math_module)
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'structure', 'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(33147, 0): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (33147, 0), instance)

        impact = _Vector(0.0, 0.5, 5.0)
        bigworld = types.ModuleType('BigWorld')
        collision_calls = []
        def collide(*args):
            collision_calls.append(args)
            return (impact, _Vector(0.0, 0.0, -1.0))
        bigworld.wg_collideSegment = collide
        bigworld.wg_getMatInfoNearPoint = (
            lambda *unused: _mat_info_1513(False))
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 4, 'modules': {73: {'health': 15}}}
                if value == filename else None))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            distance = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1))

        self.assertAlmostEqual(5.024937810560445, distance)
        self.assertEqual(1, len(calls))
        self.assertEqual((1, 33147, 0, 73), calls[0][:4])
        self.assertTrue(calls[0][-1])

    def test_typed_material_miss_continues_after_exact_thick_obb_exit(self):
        destructibles_sensor.xrange = range
        filename = (
            'content/Buildings/bld000_base/normal/lod0/'
            'bld000_base.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [[-2.0, -1.0, 4.0, 2.0, 2.0, 8.0, 73]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'structure',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 1), instance)
        impact = _Vector(0.0, 0.5, 4.0)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: (
            impact, _Vector(0.0, 0.0, -1.0))
        bigworld.wg_getMatInfoNearPoint = (
            lambda *unused: _mat_info_1513(False))
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 4, 'modules': {73: {'health': 15}}}
                if value == filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: int(
            math.ceil(scale * scale * health))
        destroyed = set()
        authority = types.SimpleNamespace(
            is_destroyed=lambda chunk, item, mat=None: (
                (chunk, item, mat) in destroyed),
            destroy_module=lambda space, chunk, item, mat, point, is_shot: (
                destroyed.add((chunk, item, mat)) or True))
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 10),
                _Vector(0, 0, 1), shot)

        self.assertEqual((22, 1, 73), next(iter(destroyed)))
        self.assertEqual(25.0, result['piercing_loss'])
        self.assertGreater(result['continue_from'], 8.0)
        self.assertLess(result['continue_from'], 8.2)

    def test_typed_dynamic_ambiguous_obb_stops_without_destroying(self):
        destructibles_sensor.xrange = range
        filenames = ('content/Environment/a.model',
                     'content/Environment/b.model')
        destructibles_sensor.set_catalog(_catalog({
            filenames[0]: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, 4.0, 1.0, 2.0, 6.0, None]],
            },
            filenames[1]: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, 4.0, 1.0, 2.0, 6.0, None]],
            },
        }))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instances = {}
        for index, filename in enumerate(filenames):
            record = destructibles_sensor._destructible_catalog[
                'resources'][filename.lower()]
            instances[(22, index)] = {
                'filename': filename.lower(),
                'descriptor_filename': filename,
                'kind': 'fragile',
                'boxes': destructibles_sensor._world_catalog_boxes(
                    record, _ItemMatrix(), _Vector(), math_module),
                'item_scale': 1.0,
            }
        destructibles_sensor.g_offh_destr_instances = instances
        destructibles_sensor.g_offh_destr_contact_bins = {}
        for identity, instance in instances.items():
            destructibles_sensor._index_catalog_instance_1513(
                destructibles_sensor.g_offh_destr_contact_bins,
                identity, instance)
        bigworld = types.SimpleNamespace(
            wg_collideSegment=lambda *unused: None,
            time=lambda: 10.0)
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: self.fail(
                'ambiguous OBB must not be destroyed'))
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 10),
                _Vector(0, 0, 1), shot)

        self.assertIsNotNone(result['stop_distance'])
        self.assertLess(result['stop_distance'], 4.1)
        self.assertEqual(result['stop_distance'], result['world_distance'])

    def test_vehicle_cap_does_not_expose_catalog_prop_one_cm_behind(self):
        destructibles_sensor.xrange = range
        filename = 'content/Environment/behind_vehicle.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                # The exact unpadded entrance is z=5.01, just behind the
                # vehicle-capped endpoint at z=5.0.
                'boxes': [[-1.0, -1.0, 5.01, 1.0, 2.0, 6.0, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'fragile',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 1), instance)
        bigworld = types.SimpleNamespace(
            wg_collideSegment=lambda *unused: None,
            time=lambda: 10.0)
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: self.fail(
                'vehicle cap exposed a prop behind it'))
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 5),
                _Vector(0, 0, 1), shot)

        self.assertIsNone(result['stop_distance'])
        self.assertEqual(99999.0, result['world_distance'])

    def test_shot_uses_native_identity_to_disambiguate_structure_module(self):
        filename = (
            'content/Buildings/bld000_base/normal/lod0/'
            'bld000_base.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [
                    [-2.0, -1.0, 4.0, 2.0, 2.0, 7.0, 73],
                    [-2.0, -1.0, 4.0, 2.0, 2.0, 7.0, 74],
                ],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'structure',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(33147, 0): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (33147, 0), instance)
        impact = _Vector(0.0, 0.5, 5.0)
        raw = _mat_info_1513(
            True, impact, _Vector(0, 1, 0), 73, '', 33147, 0)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: (
            impact, _Vector(0.0, 0.0, -1.0))
        bigworld.wg_getMatInfoNearPoint = lambda *unused: raw
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 4, 'modules': {
                    73: {'health': 15}, 74: {'health': 15}}}
                if value == filename else None))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1))

        self.assertEqual(1, len(calls))
        self.assertEqual((1, 33147, 0, 73), calls[0][:4])
        self.assertTrue(calls[0][-1])

    def test_typed_native_shot_requires_registered_scale_and_exact_case(self):
        filename = 'content/Environment/MixedCaseFragile.model'
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        point = _Vector(0.0, 0.5, 5.0)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: (
            point, _Vector(0.0, 0.0, -1.0))
        bigworld.wg_getMatInfoNearPoint = lambda *unused: _mat_info_1513(
            True, point, _Vector(0, 1, 0), 73, filename, 22, 1)
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        lookups = []
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                lookups.append(value) or
                ({'type': 3, 'health': 5} if value == filename else None)))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: int(
            math.ceil(scale * scale * health))
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        for descriptor_filename, should_continue in (
                (None, False), (filename.lower(), False), (filename, True)):
            destructibles_sensor.g_offh_destr_instances = ({
                (22, 1): {'filename': filename.lower(),
                          'descriptor_filename': descriptor_filename,
                          'kind': 'fragile', 'boxes': (),
                          'item_scale': 1.0}}
                if descriptor_filename is not None else {})
            destroyed = set()
            authority = types.SimpleNamespace(
                is_destroyed=lambda chunk, item, mat=None: (
                    (chunk, item, mat) in destroyed),
                destroy_fragile=lambda space, chunk, item, hit, is_shot: (
                    destroyed.add((chunk, item, None)) or True))
            with mock.patch.dict(
                    sys.modules, {'BigWorld': bigworld,
                                  'AreaDestructibles': area,
                                  'DestructiblesCache': cache,
                                  'Math': math_module}), \
                    mock.patch.object(
                        destructibles_sensor, '_get_destr_authority',
                        return_value=authority):
                result = destructibles_sensor.shot_world_distance(
                    bigworld, 1, _Vector(), _Vector(0, 0, 20),
                    _Vector(0, 0, 1), shot)
            self.assertEqual(should_continue,
                             result['continue_from'] is not None)

        self.assertTrue(lookups)
        self.assertEqual({filename}, set(lookups))

    def test_typed_native_trees_are_transparent_without_skipping_wall(self):
        filenames = (
            'speedtree/45_North_America/Maple.spt',
            'speedtree/45_North_America/Oak.spt')
        tree_hits = (
            (_Vector(0.0, 0.0, 5.0), 1, filenames[0]),
            (_Vector(0.0, 0.0, 5.05), 2, filenames[1]))
        wall = _Vector(0.0, 0.0, 5.1)
        bigworld = types.ModuleType('BigWorld')

        def collide(unused_space, unused_start, unused_end, unused_mask,
                    collision_filter=None):
            for point, item_index, unused_filename in tree_hits:
                if (collision_filter is None or
                        collision_filter(71, 0, item_index, 22)):
                    return point, _Vector(0.0, 0.0, -1.0)
            if (collision_filter is None or
                    collision_filter(1, 0, -1, -1)):
                return wall, _Vector(0.0, 0.0, -1.0)
            return None

        def material_probe(unused_space, unused_start, unused_stop, point,
                           unused_callback):
            for tree_point, item_index, filename in tree_hits:
                if point is tree_point:
                    return _mat_info_1513(
                        True, point, _Vector(0, 1, 0), 71,
                        filename, 22, item_index)
            return _mat_info_1513(False)

        bigworld.wg_collideSegment = collide
        bigworld.wg_getMatInfoNearPoint = material_probe
        manager = _Manager()
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 1, 'health': 20} if value in filenames else None))
        destructibles_sensor.set_event_sink(lambda unused: True)

        for shell_kind in (
                'ARMOR_PIERCING', 'ARMOR_PIERCING_CR',
                'ARMOR_PIERCING_HE', 'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE'):
            destroyed = set()
            calls = []

            def destroy_tree(*args):
                calls.append(args)
                destroyed.add(args[2])
                return True

            authority = types.SimpleNamespace(
                is_destroyed=lambda unused_chunk, item, unused_mat=None: (
                    item in destroyed),
                destroy_tree=destroy_tree)
            shot = types.SimpleNamespace(shell=types.SimpleNamespace(
                kind=shell_kind))
            with mock.patch.dict(
                    sys.modules, {'BigWorld': bigworld,
                                  'AreaDestructibles': area}), \
                    mock.patch.object(
                        destructibles_sensor, '_get_destr_authority',
                        return_value=authority):
                first = destructibles_sensor.shot_world_distance(
                    bigworld, 1, _Vector(), _Vector(0, 0, 20),
                    _Vector(0, 0, 1), shot)
                repeated = destructibles_sensor.shot_world_distance(
                    bigworld, 1, _Vector(), _Vector(0, 0, 20),
                    _Vector(0, 0, 1), shot)

            for result in (first, repeated):
                self.assertAlmostEqual(5.1, result['stop_distance'])
                self.assertIsNone(result['continue_from'])
                self.assertEqual(0.0, result['piercing_loss'])
                self.assertFalse(result['stopped_by_destructible'])
            self.assertEqual(2, len(calls), shell_kind)
            self.assertEqual(
                {(1, 22, 1), (1, 22, 2)},
                {call[:3] for call in calls})

    def test_catalog_obstacle_stops_before_native_trees_are_destroyed(self):
        destructibles_sensor.xrange = range
        fence_filename = 'content/Environment/fence.model'
        tree_filenames = (
            'speedtree/45_North_America/Maple.spt',
            'speedtree/45_North_America/Oak.spt')
        destructibles_sensor.set_catalog(_catalog({
            fence_filename: {
                'kind': 'fragile',
                'boxes': [[-0.5, -1.0, 4.0, 0.5, 2.0, 4.5, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][fence_filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        fence_instance = {
            'filename': fence_filename.lower(),
            'descriptor_filename': fence_filename,
            'kind': 'fragile',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {
            (22, 10): fence_instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 10), fence_instance)

        tree_hits = (
            (_Vector(0.0, 0.0, 5.0), 1, tree_filenames[0]),
            (_Vector(0.0, 0.0, 6.0), 2, tree_filenames[1]))
        bigworld = types.ModuleType('BigWorld')

        def collide(unused_space, unused_start, unused_end, unused_mask,
                    collision_filter=None):
            for point, item_index, unused_filename in tree_hits:
                if (collision_filter is None or
                        collision_filter(71, 0, item_index, 22)):
                    return point, _Vector(0.0, 0.0, -1.0)
            return None

        def material_probe(unused_space, unused_start, unused_stop, point,
                           unused_callback):
            for tree_point, item_index, filename in tree_hits:
                if point is tree_point:
                    return _mat_info_1513(
                        True, point, _Vector(0, 1, 0), 71,
                        filename, 22, item_index)
            return _mat_info_1513(False)

        bigworld.wg_collideSegment = collide
        bigworld.wg_getMatInfoNearPoint = material_probe
        bigworld.time = lambda: 10.0
        manager = _Manager()
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4

        def descriptor(filename):
            if filename in tree_filenames:
                return {'type': 1, 'health': 20}
            if filename == fence_filename:
                return {'type': 3, 'health': 30}
            return None

        area.g_cache = types.SimpleNamespace(getDescByFilename=descriptor)
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: int(
            math.ceil(scale * scale * health))
        tree_calls = []
        fence_calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_tree=lambda *args: tree_calls.append(args) or True,
            destroy_fragile=lambda *args: fence_calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='HOLLOW_CHARGE'))

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1), shot)

        self.assertEqual([], tree_calls)
        self.assertEqual(1, len(fence_calls))
        self.assertAlmostEqual(4.0, result['stop_distance'])
        self.assertTrue(result['stopped_by_destructible'])

    def test_typed_native_low_health_falling_pole_keeps_shell_rules(self):
        destructibles_sensor.xrange = range
        filename = (
            'content/Environment/envAM_009_Poles/normal/lod0/'
            'envAM_009_Poles_01.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'falling',
                'boxes': [[-0.5, -1.0, 4.0, 0.5, 2.0, 6.0, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'falling',
            'item_scale': 1.0,
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 1), instance)
        point = _Vector(0.0, 0.0, 4.0)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: (
            point, _Vector(0.0, 0.0, -1.0))
        bigworld.wg_getMatInfoNearPoint = lambda *unused: _mat_info_1513(
            True, point, _Vector(0, 1, 0), 72, filename, 22, 1)
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 2, 'health': 18} if value == filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: int(
            math.ceil(scale * scale * health))
        destructibles_sensor.set_event_sink(lambda unused: True)

        for shell_kind, should_continue in (
                ('ARMOR_PIERCING', True), ('HOLLOW_CHARGE', False)):
            calls = []
            authority = types.SimpleNamespace(
                is_destroyed=lambda *unused: False,
                destroy_column=lambda *args: calls.append(args) or True)
            shot = types.SimpleNamespace(shell=types.SimpleNamespace(
                kind=shell_kind))
            with mock.patch.dict(
                    sys.modules, {'BigWorld': bigworld,
                                  'AreaDestructibles': area,
                                  'DestructiblesCache': cache,
                                  'Math': math_module}), \
                    mock.patch.object(
                        destructibles_sensor, '_get_destr_authority',
                        return_value=authority):
                result = destructibles_sensor.shot_world_distance(
                    bigworld, 1, _Vector(), _Vector(0, 0, 20),
                    _Vector(0, 0, 1), shot)

            self.assertEqual(1, len(calls), shell_kind)
            self.assertEqual((1, 22, 1), calls[0][:3])
            if should_continue:
                self.assertIsNone(result['stop_distance'])
                self.assertGreater(result['continue_from'], 6.0)
                self.assertLess(result['continue_from'], 6.01)
                self.assertEqual(25.0, result['piercing_loss'])
            else:
                self.assertAlmostEqual(4.0, result['stop_distance'])
                self.assertIsNone(result['continue_from'])
                self.assertEqual(0.0, result['piercing_loss'])
                self.assertTrue(result['stopped_by_destructible'])

    def test_unknown_shell_kind_and_missing_descriptor_stop_fail_closed(self):
        self.assertIsNone(destructibles_sensor._shot_kind_1513(
            types.SimpleNamespace(shell=types.SimpleNamespace(kind='LASER'))))
        self.assertIsNone(destructibles_sensor._shot_kind_1513(
            types.SimpleNamespace(shell=types.SimpleNamespace())))
        self.assertIsNone(
            destructibles_sensor._scaled_shot_through_health_1513(
                None, 73, 1.0))

    def test_native_module_continues_after_exact_thick_obb_exit(self):
        destructibles_sensor.xrange = range
        filename = (
            'content/Buildings/bld000_base/normal/lod0/'
            'bld000_base.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [[-2.0, -1.0, 4.0, 2.0, 2.0, 8.0, 73]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'structure',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 1), instance)
        impact = _Vector(0.0, 0.5, 4.0)
        collide_calls = []
        bigworld = types.ModuleType('BigWorld')

        def collide(unused_space, start, unused_end, unused_mask):
            collide_calls.append(start.z)
            return ((impact, _Vector(0.0, 0.0, -1.0))
                    if len(collide_calls) == 1 else None)

        bigworld.wg_collideSegment = collide
        material_calls = [0]

        def material_probe(*unused):
            material_calls[0] += 1
            if material_calls[0] == 1:
                return _mat_info_1513(
                    True, impact, _Vector(0, 1, 0), 73, filename, 22, 1)
            return _mat_info_1513(False)

        bigworld.wg_getMatInfoNearPoint = material_probe
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 4, 'modules': {73: {'health': 15}}}
                if value == filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: int(
            math.ceil(scale * scale * health))
        destroyed = set()
        authority = types.SimpleNamespace(
            is_destroyed=lambda chunk, item, mat=None: (
                (chunk, item, mat) in destroyed),
            destroy_module=lambda space, chunk, item, mat, point, is_shot: (
                destroyed.add((chunk, item, mat)) or True))
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1), shot)

        self.assertIsNone(result['stop_distance'])
        self.assertEqual(25.0, result['piercing_loss'])
        self.assertGreater(result['continue_from'], 8.0)
        self.assertLess(result['continue_from'], 8.2)

    def test_native_module_exit_keeps_static_wall_authoritative(self):
        destructibles_sensor.xrange = range
        filename = (
            'content/Buildings/bld000_base/normal/lod0/'
            'bld000_base.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [[-2.0, -1.0, 4.0, 2.0, 2.0, 8.0, 73]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'structure',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 1), instance)
        impact = _Vector(0.0, 0.5, 4.0)
        # Static backing starts only 1 cm after the exact z=8.0 OBB exit.
        wall = _Vector(0.0, 0.5, 8.01)
        calls = []
        bigworld = types.ModuleType('BigWorld')

        def collide(unused_space, start, unused_end, unused_mask):
            calls.append(start.z)
            return ((impact, _Vector(0.0, 0.0, -1.0))
                    if len(calls) == 1 else
                    (wall, _Vector(0.0, 0.0, -1.0)))

        bigworld.wg_collideSegment = collide
        material_calls = [0]

        def material_probe(*unused):
            material_calls[0] += 1
            if material_calls[0] == 1:
                return _mat_info_1513(
                    True, impact, _Vector(0, 1, 0), 73, filename, 22, 1)
            return _mat_info_1513(False)

        bigworld.wg_getMatInfoNearPoint = material_probe
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 4, 'modules': {73: {'health': 15}}}
                if value == filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: int(
            math.ceil(scale * scale * health))
        destroyed = set()
        authority = types.SimpleNamespace(
            is_destroyed=lambda chunk, item, mat=None: (
                (chunk, item, mat) in destroyed),
            destroy_module=lambda space, chunk, item, mat, point, is_shot: (
                destroyed.add((chunk, item, mat)) or True))
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            first = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1), shot)
            cursor = _Vector(0, 0, first['continue_from'])
            second = destructibles_sensor.shot_world_distance(
                bigworld, 1, cursor, _Vector(0, 0, 20),
                _Vector(0, 0, 1), shot)

        self.assertGreater(first['continue_from'], 8.0)
        self.assertIsNotNone(second['stop_distance'])
        self.assertAlmostEqual((wall - cursor).length,
                               second['stop_distance'])
        self.assertFalse(second['stopped_by_destructible'])

    def test_shot_does_not_destroy_catalog_object_behind_static_wall(self):
        filename = 'content/Environment/fragile.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, 4.0, 1.0, 2.0, 6.0, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'fragile',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 1), instance)
        wall = _Vector(0.0, 0.5, 2.0)
        bigworld = types.SimpleNamespace(
            wg_collideSegment=lambda *unused: (
                wall, _Vector(0.0, 0.0, -1.0)),
            wg_getMatInfoNearPoint=(
                lambda *unused: _mat_info_1513(False)))
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: self.fail(
                'catalog object behind wall was destroyed'))

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            distance = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1))

        self.assertAlmostEqual(2.0615528128088303, distance)

    def test_shot_destroys_dynamic_only_fragile_and_continues(self):
        filename = 'content/Environment/DynamicFragile.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, 4.0, 1.0, 2.0, 6.0, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'fragile',
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(), _Vector(), math_module),
            'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 1), instance)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: None
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 3, 'health': 5}
                if value == filename else None))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            distance = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1))

        self.assertEqual(99999.0, distance)
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0][-1])

    def test_shot_lazily_registers_far_baked_fragile_after_live_validation(self):
        filename = 'content/MilitaryEnvironment/mle033_WatchTower.model'
        matrix = _ItemMatrix(_Vector(0.0, 0.0, 5.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, -1.0,
                           1.0, 2.0, 1.0, None]],
            },
        }, [list(signature) + [filename, 0, 22, 0, 1.0]]))
        self.assertEqual({}, getattr(
            destructibles_sensor, 'g_offh_destr_instances', {}))

        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 3, 'health': 5} if value == filename else None))
        category_calls = []
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: None
        # A nonblank native name is an independent exact identity channel.
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (filename,)
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *args: category_calls.append(args) or 3)
        bigworld.time = lambda: 10.0
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: int(
            math.ceil(scale * scale * health))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: calls.append(args) or True)
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))
        destructibles_sensor.xrange = range
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1), shot)

        self.assertIsNone(result['stop_distance'])
        self.assertEqual(25.0, result['piercing_loss'])
        self.assertIn((22, 0), destructibles_sensor.g_offh_destr_instances)
        self.assertEqual(2, len(category_calls))
        self.assertEqual([(1, 22, 0, -1)] * 2, category_calls)
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0][-1])

    def test_direct_shot_resource_conflict_stays_on_the_native_surface(self):
        expected = 'content/test/expected-fence.model'
        alternate = 'content/test/alternate-fence.model'
        impact = _Vector(0.0, 0.0, 5.0)
        matrix = _ItemMatrix(impact)
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            expected: {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 2, 1, None]],
            },
            alternate: {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 2, 1, None]],
            },
        }, [list(signature) + [expected, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        descriptors = {
            expected: {'type': 3, 'health': 15},
            alternate: {'type': 3, 'health': 15},
        }
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=descriptors.get)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = mock.Mock(return_value=(impact,))
        bigworld.wg_getMatInfoNearPoint = mock.Mock(return_value=
            _mat_info_1513(
                True, impact, _Vector(0, 1, 0), 75,
                alternate, 22, 0))
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (expected,)
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 3
        bigworld.time = lambda: 10.0
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_fragile=mock.Mock(return_value=True))
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1), shot)

        self.assertEqual(5.0, result['world_distance'])
        self.assertEqual(5.0, result['stop_distance'])
        self.assertEqual(0.0, result['piercing_loss'])
        authority.destroy_fragile.assert_not_called()
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertNotIn(
            (22, 0), getattr(destructibles_sensor,
                            'g_offh_destr_instances', {}))

    def _streamed_fragile_fixture(self):
        filename = 'content/test/streamed-fragile.model'
        matrix = _ItemMatrix(_Vector(0.0, 0.0, 5.0))
        chunk_translation = _Vector()
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, chunk_translation, math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, -1.0,
                           1.0, 2.0, 1.0, None]],
            },
        }, [list(signature) + [filename, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 3, 'health': 15}
                if value == filename else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (filename,)
        bigworld.wg_getDestructibleEffectCategory = mock.Mock(return_value=3)
        bigworld.wg_getChunkMatrix = mock.Mock(return_value=
            types.SimpleNamespace(translation=chunk_translation))
        bigworld.wg_getDestructibleMatrix = mock.Mock(return_value=matrix)
        destructibles_sensor.xrange = range
        return filename, area, bigworld, math_module

    def test_streamed_chunk_matrix_exception_isolates_the_chunk(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())
        bigworld.wg_getChunkMatrix.side_effect = RuntimeError(
            'chunk matrix unavailable')

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertIsNone(
                destructibles_sensor._stream_baked_shot_instance_1513(
                    1, (22, 0)))
            self.assertIsNone(
                destructibles_sensor._stream_baked_shot_instance_1513(
                    1, (22, 0)))

        bigworld.wg_getChunkMatrix.assert_called_once_with(1, 22)
        bigworld.wg_getDestructibleMatrix.assert_not_called()
        self.assertEqual(
            {22}, destructibles_sensor.g_offh_destr_isolated_chunks)
        self.assertEqual(
            {'native_chunk_matrix'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_streamed_missing_chunk_translation_retries_then_registers(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())
        bigworld.wg_getChunkMatrix.side_effect = (
            types.SimpleNamespace(translation=None),
            types.SimpleNamespace(translation=_Vector()))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}):
            first = destructibles_sensor._stream_baked_shot_instance_1513(
                1, (22, 0))
            second = destructibles_sensor._stream_baked_shot_instance_1513(
                1, (22, 0))

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(2, bigworld.wg_getChunkMatrix.call_count)
        self.assertEqual(1, bigworld.wg_getDestructibleMatrix.call_count)
        self.assertNotIn(
            'g_offh_destr_isolated_slots', destructibles_sensor.__dict__)

    def test_streamed_fragile_transform_exception_is_slot_local(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_world_catalog_boxes',
                    side_effect=RuntimeError('OBB transform failed')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertIsNone(
                destructibles_sensor._stream_baked_shot_instance_1513(
                    1, (22, 0)))

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'native_matrix_transform'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_streamed_signature_transform_exception_is_slot_local(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor,
                    '_catalog_instance_for_matrix_1513',
                    side_effect=RuntimeError('signature transform failed')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertIsNone(
                destructibles_sensor._stream_baked_shot_instance_1513(
                    1, (22, 0)))

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'native_matrix_signature'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_streamed_malformed_descriptor_is_slot_local(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())
        area.g_cache.getDescByFilename = mock.Mock(side_effect=(
            {'type': 3, 'health': 15}, object()))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertIsNone(
                destructibles_sensor._stream_baked_shot_instance_1513(
                    1, (22, 0)))

        self.assertEqual(2, area.g_cache.getDescByFilename.call_count)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'catalog_descriptor'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_streamed_handlerless_effect_channel_keeps_exact_identity(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())
        # The compacted name loop omits a resolved item whose native group has
        # no registered name handler.  Its effect-category call returns -1.
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        bigworld.wg_getDestructibleEffectCategory.return_value = -1
        writes = []

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            instance = (
                destructibles_sensor._stream_baked_shot_instance_1513(
                    1, (22, 0)))

        self.assertIsNotNone(instance)
        self.assertEqual('fragile', instance['kind'])
        self.assertEqual(
            [(1, 22, 0, -1), (1, 22, 0, -1)],
            [entry.args for entry in
             bigworld.wg_getDestructibleEffectCategory.call_args_list])
        self.assertNotIn(
            'g_offh_destr_isolated_slots', destructibles_sensor.__dict__)
        self.assertEqual(
            {'effect_category_unregistered'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        self.assertIn('DESTR accepted_native_identity', writes[0])
        self.assertIn('map=06_ensk', writes[0])
        self.assertIn('chunk=22 item=0', writes[0])
        self.assertIn('native=-1 wire=live_validated', writes[0])
        self.assertIn('repeats=suppressed_for_battle', writes[0])

    def test_handlerless_effect_category_reaches_shot_intersection(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())
        # The name loop omits this resolved handlerless item.  The first -1
        # types the compacted alignment and the second validates final admission.
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        bigworld.wg_getDestructibleEffectCategory.return_value = -1
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False)
        writes = []

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            result = destructibles_sensor._catalog_shot_intersection(
                1, _Vector(), _Vector(0, 0, 20))

        self.assertIsNotNone(result)
        self.assertEqual(
            [(1, 22, 0, -1), (1, 22, 0, -1)],
            [entry.args for entry in
             bigworld.wg_getDestructibleEffectCategory.call_args_list])
        self.assertNotIn(
            'g_offh_destr_isolated_slots', destructibles_sensor.__dict__)
        self.assertIn((22, 0), destructibles_sensor.g_offh_destr_instances)
        self.assertEqual(1, len(writes))
        self.assertIn('DESTR accepted_native_identity', writes[0])
        self.assertIn('map=06_ensk', writes[0])
        self.assertIn('chunk=22 item=0', writes[0])
        self.assertIn('native=-1 wire=live_validated', writes[0])
        self.assertIn('repeats=suppressed_for_battle', writes[0])

    def test_far_baked_fragile_fails_closed_until_chunk_is_streamed(self):
        filename = 'content/MilitaryEnvironment/mle008_Canisters.model'
        matrix = _ItemMatrix(_Vector(0.0, 0.0, 5.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, -1.0,
                           1.0, 2.0, 1.0, None]],
            },
        }, [list(signature) + [filename, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: None
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: self.fail(
                'unstreamed baked identity was destroyed'))
        shot = types.SimpleNamespace(shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING'))

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1), shot)

        self.assertTrue(result['stopped_by_destructible'])
        self.assertAlmostEqual(4.0, result['stop_distance'])
        self.assertNotIn((22, 0), getattr(
            destructibles_sensor, 'g_offh_destr_instances', {}))

    def test_baked_slot_beyond_native_count_is_isolated_before_queries(self):
        filename = 'content/MilitaryEnvironment/mle008_Canisters.model'
        matrix = _ItemMatrix(_Vector(0.0, 0.0, 5.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, -1.0,
                           1.0, 2.0, 1.0, None]],
            },
        }, [list(signature) + [filename, 0, 22, 1, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = mock.Mock(
            side_effect=AssertionError('queried past native count'))
        writes = []

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            self.assertIsNone(
                destructibles_sensor._stream_baked_shot_instance_1513(
                    1, (22, 1)))

        self.assertEqual({(22, 1)},
                         destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual({'native_count_range'},
                         destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        bigworld.wg_getChunkDestrFilenames.assert_not_called()

    def test_streamed_empty_names_unresolved_item_never_queries_matrix(self):
        filename = 'content/test/unnamed-fragile.model'
        matrix = _ItemMatrix(_Vector(0.0, 0.0, 5.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, -1.0,
                           1.0, 2.0, 1.0, None]],
            },
        }, [list(signature) + [filename, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {'type': 3, 'health': 15})
        category = mock.Mock(side_effect=ValueError('unresolved item'))
        matrix_query = mock.Mock(
            side_effect=AssertionError('unresolved slot reached matrix query'))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        bigworld.wg_getDestructibleEffectCategory = category
        bigworld.wg_getDestructibleMatrix = matrix_query

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            instance = destructibles_sensor._stream_baked_shot_instance_1513(
                1, (22, 0))

        self.assertIsNone(instance)
        category.assert_called_once_with(1, 22, 0, -1)
        matrix_query.assert_not_called()
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'name_item_unresolved'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_far_baked_fragile_effect_mismatch_fails_closed(self):
        filename = 'content/MilitaryEnvironment/mle011_GroupBoxes.model'
        matrix = _ItemMatrix(_Vector(0.0, 0.0, 5.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, -1.0,
                           1.0, 2.0, 1.0, None]],
            },
        }, [list(signature) + [filename, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 3, 'health': 5} if value == filename else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        # The exact wire/signature says fragile, but the live native category
        # says structure.  Baked geometry must not authorize this mutation.
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 4
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False)

        writes = []
        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            result = destructibles_sensor._catalog_shot_intersection(
                1, _Vector(), _Vector(0, 0, 20))

        self.assertIsNone(result)
        self.assertEqual({(22, 0)},
                         destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual({'effect_category'},
                         destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        self.assertNotIn((22, 0), getattr(
            destructibles_sensor, 'g_offh_destr_instances', {}))

    def test_baked_broad_phase_covers_signature_quantization_at_bin_edge(self):
        filename = 'content/MilitaryEnvironment/mle008_Canisters.model'
        # The live origin still quantizes to 7.999, while this asymmetric box
        # crosses the 8 m bin edge only in the exact live transform.
        matrix = _ItemMatrix(_Vector(7.99949, 0.0, 5.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        record = {
            'kind': 'fragile',
            'boxes': [[-1.0, -1.0, -1.0,
                       0.0006, 2.0, 1.0, None]],
        }
        destructibles_sensor.set_catalog(_catalog({
            filename: record,
        }, [list(signature) + [filename, 0, 22, 0, 1.0]]))
        prepared = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        live = {
            'filename': filename.lower(),
            'descriptor_filename': filename,
            'kind': 'fragile',
            'boxes': destructibles_sensor._world_catalog_boxes(
                prepared, matrix, _Vector(), math_module, 0),
            'item_scale': 1.0,
            'box_index': 0,
        }
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False)

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority), mock.patch.object(
                destructibles_sensor, '_stream_baked_shot_instance_1513',
                return_value=live) as streamed:
            hit = destructibles_sensor._catalog_shot_intersection(
                1, _Vector(8.00005, 0.0, 0.0),
                _Vector(8.00005, 0.0, 20.0))

        self.assertIsNotNone(hit)
        self.assertFalse(hit['ambiguous'])
        self.assertEqual((22, 0), hit['candidate'][:2])
        streamed.assert_called_once_with(1, (22, 0))

    def test_typed_shot_uses_scaled_health_and_shell_family(self):
        destructibles_sensor.xrange = range
        filename = 'content/Environment/ScaledFragile.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -1.0, 4.0, 1.0, 2.0, 6.0, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instance = {
            'filename': filename.lower(), 'descriptor_filename': filename,
            'kind': 'fragile', 'item_scale': 2.0,
            'boxes': destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(scale=2.0), _Vector(), math_module),
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 1), instance)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = lambda *unused: None
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 3, 'health': 5} if value == filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: int(
            math.ceil(scale * scale * health))

        for shell_kind, expected_stop, expected_loss in (
                ('ARMOR_PIERCING', True, 0.0),
                ('HOLLOW_CHARGE', True, 0.0),
                ('HIGH_EXPLOSIVE', True, 0.0)):
            destroyed = set()
            authority = types.SimpleNamespace(
                is_destroyed=lambda chunk, item, mat=None: (
                    (chunk, item, mat) in destroyed),
                destroy_fragile=lambda space, chunk, item, point, is_shot: (
                    destroyed.add((chunk, item, None)) or True))
            shot = types.SimpleNamespace(shell=types.SimpleNamespace(
                kind=shell_kind))
            with mock.patch.dict(
                    sys.modules, {'BigWorld': bigworld,
                                  'AreaDestructibles': area,
                                  'DestructiblesCache': cache,
                                  'Math': math_module}), \
                    mock.patch.object(
                        destructibles_sensor, '_get_destr_authority',
                        return_value=authority):
                result = destructibles_sensor.shot_world_distance(
                    bigworld, 1, _Vector(), _Vector(0, 0, 20),
                    _Vector(0, 0, 1), shot)
            self.assertEqual(expected_stop, result['stop_distance'] is not None)
            self.assertEqual(expected_loss, result['piercing_loss'])
            self.assertIn((22, 1, None), destroyed)

        instance['item_scale'] = 1.9
        for shell_kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR',
                           'ARMOR_PIERCING_HE'):
            destroyed = set()
            authority = types.SimpleNamespace(
                is_destroyed=lambda chunk, item, mat=None: (
                    (chunk, item, mat) in destroyed),
                destroy_fragile=lambda space, chunk, item, point, is_shot: (
                    destroyed.add((chunk, item, None)) or True))
            shot = types.SimpleNamespace(shell=types.SimpleNamespace(
                kind=shell_kind))
            with mock.patch.dict(
                    sys.modules, {'BigWorld': bigworld,
                                  'AreaDestructibles': area,
                                  'DestructiblesCache': cache,
                                  'Math': math_module}), \
                    mock.patch.object(
                        destructibles_sensor, '_get_destr_authority',
                        return_value=authority):
                result = destructibles_sensor.shot_world_distance(
                    bigworld, 1, _Vector(), _Vector(0, 0, 20),
                    _Vector(0, 0, 1), shot)
            self.assertIsNone(result['stop_distance'])
            self.assertEqual(25.0, result['piercing_loss'])

        instance['item_scale'] = None
        destroyed = set()
        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            result = destructibles_sensor.shot_world_distance(
                bigworld, 1, _Vector(), _Vector(0, 0, 20),
                _Vector(0, 0, 1), shot)
        self.assertIsNotNone(result['stop_distance'])
        self.assertEqual(0.0, result['piercing_loss'])

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
        manager.set_chunk_count(22, 1)
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
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 1
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
        self.assertEqual(2, len(calls))
        self.assertEqual(1, len(events))
        self.assertEqual((102.0, 8.0, 204.0),
                         (object_position.x, object_position.y,
                          object_position.z))
        self.assertIn((22, 0), destructibles_sensor.g_offh_tree_state['felled'])
        self.assertEqual((102.0, 8.0, 204.0),
                         (events[0]['x'], events[0]['y'], events[0]['z']))

    def test_shifted_compacted_tree_name_cannot_destroy_earlier_item(self):
        tree = 'speedtree/test/oak.spt'
        tree_descriptor = {'type': 1, 'health': 10, 'mass': 20}
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6),
                    (1.6, 1.0, 3.6), None))))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 2)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda filename: (
                tree_descriptor if filename == tree else None))
        chunk_matrix = types.SimpleNamespace(translation=_Vector(100, 5, 200))
        item_matrices = (
            types.SimpleNamespace(translation=_Vector(2, 3, 4)),
            types.SimpleNamespace(translation=_Vector(40, 3, 40)))
        bigworld = types.ModuleType('BigWorld')
        # Item 0 is an unnamed fragile near the vehicle.  The one compacted
        # tree name belongs to distant item 1 and must never be lent to item 0.
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (tree,)
        category_calls = []

        def effect_category(space, chunk, item, module):
            category_calls.append((space, chunk, item, module))
            return 3 if item == 0 else 1

        bigworld.wg_getDestructibleEffectCategory = effect_category
        bigworld.wg_getChunkMatrix = lambda *unused: chunk_matrix
        bigworld.wg_getDestructibleMatrix = (
            lambda unused_space, unused_chunk, index: item_matrices[index])
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        destroy_tree = mock.Mock(
            side_effect=AssertionError('shifted item reached native destroy'))
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
            destructibles_sensor._fell_trees_near(
                1, _Vector(102, 8, 200), 0.0, 6.0,
                type_descriptor)
            self.assertEqual(
                ('exact', None),
                destructibles_sensor.resolve_native_item_name_1513(
                    1, 22, 0))
            self.assertEqual(
                ('exact', tree),
                destructibles_sensor.resolve_native_item_name_1513(
                    1, 22, 1))

        destroy_tree.assert_not_called()
        self.assertEqual([], events)
        self.assertEqual(
            set(), destructibles_sensor.g_offh_tree_state['felled'])
        self.assertNotIn((22, 0), destructibles_sensor.g_offh_destr_instances)
        self.assertEqual(
            set(), getattr(destructibles_sensor,
                           'g_offh_destr_isolated_slots', set()))
        self.assertEqual(
            1, destructibles_sensor.g_offh_tree_state['chunks'][22]['count'])
        self.assertEqual(
            [(1, 22, 0, -1), (1, 22, 1, -1)],
            category_calls)

    def test_ensk_catalog_boxes_register_without_proximity_destruction(self):
        short_fence = (
            'content/GatesAndFences/gaf010_Fence/normal/lod0/'
            'gaf010_FenceTile1.model')
        long_fence = (
            'content/GatesAndFences/gaf011_Fence/normal/lod0/'
            'gaf011_FenceTile1.model')
        unknown = 'content/Environment/unknown/normal/lod0/small_prop.model'
        filenames = (short_fence, long_fence, unknown)
        destructibles_sensor.set_catalog(_catalog({
            short_fence: {
                'kind': 'fragile',
                'boxes': [[-0.148521, -0.034854, -2.234545,
                           0.018152, 1.88874, 0.006821, None]],
            },
            long_fence: {
                'kind': 'fragile',
                'boxes': [[-0.149872, -0.082076, -8.291013,
                           0.049425, 1.505759, 0.25288, None]],
            },
        }))
        descriptor = {'type': 3, 'health': 3}
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6),
                    (1.6, 1.0, 3.6), None))))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 3)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: descriptor)
        chunk_matrix = types.SimpleNamespace(translation=_Vector())

        item_matrices = (
            # The first is rotated across the hull; the long fence reaches it
            # from an origin outside the old 8 m gate.
            _ItemMatrix(_Vector(3.5, 0.0, 0.0), 1.5707963267948966),
            _ItemMatrix(_Vector(0.0, 0.0, 11.0)),
            _ItemMatrix(_Vector(0.0, 0.0, 3.0)))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: filenames
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 3
        bigworld.wg_getChunkMatrix = lambda *unused: chunk_matrix
        bigworld.wg_getDestructibleMatrix = (
            lambda unused_space, unused_chunk, index: item_matrices[index])
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: calls.append(args) or True)
        destructibles_sensor.xrange = range
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 0.0, type_descriptor)

        self.assertEqual([], calls)
        self.assertEqual(
            [(22, 0), (22, 1)],
            sorted(destructibles_sensor.g_offh_destr_instances))

    def test_blank_native_slots_use_unique_v4_matrix_identities(self):
        fence = (
            'content/GatesAndFences/gaf022/normal/lod0/fence.model')
        pole = 'content/Environment/env414/normal/lod0/pole.model'
        chunk_translation = _Vector(100.0, 5.0, 200.0)
        matrices = (_ItemMatrix(_Vector(2.0, 0.0, 4.0)),
                    _ItemMatrix(_Vector(-3.0, 0.0, 6.0)))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signatures = [destructibles_sensor._locator_signature(
            matrix, chunk_translation, math_module, 1000)
            for matrix in matrices]
        destructibles_sensor.set_catalog(_catalog({
            fence: {
                'kind': 'fragile',
                'boxes': [[-0.2, 0.0, -2.0, 0.2, 1.5, 2.0, None]],
            },
            pole: {
                'kind': 'falling',
                'boxes': [[-0.3, 0.0, -0.3, 0.3, 9.0, 0.3, None]],
            },
        }, [list(signatures[0]) + [fence, 0, 22, 0, 1.0],
            list(signatures[1]) + [pole, 0, 22, 1, 1.0]]))
        descriptors = {
            fence: {'type': 3, 'health': 15},
            pole: {'type': 2, 'health': 1},
        }
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 2)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: descriptors.get(value))
        bigworld = types.ModuleType('BigWorld')
        # The exact client emits a full-width list here, with anonymous BSMI
        # slots represented by legal empty strings.
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ('', '')
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=chunk_translation)
        bigworld.wg_getDestructibleMatrix = (
            lambda unused_space, unused_chunk, index: matrices[index])
        bigworld.wg_getDestructibleEffectCategory = (
            lambda unused_space, unused_chunk, index, unused_module:
            (3, 2)[index])
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range
        # The per-slot record is only retained for a diagnostics build.
        destructibles_sensor.set_diagnostics(True)
        self.addCleanup(destructibles_sensor.set_diagnostics, False)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(102.0, 5.0, 204.0), 0.0, 0.25,
                type_descriptor)

        instances = destructibles_sensor.g_offh_destr_instances
        self.assertEqual([(22, 0), (22, 1)], sorted(instances))
        self.assertEqual('fragile', instances[(22, 0)]['kind'])
        self.assertEqual('falling', instances[(22, 1)]['kind'])
        self.assertEqual(fence.lower(), instances[(22, 0)]['filename'])
        self.assertEqual(pole.lower(), instances[(22, 1)]['filename'])
        self.assertEqual('registered_falling',
                         destructibles_sensor.g_offh_tree_state['chunks'][22][
                             'slot_diagnostics'][1]['result'])

    def test_native_count_registers_unnamed_slots_beside_named_trees(self):
        tree = 'content/Trees/tree/normal/lod0/tree.model'
        fence = 'content/GatesAndFences/fence/normal/lod0/fence.model'
        pole = 'content/Environment/pole/normal/lod0/pole.model'
        chunk_translation = _Vector(100.0, 5.0, 200.0)
        matrices = (_ItemMatrix(_Vector(40.0, 0.0, 40.0)),
                    _ItemMatrix(_Vector(2.0, 0.0, 4.0)),
                    _ItemMatrix(_Vector(-3.0, 0.0, 6.0)))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signatures = [destructibles_sensor._locator_signature(
            matrix, chunk_translation, math_module, 1000)
            for matrix in matrices[1:]]
        destructibles_sensor.set_catalog(_catalog({
            fence: {
                'kind': 'fragile',
                'boxes': [[-0.2, 0.0, -2.0, 0.2, 1.5, 2.0, None]],
            },
            pole: {
                'kind': 'falling',
                'boxes': [[-0.3, 0.0, -0.3, 0.3, 9.0, 0.3, None]],
            },
        }, [list(signatures[0]) + [fence, 0, 22, 1, 1.0],
            list(signatures[1]) + [pole, 0, 22, 2, 1.0]]))
        descriptors = {
            tree: {'type': 1, 'health': 10, 'mass': 20},
            fence: {'type': 3, 'health': 15},
            pole: {'type': 2, 'health': 1},
        }
        manager = _Manager()
        manager.space_id = 1
        manager._DestructiblesManager__loadedChunkIDs = {22: 3}
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: descriptors.get(value))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (tree,)
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=chunk_translation)
        bigworld.wg_getDestructibleMatrix = (
            lambda unused_space, unused_chunk, index: matrices[index])
        bigworld.wg_getDestructibleEffectCategory = (
            lambda unused_space, unused_chunk, index, unused_module:
            (1, 3, 2)[index])
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range
        diagnostic_lines = []
        destructibles_sensor.set_diagnostics(
            True, diagnostic_lines.append)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(102.0, 5.0, 204.0), 0.0, 0.25,
                type_descriptor)

        instances = destructibles_sensor.g_offh_destr_instances
        self.assertEqual([(22, 1), (22, 2)], sorted(instances))
        self.assertEqual('fragile', instances[(22, 1)]['kind'])
        self.assertEqual('falling', instances[(22, 2)]['kind'])
        self.assertEqual(1, len(diagnostic_lines))
        self.assertIn(
            'chunk=22 slots=3 names=1 named_items=1 names_status=exact '
            'named=1 blank=2',
                      diagnostic_lines[0])
        self.assertIn('v4_unique=2', diagnostic_lines[0])
        self.assertIn('registered=falling:1,fragile:1,tree:1',
                      diagnostic_lines[0])
        self.assertIn('boxes=2', diagnostic_lines[0])

    def test_native_count_smaller_than_filename_prefix_isolates_chunk(self):
        manager = _Manager()
        manager.space_id = 1
        manager._DestructiblesManager__loadedChunkIDs = {22: 1}
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ('one', 'two')
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        writes = []
        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 0.25, type_descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 0.25, type_descriptor)

        self.assertEqual({22}, destructibles_sensor.g_offh_destr_isolated_chunks)
        self.assertEqual({'filename_prefix'},
                         destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        self.assertIn('type=filename_prefix scope=chunk chunk=22', writes[0])
        self.assertIn('map=unknown', writes[0])
        self.assertIn('repeats=suppressed_for_battle', writes[0])
        self.assertEqual([], manager.orders)

    def test_missing_native_count_abi_isolates_multiple_chunks_with_one_log(self):
        manager = _Manager()
        manager.space_id = 1
        del manager._DestructiblesManager__loadedChunkIDs
        writes = []

        with mock.patch.object(
                sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            self.assertIsNone(
                destructibles_sensor._native_chunk_destructible_count_1513(
                    manager, 22))
            self.assertIsNone(
                destructibles_sensor._native_chunk_destructible_count_1513(
                    manager, 23))

        self.assertEqual({22, 23},
                         destructibles_sensor.g_offh_destr_isolated_chunks)
        self.assertEqual({'native_count_abi'},
                         destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        self.assertIn('type=native_count_abi', writes[0])
        self.assertIn('repeats=suppressed_for_battle', writes[0])

    def test_soft_isolation_logs_have_a_strict_per_battle_cap(self):
        writes = []
        with mock.patch.object(
                sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            for index in range(20):
                destructibles_sensor._isolate_destructible_1513(
                    'failure_%s' % index, 22, index)

        self.assertEqual(20, len(
            destructibles_sensor.g_offh_destr_isolated_slots))
        self.assertEqual(
            destructibles_sensor._ISOLATION_LOG_TYPE_LIMIT, len(
            destructibles_sensor.g_offh_destr_isolation_logs))
        self.assertEqual(
            destructibles_sensor._ISOLATION_LOG_TYPE_LIMIT + 1, len(writes))
        self.assertIn(
            'limit=%d additional_types=suppressed_for_battle' %
            destructibles_sensor._ISOLATION_LOG_TYPE_LIMIT,
            writes[-1])

    def test_isolated_wire_never_reaches_native_destroy(self):
        with mock.patch.object(
                sys, 'stdout', types.SimpleNamespace(write=lambda unused: None)):
            destructibles_sensor._isolate_destructible_1513(
                'wire_identity_mismatch', 22, 7)
        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority') as get_authority:
            self.assertFalse(destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(
                    True, mat_kind=73, filename='unsafe.model',
                    chunk_id=22, item_index=7), 0.0, 10.0))

        get_authority.assert_not_called()

    def test_unregistered_isolation_does_not_scan_the_contact_index(self):
        class _NoScanDict(dict):
            def __iter__(self):
                raise AssertionError('unregistered isolation scanned bins')

        members = {(99, 1)}
        destructibles_sensor.g_offh_destr_contact_bins = _NoScanDict({
            (0, 0): members,
        })
        with mock.patch.object(
                sys, 'stdout', types.SimpleNamespace(write=lambda unused: None)):
            destructibles_sensor._isolate_destructible_1513(
                'wire_identity_mismatch', 22, 7)

        self.assertEqual({(99, 1)}, members)

    def test_normal_motion_updates_drain_one_time_chunk_diagnostics(self):
        lines = []
        destructibles_sensor.set_diagnostics(True, lines.append)
        destructibles_sensor._diagnostic_enqueue_1513(
            'chunk', ('ready', 31614),
            (('chunk', 31614), ('slots', 3)), now=0.0)
        destructibles_sensor._diagnostic_enqueue_1513(
            'chunk', ('ready', 31615),
            (('chunk', 31615), ('slots', 2)), now=0.0)
        self.assertEqual(1, len(lines))

        destructibles_sensor._catalog_motion_blocked(
            1, _Vector(), 0.0, 0.0, None, 0.30)

        self.assertEqual(2, len(lines))
        self.assertIn('chunk=31614 slots=3', lines[0])
        self.assertIn('chunk=31615 slots=2', lines[1])

    def test_chunk_without_native_count_retries_without_reading_names(self):
        manager = _Manager()
        manager.space_id = 1
        manager._DestructiblesManager__loadedChunkIDs = {}
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = mock.Mock(
            side_effect=AssertionError('names read before native load'))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range
        diagnostic_lines = []
        destructibles_sensor.set_diagnostics(
            True, diagnostic_lines.append)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 0.25, type_descriptor)

        bigworld.wg_getChunkDestrFilenames.assert_not_called()
        self.assertNotIn(22, destructibles_sensor.g_offh_tree_state['chunks'])
        self.assertEqual(1, len(diagnostic_lines))
        self.assertIn('chunk=22 state=count_pending', diagnostic_lines[0])

    def test_loaded_chunk_without_names_retries_and_reports_once(self):
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 3)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = mock.Mock(return_value=None)
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range
        diagnostic_lines = []
        destructibles_sensor.set_diagnostics(
            True, diagnostic_lines.append)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 0.25, type_descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 0.25, type_descriptor)

        self.assertEqual(2, bigworld.wg_getChunkDestrFilenames.call_count)
        self.assertNotIn(22, destructibles_sensor.g_offh_tree_state['chunks'])
        self.assertEqual(1, len(diagnostic_lines))
        self.assertIn(
            'chunk=22 state=names_pending slots=3', diagnostic_lines[0])

    def test_pending_chunk_limit_does_not_consume_ready_chunk_budget(self):
        destructibles_sensor.set_diagnostics(True, lambda unused: None)
        for chunk_id in range(10, 15):
            destructibles_sensor._diagnostic_chunk_pending_1513(
                'count_pending', chunk_id)
        for chunk_id in range(100, 124):
            destructibles_sensor._diagnostic_enqueue_1513(
                'chunk', ('ready', chunk_id), (('chunk', chunk_id),),
                now=0.0)

        state = destructibles_sensor.g_offh_destr_diagnostics
        self.assertEqual(4, len(state['seen_pending']))
        self.assertEqual(24, len(state['seen_chunks']))

    def test_blank_v4_ambiguous_slot_is_permanently_isolated(self):
        unique = 'content/test/normal/lod0/unique.model'
        ambiguous = 'content/test/normal/lod0/ambiguous.model'
        chunk_translation = _Vector()
        matrix = _ItemMatrix(_Vector(2.0, 0.0, 4.0))
        other = _ItemMatrix(_Vector(50.0, 0.0, 50.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, chunk_translation, math_module, 1000)
        other_signature = destructibles_sensor._locator_signature(
            other, chunk_translation, math_module, 1000)
        catalog = _catalog({
            unique: {
                'kind': 'fragile',
                'boxes': [[-1, 0, -1, 1, 1, 1, None]],
            },
            ambiguous: {
                'kind': 'falling',
                'boxes': [[-1, 0, -1, 1, 3, 1, None]],
            },
        }, [list(other_signature) + [unique, 0, 23, 0, 1.0]], [
            list(signature) + [[[ambiguous, 0], [ambiguous, 0]]],
        ])
        destructibles_sensor.set_catalog(catalog)
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {'type': 2, 'health': 18})
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=chunk_translation)
        bigworld.wg_getDestructibleMatrix = mock.Mock(return_value=matrix)
        bigworld.wg_getDestructibleEffectCategory = mock.Mock(return_value=2)
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'ambiguous slot reached native destruction')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(2.0, 0.0, 4.0), 0.0, 6.0,
                type_descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(2.0, 0.0, 4.0), 0.0, 6.0,
                type_descriptor)

        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'catalog_signature_ambiguous'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        bigworld.wg_getDestructibleMatrix.assert_called_once_with(1, 22, 0)
        bigworld.wg_getDestructibleEffectCategory.assert_called_once_with(
            1, 22, 0, -1)

    def test_v4_signature_miss_isolates_named_and_unnamed_slots(self):
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        for named in (True, False):
            with self.subTest(named=named):
                filename, area, bigworld, math_module = (
                    self._streamed_fragile_fixture())
                bigworld.wg_getChunkDestrFilenames = mock.Mock(
                    return_value=(filename,) if named else ())
                bigworld.wg_getDestructibleMatrix = mock.Mock(return_value=
                    _ItemMatrix(_Vector(40.0, 0.0, 40.0)))

                with mock.patch.dict(
                        sys.modules, {'AreaDestructibles': area,
                                      'BigWorld': bigworld,
                                      'Math': math_module}), \
                        mock.patch.object(
                            destructibles_sensor, '_get_destr_authority',
                            side_effect=AssertionError(
                                'signature miss reached native destruction')), \
                        mock.patch.object(sys, 'stdout', mock.Mock()):
                    destructibles_sensor._fell_trees_near(
                        1, _Vector(), 0.0, 6.0, type_descriptor)
                    destructibles_sensor._fell_trees_near(
                        1, _Vector(), 0.0, 6.0, type_descriptor)

                self.assertEqual(
                    {(22, 0)},
                    destructibles_sensor.g_offh_destr_isolated_slots)
                self.assertEqual(
                    {'catalog_signature_miss'},
                    destructibles_sensor.g_offh_destr_isolation_logs)
                bigworld.wg_getDestructibleMatrix.assert_called_once_with(
                    1, 22, 0)
                bigworld.wg_getDestructibleEffectCategory.assert_called_once_with(
                    1, 22, 0, -1)
                self.assertEqual(
                    {}, destructibles_sensor.g_offh_destr_instances)

    def test_v4_signature_transform_exception_is_slot_local(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor,
                    '_catalog_instance_for_matrix_1513',
                    side_effect=RuntimeError('signature transform failed')), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'signature failure reached native destruction')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'native_matrix_signature'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_v4_fragile_obb_transform_exception_is_slot_local(self):
        unused_filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_world_catalog_boxes',
                    side_effect=RuntimeError('OBB transform failed')), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'OBB failure reached native destruction')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'native_matrix_transform'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_v4_exact_resource_with_wrong_descriptor_kind_isolated(self):
        filename, area, bigworld, math_module = (
            self._streamed_fragile_fixture())
        area.g_cache.getDescByFilename = lambda value: (
            {'type': 1, 'health': 10} if value == filename else None)
        bigworld.wg_getDestructibleEffectCategory.return_value = 1
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    side_effect=AssertionError(
                        'wrong descriptor kind reached native destruction')), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'catalog_kind_identity'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_same_kind_different_filename_isolates_without_alias_proof(self):
        expected = 'content/test/normal/lod0/expected.model'
        matrix = _ItemMatrix()
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            expected: {
                'kind': 'fragile',
                'boxes': [[-1, 0, -1, 1, 1, 1, None]],
            },
        }, [list(signature) + [expected, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(getDescByFilename=lambda value: {
            'type': 3, 'health': 15})
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = (
            lambda *unused: ('content/test/normal/lod0/other.model',))
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 3
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        writes = []
        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)

        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'filename_identity_conflict'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        self.assertIn(
            'DESTR isolated type=filename_identity_conflict '
            'scope=slot chunk=22 item=0',
            writes[0])
        self.assertIn('native_kind=fragile catalog_kind=fragile', writes[0])
        self.assertIn('repeats=suppressed_for_battle', writes[0])

    def test_synthetic_tree_and_model_conflict_isolates_before_native_calls(self):
        # This one-item fixture proves the generic conflict policy using the
        # two filenames seen in old Prokhorovka diagnostics.  Those diagnostics
        # indexed a compacted list directly, so they do not prove that real
        # (31875, 70) still owns the poplar after exact reconstruction; that
        # mapping remains a Windows diagnostic boundary.
        poplar = 'speedtree/05_prohorovka/poplar.spt'
        toilet = ('content/Environment/env014_Toilet/normal/lod0/'
                  'env014_Toilet.model')
        matrix = _ItemMatrix()
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            toilet: {
                'kind': 'fragile',
                'boxes': [[-1, 0, -1, 1, 1, 1, None]],
            },
        }, [list(signature) + [toilet, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        descriptors = {
            poplar: {'type': 1, 'health': 10},
            toilet: {'type': 3, 'health': 15},
        }
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=descriptors.get)
        category_calls = []
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (poplar,)
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = (
            lambda space, chunk, item, module:
            category_calls.append((space, chunk, item, module)) or 1)
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: self.fail(
                'an isolated slot must not reach native destruction'),
            destroy_tree=lambda *unused: self.fail(
                'an isolated slot must not reach native destruction'))
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        writes = []
        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)
            # A repeated scan must stay bounded and must not re-log.
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'filename_identity_conflict'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)
        self.assertEqual(1, len(writes))
        self.assertIn(
            'type=filename_identity_conflict scope=slot chunk=22 item=0',
            writes[0])
        # The synthetic exact-name disagreement retains the reported resources.
        self.assertIn(
            'native=%s catalog=%s' % (poplar, toilet.lower()), writes[0])
        self.assertIn('native_kind=None catalog_kind=fragile', writes[0])
        # Only the null-safe type query for the alignment ran; no module
        # effect-category query was made for the isolated slot.
        self.assertEqual([(1, 22, 0, -1)], category_calls)

    def test_streamed_shot_reaches_the_scan_decision_for_a_conflict(self):
        # Requirement: a streamed chunk and the proximity scanner must derive
        # the same identity for the same native item.
        poplar = 'speedtree/05_prohorovka/poplar.spt'
        toilet = ('content/Environment/env014_Toilet/normal/lod0/'
                  'env014_Toilet.model')
        matrix = _ItemMatrix()
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            toilet: {
                'kind': 'fragile',
                'boxes': [[-1, 0, -1, 1, 1, 1, None]],
            },
        }, [list(signature) + [toilet, 0, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        descriptors = {
            poplar: {'type': 1, 'health': 10},
            toilet: {'type': 3, 'health': 15},
        }
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=descriptors.get)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (poplar,)
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 1

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertIsNone(
                destructibles_sensor._stream_baked_shot_instance_1513(
                    1, (22, 0)))

        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'filename_identity_conflict'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_partial_alignment_isolates_without_lending_a_descriptor(self):
        # A chunk whose compaction cannot be reconstructed yields no per-item
        # names at all.  Even an otherwise aligned type is not admitted from a
        # partial chunk, because ownership of the remaining name is unknown.
        tree = 'speedtree/05_prohorovka/poplar.spt'
        fence = ('content/GatesAndFences/gaf001_WoodFence/normal/lod0/'
                 'gaf001_WoodFence.model')
        descriptors = {
            tree: {'type': 1, 'health': 10},
            fence: {'type': 3, 'health': 15},
        }
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 3)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=descriptors.get)
        bigworld = types.ModuleType('BigWorld')
        # Two fragile items but a single fragile name: that type cannot be
        # placed, while the tree type still aligns exactly.
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (fence, tree)
        bigworld.wg_getDestructibleEffectCategory = (
            lambda unused_space, unused_chunk, item, unused_module:
            3 if item < 2 else 1)
        scalar = mock.Mock(
            side_effect=AssertionError('unsafe scalar wrapper was called'))
        bigworld.wg_getDestructibleFilename = scalar

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld}), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            self.assertEqual(
                ('invalid', None),
                destructibles_sensor.resolve_native_item_name_1513(1, 22, 2))
            self.assertEqual(
                ('invalid', None),
                destructibles_sensor.resolve_native_item_name_1513(1, 22, 0))

        scalar.assert_not_called()
        self.assertEqual(
            {22}, destructibles_sensor.g_offh_destr_isolated_chunks)
        self.assertEqual(
            {'name_alignment'},
            destructibles_sensor.g_offh_destr_isolation_logs)

    def test_v4_bad_name_payload_and_final_effect_query_fail_closed(self):
        filename = 'content/test/normal/lod0/fence.model'
        matrix = _ItemMatrix()
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        catalog = _catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1, 0, -1, 1, 1, 1, None]],
            },
        }, [list(signature) + [filename, 0, 22, 0, 1.0]])
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {'type': 3, 'health': 15})
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        destructibles_sensor.set_catalog(catalog)
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (None,)
        writes = []
        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)

        self.assertEqual({22},
                         destructibles_sensor.g_offh_destr_isolated_chunks)
        self.assertEqual({'filename_payload'},
                         destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        self.assertIn('type=filename_payload scope=chunk chunk=22',
                      writes[0])
        self.assertIn('map=06_ensk', writes[0])

        destructibles_sensor.set_catalog(catalog)
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        category_query = mock.Mock(side_effect=(
            3, RuntimeError('final effect validation failed')))
        bigworld.wg_getDestructibleEffectCategory = category_query
        matrix_query = mock.Mock(return_value=matrix)
        bigworld.wg_getDestructibleMatrix = matrix_query
        writes = []
        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)

        self.assertEqual({(22, 0)},
                         destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual({'effect_query'},
                         destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        self.assertIn('type=effect_query scope=slot chunk=22 item=0', writes[0])
        self.assertIn('map=06_ensk', writes[0])
        category_query.assert_has_calls([
            mock.call(1, 22, 0, -1),
            mock.call(1, 22, 0, -1),
        ])
        matrix_query.assert_called_once_with(1, 22, 0)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_blank_v4_structure_keeps_all_module_boxes(self):
        filename = 'content/Buildings/test/normal/lod0/house.model'
        matrix = _ItemMatrix(_Vector(3.0, 0.0, 4.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, _Vector(), math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [
                    [-2, 0, -2, 0, 3, 2, 73],
                    [0, 0, -2, 2, 3, 2, 74],
                ],
            },
        }, [list(signature) + [filename, None, 22, 0, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.DESTRUCTIBLE_MATKIND = types.SimpleNamespace(
            NORMAL_MIN=73, NORMAL_MAX=86)
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: ({
                'type': 4,
                'modules': {73: {'health': 15}, 74: {'health': 15}},
            } if value == filename else None))
        category_calls = []
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = (
            lambda space, chunk, item, module:
            category_calls.append((space, chunk, item, module)) or 4)
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(3.0, 0.0, 4.0), 0.0, 0.25,
                type_descriptor)

        instance = destructibles_sensor.g_offh_destr_instances[(22, 0)]
        self.assertEqual('structure', instance['kind'])
        self.assertEqual([73, 74], sorted(box[2] for box in instance['boxes']))
        self.assertEqual(
            [(1, 22, 0, -1), (1, 22, 0, 0), (1, 22, 0, 1)],
            category_calls)

    def test_falling_catalog_locator_uses_nonstructure_selection(self):
        filename = 'content/Environment/pole/normal/lod0/pole.model'
        signature = [0, 0, 0,
                     1000, 0, 0, 0, 1000, 0, 0, 0, 1000]
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'falling',
                'boxes': [
                    [-0.2, 0.0, -0.2, 0.2, 2.0, 0.2, None],
                    [-0.4, 0.0, -0.4, 0.4, 5.0, 0.4, None],
                ],
                'locators': [signature + [1]],
            },
        }))

        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(), _Vector(),
            types.SimpleNamespace(Vector3=_Vector))

        self.assertEqual(1, len(boxes))
        self.assertEqual((0.0, 2.5, 0.0), boxes[0][0])
        self.assertEqual((0.4, 0.0, 0.0), boxes[0][1][0])

    def test_installed_catalog_missing_falling_record_fails_closed(self):
        filename = 'content/Environment/pole/normal/lod0/missing.model'
        catalog = _catalog({
            'known.model': {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 1, 1, None]],
            },
        })
        catalog['version'] = 2
        destructibles_sensor.set_catalog(catalog)
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {
                'type': 2, 'health': 18,
                'kineticDamageCorrection': 1.0})
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = (
            lambda unused_space, unused_chunk: (filename,))
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        bigworld.wg_getDestructibleMatrix = (
            lambda *unused: _ItemMatrix(_Vector(0.0, 0.0, 3.0)))
        bigworld.wg_getDestructibleEffectCategory = mock.Mock(return_value=2)
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        authority = types.SimpleNamespace(
            destroy_column=lambda *unused: self.fail(
                'missing falling catalog record used origin fallback'))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(sys, 'stdout', mock.Mock()):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 20.0, descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 20.0, descriptor)

        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual(
            {'catalog_identity_missing'},
            destructibles_sensor.g_offh_destr_isolation_logs)
        bigworld.wg_getDestructibleEffectCategory.assert_called_once_with(
            1, 22, 0, -1)

    def test_ambiguous_fragile_locator_uses_apply_vector_at_large_origin(self):
        filename = 'content/GatesAndFences/ambiguous/normal/lod0/fence.model'
        matrix = _ItemMatrix(_Vector(50000.0004, 0.0, -50000.0004))
        chunk_translation = _Vector(10000.0, 0.0, -10000.0)
        signature = [60000000, 0, -60000000,
                     1000, 0, 0, 0, 1000, 0, 0, 0, 1000]
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [
                    [-1, -1, -1, 1, 1, 1, None],
                    [-2, -1, -2, 2, 1, 2, None],
                ],
                'locators': [signature + [1]],
            },
        }))

        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, matrix, chunk_translation,
            types.SimpleNamespace(Vector3=_Vector))

        self.assertEqual(1, len(boxes))
        self.assertEqual((60000.0004, 0.0, -60000.0004), boxes[0][0])
        self.assertEqual((2.0, 0.0, 0.0), boxes[0][1][0])

    def test_structure_catalog_registers_without_proximity_destruction(self):
        little = (
            'content/Buildings/bld003_LittleWoodShed/normal/lod0/'
            'bld003_LittleWoodShed.model')
        middle = (
            'content/Buildings/bld002_MiddleWoodShed/normal/lod0/'
            'bld002_MiddleWoodShed.model')
        destructibles_sensor.set_catalog(_catalog({
            little: {
                'kind': 'structure',
                'boxes': [[-2.433012, -0.003474, -2.0,
                           2.472872, 3.036366, 2.263299, 73]],
            },
            middle: {
                'kind': 'structure',
                'boxes': [
                    [-2.917333, -0.640957, -4.4654,
                     4.407133, 4.145072, 3.747997, 73],
                    [-6.440591, -0.610982, -2.974565,
                     1.528536, 4.150383, 4.832925, 74],
                ],
            },
        }))
        descriptors = {
            little: {'type': 4, 'modules': {73: {'health': 15}}},
            middle: {'type': 4, 'modules': {
                73: {'health': 15}, 74: {'health': 15}}},
        }
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6),
                    (1.6, 1.0, 3.6), None))))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 2)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: descriptors.get(value))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: (little, middle)
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 4
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=_Vector())
        matrices = (_ItemMatrix(_Vector(0.0, 0.0, 2.0)),
                    _ItemMatrix(_Vector(0.0, 0.0, 2.0)))
        bigworld.wg_getDestructibleMatrix = (
            lambda unused_space, unused_chunk, index: matrices[index])
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *args: calls.append(args) or True)
        destructibles_sensor.xrange = range
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, type_descriptor)

        self.assertEqual([], calls)
        self.assertIn((22, 0), destructibles_sensor.g_offh_destr_instances)
        self.assertIn((22, 1), destructibles_sensor.g_offh_destr_instances)
        self.assertAlmostEqual(
            1.0,
            destructibles_sensor.g_offh_destr_instances[(22, 0)][
                'item_scale'])
        self.assertTrue(destructibles_sensor.g_offh_destr_contact_bins)

    def test_bld002_material_proof_disambiguates_without_distance_or_normal(self):
        filename = (
            'content/Buildings/bld002_MiddleWoodShed/normal/lod0/'
            'bld002_MiddleWoodShed.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [
                    [-2.917333, -0.640957, -4.4654,
                     4.407133, 4.145072, 3.747997, 73],
                    [-6.440591, -0.610982, -2.974565,
                     1.528536, 4.150383, 4.832925, 74],
                ],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(), _Vector(),
            types.SimpleNamespace(Vector3=_Vector))
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'filename': filename.lower(),
                       'kind': 'structure', 'boxes': boxes},
        }
        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 4, 'modules': {73: {}, 74: {}}}
                if value == filename else None))
        contact = _Vector(-5.0, 1.0, 3.5)
        candidate = _Vector(-4.0, 1.0, 3.0)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertTrue(
                destructibles_sensor._solid_destructible_candidate_1513(
                    _mat_info_1513(
                        True, candidate, _Vector(1, 0, 0), 74,
                        filename, 22, 37),
                    contact, _Vector(0, 0, 1)))
            self.assertFalse(
                destructibles_sensor._solid_destructible_candidate_1513(
                    _mat_info_1513(
                        True, candidate, _Vector(1, 0, 0), 73,
                        filename, 22, 37),
                    contact, _Vector(0, 0, 1)))

    def test_hills_checkpoint_material_miss_uses_unique_catalog_contact(self):
        filename = (
            'content/MilitaryEnvironment/mle002_KPP/normal/lod0/'
            'mle002_KPP.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [
                    [-8.365126, -0.13108, -3.374999,
                     9.642537, 7.610279, 1.328597, 73],
                    [5.555273, -0.033259, -1.684319,
                     9.671488, 3.370548, 2.431895, 74],
                ],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(_Vector(149.839, 19.999, -252.346)),
            _Vector(), types.SimpleNamespace(Vector3=_Vector))
        destructibles_sensor.g_offh_destr_instances = {
            (32129, 7): {'filename': filename.lower(),
                         'kind': 'structure', 'boxes': boxes,
                         'item_scale': 1.0},
        }
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (32129, 7),
            destructibles_sensor.g_offh_destr_instances[(32129, 7)])
        contact = _Vector(149.0, 21.0, -254.0)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = (
            lambda *unused: _mat_info_1513(False))
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda value: (
                {'type': 4, 'modules': {
                    73: {'health': 1}, 74: {'health': 1}}}
                if value.lower() == filename.lower() else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: health * scale
        authority_calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *args: authority_calls.append(args) or True)
        descriptor = _Strict1513Component(physics={'weight': 25000.0})
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(149.0, 21.0, -259.0), contact,
                _Vector(0, 0, -1), 0.0, 20.0, descriptor))

        self.assertEqual((1, 32129, 7, 73), authority_calls[0][:4])

    def test_highway_pole_static_hit_uses_unique_falling_catalog_contact(self):
        # 45_north_america BSMO model 1 and its first BSMI instance.  #1513's
        # The direct material identity is accepted only because the installed
        # catalog registry proves the exact falling item and contact OBB.
        filename = (
            'content/Environment/envAM_009_Poles/normal/lod0/'
            'envAM_009_Poles_01.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'falling',
                'boxes': [[-0.204969, 0.0, -1.125720,
                           0.204852, 9.046554, 1.125167, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        matrix = _ItemMatrix(
            _Vector(-504.473633, 2.969328, 448.742371),
            -2.2726274054670954)
        boxes = destructibles_sensor._world_catalog_boxes(
            record, matrix, _Vector(),
            types.SimpleNamespace(Vector3=_Vector))
        instance = {
            'filename': filename.lower(), 'kind': 'falling',
            'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(32129, 1): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (32129, 1), instance)

        contact = _Vector(-504.473633, 3.9, 448.742371)
        normal = _Vector(0.0, 0.0, -1.0)
        direct = _mat_info_1513(
            True, contact, normal, 73, filename, 32129, 1)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = lambda *unused: direct
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=35000.0,
            getDescByFilename=lambda value: (
                {'type': 2, 'health': 18,
                 'kineticDamageCorrection': 0.0}
                if value.lower() == filename.lower() else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: scale * health
        descriptor = _Strict1513Component(physics={'weight': 21000.0})
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_column=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(
                destructibles_sensor._solid_destructible_candidate_1513(
                    direct, contact, normal))
            self.assertTrue(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(-504.473633, 3.9, 442.742371), contact,
                normal, 0.0, 11.5, descriptor))

        self.assertEqual(1, len(calls))
        self.assertEqual((1, 32129, 1), calls[0][:3])
        self.assertEqual(11.5, calls[0][4])
        self.assertIs(contact, calls[0][5])

    def test_hills_checkpoint_overlap_is_ambiguous_without_native_material(self):
        filename = (
            'content/MilitaryEnvironment/mle002_KPP/normal/lod0/'
            'mle002_KPP.model')
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [
                    [-8.365126, -0.13108, -3.374999,
                     9.642537, 7.610279, 1.328597, 73],
                    [5.555273, -0.033259, -1.684319,
                     9.671488, 3.370548, 2.431895, 74],
                ],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(), _Vector(),
            types.SimpleNamespace(Vector3=_Vector))
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'filename': filename.lower(),
                       'kind': 'structure', 'boxes': boxes,
                       'item_scale': 1.0},
        }
        destructibles_sensor.g_offh_destr_contact_bins = {
            destructibles_sensor._destructible_bin_key(6.0, 0.0): {
                (22, 37)},
        }

        self.assertIsNone(
            destructibles_sensor._catalog_candidate_at_contact(
                _Vector(6.0, 1.0, 0.0)))

    def test_catalog_contact_respects_stock_kinetic_health_gate(self):
        filename = 'slow-fence.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 1, 1, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(), _Vector(),
            types.SimpleNamespace(Vector3=_Vector))
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'filename': filename,
                       'kind': 'fragile', 'boxes': boxes,
                       'item_scale': 1.0},
        }
        destructibles_sensor.g_offh_destr_contact_bins = {
            destructibles_sensor._destructible_bin_key(0.0, 0.0): {
                (22, 37)},
        }
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = (
            lambda *unused: _mat_info_1513(False))
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': 3, 'health': 100,
                'kineticDamageCorrection': 1.0})
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: health * scale
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: self.fail(
                'slow contact passed stock kinetic gate'))
        descriptor = _Strict1513Component(physics={'weight': 10000.0})

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertFalse(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(0, 0, -5), _Vector(), _Vector(0, 0, -1),
                0.0, 1.0, descriptor))

    def test_stock_kinetic_gate_uses_streamed_y_basis_item_scale(self):
        filename = 'scaled-fence.model'
        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': 3, 'health': 100,
                'kineticDamageCorrection': 1.0})
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: scale * scale * health)
        descriptor = _Strict1513Component(physics={'weight': 10000.0})
        mat_info = _mat_info_1513(
            True, _Vector(), _Vector(0, 0, -1), 73,
            filename, 22, 37)
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'filename': filename, 'kind': 'fragile',
                       'boxes': (), 'item_scale': 2.0},
        }

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache}):
            self.assertTrue(destructibles_sensor._stock_crushable_1513(
                mat_info, 20.0, descriptor, 1.0))
            self.assertFalse(destructibles_sensor._stock_crushable_1513(
                mat_info, 20.0, descriptor))

        self.assertAlmostEqual(
            2.0, destructibles_sensor._matrix_item_scale_1513(
                _ItemMatrix(scale=2.0),
                types.SimpleNamespace(Vector3=_Vector)))

    def test_direct_material_contact_uses_stock_low_and_high_speed_gate(self):
        filename = 'direct-fence.model'
        hit = _Vector(0, 0, 1)
        normal = _Vector(0, 0, -1)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getMatInfoNearPoint = lambda *unused: _mat_info_1513(
            True, hit, normal, 73, filename, 22, 37)
        bigworld.time = lambda: 10.0
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': 3, 'health': 100,
                'kineticDamageCorrection': 1.0})
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: scale * scale * health)
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: calls.append(args) or True)
        descriptor = _Strict1513Component(physics={'weight': 10000.0})
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'filename': filename, 'kind': 'fragile',
                       'boxes': (), 'item_scale': 1.0},
        }
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertFalse(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(0, 0, -5), hit, normal,
                0.0, 1.0, descriptor))
            self.assertEqual([], calls)
            self.assertTrue(destructibles_sensor._try_destroy_solid_hit(
                1, _Vector(0, 0, -5), hit, normal,
                0.0, 20.0, descriptor))

        self.assertEqual(1, len(calls))
        self.assertAlmostEqual(
            10.2,
            destructibles_sensor.g_offh_destr_pending[(22, 37, None)])

    def _direction_catalog_fixture(self, kind='fragile', destroyed=False):
        filename = 'content/environment/test/normal/lod0/soft-item.model'
        destructibles_sensor.xrange = range
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': kind,
                'boxes': [[-0.5, -0.2, -0.5, 0.5, 1.8, 0.5, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(_Vector(0.0, 0.0, 4.0)), _Vector(),
            math_module)
        instance = {
            'filename': filename, 'descriptor_filename': filename,
            'kind': kind, 'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 37): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 37), instance)

        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = mock.Mock(return_value=None)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': 2 if kind == 'falling' else 3,
                'health': 5, 'kineticDamageCorrection': 1.0,
            })
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: scale * health)
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=destroyed),
            destroy_fragile=mock.Mock(return_value=True),
            destroy_column=mock.Mock(return_value=True))
        descriptor = _Strict1513Component(
            physics={'weight': 10000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        return (bigworld, math_module, area, cache, authority, descriptor)

    def test_direction_soft_path_is_read_only_and_recasts_backing_wall(self):
        (bigworld, math_module, area, cache, authority,
         descriptor) = self._direction_catalog_fixture()
        start = _Vector(0.0, 0.7, 0.0)
        end = _Vector(0.0, 0.7, 10.0)
        normal = _Vector(0.0, 0.0, -1.0)
        soft_hit = (_Vector(0.0, 0.7, 3.5), normal)
        events = mock.Mock(return_value=True)
        destructibles_sensor.set_event_sink(events)
        instances_before = dict(destructibles_sensor.g_offh_destr_instances)
        bins_before = dict(
            (key, set(values)) for key, values in
            destructibles_sensor.g_offh_destr_contact_bins.items())

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    destructibles_sensor, 'note_destroyed') as note, \
                mock.patch.object(
                    destructibles_sensor, '_publish_destroyed') as publish:
            self.assertTrue(
                destructibles_sensor._catalog_soft_static_path(
                    1, start, end, soft_hit, 20.0, descriptor))
            bigworld.wg_collideSegment.assert_called_once()
            recast_start = bigworld.wg_collideSegment.call_args[0][1]
            self.assertGreater(recast_start.z, 4.5)

            bigworld.wg_collideSegment.reset_mock()
            bigworld.wg_collideSegment.return_value = (
                _Vector(0.0, 0.7, 8.0), normal)
            self.assertFalse(
                destructibles_sensor._catalog_soft_static_path(
                    1, start, end, soft_hit, 20.0, descriptor))
            bigworld.wg_collideSegment.assert_called_once()

        authority.destroy_fragile.assert_not_called()
        authority.destroy_column.assert_not_called()
        note.assert_not_called()
        publish.assert_not_called()
        events.assert_not_called()
        self.assertEqual({}, getattr(
            destructibles_sensor, 'g_offh_destr_pending', {}))
        self.assertEqual(
            instances_before, destructibles_sensor.g_offh_destr_instances)
        self.assertEqual(
            bins_before, destructibles_sensor.g_offh_destr_contact_bins)

    def test_pending_shared_fence_face_recasts_into_active_neighbour(self):
        (bigworld, math_module, area, cache, authority,
         descriptor) = self._direction_catalog_fixture()
        filename = next(iter(
            destructibles_sensor._destructible_catalog['resources']))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(_Vector(0.0, 0.0, 5.0)), _Vector(),
            math_module)
        neighbour = {
            'filename': filename, 'descriptor_filename': filename,
            'kind': 'fragile', 'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances[(22, 38)] = neighbour
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 38), neighbour)
        authority.is_destroyed.side_effect = (
            lambda chunk_id, item_index, mat_kind:
            chunk_id == 22 and item_index == 37 and mat_kind is None)
        start = _Vector(0.0, 0.7, 0.0)
        end = _Vector(0.0, 0.7, 10.0)
        normal = _Vector(0.0, 0.0, -1.0)
        shared_hit = (_Vector(0.0, 0.7, 4.5), normal)
        active_neighbour_hit = (_Vector(0.0, 0.7, 4.5002), normal)
        bigworld.wg_collideSegment.return_value = active_neighbour_hit

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            result = destructibles_sensor._catalog_soft_static_path(
                1, start, end, shared_hit, 0.0, descriptor,
                require_pending_first=True)

        self.assertEqual('pending_hard', result)
        bigworld.wg_collideSegment.assert_called_once()
        recast_start = bigworld.wg_collideSegment.call_args[0][1]
        self.assertAlmostEqual(
            4.5 + destructibles_sensor._SHOT_RAY_EPSILON,
            recast_start.z)
        self.assertIs(end, bigworld.wg_collideSegment.call_args[0][2])
        authority.destroy_fragile.assert_not_called()

    def test_pending_face_never_skips_an_overlapping_active_piece(self):
        (unused_bigworld, math_module, unused_area, unused_cache, authority,
         unused_descriptor) = self._direction_catalog_fixture()
        filename = next(iter(
            destructibles_sensor._destructible_catalog['resources']))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(_Vector(0.0, 0.0, 4.45)), _Vector(),
            math_module)
        neighbour = {
            'filename': filename, 'descriptor_filename': filename,
            'kind': 'fragile', 'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances[(22, 38)] = neighbour
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 38), neighbour)
        authority.is_destroyed.side_effect = (
            lambda chunk_id, item_index, mat_kind:
            chunk_id == 22 and item_index == 37 and mat_kind is None)

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            candidate = (
                destructibles_sensor._catalog_candidate_on_ray_1513(
                    _Vector(0.0, 0.7, 4.0),
                    _Vector(0.0, 0.7, 0.0),
                    _Vector(0.0, 0.7, 10.0),
                    prefer_destroyed=True))

        self.assertIsNone(candidate)

    def test_direction_soft_path_defers_when_shared_recast_budget_is_empty(self):
        (bigworld, math_module, area, cache, authority,
         descriptor) = self._direction_catalog_fixture()
        start = _Vector(0.0, 0.7, 0.0)
        end = _Vector(0.0, 0.7, 10.0)
        collision = (
            _Vector(0.0, 0.7, 3.5), _Vector(0.0, 0.0, -1.0))
        budget = [0]

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertEqual(
                'deferred',
                destructibles_sensor._catalog_soft_static_path(
                    1, start, end, collision, 20.0, descriptor,
                    recast_budget=budget))

        self.assertEqual([0], budget)
        bigworld.wg_collideSegment.assert_not_called()
        authority.destroy_fragile.assert_not_called()
        authority.destroy_column.assert_not_called()

    def test_motion_sweep_reach_matches_grounded_margin_in_both_directions(self):
        bbox = ((-1.0, -1.0, -3.0), (1.0, 1.0, 4.0), None)

        self.assertAlmostEqual(
            0.4, destructibles_sensor._motion_travel_reach(1.0, 0.04))
        self.assertAlmostEqual(
            1.2, destructibles_sensor._motion_travel_reach(10.0, 0.1))
        self.assertAlmostEqual(
            1.2, destructibles_sensor._motion_travel_reach(-10.0, 0.1))

        forward = destructibles_sensor._vehicle_swept_box(
            _Vector(), 0.0, 10.0, bbox, 1.2)
        reverse = destructibles_sensor._vehicle_swept_box(
            _Vector(), 0.0, -10.0, bbox, 1.2)
        forward_min = forward[0][2] - abs(forward[1][2][2])
        forward_max = forward[0][2] + abs(forward[1][2][2])
        reverse_min = reverse[0][2] - abs(reverse[1][2][2])
        reverse_max = reverse[0][2] + abs(reverse[1][2][2])
        self.assertAlmostEqual(-3.0, forward_min)
        self.assertAlmostEqual(5.2, forward_max)
        self.assertAlmostEqual(-4.2, reverse_min)
        self.assertAlmostEqual(4.0, reverse_max)

    def test_direction_soft_path_drives_through_a_felled_column(self):
        (bigworld, math_module, area, cache, authority,
         descriptor) = self._direction_catalog_fixture(
             kind='falling', destroyed=True)
        start = _Vector(0.0, 0.7, 0.0)
        end = _Vector(0.0, 0.7, 10.0)
        collision = (
            _Vector(0.0, 0.7, 3.5), _Vector(0.0, 0.0, -1.0))

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    destructibles_sensor, 'note_destroyed') as note, \
                mock.patch.object(
                    destructibles_sensor, '_publish_destroyed') as publish:
            # Retail lets a vehicle drive over a felled column: once it is
            # destroyed its body stops braking and stops blocking.
            self.assertTrue(
                destructibles_sensor._catalog_soft_static_path(
                    1, start, end, collision, 20.0, descriptor))

        authority.is_destroyed.assert_called_once_with(22, 37, None)
        authority.destroy_column.assert_not_called()
        note.assert_not_called()
        publish.assert_not_called()

    def _stationary_contact_status(self, specs, current_speed=1.0,
                                   kinetic_speed=4.0, dt=0.1,
                                   return_detail=False,
                                   kinetic_commit=False):
        """Resolve one stationary hull contact with an independent speed cap."""
        destructibles_sensor.xrange = range
        resources = {}
        for index, spec in enumerate(specs):
            filename = (
                'content/stationary-contact/%d/normal/lod0/item.model' %
                index)
            spec['filename'] = filename
            resources[filename] = {
                'kind': spec.get('kind', 'fragile'),
                'boxes': spec.get('boxes', [
                    [-0.25, -0.2, -0.5, 0.25, 1.5, 0.5, None],
                ]),
            }
        destructibles_sensor.set_catalog(_catalog(resources))

        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instances = {}
        bins = {}
        descriptions = {}
        for index, spec in enumerate(specs):
            filename = spec['filename']
            record = destructibles_sensor._destructible_catalog[
                'resources'][filename]
            boxes = destructibles_sensor._world_catalog_boxes(
                record,
                _ItemMatrix(_Vector(
                    spec.get('x', 0.0), 0.0, spec.get('z', 4.05))),
                _Vector(), math_module)
            kind = spec.get('kind', 'fragile')
            item_index = 37 + index
            instance = {
                'filename': filename, 'descriptor_filename': filename,
                'kind': kind, 'boxes': boxes, 'item_scale': 1.0,
            }
            instances[(22, item_index)] = instance
            destructibles_sensor._index_catalog_instance_1513(
                bins, (22, item_index), instance)
            if kind == 'structure':
                descriptions[filename] = {
                    'type': 4,
                    'modules': dict(
                        (int(box[6]), {'health': spec.get('health', 5)})
                        for box in spec['boxes']),
                }
            else:
                descriptions[filename] = {
                    'type': 2 if kind == 'falling' else 3,
                    'health': spec.get('health', 5),
                    'kineticDamageCorrection': 1.0,
                }
        destructibles_sensor.g_offh_destr_instances = instances
        destructibles_sensor.g_offh_destr_contact_bins = bins

        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.DESTRUCTIBLE_HIDING_DELAY = 0.2
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda filename: descriptions.get(filename))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: scale * health)
        descriptor = _Strict1513Component(
            physics={'weight': 10000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_fragile=mock.Mock(return_value=True),
            destroy_module=mock.Mock(return_value=True),
            destroy_column=mock.Mock(return_value=True))
        authority.event_sink = mock.Mock(return_value=True)
        destructibles_sensor.set_event_sink(authority.event_sink)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            status = destructibles_sensor._catalog_motion_blocked(
                1, _Vector(), 0.0, current_speed, descriptor, 10.0,
                return_status=True, dt=dt,
                kinetic_speed=kinetic_speed,
                return_detail=return_detail,
                kinetic_commit=kinetic_commit)
        return status, authority, descriptor

    def test_stationary_exact_contact_classifies_kinetic_token_read_only(self):
        detail, authority, unused_descriptor = (
            self._stationary_contact_status([{}], return_detail=True))

        self.assertEqual({
            'status': 'kinetic',
            'token': ((22, 37, None),),
            'accepted_now': False,
            'used_kinetic_speed': False,
            'kinds': 'fragile',
        }, detail)
        authority.destroy_fragile.assert_not_called()
        authority.destroy_module.assert_not_called()
        authority.destroy_column.assert_not_called()
        authority.event_sink.assert_not_called()

    def test_stationary_exact_kinetic_commit_uses_cap_but_publishes_real_speed(self):
        detail, authority, unused_descriptor = (
            self._stationary_contact_status(
                [{}], current_speed=1.0, kinetic_speed=4.0,
                return_detail=True, kinetic_commit=True))

        self.assertEqual({
            'status': 'crushed',
            'token': ((22, 37, None),),
            'accepted_now': True,
            'used_kinetic_speed': True,
            'kinds': 'fragile',
        }, detail)
        authority.destroy_fragile.assert_called_once()
        authority.destroy_module.assert_not_called()
        authority.destroy_column.assert_not_called()
        authority.event_sink.assert_called_once()
        event = authority.event_sink.call_args[0][0]
        self.assertEqual('fragile', event['destructible_kind'])
        self.assertEqual((22, 37), (
            event['chunk_id'], event['item_index']))
        self.assertEqual(1.0, event['speed'])

    def test_stationary_contact_is_hard_when_speed_cap_cannot_pass_gate(self):
        status, authority, unused_descriptor = (
            self._stationary_contact_status([{'health': 100}]))

        self.assertEqual('hard', status)
        authority.destroy_fragile.assert_not_called()
        authority.destroy_module.assert_not_called()
        authority.destroy_column.assert_not_called()

    def test_stationary_adjacent_fragiles_are_kinetic_but_hard_backing_wins(self):
        adjacent = [
            {'x': -0.3, 'boxes': [
                [-0.4, -0.2, -0.5, 0.4, 1.5, 0.5, None]]},
            {'x': 0.3, 'boxes': [
                [-0.4, -0.2, -0.5, 0.4, 1.5, 0.5, None]]},
        ]
        status, authority, unused_descriptor = (
            self._stationary_contact_status(adjacent))
        self.assertEqual('kinetic', status)
        authority.destroy_fragile.assert_not_called()

        backing = {
            'kind': 'structure', 'health': 100, 'z': 4.55,
            'boxes': [[-1.8, -0.2, -0.9, 1.8, 2.0, 0.9, 73]],
        }
        status, authority, unused_descriptor = (
            self._stationary_contact_status(adjacent + [backing]))
        self.assertEqual('hard', status)
        authority.destroy_fragile.assert_not_called()
        authority.destroy_module.assert_not_called()

    def test_stationary_twenty_centimetre_gap_is_approach_only(self):
        detail, authority, descriptor = self._stationary_contact_status(
            [{'z': 4.3}], return_detail=True, kinetic_commit=True)

        self.assertTrue(destructibles_sensor._catalog_hull_contact(
            _Vector(), 0.0, 1.0, descriptor, 0.1))
        self.assertEqual('approach', detail['status'])
        self.assertIsNone(detail['token'])
        self.assertFalse(detail['accepted_now'])
        self.assertFalse(detail['used_kinetic_speed'])
        authority.destroy_fragile.assert_not_called()
        authority.event_sink.assert_not_called()

    def test_far_swept_candidate_cannot_be_committed_at_speed_cap(self):
        detail, authority, unused_descriptor = (
            self._stationary_contact_status(
                [{'z': 5.5}], current_speed=1.0, kinetic_speed=20.0,
                dt=0.1, return_detail=True, kinetic_commit=True))

        self.assertEqual('clear', detail['status'])
        self.assertIsNone(detail['token'])
        self.assertFalse(detail['accepted_now'])
        authority.destroy_fragile.assert_not_called()
        authority.event_sink.assert_not_called()

    def test_exact_hard_mix_prevents_every_kinetic_commit(self):
        detail, authority, unused_descriptor = (
            self._stationary_contact_status(
                [{}, {'health': 100, 'x': 0.5}],
                return_detail=True, kinetic_commit=True))

        self.assertEqual('hard', detail['status'])
        self.assertIsNone(detail['token'])
        self.assertFalse(detail['accepted_now'])
        self.assertFalse(detail['used_kinetic_speed'])
        authority.destroy_fragile.assert_not_called()
        authority.destroy_module.assert_not_called()
        authority.destroy_column.assert_not_called()
        authority.event_sink.assert_not_called()

    def test_stationary_kinetic_gate_fails_closed_for_non_fragile_contacts(self):
        cases = (
            ('falling', [{'kind': 'falling'}]),
            ('mixed-hard', [{}, {'health': 100, 'x': 0.5}]),
        )
        for name, specs in cases:
            with self.subTest(name=name):
                status, authority, unused_descriptor = (
                    self._stationary_contact_status(specs))
                self.assertEqual('hard', status)
                authority.destroy_fragile.assert_not_called()
                authority.destroy_module.assert_not_called()
                authority.destroy_column.assert_not_called()

    def _ground_filter_fixture(self, destroyed_keys):
        """Index one fence tile and answer with the given accepted keys."""
        destructibles_sensor.xrange = range
        filename = 'content/GatesAndFences/gaf010/normal/lod0/tile.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-1.0, -0.04, -0.5, 1.0, 1.9, 0.5, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(_Vector(0.0, 0.0, 4.0)), _Vector(),
            types.SimpleNamespace(Vector3=_Vector))
        instance = {
            'filename': filename.lower(), 'kind': 'fragile',
            'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 37): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 37), instance)
        authority = types.SimpleNamespace(
            destroyed_keys=lambda chunk_id: (
                destroyed_keys if chunk_id == 22 else ()))
        return authority

    def test_ground_filter_is_absent_until_the_item_is_broken(self):
        authority = self._ground_filter_fixture(set())

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            self.assertIsNone(
                destructibles_sensor.ground_collision_filter(0.0, 4.0))
            self.assertIsNone(
                destructibles_sensor.ground_collision_filter(80.0, 80.0))

    def test_ground_filter_hides_only_the_broken_identity(self):
        authority = self._ground_filter_fixture(set([(37, None)]))

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            ground_filter = destructibles_sensor.ground_collision_filter(
                0.0, 4.0)
            self.assertIsNone(
                destructibles_sensor.ground_collision_filter(80.0, 80.0))

        destructibles_sensor.take_ground_skip_count()
        # The engine passes (matKind, collFlags, itemIndex, chunkID) and keeps
        # the surface when the filter answers True.
        self.assertFalse(ground_filter(75, 0, 37, 22))
        self.assertTrue(ground_filter(75, 0, 38, 22))
        self.assertTrue(ground_filter(75, 0, 37, 23))
        self.assertTrue(ground_filter(2, 0, -1, -1))
        self.assertTrue(ground_filter(2, 0, None, None))
        self.assertEqual(1, destructibles_sensor.take_ground_skip_count())
        self.assertEqual(0, destructibles_sensor.take_ground_skip_count())

    def test_ground_filter_hides_one_broken_structure_module(self):
        authority = self._ground_filter_fixture(set([(37, 74)]))

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            ground_filter = destructibles_sensor.ground_collision_filter(
                0.0, 4.0)

        self.assertTrue(ground_filter(73, 0, 37, 22))
        self.assertFalse(ground_filter(74, 0, 37, 22))

    def test_horizontal_filter_covers_every_bin_crossed_by_the_hull_ray(self):
        authority = self._ground_filter_fixture(set([(37, None)]))

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            collision_filter = (
                destructibles_sensor.horizontal_collision_filter(
                    _Vector(0.0, 0.6, -1.0),
                    _Vector(0.0, 0.6, 10.0)))

        self.assertIsNotNone(collision_filter)
        self.assertFalse(collision_filter(75, 0, 37, 22))
        self.assertTrue(collision_filter(75, 0, 38, 22))
        self.assertTrue(collision_filter(75, 0, 37, 23))

    def test_stationary_multi_module_structure_crushes_each_module(self):
        detail, authority, unused_descriptor = (
            self._stationary_contact_status([{
                'kind': 'structure',
                'boxes': [
                    [-1.0, -0.2, -0.5, 1.0, 2.0, 0.5, 73],
                    [-1.0, -0.2, -0.4, 1.0, 2.0, 0.6, 74],
                ],
            }], return_detail=True, kinetic_commit=True))

        self.assertEqual('crushed', detail['status'])
        self.assertEqual('structure', detail['kinds'])
        self.assertEqual(2, authority.destroy_module.call_count)

    def test_catalog_motion_contact_clears_once_the_item_is_broken(self):
        destructibles_sensor.xrange = range
        filename = 'content/GatesAndFences/test/normal/lod0/fence.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-0.2, -0.2, -0.5, 0.2, 1.5, 0.5, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(_Vector(0.0, 0.0, 4.0)), _Vector(),
            types.SimpleNamespace(Vector3=_Vector))
        instance = {
            'filename': filename.lower(), 'kind': 'fragile',
            'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 37): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 37), instance)

        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.DESTRUCTIBLE_HIDING_DELAY = 0.2
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': 3, 'health': 5,
                'kineticDamageCorrection': 1.0})
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: scale * health
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        descriptor = _Strict1513Component(
            physics={'weight': 10000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        calls = []
        destroyed = set()

        def destroy_fragile(*args):
            calls.append(args)
            destroyed.add((args[1], args[2], None))
            return True

        authority = types.SimpleNamespace(
            is_destroyed=lambda chunk, item, mat=None: (
                (chunk, item, mat) in destroyed),
            destroy_fragile=destroy_fragile)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertEqual('hard',
                destructibles_sensor._catalog_motion_blocked(
                    1, _Vector(), 0.0, 1.0, descriptor, 10.0,
                    return_status=True))
            self.assertEqual([], calls)
            self.assertEqual('crushed',
                destructibles_sensor._catalog_motion_blocked(
                    1, _Vector(), 0.0, 10.0, descriptor, 11.0,
                    return_status=True))
            # #1513 reports the item broken from here on, so it never resists
            # again - not while its skin hides, and not afterwards.
            for moment in (11.0, 11.199, 11.2, 30.0):
                self.assertEqual('crushed',
                    destructibles_sensor._catalog_motion_blocked(
                        1, _Vector(), 0.0, 1.0, descriptor, moment,
                        return_status=True))

        self.assertEqual(1, len(calls))

    def test_catalog_motion_contact_blocks_on_one_hard_structure_module(self):
        destructibles_sensor.xrange = range
        filename = 'content/Buildings/test/normal/lod0/shed.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'structure',
                'boxes': [
                    [-2.0, -0.2, 2.0, 2.0, 3.0, 5.0, 73],
                    [-2.0, -0.2, 3.0, 2.0, 3.0, 6.0, 74],
                ],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(), _Vector(),
            types.SimpleNamespace(Vector3=_Vector))
        instance = {
            'filename': filename.lower(), 'kind': 'structure',
            'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 37): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 37), instance)
        descriptor = _Strict1513Component(
            physics={'weight': 40000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': 4, 'modules': {
                    73: {'health': 5}, 74: {'health': 100000}}})
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: scale * health
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *unused: self.fail(
                'ambiguous module contact was destroyed'))

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(destructibles_sensor._catalog_motion_blocked(
                1, _Vector(), 0.0, 20.0, descriptor, 10.0))

    def test_karelia_player_proposal_streams_exact_multi_module_structure(self):
        catalog_path = ROOT / '0.9.22' / 'destructibles' / '01_karelia.json'
        catalog = json.loads(catalog_path.read_text())
        row = next(
            value for value in catalog['instances']
            if value[14:16] == [33411, 13])
        filename = row[12]

        class CatalogMatrix(object):
            def __init__(self, source_row):
                self.row = source_row
                self.translation = _Vector(*(
                    value / 1000.0 for value in source_row[:3]))

            def applyVector(self, point):
                basis = self.row[3:12]
                return _Vector(
                    (basis[0] * point.x + basis[6] * point.z) / 1000.0,
                    (basis[1] * point.x + basis[4] * point.y +
                     basis[7] * point.z) / 1000.0,
                    (basis[2] * point.x + basis[8] * point.z) / 1000.0)

            def applyPoint(self, point):
                return self.translation + self.applyVector(point)

        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(33411, 14)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.DESTRUCTIBLE_MATKIND = types.SimpleNamespace(
            NORMAL_MIN=73, NORMAL_MAX=86)
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda value: ({
                'type': 4,
                'modules': {
                    73: {'health': 15},
                    74: {'health': 15},
                    75: {'health': 15},
                },
            } if value == filename else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = (
            lambda unused_space, unused_chunk: ())
        bigworld.wg_getChunkMatrix = (
            lambda unused_space, unused_chunk:
            types.SimpleNamespace(translation=_Vector()))
        rows_by_wire = {
            tuple(value[14:16]): value for value in catalog['instances']}
        bigworld.wg_getDestructibleMatrix = mock.Mock(
            side_effect=lambda unused_space, chunk, item: CatalogMatrix(
                rows_by_wire[(chunk, item)]))
        bigworld.wg_getDestructibleEffectCategory = mock.Mock(return_value=4)
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: scale * health
        descriptor = _Strict1513Component(
            physics={'weight': 21000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.403, -1.0, -2.771),
                    (1.403, 1.5, 2.771), None))))
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *unused: self.fail(
                'a player proposal must remain read-only'))
        destructibles_sensor.xrange = range
        destructibles_sensor.set_catalog(catalog)

        self.assertEqual(
            {}, getattr(destructibles_sensor, 'g_offh_destr_instances', {}))
        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            detail = destructibles_sensor._catalog_motion_proposal(
                1, _Vector(385.5, 58.0, 408.5), -math.pi, 1.0,
                descriptor, 10.0, dt=0.02, kinetic_speed=16.667)

        self.assertEqual('crushed', detail['status'])
        self.assertTrue(detail['requires_commit'])
        self.assertEqual(
            ((33411, 13, 73), (33411, 13, 75)), detail['token'])
        self.assertIn(
            (33411, 13), destructibles_sensor.g_offh_destr_instances)
        bigworld.wg_getDestructibleMatrix.assert_called_once_with(
            1, 33411, 13)
        self.assertEqual(
            ([mock.call(1, 33411, item, -1) for item in range(14)] +
             [mock.call(1, 33411, 13, material)
              for material in (0, 1, 2)]),
            bigworld.wg_getDestructibleEffectCategory.call_args_list)

    def test_malinovka_log_fence_streams_with_native_module_indices(self):
        catalog_path = ROOT / '0.9.22' / 'destructibles' / '02_malinovka.json'
        catalog = json.loads(catalog_path.read_text())
        row = next(
            value for value in catalog['instances']
            if value[14:16] == [32636, 25])
        filename = row[12]
        resource = catalog['resources'][filename]
        self.assertTrue(filename.endswith(
            'mil203_MilitaryDefences01.model'))
        self.assertEqual(
            [73, 74], [value[6] for value in resource['boxes']])

        class CatalogMatrix(object):
            def __init__(self, source_row):
                self.row = source_row
                self.translation = _Vector(*(
                    value / 1000.0 for value in source_row[:3]))

            def applyVector(self, point):
                basis = self.row[3:12]
                return _Vector(
                    (basis[0] * point.x + basis[6] * point.z) / 1000.0,
                    (basis[1] * point.x + basis[4] * point.y +
                     basis[7] * point.z) / 1000.0,
                    (basis[2] * point.x + basis[8] * point.z) / 1000.0)

            def applyPoint(self, point):
                return self.translation + self.applyVector(point)

        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(32636, 26)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        # Exact #1513 DestructiblesCache enum values.  The live failure returned
        # native category 0, which means tree, for raw BSP material kind 73.
        area.DESTR_TYPE_TREE = 0
        area.DESTR_TYPE_FALLING_ATOM = 1
        area.DESTR_TYPE_FRAGILE = 2
        area.DESTR_TYPE_STRUCTURE = 3
        area.DESTRUCTIBLE_MATKIND = types.SimpleNamespace(
            NORMAL_MIN=73, NORMAL_MAX=86)
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda value: ({
                'type': 3,
                'modules': {
                    73: {'health': 15}, 74: {'health': 15},
                },
            } if value == filename else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = (
            lambda unused_space, unused_chunk: ())
        bigworld.wg_getChunkMatrix = (
            lambda unused_space, unused_chunk:
            types.SimpleNamespace(translation=_Vector()))
        rows_by_wire = {
            tuple(value[14:16]): value for value in catalog['instances']}
        bigworld.wg_getDestructibleMatrix = mock.Mock(
            side_effect=lambda unused_space, chunk, item: CatalogMatrix(
                rows_by_wire[(chunk, item)]))
        native_queries = []

        def native_category(unused_space, unused_chunk, item, token):
            native_queries.append((item, token))
            # The real #1513 API accepts the zero-based module index.  Passing
            # raw BSP material kind 73 was observed live as category 0 (tree).
            return 3 if token in (0, 1) else 0

        bigworld.wg_getDestructibleEffectCategory = native_category
        bigworld._offh_item_name_budget_tick = 1
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: scale * health
        descriptor = _Strict1513Component(
            physics={'weight': 21000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.403, -1.0, -2.771),
                    (1.403, 1.5, 2.771), None))))
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_module=lambda *unused: self.fail(
                'a player proposal must remain read-only'))
        destructibles_sensor.xrange = range
        destructibles_sensor.set_catalog(catalog)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            pending_detail = destructibles_sensor._catalog_motion_proposal(
                1, _Vector(29.5, 8.0, -267.5), 0.0, 1.0,
                descriptor, 10.0, dt=0.02, kinetic_speed=16.667)
            bigworld._offh_item_name_budget_tick = 2
            detail = destructibles_sensor._catalog_motion_proposal(
                1, _Vector(29.5, 8.0, -267.5), 0.0, 1.0,
                descriptor, 10.0, dt=0.02, kinetic_speed=16.667)

        self.assertEqual('clear', pending_detail['status'])
        self.assertEqual('crushed', detail['status'])
        self.assertTrue(detail['requires_commit'])
        self.assertEqual(((32636, 25, 74),), detail['token'])
        self.assertEqual(
            [0, 1],
            [token for item, token in native_queries
             if item == 25 and token >= 0])
        self.assertFalse(any(
            token in (73, 74) for unused_item, token in native_queries))

    def test_streamed_structure_requires_every_live_descriptor_module(self):
        area = types.SimpleNamespace(
            DESTRUCTIBLE_MATKIND=types.SimpleNamespace(
                NORMAL_MIN=73, NORMAL_MAX=86),
            DESTR_TYPE_TREE=0,
            DESTR_TYPE_FALLING_ATOM=1,
            DESTR_TYPE_FRAGILE=2,
            DESTR_TYPE_STRUCTURE=3)
        record = {
            'kind': 'structure',
            'boxes': [
                [-1, 0, -1, 0, 1, 1, 73],
                [0, 0, -1, 1, 1, 1, 74],
            ],
        }
        bigworld = types.SimpleNamespace(
            wg_getDestructibleEffectCategory=mock.Mock())

        with mock.patch.object(
                sys, 'stdout', types.SimpleNamespace(write=lambda unused: None)):
            self.assertFalse(
                destructibles_sensor._validate_native_effect_categories_1513(
                    bigworld, area, record,
                    {'type': 3, 'modules': {73: {'health': 15}}},
                    1, 32636, 25))

        bigworld.wg_getDestructibleEffectCategory.assert_not_called()
        self.assertTrue(destructibles_sensor.is_isolated_1513(32636, 25))

    def test_catalog_motion_destroys_distinct_adjacent_fragile_items(self):
        destructibles_sensor.xrange = range
        filename = 'content/GatesAndFences/joint/normal/lod0/fence.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-0.25, -0.2, -0.5, 0.25, 1.5, 0.5, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        instances = {}
        bins = {}
        for item_index, x in ((37, -0.2), (38, 0.2)):
            boxes = destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(_Vector(x, 0.0, 4.0)), _Vector(),
                types.SimpleNamespace(Vector3=_Vector))
            instance = {
                'filename': filename.lower(), 'kind': 'fragile',
                'boxes': boxes, 'item_scale': 1.0,
            }
            instances[(22, item_index)] = instance
            destructibles_sensor._index_catalog_instance_1513(
                bins, (22, item_index), instance)
        destructibles_sensor.g_offh_destr_instances = instances
        destructibles_sensor.g_offh_destr_contact_bins = bins

        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.DESTRUCTIBLE_HIDING_DELAY = 0.2
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': 3, 'health': 5,
                'kineticDamageCorrection': 1.0})
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: scale * health
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        descriptor = _Strict1513Component(
            physics={'weight': 40000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        calls = []
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *args: calls.append(args) or True)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertTrue(destructibles_sensor._catalog_motion_blocked(
                1, _Vector(), 0.0, 20.0, descriptor, 10.0))

        self.assertEqual([37, 38], [call[2] for call in calls])

    def test_catalog_commit_receipt_covers_swept_ahead_proposal(self):
        destructibles_sensor.xrange = range
        filename = 'content/GatesAndFences/test/normal/lod0/fence.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-0.25, -0.2, -0.1, 0.25, 1.5, 0.1, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        boxes = destructibles_sensor._world_catalog_boxes(
            record, _ItemMatrix(_Vector(0.0, 0.0, 4.0)), _Vector(),
            types.SimpleNamespace(Vector3=_Vector))
        instance = {
            'filename': filename.lower(), 'kind': 'fragile',
            'boxes': boxes, 'item_scale': 1.0,
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 37): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 37), instance)

        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.DESTRUCTIBLE_HIDING_DELAY = 0.2
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': 3, 'health': 5,
                'kineticDamageCorrection': 1.0})
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: scale * health
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        descriptor = _Strict1513Component(
            physics={'weight': 40000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destroyed = set()

        def destroy_fragile(unused_space, chunk, item, unused_point,
                             unused_is_shot):
            destroyed.add((chunk, item, None))
            return True

        authority = types.SimpleNamespace(
            is_destroyed=lambda chunk, item, mat=None: (
                (chunk, item, mat) in destroyed),
            destroy_fragile=destroy_fragile)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            proposal = destructibles_sensor._catalog_motion_proposal(
                1, _Vector(), 0.0, 20.0, descriptor, 10.0,
                dt=0.001, kinetic_speed=20.0)
            committed = destructibles_sensor._catalog_motion_blocked(
                1, _Vector(), 0.0, 20.0, descriptor, 10.0,
                dt=0.001, kinetic_speed=20.0, return_detail=True,
                kinetic_commit=True, commit_enabled=True)

        self.assertEqual('crushed', proposal['status'])
        self.assertEqual(((22, 37, None),), proposal['token'])
        self.assertTrue(proposal['requires_commit'])
        self.assertEqual('crushed', committed['status'])
        self.assertEqual(proposal['token'], committed['token'])
        self.assertTrue(committed['accepted_now'])

    def test_remote_destroy_note_blocks_then_releases_synthetic_contact(self):
        area = types.ModuleType('AreaDestructibles')
        area.DESTRUCTIBLE_HIDING_DELAY = 0.2
        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            destructibles_sensor.note_destroyed(
                'fragile', 22, 37, None, 5.0)
        pending = destructibles_sensor.g_offh_destr_pending[(22, 37, None)]
        self.assertGreaterEqual(pending, 5.2)
        authority = types.SimpleNamespace(reset=lambda unused=None: None)
        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            destructibles_sensor.reset(1)
        self.assertNotIn('g_offh_destr_pending', destructibles_sensor.__dict__)

    def test_local_prediction_filters_only_exact_registered_surfaces(self):
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'kind': 'fragile'},
            (22, 38): {'kind': 'structure'},
            (22, 39): {'kind': 'falling'},
        }

        self.assertTrue(destructibles_sensor.begin_local_prediction((
            (22, 37, None), (22, 38, 5), (22, 38, None),
            (22, 39, None), (22, 99, None))))
        self.assertEqual({(22, 37, None), (22, 38, 5)},
                         destructibles_sensor.g_offh_destr_speculative)
        authority = types.SimpleNamespace(
            destroyed_keys=lambda unused_chunk: ())
        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            collision_filter = destructibles_sensor._broken_collision_filter(
                {(22, 37), (22, 38), (22, 39)})

        self.assertFalse(collision_filter(73, 0, 37, 22))
        self.assertFalse(collision_filter(5, 0, 38, 22))
        self.assertTrue(collision_filter(6, 0, 38, 22))
        self.assertTrue(collision_filter(73, 0, 39, 22))
        self.assertTrue(collision_filter(73, 0, 99, 22))
        self.assertTrue(destructibles_sensor.clear_local_prediction(
            ((22, 37, None), (22, 38, 5))))
        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            self.assertIsNone(destructibles_sensor._broken_collision_filter(
                {(22, 37), (22, 38)}))

    def test_local_prediction_commits_exact_native_fragile_and_module(self):
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {
                'kind': 'fragile',
                'boxes': [((1.0, 2.0, 3.0), None, None)],
            },
            (22, 38): {
                'kind': 'structure',
                'boxes': [((4.0, 5.0, 6.0), None, 73)],
            },
        }
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_fragile=mock.Mock(return_value=True),
            destroy_module=mock.Mock(return_value=True))
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority), mock.patch.dict(
                    sys.modules, {
                        'Math': math_module,
                        'BigWorld': types.SimpleNamespace(time=lambda: 10.0),
                        'AreaDestructibles': types.SimpleNamespace(
                            DESTRUCTIBLE_HIDING_DELAY=0.2),
                    }):
            self.assertTrue(destructibles_sensor.commit_local_prediction(
                1, ((22, 37, None), (22, 38, 73)),
                _Vector(), 0.25, 8.0))

        authority.destroy_fragile.assert_called_once()
        authority.destroy_module.assert_called_once()
        self.assertEqual(
            {(22, 37, None), (22, 38, 73)},
            destructibles_sensor.g_offh_destr_speculative)

    def test_isolation_clears_matching_local_prediction(self):
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'kind': 'fragile', 'bin_keys': []},
            (22, 38): {'kind': 'structure', 'bin_keys': []},
        }
        destructibles_sensor.g_offh_destr_speculative = {
            (22, 37, None), (22, 38, 5)}

        destructibles_sensor._drop_isolated_destructible_1513(22, 37)

        self.assertEqual({(22, 38, 5)},
                         destructibles_sensor.g_offh_destr_speculative)

    def test_felled_column_never_blocks_and_keeps_its_resting_obb(self):
        destructibles_sensor.xrange = range
        filename = 'content/Environment/test/normal/lod0/pole.model'
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'falling',
                'boxes': [[-0.25, 0.0, -0.25, 0.25, 8.0, 0.25, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename.lower()]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        chunk_translation = _Vector()
        initial_matrix = _ItemMatrix(_Vector(0.0, 0.0, 4.0))
        current = [initial_matrix]
        matrix_queries = []
        boxes = destructibles_sensor._world_catalog_boxes(
            record, initial_matrix, chunk_translation, math_module, 0)
        instance = {
            'filename': filename.lower(), 'kind': 'falling',
            'boxes': boxes, 'item_scale': 1.0, 'box_index': 0,
            'chunk_translation': (0.0, 0.0, 0.0),
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 37): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 37), instance)

        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        manager._DestructiblesManager__destrInitialMatrices = {
            (22, 37): initial_matrix,
        }
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.g_destructiblesAnimator = types.SimpleNamespace(
            _DestructiblesAnimator__bodies=[{
                'spaceID': 1, 'chunkID': 22, 'destrIndex': 37,
                'touchdownCallback': object(),
            }])
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=35000.0,
            getDescByFilename=lambda unused: {
                'type': 2, 'health': 18,
                'kineticDamageCorrection': 0.0})
        bigworld = types.ModuleType('BigWorld')
        def get_destructible_matrix(*unused):
            matrix_queries.append(tuple(unused))
            return current[0]

        bigworld.wg_getDestructibleMatrix = get_destructible_matrix
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = lambda scale, health: scale * health
        descriptor = _Strict1513Component(
            physics={'weight': 21000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destroyed = set()
        calls = []

        def destroy_column(*args):
            calls.append(args)
            destroyed.add((args[1], args[2], None))
            return True

        authority = types.SimpleNamespace(
            is_destroyed=lambda chunk, item, mat=None: (
                (chunk, item, mat) in destroyed),
            destroy_column=destroy_column)
        destructibles_sensor.set_event_sink(lambda unused: True)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld,
                              'DestructiblesCache': cache,
                              'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertEqual('crushed',
                destructibles_sensor._catalog_motion_blocked(
                    1, _Vector(), 0.0, 11.5, descriptor, 10.0,
                    return_status=True))
            self.assertNotIn(
                (22, 37, None),
                getattr(destructibles_sensor, 'g_offh_destr_pending', {}))
            current[0] = _ItemMatrix(
                _Vector(20.0, 0.0, 4.0), scale=1.2)
            writes = []
            with mock.patch.object(
                    sys, 'stdout',
                    types.SimpleNamespace(write=writes.append)):
                self.assertEqual('clear',
                    destructibles_sensor._catalog_motion_blocked(
                        1, _Vector(), 0.0, 11.5, descriptor, 10.02,
                        return_status=True))
            self.assertEqual({(22, 37)},
                             destructibles_sensor.g_offh_destr_isolated_slots)
            self.assertNotIn(
                (22, 37), destructibles_sensor.g_offh_destr_instances)
            self.assertFalse(any(
                (22, 37) in members for members in
                destructibles_sensor.g_offh_destr_contact_bins.values()))
            self.assertNotIn(
                (22, 37), destructibles_sensor.g_offh_destr_falling_active)
            self.assertEqual(1, len(writes))
            self.assertIn('type=falling_scale_change', writes[0])
            current[0] = initial_matrix
            self.assertEqual('clear',
                destructibles_sensor._catalog_motion_blocked(
                    1, _Vector(), 0.0, 11.5, descriptor, 10.04,
                    return_status=True))

        self.assertEqual(1, len(calls))
        self.assertNotIn(
            (22, 37), destructibles_sensor.g_offh_destr_falling_active)
        self.assertEqual(1, len(matrix_queries))

    def _donation_catalog(self):
        fence = 'content/GatesAndFences/gaf022/normal/lod0/fence.model'
        house = 'content/Buildings/test/normal/lod0/house.model'
        catalog = _catalog({
            fence: {
                'kind': 'fragile',
                'boxes': [[-0.2, 0.0, -2.0, 0.2, 1.5, 2.0, None]],
            },
            house: {
                'kind': 'structure',
                'boxes': [
                    [-2, 0, -2, 0, 3, 2, 73],
                    [0, 0, -2, 2, 3, 2, 74],
                ],
            },
        }, [
            [0] * 11 + [1, fence, 0, 31, 4, 0.75],
            [0] * 11 + [2, house, None, 31, 7, 1.25],
        ])
        return fence, house, catalog

    def _donation_environment(self, descriptors, scaled_calls=None):
        def scaled(scale, health):
            if scaled_calls is not None:
                scaled_calls.append((scale, health))
            return scale * health

        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: descriptors.get(value),
            unitVehicleMass=8000.0)
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = scaled
        return {'AreaDestructibles': area, 'DestructiblesCache': cache}

    def test_donation_covers_the_baked_catalog_without_streaming(self):
        fence, house, catalog = self._donation_catalog()
        destructibles_sensor.set_catalog(catalog)
        descriptors = {
            fence: {'type': 3, 'health': 15,
                    'kineticDamageCorrection': 0.5},
            house: {'type': 4, 'modules': {
                73: {'health': 40, 'armor': 12.0},
                74: {'health': 60}}},
        }
        scaled_calls = []
        modules = self._donation_environment(descriptors, scaled_calls)

        self.assertIsNone(
            destructibles_sensor.__dict__.get('g_offh_destr_instances'))
        with mock.patch.dict(sys.modules, modules):
            donation = destructibles_sensor.donation_rows_1513()

        self.assertEqual(8000.0, donation['unit_vehicle_mass'])
        rows = donation['instances']
        self.assertEqual(2, len(rows))
        by_wire = dict(((row[1], row[2]), row) for row in rows)
        fence_row = by_wire[(31, 4)]
        self.assertEqual(0.75 * 15, fence_row[3])
        self.assertIsNone(fence_row[4])
        house_row = by_wire[(31, 7)]
        self.assertIsNone(house_row[3])
        self.assertEqual([1.25 * 40, 12.0], house_row[4]['73'])
        self.assertEqual([1.25 * 60, 0.0], house_row[4]['74'])
        self.assertIn((0.75, 15), scaled_calls)
        self.assertIn((1.25, 40), scaled_calls)
        self.assertIn((1.25, 60), scaled_calls)
        resources = donation['resources']
        self.assertEqual('fragile',
                         resources[fence.lower()]['destr_type'])
        self.assertEqual(0.5,
                         resources[fence.lower()]['kinetic_correction'])
        self.assertEqual('structure',
                         resources[house.lower()]['destr_type'])

    def test_donation_is_unavailable_after_any_wire_is_isolated(self):
        unused_fence, unused_house, catalog = self._donation_catalog()
        destructibles_sensor.set_catalog(catalog)
        with mock.patch.object(
                sys, 'stdout', types.SimpleNamespace(write=lambda unused: None)):
            destructibles_sensor._isolate_destructible_1513(
                'wire_identity_mismatch', 31, 4)

        self.assertIsNone(destructibles_sensor.donation_rows_1513())

    def test_one_bad_descriptor_fails_the_whole_donation(self):
        fence, house, catalog = self._donation_catalog()
        descriptors = {
            fence: {'type': 3, 'health': 15},
        }

        destructibles_sensor.set_catalog(catalog)
        with mock.patch.dict(
                sys.modules, self._donation_environment(descriptors)):
            with self.assertRaisesRegex(RuntimeError,
                                        'no #1513 descriptor'):
                destructibles_sensor.donation_rows_1513()

        descriptors[house] = {'type': 3, 'health': 20}
        destructibles_sensor.set_catalog(catalog)
        with mock.patch.dict(
                sys.modules, self._donation_environment(descriptors)):
            with self.assertRaisesRegex(RuntimeError, 'kind disagrees'):
                destructibles_sensor.donation_rows_1513()

    def test_streamed_identity_mismatch_isolates_both_wires_once(self):
        fence = 'content/GatesAndFences/gaf022/normal/lod0/fence.model'
        chunk_translation = _Vector(100.0, 5.0, 200.0)
        matrix = _ItemMatrix(_Vector(2.0, 0.0, 4.0))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            matrix, chunk_translation, math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            fence: {
                'kind': 'fragile',
                'boxes': [[-0.2, 0.0, -2.0, 0.2, 1.5, 2.0, None]],
            },
        }, [list(signature) + [fence, 0, 23, 5, 1.0]]))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {'type': 3, 'health': 15})
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=chunk_translation)
        bigworld.wg_getDestructibleMatrix = lambda *unused: matrix
        bigworld.wg_getDestructibleEffectCategory = mock.Mock(return_value=3)
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range

        writes = []
        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    sys, 'stdout', types.SimpleNamespace(write=writes.append)):
            destructibles_sensor._fell_trees_near(
                1, _Vector(102.0, 5.0, 204.0), 0.0, 0.25,
                type_descriptor)

        self.assertEqual({(22, 0), (23, 5)},
                         destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertEqual({'wire_identity_mismatch'},
                         destructibles_sensor.g_offh_destr_isolation_logs)
        self.assertEqual(1, len(writes))
        self.assertIn('live=(22, 0) baked=(23, 5)', writes[0])
        bigworld.wg_getDestructibleEffectCategory.assert_called_once_with(
            1, 22, 0, -1)
        self.assertEqual({}, destructibles_sensor.g_offh_destr_instances)

    def test_late_blank_column_registration_uses_native_initial_matrix(self):
        pole = 'content/Environment/env414/normal/lod0/pole.model'
        chunk_translation = _Vector(100.0, 5.0, 200.0)
        initial = _ItemMatrix(_Vector(-3.0, 0.0, 6.0))
        current = _ItemMatrix(_Vector(30.0, 0.0, 30.0))
        decoy = 'content/Environment/env999/normal/lod0/decoy.model'
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            initial, chunk_translation, math_module, 1000)
        current_signature = destructibles_sensor._locator_signature(
            current, chunk_translation, math_module, 1000)
        catalog = _catalog({
            pole: {
                'kind': 'falling',
                'boxes': [[-0.3, 0.0, -0.3, 0.3, 9.0, 0.3, None]],
            },
            decoy: {
                'kind': 'falling',
                'boxes': [[-2.0, 0.0, -2.0, 2.0, 2.0, 2.0, None]],
            },
        }, [list(signature) + [pole, 0, 22, 0, 1.0],
            list(current_signature) + [decoy, 0, 23, 0, 1.0]])
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        manager._DestructiblesManager__destrInitialMatrices = {
            (22, 0): initial,
        }
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.g_destructiblesAnimator = types.SimpleNamespace(
            _DestructiblesAnimator__bodies=[])
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = lambda unused: 22
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                {'type': 2, 'health': 18}
                if value in (pole, decoy) else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
            translation=chunk_translation)
        bigworld.wg_getDestructibleMatrix = lambda *unused: current
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: 2
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range
        for chunk_names in ((), (pole,)):
            destructibles_sensor.set_catalog(catalog)
            destructibles_sensor.g_offh_destr_runtime_space = 1
            destructibles_sensor.note_destroyed(
                'column', 22, 0, None, 5.0)
            bigworld.wg_getChunkDestrFilenames = (
                lambda *unused: chunk_names)

            with mock.patch.dict(
                    sys.modules, {'AreaDestructibles': area,
                                  'BigWorld': bigworld, 'Math': math_module}):
                destructibles_sensor._fell_trees_near(
                    1, _Vector(97.0, 5.0, 206.0), 0.0, 0.25,
                    descriptor)

            instance = destructibles_sensor.g_offh_destr_instances[(22, 0)]
            self.assertEqual(pole.lower(), instance['filename'])
            self.assertEqual('falling', instance['kind'])
            self.assertEqual(0, instance['box_index'])
            self.assertEqual(
                (100.0, 5.0, 200.0), instance['chunk_translation'])

    def test_scaled_instance_reaching_across_chunk_boundary_is_registered(self):
        filename = 'content/GatesAndFences/scaled/normal/lod0/fence.model'
        chunk_translation = _Vector(100.0, 0.0, 0.0)
        item_matrix = _ItemMatrix(
            _Vector(1.0, 0.0, 0.0), scale=20.0)
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        signature = destructibles_sensor._locator_signature(
            item_matrix, chunk_translation, math_module, 1000)
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-2.0, -0.1, -0.1, 2.0, 0.1, 0.1, None]],
            },
        }, [list(signature) + [filename, 0, 1, 0, 20.0]]))
        descriptor = {'type': 3, 'health': 100,
                      'kineticDamageCorrection': 1.0}
        type_descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6),
                    (1.6, 1.0, 3.6), None))))
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(1, 1)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4

        def chunk_id_from_position(value):
            import math
            return (int(math.floor(value.x / 100.0)) +
                    1000 * int(math.floor(value.z / 100.0)))

        area.chunkIDFromPosition = chunk_id_from_position
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: (
                descriptor if value == filename else None))
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = (
            lambda unused_space, chunk_id: (
                (filename,) if chunk_id == 1 else ()))
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *unused: descriptor['type'])
        bigworld.wg_getChunkMatrix = (
            lambda unused_space, chunk_id: types.SimpleNamespace(
                translation=chunk_translation
                if chunk_id == 1 else _Vector()))
        bigworld.wg_getDestructibleMatrix = (
            lambda unused_space, unused_chunk, unused_index:
            item_matrix)
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: self.fail(
                'catalog registration must not destroy by proximity'))
        destructibles_sensor.xrange = range

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            destructibles_sensor._fell_trees_near(
                1, _Vector(60.0, 0.0, 0.0), 0.0, 6.0,
                type_descriptor)

        instance = destructibles_sensor.g_offh_destr_instances[(1, 0)]
        self.assertEqual(20.0, instance['item_scale'])
        self.assertEqual(
            (1, 0, None, filename, 'fragile', 20.0),
            destructibles_sensor._catalog_candidate_at_contact(
                _Vector(61.0, 0.0, 0.0)))

    def test_catalog_unknown_candidate_fails_closed_even_when_legacy_close(self):
        filename = 'content/Environment/unknown/normal/lod0/prop.model'
        destructibles_sensor.set_catalog(_catalog({
            'known.model': {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 1, 1, None]],
            },
        }))
        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda value: {'type': 3, 'health': 3})

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}):
            self.assertFalse(
                destructibles_sensor._solid_destructible_candidate_1513(
                    _mat_info_1513(
                        True, _Vector(0, 0, 0.1), _Vector(0, 0, 1), 73,
                        filename, 22, 37),
                    _Vector(), _Vector(0, 0, 1)))

    def test_installed_catalog_rejects_unknown_direct_fragile_hit(self):
        destructibles_sensor.set_catalog(_catalog({
            'known.model': {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 1, 1, None]],
            },
        }))
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {'type': 3, 'health': 3})
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused: False,
            destroy_fragile=lambda *unused: self.fail(
                'unknown catalog resource was destroyed'))

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            self.assertFalse(destructibles_sensor._try_destroy_destructible(
                1, _mat_info_1513(
                    True, _Vector(), _Vector(0, 1, 0), 73,
                    'unknown.model', 22, 37),
                0.0, 6.0))

    def test_reset_keeps_catalog_but_drops_native_instance_registry(self):
        destructibles_sensor.set_catalog(_catalog({
            'known.model': {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 1, 1, None]],
            },
        }))
        installed = destructibles_sensor._destructible_catalog
        destructibles_sensor.g_offh_destr_instances = {(1, 2): object()}
        authority = types.SimpleNamespace(reset=lambda unused=None: None)

        with mock.patch.object(
                destructibles_sensor, '_get_destr_authority',
                return_value=authority):
            destructibles_sensor.reset(1)

        self.assertIs(installed, destructibles_sensor._destructible_catalog)
        self.assertNotIn('g_offh_destr_instances', destructibles_sensor.__dict__)

    def _empty_catalog_scan_fixture(self, streamed=True):
        destructibles_sensor.set_catalog(_catalog({
            'known.model': {
                'kind': 'fragile',
                'boxes': [[-1, -1, -1, 1, 1, 1, None]],
            },
        }))
        manager = _Manager()
        manager.space_id = 1
        if streamed:
            manager.set_chunk_count(22, 0)
        mapper = mock.Mock(return_value=22)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = manager
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.chunkIDFromPosition = mapper
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: None)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = mock.Mock(return_value=())
        bigworld.wg_getChunkMatrix = mock.Mock(return_value=
            types.SimpleNamespace(translation=_Vector()))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        math_module.Matrix = lambda value: value
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None))))
        destructibles_sensor.xrange = range
        return (manager, mapper, area, bigworld, math_module, descriptor)

    def test_empty_swept_cells_avoid_repeating_full_chunk_scan_for_30_tanks(self):
        (unused_manager, mapper, area, bigworld, math_module,
         descriptor) = self._empty_catalog_scan_fixture()

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}):
            for index in range(30):
                destructibles_sensor._fell_trees_near(
                    1, _Vector(float(index) * 0.01, 0.0, 0.0),
                    0.0, 6.0, descriptor)

        # The first exact sweep maps current/forward and the surrounding 3x3
        # chunks. Later tanks only validate the native current chunk while the
        # complete streamed registry and the same empty swept cells are valid.
        self.assertEqual(40, mapper.call_count)
        bigworld.wg_getChunkDestrFilenames.assert_called_once_with(1, 22)
        bigworld.wg_getChunkMatrix.assert_called_once_with(1, 22)
        counts = destructibles_sensor.registry_counts()
        self.assertEqual(29, counts['receipt_proximity_hits'])
        self.assertEqual(1, counts['receipt_proximity_misses'])
        self.assertEqual(1, counts['receipt_proximity_stores'])
        self.assertEqual(1, counts['receipt_proximity_entries'])

    def test_empty_swept_cell_receipt_waits_for_streaming_completion(self):
        (manager, mapper, area, bigworld, math_module,
         descriptor) = self._empty_catalog_scan_fixture(streamed=False)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            manager.set_chunk_count(22, 0)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        # Pending scans must retry the full native mapping. Only the fourth
        # call may reuse the empty receipt created after streaming completed.
        self.assertEqual(34, mapper.call_count)
        bigworld.wg_getChunkDestrFilenames.assert_called_once_with(1, 22)

    def test_empty_swept_cell_receipt_drops_on_chunk_unload(self):
        (manager, mapper, area, bigworld, math_module,
         descriptor) = self._empty_catalog_scan_fixture()

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            del manager._DestructiblesManager__loadedChunkIDs[22]
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        self.assertEqual(23, mapper.call_count)
        self.assertNotIn(
            22, destructibles_sensor.g_offh_tree_state['chunks'])
        counts = destructibles_sensor.registry_counts()
        self.assertEqual(1, counts['receipt_proximity_hits'])
        self.assertEqual(2, counts['receipt_proximity_misses'])
        self.assertEqual(1, counts['receipt_proximity_invalidated'])
        self.assertEqual(0, counts['receipt_proximity_entries'])

    def test_empty_swept_cell_receipt_counts_native_count_change_as_miss(self):
        (manager, mapper, area, bigworld, math_module,
         descriptor) = self._empty_catalog_scan_fixture()
        bigworld.wg_getDestructibleMatrix = mock.Mock(
            return_value=_ItemMatrix())
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: -1

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            manager.set_chunk_count(22, 1)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        counts = destructibles_sensor.registry_counts()
        self.assertEqual(1, counts['receipt_proximity_hits'])
        self.assertEqual(2, counts['receipt_proximity_misses'])
        self.assertEqual(2, counts['receipt_proximity_stores'])
        self.assertEqual(1, counts['receipt_proximity_invalidated'])
        self.assertEqual(1, counts['receipt_proximity_entries'])

    def test_empty_swept_cell_receipt_invalidates_on_spatial_index_change(self):
        (unused_manager, mapper, area, bigworld, math_module,
         descriptor) = self._empty_catalog_scan_fixture()

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            instance = {
                'boxes': (((100.0, 0.0, 100.0),
                           ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                            (0.0, 0.0, 1.0)), None),),
            }
            destructibles_sensor._index_catalog_instance_1513(
                destructibles_sensor.g_offh_destr_contact_bins,
                (22, 7), instance)
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        # The second call uses one current-chunk validation. Adding any exact
        # footprint invalidates all older negative spatial receipts.
        self.assertEqual(23, mapper.call_count)

    def test_empty_swept_cell_receipt_is_not_used_while_a_column_moves(self):
        (unused_manager, mapper, area, bigworld, math_module,
         descriptor) = self._empty_catalog_scan_fixture()

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'BigWorld': bigworld, 'Math': math_module}):
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)
            destructibles_sensor.g_offh_destr_falling_active = {
                (22, 7): {'last_refresh': None},
            }
            destructibles_sensor._fell_trees_near(
                1, _Vector(), 0.0, 6.0, descriptor)

        # A moving column can enter a previously empty cell. It therefore
        # keeps the complete scan live until its final indexed pose settles.
        self.assertEqual(22, mapper.call_count)

    def test_empty_catalog_hull_cells_are_scanned_once_per_spatial_generation(self):
        (unused_manager, unused_mapper, unused_area, unused_bigworld,
         unused_math, descriptor) = self._empty_catalog_scan_fixture()
        destructibles_sensor.g_offh_destr_instances = {}
        destructibles_sensor.g_offh_destr_contact_bins = {}

        with mock.patch.object(
                destructibles_sensor, '_bin_keys_for_bounds',
                wraps=destructibles_sensor._bin_keys_for_bounds) as bins:
            for index in range(30):
                self.assertFalse(destructibles_sensor._catalog_hull_contact(
                    _Vector(float(index) * 0.01, 0.0, 0.0),
                    0.0, 6.0, descriptor, 0.04))

        self.assertEqual(1, bins.call_count)
        counts = destructibles_sensor.registry_counts()
        self.assertEqual(29, counts['receipt_contact_hits'])
        self.assertEqual(1, counts['receipt_contact_misses'])
        self.assertEqual(1, counts['receipt_contact_stores'])
        self.assertEqual(1, counts['receipt_contact_entries'])

    def test_empty_catalog_hull_receipt_expires_outside_swept_cell_envelope(self):
        (unused_manager, unused_mapper, unused_area, unused_bigworld,
         unused_math, descriptor) = self._empty_catalog_scan_fixture()
        destructibles_sensor.g_offh_destr_instances = {}
        destructibles_sensor.g_offh_destr_contact_bins = {}

        with mock.patch.object(
                destructibles_sensor, '_bin_keys_for_bounds',
                wraps=destructibles_sensor._bin_keys_for_bounds) as bins:
            self.assertFalse(destructibles_sensor._catalog_hull_contact(
                _Vector(), 0.0, 6.0, descriptor, 0.04))
            self.assertFalse(destructibles_sensor._catalog_hull_contact(
                _Vector(6.0, 0.0, 0.0), 0.0, 6.0, descriptor, 0.04))

        self.assertEqual(2, bins.call_count)

    def test_new_exact_contact_invalidates_empty_catalog_hull_receipt(self):
        (unused_manager, unused_mapper, unused_area, unused_bigworld,
         unused_math, descriptor) = self._empty_catalog_scan_fixture()
        destructibles_sensor.g_offh_destr_instances = {}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        self.assertFalse(destructibles_sensor._catalog_hull_contact(
            _Vector(), 0.0, 6.0, descriptor, 0.04))
        instance = {
            'filename': 'known.model',
            'descriptor_filename': 'known.model',
            'kind': 'fragile',
            'item_scale': 1.0,
            'boxes': (((0.0, 0.0, 3.8),
                       ((0.25, 0.0, 0.0), (0.0, 0.5, 0.0),
                        (0.0, 0.0, 0.25)), None),),
        }
        destructibles_sensor.g_offh_destr_instances[(22, 7)] = instance
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 7), instance)

        self.assertTrue(destructibles_sensor._catalog_hull_contact(
            _Vector(), 0.0, 6.0, descriptor, 0.04))
        counts = destructibles_sensor.registry_counts()
        self.assertEqual(2, counts['receipt_contact_misses'])
        self.assertEqual(1, counts['receipt_contact_stores'])
        self.assertEqual(1, counts['receipt_contact_invalidated'])
        self.assertEqual(0, counts['receipt_contact_entries'])

    def test_lateral_catalog_sweep_keeps_long_hull_corner_in_both_directions(
            self):
        self._empty_catalog_scan_fixture()
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -4.0), (1.6, 1.0, 6.0), None))))

        for item_index, motion_yaw, contact_x in (
                (7, math.pi * 0.5, 2.5),
                (8, -math.pi * 0.5, -2.5)):
            instance = {
                'filename': 'known.model',
                'descriptor_filename': 'known.model',
                'kind': 'fragile',
                'item_scale': 1.0,
                'boxes': (((contact_x, 0.0, 5.5),
                           ((0.25, 0.0, 0.0), (0.0, 0.5, 0.0),
                            (0.0, 0.0, 0.25)), None),),
            }
            destructibles_sensor.g_offh_destr_instances = {
                (22, item_index): instance}
            destructibles_sensor.g_offh_destr_contact_bins = {}
            destructibles_sensor._index_catalog_instance_1513(
                destructibles_sensor.g_offh_destr_contact_bins,
                (22, item_index), instance)

            self.assertTrue(destructibles_sensor._catalog_hull_contact(
                _Vector(), 0.0, 5.0, descriptor, 0.2,
                motion_yaw=motion_yaw))

    def test_lateral_catalog_sweep_excludes_rotated_hull_false_region(self):
        self._empty_catalog_scan_fixture()
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -4.0), (1.6, 1.0, 6.0), None))))
        instance = {
            'filename': 'known.model',
            'descriptor_filename': 'known.model',
            'kind': 'fragile',
            'item_scale': 1.0,
            'boxes': (((5.5, 0.0, 0.0),
                       ((0.25, 0.0, 0.0), (0.0, 0.5, 0.0),
                        (0.0, 0.0, 0.25)), None),),
        }
        destructibles_sensor.g_offh_destr_instances = {(22, 7): instance}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 7), instance)

        self.assertFalse(destructibles_sensor._catalog_hull_contact(
            _Vector(), 0.0, 5.0, descriptor, 0.2,
            motion_yaw=math.pi * 0.5))

    def test_diagonal_swept_zonotope_excludes_its_empty_aabb_corner(self):
        bbox = ((-1.6, -1.0, -4.0), (1.6, 1.0, 6.0), None)
        swept = destructibles_sensor._vehicle_swept_box(
            _Vector(), 0.0, 5.0, bbox, 1.0, motion_yaw=0.55)
        bounds = destructibles_sensor._box_xz_bounds(swept)
        outside = (
            (-2.0, 0.0, 6.7),
            ((0.05, 0.0, 0.0), (0.0, 0.5, 0.0),
             (0.0, 0.0, 0.05)), None)

        self.assertLess(bounds[0], outside[0][0])
        self.assertGreater(bounds[1], outside[0][0])
        self.assertLess(bounds[2], outside[0][2])
        self.assertGreater(bounds[3], outside[0][2])
        self.assertFalse(destructibles_sensor._boxes_intersect(
            swept, outside))

    def test_lateral_catalog_contact_invalidates_warm_empty_receipt(self):
        self._empty_catalog_scan_fixture()
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.6, -1.0, -4.0), (1.6, 1.0, 6.0), None))))
        destructibles_sensor.g_offh_destr_instances = {}
        destructibles_sensor.g_offh_destr_contact_bins = {}
        self.assertFalse(destructibles_sensor._catalog_hull_contact(
            _Vector(), 0.0, 5.0, descriptor, 0.2,
            motion_yaw=math.pi * 0.5))
        instance = {
            'filename': 'known.model',
            'descriptor_filename': 'known.model',
            'kind': 'fragile',
            'item_scale': 1.0,
            'boxes': (((2.5, 0.0, 5.5),
                       ((0.25, 0.0, 0.0), (0.0, 0.5, 0.0),
                        (0.0, 0.0, 0.25)), None),),
        }
        destructibles_sensor.g_offh_destr_instances[(22, 7)] = instance
        destructibles_sensor._index_catalog_instance_1513(
            destructibles_sensor.g_offh_destr_contact_bins,
            (22, 7), instance)

        self.assertTrue(destructibles_sensor._catalog_hull_contact(
            _Vector(), 0.0, 5.0, descriptor, 0.2,
            motion_yaw=math.pi * 0.5))
        counts = destructibles_sensor.registry_counts()
        self.assertEqual(1, counts['receipt_contact_invalidated'])

    def test_chunk_registry_limits_each_frame_to_nearby_contact_bins(self):
        registry = {'bins': {}, 'extended_bins': {}, 'count': 0,
                    'max_radius': 4.5}
        positions = [(-7.9, 0.0), (0.0, 0.0), (7.9, 0.0)]
        positions.extend((24.0 + index * 8.0, 40.0)
                         for index in range(230))
        for index, (x, z) in enumerate(positions):
            item = (index, x, 0.0, z, 3, 'fragile', 3, 0, None, 0.0)
            key = destructibles_sensor._destructible_bin_key(x, z)
            registry['bins'].setdefault(key, []).append(item)
            registry['count'] += 1
        # A catalog footprint is indexed into every exact world-space bin it
        # occupies, so the query no longer grows with the map's largest prop.
        extended = (233, 11.5, 0.0, 0.0, 3, 'large-fragile', 3, 0,
                    (), 4.5)
        key = destructibles_sensor._destructible_bin_key(7.0, 0.0)
        registry['extended_bins'].setdefault(key, []).append(extended)
        registry['count'] += 1
        destructibles_sensor.xrange = range

        nearby = list(destructibles_sensor._nearby_destructibles(
            registry, _Vector()))

        self.assertEqual(234, registry['count'])
        self.assertEqual([0, 1, 2, 233],
                         sorted(item[0] for item in nearby))

    def test_exact_box_bin_query_deduplicates_a_large_structure(self):
        item = (233, 20.0, 0.0, 0.0, 4, 'station', 300, 0, (), 25.0)
        registry = {'bins': {}, 'extended_bins': {}, 'count': 1,
                    'max_radius': 25.0}
        for key in destructibles_sensor._bin_keys_for_bounds(-10, 40, -4, 4):
            registry['extended_bins'].setdefault(key, []).append(item)
        vehicle_box = ((0.0, 0.0, 0.0),
                       ((2.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                        (0.0, 0.0, 4.0)))
        destructibles_sensor.xrange = range

        nearby = list(destructibles_sensor._nearby_destructibles(
            registry, _Vector(), vehicle_box))

        self.assertEqual([233], [value[0] for value in nearby])


def _native_item_surface(items):
    """Build the exact pinned #1513 chunk destructible surface for a test.

    ``items`` is one ``(category, filename)`` pair per native item index:

    * ``category`` ``None`` models an unresolved item, which
      ``wg_getDestructibleEffectCategory`` reports by failing;
    * ``category`` ``-1`` models a resolved item whose native type owns no
      name handler;
    * ``filename`` ``None`` models an item that contributes no name.
    * ``filename`` ``''`` models a handled item that emits an empty C string.

    ``wg_getChunkDestrFilenames`` is then built exactly as
    ``WorldOfTanks.exe`` ``0x006b1a10`` builds it: it walks item indices in
    order and appends one string per handled item, including an empty string,
    while appending nothing at all for a skipped item.  A shorter result is
    compacted, so its positions are deliberately not native item indices.
    """
    names = tuple(filename for category, filename in items
                  if category is not None and category != -1
                  and filename is not None)

    def chunk_filenames(unused_space, unused_chunk):
        return names

    def effect_category(unused_space, unused_chunk, item_index, unused_module):
        if item_index < 0 or item_index >= len(items):
            raise RuntimeError('native item index is out of range')
        category = items[item_index][0]
        if category is None:
            raise RuntimeError('native item is unresolved')
        return category

    return names, chunk_filenames, effect_category


class NativeItemNameContractTests(unittest.TestCase):
    """Cover the exact #1513 compacted-name contract read from the client."""

    TREE = 1
    FALLING = 2
    FRAGILE = 3
    STRUCTURE = 4

    def setUp(self):
        self.descriptors = {}

    def tearDown(self):
        destructibles_sensor.__dict__.pop('g_offh_destr_item_names', None)
        destructibles_sensor.__dict__.pop(
            'g_offh_destr_native_name_lists', None)
        destructibles_sensor.__dict__.pop(
            'g_offh_destr_item_name_budget', None)
        destructibles_sensor.__dict__.pop(
            'g_offh_destr_item_name_cache_serial', None)
        destructibles_sensor.__dict__.pop(
            'g_offh_destr_isolation_logs', None)
        destructibles_sensor.__dict__.pop(
            'g_offh_destr_isolated_chunks', None)
        destructibles_sensor.__dict__.pop(
            'g_offh_destr_isolated_slots', None)
        destructibles_sensor.__dict__.pop(
            'g_offh_destr_name_unresolved_slots', None)

    def _area(self):
        area = types.ModuleType('AreaDestructibles')
        area.DESTR_TYPE_TREE = self.TREE
        area.DESTR_TYPE_FALLING_ATOM = self.FALLING
        area.DESTR_TYPE_FRAGILE = self.FRAGILE
        area.DESTR_TYPE_STRUCTURE = self.STRUCTURE
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=self.descriptors.get)
        return area

    def _align(self, items):
        for unused_category, filename in items:
            if filename and filename not in self.descriptors:
                self.fail('test must type every native name')
        names, chunk_filenames, effect_category = _native_item_surface(items)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = chunk_filenames
        bigworld.wg_getDestructibleEffectCategory = effect_category
        with mock.patch.object(sys, 'stdout', mock.Mock()):
            return names, destructibles_sensor._chunk_item_names_1513(
                bigworld, self._area(), 1, 22, len(items), names)

    def test_compacted_list_position_is_never_the_item_index(self):
        # A small synthetic analogue of the old diagnostic failure: a poplar's
        # name sits at list position 1 while native item 1 is unnamed.  The old
        # reports prove that direct indexing was unsound, but do not prove the
        # reconstructed name of real Prokhorovka item (31875, 70).
        poplar = 'speedtree/05_prohorovka/poplar.spt'
        toilet = ('content/Environment/env014_Toilet/normal/lod0/'
                  'env014_Toilet.model')
        self.descriptors[poplar] = {'type': self.TREE, 'health': 10}
        self.descriptors[toilet] = {'type': self.FRAGILE, 'health': 15}
        items = ((self.TREE, poplar), (self.FRAGILE, None),
                 (self.TREE, poplar))

        names, (mapping, status, anomalous) = self._align(items)

        self.assertEqual((poplar, poplar), names)
        self.assertEqual('exact', status)
        self.assertEqual((), anomalous)
        # Item 1 owns no name at all; it is emphatically not names[1].
        self.assertEqual({0: poplar, 2: poplar}, mapping)
        self.assertIsNone(mapping.get(1))

    def test_full_width_empty_name_preserves_native_slot_positions(self):
        first = 'content/GatesAndFences/test/normal/lod0/fence-a.model'
        second = 'content/GatesAndFences/test/normal/lod0/fence-b.model'
        self.descriptors[first] = {'type': self.FRAGILE, 'health': 15}
        self.descriptors[second] = {'type': self.FRAGILE, 'health': 15}
        items = ((self.FRAGILE, first), (self.FRAGILE, ''),
                 (self.FRAGILE, second))

        names, (mapping, status, anomalous) = self._align(items)

        self.assertEqual((first, '', second), names)
        self.assertEqual('exact', status)
        self.assertEqual((), anomalous)
        self.assertEqual({0: first, 2: second}, mapping)
        self.assertIsNone(mapping.get(1))

    def test_short_list_with_empty_name_never_uses_list_positions(self):
        first = 'speedtree/test/first.spt'
        second = 'speedtree/test/second.spt'
        self.descriptors[first] = {'type': self.TREE, 'health': 10}
        self.descriptors[second] = {'type': self.TREE, 'health': 10}
        items = ((self.FRAGILE, ''), (self.TREE, first),
                 (self.FRAGILE, None), (self.TREE, second))

        names, (mapping, status, anomalous) = self._align(items)

        self.assertEqual(('', first, second), names)
        self.assertEqual('exact', status)
        self.assertEqual((), anomalous)
        self.assertEqual({1: first, 3: second}, mapping)
        self.assertIsNone(mapping.get(0))
        self.assertIsNone(mapping.get(2))

    def test_unnamed_type_declines_quietly_and_named_type_still_aligns(self):
        tree = 'speedtree/11_murovanka/oak.spt'
        fence = ('content/GatesAndFences/gaf001_WoodFence/normal/lod0/'
                 'gaf001_WoodFence.model')
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        self.descriptors[fence] = {'type': self.FRAGILE, 'health': 15}
        items = ((self.FRAGILE, None), (self.TREE, tree),
                 (self.FRAGILE, None), (self.TREE, tree))

        unused_names, (mapping, status, anomalous) = self._align(items)

        self.assertEqual('exact', status)
        self.assertEqual((), anomalous)
        self.assertEqual({1: tree, 3: tree}, mapping)

    def test_partially_named_type_is_declined_for_that_type_only(self):
        tree = 'speedtree/33_fjord/pine.spt'
        fence = ('content/GatesAndFences/gaf012_Fence/normal/lod0/'
                 'gaf012_FenceTile1.model')
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        self.descriptors[fence] = {'type': self.FRAGILE, 'health': 15}
        # Two fragile items but only one fragile name: that type's compaction
        # cannot be reconstructed, so no fragile item is given a name.
        items = ((self.FRAGILE, fence), (self.FRAGILE, None),
                 (self.TREE, tree))

        unused_names, (mapping, status, anomalous) = self._align(items)

        self.assertEqual('partial', status)
        self.assertEqual((self.FRAGILE,), anomalous)
        self.assertEqual({2: tree}, mapping)

    def test_unresolved_and_handlerless_items_are_skipped_like_the_engine(self):
        tree = 'speedtree/18_cliff/birch.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        # Item 0 is unresolved (the category call fails) and item 1 has no
        # name handler (category -1); the engine's own loop skips both.
        items = ((None, None), (-1, None), (self.TREE, tree))

        names, (mapping, status, anomalous) = self._align(items)

        self.assertEqual((tree,), names)
        self.assertEqual('exact', status)
        self.assertEqual((), anomalous)
        self.assertEqual({2: tree}, mapping)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)
        self.assertNotIn(
            (22, 1), destructibles_sensor.g_offh_destr_isolated_slots)

    def test_unresolved_slot_is_never_requeried_after_cache_invalidation(self):
        tree = 'speedtree/18_cliff/birch.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        names = (tree,)
        calls = []
        bigworld = types.ModuleType('BigWorld')

        def category(unused_space, unused_chunk, item, unused_module):
            calls.append(item)
            if item == 0:
                raise RuntimeError('native item is unresolved')
            return self.TREE

        bigworld.wg_getDestructibleEffectCategory = category
        area = self._area()
        with mock.patch.object(sys, 'stdout', mock.Mock()):
            first = destructibles_sensor._chunk_item_names_1513(
                bigworld, area, 1, 22, 2, names)
            destructibles_sensor._invalidate_chunk_native_names_1513(22)
            second = destructibles_sensor._chunk_item_names_1513(
                bigworld, area, 1, 22, 2, names)

        self.assertEqual(({1: tree}, 'exact', ()), first)
        self.assertEqual(first, second)
        self.assertEqual([0, 1, 1], calls)
        self.assertEqual(
            {(22, 0)}, destructibles_sensor.g_offh_destr_isolated_slots)

    def test_named_type_absent_from_live_items_is_reported(self):
        tree = 'speedtree/34_redshire/elm.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        names = (tree,)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: names
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *unused: self.FRAGILE)

        with mock.patch.object(sys, 'stdout', mock.Mock()):
            mapping, status, anomalous = (
                destructibles_sensor._chunk_item_names_1513(
                    bigworld, self._area(), 1, 22, 1, names))

        self.assertEqual('partial', status)
        self.assertEqual((self.TREE,), anomalous)
        self.assertEqual({}, mapping)

    def test_untypeable_name_declines_the_whole_chunk(self):
        names = ('speedtree/unknown/mystery.spt',)
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: names
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: self.TREE

        mapping, status, anomalous = (
            destructibles_sensor._chunk_item_names_1513(
                bigworld, self._area(), 1, 22, 1, names))

        self.assertIsNone(mapping)
        self.assertEqual('name_descriptor', status)
        self.assertEqual((), anomalous)

    def test_empty_name_list_still_enumerates_every_native_item_boundedly(self):
        queries = []
        tick = [1.0]
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *args: queries.append(args) or self.FRAGILE)
        bigworld.time = lambda: tick[0]
        result = None
        for unused in range(20):
            before = len(queries)
            result = destructibles_sensor._chunk_item_names_1513(
                bigworld, self._area(), 1, 22, 250, ())
            self.assertLessEqual(
                len(queries) - before,
                destructibles_sensor._ITEM_NAME_QUERY_BUDGET)
            if result[1] == 'exact':
                break
            self.assertEqual('pending_alignment', result[1])
            tick[0] += 1.0

        self.assertEqual(({}, 'exact', ()), result)
        self.assertEqual(250, len(queries))

    def test_shared_tick_budget_bounds_multiple_chunks_and_callers(self):
        tree = 'speedtree/06_ensk/poplar.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        budget = destructibles_sensor._ITEM_NAME_QUERY_BUDGET
        first_count = budget + 3
        second_count = 5
        first_names = (tree,) * first_count
        second_names = (tree,) * second_count
        queries = []
        tick = [10.0]
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *args: queries.append(args) or self.TREE)
        bigworld.time = lambda: tick[0]
        area = self._area()

        first = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 22, first_count, first_names)
        same_caller = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 22, first_count, first_names)
        other_chunk = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 23, second_count, second_names)

        self.assertEqual((None, 'pending_alignment'), first)
        self.assertEqual((None, 'pending_alignment'), same_caller)
        self.assertEqual((None, 'pending_alignment'), other_chunk)
        self.assertEqual(budget, len(queries))
        self.assertEqual({22}, set(call[1] for call in queries))

        tick[0] = 11.0
        second_waiting = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 23, second_count, second_names)
        first_done = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 22, first_count, first_names)
        second_done = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 23, second_count, second_names)
        after_completion = len(queries)
        cached = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 22, first_count, first_names)

        self.assertEqual((None, 'pending_alignment'), second_waiting)
        self.assertEqual('exact', second_done[1])
        self.assertEqual(second_count, len(second_done[0]))
        self.assertEqual('exact', first_done[1])
        self.assertEqual(first_count, len(first_done[0]))
        self.assertEqual(first_done, cached)
        self.assertEqual(budget + second_count + 3, after_completion)
        self.assertEqual(after_completion, len(queries))
        self.assertNotIn(
            'g_offh_destr_isolated_chunks', destructibles_sensor.__dict__)

    def test_clockless_stub_advances_only_on_explicit_test_tick(self):
        tree = 'speedtree/06_ensk/poplar.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        budget = destructibles_sensor._ITEM_NAME_QUERY_BUDGET
        native_count = budget + 1
        names = (tree,) * native_count
        queries = []
        bigworld = types.ModuleType('BigWorld')
        bigworld._offh_item_name_budget_tick = 1
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *args: queries.append(args) or self.TREE)
        area = self._area()

        first = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 22, native_count, names)
        same_tick = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 22, native_count, names)

        self.assertEqual((None, 'pending_alignment'), first)
        self.assertEqual((None, 'pending_alignment'), same_tick)
        self.assertEqual(budget, len(queries))

        bigworld._offh_item_name_budget_tick = 2
        completed = destructibles_sensor._chunk_native_names_1513(
            bigworld, area, 1, 22, native_count, names)

        self.assertEqual('exact', completed[1])
        self.assertEqual(native_count, len(completed[0]))
        self.assertEqual(native_count, len(queries))

    def test_reload_with_identical_content_recovers_one_mapping(self):
        tree = 'speedtree/01_karelia/spruce.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        names, chunk_filenames, effect_category = _native_item_surface(
            ((self.TREE, tree), (self.FRAGILE, None)))
        queries = []
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getChunkDestrFilenames = chunk_filenames
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *args: queries.append(args) or effect_category(*args))
        area = self._area()

        first = destructibles_sensor._chunk_item_names_1513(
            bigworld, area, 1, 22, 2, names)
        after_first = len(queries)
        second = destructibles_sensor._chunk_item_names_1513(
            bigworld, area, 1, 22, 2, names)

        self.assertEqual({0: tree}, first[0])
        self.assertEqual(first, second)
        # A reload of identical native content must not re-derive identities.
        self.assertEqual(after_first, len(queries))

    def test_changed_name_list_re_derives_the_mapping(self):
        first_tree = 'speedtree/02_malinovka/willow.spt'
        second_tree = 'speedtree/02_malinovka/aspen.spt'
        self.descriptors[first_tree] = {'type': self.TREE, 'health': 10}
        self.descriptors[second_tree] = {'type': self.TREE, 'health': 10}
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: self.TREE
        area = self._area()

        first = destructibles_sensor._chunk_item_names_1513(
            bigworld, area, 1, 22, 1, (first_tree,))
        second = destructibles_sensor._chunk_item_names_1513(
            bigworld, area, 1, 22, 1, (second_tree,))

        self.assertEqual({0: first_tree}, first[0])
        self.assertEqual({0: second_tree}, second[0])

    def test_chunk_cache_stays_bounded(self):
        tree = 'speedtree/06_ensk/poplar.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_getDestructibleEffectCategory = lambda *unused: self.TREE
        area = self._area()
        limit = destructibles_sensor._ITEM_NAME_CHUNK_CACHE_LIMIT

        for chunk_id in range(limit * 2 + 3):
            destructibles_sensor._chunk_item_names_1513(
                bigworld, area, 1, chunk_id, 1, (tree,))

        self.assertLessEqual(
            len(destructibles_sensor.g_offh_destr_item_names), limit)

    def test_abandoned_incomplete_cache_does_not_starve_new_chunk(self):
        tree = 'speedtree/06_ensk/poplar.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        budget = destructibles_sensor._ITEM_NAME_QUERY_BUDGET
        native_count = budget + 1
        names = (tree,) * native_count
        queries = []
        tick = [0.0]
        bigworld = types.ModuleType('BigWorld')
        bigworld.time = lambda: tick[0]
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *args: queries.append(args) or self.TREE)
        area = self._area()
        limit = destructibles_sensor._ITEM_NAME_CHUNK_CACHE_LIMIT

        for chunk_id in range(limit):
            if chunk_id:
                # One grace tick preserves a still-active focus; the following
                # tick proves the old partial job was abandoned and transfers it.
                tick[0] += 1.0
                before = len(queries)
                waiting = destructibles_sensor._chunk_item_names_1513(
                    bigworld, area, 1, chunk_id, native_count, names)
                self.assertEqual('pending_alignment', waiting[1])
                self.assertEqual(before, len(queries))
                tick[0] += 1.0
            before = len(queries)
            pending = destructibles_sensor._chunk_item_names_1513(
                bigworld, area, 1, chunk_id, native_count, names)
            self.assertEqual('pending_alignment', pending[1])
            self.assertEqual(budget, len(queries) - before)

        self.assertEqual(limit,
                         len(destructibles_sensor.g_offh_destr_item_names))
        active_chunk = limit
        tick[0] += 1.0
        before = len(queries)
        waiting = destructibles_sensor._chunk_item_names_1513(
            bigworld, area, 1, active_chunk, native_count, names)
        self.assertEqual('pending_alignment', waiting[1])
        self.assertEqual(before, len(queries))
        tick[0] += 1.0
        pending = destructibles_sensor._chunk_item_names_1513(
            bigworld, area, 1, active_chunk, native_count, names)
        self.assertEqual('pending_alignment', pending[1])
        tick[0] += 1.0
        completed = destructibles_sensor._chunk_item_names_1513(
            bigworld, area, 1, active_chunk, native_count, names)

        self.assertEqual('exact', completed[1])
        self.assertEqual(native_count, len(completed[0]))
        self.assertIn(
            (1, active_chunk), destructibles_sensor.g_offh_destr_item_names)
        self.assertLessEqual(
            len(destructibles_sensor.g_offh_destr_item_names), limit)

    def test_more_than_cache_limit_active_chunks_eventually_complete(self):
        tree = 'speedtree/06_ensk/poplar.spt'
        self.descriptors[tree] = {'type': self.TREE, 'health': 10}
        budget = destructibles_sensor._ITEM_NAME_QUERY_BUDGET
        native_count = budget + 1
        names = (tree,) * native_count
        queries = []
        tick = [0.0]
        bigworld = types.ModuleType('BigWorld')
        bigworld.time = lambda: tick[0]
        bigworld.wg_getDestructibleEffectCategory = (
            lambda *args: queries.append(args) or self.TREE)
        area = self._area()
        limit = destructibles_sensor._ITEM_NAME_CHUNK_CACHE_LIMIT
        pending = set(range(limit + 1))

        for cycle in range((limit + 1) * 4):
            tick[0] = float(cycle)
            before = len(queries)
            ordered = sorted(pending)
            if ordered:
                offset = cycle % len(ordered)
                ordered = ordered[offset:] + ordered[:offset]
            completed = set()
            for chunk_id in ordered:
                result = destructibles_sensor._chunk_item_names_1513(
                    bigworld, area, 1, chunk_id, native_count, names)
                if result[1] == 'exact':
                    completed.add(chunk_id)
                else:
                    self.assertEqual('pending_alignment', result[1])
            self.assertLessEqual(len(queries) - before, budget)
            pending.difference_update(completed)
            if not pending:
                break

        self.assertEqual(set(), pending)
        self.assertLessEqual(
            len(destructibles_sensor.g_offh_destr_item_names), limit)
        self.assertNotIn(
            'g_offh_destr_isolated_chunks', destructibles_sensor.__dict__)

    def test_native_name_list_snapshot_is_shared_until_invalidation(self):
        query = mock.Mock(return_value=('tree',))
        bigworld = types.SimpleNamespace(
            wg_getChunkDestrFilenames=query)

        first = destructibles_sensor._chunk_native_name_list_1513(
            bigworld, 1, 22, 1)
        second = destructibles_sensor._chunk_native_name_list_1513(
            bigworld, 1, 22, 1)
        destructibles_sensor._invalidate_chunk_native_names_1513(22)
        third = destructibles_sensor._chunk_native_name_list_1513(
            bigworld, 1, 22, 1)

        self.assertEqual((('tree',), 'ready'), first)
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(2, query.call_count)

if __name__ == '__main__':
    unittest.main()
