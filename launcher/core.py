"""Launcher logic for the exact supported 0.9.22 client.

This module keeps the client contract explicit and writes only the user-owned
settings files that the port already reads at client startup.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import socket
import subprocess
import sys
import xml.etree.ElementTree as ElementTree

PORT_0_8_2 = "0.8.2"
PORT_0_9_22 = "0.9.22"
SUPPORTED_PORTS = (PORT_0_9_22,)

MODE_SINGLE = "single"
MODE_HOST = "host"
MODE_JOIN = "join"
MODES = (MODE_SINGLE, MODE_HOST, MODE_JOIN)

DEFAULT_SERVER_PORT = 28782
DEFAULT_TEAM_SIZE = 15
MIN_TEAM_SIZE = 1
MAX_TEAM_SIZE = 15
LOCAL_HOST = "127.0.0.1"
LISTEN_HOST = "0.0.0.0"
GAME_EXECUTABLE = "WorldOfTanks.exe"
NAVGRAPH_DIR_ENV = "WOT_OFFLINE_NAVGRAPH_DIR"
# The client can close its first process and start another one while it
# starts up. The launcher waits this long after the last one before it
# stops the LAN server.
GAME_RESTART_GRACE_SECONDS = 8.0
GAME_SHUTDOWN_TIMEOUT_SECONDS = 10.0
GAME_SHUTDOWN_POLL_SECONDS = 0.1
PAIRED_PLAYER_WINDOW_CLOSE_GRACE_SECONDS = 3.0
PAIRED_PLAYER_WINDOW_POLL_SECONDS = 0.25
KNOWN_FOLDER_LIMIT = 10
COMMON_GAME_ROOTS = (
    "C:\\Games", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\WOT", "D:\\", "D:\\Games", "D:\\Program Files",
    "E:\\", "E:\\Games",
)
SERVE_FLAG = "--serve"

_VERSION_PATTERN = re.compile(r"v\.(\d+(?:\.\d+)+)(?:\s+#(\d+))?")
_PINNED_0_9_22_VERSION = "0.9.22.0.1"
_PINNED_0_9_22_BUILD = "1513"

_MOD_MARKERS = {
    PORT_0_8_2: os.path.join(
        "res_mods", "0.8.2", "scripts", "client", "gui", "mods", "offhangar"),
    PORT_0_9_22: os.path.join(
        "mods", "0.9.22.0.1", "org.peng.offline_lan_0922*.wotmod"),
}

_NAVGRAPH_RELATIVE_DIR = os.path.join(
    "res_mods", "0.8.2", "scripts", "client", "gui", "mods", "offhangar",
    "navgraphs")

SERVER_DATA_ENV_0922 = "WOT_0922_SERVER_DATA"
SERVER_TEAM_SIZE_ENV_0922 = "WOT_0922_TEAM_SIZE"
SERVER_LOOPBACK_ONLY_ENV_0922 = "WOT_0922_LOOPBACK_ONLY"
CLIENT_SERVER_HOST_ENV_0922 = "OFFLINE_LAN_0922_SERVER_HOST"
CLIENT_SERVER_PORT_ENV_0922 = "OFFLINE_LAN_0922_SERVER_PORT"
CLIENT_MODE_ENV_0922 = "OFFLINE_LAN_0922_CLIENT_MODE"
ALLOW_MULTIPLE_CLIENTS_ENV_0922 = "OFFLINE_LAN_0922_ALLOW_MULTIPLE_CLIENTS"
HIDDEN_DESKTOP_ENV_0922 = "OFFLINE_LAN_0922_HIDDEN_DESKTOP"
WORKER_READY_MARKER_ENV_0922 = "OFFLINE_LAN_0922_WORKER_READY_MARKER"
_SERVER_DATA_RELATIVE_DIR_0922 = os.path.join(
    "mods", "configs", "offline_lan_0922")

WORKER_STARTER_FILENAME_0922 = "offline_worker_starter.exe"
WORKER_READY_MARKER_FILENAME_0922 = "offline-worker.ready"
WORKER_FAILURE_LOG_FILENAME_0922 = "offline-worker-starter.log"
SERVER_LOG_FILENAME = "server.log"
PLAYER_ENGINE_CONFIG_0922 = "engine_config.offline-player.xml"
WORKER_ONLY_ARGUMENT_0922 = "--worker-only"
PAIRED_PLAYER_ARGUMENT_0922 = "--paired-player"
WORKER_READY_TIMEOUT_SECONDS_0922 = 60.0

_CLIENT_RUNTIME_FILES_0_9_22 = (
    WORKER_STARTER_FILENAME_0922,
    "mods/0.9.22.0.1/offline_instance_guard_native.pyd",
    "res_mods/0.9.22.0.1/engine_config.offline-player.xml",
    "res_mods/0.9.22.0.1/engine_config.offline-worker.xml",
)

_SERVER_ENTRIES = {
    PORT_0_8_2: (os.path.join("0.8.2"), "lan_battle_server.py"),
    PORT_0_9_22: (os.path.join("0.9.22", "server"), "windows_server.py"),
}

_SERVER_ARGUMENTS = {
    PORT_0_8_2: ("--host", LISTEN_HOST, "--port", str(DEFAULT_SERVER_PORT)),
    PORT_0_9_22: (),
}

_SERVER_PROBES = {
    PORT_0_8_2: {
        "protocol": 8,
        "client_build": "1.8.60-native-experimental-20260815",
        "vehicle": "ussr:MS-1",
        "capabilities": None,
        "server_capabilities": None,
    },
    PORT_0_9_22: {
        "protocol": 5,
        "client_build": "wot-0.9.22.0.1-cn-1513",
        "vehicle": "ussr:R11_MS-1",
        "capabilities": (
            "projectile_ledger_v1", "destructible_catalog_v5"),
        "server_capabilities": ("destructible_catalog_v5",),
    },
}

LISTENER_FREE = "free"
LISTENER_COMPATIBLE = "compatible"
LISTENER_OCCUPIED = "occupied"

_DATASETS_0_9_22 = ("navgraphs", "foliage", "destructibles", "occluders")
_DATA_INVENTORIES = {
    PORT_0_8_2: ((
        "res_mods/0.8.2/scripts/client/gui/mods/offhangar/navgraphs", 33),),
    PORT_0_9_22: tuple((
        "mods/configs/offline_lan_0922/%s" % dataset, 41)
        for dataset in _DATASETS_0_9_22),
}


class LauncherError(Exception):
    """A user-correctable launcher failure."""


def game_executable(game_root):
    return os.path.join(game_root, GAME_EXECUTABLE)


def worker_starter_executable(game_root):
    return os.path.join(game_root, WORKER_STARTER_FILENAME_0922)


def worker_ready_marker(game_root):
    return os.path.join(game_root, WORKER_READY_MARKER_FILENAME_0922)


def worker_ready_marker_token(game_root):
    """Identify one regular ready marker without following a symlink."""
    import stat

    try:
        value = os.lstat(worker_ready_marker(game_root))
    except (IOError, OSError):
        return None
    if not stat.S_ISREG(value.st_mode):
        return None
    mtime_ns = getattr(
        value, "st_mtime_ns", int(value.st_mtime * 1000000000))
    ctime_ns = getattr(
        value, "st_ctime_ns", int(value.st_ctime * 1000000000))
    return (value.st_dev, value.st_ino, value.st_size, mtime_ns, ctime_ns)


def worker_failure_log(game_root):
    return os.path.join(game_root, WORKER_FAILURE_LOG_FILENAME_0922)


def launcher_directory(executable=None, frozen=None):
    """Return the folder containing the launcher the user opened."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return os.path.dirname(os.path.abspath(executable or sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def server_log_path(executable=None, frozen=None):
    """Return the server log beside the launcher executable or script."""
    return os.path.join(
        launcher_directory(executable=executable, frozen=frozen),
        SERVER_LOG_FILENAME)


