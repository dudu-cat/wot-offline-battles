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

    def bind(self, event, callback):
        self.options.setdefault("bindings", {})[event] = callback

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


class _Process(object):
    def __init__(self, exit_code=None, stdout=None):
        self.exit_code = exit_code
        self.stdout = stdout
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.exit_code = 0

    def kill(self):
        self.killed = True
        self.exit_code = -9

    def wait(self, timeout=None):
        return self.exit_code


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

    def test_team_size_is_editable_only_when_0_9_22_hosts(self):
        self._game("0.9.22.0.1", "1513")
        for mode in (core.MODE_SINGLE, core.MODE_HOST):
            self.window.mode.set(mode)
            self.window._refresh_mode()
            self.assertEqual(
                "readonly", self.window.team_size_box.cget("state"))
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        self.assertEqual("disabled", self.window.team_size_box.cget("state"))

    def test_lan_server_button_starts_only_from_host_mode(self):
        self._game("0.9.22.0.1", "1513")
        self.assertEqual("disabled", self.window.server_button.cget("state"))
        self.window.mode.set(core.MODE_HOST)
        self.window._refresh_mode()
        self.assertEqual("normal", self.window.server_button.cget("state"))
        self.assertEqual(
            "Start LAN server", self.window.server_button.cget("text"))
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        self.assertEqual("disabled", self.window.server_button.cget("state"))

    def test_lan_server_button_installs_data_and_starts_persistent_server(self):
        game_root = self._game("0.9.22.0.1", "1513")
        self.window.mode.set(core.MODE_HOST)
        self.window.team_size.set("7")
        self.window._refresh_mode()
        with mock.patch(
                "core.install_client_mod", return_value=["installed"]) \
                as install, mock.patch.object(
                    self.window, "_start_server", return_value=True) \
                as start_server:
            self.assertTrue(self.window._toggle_lan_server())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        install.assert_called_once_with(game_root, core.PORT_0_9_22)
        start_server.assert_called_once_with(
            game_root, core.PORT_0_9_22, 7, persistent=True)

    def test_0_8_2_server_button_ignores_hidden_team_size_text(self):
        game_root = self._game("0.8.2", "335")
        self.window.mode.set(core.MODE_HOST)
        self.window.team_size.set("not a team size")
        self.window._refresh_mode()
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch.object(
                    self.window, "_start_server", return_value=True) \
                as start_server:
            self.assertTrue(self.window._toggle_lan_server())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        start_server.assert_called_once_with(
            game_root, core.PORT_0_8_2, core.DEFAULT_TEAM_SIZE,
            persistent=True)

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
        self.window.team_size.set("7")
        self.window._save_settings()
        reopened = wot_launcher.LauncherWindow(_FakeTk, _FakeTtk, self.dialog)
        self.assertEqual(reopened.mode.get(), core.MODE_HOST)
        self.assertEqual(reopened.player_name.get(), "Peng")
        self.assertEqual(reopened.team_size.get(), "7")

    def test_invalid_team_size_stops_before_starting_a_game(self):
        self._game("0.9.22.0.1", "1513")
        self.window.team_size.set("16")

        self.window._start()

        self.assertIn("Tanks per team must be 1-15", self._log_text())
        self.assertFalse(self.window._busy)

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

    def test_maintenance_buttons_are_only_enabled_for_0_9_22(self):
        self._game()
        self.assertEqual("disabled",
                         self.window.repair_button.cget("state"))
        self.assertEqual("disabled",
                         self.window.vehicle_editor_button.cget("state"))
        self._game("0.9.22.0.1", "1513")
        self.assertEqual("normal", self.window.repair_button.cget("state"))
        self.assertEqual("normal", self.window.reset_button.cget("state"))
        self.assertEqual(
            "readonly", self.window.vehicle_profile_box.cget("state"))
        self.assertEqual("normal", self.window.new_profile_button.cget("state"))
        self.assertEqual(
            "disabled", self.window.vehicle_editor_button.cget("state"))

    def test_vehicle_editor_opens_for_the_selected_0_9_22_folder(self):
        with mock.patch(
                "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                return_value=["Fast MS-1"]):
            game_root = self._game("0.9.22.0.1", "1513")
        self.window.vehicle_profile.set("Fast MS-1")
        self.window._profile_selected()
        with mock.patch(
                "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                return_value=["Fast MS-1"]), mock.patch(
                "wot_launcher.vehicle_editor_ui.open_vehicle_editor") as open_editor:
            self.assertTrue(self.window._open_vehicle_editor())

        open_editor.assert_called_once_with(
            self.window.root, game_root, "Fast MS-1", log=self.window._log)

    def test_vehicle_profile_selector_is_single_player_only(self):
        with mock.patch(
                "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                return_value=["Fast MS-1"]):
            self._game("0.9.22.0.1", "1513")
        self.window.vehicle_profile.set("Fast MS-1")
        self.window._profile_selected()
        self.assertEqual(
            "normal", self.window.vehicle_editor_button.cget("state"))

        self.window.mode.set(core.MODE_HOST)
        self.window._refresh_mode()

        self.assertEqual(
            wot_launcher.vehicle_overlays.ORIGINAL_PROFILE_LABEL,
            self.window.vehicle_profile.get())
        self.assertEqual(
            "disabled", self.window.vehicle_profile_box.cget("state"))
        self.assertEqual(
            "disabled", self.window.vehicle_editor_button.cget("state"))

    def test_stale_profile_recovery_reports_an_unsafe_manifest_path(self):
        self._game("0.9.22.0.1", "1513")
        with mock.patch(
                "wot_launcher.vehicle_overlays.manifest_path",
                side_effect=wot_launcher.vehicle_overlays.VehicleOverlayError(
                    "unsafe overlay path")):
            self.assertEqual(0, self.window._recover_stale_vehicle_profile())

        self.assertIn("could not be checked", self._log_text())
        self.assertIn("unsafe overlay path", self._log_text())

    def test_new_profile_is_selected_and_opened(self):
        with mock.patch(
                "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                side_effect=[[], [], ["Fast MS-1"], ["Fast MS-1"]]), \
                mock.patch.object(
                    self.window, "_ask_profile_name",
                    return_value="Fast MS-1"), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.create_vehicle_profile",
                    return_value="Fast MS-1") as create, \
                mock.patch(
                    "wot_launcher.vehicle_editor_ui.open_vehicle_editor") as editor:
            game_root = self._game("0.9.22.0.1", "1513")
            self.assertTrue(self.window._new_vehicle_profile())

        create.assert_called_once_with(game_root, "Fast MS-1")
        editor.assert_called_once_with(
            self.window.root, game_root, "Fast MS-1", log=self.window._log)
        self.assertEqual("Fast MS-1", self.window.vehicle_profile.get())

    def test_single_player_profile_is_removed_after_a_launch_failure(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "mode": core.MODE_SINGLE,
            "team_size": core.DEFAULT_TEAM_SIZE,
            "vehicle_profile": "Fast MS-1",
        }
        prepared = {
            "profile": "Fast MS-1",
            "installedMembers": 1,
            "removedMembers": 0,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value=prepared) as prepare, \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=1) as cleanup, \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch.object(
                    self.window, "_run_game",
                    side_effect=RuntimeError("synthetic launch failure")):
            self.window._run_session(self.settings_dir, session, "Peng")

        prepare.assert_called_once_with(self.settings_dir, "Fast MS-1")
        cleanup.assert_called_once_with(self.settings_dir)
        self.assertIn("temporary vehicle profile", self._log_text())

    def test_single_player_orders_server_worker_player_and_profile_cleanup(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": True,
            "mode": core.MODE_SINGLE,
            "team_size": 7,
            "vehicle_profile": "Fast MS-1",
        }
        order = []
        prepared = {
            "profile": "Fast MS-1",
            "installedMembers": 1,
            "removedMembers": 0,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    side_effect=lambda *unused: (
                        order.append("profile") or prepared)), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    side_effect=lambda *unused: (
                        order.append("profile_cleanup") or 1)), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch.object(
                    self.window, "_start_server",
                    side_effect=lambda *args, **kwargs: (
                        order.append("server") or True)) as start_server, \
                mock.patch.object(
                    self.window, "_start_worker",
                    side_effect=lambda *unused: (
                        order.append("worker") or True)) as start_worker, \
                mock.patch.object(
                    self.window, "_run_game",
                    side_effect=lambda *args, **kwargs: order.append("player")) \
                    as run_game, \
                mock.patch.object(
                    self.window, "_stop_worker",
                    side_effect=lambda: order.append("worker_stop")), \
                mock.patch.object(
                    self.window, "_stop_server",
                    side_effect=lambda: order.append("server_stop")):
            self.window._run_session(self.settings_dir, session, "Peng")

        start_server.assert_called_once_with(
            self.settings_dir, core.PORT_0_9_22, 7, loopback_only=True)
        start_worker.assert_called_once_with(
            self.settings_dir, core.LOCAL_HOST,
            core.DEFAULT_SERVER_PORT, 7)
        run_game.assert_called_once_with(
            self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
            core.DEFAULT_SERVER_PORT, paired_worker=True)
        self.assertEqual(
            ["profile", "server", "worker", "player", "worker_stop",
             "server_stop", "profile_cleanup"], order)

    def test_startup_repair_runs_in_the_background_and_reports_actions(self):
        game_root = self._game("0.9.22.0.1", "1513")
        with mock.patch(
                "core.repair_0_9_22_startup",
                return_value=["repair complete"]) as repair:
            self.assertTrue(self.window._repair_startup())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        repair.assert_called_once_with(game_root)
        self.assertIn("repair complete", self._log_text())
        self.assertEqual("normal", self.window.start_button.cget("state"))

    def test_reset_requires_confirmation(self):
        self._game("0.9.22.0.1", "1513")
        with mock.patch.object(
                self.window, "_confirm_reset", return_value=False), \
                mock.patch("core.reset_0_9_22_state") as reset:
            self.assertFalse(self.window._reset_all_state())

        reset.assert_not_called()
        self.assertIn("reset was cancelled", self._log_text())

    def test_confirmed_reset_runs_only_after_the_game_is_closed(self):
        game_root = self._game("0.9.22.0.1", "1513")
        with mock.patch.object(
                self.window, "_confirm_reset", return_value=True), \
                mock.patch("core.game_is_running", return_value=False), \
                mock.patch(
                    "core.reset_0_9_22_state",
                    return_value=["reset complete"]) as reset:
            self.assertTrue(self.window._reset_all_state())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        reset.assert_called_once_with(game_root)
        self.assertIn("reset complete", self._log_text())

    def test_reset_refuses_a_running_game_before_confirmation(self):
        self._game("0.9.22.0.1", "1513")
        with mock.patch.object(self.window, "_confirm_reset") as confirm, \
                mock.patch("core.game_is_running", return_value=True):
            self.assertFalse(self.window._reset_all_state())

        confirm.assert_not_called()
        self.assertIn("Close World of Tanks", self._log_text())

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

    def test_single_player_refuses_an_external_compatible_server(self):
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_COMPATIBLE), \
                mock.patch("wot_launcher.subprocess.Popen") as popen:
            self.assertFalse(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22,
                loopback_only=True))
        popen.assert_not_called()
        self.assertIn("fresh launcher-owned server", self._log_text())

    def test_launcher_owned_server_reuse_requires_the_exact_context(self):
        server = _Process()
        self.window._server = server
        game_root = os.path.realpath(self.settings_dir)
        self.window._server_context = {
            "game_root": os.path.normcase(game_root),
            "port_version": core.PORT_0_9_22,
            "loopback_only": False,
            "team_size": 7,
        }
        with mock.patch("core.listener_status") as listener:
            self.assertTrue(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22, team_size=7))
            self.assertFalse(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22, team_size=8))
            self.assertFalse(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22, team_size=7,
                loopback_only=True))
            self.assertFalse(self.window._start_server(
                os.path.join(self.settings_dir, "other"),
                core.PORT_0_9_22, team_size=7))
        listener.assert_not_called()
        self.assertIn("different game, visibility, or team", self._log_text())

    def test_persistent_server_survives_session_cleanup_until_stopped(self):
        server = _Process()
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_FREE), \
                mock.patch("core.wait_for_server", return_value=True), \
                mock.patch("core.local_addresses", return_value=[]), \
                mock.patch("wot_launcher.subprocess.Popen",
                           return_value=server):
            self.assertTrue(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22, team_size=7,
                persistent=True))

        self.assertTrue(self.window._server_persistent)
        self.assertFalse(self.window._stop_server())
        self.assertFalse(server.terminated)
        self.assertTrue(self.window._stop_server(force=True))
        self.assertTrue(server.terminated)

    def test_worker_start_failure_reports_the_native_failure_log(self):
        starter = core.worker_starter_executable(self.settings_dir)
        with open(starter, "w") as stream:
            stream.write("starter")
        marker = core.worker_ready_marker(self.settings_dir)
        with open(marker, "w") as stream:
            stream.write("live-worker-marker")
        previous_marker_token = core.worker_ready_marker_token(
            self.settings_dir)
        with open(core.worker_failure_log(self.settings_dir), "w") as stream:
            stream.write("stage=worker_exited_before_ready win32_error=7\n")
        worker = _Process(exit_code=23)
        with mock.patch(
                "core.wait_for_worker_ready", return_value=False) as wait, \
                mock.patch("wot_launcher.subprocess.Popen",
                           return_value=worker) as popen:
            self.assertFalse(self.window._start_worker(
                self.settings_dir, "10.0.0.5", 1234, 7))

        self.assertEqual(self.settings_dir, popen.call_args.kwargs["cwd"])
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(
            "10.0.0.5", environment[core.CLIENT_SERVER_HOST_ENV_0922])
        self.assertEqual(
            "1234", environment[core.CLIENT_SERVER_PORT_ENV_0922])
        self.assertEqual(
            previous_marker_token,
            wait.call_args.kwargs["previous_marker_token"])
        self.assertTrue(os.path.isfile(marker))
        self.assertIn("worker_exited_before_ready", self._log_text())

    def test_join_does_not_start_the_game_for_an_unrelated_listener(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": "10.0.0.5",
            "tcp_port": 28782,
            "needs_server": False,
            "mode": core.MODE_JOIN,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated") as isolate, \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_OCCUPIED), \
                mock.patch.object(self.window, "_run_game") as run_game:
            self.window._run_session(self.settings_dir, session, "Peng")

        isolate.assert_called_once_with(self.settings_dir)
        run_game.assert_not_called()
        self.assertIn("not the server for this client", self._log_text())

    def test_host_starts_no_hidden_worker(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": True,
            "mode": core.MODE_HOST,
            "team_size": 7,
            "vehicle_profile": None,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch.object(
                    self.window, "_start_server", return_value=True) \
                    as start_server, \
                mock.patch.object(self.window, "_start_worker") as worker, \
                mock.patch.object(self.window, "_run_game") as game, \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"):
            self.window._run_session(self.settings_dir, session, "Peng")

        start_server.assert_called_once_with(
            self.settings_dir, core.PORT_0_9_22, 7,
            loopback_only=False)
        worker.assert_not_called()
        game.assert_called_once_with(
            self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
            core.DEFAULT_SERVER_PORT, paired_worker=False)

    def test_closing_the_window_saves_the_settings(self):
        self.window.player_name.set("Peng")
        self.window._on_close()
        self.assertEqual("Peng", core.load_settings().get("name"))

    def test_close_stops_children_but_waits_for_profile_cleanup(self):
        self.window._busy = True

        self.assertFalse(self.window._on_close())

        self.assertFalse(self.window.root.destroyed)
        self.assertTrue(self.window._close_pending)
        self.assertTrue(self.window._stop_requested)
        self.assertIn("Closing the game", self._log_text())

    def test_closing_the_window_stops_a_persistent_server(self):
        stopped = []

        class _Server(object):
            def poll(self):
                return None

            def terminate(self):
                stopped.append("terminate")

            def wait(self, timeout=None):
                return 0

        self.window._server = _Server()
        self.window._server_persistent = True
        self.window._on_close()
        self.assertEqual(stopped, ["terminate"])
        self.assertTrue(self.window.root.destroyed)


if __name__ == "__main__":
    unittest.main()
