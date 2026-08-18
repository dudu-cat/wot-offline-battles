from __future__ import print_function

import sys
import time

import BigWorld

from gui.mods.offline_lan_0922.compat import g_compatibility
from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922.account_rpc.state import AccountState


_callback_id = None
_started = False
_session = None
_announcement_ui = None
_intro_skip = None
_config = None
_account_context = None
_deadline = 0.0
_login_space_seen = False
_lobby_view_loaded = False
_lobby_listener_installed = False

# Enough of every artefact that the garage never blocks a mount on stock.
OFFLINE_ARTEFACT_STOCK = 200


def _schedule(delay, function):
    global _callback_id
    _callback_id = BigWorld.callback(delay, function)


def _selected_vehicle(config):
    try:
        import nations
        from items import ITEM_TYPE_INDICES, tankmen, vehicles
        selected_descriptor = vehicles.VehicleDescr(
            typeName=config['vehicle'])
        selected_type_id = tuple(selected_descriptor.type.id)

        # The exact #1513 VehicleList exposes one nation-indexed mapping for
        # every nations.NAMES entry.  Put the configured vehicle first so its
        # inventory id remains stable while the rest of the loadable local
        # catalogue is discovered from the client, rather than hard-coded.
        type_ids = [selected_type_id]
        for nation_id in range(len(nations.NAMES)):
            for vehicle_type_id in sorted(
                    vehicles.g_list.getList(nation_id).keys()):
                type_id = (nation_id, vehicle_type_id)
                if type_id not in type_ids:
                    type_ids.append(type_id)

        vehicle_records = []
        inventory_items = {}
        shop_item_prices = {}
        vehicle_type_compact_descrs = set()
        unlock_item_compact_descrs = set()
        next_tankman_id = 100001
        for type_id in type_ids:
            try:
                if type_id == selected_type_id:
                    descriptor = selected_descriptor
                else:
                    descriptor = vehicles.VehicleDescr(typeID=type_id)

                nation_id, vehicle_type_id = descriptor.type.id
                if set(('event_battles', 'premiumIGR', 'observer')).intersection(
                        descriptor.type.tags):
                    raise ValueError(
                        'vehicle type is not available in standard battles')
                # generateTankmen filters this mask through each crew role;
                # only the commander receives the offline Sixth Sense perk.
                skills_mask = tankmen.getSkillsMask(
                    ('commander_sixthSense',))
                crew_compact_descrs = list(tankmen.generateTankmen(
                    nation_id, vehicle_type_id, descriptor.type.crewRoles,
                    False, tankmen.MAX_SKILL_LEVEL, skills_mask, False))
                if (not crew_compact_descrs or
                        len(crew_compact_descrs) !=
                        len(descriptor.type.crewRoles)):
                    raise ValueError(
                        'generated crew does not match vehicle crew slots')

                validated_tankmen = []
                for index, compact_descr in enumerate(crew_compact_descrs):
                    tankman_descr = tankmen.TankmanDescr(compact_descr)
                    roles = descriptor.type.crewRoles[index]
                    if (tankman_descr.nationID != nation_id or
                            tankman_descr.vehicleTypeID != vehicle_type_id or
                            tankman_descr.role != roles[0]):
                        raise ValueError(
                            'generated tankman does not match vehicle crew slot')
                    validated_tankmen.append(compact_descr)

                components = (
                    ('vehicleChassis', descriptor.chassis),
                    ('vehicleTurret', descriptor.turret),
                    ('vehicleGun', descriptor.gun),
                    ('vehicleEngine', descriptor.engine),
                    ('vehicleRadio', descriptor.radio),
                    ('vehicleFuelTank', descriptor.fuelTank),
                )
                record_inventory_items = {}
                for item_type_name, component in components:
                    compact_descr = component.compactDescr
                    item_type = ITEM_TYPE_INDICES[item_type_name]
                    record_inventory_items.setdefault(
                        item_type, {})[compact_descr] = 1

                shells = list(vehicles.getDefaultAmmoForGun(descriptor.gun))
                if not shells or len(shells) % 2:
                    raise ValueError(
                        'default ammo must contain descriptor/count pairs')
                record_shell_items = record_inventory_items.setdefault(
                    ITEM_TYPE_INDICES['shell'], {})
                for index in range(0, len(shells), 2):
                    record_shell_items[shells[index]] = shells[index + 1]

                vehicle_compact_descr = descriptor.makeCompactDescr()
                if not vehicle_compact_descr or descriptor.maxHealth <= 0:
                    raise ValueError('vehicle descriptor is not garage-ready')
                vehicle_int_compact_descr = (
                    vehicles.makeIntCompactDescrByID(
                        'vehicle', nation_id, vehicle_type_id))
            except Exception:
                if type_id == selected_type_id:
                    raise
                # Special or incomplete definitions can be advertised by
                # g_list but still fail native garage construction.  Skip a
                # non-selected entry unless its entire relational record is
                # valid; never publish a half-built vehicle.
                continue

            crew_ids = []
            tankman_compact_descrs = {}
            for compact_descr in validated_tankmen:
                tankman_id = next_tankman_id
                next_tankman_id += 1
                crew_ids.append(tankman_id)
                tankman_compact_descrs[tankman_id] = compact_descr

            for item_type, items in record_inventory_items.items():
                published_items = inventory_items.setdefault(item_type, {})
                for compact_descr, count in items.items():
                    published_items[compact_descr] = max(
                        int(published_items.get(compact_descr, 0)),
                        int(count))
                    shop_item_prices[compact_descr] = {
                        'credits': 0, 'gold': 0,
                    }
                    unlock_item_compact_descrs.add(compact_descr)

            vehicle_type_compact_descrs.add(vehicle_int_compact_descr)
            unlock_item_compact_descrs.add(vehicle_int_compact_descr)
            shop_item_prices[vehicle_int_compact_descr] = {
                'credits': 0, 'gold': 0,
            }
            vehicle_records.append({
                'id': len(vehicle_records) + 1,
                'compDescr': vehicle_compact_descr,
                'crew': crew_ids,
                'tankmen': tankman_compact_descrs,
                'repair': (0, descriptor.maxHealth),
                'lock': (0, 0),
                'shells': shells,
                'shellsLayout': {},
                'eqs': [0, 0, 0],
                'eqsLayout': [0, 0, 0],
                'inventoryItems': record_inventory_items,
                'vehicleTypeCompactDescr': vehicle_int_compact_descr,
            })

        if not vehicle_records:
            raise ValueError('client vehicle catalogue is empty')

        # items/__init__ ITEM_TYPE_NAMES: optionalDevice is 9 and equipment is
        # 11.  Neither was published, so the garage showed an empty equipment
        # and optional-device surface no matter what the account owned.
        artefact_counts = {}
        for item_type_name, cache_accessor in (
                ('optionalDevice', vehicles.g_cache.optionalDevices),
                ('equipment', vehicles.g_cache.equipments)):
            item_type = ITEM_TYPE_INDICES[item_type_name]
            published = inventory_items.setdefault(item_type, {})
            for descriptor in cache_accessor().values():
                try:
                    compact_descr = int(descriptor.compactDescr)
                except (TypeError, ValueError, AttributeError):
                    continue
                published[compact_descr] = max(
                    int(published.get(compact_descr, 0)),
                    OFFLINE_ARTEFACT_STOCK)
                shop_item_prices[compact_descr] = {'credits': 0, 'gold': 0}
                unlock_item_compact_descrs.add(compact_descr)
            artefact_counts[item_type_name] = len(published)
            if not published:
                raise ValueError(
                    'client %s catalogue is empty' % item_type_name)

        customization_count = 0
        customization_cache = vehicles.g_cache.customization20()
        for collection_name in (
                'paints', 'camouflages', 'decals', 'modifications', 'styles'):
            collection = getattr(customization_cache, collection_name)
            for item in collection.values():
                shop_item_prices[item.compactDescr] = {
                    # Keep zero-price appearance items credit-denominated.
                    # Money's weighted currency chooser prefers gold when
                    # both zero-valued keys are present.
                    'credits': 0,
                }
                customization_count += 1
        if customization_count <= 0:
            raise ValueError('client customization catalogue is empty')

        # Preserve the historical selected-vehicle fields for consumers that
        # only need the configured tank.  ``vehicles`` carries the complete
        # garage and account_rpc expands every record into native inventory.
        result = dict(vehicle_records[0])
        result.update({
            'vehicles': vehicle_records,
            'inventoryItems': inventory_items,
            'shopItemPrices': shop_item_prices,
            'shopNationCount': len(nations.NAMES),
            'customizationItemCount': customization_count,
            'vehicleTypeCompactDescrs': vehicle_type_compact_descrs,
            'unlockItemCompactDescrs': unlock_item_compact_descrs,
            'optionalDeviceCount': artefact_counts['optionalDevice'],
            'equipmentCount': artefact_counts['equipment'],
        })
        return result
    except Exception:
        # _run_once owns startup error reporting.  Returning an empty snapshot
        # here would merely defer a deterministic descriptor problem until a
        # native Hangar consumer crashes with a misleading IndexError.
        raise