def visible_client_command(game_root, port_version, paired_worker=False):
    """Build the visible client command for one supported port."""
    if port_version == PORT_0_9_22 and paired_worker:
        return [worker_starter_executable(game_root),
                PAIRED_PLAYER_ARGUMENT_0922]
    command = [game_executable(game_root)]
    if port_version == PORT_0_9_22:
        command.extend([
            "--config", PLAYER_ENGINE_CONFIG_0922,
            "--logFilePrefix", "offline-player-",
        ])
    return command


def visible_client_environment(port_version, host=LOCAL_HOST,
                               port=DEFAULT_SERVER_PORT,
                               paired_worker=False, environment=None):
    """Keep worker-only state out of the visible game process."""
    environment = dict(os.environ if environment is None else environment)
    if port_version != PORT_0_9_22:
        return environment
    for name in (CLIENT_MODE_ENV_0922, HIDDEN_DESKTOP_ENV_0922,
                 WORKER_READY_MARKER_ENV_0922):
        environment.pop(name, None)
    environment[CLIENT_SERVER_HOST_ENV_0922] = str(host)
    environment[CLIENT_SERVER_PORT_ENV_0922] = str(int(port))
    if paired_worker:
        environment[ALLOW_MULTIPLE_CLIENTS_ENV_0922] = "1"
    else:
        environment.pop(ALLOW_MULTIPLE_CLIENTS_ENV_0922, None)
    return environment


def worker_child_command(game_root):
    return [worker_starter_executable(game_root), WORKER_ONLY_ARGUMENT_0922]


def worker_environment(game_root, host=LOCAL_HOST,
                       port=DEFAULT_SERVER_PORT,
                       team_size=DEFAULT_TEAM_SIZE, environment=None):
    """Build the endpoint inherited by the hidden simulation client."""
    environment = server_environment(
        PORT_0_9_22, game_root, environment, team_size=team_size)
    environment[CLIENT_SERVER_HOST_ENV_0922] = str(host)
    environment[CLIENT_SERVER_PORT_ENV_0922] = str(int(port))
    return environment


def read_client_identity(game_root):
    """Return the version and build recorded in the stock version.xml."""
    path = os.path.join(game_root, "version.xml")
    try:
        root = ElementTree.parse(path).getroot()
    except (IOError, OSError, ElementTree.ParseError):
        return None
    version_node = root if root.tag == "version" else root.find("version")
    if version_node is None:
        return None
    text = "".join(version_node.itertext()).strip()
    match = _VERSION_PATTERN.fullmatch(text)
    if match is None:
        return None
    return (match.group(1), match.group(2))


def read_client_version(game_root):
    """Return the dotted client version recorded in the stock version.xml."""
    identity = read_client_identity(game_root)
    return identity[0] if identity is not None else None


def port_for_version(version, build=None):
    if not version:
        return None
    if (version == _PINNED_0_9_22_VERSION and
            str(build or "") == _PINNED_0_9_22_BUILD):
        return PORT_0_9_22
    return None


def installed_port(game_root):
    """Return the port whose client mod is installed in this game folder."""
    for port_version, marker in _MOD_MARKERS.items():
        path = os.path.join(game_root, marker)
        if ((port_version == PORT_0_9_22 and
             any(os.path.isfile(candidate) for candidate in glob.glob(path))) or
                (port_version != PORT_0_9_22 and os.path.isdir(path))):
            return port_version
    return None


def detect_port(game_root):
    identity = read_client_identity(game_root)
    if identity is None:
        return None
    return port_for_version(identity[0], identity[1])


def inspect_game_root(game_root):
    """Describe one game folder for the launcher window."""
    game_root = os.path.abspath(game_root or "")
    identity = read_client_identity(game_root)
    version, build = identity if identity is not None else (None, None)
    port_version = (port_for_version(version, build)
                    if identity is not None else None)
    installed = installed_port(game_root)
    return {
        "path": game_root,
        "has_executable": os.path.isfile(game_executable(game_root)),
        "version": version,
        "build": build,
        "client": port_version,
        "mod_installed": (port_version is not None and
                          installed == port_version),
    }


def plan_session(status, mode, join_text="", team_size=DEFAULT_TEAM_SIZE,
                 vehicle_profile=None):
    """Turn the window fields into one battle session, or explain the problem."""
    if not status.get("has_executable"):
        raise LauncherError(
            "Select the folder that contains %s." % GAME_EXECUTABLE)
    port_version = status.get("client")
    if port_version not in SUPPORTED_PORTS:
        raise LauncherError("This client version is not supported.")
    if mode not in MODES:
        raise LauncherError("Select single player, host, or join.")
    host, tcp_port = endpoint_for_mode(mode, join_text)
    effective_team_size = DEFAULT_TEAM_SIZE
    if port_version == PORT_0_9_22 and mode != MODE_JOIN:
        effective_team_size = parse_team_size(team_size)
    profile_name = str(vehicle_profile or "").strip() or None
    if profile_name is not None and (
            port_version != PORT_0_9_22 or mode != MODE_SINGLE):
        raise LauncherError(
            "Modified vehicle profiles are limited to 0.9.22 single player.")
    return {
        "client": port_version,
        "mode": mode,
        "host": host,
        "tcp_port": tcp_port,
        "needs_server": server_required(port_version, mode),
        "team_size": effective_team_size,
        "vehicle_profile": profile_name,
    }


