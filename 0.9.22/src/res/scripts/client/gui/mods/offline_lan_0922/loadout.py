from __future__ import print_function

"""One dataset for every crew and artefact effect the battle law applies.

``attribute_factors`` runs the exact #1513 chain the garage panel runs -
``items.utils.updateAttrFactorsWithSplit`` over the mounted descriptor, the
crew compact descriptors and the mounted consumables - and returns the same
``factors`` dictionary ``VehicleParameters`` reads.  Every battle consumer
takes its crew and artefact contribution from that dictionary, so the panel
and the battle cannot drift apart.

Off the client (the Python 3 test harness) ``attribute_factors`` returns None
and the name-matching bundle below stays in charge.  That bundle reproduces
#1513's own curve, ``factor = 0.57 + 0.43 * efficiency``, rather than the
0.8.2 approximation, and reads the artefact strengths from
``descriptor.miscAttrs`` instead of inferring them from a device name.

Brothers in Arms still counts only when every crew member carries it.
"""


import sys

try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)

# VehicleDescrCrew._processSkills: factor = 0.57 + 0.43 * (level / 100).
CREW_FACTOR_BASE = 0.57
CREW_FACTOR_SLOPE = 0.0043
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
_BIG_REPAIR_KIT_MARKERS = ('largerepairkit',)


def _client_modules():
    """Return ``(items.utils, items.tankmen, VEHICLE_TTC_ASPECTS)`` or None."""
    try:
        from constants import VEHICLE_TTC_ASPECTS
        from items import tankmen
        from items import utils
    except Exception:
        return None
    return utils, tankmen, VEHICLE_TTC_ASPECTS


def crew_compact_descrs(crew):
    """Compact descriptors for a garage crew, in ``crewRoles`` order.

    ``VehicleDescrCrew`` wants one compact descriptor per role, so an empty
    slot is dropped rather than filled: a partial crew makes the whole chain
    unusable and the caller falls back to the default crew.
    """
    members = list(crew or ())
    if members and all(isinstance(member, tuple) and len(member) == 2
                       for member in members):
        # items_parameters.functions.extractCrewDescrs sorts by slot index.
        members = [member[1] for member in
                   sorted(members, key=lambda entry: entry[0])]
    result = []
    for member in members:
        if member is None:
            return ()
        value = getattr(member, 'strCD', None)
        if value is None:
            descriptor = getattr(member, 'descriptor', None)
            maker = getattr(descriptor, 'makeCompactDescr', None)
            if maker is not None:
                try:
                    value = maker()
                except Exception:
                    value = None
        if value is None:
            return ()
        result.append(value)
    return tuple(result)


def _artefact(value, vehicles_module):
    """Resolve one mounted item to the ``items.artefacts`` object #1513 uses."""
    if value is None:
        return None
    if isinstance(value, _INTEGER_TYPES):
        # #1513 FittingItem.__eq__ reads other.intCD unguarded, so comparing a
        # mounted gui item against 0 raises instead of answering.
        if not value:
            return None
        try:
            return vehicles_module.getItemByCompactDescr(int(value))
        except Exception:
            return None
    if hasattr(value, 'updateVehicleAttrFactors'):
        return value
    descriptor = getattr(value, 'descriptor', None)
    if hasattr(descriptor, 'updateVehicleAttrFactors'):
        return descriptor
    return None


