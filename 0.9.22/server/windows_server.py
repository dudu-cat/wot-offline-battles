#!/usr/bin/env python3
"""Zero-configuration Windows entry point for the LAN battle server."""

from __future__ import annotations

import sys
import traceback

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 28782
SERVER_MAX_PLAYERS = 30


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