def parse_endpoint(text, default_port=DEFAULT_SERVER_PORT):
    """Parse the address the player types for a join."""
    value = str(text or "").strip()
    if not value:
        raise LauncherError("Enter the address of the PC that hosts the battle.")
    if ":" in value:
        host, raw_port = value.rsplit(":", 1)
    else:
        host, raw_port = value, default_port
    host = host.strip()
    if not host or any(character.isspace() for character in host) or "/" in host:
        raise LauncherError("The server address is invalid.")
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise LauncherError("The server port must be a number.")
    if port < 1 or port > 65535:
        raise LauncherError("The server port must be 1-65535.")
    return (host, port)


def parse_team_size(value):
    """Validate the total number of tanks on each team, including players."""
    if isinstance(value, bool):
        raise LauncherError("Tanks per team must be a whole number from 1 to 15.")
    try:
        team_size = int(value)
    except (TypeError, ValueError):
        raise LauncherError("Tanks per team must be a number from 1 to 15.")
    if isinstance(value, float) and value != team_size:
        raise LauncherError("Tanks per team must be a whole number from 1 to 15.")
    if team_size < MIN_TEAM_SIZE or team_size > MAX_TEAM_SIZE:
        raise LauncherError("Tanks per team must be 1-15.")
    return team_size


def endpoint_for_mode(mode, join_text="", default_port=DEFAULT_SERVER_PORT):
    if mode == MODE_JOIN:
        return parse_endpoint(join_text, default_port)
    return (LOCAL_HOST, default_port)


def server_required(unused_port_version, mode):
    """Report whether the launcher must run a server for this mode.

    Both clients play every battle against the LAN server, including a single
    player, so only joining somebody else's room needs no local server.
    """
    return mode != MODE_JOIN


def _write_json(path, value, indent=2):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temporary_path = path + ".tmp"
    payload = json.dumps(value, indent=indent, sort_keys=False) + "\n"
    with open(temporary_path, "wb") as stream:
        stream.write(payload.encode("utf-8"))
    try:
        os.replace(temporary_path, path)
    except OSError as error:
        # Windows refuses to replace an existing file whose read-only bit was
        # preserved by an older installation. Only relax that specific case;
        # locks and directory permission failures must still surface.
        if getattr(error, "winerror", None) != 5 or not os.path.isfile(path):
            raise
        import stat
        original_mode = os.stat(path).st_mode
        if original_mode & stat.S_IWRITE:
            raise
        os.chmod(path, original_mode | stat.S_IWRITE)
        try:
            os.replace(temporary_path, path)
        except OSError:
            try:
                os.chmod(path, original_mode)
            except OSError:
                pass
            raise


