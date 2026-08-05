import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = (ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' /
             'client' / 'gui' / 'mods' / 'offline_lan_0922' /
             'bootstrap.py')


class _Callbacks(object):
    def __init__(self):
        self.pending = []

    def callback(self, delay, function):
        self.pending.append((delay, function))
        return len(self.pending)

    def cancelCallback(self, callback_id):
        return None

    def run_next(self):
        unused_delay, function = self.pending.pop(0)
        function()


class _Compatibility(object):
    def __init__(self, events):
        self.events = events
        self.connect_calls = []

    def connect(self, show_lobby=False, account_context=None):
        self.events.append('connect')
        self.connect_calls.append((show_lobby, account_context))

    def is_ready(self):
        return False

    def fini(self):
        return None


class _AppLoader(object):
    def __init__(self, undefined):
        self.space_id = undefined
        self.lobby = types.SimpleNamespace(initialized=True)

    def getSpaceID(self):
        return self.space_id

    def getDefLobbyApp(self):
        return self.lobby


def _package(name):
    module = types.ModuleType(name)
    module.__path__ = []
    return module


class BootstrapLifecycleTests(unittest.TestCase):
    def _load(self):
        events = []
        callbacks = _Callbacks()
        spaces = types.SimpleNamespace(
            UNDEFINED=0, INTRO_VIDEO=1, LOGIN=2, LOBBY=3)
        app_loader = _AppLoader(spaces.UNDEFINED)
        compatibility = _Compatibility(events)

        bigworld = types.ModuleType('BigWorld')
        bigworld.callback = callbacks.callback
        bigworld.cancelCallback = callbacks.cancelCallback
        compat_module = types.ModuleType(
            'gui.mods.offline_lan_0922.compat')
        compat_module.g_compatibility = compatibility
        config_module = types.ModuleType(
            'gui.mods.offline_lan_0922.config')
        config_module.load = lambda: {
            'enabled': True, 'startupTimeoutSeconds': 30.0,
            'vehicle': 'ussr:R11_MS-1'}
        state_module = types.ModuleType(
            'gui.mods.offline_lan_0922.account_rpc.state')
        state_module.AccountState = object
        app_loader_module = _package('gui.app_loader')
        app_loader_module.g_appLoader = app_loader
        settings_module = types.ModuleType('gui.app_loader.settings')
        settings_module.GUI_GLOBAL_SPACE_ID = spaces

        def make_descriptor(nation_id, vehicle_type_id, base, tags=()):
            return types.SimpleNamespace(
                type=types.SimpleNamespace(
                    id=(nation_id, vehicle_type_id),
                    crewRoles=(('commander',), ('driver',)),
                    tags=frozenset(tags)),
                chassis=types.SimpleNamespace(compactDescr=base + 2),
                turret=types.SimpleNamespace(compactDescr=base + 3),
                gun=types.SimpleNamespace(compactDescr=base + 4),
                engine=types.SimpleNamespace(compactDescr=base + 5),
                fuelTank=types.SimpleNamespace(compactDescr=base + 6),
                radio=types.SimpleNamespace(compactDescr=base + 7),
                maxHealth=100 + vehicle_type_id,
                makeCompactDescr=lambda: (
                    'vehicle-%d-%d' %
                    (nation_id, vehicle_type_id)).encode('ascii'))

        descriptors = {
            (0, 11): make_descriptor(0, 11, 2000),
            (0, 12): make_descriptor(0, 12, 3000),
            (1, 7): make_descriptor(1, 7, 4000),
            (1, 8): make_descriptor(1, 8, 5000),
            (1, 9): make_descriptor(1, 9, 6000),
            (2, 1): make_descriptor(2, 1, 7000, ('event_battles',)),
            (2, 2): make_descriptor(2, 2, 8000, ('premiumIGR',)),
            (2, 3): make_descriptor(2, 3, 9000, ('observer',)),
        }
        delattr(descriptors[(1, 9)], 'gun')

        attempted_type_ids = []

        def vehicle_descr(**kwargs):
            if 'typeName' in kwargs:
                return descriptors[(0, 11)]
            type_id = tuple(kwargs['typeID'])
            attempted_type_ids.append(type_id)
            return descriptors[type_id]

        class _VehicleList(object):
            def getList(self, nation_id):
                return {
                    0: {11: object(), 12: object()},
                    1: {7: object(), 8: object(), 9: object()},
                    2: {1: object(), 2: object(), 3: object()},
                }.get(nation_id, {})

        customization = types.SimpleNamespace(
            paints={12001: types.SimpleNamespace(compactDescr=12001)},
            camouflages={12002: types.SimpleNamespace(compactDescr=12002)},
            decals={12003: types.SimpleNamespace(compactDescr=12003)},
            modifications={12004: types.SimpleNamespace(compactDescr=12004)},
            styles={12005: types.SimpleNamespace(compactDescr=12005)})
        crew_type_ids = []
        vehicles = types.SimpleNamespace(
            VehicleDescr=vehicle_descr,
            getDefaultAmmoForGun=lambda gun: [gun.compactDescr + 10000, 20],
            makeIntCompactDescrByID=lambda unused_name, nation_id, type_id: (
                90000 + nation_id * 1000 + type_id),
            g_list=_VehicleList(),
            attemptedTypeIDs=attempted_type_ids,
            crewTypeIDs=crew_type_ids,
            g_cache=types.SimpleNamespace(
                customization20=lambda: customization))

        def generate_tankmen(nation_id, vehicle_type_id, roles,
                             *unused_args):
            crew_type_ids.append((nation_id, vehicle_type_id))
            if (nation_id, vehicle_type_id) == (1, 8):
                raise ValueError('unloadable crew definition')
            return [
                ('%d:%d:%s' % (nation_id, vehicle_type_id, role[0])).encode(
                    'ascii')
                for role in roles]

        def tankman_descr(compact):
            nation_id, vehicle_type_id, role = compact.decode('ascii').split(
                ':')
            return types.SimpleNamespace(
                nationID=int(nation_id), vehicleTypeID=int(vehicle_type_id),
                role=role)

        tankmen = types.SimpleNamespace(
            MAX_SKILL_LEVEL=100,
            generateTankmen=generate_tankmen,
            TankmanDescr=tankman_descr)
        items = types.ModuleType('items')
        items.ITEM_TYPE_INDICES = {
            'vehicle': 1, 'vehicleChassis': 2, 'vehicleTurret': 3,
            'vehicleGun': 4, 'vehicleEngine': 5,
            'vehicleFuelTank': 6, 'vehicleRadio': 7, 'tankman': 8,
            'optionalDevice': 9, 'shell': 10, 'equipment': 11,
            'customization': 12,
        }
        items.tankmen = tankmen
        items.vehicles = vehicles
        nations = types.ModuleType('nations')
        nations.NAMES = tuple('nation-%d' % index for index in range(9))

        modules = {
            'BigWorld': bigworld,
            'gui': _package('gui'),
            'gui.mods': _package('gui.mods'),
            'gui.mods.offline_lan_0922': _package(
                'gui.mods.offline_lan_0922'),
            'gui.mods.offline_lan_0922.account_rpc': _package(
                'gui.mods.offline_lan_0922.account_rpc'),
            'gui.mods.offline_lan_0922.account_rpc.state': state_module,
            'gui.mods.offline_lan_0922.compat': compat_module,
            'gui.mods.offline_lan_0922.config': config_module,
            'gui.app_loader': app_loader_module,
            'gui.app_loader.settings': settings_module,
            'items': items,
            'nations': nations,
        }
        name = 'test_offline_lan_0922_bootstrap_lifecycle'
        spec = importlib.util.spec_from_file_location(name, BOOTSTRAP)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
        return (module, callbacks, compatibility, app_loader, spaces, events,
                modules)

    def test_selected_vehicle_snapshot_is_relationally_complete(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle(
                {'vehicle': 'ussr:R11_MS-1'})

        self.assertEqual([100001, 100002], selected['crew'])
        self.assertEqual(
            {100001: b'0:11:commander', 100002: b'0:11:driver'},
            selected['tankmen'])
        self.assertEqual((0, 111), selected['repair'])
        self.assertEqual((0, 0), selected['lock'])
        self.assertEqual([0, 0, 0], selected['eqs'])
        self.assertEqual([0, 0, 0], selected['eqsLayout'])
        self.assertEqual(set(range(2, 8)),
                         set(selected['inventoryItems']) - {10})
        required_prices = set()
        for item_type in tuple(range(2, 8)) + (10,):
            required_prices.update(selected['inventoryItems'][item_type])
        self.assertTrue(
            required_prices.issubset(selected['shopItemPrices']))
        self.assertEqual(9, selected['shopNationCount'])
        self.assertEqual(5, selected['customizationItemCount'])
        self.assertTrue(
            {12001, 12002, 12003, 12004, 12005}.issubset(
                selected['shopItemPrices']))
        for compact_descr in (12001, 12002, 12003, 12004, 12005):
            self.assertEqual(
                {'credits': 0}, selected['shopItemPrices'][compact_descr])
        self.assertEqual(3, len(selected['vehicles']))
        self.assertEqual([1, 2, 3], [
            record['id'] for record in selected['vehicles']])
        self.assertEqual(
            {90011, 90012, 91007},
            selected['vehicleTypeCompactDescrs'])
        self.assertTrue(
            selected['vehicleTypeCompactDescrs'].issubset(
                selected['unlockItemCompactDescrs']))
        all_tankman_ids = []
        for record in selected['vehicles']:
            all_tankman_ids.extend(record['crew'])
            self.assertEqual(set(record['crew']), set(record['tankmen']))
            for item_type in tuple(range(2, 8)) + (10,):
                self.assertTrue(record['inventoryItems'][item_type])
        self.assertEqual(len(all_tankman_ids), len(set(all_tankman_ids)))
        runtime_vehicles = modules['items'].vehicles
        self.assertTrue(
            {(1, 8), (1, 9), (2, 1), (2, 2), (2, 3)}.issubset(
                set(runtime_vehicles.attemptedTypeIDs)))
        self.assertTrue({(1, 8), (1, 9)}.issubset(
                        set(runtime_vehicles.crewTypeIDs)))
        self.assertTrue(
            {(2, 1), (2, 2), (2, 3)}.isdisjoint(
                set(runtime_vehicles.crewTypeIDs)))

    def test_account_is_created_after_login_state_clear_and_next_tick(self):
        (bootstrap, callbacks, compatibility, app_loader,
         spaces, events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            bootstrap._run_once()
            self.assertEqual([], compatibility.connect_calls)

            app_loader.space_id = spaces.INTRO_VIDEO
            callbacks.run_next()
            self.assertEqual([], compatibility.connect_calls)

            # Exact #1513 LoginState.init() clears client-only entities before
            # the state becomes observable as LOGIN.
            events.append('clear_entities_and_spaces')
            app_loader.space_id = spaces.LOGIN
            callbacks.run_next()
            self.assertEqual([], compatibility.connect_calls)

            callbacks.run_next()

        self.assertEqual(
            ['clear_entities_and_spaces', 'connect'], events)
        self.assertEqual(1, len(compatibility.connect_calls))
        self.assertTrue(compatibility.connect_calls[0][0])

    def test_login_space_must_remain_stable_for_the_deferred_tick(self):
        (bootstrap, callbacks, compatibility, app_loader,
         spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            app_loader.space_id = spaces.LOGIN
            bootstrap._run_once()
            self.assertEqual([], compatibility.connect_calls)

            app_loader.space_id = spaces.UNDEFINED
            callbacks.run_next()

        self.assertEqual([], compatibility.connect_calls)
        self.assertEqual(1, len(callbacks.pending))


if __name__ == '__main__':
    unittest.main()
