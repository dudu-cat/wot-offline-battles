import math
from pathlib import Path
import sys
import unittest
from unittest import mock
import types


ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOT = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
SERVER_ROOT = ROOT / '0.9.22' / 'server'
sys.path.insert(0, str(CLIENT_ROOT))
sys.path.insert(0, str(SERVER_ROOT))

from effective_params_fixture import effective_params
from gui.mods.offline_lan_0922 import effective_params as contract
from gui.mods.offline_lan_0922 import lan_session
from gui.mods.offline_lan_0922.lan_client import (
    CLIENT_CAPABILITIES, EFFECTIVE_PARAMS_CAPABILITY, LANClient)
from lan_battle_server import (
    BattleState, CLIENT_BUILD_0922, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY as SERVER_EFFECTIVE_PARAMS_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY, PLAYER_ENVIRONMENT_CAPABILITY,
    PLAYER_FIRE_INTENT_CAPABILITY, PROJECTILE_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY)


class _Connection(object):
    def sendall(self, payload):
        del payload


def _hello(params=None):
    return {
        'client_build': CLIENT_BUILD_0922,
        'capabilities': [
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            HUMAN_RAM_TIMELINE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
            PLAYER_FIRE_INTENT_CAPABILITY, PLAYER_ENVIRONMENT_CAPABILITY,
            SERVER_EFFECTIVE_PARAMS_CAPABILITY],
        'name': 'Player',
        'vehicle': 'ussr:R11_MS-1',
        'max_health': 90,
        'account_key': 'effective-params-test',
        'outfits': {},
        'vehicle_compact_descr': 'dGVzdA==',
        'effective_params': effective_params() if params is None else params,
    }


