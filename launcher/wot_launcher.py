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
    import vehicle_editor_ui
    import vehicle_overlays
else:
    from . import core, vehicle_editor_ui, vehicle_overlays


LAUNCHER_VERSION = "0.5.0"
WINDOW_TITLE = "World of Tanks Offline Battles %s" % LAUNCHER_VERSION
MODE_LABELS = (
    (core.MODE_SINGLE, "Single player"),
    (core.MODE_HOST, "Host a LAN battle on this PC"),
    (core.MODE_JOIN, "Join a LAN battle"),
)


def _no_console_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LauncherWindow(object):
    def __init__(self, tk_module, ttk_module, filedialog_module):
        self._tk = tk_module
        self._ttk = ttk_module
        self._filedialog = filedialog_module
        self._server = None
        self._game = None
        self._busy = False
        self._maintenance_busy = False
        self._selected_client = None
        self._profile_names = []
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
        self._folders = core.known_folders(settings)
        self.game_root = tk.StringVar(
            value=settings.get("game_root", "") or
            (self._folders[0] if self._folders else ""))
        self.folder_box = self._ttk.Combobox(
            frame, textvariable=self.game_root, values=list(self._folders),
            width=50)
        self.folder_box.grid(row=0, column=1, sticky="we", padx=(6, 6))
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
                                   width=40)
        self.join_entry.grid(row=row, column=1, sticky="we", padx=(6, 6),
                             pady=(6, 0))
        self.test_button = tk.Button(frame, text="Test connection",
                                     command=self._test_connection)
        self.test_button.grid(row=row, column=2, sticky="e", pady=(6, 0))
        row += 1

        tk.Label(frame, text="Player name").grid(row=row, column=0, sticky="w",
                                                 pady=(6, 0))
        self.player_name = tk.StringVar(value=settings.get("name", ""))
        tk.Entry(frame, textvariable=self.player_name, width=52).grid(
            row=row, column=1, columnspan=2, sticky="we", padx=(6, 0),
            pady=(6, 0))
        row += 1

        tk.Label(frame, text="Tanks per team (including players)").grid(
            row=row, column=0, sticky="w", pady=(6, 0))
        self.team_size = tk.StringVar(
            value=str(settings.get("team_size", core.DEFAULT_TEAM_SIZE)))
        self.team_size_box = self._ttk.Combobox(
            frame, textvariable=self.team_size,
            values=tuple(str(value) for value in range(
                core.MIN_TEAM_SIZE, core.MAX_TEAM_SIZE + 1)), width=10)
        self.team_size_box.grid(row=row, column=1, sticky="w", padx=(6, 6),
                                pady=(6, 0))
        row += 1

        tk.Label(frame, text="Vehicle data profile").grid(
            row=row, column=0, sticky="w", pady=(6, 0))
        self.vehicle_profile = tk.StringVar(
            value=settings.get(
                "vehicle_profile", vehicle_overlays.ORIGINAL_PROFILE_LABEL))
        self.vehicle_profile_box = self._ttk.Combobox(
            frame, textvariable=self.vehicle_profile,
            values=(vehicle_overlays.ORIGINAL_PROFILE_LABEL,),
            state="disabled", width=40)
        self.vehicle_profile_box.grid(
            row=row, column=1, columnspan=2, sticky="we", padx=(6, 0),
            pady=(6, 0))
        self.vehicle_profile_box.bind(
            "<<ComboboxSelected>>", self._profile_selected)
        row += 1

        profile_actions = tk.Frame(frame)
        profile_actions.grid(
            row=row, column=0, columnspan=3, sticky="we", pady=(6, 0))
        self.new_profile_button = tk.Button(
            profile_actions, text="New profile...",
            command=self._new_vehicle_profile)
        self.new_profile_button.pack(side="left", fill="x", expand=True)
        self.vehicle_editor_button = tk.Button(
            profile_actions, text="Edit selected profile...",
            command=self._open_vehicle_editor)
        self.vehicle_editor_button.pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.delete_profile_button = tk.Button(
            profile_actions, text="Delete selected profile...",
            command=self._delete_vehicle_profile)
        self.delete_profile_button.pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        row += 1

        maintenance = tk.Frame(frame)
        maintenance.grid(row=row, column=0, columnspan=3, sticky="we",
                         pady=(10, 0))
        self.repair_button = tk.Button(
            maintenance, text="Repair startup (keep saved data)",
            command=self._repair_startup)
        self.repair_button.pack(side="left", fill="x", expand=True)
        self.reset_button = tk.Button(
            maintenance, text="Reset all offline data...",
            command=self._reset_all_state)
        self.reset_button.pack(side="left", fill="x", expand=True,
                               padx=(6, 0))
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
        self._recover_stale_vehicle_profile()

    def _browse(self):
        selected = self._filedialog.askdirectory(
            title="Select the World of Tanks folder",
            initialdir=self.game_root.get() or None)
        if selected:
            self.game_root.set(os.path.normpath(selected))
            self._remember_folder()

    def _remember_folder(self):
        """Keep this folder at the top of the list for the next launch."""
        folder = self.game_root.get().strip()
        if not folder or not os.path.isfile(core.game_executable(folder)):
            return False
        self._folders = core.remember_folder(self._folders, folder)
        self.folder_box.config(values=list(self._folders))
        self._save_settings()
        return True

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
        self._selected_client = status["client"]
        self._refresh_profiles(status)
        self._update_action_controls()
        if hasattr(self, "team_size_box"):
            self._refresh_mode()
        return status

    def _update_action_controls(self):
        if self._busy:
            self.start_button.config(state="normal", text="Kill the game")
        elif self._maintenance_busy:
            self.start_button.config(state="disabled", text="Start game")
        else:
            self.start_button.config(state="normal", text="Start game")
        maintenance_state = (
            "normal" if self._selected_client == core.PORT_0_9_22 and
            not self._busy and not self._maintenance_busy else "disabled")
        self.repair_button.config(state=maintenance_state)
        self.reset_button.config(state=maintenance_state)
        profile_state = (
            "readonly" if maintenance_state == "normal" and
            self.mode.get() == core.MODE_SINGLE else "disabled")
        self.vehicle_profile_box.config(state=profile_state)
        create_state = (
            "normal" if profile_state == "readonly" else "disabled")
        self.new_profile_button.config(state=create_state)
        selected_profile = self.vehicle_profile.get().strip()
        selected_custom = (
            profile_state == "readonly" and selected_profile in
            self._profile_names)
        edit_state = "normal" if selected_custom else "disabled"
        self.vehicle_editor_button.config(state=edit_state)
        self.delete_profile_button.config(state=edit_state)

    def _refresh_profiles(self, status=None):
        status = status or core.inspect_game_root(self.game_root.get())
        names = []
        if status.get("client") == core.PORT_0_9_22:
            try:
                names = vehicle_overlays.list_vehicle_profiles(status["path"])
            except vehicle_overlays.VehicleOverlayError as error:
                if hasattr(self, "log_view"):
                    self._log("Vehicle profiles could not be loaded: %s" % error)
        self._profile_names = list(names)
        values = tuple(
            [vehicle_overlays.ORIGINAL_PROFILE_LABEL] + self._profile_names)
        self.vehicle_profile_box.config(values=values)
        if self.vehicle_profile.get().strip() not in values:
            self.vehicle_profile.set(vehicle_overlays.ORIGINAL_PROFILE_LABEL)
        return values

    def _profile_selected(self, unused_event=None):
        self._update_action_controls()
        self._save_settings()

    def _recover_stale_vehicle_profile(self):
        game_root = self.game_root.get().strip()
        if self._selected_client != core.PORT_0_9_22:
            return 0
        manifest_exists = os.path.lexists(
            vehicle_overlays.manifest_path(game_root))
        try:
            recovery_exists = vehicle_overlays.has_pending_vehicle_recovery(
                game_root)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log("Vehicle profile recovery could not be checked: %s" % error)
            return 0
        if not manifest_exists and not recovery_exists:
            return 0
        if core.game_is_running():
            self._log(
                "A vehicle profile is active while World of Tanks is running; "
                "close the game and reopen this launcher before any other "
                "launch so it can be cleaned safely.")
            return 0
        try:
            recovered = vehicle_overlays.recover_vehicle_profile_transactions(
                game_root)
            imported = vehicle_overlays.preserve_legacy_vehicle_overlay(
                game_root)
            removed = vehicle_overlays.restore_vehicle_defaults(game_root)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log(
                "A stale vehicle profile could not be cleaned: %s" % error)
            return 0
        if recovered or imported:
            self._refresh_profiles(core.inspect_game_root(game_root))
        if imported:
            self._log(
                "Preserved the previous vehicle edits as profile '%s'." %
                imported)
        if recovered:
            self._log(
                "Recovered an interrupted vehicle profile update.")
        if removed:
            self._log(
                "Cleaned a stale temporary vehicle profile from the previous "
                "launcher session.")
        return removed + recovered

    def _refresh_mode(self):
        state = "normal" if self.mode.get() == core.MODE_JOIN else "disabled"
        self.join_entry.config(state=state)
        self.test_button.config(state="normal")
        team_state = (
            "readonly" if self._selected_client == core.PORT_0_9_22 and
            self.mode.get() != core.MODE_JOIN else "disabled")
        self.team_size_box.config(state=team_state)
        if self.mode.get() != core.MODE_SINGLE:
            self.vehicle_profile.set(vehicle_overlays.ORIGINAL_PROFILE_LABEL)
        self._update_action_controls()

    def _test_connection(self):
        mode = self.mode.get()
        client = self._refresh_client().get("client")
        if client not in core.SUPPORTED_PORTS:
            self._log("Select a supported game folder before testing.")
            return False
        try:
            host, port = core.endpoint_for_mode(mode, self.join_address.get())
        except core.LauncherError as error:
            self._log(str(error))
            return False
        self.test_button.config(state="disabled")
        self._log("Testing %s:%d..." % (host, port))

        def probe():
            try:
                status = core.listener_status(client, host, port)
                self._log(core.listener_report(mode, host, port, status))
            finally:
                self.root.after(
                    0, lambda: self.test_button.config(state="normal"))

        thread = threading.Thread(target=probe)
        thread.daemon = True
        thread.start()
        return True

    def _log(self, message):
        def append():
            self.log_view.config(state="normal")
            self.log_view.insert("end", message.rstrip() + "\n")
            self.log_view.see("end")
            self.log_view.config(state="disabled")

        self.root.after(0, append)

    def _set_busy(self, busy):
        self._busy = busy
        self.root.after(0, self._update_action_controls)

    def _set_maintenance_busy(self, busy):
        self._maintenance_busy = busy
        self.root.after(0, self._update_action_controls)

    def _kill_game(self):
        """Close a game that did not exit on its own."""
        self._log("Closing every %s process..." % core.GAME_EXECUTABLE)
        game = self._game
        if game is not None and game.poll() is None:
            try:
                game.kill()
            except Exception as error:
                self._log("Could not close the started process: %s" % error)
        core.kill_game()
        return True

    def _save_settings(self):
        try:
            team_size = core.parse_team_size(self.team_size.get())
        except core.LauncherError:
            team_size = core.DEFAULT_TEAM_SIZE
        core.save_settings({
            "game_root": self.game_root.get().strip(),
            "folders": list(self._folders),
            "mode": self.mode.get(),
            "join_address": self.join_address.get().strip(),
            "name": self.player_name.get().strip(),
            "team_size": team_size,
            "vehicle_profile": self.vehicle_profile.get().strip(),
        })

    def _start_maintenance(self, action):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        if status.get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        self._remember_folder()
        self._set_maintenance_busy(True)

        def run():
            try:
                for message in action(status["path"]):
                    self._log(message)
            except core.LauncherError as error:
                self._log(str(error))
            except Exception as error:
                self._log("Launcher maintenance failed: %s" % error)
            finally:
                self._set_maintenance_busy(False)
                self.root.after(0, self._refresh_client)

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return True

    def _repair_startup(self):
        return self._start_maintenance(core.repair_0_9_22_startup)

    @staticmethod
    def _ask_profile_name():
        from tkinter import simpledialog

        return simpledialog.askstring(
            "New vehicle profile",
            "Profile name:")

    @staticmethod
    def _confirm_delete_profile(profile_name):
        from tkinter import messagebox

        return messagebox.askyesno(
            "Delete vehicle profile?",
            "Delete profile '%s' and all of its saved vehicle edits?" %
            profile_name,
            icon="warning")

    def _new_vehicle_profile(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        if (status.get("client") != core.PORT_0_9_22 or
                self.mode.get() != core.MODE_SINGLE):
            self._log(
                "Vehicle profiles are available for 0.9.22 single player.")
            return False
        raw_name = self._ask_profile_name()
        if raw_name is None:
            return False
        try:
            profile_name = vehicle_overlays.create_vehicle_profile(
                status["path"], raw_name)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log("Could not create the vehicle profile: %s" % error)
            return False
        self._refresh_profiles(status)
        self.vehicle_profile.set(profile_name)
        self._update_action_controls()
        self._save_settings()
        self._log("Created vehicle profile '%s'." % profile_name)
        return self._open_vehicle_editor()

    def _open_vehicle_editor(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        if status.get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        profile_name = self.vehicle_profile.get().strip()
        if profile_name not in self._profile_names:
            self._log("Create or select a vehicle profile before editing.")
            return False
        self._remember_folder()
        vehicle_editor_ui.open_vehicle_editor(
            self.root, status["path"], profile_name, log=self._log)
        return True

    def _delete_vehicle_profile(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        profile_name = self.vehicle_profile.get().strip()
        if (status.get("client") != core.PORT_0_9_22 or
                profile_name not in self._profile_names):
            self._log("Select a saved vehicle profile before deleting it.")
            return False
        if not self._confirm_delete_profile(profile_name):
            self._log("Vehicle profile deletion was cancelled.")
            return False
        try:
            vehicle_overlays.delete_vehicle_profile(
                status["path"], profile_name)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log("Could not delete the vehicle profile: %s" % error)
            return False
        self.vehicle_profile.set(vehicle_overlays.ORIGINAL_PROFILE_LABEL)
        self._refresh_profiles(status)
        self._update_action_controls()
        self._save_settings()
        self._log("Deleted vehicle profile '%s'." % profile_name)
        return True

    @staticmethod
    def _confirm_reset():
        from tkinter import messagebox

        return messagebox.askyesno(
            "Reset all offline data?",
            "This deletes this mod's saved address, account settings, garage "
            "fittings, post-battle results, configuration, and isolated client "
            "graphics/input preferences. Other mods and the normal World of "
            "Tanks profile are kept. Continue?",
            icon="warning")

    def _reset_all_state(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        if self._refresh_client().get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        if core.game_is_running():
            self._log(
                "Close World of Tanks before repairing or resetting offline data.")
            return False
        if not self._confirm_reset():
            self._log("Offline data reset was cancelled.")
            return False
        return self._start_maintenance(core.reset_0_9_22_state)

    def _start(self):
        if self._maintenance_busy:
            self._log("Wait for launcher maintenance to finish.")
            return
        if self._busy:
            self._kill_game()
            return
        status = self._refresh_client()
        selected_profile = self.vehicle_profile.get().strip()
        profile_name = (
            selected_profile if selected_profile in self._profile_names
            else None)
        try:
            session = core.plan_session(status, self.mode.get(),
                                        self.join_address.get(),
                                        self.team_size.get(), profile_name)
        except core.LauncherError as error:
            self._log(str(error))
            return
        self._remember_folder()
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
            self._log("Installing the %s mod into %s..." %
                      (session["client"], game_root))
            for action in core.install_client_mod(game_root,
                                                  session["client"]):
                self._log(action)
            if session["client"] == core.PORT_0_9_22:
                prepared = vehicle_overlays.prepare_vehicle_profile(
                    game_root, session.get("vehicle_profile"))
                if prepared["profile"] is None:
                    self._log(
                        "No launcher-owned vehicle profile is active; other "
                        "installed mods are unchanged.")
                else:
                    self._log(
                        "Activated single-player vehicle profile '%s' "
                        "(%d package member%s)." % (
                            prepared["profile"],
                            prepared["installedMembers"],
                            "" if prepared["installedMembers"] == 1 else "s"))
                self._log(core.ensure_0_9_22_preferences_isolation(game_root))
            for path in core.write_settings(game_root, session["client"],
                                            session["mode"], host, port, name):
                self._log("Wrote %s" % path)
            if session["needs_server"]:
                if not self._start_server(
                        game_root, session["client"], session["team_size"]):
                    return
            elif session["mode"] == core.MODE_JOIN:
                status = core.listener_status(
                    session["client"], host, port)
                if status == core.LISTENER_COMPATIBLE:
                    self._log("The compatible server at %s:%d answered." %
                              (host, port))
                elif status == core.LISTENER_OCCUPIED:
                    self._log("Something at %s:%d answered, but it is not "
                              "the server for this client. The game was not "
                              "started." % (host, port))
                    return
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
            if session.get("client") == core.PORT_0_9_22:
                try:
                    removed = vehicle_overlays.ensure_original_vehicle_data(
                        game_root)
                    if removed:
                        self._log(
                            "Removed the temporary vehicle profile; original "
                            "vehicle data is active again.")
                except vehicle_overlays.VehicleOverlayError as error:
                    self._log(
                        "Could not restore original vehicle data: %s" % error)
            self._set_busy(False)

    def _start_server(self, game_root, port_version,
                      team_size=core.DEFAULT_TEAM_SIZE):
        status = core.listener_status(
            port_version, core.LOCAL_HOST, core.DEFAULT_SERVER_PORT)
        if status == core.LISTENER_COMPATIBLE:
            self._log("A compatible %s LAN server is already running; "
                      "using it." % port_version)
            return True
        if status == core.LISTENER_OCCUPIED:
            self._log("Another program uses port %d and does not speak the "
                      "%s LAN protocol. Close it before starting the game." %
                      (core.DEFAULT_SERVER_PORT, port_version))
            return False
        command = core.server_child_command(port_version)
        environment = core.server_environment(
            port_version, game_root, team_size=team_size)
        self._log("Starting the %s LAN server..." % port_version)
        self._server = subprocess.Popen(
            command, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=_no_console_flags())
        pump = threading.Thread(target=self._pump_server_output)
        pump.daemon = True
        pump.start()
        if not core.wait_for_server(
                port_version, core.LOCAL_HOST, core.DEFAULT_SERVER_PORT):
            self._log("The LAN server did not answer the %s protocol on port "
                      "%d." % (port_version, core.DEFAULT_SERVER_PORT))
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
        self._log("Waiting %d seconds in case the game restarts itself..." %
                  int(core.GAME_RESTART_GRACE_SECONDS))
        core.wait_for_game_exit(
            core.game_is_running,
            on_restart=lambda: self._log(
                "The game started another process; the server stays up."))
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
        if self._busy or self._maintenance_busy:
            self._log(
                "Finish the current launcher operation before closing. Use "
                "Kill the game if a started client must be closed now.")
            return False
        self._save_settings()
        self._stop_server()
        self.root.destroy()
        return True

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
    from tkinter import filedialog, ttk

    LauncherWindow(tkinter, ttk, filedialog).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
