from __future__ import print_function

"""Project #1513 vehicle data into plain JSON for the server authority.

The server process cannot read the client's item definitions. One connected
client donates two artifacts instead: the eligible vehicle catalog
(name/level/tags, sent once after joining) and, on server request, full
descriptor projections for the vehicles one battle actually uses. Every
value is a plain JSON type; interpretation of #1513 data stays here.
"""

import math


def _value(source, name, default=None):
    """Read a #1513 component attribute or mapping key."""
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _json_safe(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value != value or abs(value) == float('inf'):
            return None
        return value
    if isinstance(value, str):
        return value
    try:
        text_types = (unicode,)
    except NameError:
        text_types = ()
    if isinstance(value, text_types):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[str(key)] = _json_safe(item, depth + 1)
        return result
    if value is None or isinstance(value, bool):
        return value
    # #1513 readers store Math.Vector2/Vector3 (readVector2/readVector3 in
    # items/vehicles.pyc); vectors iterate like fixed-size sequences.
    try:
        items = list(value)
    except Exception:
        return None
    return [_json_safe(item, depth + 1) for item in items]


def _copy_fields(source, names):
    result = {}
    for name in names:
        value = _value(source, name)
        if value is None:
            continue
        safe = _json_safe(value)
        if safe is not None:
            result[name] = safe
    return result


def _hit_tester_bbox(component):
    tester = _value(component, 'hitTester')
    if tester is None:
        return None
    bbox = getattr(tester, 'bbox', None)
    if bbox is None:
        load = getattr(tester, 'loadBspModel', None)
        if callable(load):
            try:
                load()
            except Exception:
                return None
            bbox = getattr(tester, 'bbox', None)
    if bbox is None or len(bbox) < 2:
        return None
    try:
        minimum = [float(bbox[0][index]) for index in range(3)]
        maximum = [float(bbox[1][index]) for index in range(3)]
    except (TypeError, ValueError, IndexError):
        return None
    return [minimum, maximum, None]


_GUN_FIELDS = (
    'reloadTime', 'clip', 'turretYawLimits', 'pitchLimits',
    'rotationSpeed', 'shotDispersionAngle', 'shotDispersionFactors',
    'aimingTime', 'maxAmmo', 'maxHealth', 'maxRegenHealth', 'burst',
)
_SHOT_FIELDS = (
    'speed', 'gravity', 'maxDistance', 'piercingPower',
)
_SHELL_FIELDS = (
    'kind', 'caliber', 'damage', 'explosionRadius', 'piercingPower',
    'effectsIndex', 'isTracer',
)
_TURRET_FIELDS = (
    'rotationSpeed', 'circularVisionRadius', 'primaryArmor', 'maxHealth',
    'maxRegenHealth', 'turretRotatorHealth', 'surveyingDeviceHealth',
    'invisibilityFactor', 'yawLimits',
)
_CHASSIS_FIELDS = (
    'hullPosition', 'rotationSpeed', 'shotDispersionFactors',
    'maxHealth', 'maxRegenHealth', 'terrainResistance',
)
_HULL_FIELDS = (
    'turretPositions', 'primaryArmor', 'maxHealth', 'maxRegenHealth',
    'ammoBayHealth',
)
_COMPONENT_HEALTH_FIELDS = ('maxHealth', 'maxRegenHealth')
_TYPE_FIELDS = ('invisibility', 'invisibilityFactorAtShot', 'crewRoles')


def project_descriptor(descriptor):
    """Return one vehicle's JSON projection for the server authority."""
    vehicle_type = _value(descriptor, 'type', descriptor)
    projection = {
        'name': str(_value(vehicle_type, 'name', '') or ''),
        'level': int(_value(vehicle_type, 'level', 1) or 1),
        'tags': sorted(str(tag) for tag in
                       (_value(vehicle_type, 'tags', ()) or ())),
        'maxHealth': int(_value(descriptor, 'maxHealth', 1) or 1),
    }
    projection.update(_copy_fields(vehicle_type, _TYPE_FIELDS))
    gun = _value(descriptor, 'gun', {})
    gun_projection = _copy_fields(gun, _GUN_FIELDS)
    shots = []
    for shot in (_value(gun, 'shots', ()) or ()):
        shot_projection = _copy_fields(shot, _SHOT_FIELDS)
        shell = _value(shot, 'shell', {})
        shell_projection = _copy_fields(shell, _SHELL_FIELDS)
        # #1513 stores the shell kind as shell.type (a ShellType whose .name
        # is the kind string) and explosionRadius on the HighExplosive type.
        shell_type = _value(shell, 'type')
        if 'kind' not in shell_projection:
            kind = _value(shell_type, 'name')
            if kind:
                shell_projection['kind'] = str(kind)
        if 'explosionRadius' not in shell_projection:
            radius = _json_safe(_value(shell_type, 'explosionRadius'))
            if radius is not None:
                shell_projection['explosionRadius'] = radius
        shot_projection['shell'] = shell_projection
        shots.append(shot_projection)
    gun_projection['shots'] = shots
    projection['gun'] = gun_projection
    turret = _value(descriptor, 'turret', {})
    projection['turret'] = _copy_fields(turret, _TURRET_FIELDS)
    physics = _value(descriptor, 'physics', {}) or {}
    projection['physics'] = _json_safe(dict(
        (str(key), physics[key]) for key in physics) if
        isinstance(physics, dict) else {}) or {}
    chassis = _value(descriptor, 'chassis', {})
    chassis_projection = _copy_fields(chassis, _CHASSIS_FIELDS)
    bbox = _hit_tester_bbox(chassis)
    if bbox is not None:
        chassis_projection['hitTester'] = {'bbox': bbox}
    projection['chassis'] = chassis_projection
    hull = _value(descriptor, 'hull', {})
    hull_projection = _copy_fields(hull, _HULL_FIELDS)
    bbox = _hit_tester_bbox(hull)
    if bbox is None:
        raise ValueError('descriptor hull bbox is unavailable')
    hull_projection['hitTester'] = {'bbox': bbox}
    projection['hull'] = hull_projection
    for component_name in ('engine', 'fuelTank', 'radio'):
        component = _value(descriptor, component_name)
        if component is None:
            continue
        values = _copy_fields(component, _COMPONENT_HEALTH_FIELDS)
        if values:
            projection[component_name] = values
    if not projection['name']:
        raise ValueError('descriptor has no type name')
    if not shots:
        raise ValueError('descriptor has no gun shots')
    return projection


def vehicle_catalog(runtime):
    """Return the eligible vehicle catalog as plain JSON rows."""
    rows = []
    nations = runtime.nations
    vehicle_list = runtime.vehicles.g_list
    for nation in nations.AVAILABLE_NAMES:
        nation_id = nations.INDICES[nation]
        values = vehicle_list.getList(nation_id)
        iterator = getattr(values, 'itervalues', None)
        entries = iterator() if callable(iterator) else values.values()
        for entry in entries:
            name = str(_value(entry, 'name', '') or '')
            if not name:
                continue
            try:
                level = int(_value(entry, 'level', 1) or 1)
            except (TypeError, ValueError):
                continue
            rows.append({
                'name': name,
                'level': level,
                'tags': sorted(str(tag) for tag in
                               (_value(entry, 'tags', ()) or ())),
            })
    rows.sort(key=lambda row: row['name'])
    return rows


def project_vehicles(runtime, names, failures=None, fittings=None):
    """Build requested projections and optionally report every failed name.

    ``fittings`` maps a type name to a mounted compact descriptor, so the
    server measures the tank the owner actually fitted instead of the stock
    one.
    """
    projections = {}
    for name in names:
        name = str(name)
        try:
            fitting = (fittings or {}).get(name)
            if fitting is None:
                descriptor = runtime.vehicles.VehicleDescr(typeName=name)
            else:
                descriptor = runtime.vehicles.VehicleDescr(
                    compactDescr=fitting)
            projections[name] = project_descriptor(descriptor)
        except Exception:
            if failures is not None:
                failures.append(name)
            continue
    return projections
