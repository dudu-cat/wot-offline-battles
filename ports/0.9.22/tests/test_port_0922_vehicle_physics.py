from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[3]
CLIENT_SCRIPTS = (
    ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import vehicle_physics


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


class VehiclePhysicsDescriptorTests(unittest.TestCase):

    def test_rotation_speed_reads_native_1513_chassis_attribute(self):
        descriptor = types.SimpleNamespace(
            physics={},
            chassis=_Strict1513Component(rotationSpeed=0.75))

        params = vehicle_physics.derive_params(descriptor)

        self.assertEqual(0.75, params['rotSpd'])


if __name__ == '__main__':
    unittest.main()
