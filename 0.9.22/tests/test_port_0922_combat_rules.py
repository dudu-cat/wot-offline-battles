from pathlib import Path
import math
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import combat_rules


def _shot(kind='ARMOR_PIERCING', caliber=90.0,
          piercing=(160.0, 120.0), maximum=720.0, damage=240.0,
          explosion_radius=0.0):
    shell = types.SimpleNamespace(
        kind=kind, caliber=caliber, damage=(damage,),
        explosionRadius=explosion_radius)
    return types.SimpleNamespace(
        shell=shell, piercingPower=piercing, maxDistance=maximum)


def _material(armor, vehicle_damage_factor=1.0, **flags):
    values = {
        'armor': armor,
        'vehicleDamageFactor': vehicle_damage_factor,
    }
    values.update(flags)
    return types.SimpleNamespace(**values)


def _collision(distance, angle_cos, material, component='vehicleHull'):
    return types.SimpleNamespace(
        dist=distance, hitAngleCos=angle_cos, matInfo=material,
        compName=component)


class CombatRulesTests(unittest.TestCase):

    def test_p100_p500_use_the_fixed_400_metre_slope(self):
        shot = _shot(piercing=(200.0, 100.0), maximum=900.0)

        self.assertEqual(200.0, combat_rules.range_piercing(shot, 100.0))
        self.assertEqual(150.0, combat_rules.range_piercing(shot, 300.0))
        self.assertEqual(100.0, combat_rules.range_piercing(shot, 500.0))
        self.assertEqual(50.0, combat_rules.range_piercing(shot, 700.0))
        self.assertEqual(0.25, combat_rules.range_piercing(shot, 899.0))
        self.assertEqual(0.0, combat_rules.range_piercing(shot, 900.0))

    def test_max_distance_is_a_hard_zero_not_the_p500_endpoint(self):
        shot = _shot(piercing=(200.0, 100.0), maximum=350.0)

        # A short lifetime does not change the P100/P500 slope before cutoff.
        self.assertEqual(150.0, combat_rules.range_piercing(shot, 300.0))
        self.assertAlmostEqual(
            137.50025, combat_rules.range_piercing(shot, 349.999),
            places=5)
        self.assertEqual(0.0, combat_rules.range_piercing(shot, 350.0))
        self.assertEqual(0.0, combat_rules.range_piercing(shot, 500.0))

    def test_penetration_uses_the_same_range_cutoff(self):
        result = combat_rules.penetration(
            _shot(piercing=(200.0, 100.0), maximum=500.0),
            500.0, 1.0, 1.0,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(1, result[0])
        self.assertEqual(0.0, result[2])

    def test_nominal_piercing_after_obstacles_has_no_random_roll(self):
        shot = _shot(piercing=(100.0, 50.0), maximum=720.0)

        self.assertEqual(
            75.0, combat_rules.nominal_piercing_after_loss(
                shot, 100.0, 25.0))
        self.assertEqual(
            0.0, combat_rules.nominal_piercing_after_loss(
                shot, 500.0, 50.0))

    def test_one_penetration_factor_is_reused_across_range_and_vehicle(self):
        draws = []

        def low_roll(low, high):
            draws.append((low, high))
            return 0.75

        factor = combat_rules.sample_penetration_factor(low_roll)
        self.assertEqual(0.75, factor)
        self.assertEqual([(0.75, 1.25)], draws)
        self.assertEqual(
            30.0, combat_rules.sampled_piercing(
                _shot(piercing=(40.0, 40.0)), 10.0, factor, 0.0))

        hull = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=1.0)
        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(40.0, 40.0)), 10.0,
            (types.SimpleNamespace(
                dist=10.0, hitAngleCos=1.0, matInfo=hull,
                compName='vehicleHull'),),
            pierce_loss=5.0, penetration_factor=factor,
            random_uniform=lambda unused_low, unused_high: self.fail(
                'vehicle resolution must not draw penetration again'))

        self.assertEqual(2, result[0])
        self.assertEqual(25.0, result[2])

    def test_hull_resolver_draws_one_factor_for_every_layer(self):
        draws = []

        def one_roll(low, high):
            draws.append((low, high))
            return 1.0

        screen = _material(10.0, 0.0)
        hull = _material(50.0)
        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(100.0, 100.0)), 50.0,
            (_collision(5.0, 1.0, screen, 'vehicleChassis'),
             _collision(5.2, 1.0, hull)),
            random_uniform=one_roll)

        self.assertEqual(2, result[0])
        self.assertEqual([(0.75, 1.25)], draws)

    def test_two_caliber_normalization_has_an_exact_boundary(self):
        armor = 60.0
        angle = math.radians(60.0)
        for kind, base_degrees in (
                ('ARMOR_PIERCING', 5.0),
                ('ARMOR_PIERCING_CR', 2.0)):
            with self.subTest(kind=kind, boundary='exact'):
                exact = combat_rules.penetration(
                    _shot(kind=kind, caliber=120.0,
                          piercing=(1000.0, 1000.0)),
                    50.0, armor, math.cos(angle), penetration_factor=1.0)
                expected = armor / math.cos(
                    angle - math.radians(base_degrees))
                self.assertAlmostEqual(expected, exact[1], places=8)
            with self.subTest(kind=kind, boundary='above'):
                caliber = 120.001
                above = combat_rules.penetration(
                    _shot(kind=kind, caliber=caliber,
                          piercing=(1000.0, 1000.0)),
                    50.0, armor, math.cos(angle), penetration_factor=1.0)
                normalized = (math.radians(base_degrees) * 1.4 *
                              caliber / (2.0 * armor))
                expected = armor / math.cos(angle - normalized)
                self.assertAlmostEqual(expected, above[1], places=8)

    def test_three_caliber_no_ricochet_is_strictly_greater(self):
        angle_cos = math.cos(math.radians(75.0))
        for kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR'):
            with self.subTest(kind=kind, boundary='exact'):
                exact = combat_rules.penetration(
                    _shot(kind=kind, caliber=180.0,
                          piercing=(1000.0, 1000.0)),
                    50.0, 60.0, angle_cos, penetration_factor=1.0)
                self.assertEqual(0, exact[0])
            with self.subTest(kind=kind, boundary='above'):
                above = combat_rules.penetration(
                    _shot(kind=kind, caliber=180.001,
                          piercing=(1000.0, 1000.0)),
                    50.0, 60.0, angle_cos, penetration_factor=1.0)
                self.assertNotEqual(0, above[0])

    def test_ap_and_apcr_ricochet_at_exactly_70_degrees(self):
        for kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR'):
            with self.subTest(kind=kind, boundary='below'):
                below = combat_rules.penetration(
                    _shot(kind=kind, caliber=90.0,
                          piercing=(1000.0, 1000.0)),
                    50.0, 60.0, math.cos(math.radians(69.999)),
                    penetration_factor=1.0)
                self.assertNotEqual(0, below[0])
            with self.subTest(kind=kind, boundary='exact'):
                exact = combat_rules.penetration(
                    _shot(kind=kind, caliber=90.0,
                          piercing=(1000.0, 1000.0)),
                    50.0, 60.0, math.cos(math.radians(70.0)),
                    penetration_factor=1.0)
                self.assertEqual(0, exact[0])

    def test_aphe_has_no_normalization_ricochet_or_caliber_rules(self):
        angle = math.radians(75.0)
        result = combat_rules.penetration(
            _shot(kind='ARMOR_PIERCING_HE', caliber=180.0,
                  piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(angle), penetration_factor=1.0)

        self.assertEqual(2, result[0])
        self.assertAlmostEqual(60.0 / math.cos(angle), result[1], places=8)

    def test_heat_does_not_inherit_ap_ricochet_rule(self):
        result = combat_rules.penetration(
            _shot(kind='HOLLOW_CHARGE', piercing=(400.0, 400.0)),
            50.0, 60.0, 0.30,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(2, result[0])

    def test_heat_ricochet_boundary_is_85_degrees(self):
        shot = _shot(
            kind='HOLLOW_CHARGE', caliber=1000.0,
            piercing=(2000.0, 2000.0))
        below = combat_rules.penetration(
            shot, 50.0, 60.0, math.cos(math.radians(84.999)),
            penetration_factor=1.0)
        exact = combat_rules.penetration(
            shot, 50.0, 60.0, math.cos(math.radians(85.0)),
            penetration_factor=1.0)

        self.assertEqual(2, below[0])
        # HEAT never receives the AP/APCR three-calibre exemption.
        self.assertEqual(0, exact[0])

    def test_material_may_ricochet_can_disable_auto_bounce(self):
        material = _material(60.0, mayRicochet=False)
        result = combat_rules.penetration(
            _shot(caliber=90.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(math.radians(75.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(2, result[0])

    def test_material_use_hit_angle_makes_the_plate_nominal(self):
        material = _material(60.0, useHitAngle=False)
        result = combat_rules.penetration(
            _shot(caliber=90.0, piercing=(100.0, 100.0)),
            50.0, 60.0, math.cos(math.radians(89.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(2, result[0])
        self.assertEqual(60.0, result[1])

    def test_material_can_disable_three_caliber_ricochet_check(self):
        material = _material(
            60.0, checkCaliberForRicochet=False)
        result = combat_rules.penetration(
            _shot(caliber=200.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(math.radians(75.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(0, result[0])

    def test_exact_1513_richet_field_typo_is_honored(self):
        material = _material(60.0, checkCaliberForRichet=False)
        result = combat_rules.penetration(
            _shot(caliber=200.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(math.radians(75.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(0, result[0])

    def test_material_can_disable_two_caliber_normalization(self):
        material = _material(
            60.0, checkCaliberForHitAngleNorm=False)
        angle = math.radians(60.0)
        result = combat_rules.penetration(
            _shot(caliber=150.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(angle),
            penetration_factor=1.0, material=material)

        expected = 60.0 / math.cos(angle - math.radians(5.0))
        self.assertAlmostEqual(expected, result[1], places=8)

    def test_he_non_penetration_uses_082_direct_blast_damage(self):
        shot = _shot(kind='HIGH_EXPLOSIVE', damage=400.0)

        value = combat_rules.damage(
            shot, 1, 100.0,
            random_uniform=lambda low, high: (low + high) * 0.5)

        self.assertEqual(89, value)

    def test_ap_damage_roll_stays_within_twenty_five_percent(self):
        shot = _shot(kind='ARMOR_PIERCING_CR', damage=400.0)

        low = combat_rules.damage(
            shot, 2, 100.0,
            random_uniform=lambda minimum, unused_maximum: minimum)
        high = combat_rules.damage(
            shot, 2, 100.0,
            random_uniform=lambda unused_minimum, maximum: maximum)

        self.assertEqual(300, low)
        self.assertEqual(500, high)

    def test_every_solid_shell_kind_uses_armor_damage_not_module_damage(self):
        for kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_HE',
                     'ARMOR_PIERCING_CR', 'HOLLOW_CHARGE'):
            with self.subTest(kind=kind):
                shot = _shot(kind=kind, damage=400.0)
                # Exact #1513 stores shell damage as (vehicle HP, module HP).
                # A 165 module roll must never scale a penetrating hull hit.
                shot.shell.damage = (400.0, 165.0)
                low = combat_rules.damage(
                    shot, 2, 100.0,
                    random_uniform=lambda minimum, unused_maximum: minimum)
                high = combat_rules.damage(
                    shot, 2, 100.0,
                    random_uniform=lambda unused_minimum, maximum: maximum)

                self.assertEqual(300, low)
                self.assertEqual(500, high)

    def test_spaced_armour_is_paid_before_structural_plate(self):
        track = _material(20.0, 0.0)
        hull = _material(100.0)
        collisions = (
            _collision(5.0, 0.5, track, 'vehicleChassis'),
            _collision(5.2, 1.0, hull),
        )

        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(120.0, 120.0)), 50.0, collisions,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(1, result[0])
        # The 90 mm shell triggers the two-calibre normalization rule on the
        # 20 mm external plate before that effective thickness is deducted.
        normalization = math.radians(5.0) * 1.4 * 90.0 / 40.0
        expected = 20.0 / math.cos(math.radians(60.0) - normalization)
        self.assertAlmostEqual(expected, result[3], places=8)

    def test_external_plate_must_itself_be_penetrated(self):
        screen = _material(30.0, 0.0)
        hull = _material(10.0)

        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(20.0, 20.0)), 50.0,
            (_collision(5.0, 1.0, screen, 'vehicleChassis'),
             _collision(5.2, 1.0, hull)),
            penetration_factor=1.0)

        self.assertIsNone(result)

    def test_collide_once_only_deducts_one_copy_of_the_same_plate(self):
        once_entry = _material(
            20.0, 0.0, kind=7, collideOnceOnly=True)
        once_exit = _material(
            20.0, 0.0, kind=7, collideOnceOnly=True)
        repeated = _material(20.0, 0.0, collideOnceOnly=False)
        hull = _material(80.0)

        def resolve(first, second):
            return combat_rules.resolve_hull_hit(
                _shot(piercing=(110.0, 110.0)), 50.0,
                (_collision(5.0, 1.0, first, 'vehicleChassis'),
                 _collision(5.1, 1.0, second, 'vehicleChassis'),
                 _collision(5.2, 1.0, hull)),
                penetration_factor=1.0)

        once_result = resolve(once_entry, once_exit)
        repeated_result = resolve(repeated, repeated)

        self.assertEqual(2, once_result[0])
        self.assertEqual(20.0, once_result[3])
        self.assertEqual(1, repeated_result[0])
        self.assertEqual(40.0, repeated_result[3])

    def test_destructible_loss_accumulates_before_vehicle_spaced_armour(self):
        track = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(armor=100.0, vehicleDamageFactor=1.0)
        collisions = (
            types.SimpleNamespace(
                dist=5.0, hitAngleCos=1.0, matInfo=track,
                compName='vehicleChassis'),
            types.SimpleNamespace(
                dist=5.2, hitAngleCos=1.0, matInfo=hull,
                compName='vehicleHull'),
        )

        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(160.0, 160.0)), 50.0, collisions,
            pierce_loss=50.0,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(1, result[0])
        self.assertEqual(70.0, result[3])
        self.assertEqual(90.0, result[2])

    def test_destructible_penetration_loss_does_not_reduce_damage(self):
        hull = types.SimpleNamespace(armor=50.0, vehicleDamageFactor=1.0)
        collisions = (types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0, matInfo=hull,
            compName='vehicleHull'),)
        shot = _shot(piercing=(160.0, 160.0), damage=240.0)
        clear = combat_rules.resolve_hull_hit(
            shot, 50.0, collisions,
            random_uniform=lambda unused_low, unused_high: 1.0)
        crossed = combat_rules.resolve_hull_hit(
            shot, 50.0, collisions,
            random_uniform=lambda unused_low, unused_high: 1.0,
            pierce_loss=25.0)

        self.assertEqual(2, clear[0])
        self.assertEqual(2, crossed[0])
        self.assertEqual(
            combat_rules.damage(
                shot, clear[0], 50.0,
                random_uniform=lambda unused_low, unused_high: 1.0),
            combat_rules.damage(
                shot, crossed[0], 50.0,
                random_uniform=lambda unused_low, unused_high: 1.0))

    def test_collision_adapter_rejects_incomplete_1513_result(self):
        collision = types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=100.0))

        with self.assertRaises(AttributeError):
            combat_rules.collision_layers((collision,))

    def test_heat_penetrates_external_plate_then_reaches_structure(self):
        track = _material(20.0, 0.0)
        hull = _material(100.0)

        result = combat_rules.resolve_hull_hit(
            _shot(kind='HOLLOW_CHARGE', piercing=(400.0, 400.0)),
            50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(5.2, 1.0, hull)),
            penetration_factor=1.0)

        self.assertEqual(2, result[0])
        # After the 20 mm screen, 380 mm remains. The native jet starts behind
        # that nominal 20 mm, so its 18 cm air gap costs 9% of 380 mm.
        self.assertAlmostEqual(54.2, result[3], places=8)
        self.assertAlmostEqual(345.8, result[2], places=8)

    def test_heat_must_penetrate_the_external_plate(self):
        track = _material(20.0, 0.0)
        hull = _material(1.0)

        result = combat_rules.resolve_hull_hit(
            _shot(kind='HOLLOW_CHARGE', piercing=(19.999, 19.999)),
            50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(5.1, 1.0, hull)),
            penetration_factor=1.0)

        self.assertIsNone(result)

    def test_heat_gap_halves_current_jet_penetration_per_metre(self):
        track = _material(20.0, 0.0)
        hull = _material(95.0)
        shot = _shot(kind='HOLLOW_CHARGE', piercing=(200.0, 200.0))

        short_gap = combat_rules.resolve_hull_hit(
            shot, 50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(5.82, 1.0, hull)),
            penetration_factor=1.0)
        one_metre = combat_rules.resolve_hull_hit(
            shot, 50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(6.02, 1.0, hull)),
            penetration_factor=1.0)

        self.assertEqual(2, short_gap[0])
        self.assertAlmostEqual(92.0, short_gap[3], places=8)
        self.assertAlmostEqual(108.0, short_gap[2], places=8)
        self.assertEqual(1, one_metre[0])
        self.assertAlmostEqual(110.0, one_metre[3], places=8)
        self.assertAlmostEqual(90.0, one_metre[2], places=8)

    def test_heat_jet_does_not_ricochet_after_the_external_plate(self):
        track = _material(20.0, 0.0)
        hull = _material(60.0)

        result = combat_rules.resolve_hull_hit(
            _shot(kind='HOLLOW_CHARGE', caliber=10.0,
                  piercing=(2000.0, 2000.0)),
            50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(5.2, math.cos(math.radians(85.0)), hull)),
            penetration_factor=1.0)

        self.assertEqual(2, result[0])

    def test_he_uses_first_structural_nominal_armour(self):
        track = types.SimpleNamespace(armor=40.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(armor=75.0, vehicleDamageFactor=1.0)

        armor = combat_rules.he_nominal_armor((
            types.SimpleNamespace(
                dist=2.0, hitAngleCos=1.0, matInfo=track,
                compName='vehicleChassis'),
            types.SimpleNamespace(
                dist=2.5, hitAngleCos=0.5, matInfo=hull,
                compName='vehicleHull'),
        ))

        self.assertEqual(75.0, armor)

    def test_he_splash_preserves_082_distance_and_armour_reduction(self):
        shot = _shot(
            kind='HIGH_EXPLOSIVE', damage=400.0,
            explosion_radius=10.0)

        value = combat_rules.he_splash_damage(
            shot, 50.0, 0.5,
            random_uniform=lambda low, high: (low + high) * 0.5)

        self.assertTrue(combat_rules.is_he(shot))
        self.assertEqual(10.0, combat_rules.he_radius(shot))
        self.assertEqual(44, value)

    def test_he_hull_armor_reads_native_1513_component_attributes(self):
        material = types.SimpleNamespace(
            armor=35.0, vehicleDamageFactor=1.0)

        class Hull(object):
            materials = {'armor': material}

            def get(self, *unused_args, **unused_kwargs):
                raise AssertionError('Operation is not allowed')

        descriptor = types.SimpleNamespace(hull=Hull())

        self.assertEqual(35.0, combat_rules._offh_he_hull_armor(descriptor))


if __name__ == '__main__':
    unittest.main()
