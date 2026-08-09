from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import combat_rules


def _shot(kind='ARMOR_PIERCING', caliber=90.0,
          piercing=(160.0, 120.0), maximum=500.0, damage=240.0,
          explosion_radius=0.0):
    shell = types.SimpleNamespace(
        kind=kind, caliber=caliber, damage=(damage,),
        explosionRadius=explosion_radius)
    return types.SimpleNamespace(
        shell=shell, piercingPower=piercing, maxDistance=maximum)


class CombatRulesTests(unittest.TestCase):

    def test_1513_gun_shot_owns_penetration_and_range(self):
        shot = _shot(piercing=(200.0, 100.0), maximum=500.0)

        near = combat_rules.penetration(
            shot, 100.0, 0.0, 1.0,
            random_uniform=lambda unused_low, unused_high: 1.0)
        far = combat_rules.penetration(
            shot, 500.0, 0.0, 1.0,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(200.0, near[2])
        self.assertEqual(100.0, far[2])

    def test_ap_ricochet_and_three_caliber_overmatch_match_082(self):
        grazing = 0.30
        ordinary = combat_rules.penetration(
            _shot(caliber=90.0), 50.0, 60.0, grazing,
            random_uniform=lambda unused_low, unused_high: 1.0)
        overmatch = combat_rules.penetration(
            _shot(caliber=190.0), 50.0, 60.0, grazing,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(0, ordinary[0])
        self.assertNotEqual(0, overmatch[0])

    def test_heat_does_not_inherit_ap_ricochet_rule(self):
        result = combat_rules.penetration(
            _shot(kind='HOLLOW_CHARGE', piercing=(400.0, 400.0)),
            50.0, 60.0, 0.30,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(2, result[0])

    def test_he_non_penetration_uses_082_direct_blast_damage(self):
        shot = _shot(kind='HIGH_EXPLOSIVE', damage=400.0)

        value = combat_rules.damage(
            shot, 1, 100.0,
            random_uniform=lambda low, high: (low + high) * 0.5)

        self.assertEqual(89, value)

    def test_spaced_armour_is_paid_before_structural_plate(self):
        track = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(armor=100.0, vehicleDamageFactor=1.0)
        collisions = (
            types.SimpleNamespace(dist=5.0, hitAngleCos=0.5, matInfo=track),
            types.SimpleNamespace(dist=5.2, hitAngleCos=1.0, matInfo=hull),
        )

        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(130.0, 130.0)), 50.0, collisions,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(1, result[0])
        self.assertEqual(40.0, result[3])

    def test_heat_stops_on_first_spaced_plate(self):
        track = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=1.0)

        result = combat_rules.resolve_hull_hit(
            _shot(kind='HOLLOW_CHARGE', piercing=(400.0, 400.0)),
            50.0, (
                types.SimpleNamespace(
                    dist=5.0, hitAngleCos=1.0, matInfo=track),
                types.SimpleNamespace(
                    dist=5.2, hitAngleCos=1.0, matInfo=hull),
            ), random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertIsNone(result)

    def test_he_uses_first_structural_nominal_armour(self):
        track = types.SimpleNamespace(armor=40.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(armor=75.0, vehicleDamageFactor=1.0)

        armor = combat_rules.he_nominal_armor((
            types.SimpleNamespace(dist=2.0, hitAngleCos=1.0, matInfo=track),
            types.SimpleNamespace(dist=2.5, hitAngleCos=0.5, matInfo=hull),
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
