from __future__ import print_function

"""Passive optional-device, food and crew-perk modifiers.

This is the reviewed 0.8.2 law from ``offline_battle.py`` (the block that
builds ``_gun_state`` from the garage item), moved into a pure-data module so
it can be tested without an engine and reused by the gun, spotting and repair
paths.

The 0.8.2 law matches lowercased substrings of the descriptor's own item names
rather than canonical ids, so it keeps working across the nation-specific and
class-tiered variants #1513 ships (``improvedVentilation_class1`` and the
per-nation rations, for example).

Two properties of the 0.8.2 law are deliberate and preserved:

- a plain 100% crew already yields ``crew_multiplier`` 0.952, because the law
  converts crew level through ``1 / (0.5 + 0.005 * effective_level)`` and a
  bare crew reaches ``effective_level`` 110;
- Brothers in Arms counts only when *every* crew member carries it.

Coated optics, toolbox, wet ammo rack and similar devices are not matched by
name here: #1513 folds them into ``descriptor.miscAttrs``, which the spotting
and repair paths already read.
"""


BASE_CREW_LEVEL = 100.0
COMMANDER_SHARE = 0.1
VENTILATION_CREW_BONUS = 5.0
BROTHERHOOD_CREW_BONUS = 5.0
RATION_CREW_BONUS = 10.0

RAMMER_RELOAD_FACTOR = 0.9
# 0.8.2 divides by 1.1 rather than multiplying by 0.9; keep its exact value.
AIM_DRIVE_DIVISOR = 1.1
STABILISER_BLOOM_FACTOR = 0.8
SNAP_SHOT_TURRET_FACTOR = 0.925
SMOOTH_RIDE_MOVE_FACTOR = 0.96

_RATION_MARKERS = ('ration', 'chocolate', 'cola', 'coffee', 'pudding',
                   'stimulator', 'improvedcombatrations', 'extrarations',
                   'strongcoffee', 'buchty', 'onigiri', 'gulaschkanone')
_BROTHERHOOD_MARKERS = ('brotherhood',)
_SNAP_SHOT_MARKERS = ('smoothturret', 'snapshot')
_SMOOTH_RIDE_MARKERS = ('smoothdriving', 'smoothride')
_SIXTH_SENSE_MARKERS = ('sixthsense',)


def _name_of(value):
    name = getattr(value, 'name', None)
    if not name:
        descriptor = getattr(value, 'descriptor', None)
        name = getattr(descriptor, 'name', None)
    if not name:
        name = value
    try:
        return str(name).lower()
    except Exception:
        return ''


def _matches(name, markers):
    return any(marker in name for marker in markers)


def device_names(descriptor):
    """Lowercased names of every optional device mounted on a descriptor."""
    result = []
    for device in (getattr(descriptor, 'optionalDevices', None) or ()):
        if device is None:
            continue
        name = _name_of(device)
        if name:
            result.append(name)
    return tuple(result)


def equipment_names(equipments):
    """Lowercased names of mounted consumables, skipping empty slots."""
    result = []
    for equipment in (equipments or ()):
        if not equipment:
            continue
        name = _name_of(equipment)
        if name:
            result.append(name)
    return tuple(result)


def crew_skill_names(crew):
    """One lowercased skill-name tuple per crew member.

    A member that cannot be read contributes an empty tuple, which clears
    Brothers in Arms exactly as the 0.8.2 law does for a missing crewman.
    """
    result = []
    for member in (crew or ()):
        if isinstance(member, tuple) and len(member) == 2:
            member = member[1]
        if member is None:
            result.append(())
            continue
        skills = getattr(member, 'skills', None)
        if skills is None:
            descriptor = getattr(member, 'descriptor', None)
            skills = getattr(descriptor, 'skills', None)
        names = []
        for skill in (skills or ()):
            name = _name_of(skill)
            if name:
                names.append(name)
        result.append(tuple(names))
    return tuple(result)


def modifiers(descriptor=None, equipments=(), crew_skills=None):
    """Return the passive modifier bundle for one vehicle loadout.

    ``crew_skills`` is the per-member skill-name sequence from
    ``crew_skill_names``; ``None`` means the crew is unknown, which keeps the
    bare-crew baseline instead of claiming Brothers in Arms.
    """
    devices = device_names(descriptor) if descriptor is not None else ()
    consumables = equipment_names(equipments)

    has_rammer = any('rammer' in name for name in devices)
    has_aim_drives = any('aimdrives' in name for name in devices)
    has_ventilation = any('ventilation' in name for name in devices)
    has_stabiliser = any('stabilizer' in name for name in devices)
    has_rations = any(_matches(name, _RATION_MARKERS) for name in consumables)

    has_brotherhood = False
    has_snap_shot = False
    has_smooth_ride = False
    has_sixth_sense = False
    if crew_skills:
        has_brotherhood = True
        for names in crew_skills:
            if not any(_matches(name, _BROTHERHOOD_MARKERS)
                       for name in names):
                has_brotherhood = False
            if any(_matches(name, _SNAP_SHOT_MARKERS) for name in names):
                has_snap_shot = True
            if any(_matches(name, _SMOOTH_RIDE_MARKERS) for name in names):
                has_smooth_ride = True
            if any(_matches(name, _SIXTH_SENSE_MARKERS) for name in names):
                has_sixth_sense = True

    crew_level = BASE_CREW_LEVEL
    commander_level = BASE_CREW_LEVEL
    if has_ventilation:
        crew_level += VENTILATION_CREW_BONUS
        commander_level += VENTILATION_CREW_BONUS
    if has_brotherhood:
        crew_level += BROTHERHOOD_CREW_BONUS
        commander_level += BROTHERHOOD_CREW_BONUS
    if has_rations:
        crew_level += RATION_CREW_BONUS
        commander_level += RATION_CREW_BONUS
    effective_level = crew_level + commander_level * COMMANDER_SHARE
    crew_multiplier = 1.0 / (0.5 + 0.005 * effective_level)

    reload_factor = RAMMER_RELOAD_FACTOR if has_rammer else 1.0
    aim_time_factor = (1.0 / AIM_DRIVE_DIVISOR) if has_aim_drives else 1.0
    move_factor = 1.0
    rotation_factor = 1.0
    turret_factor = 1.0
    if has_stabiliser:
        move_factor *= STABILISER_BLOOM_FACTOR
        rotation_factor *= STABILISER_BLOOM_FACTOR
        turret_factor *= STABILISER_BLOOM_FACTOR
    if has_snap_shot:
        turret_factor *= SNAP_SHOT_TURRET_FACTOR
    if has_smooth_ride:
        move_factor *= SMOOTH_RIDE_MOVE_FACTOR

    return {
        'crew_level': crew_level,
        'commander_level': commander_level,
        'effective_crew_level': effective_level,
        'crew_multiplier': crew_multiplier,
        'reload_factor': reload_factor,
        'aim_time_factor': aim_time_factor,
        'bloom_move_factor': move_factor,
        'bloom_rotation_factor': rotation_factor,
        'bloom_turret_factor': turret_factor,
        'has_rammer': has_rammer,
        'has_aim_drives': has_aim_drives,
        'has_ventilation': has_ventilation,
        'has_stabiliser': has_stabiliser,
        'has_rations': has_rations,
        'has_brotherhood': has_brotherhood,
        'has_snap_shot': has_snap_shot,
        'has_smooth_ride': has_smooth_ride,
        'has_sixth_sense': has_sixth_sense,
    }


def baseline():
    """The bare-crew bundle used when no loadout is known."""
    return modifiers()