def _read_json(path):
    try:
        with open(path, "rb") as stream:
            value = json.load(stream)
    except (IOError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_0_8_2_settings(game_root, mode, host, port, name=None):
    path = os.path.join(game_root, "offhangar_user", "config.json")
    config = _read_json(path) or {}
    config["network_mode"] = True
    config["network_server_host"] = host
    config["network_server_port"] = int(port)
    config["network_map_name"] = "server_random"
    if name:
        config["nickname"] = name
    _write_json(path, config, indent=4)
    return [path]


def write_0_9_22_settings(game_root, mode, host, port, name=None):
    config_dir = os.path.join(game_root, "mods", "configs", "offline_lan_0922")
    endpoint_path = os.path.join(config_dir, "server_endpoint.json")
    _write_json(endpoint_path, {
        "schema": 1,
        "host": host,
        "port": int(port),
    })
    written = [endpoint_path]
    if name:
        config_path = os.path.join(config_dir, "config.json")
        config = _read_json(config_path)
        if config is not None:
            config["name"] = name
            _write_json(config_path, config)
            written.append(config_path)
    return written


def write_settings(game_root, port_version, mode, host, port, name=None):
    if port_version == PORT_0_8_2:
        return write_0_8_2_settings(game_root, mode, host, port, name)
    if port_version == PORT_0_9_22:
        return write_0_9_22_settings(game_root, mode, host, port, name)
    raise LauncherError("This game folder is not a supported client.")


SERVER_PAYLOAD_DIR = "servers"
CLIENT_PAYLOAD_DIR = "client"
INSTALL_MARKER_NAME = "launcher_install.json"

# Where each port keeps the files the launcher must not delete, and the
# marker that records which package is installed.
_USER_DIRS = {
    PORT_0_8_2: "offhangar_user",
    PORT_0_9_22: "mods/configs/offline_lan_0922",
}

_MUTABLE_STATE_0_9_22 = (
    "config.json",
    "server_endpoint.json",
    "account_state.json",
    "garage_state.json",
    "postbattle_state.json",
)

# Directories the launcher replaces as one unit, the files of its own package
# it removes from a shared directory, and the members it writes only when they
# are absent.  The replacement roots keep stale baked data out of a new server
# run without touching the user's endpoint, account state, or configuration.
_CLIENT_INSTALL = {
    PORT_0_8_2: {
        "replace": ("res_mods/0.8.2",),
        "prune": (),
        "keep": (),
        "allowed": ("res_mods/0.8.2/",),
        "suffixes": (".dds", ".json", ".png", ".py", ".pyc", ".pyd"),
        "required": (
            "res_mods/0.8.2/scripts/client/CameraNode.pyc",
            "res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.py",
            "res_mods/0.8.2/scripts/client/gui/mods/offhangar/"
            "navgraphs/manifest.json",
        ),
        "owned_files": (),
        "package_pattern": None,
    },
    PORT_0_9_22: {
        "replace": tuple(
            "mods/configs/offline_lan_0922/%s" % name
            for name in _DATASETS_0_9_22),
        "prune": (("mods/0.9.22.0.1", "org.peng.offline_lan_0922*"),),
        "keep": ("mods/configs/offline_lan_0922/config.json",),
        "allowed": (
            "mods/0.9.22.0.1/",
            "mods/configs/offline_lan_0922/",
            "res_mods/0.9.22.0.1/",
            WORKER_STARTER_FILENAME_0922,
        ),
        "suffixes": (".exe", ".json", ".pyd", ".wotmod", ".xml"),
        "required": tuple(
            "mods/configs/offline_lan_0922/%s/manifest.json" % name
            for name in _DATASETS_0_9_22) + (
            "mods/configs/offline_lan_0922/config.json",
        ) + _CLIENT_RUNTIME_FILES_0_9_22,
        "owned_files": _CLIENT_RUNTIME_FILES_0_9_22,
        "package_pattern": (
            "mods/0.9.22.0.1/org.peng.offline_lan_0922*.wotmod"),
    },
}


def repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def server_root(base_dir=None):
    """Return the directory that holds the bundled or checked-out servers."""
    if base_dir is not None:
        return base_dir
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return os.path.join(bundle_dir, SERVER_PAYLOAD_DIR)
    return repository_root()


def client_archive(port_version, base_dir=None):
    """Return the bundled client archive for one port, when it exists."""
    if base_dir is None:
        bundle_dir = getattr(sys, "_MEIPASS", None)
        base_dir = bundle_dir or repository_root()
    path = os.path.join(base_dir, CLIENT_PAYLOAD_DIR,
                        "%s.zip" % port_version)
    return path if os.path.isfile(path) else None


def _payload_identity(path):
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def install_marker_path(game_root, port_version):
    return os.path.join(game_root,
                        *(_USER_DIRS[port_version].split("/") +
                          [INSTALL_MARKER_NAME]))


def installed_identity(game_root, port_version):
    marker = _read_json(install_marker_path(game_root, port_version))
    return (marker or {}).get("payload")


def _inside(root, path):
    root = os.path.normcase(os.path.realpath(os.path.abspath(root)))
    path = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    try:
        return os.path.commonpath((root, path)) == root and path != root
    except ValueError:
        return False


def _relative_path(root, relative):
    path = os.path.join(root, *relative.split("/"))
    if not _inside(root, path):
        raise LauncherError("Refusing to write outside the game folder.")
    return path


def _path_is_covered(path, roots):
    path = os.path.normcase(os.path.abspath(path))
    for root in roots:
        root = os.path.normcase(os.path.abspath(root))
        if path == root or _inside(root, path):
            return True
    return False


def _data_inventory(port_version, read_member, has_member):
    """Return the complete baked-data member set, or None when incomplete."""
    expected = set()
    try:
        for data_root, map_count in _DATA_INVENTORIES[port_version]:
            manifest_member = "%s/manifest.json" % data_root
            expected.add(manifest_member)
            manifest = json.loads(read_member(manifest_member).decode("utf-8"))
            records = manifest.get("maps") if isinstance(manifest, dict) else None
            if not isinstance(records, list) or len(records) != map_count:
                return None
            filenames = set()
            for record in records:
                filename = record.get("file") if isinstance(record, dict) else None
                if (not isinstance(filename, str) or not filename or
                        filename in (".", "..") or "/" in filename or
                        "\\" in filename or not filename.endswith(".json") or
                        filename in filenames):
                    return None
                filenames.add(filename)
                data_member = "%s/%s" % (data_root, filename)
                if not has_member(data_member):
                    return None
                expected.add(data_member)
    except Exception:
        return None
    return expected


def _validate_archive(archive, game_root, port_version, layout):
    """Return safe file members after validating the complete client ZIP."""
    import stat

    members = []
    seen = set()
    for info in archive.infolist():
        member = info.filename
        if member.endswith("/"):
            continue
        parts = member.split("/")
        if (not member or "\\" in member or
                any(not part or part in (".", "..") for part in parts) or
                not any(member.startswith(prefix)
                        for prefix in layout["allowed"]) or
                not member.lower().endswith(layout["suffixes"])):
            raise LauncherError(
                "The bundled %s mod contains an invalid path." % port_version)
        target = os.path.join(game_root, *parts)
        if not _inside(game_root, target):
            raise LauncherError(
                "Refusing to write outside the game folder.")
        key = member.casefold()
        if key in seen:
            raise LauncherError(
                "The bundled %s mod contains duplicate paths." % port_version)
        seen.add(key)
        mode = int(info.external_attr) >> 16
        if mode and stat.S_ISLNK(mode):
            raise LauncherError(
                "The bundled %s mod contains a symbolic link." % port_version)
        members.append((info, member))
    names = set(member for unused, member in members)
    missing = [name for name in layout["required"] if name not in names]
    pattern = layout["package_pattern"]
    packages = [name for name in names if pattern and
                fnmatch.fnmatch(name, pattern)]
    if missing or (pattern and len(packages) != 1):
        raise LauncherError(
            "The bundled %s mod is incomplete." % port_version)
    inventory = _data_inventory(
        port_version, archive.read, lambda member: member in names)
    if inventory is None:
        raise LauncherError(
            "The bundled %s baked data is incomplete." % port_version)
    if port_version == PORT_0_9_22:
        expected = (inventory | set(layout["keep"]) | set(packages) |
                    set(layout["owned_files"]))
        if names != expected:
            raise LauncherError(
                "The bundled 0.9.22 mod contains unexpected files.")
    bad_member = archive.testzip()
    if bad_member is not None:
        raise LauncherError(
            "The bundled %s mod is corrupt: %s" %
            (port_version, bad_member))
    return members


def _installation_complete(game_root, port_version, layout):
    if any(not os.path.isfile(_relative_path(game_root, relative))
           for relative in layout["required"]):
        return False
    pattern = layout["package_pattern"]
    if pattern is not None:
        matches = glob.glob(_relative_path(game_root, pattern))
        if len([path for path in matches if os.path.isfile(path)]) != 1:
            return False
    def read_member(member):
        with open(_relative_path(game_root, member), "rb") as stream:
            return stream.read()

    if _data_inventory(
            port_version, read_member,
            lambda member: os.path.isfile(
                _relative_path(game_root, member))) is None:
        return False
    return True


def _stage_archive(archive, members, game_root, transaction_root):
    import shutil

    staged_root = os.path.join(transaction_root, "new")
    os.makedirs(staged_root)
    for info, member in members:
        target = os.path.join(staged_root, *member.split("/"))
        if not _inside(staged_root, target):
            raise LauncherError(
                "Refusing to stage outside the installer workspace.")
        directory = os.path.dirname(target)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with archive.open(info) as source:
            with open(target, "wb") as stream:
                shutil.copyfileobj(source, stream)
    return staged_root


def _transactional_install(game_root, staged_root, members, layout):
    """Swap staged package-owned paths in, restoring every old path on error."""
    transaction_root = os.path.dirname(staged_root)
    backup_root = os.path.join(transaction_root, "backup")
    failed_root = os.path.join(transaction_root, "failed")
    os.makedirs(backup_root)
    os.makedirs(failed_root)

    replace_targets = [
        _relative_path(game_root, relative) for relative in layout["replace"]]
    operations = []
    for relative, target in zip(layout["replace"], replace_targets):
        source = os.path.join(staged_root, *relative.split("/"))
        if not os.path.isdir(source):
            raise LauncherError(
                "The bundled mod is missing %s." % relative)
        operations.append((source, target))
    for unused, member in members:
        source = os.path.join(staged_root, *member.split("/"))
        target = _relative_path(game_root, member)
        if _path_is_covered(target, replace_targets):
            continue
        if member in layout["keep"] and os.path.isfile(target):
            continue
        operations.append((source, target))

    operation_targets = [target for unused, target in operations]
    prune_targets = []
    for relative, pattern in layout["prune"]:
        directory = _relative_path(game_root, relative)
        for path in sorted(glob.glob(os.path.join(directory, pattern))):
            if (os.path.isfile(path) and
                    not _path_is_covered(path, operation_targets)):
                prune_targets.append(path)

    for unused, target in operations:
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            os.makedirs(parent)

    backup_targets = []
    for target in operation_targets + prune_targets:
        if os.path.lexists(target) and target not in backup_targets:
            backup_targets.append(target)
    backups = []
    installed = []
    try:
        for index, target in enumerate(backup_targets):
            backup = os.path.join(backup_root, str(index))
            os.replace(target, backup)
            backups.append((target, backup))
        for source, target in operations:
            os.replace(source, target)
            installed.append(target)
    except Exception as error:
        rollback_errors = []
        for index, target in enumerate(reversed(installed)):
            if not os.path.lexists(target):
                continue
            try:
                os.replace(target, os.path.join(failed_root, str(index)))
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        for target, backup in reversed(backups):
            if not os.path.lexists(backup):
                continue
            try:
                parent = os.path.dirname(target)
                if not os.path.isdir(parent):
                    os.makedirs(parent)
                os.replace(backup, target)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            failure = LauncherError(
                "Installation failed and could not be fully restored. "
                "Recovery files remain in %s." % transaction_root)
            failure.preserve_install_staging = True
            raise failure
        raise LauncherError(
            "Installation failed; the previous mod was restored: %s" % error)

    actions = []
    for target in backup_targets:
        relative = os.path.relpath(target, game_root)
        actions.append("Replaced the old %s" % relative)
    return actions, len(operations)


def install_client_mod(game_root, port_version, base_dir=None, force=False):
    """Install the bundled mod and report what changed.

    The user's own files stay: this only clears the directories the package
    owns, removes its own older package files from a shared directory, and
    never overwrites a configuration that is already there.
    """
    import shutil
    import tempfile
    import zipfile

    layout = _CLIENT_INSTALL.get(port_version)
    if layout is None:
        raise LauncherError("This game folder is not a supported client.")
    archive_path = client_archive(port_version, base_dir)
    if archive_path is None:
        raise LauncherError(
            "This launcher carries no %s mod files." % port_version)
    try:
        identity = _payload_identity(archive_path)
    except (IOError, OSError) as error:
        raise LauncherError(
            "The bundled %s mod cannot be read: %s" %
            (port_version, error))
    if (not force and installed_identity(game_root, port_version) == identity
            and _installation_complete(game_root, port_version, layout)):
        return ["The %s mod is already up to date." % port_version]
    transaction_root = None
    preserve_transaction = False
    try:
        try:
            transaction_root = tempfile.mkdtemp(
                prefix=".wot-offline-install-", dir=game_root)
        except (IOError, OSError) as error:
            raise LauncherError(
                "The game folder is not writable. Move the game to a writable "
                "folder or run the launcher with permission to update it: %s" %
                error)
        try:
            archive = zipfile.ZipFile(archive_path)
        except (IOError, OSError, zipfile.BadZipFile) as error:
            raise LauncherError(
                "The bundled %s mod cannot be opened: %s" %
                (port_version, error))
        try:
            members = _validate_archive(
                archive, game_root, port_version, layout)
            staged_root = _stage_archive(
                archive, members, game_root, transaction_root)
        finally:
            archive.close()
        actions, written = _transactional_install(
            game_root, staged_root, members, layout)
        actions.append("Installed %d %s mod paths" % (written, port_version))
        try:
            _write_json(install_marker_path(game_root, port_version),
                        {"payload": identity})
        except (IOError, OSError):
            actions.append(
                "The mod was installed, but its update marker could not be saved.")
        return actions
    except LauncherError as error:
        preserve_transaction = bool(getattr(
            error, "preserve_install_staging", False))
        raise
    finally:
        if transaction_root is not None and not preserve_transaction:
            shutil.rmtree(transaction_root, ignore_errors=True)


def _valid_0_9_22_config(path):
    """Match the startup-fatal part of the client config contract."""
    try:
        with open(path, "rb") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            return False
        for name in ("startupTimeoutSeconds", "prebattleCountdownSeconds",
                     "battleDurationSeconds"):
            if name in value:
                float(value[name])
        if "max_health" in value:
            int(value["max_health"])
        if "enabled" in value and not isinstance(value["enabled"], bool):
            return False
        for name in ("vehicle", "name"):
            if name in value and (not isinstance(value[name], str) or
                                  not value[name]):
                return False
        for name in ("physics_tuning", "he_tuning"):
            if name in value and not isinstance(value[name], dict):
                return False
        if ("perfect_accuracy" in value and
                not isinstance(value["perfect_accuracy"], bool)):
            return False
        if "authority_worker_probe" in value:
            probe = value["authority_worker_probe"]
            if (not isinstance(probe, dict) or
                    not isinstance(probe.get("enabled"), bool)):
                return False
            seconds = float(probe.get("stageSeconds"))
            if (seconds != seconds or seconds in (float("inf"),
                                                  float("-inf")) or
                    seconds < 15.0 or seconds > 60.0):
                return False
    except (IOError, OSError, TypeError, ValueError):
        return False
    return True


def _quarantine_file(path):
    candidate = path + ".invalid"
    suffix = 1
    while os.path.exists(candidate):
        candidate = path + ".invalid.%d" % suffix
        suffix += 1
    try:
        os.replace(path, candidate)
    except (IOError, OSError) as error:
        raise LauncherError(
            "The invalid offline configuration could not be quarantined: %s" %
            error)
    return candidate


def _require_0_9_22_maintenance_target(game_root, is_running=None):
    status = inspect_game_root(game_root)
    if not status["has_executable"]:
        raise LauncherError(
            "Select the folder that contains %s." % GAME_EXECUTABLE)
    if status["client"] != PORT_0_9_22:
        raise LauncherError(
            "Startup repair and saved-data reset require the supported "
            "0.9.22 client.")
    is_running = game_is_running if is_running is None else is_running
    if is_running():
        raise LauncherError(
            "Close World of Tanks before repairing or resetting offline data.")
    return status


def ensure_0_9_22_preferences_isolation(game_root):
    """Redirect the exact 0.9.22 client to its launcher-owned profile."""
    try:
        from . import preferences_overlay
    except ImportError:
        import preferences_overlay

    return preferences_overlay.ensure_preferences_overlay(game_root)


def _isolated_0_9_22_preferences_path(environment=None):
    try:
        from . import preferences_overlay
    except ImportError:
        import preferences_overlay

    return preferences_overlay.profile_path(environment)


def _normal_client_preferences_path(environment=None):
    try:
        from . import preferences_overlay
    except ImportError:
        import preferences_overlay

    return preferences_overlay.normal_profile_path(environment)


def backup_normal_client_preferences(game_root, is_running=None,
                                     environment=None, timestamp=None):
    """Move the stock client's preferences aside as a recoverable backup."""
    import time

    _require_0_9_22_maintenance_target(game_root, is_running)
    path = _normal_client_preferences_path(environment)
    if path is None:
        raise LauncherError(
            "The normal World of Tanks preferences path could not be "
            "resolved from APPDATA.")
    if not os.path.lexists(path):
        return [
            "The normal World of Tanks preferences.xml is already absent."
        ]
    if os.path.islink(path) or not os.path.isfile(path):
        raise LauncherError(
            "The normal World of Tanks preferences path is not a regular "
            "file; it was left unchanged.")

    stamp = str(timestamp or time.strftime("%Y%m%d-%H%M%S"))
    backup = "%s.wot-offline-backup-%s" % (path, stamp)
    suffix = 1
    while os.path.lexists(backup):
        backup = "%s.wot-offline-backup-%s-%d" % (path, stamp, suffix)
        suffix += 1
    try:
        os.replace(path, backup)
    except (IOError, OSError) as error:
        raise LauncherError(
            "The normal World of Tanks preferences could not be backed up: "
            "%s" % error)
    return [
        "Moved the normal World of Tanks preferences.xml to backup: %s" %
        backup
    ]


def repair_0_9_22_startup(game_root, base_dir=None, is_running=None):
    """Refresh package-owned files and preserve every usable saved value."""
    _require_0_9_22_maintenance_target(game_root, is_running)
    config_path = _relative_path(
        game_root, "mods/configs/offline_lan_0922/config.json")
    quarantined = None
    if os.path.isfile(config_path) and not _valid_0_9_22_config(config_path):
        quarantined = _quarantine_file(config_path)
    try:
        actions = install_client_mod(
            game_root, PORT_0_9_22, base_dir, force=True)
        actions.append(ensure_0_9_22_preferences_isolation(game_root))
    except Exception:
        if quarantined is not None and os.path.exists(quarantined):
            if os.path.exists(config_path):
                os.unlink(config_path)
            os.replace(quarantined, config_path)
        raise
    if quarantined is not None:
        actions.insert(0, "Quarantined invalid config.json as %s" %
                       os.path.basename(quarantined))
    actions.append(
        "Startup repair kept the saved endpoint, account, garage, "
        "post-battle results, and isolated client preferences.")
    return actions


def _reset_state_name(name):
    for base_name in _MUTABLE_STATE_0_9_22:
        if name == base_name or name in (
                base_name + ".tmp", base_name + ".bak",
                base_name + ".invalid"):
            return True
        prefix = base_name + ".invalid."
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            return True
    return False


def reset_0_9_22_state(game_root, base_dir=None, is_running=None):
    """Delete this mod's mutable state after the caller confirms the reset."""
    import shutil
    import tempfile

    _require_0_9_22_maintenance_target(game_root, is_running)
    state_root = _relative_path(
        game_root, "mods/configs/offline_lan_0922")
    targets = []
    if os.path.isdir(state_root):
        targets = [os.path.join(state_root, name)
                   for name in sorted(os.listdir(state_root))
                   if _reset_state_name(name) and
                   os.path.isfile(os.path.join(state_root, name))]
    preferences_path = _isolated_0_9_22_preferences_path()
    if preferences_path is not None and os.path.lexists(preferences_path):
        if (os.path.islink(preferences_path) or
                not os.path.isfile(preferences_path)):
            raise LauncherError(
                "The isolated client preferences path is not a regular file; "
                "it was left unchanged.")
        targets.append(preferences_path)

    backup_root = tempfile.mkdtemp(prefix=".wot-offline-reset-", dir=game_root)
    backup_roots = [backup_root]
    moved = []
    try:
        preferences_backup_root = None
        if preferences_path is not None and preferences_path in targets:
            # LOCALAPPDATA and the game can be on different volumes. Keep this
            # backup beside the profile so both the delete and rollback remain
            # atomic filesystem replacements.
            preferences_backup_root = tempfile.mkdtemp(
                prefix=".wot-offline-reset-",
                dir=os.path.dirname(preferences_path))
            backup_roots.append(preferences_backup_root)
        for index, target in enumerate(targets):
            target_backup_root = (
                preferences_backup_root
                if target == preferences_path else backup_root)
            backup = os.path.join(target_backup_root, str(index))
            os.replace(target, backup)
            moved.append((target, backup))
        actions = install_client_mod(
            game_root, PORT_0_9_22, base_dir, force=True)
        actions.append(ensure_0_9_22_preferences_isolation(game_root))
    except Exception as error:
        for target, backup in reversed(moved):
            if os.path.exists(backup):
                os.replace(backup, target)
        for directory in reversed(backup_roots):
            shutil.rmtree(directory, ignore_errors=True)
        if isinstance(error, LauncherError):
            raise
        raise LauncherError("Offline data reset failed: %s" % error)
    for directory in reversed(backup_roots):
        shutil.rmtree(directory, ignore_errors=True)
    actions.insert(0, "Deleted %d offline saved-data file(s)." % len(targets))
    return actions


def server_script(port_version, base_dir=None):
    entry = _SERVER_ENTRIES.get(port_version)
    if entry is None:
        return None
    directory, script = entry
    directory = os.path.join(server_root(base_dir), directory)
    return os.path.join(directory, script)


def server_argv(port_version, base_dir=None):
    script = server_script(port_version, base_dir)
    if script is None:
        return None
    return [script] + list(_SERVER_ARGUMENTS[port_version])


def server_environment(port_version, game_root, environment=None,
                       team_size=DEFAULT_TEAM_SIZE, loopback_only=False):
    """Point each server at the baked data installed with its client."""
    environment = dict(os.environ if environment is None else environment)
    if port_version == PORT_0_8_2:
        environment[NAVGRAPH_DIR_ENV] = os.path.join(
            game_root, _NAVGRAPH_RELATIVE_DIR)
    elif port_version == PORT_0_9_22:
        environment[SERVER_DATA_ENV_0922] = os.path.join(
            game_root, _SERVER_DATA_RELATIVE_DIR_0922)
        environment[SERVER_TEAM_SIZE_ENV_0922] = str(
            parse_team_size(team_size))
        if loopback_only:
            environment[SERVER_LOOPBACK_ONLY_ENV_0922] = "1"
        else:
            environment.pop(SERVER_LOOPBACK_ONLY_ENV_0922, None)
    return environment


def server_child_command(port_version, launcher_script=None, executable=None,
                         frozen=None):
    """Build the command that runs one server in a child of this launcher."""
    executable = executable or sys.executable
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    command = [executable]
    if not frozen:
        command.append(launcher_script or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "wot_launcher.py"))
    command.extend([SERVE_FLAG, port_version])
    return command


