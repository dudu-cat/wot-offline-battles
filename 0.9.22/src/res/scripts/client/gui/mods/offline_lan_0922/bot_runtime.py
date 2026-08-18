from __future__ import print_function

"""Authority-side, engine-free bridge from v5 bots to the local AI package."""

import math
import random

from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai import maps as tactical_maps
from gui.mods.offline_lan_0922.ai import driver as ai_driver
from gui.mods.offline_lan_0922.ai import planner as ai_planner
from gui.mods.offline_lan_0922.ai.navigation import TerrainNavigator
from gui.mods.offline_lan_0922 import critical_damage
from gui.mods.offline_lan_0922 import ballistics
from gui.mods.offline_lan_0922 import device_damage
from gui.mods.offline_lan_0922 import prebaked_navigation
from gui.mods.offline_lan_0922 import spotting
from gui.mods.offline_lan_0922 import loadout
from gui.mods.offline_lan_0922 import tank_collision
from gui.mods.offline_lan_0922 import vehicle_physics


OBSERVATION_SECONDS = 0.40
PUBLICATION_SECONDS = 1.0 / 30.0
# Eight protocol-maximum exact paths can share only two of the four native
# rays while strategic work is pending.  Give both the initial intent and each
# stale moving-target reproof their own queue-sized lifetime.
ARTILLERY_INTENT_SECONDS = 60.0
ARTILLERY_REPROOF_SECONDS = 60.0
ARTILLERY_TOTAL_PROOF_SECONDS = 120.0
# A completed exact arc is still stale if a moving target has outrun the lead
# while the bounded native queue was proving it.  Descriptors are not present
# on every canonical contact, so use a conservative half-width fallback.
ARTILLERY_IMPACT_ERROR_METRES = 1.5
ARTILLERY_RECEIPT_HOLD_SECONDS = 10.0
COVER_JOBS_PER_OBSERVATION = 3
HUMAN_TARGET_ID_BASE = 1000000
VISIBILITY_MIN_SECONDS = 0.18
VISIBILITY_JITTER_SECONDS = 0.018
SHOT_LANE_SECONDS = 0.20
# Spread full-roster tactical refreshes over the latter half of the
# observation window.  A selected target still goes through the independent
# 0.20-second final-fire gate above.
SHOT_LANE_REFRESH_SECONDS = 0.20
SHOT_LANE_PHASES = 29
# At 24 FPS the final 0.20-second window contains only five render frames.
# Keep the native collision work bounded and delay the complete observation
# instead of forcing every remaining pair through its due frame.
MAX_SHOT_LANE_PAIRS_PER_FRAME = 110
# The server never assigns a visible target beyond 560 metres.  The pinned
# #1513 catalogue tops out at 79 km/h; including the copied 1.05 downhill
# overspeed, two vehicles close less than 22 metres across one 0.40-second
# observation plus a 24 Hz render frame and 30 Hz publication frame.  Keep a
# conservative 25-metre margin, but do not spend a native collision ray on a
# pair which cannot enter the server envelope in that time.
SHOT_LANE_QUERY_DISTANCE = 585.0
# Artillery is reported shootable only after the authority has completed a
# pitch-valid curved-world probe.  Keep its query envelope at map scale without
# making every ordinary tank spend native rays beyond the server's 560 m lease.
SPG_SHOT_LANE_QUERY_DISTANCE = 2500.0
# Cover fans occupy the first half of the observation window; firing-lane
# refreshes already occupy the final half.  Keeping the two native probe
# families disjoint avoids replacing one periodic render-thread burst with a
# different combined burst.
COVER_JOB_WINDOW_SECONDS = (
    OBSERVATION_SECONDS - SHOT_LANE_REFRESH_SECONDS)
PROBE_KINDS = ('visibility', 'lane', 'cover', 'ground', 'motion')
DECISION_SECONDS = 0.0975
# Distance tiers for the render-frame work that only presentation consumes.
# A hull two hundred metres away moves less than one pixel of visible tilt per
# frame, so its four-point suspension sample and its planner cadence can be
# spread without changing what the player sees.
DETAIL_NEAR_METRES = 150.0
DETAIL_FAR_METRES = 350.0
# Travel that must accumulate before a tier re-samples the four ground rays.
SLOPE_SAMPLE_METRES = (0.35, 1.50, 4.00)
SLOPE_SAMPLE_RADIANS = (0.05, 0.15, 0.40)
# Planner cadence multiplier per tier.
DECISION_TIER_FACTOR = (1.0, 2.0, 4.0)
# Integration rate per tier.  Dead reckoning between steps EXTRAPOLATES, so
# every step corrects the guess and that correction is visible as a jump: the
# lower the rate, the bigger the jump.  Until bots own a native filter that can
# interpolate properly, only hulls beyond DETAIL_FAR_METRES step down, and only
# to 15 Hz, where the residual correction stays under a pixel.  Smooth motion
# beats the frame budget.
INTEGRATION_INTERVALS = (0.0, 0.0, 1.0 / 30.0)
INTEGRATION_PHASE_BUCKETS = 7
# The #1513 production probe owns a 15 m low-speed / 20 m high-speed,
# three-lane corridor.  A cached sample may only be reused while the hull stays
# well inside the 2.2 m outer lanes.  The time bound also limits maximum copied
# travel to 35 m/s * 0.0975 s = 3.4125 m before a mandatory new native probe.
MOTION_PROBE_SECONDS = DECISION_SECONDS
MOTION_PROBE_LATERAL_BUDGET = 1.0
MOTION_PROBE_FORWARD_BUDGET = 3.5
# A final exact receipt costs nine native rays.  Thirteen jobs per render frame
# drains all 29 Bots within three frames even at the supported 24 FPS floor,
# while preventing the first copied-motion frame from issuing 29 receipts at
# once.  Budget exhaustion pauses that Bot and retries next frame; falling back
# to a nine-ray commit sweep would merely move the same spike elsewhere.
MAX_WORLD_RECEIPTS_PER_FRAME = 13
FIRE_DURATION_SECONDS = 10.0
FIRE_TICK_SECONDS = 1.0


def _cache_deadline(now, entity_id, interval, salt=0, stagger=False):
    """Spread only the first expiry, then retain the requested cadence."""
    interval = max(0.001, float(interval))
    deadline = float(now) + interval
    if not stagger:
        return deadline
    phase = (((abs(int(entity_id)) * 17 + int(salt) * 11) % 29) /
             29.0) * interval
    return deadline + phase


def _motion_probe_deadline(now, entity_id, initial=False):
    """Stagger first rechecks without ever exceeding the safety interval."""
    if not initial:
        return float(now) + MOTION_PROBE_SECONDS
    phase = (((abs(int(entity_id)) * 17 + 7 * 11) % 29) + 1) / 29.0
    return float(now) + MOTION_PROBE_SECONDS * phase


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _position(state):
    return (_number(state.get('x')), _number(state.get('y')),
            _number(state.get('z')))


