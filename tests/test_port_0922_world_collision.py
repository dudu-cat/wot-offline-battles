import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = (
    ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import world_collision


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


class WorldCollisionTests(unittest.TestCase):

    def test_level_street_still_runs_wall_rays(self):
        calls = []

        def collide(unused_space, start, end, unused_mask):
            calls.append((start, end))
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=lambda *unused: None)
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
            wg_getMatInfoNearPoint=lambda *unused: None)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            None, False, 0.04)

        self.assertFalse(blocked)
        self.assertEqual([], horizontal_calls)


if __name__ == '__main__':
    unittest.main()