def run_server_payload(port_version, base_dir=None):
    """Run one bundled server inside this process."""
    import runpy

    # The packaged launcher carries the server sources as data, so their
    # standard-library imports reach PyInstaller only through this module.
    import server_imports  # noqa: F401

    argv = server_argv(port_version, base_dir)
    if argv is None:
        raise LauncherError("Unknown client port: %s" % port_version)
    script = argv[0]
    if not os.path.isfile(script):
        raise LauncherError("The bundled server is missing: %s" % script)
    directory = os.path.dirname(script)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    sys.argv = list(argv)
    runpy.run_path(script, run_name="__main__")


def connection_report(mode, host, port, answered):
    """Describe one connection test for the launcher window."""
    return listener_report(
        mode, host, port,
        LISTENER_COMPATIBLE if answered else LISTENER_FREE)


def listener_report(mode, host, port, status):
    """Describe whether an endpoint is free, compatible, or occupied."""
    endpoint = "%s:%d" % (host, int(port))
    if mode == MODE_JOIN:
        if status == LISTENER_COMPATIBLE:
            return "The compatible server at %s answered." % endpoint
        if status == LISTENER_OCCUPIED:
            return ("Something at %s answered, but it is not the server for "
                    "this client." % endpoint)
        return ("No answer from %s. Check that the host started the battle "
                "and that its firewall allows TCP %d." %
                (endpoint, int(port)))
    if status == LISTENER_COMPATIBLE:
        return ("A compatible server already listens on %s. Start game will "
                "use it." % endpoint)
    if status == LISTENER_OCCUPIED:
        return ("Another program listens on %s. Close it before you host "
                "here." % endpoint)
    return ("Nothing listens on %s yet. Start game runs the server there." %
            endpoint)


