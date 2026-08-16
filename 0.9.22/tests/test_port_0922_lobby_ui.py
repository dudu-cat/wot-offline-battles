import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
               'client' / 'gui' / 'mods' / 'offline_lan_0922' /
               'lobby_ui.py')


def _load():
    name = 'test_offline_lan_0922_lobby_ui'
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ChinaController(object):
    def __init__(self):
        self.lobby_calls = []
        self.manual_calls = 0

    def onLobbyInited(self, event):
        self.lobby_calls.append(event)
        self.showBrowser()
        return 'stock-result'

    def showBrowser(self):
        self.manual_calls += 1


_ORIGINAL_LOBBY_INITED = _ChinaController.onLobbyInited


class ServerAnnouncementUITests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.adapter = self.module.ServerAnnouncementUI(
            runtime=_ChinaController,
            auto_due=lambda unused_controller: True)

    def tearDown(self):
        self.adapter.uninstall()
        _ChinaController.onLobbyInited = _ORIGINAL_LOBBY_INITED

    def test_suppresses_automatic_entry_without_patching_manual_browser(self):
        self.adapter.install()
        controller = _ChinaController()

        self.assertIsNone(controller.onLobbyInited('ready'))
        controller.showBrowser()

        self.assertEqual([], controller.lobby_calls)
        self.assertEqual(1, controller.manual_calls)

    def test_nonautomatic_lobby_entry_keeps_stock_behavior(self):
        self.adapter = self.module.ServerAnnouncementUI(
            runtime=_ChinaController,
            auto_due=lambda unused_controller: False)
        self.adapter.install()
        controller = _ChinaController()

        self.assertEqual('stock-result', controller.onLobbyInited('ready'))

        self.assertEqual(['ready'], controller.lobby_calls)
        self.assertEqual(1, controller.manual_calls)

    def test_due_checker_failure_does_not_block_stock_lobby(self):
        def broken_due(unused_controller):
            raise RuntimeError('controller unavailable')

        self.adapter = self.module.ServerAnnouncementUI(
            runtime=_ChinaController, auto_due=broken_due)
        self.adapter.install()
        controller = _ChinaController()

        self.assertEqual('stock-result', controller.onLobbyInited('ready'))

        self.assertEqual(['ready'], controller.lobby_calls)
        self.assertEqual(1, controller.manual_calls)

    def test_uninstall_restores_exact_method(self):
        original = _ChinaController.__dict__['onLobbyInited']
        self.adapter.install()

        self.adapter.uninstall()

        self.assertIs(original, _ChinaController.__dict__['onLobbyInited'])

    def test_uninstall_does_not_clobber_later_wrapper(self):
        self.adapter.install()

        def later_wrapper(controller, event):
            return 'later'

        _ChinaController.onLobbyInited = later_wrapper
        self.adapter.uninstall()

        self.assertIs(later_wrapper,
                      _ChinaController.__dict__['onLobbyInited'])


class _Module(object):
    def __init__(self, value):
        self.isShowStartupVideo = value


class IntroVideoSkipTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()

    @staticmethod
    def _stock():
        return True

    def test_the_startup_video_check_reports_no_video(self):
        helpers = _Module(self._stock)
        states = _Module(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers, states))

        self.assertTrue(skip.install())

        self.assertFalse(helpers.isShowStartupVideo())
        self.assertFalse(states.isShowStartupVideo())

    def test_uninstall_restores_every_replaced_name(self):
        helpers = _Module(self._stock)
        states = _Module(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers, states))
        skip.install()

        skip.uninstall()

        self.assertIs(self._stock, helpers.isShowStartupVideo)
        self.assertIs(self._stock, states.isShowStartupVideo)

    def test_a_module_without_the_check_is_left_alone(self):
        helpers = _Module(self._stock)
        other = object()
        skip = self.module.IntroVideoSkip(runtime=(helpers, other))

        self.assertTrue(skip.install())

        self.assertFalse(helpers.isShowStartupVideo())
        self.assertFalse(hasattr(other, 'isShowStartupVideo'))

    def test_uninstall_does_not_clobber_another_replacement(self):
        helpers = _Module(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers,))
        skip.install()

        def later(*unused):
            return True

        helpers.isShowStartupVideo = later
        skip.uninstall()

        self.assertIs(later, helpers.isShowStartupVideo)

    def test_installing_twice_keeps_one_replacement(self):
        helpers = _Module(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers,))
        skip.install()
        skip.install()

        skip.uninstall()

        self.assertIs(self._stock, helpers.isShowStartupVideo)


if __name__ == '__main__':
    unittest.main()
