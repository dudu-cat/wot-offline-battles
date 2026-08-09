#!/usr/bin/env python3
"""Generate the #1513 critical-damage module from copied 0.8.2 closures.

The generated file keeps the battle-law bodies from ``offline_battle.py``.
Only presentation and transport live in the handwritten footer: the 0.8.2
mock/Flash calls cannot be used by stock #1513 Vehicle entities.
"""

from pathlib import Path
import sys


BLOCKS = (
    ('class', '_SynthDeviceExtra'),
    ('class', '_SynthMaterial'),
    ('def', '_offh_interior_zone'),
    ('def', '_offh_voice_burst_pick'),
    ('def', '_offh_ignite'),
    ('def', '_offh_extinguish'),
    ('def', '_offh_knock_out_everything'),
    ('def', '_offh_module_test_mode'),
    ('def', '_offh_internal_layout'),
    ('def', '_offh_internal_ray_hits'),
    ('def', '_device_td'),
    ('def', '_crew_roster'),
    ('def', '_recompute_crew_impaired'),
    ('def', '_crew_factor'),
    ('def', '_module_factor'),
    ('def', '_knock_out_crew'),
    ('def', '_dev_destroyed_set'),
    ('def', '_module_ui_name'),
    ('def', '_refresh_mobility_flags'),
    ('def', '_apply_module_damage'),
)


HEADER = '''from __future__ import print_function

"""Generated 0.8.2 critical-damage law with thin #1513 state adapters.

Do not edit copied functions in this file.  Run
``ports/0.9.22/tools/generate_critical_damage.py`` and let the source audit
compare every copied body with ``offline_battle.py``.
"""

import random

from gui.mods.offline_lan_0922 import device_damage as _device_damage


def _descriptor_value(value, name, default=None):
    """Read 0.8.2 mappings or native #1513 item component attributes."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def LOG_DEBUG(*unused_args):
    # The user explicitly requested no trace-heavy battle output.
    return None


_OFFH_VOICE_BURST = [None]
loaded_models = {}

_OFFH_DEATH_DEVICES = ('engineHealth', 'ammoBayHealth', 'fuelTankHealth',
                       'radioHealth', 'gunHealth',
                       'turretRotatorHealth', 'surveyingDeviceHealth',
                       'leftTrackHealth', 'rightTrackHealth')


def _push_device_ui(*unused_args, **unused_kwargs):
    # #1513 presentation is applied after the authoritative payload arrives.
    return None


def _offh_play_crit_voice(*unused_args, **unused_kwargs):
    # PlayerAvatar.showVehicleDamageInfo owns the #1513 sound/UI mapping.
    return None


def _sync_crashed_track(*unused_args, **unused_kwargs):
    # Stock #1513 Vehicle appearance owns damaged-track presentation.
    return None
'''