def attribute_factors(descriptor, crew=None, equipments=()):
    """Return the exact #1513 ``factors`` dictionary for one loadout.

    This is the same chain ``VehicleParameters`` runs for the garage panel:
    the mounted descriptor supplies the optional devices, the crew supplies
    every role and common skill, and the mounted consumables supply their own
    factors and crew-level increase.  ``None`` means the client modules are
    unavailable, which is the Python 3 test harness, not a battle.
    """
    modules = _client_modules()
    if modules is None or descriptor is None:
        return None
    utils, tankmen, aspects = modules
    try:
        from items import vehicles as vehicles_module
    except Exception:
        return None
    compact_descrs = crew_compact_descrs(crew)
    try:
        roles = descriptor.type.crewRoles
        if len(compact_descrs) != len(roles):
            compact_descrs = utils.generateDefaultCrew(
                descriptor.type, tankmen.MAX_SKILL_LEVEL)
        mounted = []
        for equipment in (equipments or ()):
            artefact = _artefact(equipment, vehicles_module)
            if artefact is not None:
                mounted.append(artefact)
        factors = utils.makeDefaultVehicleAttributeFactors()
        utils.updateAttrFactorsWithSplit(
            descriptor, list(compact_descrs), mounted, factors)
    except Exception as error:
        sys.stdout.write(
            '[Offline LAN 0.9.22] vehicle attribute factors unavailable: '
            '%s\n' % (error,))
        return None
    factors['_aspects'] = (aspects.DEFAULT, aspects.WHEN_STILL)
    return factors


def _factor(factors, name, default=1.0):
    try:
        return float(factors[name])
    except (KeyError, TypeError, ValueError):
        return float(default)


def invisibility_pair(factors, still):
    """Return the additive and multiplicative invisibility factors.

    #1513 keeps the camouflage net in the ``WHEN_STILL`` aspect only, so the
    caller picks the aspect its own stationary gate has already decided.
    """
    default = (0.0, 1.0)
    if not factors:
        return default
    try:
        aspect = factors['_aspects'][1 if still else 0]
        values = factors['invisibility'][aspect]
        return float(values[0]), float(values[1])
    except (KeyError, IndexError, TypeError, ValueError):
        return default


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


def _misc_factor(misc, name, fallback):
    """Read one descriptor factor, falling back when #1513 omits it."""
    try:
        value = float(misc.get(name, None))
    except (AttributeError, TypeError, ValueError):
        return float(fallback)
    return value if value > 0.0 else float(fallback)


