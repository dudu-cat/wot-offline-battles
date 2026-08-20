"""Safe 0.9.22 Packed XML vehicle-data overlays.

The editor never writes ``scripts.pkg``.  Every accepted edit rebuilds the
complete package member from that original archive, then writes the result to
``res_mods/0.9.22.0.1``.  A manifest owns complete members, not individual
bytes, so an existing overlay from another tool is always a conflict.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import zipfile

try:
    import packed_xml
except ImportError:
    # Source checkouts run the launcher from ``launcher``.  The packaged build
    # adds this same tools directory to PyInstaller's analysis path.
    _TOOLS_ROOT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "0.9.22", "tools")
    if _TOOLS_ROOT not in sys.path:
        sys.path.insert(0, _TOOLS_ROOT)
    import packed_xml

try:
    from . import core
except ImportError:
    import core


TARGET_VERSION = "0.9.22.0.1"
TARGET_BUILD = "1513"
SOURCE_PACKAGE = "res/packages/scripts.pkg"
OVERLAY_ROOT = "res_mods/0.9.22.0.1"
MANIFEST_NAME = "vehicle_overlays.json"
MANIFEST_SCHEMA = 1

_COMPONENT_MEMBER = re.compile(
    r"^scripts/item_defs/vehicles/([a-z][a-z0-9_]*)/components/"
    r"(chassis|engines|fuelTanks|guns|radios|shells|turrets)\.xml$")
_VEHICLE_MEMBER = re.compile(
    r"^scripts/item_defs/vehicles/([a-z][a-z0-9_]*)/"
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)\.xml$")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

_TYPE_NAMES = {
    packed_xml.TYPE_STRING: "string",
    packed_xml.TYPE_INTEGER: "integer",
    packed_xml.TYPE_VECTOR: "vector",
    packed_xml.TYPE_BOOLEAN: "boolean",
    packed_xml.TYPE_COMPRESSED_STRING: "compressed-string",
}

_HEALTH_CONTAINERS = {
    "ammoBayHealth",
    "engineHealth",
    "fuelTankHealth",
    "radioHealth",
    "surveyingDeviceHealth",
    "turretRotatorHealth",
}

_CATEGORY_LABELS = {
    "vehicle": "Vehicle",
    "chassis": "Chassis",
    "turret": "Turret",
    "engines": "Engine",
    "fuelTanks": "Fuel tank",
    "guns": "Gun",
    "radios": "Radio",
    "shells": "Shell",
}
_CATEGORY_ORDER = dict(
    (name, index) for index, name in enumerate((
        "vehicle", "chassis", "turret", "engines", "fuelTanks",
        "guns", "radios", "shells")))
_FIELD_LABELS = {
    "speedLimits": "Speed limits",
    "forward": "Forward speed",
    "backward": "Reverse speed",
    "hull": "Hull",
    "ammoBayHealth": "Ammo rack",
    "engineHealth": "Engine health",
    "fuelTankHealth": "Fuel tank health",
    "radioHealth": "Radio health",
    "surveyingDeviceHealth": "Observation device",
    "turretRotatorHealth": "Turret traverse",
    "weight": "Weight",
    "maxLoad": "Load limit",
    "maxHealth": "Maximum health",
    "maxRegenHealth": "Repair threshold",
    "power": "Power",
    "rotationSpeed": "Traverse speed",
    "reloadTime": "Reload time",
    "aimingTime": "Aiming time",
    "maxAmmo": "Ammunition capacity",
    "shots": "Shell",
    "speed": "Projectile speed",
    "maxDistance": "Maximum distance",
    "gravity": "Gravity",
    "piercingPower": "Penetration",
    "caliber": "Caliber",
    "damage": "Damage",
    "armor": "Vehicle damage",
    "devices": "Module damage",
}


class VehicleOverlayError(Exception):
    """A safe, user-correctable editor refusal."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def manifest_path(game_root):
    return os.path.join(
        os.path.abspath(game_root), *OVERLAY_ROOT.split("/"), MANIFEST_NAME)


def _validate_member(member):
    if not isinstance(member, str):
        raise VehicleOverlayError("The package member must be text.")
    if (not member or member.startswith("/") or "\\" in member or
            any(part in ("", ".", "..") for part in member.split("/"))):
        raise VehicleOverlayError("The package member path is unsafe.")
    component = _COMPONENT_MEMBER.fullmatch(member)
    if component is not None:
        return ("component", component.group(2))
    if _VEHICLE_MEMBER.fullmatch(member) is not None:
        return ("vehicle", None)
    raise VehicleOverlayError(
        "Only 0.9.22 vehicle definitions and their known component members "
        "can be edited.")


def _field_parts(field_path):
    if not isinstance(field_path, str):
        raise VehicleOverlayError("The field path must be text.")
    parts = field_path.split("/")
    if (not field_path or "\\" in field_path or
            any(not _SAFE_SEGMENT.fullmatch(part) for part in parts)):
        raise VehicleOverlayError("The field path is unsafe.")
    return parts


def _rule(rule_id, description, minimum, inclusive=False,
          integer_required=False):
    relation = ">=" if inclusive else ">"
    return {
        "id": rule_id,
        "description": "%s; finite value %s %s" % (
            description, relation, minimum),
        "minimum": float(minimum),
        "inclusive": bool(inclusive),
        "integerRequired": bool(integer_required),
    }


_POSITIVE = _rule("positive", "stock parser requires a positive number", 0)
_NONNEGATIVE = _rule(
    "nonnegative", "stock parser requires a non-negative number", 0, True)
_MAX_AMMO = _rule(
    "max-ammo", "ammunition capacity must be a non-negative integer",
    0, True, True)
_MAX_HEALTH = _rule(
    "max-health", "device maximum health must be at least one", 1, True)
_MAX_REGEN = _rule(
    "max-regen-health",
    "regeneration health must be non-negative and no greater than maxHealth",
    0, True)