def _value(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _forward_speed(descriptor):
    physics = _value(descriptor, 'physics', {}) or {}
    limits = _value(physics, 'speedLimits', (14.0, 7.0))
    try:
        value = abs(float(limits[0]))
    except (TypeError, ValueError, IndexError):
        value = 14.0
    return max(4.0, min(value, 35.0))


def _view_range(descriptor, still_seconds=0.0):
    """Bot view range, using the same device law as the player.

    A bot has no garage crew, so its crew-derived factors stay at the
    untrained baseline; its mounted devices still apply.
    """
    turret = _value(descriptor, 'turret', {}) or {}
    misc = _value(descriptor, 'miscAttrs', {}) or {}
    profile = loadout.spotting_profile(descriptor, None)
    return spotting.effective_view_range(
        _value(turret, 'circularVisionRadius', 330.0),
        commander_level=profile['commander_level'],
        vision_factor=_value(misc, 'circularVisionRadiusFactor', 1.0),
        recon_level=profile['recon_level'],
        situational_level=profile['situational_level'],
        binocular_factor=profile['binocular_factor'],
        binocular_active=(
            profile['has_binoculars'] and
            loadout.still_device_active(
                still_seconds, profile['binocular_delay'])))


def _base_invisibility(descriptor, crew_camouflage_level=0.0):
    crew_factor = spotting.crew_camouflage_factor(crew_camouflage_level)
    calculator = getattr(descriptor, 'computeBaseInvisibility', None)
    if callable(calculator):
        try:
            values = calculator(crew_factor, None)
            if isinstance(values, (list, tuple)) and len(values) >= 2:
                return (_number(values[0]), _number(values[1]))
        except Exception:
            pass
    vehicle_type = _value(descriptor, 'type', {}) or {}
    values = _value(vehicle_type, 'invisibility', (0.0, 0.0))
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        values = (0.0, 0.0)
    misc = _value(descriptor, 'miscAttrs', {}) or {}
    return spotting.base_camouflage(
        values[0], values[1],
        invisibility_factor=_value(misc, 'invisibilityFactor', 1.0))


def _shot_invisibility_factor(descriptor):
    gun = _value(descriptor, 'gun', {}) or {}
    return spotting.clamp(
        _value(gun, 'invisibilityFactorAtShot', 1.0), 0.0, 1.0)


def _detection_upper_bound(distance, view_range, base_pair, moving,
                           shot_factor, fired_recently):
    """Return detection with the best possible geometry for this pair.

    Foliage camouflage is additive and clamped to a non-negative value, so
    zero foliage is the minimum possible camouflage.  A clear line of sight is
    likewise the maximum possible visibility.  If this upper bound is false,
    neither the native collision ray nor the real foliage result can make the
    target visible.
    """
    minimum_camouflage = spotting.effective_camouflage(
        base_pair, moving=moving, shot_factor=shot_factor,
        fired_recently=fired_recently, foliage_bonus=0.0)
    return spotting.is_detected(
        distance, view_range, minimum_camouflage, True)


def _hull_dimensions(descriptor):
    """Derive AI avoidance dimensions from the admitted collision body."""
    shape = tank_collision.chassis_shape(descriptor)
    return shape[1], shape[0]


def _collision_shape(descriptor):
    """Return the current 0.8.2 chassis hit-tester body."""
    return tank_collision.chassis_shape(descriptor)


def _distance(first, second):
    dx = _number(first[0]) - _number(second[0])
    dz = _number(first[2]) - _number(second[2])
    return math.sqrt(dx * dx + dz * dz)


def _angle_delta(target, current):
    value = _number(target) - _number(current)
    while value > math.pi:
        value -= math.pi * 2.0
    while value < -math.pi:
        value += math.pi * 2.0
    return value


def _wrapped(value):
    return _angle_delta(value, 0.0)


def _point(value, fallback=(0.0, 0.0, 0.0)):
    if isinstance(value, dict):
        return (_number(value.get('x'), fallback[0]),
                _number(value.get('y'), fallback[1]),
                _number(value.get('z'), fallback[2]))
    try:
        return (_number(value[0], fallback[0]),
                _number(value[1], fallback[1]),
                _number(value[2], fallback[2]))
    except (TypeError, IndexError):
        return fallback


def _slew(current, desired, maximum_step):
    difference = float(desired) - float(current)
    step = max(0.0, float(maximum_step))
    if difference > step:
        return float(current) + step
    if difference < -step:
        return float(current) - step
    return float(desired)


def _rotation_speed(component, default):
    return max(0.0, _number(_value(component, 'rotationSpeed', default),
                            default))


def slope_pose(probe, position, yaw, half_length, half_width,
               last_pitch=0.0, last_roll=0.0):
    """One step of the copied 0.8.2 four-point suspension hull pose."""
    length = max(3.0, 2.0 * float(half_length))
    width = max(2.0, 2.0 * float(half_width))
    sine, cosine = math.sin(yaw), math.cos(yaw)
    front = probe(position[0] + sine * length * 0.5,
                  position[2] + cosine * length * 0.5, position[1])
    rear = probe(position[0] - sine * length * 0.5,
                 position[2] - cosine * length * 0.5, position[1])
    right = probe(position[0] + cosine * width * 0.5,
                  position[2] - sine * width * 0.5, position[1])
    left = probe(position[0] - cosine * width * 0.5,
                 position[2] + sine * width * 0.5, position[1])
    if None in (front, rear, right, left):
        return float(last_pitch), float(last_roll)
    pitch = -math.atan2(float(front) - float(rear), length) * 0.9
    roll = math.atan2(float(right) - float(left), width) * 0.9
    tilt = math.sqrt(pitch * pitch + roll * roll)
    if tilt > 0.61:
        scale = 0.61 / tilt
        pitch *= scale
        roll *= scale
    return (float(last_pitch) + (pitch - float(last_pitch)) * 0.5,
            float(last_roll) + (roll - float(last_roll)) * 0.5)


def _gun_pitch_limits(descriptor):
    gun = _value(descriptor, 'gun', {}) or {}
    limits = _value(gun, 'pitchLimits')
    if isinstance(limits, dict):
        limits = limits.get('absolute', limits)
    try:
        return float(limits[0]), float(limits[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return -0.35, 0.15


def _shot_ballistics(descriptor, shell_index):
    """Return frozen ``(speed, gravity, max_distance)`` or ``None``.

    Exact #1513 descriptors are attribute-only objects.  Missing physical
    fields are not permission to resurrect the old instantaneous straight-ray
    shot, so production firing fails closed when this tuple is unavailable.
    """
    gun = _value(descriptor, 'gun', {}) or {}
    shots = _value(gun, 'shots', ()) or ()
    try:
        shot = shots[max(0, int(shell_index))]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    speed = _number(_value(shot, 'speed'), -1.0)
    gravity = abs(_number(_value(shot, 'gravity'), -1.0))
    maximum = _number(_value(shot, 'maxDistance'), -1.0)
    if speed <= 1.0 or gravity <= 0.01 or maximum <= 1.0:
        return None
    return speed, gravity, maximum


def _critical_parts(state):
    critical = state.get('critical')
    if not isinstance(critical, dict):
        return {}, set(), set()
    devices = {}
    for record in critical.get('devices') or ():
        if isinstance(record, dict) and record.get('name'):
            devices[str(record['name'])] = _number(record.get('hp'))
    destroyed = set(str(name) for name in
                    (critical.get('destroyed') or ()))
    crew_ko = set(str(name) for name in (critical.get('crew_ko') or ()))
    return devices, destroyed, crew_ko


def _critical_factor(state, descriptor, stat):
    devices, destroyed, crew_ko = _critical_parts(state)
    return (device_damage.crew_stat_factor(crew_ko, stat) *
            device_damage.module_stat_factor(
                devices, destroyed, descriptor, stat))


def _critical_signature(payload):
    """Match the server's durable, three-decimal critical-state boundary."""
    if not isinstance(payload, dict) or not payload:
        return ()
    devices = []
    for record in payload.get('devices') or ():
        if not isinstance(record, dict) or not record.get('name'):
            raise ValueError('bot critical device is malformed')
        devices.append((
            str(record['name']), round(_number(record.get('hp')), 3),
            round(_number(record.get('max_hp'), 1.0), 3),
            str(record.get('state', ''))))
    signature = (
        tuple(sorted(devices)),
        tuple(sorted(str(name) for name in
                     (payload.get('destroyed') or ()))),
        tuple(sorted(str(name) for name in
                     (payload.get('crew_ko') or ()))),
        bool(payload.get('fire', False)),
        bool(payload.get('ammo_rack_death', False)))
    if signature == ((), (), (), False, False):
        return ()
    return signature


def _canonical_critical(payload):
    """Emit exactly the durable shape returned by server ``_critical_state``."""
    if not isinstance(payload, dict) or not payload:
        return {}
    devices = []
    for record in payload.get('devices') or ():
        if (not isinstance(record, dict) or not record.get('name') or
                'hp' not in record or 'max_hp' not in record or
                record.get('state') not in (
                    'normal', 'critical', 'destroyed')):
            raise ValueError('bot critical device is malformed')
        maximum = max(1.0, round(float(record['max_hp']), 3))
        hp = max(0.0, min(round(float(record['hp']), 3), maximum))
        devices.append({
            'name': str(record['name']), 'hp': hp, 'max_hp': maximum,
            'state': str(record['state'])})
    devices.sort(key=lambda record: record['name'])
    return {
        'devices': devices,
        'destroyed': sorted(str(name) for name in
                            (payload.get('destroyed') or ())),
        'crew_ko': sorted(str(name) for name in
                          (payload.get('crew_ko') or ())),
        'fire': bool(payload.get('fire', False)),
        'ammo_rack_death': bool(payload.get('ammo_rack_death', False)),
        'events': [],
    }


def _combat_signature(state):
    return (
        max(0, int(_number(state.get('health')))),
        bool(state.get('alive', False)),
        _critical_signature(state.get('critical')),
        round(_number(state.get('combat_fire_elapsed')), 6),
        round(_number(state.get('combat_fire_timer')), 6))


def _combat_record(state):
    return {
        'health': max(0, int(_number(state.get('health')))),
        'alive': bool(state.get('alive', False)),
        'critical': _canonical_critical(state.get('critical')),
        'combat_fire_elapsed': round(
            _number(state.get('combat_fire_elapsed')), 6),
        'combat_fire_timer': round(
            _number(state.get('combat_fire_timer')), 6),
    }


def _apply_combat_record(state, record):
    state['health'] = max(0, min(
        int(_number(record.get('health'))), int(state['max_health'])))
    state['alive'] = bool(record.get('alive')) and state['health'] > 0
    state['critical'] = _canonical_critical(record.get('critical'))
    state['combat_fire_elapsed'] = round(
        _number(record.get('combat_fire_elapsed')), 6)
    state['combat_fire_timer'] = round(
        _number(record.get('combat_fire_timer')), 6)
    state['display_health'] = state['health']
    if not state['alive']:
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        state['target_kind'] = None
        state['target_id'] = None


class _BotCriticalVehicle(object):
    """Detached adapter for the copied 0.8.2 repair and fire functions."""

    def __init__(self, state, descriptor, fire_started, fire_timer):
        payload = state.get('critical') or {}
        devices = {}
        for record in payload.get('devices') or ():
            if not isinstance(record, dict) or not record.get('name'):
                raise ValueError('bot critical device is malformed')
            devices[str(record['name'])] = max(
                0.0, float(record.get('hp', 0.0)))
        self.id = int(state['id'])
        self.health = max(0, int(_number(state.get('health'))))
        self.maxHealth = max(1, int(_number(
            state.get('max_health'), self.health or 1)))
        self.typeDescriptor = descriptor
        self.devices_hp = devices
        self._destroyed_devices = set(
            str(name) for name in (payload.get('destroyed') or ()))
        self._crew_ko = set(
            str(name) for name in (payload.get('crew_ko') or ()))
        self._crew_impaired = frozenset()
        self.is_on_fire = bool(payload.get('fire', False))
        self._ammo_rack_death = bool(
            payload.get('ammo_rack_death', False))
        self._fire_started = fire_started
        self._fire_timer = max(0.0, float(fire_timer or 0.0))
        self._offline_proposal_only = True
        self.is_tracked = False
        self.is_engine_dead = False
        self.is_gun_destroyed = False
        self.is_turret_locked = False


class _BotGunState(object):
    """The final 0.8.2 bot reload/clip clock, kept engine-free.

    Inventory and loaded-shell selection live in ``_BotAmmoState``. A clip
    starts full; rounds inside it use ``clip[1]`` and an empty clip is
    immediately reset but held for the full reload time.
    """

    def __init__(self, descriptor, fire_seq=0):
        gun = _value(descriptor, 'gun', {}) or {}
        raw_dispersion = _value(gun, 'shotDispersionAngle')
        try:
            self.fully_aimed_dispersion = float(raw_dispersion)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(
                'installed gun shotDispersionAngle is unavailable')
        if (isinstance(raw_dispersion, bool) or
                math.isnan(self.fully_aimed_dispersion) or
                math.isinf(self.fully_aimed_dispersion) or
                self.fully_aimed_dispersion <= 0.0):
            raise ValueError(
                'installed gun shotDispersionAngle must be positive')
        self.reload_full = max(
            0.01, _number(_value(gun, 'reloadTime', 3.0), 3.0))
        self.clip_size = 1
        self.reload_intra = 0.0
        clip = _value(gun, 'clip')
        try:
            if len(clip) == 2:
                self.clip_size = max(1, int(clip[0]))
                self.reload_intra = max(0.01, float(clip[1]))
        except (TypeError, ValueError, IndexError):
            pass
        shots = _value(gun, 'shots', ()) or ()
        try:
            self.shell_count = max(1, len(shots))
        except TypeError:
            self.shell_count = 1
        self.clip = self.clip_size
        self.elapsed = 0.0
        self.reload_duration = self.reload_full
        self.restore_fire_seq(fire_seq)

    def restore_fire_seq(self, fire_seq):
        fire_seq = max(0, int(_number(fire_seq)))
        if self.clip_size > 1:
            used = fire_seq % self.clip_size
            self.clip = self.clip_size - used if used else self.clip_size
            self.reload_duration = (
                self.reload_intra if used else self.reload_full)
        else:
            self.clip = 1
            self.reload_duration = self.reload_full
        # A takeover does not know the previous authority's sub-frame clock.
        # Waiting one complete interval is safe and prevents a duplicate shot.
        self.elapsed = 0.0

    def tick(self, dt):
        self.elapsed += max(0.0, float(dt))

    def ready(self, reload_factor=1.0):
        return self.elapsed > (
            self.reload_duration * max(0.0, float(reload_factor)))

    def shell_index(self, requested):
        return max(0, min(int(_number(requested)), self.shell_count - 1))

    def fire(self, reload_factor=1.0):
        if not self.ready(reload_factor):
            return False
        self.elapsed = 0.0
        if self.clip_size > 1:
            self.clip -= 1
            if self.clip <= 0:
                self.clip = self.clip_size
                self.reload_duration = self.reload_full
            else:
                self.reload_duration = self.reload_intra
        else:
            self.reload_duration = self.reload_full
        return True

    def remaining(self, reload_factor=1.0):
        duration = self.reload_duration * max(
            0.0, float(reload_factor))
        return max(0.0, duration - self.elapsed)


def _bot_ammo_capacity(descriptor):
    """Read the installed vehicle's real ammunition capacity."""
    gun = _value(descriptor, 'gun', {}) or {}
    maximum = _value(descriptor, 'maxAmmo', None)
    if maximum is None:
        maximum = _value(gun, 'maxAmmo', None)
    if maximum is None:
        maximum = _value(_value(descriptor, 'turret', {}), 'maxAmmo', 45)
    try:
        maximum = int(maximum)
    except (TypeError, ValueError, OverflowError):
        maximum = 45
    return max(0, min(maximum, 1000))


def _bot_ammo_categories(profile, shell_count):
    """Classify descriptor-order shells without relying on store prices.

    The pinned descriptor does not expose a stable credits/gold price at this
    seam.  The first non-HE shell is therefore the standard baseline; a later
    non-HE shell is premium only when its representative penetration is at
    least three percent higher.  This keeps standard AP/APCR as the default
    while still recognizing the usual higher-penetration APCR/HEAT round.
    """
    shells = profile.get('shells', ()) if isinstance(profile, dict) else ()
    shells = tuple(shells or ())
    by_index = {}
    for raw in shells:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get('index', -1))
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 <= index < shell_count:
            by_index[index] = raw
    non_he = []
    categories = {}
    for index in range(shell_count):
        shell = by_index.get(index, {})
        kind = str(shell.get('kind', '') or '').lower()
        is_he = ('high_explosive' in kind or
                 ('explosive' in kind and 'armor_piercing' not in kind))
        if is_he:
            categories[index] = 'he'
        else:
            non_he.append(index)
    baseline = non_he[0] if non_he else None
    baseline_penetration = max(0.0, _number(
        by_index.get(baseline, {}).get('penetration', 0.0)))
    for index in non_he:
        penetration = max(0.0, _number(
            by_index.get(index, {}).get('penetration', 0.0)))
        categories[index] = (
            'premium' if (index != baseline and
                          baseline_penetration > 0.0 and
                          penetration >= baseline_penetration * 1.03)
            else 'standard')
    return categories


def _bot_ammo_distribution(descriptor, profile, shell_count):
    """Allocate one exact, fixed per-battle inventory by shell category."""
    maximum = _bot_ammo_capacity(descriptor)
    if shell_count <= 0 or maximum <= 0:
        return [0] * max(0, shell_count)
    categories = _bot_ammo_categories(profile, shell_count)
    class_tag = str((profile or {}).get('class_tag', ''))
    # Ordinary vehicles use the requested 3:2:1 baseline. Artillery carries a
    # physically HE-led 1:1:4 load; unavailable categories are redistributed
    # across the shells that the installed gun actually exposes.
    category_weights = ({'standard': 1.0, 'premium': 1.0, 'he': 4.0}
                        if class_tag == 'SPG' else
                        {'standard': 3.0, 'premium': 2.0, 'he': 1.0})
    active_categories = sorted(set(categories.values()))
    total_weight = sum(category_weights[name] for name in active_categories)
    if total_weight <= 0.0:
        return [0] * shell_count
    category_counts = dict((name, int(
        maximum * category_weights[name] / total_weight))
        for name in active_categories)
    assigned = sum(category_counts.values())
    remainders = sorted(
        active_categories,
        key=lambda name: (
            -(maximum * category_weights[name] / total_weight -
              category_counts[name]), name))
    for offset in range(maximum - assigned):
        category_counts[remainders[offset % len(remainders)]] += 1
    result = [0] * shell_count
    for name in active_categories:
        indices = sorted(index for index, category in categories.items()
                         if category == name)
        quantity = category_counts[name]
        for offset in range(quantity):
            result[indices[offset % len(indices)]] += 1
    return result


class _BotAmmoState(object):
    """Finite Bot inventory with distinct loaded and planned-next rounds."""

    def __init__(self, descriptor, profile, raw=None):
        gun = _value(descriptor, 'gun', {}) or {}
        shots = _value(gun, 'shots', ()) or ()
        try:
            self.shell_count = max(1, len(shots))
        except TypeError:
            self.shell_count = 1
        self.categories = _bot_ammo_categories(profile, self.shell_count)
        self.remaining = _bot_ammo_distribution(
            descriptor, profile, self.shell_count)
        self.loaded = self._standard_fallback()
        self.next = self.loaded
        self.reload_pending = False
        self.plan_pending = True
        if isinstance(raw, dict):
            self.restore(raw)

    def _standard_fallback(self):
        candidates = [index for index in range(self.shell_count)
                      if self.remaining[index] > 0]
        if not candidates:
            return 0
        standard = [index for index in candidates
                    if self.categories.get(index) == 'standard']
        return standard[0] if standard else candidates[0]

    def _available(self, requested):
        try:
            requested = int(requested)
        except (TypeError, ValueError, OverflowError):
            requested = -1
        if (0 <= requested < self.shell_count and
                self.remaining[requested] > 0):
            return requested
        return self._standard_fallback()

    def restore(self, raw):
        if ('ammo_remaining' not in raw and
                'next_shell_index' not in raw and
                'ammo_reload_pending' not in raw):
            return False
        present = [name in raw for name in (
            'ammo_remaining', 'shell_index', 'next_shell_index',
            'ammo_reload_pending')]
        if any(present) and not all(present):
            raise ValueError('bot ammunition snapshot is incomplete')
        if not all(present):
            return False
        remaining = raw.get('ammo_remaining')
        if (not isinstance(remaining, (list, tuple)) or
                len(remaining) != self.shell_count):
            raise ValueError('bot ammunition inventory shape is invalid')
        parsed = []
        for quantity in remaining:
            try:
                exact = int(quantity)
            except (TypeError, ValueError, OverflowError):
                raise ValueError('bot ammunition quantity is invalid')
            if (isinstance(quantity, bool) or exact < 0 or exact > 1000 or
                    float(quantity) != exact):
                raise ValueError('bot ammunition quantity is invalid')
            parsed.append(exact)
        try:
            loaded = int(raw.get('shell_index'))
            planned = int(raw.get('next_shell_index'))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('bot ammunition selection is invalid')
        if (loaded < 0 or loaded >= self.shell_count or
                planned < 0 or planned >= self.shell_count):
            raise ValueError('bot ammunition selection is invalid')
        reload_pending = raw.get('ammo_reload_pending')
        if not isinstance(reload_pending, bool):
            raise ValueError('bot ammunition reload state is invalid')
        total = sum(parsed)
        if total > 0 and parsed[planned] <= 0:
            raise ValueError('bot planned ammunition is exhausted')
        if total > 0 and not reload_pending and parsed[loaded] <= 0:
            raise ValueError('bot loaded ammunition is exhausted')
        self.remaining = parsed
        self.loaded = loaded
        self.next = planned
        self.reload_pending = reload_pending
        # The canonical snapshot already locks the planned round.  Only a
        # real pending-to-ready reload edge may promote it or choose another.
        self.plan_pending = False
        return True

    def stage(self, requested, ready):
        """Commit loaded/next choices only at one completed reload edge."""
        if not ready:
            return False
        changed = False
        if self.reload_pending:
            selected = self._available(self.next)
            if selected != self.loaded:
                self.loaded = selected
                changed = True
            self.reload_pending = False
            self.plan_pending = True
        if self.plan_pending:
            selected = self._available(requested)
            if selected != self.next:
                self.next = selected
                changed = True
            self.plan_pending = False
        return changed

    def can_fire(self):
        return (0 <= self.loaded < self.shell_count and
                self.remaining[self.loaded] > 0 and
                not self.reload_pending)

    def consume_loaded(self):
        if not self.can_fire():
            return False
        self.remaining[self.loaded] -= 1
        self.next = self._available(self.next)
        self.reload_pending = True
        self.plan_pending = False
        return True

    def publish(self, state):
        state['shell_index'] = int(self.loaded)
        state['next_shell_index'] = int(self.next)
        state['ammo_remaining'] = list(self.remaining)
        state['ammo_reload_pending'] = bool(self.reload_pending)


def _effective_shot_dispersion(gun_state, state, descriptor):
    """Return installed fully-aimed dispersion with current critical malus."""
    value = (float(gun_state.fully_aimed_dispersion) *
             _critical_factor(state, descriptor, 'dispersion'))
    if math.isnan(value) or math.isinf(value) or value <= 0.0:
        raise ValueError('effective bot shot dispersion must be positive')
    return value


def _dispersed_barrel_angles(bot_id, round_id, fire_seq, yaw, pitch,
                             dispersion_angle):
    """Return the actual physical shot ray used by the battle resolver.

    The 0.8.2 presentation uses negative pitch for a raised barrel.  Protocol
    ``shot_pitch`` is a physical vector elevation (positive is up), matching
    the #1513 projectile/raycast boundary.  A per-shot seed makes authority
    takeover deterministic without sharing ``random`` module state.
    """
    direction = list(ai_driver.barrel_direction(yaw, pitch))
    seed = ((int(_number(round_id)) & 0xffff) * 1000003 +
            (int(bot_id) & 0xffff) * 9176 +
            (int(fire_seq) & 0x7fffffff) * 6113) & 0x7fffffff
    generator = random.Random(seed)
    try:
        dispersion_angle = float(dispersion_angle)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('bot shot dispersion is unavailable')
    if (math.isnan(dispersion_angle) or math.isinf(dispersion_angle) or
            dispersion_angle <= 0.0):
        raise ValueError('bot shot dispersion must be positive')
    sigma = dispersion_angle / 3.0
    direction[0] += generator.gauss(0.0, sigma)
    direction[1] += generator.gauss(0.0, sigma)
    direction[2] += generator.gauss(0.0, sigma)
    length = math.sqrt(sum(value * value for value in direction))
    if length <= 1e-9:
        direction = [0.0, 0.0, 1.0]
    else:
        direction = [value / length for value in direction]
    horizontal = math.sqrt(direction[0] * direction[0] +
                           direction[2] * direction[2])
    return (math.atan2(direction[0], direction[2]),
            math.atan2(direction[1], max(1e-9, horizontal)))


def _overlay_live_target_pose(command, target):
    """Replace a low-rate team-spotted order with the current target pose.

    A visible-but-occluded contact still needs a current approach goal. The
    authority's local visibility probe is deliberately not a second fire gate
    here: one ally may spot a target that another ally has the clear barrel
    lane to shoot. The actual lane is probed again immediately before firing.
    """
    result = dict(command)
    if result.get('target_id') is None:
        return result
    if target is None:
        result['fire_allowed'] = False
        return result
    if not isinstance(target, dict):
        raise ValueError('canonical live target must be a record')
    for name in ('alive', 'visible'):
        if name not in target or not isinstance(target[name], bool):
            raise ValueError(
                'canonical live target %s flag is invalid' % name)
    if not target['alive']:
        result['fire_allowed'] = False
        return result
    if 'position' not in target:
        raise ValueError('canonical live target position is unavailable')
    raw_position = target['position']
    if (not isinstance(raw_position, (list, tuple)) or
            len(raw_position) != 3 or
            any(isinstance(value, bool) for value in raw_position)):
        raise ValueError('canonical live target position is invalid')
    try:
        position = tuple(float(raw_position[index]) for index in range(3))
    except (TypeError, ValueError, OverflowError, IndexError):
        raise ValueError('canonical live target position is invalid')
    if any(math.isnan(value) or math.isinf(value) for value in position):
        raise ValueError('canonical live target position must be finite')
    result['aim_position'] = position
    result['face_position'] = position
    if result.get('combat_mode') == 'advance_contact':
        result['move_position'] = position
    return result


def _server_order_signature(order):
    """Return the strategic part of one server order.

    The server deliberately excludes a live target's aim/face position (and
    the moving advance goal) from its order revision signature.  Those values
    are overlaid from the current target pose every render frame, so treating
    them as a new strategic decision only flushes valid perception caches.
    """
    result = dict(order or {})
    if (result.get('target_id') is not None and
            bool(result.get('fire_allowed'))):
        result.pop('aim_position', None)
        result.pop('face_position', None)
        if result.get('combat_mode') == 'advance_contact':
            result.pop('move_position', None)
    return result


class BotRuntime(object):
    """Produces v5 ``bot_manifest`` and ``bot_state`` payloads without entities."""

    @staticmethod
    def _adapt_direction_probe(probe):
        """Adapt injected legacy test probes once, never after side effects.

        The pinned production callback has four arguments. Catching TypeError
        around every invocation used to mistake an exception from inside that
        callback for an old arity and execute it again, which is unsafe for a
        native collision seam. Python functions expose an exact code object;
        opaque/native callables must honor the current four-argument contract.
        """
        target = getattr(
            probe, 'im_func', getattr(probe, '__func__', probe))
        code = getattr(target, 'func_code', getattr(target, '__code__', None))
        if code is None:
            return probe
        argument_count = int(code.co_argcount)
        bound_self = getattr(
            probe, 'im_self', getattr(probe, '__self__', None))
        if bound_self is not None:
            argument_count -= 1
        has_varargs = bool(code.co_flags & 0x04)
        if has_varargs or argument_count >= 4:
            return probe
        if argument_count == 3:
            return lambda position, yaw, speed, unused_descriptor: probe(
                position, yaw, speed)
        if argument_count == 2:
            return lambda position, yaw, unused_speed, unused_descriptor: probe(
                position, yaw)
        raise ValueError('direction probe must accept 2, 3 or 4 arguments')

    def __init__(self, local_player_id, descriptor_resolver=None,
                 direction_probe=None, adapter_factory=None,
                 vehicle_selector=None, visibility_probe=None,
                 firing_lane_probe=None,
                 ballistic_solution_probe=None,
                 artillery_launch_probe=None,
                 artillery_launch_cancel=None,
                 spawn_resolver=None, ground_probe=None,
                 physics_ground_probe=None,
                 obstacle_probe=None, bounds=None, cover_probe=None,
                 native_motion=False, baked_graph=None, probe_clock=None,
                 motion_resolver=None, world_receipt_probe=None):
        self.local_player_id = local_player_id
        self.descriptor_resolver = descriptor_resolver or (lambda unused: {})
        self.direction_probe = self._adapt_direction_probe(
            direction_probe or (lambda *unused: True))
        self.adapter_factory = adapter_factory or BotAdapter
        self.vehicle_selector = vehicle_selector or (
            lambda raw: raw.get('vehicle') or 'ussr:R11_MS-1')
        self.visibility_probe = visibility_probe or (
            lambda unused_source, unused_target: True)
        # The production #1513 adapter uses the same static collision ray for
        # spotting and shooting, but they are separate decisions and caches.
        # Keeping an explicit seam also makes it impossible for a stale team
        # spot to stand in for a current clear barrel lane.
        self.firing_lane_probe = firing_lane_probe or self.visibility_probe
        # SPG solutions are completed by BattleRuntime's bounded native arc
        # queue.  Returning None means pending or fail-closed; a dict is a
        # fully probed physical solution shared by aiming and firing.
        self.ballistic_solution_probe = ballistic_solution_probe
        # BattleRuntime owns the native HP_gunFire origin. This second seam
        # publishes a frozen receipt only after the next deterministic,
        # dispersed SPG trajectory itself has passed the bounded arc queue.
        self.artillery_launch_probe = artillery_launch_probe
        self.artillery_launch_cancel = artillery_launch_cancel
        self.spawn_resolver = spawn_resolver
        self._injected_baked_graph = baked_graph
        self.baked_graph = None
        self._navigation_map_name = None
        self._navigation_error = None
        self._ground_probe = ground_probe
        self._physics_ground_probe = physics_ground_probe
        self._obstacle_probe = obstacle_probe
        self._navigation_bounds = bounds
        self.navigator = (TerrainNavigator(
            ground_probe, obstacle_probe, bounds, 18.0)
            if callable(ground_probe) else None)
        self.cover_probe = cover_probe
        self.native_motion = bool(native_motion)
        self.motion_resolver = motion_resolver
        self.world_receipt_probe = world_receipt_probe
        self._probe_clock = probe_clock if callable(probe_clock) else None
        self.adapter = None
        self.authority_id = None
        self.round_id = None
        self.states = {}
        self._accumulator = 0.0
        self._manifest_sent = False
        self._descriptors = {}
        self._gun_yaw_limits = {}
        self._gun_states = {}
        self._ammo_states = {}
        self._artillery_intents = {}
        self._artillery_reproofs = {}
        self._shot_los_cache = {}
        self._shot_los_deadlines = {}
        self._physics_params = {}
        self._player_vehicle_profiles = {}
        self._player_collision_profiles = {}
        self._spotting_profiles = {}
        self._visibility_fire = {}
        self._turn_speeds = {}
        self._ram_cooldowns = {}
        self._ram_seq = 0
        self.finished = False
        self._visibility_cache = {}
        self._server_orders = {}
        self._server_order_tokens = {}
        self._order_revision = -1
        self._next_observation = 0.0
        self._next_publication = 0.0
        self._pending_ram_reports = []
        self._cover_cursor = 0
        self._cover_queue = []
        self._cover_results = []
        self._decision_cache = {}
        self._motion_probe_cache = {}
        self._flip_diary = {}
        self.debug_logging = False
        self._camera_position = None
        self._integration_debt = {}
        self._integration_next = {}
        self._last_step = {}
        self._world_receipt_budget = 0
        self._world_receipt_waiting = []
        self._world_receipt_frame = None
        self._combat_sync = {}
        self._server_tick = -1
        # These monotonic, pull-only totals are diagnostic data.  They never
        # enter a LAN payload or feed a scheduler/cache decision.
        self._probe_totals = [0, 0, 0, 0, 0]
        self._probe_duration_totals = [0.0, 0.0, 0.0, 0.0, 0.0]

    def probe_totals(self):
        """Return logical native-query totals without resetting any state."""
        return tuple(self._probe_totals)

    def probe_duration_totals(self):
        """Return measured query time without resetting or driving work."""
        return tuple(self._probe_duration_totals)

    def _probe_started(self):
        if self._probe_clock is None:
            return None
        try:
            return float(self._probe_clock())
        except Exception:
            # Diagnostics must never change or terminate gameplay.
            self._probe_clock = None
            return None

    def _probe_finished(self, index, started):
        if started is None or self._probe_clock is None:
            return
        try:
            elapsed = float(self._probe_clock()) - float(started)
            if (elapsed > 0.0 and not math.isnan(elapsed) and
                    not math.isinf(elapsed)):
                self._probe_duration_totals[index] += elapsed
        except Exception:
            self._probe_clock = None

    def is_authority(self):
        return self.authority_id == self.local_player_id

    def _ensure_navigation_graph(self, map_name):
        """Install a matching immutable graph after the battle map is known."""
        map_name = tactical_maps.normalize_map_name(map_name)
        if not map_name:
            raise ValueError('battle map name is unavailable')
        if self._navigation_map_name == map_name:
            return
        if map_name not in prebaked_navigation.SUPPORTED_MAPS:
            raise ValueError(
                'standard battle map is not supported: %s' % map_name)
        graph = None
        injected = self._injected_baked_graph
        if isinstance(injected, dict):
            injected_name = tactical_maps.normalize_map_name(
                injected.get('map'))
            if injected_name == map_name:
                graph = injected
        if graph is None:
            graph = prebaked_navigation.load_graph(map_name)
        if graph is None:
            raise ValueError(
                'required navigation graph is missing for %s' % map_name)
        prebaked_navigation._validate(graph, map_name)
        self._validated_baked_routes(graph)
        if not callable(self._ground_probe):
            raise ValueError(
                'navigation ground probe is unavailable for %s' % map_name)
        if not callable(self._physics_ground_probe):
            raise ValueError(
                'physics ground probe is unavailable for %s' % map_name)
        try:
            navigator = TerrainNavigator(
                self._ground_probe, self._obstacle_probe,
                self._navigation_bounds, 18.0, baked_graph=graph)
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError(
                'required navigation graph cannot be installed for %s: %s' %
                (map_name, error))
        self._navigation_map_name = map_name
        self._navigation_error = None
        self.baked_graph = graph
        self.navigator = navigator

    @staticmethod
    def _validated_baked_routes(graph):
        routes = graph.get('routes') if isinstance(graph, dict) else None
        if not isinstance(routes, dict):
            raise ValueError('navigation graph routes are missing')
        for team in (1, 2):
            values = routes.get(str(team), routes.get(team))
            if not isinstance(values, (list, tuple)) or not values:
                raise ValueError(
                    'navigation graph routes are missing for team %d' % team)
            for route in values:
                if not isinstance(route, dict):
                    raise ValueError('navigation graph route is invalid')
                waypoints = route.get('waypoints')
                if (not isinstance(waypoints, (list, tuple)) or
                        not waypoints or len(waypoints) > 16):
                    raise ValueError(
                        'navigation graph route waypoint count is invalid')
                for point in waypoints:
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
                    try:
                        x = float(point[0])
                        z = float(point[1])
                    except (TypeError, ValueError, IndexError):
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
                    if (x != x or z != z or abs(x) == float('inf') or
                            abs(z) == float('inf')):
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
        return routes

    def _new_adapter(self, map_name, round_id):
        """Keep custom two-argument factories compatible with the graph seam."""
        baked_routes = (self.baked_graph or {}).get('routes')
        if (not isinstance(baked_routes, dict) or not any(
                baked_routes.get(key) for key in (1, 2, '1', '2'))):
            return self.adapter_factory(map_name, round_id)
        if self.adapter_factory is BotAdapter:
            return self.adapter_factory(map_name, round_id,
                                        baked_routes=baked_routes)
        try:
            return self.adapter_factory(map_name, round_id,
                                        baked_routes=baked_routes)
        except TypeError:
            return self.adapter_factory(map_name, round_id)

    def _clear(self, position, yaw, speed=0.0, descriptor=None):
        """Treat collision, excessive slope and water as a failed local ray."""
        return self._probe_is_clear(
            self._probe_direction(position, yaw, speed, descriptor))

    def _probe_direction(self, position, yaw, speed=0.0, descriptor=None):
        """Return one canonical direction sample for planning and physics."""
        self._probe_totals[4] += 1
        probe_started = self._probe_started()
        try:
            result = self.direction_probe(position, yaw, speed, descriptor)
        except Exception:
            return {'clear': False, 'collision': True,
                    'water': False, 'slope': 0.0}
        finally:
            self._probe_finished(4, probe_started)
        return result

    def _begin_world_receipt_frame(self):
        """Reserve receipt work for previously eligible deferred Bots."""
        waiting = []
        seen = set()
        for entry in self._world_receipt_waiting:
            bot_id, uncached = entry
            bot_id = int(bot_id)
            state = self.states.get(bot_id)
            if (bot_id not in seen and state is not None and
                    state.get('alive', True)):
                waiting.append((bot_id, bool(uncached)))
                seen.add(bot_id)
        initial_waiting = [
            bot_id for bot_id, initial in waiting if initial]
        priority_source = (initial_waiting if initial_waiting else
                           [bot_id for bot_id, unused in waiting])
        self._world_receipt_budget = MAX_WORLD_RECEIPTS_PER_FRAME
        self._world_receipt_frame = {
            'waiting': tuple(waiting),
            'waiting_initial': dict(waiting),
            'priority': set(
                priority_source[:MAX_WORLD_RECEIPTS_PER_FRAME]),
            'initial_first': bool(initial_waiting),
            'requested': [],
            'requested_set': set(),
            'request_uncached': {},
            'attempted': set(),
            'attempt_deferred': [],
        }

    def _finish_world_receipt_frame(self):
        """Rotate real deferred requests without retaining ineligible Bots."""
        frame = self._world_receipt_frame
        if not isinstance(frame, dict):
            return
        requested = frame['requested_set']
        attempted = frame['attempted']
        next_waiting = []
        seen = set()

        def append_once(bot_id, uncached):
            bot_id = int(bot_id)
            if bot_id not in seen:
                next_waiting.append((bot_id, bool(uncached)))
                seen.add(bot_id)

        # Preserve the established queue for eligible requests which did not
        # receive a native job. New requests follow in encounter order. A Bot
        # whose native callback itself deferred rotates behind both cohorts.
        request_uncached = frame['request_uncached']
        for bot_id, unused_previous_uncached in frame['waiting']:
            if bot_id in requested and bot_id not in attempted:
                append_once(bot_id, request_uncached.get(bot_id, False))
        for bot_id in frame['requested']:
            if bot_id not in attempted:
                append_once(bot_id, request_uncached.get(bot_id, False))
        deferred = set(frame['attempt_deferred'])
        for bot_id, unused_previous_uncached in frame['waiting']:
            if bot_id in deferred:
                append_once(bot_id, False)
        for bot_id in frame['requested']:
            if bot_id in deferred:
                append_once(bot_id, False)
        self._world_receipt_waiting = next_waiting
        self._world_receipt_frame = None

    def _probe_world_receipt(self, bot_id, position, yaw, speed, descriptor,
                             uncached):
        """Run one read-only exact-hull proof for the selected travel ray."""
        if not callable(self.world_receipt_probe):
            return None
        bot_id = int(bot_id)
        frame = self._world_receipt_frame
        if not isinstance(frame, dict):
            return 'deferred'
        if bot_id not in frame['requested_set']:
            frame['requested'].append(bot_id)
            frame['requested_set'].add(bot_id)
            waiting_initial = frame['waiting_initial']
            frame['request_uncached'][bot_id] = (
                waiting_initial[bot_id] if bot_id in waiting_initial else
                bool(uncached))
        priority = frame['priority']
        if (frame['initial_first'] and
                not frame['request_uncached'][bot_id]):
            return 'deferred'
        if priority and bot_id not in priority:
            return 'deferred'
        priority.discard(bot_id)
        if self._world_receipt_budget <= 0:
            return 'deferred'
        self._world_receipt_budget -= 1
        frame['attempted'].add(bot_id)
        self._probe_totals[4] += 1
        probe_started = self._probe_started()
        try:
            result = self.world_receipt_probe(
                position, yaw, speed, descriptor)
        except Exception:
            # A receipt is only an optimisation.  Its absence restores the
            # authoritative per-frame world sweep and never grants movement.
            return None
        finally:
            self._probe_finished(4, probe_started)
        if result == 'deferred':
            frame['attempt_deferred'].append(bot_id)
        return result

    @staticmethod
    def _probe_is_clear(result):
        if isinstance(result, dict):
            if not result.get('clear', True) or result.get('collision', False):
                return False
            if result.get('water', False):
                return False
            return abs(_number(result.get('slope', 0.0))) <= 0.55
        return bool(result)

    def battle_start(self, message):
        """Build a local authority manifest from the server roster once per round."""
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id')
        if round_id != self.round_id:
            self.round_id = round_id
            self.states = {}
            self._accumulator = 0.0
            self._manifest_sent = False
            self._descriptors = {}
            self._gun_yaw_limits = {}
            self._gun_states = {}
            self._ammo_states = {}
            self._clear_artillery_intents()
            self._shot_los_cache = {}
            self._shot_los_deadlines = {}
            self._physics_params = {}
            self._player_vehicle_profiles = {}
            self._player_collision_profiles = {}
            self._spotting_profiles = {}
            self._visibility_fire = {}
            self._turn_speeds = {}
            self._ram_cooldowns = {}
            self._ram_seq = 0
            self.adapter = None
            self.finished = False
            self._visibility_cache = {}
            self._server_orders = {}
            self._server_order_tokens = {}
            self._order_revision = -1
            self._next_observation = 0.0
            self._next_publication = 0.0
            self._pending_ram_reports = []
            self._cover_cursor = 0
            self._cover_queue = []
            self._cover_results = []
            self._decision_cache = {}
            self._motion_probe_cache = {}
            self._world_receipt_waiting = []
            self._world_receipt_frame = None
            self._combat_sync = {}
            self._server_tick = -1
        self._apply_orders(message)
        if message.get('battle_result') is not None:
            self.finished = True
            self._clear_artillery_intents()
        previous_authority = self.authority_id
        self.authority_id = message.get('bot_authority_id')
        authority_handoff = (
            previous_authority is not None and
            previous_authority != self.authority_id and
            self.is_authority() and
            isinstance(message.get('bot_manifest'), (list, tuple)))
        if previous_authority != self.authority_id:
            self._clear_artillery_intents()
            self._visibility_cache = {}
            self._visibility_fire = {}
            self._shot_los_cache = {}
            self._shot_los_deadlines = {}
            self._decision_cache = {}
            self._motion_probe_cache = {}
            self._world_receipt_waiting = []
            self._world_receipt_frame = None
            self._pending_ram_reports = []
            self._cover_queue = []
            self._cover_results = []
            self._next_publication = 0.0
            if self.is_authority():
                self._manifest_sent = False
        if authority_handoff:
            # The takeover manifest is an explicit server-authority boundary.
            # Existing combat sync entries may still be based on an older
            # snapshot than publications already accepted from the previous
            # authority, so keep a per-bot handoff window until one canonical
            # acknowledgement resolves that overlap.
            for sync in self._combat_sync.values():
                sync['authority_handoff_pending'] = True
        if not self.is_authority():
            return []
        if self.finished:
            return []
        if self.adapter is None:
            self._ensure_navigation_graph(message.get('map', ''))
            self.adapter = self._new_adapter(message.get('map', ''),
                                             round_id or 0)
            if self.navigator is not None:
                self.adapter.navigation_target = self._navigation_target
        manifest = message.get('bot_manifest') or message.get('bots') or []
        for raw in manifest:
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            bot_id = int(raw['id'])
            vehicle_name = self.vehicle_selector(raw)
            descriptor = self.descriptor_resolver(vehicle_name)
            half_length, half_width = _hull_dimensions(descriptor)
            self._descriptors.setdefault(bot_id, descriptor)
            self._gun_yaw_limits.setdefault(
                bot_id, ai_driver.gun_yaw_limits(descriptor))
            if bot_id not in self._gun_states:
                self._gun_states[bot_id] = _BotGunState(
                    descriptor, raw.get('fire_seq', 0))
            self._physics_params[bot_id] = vehicle_physics.derive_params(
                descriptor)
            self._turn_speeds[bot_id] = 0.0
            if isinstance(raw.get('profile'), dict):
                self.adapter.director.register_profile(bot_id, raw.get('team', 1),
                                                       raw['profile'], raw.get('name', 'Bot'))
            else:
                self.adapter.register(bot_id, raw.get('team', 1), descriptor,
                                      raw.get('name', 'Bot'))
            spawn, spawn_yaw = self._spawn(
                int(raw.get('team', 1)), int(raw.get('slot', 0)))
            agents = getattr(self.adapter.director, 'agents', {})
            agent = agents.get(bot_id, {})
            profile = agent.get('profile', {})
            route = agent.get('route') or {}
            max_health = max(1, int(getattr(descriptor, 'maxHealth',
                                            raw.get('max_health', 1000))))
            health = max(0, min(
                int(_number(raw.get('health'), max_health)), max_health))
            self.states.setdefault(bot_id, {
                'id': bot_id, 'team': int(raw.get('team', 1)),
                'slot': int(raw.get('slot', 0)), 'name': raw.get('name', 'Bot'),
                'vehicle': vehicle_name,
                'x': _number(raw.get('x'), spawn[0]),
                'y': _number(raw.get('y'), spawn[1]),
                'z': _number(raw.get('z'), spawn[2]),
                'yaw': _number(raw.get('yaw'), spawn_yaw),
                'aim_yaw': _number(raw.get('yaw'), spawn_yaw),
                'turret_yaw': 0.0, 'gun_pitch': 0.0,
                'desired_gun_pitch': 0.0,
                'gun_aligned': False, 'hull_aiming': False,
                'health': health, 'max_health': max_health,
                'alive': bool(raw.get('alive', health > 0)) and health > 0,
                'fire_seq': max(0, int(_number(raw.get('fire_seq'), 0))),
                'shell_index': max(0, min(
                    int(_number(raw.get('shell_index'), 0)), 9)),
                'speed': _number(raw.get('speed')),
                'movement_dir': 0, 'rotation_dir': 0,
                'move_speed': _forward_speed(descriptor),
                'view_range': _view_range(descriptor),
                'half_length': half_length, 'half_width': half_width,
                'collision_shape': _collision_shape(descriptor),
                'mass': self._physics_params[bot_id]['mass'],
                'push_x': 0.0, 'push_z': 0.0,
                'vertical_speed': 0.0, 'airborne': False,
                'grounded_once': False, 'last_drive_pitch': 0.0,
                'pitch': 0.0, 'roll': 0.0,
                'critical': (dict(raw.get('critical'))
                             if isinstance(raw.get('critical'), dict) else {}),
                'combat_revision': max(0, int(_number(
                    raw.get('combat_revision'), 0))),
                'combat_base_revision': max(0, int(_number(
                    raw.get('combat_base_revision'), 0))),
                'combat_ack_seq': max(0, int(_number(
                    raw.get('combat_ack_seq'), 0))),
                'combat_fire_elapsed': round(max(0.0, min(
                    FIRE_DURATION_SECONDS, _number(
                        raw.get('combat_fire_elapsed'), 0.0))), 6),
                'combat_fire_timer': round(max(0.0, min(
                    FIRE_TICK_SECONDS - 0.000001, _number(
                        raw.get('combat_fire_timer'), 0.0))), 6),
                'profile': profile, 'route': route,
            })
            gun_state = self._gun_states[bot_id]
            state = self.states[bot_id]
            ammo_state = self._ammo_states.get(bot_id)
            if ammo_state is None:
                ammo_state = _BotAmmoState(descriptor, profile, raw)
                self._ammo_states[bot_id] = ammo_state
            elif authority_handoff:
                ammo_state.restore(raw)
                gun_state.restore_fire_seq(max(
                    int(state.get('fire_seq', 0)),
                    int(_number(raw.get('fire_seq', 0)))))
            ammo_state.publish(state)
            if authority_handoff:
                self._apply_authority_takeover_motion(state, raw)
            sync = self._combat_sync_state(state)
            if authority_handoff:
                sync['authority_handoff_pending'] = True
            reload_factor = _critical_factor(state, descriptor, 'reload')
            state['clip_size'] = gun_state.clip_size
            state['clip'] = gun_state.clip
            state['reload_time'] = gun_state.remaining(reload_factor)
            state['reload_duration'] = (
                gun_state.reload_duration * reload_factor)
        if self._manifest_sent:
            return []
        bots = [self._manifest_entry(state)
                for state in self._ordered_states()]
        self._manifest_sent = True
        return [{'type': 'bot_manifest', 'bots': bots}]

    def _combat_sync_state(self, state):
        bot_id = int(state['id'])
        sync = self._combat_sync.get(bot_id)
        if sync is None:
            signature = _combat_signature(state)
            revision = max(0, int(_number(
                state.get('combat_revision'), 0)))
            base_revision = max(0, int(_number(
                state.get('combat_base_revision'), 0)))
            acked_seq = max(0, int(_number(
                state.get('combat_ack_seq'), 0)))
            sync = {
                'server_signature': signature,
                'server_combat': _combat_record(state),
                'published_signature': signature,
                'pending': [],
                'next_seq': acked_seq,
                'acked_seq': acked_seq,
                'combat_revision': revision,
                'base_revision': base_revision,
                'server_tick': -1,
                'unpublished_steps': [],
                'authority_handoff_pending': False,
            }
            self._combat_sync[bot_id] = sync
        state['combat_revision'] = sync['combat_revision']
        state['combat_base_revision'] = sync['base_revision']
        state['combat_ack_seq'] = sync['acked_seq']
        state['combat_seq'] = sync['next_seq']
        return sync

    def _apply_authority_takeover_motion(self, state, raw):
        """Rebase one resumed authority on the server's canonical pose.

        ``apply_snapshot`` deliberately never rewinds an active authority's
        locally integrated pose.  The same rule cannot apply after authority
        was lost: on handback the merged manifest is the only canonical pose
        boundary, and retaining the old local state rewinds the battle to the
        point where this client stopped simulating.  Copy only pose, aim and
        motion here; combat continues through the existing revision/ack
        reconciliation below.
        """
        yaw = _number(raw.get('yaw'), state.get('yaw'))
        aim_yaw = _number(raw.get('aim_yaw'), yaw)
        gun_pitch = _number(raw.get('gun_pitch'), 0.0)
        state['x'] = _number(raw.get('x'), state.get('x'))
        state['y'] = _number(raw.get('y'), state.get('y'))
        state['z'] = _number(raw.get('z'), state.get('z'))
        state['yaw'] = yaw
        state['aim_yaw'] = aim_yaw
        state['turret_yaw'] = _angle_delta(aim_yaw, yaw)
        state['gun_pitch'] = gun_pitch
        state['desired_gun_pitch'] = gun_pitch
        state['gun_aligned'] = False
        state['hull_aiming'] = False
        # Current LAN snapshots carry intent but not a velocity magnitude.
        # Resume from rest unless a later protocol explicitly supplies one;
        # stale pre-handoff momentum is not server-canonical state.
        state['speed'] = _number(raw.get('speed'), 0.0)
        movement = _number(raw.get('movement_dir'))
        rotation = _number(raw.get('rotation_dir'))
        state['movement_dir'] = (
            1 if movement > 0.01 else (-1 if movement < -0.01 else 0))
        state['rotation_dir'] = (
            1 if rotation > 0.01 else (-1 if rotation < -0.01 else 0))
        state['push_x'] = 0.0
        state['push_z'] = 0.0
        state['vertical_speed'] = 0.0
        state['airborne'] = False
        state['grounded_once'] = False
        state['last_drive_pitch'] = 0.0
        self._turn_speeds[int(state['id'])] = 0.0
        return True

    def _mark_combat_publication(self, state):
        sync = self._combat_sync_state(state)
        signature = _combat_signature(state)
        if signature == sync['published_signature']:
            return False
        sync['next_seq'] += 1
        sync['pending'].append({
            'seq': sync['next_seq'],
            'signature': signature,
            'combat': _combat_record(state),
            'steps': list(sync['unpublished_steps']),
        })
        sync['unpublished_steps'] = []
        sync['published_signature'] = signature
        state['combat_seq'] = sync['next_seq']
        return True

    def _apply_server_combat_state(self, state, raw, server_tick):
        """Reconcile an explicit server base/revision/ack boundary.

        Signatures validate an acknowledged publication; they never decide
        whether the server consumed it.  ``combat_ack_seq`` is the sole answer
        to that question.  When an external hit opens a new base, only the
        unacknowledged repair/fire time slices are replayed on the new canonical
        state.
        """
        sync = self._combat_sync_state(state)
        if server_tick is not None and server_tick < sync['server_tick']:
            return False
        candidate = dict(state)
        candidate['health'] = max(0, min(
            int(_number(raw.get('health'), state['health'])),
            int(state['max_health'])))
        candidate['alive'] = (
            bool(raw.get('alive', candidate['health'] > 0)) and
            candidate['health'] > 0)
        contract = ('critical', 'combat_revision', 'combat_base_revision',
                    'combat_ack_seq', 'combat_fire_elapsed',
                    'combat_fire_timer')
        if not all(name in raw for name in contract):
            raise ValueError('modern bot snapshot combat contract is missing')
        if not isinstance(raw['critical'], dict):
            raise ValueError('modern bot snapshot critical state is invalid')
        try:
            revision = int(raw['combat_revision'])
            base_revision = int(raw['combat_base_revision'])
            acked_seq = int(raw['combat_ack_seq'])
            fire_elapsed = float(raw['combat_fire_elapsed'])
            fire_timer = float(raw['combat_fire_timer'])
            exact = (
                not isinstance(raw['combat_revision'], bool) and
                not isinstance(raw['combat_base_revision'], bool) and
                not isinstance(raw['combat_ack_seq'], bool) and
                not isinstance(raw['combat_fire_elapsed'], bool) and
                not isinstance(raw['combat_fire_timer'], bool) and
                float(raw['combat_revision']) == revision and
                float(raw['combat_base_revision']) == base_revision and
                float(raw['combat_ack_seq']) == acked_seq and
                not math.isnan(fire_elapsed) and
                not math.isinf(fire_elapsed) and
                not math.isnan(fire_timer) and
                not math.isinf(fire_timer))
        except (TypeError, ValueError, OverflowError):
            exact = False
        if (not exact or revision < 0 or base_revision < 0 or
                acked_seq < 0 or base_revision > revision or
                fire_elapsed < 0.0 or
                fire_elapsed > FIRE_DURATION_SECONDS or
                fire_timer < 0.0 or fire_timer >= FIRE_TICK_SECONDS):
            raise ValueError('modern bot snapshot combat contract is invalid')
        candidate['critical'] = _canonical_critical(raw['critical'])
        if (not candidate['critical'].get('fire', False) and
                (fire_elapsed != 0.0 or fire_timer != 0.0)):
            raise ValueError('inactive bot fire has a non-zero clock')
        candidate['combat_fire_elapsed'] = round(fire_elapsed, 6)
        candidate['combat_fire_timer'] = round(fire_timer, 6)
        candidate_record = _combat_record(candidate)
        signature = _combat_signature(candidate)

        acknowledged = None
        if acked_seq > sync['acked_seq']:
            for pending in sync['pending']:
                if pending['seq'] == acked_seq:
                    acknowledged = pending
                    break
        handoff_pending = sync.get('authority_handoff_pending', False)
        handoff_new_base = (
            handoff_pending and
            base_revision > sync['base_revision'])
        handoff_same_base_overlap = (
            handoff_pending and
            base_revision == sync['base_revision'] and
            acked_seq > sync['acked_seq'] and
            (acked_seq > sync['next_seq'] or
             (acknowledged is not None and
              acknowledged['signature'] != signature)))
        handoff_canonical_reset = (
            handoff_new_base or handoff_same_base_overlap)
        if (revision < sync['combat_revision'] or
                base_revision < sync['base_revision'] or
                acked_seq < sync['acked_seq'] or
                (acked_seq > sync['next_seq'] and
                 not handoff_canonical_reset)):
            raise ValueError('server bot combat revision moved backwards')

        if handoff_canonical_reset:
            if revision <= sync['combat_revision']:
                raise ValueError('server bot combat handoff ack is inconsistent')
            # A promoted authority may start from the last snapshot it consumed
            # while the server has already accepted later publications from the
            # old authority.  Those sequence numbers overlap any work started
            # locally during the promotion window.  A new base also makes every
            # local pending step a derivative of the superseded baseline, so no
            # step can be identified safely for replay.  Treat either case as
            # an explicit canonical handoff reset and resume after the server
            # ack.
            _apply_combat_record(state, candidate_record)
            sync['server_signature'] = signature
            sync['server_combat'] = candidate_record
            sync['published_signature'] = signature
            sync['pending'] = []
            sync['unpublished_steps'] = []
            sync['next_seq'] = acked_seq
            sync['acked_seq'] = acked_seq
            sync['combat_revision'] = revision
            sync['base_revision'] = base_revision
            sync['authority_handoff_pending'] = False
            state['combat_revision'] = revision
            state['combat_base_revision'] = base_revision
            state['combat_ack_seq'] = acked_seq
            state['combat_seq'] = acked_seq
            if server_tick is not None:
                sync['server_tick'] = server_tick
            return True

        if base_revision == sync['base_revision']:
            if acked_seq == sync['acked_seq']:
                if (revision != sync['combat_revision'] or
                        signature != sync['server_signature']):
                    raise ValueError(
                        'server changed bot combat without a publication ack')
            else:
                if (acknowledged is None or
                        acknowledged['signature'] != signature or
                        revision <= sync['combat_revision']):
                    raise ValueError('server bot combat ack is inconsistent')
                sync['pending'] = [
                    pending for pending in sync['pending']
                    if pending['seq'] > acked_seq]
                # A matching publication from this authority is an ordered
                # barrier: no accepted state from the previous authority can
                # remain unresolved after it.
                sync['authority_handoff_pending'] = False
            sync['server_signature'] = signature
            sync['server_combat'] = candidate_record
            sync['acked_seq'] = acked_seq
            sync['combat_revision'] = revision
            state['combat_revision'] = revision
            state['combat_base_revision'] = base_revision
            state['combat_ack_seq'] = acked_seq
            state['combat_seq'] = sync['next_seq']
            if server_tick is not None:
                sync['server_tick'] = server_tick
            return False

        if revision < base_revision or revision <= sync['combat_revision']:
            raise ValueError('new bot combat base has no canonical revision')
        boundary = None
        if acked_seq == sync['acked_seq']:
            boundary_record = sync['server_combat']
        else:
            for pending in sync['pending']:
                if pending['seq'] == acked_seq:
                    boundary = pending
                    break
            if boundary is None:
                raise ValueError('new bot combat base has an unknown ack')
            boundary_record = boundary['combat']

        replay_steps = []
        for pending in sync['pending']:
            if pending['seq'] > acked_seq:
                replay_steps.extend(pending['steps'])
        replay_steps.extend(sync['unpublished_steps'])

        _apply_combat_record(state, candidate_record)
        sync['server_signature'] = signature
        sync['server_combat'] = candidate_record
        sync['published_signature'] = signature
        sync['pending'] = []
        sync['unpublished_steps'] = []
        sync['next_seq'] = acked_seq
        sync['acked_seq'] = acked_seq
        sync['combat_revision'] = revision
        sync['base_revision'] = base_revision
        sync['authority_handoff_pending'] = False

        for replay_step, replay_now, replay_fire in replay_steps:
            self._advance_bot_critical(
                state, replay_step, replay_now, record_step=False,
                advance_fire=replay_fire)
        replayed_signature = _combat_signature(state)
        if replayed_signature != signature:
            # A replay is local work, not a wire publication. Reserving its
            # sequence here makes the next real publication skip that unseen
            # proposal, after which the server rejects every later full-state
            # update as out of order. Retain the replay slices as unpublished
            # lineage; the next bot_state coalesces them with that frame's
            # repair/fire advancement into exactly one ack+1 proposal. If a
            # newer base arrives first, these same slices remain available for
            # another canonical rebase.
            sync['unpublished_steps'] = list(replay_steps)
        else:
            # The explicit fire clock is part of the durable signature.  A
            # replay slice that changed neither combat state nor clock is a
            # completed no-op and must not leak into a later lineage.
            sync['unpublished_steps'] = []
        state['combat_revision'] = revision
        state['combat_base_revision'] = base_revision
        state['combat_ack_seq'] = acked_seq
        state['combat_seq'] = sync['next_seq']
        if server_tick is not None:
            sync['server_tick'] = server_tick
        return True

    def _advance_bot_critical(self, state, step, now, record_step=True,
                              advance_fire=True):
        payload = state.get('critical')
        if not isinstance(payload, dict) or not payload:
            return False
        before_signature = _combat_signature(state)
        was_on_fire = bool(payload.get('fire', False))
        sync = self._combat_sync_state(state)
        descriptor = self._descriptors.get(state['id'], {})
        fire_elapsed = round(max(0.0, min(
            FIRE_DURATION_SECONDS,
            _number(state.get('combat_fire_elapsed')))), 6)
        fire_timer = round(max(0.0, min(
            FIRE_TICK_SECONDS - 0.000001,
            _number(state.get('combat_fire_timer')))), 6)
        fire_started = (
            float(now) - min(
                FIRE_DURATION_SECONDS, fire_elapsed + float(step))
            if was_on_fire and advance_fire else None)
        shadow = _BotCriticalVehicle(
            state, descriptor, fire_started, fire_timer)
        repair_payload = critical_damage.tick_repair(shadow, step)
        fire_damage = 0
        fire_payload = None
        if advance_fire:
            fire_damage, fire_payload = critical_damage.tick_fire(
                shadow, step, now=now)
        if was_on_fire and advance_fire and shadow.is_on_fire:
            state['combat_fire_elapsed'] = round(min(
                FIRE_DURATION_SECONDS, fire_elapsed + float(step)), 6)
            state['combat_fire_timer'] = round(
                max(0.0, min(FIRE_TICK_SECONDS - 0.000001,
                             shadow._fire_timer)), 6)
        elif not shadow.is_on_fire:
            state['combat_fire_elapsed'] = 0.0
            state['combat_fire_timer'] = 0.0
        durable = fire_payload or repair_payload
        if durable is not None:
            state['critical'] = _canonical_critical(durable)
        if fire_damage > 0:
            state['health'] = max(
                0, int(state['health']) - int(fire_damage))
            state['alive'] = state['health'] > 0
            state['display_health'] = state['health']
            if not state['alive']:
                state['death_reason'] = 1
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
                state['target_kind'] = None
                state['target_id'] = None
        changed = _combat_signature(state) != before_signature
        if (record_step and
                (changed or was_on_fire or
                 bool((state.get('critical') or {}).get('fire', False)))):
            sync['unpublished_steps'].append(
                (float(step), float(now), bool(was_on_fire)))
        return changed

    def apply_snapshot(self, message):
        """Apply only server-owned combat state to the authority simulation.

        Bot poses remain locally simulated to avoid feeding a delayed echo back
        into steering.  Health and alive state are server authoritative because
        hits may be reported by other clients or by the authority's collision
        resolver after the local AI tick that fired the shot.
        """
        message = message if isinstance(message, dict) else {}
        self._apply_orders(message)
        if message.get('battle_result') is not None:
            self.finished = True
            self._clear_artillery_intents()
        server_tick = message.get('server_tick')
        if server_tick is not None:
            try:
                server_tick = int(server_tick)
            except (TypeError, ValueError):
                raise ValueError('bot snapshot server_tick is invalid')
            if server_tick < self._server_tick:
                return
            self._server_tick = server_tick
        for raw in message.get('bots') or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                state = self.states.get(int(raw['id']))
            except (TypeError, ValueError):
                continue
            if state is None:
                continue
            self._apply_server_combat_state(state, raw, server_tick)
            previous_fire_seq = int(state.get('fire_seq', 0))
            previous_shell_index = int(state.get('shell_index', 0))
            incoming_fire_seq = max(
                0, int(_number(raw.get('fire_seq'), 0)))
            state['fire_seq'] = max(previous_fire_seq, incoming_fire_seq)
            if incoming_fire_seq > previous_fire_seq:
                gun_state = self._gun_states.get(state['id'])
                if gun_state is not None:
                    gun_state.restore_fire_seq(incoming_fire_seq)
                    descriptor = self._descriptors.get(state['id'], {})
                    reload_factor = _critical_factor(
                        state, descriptor, 'reload')
                    state['clip'] = gun_state.clip
                    state['reload_time'] = gun_state.remaining(reload_factor)
                    state['reload_duration'] = (
                        gun_state.reload_duration * reload_factor)
            ammo_contract = all(name in raw for name in (
                'ammo_remaining', 'shell_index', 'next_shell_index',
                'ammo_reload_pending'))
            ammo_state = self._ammo_states.get(state['id'])
            if (incoming_fire_seq > previous_fire_seq and ammo_contract and
                    ammo_state is not None):
                ammo_state.restore(raw)
                ammo_state.publish(state)
            elif not ammo_contract:
                # Compatibility for pre-ammunition snapshots. Current clients
                # always carry the finite inventory contract.
                state['shell_index'] = max(0, min(
                    int(_number(raw.get('shell_index'),
                                state.get('shell_index', 0))), 9))
            if (incoming_fire_seq > previous_fire_seq or
                    state['shell_index'] != previous_shell_index or
                    not state['alive']):
                self._cancel_artillery_intent(state['id'])
            if not state['alive']:
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
                state['target_kind'] = None
                state['target_id'] = None

    def _apply_orders(self, message):
        if not isinstance(message, dict) or 'bot_orders' not in message:
            return False
        orders = message.get('bot_orders')
        if not isinstance(orders, (list, tuple)):
            return False
        try:
            revision = int(message.get('bot_order_revision'))
        except (TypeError, ValueError):
            return False
        if revision < self._order_revision:
            return False
        accepted = {}
        for raw in orders[:30]:
            if not isinstance(raw, dict) or raw.get('id') is None:
                return False
            try:
                bot_id = int(raw.get('id'))
            except (TypeError, ValueError):
                return False
            if bot_id in accepted:
                return False
            order = dict(raw)
            # Match the mature 0.8.2 network boundary: JSON point records are
            # converted before any planner, navigator, or driver sees them.
            # Leaving route_anchor as a dict makes TerrainNavigator execute
            # tuple(dict), then float('x'/'y'/'z') on the first live tick.
            for name in ('aim_position', 'face_position', 'move_position',
                         'route_anchor'):
                point = order.get(name)
                if isinstance(point, dict):
                    order[name] = _position(point)
            accepted[bot_id] = order
        previous = self._server_orders
        changed_ids = set(previous).union(accepted)
        changed_ids = set(
            bot_id for bot_id in changed_ids
            if _server_order_signature(previous.get(bot_id)) !=
            _server_order_signature(accepted.get(bot_id)))
        for bot_id in changed_ids:
            self._server_order_tokens[bot_id] = (
                int(self._server_order_tokens.get(bot_id, 0)) + 1)
            self._decision_cache.pop(bot_id, None)
            self._motion_probe_cache.pop(bot_id, None)
        self._server_orders = accepted
        self._order_revision = revision
        return True

    def _manifest_entry(self, state):
        keys = ('id', 'team', 'slot', 'name', 'vehicle', 'health',
                'max_health', 'x', 'y', 'z', 'yaw', 'profile', 'fire_seq',
                'shell_index', 'next_shell_index', 'ammo_remaining',
                'ammo_reload_pending')
        result = dict((key, state[key]) for key in keys)
        # These coordinates were resolved against the loaded retail map by
        # the authority.  Consumers must not run the formation resolver a
        # second time and nudge the same slot away from its canonical pose.
        result['world_pose'] = True
        route = state.get('route') or {}
        waypoints = route.get('waypoints', ()) or ()
        if len(waypoints) > 16:
            raise ValueError(
                'bot route exceeds the 16-waypoint LAN protocol limit')
        result['route'] = {
            'id': route.get('id', 'map_route'),
            'waypoints': [
                {'x': point[0], 'y': 0.0, 'z': point[1],
                 'hold': bool(point[2]) if len(point) > 2 else False}
                for point in waypoints],
        }
        return result

    def _ordered_states(self):
        return sorted(self.states.values(), key=lambda state: (
            int(state.get('slot', 0)), int(state.get('team', 1))))

    def _spawn(self, team, slot):
        if callable(self.spawn_resolver):
            return self.spawn_resolver(team, slot)
        raise ValueError(
            'validated spawn resolver is missing for team %s slot %s' %
            (team, slot))

    def _spotting_profile(self, target):
        kind = target.get('kind')
        target_id = target.get('network_id', target.get('id', 0))
        key = (kind, int(target_id))
        cached = self._spotting_profiles.get(key)
        if cached is not None:
            return cached
        if kind == 'bot':
            descriptor = self._descriptors.get(int(target_id), {})
            cached = (_base_invisibility(descriptor),
                      _shot_invisibility_factor(descriptor))
        else:
            vehicle_profile = self._player_vehicle_profile(target)
            cached = vehicle_profile['spotting']
        self._spotting_profiles[key] = cached
        return cached

    def _target_fired_recently(self, target, now):
        if target.get('fire_seq') is None:
            return False, False, None
        kind = target.get('kind')
        target_id = target.get('network_id', target.get('id', 0))
        key = (kind, int(target_id))
        try:
            fire_seq = max(0, int(target.get('fire_seq')))
        except (TypeError, ValueError):
            return False, False, None
        previous = self._visibility_fire.get(key)
        if previous is None or fire_seq < previous[0]:
            self._visibility_fire[key] = (fire_seq, 0.0)
            return False, False, fire_seq
        if fire_seq > previous[0]:
            deadline = _number(now) + spotting.SHOT_CAMOUFLAGE_SECONDS
            self._visibility_fire[key] = (fire_seq, deadline)
            return True, True, fire_seq
        return _number(now) < previous[1], False, fire_seq

    def _visible(self, source, target, now):
        target_id = target.get('network_id', target.get('id', 0))
        key = (int(source.get('id', 0)), target.get('kind'), int(target_id))
        fired_recently, fire_changed, fire_seq = self._target_fired_recently(
            target, now)
        cached = self._visibility_cache.get(key)
        ttl = (VISIBILITY_MIN_SECONDS +
               ((key[0] * 31 + key[2] * 17) % 11) *
               VISIBILITY_JITTER_SECONDS)
        if (not fire_changed and cached is not None and
                cached[2] == fire_seq and
                _number(now) - cached[0] < ttl):
            return cached[1]
        distance = _distance(_position(source), target.get('position') or
                             _position(target))
        view_range = _number(source.get('view_range'), 330.0)
        if distance <= spotting.PROXIMITY_SPOT_DISTANCE:
            value = True
        elif distance > spotting.MAX_SPOT_DISTANCE:
            value = False
        else:
            base_pair, shot_factor = self._spotting_profile(target)
            moving = (abs(_number(target.get('speed'))) >
                      spotting.MOVING_SPEED_EPSILON)
            if not _detection_upper_bound(
                    distance, view_range, base_pair, moving, shot_factor,
                    fired_recently):
                value = False
            else:
                try:
                    self._probe_totals[0] += 1
                    probe_started = self._probe_started()
                    try:
                        try:
                            visibility = self.visibility_probe(
                                source, target, fired_recently)
                        except TypeError:
                            # Preserve the engine-free two-argument probe contract.
                            visibility = self.visibility_probe(source, target)
                    finally:
                        self._probe_finished(0, probe_started)
                except Exception:
                    visibility = False
                if isinstance(visibility, dict):
                    has_line_of_sight = bool(
                        visibility.get('line_of_sight', False))
                    foliage_bonus = _number(
                        visibility.get('foliage_bonus'), 0.0)
                else:
                    has_line_of_sight = bool(visibility)
                    foliage_bonus = 0.0
                camouflage = spotting.effective_camouflage(
                    base_pair, moving=moving, shot_factor=shot_factor,
                    fired_recently=fired_recently,
                    foliage_bonus=foliage_bonus)
                value = spotting.is_detected(
                    distance, view_range, camouflage, has_line_of_sight)
        self._visibility_cache[key] = (_number(now), value, fire_seq)
        if len(self._visibility_cache) > 1024:
            oldest = sorted(self._visibility_cache.items(),
                            key=lambda item: item[1][0])[:256]
            for old_key, unused_value in oldest:
                self._visibility_cache.pop(old_key, None)
        return value

    @staticmethod
    def _human_planner_id(player_id):
        return HUMAN_TARGET_ID_BASE + int(player_id)

    def _contacts_for(self, source, players, now):
        contacts = []
        lookup = {}
        source_team = int(source.get('team', 0))
        for raw in players or ():
            if (not isinstance(raw, dict) or raw.get('id') is None or
                    not raw.get('alive', True) or
                    int(raw.get('team', 0)) == source_team):
                continue
            target = dict(raw)
            target['kind'] = 'human'
            target['network_id'] = int(raw['id'])
            planner_id = self._human_planner_id(raw['id'])
            target['id'] = planner_id
            target['position'] = _position(raw)
            vehicle_profile = self._player_vehicle_profile(raw)
            target['class_tag'] = vehicle_profile['class_tag']
            target['armor'] = vehicle_profile['armor']
            target['visible'] = self._visible(source, target, now)
            lookup[planner_id] = target
            contacts.append(target)
        for bot_id, raw in self.states.items():
            if (bot_id == source.get('id') or not raw.get('alive', True) or
                    int(raw.get('team', 0)) == source_team):
                continue
            target = dict(raw)
            target['kind'] = 'bot'
            target['network_id'] = int(bot_id)
            target['position'] = _position(raw)
            target['visible'] = self._visible(source, target, now)
            lookup[int(bot_id)] = target
            contacts.append(target)
        return contacts, lookup

    def _index_live_players(self, players):
        """Index current human records once for one rendered update."""
        live_players = {}
        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            live_players[self._human_planner_id(raw['id'])] = raw
        return live_players

    @staticmethod
    def _overlay_target_state(cached, live):
        """Copy one cached contact and overlay one canonical live record."""
        target = dict(cached)
        if live is not None:
            target['position'] = _position(live)
            for name in ('alive', 'health', 'max_health', 'team',
                         'x', 'y', 'z', 'yaw', 'speed'):
                if name in live:
                    target[name] = live[name]
        return target

    def _refresh_target_pose(self, planner_id, cached, live_players):
        """Copy one cached contact and overlay its current canonical pose."""
        if cached.get('kind') == 'human':
            live = live_players.get(planner_id)
        else:
            live = self.states.get(int(cached.get(
                'network_id', planner_id)))
        return self._overlay_target_state(cached, live)

    def _probe_target_pose(self, planner_id, cached, live_players,
                           probe_targets, processed_bot_ids):
        """Share only the canonical fields consumed by static lane probes.

        Spotting visibility belongs to the observer's cached contact and is
        read from that record separately.  Static firing-lane rays consume the
        target identity and current pose. A copied bot has at most two poses
        while sources are processed: before and after its own integration.
        """
        kind = cached.get('kind')
        target_id = int(cached.get('network_id', planner_id))
        phase = bool(kind == 'bot' and target_id in processed_bot_ids)
        key = (kind, target_id, phase)
        target = probe_targets.get(key)
        if target is None:
            target = self._refresh_target_pose(
                planner_id, cached, live_players)
            probe_targets[key] = target
        return target

    def _refresh_target_poses(self, targets, players=None,
                              live_players=None, probe_targets=None,
                              processed_bot_ids=None):
        """Overlay target poses for one source at its copied-motion boundary."""
        if live_players is None:
            live_players = self._index_live_players(players)
        if probe_targets is None:
            probe_targets = {}
        if processed_bot_ids is None:
            processed_bot_ids = set()
        refreshed = {}
        for planner_id, cached in (targets or {}).items():
            refreshed[planner_id] = self._probe_target_pose(
                planner_id, cached, live_players, probe_targets,
                processed_bot_ids)
        return refreshed

    @staticmethod
    def _world_receipt_contains(receipt, position, travel_yaw, speed, dt):
        """Return whether exact typed rays still contain this hull sweep.

        The planning sample has a deliberately short lifetime because slope and
        steering alternatives must be refreshed often.  The typed 3x3 receipt
        owns its own exact origin, yaw and direction, so a later planning sample
        may retain it while the current hull sweep remains a strict subset.
        """
        if not isinstance(receipt, dict):
            return False
        origin = receipt.get('origin')
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            return False
        receipt_yaw = _number(receipt.get('yaw'))
        receipt_sign = int(_number(receipt.get('direction')))
        current_sign = -1 if _number(speed) < 0.0 else 1
        if receipt_sign not in (-1, 1) or receipt_sign != current_sign:
            return False
        rdx = _number(position[0]) - _number(origin[0])
        rdy = abs(_number(position[1]) - _number(origin[1]))
        rdz = _number(position[2]) - _number(origin[2])
        rsine, rcosine = math.sin(receipt_yaw), math.cos(receipt_yaw)
        receipt_forward = rdx * rsine + rdz * rcosine
        receipt_lateral = abs(rdx * rcosine - rdz * rsine)
        receipt_angle = abs(_angle_delta(travel_yaw, receipt_yaw))
        leading = max(0.0, _number(receipt.get('leading')))
        distance = max(0.0, _number(receipt.get('distance')))
        frame_step = max(0.0, min(0.2, _number(dt)))
        current_reach = max(
            0.4, abs(_number(speed)) * frame_step + 0.2)
        return bool(
            receipt_forward >= -0.0001 and
            receipt_forward + leading + current_reach <= distance and
            rdy <= 0.0001 and receipt_lateral <= 0.0001 and
            receipt_angle <= 0.00001)

    @staticmethod
    def _contained_cached_world_receipt(cached, position, travel_yaw,
                                         speed, dt):
        """Return one contained typed receipt independently of plan expiry."""
        if not isinstance(cached, dict):
            return None
        result = cached.get('result')
        if (not isinstance(result, dict) or
                result.get('deferred', False) or
                not BotRuntime._probe_is_clear(result)):
            return None
        receipt = result.get('world_receipt')
        if BotRuntime._world_receipt_contains(
                receipt, position, travel_yaw, speed, dt):
            return receipt
        return None

    @staticmethod
    def _motion_probe_reusable(cached, position, travel_yaw, speed, now,
                               settled=False, dt=None):
        """Prove that a cached hull corridor still contains this motion ray."""
        if not isinstance(cached, dict):
            return False
        if isinstance(cached.get('result'), dict) and cached['result'].get(
                'deferred', False):
            # Exhausting the shared native recast budget proves neither a wall
            # nor a soft path. Retry next frame instead of pinning this Bot's
            # fixed-id cache to a false answer.
            return False
        sample_position = cached.get('position')
        if not isinstance(sample_position, (list, tuple)) or len(sample_position) != 3:
            return False
        sample_yaw = _number(cached.get('yaw'))
        dx = _number(position[0]) - _number(sample_position[0])
        dy = abs(_number(position[1]) - _number(sample_position[1]))
        dz = _number(position[2]) - _number(sample_position[2])
        sine, cosine = math.sin(sample_yaw), math.cos(sample_yaw)
        forward = dx * sine + dz * cosine
        lateral = abs(dx * cosine - dz * sine)
        angle = abs(_angle_delta(travel_yaw, sample_yaw))
        # A fully settled hull cannot enter a new corridor. Preserve its
        # established slope while its pose and heading stay exact; the first
        # movement, turn, collision push or slide restores the normal expiry.
        if settled:
            return bool(
                abs(forward) <= 0.05 and lateral <= 0.05 and dy <= 0.05 and
                angle <= 0.005)
        if now >= cached.get('deadline', 0.0):
            return False
        lookahead = 20.0 if abs(_number(speed)) > 5.0 else 15.0
        heading_drift = lookahead * abs(math.sin(angle))
        reusable = bool(
            -0.1 <= forward <= MOTION_PROBE_FORWARD_BUDGET and
            lateral + heading_drift <= MOTION_PROBE_LATERAL_BUDGET)
        if not reusable:
            return False
        receipt = (cached.get('result') or {}).get('world_receipt')
        if (receipt is not None and
                not BotRuntime._world_receipt_contains(
                    receipt, position, travel_yaw, speed, dt)):
            return False
        return True

    def motion_world_receipt_reusable(self, bot_id, position, travel_yaw,
                                      speed, now, dt):
        """Return whether the current exact hull rays reuse a typed receipt."""
        cached = self._motion_probe_cache.get(int(bot_id))
        if not isinstance(cached, dict):
            return False
        result = cached.get('result')
        if not isinstance(result, dict) or not isinstance(
                result.get('world_receipt'), dict):
            return False
        return self._motion_probe_reusable(
            cached, position, travel_yaw, speed, now, False, dt)

    def _neighbours_for(self, source, supplied, spatial_index=None,
                        traffic_bodies=None):
        if spatial_index is not None and traffic_bodies is not None:
            position = _position(source)
            result = []
            for body_id in tank_collision.nearby_ids(
                    spatial_index, position[0], position[2]):
                if body_id == source.get('id'):
                    continue
                body = traffic_bodies.get(body_id)
                if body is not None:
                    result.append(body)
            return result
        result = list(supplied or ())
        for bot_id, raw in self.states.items():
            if bot_id == source.get('id'):
                continue
            speed = (_number(raw.get('speed'))
                     if raw.get('alive', True) else 0.0)
            result.append({
                'id': bot_id, 'position': _position(raw),
                'team': int(_number(raw.get('team'))),
                'yaw': _number(raw.get('yaw')),
                'velocity': (
                    math.sin(_number(raw.get('yaw'))) * speed,
                    0.0,
                    math.cos(_number(raw.get('yaw'))) * speed),
                'half_length': _number(raw.get('half_length'), 3.5),
                'half_width': _number(raw.get('half_width'), 1.7),
            })
        return result

    def _traffic_snapshot(self, supplied):
        """Build one immutable local-traffic snapshot for this authority tick."""
        bodies = {}
        for raw in supplied or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            body = dict(raw)
            body['position'] = _position(raw)
            bodies[raw['id']] = body
        for bot_id, raw in self.states.items():
            yaw = _number(raw.get('yaw'))
            speed = (_number(raw.get('speed'))
                     if raw.get('alive', True) else 0.0)
            bodies[bot_id] = {
                'id': bot_id, 'position': _position(raw), 'yaw': yaw,
                'team': int(_number(raw.get('team'))),
                'velocity': (math.sin(yaw) * speed, 0.0,
                             math.cos(yaw) * speed),
                'half_length': _number(raw.get('half_length'), 3.5),
                'half_width': _number(raw.get('half_width'), 1.7),
            }
        return bodies, tank_collision.build_spatial_index(bodies)

    def apply_native_pose(self, bot_id, position, yaw, speed):
        """Feed a sampled #1513 WGVehiclePhysics pose back to the AI law."""
        try:
            state = self.states.get(int(bot_id))
            if state is None:
                return False
            if isinstance(position, dict):
                point = _position(position)
            else:
                point = (_number(position[0]), _number(position[1]),
                         _number(position[2]))
            state['x'], state['y'], state['z'] = point
            state['yaw'] = _number(yaw, state.get('yaw', 0.0))
            state['speed'] = _number(speed, state.get('speed', 0.0))
            return True
        except (TypeError, ValueError):
            return False

    def _terrain_support(self, state):
        """Probe centre first, then the 0.8.2 edge fallback when unsupported."""
        position = _position(state)
        yaw = _number(state.get('yaw'))
        half_length = max(1.5, _number(state.get('half_length'), 3.5))
        sine, cosine = math.sin(yaw), math.cos(yaw)
        self._probe_totals[3] += 1
        probe_started = self._probe_started()
        try:
            centre = self._physics_ground_probe(
                position[0], position[2], position[1])
        finally:
            self._probe_finished(3, probe_started)
        if centre is not None:
            centre = float(centre)
            # The vertical law below always selects centre while it exists;
            # front/back could not affect the realised pose on this branch.
            return centre, centre
        highest = None
        for distance in (half_length, -half_length):
            x = position[0] + sine * distance
            z = position[2] + cosine * distance
            self._probe_totals[3] += 1
            probe_started = self._probe_started()
            try:
                value = self._physics_ground_probe(x, z, position[1])
            finally:
                self._probe_finished(3, probe_started)
            if value is None:
                continue
            value = float(value)
            if highest is None or value > highest:
                highest = value
        return highest, centre

    def _log_direction_flip(self, state, path_clear, motion_probe, now):
        """Log rapid drive reversals with the corridor verdict behind them."""
        if not self.debug_logging:
            return False
        direction = int(state.get('movement_dir', 0))
        bot_id = state['id']
        diary = self._flip_diary.get(bot_id)
        if diary is None:
            self._flip_diary[bot_id] = {
                'dir': direction, 'changed': _number(now), 'logged': -10.0}
            return False
        previous = diary['dir']
        if direction == previous:
            return False
        elapsed = _number(now) - diary['changed']
        diary['dir'] = direction
        diary['changed'] = _number(now)
        if (direction == 0 or previous == 0 or elapsed > 2.0 or
                _number(now) - diary['logged'] < 1.0):
            return False
        diary['logged'] = _number(now)
        verdict = 'none'
        if isinstance(motion_probe, dict):
            verdict = 'clear=%s collision=%s deferred=%s' % (
                bool(motion_probe.get('clear')),
                bool(motion_probe.get('collision')),
                bool(motion_probe.get('deferred')))
        print('[BOT FLIP] id=%s reversed %+d->%+d after %.2fs at '
              '(%.1f,%.1f) path_clear=%s probe=%s' % (
                  bot_id, previous, direction, elapsed,
                  _number(state.get('x')), _number(state.get('z')),
                  bool(path_clear), verdict))
        return True

    def set_camera_position(self, position):
        """Publish the viewpoint that drives the presentation detail tiers."""
        if position is None:
            self._camera_position = None
            return False
        self._camera_position = (
            _number(position[0]), _number(position[1]), _number(position[2]))
        return True

    def _integration_step(self, state, now, frame_step):
        """Return this bot's step for this frame, or 0.0 to skip it.

        A near bot integrates every frame.  A mid or far bot banks the frame's
        delta and integrates once its own interval elapses, with the whole
        banked step, so the same distance is covered with fewer, larger steps.
        The phase is spread by bot id so the skipped work never lands on one
        frame.
        """
        bot_id = int(state['id'])
        banked = self._integration_debt.get(bot_id, 0.0) + max(
            0.0, float(frame_step))
        tier = self._detail_tier(state)
        interval = INTEGRATION_INTERVALS[tier]
        if interval <= 0.0:
            self._integration_debt[bot_id] = 0.0
            self._last_step[bot_id] = banked
            return banked
        deadline = self._integration_next.get(bot_id)
        if deadline is None:
            # Spread the first deadline so 29 bots never share a frame.
            deadline = _number(now) + interval * (
                (abs(bot_id) % INTEGRATION_PHASE_BUCKETS) /
                float(INTEGRATION_PHASE_BUCKETS))
            self._integration_next[bot_id] = deadline
        if _number(now) + 1e-9 < deadline:
            self._integration_debt[bot_id] = banked
            return 0.0
        # Never let a long stall replay as one huge step.
        banked = min(banked, 0.2)
        self._integration_debt[bot_id] = 0.0
        self._last_step[bot_id] = banked
        intervals = int(math.floor(
            (_number(now) - deadline) / interval)) + 1
        self._integration_next[bot_id] = deadline + intervals * interval
        return banked

    def _detail_tier(self, state):
        """Return 0 near the camera, 1 at medium range, 2 far away."""
        camera = self._camera_position
        if camera is None:
            return 0
        dx = _number(state.get('x')) - camera[0]
        dz = _number(state.get('z')) - camera[2]
        distance_sq = dx * dx + dz * dz
        if distance_sq <= DETAIL_NEAR_METRES * DETAIL_NEAR_METRES:
            return 0
        if distance_sq <= DETAIL_FAR_METRES * DETAIL_FAR_METRES:
            return 1
        return 2

    def _update_slope_pose(self, state):
        """Refresh the four-point hull pose after this tick's ground settle."""
        if state.get('airborne', False) or not state.get(
                'grounded_once', False):
            return False
        yaw = _number(state.get('yaw'))
        x = _number(state.get('x'))
        z = _number(state.get('z'))
        tier = self._detail_tier(state)
        travel = SLOPE_SAMPLE_METRES[tier]
        turn = SLOPE_SAMPLE_RADIANS[tier]
        marker = state.get('pose_sample')
        if (isinstance(marker, (list, tuple)) and len(marker) == 3 and
                abs(x - _number(marker[0])) < travel and
                abs(z - _number(marker[1])) < travel and
                abs(yaw - _number(marker[2])) < turn):
            return False

        def probe(sample_x, sample_z, hint):
            self._probe_totals[3] += 1
            probe_started = self._probe_started()
            try:
                return self._physics_ground_probe(sample_x, sample_z, hint)
            finally:
                self._probe_finished(3, probe_started)

        pitch, roll = slope_pose(
            probe, (x, _number(state.get('y')), z), yaw,
            _number(state.get('half_length'), 3.5),
            _number(state.get('half_width'), 1.7),
            _number(state.get('pitch')), _number(state.get('roll')))
        state['pitch'] = pitch
        state['roll'] = roll
        state['pose_sample'] = (x, z, yaw)
        return True

    def _invalidate_realised_motion(self, bot_id, attempted_yaw):
        """Forget a command whose committed pose hit a real obstacle."""
        self._decision_cache.pop(bot_id, None)
        self._motion_probe_cache.pop(bot_id, None)
        driver = getattr(self.adapter, 'driver', None)
        remember = getattr(driver, 'remember_failure', None)
        if callable(remember):
            remember(bot_id, attempted_yaw, 5.0)

    def _update_vertical_motion(self, state, step, tick_pose=None,
                                attempted_yaw=None):
        """Run grounded/ballistic phases and reject false raised support."""
        highest, centre = self._terrain_support(state)
        ground = centre if centre is not None else highest
        if ground is not None:
            speed = abs(_number(state.get('speed')))
            snap_gap = max(0.8, min(2.5, speed * step * 2.0 + 0.6))
            max_climb = max(0.6, speed * step * 2.5)
            com_gap = snap_gap if centre is None else state['y'] - centre
            land_y = ground if centre is None else centre
            if not state.get('grounded_once', False):
                state['y'] = land_y
                state['vertical_speed'] = 0.0
                state['airborne'] = False
                state['grounded_once'] = True
            elif tank_collision.support_rise_is_obstacle(
                    state.get('y'), centre, max_climb):
                # The centre ray hit a wagon deck, roof, or large prop only
                # after this tick's horizontal integration put the hull partly
                # inside it. Restore only this tick's pose and let LocalDriver
                # choose its normal reverse/turn recovery on the next update.
                if tick_pose is not None:
                    state['x'], state['y'], state['z'] = tick_pose
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
                state['push_x'] = 0.0
                state['push_z'] = 0.0
                state['vertical_speed'] = 0.0
                state['airborne'] = False
                state.pop('destructible_contact_speed', None)
                self._turn_speeds[state['id']] = 0.0
                self._invalidate_realised_motion(
                    state['id'],
                    (_number(state.get('yaw')) if attempted_yaw is None
                     else attempted_yaw))
                return True
            elif (state['y'] <= ground or
                  (com_gap <= snap_gap and not state.get('airborne', False))):
                if state['y'] < ground:
                    rise = ground - state['y']
                    state['y'] += min(rise, max_climb)
                else:
                    state['y'] += ((ground - state['y']) *
                                   min(1.0, step * 15.0))
                    state['y'] = min(state['y'], ground + 0.12)
                state['vertical_speed'] = 0.0
                state['airborne'] = False
            else:
                if not state.get('airborne', False):
                    pitch = _number(state.get('last_drive_pitch'))
                    state['vertical_speed'] = (
                        _number(state.get('speed')) * math.sin(-pitch)
                        if pitch < 0.0 else 0.0)
                state['airborne'] = True
                substeps = min(8, max(
                    1, int(abs(_number(state.get('vertical_speed')) * step) /
                           0.5) + 1))
                sub_step = step / float(substeps)
                for unused_step in range(substeps):
                    state['vertical_speed'] -= (
                        vehicle_physics.GRAVITY * sub_step)
                    state['y'] += state['vertical_speed'] * sub_step
                    if state['y'] <= land_y:
                        state['y'] = land_y
                        state['vertical_speed'] = 0.0
                        state['airborne'] = False
                        break
        elif state.get('grounded_once', False):
            state['airborne'] = True
            state['vertical_speed'] -= vehicle_physics.GRAVITY * step
            state['y'] += state['vertical_speed'] * step
        else:
            # Terrain streaming owns the first placement. A missing first hit
            # must not turn map loading into a fictitious fall from altitude.
            state['vertical_speed'] = 0.0
            state['airborne'] = False
        return False

    def _guard_realised_pose(self, state, tick_pose, tick_was_safe,
                             attempted_yaw):
        """Cancel only this tick's newly unsafe motion; never rewind history."""
        if (not tick_was_safe or
                prebaked_navigation.pose_is_safe(
                    self.baked_graph, _position(state), shoulder_cells=0)):
            return False
        state['x'], state['y'], state['z'] = tick_pose
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        state['push_x'] = 0.0
        state['push_z'] = 0.0
        state['vertical_speed'] = 0.0
        state['airborne'] = False
        self._invalidate_realised_motion(state['id'], attempted_yaw)
        return True

    def _navigation_target(self, bot_id, position, goal, strategic, state):
        if self.navigator is None or _distance(position, goal) <= 15.0:
            return goal
        mode = strategic.get('combat_mode', 'route')
        route_index = int(_number(strategic.get('route_index'), 0))
        if mode == 'base_defense':
            path_key = (
                'local', int(bot_id), 'base_defense',
                str(strategic.get('defense_base_id') or 'own_base'))
            anchor = None
        elif mode in ('route', 'advance', 'hold'):
            anchor = (strategic.get('route_anchor')
                      if bool(strategic.get('route_join')) else None)
            if anchor is not None:
                # A route's first shared path used to be cached from whichever
                # spawn slot requested it first. Every following tank then
                # converged onto that one hull's egress line. Keep the strategic
                # destination shared, but join it from each real slot through a
                # bot-scoped terrain path. Later route segments remain shared.
                path_key = (
                    'route_join', int(bot_id),
                    int(self.states[bot_id].get('team', 0)),
                    strategic.get('route_id', 'direct'), route_index)
            else:
                path_key = (
                    'route', int(self.states[bot_id].get('team', 0)),
                    strategic.get('route_id', 'direct'), route_index)
        else:
            path_key = ('local', int(bot_id), mode,
                        strategic.get('target_id'))
            anchor = None
        avoid = []
        for neighbour in state.get('neighbours') or ():
            other = (neighbour.get('position') if isinstance(neighbour, dict)
                     else neighbour)
            if other is not None:
                avoid.append(other)
        return self.navigator.next_target(
            bot_id, position, goal, path_key, state.get('now', 0.0),
            anchor, avoid)

    @staticmethod
    def _player_neighbours(players):
        result = []
        for raw in players or ():
            if (not isinstance(raw, dict) or raw.get('id') is None or
                    not raw.get('alive', True)):
                continue
            yaw = _number(raw.get('yaw'))
            speed = _number(raw.get('speed'))
            result.append({
                'id': HUMAN_TARGET_ID_BASE + int(raw['id']),
                'position': _position(raw),
                'team': int(_number(raw.get('team'))), 'yaw': yaw,
                'velocity': (math.sin(yaw) * speed, 0.0,
                             math.cos(yaw) * speed),
            })
        return result

    @staticmethod
    def _traffic_throttle(source, command, neighbours):
        """Return ``(throttle, waiting)`` for nearby friendly traffic.

        Same-lane followers always respect the vehicle ahead. At a crossing or
        merge, the lower bot id has deterministic right of way; every bot yields
        to a human. This breaks the symmetric stop/turn/reverse loop without
        changing route selection or the physical tank-contact response.
        """
        throttle = max(-1.0, min(1.0, _number(command.get('throttle'))))
        if throttle <= 0.01:
            return throttle, False
        position = _position(source)
        source_team = int(_number(source.get('team')))
        if source_team not in (1, 2):
            return throttle, False
        yaw = _number(source.get('yaw'))
        own_speed = abs(_number(source.get('speed')))
        own_length = max(0.5, _number(source.get('half_length'), 3.5))
        own_width = max(0.3, _number(source.get('half_width'), 1.7))
        sine, cosine = math.sin(yaw), math.cos(yaw)
        target_yaw = _number(command.get('target_yaw'), yaw)
        target_sine = math.sin(target_yaw)
        target_cosine = math.cos(target_yaw)
        nearest = None
        for raw in neighbours or ():
            if not isinstance(raw, dict):
                continue
            if int(_number(raw.get('team'))) != source_team:
                continue
            other = raw.get('position') or raw.get('pos')
            if other is None:
                continue
            try:
                dx = float(other[0]) - position[0]
                dz = float(other[2]) - position[2]
                if abs(float(other[1]) - position[1]) > 5.0:
                    continue
            except (TypeError, ValueError, IndexError):
                continue
            forward = dx * sine + dz * cosine
            lateral = abs(dx * cosine - dz * sine)
            if abs(_angle_delta(target_yaw, yaw)) > 0.20:
                target_forward = dx * target_sine + dz * target_cosine
                target_lateral = abs(
                    dx * target_cosine - dz * target_sine)
                if (target_forward > 0.0 and
                        target_lateral < lateral):
                    forward = target_forward
                    lateral = target_lateral
            other_length = max(
                0.5, _number(raw.get('half_length'), 3.5))
            other_width = max(
                0.3, _number(raw.get('half_width'), 1.7))
            corridor_yaw = yaw
            if abs(_angle_delta(target_yaw, yaw)) > 0.20:
                corridor_yaw = target_yaw
            other_yaw = _number(raw.get('yaw'), corridor_yaw)
            same_direction = abs(
                _angle_delta(other_yaw, corridor_yaw)) < 0.65
            if (not same_direction and raw.get('id') is not None and
                    source.get('id') is not None):
                try:
                    other_id = int(raw.get('id'))
                    if (other_id < HUMAN_TARGET_ID_BASE and
                            other_id > int(source.get('id'))):
                        continue
                except (TypeError, ValueError):
                    pass
            clearance = forward - own_length - other_length
            if (forward <= 0.0 or clearance > 9.0 or
                    lateral > own_width + other_width + 0.75):
                continue
            other_velocity = raw.get('velocity') or raw.get('vel')
            try:
                other_vx = float(other_velocity[0])
                other_vz = float(other_velocity[2])
            except (TypeError, ValueError, IndexError):
                other_vx = 0.0
                other_vz = 0.0
            corridor_sine = math.sin(corridor_yaw)
            corridor_cosine = math.cos(corridor_yaw)
            other_forward = max(
                0.0, other_vx * corridor_sine +
                other_vz * corridor_cosine)
            candidate = (clearance, other_forward)
            if nearest is None or candidate[0] < nearest[0]:
                nearest = candidate
        if nearest is None:
            return throttle, False
        clearance, leader_speed = nearest
        safe_clearance = max(1.5, own_speed * 1.0)
        if clearance <= safe_clearance:
            return 0.0, True
        if own_speed > leader_speed + 0.5:
            limited = min(throttle, max(0.0, min(
                1.0, (clearance - safe_clearance) / 4.0)))
            return limited, limited + 1e-9 < throttle
        return throttle, False

    def _player_vehicle_profile(self, raw):
        vehicle_name = raw.get('vehicle')
        cache_key = vehicle_name or ''
        cached = self._player_vehicle_profiles.get(cache_key)
        if cached is not None:
            return cached
        descriptor = {}
        tactical = {}
        try:
            descriptor = self.descriptor_resolver(
                vehicle_name or 'ussr:R11_MS-1')
        except Exception:
            descriptor = {}
        if vehicle_name:
            try:
                tactical = ai_planner.build_vehicle_profile(descriptor)
            except Exception:
                tactical = {}
        cached = {
            'descriptor': descriptor,
            'class_tag': str(tactical.get('class_tag') or 'unknown'),
            'armor': max(0.0, _number(tactical.get('armor'))),
            'spotting': (_base_invisibility(descriptor),
                         _shot_invisibility_factor(descriptor)),
        }
        self._player_vehicle_profiles[cache_key] = cached
        return cached

    def _player_collision_profile(self, raw):
        cache_key = raw.get('vehicle') or ''
        cached = self._player_collision_profiles.get(cache_key)
        if cached is not None:
            return cached
        descriptor = self._player_vehicle_profile(raw)['descriptor']
        params = vehicle_physics.derive_params(descriptor)
        cached = {
            'mass': params.get('mass', 25000.0),
            'shape': _collision_shape(descriptor),
        }
        self._player_collision_profiles[cache_key] = cached
        return cached

    def _resolve_tank_contacts(self, players, now, step):
        """Apply current 0.8.2 chassis OBB response and report rams."""
        if self.native_motion:
            return []
        tanks = []
        for state in self._ordered_states():
            alive = bool(state.get('alive', True))
            yaw = _number(state.get('yaw'))
            speed = _number(state.get('speed')) if alive else 0.0
            tanks.append({
                'id': int(state['id']), 'alive': alive,
                'x': _number(state.get('x')), 'y': _number(state.get('y')),
                'z': _number(state.get('z')), 'yaw': yaw,
                'mass': _number(state.get('mass'), 25000.0),
                'shape': state.get('collision_shape'),
                'vx': (math.sin(yaw) * speed +
                       _number(state.get('push_x'))),
                'vz': (math.cos(yaw) * speed +
                       _number(state.get('push_z'))),
            })
        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                player_id = HUMAN_TARGET_ID_BASE + int(raw['id'])
            except (TypeError, ValueError):
                continue
            profile = self._player_collision_profile(raw)
            alive = bool(raw.get('alive', True))
            yaw = _number(raw.get('yaw'))
            speed = _number(raw.get('speed')) if alive else 0.0
            tanks.append({
                'id': player_id, 'alive': alive,
                # The human client owns its own contact impulse; taking it
                # here too would make the pair shake.
                'impulse': False,
                'x': _number(raw.get('x')), 'y': _number(raw.get('y')),
                'z': _number(raw.get('z')), 'yaw': yaw,
                'mass': profile['mass'], 'shape': profile['shape'],
                'vx': math.sin(yaw) * speed,
                'vz': math.cos(yaw) * speed,
            })

        by_id = dict((tank['id'], tank) for tank in tanks)
        collision_bodies = {}
        maximum_radius = 4.0
        for tank in tanks:
            shape = tank.get('shape') or tank_collision.DEFAULT_SHAPE
            radius = math.sqrt(
                _number(shape[0]) * _number(shape[0]) +
                _number(shape[1]) * _number(shape[1]))
            maximum_radius = max(maximum_radius, radius)
            collision_bodies[tank['id']] = {
                'position': (tank['x'], tank['y'], tank['z'])}
        collision_index = tank_collision.build_spatial_index(
            collision_bodies, maximum_radius * 2.0 + 4.0)
        reports = []
        for state in self._ordered_states():
            if not state.get('alive', True):
                continue
            own = by_id.get(int(state['id']))
            if own is None:
                continue
            candidate_ids = tank_collision.nearby_ids(
                collision_index, own['x'], own['z'])
            result = tank_collision.resolve_tank(
                own, (by_id[tank_id] for tank_id in candidate_ids
                      if tank_id != own['id'] and tank_id in by_id),
                now=now, ram_cooldowns=self._ram_cooldowns)
            self._ram_cooldowns = result['cooldowns']
            delta_x, delta_z = result['delta_velocity']
            yaw = _number(state.get('yaw'))
            speed = _number(state.get('speed'))
            forward_impulse = (delta_x * math.sin(yaw) +
                               delta_z * math.cos(yaw))
            applied_forward = 0.0
            if forward_impulse * speed < 0.0:
                applied_forward = (-speed if
                                   abs(forward_impulse) >= abs(speed)
                                   else forward_impulse)
                state['speed'] = speed + applied_forward
            push_x = (_number(state.get('push_x')) + delta_x -
                      applied_forward * math.sin(yaw))
            push_z = (_number(state.get('push_z')) + delta_z -
                      applied_forward * math.cos(yaw))
            correction_x, correction_z = result['correction']
            move_x = correction_x + push_x * step
            move_z = correction_z + push_z * step
            move_distance = math.sqrt(move_x * move_x + move_z * move_z)
            if move_distance > 0.0001:
                contact_yaw = math.atan2(move_x, move_z)
                contact_speed = move_distance / max(float(step), 1.0 / 120.0)
                if not self._clear(
                        _position(state), contact_yaw, contact_speed, None):
                    # Tank separation is not permission to cross static world
                    # geometry. Let the other hull take its own inverse-mass
                    # share instead of pushing this hull through a wall.
                    move_x = 0.0
                    move_z = 0.0
                    push_x = 0.0
                    push_z = 0.0
            state['x'] += move_x
            state['z'] += move_z
            state['push_x'] = push_x * 0.90
            state['push_z'] = push_z * 0.90
            for event in result['ram_events']:
                other_id = int(event['other_id'])
                target_kind = ('human' if
                               other_id >= HUMAN_TARGET_ID_BASE else 'bot')
                if target_kind == 'human':
                    other_id -= HUMAN_TARGET_ID_BASE
                self._ram_seq += 1
                reports.append({
                    'type': 'bot_ram', 'bot_id': int(state['id']),
                    'target_kind': target_kind, 'target_id': other_id,
                    'ram_seq': self._ram_seq,
                    'damage_to_bot': event['damage_to_self'],
                    'damage_to_target': event['damage_to_other'],
                })
        return reports

    @staticmethod
    def _target_velocity(target):
        if target is None:
            return (0.0, 0.0, 0.0)
        raw = target.get('velocity')
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            return (_number(raw[0]), _number(raw[1]), _number(raw[2]))
        yaw = _number(target.get('yaw'))
        speed = _number(target.get('speed'))
        return (math.sin(yaw) * speed, 0.0, math.cos(yaw) * speed)

    def _local_ballistic_solution(self, state, target, descriptor,
                                  shell_index):
        """Solve the ordinary low arc and moving-target lead without BSP."""
        physical = _shot_ballistics(descriptor, shell_index)
        if target is None or physical is None:
            return None
        speed, gravity, maximum = physical
        start = (_number(state.get('x')), _number(state.get('y')) + 1.5,
                 _number(state.get('z')))
        target_position = _point(
            target.get('position'), _position(target))
        target_position = (
            target_position[0], target_position[1] + 1.0,
            target_position[2])
        solution = ballistics.ballistic_intercept(
            start, target_position, self._target_velocity(target),
            speed, gravity, *_gun_pitch_limits(descriptor))
        if solution is None:
            return None
        aim_position, pitch, flight_time = solution
        if (flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                speed * flight_time > maximum + 1e-6):
            return None
        yaw = math.atan2(
            aim_position[0] - start[0], aim_position[2] - start[2])
        return {
            'aim_position': aim_position, 'yaw': yaw, 'pitch': pitch,
            'flight_time': flight_time, 'arc': 'low',
        }

    @staticmethod
    def _artillery_target_identity(target):
        if not isinstance(target, dict):
            return None
        target_id = target.get('network_id', target.get('id'))
        if target_id is None:
            return None
        try:
            return str(target.get('kind') or ''), int(target_id)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _artillery_source_pose(state):
        return (
            _number(state.get('x')), _number(state.get('y')),
            _number(state.get('z')), _number(state.get('yaw')),
        )

    def _cancel_artillery_intent(self, bot_id, preserve_reproof=False):
        try:
            bot_id = int(bot_id)
        except (TypeError, ValueError, OverflowError):
            return False
        intent = self._artillery_intents.pop(bot_id, None)
        reproof = self._artillery_reproofs.get(bot_id)
        if not preserve_reproof:
            reproof = self._artillery_reproofs.pop(bot_id, reproof)
        cancel = self.artillery_launch_cancel
        if intent is not None and callable(cancel):
            try:
                cancel(dict(intent['source']))
            except Exception:
                # Local intent deletion is the safety boundary. A failed
                # native-queue cleanup may waste bounded work, but must not
                # resurrect or consume the cancelled next-fire sequence.
                pass
        return intent is not None or reproof is not None

    def _clear_artillery_intents(self):
        bot_ids = set(self._artillery_intents)
        bot_ids.update(self._artillery_reproofs)
        for bot_id in list(bot_ids):
            self._cancel_artillery_intent(bot_id)

    def _active_artillery_reproof(
            self, state, target, descriptor, shell_index, now):
        try:
            bot_id = int(state.get('id', 0))
        except (TypeError, ValueError, OverflowError):
            return None
        reproof = self._artillery_reproofs.get(bot_id)
        if reproof is None:
            return None
        physical = _shot_ballistics(descriptor, shell_index)
        target_dead = (
            not isinstance(target, dict) or
            not bool(target.get('alive', True)) or
            ('health' in target and _number(target.get('health')) <= 0.0))
        pose = self._artillery_source_pose(state)
        baseline = reproof['source_pose']
        moved = math.sqrt(sum(
            (pose[index] - baseline[index]) ** 2 for index in range(3)))
        invalid = (
            target_dead or
            self._artillery_target_identity(target) !=
            reproof['target_identity'] or
            int(shell_index) != reproof['shell_index'] or
            int(state.get('fire_seq', 0)) + 1 != reproof['fire_seq'] or
            physical != reproof['physical'] or
            moved > 0.05 or
            abs(_angle_delta(pose[3], baseline[3])) > 0.001)
        if invalid:
            self._cancel_artillery_intent(bot_id)
            return None
        if _number(now) > reproof['deadline'] + 1e-9:
            self._cancel_artillery_intent(bot_id)
            return None
        return reproof

    def _active_artillery_intent(
            self, state, target, descriptor, shell_index, now):
        try:
            bot_id = int(state.get('id', 0))
        except (TypeError, ValueError, OverflowError):
            return None
        intent = self._artillery_intents.get(bot_id)
        if intent is None:
            return None
        reproof = self._active_artillery_reproof(
            state, target, descriptor, shell_index, now)
        if reproof is None:
            return None
        return intent

    def _create_artillery_intent(
            self, state, target, descriptor, shell_index, gun_state,
            ballistic_solution, now):
        physical = _shot_ballistics(descriptor, shell_index)
        target_identity = self._artillery_target_identity(target)
        target_dead = (
            not bool(target.get('alive', True)) or
            ('health' in target and _number(target.get('health')) <= 0.0))
        if (physical is None or target_identity is None or
                target_dead or
                abs(_number(state.get('speed'))) > 0.05 or
                not self._spg_exact_aligned(state, ballistic_solution)):
            return None
        bot_id = int(state['id'])
        fire_seq = int(state.get('fire_seq', 0)) + 1
        reproof = self._active_artillery_reproof(
            state, target, descriptor, shell_index, now)
        if reproof is None:
            reproof = {
                'source': {'id': bot_id},
                'source_pose': self._artillery_source_pose(state),
                'target_identity': target_identity,
                'shell_index': int(shell_index),
                'fire_seq': fire_seq,
                'physical': physical,
                # Reproofs separately predict native-queue target motion and
                # cancel the deterministic next-fire dispersion endpoint.
                'compensation_offset': (0.0, 0.0, 0.0),
                'proof_latency': 0.0,
                'held_receipt': None,
                'attempts': 0,
                'created': _number(now),
                'deadline': _number(now) + ARTILLERY_INTENT_SECONDS,
                'absolute_deadline': (
                    _number(now) + ARTILLERY_TOTAL_PROOF_SECONDS),
            }
            self._artillery_reproofs[bot_id] = reproof
        elif reproof.get('attempts', 0):
            reproof['deadline'] = min(
                _number(reproof.get(
                    'absolute_deadline', reproof['deadline'])),
                _number(now) + ARTILLERY_REPROOF_SECONDS)
        shot_yaw, shot_pitch = _dispersed_barrel_angles(
            state['id'], self.round_id, fire_seq,
            state['aim_yaw'], state['gun_pitch'],
            _effective_shot_dispersion(gun_state, state, descriptor))
        solution = dict(ballistic_solution)
        solution['aim_position'] = _point(solution['aim_position'])
        solution['yaw'] = float(solution['yaw'])
        solution['pitch'] = float(solution['pitch'])
        solution['flight_time'] = float(solution['flight_time'])
        reproof['hold_solution'] = dict(solution)
        intent = {
            'source': {'id': bot_id},
            'source_pose': reproof['source_pose'],
            'target_identity': target_identity,
            'shell_index': int(shell_index),
            'fire_seq': fire_seq,
            'physical': physical,
            'solution': solution,
            'shot_yaw': shot_yaw,
            'shot_pitch': shot_pitch,
            'created': _number(now),
            'deadline': reproof['deadline'],
            'compensation_offset': reproof['compensation_offset'],
        }
        self._artillery_intents[bot_id] = intent
        return intent

    @staticmethod
    def _corrected_artillery_target(target, offset):
        corrected = dict(target)
        position = _point(target.get('position'), _position(target))
        position = tuple(position[index] + offset[index]
                         for index in range(3))
        corrected['position'] = position
        corrected['x'], corrected['y'], corrected['z'] = position
        return corrected

    def _artillery_reproof_solution(
            self, state, target, descriptor, shell_index, reproof):
        """Re-lead a proved SPG arc without repeating strategic world rays.

        The first strategic proof selects a clear low/high family.  A stale
        exact endpoint does not invalidate that family: re-solve it at the
        latest contact plus the observed exact-queue latency, then submit the
        new immutable parabola to the full exact world probe.  Any changed
        target motion still has to pass the final three-dimensional endpoint
        gate, so this prediction can only save redundant strategic work.
        """
        physical = _shot_ballistics(descriptor, shell_index)
        if physical is None or not isinstance(target, dict):
            return None
        speed, gravity, maximum = physical
        start = (
            _number(state.get('x')), _number(state.get('y')) + 1.5,
            _number(state.get('z')))
        target_position = _point(
            target.get('position'), _position(target))
        target_position = (
            target_position[0], target_position[1] + 1.0,
            target_position[2])
        target_velocity = self._target_velocity(target)
        proof_latency = max(0.0, _number(reproof.get('proof_latency')))
        correction = reproof.get('compensation_offset')
        if not isinstance(correction, (list, tuple)) or len(correction) < 3:
            return None
        predicted = tuple(
            target_position[index] +
            target_velocity[index] * proof_latency +
            _number(correction[index])
            for index in range(3))
        arc = str(reproof.get('arc') or '')
        if arc not in ('low', 'high'):
            return None
        minimum_pitch, maximum_pitch = _gun_pitch_limits(descriptor)
        solution = ballistics.ballistic_intercept(
            start, predicted, target_velocity, speed, gravity,
            minimum_pitch, maximum_pitch, arc == 'high')
        if solution is None:
            return None
        aim_position, pitch, flight_time = solution
        if (flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                speed * flight_time > maximum + 1e-6):
            return None
        return {
            'aim_position': aim_position,
            'yaw': math.atan2(
                aim_position[0] - start[0], aim_position[2] - start[2]),
            'pitch': pitch, 'flight_time': flight_time, 'arc': arc,
        }

    def _ballistic_solution(self, state, target, descriptor, shell_index,
                            now):
        profile = state.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        if str(profile.get('class_tag') or '') == 'SPG':
            intent = self._active_artillery_intent(
                state, target, descriptor, shell_index, now)
            if intent is not None:
                return dict(intent['solution'])
            reproof = self._active_artillery_reproof(
                state, target, descriptor, shell_index, now)
            if reproof is not None and reproof.get('attempts', 0):
                return self._artillery_reproof_solution(
                    state, target, descriptor, shell_index, reproof)
            if not callable(self.ballistic_solution_probe):
                return None
            probe_target = target
            if reproof is not None and isinstance(target, dict):
                probe_target = self._corrected_artillery_target(
                    target, reproof['compensation_offset'])
            value = self.ballistic_solution_probe(
                dict(state), (dict(probe_target)
                              if probe_target is not None else None),
                descriptor, int(shell_index), _number(now))
            if not isinstance(value, dict):
                return None
            try:
                aim = _point(value['aim_position'])
                yaw = float(value['yaw'])
                pitch = float(value['pitch'])
                flight_time = float(value['flight_time'])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
            minimum, maximum = _gun_pitch_limits(descriptor)
            if (flight_time <= 0.0 or
                    flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                    pitch < minimum - 0.0001 or
                    pitch > maximum + 0.0001):
                return None
            result = dict(value)
            result.update({
                'aim_position': aim, 'yaw': yaw, 'pitch': pitch,
                'flight_time': flight_time,
            })
            return result
        return self._local_ballistic_solution(
            state, target, descriptor, shell_index)

    def _update_gun_aim(self, state, command, target, step):
        """Slew the rendered turret and barrel through the 0.8.2 limits."""
        descriptor = self._descriptors.get(state['id'], {})
        ballistic_solution = command.get('_ballistic_solution')
        fallback = (target.get('position') if target is not None
                    else _position(state))
        if isinstance(ballistic_solution, dict):
            aim_position = _point(
                ballistic_solution.get('aim_position'), fallback)
        else:
            aim_position = _point(command.get('aim_position'), fallback)
        dx = aim_position[0] - _number(state.get('x'))
        dz = aim_position[2] - _number(state.get('z'))
        horizontal = math.sqrt(dx * dx + dz * dz)
        desired_yaw = (_number(ballistic_solution.get('yaw'))
                       if isinstance(ballistic_solution, dict) else
                       (math.atan2(dx, dz) if horizontal > 0.1
                        else _number(state.get('yaw'))))
        gun_yaw_limits = self._gun_yaw_limits.get(state['id'])
        if gun_yaw_limits is None:
            gun_yaw_limits = ai_driver.gun_yaw_limits(descriptor)
            self._gun_yaw_limits[state['id']] = gun_yaw_limits
        minimum_yaw, maximum_yaw, limited = gun_yaw_limits
        desired_relative = _angle_delta(desired_yaw, state['yaw'])
        if limited:
            desired_relative = max(
                minimum_yaw, min(maximum_yaw, desired_relative))
        turret = _value(descriptor, 'turret', {}) or {}
        turret_step = (_rotation_speed(turret, 0.5) * step *
                       _critical_factor(state, descriptor, 'turret_speed'))
        current_relative = _number(state.get('turret_yaw'))
        turret_difference = _angle_delta(desired_relative, current_relative)
        current_relative = _wrapped(
            current_relative + max(-turret_step,
                                   min(turret_step, turret_difference)))
        if limited:
            current_relative = max(
                minimum_yaw, min(maximum_yaw, current_relative))
        state['turret_yaw'] = current_relative
        state['aim_yaw'] = _wrapped(state['yaw'] + current_relative)

        # BigWorld's rendered gun convention is negative pitch for a raised
        # barrel.  The offsets are the mature implementation's target and
        # muzzle heights, rather than aiming the model roots at each other.
        desired_pitch = (_number(ballistic_solution.get('pitch'))
                         if isinstance(ballistic_solution, dict) else
                         -math.atan2(
                             (aim_position[1] + 1.0) -
                             (_number(state.get('y')) + 1.5),
                             max(0.5, horizontal)))
        minimum_pitch, maximum_pitch = _gun_pitch_limits(descriptor)
        desired_pitch = max(
            minimum_pitch, min(maximum_pitch, desired_pitch))
        gun = _value(descriptor, 'gun', {}) or {}
        state['gun_pitch'] = _slew(
            _number(state.get('gun_pitch')), desired_pitch,
            _rotation_speed(gun, 0.35) * step)
        state['desired_gun_pitch'] = desired_pitch
        state['gun_aligned'] = bool(
            target is not None and ai_driver.gun_aligned(
                desired_yaw, state['yaw'], state['turret_yaw'],
                desired_pitch, state['gun_pitch']))
        return desired_yaw, horizontal

    @staticmethod
    def _shot_los_key(source, target):
        target_id = target.get('network_id', target.get('id', 0))
        return (int(source.get('id', 0)), target.get('kind'), int(target_id))

    @staticmethod
    def _shot_los_phase(key):
        kind_salt = 11 if key[1] == 'human' else 0
        bucket = ((abs(int(key[0])) * 31 + abs(int(key[2])) * 17 +
                   kind_salt) % SHOT_LANE_PHASES)
        return (float(bucket) / SHOT_LANE_PHASES) * \
            SHOT_LANE_REFRESH_SECONDS

    def _shot_clear(self, source, target, now, force=False,
                    probe_budget=None, lane_key=None, distance_cache=None):
        """Probe a current static firing lane independently from team spotting."""
        key = (lane_key if lane_key is not None else
               self._shot_los_key(source, target))
        if distance_cache is not None and distance_cache[0] is not None:
            target_distance = distance_cache[0]
        else:
            target_position = _point(
                target.get('position'), _position(target))
            target_distance = _distance(_position(source), target_position)
            if distance_cache is not None:
                distance_cache[0] = target_distance
        profile = source.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        query_distance = (SPG_SHOT_LANE_QUERY_DISTANCE
                          if str(profile.get('class_tag') or '') == 'SPG'
                          else SHOT_LANE_QUERY_DISTANCE)
        if target_distance > query_distance:
            self._shot_los_cache[key] = (_number(now), False)
            return False
        cached = self._shot_los_cache.get(key)
        if (not force and cached is not None and
                _number(now) - cached[0] <= SHOT_LANE_SECONDS + 1e-9):
            return cached[1]
        if probe_budget is not None:
            if probe_budget[0] <= 0:
                return None
            probe_budget[0] -= 1
        self._probe_totals[1] += 1
        probe_started = self._probe_started()
        try:
            value = bool(self.firing_lane_probe(source, target))
        finally:
            self._probe_finished(1, probe_started)
        self._shot_los_cache[key] = (_number(now), value)
        if len(self._shot_los_cache) > 1024:
            oldest = sorted(self._shot_los_cache.items(),
                            key=lambda item: item[1][0])[:256]
            for old_key, unused_value in oldest:
                self._shot_los_cache.pop(old_key, None)
                self._shot_los_deadlines.pop(old_key, None)
        return value

    def _refresh_shot_clear(self, source, target, now, observation_time,
                            probe_budget=None, lane_key=None,
                            distance_cache=None):
        """Refresh one pair on a stable phase before its observation."""
        key = (lane_key if lane_key is not None else
               self._shot_los_key(source, target))
        now = _number(now)
        observation_time = _number(observation_time)
        if self._shot_los_deadlines.get(key) == observation_time:
            return False
        window_start = observation_time - SHOT_LANE_REFRESH_SECONDS
        deadline = window_start + self._shot_los_phase(key)
        cached = self._shot_los_cache.get(key)
        if cached is not None and cached[0] > window_start + 1e-9:
            self._shot_los_deadlines[key] = observation_time
            return False
        if now + 1e-9 < deadline:
            return False
        value = self._shot_clear(
            source, target, now, force=True, probe_budget=probe_budget,
            lane_key=key, distance_cache=distance_cache)
        if value is None:
            return False
        self._shot_los_deadlines[key] = observation_time
        return True

    @staticmethod
    def _pack_observations(aggregate):
        """Serialise one lightweight record per canonical team target.

        Lane checks remain in the per-bot loop so their cache and native-probe
        side effects keep the same order.  Serialisation happens only after the
        complete lane set is ready; the last target snapshot and visibility OR
        match the previous overwrite-on-each-observer implementation.
        """
        packed = []
        for key in sorted(aggregate):
            target_visible, shootable, observed_target = aggregate[key]
            profile = observed_target.get('profile')
            profile = profile if isinstance(profile, dict) else {}
            packed.append({
                'observing_team': key[0], 'target_kind': key[1],
                'target_id': key[2],
                'target_team': int(observed_target.get('team', 0)),
                'visible': bool(target_visible),
                # Current clients always publish this field. An empty list
                # means team-spotted without a local firing lane; the server
                # rejects omission rather than guessing.
                'shootable_by_bot_ids': sorted(shootable),
                'x': _number(observed_target.get('x')),
                'y': _number(observed_target.get('y')),
                'z': _number(observed_target.get('z')),
                'health': max(0, int(_number(
                    observed_target.get('health'), 1))),
                'max_health': max(1, int(_number(
                    observed_target.get('max_health'), 1))),
                'class_tag': observed_target.get(
                    'class_tag', profile.get('class_tag', 'unknown')),
                'armor': max(0.0, _number(
                    observed_target.get(
                        'armor', profile.get('armor', 0.0)))),
            })
        return packed

    @staticmethod
    def _spg_exact_aligned(state, ballistic_solution):
        if (not state.get('gun_aligned') or
                not isinstance(ballistic_solution, dict)):
            return False
        return (
            abs(_angle_delta(
                _number(ballistic_solution.get('yaw')),
                _number(state.get('aim_yaw')))) <= 1e-7 and
            abs(_number(ballistic_solution.get('pitch')) -
                _number(state.get('gun_pitch'))) <= 1e-7)

    def _validated_artillery_receipt(
            self, value, descriptor, shell_index, fire_seq,
            shot_yaw, shot_pitch, flight_time):
        if not isinstance(value, dict) or 'proof_key' not in value:
            return None
        required = (
            'origin', 'velocity', 'shot_yaw', 'shot_pitch', 'gravity',
            'max_distance', 'max_time_ms', 'fire_seq', 'shell_index',
            'flight_time')
        if any(name not in value for name in required):
            return None
        try:
            origin = tuple(float(component) for component in value['origin'])
            velocity = tuple(
                float(component) for component in value['velocity'])
            receipt_yaw = float(value['shot_yaw'])
            receipt_pitch = float(value['shot_pitch'])
            gravity = float(value['gravity'])
            maximum = float(value['max_distance'])
            max_time_ms = int(value['max_time_ms'])
            receipt_fire_seq = int(value['fire_seq'])
            receipt_shell_index = int(value['shell_index'])
            receipt_flight = float(value['flight_time'])
            if len(origin) != 3 or len(velocity) != 3:
                return None
        except (TypeError, ValueError, OverflowError):
            return None
        values = origin + velocity + (
            receipt_yaw, receipt_pitch, gravity, maximum, receipt_flight)
        physical = _shot_ballistics(descriptor, shell_index)
        if (physical is None or
                any(math.isnan(component) or math.isinf(component)
                    for component in values) or
                receipt_fire_seq != int(fire_seq) or
                receipt_shell_index != int(shell_index) or
                receipt_yaw != float(shot_yaw) or
                receipt_pitch != float(shot_pitch) or
                receipt_flight != float(flight_time) or
                max_time_ms <= 0 or max_time_ms > 20000 or
                gravity != float(physical[1]) or
                maximum != float(physical[2])):
            return None
        horizontal = math.cos(shot_pitch)
        expected_velocity = (
            math.sin(shot_yaw) * horizontal * physical[0],
            math.sin(shot_pitch) * physical[0],
            math.cos(shot_yaw) * horizontal * physical[0],
        )
        if any(abs(velocity[index] - expected_velocity[index]) > 1e-7
               for index in range(3)):
            return None
        result = dict(value)
        result.update({
            'origin': origin, 'velocity': velocity,
            'shot_yaw': receipt_yaw, 'shot_pitch': receipt_pitch,
            'gravity': gravity, 'max_distance': maximum,
            'max_time_ms': max_time_ms, 'flight_time': receipt_flight,
        })
        return result

    @staticmethod
    def _artillery_receipt_terminal(receipt):
        """Return the exact proved parabolic endpoint, or ``None``."""
        if not isinstance(receipt, dict):
            return None
        try:
            origin = receipt['origin']
            velocity = receipt['velocity']
            gravity = float(receipt['gravity'])
            flight_time = float(receipt['flight_time'])
            terminal = (
                origin[0] + velocity[0] * flight_time,
                origin[1] + velocity[1] * flight_time -
                0.5 * gravity * flight_time * flight_time,
                origin[2] + velocity[2] * flight_time,
            )
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            return None
        if any(math.isnan(value) or math.isinf(value) for value in terminal):
            return None
        return terminal

    @staticmethod
    def _artillery_receipt_impact_error(receipt, target):
        """Compare the proved shell endpoint with a moving target at impact."""
        if not isinstance(receipt, dict) or not isinstance(target, dict):
            return None
        terminal = BotRuntime._artillery_receipt_terminal(receipt)
        if terminal is None:
            return None
        try:
            flight_time = float(receipt['flight_time'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        target_velocity = BotRuntime._target_velocity(target)
        target_position = _point(
            target.get('position'), _position(target))
        target_at_impact = (
            target_position[0] + target_velocity[0] * flight_time,
            target_position[1] + 1.0 +
            target_velocity[1] * flight_time,
            target_position[2] + target_velocity[2] * flight_time,
        )
        error = tuple(target_at_impact[index] - terminal[index]
                      for index in range(3))
        values = terminal + target_at_impact + error
        if any(math.isnan(value) or math.isinf(value) for value in values):
            return None
        distance = math.sqrt(sum(value * value for value in error))
        return distance, error

    def _reject_stale_artillery_receipt(
            self, state, target, descriptor, shell_index, intent,
            receipt, now):
        impact_error = self._artillery_receipt_impact_error(receipt, target)
        if impact_error is None:
            self._cancel_artillery_intent(state.get('id'))
            return True
        distance, error = impact_error
        if distance <= ARTILLERY_IMPACT_ERROR_METRES + 1e-9:
            return False
        reproof = self._active_artillery_reproof(
            state, target, descriptor, shell_index, now)
        if reproof is None:
            self._cancel_artillery_intent(state.get('id'))
            return True
        terminal = self._artillery_receipt_terminal(receipt)
        intent_solution = intent.get('solution')
        intent_solution = (intent_solution
                           if isinstance(intent_solution, dict) else {})
        try:
            intended_impact = _point(intent_solution['aim_position'])
        except (KeyError, TypeError, ValueError, OverflowError):
            self._cancel_artillery_intent(state.get('id'))
            return True
        if terminal is None:
            self._cancel_artillery_intent(state.get('id'))
            return True
        # The receipt is the dispersed physical path.  Its displacement from
        # the undispersed strategic aim is deterministic for this reserved
        # fire sequence, so cancel it independently from target motion.
        new_offset = tuple(intended_impact[index] - terminal[index]
                           for index in range(3))
        if any(math.isnan(value) or math.isinf(value)
               for value in new_offset):
            self._cancel_artillery_intent(state.get('id'))
            return True
        reproof['compensation_offset'] = new_offset
        reproof['proof_latency'] = max(
            0.0, _number(now) - _number(intent.get('created')))
        reproof['attempts'] = int(reproof.get('attempts', 0)) + 1
        reproof['deadline'] = min(
            _number(reproof.get('absolute_deadline', reproof['deadline'])),
            _number(now) + ARTILLERY_REPROOF_SECONDS)
        reproof['last_proof_latency'] = max(
            0.0, _number(now) - _number(intent.get('created')))
        reproof['last_impact_error'] = distance
        reproof['arc'] = str(intent_solution.get('arc') or '')
        self._cancel_artillery_intent(
            state.get('id'), preserve_reproof=True)
        return True

    def _held_artillery_receipt(self, reproof, target, now):
        """Return a pinned exact receipt when the target reaches its endpoint.

        A completed native proof remains valid for its immutable parabola.  If
        a constant-velocity contact will cross that endpoint shortly, retain
        the receipt and wait without spending more rays.  Every frame uses the
        current target pose and velocity; stopping, reversing or changing
        height invalidates the hold and falls back to a fresh exact proof.
        """
        held = reproof.get('held_receipt')
        if not isinstance(held, dict) or not isinstance(target, dict):
            return None, False
        if (self._artillery_target_identity(target) !=
                held.get('target_identity')):
            reproof['held_receipt'] = None
            return None, False
        if _number(now) > _number(held.get('deadline')) + 1e-9:
            reproof['held_receipt'] = None
            return None, False
        terminal = self._artillery_receipt_terminal(held.get('receipt'))
        if terminal is None:
            reproof['held_receipt'] = None
            return None, False
        position = _point(target.get('position'), _position(target))
        position = (position[0], position[1] + 1.0, position[2])
        velocity = self._target_velocity(target)
        baseline = held.get('velocity')
        if (not isinstance(baseline, (list, tuple)) or len(baseline) < 3 or
                any(abs(velocity[index] - _number(baseline[index])) > 1e-6
                    for index in range(3))):
            reproof['held_receipt'] = None
            return None, False
        speed_sq = sum(value * value for value in velocity)
        if speed_sq <= 1e-9:
            reproof['held_receipt'] = None
            return None, False
        delta = tuple(terminal[index] - position[index] for index in range(3))
        time_to_closest = sum(
            delta[index] * velocity[index] for index in range(3)) / speed_sq
        if time_to_closest < -1e-9:
            reproof['held_receipt'] = None
            return None, False
        closest = tuple(
            position[index] + velocity[index] * max(0.0, time_to_closest)
            for index in range(3))
        miss = math.sqrt(sum(
            (closest[index] - terminal[index]) ** 2 for index in range(3)))
        if miss > ARTILLERY_IMPACT_ERROR_METRES + 1e-9:
            reproof['held_receipt'] = None
            return None, False
        flight_time = _number(held['receipt'].get('flight_time'))
        impact_position = tuple(
            position[index] + velocity[index] * flight_time
            for index in range(3))
        impact_miss = math.sqrt(sum(
            (impact_position[index] - terminal[index]) ** 2
            for index in range(3)))
        if impact_miss <= ARTILLERY_IMPACT_ERROR_METRES + 1e-9:
            return held['receipt'], True
        return None, True

    def _artillery_launch_receipt(
            self, state, target, descriptor, shell_index, gun_state,
            ballistic_solution, now):
        if not callable(self.artillery_launch_probe):
            return None
        reproof = self._active_artillery_reproof(
            state, target, descriptor, shell_index, now)
        if reproof is not None:
            held, waiting = self._held_artillery_receipt(
                reproof, target, now)
            if waiting:
                return held
        intent = self._active_artillery_intent(
            state, target, descriptor, shell_index, now)
        if intent is None:
            intent = self._create_artillery_intent(
                state, target, descriptor, shell_index, gun_state,
                ballistic_solution, now)
        if (intent is None or
                not self._spg_exact_aligned(state, intent['solution'])):
            return None
        fire_seq = intent['fire_seq']
        shot_yaw = intent['shot_yaw']
        shot_pitch = intent['shot_pitch']
        flight_time = intent['solution']['flight_time']
        value = self.artillery_launch_probe(
            dict(state), dict(target), descriptor, int(shell_index),
            fire_seq, shot_yaw, shot_pitch, flight_time, _number(now))
        receipt = self._validated_artillery_receipt(
            value, descriptor, shell_index, fire_seq,
            shot_yaw, shot_pitch, flight_time)
        if receipt is None:
            return None
        impact_error = self._artillery_receipt_impact_error(receipt, target)
        if (impact_error is not None and
                impact_error[0] > ARTILLERY_IMPACT_ERROR_METRES + 1e-9):
            reproof = self._active_artillery_reproof(
                state, target, descriptor, shell_index, now)
            velocity = self._target_velocity(target)
            terminal = self._artillery_receipt_terminal(receipt)
            position = _point(target.get('position'), _position(target))
            position = (position[0], position[1] + 1.0, position[2])
            if reproof is not None and terminal is not None:
                speed_sq = sum(value * value for value in velocity)
                delta = tuple(terminal[index] - position[index]
                              for index in range(3))
                closest_time = (sum(
                    delta[index] * velocity[index] for index in range(3)) /
                    speed_sq if speed_sq > 1e-9 else -1.0)
                closest = tuple(
                    position[index] + velocity[index] * max(0.0, closest_time)
                    for index in range(3))
                closest_miss = math.sqrt(sum(
                    (closest[index] - terminal[index]) ** 2
                    for index in range(3)))
                if (closest_time >= 0.0 and
                        closest_time <= ARTILLERY_RECEIPT_HOLD_SECONDS and
                        closest_miss <= ARTILLERY_IMPACT_ERROR_METRES + 1e-9):
                    reproof['held_receipt'] = {
                        'receipt': receipt, 'velocity': velocity,
                        'target_identity': self._artillery_target_identity(
                            target),
                        'deadline': (_number(now) +
                                     ARTILLERY_RECEIPT_HOLD_SECONDS),
                    }
                    return None
        if self._reject_stale_artillery_receipt(
                state, target, descriptor, shell_index, intent, receipt, now):
            return None
        return receipt

    def _fire(self, state, gun_state, reload_factor, descriptor,
              launch_receipt=None, ammo_state=None):
        next_fire_seq = int(state.get('fire_seq', 0)) + 1
        if launch_receipt is not None:
            if int(launch_receipt.get('fire_seq', -1)) != next_fire_seq:
                return False
        if ammo_state is None:
            ammo_state = self._ammo_states.get(int(state.get('id', 0)))
        if ammo_state is None:
            ammo_state = _BotAmmoState(
                descriptor, state.get('profile') or {}, state)
            self._ammo_states[int(state.get('id', 0))] = ammo_state
            ammo_state.stage(state.get('shell_index', 0), True)
        if not ammo_state.can_fire():
            return False
        if not gun_state.fire(reload_factor):
            return False
        if not ammo_state.consume_loaded():
            raise RuntimeError('bot ammunition changed during atomic fire')
        state['fire_seq'] = next_fire_seq
        for name in (
                'shot_origin', 'shot_velocity', 'shot_gravity',
                'shot_max_distance', 'shot_max_time_ms', 'shot_proof_key'):
            state.pop(name, None)
        if launch_receipt is None:
            state['shot_yaw'], state['shot_pitch'] = \
                _dispersed_barrel_angles(
                    state['id'], self.round_id, state['fire_seq'],
                    state['aim_yaw'], state['gun_pitch'],
                    _effective_shot_dispersion(
                        gun_state, state, descriptor))
        else:
            state['shot_yaw'] = launch_receipt['shot_yaw']
            state['shot_pitch'] = launch_receipt['shot_pitch']
            state['shot_origin'] = tuple(launch_receipt['origin'])
            state['shot_velocity'] = tuple(launch_receipt['velocity'])
            state['shot_gravity'] = launch_receipt['gravity']
            state['shot_max_distance'] = launch_receipt['max_distance']
            state['shot_max_time_ms'] = launch_receipt['max_time_ms']
            state['shot_proof_key'] = launch_receipt['proof_key']
        state['clip'] = gun_state.clip
        state['reload_time'] = gun_state.remaining(reload_factor)
        state['reload_duration'] = (
            gun_state.reload_duration * reload_factor)
        ammo_state.publish(state)
        return True

    def update(self, dt, now, players=None, neighbours=None):
        """Advance bots locally and publish state plus periodic observations."""
        if (not self.is_authority() or self.adapter is None or
                self.finished):
            return []
        # Match the mature 0.8.2 split: copied bot physics and presentation
        # advance once per rendered frame, while one canonical combat/pose
        # publication is formed at no more than 30 Hz.  Accumulating render
        # deltas until 1/30 s made the authority client's visible bots move in
        # discrete steps; dropping already-formed messages in LANClient would
        # instead create gaps in the strict combat proposal sequence.
        self._accumulator += max(0.0, _number(dt))
        if self._accumulator <= 0.0:
            return []
        frame_step = min(self._accumulator, 0.2)
        self._accumulator = 0.0
        step = frame_step
        now = _number(now)
        publish = now >= self._next_publication
        if publish:
            if self._next_publication <= 0.0:
                self._next_publication = now
            # Advance the nominal clock rather than restarting it from a late
            # rendered frame.  At 40 FPS a restart would quantise 30 Hz down
            # to 20 Hz (one publication every other frame).  Carrying the
            # deadline preserves the requested average cadence while still
            # forming at most one proposal in any render callback.
            while self._next_publication <= now:
                self._next_publication += PUBLICATION_SECONDS
        players = list(players or [])
        live_players = None
        live_probe_targets = {}
        processed_bot_ids = set()
        # Cover geometry is sampled on the render thread.  A batch selected by
        # one observation is phased through the following observation window,
        # so a later observation is not formed until all of its cover results
        # are ready.  Supported render rates have ample room for three jobs;
        # unusually low rates delay the observation instead of publishing a
        # partial batch or running several native probes on one frame.
        observation_due = now >= self._next_observation
        collect_observation = (
            publish and observation_due and not self._cover_queue)
        refresh_shot_lanes = (
            not self._cover_queue and
            (observation_due or
             (self._next_observation > 0.0 and
              now + SHOT_LANE_REFRESH_SECONDS + 1e-9 >=
              self._next_observation)))
        shot_lane_budget = [MAX_SHOT_LANE_PAIRS_PER_FRAME]
        shot_lanes_ready = True
        self._begin_world_receipt_frame()
        neighbours = list(neighbours or []) + self._player_neighbours(players)
        # Native terrain and visibility probes run on BigWorld's render thread.
        # Build the traffic view lazily, only when a staggered decision is due;
        # render-only frames integrate the last accepted command and pose.
        traffic_bodies = None
        traffic_index = None
        observation_entries = {}
        cover_jobs = []
        tick_poses = {}
        tick_safe = {}
        attempted_yaws = {}
        integrated = set()
        for state in self.states.values():
            if not state['alive']:
                continue
            # Distance-tiered INTEGRATION, not just probe throttling: a far
            # bot advances at a lower rate with the whole accumulated step, so
            # the per-frame Python cost scales with nearby bots rather than
            # with the roster size.  The pose it publishes is unchanged
            # between its own steps, which is what the engine interpolates.
            step = self._integration_step(state, now, frame_step)
            if step <= 0.0:
                continue
            integrated.add(state['id'])
            self._advance_bot_critical(state, step, now)
            if not state['alive']:
                continue
            position = _position(state)
            tick_poses[state['id']] = position
            tick_safe[state['id']] = prebaked_navigation.pose_is_safe(
                self.baked_graph, position, shoulder_cells=0)
            server_order = self._server_orders.get(state['id'])
            decide_with_order = getattr(self.adapter, 'decide_with_order', None)
            cache_key = (('server', self._server_order_tokens.get(
                              state['id'], 0))
                         if server_order is not None else ('local',))
            decision_cache = self._decision_cache.get(state['id'])
            decision_due = not (
                decision_cache is not None and
                decision_cache[0] == cache_key and
                _number(now) < decision_cache[1])
            probe_samples = {}

            def sample_direction(sample_yaw):
                # A planner can ask about the same heading that physics consumes
                # later in this tick.  One raw probe owns both answers.
                normalised = ((float(sample_yaw) + math.pi) %
                              (2.0 * math.pi) - math.pi)
                key = round(normalised, 4)
                if key not in probe_samples:
                    probe_samples[key] = self._probe_direction(
                        position, sample_yaw, state.get('speed', 0.0),
                        self._descriptors.get(state['id']))
                return probe_samples[key]

            def sample_clear(sample_yaw):
                return self._probe_is_clear(sample_direction(sample_yaw))

            if not decision_due:
                if len(decision_cache) < 6:
                    raise RuntimeError(
                        'cached bot perception is unavailable')
                command = dict(decision_cache[3])
                contacts = decision_cache[4]
                targets = decision_cache[5]
            else:
                contacts, targets = self._contacts_for(state, players, now)
                if traffic_bodies is None:
                    traffic_bodies, traffic_index = self._traffic_snapshot(
                        neighbours)
                decision_step = step
                if decision_cache is not None:
                    decision_step = max(
                        step, min(0.35, _number(now) - decision_cache[2]))
                decision_state = {
                    'id': state['id'], 'position': position,
                    'yaw': state['yaw'],
                    'speed': abs(_number(state.get('speed'))),
                    'dt': decision_step, 'now': now,
                    'health': state['health'],
                    'max_health': state['max_health'],
                    'contacts': contacts,
                    'neighbours': self._neighbours_for(
                        state, neighbours, traffic_index, traffic_bodies),
                    'velocity': (
                        math.sin(_number(state.get('yaw'))) *
                        _number(state.get('speed')), 0.0,
                        math.cos(_number(state.get('yaw'))) *
                        _number(state.get('speed'))),
                    'half_length': _number(state.get('half_length'), 3.5),
                    'half_width': _number(state.get('half_width'), 1.7),
                }
                traffic_neighbours = decision_state['neighbours']
                if server_order is not None and callable(decide_with_order):
                    server_order = dict(server_order)
                    if (server_order.get('target_kind') == 'human' and
                            server_order.get('target_id') is not None):
                        server_order['target_id'] = self._human_planner_id(
                            server_order.get('target_id'))
                    server_order = _overlay_live_target_pose(
                        server_order,
                        targets.get(server_order.get('target_id')))
                    command = decide_with_order(
                        decision_state, server_order,
                        sample_clear)
                else:
                    command = self.adapter.decide(
                        decision_state, sample_clear)
                command['throttle'], waiting_for_traffic = \
                    self._traffic_throttle(
                        state, command, traffic_neighbours)
                if waiting_for_traffic:
                    driver = getattr(self.adapter, 'driver', None)
                    wait = getattr(driver, 'wait_for_traffic', None)
                    if callable(wait):
                        wait(state['id'])
                self._decision_cache[state['id']] = (
                    cache_key,
                    _cache_deadline(
                        now, state['id'],
                        DECISION_SECONDS *
                        DECISION_TIER_FACTOR[self._detail_tier(state)],
                        3, decision_cache is None),
                    _number(now), dict(command), contacts, targets)
            # Preserve the old refresh point: in copied-physics mode, a later
            # bot observes poses integrated by earlier bots in this same tick.
            # Human records do not change inside update, so index them once at
            # the first live bot instead of rebuilding the map for every bot.
            if live_players is None:
                live_players = self._index_live_players(players)
            refresh_all_targets = bool(
                collect_observation or refresh_shot_lanes)
            active_contacts = contacts if refresh_all_targets else ()
            live_targets = None
            if refresh_all_targets:
                live_targets = self._refresh_target_poses(
                    targets, live_players=live_players,
                    probe_targets=live_probe_targets,
                    processed_bot_ids=processed_bot_ids)
            for cached_target in active_contacts:
                observed_target = live_targets.get(
                    cached_target.get('id'), cached_target)
                lane_key = self._shot_los_key(state, observed_target)
                lane_distance = ([None] if
                                 collect_observation and refresh_shot_lanes
                                 else None)
                if refresh_shot_lanes:
                    if (self._shot_los_deadlines.get(lane_key) !=
                            self._next_observation):
                        self._refresh_shot_clear(
                            state, observed_target, now,
                            self._next_observation, shot_lane_budget,
                            lane_key=lane_key,
                            distance_cache=lane_distance)
                    if (self._shot_los_deadlines.get(lane_key) !=
                            self._next_observation):
                        shot_lanes_ready = False
                if not collect_observation:
                    continue
                key = (int(state.get('team', 0)),
                       observed_target.get('kind'),
                       int(observed_target.get('network_id', 0)))
                if ('visible' not in observed_target or
                        not isinstance(observed_target['visible'], bool)):
                    raise ValueError(
                        'canonical contact visible flag is invalid')
                target_visible = cached_target['visible']
                shooter_id = None
                if (self._shot_los_deadlines.get(lane_key) ==
                        self._next_observation and
                        self._shot_clear(
                            state, observed_target, now,
                            probe_budget=shot_lane_budget,
                            lane_key=lane_key,
                            distance_cache=lane_distance)):
                    shooter_id = int(state['id'])
                # Once one pair misses the fixed deadline, this observation
                # cannot become complete again during the same ``now``. Keep
                # all lane-cache calls above, but avoid constructing payload
                # intermediates that the end-of-frame completeness gate will
                # discard.
                if shot_lanes_ready:
                    entry = observation_entries.get(key)
                    if entry is None:
                        entry = [False, set(), observed_target]
                        observation_entries[key] = entry
                    entry[0] = bool(target_visible or entry[0])
                    if shooter_id is not None:
                        entry[1].add(shooter_id)
                    entry[2] = observed_target
            target_id = command.get('target_id')
            if target_id in (targets or {}):
                # Aim/fire gating retains the observer-specific spotting flag.
                target = self._refresh_target_pose(
                    target_id, targets[target_id], live_players)
            else:
                target = None
            command = _overlay_live_target_pose(command, target)
            state['target_kind'] = (
                target.get('kind') if target is not None else None)
            state['target_id'] = (
                target.get('network_id') if target is not None else None)
            descriptor = self._descriptors.get(state['id'], {})
            profile = state.get('profile')
            profile = profile if isinstance(profile, dict) else {}
            gun_state = self._gun_states.get(state['id'])
            if gun_state is None:
                gun_state = _BotGunState(
                    descriptor, state.get('fire_seq', 0))
                self._gun_states[state['id']] = gun_state
            ammo_state = self._ammo_states.get(state['id'])
            if ammo_state is None:
                ammo_state = _BotAmmoState(descriptor, profile, state)
                self._ammo_states[state['id']] = ammo_state
            gun_state.tick(step)
            reload_factor = _critical_factor(
                state, descriptor, 'reload')
            ammo_state.stage(
                gun_state.shell_index(command.get('shell_index', 0)),
                gun_state.ready(reload_factor))
            ammo_state.publish(state)
            is_spg = str(profile.get('class_tag') or '') == 'SPG'
            pending_intent = None
            pending_reproof = None
            if is_spg:
                # Artillery proofs are keyed to the physically loaded round,
                # never the server's desired future selection.
                intent_shell = int(state['shell_index'])
                if not command.get('fire_allowed'):
                    self._cancel_artillery_intent(state['id'])
                else:
                    pending_intent = self._active_artillery_intent(
                        state, target, descriptor, intent_shell, now)
                    pending_reproof = self._active_artillery_reproof(
                        state, target, descriptor, intent_shell, now)
                if pending_intent is not None:
                    frozen = pending_intent['solution']
                    command['aim_position'] = frozen['aim_position']
                    command['face_position'] = frozen['aim_position']
                elif (pending_reproof is not None and
                      isinstance(pending_reproof.get('hold_solution'), dict)):
                    frozen = pending_reproof['hold_solution']
                    command['aim_position'] = frozen['aim_position']
                    command['face_position'] = frozen['aim_position']
                if pending_reproof is not None:
                    command['throttle'] = 0.0
                    command['turn'] = 0.0
                    command['movement_intent'] = False
            throttle = max(-1.0, min(1.0, _number(command['throttle'])))
            turn = max(-1.0, min(1.0, _number(command.get('turn'))))
            aim_fallback = (target.get('position') if target is not None
                            else _position(state))
            aim_position = _point(command.get('aim_position'), aim_fallback)
            aim_dx = aim_position[0] - _number(state.get('x'))
            aim_dz = aim_position[2] - _number(state.get('z'))
            aim_distance = math.sqrt(aim_dx * aim_dx + aim_dz * aim_dz)
            desired_aim_yaw = (
                math.atan2(aim_dx, aim_dz) if aim_distance > 0.1
                else _number(state.get('yaw')))
            gun_yaw_limits = self._gun_yaw_limits.get(state['id'])
            if gun_yaw_limits is None:
                gun_yaw_limits = ai_driver.gun_yaw_limits(descriptor)
                self._gun_yaw_limits[state['id']] = gun_yaw_limits
            minimum_yaw, maximum_yaw, unused_limited = gun_yaw_limits
            turn, throttle, hull_aiming = ai_driver.combat_hull_aim(
                state['yaw'], desired_aim_yaw, minimum_yaw, maximum_yaw,
                turn, throttle, command.get('recovery_mode', 'drive'),
                target is not None and
                command.get('combat_mode') != 'base_defense')
            state['hull_aiming'] = bool(hull_aiming)
            unused_devices, destroyed_devices, unused_crew = (
                _critical_parts(state))
            if destroyed_devices.intersection((
                    'engineHealth', 'leftTrackHealth',
                    'rightTrackHealth')):
                throttle = 0.0
                turn = 0.0
            elif abs(throttle) > 0.01:
                throttle *= _critical_factor(
                    state, descriptor, 'mobility')
            travel_yaw = (state['yaw'] if throttle >= 0.0
                          else state['yaw'] + math.pi)
            attempted_yaws[state['id']] = travel_yaw
            cached_motion_probe = self._motion_probe_cache.get(state['id'])
            settled_motion = bool(
                abs(throttle) <= 0.01 and abs(turn) <= 0.01 and
                abs(_number(state.get('speed'))) <= 0.02 and
                state.get('grounded_once', False) and
                not state.get('airborne', False))
            if not self._motion_probe_reusable(
                    cached_motion_probe, position, travel_yaw,
                    state.get('speed', 0.0), now, settled_motion, step):
                motion_probe = sample_direction(travel_yaw)
                # Planner alternatives keep the mature six horizontal rays.
                # Only the finally selected, powered, non-turning travel sample
                # pays for the exact 3x3 receipt used by commit-side motion.
                if (isinstance(motion_probe, dict) and
                        self._probe_is_clear(motion_probe) and
                        not motion_probe.get('deferred', False) and
                        abs(_number(motion_probe.get('slope'))) <= 0.01 and
                        abs(throttle) > 0.01 and abs(turn) <= 0.01 and
                        abs(_number(self._turn_speeds.get(
                            state['id'], 0.0))) <= 0.01 and
                        not state.get('airborne', False)):
                    motion_probe = dict(motion_probe)
                    receipt_speed = abs(_number(state.get('speed')))
                    if throttle < 0.0:
                        # Preserve reverse intent even when the copied hull is
                        # starting from exactly zero. ``-0.0 < 0`` is false
                        # and would select the forward hull extent.
                        receipt_speed = -max(receipt_speed, 0.000001)
                    receipt = self._contained_cached_world_receipt(
                        cached_motion_probe, position, travel_yaw,
                        receipt_speed, step)
                    if receipt is not None:
                        # Refresh the generic slope/steering sample without
                        # paying another nine exact rays for the same contained
                        # world corridor.
                        motion_probe['world_receipt'] = receipt
                    else:
                        receipt = self._probe_world_receipt(
                            state['id'], position, travel_yaw, receipt_speed,
                            descriptor, not isinstance(
                                ((cached_motion_probe or {}).get('result') or
                                 {}).get('world_receipt'), dict))
                        if receipt == 'deferred':
                            motion_probe['deferred'] = True
                            motion_probe['clear'] = False
                            motion_probe['collision'] = False
                        elif receipt is False:
                            motion_probe.update({
                                'clear': False,
                                'collision': True,
                            })
                        elif isinstance(receipt, dict):
                            motion_probe['world_receipt'] = receipt
                if not (isinstance(motion_probe, dict) and
                        motion_probe.get('deferred', False)):
                    self._motion_probe_cache[state['id']] = {
                        'result': motion_probe,
                        'position': position,
                        'yaw': travel_yaw,
                        'deadline': _motion_probe_deadline(
                            now, state['id'], cached_motion_probe is None),
                    }
                else:
                    old_result = ((cached_motion_probe or {}).get(
                        'result') or {})
                    if not isinstance(old_result.get(
                            'world_receipt'), dict):
                        self._motion_probe_cache.pop(state['id'], None)
            else:
                motion_probe = cached_motion_probe['result']
            probe_deferred = bool(
                isinstance(motion_probe, dict) and
                motion_probe.get('deferred', False))
            path_clear = (True if (abs(throttle) <= 0.01 or
                                   state.get('airborne', False)) else
                          self._probe_is_clear(motion_probe))
            if not path_clear:
                throttle = 0.0
                driver = getattr(self.adapter, 'driver', None)
                remember = getattr(driver, 'remember_failure', None)
                if callable(remember) and not probe_deferred:
                    remember(state['id'], travel_yaw)
            steer_dir = 0
            if abs(turn) > 0.01:
                # LocalDriver already inverts reverse recovery steering for the
                # copied traverse law.  Re-deriving this sign from target_yaw
                # discards that command and recreates the stationary spin.
                steer_dir = 1 if turn > 0.0 else -1
            state['movement_dir'] = (
                1 if throttle > 0.01 else (-1 if throttle < -0.01 else 0))
            state['rotation_dir'] = steer_dir
            self._log_direction_flip(state, path_clear, motion_probe, now)
            if not self.native_motion:
                params = self._physics_params.get(state['id'])
                if params is None:
                    params = vehicle_physics.derive_params({})
                    self._physics_params[state['id']] = params
                # The selected corridor's ground sample is also the copied
                # physics slope.  A second native probe here used to double the
                # render-thread work for every moving bot.
                slope = (_number(motion_probe.get('slope'))
                         if isinstance(motion_probe, dict) else 0.0)
                slope_pitch = -math.atan(slope)
                turn_speed = vehicle_physics.traverse_step(
                    params, self._turn_speeds.get(state['id'], 0.0),
                    turn, _number(state.get('speed')), step,
                    drive_intent=throttle)
                self._turn_speeds[state['id']] = turn_speed
                state['yaw'] += turn_speed * step
                while state['yaw'] > math.pi:
                    state['yaw'] -= math.pi * 2.0
                while state['yaw'] < -math.pi:
                    state['yaw'] += math.pi * 2.0
                previous_speed = _number(state.get('speed'))
                speed = vehicle_physics.longitudinal_step(
                    params, previous_speed, throttle,
                    steer_dir != 0, slope_pitch, step,
                    bool(state.get('airborne', False)), 0, False)
                state['last_drive_pitch'] = slope_pitch
                if not path_clear:
                    if probe_deferred:
                        # Native-query scheduling is not a collision.  Pause
                        # this copied pose for one render frame while retaining
                        # its real pre-step momentum; never feed the scheduler
                        # into route failure recovery or hard-wall damping.
                        speed = previous_speed
                    else:
                        speed *= 0.2
                    state.pop('destructible_contact_speed', None)
                motion_status = 'clear'
                contact_speed = _number(state.get(
                    'destructible_contact_speed'), speed)
                if (path_clear and abs(speed) > 0.0001 and
                        callable(self.motion_resolver)):
                    motion_status = self.motion_resolver(
                        state['id'], position, state['yaw'], speed,
                        descriptor, step, now)
                    if motion_status not in (
                            'clear', 'crushed', 'soft', 'cap_crushed',
                            'hard'):
                        raise RuntimeError(
                            'bot motion resolver returned an invalid status')
                    if motion_status not in ('clear', 'crushed'):
                        path_clear = False
                        if motion_status == 'soft':
                            # Native contact has not cleared yet. Freeze the
                            # copied wheel speed at the real impact value; do
                            # not accelerate against a pose that did not move.
                            contact_speed = min(abs(contact_speed), abs(speed))
                            speed = (-contact_speed if speed < 0.0 else
                                     contact_speed)
                            state['destructible_contact_speed'] = speed
                        elif motion_status == 'cap_crushed':
                            # The directional cap is a stock-gate proof, not
                            # physical momentum.  The accepted item blocks this
                            # tick while the bot keeps its pre-step real speed.
                            speed = previous_speed
                            state.pop('destructible_contact_speed', None)
                        elif motion_status == 'hard':
                            self._invalidate_realised_motion(
                                state['id'], travel_yaw)
                            speed *= 0.2
                            state.pop('destructible_contact_speed', None)
                    else:
                        state.pop('destructible_contact_speed', None)
                state['speed'] = speed
                if path_clear:
                    state['x'] += math.sin(state['yaw']) * speed * step
                    state['z'] += math.cos(state['yaw']) * speed * step
            ammo_state.publish(state)
            ballistic_solution = self._ballistic_solution(
                state, target, descriptor, state['shell_index'], now)
            command['_ballistic_solution'] = ballistic_solution
            unused_desired_yaw, unused_horizontal = self._update_gun_aim(
                state, command, target, step)
            state['clip_size'] = gun_state.clip_size
            state['clip'] = gun_state.clip
            state['reload_time'] = gun_state.remaining(reload_factor)
            state['reload_duration'] = (
                gun_state.reload_duration * reload_factor)
            fire_range = max(0.0, _number(command.get('fire_range'), 0.0))
            target_distance = (_distance(_position(state), target['position'])
                               if target is not None else 0.0)
            in_range = (target is not None and target_distance > 1.0 and
                        ballistic_solution is not None and
                        (fire_range <= 0.0 or target_distance < fire_range))
            if (publish and command['fire_allowed'] and target is not None and
                    in_range and
                    'gunHealth' not in destroyed_devices and
                    state.get('gun_aligned') and
                    gun_state.ready(reload_factor) and
                    ammo_state.can_fire() and
                    (pending_intent is not None or
                     pending_reproof is not None or
                     self._shot_clear(
                        state, target, now,
                        probe_budget=shot_lane_budget))):
                launch_receipt = None
                if is_spg:
                    launch_receipt = self._artillery_launch_receipt(
                        state, target, descriptor, state['shell_index'],
                        gun_state, ballistic_solution, now)
                if not is_spg or launch_receipt is not None:
                    fired = self._fire(
                        state, gun_state, reload_factor, descriptor,
                        launch_receipt=launch_receipt,
                        ammo_state=ammo_state)
                    if fired and is_spg:
                        self._cancel_artillery_intent(state['id'])
            mode = command.get('combat_mode')
            if (collect_observation and
                    target is not None and target.get('visible') and
                    callable(self.cover_probe) and
                    (mode in ('take_cover', 'cover_hold', 'cover_peek',
                              'cover_return') or
                     (command.get('fire_allowed') and mode in (
                         'engage', 'advance_contact', 'jiggle_forward',
                         'jiggle_back')))):
                cover_jobs.append((state['id'], dict(state), dict(target),
                                   command.get('move_position', position)))
            processed_bot_ids.add(int(state['id']))
        self._finish_world_receipt_frame()
        if collect_observation and not shot_lanes_ready:
            collect_observation = False
        completed_affordances = ()
        if collect_observation:
            completed_affordances = tuple(self._cover_results)
            self._cover_results = []
        cover_jobs.sort(key=lambda value: value[0])
        if collect_observation and cover_jobs:
            cursor = self._cover_cursor % len(cover_jobs)
            ordered = cover_jobs[cursor:] + cover_jobs[:cursor]
            count = min(COVER_JOBS_PER_OBSERVATION, len(ordered))
            self._cover_cursor = (cursor + count) % len(cover_jobs)
            ally_positions = dict((team, [
                _position(value) for value in self.states.values()
                if value.get('alive') and value.get('team') == team])
                for team in (1, 2))
            window_start = _number(now)
            for index, value in enumerate(ordered[:count]):
                bot_id, source, target, route = value
                ready_at = window_start + (
                    COVER_JOB_WINDOW_SECONDS * float(index) /
                    float(count))
                self._cover_queue.append((
                    ready_at, bot_id, source, target, route,
                    tuple(ally_positions.get(source.get('team'), ()))))
        if self._cover_queue:
            ready_at, bot_id, source, target, route, allies = \
                self._cover_queue[0]
            if now + 1e-9 >= ready_at:
                del self._cover_queue[0]
                try:
                    self._probe_totals[2] += 1
                    probe_started = self._probe_started()
                    try:
                        candidates = self.cover_probe(
                            source, target, route, allies,
                            (self.navigator.grid.segment_clear
                             if self.navigator is not None else None))
                    finally:
                        self._probe_finished(2, probe_started)
                except Exception:
                    candidates = ()
                if candidates:
                    self._cover_results.append({
                        'bot_id': int(bot_id),
                        'target_id': int(target.get('network_id')),
                        'target_kind': target.get('kind', 'human'),
                        'candidates': list(candidates),
                    })
        self._pending_ram_reports.extend(
            self._resolve_tank_contacts(players, now, step))
        for state in self._ordered_states():
            if state.get('alive', True) and state['id'] in integrated:
                attempted_yaw = attempted_yaws.get(
                    state['id'], state.get('yaw', 0.0))
                support_blocked = self._update_vertical_motion(
                    state, self._last_step.get(state['id'], frame_step),
                    tick_poses[state['id']], attempted_yaw)
                if not support_blocked:
                    self._guard_realised_pose(
                        state, tick_poses[state['id']], tick_safe[state['id']],
                        attempted_yaw)
                self._update_slope_pose(state)
            if publish:
                self._mark_combat_publication(state)
        if not publish:
            return []
        outgoing = [{'type': 'bot_state', 'bots': [dict(state)
                                                   for state in self._ordered_states()]}]
        # The server validates ram proximity against its latest authority pose.
        # Publish state first, then the cooldown-gated damage reports.
        outgoing.extend(self._pending_ram_reports)
        self._pending_ram_reports = []
        if collect_observation:
            self._next_observation = _number(now) + OBSERVATION_SECONDS
            outgoing.append({
                'type': 'bot_observation',
                'contacts': self._pack_observations(observation_entries),
                'affordances': list(completed_affordances),
            })
        return outgoing

    def presentation_states(self, now=None):
        """Return current authority poses without forming a LAN proposal.

        Poses are published exactly as integrated.  Smoothing between two
        accepted poses belongs to the compound's own MatrixAnimation, which
        INTERPOLATES; extrapolating here as well would guess ahead and then
        correct itself, and that correction is what reads as a jump.
        """
        if not self.is_authority() or self.adapter is None or self.finished:
            return ()
        return tuple(dict(state) for state in self._ordered_states())