def _on_lobby_view_loaded(event):
    global _lobby_view_loaded
    _lobby_view_loaded = True


def _install_lobby_listener():
    global _lobby_listener_installed
    if _lobby_listener_installed:
        return
    from gui.shared import events, g_eventBus
    g_eventBus.addListener(
        events.GUICommonEvent.LOBBY_VIEW_LOADED,
        _on_lobby_view_loaded)
    _lobby_listener_installed = True


def _remove_lobby_listener():
    global _lobby_listener_installed
    if not _lobby_listener_installed:
        return
    from gui.shared import events, g_eventBus
    g_eventBus.removeListener(
        events.GUICommonEvent.LOBBY_VIEW_LOADED,
        _on_lobby_view_loaded)
    _lobby_listener_installed = False


def _cleanup_runtime():
    global _account_context, _callback_id, _config, _deadline
    global _lobby_listener_installed, _lobby_view_loaded
    global _announcement_ui, _intro_skip, _login_space_seen, _session, _started
    errors = []

    callback_id = _callback_id
    _callback_id = None
    if callback_id is not None:
        try:
            BigWorld.cancelCallback(callback_id)
        except Exception as error:
            errors.append(error)

    session = _session
    _session = None
    if session is not None:
        try:
            # Global mod shutdown is followed by compatibility.disconnect().
            # Do not create a fresh Account and start lobby coroutines only to
            # destroy it immediately in the next cleanup stage.
            session.stop(show_login=False, restore_account=False,
                         release_join=True)
        except Exception as error:
            errors.append(error)

    intro_skip = _intro_skip
    _intro_skip = None
    if intro_skip is not None:
        try:
            intro_skip.uninstall()
        except Exception as error:
            errors.append(error)

    announcement_ui = _announcement_ui
    _announcement_ui = None
    if announcement_ui is not None:
        try:
            announcement_ui.uninstall()
        except Exception as error:
            errors.append(error)

    try:
        _remove_lobby_listener()
    except Exception as error:
        errors.append(error)
        _lobby_listener_installed = False

    try:
        g_compatibility.fini()
    except Exception as error:
        errors.append(error)

    _account_context = None
    _config = None
    _deadline = 0.0
    _login_space_seen = False
    _lobby_view_loaded = False
    _started = False
    if errors:
        return errors[0]
    return None