def probe_endpoint(host, port, timeout=1.5, connect=None):
    """Report whether something accepts TCP connections at this endpoint."""
    connect = connect or socket.create_connection
    try:
        connection = connect((host, int(port)), timeout)
    except (socket.error, OSError, ValueError):
        return False
    try:
        connection.close()
    except (socket.error, OSError):
        pass
    return True


def probe_server_protocol(port_version, host, port, timeout=1.5, connect=None):
    """Report whether the endpoint speaks the selected client's LAN protocol."""
    contract = _SERVER_PROBES.get(port_version)
    if contract is None:
        return False
    connect = connect or socket.create_connection
    connection = None
    try:
        connection = connect((host, int(port)), timeout)
        settimeout = getattr(connection, "settimeout", None)
        if callable(settimeout):
            settimeout(timeout)
        hello = {
            "type": "hello",
            "protocol": contract["protocol"],
            "client_build": contract["client_build"],
            "name": "Launcher-Probe",
            "vehicle": contract["vehicle"],
            "max_health": 1,
        }
        if contract["capabilities"] is not None:
            hello["capabilities"] = list(contract["capabilities"])
        connection.sendall(
            (json.dumps(hello, separators=(",", ":")) + "\n").encode(
                "utf-8"))
        payload = b""
        while b"\n" not in payload and len(payload) < 256 * 1024:
            chunk = connection.recv(4096)
            if not chunk:
                break
            payload += chunk
        line, separator, unused = payload.partition(b"\n")
        if not separator:
            return False
        reply = json.loads(line.decode("utf-8"))
        if (not isinstance(reply, dict) or reply.get("type") != "welcome" or
                int(reply.get("protocol", -1)) != contract["protocol"] or
                reply.get("client_build") != contract["client_build"]):
            return False
        capabilities = contract["capabilities"]
        compatible = (capabilities is None or set(capabilities).issubset(
            set(reply.get("capabilities") or ())))
        server_capabilities = contract.get("server_capabilities")
        compatible = (compatible and (
            server_capabilities is None or set(server_capabilities).issubset(
                set(reply.get("server_capabilities") or ()))))
        if compatible:
            try:
                connection.sendall(b'{"type":"leave"}\n')
                shutdown = getattr(connection, "shutdown", None)
                if callable(shutdown):
                    shutdown(socket.SHUT_WR)
                while connection.recv(4096):
                    pass
            except (IOError, OSError, socket.error):
                pass
        return compatible
    except (IOError, OSError, TypeError, ValueError, socket.error):
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except (IOError, OSError, socket.error):
                pass