class EffectiveParamsContractTests(unittest.TestCase):
    def test_garage_builder_uses_exact_client_final_value_providers(self):
        expected = effective_params()
        descriptor = types.SimpleNamespace()
        descriptor.gun = types.SimpleNamespace(
            invisibilityFactorAtShot=0.4, clip=(2, 1.0), shots=tuple(
                types.SimpleNamespace(
                    speed=entry['source_shot']['speed'],
                    gravity=entry['source_shot']['gravity'],
                    maxDistance=entry['source_shot']['maxDistance'],
                    piercingPower=entry['source_shot']['piercingPower'],
                    shell=types.SimpleNamespace(
                        compactDescr=entry['compact_descr'],
                        **entry['source_shot']['shell']))
                for entry in expected['gun']['shots']))
        descriptor.computeBaseInvisibility = mock.Mock(
            return_value=(0.2, 0.3))
        crew = [types.SimpleNamespace(skills=[types.SimpleNamespace(
            name='gunner_sniper', isActive=True, level=100.0)])]
        consumables = types.SimpleNamespace(
            getInstalledItems=lambda: (types.SimpleNamespace(name='ration'),))
        item = types.SimpleNamespace(
            descriptor=descriptor,
            crew=crew,
            equipment=types.SimpleNamespace(regularConsumables=consumables),
            shells=(types.SimpleNamespace(intCD=2, count=10),
                    types.SimpleNamespace(intCD=1, count=20)),
            getBonusCamo=lambda: types.SimpleNamespace(id=7))
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(item=item)
        factors = {'exact': True}

        with mock.patch.dict(sys.modules, {'CurrentVehicle': current_vehicle}), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.attribute_factors',
                    return_value=factors) as attribute_factors, \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.crew_skill_names',
                    return_value=(('gunner_sniper',),)), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.modifiers',
                    return_value=expected['loadout']), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.spotting_profile',
                    return_value=expected['spotting']), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.crew_level_increase',
                    return_value=0.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.ramming_bonus',
                    return_value=0.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.intuition_chances',
                    return_value=1), \
                mock.patch(
                    'gui.mods.offline_lan_0922.vehicle_physics.derive_params',
                    return_value=expected['physics']) as derive_params, \
                mock.patch(
                    'gui.mods.offline_lan_0922.tank_collision.'
                    'descriptor_ram_profile',
                    return_value=expected['ramming']):
            result = lan_session._selected_vehicle_effective_params()

        attribute_factors.assert_called_once()
        self.assertIs(attribute_factors.call_args.args[0], descriptor)
        derive_params.assert_called_once_with(descriptor, factors)
        descriptor.computeBaseInvisibility.assert_called_once_with(0.57, 7)
        self.assertEqual([[1, 20], [2, 10]], result['ammo'])
        self.assertEqual(0.2, result['camouflage']['base_moving'])
        self.assertEqual(0.4, result['camouflage']['shot_factor'])
        self.assertTrue(result['skills']['deadeye'])
        self.assertEqual(1, result['skills']['intuition_chances'])
        self.assertEqual(2, result['gun']['clip_size'])
        self.assertEqual([1, 2], [
            shot['compact_descr'] for shot in result['gun']['shots']])
        self.assertTrue(all(
            shot['source_shot']['deadeye']
            for shot in result['gun']['shots']))

    def test_complete_snapshot_is_canonical_and_detached(self):
        source = effective_params()
        source['physics']['terrainResist'] = (1.1, 1.4, 2.6)

        result = contract.canonical(source)

        self.assertIsNotNone(result)
        self.assertEqual([1.1, 1.4, 2.6],
                         result['physics']['terrainResist'])
        source['loadout']['reload_factor'] = 99.0
        self.assertEqual(0.96, result['loadout']['reload_factor'])

    def test_schema_rejects_omission_non_finite_and_duplicate_ammo(self):
        missing = effective_params()
        del missing['skills']
        self.assertIsNone(contract.canonical(missing))

        non_finite = effective_params()
        non_finite['physics']['powerW'] = float('nan')
        self.assertIsNone(contract.canonical(non_finite))

        duplicate = effective_params()
        duplicate['ammo'] = [[1, 20], [1, 10]]
        self.assertIsNone(contract.canonical(duplicate))

        fallback_loadout = effective_params()
        fallback_loadout['loadout']['from_client_factors'] = False
        self.assertIsNone(contract.canonical(fallback_loadout))

        fallback_spotting = effective_params()
        fallback_spotting['spotting']['from_client_factors'] = False
        self.assertIsNone(contract.canonical(fallback_spotting))

        mismatched_deadeye = effective_params()
        mismatched_deadeye['gun']['shots'][0][
            'source_shot']['deadeye'] = True
        self.assertIsNone(contract.canonical(mismatched_deadeye))

        duplicate_shot = effective_params()
        duplicate_shot['gun']['shots'][1]['compact_descr'] = 1
        self.assertIsNone(contract.canonical(duplicate_shot))

    def test_player_hello_requires_and_publishes_snapshot(self):
        client = LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:R11_MS-1',
            max_health=90, account_key='account', outfits={},
            vehicle_compact_descr='dGVzdA==',
            effective_params=effective_params())

        hello = client._hello_payload()

        self.assertIn(EFFECTIVE_PARAMS_CAPABILITY, CLIENT_CAPABILITIES)
        self.assertEqual(
            effective_params()['loadout']['reload_factor'],
            hello['effective_params']['loadout']['reload_factor'])
        invalid = LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:R11_MS-1',
            max_health=90, account_key='account', outfits={},
            vehicle_compact_descr='dGVzdA==')
        with self.assertRaises(ValueError):
            invalid._hello_payload()

    def test_server_stores_updates_and_omits_snapshot_only_on_lean_rows(self):
        state = BattleState()
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 2000), _hello())
        self.assertIsNone(error)
        self.assertEqual(0.96,
                         player.effective_params['loadout']['reload_factor'])
        self.assertIn('effective_params', state._public_player(player))
        self.assertNotIn(
            'effective_params',
            state._public_player(player, include_outfits=False))

        updated = effective_params()
        updated['loadout']['reload_factor'] = 0.81
        self.assertTrue(state.select_vehicle(player.player_id, {
            'vehicle': 'ussr:R11_MS-1',
            'max_health': 90,
            'outfits': {},
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': updated,
        }))
        self.assertEqual(0.81,
                         player.effective_params['loadout']['reload_factor'])

    def test_server_rejects_invalid_snapshot_without_storing_player(self):
        state = BattleState()
        invalid = effective_params()
        invalid['camouflage']['shot_factor'] = math.inf

        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 2000), _hello(invalid))

        self.assertIsNone(player)
        self.assertEqual('invalid_effective_params', error)
        self.assertEqual({}, state.players)

    def test_start_gate_rechecks_capability_and_canonical_snapshot(self):
        state = BattleState()
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 2000), _hello())
        self.assertIsNone(error)

        player.capabilities = tuple(
            value for value in player.capabilities
            if value != SERVER_EFFECTIVE_PARAMS_CAPABILITY)
        message, error = state.request_start(player.player_id)
        self.assertIsNone(message)
        self.assertEqual('missing_effective_params_capability', error)

        player.capabilities += (SERVER_EFFECTIVE_PARAMS_CAPABILITY,)
        player.effective_params['loadout']['from_client_factors'] = False
        message, error = state.request_start(player.player_id)
        self.assertIsNone(message)
        self.assertEqual('invalid_effective_params', error)

    def test_lean_snapshot_inherits_canonical_static_parameters(self):
        client = LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:R11_MS-1',
            effective_params=effective_params())
        full = client._remember_player_outfits([{
            'id': 1, 'outfits': {},
            'effective_params': effective_params(),
        }])
        lean = client._remember_player_outfits([{'id': 1}])

        self.assertEqual(full[0]['effective_params'],
                         lean[0]['effective_params'])
        self.assertIsNot(full[0]['effective_params'],
                         lean[0]['effective_params'])


if __name__ == '__main__':
    unittest.main()
