"""Compact Tk editor for one exact Bot lineup profile."""

from __future__ import annotations

try:
    from . import bot_lineup_profiles, vehicle_overlays
except ImportError:
    import bot_lineup_profiles
    import vehicle_overlays


class BotLineupEditorWindow(object):
    def __init__(self, parent, game_root, profile_name, store, on_save,
                 tk_module, ttk_module, messagebox_module, log=None):
        self._tk = tk_module
        self._ttk = ttk_module
        self._messagebox = messagebox_module
        self._game_root = game_root
        self._profile_name = profile_name
        self._store = store
        self._on_save = on_save
        self._log = log or (lambda unused: None)
        self._choices = []
        self._choices_by_nation = {}
        self._rows = {}
        self._build(parent)
        self._load_choices()

    def _build(self, parent):
        tk = self._tk
        self.root = tk.Toplevel(parent)
        self.root.title("Exact Bot lineup: %s" % self._profile_name)
        frame = tk.Frame(self.root, padx=12, pady=12)
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame,
            text=("Choose a nation and vehicle for any Bot slot. Empty slots "
                  "continue to use the waiting-room tier preset. A human in a "
                  "slot simply leaves that saved Bot choice unused."),
            justify="left", anchor="w", wraplength=820).grid(
                row=0, column=0, columnspan=6, sticky="we", pady=(0, 8))
        for column, title in ((0, "Team 1 Bot"), (1, "Nation"),
                              (2, "Vehicle"), (3, "Team 2 Bot"),
                              (4, "Nation"), (5, "Vehicle")):
            tk.Label(frame, text=title, anchor="w").grid(
                row=1, column=column, sticky="we", padx=(0, 5))
        for slot in range(15):
            self._make_slot(frame, 2 + slot, 1, slot, 0)
            self._make_slot(frame, 2 + slot, 2, slot, 3)
        self.status = tk.StringVar(value="Loading original vehicle list…")
        tk.Label(frame, textvariable=self.status, anchor="w").grid(
            row=17, column=0, columnspan=6, sticky="we", pady=(8, 0))
        tk.Button(
            frame, text="Clear all saved slots", command=self._clear_all).grid(
                row=18, column=0, columnspan=3, sticky="we", pady=(8, 0))
        tk.Button(frame, text="Close", command=self.root.destroy).grid(
            row=18, column=3, columnspan=3, sticky="we", padx=(6, 0),
            pady=(8, 0))
        for column in (2, 5):
            frame.grid_columnconfigure(column, weight=1)

    def _make_slot(self, frame, row, team, slot, column):
        tk = self._tk
        tk.Label(frame, text="%d" % (slot + 1), anchor="w").grid(
            row=row, column=column, sticky="w", padx=(0, 5))
        nation = tk.StringVar(value="")
        vehicle = tk.StringVar(value="")
        nation_box = self._ttk.Combobox(
            frame, textvariable=nation, state="readonly", width=13)
        nation_box.grid(row=row, column=column + 1, sticky="we", padx=(0, 5))
        vehicle_box = self._ttk.Combobox(
            frame, textvariable=vehicle, state="readonly", width=32)
        vehicle_box.grid(row=row, column=column + 2, sticky="we", padx=(0, 5))
        nation_box.bind(
            "<<ComboboxSelected>>",
            lambda unused, key=(team, slot): self._nation_changed(key))
        vehicle_box.bind(
            "<<ComboboxSelected>>",
            lambda unused, key=(team, slot): self._vehicle_changed(key))
        self._rows[(team, slot)] = {
            "nation": nation,
            "vehicle": vehicle,
            "nation_box": nation_box,
            "vehicle_box": vehicle_box,
        }

    def _load_choices(self):
        try:
            source_choices = vehicle_overlays.list_vehicle_choices(
                self._game_root)
            self._choices = bot_lineup_profiles.eligible_vehicle_choices(
                source_choices)
        except (vehicle_overlays.VehicleOverlayError,
                bot_lineup_profiles.BotLineupProfileError) as error:
            self.status.set(
                "Could not read the original vehicle list: %s" % error)
            return
        by_nation = {}
        for choice in self._choices:
            by_nation.setdefault(choice["nation"], []).append(choice)
        self._choices_by_nation = dict(
            (nation, sorted(values, key=lambda value: (
                value["label"].casefold(), value["type_name"])))
            for nation, values in by_nation.items())
        assignments = dict(
            ((value["team"], value["slot"]), value["vehicle"])
            for value in bot_lineup_profiles.assignments_for(
                self._store, self._profile_name))
        nations = tuple(sorted(self._choices_by_nation))
        choice_by_vehicle = dict(
            (choice["type_name"], choice) for choice in self._choices)
        for key, row in self._rows.items():
            row["nation_box"].config(values=nations)
            choice = choice_by_vehicle.get(assignments.get(key))
            if choice is not None:
                row["nation"].set(choice["nation"])
                self._set_vehicle_values(key, choice["nation"])
                row["vehicle"].set(choice["label"])
            elif nations:
                row["nation"].set(nations[0])
                self._set_vehicle_values(key, nations[0])
                row["vehicle"].set("")
        self.status.set(
            "Saved slots override the generated Bot lineup for this battle.")

    def _set_vehicle_values(self, key, nation):
        row = self._rows[key]
        row["vehicle_box"].config(values=tuple(
            choice["label"]
            for choice in self._choices_by_nation.get(nation, ())))

    def _nation_changed(self, key):
        row = self._rows[key]
        self._set_vehicle_values(key, row["nation"].get())
        row["vehicle"].set("")
        self._clear_slot(key)

    def _selected_choice(self, key):
        row = self._rows[key]
        choices = self._choices_by_nation.get(row["nation"].get(), ())
        try:
            index = int(row["vehicle_box"].current())
        except (AttributeError, TypeError, ValueError):
            index = -1
        if 0 <= index < len(choices):
            return choices[index]
        matches = [
            choice for choice in choices
            if choice["label"] == row["vehicle"].get()
        ]
        return matches[0] if len(matches) == 1 else None

    def _vehicle_changed(self, key):
        selected = self._selected_choice(key)
        if selected is None:
            return self._clear_slot(key)
        try:
            self._store = bot_lineup_profiles.set_assignment(
                self._store, self._profile_name, key[0], key[1],
                selected["type_name"])
            self._on_save(self._store)
            self.status.set("Saved Team %d Bot %d: %s" % (
                key[0], key[1] + 1, selected["label"]))
        except bot_lineup_profiles.BotLineupProfileError as error:
            self.status.set(str(error))

    def _clear_slot(self, key):
        try:
            self._store = bot_lineup_profiles.clear_assignment(
                self._store, self._profile_name, key[0], key[1])
            self._on_save(self._store)
        except bot_lineup_profiles.BotLineupProfileError as error:
            self.status.set(str(error))

    def _clear_all(self):
        try:
            for team, slot in self._rows:
                self._store = bot_lineup_profiles.clear_assignment(
                    self._store, self._profile_name, team, slot)
            self._on_save(self._store)
            for row in self._rows.values():
                row["vehicle"].set("")
            self.status.set("All saved Bot slots were cleared.")
        except bot_lineup_profiles.BotLineupProfileError as error:
            self.status.set(str(error))


def open_bot_lineup_editor(parent, game_root, profile_name, store, on_save,
                           log=None, tk_module=None, ttk_module=None,
                           messagebox_module=None):
    if tk_module is None or ttk_module is None:
        import tkinter as tk
        from tkinter import messagebox, ttk
        tk_module, ttk_module, messagebox_module = tk, ttk, messagebox
    return BotLineupEditorWindow(
        parent, game_root, profile_name, store, on_save, tk_module, ttk_module,
        messagebox_module, log=log)
