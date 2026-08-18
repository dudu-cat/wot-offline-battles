from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import gun_mechanics, loadout


def _device(name):
    return types.SimpleNamespace(name=name)


def _descriptor(devices=()):
    return types.SimpleNamespace(optionalDevices=list(devices))


def _crew(*skill_lists):
    return tuple(
        types.SimpleNamespace(
            skills=[types.SimpleNamespace(name=name) for name in skills])
        for skills in skill_lists)


class LoadoutLawTests(unittest.TestCase):

    def test_bare_crew_keeps_the_082_baseline_multiplier(self):
        values = loadout.baseline()

        self.assertEqual(110.0, values['effective_crew_level'])
        self.assertAlmostEqual(1.0 / 1.05, values['crew_multiplier'])
        self.assertEqual(1.0, values['reload_factor'])
        self.assertEqual(1.0, values['aim_time_factor'])

    def test_ventilation_brotherhood_and_food_stack_on_the_crew_level(self):
        crew = _crew(['brotherhood'], ['brotherhood'])

        values = loadout.modifiers(
            _descriptor([_device('improvedVentilation_class1')]),
            equipments=[_device('chocolate')], crew_skills=
            loadout.crew_skill_names(crew))

        # 100 + 5 + 5 + 10 for both the crew and the commander share.
        self.assertEqual(120.0, values['crew_level'])
        self.assertEqual(132.0, values['effective_crew_level'])
        self.assertTrue(values['has_ventilation'])
        self.assertTrue(values['has_brotherhood'])
        self.assertTrue(values['has_rations'])
        self.assertAlmostEqual(
            1.0 / (0.5 + 0.005 * 132.0), values['crew_multiplier'])

    def test_brotherhood_needs_every_crew_member(self):
        crew = _crew(['brotherhood'], ['repair'])

        values = loadout.modifiers(
            crew_skills=loadout.crew_skill_names(crew))

        self.assertFalse(values['has_brotherhood'])
        self.assertEqual(110.0, values['effective_crew_level'])

    def test_rammer_and_gun_laying_drive_use_the_exact_082_factors(self):
        values = loadout.modifiers(
            _descriptor([_device('gunRammer'), _device('aimDrives')]))

        self.assertEqual(0.9, values['reload_factor'])
        self.assertAlmostEqual(1.0 / 1.1, values['aim_time_factor'])

    def test_stabiliser_snap_shot_and_smooth_ride_dampen_the_bloom(self):
        crew = _crew(['snapShot', 'smoothDriving'])

        values = loadout.modifiers(
            _descriptor([_device('stabilizer')]),
            crew_skills=loadout.crew_skill_names(crew))

        self.assertAlmostEqual(0.8 * 0.96, values['bloom_move_factor'])
        self.assertAlmostEqual(0.8, values['bloom_rotation_factor'])
        self.assertAlmostEqual(0.8 * 0.925, values['bloom_turret_factor'])

    def test_unknown_crew_never_claims_brothers_in_arms(self):
        self.assertFalse(loadout.modifiers()['has_brotherhood'])
        self.assertFalse(
            loadout.modifiers(crew_skills=())['has_brotherhood'])


class GunStateLoadoutTests(unittest.TestCase):

    def _gun_descriptor(self):
        return types.SimpleNamespace(
            gun={'shots': [{'shell': {'damage': (100.0,)}}],
                 'shotDispersionAngle': 0.1,
                 'shotDispersionFactors': {'afterShot': 1.5,
                                           'turretRotation': 1.0},
                 'aimingTime': 2.0, 'reloadTime': 10.0,
                 'clip': (1, 2.0), 'maxAmmo': 40},
            chassis={'shotDispersionFactors': (0.1, 0.1)},
            turret={'maxAmmo': 40}, maxAmmo=40, activeGunShotIndex=0,
            optionalDevices=[])

    def test_a_rammer_shortens_the_reload_on_top_of_the_crew_law(self):
        descriptor = self._gun_descriptor()
        plain = gun_mechanics.GunState(descriptor)

        descriptor.optionalDevices = [_device('gunRammer')]
        rammed = gun_mechanics.GunState(
            descriptor, loadout.modifiers(descriptor))

        self.assertAlmostEqual(plain.reload * 0.9, rammed.reload)
        self.assertAlmostEqual(plain.aim_time, rammed.aim_time)

    def test_a_gun_laying_drive_shortens_only_the_aiming_time(self):
        descriptor = self._gun_descriptor()
        plain = gun_mechanics.GunState(descriptor)

        descriptor.optionalDevices = [_device('aimDrives')]
        aided = gun_mechanics.GunState(
            descriptor, loadout.modifiers(descriptor))

        self.assertAlmostEqual(plain.aim_time / 1.1, aided.aim_time)
        self.assertAlmostEqual(plain.reload, aided.reload)

    def test_a_stabiliser_damps_the_movement_bloom(self):
        descriptor = self._gun_descriptor()
        plain = gun_mechanics.GunState(descriptor)
        plain.tick(0.0, False, 10.0, 0.0, 0.0, descriptor)

        descriptor.optionalDevices = [_device('vertStabilizer')]
        steady = gun_mechanics.GunState(
            descriptor, loadout.modifiers(descriptor))
        steady.tick(0.0, False, 10.0, 0.0, 0.0, descriptor)

        self.assertLess(steady.dispersion, plain.dispersion)


if __name__ == '__main__':
    unittest.main()
