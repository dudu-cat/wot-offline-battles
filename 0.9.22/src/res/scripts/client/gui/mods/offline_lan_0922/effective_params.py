from __future__ import print_function

"""Canonical wire contract for client-derived effective vehicle parameters.

The garage client is the only endpoint that owns the mounted crew,
consumables, optional devices and ammunition.  It therefore publishes the
final values computed by the exact #1513 item pipeline.  The server and the
hidden native worker validate and relay this immutable round input; neither
endpoint reconstructs a second loadout from a bare descriptor.

This module intentionally depends only on the standard library and remains
importable on both the embedded Python 2 client and the Python 3 server.
"""

import math


SCHEMA_VERSION = 1
CAPABILITY = 'effective_params_v1'

_LOADOUT_FLOATS = (
    'crew_level', 'commander_level', 'effective_crew_level',
    'crew_multiplier', 'crew_factor', 'gun_rotation_factor',
    'reload_factor', 'aim_time_factor', 'dispersion_factor',
    'repair_factor', 'vehicle_rotation_factor', 'radio_factor',
    'bloom_move_factor', 'bloom_rotation_factor',
    'bloom_turret_factor',
)
_LOADOUT_BOOLS = (
    'has_big_kit', 'from_client_factors', 'has_rammer',
    'has_aim_drives', 'has_ventilation', 'has_stabiliser', 'has_rations',
    'has_brotherhood', 'has_snap_shot', 'has_smooth_ride',
    'has_sixth_sense',
)
_LOADOUT_KEYS = frozenset(
    _LOADOUT_FLOATS + _LOADOUT_BOOLS + ('terrain_resistance_factors',))

_PHYSICS_FLOATS = (
    'mass', 'powerW', 'speedFwd', 'speedBwd', 'rotSpd',
    'specificFriction', 'brakeDecel', 'trackCenter', 'minPlaneNormalY',
    'nativePowerRatio',
)
_PHYSICS_KEYS = frozenset(_PHYSICS_FLOATS + ('terrainResist',))

_SPOTTING_FLOATS = (
    'commander_level', 'recon_level', 'situational_level',
    'camouflage_level', 'binocular_factor', 'binocular_delay',
    'camouflage_net_bonus', 'camouflage_net_delay', 'vision_factor',
    'camouflage_factor',
)
_SPOTTING_BOOLS = (
    'has_binoculars', 'has_camouflage_net', 'from_client_factors',
)
_SPOTTING_KEYS = frozenset(
    _SPOTTING_FLOATS + _SPOTTING_BOOLS +
    ('invisibility_moving', 'invisibility_still'))

_RAMMING_KEYS = frozenset(('spall_coefficient', 'ramming_bonus'))
_CAMOUFLAGE_KEYS = frozenset(
    ('camouflage_id', 'base_moving', 'base_still', 'shot_factor'))
_SKILL_KEYS = frozenset(('deadeye', 'intuition_chances'))
_GUN_KEYS = frozenset(('clip_size', 'shots'))
_GUN_SHOT_KEYS = frozenset(('compact_descr', 'source_shot'))
_SOURCE_SHOT_KEYS = frozenset((
    'speed', 'gravity', 'maxDistance', 'piercingPower', 'deadeye', 'shell'))
_SOURCE_SHELL_KEYS = frozenset((
    'kind', 'caliber', 'damage', 'explosionRadius'))
_PROJECTILE_SHELL_KINDS = frozenset((
    'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE', 'ARMOR_PIERCING',
    'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR'))
_TOP_LEVEL_KEYS = frozenset((
    'version', 'loadout', 'physics', 'spotting', 'ramming', 'ammo',
    'camouflage', 'skills', 'gun'))


try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


def _exact_int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, integer_types):
        return None
    value = int(value)
    return value if minimum <= value <= maximum else None


def _number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    return value if minimum <= value <= maximum else None


def _bool(value):
    return value if isinstance(value, bool) else None


def _tuple(value, size, minimum, maximum):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return None
    result = []
    for entry in value:
        entry = _number(entry, minimum, maximum)
        if entry is None:
            return None
        result.append(entry)
    return result