def listener_status(port_version, host, port, timeout=1.5,
                    endpoint_probe=None, protocol_probe=None):
    """Classify a free port, a matching server, or an unrelated listener."""
    endpoint_probe = endpoint_probe or probe_endpoint
    protocol_probe = protocol_probe or probe_server_protocol
    if not endpoint_probe(host, port, timeout=timeout):
        return LISTENER_FREE
    if protocol_probe(port_version, host, port, timeout=timeout):
        return LISTENER_COMPATIBLE
    return LISTENER_OCCUPIED


def wait_for_listener(host, port, timeout=20.0, interval=0.25, connect=None,
                      clock=None, sleep=None):
    """Wait until the local server accepts a connection."""
    import time as time_module

    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    deadline = clock() + float(timeout)
    while True:
        if probe_endpoint(host, port, timeout=interval, connect=connect):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def wait_for_server(port_version, host, port, timeout=20.0, interval=0.25,
                    probe=None, clock=None, sleep=None, cancelled=None):
    """Wait until the selected client's protocol answers at the endpoint."""
    import time as time_module

    probe = probe or probe_server_protocol
    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    deadline = clock() + float(timeout)
    while True:
        if callable(cancelled) and cancelled():
            return False
        if probe(port_version, host, port, timeout=interval):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def wait_for_worker_ready(process, game_root,
                          timeout=WORKER_READY_TIMEOUT_SECONDS_0922,
                          interval=0.05, clock=None, sleep=None,
                          cancelled=None, previous_marker_token=None):
    """Wait for a live hidden client to publish its ready marker."""
    import time as time_module

    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    deadline = clock() + float(timeout)
    previous_marker_disappeared = False
    while True:
        if callable(cancelled) and cancelled():
            return False
        if process.poll() is not None:
            return False
        current_marker_token = worker_ready_marker_token(game_root)
        if current_marker_token is None:
            if previous_marker_token is not None:
                previous_marker_disappeared = True
        elif (previous_marker_token is None or
              previous_marker_disappeared or
              current_marker_token != previous_marker_token):
            return process.poll() is None
        if clock() >= deadline:
            return False
        sleep(interval)


