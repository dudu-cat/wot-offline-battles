import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

import core
import vehicle_overlays


packed = vehicle_overlays.packed_xml


def scalar(value_type, value):
    return packed.PackedValue(value_type, value)


def element(children):
    return packed.PackedValue(
        packed.TYPE_ELEMENT, packed.PackedElement(children=children))


def child(parent, name):
    encoded = name.encode("utf-8")
    return next(value for current, value in parent.children
                if current == encoded)


class VehicleOverlayTest(unittest.TestCase):
    LIST = "scripts/item_defs/vehicles/ussr/list.xml"
    VEHICLE = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml"
    VEHICLE_TWO = "scripts/item_defs/vehicles/ussr/R12_Test.xml"
    OBSERVER = "scripts/item_defs/vehicles/ussr/Observer.xml"
    ENGINES = "scripts/item_defs/vehicles/ussr/components/engines.xml"
    GUNS = "scripts/item_defs/vehicles/ussr/components/guns.xml"
    SHELLS = "scripts/item_defs/vehicles/ussr/components/shells.xml"

    def setUp(self):
        self.game = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.game, True)
        self._write(core.GAME_EXECUTABLE, b"")
        self._write(
            "version.xml", b"<version> v.0.9.22.0.1 #1513 </version>")
        self.members = self._members()
        self._write_package()

    def _write(self, relative_path, data):
        path = os.path.join(self.game, *relative_path.split("/"))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "wb") as stream:
            stream.write(data)
        return path

    def _write_package(self):
        path = os.path.join(
            self.game, *vehicle_overlays.SOURCE_PACKAGE.split("/"))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in sorted(self.members.items()):
                archive.writestr(name, data)
        return path

    def _members(self):
        speed_limits = packed.PackedElement(children=[
            (b"forward", scalar(packed.TYPE_INTEGER, 32)),
            (b"backward", scalar(packed.TYPE_INTEGER, 8)),
        ])
        chassis_record = packed.PackedElement(children=[
            (b"weight", scalar(packed.TYPE_INTEGER, 1200)),
            (b"maxLoad", scalar(packed.TYPE_INTEGER, 6500)),
            (b"maxHealth", scalar(packed.TYPE_INTEGER, 50)),
            (b"maxRegenHealth", scalar(packed.TYPE_INTEGER, 40)),
            (b"resource", scalar(
                packed.TYPE_STRING, b"vehicles/forbidden.model")),
        ])
        vehicle = packed.PackedElement(children=[
            (b"speedLimits", element(speed_limits.children)),
            (b"chassis", element([
                (b"T-18Bis", element(chassis_record.children)),
            ])),
            (b"turrets0", element([
                (b"T-18_mod", element([
                    (b"guns", element([
                        (b"Gun-A", element([])),
                    ])),
                ])),
            ])),
            (b"engines", element([
                (b"GAZ-M1", scalar(packed.TYPE_STRING, b"shared")),
            ])),
        ])

        engine_record = packed.PackedElement(children=[
            (b"power", scalar(packed.TYPE_INTEGER, 90)),
            (b"weight", scalar(packed.TYPE_INTEGER, 300)),
            (b"maxHealth", scalar(packed.TYPE_INTEGER, 40)),
            (b"maxRegenHealth", scalar(packed.TYPE_INTEGER, 20)),
            (b"tags", scalar(packed.TYPE_COMPRESSED_STRING, b"\x81\x01")),
        ])
        engines = packed.PackedElement(children=[
            (b"ids", element([
                (b"GAZ-M1", scalar(packed.TYPE_INTEGER, 15)),
            ])),
            (b"shared", element([
                (b"GAZ-M1", element(engine_record.children)),
            ])),
        ])

        shot = packed.PackedElement(children=[
            (b"speed", scalar(packed.TYPE_INTEGER, 825)),
            (b"gravity", scalar(packed.TYPE_STRING, b"9.81")),
            (b"maxDistance", scalar(packed.TYPE_INTEGER, 400)),
            (b"piercingPower", scalar(packed.TYPE_STRING, b"22 18")),
        ])
        gun = packed.PackedElement(children=[
            (b"rotationSpeed", scalar(packed.TYPE_STRING, b"35.5")),
            (b"reloadTime", scalar(packed.TYPE_STRING, b"2.5")),
            (b"aimingTime", scalar(packed.TYPE_STRING, b"1.9")),
            (b"weight", scalar(packed.TYPE_INTEGER, 200)),
            (b"maxAmmo", scalar(packed.TYPE_INTEGER, 45)),
            (b"shots", element([
                (b"Shell-A", element(shot.children)),
            ])),
        ])
        guns = packed.PackedElement(children=[
            (b"shared", element([
                (b"Gun-A", element(gun.children)),
            ])),
        ])

        shell_record = packed.PackedElement(children=[
            (b"id", scalar(packed.TYPE_INTEGER, 1)),
            (b"caliber", scalar(packed.TYPE_INTEGER, 20)),
            (b"damage", element([
                (b"armor", scalar(packed.TYPE_INTEGER, 10)),
                (b"devices", scalar(packed.TYPE_INTEGER, 27)),
            ])),
            (b"effects", scalar(
                packed.TYPE_COMPRESSED_STRING, b"\x81\x99\x02")),
        ])
        shells = packed.PackedElement(children=[
            (b"Shell-A", element(shell_record.children)),
        ])
        roster = packed.PackedElement(children=[
            (b"xmlns:xmlref", scalar(
                packed.TYPE_STRING, b"http://www.w3.org/2001/XInclude")),
            (b"R11_MS-1", element([
                (b"tags", scalar(packed.TYPE_STRING, b"lightTank")),
            ])),
            (b"R12_Test", element([
                (b"tags", scalar(packed.TYPE_STRING, b"lightTank")),
            ])),
            (b"Observer", element([
                (b"tags", scalar(
                    packed.TYPE_STRING, b"observer secret lightTank")),
            ])),
        ])
        return {
            self.LIST: packed.write_packed_xml(roster),
            self.VEHICLE: packed.write_packed_xml(vehicle),
            self.VEHICLE_TWO: packed.write_packed_xml(vehicle),
            self.OBSERVER: packed.write_packed_xml(vehicle),
            self.ENGINES: packed.write_packed_xml(engines),
            self.GUNS: packed.write_packed_xml(guns),
            self.SHELLS: packed.write_packed_xml(shells),
        }

    def _overlay(self, member):
        return os.path.join(
            self.game, *vehicle_overlays.OVERLAY_ROOT.split("/"),
            *member.split("/"))

    def _root(self, member):
        with open(self._overlay(member), "rb") as stream:
            return packed.read_packed_xml(stream.read())

    def _value(self, member, field_path):
        return vehicle_overlays._find_value(
            self._root(member), field_path).value

    def test_inspect_shows_exact_path_original_type_and_constraint(self):
        result = vehicle_overlays.inspect_vehicle_field(
            self.game, self.VEHICLE, "speedLimits/forward")

        self.assertEqual("32", result["originalValue"])
        self.assertEqual("32", result["currentValue"])
        self.assertEqual("integer", result["packedType"])
        self.assertIn("positive", result["constraint"])
        self.assertEqual(self._overlay(self.VEHICLE), result["overlayPath"])
        self.assertEqual("", result["conflict"])

    def test_apply_writes_only_res_mods_and_records_complete_member_ownership(self):
        package_path = self._write_package()
        with open(package_path, "rb") as stream:
            package_before = stream.read()

        result = vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)

        self.assertEqual("40", result["currentValue"])
        self.assertEqual(40, self._value(
            self.VEHICLE, "speedLimits/forward"))
        self.assertEqual(packed.TYPE_INTEGER, vehicle_overlays._find_value(
            self._root(self.VEHICLE), "speedLimits/forward").value_type)
        with open(package_path, "rb") as stream:
            self.assertEqual(package_before, stream.read())
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            manifest = json.load(stream)
        self.assertEqual(1, manifest["schema"])
        self.assertEqual(self.VEHICLE,
                         manifest["members"][0]["sourceMember"])
        self.assertEqual(self.VEHICLE,
                         manifest["members"][0]["overlayRelativePath"])
        edit = manifest["members"][0]["edits"][0]
        self.assertEqual("integer", edit["originalPackedType"])
        self.assertEqual(32, edit["originalValue"])
        self.assertEqual(40, edit["replacementValue"])

    def test_later_edits_rebuild_the_member_from_original_and_merge_all_edits(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/backward", "12",
            is_running=lambda: False)

        self.assertEqual(40, self._value(
            self.VEHICLE, "speedLimits/forward"))
        self.assertEqual(12, self._value(
            self.VEHICLE, "speedLimits/backward"))
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            manifest = json.load(stream)
        self.assertEqual(2, len(manifest["members"][0]["edits"]))

    def test_rebuilding_one_member_preserves_unedited_compressed_strings(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.ENGINES, "shared/GAZ-M1/power", "100",
            is_running=lambda: False)

        tags = vehicle_overlays._find_value(
            self._root(self.ENGINES), "shared/GAZ-M1/tags")
        self.assertEqual(packed.TYPE_COMPRESSED_STRING, tags.value_type)
        self.assertEqual(b"\x81\x01", tags.value)

    def test_existing_overlay_without_this_manifest_is_a_conflict(self):
        self._write(
            "/".join((vehicle_overlays.OVERLAY_ROOT, self.VEHICLE)),
            b"another tool")

        result = vehicle_overlays.inspect_vehicle_field(
            self.game, self.VEHICLE, "speedLimits/forward")
        self.assertIn("Conflict", result["conflict"])
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "not owned"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/forward", "40",
                is_running=lambda: False)
        with open(self._overlay(self.VEHICLE), "rb") as stream:
            self.assertEqual(b"another tool", stream.read())

    def test_member_and_field_browsers_expose_only_existing_safe_contracts(self):
        self.assertEqual(
            sorted(self.members),
            vehicle_overlays.list_vehicle_members(self.game))

        gun_fields = vehicle_overlays.list_editable_fields(
            self.game, self.GUNS)
        paths = [record["fieldPath"] for record in gun_fields]
        self.assertIn("shared/Gun-A/reloadTime", paths)
        self.assertIn(
            "shared/Gun-A/shots/Shell-A/piercingPower", paths)
        penetration = next(
            record for record in gun_fields
            if record["fieldPath"].endswith("piercingPower"))
        self.assertEqual("22 18", penetration["originalValue"])
        self.assertEqual("string", penetration["packedType"])
        self.assertIn("exactly two", penetration["constraint"])

        engine_paths = [
            record["fieldPath"] for record in
            vehicle_overlays.list_editable_fields(self.game, self.ENGINES)]
        self.assertNotIn("shared/GAZ-M1/tags", engine_paths)
        self.assertNotIn("ids/GAZ-M1", engine_paths)

    def test_vehicle_browser_resolves_shared_topology_and_impact(self):
        choices = vehicle_overlays.list_vehicle_choices(self.game)
        self.assertEqual(
            [("ussr", "R11_MS-1"), ("ussr", "R12_Test")],
            [(choice["nation"], choice["vehicle"])
             for choice in choices])
        self.assertFalse(any(choice["vehicle"] in ("Observer", "list")
                             for choice in choices))

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)
        direct = next(record for record in fields
                      if record["fieldPath"] == "speedLimits/forward")
        engine = next(record for record in fields
                      if record["fieldPath"] == "shared/GAZ-M1/power")
        gun = next(record for record in fields
                   if record["fieldPath"] == "shared/Gun-A/reloadTime")
        shell = next(record for record in fields
                     if record["fieldPath"] == "Shell-A/damage/armor")

        self.assertEqual("Vehicle", direct["categoryLabel"])
        self.assertFalse(direct["shared"])
        self.assertEqual(("R11_MS-1",), direct["affectedVehicles"])
        for record, category in ((engine, "Engine"), (gun, "Gun"),
                                 (shell, "Shell")):
            self.assertEqual(category, record["categoryLabel"])
            self.assertTrue(record["shared"])
            self.assertEqual(
                ("Observer", "R11_MS-1", "R12_Test"),
                record["affectedVehicles"])
            self.assertIn(
                "Observer, R11_MS-1, R12_Test", record["scope"])

        self.assertEqual(self.ENGINES, engine["member"])
        self.assertEqual(self.GUNS, gun["member"])
        self.assertEqual(self.SHELLS, shell["member"])

    def test_vehicle_browser_does_not_infer_unlisted_component_links(self):
        root = packed.read_packed_xml(self.members[self.VEHICLE])
        engine = child(root, "engines").value
        child(engine, "GAZ-M1").value = b"vehicle-local"
        self.members[self.VEHICLE] = packed.write_packed_xml(root)
        self._write_package()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)

        self.assertFalse(any(
            record["member"] == self.ENGINES for record in fields))

    def test_existing_two_value_string_penetration_is_safely_editable(self):
        field_path = "shared/Gun-A/shots/Shell-A/piercingPower"

        result = vehicle_overlays.apply_vehicle_edit(
            self.game, self.GUNS, field_path, "30.0  24",
            is_running=lambda: False)

        value = vehicle_overlays._find_value(self._root(self.GUNS), field_path)
        self.assertEqual(packed.TYPE_STRING, value.value_type)
        self.assertEqual(b"30 24", value.value)
        self.assertEqual("22 18", result["originalValue"])
        self.assertEqual("30 24", result["currentValue"])

    def test_ids_resources_compressed_strings_and_missing_children_are_refused(self):
        refused = (
            (self.ENGINES, "ids/GAZ-M1"),
            (self.VEHICLE, "chassis/T-18Bis/resource"),
            (self.ENGINES, "shared/GAZ-M1/tags"),
            (self.VEHICLE, "speedLimits/newChild"),
        )
        for member, field_path in refused:
            with self.assertRaises(vehicle_overlays.VehicleOverlayError,
                                   msg=field_path):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, member, field_path, "1",
                    is_running=lambda: False)

    def test_parser_and_storage_constraints_fail_before_writing(self):
        refused = (
            (self.VEHICLE, "speedLimits/forward", "0"),
            (self.VEHICLE, "speedLimits/forward", "1.5"),
            (self.GUNS, "shared/Gun-A/reloadTime", "nan"),
            (self.GUNS, "shared/Gun-A/reloadTime", "inf"),
            (self.GUNS, "shared/Gun-A/maxAmmo", str(1 << 63)),
            (self.SHELLS, "Shell-A/damage/armor", "-1"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "30"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "30 20 10"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "30 nan"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "0 0"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "20 30"),
        )
        for member, field_path, replacement in refused:
            with self.assertRaises(vehicle_overlays.VehicleOverlayError,
                                   msg=(field_path, replacement)):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, member, field_path, replacement,
                    is_running=lambda: False)
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))

    def test_penetration_refuses_a_non_string_stock_type(self):
        field_path = "shared/Gun-A/shots/Shell-A/piercingPower"
        root = packed.read_packed_xml(self.members[self.GUNS])
        value = vehicle_overlays._find_value(root, field_path)
        value.value_type = packed.TYPE_INTEGER
        value.value = 22
        self.members[self.GUNS] = packed.write_packed_xml(root)
        self._write_package()

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "Packed string type"):
            vehicle_overlays.inspect_vehicle_field(
                self.game, self.GUNS, field_path)

    def test_health_relation_is_validated_after_all_logical_edits(self):
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "maxHealth"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.ENGINES,
                "shared/GAZ-M1/maxRegenHealth", "50",
                is_running=lambda: False)

        self.assertFalse(os.path.exists(self._overlay(self.ENGINES)))

    def test_running_game_refuses_apply_and_restore(self):
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "Close World of Tanks"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/forward", "40",
                is_running=lambda: True)
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "Close World of Tanks"):
            vehicle_overlays.restore_vehicle_defaults(
                self.game, is_running=lambda: True)

    def test_failed_transaction_restores_previous_overlay_and_manifest(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        with open(self._overlay(self.VEHICLE), "rb") as stream:
            overlay_before = stream.read()
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            manifest_before = stream.read()
        original_replace = os.replace

        def fail_manifest_install(source, target):
            if (".wot-vehicle-overlay-" in source and
                    os.path.basename(source).startswith("new-") and
                    target.endswith(vehicle_overlays.MANIFEST_NAME)):
                raise OSError("synthetic manifest failure")
            return original_replace(source, target)

        with mock.patch(
                "vehicle_overlays.os.replace",
                side_effect=fail_manifest_install):
            with self.assertRaisesRegex(
                    vehicle_overlays.VehicleOverlayError, "rolled back"):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, self.VEHICLE, "speedLimits/forward", "41",
                    is_running=lambda: False)

        with open(self._overlay(self.VEHICLE), "rb") as stream:
            self.assertEqual(overlay_before, stream.read())
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            self.assertEqual(manifest_before, stream.read())

    def test_incomplete_rollback_keeps_mapped_recovery_files(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        original_replace = os.replace

        def fail_install_and_rollback(source, target):
            if (".wot-vehicle-overlay-" in source and
                    os.path.basename(source).startswith("new-") and
                    target.endswith(vehicle_overlays.MANIFEST_NAME)):
                raise OSError("synthetic install failure")
            if (".wot-vehicle-overlay-" in source and
                    os.path.basename(source) == "backup-0" and
                    target == self._overlay(self.VEHICLE)):
                raise OSError("synthetic rollback failure")
            return original_replace(source, target)

        with mock.patch(
                "vehicle_overlays.os.replace",
                side_effect=fail_install_and_rollback):
            with self.assertRaisesRegex(
                    vehicle_overlays.VehicleOverlayError,
                    "Recovery files were kept"):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, self.VEHICLE, "speedLimits/forward", "41",
                    is_running=lambda: False)

        recovery_roots = [
            os.path.join(self.game, name) for name in os.listdir(self.game)
            if name.startswith(".wot-vehicle-overlay-")]
        self.assertEqual(1, len(recovery_roots))
        with open(os.path.join(
                recovery_roots[0], "recovery.json"), "rb") as stream:
            recovery = json.load(stream)
        self.assertEqual("apply", recovery["operation"])
        self.assertEqual(
            os.path.relpath(self._overlay(self.VEHICLE), self.game),
            recovery["targets"][0]["target"].replace("/", os.sep))
        self.assertTrue(os.path.isfile(os.path.join(
            recovery_roots[0], "backup-0")))

    def test_failed_default_restore_puts_every_owned_file_back(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.ENGINES, "shared/GAZ-M1/power", "100",
            is_running=lambda: False)
        targets = (
            self._overlay(self.VEHICLE),
            self._overlay(self.ENGINES),
            vehicle_overlays.manifest_path(self.game),
        )
        before = {}
        for path in targets:
            with open(path, "rb") as stream:
                before[path] = stream.read()
        original_replace = os.replace

        def fail_second_move(source, target):
            if (source == self._overlay(self.ENGINES) and
                    ".wot-vehicle-restore-" in target):
                raise OSError("synthetic restore failure")
            return original_replace(source, target)

        with mock.patch(
                "vehicle_overlays.os.replace", side_effect=fail_second_move):
            with self.assertRaisesRegex(
                    vehicle_overlays.VehicleOverlayError, "rolled back"):
                vehicle_overlays.restore_vehicle_defaults(
                    self.game, is_running=lambda: False)

        for path in targets:
            with open(path, "rb") as stream:
                self.assertEqual(before[path], stream.read())
        self.assertFalse(any(
            name.startswith(".wot-vehicle-restore-")
            for name in os.listdir(self.game)))

    def test_restore_removes_only_owned_members_and_keeps_other_mods(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        other = self._write(
            vehicle_overlays.OVERLAY_ROOT + "/other-author/mod.xml", b"keep")

        count = vehicle_overlays.restore_vehicle_defaults(
            self.game, is_running=lambda: False)

        self.assertEqual(1, count)
        self.assertFalse(os.path.exists(self._overlay(self.VEHICLE)))
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))
        with open(other, "rb") as stream:
            self.assertEqual(b"keep", stream.read())

    def test_restore_refuses_to_delete_an_externally_changed_owned_member(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        self._write(
            "/".join((vehicle_overlays.OVERLAY_ROOT, self.VEHICLE)),
            b"changed externally")

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "another tool"):
            vehicle_overlays.restore_vehicle_defaults(
                self.game, is_running=lambda: False)

        self.assertTrue(os.path.exists(self._overlay(self.VEHICLE)))
        self.assertTrue(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))

    def test_invalid_manifest_fails_closed(self):
        self._write(
            "/".join((vehicle_overlays.OVERLAY_ROOT,
                      vehicle_overlays.MANIFEST_NAME)),
            b'{"schema":999,"members":[]}')

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "does not belong"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/forward", "40",
                is_running=lambda: False)

    def test_manifest_values_cannot_change_the_recorded_packed_type(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        path = vehicle_overlays.manifest_path(self.game)
        with open(path, "rb") as stream:
            manifest = json.load(stream)
        manifest["members"][0]["edits"][0]["replacementValue"] = "41"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream)

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "keep integer values"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/backward", "12",
                is_running=lambda: False)

    def test_changed_original_package_contract_refuses_a_saved_edit(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        root = packed.read_packed_xml(self.members[self.VEHICLE])
        vehicle_overlays._find_value(
            root, "speedLimits/forward").value = 33
        self.members[self.VEHICLE] = packed.write_packed_xml(root)
        self._write_package()

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "original scripts.pkg"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/backward", "12",
                is_running=lambda: False)

    def test_packaged_launcher_analysis_includes_the_shared_packed_xml_parser(self):
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "build_launcher.ps1")
        with open(script, "r", encoding="utf-8") as stream:
            content = stream.read()
        self.assertIn('0.9.22\\tools', content)
        self.assertIn('--paths', content)


if __name__ == "__main__":
    unittest.main()
