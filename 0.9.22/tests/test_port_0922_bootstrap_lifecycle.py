import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
             'client' / 'gui' / 'mods' / 'offline_lan_0922' /
             'bootstrap.py')


MAX_SKILL_LEVEL = 100
# The exact #1513 tankmen._makeLevelXpCosts, with _LEVELUP_K1/_LEVELUP_K2.
SKILL_XP_COSTS = [0]
for _level in range(1, MAX_SKILL_LEVEL + 1):
    SKILL_XP_COSTS.append(SKILL_XP_COSTS[-1] + int(round(
        50.0 * pow(100.0, float(_level - 1) / MAX_SKILL_LEVEL))))


class _TankmanDescr(object):
    """Reproduces the #1513 TankmanDescr skill/XP surface bootstrap uses."""

    def __init__(self, compact_descr):
        passport, _, tail = compact_descr.partition(b'|')
        skills, _, free_xp = tail.partition(b'|')
        nation_id, vehicle_type_id, role = passport.decode('ascii').split(':')
        self.nationID = int(nation_id)
        self.vehicleTypeID = int(vehicle_type_id)
        self.role = role
        self._passport = passport
        self.skills = [name for name in skills.decode('ascii').split(',')
                       if name]
        self.freeSkillsNumber = 0
        self.freeXP = int(free_xp or 0)

    @property
    def lastSkillNumber(self):
        return len(self.skills)

    @staticmethod
    def levelUpXpCost(from_skill_level, skill_sequence_number):
        return 2 ** skill_sequence_number * (
            SKILL_XP_COSTS[from_skill_level + 1] -
            SKILL_XP_COSTS[from_skill_level])

    def makeCompactDescr(self):
        return b'%s|%s|%d' % (self._passport,
                              ','.join(self.skills).encode('ascii'),
                              self.freeXP)


