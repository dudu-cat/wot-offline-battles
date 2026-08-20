"""Callback tests for the advanced vehicle editor window."""

import unittest

import vehicle_editor_ui


class _Widget(object):
    def __init__(self, master=None, **options):
        self.options = dict(options)
        self.children = []
        if master is not None and hasattr(master, "children"):
            master.children.append(self)

    def pack(self, **unused):
        pass

    def grid(self, **unused):
        pass

    def grid_columnconfigure(self, *unused, **unused_options):
        pass

    def bind(self, event, callback):
        self.options.setdefault("bindings", {})[event] = callback

    def config(self, **options):
        self.options.update(options)

    def cget(self, name):
        return self.options.get(name)


class _Root(_Widget):
    def title(self, unused_title):
        pass


class _StringVar(object):
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeTk(object):
    Toplevel = _Root
    Frame = _Widget
    Label = _Widget
    Entry = _Widget
    Button = _Widget
    StringVar = _StringVar


class _FakeTtk(object):
    Combobox = _Widget


class _MessageBox(object):
    def __init__(self, answer=True):
        self.answer = answer
        self.calls = []

    def askyesno(self, *args, **options):
        self.calls.append((args, options))
        return self.answer


class _Service(object):
    class VehicleOverlayError(Exception):
        pass

    def __init__(self):
        self.inspect_calls = []
        self.catalog_calls = []
        self.topology_calls = []
        self.apply_calls = []
        self.restore_calls = []
        self.inspect_error = None
        self.apply_error = None
        self.restore_error = None
        self.conflict = ""
        self.current = "32"
        self.choices = [
            {"nation": "ussr", "vehicle": "R11_MS-1",
             "member": vehicle_editor_ui.DEFAULT_MEMBER},
            {"nation": "ussr", "vehicle": "R12_Test",
             "member": "scripts/item_defs/vehicles/ussr/R12_Test.xml"},
            {"nation": "usa", "vehicle": "A01_T1_Cunningham",
             "member": (
                 "scripts/item_defs/vehicles/usa/A01_T1_Cunningham.xml")},
        ]

    def list_vehicle_choices(self, game_root):
        self.catalog_calls.append(game_root)
        return list(self.choices)

    def list_vehicle_field_choices(self, game_root, member):
        self.topology_calls.append((game_root, member))
        if member.endswith("R12_Test.xml"):
            return [self._field(
                member, "speedLimits/forward", "Vehicle",
                "Speed limits / Forward speed", False, ("R12_Test",))]
        return [
            self._field(
                member, vehicle_editor_ui.DEFAULT_FIELD, "Vehicle",
                "Speed limits / Forward speed", False, ("R11_MS-1",)),
            self._field(
                "scripts/item_defs/vehicles/ussr/components/guns.xml",
                "shared/Gun-A/reloadTime", "Gun",
                "Gun-A / Reload time", True,
                ("R11_MS-1", "R12_Test"), original="2.5"),
        ]

    @staticmethod
    def _field(member, field_path, category, field_label, shared, affected,
               original="32"):
        scope = ("Shared component; affects %s" % ", ".join(affected)
                 if shared else "Affects this vehicle only.")
        return {
                "member": member,
                "fieldPath": field_path,
                "categoryLabel": category,
                "fieldLabel": field_label,
                "scope": scope,
                "shared": shared,
                "affectedVehicles": affected,
                "originalValue": original,
                "packedType": (
                    "string" if field_path.endswith("reloadTime")
                    else "integer"),
                "constraint": "positive",
            }

    def _result(self, member, field_path):
        return {
            "member": member,
            "fieldPath": field_path,
            "originalValue": "32",
            "currentValue": self.current,
            "packedType": "integer",
            "constraint": "stock parser requires a positive number",
            "overlayPath": "C:/WoT/res_mods/0.9.22.0.1/" + member,
            "conflict": self.conflict,
        }

    def inspect_vehicle_field(self, game_root, member, field_path):
        self.inspect_calls.append((game_root, member, field_path))
        if self.inspect_error:
            raise self.VehicleOverlayError(self.inspect_error)
        return self._result(member, field_path)

    def apply_vehicle_edit(self, game_root, member, field_path, replacement):
        self.apply_calls.append(
            (game_root, member, field_path, replacement))
        if self.apply_error:
            raise self.VehicleOverlayError(self.apply_error)
        self.current = replacement
        return self._result(member, field_path)

    def restore_vehicle_defaults(self, game_root):
        self.restore_calls.append(game_root)
        if self.restore_error:
            raise self.VehicleOverlayError(self.restore_error)
        self.current = "32"
        return 2


