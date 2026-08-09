from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import critical_damage
from gui.mods.offline_lan_0922 import device_damage


class _Extra(object):

    def __init__(self, name):
        self.name = name


class _Material(object):

    def __init__(self, name, chance=1.0):
        self.extra = _Extra(name)
        self.armor = 20.0
        self.vehicleDamageFactor = 0.0
        self.chanceToHitByProjectile = chance
        self.chanceToHitByExplosion = chance


class _Strict1513Component(object):

    def __init__(self, **values):
        self.__dict__.update(values)

    def get(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')


def _strict_1513_descriptor():
    health = lambda: _Strict1513Component(
        maxHealth=100, maxRegenHealth=50)
    return types.SimpleNamespace(
        chassis=health(),
        engine=_Strict1513Component(
            maxHealth=100, maxRegenHealth=50,
            fireStartingChance=0.12),
        hull=_Strict1513Component(ammoBayHealth=health()),
        fuelTank=health(), radio=health(), gun=health(),
        turret=_Strict1513Component(
            turretRotatorHealth=health(),
            surveyingDeviceHealth=health()),
        miscAttrs=_Strict1513Component(engineHealthFactor=1.5),
        type=types.SimpleNamespace(
            crewRoles=(('commander',), ('driver',), ('loader',))))


def _descriptor():
    return types.SimpleNamespace(
        chassis={'maxHealth': 100, 'maxRegenHealth': 50},
        engine={'maxHealth': 100, 'maxRegenHealth': 50,
                'fireStartingChance': 0.0},
        hull={'ammoBayHealth': {'maxHealth': 100,
                               'maxRegenHealth': 50}},
        fuelTank={'maxHealth': 100, 'maxRegenHealth': 50},
        radio={'maxHealth': 100, 'maxRegenHealth': 50},
        gun={'maxHealth': 100, 'maxRegenHealth': 50},
        turret={
            'turretRotatorHealth': {'maxHealth': 100,
                                   'maxRegenHealth': 50},
            'surveyingDeviceHealth': {'maxHealth': 100,
                                      'maxRegenHealth': 50}},
        miscAttrs={},
        type=types.SimpleNamespace(
            crewRoles=(('commander',), ('driver',), ('loader',))))


class CriticalDamageTests(unittest.TestCase):

    def setUp(self):
        self.player = types.SimpleNamespace(
            playerVehicleID=999,
            arena=types.SimpleNamespace(onVehicleKilled=lambda *args: None),
            vehicleTypeDescriptor=_descriptor())
        self.bigworld = types.ModuleType('BigWorld')
        self.bigworld.player = lambda: self.player
        self.bigworld.time = lambda: 12.0
        self.math = types.ModuleType('Math')

    def test_native_1513_components_never_call_forbidden_legacy_get(self):
        descriptor = _strict_1513_descriptor()

        self.assertEqual(150, device_damage.device_max_hp(
            descriptor, 'engineHealth'))
        for name in (
                'ammoBayHealth', 'fuelTankHealth', 'radioHealth',
                'leftTrackHealth', 'rightTrackHealth', 'gunHealth',
                'turretRotatorHealth', 'surveyingDeviceHealth'):
            self.assertEqual(100, device_damage.device_max_hp(
                descriptor, name))

        vehicle = types.SimpleNamespace(
            typeDescriptor=descriptor, health=0,
            devices_hp={}, _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=False)
        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.apply_death(vehicle, 'shot')
        self.assertIsNotNone(payload)
        self.assertEqual(
            set(critical_damage._OFFH_DEATH_DEVICES),
            vehicle._destroyed_devices)

    def test_critical_descriptor_adapter_uses_native_attributes(self):
        component = _Strict1513Component(
            itemTypeName='vehicleTurret', fireStartingChance=0.12)

        self.assertEqual(
            'vehicleTurret',
            critical_damage._descriptor_value(component, 'itemTypeName'))
        self.assertEqual(
            0.12,
            critical_damage._descriptor_value(
                component, 'fireStartingChance'))

    def test_external_track_uses_copied_082_crit_loop(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor())
        collision = (1.0, 1.0, _Material('leftTrackHealth'), None)
        shell = {'damage': (100.0, 120.0)}

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=120.0), \
                mock.patch('random.random', return_value=0.0):
            damage, payload = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0, shell,
                attacker_id=2, penetrated=False)

        self.assertEqual(0, damage)
        self.assertEqual(0.0, vehicle.devices_hp['leftTrackHealth'])
        self.assertIn('leftTrackHealth', vehicle._destroyed_devices)
        self.assertEqual('destroyed', payload['devices'][0]['state'])
        self.assertEqual(
            [{'kind': 'device', 'name': 'leftTrackHealth',
              'old_state': 'normal', 'state': 'destroyed',
              'cause': 'shot'}],
            payload['events'])

    def test_critical_proposal_does_not_mutate_live_vehicle(self):
        self.player.playerVehicleID = 999
        self.player.arena.onVehicleKilled = mock.Mock()
        vehicle = types.SimpleNamespace(
            id=999, health=500, typeDescriptor=_descriptor(),
            position=object(), matrix=object(),
            devices_hp={'ammoBayHealth': 100.0},
            _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=False, getComponents=lambda: ())
        collision = (1.0, 1.0, _Material('ammoBayHealth'), None)
        shell = {'damage': (100.0, 120.0)}

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=120.0), \
                mock.patch('random.random', return_value=0.0):
            damage, payload = critical_damage.propose_direct(
                vehicle, (collision,), object(), object(), 0, shell,
                attacker_id=2, penetrated=False)

        self.assertEqual(510, damage)
        self.assertEqual({'ammoBayHealth': 100.0}, vehicle.devices_hp)
        self.assertEqual(set(), vehicle._destroyed_devices)
        self.assertFalse(hasattr(vehicle, '_ammo_rack_death'))
        self.player.arena.onVehicleKilled.assert_not_called()
        self.assertTrue(payload['ammo_rack_death'])
        self.assertEqual('ammo_rack', payload['events'][-1]['kind'])

    def test_payload_is_installed_without_reroll(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500)
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'],
            'crew_ko': ['driver'],
            'fire': True,
            'ammo_rack_death': False,
            'events': [{'kind': 'device', 'name': 'engineHealth',
                        'state': 'destroyed'}],
        }

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            events = critical_damage.apply_payload(vehicle, payload)

        self.assertEqual(0.0, vehicle.devices_hp['engineHealth'])
        self.assertTrue(vehicle.is_engine_dead)
        self.assertEqual(set(['driver']), vehicle._crew_ko)
        self.assertTrue(vehicle.is_on_fire)
        self.assertIsNotNone(vehicle._fire_started)
        self.assertEqual(tuple(payload['events']), events)

    def test_eventless_snapshot_derives_missed_damage_transitions(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500)
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'], 'crew_ko': ['driver'],
            'fire': True, 'ammo_rack_death': True, 'events': []}

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            events = critical_damage.apply_payload(vehicle, payload)

        self.assertEqual(
            set([('device', 'destroyed'), ('crew', 'destroyed'),
                 ('fire', True), ('ammo_rack', 'destroyed')]),
            set((event['kind'], event['state']) for event in events))
        self.assertTrue(all(event['cause'] == 'shot' for event in events))

    def test_fire_uses_one_second_five_percent_tick_and_burns_out(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500, maxHealth=500,
            devices_hp={'fuelTankHealth': 0.0},
            _destroyed_devices=set(['fuelTankHealth']), _crew_ko=set(),
            is_on_fire=True, _fire_started=0.0, _fire_timer=0.0)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            damage, payload = critical_damage.tick_fire(
                vehicle, 1.0, now=1.0)
        self.assertEqual(25, damage)
        self.assertIsNone(payload)

        vehicle._fire_timer = 0.9
        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            damage, payload = critical_damage.tick_fire(
                vehicle, 0.1, now=10.0)
        self.assertEqual(25, damage)
        self.assertFalse(vehicle.is_on_fire)
        self.assertEqual(50.0, vehicle.devices_hp['fuelTankHealth'])
        self.assertNotIn('fuelTankHealth', vehicle._destroyed_devices)
        self.assertEqual('repair', payload['events'][0]['cause'])
        self.assertEqual('critical', payload['events'][0]['state'])
        self.assertEqual('fire', payload['events'][1]['kind'])

    def test_drowning_knocks_out_all_modules_and_real_crew_roster(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={}, _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=False)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.apply_drowning(vehicle)

        self.assertEqual(
            set(critical_damage._OFFH_DEATH_DEVICES),
            vehicle._destroyed_devices)
        self.assertEqual(
            set(['commander', 'driver', 'loader1']), vehicle._crew_ko)
        self.assertTrue(all(
            event.get('cause') == 'drowning'
            for event in payload['events']))

    def test_ordinary_death_extinguishes_fire_and_knocks_out_everything(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=0,
            devices_hp={'fuelTankHealth': 0.0},
            _destroyed_devices=set(['fuelTankHealth']), _crew_ko=set(),
            is_on_fire=True, _fire_started=1.0)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.apply_death(vehicle, 'fire')

        self.assertFalse(vehicle.is_on_fire)
        self.assertEqual(
            set(critical_damage._OFFH_DEATH_DEVICES),
            vehicle._destroyed_devices)
        self.assertEqual(
            set(['commander', 'driver', 'loader1']), vehicle._crew_ko)
        self.assertIn(
            {'kind': 'fire', 'state': False, 'cause': 'fire'},
            payload['events'])

    def test_destroyed_track_repairs_to_descriptor_regen_cap(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={'leftTrackHealth': 0.0},
            _destroyed_devices=set(['leftTrackHealth']),
            _crew_ko=set(), is_on_fire=False)

        payload = critical_damage.tick_repair(
            vehicle, 10.0, repair_skill=0.0)

        self.assertEqual(50.0, vehicle.devices_hp['leftTrackHealth'])
        self.assertNotIn('leftTrackHealth', vehicle._destroyed_devices)
        self.assertEqual('critical', payload['devices'][0]['state'])
        self.assertEqual('destroyed', payload['events'][0]['old_state'])
        self.assertEqual('critical', payload['events'][0]['state'])

    def test_extinguisher_uses_copied_fire_stop_transition(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={}, _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=True, _fire_started=1.0, _fire_timer=0.5)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.use_extinguisher(vehicle)

        self.assertFalse(vehicle.is_on_fire)
        self.assertIsNone(vehicle._fire_started)
        self.assertEqual(
            [{'kind': 'fire', 'state': False, 'cause': 'repair'}],
            payload['events'])

    def test_small_repair_kit_restores_only_selected_device(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={'engineHealth': 0.0, 'gunHealth': 0.0},
            _destroyed_devices=set(['engineHealth', 'gunHealth']),
            _crew_ko=set(), is_on_fire=False)

        payload = critical_damage.repair_device(vehicle, 'engine')

        self.assertEqual(100.0, vehicle.devices_hp['engineHealth'])
        self.assertEqual(0.0, vehicle.devices_hp['gunHealth'])
        self.assertNotIn('engineHealth', vehicle._destroyed_devices)
        self.assertIn('gunHealth', vehicle._destroyed_devices)
        self.assertEqual(
            [('engineHealth', 'normal', 'repair')],
            [(event['name'], event['state'], event['cause'])
             for event in payload['events']])

    def test_small_med_kit_restores_only_selected_crew_member(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={}, _destroyed_devices=set(),
            _crew_ko=set(['driver', 'loader1']), is_on_fire=False)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.restore_crew(vehicle, 'driver')

        self.assertNotIn('driver', vehicle._crew_ko)
        self.assertIn('loader1', vehicle._crew_ko)
        self.assertEqual(
            [('driver', 'normal', 'repair')],
            [(event['name'], event['state'], event['cause'])
             for event in payload['events']])


if __name__ == '__main__':
    unittest.main()