def _fail_startup(error, prefix='startup failed'):
    cleanup_error = _cleanup_runtime()
    if cleanup_error is None:
        sys.stdout.write('[Offline LAN 0.9.22] %s: %s\n' %
                         (prefix, error))
    else:
        sys.stdout.write(
            '[Offline LAN 0.9.22] %s: %s; cleanup failed: %s\n' %
            (prefix, error, cleanup_error))


def _lobby_is_ready(app_loader, lobby):
    if lobby is None or not _lobby_view_loaded:
        return False
    # In build #1513 SFApplication exists before its Scaleform managers and
    # cursor are initialized.  Starting the LAN picker in that interval queues
    # a view which cannot be synchronously closed if the connection then fails.
    if not bool(getattr(lobby, 'initialized', True)):
        return False

    from gui.app_loader.settings import GUI_GLOBAL_SPACE_ID
    if app_loader.getSpaceID() != GUI_GLOBAL_SPACE_ID.LOBBY:
        return False

    from gui.shared.utils.HangarSpace import g_hangarSpace
    if not (g_hangarSpace.inited and g_hangarSpace.spaceInited):
        return False

    from CurrentVehicle import g_currentVehicle
    if g_currentVehicle.isPresent():
        vehicle = g_hangarSpace.getVehicleEntity()
        if vehicle is None or getattr(vehicle, 'model', None) is None:
            return False
    return True


def _login_space_is_ready():
    """Whether LoginState has finished entering its exact GUI space."""
    from gui.app_loader import g_appLoader
    from gui.app_loader.settings import GUI_GLOBAL_SPACE_ID

    if g_appLoader.getSpaceID() != GUI_GLOBAL_SPACE_ID.LOGIN:
        return False
    lobby = g_appLoader.getDefLobbyApp()
    return (lobby is not None and
            bool(getattr(lobby, 'initialized', True)))


def _native_lobby_is_ready():
    from gui.app_loader import g_appLoader
    return _lobby_is_ready(g_appLoader, g_appLoader.getDefLobbyApp())


def _install_lan_session():
    """Install the Battle callback before Scaleform binds LobbyHeader."""
    global _session
    if _session is not None:
        return True
    from gui.mods.offline_lan_0922.lan_session import LANSession
    session = LANSession(
        _config, lobby_ready=_native_lobby_is_ready,
        callback=BigWorld.callback,
        cancel_callback=BigWorld.cancelCallback)
    try:
        if not session.install():
            raise RuntimeError('LAN Battle button did not install')
    except Exception:
        session.stop(show_login=False, restore_account=False,
                     release_join=True)
        raise
    _session = session
    return True