def _mapping(value, keys):
    return isinstance(value, dict) and set(value) == keys


def _canonical_loadout(value):
    if not _mapping(value, _LOADOUT_KEYS):
        return None
    result = {}
    for name in _LOADOUT_FLOATS:
        maximum = 1000.0 if name.endswith('_level') else 100.0
        number = _number(value.get(name), 0.0, maximum)
        if number is None:
            return None
        result[name] = number
    for name in _LOADOUT_BOOLS:
        flag = _bool(value.get(name))
        if flag is None:
            return None
        result[name] = flag
    if not result['from_client_factors']:
        return None
    terrain = _tuple(
        value.get('terrain_resistance_factors'), 3, 0.000001, 1000.0)
    if terrain is None:
        return None
    result['terrain_resistance_factors'] = terrain
    return result


def _canonical_physics(value):
    if not _mapping(value, _PHYSICS_KEYS):
        return None
    bounds = {
        'mass': (1.0, 1000000.0),
        'powerW': (1.0, 1000000000.0),
        'speedFwd': (0.0, 1000.0),
        'speedBwd': (0.0, 1000.0),
        'rotSpd': (0.0, 100.0),
        'specificFriction': (0.0, 1000.0),
        'brakeDecel': (0.0, 10000.0),
        'trackCenter': (0.01, 100.0),
        'minPlaneNormalY': (-1.0, 1.0),
        'nativePowerRatio': (0.000001, 1000.0),
    }
    result = {}
    for name in _PHYSICS_FLOATS:
        number = _number(value.get(name), *bounds[name])
        if number is None:
            return None
        result[name] = number
    terrain = _tuple(value.get('terrainResist'), 3, 0.000001, 1000000.0)
    if terrain is None:
        return None
    result['terrainResist'] = terrain
    return result


def _canonical_spotting(value):
    if not _mapping(value, _SPOTTING_KEYS):
        return None
    result = {}
    for name in _SPOTTING_FLOATS:
        maximum = 1000.0 if name.endswith('_level') else 100.0
        number = _number(value.get(name), 0.0, maximum)
        if number is None:
            return None
        result[name] = number
    for name in _SPOTTING_BOOLS:
        flag = _bool(value.get(name))
        if flag is None:
            return None
        result[name] = flag
    if not result['from_client_factors']:
        return None
    for name in ('invisibility_moving', 'invisibility_still'):
        pair = _tuple(value.get(name), 2, -100.0, 100.0)
        if pair is None or pair[1] < 0.0:
            return None
        result[name] = pair
    return result


def _canonical_ramming(value):
    if not _mapping(value, _RAMMING_KEYS):
        return None
    spall = _number(value.get('spall_coefficient'), 1.0, 100.0)
    bonus = _number(value.get('ramming_bonus'), 0.0, 0.15)
    if spall is None or bonus is None:
        return None
    return {'spall_coefficient': spall, 'ramming_bonus': bonus}


def _canonical_ammo(value):
    if not isinstance(value, (list, tuple)) or len(value) > 64:
        return None
    result = []
    previous = -1
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            return None
        compact_descr = _exact_int(entry[0], 1, 4294967295)
        count = _exact_int(entry[1], 0, 1000000)
        if compact_descr is None or count is None or compact_descr <= previous:
            return None
        previous = compact_descr
        result.append([compact_descr, count])
    return result


def _canonical_camouflage(value):
    if not _mapping(value, _CAMOUFLAGE_KEYS):
        return None
    camouflage_id = value.get('camouflage_id')
    if camouflage_id is not None:
        camouflage_id = _exact_int(camouflage_id, 0, 4294967295)
        if camouflage_id is None:
            return None
    moving = _number(value.get('base_moving'), 0.0, 100.0)
    still = _number(value.get('base_still'), 0.0, 100.0)
    shot = _number(value.get('shot_factor'), 0.0, 1.0)
    if moving is None or still is None or shot is None:
        return None
    return {
        'camouflage_id': camouflage_id,
        'base_moving': moving,
        'base_still': still,
        'shot_factor': shot,
    }