def new_skill_count(descriptor, active_skills):
    """The exact #1513 Tankman.newSkillCount loop, as a pure simulation."""
    available = list(active_skills)
    count = 0
    last_skill_level = MAX_SKILL_LEVEL
    free_xp = descriptor.freeXP
    skills = list(descriptor.skills)
    while last_skill_level == MAX_SKILL_LEVEL or not skills:
        if not available:
            break
        name = available.pop()
        if name in skills:
            continue
        skills.append(name)
        count += 1
        last_skill_level = 0
        sequence = len(skills) - descriptor.freeSkillsNumber
        while last_skill_level < MAX_SKILL_LEVEL:
            cost = descriptor.levelUpXpCost(last_skill_level, sequence)
            if cost > free_xp:
                break
            free_xp -= cost
            last_skill_level += 1
    return count


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
        session = types.SimpleNamespace(
            install=lambda: events.append('install_battle_router') or True,
            stop=lambda **unused_kwargs: None)
        lan_session_module = types.ModuleType(
            'gui.mods.offline_lan_0922.lan_session')
        lan_session_module.LANSession = lambda *args, **kwargs: session
        announcement_ui = types.SimpleNamespace(
            install=lambda: events.append('install_announcement_router'),
            uninstall=lambda: events.append('uninstall_announcement_router'))
        lobby_ui_module = types.ModuleType(
            'gui.mods.offline_lan_0922.lobby_ui')
        lobby_ui_module.ServerAnnouncementUI = lambda: announcement_ui
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
        optional_devices = dict(
            (9000 + index, types.SimpleNamespace(
                compactDescr=9000 + index, tags=frozenset()))
            for index in range(4))
        equipments = dict(
            (11000 + index, types.SimpleNamespace(
                compactDescr=11000 + index, tags=frozenset(),
                equipmentType=0))
            for index in range(3))
        # #1513 tags the artillery and airstrike consumables 'avatar' and
        # gives every battle booster a non-regular equipmentType.
        equipments[11100] = types.SimpleNamespace(
            compactDescr=11100, tags=frozenset(('avatar', 'trigger')),
            equipmentType=0)
        equipments[11200] = types.SimpleNamespace(
            compactDescr=11200, tags=frozenset(('notForSale',)),
            equipmentType=1)
        crew_type_ids = []
        crew_skill_masks = []
        vehicles = types.SimpleNamespace(
            VehicleDescr=vehicle_descr,
            getDefaultAmmoForGun=lambda gun: [gun.compactDescr + 10000, 20],
            makeIntCompactDescrByID=lambda unused_name, nation_id, type_id: (
                90000 + nation_id * 1000 + type_id),
            g_list=_VehicleList(),
            attemptedTypeIDs=attempted_type_ids,
            crewTypeIDs=crew_type_ids,
            g_cache=types.SimpleNamespace(
                customization20=lambda: customization,
                optionalDevices=lambda: optional_devices,
                equipments=lambda: equipments))

        def generate_tankmen(nation_id, vehicle_type_id, roles,
                             is_premium, role_level, skills_mask, is_preview):
            crew_type_ids.append((nation_id, vehicle_type_id))
            crew_skill_masks.append(skills_mask)
            if (nation_id, vehicle_type_id) == (1, 8):
                raise ValueError('unloadable crew definition')
            # Only the commander receives the offline Sixth Sense perk.
            return [
                ('%d:%d:%s|%s|0' % (
                    nation_id, vehicle_type_id, role[0],
                    'commander_sixthSense'
                    if skills_mask and role[0] == 'commander' else '')).encode(
                        'ascii')
                for role in roles]

        tankmen = types.SimpleNamespace(
            MAX_SKILL_LEVEL=100,
            getSkillsMask=lambda skills: (
                1 << 18 if tuple(skills) ==
                ('commander_sixthSense',) else 0),
            generateTankmen=generate_tankmen,
            TankmanDescr=_TankmanDescr,
            generatedSkillMasks=crew_skill_masks)
        items = types.ModuleType('items')
        items.EQUIPMENT_TYPES = types.SimpleNamespace(
            regular=0, battleBoosters=1)
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
        # The exact #1513 scripts/common/AccountCommands.pyc values.
        account_commands = types.ModuleType('AccountCommands')
        account_commands.VEHICLE_SETTINGS_FLAG = types.SimpleNamespace(
            NONE=0, XP_TO_TMAN=1, AUTO_REPAIR=2, AUTO_LOAD=4, AUTO_EQUIP=8,
            GROUP_0=16, ORIGINAL_CREW=32, NO_BATTLE=64,
            AUTO_EQUIP_BOOSTER=128, AUTO_RENT_CUSTOMIZATION=256)

        modules = {
            'AccountCommands': account_commands,
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
            'gui.mods.offline_lan_0922.lan_session': lan_session_module,
            'gui.mods.offline_lan_0922.lobby_ui': lobby_ui_module,
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

    ACTIVE_SKILLS = ('repair', 'camouflage', 'brotherhood', 'firefighting',
                     'commander_sixthSense', 'driver_virtuoso',
                     'gunner_smoothTurret')

    def test_a_fresh_garage_vehicle_starts_with_the_refill_switches_on(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle({'vehicle': 'ussr:R11_MS-1'})

        # XP_TO_TMAN | AUTO_REPAIR | AUTO_LOAD | AUTO_EQUIP
        self.assertEqual(15, selected['settings'])
        for record in selected['vehicles']:
            self.assertEqual(15, record['settings'])

    def test_every_crewman_starts_with_three_skills_left_to_pick(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle({'vehicle': 'ussr:R11_MS-1'})

        for record in selected['vehicles']:
            for compact_descr in record['tankmen'].values():
                descriptor = _TankmanDescr(compact_descr)
                self.assertEqual(
                    3, new_skill_count(descriptor, self.ACTIVE_SKILLS))

    def test_no_crew_skill_is_chosen_for_the_player(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle({'vehicle': 'ussr:R11_MS-1'})

        for record in selected['vehicles']:
            for compact_descr in record['tankmen'].values():
                skills = _TankmanDescr(compact_descr).skills
                self.assertIn(skills, ([], ['commander_sixthSense']))

    def test_the_ammunition_layout_mirrors_the_loaded_shells(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle({'vehicle': 'ussr:R11_MS-1'})

        # Vehicle.isAutoLoadFull compares every loaded count with this layout.
        for record in selected['vehicles']:
            key = record['shellsLayoutIdx']
            self.assertEqual({key: record['shells']}, record['shellsLayout'])

    def test_selected_vehicle_snapshot_is_relationally_complete(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle(
                {'vehicle': 'ussr:R11_MS-1'})

        self.assertEqual([100001, 100002], selected['crew'])
        self.assertEqual(
            [b'0:11:commander|commander_sixthSense|1260360',
             b'0:11:driver||630180'],
            [selected['tankmen'][100001], selected['tankmen'][100002]])
        self.assertEqual((0, 111), selected['repair'])
        self.assertEqual((0, 0), selected['lock'])
        self.assertEqual([0, 0, 0], selected['eqs'])
        self.assertEqual([0, 0, 0], selected['eqsLayout'])
        # 9 is optionalDevice and 11 is equipment: account-wide catalogues
        # the garage needs before it can offer a mount.
        self.assertEqual(set(range(2, 8)) | {9, 10, 11},
                         set(selected['inventoryItems']))
        required_prices = set()
        for item_type in tuple(range(2, 8)) + (9, 10, 11):
            required_prices.update(selected['inventoryItems'][item_type])
        self.assertTrue(
            required_prices.issubset(selected['shopItemPrices']))
        self.assertTrue(
            required_prices.issubset(selected['unlockItemCompactDescrs']))
        self.assertEqual(4, selected['optionalDeviceCount'])
        # The avatar strike and the battle booster stay out of the catalogue.
        self.assertEqual(3, selected['equipmentCount'])
        for compact_descr in (11100, 11200):
            self.assertNotIn(compact_descr, selected['inventoryItems'][11])
            self.assertNotIn(compact_descr, selected['shopItemPrices'])
        for compact_descr in (9000, 9003, 11000, 11002):
            self.assertEqual(
                200,
                selected['inventoryItems'][
                    9 if compact_descr < 10000 else 11][compact_descr])
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
        self.assertEqual(
            {1 << 18},
            set(modules['items'].tankmen.generatedSkillMasks))
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
            ['clear_entities_and_spaces', 'install_announcement_router',
             'install_battle_router', 'connect'],
            events)
        self.assertEqual(1, len(compatibility.connect_calls))
        self.assertTrue(compatibility.connect_calls[0][0])

        with mock.patch.dict(sys.modules, modules):
            self.assertIsNone(bootstrap._cleanup_runtime())
        self.assertEqual('uninstall_announcement_router', events[-1])

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
