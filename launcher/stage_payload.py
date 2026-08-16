#!/usr/bin/env python3
"""Copy the files the packaged launcher carries.

The bundle holds two payloads: the LAN servers it runs, and the client mod it
installs into the game folder. The 0.8.2 navigation graphs travel with the
client mod, so the 0.8.2 server reads them from the installed client through
`WOT_OFFLINE_NAVGRAPH_DIR`.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

SERVER_DIR = "servers"
CLIENT_DIR = "client"

PAYLOAD_FILES = {
    "0.8.2": (
        "lan_battle_server.py",
        "server_bot_ai.py",
        "server_bot_navigation.py",
        "scripts/client/gui/mods/__init__.py",
        "scripts/client/gui/mods/offhangar/__init__.py",
        "scripts/client/gui/mods/offhangar/bot_ai_cover.py",
        "scripts/client/gui/mods/offhangar/bot_ai_navigation.py",
    ),
    "0.9.22": (
        "server/lan_battle_server.py",
        "server/server_bot_ai.py",
        "server/windows_server.py",
    ),
}

PAYLOAD_TREES = {
    "0.9.22": ("src/res/scripts/client/gui/mods/offline_lan_0922",),
}

# Client mod trees, as (source directory, directory inside the payload).
CLIENT_TREES = {
    "0.8.2": (("scripts", "scripts"), ("gui", "gui")),
    "0.9.22": (("mods", "mods"),),
}

CLIENT_0922_OVERLAY = "WoT-0.9.22-LAN-Client-*"


def repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client_source(port_version, source_root=None):
    """Return the directory that holds one port's installable client mod."""
    source_root = source_root or repository_root()
    if port_version == "0.8.2":
        return os.path.join(source_root, "0.8.2")
    overlays = sorted(glob.glob(os.path.join(
        source_root, "0.9.22", "dist", CLIENT_0922_OVERLAY)))
    overlays = [path for path in overlays if os.path.isdir(path)]
    if not overlays:
        return None
    return overlays[-1]


def _copy_file(source, target):
    directory = os.path.dirname(target)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    shutil.copy2(source, target)


def _copy_tree(source, target):
    written = []
    for directory, unused_dirs, names in os.walk(source):
        for name in names:
            if name.endswith(".pyc"):
                continue
            source_path = os.path.join(directory, name)
            target_path = os.path.join(
                target, os.path.relpath(source_path, source))
            _copy_file(source_path, target_path)
            written.append(target_path)
    return written


def stage_servers(target_root, source_root=None):
    source_root = source_root or repository_root()
    written = []
    for port_version, relative_paths in PAYLOAD_FILES.items():
        for relative_path in relative_paths:
            source = os.path.join(source_root, port_version,
                                  *relative_path.split("/"))
            target = os.path.join(target_root, port_version,
                                  *relative_path.split("/"))
            _copy_file(source, target)
            written.append(target)
    for port_version, relative_dirs in PAYLOAD_TREES.items():
        for relative_dir in relative_dirs:
            source = os.path.join(source_root, port_version,
                                  *relative_dir.split("/"))
            target = os.path.join(target_root, port_version,
                                  *relative_dir.split("/"))
            written.extend(_copy_tree(source, target))
    return written


def stage_clients(target_root, source_root=None, client_0922=None):
    source_root = source_root or repository_root()
    written = []
    for port_version, trees in CLIENT_TREES.items():
        if port_version == "0.9.22" and client_0922 is not None:
            port_source = client_0922
        else:
            port_source = client_source(port_version, source_root)
        if port_source is None or not os.path.isdir(port_source):
            raise ValueError(
                "no installable client mod for %s; build it first" %
                port_version)
        for source_relative, target_relative in trees:
            source = os.path.join(port_source, *source_relative.split("/"))
            if not os.path.isdir(source):
                raise ValueError("client mod is incomplete: %s/%s" %
                                 (port_version, source_relative))
            target = os.path.join(target_root, port_version,
                                  *target_relative.split("/"))
            written.extend(_copy_tree(source, target))
    return written


def stage(target_root, source_root=None, include_clients=True,
          client_0922=None):
    """Write the complete payload under target_root and return every file."""
    if os.path.isdir(target_root):
        shutil.rmtree(target_root)
    written = stage_servers(os.path.join(target_root, SERVER_DIR), source_root)
    if include_clients:
        written.extend(stage_clients(
            os.path.join(target_root, CLIENT_DIR), source_root, client_0922))
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True,
                        help="directory that receives the payload")
    parser.add_argument("--source", default=None,
                        help="repository root (default: this checkout)")
    parser.add_argument("--client-0922", default=None,
                        help="0.9.22 client overlay directory that holds mods")
    parser.add_argument("--servers-only", action="store_true",
                        help="stage the LAN servers without the client mods")
    arguments = parser.parse_args(argv)
    try:
        written = stage(arguments.output, arguments.source,
                        include_clients=not arguments.servers_only,
                        client_0922=arguments.client_0922)
    except ValueError as error:
        sys.stderr.write("%s\n" % error)
        return 1
    print("Staged %d payload files in %s" % (len(written), arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