def _canonical_skills(value):
    if not _mapping(value, _SKILL_KEYS):
        return None
    deadeye = _bool(value.get('deadeye'))
    intuition = _exact_int(value.get('intuition_chances'), 0, 16)
    if deadeye is None or intuition is None:
        return None
    return {'deadeye': deadeye, 'intuition_chances': intuition}


def _canonical_source_shot(value):
    """Freeze one exact mounted-shell law for worker-side resolution."""
    if not _mapping(value, _SOURCE_SHOT_KEYS):
        return None
    shell = value.get('shell')
    if not _mapping(shell, _SOURCE_SHELL_KEYS):
        return None
    kind = shell.get('kind')
    deadeye = _bool(value.get('deadeye'))
    if (not isinstance(kind, string_types) or
            kind not in _PROJECTILE_SHELL_KINDS or deadeye is None):
        return None
    speed = _number(value.get('speed'), 0.000001, 3000.0)
    gravity = _number(value.get('gravity'), 0.000001, 500.0)
    maximum = _number(value.get('maxDistance'), 0.000001, 10000.0)
    piercing = _tuple(value.get('piercingPower'), 2, 0.0, 10000.0)
    caliber = _number(shell.get('caliber'), 0.000001, 1000.0)
    damage = _tuple(shell.get('damage'), 2, 0.0, 10000.0)
    radius = _number(shell.get('explosionRadius'), 0.0, 100.0)
    if (speed is None or gravity is None or maximum is None or
            piercing is None or caliber is None or damage is None or
            damage[0] <= 0.0 or radius is None):
        return None
    return {
        'speed': speed,
        'gravity': gravity,
        'maxDistance': maximum,
        'piercingPower': piercing,
        'deadeye': deadeye,
        'shell': {
            'kind': kind,
            'caliber': caliber,
            'damage': damage,
            'explosionRadius': radius,
        },
    }


def _canonical_gun(value):
    """Validate shot order and clip shape donated by the mounted gun."""
    if not _mapping(value, _GUN_KEYS):
        return None
    clip_size = _exact_int(value.get('clip_size'), 1, 255)
    shots = value.get('shots')
    if (clip_size is None or not isinstance(shots, (list, tuple)) or
            not 1 <= len(shots) <= 64):
        return None
    result = []
    compact_descrs = set()
    for entry in shots:
        if not _mapping(entry, _GUN_SHOT_KEYS):
            return None
        compact_descr = _exact_int(
            entry.get('compact_descr'), 1, 4294967295)
        source_shot = _canonical_source_shot(entry.get('source_shot'))
        if (compact_descr is None or compact_descr in compact_descrs or
                source_shot is None):
            return None
        compact_descrs.add(compact_descr)
        result.append({
            'compact_descr': compact_descr,
            'source_shot': source_shot,
        })
    return {'clip_size': clip_size, 'shots': result}


def canonical(value):
    """Return a detached canonical snapshot, or ``None`` when invalid."""
    if not _mapping(value, _TOP_LEVEL_KEYS):
        return None
    if _exact_int(value.get('version'), 1, 1) != SCHEMA_VERSION:
        return None
    loadout = _canonical_loadout(value.get('loadout'))
    physics = _canonical_physics(value.get('physics'))
    spotting = _canonical_spotting(value.get('spotting'))
    ramming = _canonical_ramming(value.get('ramming'))
    ammo = _canonical_ammo(value.get('ammo'))
    camouflage = _canonical_camouflage(value.get('camouflage'))
    skills = _canonical_skills(value.get('skills'))
    gun = _canonical_gun(value.get('gun'))
    if any(entry is None for entry in (
            loadout, physics, spotting, ramming, ammo, camouflage, skills,
            gun)):
        return None
    if any(shot['source_shot']['deadeye'] != skills['deadeye']
           for shot in gun['shots']):
        return None
    return {
        'version': SCHEMA_VERSION,
        'loadout': loadout,
        'physics': physics,
        'spotting': spotting,
        'ramming': ramming,
        'ammo': ammo,
        'camouflage': camouflage,
        'skills': skills,
        'gun': gun,
    }
