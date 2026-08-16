"""Launcher logic shared by the 0.8.2 and 0.9.22 client ports.

This module keeps every version-specific contract explicit. It writes only the
user-owned settings files that each port already reads at client startup.
"""

from __future__ import annotations

import glob
import json
import os
import re
import socket
import subprocess
import sys

PORT_0_8_2 = "0.8.2"
PORT_0_9_22 = "0.9.22"
SUPPORTED_PORTS = (PORT_0_8_2, PORT_0_9_22)

MODE_SINGLE = "single"
MODE_HOST = "host"
MODE_JOIN = "join"
MODES = (MODE_SINGLE, MODE_HOST, MODE_JOIN)

DEFAULT_SERVER_PORT = 28782
LOCAL_HOST = "127.0.0.1"
LISTEN_HOST = "0.0.0.0"
GAME_EXECUTABLE = "WorldOfTanks.exe"
NAVGRAPH_DIR_ENV = "WOT_OFFLINE_NAVGRAPH_DIR"
# The client can close its first process and start another one while it
# starts up. The launcher waits this long after the last one before it
# stops the LAN server.
GAME_RESTART_GRACE_SECONDS = 30.0
KNOWN_FOLDER_LIMIT = 10
COMMON_GAME_ROOTS = (
    "C:\\Games", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\WOT", "D:\\", "D:\\Games", "D:\\Program Files",
    "E:\\", "E:\\Games",
)
SERVE_FLAG = "--serve"

_VERSION_PATTERN = re.compile(r"v\.(\d+(?:\.\d+)+)")

_MOD_MARKERS = {
    PORT_0_8_2: os.path.join(
        "res_mods", "0.8.2", "scripts", "client", "gui", "mods", "offhangar"),
    PORT_0_9_22: os.path.join("mods", "0.9.22.0.1"),
}

_NAVGRAPH_RELATIVE_DIR = os.path.join(
    "res_mods", "0.8.2", "scripts", "client", "gui", "mods", "offhangar",
    "navgraphs")

_SERVER_ENTRIES = {
    PORT_0_8_2: (os.path.join("0.8.2"), "lan_battle_server.py"),
    PORT_0_9_22: (os.path.join("0.9.22", "server"), "windows_server.py"),
}

_SERVER_ARGUMENTS = {
    PORT_0_8_2: ("--host", LISTEN_HOST, "--port", str(DEFAULT_SERVER_PORT)),
    PORT_0_9_22: (),
}


class LauncherError(Exception):
    """A user-correctable launcher failure."""


def game_executable(game_root):
    return os.path.join(game_root, GAME_EXECUTABLE)


def read_client_version(game_root):
    """Return the client version recorded in the stock version.xml."""
    path = os.path.join(game_root, "version.xml")
    try:
        with open(path, "rb") as stream:
            text = stream.read().decode("utf-8", "replace")
    except (IOError, OSError):
        return None
    match = _VERSION_PATTERN.search(text)
    if match is None:
        return None
    return match.group(1)


def port_for_version(version):
    if not version:
        return None
    for port_version in SUPPORTED_PORTS:
        if version == port_version or version.startswith(port_version + "."):
            return port_version
    return None


def installed_port(game_root):
    """Return the port whose client mod is installed in this game folder."""
    for port_version, marker in _MOD_MARKERS.items():
        if os.path.isdir(os.path.join(game_root, marker)):
            return port_version
    return None


def detect_port(game_root):
    return (port_for_version(read_client_version(game_root)) or
            installed_port(game_root))


def inspect_game_root(game_root):
    """Describe one game folder for the launcher window."""
    game_root = os.path.abspath(game_root or "")
    version = read_client_version(game_root)
    port_version = port_for_version(version) or installed_port(game_root)
    return {
        "path": game_root,
        "has_executable": os.path.isfile(game_executable(game_root)),
        "version": version,
        "client": port_version,
        "mod_installed": installed_port(game_root) == port_version,
    }


