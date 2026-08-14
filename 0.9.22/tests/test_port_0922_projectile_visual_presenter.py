import math
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.entities.remote_vehicle import (
    RemoteVehicleFactory, _RemoteShotPresenter)


class _Vector(object):

    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            try:
                x, y, z = x[0], x[1], x[2]
            except (TypeError, IndexError):
                x, y, z = x.x, x.y, x.z
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def scale(self, value):
        return _Vector(self.x * value, self.y * value, self.z * value)

    def normalise(self):
        length = math.sqrt(self.x * self.x + self.y * self.y +
                           self.z * self.z)
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


class _Matrix(object):

    def __init__(self, value=None):
        self.translation = _Vector(getattr(value, 'translation', _Vector()))


class _BigWorld(object):

    def __init__(self):
        self.entity = lambda unused_entity_id: None
        self.entities = {}
        self.camera = lambda: types.SimpleNamespace(
            position=_Vector(91.0, 17.0, -4.0))


class _ProjectileMover(object):

    instances = []

    def __init__(self):
        self.calls = []
        self.hide_calls = []
        self.destroy_calls = 0
        self.__class__.instances.append(self)

    def add(self, *args):
        self.calls.append(args)

    def hide(self, *args):
        self.hide_calls.append(args)

    def destroy(self):
        self.destroy_calls += 1


def _descriptor(effects_index=7):
    shell = types.SimpleNamespace(effectsIndex=effects_index)
    shot = types.SimpleNamespace(
        shell=shell, speed=925.0, gravity=9.81, maxDistance=640.0)
    return types.SimpleNamespace(
        activeGunShotIndex=0,
        gun=types.SimpleNamespace(shots=[shot]))


def _modules(effects=None):
    if effects is None:
        effects = {7: {'projectile': 'canonical-tracer'}}
    items = types.ModuleType('items')
    items.vehicles = types.SimpleNamespace(
        g_cache=types.SimpleNamespace(shotEffects=effects))
    projectile_mover = types.ModuleType('ProjectileMover')
    projectile_mover.ProjectileMover = _ProjectileMover
    return {'items': items, 'ProjectileMover': projectile_mover}


