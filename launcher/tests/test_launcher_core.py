import ast
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import core
import preferences_overlay
import server_imports
import stage_payload


class EndpointTest(unittest.TestCase):
    def test_address_without_port_uses_the_default(self):
        self.assertEqual(core.parse_endpoint("192.168.1.10"),
                         ("192.168.1.10", core.DEFAULT_SERVER_PORT))

    def test_address_with_port(self):
        self.assertEqual(core.parse_endpoint(" host.lan:9000 "),
                         ("host.lan", 9000))

    def test_empty_address_is_rejected(self):
        self.assertRaises(core.LauncherError, core.parse_endpoint, "  ")

    def test_port_out_of_range_is_rejected(self):
        self.assertRaises(core.LauncherError, core.parse_endpoint, "host:70000")

    def test_port_text_is_rejected(self):
        self.assertRaises(core.LauncherError, core.parse_endpoint, "host:abc")

    def test_single_player_and_host_use_the_local_endpoint(self):
        for mode in (core.MODE_SINGLE, core.MODE_HOST):
            self.assertEqual(core.endpoint_for_mode(mode, "10.0.0.5"),
                             (core.LOCAL_HOST, core.DEFAULT_SERVER_PORT))

    def test_join_uses_the_typed_endpoint(self):
        self.assertEqual(core.endpoint_for_mode(core.MODE_JOIN, "10.0.0.5:1234"),
                         ("10.0.0.5", 1234))


