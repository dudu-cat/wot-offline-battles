#!/usr/bin/env python3
"""Desktop launcher for the World of Tanks offline-battle client ports.

The launcher writes the server address before the client starts, runs the LAN
server for a host, and stops that server when the game exits. In the game the
player then only clicks the battle button.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import core
else:
    from . import core


WINDOW_TITLE = "World of Tanks Offline Battles"
MODE_LABELS = (
    (core.MODE_SINGLE, "Single player"),
    (core.MODE_HOST, "Host a LAN battle on this PC"),
    (core.MODE_JOIN, "Join a LAN battle"),
)


def _no_console_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LauncherWindow(object):
    def __init__(self, tk_module, filedialog_module):
        self._tk = tk_module
        self._filedialog = filedialog_module
        self._server = None
        self._game = None
        self._busy = False
        self._build()

    def _build(self):
        tk = self._tk
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        settings = core.load_settings()

        frame = tk.Frame(self.root, padx=12, pady=12)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="Game folder").grid(row=0, column=0, sticky="w")
        self.game_root = tk.StringVar(value=settings.get("game_root", ""))
        entry = tk.Entry(frame, textvariable=self.game_root, width=52)
        entry.grid(row=0, column=1, sticky="we", padx=(6, 6))
        tk.Button(frame, text="Browse...", command=self._browse).grid(
            row=0, column=2, sticky="e")
        self.game_root.trace_add("write", lambda *unused: self._refresh_client())

        self.client_label = tk.Label(frame, text="", anchor="w")
        self.client_label.grid(row=1, column=0, columnspan=3, sticky="we",
                               pady=(4, 10))

        self.mode = tk.StringVar(value=settings.get("mode", core.MODE_SINGLE))
        row = 2
        for value, label in MODE_LABELS:
            tk.Radiobutton(frame, text=label, value=value, variable=self.mode,
                           anchor="w", command=self._refresh_mode).grid(
                row=row, column=0, columnspan=3, sticky="w")
            row += 1

        tk.Label(frame, text="Server address").grid(row=row, column=0,
                                                    sticky="w", pady=(6, 0))
        self.join_address = tk.StringVar(
            value=settings.get("join_address", "192.168.1.10:%d" %
                               core.DEFAULT_SERVER_PORT))
        self.join_entry = tk.Entry(frame, textvariable=self.join_address,
                                   width=52)
        self.join_entry.grid(row=row, column=1, columnspan=2, sticky="we",
                             padx=(6, 0), pady=(6, 0))
        row += 1

        tk.Label(frame, text="Player name").grid(row=row, column=0, sticky="w",
                                                 pady=(6, 0))
        self.player_name = tk.StringVar(value=settings.get("name", ""))
        tk.Entry(frame, textvariable=self.player_name, width=52).grid(
            row=row, column=1, columnspan=2, sticky="we", padx=(6, 0),
            pady=(6, 0))
        row += 1

        self.start_button = tk.Button(frame, text="Start game",
                                      command=self._start)
        self.start_button.grid(row=row, column=0, columnspan=3, sticky="we",
                               pady=(12, 6))
        row += 1

        self.log_view = tk.Text(frame, height=12, width=72, state="disabled",
                                wrap="none")
        self.log_view.grid(row=row, column=0, columnspan=3, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(row, weight=1)

        self._refresh_client()
        self._refresh_mode()

    def _browse(self):
        selected = self._filedialog.askdirectory(
            title="Select the World of Tanks folder",
            initialdir=self.game_root.get() or None)
        if selected:
            self.game_root.set(os.path.normpath(selected))

    def _refresh_client(self):
        status = core.inspect_game_root(self.game_root.get())
        if not self.game_root.get().strip():
            text = "Select the folder that contains %s." % core.GAME_EXECUTABLE
        elif not status["has_executable"]:
            text = "%s was not found in this folder." % core.GAME_EXECUTABLE
        elif status["client"] is None:
            text = "This client version is not supported."
        elif not status["mod_installed"]:
            text = ("World of Tanks %s found. Start game installs the mod." %
                    (status["version"] or status["client"]))
        else:
            text = "World of Tanks %s ready. Start game updates the mod." % (
                status["version"] or status["client"])
        self.client_label.config(text=text)
        return status

    def _refresh_mode(self):
        state = "normal" if self.mode.get() == core.MODE_JOIN else "disabled"
        self.join_entry.config(state=state)

    def _log(self, message):
        def append():
            self.log_view.config(state="normal")
            self.log_view.insert("end", message.rstrip() + "\n")
            self.log_view.see("end")
            self.log_view.config(state="disabled")

        self.root.after(0, append)

    def _set_busy(self, busy):
        self._busy = busy
        self.root.after(
            0, lambda: self.start_button.config(
                state="disabled" if busy else "normal",
                text="Game is running" if busy else "Start game"))

    def _save_settings(self):
        core.save_settings({
            "game_root": self.game_root.get().strip(),
            "mode": self.mode.get(),
            "join_address": self.join_address.get().strip(),
            "name": self.player_name.get().strip(),
        })

    def _start(self):
        if self._busy:
            return
        status = self._refresh_client()
        try:
            session = core.plan_session(status, self.mode.get(),
                                        self.join_address.get())
        except core.LauncherError as error:
            self._log(str(error))
            return
        self._save_settings()
        self._set_busy(True)
        thread = threading.Thread(
            target=self._run_session,
            args=(status["path"], session, self.player_name.get().strip()))
        thread.daemon = True
        thread.start()

    def _run_session(self, game_root, session, name):
        host = session["host"]
        port = session["tcp_port"]
        try:
            for action in core.install_client_mod(game_root,
                                                  session["client"]):
                self._log(action)
            for path in core.write_settings(game_root, session["client"],
                                            session["mode"], host, port, name):
                self._log("Wrote %s" % path)
            if session["needs_server"]:
                if not self._start_server(game_root, session["client"]):
                    return
            elif session["mode"] == core.MODE_JOIN:
                if core.probe_endpoint(host, port):
                    self._log("The server at %s:%d answered." % (host, port))
                else:
                    self._log("Warning: %s:%d did not answer. Start the game "
                              "anyway and click the battle button when the "
                              "host is ready." % (host, port))
            self._run_game(game_root)
        except core.LauncherError as error:
            self._log(str(error))
        except Exception as error:  # The window must survive any failure.
            self._log("The launcher failed: %s" % error)
        finally:
            self._stop_server()
            self._set_busy(False)

    def _start_server(self, game_root, port_version):
        command = core.server_child_command(port_version)
        environment = core.server_environment(port_version, game_root)
        self._log("Starting the %s LAN server..." % port_version)
        self._server = subprocess.Popen(
            command, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=_no_console_flags())
        pump = threading.Thread(target=self._pump_server_output)
        pump.daemon = True
        pump.start()
        if not core.wait_for_listener(core.LOCAL_HOST, core.DEFAULT_SERVER_PORT):
            self._log("The LAN server did not open port %d." %
                      core.DEFAULT_SERVER_PORT)
            return False
        self._log("The LAN server listens on port %d." %
                  core.DEFAULT_SERVER_PORT)
        for address in core.local_addresses():
            self._log("Other players join with %s:%d" %
                      (address, core.DEFAULT_SERVER_PORT))
        return True

    def _pump_server_output(self):
        server = self._server
        if server is None or server.stdout is None:
            return
        for line in iter(server.stdout.readline, b""):
            self._log("[server] " + line.decode("utf-8", "replace").rstrip())

    def _run_game(self, game_root):
        self._log("Starting %s..." % core.GAME_EXECUTABLE)
        self._game = subprocess.Popen([core.game_executable(game_root)],
                                      cwd=game_root)
        self._game.wait()
        self._game = None
        self._log("The game closed.")

    def _stop_server(self):
        server = self._server
        self._server = None
        if server is None or server.poll() is not None:
            return
        self._log("Stopping the LAN server...")
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    def _on_close(self):
        self._stop_server()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def _open_server_log():
    """A windowed build can leave the child process without usable streams."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    path = os.path.join(os.path.dirname(core.settings_path()), "server.log")
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    stream = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def _serve(argv):
    index = argv.index(core.SERVE_FLAG)
    _open_server_log()
    if index + 1 >= len(argv):
        print("--serve needs a client version.")
        return 2
    port_version = argv[index + 1]
    print("Starting the %s LAN server from %s" %
          (port_version, core.server_root()))
    try:
        core.run_server_payload(port_version)
    except Exception:
        # A windowed build turns an unhandled exception into a dialog that
        # waits for a user who is not there. Report it and exit instead.
        import traceback

        print("The %s LAN server stopped: %s" %
              (port_version, traceback.format_exc()))
        return 1
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if core.SERVE_FLAG in argv:
        return _serve(argv)
    import tkinter
    from tkinter import filedialog

    LauncherWindow(tkinter, filedialog).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
