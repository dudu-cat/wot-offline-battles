import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
CLIENT_SCRIPTS = (
    ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import destructibles_sensor, world_collision


class _Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            x, y, z = x.x, x.y, x.z
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __add__(self, other):
        return _Vector(self.x + other.x, self.y + other.y,
                       self.z + other.z)

    def __sub__(self, other):
        return _Vector(self.x - other.x, self.y - other.y,
                       self.z - other.z)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y +
                         self.z * self.z)

    def scale(self, value):
        return _Vector(self.x * value, self.y * value, self.z * value)

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


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


def _miss_mat_info_1513(*unused):
    return False, _Vector(), _Vector(), 0, '', 0, 0


class WorldCollisionTests(unittest.TestCase):

    def test_native_1513_hull_uses_attributes_without_mapping_protocol(self):
        horizontal_calls = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            horizontal_calls.append((start, end))
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.8, -0.8, -3.4),
                    (1.8, 1.0, 3.4), None))))

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            descriptor, False, 0.04)

        self.assertFalse(blocked)
        self.assertTrue(horizontal_calls)

    def test_level_street_still_runs_wall_rays(self):
        calls = []

        def collide(unused_space, start, end, unused_mask):
            calls.append((start, end))
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    _Vector(0.0, 0.0, -1.0), 0)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            None, False, 0.04)

        self.assertTrue(blocked)
        self.assertTrue(any(abs(start.y - end.y) < 0.01
                            for start, end in calls))

    def test_gradually_rising_ground_remains_drivable(self):
        horizontal_calls = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, start.z * 0.10, start.z),)
            horizontal_calls.append((start, end))
            return (_Vector(start.x, start.y, end.z),)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            None, False, 0.04)

        self.assertFalse(blocked)
        self.assertEqual([], horizontal_calls)

    def test_native_destructible_failure_is_not_silently_passable(self):
        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    _Vector(0.0, 0.0, -1.0), 0)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.object(
                world_collision, '_try_destroy_solid_hit',
                side_effect=RuntimeError('native destroy failed')):
            with self.assertRaisesRegex(RuntimeError, 'native destroy failed'):
                world_collision.check_horizontal_collision(
                    bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                    None, False, 0.04)

    def test_solid_contact_forwards_native_surface_normal(self):
        surface_normal = _Vector(1.0, 0.0, 0.0)

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    surface_normal, 75)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)
        contacts = []

        def destroy(unused_space, hit_point, normal, unused_yaw,
                    unused_velocity):
            contacts.append((hit_point, normal))
            return True

        with mock.patch.object(
                world_collision, '_try_destroy_solid_hit',
                side_effect=destroy):
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertFalse(blocked)
        self.assertTrue(contacts)
        self.assertTrue(all(normal is surface_normal
                            for unused_point, normal in contacts))

    def test_ruinberg_fragile_truck_contact_reaches_native_authority(self):
        """Connect the exact #1513 collision and material-hit boundaries."""
        truck_filename = (
            'content/Environment/env419_OldGTruck/normal/lod0/'
            'env418_OldGMercedes_01.model')
        surface_normal = _Vector(1.0, 0.0, 0.0)
        material_calls = []
        destroyed = set()
        authority_calls = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    surface_normal, 112)

        def material_probe(unused_space, start, stop, point, unused_cb):
            material_calls.append((start, stop, point))
            # The independent centre-lane scan does not own a collision point.
            # Only the stock point-normal*3 / point+normal*2 probe identifies
            # this compiled type-2 Ruinberg prop.
            if (abs((point.x - start.x) - 3.0) < 0.001 and
                    abs((stop.x - point.x) - 2.0) < 0.001):
                return (True, point, surface_normal, 73, truck_filename,
                        37, 22)
            return _miss_mat_info_1513()

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=material_probe)
        math_module = types.SimpleNamespace(Vector3=_Vector)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda filename: (
                {'type': area.DESTR_TYPE_FRAGILE, 'health': 19}
                if filename == truck_filename else None))

        def destroy_fragile(*args):
            authority_calls.append(args)
            destroyed.add((args[1], args[2]))
            return True

        authority = types.SimpleNamespace(
            is_destroyed=lambda chunk_id, item_index, *unused: (
                (chunk_id, item_index) in destroyed),
            destroy_fragile=destroy_fragile)

        with mock.patch.dict(sys.modules, {'AreaDestructibles': area}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    destructibles_sensor, '_event_sink',
                    lambda unused_event: True):
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertFalse(blocked)
        self.assertTrue(material_calls)
        self.assertEqual(1, len(authority_calls))
        self.assertEqual((1, 22, 37), authority_calls[0][:3])
        self.assertEqual(False, authority_calls[0][4])


if __name__ == '__main__':
    unittest.main()