class ServerRequirementTest(unittest.TestCase):
    def test_host_always_needs_a_server(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(core.server_required(port_version, core.MODE_HOST))

    def test_join_never_starts_a_local_server(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertFalse(core.server_required(port_version, core.MODE_JOIN))

    def test_single_player_needs_a_server(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(
                core.server_required(port_version, core.MODE_SINGLE))


class GameRootTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write(self, relative_path, text=""):
        path = os.path.join(self.root, relative_path)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w") as stream:
            stream.write(text)
        return path

    def test_version_file_identifies_the_port(self):
        self._write("version.xml", "<version> v.0.9.22.0.1 #1513 </version>")
        self.assertEqual(core.read_client_version(self.root), "0.9.22.0.1")
        self.assertEqual(core.read_client_identity(self.root),
                         ("0.9.22.0.1", "1513"))
        self.assertEqual(core.detect_port(self.root), core.PORT_0_9_22)

    def test_another_0_9_22_build_is_not_treated_as_the_pinned_client(self):
        self._write("version.xml", "<version> v.0.9.22.0.1 #0789 </version>")
        self._write(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.6.0-alpha.1.wotmod")
        self.assertIsNone(core.detect_port(self.root))

    def test_another_0_9_22_patch_is_not_treated_as_the_pinned_client(self):
        self._write("version.xml", "<version> v.0.9.22.1 #1513 </version>")
        self.assertIsNone(core.detect_port(self.root))

    def test_installed_mod_does_not_identify_a_client_without_version_xml(self):
        self._write(os.path.join(
            "res_mods", "0.8.2", "scripts", "client", "gui", "mods",
            "offhangar", "__init__.py"))
        self.assertIsNone(core.read_client_version(self.root))
        self.assertIsNone(core.detect_port(self.root))
        status = core.inspect_game_root(self.root)
        self.assertIsNone(status["client"])
        self.assertFalse(status["mod_installed"])

    def test_unsupported_client_reports_no_port(self):
        self._write("version.xml", "<version> v.1.0.0 #1 </version>")
        self._write(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.6.0-alpha.1.wotmod")
        self.assertIsNone(core.detect_port(self.root))

    def test_an_installed_0_9_22_package_is_not_a_version_fallback(self):
        self._write(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.6.0-alpha.1.wotmod")
        self.assertIsNone(core.detect_port(self.root))
        self.assertIsNone(core.inspect_game_root(self.root)["client"])

    def test_an_unparseable_version_file_fails_closed(self):
        self._write(
            "version.xml",
            "<broken><version>v.0.9.22.0.1 #1513</version>")
        self._write(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.6.0-alpha.1.wotmod")
        self.assertIsNone(core.detect_port(self.root))
        self.assertIsNone(core.inspect_game_root(self.root)["client"])

    def test_an_empty_stock_mod_directory_is_not_an_install_marker(self):
        os.makedirs(os.path.join(self.root, "mods", "0.9.22.0.1"))
        self.assertIsNone(core.installed_port(self.root))

    def test_0_8_2_is_not_a_supported_client(self):
        self._write("version.xml", "<version> v.0.8.2 #100 </version>")
        status = core.inspect_game_root(self.root)
        self.assertFalse(status["has_executable"])
        self.assertFalse(status["mod_installed"])
        self.assertIsNone(status["client"])

    def test_an_old_0_8_2_install_marker_does_not_restore_support(self):
        self._write("version.xml", "<version> v.0.8.2 #100 </version>")
        self._write(core.GAME_EXECUTABLE)
        self._write(os.path.join(
            "res_mods", "0.8.2", "scripts", "client", "gui", "mods",
            "offhangar", "__init__.py"))
        status = core.inspect_game_root(self.root)
        self.assertTrue(status["has_executable"])
        self.assertIsNone(status["client"])
        self.assertFalse(status["mod_installed"])


class SessionPlanTest(unittest.TestCase):
    @staticmethod
    def _status(**overrides):
        status = {
            "path": "C:\\Games\\WoT",
            "has_executable": True,
            "version": "0.9.22.0.1",
            "client": core.PORT_0_9_22,
            "mod_installed": True,
        }
        status.update(overrides)
        return status

    def test_join_plan_carries_the_typed_endpoint(self):
        session = core.plan_session(self._status(), core.MODE_JOIN,
                                    "10.0.0.5:1234")
        self.assertEqual(session["host"], "10.0.0.5")
        self.assertEqual(session["tcp_port"], 1234)
        self.assertFalse(session["needs_server"])

    def test_0_9_22_single_player_plan_starts_a_local_server(self):
        session = core.plan_session(
            self._status(), core.MODE_SINGLE, team_size="7",
            vehicle_profile="Fast MS-1")
        self.assertEqual(session["host"], core.LOCAL_HOST)
        self.assertTrue(session["needs_server"])
        self.assertEqual(7, session["team_size"])
        self.assertEqual("Fast MS-1", session["vehicle_profile"])

    def test_modified_profile_is_refused_for_lan(self):
        for status, mode in (
                (self._status(), core.MODE_HOST),
                (self._status(), core.MODE_JOIN)):
            with self.assertRaisesRegex(
                    core.LauncherError, "limited to 0.9.22 single player"):
                core.plan_session(
                    status, mode, "10.0.0.5" if mode == core.MODE_JOIN else "",
                    vehicle_profile="Fast MS-1")

    def test_0_9_22_team_size_must_be_between_one_and_fifteen(self):
        for value in ("", "four", 0, 16, 1.5, True):
            with self.assertRaises(core.LauncherError, msg=value):
                core.plan_session(
                    self._status(), core.MODE_HOST, team_size=value)

    def test_join_does_not_apply_the_local_team_size(self):
        session = core.plan_session(
            self._status(), core.MODE_JOIN, "10.0.0.5", team_size="invalid")
        self.assertEqual(core.DEFAULT_TEAM_SIZE, session["team_size"])

    def test_0_8_2_session_is_refused(self):
        with self.assertRaisesRegex(
                core.LauncherError, "not supported"):
            core.plan_session(
                self._status(client=core.PORT_0_8_2, version="0.8.2"),
                core.MODE_SINGLE)

    def test_a_missing_executable_stops_the_session(self):
        self.assertRaises(core.LauncherError, core.plan_session,
                          self._status(has_executable=False), core.MODE_SINGLE)

    def test_an_unsupported_client_stops_the_session(self):
        self.assertRaises(core.LauncherError, core.plan_session,
                          self._status(client=None), core.MODE_SINGLE)

    def test_an_unknown_mode_stops_the_session(self):
        self.assertRaises(core.LauncherError, core.plan_session,
                          self._status(), "spectate")

    def test_an_invalid_join_address_stops_the_session(self):
        self.assertRaises(core.LauncherError, core.plan_session,
                          self._status(), core.MODE_JOIN, "")

    def test_a_missing_mod_still_plans_a_session(self):
        session = core.plan_session(self._status(mod_installed=False),
                                    core.MODE_HOST)
        self.assertTrue(session["needs_server"])


class SettingsFileTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _read(self, relative_path):
        with open(os.path.join(self.root, relative_path), "rb") as stream:
            return json.load(stream)

    def test_0_8_2_join_enables_network_mode_and_keeps_other_keys(self):
        path = os.path.join(self.root, "offhangar_user", "config.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as stream:
            json.dump({"bots_per_team": 7, "network_mode": False}, stream)
        core.write_settings(self.root, core.PORT_0_8_2, core.MODE_JOIN,
                            "10.0.0.5", 1234, "Peng")
        config = self._read(os.path.join("offhangar_user", "config.json"))
        self.assertEqual(config["bots_per_team"], 7)
        self.assertTrue(config["network_mode"])
        self.assertEqual(config["network_server_host"], "10.0.0.5")
        self.assertEqual(config["network_server_port"], 1234)
        self.assertEqual(config["network_map_name"], "server_random")
        self.assertEqual(config["nickname"], "Peng")

    def test_0_8_2_single_player_still_plays_against_the_server(self):
        core.write_settings(self.root, core.PORT_0_8_2, core.MODE_SINGLE,
                            core.LOCAL_HOST, core.DEFAULT_SERVER_PORT)
        config = self._read(os.path.join("offhangar_user", "config.json"))
        self.assertTrue(config["network_mode"])
        self.assertEqual(config["network_server_host"], core.LOCAL_HOST)
        self.assertNotIn("nickname", config)

    def test_0_9_22_writes_the_user_owned_endpoint(self):
        written = core.write_settings(self.root, core.PORT_0_9_22,
                                      core.MODE_JOIN, "10.0.0.5", 1234)
        endpoint = self._read(os.path.join(
            "mods", "configs", "offline_lan_0922", "server_endpoint.json"))
        self.assertEqual(endpoint, {"schema": 1, "host": "10.0.0.5",
                                    "port": 1234})
        self.assertEqual(len(written), 1)

    def test_0_9_22_name_updates_an_existing_config(self):
        config_path = os.path.join(self.root, "mods", "configs",
                                   "offline_lan_0922", "config.json")
        os.makedirs(os.path.dirname(config_path))
        with open(config_path, "w") as stream:
            json.dump({"schema": 1, "name": "Player", "max_health": 90}, stream)
        core.write_settings(self.root, core.PORT_0_9_22, core.MODE_HOST,
                            core.LOCAL_HOST, core.DEFAULT_SERVER_PORT, "Peng")
        config = self._read(os.path.join(
            "mods", "configs", "offline_lan_0922", "config.json"))
        self.assertEqual(config["name"], "Peng")
        self.assertEqual(config["max_health"], 90)

    def test_0_9_22_name_updates_a_windows_read_only_config(self):
        config_path = os.path.join(self.root, "mods", "configs",
                                   "offline_lan_0922", "config.json")
        os.makedirs(os.path.dirname(config_path))
        with open(config_path, "w") as stream:
            json.dump({"schema": 1, "name": "Player", "max_health": 90},
                      stream)
        os.chmod(config_path, stat.S_IREAD)
        original_replace = os.replace
        config_replace_attempts = []

        def windows_replace(source, target):
            if target == config_path:
                config_replace_attempts.append((source, target))
            if target == config_path and len(config_replace_attempts) == 1:
                error = PermissionError(13, "Access is denied", target)
                error.winerror = 5
                raise error
            return original_replace(source, target)

        with mock.patch("core.os.replace", side_effect=windows_replace):
            core.write_settings(
                self.root, core.PORT_0_9_22, core.MODE_HOST,
                core.LOCAL_HOST, core.DEFAULT_SERVER_PORT, "Peng")

        config = self._read(os.path.join(
            "mods", "configs", "offline_lan_0922", "config.json"))
        self.assertEqual(config["name"], "Peng")
        self.assertEqual(config["max_health"], 90)
        self.assertEqual(len(config_replace_attempts), 2)
        self.assertFalse(os.path.exists(config_path + ".tmp"))

    def test_unsupported_port_is_rejected(self):
        self.assertRaises(core.LauncherError, core.write_settings, self.root,
                          "0.1.0", core.MODE_SINGLE, core.LOCAL_HOST, 1)


class ServerPayloadTest(unittest.TestCase):
    def test_server_log_lives_beside_the_frozen_launcher(self):
        executable = os.path.join(
            tempfile.gettempdir(), "portable-launcher", "Launcher.exe")

        self.assertEqual(
            os.path.join(os.path.dirname(executable), "server.log"),
            core.server_log_path(executable=executable, frozen=True))

    def test_source_server_log_lives_beside_the_launcher_script(self):
        self.assertEqual(
            os.path.join(os.path.dirname(core.__file__), "server.log"),
            core.server_log_path(frozen=False))

    def test_repository_layout_resolves_both_servers(self):
        base = core.server_root()
        self.assertTrue(os.path.isfile(core.server_script(core.PORT_0_8_2,
                                                          base)))
        self.assertTrue(os.path.isfile(core.server_script(core.PORT_0_9_22,
                                                          base)))

    def test_0_8_2_server_receives_bind_arguments(self):
        argv = core.server_argv(core.PORT_0_8_2, "/payload")
        self.assertEqual(argv[1:], ["--host", core.LISTEN_HOST, "--port",
                                    str(core.DEFAULT_SERVER_PORT)])

    def test_0_9_22_server_binds_without_arguments(self):
        argv = core.server_argv(core.PORT_0_9_22, "/payload")
        self.assertEqual(argv[1:], [])

    def test_frozen_command_reruns_the_launcher_executable(self):
        command = core.server_child_command(
            core.PORT_0_8_2, executable="C:\\launcher.exe", frozen=True)
        self.assertEqual(command, ["C:\\launcher.exe", core.SERVE_FLAG,
                                   core.PORT_0_8_2])

    def test_source_command_passes_the_launcher_script(self):
        command = core.server_child_command(
            core.PORT_0_9_22, launcher_script="/repo/launcher/wot_launcher.py",
            executable="/usr/bin/python3", frozen=False)
        self.assertEqual(command, ["/usr/bin/python3",
                                   "/repo/launcher/wot_launcher.py",
                                   core.SERVE_FLAG, core.PORT_0_9_22])

    def test_each_server_receives_its_client_baked_data_directory(self):
        environment = core.server_environment(core.PORT_0_8_2, "/game", {})
        self.assertTrue(environment[core.NAVGRAPH_DIR_ENV].endswith("navgraphs"))
        self.assertIn("/game", environment[core.NAVGRAPH_DIR_ENV])
        self.assertNotIn(core.SERVER_DATA_ENV_0922, environment)
        environment = core.server_environment(core.PORT_0_9_22, "/game", {})
        self.assertTrue(environment[core.SERVER_DATA_ENV_0922].endswith(
            os.path.join("configs", "offline_lan_0922")))
        self.assertIn("/game", environment[core.SERVER_DATA_ENV_0922])
        self.assertEqual(
            str(core.DEFAULT_TEAM_SIZE),
            environment[core.SERVER_TEAM_SIZE_ENV_0922])
        self.assertNotIn(core.NAVGRAPH_DIR_ENV, environment)

    def test_0_9_22_server_receives_the_selected_team_size(self):
        environment = core.server_environment(
            core.PORT_0_9_22, "/game", {}, team_size=4)
        self.assertEqual("4", environment[core.SERVER_TEAM_SIZE_ENV_0922])

    def test_single_player_server_is_explicitly_loopback_only(self):
        environment = core.server_environment(
            core.PORT_0_9_22, "/game", {}, loopback_only=True)
        self.assertEqual(
            "1", environment[core.SERVER_LOOPBACK_ONLY_ENV_0922])
        lan_environment = core.server_environment(
            core.PORT_0_9_22, "/game",
            {core.SERVER_LOOPBACK_ONLY_ENV_0922: "1"})
        self.assertNotIn(core.SERVER_LOOPBACK_ONLY_ENV_0922, lan_environment)

    def test_hidden_worker_inherits_the_selected_server_endpoint(self):
        environment = core.worker_environment(
            "/game", "10.0.0.5", 1234, team_size=7, environment={})
        self.assertEqual(
            "10.0.0.5", environment[core.CLIENT_SERVER_HOST_ENV_0922])
        self.assertEqual(
            "1234", environment[core.CLIENT_SERVER_PORT_ENV_0922])
        self.assertEqual("7", environment[core.SERVER_TEAM_SIZE_ENV_0922])
        self.assertEqual(
            [os.path.join("/game", core.WORKER_STARTER_FILENAME_0922),
             core.WORKER_ONLY_ARGUMENT_0922],
            core.worker_child_command("/game"))

    def test_visible_0_9_22_client_uses_isolated_config_and_endpoint(self):
        command = core.visible_client_command("/game", core.PORT_0_9_22)
        self.assertEqual(os.path.join("/game", core.GAME_EXECUTABLE), command[0])
        self.assertIn(core.PLAYER_ENGINE_CONFIG_0922, command)
        self.assertEqual(
            [os.path.join("/game", core.WORKER_STARTER_FILENAME_0922),
             core.PAIRED_PLAYER_ARGUMENT_0922],
            core.visible_client_command(
                "/game", core.PORT_0_9_22, paired_worker=True))
        environment = core.visible_client_environment(
            core.PORT_0_9_22, "10.0.0.5", 1234, paired_worker=True,
            environment={
                core.CLIENT_MODE_ENV_0922: "simulation_worker",
                core.HIDDEN_DESKTOP_ENV_0922: "1",
                core.WORKER_READY_MARKER_ENV_0922: "stale",
            })
        self.assertEqual(
            "10.0.0.5", environment[core.CLIENT_SERVER_HOST_ENV_0922])
        self.assertEqual(
            "1234", environment[core.CLIENT_SERVER_PORT_ENV_0922])
        self.assertEqual(
            "1", environment[core.ALLOW_MULTIPLE_CLIENTS_ENV_0922])
        for name in (core.CLIENT_MODE_ENV_0922,
                     core.HIDDEN_DESKTOP_ENV_0922,
                     core.WORKER_READY_MARKER_ENV_0922):
            self.assertNotIn(name, environment)

    def test_missing_payload_reports_a_launcher_error(self):
        self.assertRaises(core.LauncherError, core.run_server_payload,
                          core.PORT_0_8_2, tempfile.mkdtemp())


class ClientInstallTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)
        self.game = os.path.join(self.work, "game")
        self.payload = os.path.join(self.work, "payload")
        os.makedirs(self.game)

    def _write(self, root, relative_path, text="x"):
        path = os.path.join(root, *relative_path.split("/"))
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w") as stream:
            stream.write(text)
        return path

    def _read(self, relative_path):
        with open(os.path.join(self.game, *relative_path.split("/"))) as stream:
            return stream.read()

    def _archive(self, port_version, members):
        directory = os.path.join(self.payload, core.CLIENT_PAYLOAD_DIR)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        path = os.path.join(directory, "%s.zip" % port_version)
        with zipfile.ZipFile(path, "w") as archive:
            for member, content in members.items():
                archive.writestr(member, content)
        return path

    def _stage_0_8_2(self, content="new"):
        members = {
            "res_mods/0.8.2/scripts/client/CameraNode.pyc": content,
            "res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.py": content,
            "res_mods/0.8.2/gui/maps/icons/offhangar/pixel.dds": content,
        }
        records = []
        data_root = (
            "res_mods/0.8.2/scripts/client/gui/mods/offhangar/navgraphs")
        for index in range(33):
            filename = "map-%02d.json" % index
            records.append({"file": filename})
            members["%s/%s" % (data_root, filename)] = content
        members["%s/manifest.json" % data_root] = json.dumps({"maps": records})
        return self._archive("0.8.2", members)

    def _stage_0_9_22(self, content="new"):
        members = {
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_9.9.9.wotmod": content,
            "mods/0.9.22.0.1/offline_instance_guard_native.pyd": content,
            "mods/configs/offline_lan_0922/config.json": content,
            "offline_worker_starter.exe": content,
            "res_mods/0.9.22.0.1/engine_config.offline-player.xml": content,
            "res_mods/0.9.22.0.1/engine_config.offline-worker.xml": content,
        }
        for name in ("navgraphs", "foliage", "destructibles", "occluders"):
            records = []
            for index in range(41):
                filename = "map-%02d.json" % index
                records.append({"file": filename})
                members[
                    "mods/configs/offline_lan_0922/%s/%s" %
                    (name, filename)
                ] = content
            members[
                "mods/configs/offline_lan_0922/%s/manifest.json" % name
            ] = json.dumps({"maps": records})
        return self._archive("0.9.22", members)

    def _make_0_9_22_target(self):
        self._write(self.game, core.GAME_EXECUTABLE, "")
        self._write(
            self.game, "version.xml",
            "<version> v.0.9.22.0.1 #1513 </version>")
        engine_config = preferences_overlay.packed_xml.PackedElement(children=[
            (b"preferences", preferences_overlay.packed_xml.PackedValue(
                preferences_overlay.packed_xml.TYPE_STRING,
                b"preferences.xml")),
        ])
        path = os.path.join(self.game, "res", "engine_config.xml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(preferences_overlay.packed_xml.write_packed_xml(
                engine_config))

    def test_0_8_2_install_replaces_the_whole_mod_directory(self):
        self._stage_0_8_2()
        self._write(self.game, "res_mods/0.8.2/leftover.txt", "stale")
        actions = core.install_client_mod(self.game, core.PORT_0_8_2,
                                          self.payload)
        self.assertFalse(os.path.exists(
            os.path.join(self.game, "res_mods", "0.8.2", "leftover.txt")))
        self.assertEqual("new", self._read(
            "res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.py"))
        self.assertTrue(any("Replaced the old" in action
                            for action in actions))

    def test_0_8_2_install_carries_the_loader_bytecode(self):
        self._stage_0_8_2()
        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload)
        self.assertTrue(os.path.isfile(os.path.join(
            self.game, "res_mods", "0.8.2", "scripts", "client",
            "CameraNode.pyc")))

    def test_0_8_2_install_keeps_the_user_directory(self):
        self._stage_0_8_2()
        self._write(self.game, "offhangar_user/config.json", "mine")
        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload)
        self.assertEqual("mine", self._read("offhangar_user/config.json"))

    def test_0_9_22_install_replaces_old_packages_and_data(self):
        self._stage_0_9_22()
        self._write(self.game,
                    "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.1.0.wotmod",
                    "stale")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertFalse(os.path.exists(os.path.join(
            self.game, "mods", "0.9.22.0.1",
            "org.peng.offline_lan_0922_0.1.0.wotmod")))
        self.assertEqual("new", self._read(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_9.9.9.wotmod"))

    def test_0_9_22_install_removes_stale_baked_data(self):
        self._stage_0_9_22()
        self._write(self.game,
                    "mods/configs/offline_lan_0922/navgraphs/stale.json",
                    "stale")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertFalse(os.path.exists(os.path.join(
            self.game, "mods", "configs", "offline_lan_0922", "navgraphs",
            "stale.json")))

    def test_0_9_22_install_keeps_another_authors_mod(self):
        self._stage_0_9_22()
        self._write(self.game, "mods/0.9.22.0.1/com.other.mod.wotmod", "theirs")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertEqual("theirs",
                         self._read("mods/0.9.22.0.1/com.other.mod.wotmod"))

    def test_0_9_22_install_keeps_the_saved_settings(self):
        self._stage_0_9_22()
        self._write(self.game,
                    "mods/configs/offline_lan_0922/server_endpoint.json", "mine")
        self._write(self.game, "mods/configs/offline_lan_0922/config.json",
                    "mine")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertEqual("mine", self._read(
            "mods/configs/offline_lan_0922/server_endpoint.json"))
        self.assertEqual("mine", self._read(
            "mods/configs/offline_lan_0922/config.json"))

    def test_0_9_22_install_writes_a_missing_configuration(self):
        self._stage_0_9_22()
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertEqual("new", self._read(
            "mods/configs/offline_lan_0922/config.json"))

    def test_startup_repair_quarantines_only_an_invalid_config(self):
        default_config = json.dumps({
            "enabled": True,
            "startupTimeoutSeconds": 30.0,
            "physics_tuning": {},
            "he_tuning": {},
            "perfect_accuracy": False,
        })
        self._stage_0_9_22(default_config)
        self._make_0_9_22_target()
        self._write(
            self.game, "mods/configs/offline_lan_0922/config.json",
            "{broken")
        for name in ("server_endpoint.json", "account_state.json",
                     "garage_state.json"):
            self._write(
                self.game, "mods/configs/offline_lan_0922/" + name,
                "saved-" + name)
        postbattle = json.dumps({
            "schema": 1,
            "accountKey": "offline",
            "pending": [{"arenaUniqueID": 17}],
            "history": [],
            "progress": {},
        })
        self._write(
            self.game,
            "mods/configs/offline_lan_0922/postbattle_state.json",
            postbattle)
        self._write(
            self.game, "mods/0.9.22.0.1/com.other.mod.wotmod", "theirs")

        actions = core.repair_0_9_22_startup(
            self.game, self.payload, is_running=lambda: False)

        self.assertEqual(default_config, self._read(
            "mods/configs/offline_lan_0922/config.json"))
        self.assertEqual("{broken", self._read(
            "mods/configs/offline_lan_0922/config.json.invalid"))
        for name in ("server_endpoint.json", "account_state.json",
                     "garage_state.json"):
            self.assertEqual("saved-" + name, self._read(
                "mods/configs/offline_lan_0922/" + name))
        self.assertEqual(postbattle, self._read(
            "mods/configs/offline_lan_0922/postbattle_state.json"))
        self.assertEqual("theirs", self._read(
            "mods/0.9.22.0.1/com.other.mod.wotmod"))
        self.assertIn("kept the saved endpoint", " ".join(actions))

    def test_startup_repair_keeps_a_valid_config(self):
        default_config = json.dumps({"enabled": True})
        self._stage_0_9_22(default_config)
        self._make_0_9_22_target()
        saved_config = json.dumps({
            "enabled": False, "startupTimeoutSeconds": 45.0})
        self._write(
            self.game, "mods/configs/offline_lan_0922/config.json",
            saved_config)

        core.repair_0_9_22_startup(
            self.game, self.payload, is_running=lambda: False)

        self.assertEqual(saved_config, self._read(
            "mods/configs/offline_lan_0922/config.json"))
        self.assertFalse(os.path.exists(os.path.join(
            self.game, "mods", "configs", "offline_lan_0922",
            "config.json.invalid")))

    def test_startup_repair_refuses_to_touch_a_running_game(self):
        self._stage_0_9_22(json.dumps({"enabled": True}))
        self._make_0_9_22_target()
        self._write(
            self.game, "mods/configs/offline_lan_0922/config.json",
            "{broken")

        with self.assertRaisesRegex(core.LauncherError, "Close World of Tanks"):
            core.repair_0_9_22_startup(
                self.game, self.payload, is_running=lambda: True)

        self.assertEqual("{broken", self._read(
            "mods/configs/offline_lan_0922/config.json"))

    def test_failed_startup_repair_restores_the_invalid_config(self):
        self._make_0_9_22_target()
        self._write(
            self.game, "mods/configs/offline_lan_0922/config.json",
            "{broken")

        def fail_install(game_root, port_version, base_dir, force):
            self._write(
                self.game, "mods/configs/offline_lan_0922/config.json",
                "partial-default")
            raise core.LauncherError("install failed")

        with mock.patch.object(
                core, "install_client_mod", side_effect=fail_install):
            with self.assertRaisesRegex(core.LauncherError, "install failed"):
                core.repair_0_9_22_startup(
                    self.game, self.payload, is_running=lambda: False)

        self.assertEqual("{broken", self._read(
            "mods/configs/offline_lan_0922/config.json"))
        self.assertFalse(os.path.exists(os.path.join(
            self.game, "mods", "configs", "offline_lan_0922",
            "config.json.invalid")))

    def test_normal_client_preferences_are_moved_to_a_recoverable_backup(self):
        self._make_0_9_22_target()
        app_data = os.path.join(self.work, "app-data")
        preferences = os.path.join(
            app_data,
            *preferences_overlay.NORMAL_PROFILE_RELATIVE_PATH.split("/"))
        os.makedirs(os.path.dirname(preferences))
        with open(preferences, "w") as stream:
            stream.write("normal client settings")
        first_backup = preferences + ".wot-offline-backup-20260822-120000"
        with open(first_backup, "w") as stream:
            stream.write("older backup")

        actions = core.backup_normal_client_preferences(
            self.game, is_running=lambda: False,
            environment={"APPDATA": app_data},
            timestamp="20260822-120000")

        backup = first_backup + "-1"
        self.assertFalse(os.path.lexists(preferences))
        with open(first_backup) as stream:
            self.assertEqual("older backup", stream.read())
        with open(backup) as stream:
            self.assertEqual("normal client settings", stream.read())
        self.assertIn(backup, actions[0])

    def test_normal_client_preferences_cleanup_is_idempotent(self):
        self._make_0_9_22_target()
        app_data = os.path.join(self.work, "empty-app-data")

        actions = core.backup_normal_client_preferences(
            self.game, is_running=lambda: False,
            environment={"APPDATA": app_data},
            timestamp="20260822-120000")

        self.assertIn("already absent", actions[0])

    def test_normal_client_preferences_cleanup_refuses_a_file_link(self):
        self._make_0_9_22_target()
        app_data = os.path.join(self.work, "linked-app-data")
        preferences = os.path.join(
            app_data,
            *preferences_overlay.NORMAL_PROFILE_RELATIVE_PATH.split("/"))
        os.makedirs(os.path.dirname(preferences))
        target = os.path.join(self.work, "outside-preferences.xml")
        with open(target, "w") as stream:
            stream.write("outside")
        try:
            os.symlink(target, preferences)
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")

        with self.assertRaisesRegex(core.LauncherError, "regular file"):
            core.backup_normal_client_preferences(
                self.game, is_running=lambda: False,
                environment={"APPDATA": app_data},
                timestamp="20260822-120000")

        self.assertTrue(os.path.islink(preferences))
        with open(target) as stream:
            self.assertEqual("outside", stream.read())

    def test_reset_deletes_only_known_offline_state_after_confirmation(self):
        default_config = json.dumps({"enabled": True})
        self._stage_0_9_22(default_config)
        self._make_0_9_22_target()
        state_root = "mods/configs/offline_lan_0922/"
        for name in ("config.json", "config.json.invalid",
                     "server_endpoint.json", "server_endpoint.json.tmp",
                     "account_state.json", "garage_state.json.bak",
                     "postbattle_state.json"):
            self._write(self.game, state_root + name, "saved-" + name)
        self._write(self.game, state_root + "notes.json", "keep")
        self._write(
            self.game, state_root + "vehicle_profiles.json", "profiles")
        self._write(
            self.game, "mods/0.9.22.0.1/com.other.mod.wotmod", "theirs")

        actions = core.reset_0_9_22_state(
            self.game, self.payload, is_running=lambda: False)

        self.assertEqual(default_config, self._read(state_root + "config.json"))
        for name in ("config.json.invalid", "server_endpoint.json",
                     "server_endpoint.json.tmp", "account_state.json",
                     "garage_state.json.bak", "postbattle_state.json"):
            self.assertFalse(os.path.exists(os.path.join(
                self.game, *(state_root + name).split("/"))))
        self.assertEqual("keep", self._read(state_root + "notes.json"))
        self.assertEqual(
            "profiles", self._read(state_root + "vehicle_profiles.json"))
        self.assertEqual("theirs", self._read(
            "mods/0.9.22.0.1/com.other.mod.wotmod"))
        self.assertIn("Deleted 7 offline saved-data file(s).", actions)

    def test_reset_also_deletes_only_the_isolated_client_preferences(self):
        default_config = json.dumps({"enabled": True})
        self._stage_0_9_22(default_config)
        self._make_0_9_22_target()
        preferences = os.path.join(
            self.work, "local-app-data",
            *preferences_overlay.PROFILE_RELATIVE_PATH.split("/"))
        os.makedirs(os.path.dirname(preferences))
        with open(preferences, "w") as stream:
            stream.write("offline graphics and input settings")

        with mock.patch.object(
                core, "_isolated_0_9_22_preferences_path",
                return_value=preferences):
            actions = core.reset_0_9_22_state(
                self.game, self.payload, is_running=lambda: False)

        self.assertFalse(os.path.exists(preferences))
        self.assertIn("Deleted 1 offline saved-data file(s).", actions)

    def test_failed_reset_restores_the_isolated_client_preferences(self):
        self._make_0_9_22_target()
        preferences = os.path.join(
            self.work, "local-app-data",
            *preferences_overlay.PROFILE_RELATIVE_PATH.split("/"))
        os.makedirs(os.path.dirname(preferences))
        with open(preferences, "w") as stream:
            stream.write("keep me")

        with mock.patch.object(
                core, "_isolated_0_9_22_preferences_path",
                return_value=preferences), mock.patch.object(
                    core, "install_client_mod",
                    side_effect=core.LauncherError("install failed")):
            with self.assertRaisesRegex(core.LauncherError, "install failed"):
                core.reset_0_9_22_state(
                    self.game, self.payload, is_running=lambda: False)

        with open(preferences) as stream:
            self.assertEqual("keep me", stream.read())

    def test_failed_reset_restores_every_saved_file(self):
        self._make_0_9_22_target()
        state_root = "mods/configs/offline_lan_0922/"
        for name in ("config.json", "server_endpoint.json",
                     "garage_state.json", "postbattle_state.json"):
            self._write(self.game, state_root + name, "saved-" + name)

        with mock.patch.object(
                core, "install_client_mod",
                side_effect=core.LauncherError("install failed")):
            with self.assertRaisesRegex(core.LauncherError, "install failed"):
                core.reset_0_9_22_state(
                    self.game, self.payload, is_running=lambda: False)

        for name in ("config.json", "server_endpoint.json",
                     "garage_state.json", "postbattle_state.json"):
            self.assertEqual("saved-" + name,
                             self._read(state_root + name))

    def test_the_same_package_is_not_installed_twice(self):
        self._stage_0_8_2()
        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload)
        self._write(self.game, "res_mods/0.8.2/leftover.txt", "kept")

        actions = core.install_client_mod(self.game, core.PORT_0_8_2,
                                          self.payload)

        self.assertEqual(["The 0.8.2 mod is already up to date."], actions)
        self.assertEqual("kept", self._read("res_mods/0.8.2/leftover.txt"))

    def test_a_missing_required_file_forces_a_reinstall(self):
        self._stage_0_9_22()
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        manifest = os.path.join(
            self.game, "mods", "configs", "offline_lan_0922", "occluders",
            "manifest.json")
        os.unlink(manifest)

        actions = core.install_client_mod(
            self.game, core.PORT_0_9_22, self.payload)

        self.assertTrue(os.path.isfile(manifest))
        self.assertNotIn("already up to date", " ".join(actions))

    def test_a_missing_manifest_referenced_map_forces_a_reinstall(self):
        self._stage_0_9_22()
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        map_path = os.path.join(
            self.game, "mods", "configs", "offline_lan_0922", "occluders",
            "map-17.json")
        os.unlink(map_path)

        actions = core.install_client_mod(
            self.game, core.PORT_0_9_22, self.payload)

        self.assertTrue(os.path.isfile(map_path))
        self.assertNotIn("already up to date", " ".join(actions))

    def test_a_missing_0_8_2_navgraph_forces_a_reinstall(self):
        self._stage_0_8_2()
        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload)
        map_path = os.path.join(
            self.game, "res_mods", "0.8.2", "scripts", "client", "gui",
            "mods", "offhangar", "navgraphs", "map-17.json")
        os.unlink(map_path)

        actions = core.install_client_mod(
            self.game, core.PORT_0_8_2, self.payload)

        self.assertTrue(os.path.isfile(map_path))
        self.assertNotIn("already up to date", " ".join(actions))

    def test_an_archive_missing_a_manifest_referenced_map_is_rejected(self):
        archive_path = self._stage_0_9_22()
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {
                name: archive.read(name) for name in archive.namelist()
                if name != ("mods/configs/offline_lan_0922/occluders/"
                            "map-17.json")
            }
        self._archive(core.PORT_0_9_22, members)
        self._write(
            self.game,
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_old.wotmod",
            "previous")

        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

        self.assertEqual("previous", self._read(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_old.wotmod"))

    def test_a_malformed_0_9_22_manifest_archive_is_rejected(self):
        archive_path = self._stage_0_9_22()
        manifest_name = (
            "mods/configs/offline_lan_0922/occluders/manifest.json")
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {name: archive.read(name)
                       for name in archive.namelist()}
        members[manifest_name] = "not json"
        self._archive(core.PORT_0_9_22, members)

        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

    def test_an_0_8_2_archive_missing_a_navgraph_is_rejected(self):
        archive_path = self._stage_0_8_2()
        missing = (
            "res_mods/0.8.2/scripts/client/gui/mods/offhangar/navgraphs/"
            "map-17.json")
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {name: archive.read(name)
                       for name in archive.namelist() if name != missing}
        self._archive(core.PORT_0_8_2, members)

        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_8_2, self.payload)

    def test_a_new_package_replaces_the_installed_one(self):
        self._stage_0_8_2()
        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload)
        self._stage_0_8_2(content="newer")

        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload)

        self.assertEqual("newer", self._read(
            "res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.py"))

    def test_a_forced_install_ignores_the_marker(self):
        self._stage_0_8_2()
        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload)
        self._write(self.game, "res_mods/0.8.2/leftover.txt", "stale")

        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload,
                                force=True)

        self.assertFalse(os.path.exists(
            os.path.join(self.game, "res_mods", "0.8.2", "leftover.txt")))

    def test_a_launcher_without_mod_files_reports_it(self):
        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_8_2, self.payload)

    def test_a_member_outside_the_game_folder_is_refused(self):
        self._archive("0.8.2", {"../escape.txt": "no"})
        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_8_2, self.payload)

    def test_an_unexpected_payload_file_type_is_refused(self):
        archive_path = self._stage_0_8_2()
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {name: archive.read(name)
                       for name in archive.namelist()}
        members["res_mods/0.8.2/scripts/client/development.exe"] = "no"
        self._archive(core.PORT_0_8_2, members)
        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_8_2, self.payload)

    def test_an_unrelated_0_9_22_mod_is_not_installed_from_the_payload(self):
        archive_path = self._stage_0_9_22()
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {name: archive.read(name)
                       for name in archive.namelist()}
        members["mods/0.9.22.0.1/com.other.mod.wotmod"] = "no"
        self._archive(core.PORT_0_9_22, members)
        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

    def test_an_invalid_archive_does_not_remove_the_previous_mod(self):
        self._archive("0.8.2", {
            "res_mods/0.8.2/scripts/client/CameraNode.pyc": "new",
            "../escape.txt": "no",
        })
        self._write(self.game, "res_mods/0.8.2/previous.txt", "previous")

        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_8_2, self.payload)

        self.assertEqual("previous",
                         self._read("res_mods/0.8.2/previous.txt"))

    def test_a_failed_atomic_swap_restores_the_previous_mod(self):
        self._stage_0_8_2()
        self._write(self.game, "res_mods/0.8.2/previous.txt", "previous")
        original_replace = os.replace

        def replace(source, target):
            normalized = source.replace("\\", "/")
            if ("/.wot-offline-install-" in normalized and
                    "/new/res_mods/0.8.2" in normalized):
                raise OSError("synthetic install failure")
            return original_replace(source, target)

        with mock.patch("core.os.replace", side_effect=replace):
            self.assertRaises(core.LauncherError, core.install_client_mod,
                              self.game, core.PORT_0_8_2, self.payload)

        self.assertEqual("previous",
                         self._read("res_mods/0.8.2/previous.txt"))

    def test_an_unwritable_game_folder_reports_a_permission_remedy(self):
        self._stage_0_8_2()
        with mock.patch("tempfile.mkdtemp",
                        side_effect=PermissionError("access denied")):
            with self.assertRaises(core.LauncherError) as caught:
                core.install_client_mod(
                    self.game, core.PORT_0_8_2, self.payload)
        self.assertIn("not writable", str(caught.exception))
        self.assertIn("permission", str(caught.exception))


class PayloadStagingTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(), "payload")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.root), True)
        self.written = stage_payload.stage(self.root, include_clients=False)
        self.target = os.path.join(self.root, stage_payload.SERVER_DIR)

    @staticmethod
    def _write_0_8_2_navgraphs(source):
        data_root = os.path.join(
            source, "0.8.2", "scripts", "client", "gui", "mods",
            "offhangar", "navgraphs")
        os.makedirs(data_root)
        records = []
        for index in range(33):
            filename = "map-%02d.json" % index
            records.append({"file": filename})
            with open(os.path.join(data_root, filename), "w") as stream:
                stream.write("{}")
        with open(os.path.join(data_root, "manifest.json"), "w") as stream:
            json.dump({"maps": records}, stream)

    @staticmethod
    def _write_0_9_22_data(overlay):
        runtime_files = (
            "offline_worker_starter.exe",
            "mods/0.9.22.0.1/offline_instance_guard_native.pyd",
            "res_mods/0.9.22.0.1/engine_config.offline-player.xml",
            "res_mods/0.9.22.0.1/engine_config.offline-worker.xml",
        )
        for relative in runtime_files:
            path = os.path.join(overlay, *relative.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as stream:
                stream.write("runtime")
        data_root = os.path.join(
            overlay, "mods", "configs", "offline_lan_0922")
        for dataset in ("navgraphs", "foliage", "destructibles",
                        "occluders"):
            dataset_root = os.path.join(data_root, dataset)
            os.makedirs(dataset_root)
            records = []
            for index in range(41):
                filename = "map-%02d.json" % index
                records.append({"file": filename})
                with open(os.path.join(dataset_root, filename), "w") as stream:
                    stream.write("{}")
            with open(os.path.join(dataset_root, "manifest.json"), "w") as stream:
                json.dump({"maps": records}, stream)

    def test_supported_server_entry_points_are_staged(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(
                os.path.isfile(core.server_script(port_version, self.target)),
                port_version)
        self.assertFalse(os.path.exists(os.path.join(self.target, "0.8.2")))

    def test_the_0_9_22_server_finds_its_client_modules(self):
        self.assertTrue(os.path.isfile(os.path.join(
            self.target, "0.9.22", "src", "res", "scripts", "client", "gui",
            "mods", "offline_lan_0922", "ai", "maps.py")))

    def test_the_0_9_22_server_stages_its_reward_module(self):
        self.assertTrue(os.path.isfile(os.path.join(
            self.target, "0.9.22", "server", "offline_rewards.py")))

    def test_the_navigation_graphs_stay_out_of_the_bundle(self):
        self.assertFalse(any(
            os.path.sep + "navgraphs" + os.path.sep in path
            for path in self.written))

    def test_client_staging_carries_only_the_0_9_22_mod(self):
        source = os.path.join(tempfile.mkdtemp(), "repo")
        self.addCleanup(shutil.rmtree, os.path.dirname(source), True)
        overlay = os.path.join(source, "0.9.22", "dist",
                               "WoT-0.9.22-LAN-Client-abc1234")
        relative_paths = [
            os.path.join("0.8.2", "scripts", "client", "a.py"),
            os.path.join("0.8.2", "scripts", "client", "CameraNode.pyc"),
            os.path.join("0.8.2", "scripts", "client", "gui", "mods",
                         "mod_offhangar.py"),
            os.path.join("0.8.2", "gui", "maps", "a.dds"),
            os.path.join(overlay, "mods", "0.9.22.0.1",
                         "org.peng.offline_lan_0922_0.6.0-alpha.1.wotmod"),
            os.path.join(overlay, "mods", "configs", "offline_lan_0922",
                         "config.json"),
        ]
        for relative in relative_paths:
            path = os.path.join(source, relative)
            if not os.path.isdir(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            with open(path, "w") as stream:
                stream.write("x")
        self._write_0_8_2_navgraphs(source)
        self._write_0_9_22_data(overlay)
        target = os.path.join(source, "staged")
        stage_payload.stage_clients(target, source)
        expected = {
            "0.9.22": ("mods/0.9.22.0.1/"
                       "org.peng.offline_lan_0922_0.6.0-alpha.1.wotmod",
                       "mods/configs/offline_lan_0922/config.json",
                       "offline_worker_starter.exe",
                       "mods/0.9.22.0.1/offline_instance_guard_native.pyd",
                       "res_mods/0.9.22.0.1/"
                       "engine_config.offline-player.xml",
                       "res_mods/0.9.22.0.1/"
                       "engine_config.offline-worker.xml"),
        }
        for port_version, members in expected.items():
            archive = zipfile.ZipFile(
                os.path.join(target, "%s.zip" % port_version))
            try:
                names = set(archive.namelist())
            finally:
                archive.close()
            for member in members:
                self.assertIn(member, names)
        self.assertFalse(os.path.exists(os.path.join(target, "0.8.2.zip")))

    def test_client_staging_without_a_built_package_reports_it(self):
        source = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, source, True)
        os.makedirs(os.path.join(source, "0.8.2", "scripts"))
        os.makedirs(os.path.join(source, "0.8.2", "gui"))
        self.assertRaises(ValueError, stage_payload.stage_clients,
                          os.path.join(source, "staged"), source)

    def test_staging_replaces_an_earlier_payload(self):
        stale = os.path.join(self.root, "stale.txt")
        with open(stale, "w") as stream:
            stream.write("old")
        stage_payload.stage(self.root, include_clients=False)
        self.assertFalse(os.path.exists(stale))

    def test_staging_excludes_development_junk(self):
        source = os.path.join(tempfile.mkdtemp(), "repo")
        self.addCleanup(shutil.rmtree, os.path.dirname(source), True)
        overlay = os.path.join(source, "0.9.22", "dist",
                               "WoT-0.9.22-LAN-Client-abc1234")
        paths = {
            "0.8.2/scripts/client/CameraNode.pyc": "x",
            "0.8.2/scripts/client/gui/mods/mod_offhangar.py": "x",
            "0.8.2/scripts/client/debug.log": "secret",
            "0.8.2/gui/maps/a.dds": "x",
        }
        for prefix in stage_payload.SKIPPED_CLIENT_PREFIXES:
            paths[
                "0.8.2/scripts/client/gui/mods/offhangar/%shelper.py" %
                prefix
            ] = "secret"
        paths[os.path.join(
            overlay, "mods/0.9.22.0.1",
            "org.peng.offline_lan_0922_0.6.0-alpha.1.wotmod")] = "x"
        paths[os.path.join(
            overlay, "mods/0.9.22.0.1",
            "org.peng.offline_lan_0922_0.6.0-alpha.1.wotmod.sha256")] = "secret"
        paths[os.path.join(
            overlay, "mods/configs/offline_lan_0922/config.json")] = "x"
        paths[os.path.join(
            overlay, "mods/configs/offline_lan_0922/debug.log")] = "secret"
        for relative, content in paths.items():
            path = (relative if os.path.isabs(relative) else
                    os.path.join(source, *relative.split("/")))
            if not os.path.isdir(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            with open(path, "w") as stream:
                stream.write(content)
        self._write_0_8_2_navgraphs(source)
        self._write_0_9_22_data(overlay)

        target = os.path.join(source, "staged")
        stage_payload.stage_clients(target, source)

        for port_version in core.SUPPORTED_PORTS:
            with zipfile.ZipFile(os.path.join(
                    target, "%s.zip" % port_version)) as archive:
                self.assertFalse(any(name.endswith("debug.log")
                                     for name in archive.namelist()))
                self.assertFalse(any(
                    name.rsplit("/", 1)[-1].startswith(
                        stage_payload.SKIPPED_CLIENT_PREFIXES)
                    for name in archive.namelist()))
                self.assertFalse(any(name.endswith(".sha256")
                                     for name in archive.namelist()))

    def test_multiple_0_9_22_overlays_are_rejected(self):
        source = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, source, True)
        for suffix in ("aaaaaaa", "bbbbbbb"):
            os.makedirs(os.path.join(
                source, "0.9.22", "dist",
                "WoT-0.9.22-LAN-Client-" + suffix))
        self.assertRaises(ValueError, stage_payload.client_source,
                          "0.9.22", source)


class ServerImportTest(unittest.TestCase):
    """The bundle must carry every module the servers import.

    PyInstaller cannot see through ``runpy``, so a server import that is not
    declared in ``server_imports`` is missing from the packaged launcher.
    """

    ENTRY_POINTS = {
        core.PORT_0_9_22: ('server/windows_server.py',),
    }

    def setUp(self):
        root = os.path.join(tempfile.mkdtemp(), "payload")
        self.addCleanup(shutil.rmtree, os.path.dirname(root), True)
        stage_payload.stage(root, include_clients=False)
        self.payload = os.path.join(root, stage_payload.SERVER_DIR)

    def _module_file(self, root, name):
        relative = name.replace('.', os.path.sep)
        for candidate in (relative + '.py',
                          os.path.join(relative, '__init__.py')):
            path = os.path.join(root, candidate)
            if os.path.isfile(path):
                return path
        return None

    def _imports(self, path):
        with open(path, 'rb') as stream:
            tree = ast.parse(stream.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module:
                    names.add(node.module)
        return names

    def _closure(self, port_version):
        port_root = os.path.join(self.payload, port_version)
        search_roots = [port_root]
        if port_version == core.PORT_0_9_22:
            search_roots.append(os.path.join(port_root, 'server'))
            search_roots.append(os.path.join(
                port_root, 'src', 'res', 'scripts', 'client'))
        pending = [os.path.join(port_root, *entry.split('/'))
                   for entry in self.ENTRY_POINTS[port_version]]
        seen = set()
        external = set()
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            for name in self._imports(path):
                local = None
                for root in search_roots:
                    local = self._module_file(root, name)
                    if local is not None:
                        break
                if local is None:
                    external.add(name.split('.')[0])
                else:
                    pending.append(local)
        return external

    def test_every_server_import_is_declared(self):
        declared = set(server_imports.SERVER_STDLIB_MODULES)
        for port_version in core.SUPPORTED_PORTS:
            external = self._closure(port_version)
            required = {name for name in external
                        if name in sys.stdlib_module_names and
                        name != '__future__'}
            self.assertLessEqual(required, declared, port_version)

    def test_the_declared_modules_all_import(self):
        for name in server_imports.SERVER_STDLIB_MODULES:
            self.assertIn(name, sys.modules, name)


class ListenerTest(unittest.TestCase):
    class _ProtocolConnection(object):
        def __init__(self, reply_overrides=None):
            self.reply_overrides = dict(reply_overrides or {})
            self.reply = b""

        def settimeout(self, unused):
            pass

        def sendall(self, payload):
            hello = json.loads(payload.decode("utf-8"))
            if hello.get("type") == "leave":
                self.reply = b""
                return
            reply = {
                "type": "welcome",
                "protocol": hello["protocol"],
                "client_build": hello["client_build"],
                "capabilities": hello.get("capabilities", []),
                "server_capabilities": ["destructible_catalog_v5"],
            }
            reply.update(self.reply_overrides)
            self.reply = (json.dumps(reply) + "\n").encode("utf-8")

        def recv(self, unused):
            reply, self.reply = self.reply, b""
            return reply

        def close(self):
            pass

    def test_probe_reports_a_closed_port(self):
        def refuse(address, timeout):
            raise OSError("refused")

        self.assertFalse(core.probe_endpoint("127.0.0.1", 1, connect=refuse))

    def test_wait_returns_when_the_server_answers(self):
        attempts = []

        class Connection(object):
            def close(self):
                pass

        def connect(address, timeout):
            attempts.append(address)
            if len(attempts) < 3:
                raise OSError("not yet")
            return Connection()

        self.assertTrue(core.wait_for_listener(
            "127.0.0.1", 28782, timeout=5.0, connect=connect,
            clock=lambda: 0.0, sleep=lambda seconds: None))
        self.assertEqual(len(attempts), 3)

    def test_wait_gives_up_after_the_timeout(self):
        times = iter([0.0, 1.0, 2.0, 3.0])

        def connect(address, timeout):
            raise OSError("refused")

        self.assertFalse(core.wait_for_listener(
            "127.0.0.1", 28782, timeout=1.0, connect=connect,
            clock=lambda: next(times), sleep=lambda seconds: None))

    def test_protocol_probe_accepts_each_matching_server(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(core.probe_server_protocol(
                port_version, "127.0.0.1", 28782,
                connect=lambda address, timeout: self._ProtocolConnection()))

    def test_protocol_probe_rejects_an_unrelated_listener(self):
        connection = self._ProtocolConnection({"client_build": "wrong"})
        self.assertFalse(core.probe_server_protocol(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            connect=lambda address, timeout: connection))

    def test_protocol_probe_rejects_a_pre_schema_5_server(self):
        connection = self._ProtocolConnection({"server_capabilities": []})
        self.assertFalse(core.probe_server_protocol(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            connect=lambda address, timeout: connection))

    def test_listener_status_distinguishes_protocol_from_raw_tcp(self):
        endpoint = lambda host, port, timeout=None: True
        compatible = lambda version, host, port, timeout=None: True
        incompatible = lambda version, host, port, timeout=None: False
        self.assertEqual(core.LISTENER_COMPATIBLE, core.listener_status(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            endpoint_probe=endpoint, protocol_probe=compatible))
        self.assertEqual(core.LISTENER_OCCUPIED, core.listener_status(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            endpoint_probe=endpoint, protocol_probe=incompatible))
        self.assertEqual(core.LISTENER_FREE, core.listener_status(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            endpoint_probe=lambda host, port, timeout=None: False,
            protocol_probe=compatible))

    def test_wait_for_server_requires_the_protocol_probe(self):
        attempts = []

        def probe(port_version, host, port, timeout=None):
            attempts.append((port_version, host, port))
            return len(attempts) == 3

        self.assertTrue(core.wait_for_server(
            core.PORT_0_9_22, "127.0.0.1", 28782, timeout=5.0,
            probe=probe, clock=lambda: 0.0,
            sleep=lambda seconds: None))
        self.assertEqual(3, len(attempts))

    def test_probe_contracts_match_the_bundled_servers(self):
        def constants(path):
            with open(path, "rb") as stream:
                tree = ast.parse(stream.read())
            values = {}
            for statement in tree.body:
                if (isinstance(statement, ast.Assign) and
                        len(statement.targets) == 1 and
                        isinstance(statement.targets[0], ast.Name)):
                    try:
                        values[statement.targets[0].id] = ast.literal_eval(
                            statement.value)
                    except (TypeError, ValueError):
                        pass
            return values

        server082 = constants(os.path.join(
            stage_payload.repository_root(), "0.8.2", "lan_battle_server.py"))
        server0922 = constants(os.path.join(
            stage_payload.repository_root(), "0.9.22", "server",
            "lan_battle_server.py"))
        self.assertEqual(server082["PROTOCOL_VERSION"],
                         core._SERVER_PROBES[core.PORT_0_8_2]["protocol"])
        self.assertEqual(server082["CLIENT_BUILD"],
                         core._SERVER_PROBES[core.PORT_0_8_2]["client_build"])
        self.assertEqual(server0922["PROTOCOL_VERSION"],
                         core._SERVER_PROBES[core.PORT_0_9_22]["protocol"])
        self.assertEqual(server0922["CLIENT_BUILD_0922"],
                         core._SERVER_PROBES[core.PORT_0_9_22]["client_build"])
        self.assertIn(server0922["PROJECTILE_CAPABILITY"],
                      core._SERVER_PROBES[core.PORT_0_9_22]["capabilities"])
        self.assertIn(
            server0922["DESTRUCTIBLE_CATALOG_V5_CAPABILITY"],
            core._SERVER_PROBES[core.PORT_0_9_22]["capabilities"])
        self.assertIn(
            server0922["DESTRUCTIBLE_CATALOG_V5_CAPABILITY"],
            core._SERVER_PROBES[core.PORT_0_9_22]["server_capabilities"])


class ConnectionReportTest(unittest.TestCase):
    def test_a_reachable_join_target_is_confirmed(self):
        self.assertIn("answered", core.connection_report(
            core.MODE_JOIN, "10.0.0.5", 28782, True))

    def test_an_unreachable_join_target_names_the_firewall(self):
        message = core.connection_report(core.MODE_JOIN, "10.0.0.5", 28782,
                                         False)
        self.assertIn("No answer from 10.0.0.5:28782", message)
        self.assertIn("firewall", message)

    def test_a_busy_port_warns_the_host(self):
        message = core.connection_report(core.MODE_HOST, core.LOCAL_HOST,
                                         28782, True)
        self.assertIn("already listens", message)

    def test_an_unrelated_listener_is_not_reported_as_the_server(self):
        message = core.listener_report(
            core.MODE_HOST, core.LOCAL_HOST, 28782,
            core.LISTENER_OCCUPIED)
        self.assertIn("Another program", message)

    def test_a_free_port_tells_the_host_what_happens_next(self):
        message = core.connection_report(core.MODE_SINGLE, core.LOCAL_HOST,
                                         28782, False)
        self.assertIn("Start game runs the server", message)


class LocalAddressTest(unittest.TestCase):
    def test_loopback_is_never_offered_to_other_players(self):
        self.assertEqual(
            core.local_addresses(lambda: ['127.0.0.1', '192.168.1.20']),
            ['192.168.1.20'])

    def test_duplicate_addresses_collapse(self):
        self.assertEqual(
            core.local_addresses(lambda: ['10.0.0.5', '10.0.0.5']),
            ['10.0.0.5'])

    def test_a_failed_lookup_reports_no_address(self):
        def fail():
            raise OSError('no name')

        self.assertEqual(core.local_addresses(fail), [])


class GameProcessTest(unittest.TestCase):
    """The client can restart itself once while it starts up."""

    class _Result(object):
        def __init__(self, stdout):
            self.stdout = stdout

    class _Process(object):
        def __init__(self, states):
            self.states = list(states)

        def poll(self):
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0]

    class _TerminableProcess(object):
        def __init__(self):
            self.exit_code = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.exit_code

        def terminate(self):
            self.terminated = True
            self.exit_code = 1

        def kill(self):
            self.killed = True
            self.exit_code = -9

        def wait(self, timeout=None):
            return self.exit_code

    def test_a_listed_process_means_the_game_runs(self):
        listing = ("WorldOfTanks.exe   9876 Console   1   1,234,567 K\r\n"
                   ).encode("utf-8")
        self.assertTrue(core.game_is_running(
            runner=lambda *args, **kwargs: self._Result(listing)))

    def test_an_empty_listing_means_the_game_is_gone(self):
        listing = b"INFO: No tasks are running which match the criteria.\r\n"
        self.assertFalse(core.game_is_running(
            runner=lambda *args, **kwargs: self._Result(listing)))

    def test_a_failed_lookup_reports_the_game_as_gone(self):
        def fail(*args, **kwargs):
            raise OSError("tasklist is missing")

        self.assertFalse(core.game_is_running(runner=fail))

    def test_visible_game_window_matches_the_selected_client_path(self):
        selected = core.game_executable("/selected-game")

        self.assertTrue(core.game_window_is_visible(
            "/selected-game", enumerator=lambda: [selected]))
        self.assertFalse(core.game_window_is_visible(
            "/selected-game",
            enumerator=lambda: [core.game_executable("/other-game")]))

    def test_hidden_worker_does_not_latch_paired_player_as_closed(self):
        process = self._Process([None, None, 0])
        ticks = iter([0.0, 10.0])

        self.assertEqual(
            (0, False),
            core.wait_for_paired_player_exit(
                process, "/game", window_visible=lambda: False,
                close_grace=1.0, poll=1.0,
                clock=lambda: next(ticks), sleep=lambda unused: None))

    def test_paired_player_retires_after_continuous_window_loss(self):
        process = self._TerminableProcess()
        visible = iter([False, False, True, False, None, False, False, False])
        ticks = iter(float(index) for index in range(8))

        self.assertEqual(
            (1, True),
            core.wait_for_paired_player_exit(
                process, "/game", window_visible=lambda: next(visible),
                close_grace=2.0, poll=1.0,
                clock=lambda: next(ticks), sleep=lambda unused: None))
        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)

    def test_the_wait_ends_after_a_quiet_grace_period(self):
        ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertFalse(core.wait_for_game_exit(
            lambda: False, grace=3.0, poll=1.0,
            clock=lambda: next(ticks), sleep=lambda seconds: None))

    def test_a_restarted_game_keeps_the_wait_open(self):
        seen = []
        running = [True, True, False, False, False, False]
        ticks = iter([float(index) for index in range(20)])

        def is_running():
            return running.pop(0) if running else False

        self.assertTrue(core.wait_for_game_exit(
            is_running, on_restart=lambda: seen.append(1), grace=2.0, poll=1.0,
            clock=lambda: next(ticks), sleep=lambda seconds: None))
        self.assertEqual([1], seen)

    def test_shutdown_waits_for_terminated_processes_to_disappear(self):
        running = [True, True, False]
        ticks = iter([0.0, 0.0, 0.1])
        sleeps = []

        self.assertTrue(core.wait_for_game_shutdown(
            is_running=lambda: running.pop(0), timeout=1.0, poll=0.1,
            clock=lambda: next(ticks), sleep=sleeps.append))
        self.assertEqual([0.1, 0.1], sleeps)

    def test_shutdown_wait_is_bounded(self):
        ticks = iter([0.0, 1.0])

        self.assertFalse(core.wait_for_game_shutdown(
            is_running=lambda: True, timeout=1.0, poll=0.1,
            clock=lambda: next(ticks), sleep=lambda unused: None))

    def test_worker_ready_requires_a_live_process_and_marker(self):
        game_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, game_root, True)
        marker = core.worker_ready_marker(game_root)
        attempts = []

        def sleep(unused):
            attempts.append(1)
            with open(marker, "w") as stream:
                stream.write("ready")

        process = self._Process([None])
        self.assertTrue(core.wait_for_worker_ready(
            process, game_root, timeout=1.0, interval=0.1,
            clock=lambda: 0.0, sleep=sleep))
        self.assertEqual([1], attempts)

    def test_worker_ready_rejects_an_unchanged_stale_marker(self):
        game_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, game_root, True)
        marker = core.worker_ready_marker(game_root)
        with open(marker, "w") as stream:
            stream.write("stale")
        previous = core.worker_ready_marker_token(game_root)
        attempts = []

        def sleep(unused):
            attempts.append(1)
            with open(marker, "w") as stream:
                stream.write("new-ready-marker")

        self.assertTrue(core.wait_for_worker_ready(
            self._Process([None]), game_root,
            previous_marker_token=previous, timeout=1.0, interval=0.1,
            clock=lambda: 0.0, sleep=sleep))
        self.assertEqual([1], attempts)

    def test_worker_exit_or_cancellation_rejects_readiness(self):
        game_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, game_root, True)
        with open(core.worker_ready_marker(game_root), "w") as stream:
            stream.write("stale")
        self.assertFalse(core.wait_for_worker_ready(
            self._Process([9]), game_root, clock=lambda: 0.0,
            sleep=lambda unused: None))
        self.assertFalse(core.wait_for_worker_ready(
            self._Process([None]), game_root, cancelled=lambda: True,
            clock=lambda: 0.0, sleep=lambda unused: None))


