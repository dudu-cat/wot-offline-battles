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
            'enabled': True, 'startupTimeoutSeconds': 30.0}
        state_module = types.ModuleType(
            'gui.mods.offline_lan_0922.account_rpc.state')
        state_module.AccountState = object
        app_loader_module = _package('gui.app_loader')
        app_loader_module.g_appLoader = app_loader
        settings_module = types.ModuleType('gui.app_loader.settings')
        settings_module.GUI_GLOBAL_SPACE_ID = spaces

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
        }
        name = 'test_offline_lan_0922_bootstrap_lifecycle'
        spec = importlib.util.spec_from_file_location(name, BOOTSTRAP)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
        return (module, callbacks, compatibility, app_loader, spaces, events,
                modules)

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
