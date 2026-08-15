#!/usr/bin/env python3
"""Zero-configuration Windows entry point for the LAN battle server."""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import sys
import traceback

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 28782
SERVER_MAX_PLAYERS = 30
WINDOWS_FIREWALL_RULE_PREFIX = "WoT 0.9.22 LAN Server"
WINDOWS_FIREWALL_REMOTE_IP = "any"


def _is_frozen_windows_executable():
    """Return whether this process is the packaged Windows server."""
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _windows_firewall_rule_name(
        executable_path, port, remote_ip=WINDOWS_FIREWALL_REMOTE_IP):
    """Build a stable rule name for this executable, port, and scope."""
    normalized_path = str(executable_path).replace("/", "\\").casefold()
    normalized_remote_ip = str(remote_ip).strip().casefold()
    identity = "%s|%d|%s" % (
        normalized_path, int(port), normalized_remote_ip)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return "%s TCP %d - %s" % (
        WINDOWS_FIREWALL_RULE_PREFIX, int(port), digest)


def _powershell_single_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _windows_firewall_rule_exists(rule_name, runner=None,
                                  powershell_path=None):
    """Check the deterministic inbound rule without requesting elevation."""
    if runner is None:
        runner = subprocess.run
    if powershell_path is None:
        powershell_path = _windows_system_path(
            r"WindowsPowerShell\v1.0\powershell.exe")
    script = (
        "$rule = Get-NetFirewallRule -DisplayName %s -Direction Inbound "
        "-Enabled True -Action Allow -ErrorAction SilentlyContinue | "
        "Select-Object -First 1; "
        "if ($null -eq $rule) { exit 1 }; exit 0"
    ) % _powershell_single_quote(rule_name)
    result = runner(
        [
            powershell_path, "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=5.0,
    )
    return result.returncode == 0


def _windows_system_path(relative_path, get_system_directory=None):
    """Resolve a relative executable through the trusted system directory."""
    if get_system_directory is None:
        get_system_directory = ctypes.windll.kernel32.GetSystemDirectoryW
        get_system_directory.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
        get_system_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(get_system_directory(buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        raise OSError("GetSystemDirectoryW failed")
    return (buffer.value.rstrip("\\/") + "\\" +
            str(relative_path).lstrip("\\/"))


def _windows_system_netsh_path(get_system_directory=None):
    return _windows_system_path(
        "netsh.exe", get_system_directory=get_system_directory)


def _request_windows_firewall_rule(rule_name, executable_path, port,
                                   shell_execute=None, netsh_path=None):
    """Open one UAC prompt for the narrowly scoped inbound rule."""
    arguments = subprocess.list2cmdline([
        "advfirewall", "firewall", "add", "rule",
        "name=" + rule_name,
        "dir=in",
        "action=allow",
        "enable=yes",
        "profile=any",
        "program=" + executable_path,
        "protocol=TCP",
        "localport=%d" % int(port),
        "remoteip=" + WINDOWS_FIREWALL_REMOTE_IP,
    ])
    if shell_execute is None:
        shell_execute = ctypes.windll.shell32.ShellExecuteW
        shell_execute.restype = ctypes.c_void_p
    if netsh_path is None:
        netsh_path = _windows_system_netsh_path()
    result = shell_execute(
        None, "runas", netsh_path, arguments, None, 1)
    return int(result or 0) > 32


def _ensure_windows_firewall_rule(port):
    """Request one inbound rule only for the packaged Windows executable."""
    if not _is_frozen_windows_executable():
        return False

    executable_path = os.path.abspath(sys.executable)
    rule_name = _windows_firewall_rule_name(executable_path, port)
    try:
        if _windows_firewall_rule_exists(rule_name):
            return True
        print(
            "Windows Firewall access needs approval for LAN clients; "
            "opening one UAC prompt.")
        if _request_windows_firewall_rule(
                rule_name, executable_path, port):
            print(
                "Windows Firewall rule request launched for TCP %d "
                "(all remote addresses)." % int(port))
            return True
        print(
            "Windows Firewall rule was not requested; remote LAN clients "
            "may remain blocked.")
    except Exception as error:
        print(
            "Windows Firewall rule setup failed (%s); server will continue."
            % error)
    return False


def _pause_after_error():
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return
    try:
        input("Press Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass


def _load_server():
    from lan_battle_server import DEFAULT_MAP, run_server
    return (DEFAULT_MAP, run_server)


def main():
    print("WoT 0.9.22 Offline LAN Server")
    print("Listening on all network interfaces, port %d." % SERVER_PORT)
    print("Use 127.0.0.1 in the client on this PC, or this PC's LAN IP on another PC.")
    print("Press Ctrl+C to stop the server.\n")
    try:
        default_map, run_server = _load_server()
        _ensure_windows_firewall_rule(SERVER_PORT)
        run_server(
            SERVER_HOST,
            SERVER_PORT,
            default_map,
            SERVER_MAX_PLAYERS,
        )
    except Exception:
        traceback.print_exc()
        _pause_after_error()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