def modifiers(descriptor=None, equipments=(), crew_skills=None, factors=None):
    """Return the passive modifier bundle for one vehicle loadout.

    ``crew_skills`` is the per-member skill-name sequence from
    ``crew_skill_names``; ``None`` means the crew is unknown, which keeps the
    bare-crew baseline instead of claiming Brothers in Arms.  ``factors`` is
    the dictionary ``attribute_factors`` built; when it is present every crew
    and artefact contribution below comes from it.
    """
    devices = device_names(descriptor) if descriptor is not None else ()
    consumables = equipment_names(equipments)

    has_rammer = any('rammer' in name for name in devices)
    has_aim_drives = any('aimdrives' in name for name in devices)
    has_ventilation = any('ventilation' in name for name in devices)
    has_stabiliser = any('stabilizer' in name for name in devices)
    has_rations = any(_matches(name, _RATION_MARKERS) for name in consumables)
    has_big_kit = any(_matches(name, _BIG_REPAIR_KIT_MARKERS)
                      for name in consumables)

    has_brotherhood = False
    has_snap_shot = False
    has_smooth_ride = False
    has_sixth_sense = False
    repair_share = 0.0
    if crew_skills:
        has_brotherhood = True
        for names in crew_skills:
            if any('repair' == name for name in names):
                repair_share += 1.0 / len(crew_skills)
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
    # #1513 builds one factor per crew role, 0.57 + 0.43 * efficiency, in
    # VehicleDescrCrew._processSkills.  Times divide by it and speeds multiply
    # by it, so both directions come from the same number.
    crew_factor = CREW_FACTOR_BASE + CREW_FACTOR_SLOPE * effective_level
    crew_multiplier = 1.0 / crew_factor

    # #1513 folds these into the descriptor, and the delux variants differ
    # from the plain ones (0.875 and 0.89), so read the value rather than
    # inferring one from the device name.
    # ``reload_factor``, ``aim_time_factor`` and ``dispersion_factor`` are the
    # COMPLETE multipliers on the gun's own descriptor values, crew included,
    # exactly like items.utils.getReloadTime and getGunAimingTime.
    misc = getattr(descriptor, 'miscAttrs', None) or {}
    reload_factor = crew_multiplier * _misc_factor(
        misc, 'gunReloadTimeFactor',
        RAMMER_RELOAD_FACTOR if has_rammer else 1.0)
    aim_time_factor = crew_multiplier * _misc_factor(
        misc, 'gunAimingTimeFactor',
        (1.0 / AIM_DRIVE_DIVISOR) if has_aim_drives else 1.0)
    dispersion_factor = crew_multiplier
    repair_factor = CREW_FACTOR_BASE + (1.0 - CREW_FACTOR_BASE) * repair_share
    gun_rotation_factor = crew_factor
    vehicle_rotation_factor = 1.0
    terrain_resistance_factors = (1.0, 1.0, 1.0)
    radio_factor = 1.0
    if factors:
        # Every crew and artefact contribution now comes from the dictionary
        # the garage panel reads, so the two sides cannot drift.
        crew_factor = _factor(factors, 'turret/rotationSpeed')
        crew_multiplier = 1.0 / max(crew_factor, 1e-6)
        gun_rotation_factor = _factor(factors, 'gun/rotationSpeed')
        reload_factor = (_misc_factor(misc, 'gunReloadTimeFactor', 1.0) *
                         _factor(factors, 'gun/reloadTime'))
        aim_time_factor = (_misc_factor(misc, 'gunAimingTimeFactor', 1.0) *
                           _factor(factors, 'gun/aimingTime'))
        try:
            dispersion_factor = float(factors['shotDispersion'][0])
        except (KeyError, IndexError, TypeError, ValueError):
            dispersion_factor = crew_multiplier
        repair_factor = _factor(factors, 'repairSpeed')
        vehicle_rotation_factor = _factor(factors, 'vehicle/rotationSpeed')
        radio_factor = _factor(factors, 'radio/distance')
        try:
            resistances = factors['chassis/terrainResistance']
            terrain_resistance_factors = (
                float(resistances[0]), float(resistances[1]),
                float(resistances[2]))
        except (KeyError, IndexError, TypeError, ValueError):
            terrain_resistance_factors = (1.0, 1.0, 1.0)
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
        'crew_factor': crew_factor,
        'gun_rotation_factor': gun_rotation_factor,
        'reload_factor': reload_factor,
        'aim_time_factor': aim_time_factor,
        'dispersion_factor': dispersion_factor,
        'repair_factor': repair_factor,
        'vehicle_rotation_factor': vehicle_rotation_factor,
        'terrain_resistance_factors': terrain_resistance_factors,
        'radio_factor': radio_factor,
        'has_big_kit': has_big_kit,
        'from_client_factors': bool(factors),
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


# The two situational devices are absent from ``miscAttrs``: #1513 gives
# ``Stereoscope`` and ``CamouflageNet`` only ``updateVehicleAttrFactors``, which
# writes a caller-owned factors dict this port never builds.  Coated optics uses
# ``updateVehicleDescrAttrs`` instead, so it IS already folded into
# ``miscAttrs['circularVisionRadiusFactor']`` and must not be applied twice.
_BINOCULAR_MARKERS = ('stereoscope',)
_CAMOUFLAGE_NET_MARKERS = ('camouflagenet',)
_RECON_SKILL = 'commander_eagleeye'
_SITUATIONAL_SKILL = 'radioman_finder'
_CAMOUFLAGE_SKILL = 'camouflage'
_INTUITION_SKILL = 'loader_intuition'
# tankmen.xml loader_intuition: one independent chance per finished perk.
INTUITION_CHANCE = 0.17
# tankmen.xml: commander_eagleEye distanceFactorPerLevelWhenDeviceWorking and
# radioman_finder visionRadiusFactorPerLevel.
RECON_FACTOR_PER_LEVEL = 0.0002
SITUATIONAL_FACTOR_PER_LEVEL = 0.0003


def _number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value


def _skill_level(member, wanted):
    """Return one crew member's level in a named skill, 0.0 when absent."""
    skills = getattr(member, 'skills', None)
    if skills is None:
        skills = getattr(getattr(member, 'descriptor', None), 'skills', None)
    for skill in (skills or ()):
        if _name_of(skill) != wanted:
            continue
        if not bool(getattr(skill, 'isActive', True)):
            return 0.0
        return max(0.0, _number(getattr(skill, 'level', 100.0), 100.0))
    return 0.0


