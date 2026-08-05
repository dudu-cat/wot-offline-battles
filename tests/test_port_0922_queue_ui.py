import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _install_package_modules():
    created = []
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
            created.append(name)
    return created


def _load(name):
    _install_package_modules()
    full_name = 'gui.mods.offline_lan_0922.' + name
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(full_name,
                                                   PACKAGE_ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


class _ArenaType(object):
    def __init__(self, geometry_name, gameplay='ctf', name=None):
        self.geometryName = geometry_name
        self.gameplayName = gameplay
        self.name = name or geometry_name
        self.maxPlayersInTeam = 15
        self.roundLength = 900


class _Window(object):
    def __init__(self, ctx=None):
        self.ctx = ctx
        self.calls = []
        self.closed = False
        self.close_calls = 0
        self._TrainingSettingsWindow__arenasCache = None

    def updateTrainingRoom(self, arena, round_length, is_private, comment):
        self.calls.append((arena, round_length, is_private, comment))
        return 'stock'

    def onWindowClose(self):
        self.closed = True
        self.close_calls += 1


_WINDOW_INIT = _Window.__init__
_WINDOW_UPDATE = _Window.updateTrainingRoom


class QueueUITests(unittest.TestCase):
    def setUp(self):
        self.catalog = _load('map_catalog')
        self.queue_ui = _load('queue_ui')
        self.arena_type = types.SimpleNamespace(g_cache={
            1: _ArenaType('01_karelia'),
            2: _ArenaType('04_himmelsdorf', gameplay='assault'),
            3: _ArenaType('05_prohorovka'),
        })
        self.started = []
        self.adapter = self.queue_ui.QueueUI(
            self.started.append, lambda: ('05_prohorovka',),
            runtime=(self.arena_type, _Window))

    def tearDown(self):
        self.adapter.uninstall()
        _Window.__init__ = _WINDOW_INIT
        _Window.updateTrainingRoom = _WINDOW_UPDATE

    def test_catalog_filters_non_ctf_and_server_pool(self):
        rows = self.catalog.build(self.arena_type.g_cache,
                                  ('05_prohorovka',)).cache
        self.assertEqual(['05_prohorovka'], [row['name'] for row in rows])

    def test_catalog_uses_stock_1513_map_icon_formatter(self):
        formatters = types.ModuleType(
            'gui.Scaleform.daapi.view.lobby.trainings.formatters')
        formatters.getMapIconPath = lambda arena: 'icons/' + arena.geometryName
        trainings = types.ModuleType(
            'gui.Scaleform.daapi.view.lobby.trainings')
        trainings.formatters = formatters
        modules = {
            'gui.Scaleform': types.ModuleType('gui.Scaleform'),
            'gui.Scaleform.daapi': types.ModuleType('gui.Scaleform.daapi'),
            'gui.Scaleform.daapi.view': types.ModuleType(
                'gui.Scaleform.daapi.view'),
            'gui.Scaleform.daapi.view.lobby': types.ModuleType(
                'gui.Scaleform.daapi.view.lobby'),
            'gui.Scaleform.daapi.view.lobby.trainings': trainings,
            'gui.Scaleform.daapi.view.lobby.trainings.formatters': formatters,
        }

        with unittest.mock.patch.dict(sys.modules, modules):
            row = self.catalog.build(
                self.arena_type.g_cache, ('05_prohorovka',)).cache[0]

        self.assertEqual('icons/05_prohorovka', row['icon'])

    def test_records_exact_upstream_hook_reference(self):
        self.assertEqual(
            'c0bc550c46deac980194b7b860ee8781d53ec97b',
            self.queue_ui.UPSTREAM_TUXEDO_COMMIT)
        self.assertIn(self.queue_ui.UPSTREAM_TUXEDO_COMMIT,
                      self.queue_ui.UPSTREAM_TUXEDO_URL)

    def test_open_picker_uses_exact_1513_lobby_view_contract(self):
        class ViewLoadParams(object):
            def __init__(self, alias, name=None):
                self.alias = alias
                self.name = name

        app = types.SimpleNamespace(loadView=unittest.mock.Mock())
        loaders = types.ModuleType(
            'gui.Scaleform.framework.managers.loaders')
        loaders.ViewLoadParams = ViewLoadParams
        aliases = types.ModuleType(
            'gui.Scaleform.genConsts.PREBATTLE_ALIASES')
        aliases.PREBATTLE_ALIASES = types.SimpleNamespace(
            TRAINING_SETTINGS_WINDOW_PY='trainingSettingsWindow')
        app_loader = types.ModuleType('gui.app_loader')
        app_loader.g_appLoader = types.SimpleNamespace(
            getDefLobbyApp=unittest.mock.Mock(return_value=app))
        modules = {
            'gui.Scaleform': types.ModuleType('gui.Scaleform'),
            'gui.Scaleform.framework': types.ModuleType(
                'gui.Scaleform.framework'),
            'gui.Scaleform.framework.managers': types.ModuleType(
                'gui.Scaleform.framework.managers'),
            'gui.Scaleform.framework.managers.loaders': loaders,
            'gui.Scaleform.genConsts': types.ModuleType(
                'gui.Scaleform.genConsts'),
            'gui.Scaleform.genConsts.PREBATTLE_ALIASES': aliases,
            'gui.app_loader': app_loader,
        }

        with unittest.mock.patch.dict(sys.modules, modules):
            self.assertTrue(self.queue_ui.open_picker())

        app_loader.g_appLoader.getDefLobbyApp.assert_called_once_with()
        params, context = app.loadView.call_args[0]
        self.assertEqual('trainingSettingsWindow', params.alias)
        self.assertEqual('trainingSettingsWindow', params.name)
        self.assertEqual({
            'isCreateRequest': True,
            'isOfflineLanPicker': True,
        }, context)

    def test_offline_picker_requests_allowed_map_without_stock_event(self):
        self.adapter.install()
        window = _Window({'isOfflineLanPicker': True})

        self.assertEqual(['05_prohorovka'], [
            row['name'] for row in
            window._TrainingSettingsWindow__arenasCache.cache])
        self.assertTrue(window.updateTrainingRoom(3, 15, False, 'ignored'))
        self.assertEqual(['05_prohorovka'], self.started)
        self.assertEqual([], window.calls)
        self.assertTrue(window.closed)
        self.assertFalse(getattr(window, self.queue_ui._PICKER_MARKER))

    def test_close_clears_marker_before_stock_window_cleanup(self):
        self.adapter.install()
        window = _Window({'isOfflineLanPicker': True})

        self.assertTrue(self.adapter.close())
        self.assertTrue(window.closed)
        self.assertFalse(getattr(window, self.queue_ui._PICKER_MARKER))

    def test_synchronous_session_close_does_not_destroy_window_twice(self):
        adapter = None

        def request_start(map_name):
            self.started.append(map_name)
            adapter.close()
            return True

        adapter = self.queue_ui.QueueUI(
            request_start, lambda: ('05_prohorovka',),
            runtime=(self.arena_type, _Window))
        self.adapter = adapter
        adapter.install()
        window = _Window({'isOfflineLanPicker': True})

        self.assertTrue(window.updateTrainingRoom(3, 15, False, 'ignored'))
        self.assertEqual(['05_prohorovka'], self.started)
        self.assertEqual(1, window.close_calls)

    def test_offline_picker_rejects_unavailable_map(self):
        self.adapter.install()
        window = _Window({'isOfflineLanPicker': True})

        self.assertFalse(window.updateTrainingRoom(1, 15, False, 'ignored'))
        self.assertEqual([], self.started)
        self.assertFalse(window.closed)

    def test_normal_training_window_fully_forwards(self):
        self.adapter.install()
        window = _Window({'isCreateRequest': True})

        self.assertEqual('stock', window.updateTrainingRoom(1, 15, True, 'x'))
        self.assertEqual([(1, 15, True, 'x')], window.calls)
        self.assertEqual([], self.started)

    def test_uninstall_does_not_clobber_later_wrapper(self):
        self.adapter.install()

        def later_wrapper(*args):
            return 'later'

        _Window.updateTrainingRoom = later_wrapper
        self.adapter.uninstall()
        self.assertIs(later_wrapper, _Window.updateTrainingRoom)

    def test_uninstall_restores_raw_class_functions(self):
        original_init = _Window.__dict__['__init__']
        original_update = _Window.__dict__['updateTrainingRoom']
        self.adapter.install()

        self.adapter.uninstall()

        self.assertIs(original_init, _Window.__dict__['__init__'])
        self.assertIs(original_update,
                      _Window.__dict__['updateTrainingRoom'])