def plan_session(status, mode, join_text=""):
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
    return {
        "client": port_version,
        "mode": mode,
        "host": host,
        "tcp_port": tcp_port,
        "needs_server": server_required(port_version, mode),
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


def endpoint_for_mode(mode, join_text="", default_port=DEFAULT_SERVER_PORT):
    if mode == MODE_JOIN:
        return parse_endpoint(join_text, default_port)
    return (LOCAL_HOST, default_port)


def server_required(port_version, mode):
    """Report whether the launcher must run a server for this mode."""
    if mode == MODE_JOIN:
        return False
    if mode == MODE_HOST:
        return True
    return port_version == PORT_0_9_22


def _write_json(path, value, indent=2):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temporary_path = path + ".tmp"
    payload = json.dumps(value, indent=indent, sort_keys=False) + "\n"
    with open(temporary_path, "wb") as stream:
        stream.write(payload.encode("utf-8"))
    os.replace(temporary_path, path)


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
    config["network_mode"] = mode != MODE_SINGLE
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

# Directories the launcher wipes before it copies the bundled mod, the
# directories it replaces, and the files it writes only when they are absent.
_CLIENT_INSTALL = {
    PORT_0_8_2: {
        "clear": ("res_mods/0.8.2",),
        "prune": (),
        "merge": (),
        "replace": (
            ("scripts", "res_mods/0.8.2/scripts"),
            ("gui", "res_mods/0.8.2/gui"),
        ),
        "keep": (),
    },
    PORT_0_9_22: {
        "clear": (),
        # Other mods may live in the same directory, so only this package's
        # own files are pruned before the bundled one is copied in.
        "prune": (("mods/0.9.22.0.1", "org.peng.offline_lan_0922*"),),
        "merge": (("mods/0.9.22.0.1", "mods/0.9.22.0.1"),),
        "replace": (
            ("mods/configs/offline_lan_0922/navgraphs",
             "mods/configs/offline_lan_0922/navgraphs"),
            ("mods/configs/offline_lan_0922/foliage",
             "mods/configs/offline_lan_0922/foliage"),
            ("mods/configs/offline_lan_0922/destructibles",
             "mods/configs/offline_lan_0922/destructibles"),
        ),
        "keep": (
            ("mods/configs/offline_lan_0922/config.json",
             "mods/configs/offline_lan_0922/config.json"),
        ),
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


def client_root(port_version, base_dir=None):
    """Return the directory that holds one port's installable client mod."""
    if base_dir is not None:
        return os.path.join(base_dir, CLIENT_PAYLOAD_DIR, port_version)
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return os.path.join(bundle_dir, CLIENT_PAYLOAD_DIR, port_version)
    root = repository_root()
    if port_version == PORT_0_8_2:
        return os.path.join(root, PORT_0_8_2)
    overlays = sorted(
        path for path in glob.glob(os.path.join(
            root, PORT_0_9_22, "dist", "WoT-0.9.22-LAN-Client-*"))
        if os.path.isdir(path))
    return overlays[-1] if overlays else os.path.join(root, PORT_0_9_22,
                                                      "dist")


def _inside(root, path):
    root = os.path.abspath(root) + os.path.sep
    return os.path.abspath(path).startswith(root)


def install_client_mod(game_root, port_version, base_dir=None):
    """Replace the installed mod with the bundled one and report what changed.

    User files stay: the 0.9.22 configuration directory keeps everything this
    package does not own, and an existing `config.json` is never overwritten.
    """
    import shutil

    layout = _CLIENT_INSTALL.get(port_version)
    if layout is None:
        raise LauncherError("This game folder is not a supported client.")
    source_root = client_root(port_version, base_dir)
    if not os.path.isdir(source_root):
        raise LauncherError(
            "This launcher carries no %s mod files." % port_version)
    actions = []
    for relative in layout["clear"]:
        target = os.path.join(game_root, *relative.split("/"))
        if _inside(game_root, target) and os.path.isdir(target):
            shutil.rmtree(target)
            actions.append("Removed the old %s" % relative)
    for relative, pattern in layout["prune"]:
        directory = os.path.join(game_root, *relative.split("/"))
        if not _inside(game_root, directory):
            continue
        for path in sorted(glob.glob(os.path.join(directory, pattern))):
            if os.path.isfile(path):
                os.unlink(path)
                actions.append("Removed the old %s" %
                               os.path.basename(path))
    for source_relative, target_relative in layout["replace"]:
        source = os.path.join(source_root, *source_relative.split("/"))
        target = os.path.join(game_root, *target_relative.split("/"))
        if not os.path.isdir(source):
            raise LauncherError("The bundled mod is incomplete: %s" %
                                source_relative)
        if not _inside(game_root, target):
            raise LauncherError("Refusing to write outside the game folder.")
        if os.path.isdir(target):
            shutil.rmtree(target)
        parent = os.path.dirname(target)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        shutil.copytree(source, target)
        actions.append("Installed %s" % target_relative)
    for source_relative, target_relative in layout["merge"]:
        source = os.path.join(source_root, *source_relative.split("/"))
        target = os.path.join(game_root, *target_relative.split("/"))
        if not os.path.isdir(source):
            raise LauncherError("The bundled mod is incomplete: %s" %
                                source_relative)
        if not _inside(game_root, target):
            raise LauncherError("Refusing to write outside the game folder.")
        if not os.path.isdir(target):
            os.makedirs(target)
        for name in sorted(os.listdir(source)):
            shutil.copy2(os.path.join(source, name),
                         os.path.join(target, name))
        actions.append("Installed %s" % target_relative)
    for source_relative, target_relative in layout["keep"]:
        source = os.path.join(source_root, *source_relative.split("/"))
        target = os.path.join(game_root, *target_relative.split("/"))
        if not os.path.isfile(source) or os.path.isfile(target):
            continue
        parent = os.path.dirname(target)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        shutil.copy2(source, target)
        actions.append("Installed %s" % target_relative)
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


def server_environment(port_version, game_root, environment=None):
    """Point the 0.8.2 server at the navigation graphs of this client."""
    environment = dict(os.environ if environment is None else environment)
    if port_version == PORT_0_8_2:
        environment[NAVGRAPH_DIR_ENV] = os.path.join(
            game_root, _NAVGRAPH_RELATIVE_DIR)
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
    endpoint = "%s:%d" % (host, int(port))
    if mode == MODE_JOIN:
        if answered:
            return "The server at %s answered." % endpoint
        return ("No answer from %s. Check that the host started the battle "
                "and that its firewall allows TCP %d." %
                (endpoint, int(port)))
    if answered:
        return ("A server already listens on %s. Close it before you host "
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