def ramming_bonus(crew):
    """Return #1513 Controlled Impact's active 0.0--0.15 bonus.

    The pinned ``tankmen.xml`` stores 0.0015 per trained percentage point;
    the contemporaneous Wargaming Crew page documents the same progressive
    15 percent maximum.  Only the driver can own this named skill, so the
    highest mounted value is the vehicle value.
    """
    level = 0.0
    for member in (crew or ()):
        if isinstance(member, tuple) and len(member) == 2:
            member = member[1]
        if member is None:
            continue
        level = max(level, _skill_level(member, 'driver_rammingmaster'))
    return min(100.0, level) * 0.0015


def intuition_chances(crew):
    """How many crewmen carry a finished ``loader_intuition`` perk.

    #1513 makes it a perk, so it only counts at full proficiency, and the skill
    text says two loaders stack.
    """
    count = 0
    for member in (crew or ()):
        if isinstance(member, tuple) and len(member) == 2:
            member = member[1]
        if member is None:
            continue
        if _skill_level(member, _INTUITION_SKILL) >= 100.0:
            count += 1
    return count


def crew_level_increase(descriptor, equipments=(), crew_skills=None):
    """Return #1513's per-crewman level increase for this loadout.

    ``miscAttrs['crewLevelIncrease']`` is where a mounted ventilation folds
    itself into the descriptor; Brothers in Arms and rations add on top of it
    the same way ``VehicleDescrCrew`` does.
    """
    misc = getattr(descriptor, 'miscAttrs', None) or {}
    try:
        increase = float(misc.get('crewLevelIncrease', 0.0) or 0.0)
    except (AttributeError, TypeError, ValueError):
        increase = 0.0
    bundle = modifiers(descriptor, equipments, crew_skills)
    if bundle['has_brotherhood']:
        increase += BROTHERHOOD_CREW_BONUS
    if bundle['has_rations']:
        increase += RATION_CREW_BONUS
    return increase