FOOTER = r'''

def _state(vehicle):
    devices = dict(getattr(vehicle, 'devices_hp', None) or {})
    destroyed = set(getattr(vehicle, '_destroyed_devices', None) or ())
    crew_ko = set(getattr(vehicle, '_crew_ko', None) or ())
    return {
        'devices': devices,
        'destroyed': destroyed,
        'crew_ko': crew_ko,
        'fire': bool(getattr(vehicle, 'is_on_fire', False)),
        'ammo_rack_death': bool(
            getattr(vehicle, '_ammo_rack_death', False)),
    }


def _device_record(name, hp, descriptor, destroyed):
    max_hp = _device_damage.device_max_hp(descriptor, name)
    if max_hp is None:
        max_hp = max(1, int(round(float(hp or 0.0))))
    return {
        'name': str(name),
        'hp': max(0.0, float(hp)),
        'max_hp': max(1.0, float(max_hp)),
        'state': ('destroyed' if name in destroyed else
                  _device_damage.device_state(float(hp), float(max_hp))),
    }


def _payload(before, after, descriptor, cause=None):
    names = sorted(set(before['devices']) | set(after['devices']))
    device_records = [
        _device_record(name, after['devices'].get(
            name, before['devices'].get(name, 0.0)), descriptor,
            after['destroyed']) for name in names]
    events = []
    for record in device_records:
        name = record['name']
        old_hp = before['devices'].get(name)
        old_max = _device_damage.device_max_hp(descriptor, name)
        if old_hp is None:
            old_state = 'normal'
        elif name in before['destroyed']:
            old_state = 'destroyed'
        else:
            old_state = _device_damage.device_state(
                old_hp, old_max if old_max is not None else record['max_hp'])
        if old_state != record['state']:
            event = {'kind': 'device', 'name': name,
                     'old_state': old_state,
                     'state': record['state']}
            if cause:
                event['cause'] = cause
            events.append(event)
    for name in sorted(after['crew_ko'] - before['crew_ko']):
        event = {'kind': 'crew', 'name': str(name),
                 'state': 'destroyed'}
        if cause:
            event['cause'] = cause
        events.append(event)
    for name in sorted(before['crew_ko'] - after['crew_ko']):
        event = {'kind': 'crew', 'name': str(name), 'state': 'normal'}
        if cause:
            event['cause'] = cause
        events.append(event)
    if before['fire'] != after['fire']:
        event = {'kind': 'fire', 'state': bool(after['fire'])}
        if cause:
            event['cause'] = cause
        events.append(event)
    if (not before['ammo_rack_death'] and
            after['ammo_rack_death']):
        event = {'kind': 'ammo_rack', 'state': 'destroyed'}
        if cause:
            event['cause'] = cause
        events.append(event)
    changed = (events or before['devices'] != after['devices'] or
               before['destroyed'] != after['destroyed'] or
               before['crew_ko'] != after['crew_ko'] or
               before['ammo_rack_death'] != after['ammo_rack_death'])
    if not changed:
        return None
    return {
        'devices': device_records,
        'destroyed': sorted(str(name) for name in after['destroyed']),
        'crew_ko': sorted(str(name) for name in after['crew_ko']),
        'fire': bool(after['fire']),
        'ammo_rack_death': bool(after['ammo_rack_death']),
        'events': events,
    }


def apply_direct(vehicle, collisions, start_pos, end_pos, hull_damage,
                 shell, attacker_id, penetrated=None, by_explosion=False):
    """Run the copied 0.8.2 crit loop and return its authoritative delta."""
    if getattr(vehicle, 'devices_hp', None) is None:
        vehicle.devices_hp = {}
    if getattr(vehicle, '_destroyed_devices', None) is None:
        vehicle._destroyed_devices = set()
    if getattr(vehicle, '_crew_ko', None) is None:
        vehicle._crew_ko = set()
    if not hasattr(vehicle, 'is_on_fire'):
        vehicle.is_on_fire = False
    before = _state(vehicle)
    damage = _apply_module_damage(
        vehicle, collisions, start_pos, end_pos, hull_damage, shell,
        attacker_id, penetrated, by_explosion)
    after = _state(vehicle)
    return damage, _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None),
        'explosion' if by_explosion else 'shot')


class _CriticalProposalVehicle(object):
    """Detached state used while a firing client proposes a critical hit.

    The descriptor, pose and component matrices are immutable inputs to the
    copied 0.8.2 law.  Every mutable battle field is copied explicitly so a
    proposal cannot alter the native Vehicle before the server accepts it.
    """
    __slots__ = (
        'id', 'health', 'typeDescriptor', 'position', 'matrix',
        'devices_hp', '_destroyed_devices', '_crew_ko', '_crew_impaired',
        'is_on_fire', '_ammo_rack_death', '_fire_started', '_fire_timer',
        '_is_killed', 'last_sound', 'is_tracked', 'is_engine_dead',
        'is_gun_destroyed', 'is_turret_locked', '_offline_proposal_only',
        '_components')

    def __init__(self, source):
        self.id = source.id
        self.health = source.health
        self.typeDescriptor = source.typeDescriptor
        self.position = source.position
        self.matrix = source.matrix
        self.devices_hp = dict(
            getattr(source, 'devices_hp', None) or {})
        self._destroyed_devices = set(
            getattr(source, '_destroyed_devices', None) or ())
        self._crew_ko = set(getattr(source, '_crew_ko', None) or ())
        self._crew_impaired = frozenset(
            getattr(source, '_crew_impaired', None) or ())
        self.is_on_fire = bool(getattr(source, 'is_on_fire', False))
        self._ammo_rack_death = bool(
            getattr(source, '_ammo_rack_death', False))
        self._fire_started = getattr(source, '_fire_started', None)
        self._fire_timer = float(
            getattr(source, '_fire_timer', 0.0) or 0.0)
        self._is_killed = bool(getattr(source, '_is_killed', False))
        self.last_sound = getattr(source, 'last_sound', None)
        self.is_tracked = bool(getattr(source, 'is_tracked', False))
        self.is_engine_dead = bool(
            getattr(source, 'is_engine_dead', False))
        self.is_gun_destroyed = bool(
            getattr(source, 'is_gun_destroyed', False))
        self.is_turret_locked = bool(
            getattr(source, 'is_turret_locked', False))
        self._offline_proposal_only = True
        self._components = tuple(source.getComponents())

    def getComponents(self):
        return self._components


def propose_direct(vehicle, collisions, start_pos, end_pos, hull_damage,
                   shell, attacker_id, penetrated=None, by_explosion=False):
    """Return a critical-hit proposal without mutating the live Vehicle."""
    if vehicle is None:
        raise ValueError('critical proposal requires a vehicle')
    shadow = _CriticalProposalVehicle(vehicle)
    return apply_direct(
        shadow, collisions, start_pos, end_pos, hull_damage, shell,
        attacker_id, penetrated, by_explosion)


def apply_payload(vehicle, payload):
    """Install one server-relayed state without re-rolling any damage law."""
    if not isinstance(payload, dict):
        return ()
    before = _state(vehicle)
    was_on_fire = bool(getattr(vehicle, 'is_on_fire', False))
    devices = {}
    for record in payload.get('devices') or ():
        if not isinstance(record, dict):
            continue
        name = record.get('name')
        if name:
            devices[str(name)] = max(0.0, float(record.get('hp', 0.0)))
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = set(
        str(name) for name in payload.get('destroyed') or ())
    vehicle._crew_ko = set(
        str(name) for name in payload.get('crew_ko') or ())
    is_on_fire = bool(payload.get('fire', False))
    if is_on_fire and not was_on_fire:
        _offh_ignite(vehicle, False, 'network')
    elif was_on_fire and not is_on_fire:
        _offh_extinguish(vehicle, False, 'network')
    else:
        vehicle.is_on_fire = is_on_fire
    vehicle._ammo_rack_death = bool(
        payload.get('ammo_rack_death', False))
    _recompute_crew_impaired(vehicle)
    _refresh_mobility_flags(vehicle)
    events = tuple(payload.get('events') or ())
    if events:
        return events
    derived = _payload(
        before, _state(vehicle), getattr(vehicle, 'typeDescriptor', None),
        'network')
    if derived is None:
        return ()
    normalized = []
    for source in derived.get('events') or ():
        event = dict(source)
        if (event.get('state') == 'normal' or
                (event.get('kind') == 'device' and
                 event.get('old_state') == 'destroyed' and
                 event.get('state') == 'critical')):
            event['cause'] = 'repair'
        else:
            event['cause'] = 'shot'
        normalized.append(event)
    return tuple(normalized)


def tick_repair(vehicle, dt, repair_skill=100.0, has_big_kit=False):
    """Advance copied 0.8.2 repair law; transport/presentation stay outside."""
    if vehicle is None or dt is None or dt <= 0.0:
        return None
    if float(getattr(vehicle, 'health', 0.0) or 0.0) <= 0.0:
        return None
    descriptor = getattr(vehicle, 'typeDescriptor', None)
    before = _state(vehicle)
    devices = getattr(vehicle, 'devices_hp', None) or {}
    destroyed = set(getattr(vehicle, '_destroyed_devices', None) or ())
    for name in list(devices):
        cap = _device_damage.device_regen_hp(descriptor, name)
        if cap is None or devices[name] >= cap:
            continue
        if (name in _device_damage.NO_REPAIR_PROGRESS_DEVICES and
                bool(getattr(vehicle, 'is_on_fire', False))):
            continue
        devices[name] = _device_damage.repair_step_hp(
            devices[name], name, descriptor, dt, repair_skill, has_big_kit)
        if name in destroyed and devices[name] >= cap:
            destroyed.discard(name)
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = destroyed
    _refresh_mobility_flags(vehicle)
    after = _state(vehicle)
    return _payload(before, after, descriptor, 'repair')


def tick_fire(vehicle, dt, now=None, module_test_mode=False):
    """Advance the copied 0.8.2 fire duration and one-second HP tick."""
    if vehicle is None or dt is None or dt <= 0.0:
        return 0, None
    if (not bool(getattr(vehicle, 'is_on_fire', False)) or
            float(getattr(vehicle, 'health', 0.0) or 0.0) <= 0.0):
        return 0, None
    before = _state(vehicle)
    if now is None:
        try:
            import BigWorld
            now = float(BigWorld.time())
        except Exception:
            now = None
    started = getattr(vehicle, '_fire_started', None)
    if started is None and now is not None:
        started = float(now)
        vehicle._fire_started = started
    if (started is not None and now is not None and
            float(now) - float(started) >=
            _device_damage.FIRE_DURATION_SECONDS):
        # Keep the source ordering: the frame that extinguishes may also
        # complete the final one-second burn tick below.
        _offh_extinguish(vehicle, False, 'burnt out')
    timer = float(getattr(vehicle, '_fire_timer', 0.0) or 0.0) + float(dt)
    damage = 0
    if timer >= 1.0:
        timer -= 1.0
        if not module_test_mode:
            damage = max(1, int(
                float(getattr(vehicle, 'maxHealth', 0.0) or 0.0) *
                _device_damage.FIRE_DAMAGE_FRACTION_PER_SEC))
    vehicle._fire_timer = timer
    after = _state(vehicle)
    return damage, _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None), 'repair')


def apply_drowning(vehicle):
    """Apply the copied all-module/all-crew drowning knockout law."""
    if vehicle is None:
        return None
    before = _state(vehicle)
    _offh_knock_out_everything(vehicle, False)
    after = _state(vehicle)
    return _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None), 'drowning')


def apply_death(vehicle, cause='shot'):
    """Apply the copied ordinary-death module/crew/fire terminal state."""
    if vehicle is None:
        return None
    before = _state(vehicle)
    if bool(getattr(vehicle, 'is_on_fire', False)):
        _offh_extinguish(vehicle, False, cause)
    _offh_knock_out_everything(vehicle, False)
    after = _state(vehicle)
    return _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None), cause)


def use_extinguisher(vehicle):
    if vehicle is None or not bool(getattr(vehicle, 'is_on_fire', False)):
        return None
    before = _state(vehicle)
    _offh_extinguish(vehicle, False, 'extinguisher')
    return _payload(
        before, _state(vehicle), getattr(vehicle, 'typeDescriptor', None),
        'repair')


def repair_device(vehicle, name=None, repair_all=False):
    if vehicle is None:
        return None
    before = _state(vehicle)
    descriptor = getattr(vehicle, 'typeDescriptor', None)
    devices = getattr(vehicle, 'devices_hp', None) or {}
    destroyed = set(getattr(vehicle, '_destroyed_devices', None) or ())
    names = sorted(set(devices) | destroyed)
    if not repair_all:
        if name:
            name = str(name)
            if not name.endswith('Health'):
                name += 'Health'
        if name not in names:
            return None
        names = [name]
    changed = False
    for device_name in names:
        maximum = _device_damage.device_max_hp(descriptor, device_name)
        if maximum is None:
            continue
        if (device_name in destroyed or
                float(devices.get(device_name, maximum)) < float(maximum)):
            devices[device_name] = float(maximum)
            destroyed.discard(device_name)
            changed = True
    if not changed:
        return None
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = destroyed
    _refresh_mobility_flags(vehicle)
    return _payload(before, _state(vehicle), descriptor, 'repair')


def restore_crew(vehicle, name=None, restore_all=False):
    if vehicle is None:
        return None
    before = _state(vehicle)
    crew_ko = set(getattr(vehicle, '_crew_ko', None) or ())
    if restore_all:
        if not crew_ko:
            return None
        crew_ko.clear()
    else:
        name = str(name or '')
        if name not in crew_ko:
            return None
        crew_ko.discard(name)
    vehicle._crew_ko = crew_ko
    _recompute_crew_impaired(vehicle)
    return _payload(
        before, _state(vehicle), getattr(vehicle, 'typeDescriptor', None),
        'repair')


def stat_factor(vehicle, stat):
    if vehicle is None:
        return 1.0
    crew = _crew_factor(vehicle, stat)
    modules = _module_factor(vehicle, stat)
    return crew * modules
'''