_PIERCING_PAIR = {
    "id": "piercing-pair",
    "description": (
        "penetration must contain exactly two positive finite numbers; "
        "the first value must be no less than the second"),
    "arity": 2,
}


def _health_rule(name):
    return _MAX_HEALTH if name == "maxHealth" else _MAX_REGEN


def _field_rule(member, field_path):
    member_kind, component_name = _validate_member(member)
    parts = _field_parts(field_path)

    if member_kind == "vehicle":
        if parts in (["speedLimits", "forward"],
                     ["speedLimits", "backward"]):
            return _POSITIVE
        if (len(parts) == 3 and parts[0] == "chassis" and
                parts[2] in ("weight", "maxLoad")):
            return _POSITIVE
        if parts in (["hull", "ammoBayHealth", "maxHealth"],
                     ["hull", "ammoBayHealth", "maxRegenHealth"]):
            return _health_rule(parts[-1])
        if (len(parts) == 3 and parts[0] == "chassis" and
                parts[-1] in ("maxHealth", "maxRegenHealth")):
            return _health_rule(parts[-1])
        if (len(parts) == 3 and re.fullmatch(r"turrets\d+", parts[0]) and
                parts[-1] in ("maxHealth", "maxRegenHealth")):
            return _health_rule(parts[-1])
        if (len(parts) == 4 and re.fullmatch(r"turrets\d+", parts[0]) and
                parts[2] in _HEALTH_CONTAINERS and
                parts[-1] in ("maxHealth", "maxRegenHealth")):
            return _health_rule(parts[-1])

    if member_kind == "component":
        if (component_name == "engines" and len(parts) == 3 and
                parts[0] == "shared" and parts[-1] in ("power", "weight")):
            return _POSITIVE
        if (component_name == "chassis" and len(parts) == 3 and
                parts[0] == "shared" and
                parts[-1] in ("weight", "maxLoad")):
            return _POSITIVE
        if component_name == "guns" and parts[:1] == ["shared"]:
            if len(parts) == 3 and parts[-1] == "rotationSpeed":
                return _NONNEGATIVE
            if (len(parts) == 3 and
                    parts[-1] in ("weight", "reloadTime", "aimingTime")):
                return _POSITIVE
            if len(parts) == 3 and parts[-1] == "maxAmmo":
                return _MAX_AMMO
            if (len(parts) == 5 and parts[2] == "shots" and
                    parts[-1] in ("speed", "maxDistance")):
                return _POSITIVE
            if (len(parts) == 5 and parts[2] == "shots" and
                    parts[-1] == "gravity"):
                return _NONNEGATIVE
            if (len(parts) == 5 and parts[2] == "shots" and
                    parts[-1] == "piercingPower"):
                return _PIERCING_PAIR
        if component_name == "shells":
            if len(parts) == 2 and parts[-1] == "caliber":
                return _POSITIVE
            if (len(parts) == 3 and parts[1] == "damage" and
                    parts[-1] in ("armor", "devices")):
                return _POSITIVE
        if component_name != "shells" and parts[:1] == ["shared"]:
            if (len(parts) == 3 and
                    parts[-1] in ("maxHealth", "maxRegenHealth")):
                return _health_rule(parts[-1])
            if (len(parts) == 4 and parts[2] in _HEALTH_CONTAINERS and
                    parts[-1] in ("maxHealth", "maxRegenHealth")):
                return _health_rule(parts[-1])

    raise VehicleOverlayError(
        "This field is not in the first safe scalar allowlist. IDs, topology, "
        "resource paths, arbitrary vectors, compressed strings, and unknown "
        "fields are never editable.")


def _overlay_path(game_root, member):
    _validate_member(member)
    return os.path.join(
        os.path.abspath(game_root), *OVERLAY_ROOT.split("/"),
        *member.split("/"))


def _require_target(game_root, require_closed=False, is_running=None):
    status = core.inspect_game_root(game_root)
    if not status.get("has_executable"):
        raise VehicleOverlayError(
            "Select the folder that contains %s." % core.GAME_EXECUTABLE)
    if status.get("client") != core.PORT_0_9_22:
        raise VehicleOverlayError(
            "Vehicle data editing requires the exact supported 0.9.22 client.")
    package_path = os.path.join(
        status["path"], *SOURCE_PACKAGE.split("/"))
    if not os.path.isfile(package_path):
        raise VehicleOverlayError("The original scripts.pkg is missing.")
    if require_closed:
        checker = core.game_is_running if is_running is None else is_running
        if checker():
            raise VehicleOverlayError(
                "Close World of Tanks before changing vehicle data.")
    return status, package_path


