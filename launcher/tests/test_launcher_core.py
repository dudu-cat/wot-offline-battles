import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

import core
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

    def test_single_player_needs_a_server_in_both_clients(self):
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
        self.assertEqual(core.detect_port(self.root), core.PORT_0_9_22)

    def test_installed_mod_identifies_the_port_without_a_version_file(self):
        self._write(os.path.join(
            "res_mods", "0.8.2", "scripts", "client", "gui", "mods",
            "offhangar", "__init__.py"))
        self.assertIsNone(core.read_client_version(self.root))
        self.assertEqual(core.detect_port(self.root), core.PORT_0_8_2)

    def test_unsupported_client_reports_no_port(self):
        self._write("version.xml", "<version> v.1.0.0 #1 </version>")
        self.assertIsNone(core.detect_port(self.root))

    def test_inspection_reports_a_missing_executable_and_mod(self):
        self._write("version.xml", "<version> v.0.8.2 #100 </version>")
        status = core.inspect_game_root(self.root)
        self.assertFalse(status["has_executable"])
        self.assertFalse(status["mod_installed"])
        self.assertEqual(status["client"], core.PORT_0_8_2)

    def test_inspection_reports_a_complete_installation(self):
        self._write("version.xml", "<version> v.0.8.2 #100 </version>")
        self._write(core.GAME_EXECUTABLE)
        self._write(os.path.join(
            "res_mods", "0.8.2", "scripts", "client", "gui", "mods",
            "offhangar", "__init__.py"))
        status = core.inspect_game_root(self.root)
        self.assertTrue(status["has_executable"])
        self.assertTrue(status["mod_installed"])


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
        session = core.plan_session(self._status(), core.MODE_SINGLE)
        self.assertEqual(session["host"], core.LOCAL_HOST)
        self.assertTrue(session["needs_server"])

    def test_0_8_2_single_player_plan_starts_a_local_server(self):
        session = core.plan_session(
            self._status(client=core.PORT_0_8_2, version="0.8.2"),
            core.MODE_SINGLE)
        self.assertTrue(session["needs_server"])

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

    def test_unsupported_port_is_rejected(self):
        self.assertRaises(core.LauncherError, core.write_settings, self.root,
                          "0.1.0", core.MODE_SINGLE, core.LOCAL_HOST, 1)


class ServerPayloadTest(unittest.TestCase):
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

    def test_only_the_0_8_2_server_receives_the_navigation_graph_directory(self):
        environment = core.server_environment(core.PORT_0_8_2, "/game", {})
        self.assertTrue(environment[core.NAVGRAPH_DIR_ENV].endswith("navgraphs"))
        self.assertIn("/game", environment[core.NAVGRAPH_DIR_ENV])
        self.assertEqual(
            core.server_environment(core.PORT_0_9_22, "/game", {}), {})

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
        return self._archive("0.8.2", {
            "res_mods/0.8.2/scripts/client/CameraNode.pyc": content,
            "res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.py": content,
            "res_mods/0.8.2/gui/maps/icons/offhangar/pixel.dds": content,
        })

    def _stage_0_9_22(self, content="new"):
        return self._archive("0.9.22", {
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_9.9.9.wotmod": content,
            "mods/configs/offline_lan_0922/navgraphs/a.json": content,
            "mods/configs/offline_lan_0922/config.json": content,
        })

    def test_0_8_2_install_replaces_the_whole_mod_directory(self):
        self._stage_0_8_2()
        self._write(self.game, "res_mods/0.8.2/leftover.txt", "stale")
        actions = core.install_client_mod(self.game, core.PORT_0_8_2,
                                          self.payload)
        self.assertFalse(os.path.exists(
            os.path.join(self.game, "res_mods", "0.8.2", "leftover.txt")))
        self.assertEqual("new", self._read(
            "res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.py"))
        self.assertTrue(any("Removed the old" in action
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

    def test_the_same_package_is_not_installed_twice(self):
        self._stage_0_8_2()
        core.install_client_mod(self.game, core.PORT_0_8_2, self.payload)
        self._write(self.game, "res_mods/0.8.2/leftover.txt", "kept")

        actions = core.install_client_mod(self.game, core.PORT_0_8_2,
                                          self.payload)

        self.assertEqual(["The 0.8.2 mod is already up to date."], actions)
        self.assertEqual("kept", self._read("res_mods/0.8.2/leftover.txt"))

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


class PayloadStagingTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(), "payload")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.root), True)
        self.written = stage_payload.stage(self.root, include_clients=False)
        self.target = os.path.join(self.root, stage_payload.SERVER_DIR)

    def test_both_server_entry_points_are_staged(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(
                os.path.isfile(core.server_script(port_version, self.target)),
                port_version)

    def test_the_0_9_22_server_finds_its_client_modules(self):
        self.assertTrue(os.path.isfile(os.path.join(
            self.target, "0.9.22", "src", "res", "scripts", "client", "gui",
            "mods", "offline_lan_0922", "ai", "maps.py")))

    def test_the_navigation_graphs_stay_out_of_the_bundle(self):
        self.assertFalse(any(
            os.path.sep + "navgraphs" + os.path.sep in path
            for path in self.written))

    def test_client_staging_takes_both_mods_from_the_checkout(self):
        source = os.path.join(tempfile.mkdtemp(), "repo")
        self.addCleanup(shutil.rmtree, os.path.dirname(source), True)
        overlay = os.path.join(source, "0.9.22", "dist",
                               "WoT-0.9.22-LAN-Client-abc1234")
        for relative in (
                os.path.join("0.8.2", "scripts", "client", "a.py"),
                os.path.join("0.8.2", "gui", "maps", "a.dds"),
                os.path.join(overlay, "mods", "0.9.22.0.1", "a.wotmod"),
                os.path.join(overlay, "mods", "configs", "offline_lan_0922",
                             "config.json")):
            path = os.path.join(source, relative)
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as stream:
                stream.write("x")
        target = os.path.join(source, "staged")
        stage_payload.stage_clients(target, source)
        expected = {
            "0.8.2": ("res_mods/0.8.2/scripts/client/a.py",
                      "res_mods/0.8.2/gui/maps/a.dds"),
            "0.9.22": ("mods/0.9.22.0.1/a.wotmod",
                       "mods/configs/offline_lan_0922/config.json"),
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


class ServerImportTest(unittest.TestCase):
    """The bundle must carry every module the servers import.

    PyInstaller cannot see through ``runpy``, so a server import that is not
    declared in ``server_imports`` is missing from the packaged launcher.
    """

    ENTRY_POINTS = {
        core.PORT_0_8_2: ('lan_battle_server.py',),
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