def _install_intro_skip():
    """Skip the startup video so the client reaches the login screen."""
    global _intro_skip
    if _intro_skip is not None:
        return _intro_skip
    try:
        from gui.mods.offline_lan_0922.lobby_ui import IntroVideoSkip
        intro_skip = IntroVideoSkip()
        intro_skip.install()
    except Exception as error:
        # The startup video is presentation only. Never let it stop startup.
        sys.stdout.write(
            '[Offline LAN 0.9.22] the startup video stays: %s\n' % error)
        return None
    _intro_skip = intro_skip
    return intro_skip


def _install_announcement_ui():
    """Own only the stock CN automatic server-announcement window."""
    global _announcement_ui
    if _announcement_ui is not None:
        return True
    from gui.mods.offline_lan_0922.lobby_ui import ServerAnnouncementUI
    announcement_ui = ServerAnnouncementUI()
    announcement_ui.install()
    _announcement_ui = announcement_ui
    return True


def _wait_for_login_space():
    """Create the client-only Account one tick after stable LoginState."""
    global _callback_id, _login_space_seen
    _callback_id = None
    try:
        if not _login_space_is_ready():
            _login_space_seen = False
            _schedule(0.10, _wait_for_login_space)
            return
        if not _login_space_seen:
            # LoginState.init() clears every client-only entity and space.
            # Recheck on the next engine tick so that cleanup always precedes
            # creation of our Account, including after the startup video.
            _login_space_seen = True
            _schedule(0.0, _wait_for_login_space)
            return
        _login_space_seen = False
        # LobbyHeaderMeta stores a bound ``fightClick`` Function when its
        # Scaleform movie receives ``script = self``.  A class patch installed
        # after HANGAR_READY can repaint the button but cannot replace that
        # cached callback.  Own the action before connect creates the lobby.
        _install_announcement_ui()
        _install_lan_session()
        g_compatibility.connect(
            show_lobby=True, account_context=_account_context)
        _schedule(0.10, _wait_for_lobby)
    except Exception as error:
        _fail_startup(error)


def _wait_for_lobby():
    global _callback_id, _deadline
    _callback_id = None
    try:
        if _lobby_view_loaded and _deadline <= 0.0:
            _deadline = time.time() + float(
                _config.get('startupTimeoutSeconds', 30.0))
        from gui.app_loader import g_appLoader
        lobby = g_appLoader.getDefLobbyApp()
        if (g_compatibility.is_ready() and
                _lobby_is_ready(g_appLoader, lobby)):
            if _session is None:
                raise RuntimeError('LAN Battle button is not installed')
            _remove_lobby_listener()
            sys.stdout.write(
                '[Offline LAN 0.9.22] lobby ready; click Battle to join '
                '%s:%s\n' % (
                    _config.get('host', '127.0.0.1'),
                    _config.get('port', 28782)))
            return
        # EULA and other first-run screens require user interaction and must
        # not consume the hangar-startup timeout.  The deadline begins when
        # the native lobby view reports that it has loaded.
        if (_lobby_view_loaded and _deadline > 0.0 and
                time.time() >= _deadline):
            raise RuntimeError('offline lobby loading timed out')
        _schedule(0.10, _wait_for_lobby)
    except Exception as error:
        _fail_startup(error)


def _run_once():
    global _account_context, _callback_id, _config, _deadline
    _callback_id = None
    try:
        _config = port_config.load()
        if not _config['enabled']:
            _cleanup_runtime()
            sys.stdout.write('[Offline LAN 0.9.22] disabled by config\n')
            return
        _account_context = {
            'selected_vehicle': _selected_vehicle(_config),
            # Account settings are server-owned in #1513.  Keep their local
            # offline substitute beside config.json across client restarts.
            'account_state': AccountState(),
        }
        _deadline = 0.0
        _wait_for_login_space()
    except Exception as error:
        _fail_startup(error)


def init():
    global _callback_id, _started
    if _started:
        return
    _started = True
    try:
        _install_intro_skip()
        _install_lobby_listener()
        _schedule(0.0, _run_once)
    except Exception as error:
        _fail_startup(error, prefix='startup callback failed')


def fini():
    cleanup_error = _cleanup_runtime()
    if cleanup_error is not None:
        sys.stdout.write(
            '[Offline LAN 0.9.22] shutdown failed: %s\n' % cleanup_error)
