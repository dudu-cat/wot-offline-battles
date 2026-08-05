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

        descriptor = types.SimpleNamespace(
            type=types.SimpleNamespace(
                id=(0, 11),
                crewRoles=(('commander',), ('driver',))),
            chassis=types.SimpleNamespace(compactDescr=2002),
            turret=types.SimpleNamespace(compactDescr=2003),
            gun=types.SimpleNamespace(compactDescr=2004),
            engine=types.SimpleNamespace(compactDescr=2005),
            fuelTank=types.SimpleNamespace(compactDescr=2006),
            radio=types.SimpleNamespace(compactDescr=2007),
            maxHealth=100,
            makeCompactDescr=lambda: b'vehicle')
        customization = types.SimpleNamespace(
            paints={12001: types.SimpleNamespace(compactDescr=12001)},
            camouflages={12002: types.SimpleNamespace(compactDescr=12002)},
            decals={12003: types.SimpleNamespace(compactDescr=12003)},
            modifications={12004: types.SimpleNamespace(compactDescr=12004)},
            styles={12005: types.SimpleNamespace(compactDescr=12005)})
        vehicles = types.SimpleNamespace(
            VehicleDescr=lambda **unused_kwargs: descriptor,
            getDefaultAmmoForGun=lambda unused_gun: [10010, 20],
            makeIntCompactDescrByID=lambda *unused_args: 1001,
            g_cache=types.SimpleNamespace(
                customization20=lambda: customization))
        tankman_roles = {b'commander': 'commander', b'driver': 'driver'}
        tankmen = types.SimpleNamespace(
            MAX_SKILL_LEVEL=100,
            generateTankmen=lambda *unused_args: [b'commander', b'driver'],
            TankmanDescr=lambda compact: types.SimpleNamespace(
                nationID=0, vehicleTypeID=11,
                role=tankman_roles[compact]))
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

        self.assertEqual([1001, 1002], selected['crew'])
        self.assertEqual(
            {1001: b'commander', 1002: b'driver'}, selected['tankmen'])
        self.assertEqual((0, 100), selected['repair'])
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
