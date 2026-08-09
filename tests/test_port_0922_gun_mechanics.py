import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.gun_mechanics import GunState


class _Vector(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def normalise(self):
        length = math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)
        self.x /= length
        self.y /= length
        self.z /= length


def _descriptor(max_ammo=100, clip=(3, 1.0)):
    shells = [types.SimpleNamespace(compactDescr=index + 1)
              for index in range(3)]
    shots = [types.SimpleNamespace(shell=shell) for shell in shells]
    gun = types.SimpleNamespace(
        shots=shots, maxAmmo=max_ammo, clip=clip, reloadTime=6.0,
        aimingTime=2.0, shotDispersionAngle=0.12,
        shotDispersionFactors={'afterShot': 1.5, 'turretRotation': 0.3})
    return types.SimpleNamespace(
        gun=gun, turret=types.SimpleNamespace(maxAmmo=max_ammo),
        chassis={'shotDispersionFactors': (0.2, 0.4)},
        activeGunShotIndex=0)


class GunMechanicsParityTests(unittest.TestCase):

    def test_descriptor_state_preserves_082_fallback_ammo_and_crew_factor(self):
        state = GunState(_descriptor())
        crew_multiplier = 1.0 / (0.5 + 0.005 * 110.0)

        self.assertEqual([60, 30, 10], state.ammo)
        self.assertAlmostEqual(0.12 * crew_multiplier,
                               state.base_dispersion)
        self.assertAlmostEqual(6.0 * crew_multiplier, state.reload)
        self.assertEqual(0, state.clip)
        self.assertAlmostEqual(state.reload, state.reload_time)

    def test_reload_does_not_advance_during_countdown(self):
        descriptor = _descriptor()
        state = GunState(descriptor)
        pending = state.reload_time

        state.tick(5.0, False, 0.0, 0.0, 0.0, descriptor)
        self.assertEqual(pending, state.reload_time)
        self.assertEqual(0, state.clip)

        state.tick(pending, True, 0.0, 0.0, 0.0, descriptor)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual(3, state.clip)

    def test_after_shot_bloom_and_full_reload_factor_match_082(self):
        descriptor = _descriptor(clip=(1, 2.0))
        state = GunState(descriptor)
        state.reload_time = 0.0
        state.clip = 1
        before = state.dispersion

        self.assertTrue(state.commit_fire(2.0))
        jump = state.base_dispersion * state.after_shot
        self.assertAlmostEqual(
            math.sqrt(before * before + jump * jump), state.dispersion)
        self.assertAlmostEqual(state.reload * 2.0, state.reload_time)
        self.assertAlmostEqual(state.reload_time, state.reload_duration)

    def test_manual_shell_change_empties_clip_for_full_reload(self):
        state = GunState(_descriptor())
        state.clip = 3
        state.reload_time = 0.0

        self.assertTrue(state.sync_shell_index(1))
        self.assertEqual(1, state.shot_index)
        self.assertEqual(0, state.clip)
        self.assertAlmostEqual(state.reload, state.reload_time)

    def test_scatter_uses_082_three_axis_gaussian(self):
        state = GunState(_descriptor())
        calls = []

        def gauss(mean, sigma):
            calls.append((mean, sigma))
            return sigma

        direction = state.scatter(_Vector(0.0, 0.0, 1.0), gauss=gauss)

        self.assertEqual(3, len(calls))
        self.assertTrue(all(
            abs(sigma - state.dispersion / 3.0) < 1e-12
            for unused_mean, sigma in calls))
        self.assertAlmostEqual(1.0, math.sqrt(
            direction.x ** 2 + direction.y ** 2 + direction.z ** 2))

    def test_scatter_accepts_native_1513_reticle_angle(self):
        state = GunState(_descriptor())
        calls = []

        def gauss(mean, sigma):
            calls.append((mean, sigma))
            return 0.0

        state.scatter(
            _Vector(0.0, 0.0, 1.0), gauss=gauss,
            dispersion_angle=0.03)

        self.assertEqual([(0.0, 0.01)] * 3, calls)


if __name__ == '__main__':
    unittest.main()
