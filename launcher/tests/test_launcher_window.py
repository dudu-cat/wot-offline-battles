"""Wiring tests for the launcher window with a fake Tk module.

Widget option names and real Tk behavior stay unproven here. Only the callback
wiring and the guard paths are covered.
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import core
import wot_launcher


class _Widget(object):
    def __init__(self, master=None, **options):
        self.options = dict(options)
        self.children = []
        if master is not None and hasattr(master, "children"):
            master.children.append(self)

    def pack(self, **unused):
        pass

    def grid(self, **unused):
        pass

    def grid_columnconfigure(self, *unused, **unused_options):
        pass

    grid_rowconfigure = grid_columnconfigure

    def config(self, **options):
        self.options.update(options)

    def cget(self, name):
        return self.options.get(name)


class _Text(_Widget):
    def __init__(self, master=None, **options):
        _Widget.__init__(self, master, **options)
        self.lines = []

    def insert(self, unused_index, text):
        self.lines.append(text)

    def see(self, unused_index):
        pass


class _StringVar(object):
    def __init__(self, value=""):
        self._value = value
        self._callbacks = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for callback in self._callbacks:
            callback()

    def trace_add(self, unused_mode, callback):
        self._callbacks.append(lambda: callback())


class _Root(_Widget):
    def __init__(self):
        _Widget.__init__(self)
        self.destroyed = False

    def title(self, unused_title):
        pass

    def protocol(self, unused_name, unused_handler):
        pass

    def after(self, unused_delay, callback):
        callback()

    def destroy(self):
        self.destroyed = True


class _FakeTk(object):
    Tk = _Root
    Frame = _Widget
    Label = _Widget
    Entry = _Widget
    Button = _Widget
    Radiobutton = _Widget
    Text = _Text
    StringVar = _StringVar


class _FakeTtk(object):
    Combobox = _Widget


class _FakeFileDialog(object):
    def __init__(self, selection=""):
        self.selection = selection

    def askdirectory(self, **unused):
        return self.selection


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.settings_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.settings_dir, True)
        self.addCleanup(setattr, core, "settings_path", core.settings_path)
        self.addCleanup(setattr, core, "discover_game_folders",
                        core.discover_game_folders)
        core.settings_path = lambda: os.path.join(self.settings_dir,
                                                  "launcher.json")
        core.discover_game_folders = lambda *unused, **unused_options: []
        self.dialog = _FakeFileDialog()
        self.window = wot_launcher.LauncherWindow(_FakeTk, _FakeTtk, self.dialog)

    def _log_text(self):
        return "".join(self.window.log_view.lines)

    def _game(self, version="0.8.2", build="335"):
        game_root = os.path.join(self.settings_dir, "game-" + version)
        os.makedirs(game_root)
        with open(os.path.join(game_root, core.GAME_EXECUTABLE), "w") as stream:
            stream.write("")
        with open(os.path.join(game_root, "version.xml"), "w") as stream:
            stream.write("<version> v.%s #%s </version>" % (version, build))
        self.window.game_root.set(game_root)
        return game_root

    def test_the_address_field_and_test_button_follow_the_mode(self):
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        self.assertEqual(self.window.join_entry.cget("state"), "normal")
        self.assertEqual(self.window.test_button.cget("state"), "normal")
        for mode in (core.MODE_SINGLE, core.MODE_HOST):
            self.window.mode.set(mode)
            self.window._refresh_mode()
            self.assertEqual(self.window.join_entry.cget("state"), "disabled")
            self.assertEqual(self.window.test_button.cget("state"), "normal")

    def test_an_empty_folder_asks_for_the_game_executable(self):
        self.window.game_root.set("")
        self.assertIn(core.GAME_EXECUTABLE,
                      self.window.client_label.cget("text"))

    def test_a_folder_without_the_executable_is_reported(self):
        self.window.game_root.set(self.settings_dir)
        self.assertIn("was not found", self.window.client_label.cget("text"))

    def test_browsing_fills_in_the_selected_folder(self):
        self.dialog.selection = self.settings_dir
        self.window._browse()
        self.assertEqual(self.window.game_root.get(),
                         os.path.normpath(self.settings_dir))

    def test_start_reports_the_problem_and_runs_nothing(self):
        self.window.game_root.set(self.settings_dir)
        self.window._start()
        self.assertIn(core.GAME_EXECUTABLE, self._log_text())
        self.assertFalse(self.window._busy)

    def test_start_reports_an_invalid_join_address(self):
        game_root = self._game()
        os.makedirs(os.path.join(game_root, "res_mods", "0.8.2", "scripts",
                                 "client", "gui", "mods", "offhangar"))
        self.window.mode.set(core.MODE_JOIN)
        self.window.join_address.set("")
        self.window._start()
        self.assertIn("Enter the address", self._log_text())
        self.assertFalse(self.window._busy)

    def test_settings_survive_a_new_window(self):
        self.window.game_root.set(self.settings_dir)
        self.window.mode.set(core.MODE_HOST)
        self.window.player_name.set("Peng")
        self.window._save_settings()
        reopened = wot_launcher.LauncherWindow(_FakeTk, _FakeTtk, self.dialog)
        self.assertEqual(reopened.mode.get(), core.MODE_HOST)
        self.assertEqual(reopened.player_name.get(), "Peng")

    def test_a_selected_game_folder_joins_the_known_list(self):
        game = os.path.join(self.settings_dir, "game")
        os.makedirs(game)
        with open(os.path.join(game, core.GAME_EXECUTABLE), "w") as stream:
            stream.write("")
        self.dialog.selection = game
        self.window._browse()
        self.assertEqual([game], self.window._folders)
        self.assertEqual([game], self.window.folder_box.cget("values"))
        reopened = wot_launcher.LauncherWindow(_FakeTk, _FakeTtk, self.dialog)
        self.assertEqual([game], reopened._folders)
        self.assertEqual(game, reopened.game_root.get())

    def test_a_folder_without_the_game_is_not_remembered(self):
        self.dialog.selection = self.settings_dir
        self.window._browse()
        self.assertEqual([], self.window._folders)

    def test_the_test_button_probes_the_typed_address(self):
        probed = []
        self._game()
        self.addCleanup(setattr, core, "listener_status", core.listener_status)
        core.listener_status = lambda client, host, port: (
            probed.append((client, host, port)) or core.LISTENER_COMPATIBLE)
        self.window.mode.set(core.MODE_JOIN)
        self.window.join_address.set("10.0.0.5:1234")
        self.assertTrue(self.window._test_connection())
        for attempt in range(200):
            if probed:
                break
            time.sleep(0.01)
        self.assertEqual([(core.PORT_0_8_2, "10.0.0.5", 1234)], probed)
        self.assertIn("Testing 10.0.0.5:1234", self._log_text())

    def test_the_test_button_reports_an_invalid_address(self):
        self._game()
        self.window.mode.set(core.MODE_JOIN)
        self.window.join_address.set("")
        self.assertFalse(self.window._test_connection())
        self.assertIn("Enter the address", self._log_text())

    def test_a_matching_existing_server_is_reused(self):
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_COMPATIBLE), \
                mock.patch("wot_launcher.subprocess.Popen") as popen:
            self.assertTrue(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22))
        popen.assert_not_called()
        self.assertIn("already running", self._log_text())

    def test_an_unrelated_listener_blocks_server_start(self):
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_OCCUPIED), \
                mock.patch("wot_launcher.subprocess.Popen") as popen:
            self.assertFalse(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22))
        popen.assert_not_called()
        self.assertIn("does not speak", self._log_text())

    def test_join_does_not_start_the_game_for_an_unrelated_listener(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": "10.0.0.5",
            "tcp_port": 28782,
            "needs_server": False,
            "mode": core.MODE_JOIN,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_OCCUPIED), \
                mock.patch.object(self.window, "_run_game") as run_game:
            self.window._run_session(self.settings_dir, session, "Peng")

        run_game.assert_not_called()
        self.assertIn("not the server for this client", self._log_text())

    def test_closing_the_window_saves_the_settings(self):
        self.window.player_name.set("Peng")
        self.window._on_close()
        self.assertEqual("Peng", core.load_settings().get("name"))

    def test_closing_the_window_stops_the_server(self):
        stopped = []

        class _Server(object):
            def poll(self):
                return None

            def terminate(self):
                stopped.append("terminate")

            def wait(self, timeout=None):
                return 0

        self.window._server = _Server()
        self.window._on_close()
        self.assertEqual(stopped, ["terminate"])
        self.assertTrue(self.window.root.destroyed)


if __name__ == "__main__":
    unittest.main()
