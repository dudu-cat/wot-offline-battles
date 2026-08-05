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
_config = None
_account_context = None
_deadline = 0.0
_login_space_seen = False
_lobby_view_loaded = False
_lobby_listener_installed = False


def _schedule(delay, function):
    global _callback_id
    _callback_id = BigWorld.callback(delay, function)


def _selected_vehicle(config):
    try:
        import nations
        from items import ITEM_TYPE_INDICES, tankmen, vehicles
        descriptor = vehicles.VehicleDescr(typeName=config['vehicle'])
        vehicle_id = 1
        nation_id, vehicle_type_id = descriptor.type.id

        crew_compact_descrs = list(tankmen.generateTankmen(
            nation_id, vehicle_type_id, descriptor.type.crewRoles,
            False, tankmen.MAX_SKILL_LEVEL, 0))
        if len(crew_compact_descrs) != len(descriptor.type.crewRoles):
            raise ValueError('generated crew does not match vehicle crew slots')

        crew_ids = []
        tankman_compact_descrs = {}
        for index, compact_descr in enumerate(crew_compact_descrs):
            tankman_id = 1001 + index
            tankman_descr = tankmen.TankmanDescr(compact_descr)
            roles = descriptor.type.crewRoles[index]
            if (tankman_descr.nationID != nation_id or
                    tankman_descr.vehicleTypeID != vehicle_type_id or
                    tankman_descr.role != roles[0]):
                raise ValueError(
                    'generated tankman does not match vehicle crew slot')
            crew_ids.append(tankman_id)
            tankman_compact_descrs[tankman_id] = compact_descr

        inventory_items = {}
        shop_item_prices = {}
        components = (
            ('vehicleChassis', descriptor.chassis),
            ('vehicleTurret', descriptor.turret),
            ('vehicleGun', descriptor.gun),
            ('vehicleEngine', descriptor.engine),
            ('vehicleRadio', descriptor.radio),
            ('vehicleFuelTank', descriptor.fuelTank),
        )
        for item_type_name, component in components:
            compact_descr = component.compactDescr
            item_type = ITEM_TYPE_INDICES[item_type_name]
            inventory_items.setdefault(item_type, {})[compact_descr] = 1
            # ShopDataParser uses membership in itemPrices as its module
            # catalogue.  A zero price keeps installed modules discoverable
            # without reimplementing the client's XML price reader.
            shop_item_prices[compact_descr] = {'credits': 0, 'gold': 0}

        shells = list(vehicles.getDefaultAmmoForGun(descriptor.gun))
        shell_items = inventory_items.setdefault(
            ITEM_TYPE_INDICES['shell'], {})
        for index in range(0, len(shells), 2):
            shell_compact_descr = shells[index]
            shell_items[shell_compact_descr] = shells[index + 1]
            shop_item_prices[shell_compact_descr] = {
                'credits': 0, 'gold': 0,
            }

        vehicle_compact_descr = descriptor.makeCompactDescr()
        vehicle_int_compact_descr = vehicles.makeIntCompactDescrByID(
            'vehicle', nation_id, vehicle_type_id)
        shop_item_prices[vehicle_int_compact_descr] = {
            'credits': 0, 'gold': 0,
        }

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

        return {
            'id': vehicle_id,
            'compDescr': vehicle_compact_descr,
            'crew': crew_ids,
            'tankmen': tankman_compact_descrs,
            'repair': (0, descriptor.maxHealth),
            'lock': (0, 0),
            'shells': shells,
            'shellsLayout': {},
            'eqs': [0, 0, 0],
            'eqsLayout': [0, 0, 0],
            'inventoryItems': inventory_items,
            'shopItemPrices': shop_item_prices,
            'shopNationCount': len(nations.NAMES),
            'customizationItemCount': customization_count,
        }
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
    global _login_space_seen, _session, _started
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
            session.stop(show_login=False, restore_account=False)
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
        g_compatibility.connect(
            show_lobby=True, account_context=_account_context)
        _schedule(0.10, _wait_for_lobby)
    except Exception as error:
        _fail_startup(error)


def _wait_for_lobby():
    global _callback_id, _deadline, _session
    _callback_id = None
    try:
        if _lobby_view_loaded and _deadline <= 0.0:
            _deadline = time.time() + float(
                _config.get('startupTimeoutSeconds', 30.0))
        from gui.app_loader import g_appLoader
        lobby = g_appLoader.getDefLobbyApp()
        if (g_compatibility.is_ready() and
                _lobby_is_ready(g_appLoader, lobby)):
            from gui.mods.offline_lan_0922.lan_session import LANSession
            _session = LANSession(
                _config, lobby_ready=_native_lobby_is_ready,
                callback=BigWorld.callback,
                cancel_callback=BigWorld.cancelCallback)
            if not _session.install():
                _session.stop(show_login=False, restore_account=False)
                _session = None
                raise RuntimeError('LAN Battle button did not install')
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
        _install_lobby_listener()
        _schedule(0.0, _run_once)
    except Exception as error:
        _fail_startup(error, prefix='startup callback failed')


def fini():
    cleanup_error = _cleanup_runtime()
    if cleanup_error is not None:
        sys.stdout.write(
            '[Offline LAN 0.9.22] shutdown failed: %s\n' % cleanup_error)
