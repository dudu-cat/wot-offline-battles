import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load(name):
    for parent in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922',
                   'gui.mods.offline_lan_0922.account_rpc'):
        if parent not in sys.modules:
            module = types.ModuleType(parent)
            module.__path__ = [str(PACKAGE_ROOT)] if parent.endswith(
                'offline_lan_0922') else [str(PACKAGE_ROOT / 'account_rpc')]
            sys.modules[parent] = module
    sys.modules['gui.mods.offline_lan_0922'].__path__ = [str(PACKAGE_ROOT)]
    sys.modules['gui.mods.offline_lan_0922.account_rpc'].__path__ = [
        str(PACKAGE_ROOT / 'account_rpc')]
    full = 'gui.mods.offline_lan_0922.account_rpc.%s' % name
    sys.modules.pop(full, None)
    spec = importlib.util.spec_from_file_location(
        full, PACKAGE_ROOT / 'account_rpc' / ('%s.py' % name))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _request_modules():
    """Load the handlers and reuse the exact module graph they bound.

    Loading a dependency separately would hand the test a different
    ``GarageError`` class than the handler catches.
    """
    requests = _load('requests')
    return requests, requests.commands, requests.garage


SNAPSHOT = {
    'vehicles': [{
        'id': 9,
        'compDescr': b'veh:9',
        'crew': [101, 102],
        'tankmen': {101: b'tman:101', 102: b'tman:102'},
        'repair': (0, 100),
        'lock': (0, 0),
        'shells': [10010, 20, 10011, 10],
        'shellsLayout': {},
        'eqs': [0, 0, 0],
        'eqsLayout': [0, 0, 0],
        'inventoryItems': {
            2: {2002: 1}, 3: {2003: 1}, 4: {2004: 1},
            5: {2005: 1}, 6: {2006: 1}, 7: {2007: 1},
            10: {10010: 20, 10011: 10},
        },
        'vehicleTypeCompactDescr': 50001,
    }],
    # bootstrap's top-level catalogue covers every per-record item plus
    # the account-wide device and equipment stock.
    'inventoryItems': {
        2: {2002: 1}, 3: {2003: 1}, 4: {2004: 1},
        5: {2005: 1}, 6: {2006: 1}, 7: {2007: 1},
        9: {9001: 200, 9002: 200},
        10: {10010: 20, 10011: 10},
        11: {11001: 200},
    },
    'shopItemPrices': dict(
        (compact_descr, {'credits': 0, 'gold': 0})
        for compact_descr in (2002, 2003, 2004, 2005, 2006, 2007,
                              10010, 10011, 9001, 9002, 11001, 50001)),
    'unlockItemCompactDescrs': set(),
    'shopNationCount': 9,
    'customizationItemCount': 1,
}


class _Descriptor(object):
    def __init__(self, compact_descr):
        self.compact_descr = compact_descr
        self.devices = {}
        self.components = {}
        self.gun = 'gun'

    def installOptionalDevice(self, compact_descr, slot_index):
        if slot_index in self.devices:
            raise ValueError('slot is occupied')
        self.devices[slot_index] = compact_descr

    def removeOptionalDevice(self, slot_index):
        if slot_index not in self.devices:
            raise ValueError('slot is empty')
        del self.devices[slot_index]

    def installComponent(self, compact_descr, position_index):
        self.components[position_index] = compact_descr
        self.gun = 'gun:%d' % compact_descr

    def makeCompactDescr(self):
        return b'veh:9|dev=%s|comp=%s' % (
            repr(sorted(self.devices.items())).encode('ascii'),
            repr(sorted(self.components.items())).encode('ascii'))


class _TankmanDescriptor(object):
    def __init__(self, compact_descr):
        # The real TankmanDescr parses its skills out of the compact
        # descriptor, so the fake must round-trip them too.
        base, _, encoded = compact_descr.partition(b'|')
        self.compact_descr = base
        self.skills = [name.decode('ascii')
                       for name in encoded.split(b',') if name]

    def addSkill(self, name):
        if name in self.skills:
            raise ValueError('already learned')
        self.skills.append(name)

    def dropSkills(self, fraction, throw):
        self.skills = []

    def makeCompactDescr(self):
        return self.compact_descr + b'|' + ','.join(
            self.skills).encode('ascii')


def _modules():
    vehicles = types.SimpleNamespace(
        VehicleDescr=lambda compactDescr: _Descriptor(compactDescr),
        getDefaultAmmoForGun=lambda gun: [20010, 30, 20011, 15])
    tankmen = types.SimpleNamespace(
        TankmanDescr=_TankmanDescriptor,
        SKILL_NAMES=('repair', 'camouflage', 'brotherhood'))
    return vehicles, tankmen


