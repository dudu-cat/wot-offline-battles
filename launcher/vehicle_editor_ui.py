"""Tk window for the safe 0.9.22 vehicle-data overlay editor."""

from __future__ import annotations

try:
    from . import vehicle_overlays
except ImportError:
    import vehicle_overlays


DEFAULT_MEMBER = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml"
DEFAULT_FIELD = "speedLimits/forward"
DEFAULT_NATION = "ussr"
DEFAULT_VEHICLE = "R11_MS-1"


class VehicleEditorWindow(object):
    """Small advanced editor backed by the strict overlay service."""

    def __init__(self, parent, game_root, tk_module, ttk_module,
                 messagebox_module, log=None, service=vehicle_overlays):
        self._tk = tk_module
        self._ttk = ttk_module
        self._messagebox = messagebox_module
        self._service = service
        self._game_root = game_root
        self._log = log or (lambda unused_message: None)
        self._vehicle_choices = []
        self._fields = []
        self._field_by_label = {}
        self._build(parent)
        self.refresh_catalog()

    def _build(self, parent):
        tk = self._tk
        self.root = tk.Toplevel(parent)
        self.root.title("0.9.22 vehicle data editor")

        frame = tk.Frame(self.root, padx=12, pady=12)
        frame.pack(fill="both", expand=True)

        explanation = (
            "Choose a nation and vehicle, then a category and field. The "
            "launcher resolves those choices through the original 0.9.22 "
            "vehicle topology and writes a safe overlay under "
            "res_mods/0.9.22.0.1. Shared guns, engines and other components "
            "show every vehicle they affect. IDs, resource paths, topology "
            "and unknown fields remain locked.")
        tk.Label(frame, text=explanation, justify="left", anchor="w",
                 wraplength=720).grid(
                     row=0, column=0, columnspan=3, sticky="we",
                     pady=(0, 10))

        self.nation = tk.StringVar(value=DEFAULT_NATION)
        self.vehicle = tk.StringVar(value=DEFAULT_VEHICLE)
        self.category = tk.StringVar(value="Vehicle")
        self.field = tk.StringVar(value="")
        self.member = tk.StringVar(value=DEFAULT_MEMBER)
        self.field_path = tk.StringVar(value=DEFAULT_FIELD)
        self.replacement = tk.StringVar(value="")
        self.original = tk.StringVar(value="-")
        self.current = tk.StringVar(value="-")
        self.packed_type = tk.StringVar(value="-")
        self.constraint = tk.StringVar(value="-")
        self.scope = tk.StringVar(value="-")
        self.source = tk.StringVar(value="-")
        self.overlay_path = tk.StringVar(value="-")
        self.status = tk.StringVar(value="Choose a vehicle field.")

        row = 1
        row, self.nation_box = self._selector_row(
            frame, row, "Nation", self.nation,
            "Only nations found in the original vehicle definitions.")
        row, self.vehicle_box = self._selector_row(
            frame, row, "Vehicle", self.vehicle,
            "Vehicle code from the selected nation's original data.")
        row, self.category_box = self._selector_row(
            frame, row, "Category", self.category,
            "Only categories with an existing safe field are shown.")
        row, self.field_box = self._selector_row(
            frame, row, "Field", self.field,
            "The exact package member and field path stay internal.")
        self.nation_box.bind("<<ComboboxSelected>>", self.refresh_vehicles)
        self.vehicle_box.bind(
            "<<ComboboxSelected>>", self.refresh_vehicle_fields)
        self.category_box.bind("<<ComboboxSelected>>", self.refresh_fields)
        self.field_box.bind("<<ComboboxSelected>>", self.inspect)

        self.inspect_button = tk.Button(
            frame, text="Inspect selected field", command=self.inspect)
        self.inspect_button.grid(row=row, column=1, sticky="w", pady=(4, 8))
        row += 1

        for label, variable in (
                ("Impact", self.scope),
                ("Original value", self.original),
                ("Current value", self.current),
                ("Packed type", self.packed_type),
                ("Constraint", self.constraint),
                ("Technical source", self.source),
                ("Overlay path", self.overlay_path)):
            tk.Label(frame, text=label, anchor="w").grid(
                row=row, column=0, sticky="nw", pady=(2, 0))
            tk.Label(frame, textvariable=variable, anchor="w",
                     justify="left", wraplength=570).grid(
                         row=row, column=1, columnspan=2, sticky="we",
                         padx=(8, 0), pady=(2, 0))
            row += 1

        tk.Label(frame, text="Replacement value", anchor="w").grid(
            row=row, column=0, sticky="w", pady=(8, 0))
        self.replacement_entry = tk.Entry(
            frame, textvariable=self.replacement, width=32)
        self.replacement_entry.grid(
            row=row, column=1, sticky="we", padx=(8, 8), pady=(8, 0))
        self.apply_button = tk.Button(
            frame, text="Apply overlay", command=self.apply)
        self.apply_button.grid(row=row, column=2, sticky="e", pady=(8, 0))
        row += 1

        self.restore_button = tk.Button(
            frame, text="Restore all vehicle defaults...",
            command=self.restore_defaults)
        self.restore_button.grid(
            row=row, column=1, columnspan=2, sticky="w", pady=(10, 0))
        row += 1

        tk.Label(frame, textvariable=self.status, anchor="w", justify="left",
                 wraplength=720).grid(
                     row=row, column=0, columnspan=3, sticky="we",
                     pady=(10, 0))

        frame.grid_columnconfigure(1, weight=1)

    def _selector_row(self, frame, row, label, variable, hint, button=None):
        tk = self._tk
        tk.Label(frame, text=label, anchor="w").grid(
            row=row, column=0, sticky="w")
        box = self._ttk.Combobox(
            frame, textvariable=variable, values=(), width=76,
            state="readonly")
        box.grid(row=row, column=1, columnspan=1 if button else 2,
                 sticky="we", padx=(8, 8 if button else 0))
        if button:
            text, command = button
            tk.Button(frame, text=text, command=command).grid(
                row=row, column=2, sticky="e")
        row += 1
        tk.Label(frame, text=hint, anchor="w").grid(
            row=row, column=1, columnspan=2, sticky="w", padx=(8, 0),
            pady=(0, 5))
        return row + 1, box

    def _selection(self):
        record = self._field_by_label.get(self.field.get().strip())
        if record is None:
            raise self._service.VehicleOverlayError(
                "Choose one listed vehicle field.")
        return (record["member"], record["fieldPath"])

    def _show_result(self, result, success_message=None):
        self.original.set(result["originalValue"])
        self.current.set(result["currentValue"])
        self.packed_type.set(result["packedType"])
        self.constraint.set(result["constraint"])
        self.overlay_path.set(result["overlayPath"])
        conflict = result.get("conflict", "")
        self.apply_button.config(
            state="disabled" if conflict.startswith("Conflict:") else "normal")
        if conflict:
            self.status.set(conflict)
        else:
            self.status.set(success_message or "Field is safe to edit.")

    def _show_error(self, error, clear_contract=False):
        message = str(error)
        self.status.set("Validation error: %s" % message)
        self.apply_button.config(state="disabled")
        if clear_contract:
            self.original.set("-")
            self.current.set("-")
            self.packed_type.set("-")
            self.constraint.set("-")
            self.scope.set("-")
            self.source.set("-")
            self.overlay_path.set("-")
        return False

    def refresh_catalog(self):
        try:
            choices = self._service.list_vehicle_choices(self._game_root)
        except self._service.VehicleOverlayError as error:
            return self._show_error(error, clear_contract=True)
        if not choices:
            return self._show_error(
                "No supported vehicles were found in scripts.pkg.",
                clear_contract=True)
        self._vehicle_choices = list(choices)
        nations = sorted(set(choice["nation"] for choice in choices))
        self.nation_box.config(values=tuple(nations))
        if self.nation.get().strip() not in nations:
            self.nation.set(
                DEFAULT_NATION if DEFAULT_NATION in nations else nations[0])
        return self.refresh_vehicles()

    def refresh_members(self):
        """Compatibility alias for callers that refresh the editor."""
        return self.refresh_catalog()

    def refresh_vehicles(self, unused_event=None):
        nation = self.nation.get().strip()
        choices = [choice for choice in self._vehicle_choices
                   if choice["nation"] == nation]
        vehicles = [choice["vehicle"] for choice in choices]
        self.vehicle_box.config(values=tuple(vehicles))
        if not vehicles:
            return self._show_error(
                "The selected nation has no supported vehicles.",
                clear_contract=True)
        if self.vehicle.get().strip() not in vehicles:
            self.vehicle.set(
                DEFAULT_VEHICLE if DEFAULT_VEHICLE in vehicles
                else vehicles[0])
        return self.refresh_vehicle_fields()

    def refresh_vehicle_fields(self, unused_event=None):
        nation = self.nation.get().strip()
        vehicle = self.vehicle.get().strip()
        choice = next((item for item in self._vehicle_choices
                       if item["nation"] == nation and
                       item["vehicle"] == vehicle), None)
        if choice is None:
            return self._show_error(
                "Choose one listed vehicle.", clear_contract=True)
        try:
            fields = self._service.list_vehicle_field_choices(
                self._game_root, choice["member"])
        except self._service.VehicleOverlayError as error:
            return self._show_error(error, clear_contract=True)
        if not fields:
            return self._show_error(
                "This vehicle has no existing fields in the safe allowlist.",
                clear_contract=True)
        self._fields = list(fields)
        categories = []
        for record in self._fields:
            label = record["categoryLabel"]
            if label not in categories:
                categories.append(label)
        self.category_box.config(values=tuple(categories))
        if self.category.get().strip() not in categories:
            self.category.set(categories[0])
        return self.refresh_fields()

    def refresh_fields(self, unused_event=None):
        category = self.category.get().strip()
        fields = [record for record in self._fields
                  if record["categoryLabel"] == category]
        if not fields:
            self.field_box.config(values=())
            return self._show_error(
                "This category has no existing fields in the safe allowlist.",
                clear_contract=True)
        labels = [record["fieldLabel"] for record in fields]
        if len(labels) != len(set(labels)):
            return self._show_error(
                "The original topology produced ambiguous field labels.",
                clear_contract=True)
        self._field_by_label = dict(
            (record["fieldLabel"], record) for record in fields)
        self.field_box.config(values=tuple(labels))
        if self.field.get().strip() not in labels:
            self.field.set(labels[0])
        return self.inspect()

    def inspect(self, unused_event=None):
        try:
            member, field_path = self._selection()
            record = self._field_by_label[self.field.get().strip()]
            result = self._service.inspect_vehicle_field(
                self._game_root, member, field_path)
        except self._service.VehicleOverlayError as error:
            return self._show_error(error, clear_contract=True)
        self.member.set(member)
        self.field_path.set(field_path)
        self.scope.set(record["scope"])
        self.source.set("%s :: %s" % (member, field_path))
        self.replacement.set(result["currentValue"])
        self._show_result(result)
        return True

    def apply(self):
        try:
            member, field_path = self._selection()
            result = self._service.apply_vehicle_edit(
                self._game_root, member, field_path, self.replacement.get())
        except self._service.VehicleOverlayError as error:
            return self._show_error(error)
        message = "Overlay applied and reparsed successfully."
        self._show_result(result, message)
        self._log("Vehicle data editor: %s" % message)
        return True

    def restore_defaults(self):
        if not self._messagebox.askyesno(
                "Restore vehicle defaults?",
                "Remove every complete package member owned by this editor? "
                "Other res_mods files are kept.",
                parent=self.root, icon="warning"):
            self.status.set("Default restoration was cancelled.")
            return False
        try:
            count = self._service.restore_vehicle_defaults(self._game_root)
        except self._service.VehicleOverlayError as error:
            return self._show_error(error)
        message = (
            "Restored defaults for %d owned package member%s. Other mods were "
            "kept." % (count, "" if count == 1 else "s"))
        self._log("Vehicle data editor: %s" % message)
        if self.inspect():
            self.status.set(message)
        return True


def open_vehicle_editor(parent, game_root, log=None):
    """Open a vehicle editor using the real Tk modules."""
    import tkinter
    from tkinter import messagebox, ttk

    return VehicleEditorWindow(
        parent, game_root, tkinter, ttk, messagebox, log=log)
