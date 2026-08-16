#!/usr/bin/env python3
"""Copy the files that the packaged launcher must carry for both servers.

The 0.8.2 navigation graphs stay out of the bundle. The launcher points that
server at the graphs installed with the client, so both sides always agree.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

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


def repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _copy_file(source, target):
    directory = os.path.dirname(target)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    shutil.copy2(source, target)


def stage(target_root, source_root=None):
    """Write the server payload under target_root and return every file."""
    source_root = source_root or repository_root()
    if os.path.isdir(target_root):
        shutil.rmtree(target_root)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True,
                        help="directory that receives the server payload")
    parser.add_argument("--source", default=None,
                        help="repository root (default: this checkout)")
    arguments = parser.parse_args(argv)
    written = stage(arguments.output, arguments.source)
    print("Staged %d server payload files in %s" %
          (len(written), arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