class KnownFolderTest(unittest.TestCase):
    def test_a_folder_moves_to_the_top_without_duplicates(self):
        folders = core.remember_folder([], os.path.join("C:", "Games", "WoT"))
        folders = core.remember_folder(folders, os.path.join("D:", "WoT922"))
        folders = core.remember_folder(folders, os.path.join("C:", "Games",
                                                             "WoT"))
        self.assertEqual([os.path.join("C:", "Games", "WoT"),
                          os.path.join("D:", "WoT922")], folders)

    def test_the_list_stays_bounded(self):
        folders = []
        for index in range(15):
            folders = core.remember_folder(folders, "/games/wot%d" % index,
                                           limit=4)
        self.assertEqual(4, len(folders))
        self.assertEqual(os.path.normpath("/games/wot14"), folders[0])

    def test_an_empty_folder_is_ignored(self):
        self.assertEqual(["/games/wot"],
                         core.remember_folder(["/games/wot"], "   "))

    def test_discovery_finds_a_game_beside_the_common_roots(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        for name in ("World_of_Tanks_0.8.2", "Some Other Game"):
            os.makedirs(os.path.join(root, name))
        with open(os.path.join(root, "World_of_Tanks_0.8.2",
                               core.GAME_EXECUTABLE), "w") as stream:
            stream.write("")
        self.assertEqual(
            [os.path.join(root, "World_of_Tanks_0.8.2")],
            core.discover_game_folders(roots=(root,)))

    def test_discovery_accepts_a_root_that_is_the_game(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, core.GAME_EXECUTABLE), "w") as stream:
            stream.write("")
        self.assertEqual([root], core.discover_game_folders(roots=(root,)))

    def test_discovery_survives_a_missing_root(self):
        self.assertEqual([], core.discover_game_folders(
            roots=("/nonexistent-root",)))

    def test_remembered_folders_come_before_discovered_ones(self):
        folders = core.known_folders(
            {"folders": ["/games/wot082"]},
            discovered=["/games/wot0922", "/games/wot082"])
        self.assertEqual([os.path.normpath("/games/wot082"),
                          os.path.normpath("/games/wot0922")], folders)


class LauncherSettingsTest(unittest.TestCase):
    def test_settings_round_trip(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "launcher.json")
        self.assertTrue(core.save_settings({"mode": core.MODE_HOST}, path))
        self.assertEqual(core.load_settings(path), {"mode": core.MODE_HOST})

    def test_missing_settings_are_empty(self):
        self.assertEqual(core.load_settings("/nonexistent/launcher.json"), {})


if __name__ == "__main__":
    unittest.main()
