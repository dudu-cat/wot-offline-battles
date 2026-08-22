#!/usr/bin/env python3
"""Desktop launcher for the World of Tanks offline-battle client ports.

The launcher installs client payloads, manages the hidden single-player
authority, and can run a persistent LAN server explicitly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import core
    import i18n
    import vehicle_editor_ui
    import vehicle_overlays
else:
    from . import core, i18n, vehicle_editor_ui, vehicle_overlays


LAUNCHER_VERSION = "0.6.0-alpha.1"
WINDOW_TITLE = "World of Tanks Offline Battles %s" % LAUNCHER_VERSION

_CHINESE = {
    "Language": "语言",
    "Game client": "游戏客户端",
    "Game folder": "游戏目录",
    "Browse...": "浏览…",
    "Single player": "单人游戏",
    "Online": "联网游戏",
    "Player name": "玩家名",
    "Tanks per team (including players)": "每队坦克数（包含玩家）",
    "The launcher starts the private server and hidden simulation client "
    "automatically.": "启动游戏时会自动运行隐藏服务器和模拟客户端。",
    "Start single-player battle": "开始单人战斗",
    "Server address": "服务器地址",
    "Test connection": "测试连接",
    "Tanks per team on this server": "本机服务器每队坦克数",
    "Start server": "启动服务器",
    "Stop server": "关闭服务器",
    "To host: start the server, then join the game. Other players use a LAN "
    "address shown in the log.": "作为主机：先启动服务器，再加入游戏；其他玩家使用日志中显示的局域网地址。",
    "Join network battle": "加入联网战斗",
    "Vehicle modifier": "坦克属性修改器",
    "Vehicle data profile": "车辆属性方案",
    "New profile...": "新建方案…",
    "Edit selected profile...": "编辑所选方案…",
    "Delete selected profile...": "删除所选方案…",
    "Repair": "修复",
    "Repair startup (keep saved data)": "修复启动问题（保留存档）",
    "Normal client stuck loading? Clean preferences...":
        "正式客户端卡在加载界面？点击清理配置…",
    "Reset all offline data...": "重置全部离线数据…",
    "Activity log": "运行日志",
    "Close game": "关闭游戏",
    "Select the folder that contains %s.": "请选择包含 %s 的目录。",
    "%s was not found in this folder.": "此目录中没有找到 %s。",
    "This client version is not supported.": "不支持此客户端版本。",
    "World of Tanks %s found. Starting the game installs the mod.":
        "已找到 World of Tanks %s；启动游戏时会安装 Mod。",
    "World of Tanks %s ready. Starting the game updates the mod.":
        "World of Tanks %s 已准备就绪；启动游戏时会更新 Mod。",
    "Select the World of Tanks folder": "选择 World of Tanks 游戏目录",
    "New vehicle profile": "新建车辆属性方案",
    "Profile name:": "方案名称：",
    "Delete vehicle profile?": "删除车辆属性方案？",
    "Delete profile '%s' and all of its saved vehicle edits?":
        "删除方案“%s”及其中保存的全部车辆修改？",
    "Reset all offline data?": "重置全部离线数据？",
    "Clean normal client preferences?": "清理正式客户端配置？",
    "This moves the normal World of Tanks preferences.xml aside as a backup. "
    "Graphics, window, and input settings will reset the next time the normal "
    "client starts. Offline saved data is not changed. Continue?":
        "这会把正式客户端的 preferences.xml 移到备份文件。下次启动正式客户端时，画面、窗口和按键设置会恢复默认；离线 Mod 存档不会改变。是否继续？",
    "This deletes this mod's saved address, account settings, garage fittings, "
    "post-battle results, configuration, and isolated client graphics/input "
    "preferences. Other mods and the normal World of Tanks profile are kept. "
    "Continue?": "这会删除本 Mod 保存的地址、账号设置、车库配件、战后结果、配置，以及独立的客户端画面/输入偏好。其他 Mod 和正常的 World of Tanks 配置会保留。是否继续？",
}


def _no_console_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


SERVER_LOG_MAX_BYTES = 1024 * 1024
SERVER_LOG_RETAIN_BYTES = 768 * 1024


class _BoundedLogStream(object):
    """Keep the newest complete UTF-8 log lines within a fixed-size file."""

    def __init__(self, path, max_bytes=SERVER_LOG_MAX_BYTES,
                 retain_bytes=SERVER_LOG_RETAIN_BYTES):
        self._max_bytes = max(1, int(max_bytes))
        self._retain_bytes = max(
            1, min(int(retain_bytes), self._max_bytes - 1))
        # One launcher-owned server run is one diagnostic unit. Starting a
        # new run discards stale output from older builds and battles.
        self._stream = open(path, "w+b")
        self._size = 0

    @staticmethod
    def _complete_tail(payload):
        newline = payload.find(b"\n")
        if newline >= 0:
            return payload[newline + 1:]
        return b""

    def _compact(self, incoming_bytes=0):
        keep = min(
            self._retain_bytes, self._size,
            max(0, self._max_bytes - int(incoming_bytes)))
        self._stream.flush()
        self._stream.seek(max(0, self._size - keep))
        tail = self._stream.read(keep)
        if keep < self._size:
            tail = self._complete_tail(tail)
        self._stream.seek(0)
        self._stream.truncate()
        self._stream.write(tail)
        self._size = len(tail)

    def write(self, value):
        text = str(value)
        payload = text.encode("utf-8", "replace")
        if len(payload) >= self._max_bytes:
            payload = self._complete_tail(payload[-self._retain_bytes:])
        if self._size + len(payload) > self._max_bytes:
            self._compact(len(payload))
        self._stream.seek(0, os.SEEK_END)
        self._stream.write(payload)
        self._size += len(payload)
        return len(text)

    def flush(self):
        self._stream.flush()

    def close(self):
        self._stream.close()

    @property
    def closed(self):
        return self._stream.closed


class _TeeTextStream(object):
    """Mirror server output to its inherited stream and a persistent log."""

    def __init__(self, primary, log_stream, lock):
        self._primary = primary
        self._log_stream = log_stream
        self._lock = lock

    def write(self, value):
        with self._lock:
            result = None
            if self._primary is not None:
                result = self._primary.write(value)
            self._write_log(value)
            return len(value) if result is None else result

    def flush(self):
        with self._lock:
            if self._primary is not None:
                self._primary.flush()
            self._flush_log()

    def _write_log(self, value):
        try:
            if self._log_stream.closed:
                return
            self._log_stream.write(value)
        except Exception:
            self._disable_log()

    def _flush_log(self):
        try:
            if self._log_stream.closed:
                return
            self._log_stream.flush()
        except Exception:
            self._disable_log()

    def _disable_log(self):
        try:
            self._log_stream.close()
        except Exception:
            pass

    def __getattr__(self, name):
        target = self._primary or self._log_stream
        return getattr(target, name)


class LauncherWindow(object):
    def __init__(self, tk_module, ttk_module, filedialog_module):
        self._tk = tk_module
        self._ttk = ttk_module
        self._filedialog = filedialog_module
        self._server = None
        self._server_persistent = False
        self._server_context = None
        self._worker = None
        self._game = None
        self._busy = False
        self._maintenance_busy = False
        self._stop_requested = False
        self._close_pending = False
        self._selected_client = None
        self._profile_names = []
        self._build()

    def _build(self):
        tk = self._tk
        settings = core.load_settings()
        preference = settings.get("language", i18n.LANGUAGE_AUTO)
        if preference not in i18n.LANGUAGES:
            preference = i18n.LANGUAGE_ENGLISH
        self.language_preference = preference
        self.language = i18n.resolve_language(preference)

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        frame = tk.Frame(self.root, padx=12, pady=12)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame)
        header.grid(row=0, column=0, sticky="we", pady=(0, 8))
        tk.Label(
            header, text="World of Tanks Offline Battles",
            font=("TkDefaultFont", 11, "bold")).pack(side="left")
        language_controls = tk.Frame(header)
        language_controls.pack(side="right")
        self.language_label = tk.Label(language_controls, text="")
        self.language_label.pack(side="left", padx=(0, 6))
        self.language_choice = tk.StringVar(
            value=i18n.choice_for_language(self.language_preference))
        self.language_box = self._ttk.Combobox(
            language_controls, textvariable=self.language_choice,
            values=tuple(label for unused, label in i18n.LANGUAGE_CHOICES),
            state="readonly", width=13)
        self.language_box.pack(side="left")
        self.language_box.bind(
            "<<ComboboxSelected>>", self._language_selected)

        self.game_panel = tk.LabelFrame(frame, text="", padx=8, pady=8)
        self.game_panel.grid(row=1, column=0, sticky="we", pady=(0, 8))
        self.game_folder_label = tk.Label(self.game_panel, text="")
        self.game_folder_label.grid(row=0, column=0, sticky="w")
        self._folders = core.known_folders(settings)
        self.game_root = tk.StringVar(
            value=settings.get("game_root", "") or
            (self._folders[0] if self._folders else ""))
        self.folder_box = self._ttk.Combobox(
            self.game_panel, textvariable=self.game_root,
            values=list(self._folders),
            width=50)
        self.folder_box.grid(row=0, column=1, sticky="we", padx=(6, 6))
        self.browse_button = tk.Button(
            self.game_panel, text="", command=self._browse)
        self.browse_button.grid(row=0, column=2, sticky="e")
        self.game_root.trace_add("write", lambda *unused: self._refresh_client())

        self.client_label = tk.Label(self.game_panel, text="", anchor="w")
        self.client_label.grid(
            row=1, column=0, columnspan=3, sticky="we", pady=(4, 0))
        self.game_panel.grid_columnconfigure(1, weight=1)

        saved_mode = settings.get("mode", core.MODE_SINGLE)
        # Older launchers exposed Host and Join separately. Both now open the
        # Online tab; hosting is an explicit server action inside that tab.
        saved_mode = (core.MODE_SINGLE if saved_mode == core.MODE_SINGLE
                      else core.MODE_JOIN)
        self.mode = tk.StringVar(value=saved_mode)
        self.player_name = tk.StringVar(value=settings.get("name", ""))
        self.team_size = tk.StringVar(
            value=str(settings.get("team_size", core.DEFAULT_TEAM_SIZE)))
        self.join_address = tk.StringVar(
            value=settings.get(
                "join_address", "%s:%d" %
                (core.LOCAL_HOST, core.DEFAULT_SERVER_PORT)))

        self.battle_tabs = self._ttk.Notebook(frame)
        self.battle_tabs.grid(row=2, column=0, sticky="we", pady=(0, 8))
        self.single_panel = tk.Frame(self.battle_tabs, padx=10, pady=10)
        self.network_panel = tk.Frame(self.battle_tabs, padx=10, pady=10)
        self.battle_tabs.add(self.single_panel, text="")
        self.battle_tabs.add(self.network_panel, text="")
        self.battle_tabs.bind("<<NotebookTabChanged>>", self._mode_tab_changed)

        self.single_player_name_label = tk.Label(self.single_panel, text="")
        self.single_player_name_label.grid(row=0, column=0, sticky="w")
        self.single_player_name_entry = tk.Entry(
            self.single_panel, textvariable=self.player_name, width=52)
        self.single_player_name_entry.grid(
            row=0, column=1, columnspan=2, sticky="we", padx=(6, 0))
        self.single_team_size_label = tk.Label(self.single_panel, text="")
        self.single_team_size_label.grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.single_team_size_box = self._ttk.Combobox(
            self.single_panel, textvariable=self.team_size,
            values=tuple(str(value) for value in range(
                core.MIN_TEAM_SIZE, core.MAX_TEAM_SIZE + 1)), width=10)
        self.single_team_size_box.grid(
            row=1, column=1, sticky="w", padx=(6, 6), pady=(6, 0))
        self.team_size_box = self.single_team_size_box
        self.single_help_label = tk.Label(
            self.single_panel, text="", anchor="w", justify="left")
        self.single_help_label.grid(
            row=2, column=0, columnspan=3, sticky="we", pady=(8, 6))
        self.single_start_button = tk.Button(
            self.single_panel, text="", command=self._start_single,
            height=2, font=("TkDefaultFont", 10, "bold"))
        self.single_start_button.grid(
            row=3, column=0, columnspan=3, sticky="we")
        self.single_panel.grid_columnconfigure(1, weight=1)

        self.server_address_label = tk.Label(self.network_panel, text="")
        self.server_address_label.grid(row=0, column=0, sticky="w")
        self.join_entry = tk.Entry(
            self.network_panel, textvariable=self.join_address, width=40)
        self.join_entry.grid(row=0, column=1, sticky="we", padx=(6, 6))
        self.test_button = tk.Button(
            self.network_panel, text="", command=self._test_connection)
        self.test_button.grid(row=0, column=2, sticky="e")
        self.network_player_name_label = tk.Label(self.network_panel, text="")
        self.network_player_name_label.grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.network_player_name_entry = tk.Entry(
            self.network_panel, textvariable=self.player_name, width=52)
        self.network_player_name_entry.grid(
            row=1, column=1, columnspan=2, sticky="we", padx=(6, 0),
            pady=(6, 0))
        self.network_team_size_label = tk.Label(self.network_panel, text="")
        self.network_team_size_label.grid(
            row=2, column=0, sticky="w", pady=(6, 0))
        self.network_team_size_box = self._ttk.Combobox(
            self.network_panel, textvariable=self.team_size,
            values=tuple(str(value) for value in range(
                core.MIN_TEAM_SIZE, core.MAX_TEAM_SIZE + 1)), width=10)
        self.network_team_size_box.grid(
            row=2, column=1, sticky="w", padx=(6, 6), pady=(6, 0))
        self.server_button = tk.Button(
            self.network_panel, text="", command=self._toggle_lan_server)
        self.server_button.grid(
            row=3, column=0, columnspan=3, sticky="we", pady=(8, 0))
        self.network_help_label = tk.Label(
            self.network_panel, text="", anchor="w", justify="left",
            wraplength=620)
        self.network_help_label.grid(
            row=4, column=0, columnspan=3, sticky="we", pady=(8, 6))
        self.network_start_button = tk.Button(
            self.network_panel, text="", command=self._start_network,
            height=2, font=("TkDefaultFont", 10, "bold"))
        self.network_start_button.grid(
            row=5, column=0, columnspan=3, sticky="we")
        self.network_panel.grid_columnconfigure(1, weight=1)

        self.tools_tabs = self._ttk.Notebook(frame)
        self.tools_tabs.grid(row=3, column=0, sticky="we", pady=(0, 8))
        self.vehicle_panel = tk.Frame(self.tools_tabs, padx=10, pady=10)
        self.repair_panel = tk.Frame(self.tools_tabs, padx=10, pady=10)
        self.tools_tabs.add(self.vehicle_panel, text="")
        self.tools_tabs.add(self.repair_panel, text="")

        self.vehicle_profile_label = tk.Label(self.vehicle_panel, text="")
        self.vehicle_profile_label.grid(row=0, column=0, sticky="w")
        self.vehicle_profile = tk.StringVar(
            value=settings.get(
                "vehicle_profile", vehicle_overlays.ORIGINAL_PROFILE_LABEL))
        self.vehicle_profile_box = self._ttk.Combobox(
            self.vehicle_panel, textvariable=self.vehicle_profile,
            values=(vehicle_overlays.ORIGINAL_PROFILE_LABEL,),
            state="disabled", width=40)
        self.vehicle_profile_box.grid(
            row=0, column=1, sticky="we", padx=(6, 0))
        self.vehicle_profile_box.bind(
            "<<ComboboxSelected>>", self._profile_selected)

        profile_actions = tk.Frame(self.vehicle_panel)
        profile_actions.grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self.new_profile_button = tk.Button(
            profile_actions, text="",
            command=self._new_vehicle_profile)
        self.new_profile_button.pack(side="left", fill="x", expand=True)
        self.vehicle_editor_button = tk.Button(
            profile_actions, text="",
            command=self._open_vehicle_editor)
        self.vehicle_editor_button.pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.delete_profile_button = tk.Button(
            profile_actions, text="",
            command=self._delete_vehicle_profile)
        self.delete_profile_button.pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.vehicle_panel.grid_columnconfigure(1, weight=1)

        self.repair_button = tk.Button(
            self.repair_panel, text="",
            command=self._repair_startup)
        self.repair_button.grid(row=0, column=0, sticky="we")
        self.reset_button = tk.Button(
            self.repair_panel, text="",
            command=self._reset_all_state)
        self.reset_button.grid(row=0, column=1, sticky="we", padx=(6, 0))
        self.normal_preferences_button = tk.Button(
            self.repair_panel, text="",
            command=self._clean_normal_client_preferences)
        self.normal_preferences_button.grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self.repair_panel.grid_columnconfigure(0, weight=1)
        self.repair_panel.grid_columnconfigure(1, weight=1)

        self.log_panel = tk.LabelFrame(frame, text="", padx=6, pady=6)
        self.log_panel.grid(row=4, column=0, sticky="nsew")
        self.log_view = tk.Text(
            self.log_panel, height=10, width=72, state="disabled",
                                wrap="none")
        self.log_view.pack(fill="both", expand=True)
        self.author_text = tk.StringVar(value=(
            "作者：伪红学家  B站：tiancaihb  QQ群：302519768  GitHub: "
            "https://github.com/pengw0048/wot-offline-battles"))
        self.author_entry = tk.Entry(
            frame, textvariable=self.author_text, state="readonly",
            relief="flat", borderwidth=0, highlightthickness=0)
        self.author_entry.grid(row=5, column=0, sticky="we", pady=(8, 0))
        self.distribution_notice_text = tk.StringVar(value=(
            "本mod免费传播、开源、欢迎二创，使用无需付费，售卖与本人无关，"
            "仅供个人学习交流"))
        self.distribution_notice_entry = tk.Entry(
            frame, textvariable=self.distribution_notice_text,
            state="readonly", relief="flat", borderwidth=0,
            highlightthickness=0)
        self.distribution_notice_entry.grid(
            row=6, column=0, sticky="we", pady=(2, 0))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(4, weight=1)

        self.start_button = (self.single_start_button
                             if self.mode.get() == core.MODE_SINGLE
                             else self.network_start_button)
        self._sync_mode_tab()
        self._apply_language(refresh=False)

        self._refresh_client()
        self._refresh_mode()
        self._recover_stale_vehicle_profile()

    def _t(self, text):
        if self.language == i18n.LANGUAGE_CHINESE:
            return _CHINESE.get(text, text)
        return text

    def _apply_language(self, refresh=True):
        self.language_label.config(text=self._t("Language"))
        self.game_panel.config(text=self._t("Game client"))
        self.game_folder_label.config(text=self._t("Game folder"))
        self.browse_button.config(text=self._t("Browse..."))
        self.battle_tabs.tab(
            self.single_panel, text=self._t("Single player"))
        self.battle_tabs.tab(self.network_panel, text=self._t("Online"))
        self.single_player_name_label.config(text=self._t("Player name"))
        self.network_player_name_label.config(text=self._t("Player name"))
        self.single_team_size_label.config(
            text=self._t("Tanks per team (including players)"))
        self.network_team_size_label.config(
            text=self._t("Tanks per team on this server"))
        self.single_help_label.config(text=self._t(
            "The launcher starts the private server and hidden simulation "
            "client automatically."))
        self.server_address_label.config(text=self._t("Server address"))
        self.test_button.config(text=self._t("Test connection"))
        self.network_help_label.config(text=self._t(
            "To host: start the server, then join the game. Other players use "
            "a LAN address shown in the log."))
        self.tools_tabs.tab(
            self.vehicle_panel, text=self._t("Vehicle modifier"))
        self.tools_tabs.tab(self.repair_panel, text=self._t("Repair"))
        self.vehicle_profile_label.config(text=self._t("Vehicle data profile"))
        self.new_profile_button.config(text=self._t("New profile..."))
        self.vehicle_editor_button.config(
            text=self._t("Edit selected profile..."))
        self.delete_profile_button.config(
            text=self._t("Delete selected profile..."))
        self.repair_button.config(
            text=self._t("Repair startup (keep saved data)"))
        self.normal_preferences_button.config(text=self._t(
            "Normal client stuck loading? Clean preferences..."))
        self.reset_button.config(text=self._t("Reset all offline data..."))
        self.log_panel.config(text=self._t("Activity log"))
        self._update_action_controls()
        if refresh:
            self._refresh_client()

    def _language_selected(self, unused_event=None):
        self.language_preference = i18n.language_for_choice(
            self.language_choice.get())
        self.language = i18n.resolve_language(self.language_preference)
        self._apply_language()
        self._save_settings()

    def _sync_mode_tab(self):
        panel = (self.single_panel if self.mode.get() == core.MODE_SINGLE
                 else self.network_panel)
        self.battle_tabs.select(panel)
        self.start_button = (self.single_start_button
                             if self.mode.get() == core.MODE_SINGLE
                             else self.network_start_button)

    def _mode_tab_changed(self, unused_event=None):
        try:
            index = self.battle_tabs.index("current")
        except Exception:
            return
        self.mode.set(core.MODE_SINGLE if index == 0 else core.MODE_JOIN)
        self._refresh_mode(sync_tab=False)

    def _start_single(self):
        self.mode.set(core.MODE_SINGLE)
        self._refresh_mode()
        return self._start()

    def _start_network(self):
        self.mode.set(core.MODE_JOIN)
        self._refresh_mode()
        return self._start()

    def _browse(self):
        selected = self._filedialog.askdirectory(
            title=self._t("Select the World of Tanks folder"),
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
            text = self._t("Select the folder that contains %s.") % (
                core.GAME_EXECUTABLE,)
        elif not status["has_executable"]:
            text = self._t("%s was not found in this folder.") % (
                core.GAME_EXECUTABLE,)
        elif status["client"] is None:
            text = self._t("This client version is not supported.")
        elif not status["mod_installed"]:
            text = self._t(
                "World of Tanks %s found. Starting the game installs the mod."
            ) % (status["version"] or status["client"])
        else:
            text = self._t(
                "World of Tanks %s ready. Starting the game updates the mod."
            ) % (status["version"] or status["client"])
        self.client_label.config(text=text)
        self._selected_client = status["client"]
        self._refresh_profiles(status)
        self._update_action_controls()
        if hasattr(self, "team_size_box"):
            self._refresh_mode()
        return status

    def _server_is_running(self):
        return self._server is not None and self._server.poll() is None

    def _update_action_controls(self):
        server_running = self._server_is_running()
        self.start_button = (self.single_start_button
                             if self.mode.get() == core.MODE_SINGLE
                             else self.network_start_button)
        self.single_start_button.config(
            text=self._t("Start single-player battle"))
        self.network_start_button.config(text=self._t("Join network battle"))
        if self._busy:
            inactive = (self.network_start_button
                        if self.start_button is self.single_start_button
                        else self.single_start_button)
            inactive.config(state="disabled")
            self.start_button.config(
                state="normal", text=self._t("Close game"))
        elif self._maintenance_busy:
            self.single_start_button.config(state="disabled")
            self.network_start_button.config(state="disabled")
        else:
            self.single_start_button.config(state="normal")
            self.network_start_button.config(state="normal")
        if server_running:
            server_state = (
                "normal" if not self._busy and not self._maintenance_busy
                else "disabled")
            self.server_button.config(
                state=server_state, text=self._t("Stop server"))
        else:
            server_state = (
                "normal" if self._selected_client in core.SUPPORTED_PORTS and
                self.mode.get() == core.MODE_JOIN and not self._busy and
                not self._maintenance_busy else "disabled")
            self.server_button.config(
                state=server_state, text=self._t("Start server"))
        maintenance_state = (
            "normal" if self._selected_client == core.PORT_0_9_22 and
            not self._busy and not self._maintenance_busy and
            not server_running else "disabled")
        self.repair_button.config(state=maintenance_state)
        self.normal_preferences_button.config(state=maintenance_state)
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
        try:
            manifest_exists = os.path.lexists(
                vehicle_overlays.manifest_path(game_root))
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

    def _refresh_mode(self, sync_tab=True):
        if self.mode.get() not in (core.MODE_SINGLE, core.MODE_JOIN):
            self.mode.set(core.MODE_JOIN)
        if sync_tab:
            self._sync_mode_tab()
        network = self.mode.get() == core.MODE_JOIN
        network_state = (
            "normal" if network and not self._busy and
            not self._maintenance_busy else "disabled")
        self.join_entry.config(state=network_state)
        self.test_button.config(state=network_state)
        controls_available = (
            self._selected_client == core.PORT_0_9_22 and not self._busy and
            not self._maintenance_busy and not self._server_is_running())
        self.single_team_size_box.config(
            state="readonly" if controls_available and not network
            else "disabled")
        self.network_team_size_box.config(
            state="readonly" if controls_available and network
            else "disabled")
        self._update_action_controls()

    def _test_connection(self):
        mode = core.MODE_JOIN
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

    def _kill_game(self, stop_persistent_server=False):
        """Close a game that did not exit on its own."""
        self._stop_requested = True
        self._log("Closing every %s process..." % core.GAME_EXECUTABLE)
        game = self._game
        if game is not None and game.poll() is None:
            try:
                game.kill()
            except Exception as error:
                self._log("Could not close the started process: %s" % error)
        core.kill_game()
        self._stop_worker()
        self._stop_server(force=stop_persistent_server)
        return True

    def _save_settings(self):
        try:
            team_size = core.parse_team_size(self.team_size.get())
        except core.LauncherError:
            team_size = core.DEFAULT_TEAM_SIZE
        core.save_settings({
            "game_root": self.game_root.get().strip(),
            "folders": list(self._folders),
            "mode": (core.MODE_SINGLE
                     if self.mode.get() == core.MODE_SINGLE
                     else core.MODE_JOIN),
            "join_address": self.join_address.get().strip(),
            "name": self.player_name.get().strip(),
            "team_size": team_size,
            "vehicle_profile": self.vehicle_profile.get().strip(),
            "language": self.language_preference,
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

    def _confirm_normal_preferences_cleanup(self):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Clean normal client preferences?"),
            self._t(
                "This moves the normal World of Tanks preferences.xml aside "
                "as a backup. Graphics, window, and input settings will reset "
                "the next time the normal client starts. Offline saved data "
                "is not changed. Continue?"),
            icon="warning")

    def _clean_normal_client_preferences(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        if self._refresh_client().get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        if core.game_is_running():
            self._log(
                "Close World of Tanks before cleaning normal client "
                "preferences.")
            return False
        if not self._confirm_normal_preferences_cleanup():
            self._log("Normal client preferences cleanup was cancelled.")
            return False
        return self._start_maintenance(core.backup_normal_client_preferences)

    def _ask_profile_name(self):
        from tkinter import simpledialog

        return simpledialog.askstring(
            self._t("New vehicle profile"),
            self._t("Profile name:"))

    def _confirm_delete_profile(self, profile_name):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Delete vehicle profile?"),
            self._t(
                "Delete profile '%s' and all of its saved vehicle edits?"
            ) % profile_name,
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
            self.root, status["path"], profile_name, log=self._log,
            language=self.language)
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

    def _confirm_reset(self):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Reset all offline data?"),
            self._t(
                "This deletes this mod's saved address, account settings, "
                "garage fittings, post-battle results, configuration, and "
                "isolated client graphics/input preferences. Other mods and "
                "the normal World of Tanks profile are kept. Continue?"),
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

    def _toggle_lan_server(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        if self._server_is_running():
            self._stop_server(force=True)
            self._update_action_controls()
            return True
        if self._server is not None:
            self._stop_server(force=True)
        status = self._refresh_client()
        if (status.get("client") not in core.SUPPORTED_PORTS or
                self.mode.get() != core.MODE_JOIN):
            self._log(
                "Select Online and a supported game folder first.")
            return False
        try:
            team_size = (core.parse_team_size(self.team_size.get())
                         if status["client"] == core.PORT_0_9_22
                         else core.DEFAULT_TEAM_SIZE)
        except core.LauncherError as error:
            self._log(str(error))
            return False
        self._remember_folder()
        self._save_settings()
        self._set_maintenance_busy(True)

        def run():
            try:
                self._log("Installing the %s server data into %s..." %
                          (status["client"], status["path"]))
                for action in core.install_client_mod(
                        status["path"], status["client"]):
                    self._log(action)
                if self._start_server(
                        status["path"], status["client"], team_size,
                        persistent=True):
                    self.root.after(0, self._use_local_server_address)
            except core.LauncherError as error:
                self._log(str(error))
            except Exception as error:
                self._log("The LAN server could not start: %s" % error)
            finally:
                self._set_maintenance_busy(False)

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return True

    def _use_local_server_address(self):
        self.join_address.set("%s:%d" % (
            core.LOCAL_HOST, core.DEFAULT_SERVER_PORT))
        self._save_settings()

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
            selected_profile if self.mode.get() == core.MODE_SINGLE and
            selected_profile in self._profile_names
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
        self._stop_requested = False
        self._set_busy(True)
        thread = threading.Thread(
            target=self._run_session,
            args=(status["path"], session, self.player_name.get().strip()))
        thread.daemon = True
        thread.start()

    def _run_session(self, game_root, session, name):
        host = session["host"]
        port = session["tcp_port"]
        needs_worker = (
            session["client"] == core.PORT_0_9_22 and
            session["mode"] == core.MODE_SINGLE)
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
                        game_root, session["client"], session["team_size"],
                        loopback_only=needs_worker):
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
            if self._stop_requested:
                return
            if needs_worker and not self._start_worker(
                    game_root, host, port, session["team_size"]):
                return
            if self._stop_requested:
                return
            self._run_game(
                game_root, session["client"], host, port,
                paired_worker=needs_worker)
        except core.LauncherError as error:
            self._log(str(error))
        except Exception as error:  # The window must survive any failure.
            self._log("The launcher failed: %s" % error)
        finally:
            self._stop_worker()
            self._stop_server()
            if session.get("client") == core.PORT_0_9_22:
                try:
                    if not core.wait_for_game_shutdown():
                        self._log(
                            "A World of Tanks process did not finish closing; "
                            "vehicle cleanup will retry at the next launcher "
                            "start.")
                    else:
                        removed = (
                            vehicle_overlays.ensure_original_vehicle_data(
                                game_root))
                        if removed:
                            self._log(
                                "Removed the temporary vehicle profile; "
                                "original vehicle data is active again.")
                except vehicle_overlays.VehicleOverlayError as error:
                    self._log(
                        "Could not restore original vehicle data: %s" % error)
            self._set_busy(False)
            if self._close_pending:
                self.root.after(0, self._finish_close)

    def _start_server(self, game_root, port_version,
                      team_size=core.DEFAULT_TEAM_SIZE,
                      loopback_only=False, persistent=False):
        requested_context = {
            "game_root": os.path.normcase(os.path.realpath(
                os.path.abspath(game_root))),
            "port_version": port_version,
            "loopback_only": bool(loopback_only),
            "team_size": core.parse_team_size(team_size),
        }
        if self._server_is_running():
            if self._server_context != requested_context:
                self._log(
                    "The launcher-owned LAN server uses different game, "
                    "visibility, or team settings. Stop it before starting "
                    "this session.")
                return False
            self._log("Reusing the launcher-owned %s LAN server." %
                      port_version)
            return True
        if self._server is not None:
            self._stop_server(force=True)
        status = core.listener_status(
            port_version, core.LOCAL_HOST, core.DEFAULT_SERVER_PORT)
        if status == core.LISTENER_COMPATIBLE:
            if loopback_only:
                self._log(
                    "Single player needs a fresh launcher-owned server, but "
                    "a compatible server already uses port %d. Close it "
                    "first." % core.DEFAULT_SERVER_PORT)
                return False
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
            port_version, game_root, team_size=team_size,
            loopback_only=loopback_only)
        self._log("Starting the %s LAN server..." % port_version)
        self._log("Server log: %s" % core.server_log_path())
        self._server = subprocess.Popen(
            command, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=_no_console_flags())
        self._server_persistent = bool(persistent)
        self._server_context = requested_context
        pump = threading.Thread(
            target=self._pump_server_output, args=(self._server,))
        pump.daemon = True
        pump.start()
        if not core.wait_for_server(
                port_version, core.LOCAL_HOST, core.DEFAULT_SERVER_PORT,
                cancelled=(None if persistent else
                           lambda: self._stop_requested)):
            if not self._stop_requested:
                self._log(
                    "The LAN server did not answer the %s protocol on port "
                    "%d." % (port_version, core.DEFAULT_SERVER_PORT))
            self._stop_server(force=True)
            return False
        self._log("The LAN server listens on port %d." %
                  core.DEFAULT_SERVER_PORT)
        if not loopback_only:
            for address in core.local_addresses():
                self._log("Other players join with %s:%d" %
                          (address, core.DEFAULT_SERVER_PORT))
        self.root.after(0, self._update_action_controls)
        return True

    def _pump_server_output(self, server=None):
        server = server or self._server
        if server is None or server.stdout is None:
            return
        for line in iter(server.stdout.readline, b""):
            self._log("[server] " + line.decode("utf-8", "replace").rstrip())
        if server is self._server and server.poll() not in (None, 0):
            self._log("The LAN server stopped with exit code %s." %
                      server.poll())
        self.root.after(0, self._update_action_controls)

    def _start_worker(self, game_root, host, port, team_size):
        starter = core.worker_starter_executable(game_root)
        if not os.path.isfile(starter):
            raise core.LauncherError(
                "The hidden simulation worker starter is missing: %s" %
                starter)
        previous_marker_token = core.worker_ready_marker_token(game_root)
        self._log("Starting the hidden simulation worker...")
        self._worker = subprocess.Popen(
            core.worker_child_command(game_root), cwd=game_root,
            env=core.worker_environment(
                game_root, host, port, team_size=team_size),
            creationflags=_no_console_flags())
        if core.wait_for_worker_ready(
                self._worker, game_root,
                cancelled=lambda: self._stop_requested,
                previous_marker_token=previous_marker_token):
            self._log("The hidden simulation worker is ready.")
            return True
        if not self._stop_requested:
            exit_code = self._worker.poll()
            if exit_code is None:
                self._log("The hidden simulation worker did not become ready.")
            else:
                self._log(
                    "The hidden simulation worker stopped with exit code %s." %
                    exit_code)
            self._log_worker_failure(game_root)
        self._stop_worker()
        return False

    def _log_worker_failure(self, game_root):
        try:
            with open(core.worker_failure_log(game_root), "r",
                      encoding="utf-8", errors="replace") as stream:
                detail = stream.read().strip()
        except (IOError, OSError):
            return
        if detail:
            self._log("[worker] %s" % detail.replace("\n", " | "))

    def _stop_worker(self):
        worker = self._worker
        self._worker = None
        if worker is not None and worker.poll() is None:
            self._log("Stopping the hidden simulation worker...")
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()

    def _run_game(self, game_root, port_version, host, port,
                  paired_worker=False):
        self._log("Starting %s..." % core.GAME_EXECUTABLE)
        command = core.visible_client_command(
            game_root, port_version, paired_worker=paired_worker)
        environment = core.visible_client_environment(
            port_version, host, port, paired_worker=paired_worker)
        self._game = subprocess.Popen(
            command, cwd=game_root, env=environment)
        try:
            window_closed = False
            if paired_worker:
                exit_code, window_closed = core.wait_for_paired_player_exit(
                    self._game, game_root)
            else:
                exit_code = self._game.wait()
        finally:
            self._game = None
        if (exit_code not in (None, 0) and not self._stop_requested and
                not window_closed):
            self._log("The game stopped with exit code %s." % exit_code)
        if paired_worker:
            self._log("The game closed.")
            return
        self._log("Waiting %d seconds in case the game restarts itself..." %
                  int(core.GAME_RESTART_GRACE_SECONDS))
        core.wait_for_game_exit(
            core.game_is_running,
            on_restart=lambda: self._log(
                "The game started another process; the server stays up."))
        self._log("The game closed.")

    def _stop_server(self, force=False):
        if self._server_persistent and not force:
            return False
        server = self._server
        self._server = None
        self._server_persistent = False
        self._server_context = None
        if server is not None and server.poll() is None:
            self._log("Stopping the LAN server...")
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        self.root.after(0, self._update_action_controls)
        return server is not None

    def _on_close(self):
        if self._maintenance_busy:
            self._log(
                "Finish the current launcher maintenance before closing.")
            return False
        if self._busy:
            self._close_pending = True
            self._log("Closing the game and its offline processes...")
            self._kill_game(stop_persistent_server=True)
            return False
        return self._finish_close()

    def _finish_close(self):
        if self._busy or self._maintenance_busy:
            return False
        self._save_settings()
        self._stop_worker()
        self._stop_server(force=True)
        self.root.destroy()
        return True

    def run(self):
        self.root.mainloop()


def _open_server_log():
    """Persist one bounded server run while preserving the live pipe."""
    try:
        path = core.server_log_path()
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        stream = _BoundedLogStream(path)
    except Exception as error:
        try:
            sys.stderr.write(
                "Server log is unavailable; continuing with live output: "
                "%s\n" % error)
            sys.stderr.flush()
        except Exception:
            pass
        return None
    lock = threading.RLock()
    sys.stdout = _TeeTextStream(sys.stdout, stream, lock)
    sys.stderr = _TeeTextStream(sys.stderr, stream, lock)
    return path


def _serve(argv):
    index = argv.index(core.SERVE_FLAG)
    if index + 1 >= len(argv):
        print("--serve needs a client version.")
        return 2
    port_version = argv[index + 1]
    if port_version not in core.SUPPORTED_PORTS:
        print("Unsupported client version: %s" % port_version)
        return 2
    _open_server_log()
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
