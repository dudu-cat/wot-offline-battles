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
_deadline = 0.0
_lobby_view_loaded = False
_lobby_listener_installed = False


def _schedule(delay, function):
    global _callback_id
    _callback_id = BigWorld.callback(delay, function)


def _selected_vehicle(config):
    try:
        from items import vehicles
        descriptor = vehicles.VehicleDescr(typeName=config['vehicle'])
        return {'id': 1, 'compDescr': descriptor.makeCompactDescr()}
    except Exception:
        return None


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
    global _callback_id, _config, _deadline, _lobby_listener_installed
    global _lobby_view_loaded, _session, _started
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
            session.stop(show_login=False)
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

    _config = None
    _deadline = 0.0
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
            _session = LANSession(_config)
            if not _session.start():
                _session.stop(show_login=False)
                _session = None
                raise RuntimeError('LAN session did not start')
            _remove_lobby_listener()
            sys.stdout.write(
                '[Offline LAN 0.9.22] lobby and LAN session ready\n')
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
    global _callback_id, _config, _deadline
    _callback_id = None
    try:
        _config = port_config.load()
        if not _config['enabled']:
            _cleanup_runtime()
            sys.stdout.write('[Offline LAN 0.9.22] disabled by config\n')
            return
        context = {
            'selected_vehicle': _selected_vehicle(_config),
            # Account settings are server-owned in #1513.  Keep their local
            # offline substitute beside config.json across client restarts.
            'account_state': AccountState(),
        }
        _deadline = 0.0
        g_compatibility.connect(show_lobby=True, account_context=context)
        _schedule(0.10, _wait_for_lobby)
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
