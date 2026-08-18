from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import gun_mechanics, loadout, spotting


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


class SpottingProfileTests(unittest.TestCase):
    """The device magnitudes come from the client's own descriptors."""

    def _descriptor(self, devices=(), optics=1.0, net_bonus=0.1):
        return types.SimpleNamespace(
            optionalDevices=list(devices),
            miscAttrs={'circularVisionRadiusFactor': optics},
            type=types.SimpleNamespace(
                invisibilityDeltas={'camouflageNetBonus': net_bonus}))

    def _binoculars(self, factor=1.25, delay=3.0):
        return types.SimpleNamespace(
            name='stereoscope', circularVisionRadiusFactor=factor,
            activateWhenStillSec=delay)

    def _camouflage_net(self, delay=3.0):
        return types.SimpleNamespace(
            name='camouflageNet', activateWhenStillSec=delay)

    def test_a_bare_vehicle_carries_no_situational_device(self):
        profile = loadout.spotting_profile(self._descriptor())

        self.assertFalse(profile['has_binoculars'])
        self.assertFalse(profile['has_camouflage_net'])
        self.assertEqual(1.0, profile['binocular_factor'])
        self.assertEqual(0.0, profile['camouflage_net_bonus'])

    def test_binoculars_replace_coated_optics_instead_of_stacking(self):
        # #1513's Stereoscope divides the descriptor's optics factor out.
        profile = loadout.spotting_profile(
            self._descriptor([self._binoculars()], optics=1.1))

        self.assertTrue(profile['has_binoculars'])
        self.assertAlmostEqual(1.25 / 1.1, profile['binocular_factor'])
        base = 400.0 * 1.1
        self.assertAlmostEqual(
            400.0 * 1.25,
            spotting.effective_view_range(
                400.0, vision_factor=1.1,
                binocular_factor=profile['binocular_factor'],
                binocular_active=True))
        self.assertAlmostEqual(
            base,
            spotting.effective_view_range(400.0, vision_factor=1.1))

    def test_the_camouflage_net_bonus_comes_from_the_vehicle_type(self):
        profile = loadout.spotting_profile(
            self._descriptor([self._camouflage_net()], net_bonus=0.13))

        self.assertTrue(profile['has_camouflage_net'])
        self.assertAlmostEqual(0.13, profile['camouflage_net_bonus'])

    def test_a_still_device_waits_for_its_activation_delay(self):
        self.assertFalse(loadout.still_device_active(2.9, 3.0))
        self.assertTrue(loadout.still_device_active(3.0, 3.0))
        self.assertTrue(loadout.still_device_active(9.0, 3.0))

    def test_recon_and_situational_take_the_best_single_crewman(self):
        crew = (
            types.SimpleNamespace(role='commander', roleLevel=100.0, skills=[
                types.SimpleNamespace(name='commander_eagleEye', level=60.0)]),
            types.SimpleNamespace(role='radioman', roleLevel=100.0, skills=[
                types.SimpleNamespace(name='commander_eagleEye', level=90.0),
                types.SimpleNamespace(name='radioman_finder', level=50.0)]),
        )

        profile = loadout.spotting_profile(self._descriptor(), crew)

        self.assertEqual(90.0, profile['recon_level'])
        self.assertEqual(50.0, profile['situational_level'])

    def test_camouflage_is_averaged_over_the_whole_crew(self):
        crew = (
            types.SimpleNamespace(role='commander', roleLevel=100.0, skills=[
                types.SimpleNamespace(name='camouflage', level=100.0)]),
            types.SimpleNamespace(role='driver', roleLevel=100.0, skills=[]),
        )

        profile = loadout.spotting_profile(self._descriptor(), crew)

        # A member without the skill contributes zero, it is not skipped.
        self.assertAlmostEqual(50.0, profile['camouflage_level'])

    def test_an_inactive_skill_contributes_nothing(self):
        crew = (types.SimpleNamespace(role='commander', roleLevel=100.0, skills=[
            types.SimpleNamespace(
                name='radioman_finder', level=100.0, isActive=False)]),)

        profile = loadout.spotting_profile(self._descriptor(), crew)

        self.assertEqual(0.0, profile['situational_level'])

    def test_the_skills_lengthen_the_view_range(self):
        plain = spotting.effective_view_range(400.0)
        keen = spotting.effective_view_range(
            400.0, recon_level=100.0, situational_level=100.0)

        self.assertAlmostEqual(plain * 1.02 * 1.03, keen)


if __name__ == '__main__':
    unittest.main()