class VehicleEditorWindowTest(unittest.TestCase):
    def setUp(self):
        self.parent = _Root()
        self.service = _Service()
        self.messagebox = _MessageBox()
        self.log = []
        self.window = vehicle_editor_ui.VehicleEditorWindow(
            self.parent, "C:/WoT", _FakeTk, _FakeTtk, self.messagebox,
            log=self.log.append, service=self.service)

    def test_opening_inspects_and_shows_the_original_contract(self):
        self.assertEqual(["C:/WoT"], self.service.catalog_calls)
        self.assertEqual(
            [("C:/WoT", vehicle_editor_ui.DEFAULT_MEMBER)],
            self.service.topology_calls)
        self.assertEqual(
            [("C:/WoT", vehicle_editor_ui.DEFAULT_MEMBER,
              vehicle_editor_ui.DEFAULT_FIELD)],
            self.service.inspect_calls)
        self.assertEqual("32", self.window.original.get())
        self.assertEqual("32", self.window.replacement.get())
        self.assertEqual("integer", self.window.packed_type.get())
        self.assertIn("positive", self.window.constraint.get())
        self.assertIn("res_mods/0.9.22.0.1",
                      self.window.overlay_path.get())
        self.assertEqual(("usa", "ussr"),
                         self.window.nation_box.cget("values"))
        self.assertEqual(("R11_MS-1", "R12_Test"),
                         self.window.vehicle_box.cget("values"))
        self.assertEqual(("Vehicle", "Gun"),
                         self.window.category_box.cget("values"))
        self.assertEqual(
            ("Speed limits / Forward speed",),
            self.window.field_box.cget("values"))

    def test_selecting_a_category_loads_its_discovered_safe_fields(self):
        self.window.category.set("Gun")

        self.assertTrue(self.window.refresh_fields())

        self.assertEqual("Gun-A / Reload time", self.window.field.get())
        self.assertEqual(
            ("Gun-A / Reload time",),
            self.window.field_box.cget("values"))
        self.assertIn("R11_MS-1, R12_Test", self.window.scope.get())
        self.assertEqual(
            "scripts/item_defs/vehicles/ussr/components/guns.xml",
            self.window.member.get())
        self.assertEqual("shared/Gun-A/reloadTime",
                         self.window.field_path.get())

    def test_selecting_a_nation_filters_the_vehicle_list(self):
        self.window.nation.set("usa")

        self.assertTrue(self.window.refresh_vehicles())

        self.assertEqual(("A01_T1_Cunningham",),
                         self.window.vehicle_box.cget("values"))
        self.assertEqual("A01_T1_Cunningham", self.window.vehicle.get())

    def test_inspect_disables_apply_and_shows_a_conflict(self):
        self.service.conflict = (
            "Conflict: another tool owns this complete member.")

        self.assertTrue(self.window.inspect())

        self.assertEqual("disabled", self.window.apply_button.cget("state"))
        self.assertEqual(self.service.conflict, self.window.status.get())

    def test_invalid_field_shows_the_validation_error(self):
        self.service.inspect_error = "This field is not in the allowlist."

        self.assertFalse(self.window.inspect())

        self.assertIn("Validation error", self.window.status.get())
        self.assertEqual("disabled", self.window.apply_button.cget("state"))

    def test_apply_passes_the_visible_selection_and_reports_success(self):
        self.window.replacement.set("40")

        self.assertTrue(self.window.apply())

        self.assertEqual(
            [("C:/WoT", vehicle_editor_ui.DEFAULT_MEMBER,
              vehicle_editor_ui.DEFAULT_FIELD, "40")],
            self.service.apply_calls)
        self.assertEqual("40", self.window.current.get())
        self.assertIn("reparsed successfully", self.window.status.get())
        self.assertIn("reparsed successfully", self.log[-1])

    def test_apply_reports_the_game_running_refusal(self):
        self.service.apply_error = (
            "Close World of Tanks before changing vehicle data.")

        self.assertFalse(self.window.apply())

        self.assertIn("Close World of Tanks", self.window.status.get())

    def test_restore_requires_confirmation(self):
        self.messagebox.answer = False

        self.assertFalse(self.window.restore_defaults())

        self.assertEqual([], self.service.restore_calls)
        self.assertIn("cancelled", self.window.status.get())

    def test_restore_removes_only_owned_members_and_reinspects(self):
        self.service.current = "40"

        self.assertTrue(self.window.restore_defaults())

        self.assertEqual(["C:/WoT"], self.service.restore_calls)
        self.assertEqual("32", self.window.current.get())
        self.assertIn("Other mods were kept", self.log[-1])
        self.assertEqual(2, len(self.service.inspect_calls))


if __name__ == "__main__":
    unittest.main()