def extract_block(source, kind, name):
    lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    marker = '%s %s' % (kind, name)
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith(marker):
            continue
        following = stripped[len(marker):len(marker) + 1]
        if following not in ('(', ':'):
            continue
        prefix = line[:-len(stripped)]
        block = [stripped]
        for candidate in lines[index + 1:]:
            if not candidate.strip():
                block.append('')
                continue
            candidate_prefix = candidate[:-len(candidate.lstrip())]
            if len(candidate_prefix) <= len(prefix):
                break
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
            block.append(candidate.rstrip())
        while block and not block[-1]:
            block.pop()
        return '\n'.join(block)
    raise ValueError('missing %s %s' % (kind, name))


def generate(repo_root):
    repo_root = Path(repo_root).resolve()
    source_path = (repo_root / 'scripts/client/gui/mods/offhangar' /
                   'offline_battle.py')
    target_path = (repo_root / 'ports/0.9.22/src/res/scripts/client/gui/mods' /
                   'offline_lan_0922/critical_damage.py')
    source = source_path.read_text(encoding='utf-8')
    copied = []
    for kind, name in BLOCKS:
        block = extract_block(source, kind, name)
        block = block.replace(
            'gui.mods.offhangar', 'gui.mods.offline_lan_0922')
        # #1513 BasicItem components expose NoLegacyStuff.get(), but that
        # compatibility method intentionally raises "Operation is not
        # allowed".  Preserve the copied laws and adapt only their descriptor
        # access seam to native attributes.
        block = block.replace(
            "_comp is not None and hasattr(_comp, 'get')",
            "_comp is not None")
        block = block.replace(
            "_comp.get('itemTypeName', '')",
            "_descriptor_value(_comp, 'itemTypeName', '')")
        block = block.replace(
            "_eng is not None and hasattr(_eng, 'get')",
            "_eng is not None")
        block = block.replace(
            "_eng.get('fireStartingChance', 0.15)",
            "_descriptor_value(_eng, 'fireStartingChance', 0.15)")
        block = block.replace(
            "_eng2 is not None and hasattr(_eng2, 'get')",
            "_eng2 is not None")
        block = block.replace(
            "_eng2.get('fireStartingChance', 0.15)",
            "_descriptor_value(_eng2, 'fireStartingChance', 0.15)")
        # A firing client may calculate a critical proposal, but native battle
        # presentation is committed only after the server-relayed event.  Keep
        # the copied law while suppressing its legacy UI callbacks on the
        # detached proposal vehicle.
        if name == '_offh_ignite':
            block = block.replace(
                'if not is_player_target:\n\t\treturn',
                "if (not is_player_target or getattr(target_mock, "
                "'_offline_proposal_only', False)):\n\t\treturn")
        elif name == '_offh_extinguish':
            block = block.replace(
                'if is_player_target:\n\t\ttry:',
                "if (is_player_target and not getattr(target_mock, "
                "'_offline_proposal_only', False)):\n\t\ttry:")
        elif name == '_knock_out_crew':
            block = block.replace(
                'if is_player_target:\n\t\ttry:',
                "if (is_player_target and not getattr(mock, "
                "'_offline_proposal_only', False)):\n\t\ttry:")
        elif name == '_apply_module_damage':
            block = block.replace(
                'BigWorld.player().arena.onVehicleKilled('
                'target_mock.id, attacker_id, 1)',
                "if not getattr(target_mock, "
                "'_offline_proposal_only', False):\n"
                '\t\t\t\t\t\tBigWorld.player().arena.onVehicleKilled('
                'target_mock.id, attacker_id, 1)')
            block = block.replace(
                "if is_player_target and not getattr(target_mock, "
                "'_is_killed', False)",
                "if (is_player_target and not getattr(target_mock, "
                "'_offline_proposal_only', False) and not "
                "getattr(target_mock, '_is_killed', False))")
        copied.append(block)
    output = HEADER.rstrip() + '\n\n\n' + '\n\n\n'.join(copied)
    output += FOOTER.rstrip() + '\n'
    target_path.write_text(output, encoding='utf-8')
    return target_path


if __name__ == '__main__':
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[3]
    print(generate(root))