def _read_source_member(package_path, member):
    _validate_member(member)
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            matches = [info for info in archive.infolist()
                       if info.filename == member]
            if len(matches) != 1:
                raise VehicleOverlayError(
                    "The original package must contain exactly one %s." % member)
            data = archive.read(matches[0])
        return data, packed_xml.read_packed_xml(data)
    except VehicleOverlayError:
        raise
    except (IOError, OSError, KeyError, ValueError,
            zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original Packed XML member is unreadable: %s" % error)


def _find_value(root, field_path):
    parts = _field_parts(field_path)
    element = root
    for offset, part in enumerate(parts):
        encoded = part.encode("utf-8")
        matches = [(index, value)
                   for index, (name, value) in enumerate(element.children)
                   if name == encoded]
        if len(matches) != 1:
            raise VehicleOverlayError(
                "Field path component %s must exist exactly once." % part)
        unused_index, value = matches[0]
        if offset == len(parts) - 1:
            if value.value_type == packed_xml.TYPE_ELEMENT:
                raise VehicleOverlayError(
                    "Only an existing scalar leaf can be edited.")
            return value
        if value.value_type != packed_xml.TYPE_ELEMENT:
            raise VehicleOverlayError(
                "Field path component %s is not an element." % part)
        element = value.value
    raise VehicleOverlayError("The field path is empty.")


def _scalar_text(value):
    if value.value_type == packed_xml.TYPE_INTEGER:
        return str(int(value.value))
    if value.value_type == packed_xml.TYPE_STRING:
        try:
            return value.value.decode("ascii").strip()
        except (AttributeError, UnicodeDecodeError):
            raise VehicleOverlayError(
                "The original numeric string is not ASCII text.")
    type_name = _TYPE_NAMES.get(value.value_type, "unknown")
    raise VehicleOverlayError(
        "Packed type %s is not an editable numeric scalar." % type_name)


def _numeric_value(value):
    text = _scalar_text(value)
    try:
        numeric = float(text)
    except (TypeError, ValueError, OverflowError):
        raise VehicleOverlayError(
            "The original field is not a numeric scalar.")
    if not math.isfinite(numeric):
        raise VehicleOverlayError("The original field is not finite.")
    return numeric


def _normalize_piercing_pair(raw_value, label):
    parts = str(raw_value).strip().split()
    if len(parts) != 2:
        raise VehicleOverlayError(
            "%s must contain exactly two numbers." % label)
    numbers = []
    for part in parts:
        try:
            number = float(part)
        except (TypeError, ValueError, OverflowError):
            raise VehicleOverlayError(
                "%s must contain exactly two numbers." % label)
        if not math.isfinite(number) or number <= 0:
            raise VehicleOverlayError(
                "%s values must be positive and finite." % label)
        numbers.append(number)
    if numbers[0] < numbers[1]:
        raise VehicleOverlayError(
            "%s first value must be no less than the second." % label)
    return " ".join(format(number, ".15g") for number in numbers)


def _validate_original(value, rule):
    if rule.get("arity") == 2:
        if value.value_type != packed_xml.TYPE_STRING:
            raise VehicleOverlayError(
                "piercingPower must use the stock Packed string type.")
        _normalize_piercing_pair(_scalar_text(value), "Original penetration")
        return
    _numeric_value(value)
    if (rule.get("integerRequired") and
            value.value_type != packed_xml.TYPE_INTEGER):
        raise VehicleOverlayError(
            "This field does not use the required Packed integer type.")


def list_vehicle_members(game_root):
    """List source package members that the editor can safely address."""
    unused_status, package_path = _require_target(game_root)
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            counts = {}
            for info in archive.infolist():
                name = info.filename
                if (_COMPONENT_MEMBER.fullmatch(name) is not None or
                        _VEHICLE_MEMBER.fullmatch(name) is not None):
                    counts[name] = counts.get(name, 0) + 1
    except (IOError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original scripts.pkg member list is unreadable: %s" % error)
    return sorted(name for name, count in counts.items() if count == 1)


def list_vehicle_choices(game_root):
    """List real vehicle definitions as nation/vehicle choices."""
    unused_status, package_path = _require_target(game_root)
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            counts = {}
            for info in archive.infolist():
                counts[info.filename] = counts.get(info.filename, 0) + 1
            roster = _vehicle_roster_from_archive(archive, counts)
    except VehicleOverlayError:
        raise
    except (IOError, OSError, KeyError, TypeError, ValueError,
            zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original vehicle roster is unreadable: %s" % error)
    return [dict((key, record[key]) for key in (
        "nation", "vehicle", "member"))
        for record in roster if record["selectable"]]


def _vehicle_roster_from_archive(archive, counts, nation=None):
    """Resolve vehicle definitions from each nation's stock list.xml."""
    list_members = []
    for member, count in counts.items():
        match = _VEHICLE_MEMBER.fullmatch(member)
        if (match is not None and match.group(2) == "list" and
                (nation is None or match.group(1) == nation)):
            if count != 1:
                raise VehicleOverlayError(
                    "A stock vehicle roster member is repeated.")
            list_members.append((match.group(1), member))
    if nation is not None and not list_members:
        raise VehicleOverlayError(
            "The selected nation's stock vehicle roster is missing.")

    records = []
    for roster_nation, list_member in sorted(list_members):
        root = packed_xml.read_packed_xml(archive.read(list_member))
        seen = set()
        for raw_name, value in root.children:
            # China and Japan retain the stock XML-reference namespace as a
            # scalar Packed XML metadata node.  It is not a vehicle entry.
            if (raw_name == b"xmlns:xmlref" and
                    value.value_type != packed_xml.TYPE_ELEMENT):
                continue
            try:
                vehicle = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                raise VehicleOverlayError(
                    "A stock vehicle roster name is not valid UTF-8.")
            if (vehicle in seen or _SAFE_SEGMENT.fullmatch(vehicle) is None or
                    value.value_type != packed_xml.TYPE_ELEMENT):
                raise VehicleOverlayError(
                    "A stock vehicle roster entry is ambiguous.")
            seen.add(vehicle)
            member = "scripts/item_defs/vehicles/%s/%s.xml" % (
                roster_nation, vehicle)
            if counts.get(member) != 1:
                raise VehicleOverlayError(
                    "A listed vehicle definition is missing or repeated: %s" %
                    member)

            tags = ""
            tag_values = [child for name, child in value.value.children
                          if name == b"tags"]
            if len(tag_values) == 1:
                try:
                    tags = _scalar_text(tag_values[0])
                except VehicleOverlayError:
                    tags = ""
            records.append({
                "nation": roster_nation,
                "vehicle": vehicle,
                "member": member,
                "selectable": "observer" not in tags.split(),
            })
    return sorted(records, key=lambda record: (
        record["nation"], record["vehicle"], record["member"]))


def _editable_fields_from_root(member, root):
    records = []

    def visit(element, prefix=()):
        for raw_name, value in element.children:
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                continue
            path_parts = prefix + (name,)
            if value.value_type == packed_xml.TYPE_ELEMENT:
                visit(value.value, path_parts)
                continue
            field_path = "/".join(path_parts)
            try:
                rule = _field_rule(member, field_path)
                # Duplicate child names are not addressable without changing
                # topology, even when one of them happens to be allowlisted.
                if _find_value(root, field_path) is not value:
                    continue
                _validate_original(value, rule)
            except VehicleOverlayError:
                continue
            records.append({
                "fieldPath": field_path,
                "originalValue": _scalar_text(value),
                "packedType": _TYPE_NAMES.get(value.value_type, "unknown"),
                "constraint": rule["description"],
            })

    visit(root)
    return sorted(records, key=lambda record: record["fieldPath"])


def _element_child(element, name):
    encoded = name.encode("utf-8")
    values = [value for current, value in element.children
              if current == encoded]
    if (len(values) != 1 or
            values[0].value_type != packed_xml.TYPE_ELEMENT):
        return None
    return values[0].value


def _vehicle_component_references(root):
    """Read only component references explicitly present in one vehicle."""
    references = dict((name, set()) for name in (
        "chassis", "engines", "fuelTanks", "guns", "radios", "turrets"))
    for category in ("chassis", "engines", "fuelTanks", "radios"):
        container = _element_child(root, category)
        if container is None:
            continue
        for raw_name, value in container.children:
            if value.value_type == packed_xml.TYPE_ELEMENT:
                continue
            try:
                if _scalar_text(value) == "shared":
                    references[category].add(raw_name.decode("utf-8"))
            except (UnicodeDecodeError, VehicleOverlayError):
                continue

    for raw_group, group_value in root.children:
        try:
            group_name = raw_group.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if (re.fullmatch(r"turrets\d+", group_name) is None or
                group_value.value_type != packed_xml.TYPE_ELEMENT):
            continue
        for raw_turret, turret_value in group_value.value.children:
            try:
                turret_name = raw_turret.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if turret_value.value_type != packed_xml.TYPE_ELEMENT:
                try:
                    if _scalar_text(turret_value) == "shared":
                        references["turrets"].add(turret_name)
                except VehicleOverlayError:
                    pass
                continue
            guns = _element_child(turret_value.value, "guns")
            if guns is None:
                continue
            for raw_gun, unused_value in guns.children:
                try:
                    references["guns"].add(raw_gun.decode("utf-8"))
                except UnicodeDecodeError:
                    continue
    return references


def _gun_shell_references(guns_root, gun_names):
    shared = _element_child(guns_root, "shared")
    if shared is None:
        return set()
    shells = set()
    for gun_name in gun_names:
        gun = _element_child(shared, gun_name)
        if gun is None:
            continue
        shots = _element_child(gun, "shots")
        if shots is None:
            continue
        for raw_shell, unused_value in shots.children:
            try:
                shells.add(raw_shell.decode("utf-8"))
            except UnicodeDecodeError:
                continue
    return shells


def _component_name(category, field_path):
    parts = _field_parts(field_path)
    if category == "shells":
        return parts[0] if len(parts) >= 2 else None
    if len(parts) >= 3 and parts[0] == "shared":
        return parts[1]
    return None


def _direct_category(field_path):
    first = _field_parts(field_path)[0]
    if first == "chassis":
        return "chassis"
    if re.fullmatch(r"turrets\d+", first) is not None:
        return "turret"
    return "vehicle"


def _field_label(category, field_path):
    parts = _field_parts(field_path)
    if category != "shells" and parts[:1] == ["shared"]:
        parts = parts[1:]
    return " / ".join(_FIELD_LABELS.get(part, part) for part in parts)


def _choice_record(nation, vehicle, category, member, field, shared,
                   component, affected):
    affected = tuple(sorted(affected))
    if shared:
        scope = ("Shared %s %s; affects %d vehicle%s in %s: %s" % (
            _CATEGORY_LABELS[category].lower(), component, len(affected),
            "" if len(affected) == 1 else "s", nation,
            ", ".join(affected)))
    else:
        scope = "Stored in %s only; affects this vehicle only." % vehicle
    result = dict(field)
    result.update({
        "nation": nation,
        "vehicle": vehicle,
        "category": category,
        "categoryLabel": _CATEGORY_LABELS[category],
        "fieldLabel": _field_label(category, field["fieldPath"]),
        "member": member,
        "shared": bool(shared),
        "component": component,
        "affectedVehicles": affected,
        "scope": scope,
    })
    return result


def list_vehicle_field_choices(game_root, vehicle_member):
    """Resolve one vehicle to safe fields through its original topology.

    Shared component choices are included only when every affected vehicle can
    be derived from the same nation's original vehicle definitions.
    """
    status, package_path = _require_target(game_root)
    selected_match = _VEHICLE_MEMBER.fullmatch(vehicle_member)
    if selected_match is None or "/components/" in vehicle_member:
        raise VehicleOverlayError("Select one original vehicle definition.")
    nation, vehicle = selected_match.groups()
    component_members = dict(
        (category, "scripts/item_defs/vehicles/%s/components/%s.xml" %
         (nation, category))
        for category in (
            "chassis", "engines", "fuelTanks", "guns", "radios",
            "shells", "turrets"))
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            counts = {}
            for info in archive.infolist():
                counts[info.filename] = counts.get(info.filename, 0) + 1

            roster = _vehicle_roster_from_archive(
                archive, counts, nation=nation)
            selectable = [record for record in roster
                          if record["selectable"]]
            if vehicle_member not in [record["member"]
                                      for record in selectable]:
                raise VehicleOverlayError(
                    "The selected vehicle is not in the stock vehicle roster.")

            roots = {}
            references = {}
            for choice in roster:
                member = choice["member"]
                roots[member] = packed_xml.read_packed_xml(archive.read(member))
                references[member] = _vehicle_component_references(
                    roots[member])

            component_roots = {}
            for category, member in component_members.items():
                if counts.get(member) == 1:
                    component_roots[category] = packed_xml.read_packed_xml(
                        archive.read(member))
    except VehicleOverlayError:
        raise
    except (IOError, OSError, KeyError, TypeError, ValueError,
            zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original vehicle topology is unreadable: %s" % error)

    selected_refs = references[vehicle_member]
    all_guns = set()
    for value in references.values():
        all_guns.update(value["guns"])
    gun_shells = {}
    guns_root = component_roots.get("guns")
    if guns_root is not None:
        for gun_name in all_guns:
            gun_shells[gun_name] = _gun_shell_references(
                guns_root, (gun_name,))

    affected = {}
    for choice in roster:
        member = choice["member"]
        vehicle_name = choice["vehicle"]
        for category, names in references[member].items():
            for component in names:
                affected.setdefault(
                    (category, component), set()).add(vehicle_name)
        shells = set()
        for gun_name in references[member]["guns"]:
            shells.update(gun_shells.get(gun_name, ()))
        for shell_name in shells:
            affected.setdefault(
                ("shells", shell_name), set()).add(vehicle_name)

    records = []
    for field in _editable_fields_from_root(
            vehicle_member, roots[vehicle_member]):
        category = _direct_category(field["fieldPath"])
        records.append(_choice_record(
            nation, vehicle, category, vehicle_member, field, False,
            vehicle, (vehicle,)))

    selected_components = dict(
        (category, set(names)) for category, names in selected_refs.items())
    selected_shells = set()
    for gun_name in selected_refs["guns"]:
        selected_shells.update(gun_shells.get(gun_name, ()))
    selected_components["shells"] = selected_shells

    for category, components in selected_components.items():
        if not components:
            continue
        member = component_members.get(category)
        root = component_roots.get(category)
        if member is None or root is None:
            raise VehicleOverlayError(
                "The shared %s topology for %s cannot be resolved safely." %
                (_CATEGORY_LABELS.get(category, category), vehicle))
        for field in _editable_fields_from_root(member, root):
            component = _component_name(category, field["fieldPath"])
            if component not in components:
                continue
            users = affected.get((category, component), set())
            if vehicle not in users:
                raise VehicleOverlayError(
                    "The shared component impact set is incomplete.")
            records.append(_choice_record(
                nation, vehicle, category, member, field, True,
                component, users))

    return sorted(records, key=lambda record: (
        _CATEGORY_ORDER[record["category"]], record["fieldLabel"],
        record["member"], record["fieldPath"]))


def list_editable_fields(game_root, member):
    """List existing allowlisted fields and their original contracts."""
    unused_status, package_path = _require_target(game_root)
    unused_data, root = _read_source_member(package_path, member)
    return _editable_fields_from_root(member, root)


def _manifest_scalar(value):
    if value.value_type == packed_xml.TYPE_INTEGER:
        return int(value.value)
    return _scalar_text(value)


def _parse_replacement(raw_value, original, rule):
    if original.value_type not in (
            packed_xml.TYPE_STRING, packed_xml.TYPE_INTEGER):
        raise VehicleOverlayError(
            "Only existing string or integer numeric scalars can be edited.")
    text = str(raw_value).strip()
    if not text:
        raise VehicleOverlayError("Enter a replacement value.")

    if rule.get("arity") == 2:
        if original.value_type != packed_xml.TYPE_STRING:
            raise VehicleOverlayError(
                "piercingPower must preserve the stock Packed string type.")
        manifest_value = _normalize_piercing_pair(
            text, "Replacement penetration")
        return (packed_xml.PackedValue(
            packed_xml.TYPE_STRING, manifest_value.encode("ascii")),
                manifest_value)

    if original.value_type == packed_xml.TYPE_INTEGER:
        try:
            value = int(text)
        except (TypeError, ValueError, OverflowError):
            raise VehicleOverlayError(
                "This field uses the Packed integer type; enter a whole number.")
        if value < -(1 << 63) or value > (1 << 63) - 1:
            raise VehicleOverlayError(
                "Packed integers must fit signed 64-bit storage.")
        numeric = float(value)
        replacement = packed_xml.PackedValue(
            packed_xml.TYPE_INTEGER, value)
        manifest_value = value
    else:
        if rule.get("integerRequired"):
            raise VehicleOverlayError(
                "This field must use the stock Packed integer type.")
        try:
            numeric = float(text)
        except (TypeError, ValueError, OverflowError):
            raise VehicleOverlayError("Enter one numeric scalar.")
        if not math.isfinite(numeric):
            raise VehicleOverlayError("The replacement must be finite.")
        manifest_value = format(numeric, ".15g")
        replacement = packed_xml.PackedValue(
            packed_xml.TYPE_STRING, manifest_value.encode("ascii"))

    if not math.isfinite(numeric):
        raise VehicleOverlayError("The replacement must be finite.")
    minimum = rule["minimum"]
    accepted = (numeric >= minimum if rule["inclusive"]
                else numeric > minimum)
    if not accepted:
        raise VehicleOverlayError(rule["description"] + ".")
    return replacement, manifest_value


def _empty_manifest():
    timestamp = _now()
    return {
        "schema": MANIFEST_SCHEMA,
        "targetVersion": TARGET_VERSION,
        "targetBuild": TARGET_BUILD,
        "sourcePackage": SOURCE_PACKAGE,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "members": [],
    }


def _validate_manifest(value):
    if not isinstance(value, dict):
        raise VehicleOverlayError("vehicle_overlays.json must be an object.")
    if (value.get("schema") != MANIFEST_SCHEMA or
            value.get("targetVersion") != TARGET_VERSION or
            str(value.get("targetBuild")) != TARGET_BUILD or
            value.get("sourcePackage") != SOURCE_PACKAGE):
        raise VehicleOverlayError(
            "vehicle_overlays.json does not belong to this editor and build.")
    members = value.get("members")
    if not isinstance(members, list):
        raise VehicleOverlayError("The overlay manifest member list is invalid.")
    seen_members = set()
    for entry in members:
        if not isinstance(entry, dict):
            raise VehicleOverlayError("An overlay manifest member is invalid.")
        member = entry.get("sourceMember")
        _validate_member(member)
        if member in seen_members:
            raise VehicleOverlayError("The overlay manifest repeats a member.")
        seen_members.add(member)
        if (entry.get("sourcePackage") != SOURCE_PACKAGE or
                entry.get("overlayRelativePath") != member or
                not _DIGEST.fullmatch(str(entry.get("overlaySha256", "")))):
            raise VehicleOverlayError(
                "The overlay manifest ownership record is invalid.")
        edits = entry.get("edits")
        if not isinstance(edits, list) or not edits:
            raise VehicleOverlayError("An owned member has no logical edits.")
        seen_fields = set()
        for edit in edits:
            if not isinstance(edit, dict):
                raise VehicleOverlayError("A manifest edit is invalid.")
            field_path = edit.get("fieldPath")
            _field_rule(member, field_path)
            if field_path in seen_fields:
                raise VehicleOverlayError("A manifest repeats one field edit.")
            seen_fields.add(field_path)
            if edit.get("originalPackedType") not in (
                    "integer", "string"):
                raise VehicleOverlayError(
                    "A manifest edit has an unsupported Packed type.")
            if "originalValue" not in edit or "replacementValue" not in edit:
                raise VehicleOverlayError(
                    "A manifest edit is missing its values.")
            if edit["originalPackedType"] == "integer":
                if (not isinstance(edit["originalValue"], int) or
                        isinstance(edit["originalValue"], bool) or
                        not isinstance(edit["replacementValue"], int) or
                        isinstance(edit["replacementValue"], bool)):
                    raise VehicleOverlayError(
                        "A Packed integer manifest edit must keep integer "
                        "values.")
            elif (not isinstance(edit["originalValue"], str) or
                  not isinstance(edit["replacementValue"], str)):
                raise VehicleOverlayError(
                    "A Packed string manifest edit must keep string values.")
    return value


def _load_manifest(game_root):
    path = manifest_path(game_root)
    if not os.path.lexists(path):
        return _empty_manifest(), False
    if os.path.islink(path) or not os.path.isfile(path):
        raise VehicleOverlayError(
            "vehicle_overlays.json is not a regular file.")
    try:
        with open(path, "rb") as stream:
            value = json.load(stream)
    except (IOError, OSError, TypeError, ValueError) as error:
        raise VehicleOverlayError(
            "vehicle_overlays.json is unreadable: %s" % error)
    return _validate_manifest(value), True


def _entry_map(manifest):
    return dict((entry["sourceMember"], entry)
                for entry in manifest["members"])


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read_file(path):
    with open(path, "rb") as stream:
        return stream.read()


def _ownership_problem(game_root, member, entry):
    path = _overlay_path(game_root, member)
    if not os.path.lexists(path):
        return ("Owned overlay is missing; Apply will rebuild it."
                if entry is not None else "")
    if os.path.islink(path) or not os.path.isfile(path):
        return "Conflict: the overlay target is not a regular file."
    if entry is None:
        return (
            "Conflict: this complete package member already exists in "
            "res_mods but is not owned by vehicle_overlays.json.")
    try:
        current_digest = _sha256(_read_file(path))
    except (IOError, OSError) as error:
        return "Conflict: the existing overlay cannot be read: %s" % error
    if current_digest != entry["overlaySha256"]:
        return (
            "Conflict: this editor's owned overlay was changed by another "
            "tool. No file will be overwritten or removed.")
    return ""


def _assert_owned_files_safe(game_root, old_entries, new_members=()):
    for member, entry in old_entries.items():
        problem = _ownership_problem(game_root, member, entry)
        if problem and not problem.startswith("Owned overlay is missing"):
            raise VehicleOverlayError(problem)
    for member in new_members:
        if member in old_entries:
            continue
        problem = _ownership_problem(game_root, member, None)
        if problem:
            raise VehicleOverlayError(problem)


def _same_recorded_value(recorded, current, value_type):
    if value_type == packed_xml.TYPE_INTEGER:
        return (isinstance(recorded, int) and not isinstance(recorded, bool)
                and recorded == int(current))
    return isinstance(recorded, str) and recorded == str(current)


def _compare_trees(original, rebuilt, edited_paths, prefix=()):
    if original.value.value_type != rebuilt.value.value_type:
        raise VehicleOverlayError("Packed XML root type changed during rebuild.")
    if prefix not in edited_paths and original.value.value != rebuilt.value.value:
        raise VehicleOverlayError("An unedited Packed XML value changed.")
    if len(original.children) != len(rebuilt.children):
        raise VehicleOverlayError("Packed XML topology changed during rebuild.")
    for (old_name, old_value), (new_name, new_value) in zip(
            original.children, rebuilt.children):
        if old_name != new_name or old_value.value_type != new_value.value_type:
            raise VehicleOverlayError(
                "Packed XML names or types changed during rebuild.")
        child_path = prefix + (old_name.decode("utf-8"),)
        if old_value.value_type == packed_xml.TYPE_ELEMENT:
            _compare_trees(
                old_value.value, new_value.value, edited_paths, child_path)
        elif (child_path not in edited_paths and
              old_value.value != new_value.value):
            raise VehicleOverlayError(
                "An unedited Packed XML scalar changed during rebuild.")


def _validate_health_relations(member, element, prefix=()):
    direct = {}
    for name, value in element.children:
        decoded = name.decode("utf-8")
        direct.setdefault(decoded, []).append(value)
    if (len(direct.get("maxHealth", ())) == 1 and
            len(direct.get("maxRegenHealth", ())) == 1):
        max_path = "/".join(prefix + ("maxHealth",))
        regen_path = "/".join(prefix + ("maxRegenHealth",))
        try:
            _field_rule(member, max_path)
            _field_rule(member, regen_path)
        except VehicleOverlayError:
            pass
        else:
            maximum = _numeric_value(direct["maxHealth"][0])
            regeneration = _numeric_value(direct["maxRegenHealth"][0])
            if maximum < 1 or regeneration < 0 or regeneration > maximum:
                raise VehicleOverlayError(
                    "%s must be between zero and maxHealth (%s)." %
                    (regen_path, _scalar_text(direct["maxHealth"][0])))
    for name, value in element.children:
        if value.value_type == packed_xml.TYPE_ELEMENT:
            _validate_health_relations(
                member, value.value, prefix + (name.decode("utf-8"),))


def _build_member(package_path, entry):
    member = entry["sourceMember"]
    unused_source, original_root = _read_source_member(package_path, member)
    rebuilt_root = copy.deepcopy(original_root)
    edited_paths = set()
    normalized_edits = []
    for edit in sorted(entry["edits"], key=lambda item: item["fieldPath"]):
        field_path = edit["fieldPath"]
        rule = _field_rule(member, field_path)
        original = _find_value(original_root, field_path)
        target = _find_value(rebuilt_root, field_path)
        type_name = _TYPE_NAMES.get(original.value_type, "unknown")
        if edit["originalPackedType"] != type_name:
            raise VehicleOverlayError(
                "The original Packed type changed for %s." % field_path)
        original_value = _manifest_scalar(original)
        if not _same_recorded_value(
                edit["originalValue"], original_value, original.value_type):
            raise VehicleOverlayError(
                "The original scripts.pkg value changed for %s." % field_path)
        replacement, manifest_value = _parse_replacement(
            edit["replacementValue"], original, rule)
        if target.value_type != replacement.value_type:
            raise VehicleOverlayError(
                "The Packed type changed for %s." % field_path)
        target.value = replacement.value
        edited_paths.add(tuple(_field_parts(field_path)))
        normalized = dict(edit)
        normalized["replacementValue"] = manifest_value
        normalized["constraint"] = rule["description"]
        normalized_edits.append(normalized)

    _validate_health_relations(member, rebuilt_root)
    try:
        output = packed_xml.write_packed_xml(rebuilt_root)
        reparsed = packed_xml.read_packed_xml(output)
    except (TypeError, ValueError, OverflowError) as error:
        raise VehicleOverlayError(
            "The rebuilt Packed XML failed validation: %s" % error)
    _compare_trees(original_root, reparsed, edited_paths)
    for edit in normalized_edits:
        value = _find_value(reparsed, edit["fieldPath"])
        replacement, unused = _parse_replacement(
            edit["replacementValue"], value,
            _field_rule(member, edit["fieldPath"]))
        if (value.value_type != replacement.value_type or
                value.value != replacement.value):
            raise VehicleOverlayError(
                "The rebuilt value did not round-trip for %s." %
                edit["fieldPath"])
    return output, normalized_edits


def _write_staged(path, data):
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "wb") as stream:
        stream.write(data)
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except (AttributeError, OSError):
            pass


def _recovery_bytes(game_root, operation, targets):
    records = []
    for index, target in enumerate(targets):
        records.append({
            "backup": "backup-%d" % index,
            "target": os.path.relpath(target, game_root).replace(os.sep, "/"),
        })
    return (json.dumps({
        "operation": operation,
        "targets": records,
    }, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _transactional_write(game_root, writes):
    game_root = os.path.abspath(game_root)
    try:
        transaction_root = tempfile.mkdtemp(
            prefix=".wot-vehicle-overlay-", dir=game_root)
    except (IOError, OSError) as error:
        raise VehicleOverlayError(
            "The overlay transaction could not start: %s" % error)
    staged = []
    backups = []
    installed = []
    preserve_recovery = False
    try:
        _write_staged(
            os.path.join(transaction_root, "recovery.json"),
            _recovery_bytes(
                game_root, "apply",
                [target for target, unused_data in writes]))
        for index, (target, data) in enumerate(writes):
            staged_path = os.path.join(transaction_root, "new-%d" % index)
            _write_staged(staged_path, data)
            staged.append((staged_path, target))
        for index, (unused_staged, target) in enumerate(staged):
            if os.path.lexists(target):
                backup = os.path.join(
                    transaction_root, "backup-%d" % index)
                os.replace(target, backup)
                backups.append((target, backup))
        for staged_path, target in staged:
            directory = os.path.dirname(target)
            if not os.path.isdir(directory):
                os.makedirs(directory)
            os.replace(staged_path, target)
            installed.append(target)
    except Exception as error:
        rollback_errors = []
        backed_targets = set(target for target, unused_backup in backups)
        for target in reversed(installed):
            if target not in backed_targets and os.path.lexists(target):
                try:
                    os.unlink(target)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
        for target, backup in reversed(backups):
            if os.path.lexists(backup):
                try:
                    directory = os.path.dirname(target)
                    if not os.path.isdir(directory):
                        os.makedirs(directory)
                    os.replace(backup, target)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            else:
                rollback_errors.append("A transaction backup is missing.")
        if rollback_errors:
            preserve_recovery = True
            raise VehicleOverlayError(
                "The overlay transaction failed and automatic rollback was "
                "incomplete. Recovery files were kept in %s: %s" %
                (transaction_root, "; ".join(rollback_errors)))
        raise VehicleOverlayError(
            "The overlay transaction was rolled back: %s" % error)
    finally:
        if not preserve_recovery:
            shutil.rmtree(transaction_root, ignore_errors=True)


def _manifest_bytes(manifest):
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def inspect_vehicle_field(game_root, member, field_path):
    """Describe one original scalar and any currently owned override."""
    status, package_path = _require_target(game_root)
    rule = _field_rule(member, field_path)
    unused_data, original_root = _read_source_member(package_path, member)
    original = _find_value(original_root, field_path)
    _validate_original(original, rule)

    manifest, unused_exists = _load_manifest(status["path"])
    entry = _entry_map(manifest).get(member)
    conflict = _ownership_problem(status["path"], member, entry)
    current = original
    overlay_path = _overlay_path(status["path"], member)
    if entry is not None and not conflict and os.path.isfile(overlay_path):
        try:
            current_root = packed_xml.read_packed_xml(_read_file(overlay_path))
            current = _find_value(current_root, field_path)
        except (IOError, OSError, TypeError, ValueError) as error:
            conflict = "Conflict: the installed overlay is unreadable: %s" % error

    return {
        "member": member,
        "fieldPath": field_path,
        "originalValue": _scalar_text(original),
        "currentValue": _scalar_text(current),
        "packedType": _TYPE_NAMES.get(original.value_type, "unknown"),
        "constraint": rule["description"],
        "overlayPath": overlay_path,
        "conflict": conflict,
    }


def apply_vehicle_edit(game_root, member, field_path, replacement_value,
                       is_running=None):
    """Merge one edit, rebuild every owned member, and commit atomically."""
    status, package_path = _require_target(
        game_root, require_closed=True, is_running=is_running)
    rule = _field_rule(member, field_path)
    unused_data, source_root = _read_source_member(package_path, member)
    original = _find_value(source_root, field_path)
    _validate_original(original, rule)
    replacement, manifest_value = _parse_replacement(
        replacement_value, original, rule)
    if replacement.value_type != original.value_type:
        raise VehicleOverlayError("The replacement changed the Packed type.")

    manifest, unused_exists = _load_manifest(status["path"])
    old_entries = _entry_map(manifest)
    entries = copy.deepcopy(old_entries)
    entry = entries.get(member)
    if entry is None:
        entry = {
            "sourcePackage": SOURCE_PACKAGE,
            "sourceMember": member,
            "overlayRelativePath": member,
            # Filled after the complete member has been rebuilt.
            "overlaySha256": "0" * 64,
            "edits": [],
        }
        entries[member] = entry
    edits = dict((edit["fieldPath"], edit) for edit in entry["edits"])
    existing = edits.get(field_path)
    original_type = _TYPE_NAMES.get(original.value_type, "unknown")
    original_value = _manifest_scalar(original)
    if existing is not None:
        if (existing.get("originalPackedType") != original_type or
                not _same_recorded_value(
                    existing.get("originalValue"), original_value,
                    original.value_type)):
            raise VehicleOverlayError(
                "The original package contract changed for this saved edit.")
    edits[field_path] = {
        "fieldPath": field_path,
        "originalPackedType": original_type,
        "originalValue": original_value,
        "replacementValue": manifest_value,
        "constraint": rule["description"],
    }
    entry["edits"] = sorted(
        edits.values(), key=lambda item: item["fieldPath"])

    _assert_owned_files_safe(status["path"], old_entries, entries)
    rebuilt = {}
    for owned_member in sorted(entries):
        output, normalized_edits = _build_member(
            package_path, entries[owned_member])
        entries[owned_member]["edits"] = normalized_edits
        entries[owned_member]["overlaySha256"] = _sha256(output)
        rebuilt[owned_member] = output

    manifest["members"] = [entries[name] for name in sorted(entries)]
    manifest["updatedAt"] = _now()
    _validate_manifest(manifest)
    writes = [(_overlay_path(status["path"], name), rebuilt[name])
              for name in sorted(rebuilt)]
    # The ownership record is installed last.  Any ordinary failure restores
    # both earlier overlays and the previous manifest.
    writes.append((manifest_path(status["path"]), _manifest_bytes(manifest)))
    _transactional_write(status["path"], writes)
    return inspect_vehicle_field(
        status["path"], member, field_path)


def restore_vehicle_defaults(game_root, is_running=None):
    """Remove only complete members proven to be owned by this manifest."""
    status, unused_package = _require_target(
        game_root, require_closed=True, is_running=is_running)
    manifest, exists = _load_manifest(status["path"])
    if not exists:
        return 0
    entries = _entry_map(manifest)
    for member, entry in entries.items():
        problem = _ownership_problem(status["path"], member, entry)
        if problem and not problem.startswith("Owned overlay is missing"):
            raise VehicleOverlayError(problem)

    try:
        transaction_root = tempfile.mkdtemp(
            prefix=".wot-vehicle-restore-", dir=status["path"])
    except (IOError, OSError) as error:
        raise VehicleOverlayError(
            "Default restoration could not start: %s" % error)
    moved = []
    preserve_recovery = False
    try:
        targets = [(_overlay_path(status["path"], member), member)
                   for member in sorted(entries)]
        targets.append((manifest_path(status["path"]), MANIFEST_NAME))
        _write_staged(
            os.path.join(transaction_root, "recovery.json"),
            _recovery_bytes(
                status["path"], "restore-defaults",
                [target for target, unused_name in targets]))
        for index, (target, unused_name) in enumerate(targets):
            if not os.path.lexists(target):
                continue
            backup = os.path.join(transaction_root, "backup-%d" % index)
            os.replace(target, backup)
            moved.append((target, backup))
    except Exception as error:
        rollback_errors = []
        for target, backup in reversed(moved):
            if os.path.lexists(backup):
                try:
                    directory = os.path.dirname(target)
                    if not os.path.isdir(directory):
                        os.makedirs(directory)
                    os.replace(backup, target)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            else:
                rollback_errors.append("A restoration backup is missing.")
        if rollback_errors:
            preserve_recovery = True
            raise VehicleOverlayError(
                "Default restoration failed and automatic rollback was "
                "incomplete. Recovery files were kept in %s: %s" %
                (transaction_root, "; ".join(rollback_errors)))
        raise VehicleOverlayError(
            "Default restoration was rolled back: %s" % error)
    finally:
        if not preserve_recovery:
            shutil.rmtree(transaction_root, ignore_errors=True)
    return len(entries)