class GarageStateTests(unittest.TestCase):

    def setUp(self):
        unused_requests, unused_commands, self.garage = _request_modules()
        vehicles, tankmen = _modules()
        self.state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)

    def _record(self):
        return self.state.snapshot()['vehicles'][0]

    def test_the_snapshot_is_copied_not_aliased(self):
        self.state.equip_equipments(9, [11001, 0, 0])

        self.assertEqual([0, 0, 0], SNAPSHOT['vehicles'][0]['eqs'])

    def test_mounting_consumables_fills_three_slots(self):
        self.state.equip_equipments(9, [11001])

        self.assertEqual([11001, 0, 0], self._record()['eqs'])
        self.assertEqual(
            1, self._record()['inventoryItems'][11][11001])

    def test_mounting_a_fourth_consumable_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.equip_equipments(9, [1, 2, 3, 4])

    def test_shell_counts_keep_the_inventory_and_pair_list_in_step(self):
        self.state.equip_shells(9, [10010, 5, 10011, 40])

        self.assertEqual([10010, 5, 10011, 40], self._record()['shells'])
        self.assertEqual(
            {10010: 5, 10011: 40},
            self._record()['inventoryItems'][10])

    def test_odd_shell_payload_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.equip_shells(9, [10010, 5, 10011])

    def test_mounting_an_optional_device_rebuilds_the_compact_descriptor(self):
        original = self._record()['compDescr']

        self.state.equip_optional_device(9, 9001, 0)

        self.assertNotEqual(original, self._record()['compDescr'])
        self.assertIn(b'9001', self._record()['compDescr'])
        self.assertEqual(1, self._record()['inventoryItems'][9][9001])

    def test_remounting_the_same_slot_replaces_the_device(self):
        self.state.equip_optional_device(9, 9001, 0)
        self.state.equip_optional_device(9, 9002, 0)

        descriptor = self._record()['compDescr']
        self.assertIn(b'9002', descriptor)
        self.assertNotIn(b'9001', descriptor)

    def test_clearing_a_slot_removes_the_device(self):
        self.state.equip_optional_device(9, 9001, 0)
        self.state.equip_optional_device(9, 0, 0)

        self.assertNotIn(b'9001', self._record()['compDescr'])

    def test_a_gun_swap_refills_the_default_ammunition(self):
        self.state.install_component(9, 4444, 0)

        self.assertIn(b'4444', self._record()['compDescr'])
        self.assertEqual([20010, 30, 20011, 15], self._record()['shells'])
        self.assertEqual(
            {20010: 30, 20011: 15},
            self._record()['inventoryItems'][10])

    def test_a_crew_skill_rebuilds_only_that_tankman(self):
        self.state.add_tankman_skill(101, 2)

        self.assertEqual(
            b'tman:101|brotherhood', self._record()['tankmen'][101])
        self.assertEqual(b'tman:102', self._record()['tankmen'][102])

    def test_an_unknown_skill_index_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.add_tankman_skill(101, 99)

    def test_an_unknown_tankman_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.add_tankman_skill(999, 0)

    def test_dropping_skills_clears_them(self):
        self.state.add_tankman_skill(101, 0)
        self.state.drop_tankman_skills(101)

        self.assertEqual(b'tman:101|', self._record()['tankmen'][101])

    def test_an_unknown_vehicle_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.equip_equipments(4242, [11001])

    def test_layouts_decode_shell_pairs_and_equipment_slots(self):
        self.state.set_layouts(9, [10010, 12], 0, [11001])

        self.assertEqual({10010: 12}, self._record()['shellsLayout'])
        self.assertEqual([11001, 0, 0], self._record()['eqsLayout'])


class FittingRequestTests(unittest.TestCase):

    def setUp(self):
        self.requests, self.commands, self.garage = _request_modules()
        vehicles, tankmen = _modules()
        self.pushed = []
        self.state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)
        self.context = {
            'selected_vehicle': copy.deepcopy(SNAPSHOT),
            'garage': self.state,
            'push_update': self.pushed.append,
        }

    def _dispatch(self, command, args):
        return self.requests.dispatch(command, self.context, args)

    def test_equip_eqs_decodes_the_exact_1513_payload(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_EQS, ([9, 11001, 0, 0],))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        result.before_response()
        self.assertEqual(1, len(self.pushed))
        self.assertEqual(
            [11001, 0, 0], self.pushed[0]['inventory'][1]['eqs'][9])

    def test_equip_optdev_skips_the_leading_shop_revision(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_OPTDEV, ([77, 9, 9001, 1, 0],))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertIn(
            b'9001', self.state.snapshot()['vehicles'][0]['compDescr'])

    def test_equip_shells_updates_the_published_inventory(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_SHELLS, ([9, 10010, 7],))
        result.before_response()

        self.assertEqual(
            [10010, 7], self.pushed[0]['inventory'][1]['shells'][9])
        self.assertEqual({10010: 7}, self.pushed[0]['inventory'][10])

    def test_add_skill_uses_the_int3_payload(self):
        result = self._dispatch(self.commands.CMD_TMAN_ADD_SKILL, (101, 0, 0))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            b'tman:101|repair',
            self.state.snapshot()['vehicles'][0]['tankmen'][101])

    def test_set_and_fill_layouts_decodes_both_counted_blocks(self):
        payload = [77, 9, 2, 10010, 12, 0, 3, 11001, 0, 0]

        result = self._dispatch(
            self.commands.CMD_SET_AND_FILL_LAYOUTS, (payload,))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        record = self.state.snapshot()['vehicles'][0]
        self.assertEqual({10010: 12}, record['shellsLayout'])
        self.assertEqual([11001, 0, 0], record['eqsLayout'])

    def test_a_refused_fitting_returns_failure_and_pushes_nothing(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_EQS, ([4242, 11001],))

        self.assertEqual(self.commands.RES_FAILURE, result.result_id)
        self.assertEqual([], self.pushed)

    def test_a_malformed_payload_never_reaches_the_garage(self):
        for command in (self.commands.CMD_EQUIP_EQS,
                        self.commands.CMD_EQUIP_SHELLS,
                        self.commands.CMD_EQUIP_OPTDEV,
                        self.commands.CMD_SET_AND_FILL_LAYOUTS):
            result = self._dispatch(command, ([],))
            self.assertEqual(
                self.commands.RES_FAILURE, result.result_id, command)
        self.assertEqual([], self.pushed)

    def test_the_context_snapshot_follows_the_mutation(self):
        self._dispatch(self.commands.CMD_EQUIP_EQS, ([9, 11001, 0, 0],))

        self.assertIs(
            self.state.snapshot(), self.context['selected_vehicle'])


if __name__ == '__main__':
    unittest.main()