def local_addresses(resolver=None):
    """Return the addresses other players can use to reach this host."""
    if resolver is None:
        def resolver():
            return socket.gethostbyname_ex(socket.gethostname())[2]
    try:
        addresses = resolver()
    except (socket.error, OSError, IndexError):
        return []
    return sorted({address for address in addresses
                   if address and not address.startswith('127.')})


def game_is_running(runner=None, executable=GAME_EXECUTABLE):
    """Report whether a game process runs, through the Windows task list."""
    if runner is None:
        if os.name != "nt":
            return False
        runner = subprocess.run
    try:
        result = runner(
            ["tasklist", "/FI", "IMAGENAME eq %s" % executable, "/NH"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        # A failed lookup must never stop a battle. Report the game as gone
        # only after the caller's own grace period.
        return False
    output = getattr(result, "stdout", b"") or b""
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    return executable.lower() in output.lower()


def _visible_window_process_paths():
    """Return executable paths owning visible windows on this desktop."""
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    process_ids = set()
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    @callback_type
    def collect(window, unused):
        if user32.IsWindowVisible(window):
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(
                window, ctypes.byref(process_id))
            if process_id.value:
                process_ids.add(process_id.value)
        return True

    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError()

    query_limited_information = 0x1000
    paths = []
    for process_id in process_ids:
        process = kernel32.OpenProcess(
            query_limited_information, False, process_id)
        if not process:
            continue
        try:
            path = ctypes.create_unicode_buffer(32768)
            path_length = wintypes.DWORD(len(path))
            if kernel32.QueryFullProcessImageNameW(
                    process, 0, path, ctypes.byref(path_length)):
                paths.append(path.value)
        finally:
            kernel32.CloseHandle(process)
    return paths


def game_window_is_visible(game_root, enumerator=None):
    """Report whether this game's visible client window still exists.

    ``None`` means the Windows window lookup was unavailable. The hidden
    simulation client lives on a private desktop, so it is deliberately absent
    from this lookup.
    """
    try:
        paths = (_visible_window_process_paths() if enumerator is None
                 else enumerator())
    except Exception:
        return None
    if paths is None:
        return None
    target = os.path.normcase(os.path.realpath(game_executable(game_root)))
    return any(
        os.path.normcase(os.path.realpath(path)) == target
        for path in paths)


def wait_for_paired_player_exit(
        process, game_root, window_visible=None,
        close_grace=PAIRED_PLAYER_WINDOW_CLOSE_GRACE_SECONDS,
        poll=PAIRED_PLAYER_WINDOW_POLL_SECONDS, sleep=None, clock=None):
    """Wait for the paired player, retiring a windowless process residue.

    The #1513 client can destroy its only visible window without terminating
    its process. Only treat that as closure after a player window has first
    appeared and then remained absent for the full grace period.
    """
    import time as time_module

    sleep = sleep or time_module.sleep
    clock = clock or time_module.monotonic
    window_visible = window_visible or (
        lambda: game_window_is_visible(game_root))
    window_seen = False
    missing_since = None
    while True:
        exit_code = process.poll()
        if exit_code is not None:
            return exit_code, False
        visible = window_visible()
        now = clock()
        if visible is True:
            window_seen = True
            missing_since = None
        elif visible is False and window_seen:
            if missing_since is None:
                missing_since = now
            elif now - missing_since >= max(0.0, float(close_grace)):
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    exit_code = process.wait(
                        timeout=GAME_SHUTDOWN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    exit_code = process.wait()
                return exit_code, True
        else:
            # An unavailable lookup does not count toward a confirmed absence.
            missing_since = None
        sleep(max(0.001, float(poll)))


def kill_game(runner=None, executable=GAME_EXECUTABLE):
    """Force every game process to close."""
    if runner is None:
        if os.name != "nt":
            return False
        runner = subprocess.run
    try:
        runner(["taskkill", "/IM", executable, "/T", "/F"],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               timeout=30,
               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    return True


def wait_for_game_exit(is_running, on_restart=None,
                       grace=GAME_RESTART_GRACE_SECONDS, poll=2.0,
                       sleep=None, clock=None):
    """Wait until no game process has run for the whole grace period."""
    import time as time_module

    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    restarted = False
    quiet_since = clock()
    while clock() - quiet_since < grace:
        if is_running():
            if not restarted and callable(on_restart):
                on_restart()
            restarted = True
            quiet_since = clock()
        sleep(poll)
    return restarted


def wait_for_game_shutdown(
        is_running=None, timeout=GAME_SHUTDOWN_TIMEOUT_SECONDS,
        poll=GAME_SHUTDOWN_POLL_SECONDS, sleep=None, clock=None):
    """Wait a bounded time for terminated game processes to disappear."""
    import time as time_module

    is_running = game_is_running if is_running is None else is_running
    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    deadline = clock() + max(0.0, float(timeout))
    while is_running():
        remaining = deadline - clock()
        if remaining <= 0.0:
            return False
        sleep(min(max(0.001, float(poll)), remaining))
    return True


def remember_folder(folders, path, limit=KNOWN_FOLDER_LIMIT):
    """Put one folder at the top of the remembered list."""
    path = os.path.normpath(str(path or "").strip())
    if not path or path == ".":
        return [str(folder) for folder in folders or ()]
    key = os.path.normcase(path)
    kept = [str(folder) for folder in folders or ()
            if os.path.normcase(os.path.normpath(str(folder))) != key]
    return [path] + kept[:limit - 1]


def discover_game_folders(roots=None, is_game=None):
    """Find game folders in the usual install locations."""
    roots = COMMON_GAME_ROOTS if roots is None else roots
    if is_game is None:
        def is_game(path):
            return os.path.isfile(game_executable(path))
    found = []
    for root in roots:
        candidates = [root]
        try:
            candidates.extend(sorted(
                os.path.join(root, name) for name in os.listdir(root)))
        except OSError:
            pass
        for candidate in candidates:
            if candidate not in found and is_game(candidate):
                found.append(candidate)
    return found


def known_folders(settings, discovered=None):
    """Merge the remembered folders with the ones found on this PC."""
    if discovered is None:
        discovered = discover_game_folders()
    folders = []
    seen = set()
    for folder in list(settings.get("folders") or ()) + list(discovered):
        path = os.path.normpath(str(folder))
        key = os.path.normcase(path)
        if not path or path == "." or key in seen:
            continue
        seen.add(key)
        folders.append(path)
    return folders


def settings_path():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "WoTOfflineBattles", "launcher.json")


def load_settings(path=None):
    return _read_json(path or settings_path()) or {}


def save_settings(values, path=None):
    path = path or settings_path()
    try:
        _write_json(path, dict(values))
    except (IOError, OSError):
        return False
    return True