class ProjectileVisualPresenterTests(unittest.TestCase):

    def setUp(self):
        _ProjectileMover.instances[:] = []
        self.bigworld = _BigWorld()
        self.math = types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix)

    def _factory(self):
        return RemoteVehicleFactory(
            self.bigworld, self.math, types.SimpleNamespace(), 7)

    def test_public_canonical_launch_preserves_exact_nine_argument_values(self):
        effects = {'projectile': 'canonical-tracer'}
        with mock.patch.dict(sys.modules, _modules({7: effects})):
            factory = self._factory()
            visual_id = factory.play_projectile_tracer(
                _descriptor(), 0, (1.25, -2.5, 3.75),
                (812.125, 4.5, -11.25), 6.75, 777.5, 42)
            second_id = factory.play_projectile_tracer(
                _descriptor(), 0, (2.0, 3.0, 4.0),
                (700.0, 0.0, 0.0), 0.0, 5000.0, 43)

            self.assertEqual(1000000, visual_id)
            self.assertEqual(1000001, second_id)
            self.assertEqual(1, len(_ProjectileMover.instances))
            args = _ProjectileMover.instances[0].calls[0]
            self.assertEqual(9, len(args))
            self.assertEqual(1000000, args[0])
            self.assertIs(effects, args[1])
            self.assertEqual(struct.pack('>d', 6.75),
                             struct.pack('>d', args[2]))
            self.assertEqual(
                struct.pack('>ddd', 1.25, -2.5, 3.75),
                struct.pack('>ddd', args[3].x, args[3].y, args[3].z))
            self.assertEqual(
                struct.pack('>ddd', 812.125, 4.5, -11.25),
                struct.pack('>ddd', args[4].x, args[4].y, args[4].z))
            self.assertIs(args[3], args[5])
            self.assertEqual(struct.pack('>d', 777.5),
                             struct.pack('>d', args[6]))
            self.assertEqual(42, args[7])
            self.assertEqual((91.0, 17.0, -4.0),
                             (args[8].x, args[8].y, args[8].z))

            factory.destroy_all()

    def test_transient_remote_launch_prefers_canonical_values(self):
        with mock.patch.dict(sys.modules, _modules()):
            presenter = _RemoteShotPresenter(
                self.bigworld, self.math, types.SimpleNamespace())
            model = types.SimpleNamespace(
                node=mock.Mock(side_effect=AssertionError(
                    'canonical tracer must not read the current muzzle')))
            vehicle = types.SimpleNamespace(
                id=57, model=model, typeDescriptor=_descriptor(),
                _offlineLANShotIndex=0,
                _offlineLANShotOrigin=(8.0, 9.0, 10.0),
                _offlineLANShotVelocity=(100.0, 200.0, 300.0),
                _offlineLANShotGravity=3.25,
                _offlineLANShotMaxDistance=1234.0,
                position=_Vector(-99.0, -99.0, -99.0), yaw=2.0)

            self.assertTrue(presenter.play_tracer(vehicle))

            args = _ProjectileMover.instances[0].calls[0]
            self.assertEqual((8.0, 9.0, 10.0),
                             (args[3].x, args[3].y, args[3].z))
            self.assertEqual((100.0, 200.0, 300.0),
                             (args[4].x, args[4].y, args[4].z))
            self.assertEqual(3.25, args[2])
            self.assertEqual(1234.0, args[6])
            self.assertEqual(57, args[7])
            model.node.assert_not_called()

    def test_delayed_canonical_launch_uses_current_reference_and_stops_once(self):
        with mock.patch.dict(sys.modules, _modules()):
            factory = self._factory()
            visual_id = factory.play_projectile_tracer(
                _descriptor(), 0, (0.0, 1.0, 0.0),
                (100.0, 20.0, 0.0), 10.0, 1000.0, 42,
                '1:p:42:7', (75.0, 13.1875, 0.0),
                (100.0, 12.5, 0.0))
            duplicate = factory.play_projectile_tracer(
                _descriptor(), 0, (999.0, 999.0, 999.0),
                (1.0, 1.0, 1.0), 10.0, 1000.0, 42,
                '1:p:42:7', (1.0, 1.0, 1.0), (1.0, 1.0, 1.0))

            self.assertEqual(1000000, visual_id)
            self.assertEqual(visual_id, duplicate)
            mover = _ProjectileMover.instances[0]
            self.assertEqual(1, len(mover.calls))
            args = mover.calls[0]
            self.assertEqual((75.0, 13.1875, 0.0),
                             (args[3].x, args[3].y, args[3].z))
            self.assertEqual((100.0, 12.5, 0.0),
                             (args[4].x, args[4].y, args[4].z))
            self.assertEqual((0.0, 1.0, 0.0),
                             (args[5].x, args[5].y, args[5].z))

            self.assertTrue(factory.stop_projectile_tracer(
                '1:p:42:7', (80.0, 12.0, 0.0)))
            self.assertFalse(factory.stop_projectile_tracer(
                '1:p:42:7', (80.0, 12.0, 0.0)))
            self.assertEqual(1, len(mover.hide_calls))
            hidden_id, hidden_at = mover.hide_calls[0]
            self.assertEqual(visual_id, hidden_id)
            self.assertEqual((80.0, 12.0, 0.0),
                             (hidden_at.x, hidden_at.y, hidden_at.z))

    def test_remote_launch_without_transient_values_keeps_legacy_path(self):
        with mock.patch.dict(sys.modules, _modules()):
            presenter = _RemoteShotPresenter(
                self.bigworld, self.math, types.SimpleNamespace())
            node = types.SimpleNamespace(translation=_Vector(4.0, 5.0, 6.0))
            vehicle = types.SimpleNamespace(
                id=58,
                model=types.SimpleNamespace(node=mock.Mock(return_value=node)),
                typeDescriptor=_descriptor(), _offlineLANShotIndex=0,
                position=_Vector(-99.0, -99.0, -99.0),
                yaw=0.0, _aim_yaw=math.pi / 2.0, _gun_pitch=-0.2)

            self.assertTrue(presenter.play_tracer(vehicle))

            args = _ProjectileMover.instances[0].calls[0]
            self.assertEqual((4.0, 5.0, 6.0),
                             (args[3].x, args[3].y, args[3].z))
            self.assertAlmostEqual(math.cos(0.2) * 925.0, args[4].x)
            self.assertAlmostEqual(math.sin(0.2) * 925.0, args[4].y)
            self.assertAlmostEqual(0.0, args[4].z, places=8)
            self.assertEqual(9.81, args[2])
            self.assertEqual(640.0, args[6])
            self.assertEqual(58, args[7])
            vehicle.model.node.assert_called_once_with('HP_gunFire')

    def test_invalid_native_values_and_missing_effects_fail_closed(self):
        with mock.patch.dict(sys.modules, _modules({})):
            factory = self._factory()
            descriptor = _descriptor()
            invalid = (
                (descriptor, 0, (float('nan'), 0.0, 0.0),
                 (1.0, 0.0, 0.0), 1.0, 10.0, 1),
                (descriptor, 0, (0.0, 0.0, 0.0),
                 (float('inf'), 0.0, 0.0), 1.0, 10.0, 1),
                (descriptor, 0, (0.0, 0.0, 0.0),
                 (1.0, 0.0, 0.0), -1.0, 10.0, 1),
                (descriptor, 0, (0.0, 0.0, 0.0),
                 (1.0, 0.0, 0.0), 1.0, 0.0, 1),
                (descriptor, 0, (0.0, 0.0, 0.0),
                 (1.0, 0.0, 0.0), 1.0, 10.0, 0),
                (descriptor, 4, (0.0, 0.0, 0.0),
                 (1.0, 0.0, 0.0), 1.0, 10.0, 1),
            )
            for arguments in invalid:
                self.assertFalse(factory.play_projectile_tracer(*arguments))
            self.assertFalse(factory.play_projectile_tracer(
                descriptor, 0, (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0), 1.0, 10.0, 1))
            self.assertEqual([], _ProjectileMover.instances)

            factory.destroy_all()

    def test_destroy_closes_presenter_and_destroys_mover_once(self):
        with mock.patch.dict(sys.modules, _modules()):
            factory = self._factory()
            self.assertEqual(1000000, factory.play_projectile_tracer(
                _descriptor(), 0, (0.0, 1.0, 2.0),
                (3.0, 4.0, 5.0), 9.81, 500.0, 77))
            mover = _ProjectileMover.instances[0]

            factory.destroy_all()

            self.assertEqual(1, mover.destroy_calls)
            self.assertFalse(factory.play_projectile_tracer(
                _descriptor(), 0, (0.0, 1.0, 2.0),
                (3.0, 4.0, 5.0), 9.81, 500.0, 77))
            self.assertEqual(1, len(mover.calls))


if __name__ == '__main__':
    unittest.main()