def spotting_profile(descriptor=None, crew=None, level_increase=0.0,
                     factors=None):
    """Return the vision and concealment inputs our spotting law needs.

    With ``factors`` the vision and camouflage multipliers are the client's
    own ``circularVisionRadius`` and ``camouflage`` entries, so a mounted
    ventilation, a trained commander and a coated optic all arrive together.
    The still-only stereoscope is divided back out because the battle gates it
    on three stationary seconds while the garage panel shows it always.
    """
    profile = {
        'commander_level': 100.0,
        'recon_level': 0.0,
        'situational_level': 0.0,
        'camouflage_level': 0.0,
        'binocular_factor': 1.0,
        'binocular_delay': 3.0,
        'camouflage_net_bonus': 0.0,
        'camouflage_net_delay': 3.0,
        'has_binoculars': False,
        'has_camouflage_net': False,
        'vision_factor': 1.0,
        'camouflage_factor': CREW_FACTOR_BASE,
        'invisibility_moving': (0.0, 1.0),
        'invisibility_still': (0.0, 1.0),
        'from_client_factors': bool(factors),
    }
    if descriptor is not None:
        misc = getattr(descriptor, 'miscAttrs', None) or {}
        try:
            optics_factor = float(
                misc.get('circularVisionRadiusFactor', 1.0) or 1.0)
        except (AttributeError, TypeError, ValueError):
            optics_factor = 1.0
        for device in (getattr(descriptor, 'optionalDevices', None) or ()):
            if device is None:
                continue
            name = _name_of(device)
            if _matches(name, _BINOCULAR_MARKERS):
                factor = _number(
                    getattr(device, 'circularVisionRadiusFactor', 0.0))
                if factor > 0.0:
                    # #1513 divides the descriptor's optics factor out, so the
                    # binocular value replaces it instead of stacking.
                    profile['binocular_factor'] = max(
                        profile['binocular_factor'],
                        factor / max(optics_factor, 1e-6))
                profile['has_binoculars'] = True
                profile['binocular_delay'] = _number(
                    getattr(device, 'activateWhenStillSec', 3.0), 3.0)
            elif _matches(name, _CAMOUFLAGE_NET_MARKERS):
                profile['has_camouflage_net'] = True
                profile['camouflage_net_delay'] = _number(
                    getattr(device, 'activateWhenStillSec', 3.0), 3.0)
        if profile['has_camouflage_net']:
            deltas = getattr(
                getattr(descriptor, 'type', None), 'invisibilityDeltas',
                None) or {}
            try:
                profile['camouflage_net_bonus'] = max(
                    0.0, float(deltas.get('camouflageNetBonus', 0.0) or 0.0))
            except (AttributeError, TypeError, ValueError):
                profile['camouflage_net_bonus'] = 0.0

    members = []
    for member in (crew or ()):
        if isinstance(member, tuple) and len(member) == 2:
            member = member[1]
        if member is not None:
            members.append(member)
    if members:
        camouflage_total = 0.0
        for member in members:
            role = str(getattr(member, 'role', '') or '').lower()
            if role == 'commander' or not profile.get('_commander_seen'):
                level = getattr(member, 'roleLevel', None)
                if role == 'commander' and level is not None:
                    profile['commander_level'] = max(
                        0.0, _number(level, 100.0))
                    profile['_commander_seen'] = True
            # #1513 takes the single best crewman for a role-specific skill.
            profile['recon_level'] = max(
                profile['recon_level'], _skill_level(member, _RECON_SKILL))
            profile['situational_level'] = max(
                profile['situational_level'],
                _skill_level(member, _SITUATIONAL_SKILL))
            camouflage_total += _skill_level(member, _CAMOUFLAGE_SKILL)
        # Camouflage is crew-wide: the sum is divided by the whole crew, so a
        # member without the skill contributes zero rather than being skipped.
        profile['camouflage_level'] = camouflage_total / float(len(members))
    profile.pop('_commander_seen', None)
    # #1513 adds the increase to every crewman before any efficiency is taken,
    # so ventilation raises the commander and every trained skill alike.
    increase = max(0.0, _number(level_increase, 0.0))
    if increase:
        profile['commander_level'] += increase
        for name in ('recon_level', 'situational_level'):
            if profile[name] > 0.0:
                profile[name] += increase
        if profile['camouflage_level'] > 0.0:
            profile['camouflage_level'] += increase
    if factors:
        binocular = profile['binocular_factor'] if profile[
            'has_binoculars'] else 1.0
        profile['vision_factor'] = (
            _factor(factors, 'circularVisionRadius') /
            max(binocular, 1e-6))
        profile['camouflage_factor'] = _factor(factors, 'camouflage')
        profile['invisibility_moving'] = invisibility_pair(factors, False)
        profile['invisibility_still'] = invisibility_pair(factors, True)
    else:
        profile['vision_factor'] = _legacy_vision_factor(profile)
        profile['camouflage_factor'] = (
            CREW_FACTOR_BASE + (1.0 - CREW_FACTOR_BASE) *
            min(1.0, max(0.0, profile['camouflage_level'] / 100.0)))
        profile['invisibility_still'] = (
            profile['camouflage_net_bonus'] if profile['has_camouflage_net']
            else 0.0, 1.0)
    return profile


def _legacy_vision_factor(profile):
    """Reproduce the client's own vision chain without the client modules."""
    factor = (CREW_FACTOR_BASE + (1.0 - CREW_FACTOR_BASE) *
              min(1.5, max(0.0, profile['commander_level'] / 100.0)))
    factor *= 1.0 + RECON_FACTOR_PER_LEVEL * max(0.0, profile['recon_level'])
    factor *= 1.0 + SITUATIONAL_FACTOR_PER_LEVEL * max(
        0.0, profile['situational_level'])
    return factor


def still_device_active(still_seconds, delay_seconds):
    """Whether a stationary-only device has finished its activation delay."""
    return _number(still_seconds, 0.0) >= max(0.0, _number(delay_seconds, 3.0))
