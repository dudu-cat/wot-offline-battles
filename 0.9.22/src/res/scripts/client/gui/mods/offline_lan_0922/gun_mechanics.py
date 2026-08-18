from __future__ import print_function

"""0.8.2 ammunition, reload and shot-scatter state for pinned #1513.

The source implementation keeps this state inside ``offline_battle.py``.
These methods preserve its descriptor reads, bloom/convergence formulas,
empty-at-countdown start, clip transitions and Gaussian shot dispersion.
Only storage moved from a closure dictionary to an object.
"""

import math
import random

from gui.mods.offline_lan_0922 import loadout


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _positive(value, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if value > 0.0 else float(default)


class GunState(object):

    def __init__(self, descriptor, loadout_modifiers=None, ammo_layout=None):
        gun = descriptor.gun
        self.shots = tuple(_field(gun, 'shots', ()) or ())
        self.base_dispersion = _positive(
            _field(gun, 'shotDispersionAngle', 0.1), 0.1)
        factors = _field(gun, 'shotDispersionFactors', {}) or {}
        self.after_shot = _positive(_field(factors, 'afterShot', 1.5), 1.5)
        self.aim_time = _positive(_field(gun, 'aimingTime', 2.0), 2.0)
        self.reload = _positive(_field(gun, 'reloadTime', 5.0), 5.0)
        clip = _field(gun, 'clip', (1, 2.0)) or (1, 2.0)
        try:
            self.clip_size = max(1, int(clip[0]))
        except (TypeError, ValueError, IndexError):
            self.clip_size = 1
        try:
            self.clip_reload = _positive(clip[1], 2.0)
        except (TypeError, ValueError, IndexError):
            self.clip_reload = 2.0
        maximum = _field(descriptor, 'maxAmmo', None)
        if maximum is None:
            maximum = _field(gun, 'maxAmmo', None)
        if maximum is None:
            maximum = _field(_field(descriptor, 'turret', None),
                             'maxAmmo', 45)
        try:
            maximum = max(0, int(maximum))
        except (TypeError, ValueError):
            maximum = 45
        # The garage layout wins when we know it.  Only an unknown loadout
        # falls back to an even split of maxAmmo.
        self.ammo = self._layout_ammo(ammo_layout)
        if self.ammo is None:
            self.ammo = self._distribute_ammo(maximum, len(self.shots))
        try:
            selected = int(getattr(descriptor, 'activeGunShotIndex', 0))
        except (TypeError, ValueError):
            selected = 0
        self.shot_index = max(0, min(selected, max(0, len(self.shots) - 1)))
        if not self.ammo or self.ammo[self.shot_index] <= 0:
            # Retail loads the first shell the vehicle actually carries.
            for index, count in enumerate(self.ammo):
                if count > 0:
                    self.shot_index = index
                    break
        # 0.8.2 converts the crew level through 1/(0.5 + 0.005 * level) and
        # then applies the gun rammer and gun laying drive.  A bare 100% crew
        # already yields 0.952; ventilation, Brothers in Arms and rations move
        # the level before the conversion.
        if loadout_modifiers is None:
            loadout_modifiers = loadout.baseline()
        self.loadout = dict(loadout_modifiers)
        crew_multiplier = _positive(
            self.loadout.get('crew_multiplier'), 1.0 / (0.5 + 0.005 * 110.0))
        self.base_dispersion *= crew_multiplier
        self.aim_time *= crew_multiplier
        self.reload *= crew_multiplier
        self.clip_reload *= crew_multiplier
        self.aim_time *= _positive(self.loadout.get('aim_time_factor'), 1.0)
        reload_factor = _positive(self.loadout.get('reload_factor'), 1.0)
        self.reload *= reload_factor
        self.clip_reload *= reload_factor
        self.clip = 0
        self.reload_time = self.reload
        self.reload_duration = self.reload
        self.dispersion = self.base_dispersion
        self.load_started = False

    def _layout_ammo(self, ammo_layout):
        """Map a garage shell layout onto this gun's shot order.

        ``ammo_layout`` is the mounted ``{shellCompactDescr: count}`` mapping.
        A shell the current gun cannot fire is ignored, and a shot the player
        carries none of stays at zero, so an empty slot really is empty.
        """
        if not ammo_layout:
            return None
        wanted = {}
        for compact_descr, count in dict(ammo_layout).items():
            try:
                wanted[int(compact_descr)] = max(0, int(count))
            except (TypeError, ValueError):
                continue
        if not wanted:
            return None
        result = []
        matched = False
        for shot in self.shots:
            shell = _field(shot, 'shell', {})
            compact_descr = _field(shell, 'compactDescr', None)
            try:
                compact_descr = int(compact_descr)
            except (TypeError, ValueError):
                compact_descr = None
            count = wanted.get(compact_descr, 0)
            if compact_descr is not None and compact_descr in wanted:
                matched = True
            result.append(count)
        if not matched:
            return None
        return result

    @staticmethod
    def _distribute_ammo(maximum, count):
        if count <= 0:
            return []
        # Exact 0.8.2 offline fallback when CurrentVehicle has no shell counts.
        weights = (0.6, 0.3, 0.1)
        result = []
        for index in range(count):
            weight = weights[index] if index < len(weights) else weights[-1]
            quantity = int(maximum * weight)
            if quantity == 0 and maximum > 0:
                quantity = 1
            result.append(quantity)
        return result

    def sync_shell_index(self, index):
        if not self.shots:
            return False
        try:
            index = int(index)
        except (TypeError, ValueError):
            return False
        index = max(0, min(index, len(self.shots) - 1))
        if index == self.shot_index:
            return False
        self.shot_index = index
        self.clip = 0
        self.reload_time = self.reload
        self.reload_duration = self.reload
        self.load_started = False
        return True

    def can_fire(self, battle_live=True):
        if not battle_live or not self.shots or self.reload_time > 0.0:
            return False
        if self.shot_index >= len(self.ammo):
            return False
        return self.clip > 0 and self.ammo[self.shot_index] > 0

    def commit_fire(self, reload_factor=1.0):
        if not self.can_fire(True):
            return False
        index = self.shot_index
        self.ammo[index] -= 1
        self.clip -= 1
        jump = self.base_dispersion * self.after_shot
        self.dispersion = math.sqrt(
            self.dispersion ** 2 + jump ** 2)
        self.dispersion = min(
            self.dispersion, self.base_dispersion * 15.0)
        if self.clip > 0:
            self.reload_time = self.clip_reload
            self.reload_duration = self.clip_reload
        else:
            self.reload_time = self.reload * max(0.0, float(reload_factor))
            self.reload_duration = self.reload_time
        if self.ammo[index] <= 0:
            for offset in range(1, len(self.ammo) + 1):
                candidate = (index + offset) % len(self.ammo)
                if self.ammo[candidate] > 0:
                    self.shot_index = candidate
                    self.clip = min(self.clip_size, self.ammo[candidate])
                    self.reload_time = max(self.reload_time, self.reload)
                    self.reload_duration = self.reload_time
                    break
        return True

    def tick(self, dt, battle_live, move_speed, rotation_speed,
             turret_speed, descriptor, dispersion_factor=1.0,
             aim_time_factor=1.0):
        dt = max(0.0, float(dt))
        gun = descriptor.gun
        target_dispersion = self.base_dispersion
        try:
            chassis_factors = _field(
                descriptor.chassis, 'shotDispersionFactors')
            if chassis_factors is None:
                raise RuntimeError(
                    '#1513 chassis shot dispersion factors are unavailable')
            move_factor, rotation_factor = chassis_factors
            gun_factors = _field(gun, 'shotDispersionFactors', {}) or {}
            turret_factor = _field(gun_factors, 'turretRotation', 0.0)
            move_term = (float(move_speed) * float(move_factor) *
                         self.loadout.get('bloom_move_factor', 1.0))
            rotation_term = (float(rotation_speed) * float(rotation_factor) *
                             self.loadout.get('bloom_rotation_factor', 1.0))
            turret_term = (float(turret_speed) * float(turret_factor) *
                           self.loadout.get('bloom_turret_factor', 1.0))
            target_dispersion = self.base_dispersion * math.sqrt(
                1.0 + move_term * move_term +
                rotation_term * rotation_term +
                turret_term * turret_term)
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise RuntimeError(
                '#1513 shot dispersion descriptor is invalid: %s' % error)
        target_dispersion *= max(0.0, float(dispersion_factor))
        aiming_time = self.aim_time * max(0.0, float(aim_time_factor))
        if self.dispersion > target_dispersion:
            factor = math.exp(-dt / max(aiming_time, 0.1))
            self.dispersion = target_dispersion + (
                self.dispersion - target_dispersion) * factor
        else:
            self.dispersion = min(
                self.dispersion +
                (target_dispersion - self.dispersion) * 0.2, 5.0)
        if not battle_live:
            self.load_started = False
            return
        self.load_started = True
        if self.reload_time <= 0.0:
            return
        self.reload_time -= dt
        if self.reload_time <= 0.0:
            self.reload_time = 0.0
            if self.clip == 0 and self.shot_index < len(self.ammo):
                self.clip = min(
                    self.clip_size, self.ammo[self.shot_index])

    def scatter(self, direction, perfect_accuracy=False, gauss=None,
                dispersion_angle=None):
        gauss = gauss or random.gauss
        dispersion = (self.dispersion if dispersion_angle is None
                      else float(dispersion_angle))
        sigma = 0.0 if perfect_accuracy else dispersion / 3.0
        direction.x += gauss(0.0, sigma)
        direction.y += gauss(0.0, sigma)
        direction.z += gauss(0.0, sigma)
        direction.normalise()
        return direction
