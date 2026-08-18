from __future__ import print_function

"""Playable #1513 battle runtime built on stock Avatar and Vehicle entities."""

import math
import random
import sys
import time
import traceback

from gui.mods.offline_lan_0922.ai import maps as tactical_maps
from gui.mods.offline_lan_0922.ai import planner as bot_planner
from gui.mods.offline_lan_0922.ai.cover import score_candidates
from gui.mods.offline_lan_0922.artillery_controller import \
    ArtilleryController
from gui.mods.offline_lan_0922.battle_feedback import (
    SixthSenseController, VehicleStatePresenter)
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime, PROBE_KINDS
from gui.mods.offline_lan_0922.entities.avatar_server import AvatarServerBridge
from gui.mods.offline_lan_0922.entities.bigworld_binding import \
    BigWorldVehicleBinding
from gui.mods.offline_lan_0922.entities.remote_vehicle import (
    RemoteVehicleFactory, collide_vehicle_at_matrix)
from gui.mods.offline_lan_0922.entities.runtime import EntityPropertyBuilder
from gui.mods.offline_lan_0922.projectile_manager import InFlightProjectiles
from gui.mods.offline_lan_0922.projectile_runtime import (
    PROJECTILE_BROADPHASE_RADIUS, lerp3, point_in_expanded_segment_bounds,
    point_segment_distance_sq, trajectory_position)
from gui.mods.offline_lan_0922.snapshot_sync import SnapshotSync
from gui.mods.offline_lan_0922.spawn_planner import SpawnPlanner
from gui.mods.offline_lan_0922 import (
    combat_rules, critical_damage, destructibles_compat, gun_mechanics,
    prebaked_destructibles, prebaked_foliage, prebaked_navigation, spotting,
    tank_collision, vehicle_physics, world_collision)


# BigWorld callbacks run on rendered frames.  The mature 0.8.2 battle asks for
# the next frame explicitly; a positive 60 Hz delay can skip rendered frames
# and makes copied local physics, authority bots and remote interpolation step
# even while the renderer itself reports a healthy frame rate.
FRAME_SECONDS = 0.0
# This release is intentionally a measurement build.  The profiler is
# observational only: it never feeds a gameplay clock, deadline or budget.
PERFORMANCE_DIAGNOSTICS = True
DIAGNOSTIC_WINDOW_SECONDS = 5.0
DIAGNOSTIC_TOP_FRAMES = 5
AMMO_SECONDS = 0.10
NETWORK_INPUT_SECONDS = 1.0 / 30.0
RPM_PRESENTATION_SECONDS = 0.10
SPOTTING_UPDATE_SECONDS = 0.10
SPOTTING_PROBE_SECONDS = 0.50
SPOTTING_PHASE_BUCKETS = 5
# Bot tree/column enumeration is a proximity sensor, not presentation work.
# The sensor looks 6 m ahead plus the admitted hull extent, while copied bot
# speed is capped at 35 m/s.  Recheck within 0.10 s or 3 m of realised travel,
# whichever comes first, so no moving hull can skip the contact volume.
BOT_DESTRUCTIBLE_SECONDS = 0.10
BOT_DESTRUCTIBLE_TRAVEL_METRES = 3.0
BOT_SOFT_RECAST_BUDGET = 24
CRITICAL_REPAIR_NETWORK_SECONDS = 1.0
PROJECTILE_PROGRESS_SECONDS = 0.10
PROJECTILE_MAX_TIME_MS = 20000
PROJECTILE_MAX_ACTIVE = 128
PROJECTILE_CHORDS_PER_FRAME = 32
PROJECTILE_MAX_CHORDS_PER_FRAME = 256
# Size the fair global budget for the observed low-FPS boundary without ever
# exceeding the previous release's 256-chord hard cap.
PROJECTILE_SUSTAIN_SECONDS = 1.0 / 15.0
ARTILLERY_ARC_RAYS_PER_FRAME = 4
STANDARD_GAMEPLAY = 'ctf'
PREBATTLE_SECONDS = 15.0
BATTLE_SECONDS = 900.0
BOT_SPAWN_SECONDS = 0.30
_SHOT_EVENT_KINDS = ('shot', 'bot_shot')
_COMBAT_EVENT_KINDS = (
    'health', 'hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit')
_SHOT_OCCLUSION_EPSILON = 1.0e-3
# physics_shared.TRACK_SCROLL_LIMITS: the exact #1513 belt-speed wire range.
TRACK_SCROLL_LIMITS = (-15.0, 30.0)
# PyTrackScroll zeroes both belts while engineMode[0] is at most 1.
ENGINE_MODE_OFF = 0
ENGINE_MODE_IDLE = 1
ENGINE_MODE_RUNNING = 2
# The stock #1513 descriptor converts XML movement bloom to per-m/s and
# per-rad/s factors. Feed those raw factors to PlayerAvatar unchanged: the
# native gun rotator owns the one dispersion state shared by HUD and shots.

# Exact #1513 ``Avatar._MOVEMENT_FLAGS`` values.  PlayerAvatar owns the R/F
# state machine and native cruise HUD; the local server only has to preserve
# the throttle encoded in each ``vehicle_moveWith(flags)`` mailbox call.
_MOVEMENT_FORWARD = 1
_MOVEMENT_BACKWARD = 2
_MOVEMENT_CRUISE_CONTROL50 = 16
_MOVEMENT_CRUISE_CONTROL25 = 32
_CRUISE_MODE_THROTTLE = {
    -2: -1.0,
    -1: -0.5,
    0: 0.0,
    1: 0.25,
    2: 0.5,
    3: 1.0,
}


def _monotonic_time():
    """Use the same non-adjustable clock domain as LANClient deadlines."""
    function = getattr(time, 'monotonic', None)
    if callable(function):
        return float(function())
    return float(time.clock())


_PROFILE_CLOCK = getattr(time, 'perf_counter', None)
if not callable(_PROFILE_CLOCK):
    _PROFILE_CLOCK = time.clock


_FRAME_STAGE_NAMES = (
    'house', 'sync', 'critical', 'drown', 'transition', 'local',
    'outline', 'bots_update', 'bot_present', 'bot_events', 'spot', 'lock',
    'schedule', 'diag_emit')
_PROJECTILE_METRIC_NAMES = (
    'active', 'chords', 'debt', 'advance', 'terminals', 'scans',
    'candidates')


class _FrameDiagnostics(object):
    """Correlate one callback's work with the following render interval."""

    def __init__(self, clock=None, writer=None,
                 window_seconds=DIAGNOSTIC_WINDOW_SECONDS):
        self._clock = clock or _PROFILE_CLOCK
        self._writer = writer or sys.stdout.write
        self._window_seconds = max(0.25, float(window_seconds))
        self.enabled = True
        self.reset()

    def reset(self):
        self._pending = None
        self._frame_id = 0
        self._window_id = 0
        self._reset_window()

    def _reset_window(self):
        self._samples = 0
        self._window_elapsed = 0.0
        self._gap_sum = 0.0
        self._gap_max = 0.0
        self._raw_sum = 0.0
        self._raw_max = 0.0
        self._exec_sum = 0.0
        self._exec_max = 0.0
        self._outside_sum = 0.0
        self._outside_max = 0.0
        self._stage_sums = dict((name, 0.0)
                                for name in _FRAME_STAGE_NAMES)
        self._stage_maxima = dict((name, 0.0)
                                  for name in _FRAME_STAGE_NAMES)
        self._probe_sums = dict((name, 0) for name in PROBE_KINDS)
        self._probe_maxima = dict((name, 0) for name in PROBE_KINDS)
        self._probe_duration_sums = dict(
            (name, 0.0) for name in PROBE_KINDS)
        self._probe_duration_maxima = dict(
            (name, 0.0) for name in PROBE_KINDS)
        self._projectile_sums = dict(
            (name, 0.0) for name in _PROJECTILE_METRIC_NAMES)
        self._projectile_maxima = dict(
            (name, 0.0) for name in _PROJECTILE_METRIC_NAMES)
        self._slow = []
        self._over_50 = 0
        self._over_67 = 0
        self._over_100 = 0
        self._sim_caps = 0
        self._clock_regressions = 0
        self._authority_frames = 0
        self._last_context = {}
        self._emit_due = False

    def _disable(self):
        self.enabled = False
        self._pending = None
        self._slow = []

    def begin(self, entry_wall, raw_dt):
        """Seal the previous callback using this callback's entry interval."""
        self._frame_id += 1
        frame_id = self._frame_id
        if not self.enabled:
            return frame_id
        try:
            pending = self._pending
            if pending is not None:
                wall_gap = float(entry_wall) - pending['entry_wall']
                if wall_gap < 0.0:
                    wall_gap = 0.0
                    self._clock_regressions += 1
                observed_raw = float(raw_dt)
                if observed_raw < 0.0:
                    self._clock_regressions += 1
                row = dict(pending)
                row.update({
                    'next': frame_id,
                    'wall_gap': wall_gap,
                    'raw_dt': observed_raw,
                    'outside': max(0.0, wall_gap - pending['exec']),
                    'bw_minus_wall': observed_raw - wall_gap,
                })
                self._add(row)
            return frame_id
        except Exception:
            self._disable()
            return frame_id

    def _add(self, row):
        self._samples += 1
        gap = row['wall_gap']
        raw_dt = row['raw_dt']
        execution = row['exec']
        outside = row['outside']
        self._window_elapsed += gap
        self._gap_sum += gap
        self._gap_max = max(self._gap_max, gap)
        self._raw_sum += raw_dt
        self._raw_max = max(self._raw_max, raw_dt)
        self._exec_sum += execution
        self._exec_max = max(self._exec_max, execution)
        self._outside_sum += outside
        self._outside_max = max(self._outside_max, outside)
        if gap >= 0.050:
            self._over_50 += 1
        if gap >= 0.067:
            self._over_67 += 1
        if gap >= 0.100:
            self._over_100 += 1
        if raw_dt > 0.100:
            self._sim_caps += 1
        if row.get('context', {}).get('role') == 'authority':
            self._authority_frames += 1
        for name in _FRAME_STAGE_NAMES:
            value = max(0.0, float(row['stages'].get(name, 0.0)))
            self._stage_sums[name] += value
            self._stage_maxima[name] = max(
                self._stage_maxima[name], value)
        for name in PROBE_KINDS:
            value = max(0, int(row['probes'].get(name, 0)))
            self._probe_sums[name] += value
            self._probe_maxima[name] = max(
                self._probe_maxima[name], value)
            duration = max(
                0.0, float(row.get('probe_durations', {}).get(name, 0.0)))
            self._probe_duration_sums[name] += duration
            self._probe_duration_maxima[name] = max(
                self._probe_duration_maxima[name], duration)
        projectile = row.get('projectile') or {}
        for name in _PROJECTILE_METRIC_NAMES:
            value = max(0.0, float(projectile.get(name, 0.0)))
            self._projectile_sums[name] += value
            self._projectile_maxima[name] = max(
                self._projectile_maxima[name], value)
        self._last_context = dict(row.get('context') or {})
        score = (gap, execution)
        inserted = False
        for index, existing in enumerate(self._slow):
            if score > (existing['wall_gap'], existing['exec']):
                self._slow.insert(index, row)
                inserted = True
                break
        if not inserted:
            self._slow.append(row)
        if len(self._slow) > DIAGNOSTIC_TOP_FRAMES:
            del self._slow[DIAGNOSTIC_TOP_FRAMES:]
        if self._window_elapsed >= self._window_seconds:
            self._emit_due = True

    @staticmethod
    def _milliseconds(value):
        return max(0.0, float(value)) * 1000.0

    def _format(self):
        samples = max(1, self._samples)
        elapsed = max(1e-9, self._window_elapsed)
        context = self._last_context
        self._window_id += 1
        prefix = '[Offline LAN 0.9.22] PERF '
        lines = [
            (prefix +
             'summary v=2 window=%d round=%s map=%s phase=%s '
             'samples=%d seconds=%.3f fps=%.2f authority_frames=%d '
             'gap_ms_avg_max=%.3f/%.3f raw_dt_ms_avg_max=%.3f/%.3f '
             'exec_ms_avg_max=%.3f/%.3f outside_ms_avg_max=%.3f/%.3f '
             'over_50_67_100=%d/%d/%d sim_caps=%d clock_regress=%d\n') % (
                 self._window_id, context.get('round', '-'),
                 context.get('map', '-'), context.get('phase', '-'),
                 self._samples, self._window_elapsed,
                 self._samples / elapsed, self._authority_frames,
                 self._milliseconds(self._gap_sum / samples),
                 self._milliseconds(self._gap_max),
                 self._milliseconds(self._raw_sum / samples),
                 self._milliseconds(self._raw_max),
                 self._milliseconds(self._exec_sum / samples),
                 self._milliseconds(self._exec_max),
                 self._milliseconds(self._outside_sum / samples),
                 self._milliseconds(self._outside_max),
                 self._over_50, self._over_67, self._over_100,
                 self._sim_caps, self._clock_regressions),
        ]
        stage_values = []
        for name in _FRAME_STAGE_NAMES:
            stage_values.append('%s=%.3f/%.3f' % (
                name,
                self._milliseconds(self._stage_sums[name] / samples),
                self._milliseconds(self._stage_maxima[name])))
        lines.append(prefix + 'stages_ms_avg_max ' +
                     ' '.join(stage_values) + '\n')
        probe_values = []
        for name in PROBE_KINDS:
            probe_values.append('%s=%.2f/%d' % (
                name, float(self._probe_sums[name]) / samples,
                self._probe_maxima[name]))
        lines.append(prefix + 'probes_avg_max ' +
                     ' '.join(probe_values) + '\n')
        probe_duration_values = []
        for name in PROBE_KINDS:
            probe_duration_values.append('%s=%.3f/%.3f' % (
                name,
                self._milliseconds(
                    self._probe_duration_sums[name] / samples),
                self._milliseconds(self._probe_duration_maxima[name])))
        lines.append(prefix + 'probe_ms_avg_max ' +
                     ' '.join(probe_duration_values) + '\n')
        lines.append(
            (prefix +
             'projectile_avg_max active=%.2f/%.0f chords=%.2f/%.0f '
             'debt_ms=%.3f/%.3f advance_ms=%.3f/%.3f '
             'terminal=%.2f/%.0f scans=%.2f/%.0f '
             'candidates=%.2f/%.0f\n') % (
                 self._projectile_sums['active'] / samples,
                 self._projectile_maxima['active'],
                 self._projectile_sums['chords'] / samples,
                 self._projectile_maxima['chords'],
                 self._milliseconds(
                     self._projectile_sums['debt'] / samples),
                 self._milliseconds(self._projectile_maxima['debt']),
                 self._milliseconds(
                     self._projectile_sums['advance'] / samples),
                 self._milliseconds(self._projectile_maxima['advance']),
                 self._projectile_sums['terminals'] / samples,
                 self._projectile_maxima['terminals'],
                 self._projectile_sums['scans'] / samples,
                 self._projectile_maxima['scans'],
                 self._projectile_sums['candidates'] / samples,
                 self._projectile_maxima['candidates']))
        for rank, row in enumerate(self._slow, 1):
            stages = row['stages']
            probes = row['probes']
            probe_durations = row.get('probe_durations', {})
            projectile = row.get('projectile') or {}
            context = row.get('context') or {}
            lines.append(
                (prefix +
                 'slow rank=%d cause=%d next=%d gap_ms=%.3f '
                 'raw_dt_ms=%.3f bw_minus_wall_ms=%.3f '
                 'prev_exec_ms=%.3f outside_ms=%.3f '
                 'cause_tick_ms=%.3f cause_motion_ms=%.3f '
                 'pose_step_m=%.4f speed_mps=%.3f camera_mps=%.3f '
                 'airborne=%d grind=%d bots=%d outgoing=%d '
                 'transition=%d prev_emit=%d '
                 'projectile=%s stages_ms=%s probes=%s probe_ms=%s\n') % (
                     rank, row['cause'], row['next'],
                     self._milliseconds(row['wall_gap']),
                     row['raw_dt'] * 1000.0,
                     row['bw_minus_wall'] * 1000.0,
                     self._milliseconds(row['exec']),
                     self._milliseconds(row['outside']),
                     self._milliseconds(row['tick_dt']),
                     self._milliseconds(row['motion_dt']),
                     float(context.get('pose_step', 0.0)),
                     float(context.get('speed', 0.0)),
                     float(context.get('camera_speed', 0.0)),
                     int(bool(context.get('airborne'))),
                     int(context.get('grind', 0)),
                     int(context.get('bot_count', 0)),
                     int(context.get('outgoing_count', 0)),
                     int(bool(context.get('transitioned'))),
                     int(bool(row.get('emitted'))),
                     ('active:%d,chords:%d,debt_ms:%.3f,'
                      'advance_ms:%.3f,terminal:%d,scans:%d,'
                      'candidates:%d') % (
                          int(projectile.get('active', 0)),
                          int(projectile.get('chords', 0)),
                          self._milliseconds(projectile.get('debt', 0.0)),
                          self._milliseconds(
                              projectile.get('advance', 0.0)),
                          int(projectile.get('terminals', 0)),
                          int(projectile.get('scans', 0)),
                          int(projectile.get('candidates', 0))),
                     ','.join('%s:%.3f' % (
                         name, self._milliseconds(stages.get(name, 0.0)))
                              for name in _FRAME_STAGE_NAMES),
                     ','.join('%s:%d' % (
                         name, int(probes.get(name, 0)))
                              for name in PROBE_KINDS),
                     ','.join('%s:%.3f' % (
                         name, self._milliseconds(
                             probe_durations.get(name, 0.0)))
                              for name in PROBE_KINDS)))
        return ''.join(lines)

    def finish(self, frame_id, entry_wall, tick_dt, motion_dt, stages,
               probes, context, probe_durations=None, projectile=None):
        if not self.enabled:
            return
        try:
            stages = dict(stages or {})
            probes = dict(probes or {})
            emitted = False
            emit_seconds = 0.0
            if self._emit_due and self._samples:
                emit_start = self._clock()
                payload = self._format()
                self._writer(payload)
                emit_seconds = max(0.0, self._clock() - emit_start)
                emitted = True
                self._reset_window()
            stages['diag_emit'] = emit_seconds
            end_wall = self._clock()
            self._pending = {
                'cause': int(frame_id), 'entry_wall': float(entry_wall),
                'exec': max(0.0, end_wall - float(entry_wall)),
                'tick_dt': max(0.0, float(tick_dt)),
                'motion_dt': max(0.0, float(motion_dt)),
                'stages': stages, 'probes': probes,
                'probe_durations': dict(probe_durations or {}),
                'projectile': dict(projectile or {}),
                'context': dict(context or {}), 'emitted': emitted,
            }
        except Exception:
            self._disable()


def _number(value, default=0.0):
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return float(default)
        return value
    except (TypeError, ValueError):
        return float(default)


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _xyz(value):
    if isinstance(value, dict):
        return (_number(value.get('x')), _number(value.get('y')),
                _number(value.get('z')))
    try:
        return (_number(value[0]), _number(value[1]), _number(value[2]))
    except (TypeError, IndexError):
        return (_number(getattr(value, 'x', 0.0)),
                _number(getattr(value, 'y', 0.0)),
                _number(getattr(value, 'z', 0.0)))


def _distance_2d(first, second):
    dx = float(first[0]) - float(second[0])
    dz = float(first[2]) - float(second[2])
    return math.sqrt(dx * dx + dz * dz)


def _engine_rotation(yaw, pitch=0.0, roll=0.0):
    """Return BigWorld's rotation vector in roll, pitch, yaw order."""
    return (float(roll), float(pitch), float(yaw))


def _movement_throttle(flags):
    """Decode #1513's direction and native R/F preset flags."""
    flags = int(flags)
    if flags & _MOVEMENT_FORWARD:
        direction = 1.0
    elif flags & _MOVEMENT_BACKWARD:
        direction = -1.0
    else:
        return 0.0
    if flags & _MOVEMENT_CRUISE_CONTROL25:
        return direction * 0.25
    if flags & _MOVEMENT_CRUISE_CONTROL50:
        return direction * 0.5
    return direction


def _load_runtime():
    import AccountCommands
    import AreaDestructibles
    import ArenaType
    import AvatarInputHandler
    import BattleFeedbackCommon
    import BigWorld
    import DestructiblesCache
    import Math
    import constants
    import game
    import nations
    from Avatar import ClientVisibilityFlags
    from OfflineMapCreator import g_offlineMapCreator
    from gun_rotation_shared import encodeGunAngles
    from gui.app_loader import g_appLoader
    from gui.app_loader.settings import GUI_GLOBAL_SPACE_ID
    from gui.battle_control.battle_constants import VEHICLE_VIEW_STATE
    from gui.mods.offline_lan_0922.compat import g_compatibility
    from gui.shared.utils import HangarSpace
    from items import vehicles
    from vehicle_systems import model_assembler

    class Runtime(object):
        pass

    runtime = Runtime()
    runtime.account_commands = AccountCommands
    runtime.area_destructibles = AreaDestructibles
    runtime.avatar_input_handler = AvatarInputHandler
    runtime.app_loader = g_appLoader
    runtime.arena_cache = ArenaType.g_cache
    runtime.arena_visibility_mask = ArenaType.getVisibilityMask
    runtime.bigworld = BigWorld
    runtime.client_visibility_flags = ClientVisibilityFlags
    runtime.battle_feedback_common = BattleFeedbackCommon
    runtime.compatibility = g_compatibility
    runtime.constants = constants
    runtime.destructibles_cache = DestructiblesCache
    runtime.encode_gun_angles = encodeGunAngles
    runtime.game = game
    runtime.gui_global_space_id = GUI_GLOBAL_SPACE_ID
    runtime.hangar_space = HangarSpace
    runtime.math = Math
    runtime.model_assembler = model_assembler
    runtime.nations = nations
    runtime.offline_map_creator = g_offlineMapCreator
    runtime.vehicles = vehicles
    runtime.vehicle_view_state = VEHICLE_VIEW_STATE
    return runtime


def _selected_vehicle_has_sixth_sense():
    """Read the selected #1513 crew before the lobby Account is retired."""
    try:
        from CurrentVehicle import g_currentVehicle
        item = getattr(g_currentVehicle, 'item', None)
        for entry in (getattr(item, 'crew', ()) or ()):
            tankman = (entry[1] if isinstance(entry, tuple) and
                       len(entry) == 2 else entry)
            if tankman is None:
                continue
            skills = getattr(tankman, 'skills', None)
            if skills is None:
                skills = getattr(
                    getattr(tankman, 'descriptor', None), 'skills', ())
            for skill in (skills or ()):
                name = str(getattr(skill, 'name', skill)).lower()
                if 'sixthsense' in name:
                    return True
    except Exception:
        pass
    return False


class _LANInputSender(object):

    def __init__(self, owner):
        self.owner = owner
        self.forward = 0.0
        self.turn = 0.0
        self.aim_yaw = 0.0
        self.gun_pitch = 0.0
        self.handbrake = False

    def align_aim(self, turret_yaw=0.0, gun_pitch=0.0):
        """Seed the world-space LAN aim from the attached native gun."""
        unused_position, vehicle_yaw = self.owner.local_pose()
        self.aim_yaw = float(vehicle_yaw) + float(turret_yaw)
        self.gun_pitch = float(gun_pitch)
        return True

    def send_avatar_input(self, vehicle_id, kind, payload):
        payload = payload if isinstance(payload, dict) else {}
        if kind == 'move':
            flags = int(payload.get('flags', 0))
            self.forward = _movement_throttle(flags)
            self.turn = 1.0 if flags & 8 else (-1.0 if flags & 4 else 0.0)
            self.handbrake = bool(flags & 64)
            return self.send_current()
        if kind == 'cruise':
            mode = int(payload.get('mode', 0))
            self.forward = _CRUISE_MODE_THROTTLE.get(mode, 0.0)
            return self.send_current()
        if kind in ('track_world', 'track_relative'):
            self._track(payload.get('point'), kind == 'track_relative')
            # The retail cell echoes an accepted packed gun angle.  Without
            # that sample #1513's VehicleGunRotator compares every client
            # step with the spawn-time zero angle and snaps the turret back
            # toward the hull.  This trusted-client server boundary echoes
            # the rotator's current, speed-limited angle before its next step.
            self.owner._echo_local_gun_angles()
            return self.send_current()
        if kind == 'stop_tracking':
            unused_position, vehicle_yaw = self.owner.local_pose()
            turret_yaw = _number(payload.get('turret_yaw'))
            gun_pitch = _number(payload.get('gun_pitch'))
            self.aim_yaw = vehicle_yaw + turret_yaw
            self.gun_pitch = gun_pitch
            self.owner._echo_local_gun_angles(turret_yaw, gun_pitch)
            return self.send_current()
        if kind == 'shoot':
            return self.owner.shoot(self.aim_yaw, self.gun_pitch)
        if kind == 'development':
            return True
        return False

    def change_vehicle_setting(self, vehicle_id, code, value):
        return self.owner.change_vehicle_setting(code, value)

    def _track(self, point, relative=False):
        target = _xyz(point)
        if relative:
            dx, dy, dz = target
        else:
            position, unused_yaw = self.owner.local_pose()
            dx = target[0] - position[0]
            dy = target[1] - position[1]
            dz = target[2] - position[2]
        horizontal = math.sqrt(dx * dx + dz * dz)
        self.aim_yaw = math.atan2(dx, dz)
        self.gun_pitch = math.atan2(dy, max(horizontal, 0.001))

    def send_current(self):
        position, yaw = self.owner.local_pose()
        health_getter = getattr(self.owner, 'local_health', None)
        health = health_getter() if callable(health_getter) else None
        report_getter = getattr(self.owner, 'local_damage_report', None)
        report = report_getter() if callable(report_getter) else None
        result = self.owner.client.send_input(
            self.forward, self.turn, self.aim_yaw, self.gun_pitch,
            position, yaw, speed=getattr(self.owner, '_local_speed', 0.0),
            reported_health=health,
            reported_critical=(report or {}).get('critical'),
            reported_reason=(report or {}).get('reason'),
            reported_display_health=(report or {}).get('display_health'),
            reported_attacker=(report or {}).get('attacker'),
            reported_attacker_bot=(report or {}).get('attacker_bot'),
            reported_critical_base_revision=(report or {}).get(
                'critical_base_revision'),
            reported_critical_seq=(report or {}).get('critical_seq'))
        return result


class BattleRuntime(object):
    """Own map, real Vehicle entities, snapshot smoothing and authority bots."""

    def __init__(self, runtime=None):
        self._runtime = runtime
        self._config = None
        self._start_message = None
        self.client = None
        self.state = 'idle'
        self.error = None
        self._generation = 0
        self._callback_id = None
        self._ammo_callback_id = None
        self._callback_token = None
        self._ammo_callback_token = None
        self._deadline = 0.0
        self._vehicle_ready_deadline = 0.0
        self._map_create_attempted = False
        self._lobby_retire_started = False
        self._app_loader_guard = None
        self._avatar = None
        self._binding = None
        self._server = None
        self._remote_factory = None
        self._sender = None
        self._sync = None
        self._bots = None
        self._frame_diagnostics = (
            _FrameDiagnostics() if PERFORMANCE_DIAGNOSTICS else None)
        self._sixth_sense = None
        self._has_sixth_sense = False
        self._records = {}
        self._last_snapshot = None
        self._last_frame_time = None
        if self._frame_diagnostics is not None:
            self._frame_diagnostics.enabled = True
            self._frame_diagnostics.reset()
        self._local_position = (0.0, 0.0, 0.0)
        self._local_yaw = 0.0
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._bot_fire_seen = {}
        self._bot_destructible_samples = {}
        self._local_speed = 0.0
        self._local_turn_speed = 0.0
        self._local_push_x = 0.0
        self._local_push_z = 0.0
        self._local_physics = None
        self._local_pitch = 0.0
        self._local_roll = 0.0
        self._local_matrix = None
        self._local_model = None
        self._local_native_matrix = None
        self._local_native_stabilised_matrix = None
        self._local_camera_velocity = None
        self._local_engine_mode = None
        self._spectated_engine_id = None
        self._local_grind = 0
        self._local_motion_soft_block = False
        self._local_motion_cap_crushed = False
        self._soft_static_recast_budget = [BOT_SOFT_RECAST_BUDGET]
        self._local_vertical_speed = 0.0
        self._local_airborne = False
        self._local_fall_armed = False
        self._local_last_pitch = 0.0
        self._local_drive_pitch_history = None
        self._local_smooth_drive_pitch = 0.0
        self._local_slide_speed = 0.0
        self._local_downhill = (0.0, 0.0, 0.0)
        self._local_slope_tangent = 0.0
        self._local_air_lateral = (0.0, 0.0)
        self._input_accumulator = 0.0
        self._gun_state = None
        self._gun_last_tick = None
        self._ammo_signature = None
        self._targeting_signature = None
        self._reload_event = None
        self._equipment_state = None
        self._battle_result = None
        self._round_finished_notified = False
        self._on_local_leave = None
        self._battle_live = True
        self._prebattle_deadline = None
        self._pending_bot_creates = {}
        self._pending_bot_create_order = []
        self._last_bot_create_team = None
        self._next_bot_create_time = 0.0
        self._arena_type = None
        self._spawn_planner = None
        self._navigation_graph = None
        self._grounded_bot_ids = set()
        self._bot_vehicle_assignments = {}
        self._spawn_cache = {}
        self._rules_state = {'bases': {}}
        self._ready_sent = False
        self._destructibles = None
        self._local_damage_report = None
        self._local_critical_base_revision = 0
        self._local_critical_server_revision = 0
        self._local_critical_next_seq = 0
        self._local_critical_owned = False
        self._accepted_event_ids = set()
        self._applied_event_ids = set()
        # Retain the old name as a read-only compatibility view for audits;
        # an event is "seen" only after its native presentation was applied.
        self._seen_event_ids = self._applied_event_ids
        self._event_journal = []
        self._local_last_attacker = None
        self._next_critical_report_time = 0.0
        self._last_presented_rpm = None
        self._next_rpm_time = 0.0
        self._drown_check = 0.0
        self._drown_time = 0.0
        self._drown_level = 0
        self._drown_started = None
        self._outlined_engine_id = None
        self._next_outline_time = 0.0
        self._next_spotting_time = 0.0
        self._foliage = None
        self._projectiles = None
        self._projectile_meta = {}
        self._projectile_visual_meta = {}
        self._projectile_terminal_data = {}
        self._projectile_target_positions = {}
        self._projectile_position_history = []
        self._projectile_lineage = set()
        self._projectile_epoch = None
        self._projectile_server_time_ms = None
        self._projectile_server_local_time = None
        self._projectile_revision = -1
        self._next_projectile_progress_time = 0.0
        self._projectile_frame_start = 0.0
        self._projectile_frame_end = 0.0
        self._projectile_destructible_context = None
        self._projectile_perf = {}
        self._projectile_scan_count = 0
        self._projectile_candidate_count = 0
        self._artillery = None

    def start(self, config, message=None, lan_client=None,
              on_local_leave=None):
        if self.state not in ('idle', 'stopped', 'failed'):
            return False
        if lan_client is None:
            raise ValueError('LAN client is required')
        self._runtime = self._runtime or _load_runtime()
        self._config = dict(config or {})
        area_destructibles = getattr(
            self._runtime, 'area_destructibles', None)
        destructibles_cache = getattr(
            self._runtime, 'destructibles_cache', None)
        if area_destructibles is not None and destructibles_cache is not None:
            destructibles_compat.install(
                area_destructibles, destructibles_cache)
            from gui.mods.offline_lan_0922 import destructibles_sensor
            self._destructibles = destructibles_sensor
        else:
            # Pure-logic tests inject no engine modules.  Production runtime
            # construction above always supplies both exact #1513 modules.
            self._destructibles = None
        # Copy the 0.8.2 ordering: tuning must be applied before either the
        # player or bot descriptor-derived physics parameters are created.
        vehicle_physics.apply_tuning(self._config.get('physics_tuning'))
        combat_rules.apply_he_tuning(self._config.get('he_tuning'))
        self._start_message = dict(message or {})
        self.client = lan_client
        self._sixth_sense = None
        self._has_sixth_sense = _selected_vehicle_has_sixth_sense()
        self._last_snapshot = None
        self._last_frame_time = None
        if self._frame_diagnostics is not None:
            self._frame_diagnostics.enabled = True
            self._frame_diagnostics.reset()
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._bot_fire_seen = {}
        self._bot_destructible_samples = {}
        self._local_speed = 0.0
        self._local_turn_speed = 0.0
        self._local_push_x = 0.0
        self._local_push_z = 0.0
        self._local_physics = None
        self._local_pitch = 0.0
        self._local_roll = 0.0
        self._local_matrix = None
        self._local_model = None
        self._local_native_matrix = None
        self._local_native_stabilised_matrix = None
        self._local_camera_velocity = None
        self._local_engine_mode = None
        self._spectated_engine_id = None
        self._local_grind = 0
        self._local_motion_soft_block = False
        self._local_motion_cap_crushed = False
        self._soft_static_recast_budget = [BOT_SOFT_RECAST_BUDGET]
        self._local_vertical_speed = 0.0
        self._local_airborne = False
        self._local_fall_armed = False
        self._local_last_pitch = 0.0
        self._local_drive_pitch_history = None
        self._local_smooth_drive_pitch = 0.0
        self._local_slide_speed = 0.0
        self._local_downhill = (0.0, 0.0, 0.0)
        self._local_slope_tangent = 0.0
        self._local_air_lateral = (0.0, 0.0)
        self._input_accumulator = 0.0
        self._gun_state = None
        self._gun_last_tick = None
        self._ammo_signature = None
        self._targeting_signature = None
        self._reload_event = None
        self._equipment_state = None
        self._battle_result = self._start_message.get('battle_result')
        self._round_finished_notified = False
        self._on_local_leave = on_local_leave
        self._battle_live = False
        self._prebattle_deadline = None
        self._pending_bot_creates = {}
        self._pending_bot_create_order = []
        self._last_bot_create_team = None
        self._next_bot_create_time = 0.0
        self._navigation_graph = None
        self._grounded_bot_ids = set()
        self._bot_vehicle_assignments = {}
        self._spawn_cache = {}
        self._rules_state = {'bases': {}}
        self._ready_sent = False
        self._lobby_retire_started = False
        self._local_damage_report = None
        self._local_critical_base_revision = 0
        self._local_critical_server_revision = 0
        self._local_critical_next_seq = 0
        self._local_critical_owned = False
        self._accepted_event_ids = set()
        self._applied_event_ids = set()
        self._seen_event_ids = self._applied_event_ids
        self._event_journal = []
        self._local_last_attacker = None
        self._next_critical_report_time = 0.0
        self._last_presented_rpm = None
        self._next_rpm_time = 0.0
        self._drown_check = 0.0
        self._drown_time = 0.0
        self._drown_level = 0
        self._drown_started = None
        self._next_spotting_time = 0.0
        self._foliage = None
        projectile_now = self._clock()
        self._projectiles = InFlightProjectiles(
            maximum_active=PROJECTILE_MAX_ACTIVE,
            initial_time=projectile_now)
        self._projectile_meta = {}
        self._projectile_visual_meta = {}
        self._projectile_terminal_data = {}
        self._projectile_target_positions = {}
        self._projectile_position_history = []
        self._projectile_lineage = set()
        self._projectile_epoch = None
        self._projectile_server_time_ms = None
        self._projectile_server_local_time = None
        self._projectile_revision = -1
        self._next_projectile_progress_time = projectile_now
        self._projectile_frame_start = projectile_now
        self._projectile_frame_end = projectile_now
        self._projectile_destructible_context = None
        self._projectile_perf = {}
        self._projectile_scan_count = 0
        self._projectile_candidate_count = 0
        self._artillery = ArtilleryController()
        self._generation += 1
        self._deadline = self._clock() + float(
            self._config.get('startupTimeoutSeconds', 30.0))
        self._vehicle_ready_deadline = 0.0
        self.state = 'creating_map'
        self.error = None
        try:
            arena_type = self._standard_arena(self._config.get('map'))
            if arena_type is None:
                raise RuntimeError('standard arena definition is unavailable')
            self._arena_type = arena_type
            graph_loader = getattr(
                self._runtime, 'navigation_graph_loader',
                prebaked_navigation.load_graph)
            self._navigation_graph = graph_loader(self._config.get('map'))
            map_name = prebaked_navigation._short_map_name(
                self._config.get('map'))
            if (map_name in prebaked_navigation.SUPPORTED_MAPS and
                    self._navigation_graph is None):
                raise RuntimeError(
                    'validated navigation graph is unavailable for %s' %
                    map_name)
            foliage_loader = getattr(
                self._runtime, 'foliage_loader',
                prebaked_foliage.load_foliage)
            self._foliage = foliage_loader(self._config.get('map'))
            if self._destructibles is not None:
                catalog_loader = getattr(
                    self._runtime, 'destructible_catalog_loader',
                    prebaked_destructibles.load_catalog)
                destructible_catalog = catalog_loader(
                    self._config.get('map'))
                if (map_name in prebaked_navigation.SUPPORTED_MAPS and
                        destructible_catalog is None):
                    raise RuntimeError(
                        'validated destructible catalog is unavailable for %s' %
                        map_name)
                self._destructibles.set_catalog(destructible_catalog)
            self._spawn_planner = SpawnPlanner(
                arena_type,
                tactical_maps.get_tactical_map(self._config['map']),
                self._navigation_graph)
            constants = self._runtime.constants
            local_identity = self._local_state()
            self._runtime.compatibility.set_battle_network_client(self.client)
            self._runtime.compatibility.configure_battle(
                getattr(constants.ARENA_GUI_TYPE, 'RANDOM', 0),
                getattr(constants.ARENA_BONUS_TYPE, 'REGULAR', 0),
                local_identity.get('name', self.client.name),
                int(local_identity.get('team', self.client.team)))
            lobby_boundary = self._preflight_lobby_retirement()
            self._install_battle_gui_guard()
            self._enter_battle_loading()
            self._retire_lobby_entities(lobby_boundary)
            # OfflineMapCreator.create() catches some native setup failures and
            # only calls cancel(), which resets ids but does not clear the
            # partially-created Avatar or space.  Remember the attempt before
            # entering stock code so every exit can run its stronger destroy()
            # rollback, even when Active() is already false afterward.
            self._map_create_attempted = True
            self._create_native_battle_map(self._config['map'])
            if not self._runtime.offline_map_creator.Active():
                raise RuntimeError('stock OfflineMapCreator rejected the map')
            self._avatar = self._runtime.bigworld.player()
            if self._avatar is None:
                raise RuntimeError('stock OfflineMapCreator created no Avatar')
            self._configure_standard_space_visibility()
            if not getattr(
                    self._avatar, '_offlineLANInitComplete', False):
                raise RuntimeError(
                    'stock OfflineMapCreator returned a partial Avatar')
            if not getattr(
                    self._avatar, '_offlineLANPlayerReady', False):
                raise RuntimeError(
                    'stock OfflineMapCreator did not promote its Avatar')
            if self._destructibles is not None:
                self._destructibles.reset(self._avatar.spaceID)
                self._destructibles.set_event_sink(
                    self._report_destructible)
            # From this point onward every stock Avatar branch must see a real
            # battle, not the viewer mode used by OfflineMapCreator.  destroy()
            # does not require Active(), so it still owns the exact space ids.
            self._runtime.offline_map_creator.SetActive(False)
            # Arena metadata exists while geometry and Vehicle prerequisites
            # are still loading in a normal battle.  Publishing it now gives
            # ArenaDataProvider a player id before a fast space-complete
            # callback can request the final battle page.
            self._create_entities()
            return self.state != 'failed'
        except Exception as error:
            self._fail(error)
            return False

    def _preflight_lobby_retirement(self):
        """Validate destructive lobby boundaries before changing GUI state."""
        clear = getattr(
            self._runtime.bigworld, 'clearEntitiesAndSpaces', None)
        if not callable(clear):
            raise RuntimeError(
                'BigWorld.clearEntitiesAndSpaces is unavailable')
        hangar_space = getattr(
            self._runtime.hangar_space, 'g_hangarSpace', None)
        if hangar_space is None:
            raise RuntimeError('hangar space owner is unavailable')
        if not (bool(getattr(hangar_space, 'inited', False)) and
                bool(getattr(hangar_space, 'spaceInited', False))):
            raise RuntimeError(
                'hangar space is not ready for battle transition')
        return clear, hangar_space

    def _retire_lobby_entities(self, boundary):
        """Cross the same Account-to-Avatar boundary as the #1513 observer.

        BigWorld cannot safely promote a client-only Avatar while the lobby
        Account and hangar space are still alive.  The public 0.9.22 observer
        clears them before creating its Avatar; retaining the Account here can
        terminate the native process before Python gets a traceback.
        """
        clear, hangar_space = boundary
        # PlayerAccount.onBecomeNonPlayer owns the complete stock transition:
        # it first detaches ChatManager and all account helpers, then its
        # personality event destroys current/preview vehicles and HangarSpace.
        # Clearing only HangarSpace leaves zombie references to the Account
        # after BigWorld empties the PyEntity dictionary.
        self._lobby_retire_started = True
        if not self._runtime.compatibility.retire_current_player():
            raise RuntimeError('lobby Account retirement did not run')
        if (bool(getattr(hangar_space, 'inited', False)) or
                bool(getattr(hangar_space, 'spaceInited', False))):
            raise RuntimeError(
                'Account retirement did not destroy the hangar space')

        # Keep Account.g_accountRepository alive deliberately.  Exact #1513
        # PlayerAvatar.__init__ reuses its syncData, intUserSettings and
        # prebattleInvitations; the public observer creates that repository
        # when necessary instead of deleting it during this transition.
        clear()
        try:
            player = self._runtime.bigworld.player()
        except ReferenceError:
            player = None
        if player is not None:
            raise RuntimeError('lobby Account survived battle transition')

    def _actual_gui_space_id(self):
        """Read the accepted AppLoader state, not its optimistic context."""
        state = getattr(
            self._runtime.app_loader, '_AppLoader__state', None)
        get_space_id = getattr(state, 'getSpaceID', None)
        if not callable(get_space_id):
            raise RuntimeError('actual battle GUI state is unavailable')
        return get_space_id()

    def _enter_battle_loading(self):
        """Dispose the live lobby before retiring its Account owner."""
        space_ids = self._runtime.gui_global_space_id
        app_loader = self._runtime.app_loader
        if self._actual_gui_space_id() != space_ids.LOBBY:
            raise RuntimeError('battle GUI is not in the lobby state')
        if not app_loader.showBattleLoading():
            raise RuntimeError('battle loading GUI transition was rejected')
        if self._actual_gui_space_id() != space_ids.BATTLE_LOADING:
            raise RuntimeError('battle loading GUI transition did not finish')

    def _create_native_battle_map(self, map_name):
        """Use stock map bookkeeping without starting its viewer UI.

        OfflineMapCreator is a map-viewer helper: it opens the battle page
        before loading, then replaces the battle camera and leaves the GUI
        visibility watcher disabled.  The LAN runtime intentionally starts the
        normal PlayerAvatar battle session, whose ArenaLoadController owns the
        eventual battle page.  Both viewer-only steps are suppressed here.
        The stock helper still owns space creation, geometry mapping, Avatar
        properties and teardown ids.
        """
        creator = self._runtime.offline_map_creator
        setup_name = '_OfflineMapCreator__setupCamera'
        original_setup = getattr(creator, setup_name, None)
        if not callable(original_setup):
            raise RuntimeError(
                'OfflineMapCreator viewer-camera boundary is unavailable')
        creator_dict = getattr(creator, '__dict__', {})
        had_instance_setup = setup_name in creator_dict
        original_instance_setup = creator_dict.get(setup_name)

        app_loader = self._runtime.app_loader
        page_name = 'showBattlePage'
        original_show_page = getattr(app_loader, page_name, None)
        if not callable(original_show_page):
            raise RuntimeError(
                'OfflineMapCreator battle-page boundary is unavailable')
        # Exact _AppLoader uses __slots__, so its instance cannot be patched.
        # Patch the defining class for this synchronous create() window.  Read
        # and restore the raw class attribute to avoid Python 2 bound-method
        # wrappers and never overwrite another patch installed meanwhile.
        loader_type = type(app_loader)
        loader_dict = getattr(loader_type, '__dict__', {})
        had_class_show_page = page_name in loader_dict
        original_class_show_page = loader_dict.get(page_name)

        bigworld = self._runtime.bigworld
        game_module = self._runtime.game

        original_abort = getattr(game_module, 'abort', None)
        if not callable(original_abort):
            raise RuntimeError('game.abort boundary is unavailable')

        def defer_battle_page(unused_app_loader):
            return None

        def finish_native_setup():
            set_watcher = getattr(bigworld, 'setWatcher', None)
            if callable(set_watcher):
                set_watcher('Visibility/GUI', True)

        def reject_game_abort(*unused_args, **unused_kwargs):
            raise RuntimeError(
                'native Avatar requested game.abort during battle start')

        setattr(loader_type, page_name, defer_battle_page)
        try:
            game_module.abort = reject_game_abort
            setattr(creator, setup_name, finish_native_setup)
            try:
                creator.create(map_name)
            finally:
                current_setup = getattr(
                    creator, '__dict__', {}).get(setup_name)
                if current_setup is finish_native_setup:
                    if had_instance_setup:
                        setattr(
                            creator, setup_name, original_instance_setup)
                    else:
                        try:
                            delattr(creator, setup_name)
                        except AttributeError:
                            pass
        finally:
            if getattr(game_module, 'abort', None) is reject_game_abort:
                game_module.abort = original_abort
            current_show_page = getattr(
                loader_type, '__dict__', {}).get(page_name)
            if current_show_page is defer_battle_page:
                if had_class_show_page:
                    setattr(
                        loader_type, page_name, original_class_show_page)
                else:
                    try:
                        delattr(loader_type, page_name)
                    except AttributeError:
                        pass

    def _install_battle_gui_guard(self):
        """Keep exact #1513 GUI transitions ordered for this local round.

        Space loading and arena-roster polling run on separate callbacks.  The
        stock server makes their ordering deterministic; this client-only
        runtime must tolerate either callback arriving first without allowing
        Lobby -> Battle or a late Battle -> BattleLoading regression.
        """
        if self._app_loader_guard is not None:
            return
        app_loader = self._runtime.app_loader
        loader_type = type(app_loader)
        loader_dict = getattr(loader_type, '__dict__', {})
        original_loading = loader_dict.get('showBattleLoading')
        original_page = loader_dict.get('showBattlePage')
        space_ids = getattr(self._runtime, 'gui_global_space_id', None)
        if (not callable(original_loading) or not callable(original_page) or
                space_ids is None):
            raise RuntimeError('battle GUI state boundaries are unavailable')
        lobby_id = space_ids.LOBBY
        loading_id = space_ids.BATTLE_LOADING
        battle_id = space_ids.BATTLE

        def actual_space_id(loader):
            if loader is not app_loader:
                state = getattr(loader, '_AppLoader__state', None)
                get_state_space_id = getattr(state, 'getSpaceID', None)
                if not callable(get_state_space_id):
                    raise RuntimeError(
                        'actual battle GUI state is unavailable')
                return get_state_space_id()
            # Exact #1513 getSpaceID() returns __ctx.guiSpaceID.  changeSpace()
            # writes that requested id *before* asking the current state to
            # accept it, so a rejected transition leaves the public value
            # polluted.  The state object is the authoritative boundary.
            return self._actual_gui_space_id()

        if actual_space_id(app_loader) != lobby_id:
            raise RuntimeError('battle GUI is not in the lobby state')

        def ordered_loading(loader):
            if loader is not app_loader:
                return original_loading(loader)
            if actual_space_id(loader) != lobby_id:
                return None
            result = original_loading(loader)
            if (not result or
                    actual_space_id(loader) != loading_id):
                return None
            return result

        def ordered_page(loader):
            if loader is not app_loader:
                return original_page(loader)
            current = actual_space_id(loader)
            if current == battle_id:
                return None
            if current == lobby_id:
                if not ordered_loading(loader):
                    return None
                current = actual_space_id(loader)
            # Never hand an illegal transition to Scaleform.  The startup
            # timeout will recover the lobby if the native loading state could
            # not be established.
            if current != loading_id:
                return None
            result = original_page(loader)
            if (not result or
                    actual_space_id(loader) != battle_id):
                return None
            return result

        loader_type.showBattleLoading = ordered_loading
        loader_type.showBattlePage = ordered_page
        self._app_loader_guard = {
            'type': loader_type,
            'loading_original': original_loading,
            'loading_wrapper': ordered_loading,
            'page_original': original_page,
            'page_wrapper': ordered_page,
        }

    def _restore_battle_gui_guard(self):
        guard = self._app_loader_guard
        self._app_loader_guard = None
        if guard is None:
            return
        loader_type = guard['type']
        loader_dict = getattr(loader_type, '__dict__', {})
        if (loader_dict.get('showBattleLoading') is
                guard['loading_wrapper']):
            loader_type.showBattleLoading = guard['loading_original']
        if loader_dict.get('showBattlePage') is guard['page_wrapper']:
            loader_type.showBattlePage = guard['page_original']

    def _standard_arena(self, map_name):
        wanted = tactical_maps.normalize_map_name(map_name)
        for unused_id, arena_type in self._runtime.arena_cache.items():
            geometry = tactical_maps.normalize_map_name(
                getattr(arena_type, 'geometryName', None))
            if (geometry == wanted and
                    getattr(arena_type, 'gameplayName', None) ==
                    STANDARD_GAMEPLAY):
                return arena_type
        return None

    def _configure_standard_space_visibility(self):
        """Install the selected gameplay bit normally supplied by the server."""
        bigworld = self._runtime.bigworld
        set_mask = getattr(
            bigworld, 'wg_setSpaceItemsVisibilityMask', None)
        visibility = getattr(
            self._runtime, 'client_visibility_flags', None)
        gameplay_mask = getattr(
            self._runtime, 'arena_visibility_mask', None)
        if (not callable(set_mask) or visibility is None or
                not callable(gameplay_mask)):
            raise RuntimeError(
                '#1513 space visibility boundary is unavailable')
        try:
            # These are unsigned 32-bit masks.  CLIENT_MASK is a Python long
            # in the 32-bit #1513 client and cannot be narrowed through int().
            client_bits = visibility.CLIENT_MASK
            server_bits = visibility.SERVER_MASK
            gameplay_id = int(self._arena_type.gameplayID)
            selected_bit = gameplay_mask(gameplay_id)
            space_id = self._avatar.spaceID
        except (AttributeError, TypeError, ValueError, OverflowError):
            raise RuntimeError(
                '#1513 space visibility contract is invalid')
        if (client_bits & server_bits or
                (client_bits | server_bits) != 0xffffffff or
                selected_bit <= 0 or selected_bit & (selected_bit - 1) or
                selected_bit & ~server_bits):
            raise RuntimeError(
                '#1513 gameplay visibility mask is invalid')
        # PlayerAvatar.__onInitStepCompleted supplies no client visibility
        # bits for an ordinary player.  The complete final stock mask is
        # therefore the server-selected gameplay bit.
        expected = selected_bit
        try:
            # Exact #1513 ClientHangarSpace configures a client-only space
            # through this native setter immediately after createSpace().
            # Such a space has no server-published GeneralSpaceData object, so
            # BigWorld.spaces[spaceID] is not a valid startup requirement.
            set_mask(space_id, expected)
        except (AttributeError, TypeError, ValueError, OverflowError):
            raise RuntimeError(
                '#1513 gameplay visibility mask could not be applied')
        return expected

    def _clock(self):
        function = getattr(self._runtime.bigworld, 'time', None)
        if callable(function):
            try:
                return float(function())
            except Exception:
                pass
        return time.time()

    def _server_clock(self):
        """Return the clock used by exact #1513 countdown consumers."""
        function = getattr(self._runtime.bigworld, 'serverTime', None)
        if callable(function):
            try:
                return float(function())
            except Exception:
                pass
        return self._clock()

    def _server_entity(self, entity_id):
        """Resolve authority state without widening the stock AOI view."""
        if self._remote_factory is not None:
            entity = self._remote_factory.get(entity_id)
            if entity is not None:
                return entity
        return self._runtime.bigworld.entity(entity_id)

    def _attack_reason(self, member, fallback):
        """Resolve exact #1513 ATTACK_REASON indices without guessing."""
        constants = self._runtime.constants
        group = getattr(constants, 'ATTACK_REASON', None)
        indices = getattr(constants, 'ATTACK_REASON_INDICES', {})
        name = getattr(group, member, None)
        try:
            return int(indices[name])
        except (KeyError, TypeError, ValueError):
            return int(fallback)

    def _schedule(self, delay, function, ammo=False):
        generation = self._generation
        if ammo and self._ammo_callback_id is not None:
            try:
                self._runtime.bigworld.cancelCallback(
                    self._ammo_callback_id)
            except Exception:
                pass
            self._ammo_callback_id = None
            self._ammo_callback_token = None
        token = object()
        if ammo:
            self._ammo_callback_token = token
        else:
            self._callback_token = token

        def invoke():
            if ammo:
                if self._ammo_callback_token is token:
                    self._ammo_callback_token = None
                    self._ammo_callback_id = None
            else:
                if self._callback_token is token:
                    self._callback_token = None
                    self._callback_id = None
            if generation == self._generation:
                function()

        try:
            callback_id = self._runtime.bigworld.callback(delay, invoke)
        except Exception:
            if ammo and self._ammo_callback_token is token:
                self._ammo_callback_token = None
            elif not ammo and self._callback_token is token:
                self._callback_token = None
            raise
        if ammo:
            if self._ammo_callback_token is token:
                self._ammo_callback_id = callback_id
        else:
            if self._callback_token is token:
                self._callback_id = callback_id

    def _create_entities(self):
        try:
            self.state = 'loading_entities'
            self._vehicle_ready_deadline = 0.0
            local = self._local_state()
            descriptor = self._runtime.vehicles.VehicleDescr(
                typeName=local.get('vehicle', self._config['vehicle']))
            self._binding = BigWorldVehicleBinding(
                self._runtime.bigworld, self._avatar,
                self._runtime.constants, self._runtime.vehicles.VehicleDescr,
                self._runtime.encode_gun_angles,
                outfit_provider=lambda unused_descriptor: '',
                authority_entity_resolver=self._server_entity)
            self._remote_factory = RemoteVehicleFactory(
                self._runtime.bigworld, self._runtime.math,
                self._runtime.model_assembler, self._avatar.spaceID)
            self._remote_factory.prepare_descriptor(descriptor)
            builder = EntityPropertyBuilder(
                BigWorldVehicleBinding.PROPERTY_NAMES)
            self._sender = _LANInputSender(self)
            position, yaw = self._state_world_pose(local)
            self._local_position = position
            self._local_yaw = yaw
            self._local_descriptor = descriptor
            commands = self._runtime.account_commands
            self._server = AvatarServerBridge(
                self._avatar, self._binding, builder, self._sender,
                account_commands=(commands.CMD_GET_AVATAR_SYNC,
                                  commands.CMD_ADD_INT_USER_SETTINGS,
                                  commands.CMD_DEL_INT_USER_SETTINGS),
                on_ready=self._on_client_ready,
                on_leave=self._defer_avatar_leave,
                on_vehicle_enter=self._prepare_local_presentation,
                on_viewpoint_switch=self._switch_postmortem_viewpoint,
                initial_period='prebattle',
                initial_period_seconds=self._prebattle_seconds())
            self._runtime.compatibility.attach_avatar_server(
                self._avatar, self._server)
            properties = self._binding.properties_from_compact_descr(
                descriptor.makeCompactDescr(), int(local.get('team', 1)),
                local.get('name', self._config.get('name', 'Player')))
            properties['health'] = max(1, min(
                int(local.get('health', descriptor.maxHealth)),
                int(descriptor.maxHealth)))
            snapshot = {
                'properties': properties,
                'position': self._vector(position),
                'rotation': _engine_rotation(yaw),
                'period': 'battle',
            }
            vehicle_id = self._server.addVehicleToArena(snapshot)
            self._synchronise_player_identity(vehicle_id)
            self._invalidate_native_arena_info()
            local_key = 'player:%s' % self.client.player_id
            self._records[local_key] = {
                'engine_id': vehicle_id, 'state': dict(local),
                'kind': 'player', 'network_id': self.client.player_id,
                'local': True, 'ready': False,
                'shot_penalty_until': 0.0}
            self._schedule(0.0, self._wait_for_client_ready)
        except Exception as error:
            self._fail(error)

    def _invalidate_native_arena_info(self):
        """Start stock BattleLoading after player id and roster are present."""
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        shared = getattr(provider, 'shared', None)
        arena_load = getattr(shared, 'arenaLoad', None)
        invalidate = getattr(arena_load, 'invalidateArenaInfo', None)
        if not callable(invalidate):
            raise RuntimeError('native arena-load controller is unavailable')
        invalidate()

    def _synchronise_player_identity(self, expected_vehicle_id):
        """Refresh ArenaDP before marker plugins cache the local vehicle id."""
        expected_vehicle_id = int(expected_vehicle_id)
        if expected_vehicle_id <= 0:
            raise RuntimeError('#1513 player vehicle identity is invalid')
        get_player = getattr(self._runtime.bigworld, 'player', None)
        if not callable(get_player):
            raise RuntimeError('#1513 BigWorld player API is unavailable')
        current_player = get_player()
        if current_player is not self._avatar:
            raise RuntimeError(
                '#1513 BigWorld player changed before ArenaDP refresh')
        avatar_vehicle_id = int(getattr(current_player, 'playerVehicleID', 0))
        if avatar_vehicle_id != expected_vehicle_id:
            raise RuntimeError(
                '#1513 Avatar player identity mismatch before ArenaDP '
                'refresh: expected=%s avatar=%s' % (
                    expected_vehicle_id, avatar_vehicle_id))
        avatar_team = int(getattr(current_player, 'team', 0))
        if avatar_team not in (1, 2):
            raise RuntimeError(
                '#1513 Avatar team is invalid before ArenaDP refresh: '
                'team=%s' % avatar_team)
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        get_arena_dp = getattr(provider, 'getArenaDP', None)
        if not callable(get_arena_dp):
            raise RuntimeError('#1513 ArenaDP provider is unavailable')
        arena_dp = get_arena_dp()
        required = getattr(arena_dp, 'isRequiredDataExists', None)
        get_player_vehicle_id = getattr(
            arena_dp, 'getPlayerVehicleID', None)
        if not callable(required) or not callable(get_player_vehicle_id):
            raise RuntimeError('#1513 ArenaDP player identity API is unavailable')
        # #1513 initializes ArenaDP before the local Vehicle exists, so its
        # cached player id is the integer 0.  getPlayerVehicleID(True) only
        # refreshes a None cache and therefore cannot repair that state.
        # isRequiredDataExists() is the stock boundary which treats 0 as
        # incomplete and re-reads the already-validated Avatar identity.
        if not required():
            raise RuntimeError('#1513 ArenaDP player identity is incomplete')
        refreshed_vehicle_id = int(get_player_vehicle_id(False))
        if refreshed_vehicle_id != expected_vehicle_id:
            raise RuntimeError(
                '#1513 ArenaDP player identity refresh mismatch: '
                'expected=%s arenaDP=%s' % (
                    expected_vehicle_id, refreshed_vehicle_id))
        self._runtime.compatibility.synchronise_vehicle_marker_identity(
            expected_vehicle_id)
        return self._assert_player_identity(expected_vehicle_id)

    def _assert_player_identity(self, expected_vehicle_id):
        """Reject any drift that would relabel player damage as ally damage."""
        expected_vehicle_id = int(expected_vehicle_id)
        avatar_vehicle_id = int(getattr(self._avatar, 'playerVehicleID', 0))
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        get_arena_dp = getattr(provider, 'getArenaDP', None)
        if not callable(get_arena_dp):
            raise RuntimeError('#1513 ArenaDP provider is unavailable')
        arena_dp = get_arena_dp()
        get_player_vehicle_id = getattr(
            arena_dp, 'getPlayerVehicleID', None)
        if not callable(get_player_vehicle_id):
            raise RuntimeError('#1513 ArenaDP player identity API is unavailable')
        arena_vehicle_id = int(get_player_vehicle_id(False))
        if (avatar_vehicle_id != expected_vehicle_id or
                arena_vehicle_id != expected_vehicle_id):
            raise RuntimeError(
                '#1513 player identity mismatch: expected=%s avatar=%s '
                'arenaDP=%s' % (
                    expected_vehicle_id, avatar_vehicle_id,
                    arena_vehicle_id))
        self._runtime.compatibility.assert_vehicle_marker_identity(
            expected_vehicle_id)
        return True

    def _wait_for_client_ready(self):
        if self.state != 'loading_entities':
            return
        try:
            if float(self._runtime.bigworld.spaceLoadStatus()) < 1.0:
                if self._clock() >= self._deadline:
                    self._fail(RuntimeError('map loading timed out'))
                    return
                self._schedule(0.05, self._wait_for_client_ready)
                return
            if self._vehicle_ready_deadline <= 0.0:
                self._vehicle_ready_deadline = self._clock() + float(
                    self._config.get('startupTimeoutSeconds', 30.0))
            self._server.flushClientReady()
            if self._client_ready_received:
                self._finish_entity_startup()
                return
        except Exception as error:
            self._fail(error)
            return
        if self._clock() >= self._vehicle_ready_deadline:
            self._fail(RuntimeError(
                'player Vehicle did not enter world before startup timeout'))
            return
        self._schedule(0.05, self._wait_for_client_ready)

    def _finish_entity_startup(self):
        try:
            descriptor = self._local_descriptor
            if descriptor is None:
                raise RuntimeError('player Vehicle descriptor is unavailable')
            local_key = 'player:%s' % self.client.player_id
            record = self._records.get(local_key)
            if record is None:
                raise RuntimeError('player Vehicle record is unavailable')
            # Exact #1513 reapplies ClientVisibilityFlags late in
            # PlayerAvatar.__onInitStepCompleted.  Cross that stock boundary
            # before restoring the server-selected gameplay bit for this
            # client-only space.
            self._configure_standard_space_visibility()
            record['ready'] = True
            self._attach_local_presentation()
            self._runtime.compatibility.set_control_mode_listener(
                self._on_control_mode_changed)
            self._gun_state = gun_mechanics.GunState(descriptor)
            self._gun_last_tick = self._clock()
            self._sync = SnapshotSync(
                self.client.player_id, on_event=self._apply_sync_event,
                clock=self._clock, pose_safe=self._baked_pose_safe)
            # ``battle_start.bots`` is only a roster reservation.  It has no
            # world pose yet.  Registering those identities here used to make
            # an empty snapshot received during map loading tombstone all 29
            # bots; later canonical states were then intentionally ignored as
            # attempts to resurrect dead entities.  Seed only humans and let
            # the authority manifest / first canonical snapshot create bots.
            initial_manifest = dict(self._start_message)
            initial_manifest['bots'] = []
            self._sync.manifest(initial_manifest)
            latest_snapshot = getattr(self.client, 'last_snapshot', None)
            if (isinstance(latest_snapshot, dict) and
                    latest_snapshot.get('round_id') ==
                    self._start_message.get('round_id')):
                self._last_snapshot = dict(latest_snapshot)
            if self._last_snapshot is not None:
                self._sync.snapshot(self._last_snapshot)
            self._prepare_bot_vehicle_assignments(descriptor)
            self._bots = BotRuntime(
                self.client.player_id,
                descriptor_resolver=self._resolve_descriptor,
                direction_probe=self._direction_probe,
                vehicle_selector=self._select_bot_vehicle,
                visibility_probe=self._bot_visibility,
                firing_lane_probe=self._bot_firing_lane,
                ballistic_solution_probe=self._bot_ballistic_solution,
                artillery_launch_probe=self._bot_artillery_launch,
                artillery_launch_cancel=self._bot_artillery_cancel,
                spawn_resolver=self._formation_pose,
                ground_probe=self._navigation_ground,
                physics_ground_probe=self._ground_y,
                obstacle_probe=self._navigation_obstacle,
                bounds=getattr(self._spawn_planner, 'bounds', None),
                cover_probe=self._sample_bot_cover,
                motion_resolver=self._resolve_bot_motion,
                world_receipt_probe=self._direction_world_receipt,
                baked_graph=self._navigation_graph,
                # Keep the mature 0.8.2 authority model: the copied physics
                # integrator owns bot poses and the engine interpolates those
                # poses for presentation.  A remote #1513 Vehicle has no
                # retail server stream, so treating its WGVehiclePhysics as
                # authoritative leaves movement inputs without pose samples.
                # Keep logical probe counts for frame correlation, but do not
                # read a high-resolution clock around every native query. The
                # two clock calls are diagnostic work on the render thread and
                # cannot affect probe order, results, deadlines or budgets.
                native_motion=False)
            provider = getattr(self._avatar, 'guiSessionProvider', None)
            vehicle_view_state = getattr(
                self._runtime, 'vehicle_view_state', None)
            if provider is not None and vehicle_view_state is not None:
                self._sixth_sense = SixthSenseController(
                    self._runtime.bigworld.callback,
                    self._runtime.bigworld.cancelCallback,
                    lambda: self._generation,
                    lambda: self._has_sixth_sense,
                    lambda: (self.local_health() or 0) > 0,
                    lambda: self.state == 'running' and self._battle_live,
                    VehicleStatePresenter(provider, vehicle_view_state))
            for outgoing in self._bots.battle_start(self._start_message):
                # The authority already owns the exact bot poses it is about
                # to publish.  Materialize that canonical lineup locally now,
                # like 0.8.2 does, instead of waiting for a server echo.  Do
                # not register it in SnapshotSync until the server echoes the
                # canonical lineup: an in-flight empty snapshot between send
                # and echo must not tombstone the local manifest.
                if outgoing.get('type') == 'bot_manifest':
                    for state in outgoing.get('bots') or ():
                        if isinstance(state, dict) and state.get('id') is not None:
                            self._queue_bot_create({
                                'type': 'create',
                                'entity': 'bot:%s' % state['id'],
                                'kind': 'bot', 'id': state['id'],
                                'state': state})
                self._send_bot_message(outgoing)
            if self._last_snapshot is not None:
                self._bots.apply_snapshot(self._last_snapshot)
            self.state = 'running'
            self._bind_local_arcade_camera()
            self._publish_rpm(self._clock(), force=True)
            self._last_frame_time = self._clock()
            self._ammo_tick()
            if self.state != 'running':
                return
            if self._battle_result is not None:
                self._apply_battle_result(self._battle_result)
            if self.state != 'running':
                return
            ready = getattr(self.client, 'send_battle_ready', None)
            if not callable(ready):
                # Engine-free contract tests and non-LAN harnesses have no
                # socket load barrier. Preserve the copied local countdown.
                self.on_battle_live({
                    'countdown_seconds': self._prebattle_seconds(),
                    'battle_duration_seconds': self._battle_seconds(),
                })
            self._schedule(FRAME_SECONDS, self._frame)
        except Exception as error:
            self._fail(error)

    def _local_state(self):
        for value in self._start_message.get('players') or ():
            if value.get('id') == self.client.player_id:
                return dict(value)
        return {
            'id': self.client.player_id, 'name': self.client.name,
            'vehicle': self.client.vehicle, 'team': self.client.team,
            'slot': self.client.slot, 'health': self.client.max_health,
            'max_health': self.client.max_health, 'alive': True}

    def _prebattle_seconds(self):
        return max(0.0, _number(
            self._config.get('prebattleCountdownSeconds',
                             PREBATTLE_SECONDS), PREBATTLE_SECONDS))

    def _battle_seconds(self):
        return max(1.0, _number(
            self._config.get('battleDurationSeconds', BATTLE_SECONDS),
            BATTLE_SECONDS))

    def _begin_battle(self):
        if self._battle_live:
            return False
        duration = self._battle_seconds()
        deadline = getattr(self.client, 'combat_end_deadline', None)
        if deadline is not None:
            duration = max(0.1, float(deadline) - _monotonic_time())
        self._binding.arena_period('battle', duration)
        self._battle_live = True
        self._prebattle_deadline = None
        self._last_frame_time = self._clock()
        if self._gun_state is not None and self._server is not None:
            self._publish_reload_event(
                self._gun_state.reload_time,
                self._gun_state.reload_duration, force=True)
        return True

    def on_battle_live(self, message):
        """Start the one server-owned countdown after every map is ready."""
        if self.state != 'running' or self._battle_live:
            return False
        countdown = max(0.0, _number(
            (message or {}).get('countdown_seconds'),
            self._prebattle_seconds()))
        duration = max(1.0, _number(
            (message or {}).get('battle_duration_seconds'),
            self._battle_seconds()))
        deadline = getattr(self.client, 'combat_deadline', None)
        if deadline is not None:
            countdown = max(0.0, float(deadline) - _monotonic_time())
        network_duration = getattr(self.client, 'combat_duration', None)
        if network_duration is not None:
            duration = max(1.0, float(network_duration))
        self._config['battleDurationSeconds'] = duration
        self._binding.arena_period('prebattle', countdown)
        self._prebattle_deadline = self._clock() + countdown
        self._last_frame_time = self._clock()
        if countdown <= 0.0:
            self._begin_battle()
        return True

    def _bind_local_arcade_camera(self):
        """Bind the initial arcade camera and every aiming provider."""
        handler = getattr(self._avatar, 'inputHandler', None)
        if handler is None:
            raise RuntimeError('native input handler is unavailable')
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if modes is None:
            raise RuntimeError('#1513 control-mode constants are unavailable')
        current = getattr(handler, '_AvatarInputHandler__ctrlModeName', None)
        if current != modes.ARCADE:
            raise RuntimeError('initial #1513 control mode is not arcade')
        self._bind_local_control_sources(handler, current)
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        camera = getattr(control, 'camera', None)
        align_camera = getattr(camera, 'setToVehicleDirection', None)
        if not callable(align_camera):
            raise RuntimeError(
                '#1513 arcade camera direction boundary is unavailable')
        # AvatarInputHandler creates ArcadeCamera before the client-only
        # Vehicle exists.  Its initial yaw therefore comes from the identity
        # target matrix.  Rebinding vehicleMProv above preserves that stale
        # yaw; use the stock public reset after the live matrix is attached.
        align_camera()
        rotator = getattr(self._avatar, 'gunRotator', None)
        reset_rotator = getattr(rotator, 'reset', None)
        if not callable(reset_rotator):
            raise RuntimeError(
                '#1513 gun-direction reset boundary is unavailable')
        # Vehicle.getAimParams reads the appearance turret/gun matrices, not
        # the packed server echo.  Those matrices can still contain a loading
        # angle when the first targeting tick runs.  Exact #1513's public
        # VehicleGunRotator.reset() clears both angles and both matrices
        # without restarting its timer, marker lifecycle or sound objects.
        reset_rotator()
        self._echo_local_gun_angles(0.0, 0.0)
        align_sender = getattr(self._sender, 'align_aim', None)
        if not callable(align_sender):
            raise RuntimeError('player LAN aim sender is unavailable')
        align_sender(0.0, 0.0)
        return True

    def _on_control_mode_changed(self, handler, mode):
        """Verify the new control captured its canonical pose."""
        if self.state != 'running' or self._local_matrix is None:
            return False
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if modes is None:
            raise RuntimeError('#1513 control-mode constants are unavailable')
        if mode == modes.POSTMORTEM:
            result = self._assert_postmortem_control_sources(handler)
            if self._server is not None:
                self._spectated_engine_id = int(self._server.vehicle_id)
            return result
        return self._assert_local_control_sources(handler, mode)

    def _spectator_record(self, engine_id, allow_self=False):
        """Resolve one server-valid postmortem vehicle target."""
        try:
            engine_id = int(engine_id)
        except (TypeError, ValueError, OverflowError):
            return None, None
        for record in self._records.values():
            if int(record.get('engine_id', 0) or 0) != engine_id:
                continue
            if (record.get('tombstone') or not record.get('ready') or
                    int((record.get('state') or {}).get('team', 0) or 0) !=
                    int(getattr(self.client, 'team', 0) or 0)):
                return None, None
            entity = self._server_entity(engine_id)
            if entity is None or getattr(entity, 'matrix', None) is None:
                return None, None
            if record.get('local'):
                if not allow_self:
                    return None, None
            elif not self._record_alive(record, entity):
                return None, None
            return record, entity
        return None, None

    def _switch_postmortem_viewpoint(self, is_viewpoint, engine_id):
        """Perform #1513's server reattach and client callback transaction."""
        if self.state != 'running' or is_viewpoint:
            return False
        handler = getattr(self._avatar, 'inputHandler', None)
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if handler is None or modes is None:
            return False
        if (getattr(handler, '_AvatarInputHandler__ctrlModeName', None) !=
                modes.POSTMORTEM):
            return False
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        if (control is None or
                getattr(control, 'curPostmortemDelay', None) is not None):
            return False
        local_id = (int(self._server.vehicle_id)
                    if self._server is not None else 0)
        record, entity = self._spectator_record(
            engine_id, allow_self=int(engine_id) == local_id)
        if record is None:
            return False

        matrices = getattr(self._avatar, 'consistentMatrices', None)
        attached = getattr(matrices, 'attachedVehicleMatrix', None)
        setter = getattr(matrices, '_ConsistentMatrices__setTarget', None)
        camera = getattr(control, '_PostMortemControlMode__cam', None)
        callback = getattr(self._avatar, 'onSwitchViewpoint', None)
        if (attached is None or not callable(setter) or camera is None or
                not callable(callback)):
            return False
        try:
            previous_target = attached.target
            previous_camera = camera.vehicleMProv
        except AttributeError:
            return False

        target_matrix = (self._local_matrix if record.get('local')
                         else entity.matrix)
        if target_matrix is None:
            return False
        try:
            # A retail cell attachment changes Avatar.vehicle first.  The
            # client-created LAN entities have no cell relationship, so copy
            # its exact Python-visible result before invoking the stock client
            # callback: live attached matrix, then postmortem camera provider.
            if not record.get('local'):
                entity._postmortem_visible = True
            if (self._runtime.bigworld.entity(int(engine_id)) is not entity or
                    int(engine_id) not in self._runtime.bigworld.entities):
                raise RuntimeError(
                    '#1513 spectator entity lookup was rejected')
            setter(target_matrix, False)
            if attached.target is not target_matrix:
                raise RuntimeError(
                    '#1513 spectator matrix attachment was rejected')
            camera.vehicleMProv = attached
            if camera.vehicleMProv is not attached:
                raise RuntimeError(
                    '#1513 spectator camera attachment was rejected')
            position = self._runtime.math.Vector3(0.0, 0.0, 0.0)
            callback(int(engine_id), position)
        except Exception:
            try:
                setter(previous_target, False)
                camera.vehicleMProv = previous_camera
            except Exception:
                pass
            if not record.get('local'):
                entity._postmortem_visible = False
            raise
        previous_id = self._spectated_engine_id
        if previous_id is not None and int(previous_id) != int(engine_id):
            previous = self._server_entity(previous_id)
            if previous is not None and bool(getattr(
                    previous, '_offlineLANPresentation', False)):
                previous._postmortem_visible = False
        self._spectated_engine_id = int(engine_id)
        return True

    def _fallback_postmortem_viewpoint(self, excluded_engine_id):
        """Move off a dead/removed observed ally, preferring the nearest."""
        if self._spectated_engine_id != int(excluded_engine_id):
            return False
        origin = self._local_position
        candidates = []
        for record in self._records.values():
            engine_id = int(record.get('engine_id', 0) or 0)
            if not engine_id or engine_id == int(excluded_engine_id):
                continue
            valid, entity = self._spectator_record(engine_id)
            if valid is None:
                continue
            position = _xyz(entity.position)
            distance = ((position[0] - origin[0]) ** 2 +
                        (position[2] - origin[2]) ** 2)
            candidates.append((distance, engine_id))
        candidates.sort()
        for unused_distance, engine_id in candidates:
            if self._switch_postmortem_viewpoint(False, engine_id):
                return True
        local_id = (int(self._server.vehicle_id)
                    if self._server is not None else 0)
        if local_id and local_id != int(excluded_engine_id):
            return self._switch_postmortem_viewpoint(False, local_id)
        self._spectated_engine_id = None
        return False

    def _release_postmortem_visibility(self):
        engine_id = self._spectated_engine_id
        self._spectated_engine_id = None
        if engine_id is None:
            return False
        entity = self._server_entity(engine_id)
        if (entity is None or not bool(getattr(
                entity, '_offlineLANPresentation', False))):
            return False
        entity._postmortem_visible = False
        return True

    def _assert_postmortem_control_sources(self, handler):
        """Verify the exact stock death-camera provider selected at enable.

        ``PostMortemControlMode.enable`` first binds the attached matrix.  If
        postmortem delay is active, its synchronous ``start()`` then moves the
        same camera before the control-mode callback returns.  Exact #1513
        selects the still-registered player ``Vehicle.matrix`` or, after that
        entity has left the registry, the steady calculator output.  Mirror
        that branch and keep every selected provider on the copied live pose.
        """
        matrices = getattr(self._avatar, 'consistentMatrices', None)
        attached = getattr(matrices, 'attachedVehicleMatrix', None)
        if attached is None:
            raise RuntimeError(
                '#1513 attached vehicle matrix provider is unavailable')
        try:
            attached_target = attached.target
        except AttributeError:
            raise RuntimeError(
                '#1513 attached vehicle matrix target is unavailable')
        expected_target = self._local_matrix
        if (self._spectated_engine_id is not None and self._server is not None
                and int(self._spectated_engine_id) !=
                int(self._server.vehicle_id)):
            record, vehicle = self._spectator_record(
                self._spectated_engine_id)
            if record is not None:
                expected_target = vehicle.matrix
        if attached_target is not expected_target:
            raise RuntimeError(
                '#1513 postmortem attached provider captured a stale '
                'vehicle pose')
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        camera = getattr(control, '_PostMortemControlMode__cam', None)
        if camera is None:
            raise RuntimeError('#1513 postmortem camera is unavailable')
        delay = getattr(control, 'curPostmortemDelay', None)
        expected_camera = attached
        if delay is not None:
            entity_lookup = getattr(self._runtime.bigworld, 'entity', None)
            if not callable(entity_lookup):
                raise RuntimeError(
                    '#1513 postmortem vehicle lookup is unavailable')
            vehicle = entity_lookup(self._avatar.playerVehicleID)
            if vehicle is not None:
                expected_camera = getattr(vehicle, 'matrix', None)
                if expected_camera is not self._local_matrix:
                    raise RuntimeError(
                        '#1513 postmortem vehicle captured a stale vehicle '
                        'pose')
            else:
                calculator = getattr(
                    handler, 'steadyVehicleMatrixCalculator', None)
                expected_camera = getattr(calculator, 'outputMProv', None)
                if expected_camera is None:
                    raise RuntimeError(
                        '#1513 postmortem delay matrix provider is unavailable')
                if (getattr(expected_camera, 'rotationSrc', None) is not
                        self._local_matrix or
                        getattr(expected_camera, 'translationSrc', None) is not
                        self._local_matrix):
                    raise RuntimeError(
                        '#1513 postmortem delay captured a stale vehicle pose')
            if expected_camera is None:
                raise RuntimeError(
                    '#1513 postmortem delay matrix provider is unavailable')
        try:
            camera_matrix = camera.vehicleMProv
        except AttributeError:
            raise RuntimeError(
                '#1513 postmortem camera has no vehicle matrix provider')
        if camera_matrix is not expected_camera:
            raise RuntimeError(
                '#1513 postmortem camera captured a stale vehicle pose')
        return True

    def _assert_local_control_sources(self, handler, mode):
        """Reject a camera transition that captured a stale native filter."""
        calculator = getattr(
            handler, 'steadyVehicleMatrixCalculator', None)
        if calculator is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix calculator is unavailable')
        output = getattr(
            calculator,
            '_SteadyVehicleMatrixCalculator__outputMProv', None)
        stabilised = getattr(
            calculator,
            '_SteadyVehicleMatrixCalculator__stabilisedMProv', None)
        if output is None or stabilised is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix providers are unavailable')
        if (output.rotationSrc is not self._local_matrix or
                output.translationSrc is not self._local_matrix or
                stabilised.target is not self._local_matrix):
            raise RuntimeError(
                '#1513 control mode captured a stale vehicle pose')
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if modes is None:
            raise RuntimeError('#1513 control-mode constants are unavailable')
        if mode != modes.ARCADE:
            return True
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        camera = getattr(control, 'camera', None)
        if camera is None:
            raise RuntimeError('native current camera is unavailable')
        try:
            camera_matrix = camera.vehicleMProv
        except AttributeError:
            raise RuntimeError(
                'initial #1513 camera has no vehicle matrix provider')
        if camera_matrix is not self._local_matrix:
            raise RuntimeError(
                '#1513 arcade camera captured a stale vehicle pose')
        return True

    def _bind_local_control_sources(self, handler, mode):
        """Make arcade/sniper aiming consume the copied live vehicle pose.

        Exact #1513 calls ``SteadyVehicleMatrixCalculator.relinkSources`` at
        the beginning of every control-mode change.  That method reads the
        retail ``WGVehicleFilter.stabilisedMatrix`` and
        ``groundPlacingMatrixFiltered``; a client-only Vehicle never receives
        the server samples that would move those providers beyond spawn.
        The compatibility layer replaces the native relink boundary before a
        stock transition enables its new control. This method establishes the
        initial provider graph and the post-transition listener only verifies
        that the same graph survived.
        """
        if self._local_matrix is None:
            raise RuntimeError('player control bind requires a live pose')
        calculator = getattr(
            handler, 'steadyVehicleMatrixCalculator', None)
        if calculator is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix calculator is unavailable')
        output = getattr(
            calculator,
            '_SteadyVehicleMatrixCalculator__outputMProv', None)
        stabilised = getattr(
            calculator,
            '_SteadyVehicleMatrixCalculator__stabilisedMProv', None)
        if output is None or stabilised is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix providers are unavailable')
        output.rotationSrc = self._local_matrix
        output.translationSrc = self._local_matrix
        stabilised.target = self._local_matrix
        if (output.rotationSrc is not self._local_matrix or
                output.translationSrc is not self._local_matrix or
                stabilised.target is not self._local_matrix):
            raise RuntimeError(
                '#1513 steady vehicle matrix providers rejected live pose')

        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if modes is None:
            raise RuntimeError('#1513 control-mode constants are unavailable')
        if mode != modes.ARCADE:
            # Sniper aiming consumes the steady calculator above. Other stock
            # modes own different cameras and do not expose ArcadeCamera's
            # writable vehicleMProv property.
            return True
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        if control is None:
            raise RuntimeError('native current control mode is unavailable')
        camera = getattr(control, 'camera', None)
        if camera is None:
            raise RuntimeError('native current camera is unavailable')
        try:
            previous = camera.vehicleMProv
        except AttributeError:
            raise RuntimeError(
                'initial #1513 camera has no vehicle matrix provider')
        camera.vehicleMProv = self._local_matrix
        if camera.vehicleMProv is not self._local_matrix:
            # The exact #1513 getter unwraps the translation-only provider and
            # returns its source.  An identity mismatch therefore means the
            # native setter did not accept the copied live matrix.
            camera.vehicleMProv = previous
            raise RuntimeError(
                'native arcade camera rejected the player pose provider')
        return True

    def local_health(self):
        if self._server is None:
            return None
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None:
            return None
        try:
            return max(0, int(entity.health))
        except (TypeError, ValueError, AttributeError):
            return None

    def _normalised_rpm(self):
        """Copy #1513's three-gear simulated RPM law for local physics."""
        descriptor = self._local_descriptor
        if descriptor is None:
            raise RuntimeError('player descriptor is unavailable for RPM')
        physics = _field(descriptor, 'physics', {})
        limits = tuple(_field(physics, 'speedLimits', ()) or ())
        if len(limits) < 2:
            raise RuntimeError('#1513 vehicle speed limits are unavailable')
        speed_range = (abs(float(limits[0])) + abs(float(limits[1]))) / 3.0
        if speed_range <= 0.0:
            raise RuntimeError('#1513 vehicle speed range is invalid')
        speed = abs(float(self._local_speed))
        if speed < 0.05:
            return 0.0
        gear = math.ceil(
            math.floor(speed * 50.0) / 50.0 / speed_range)
        gear = max(1.0, gear)
        rpm = abs(1.0 + (speed - gear * speed_range) / speed_range)
        # Exact _RpmStateHandler shifts a running engine from the raw 0..1
        # range to 0.3..1.0 before presenting it on the HUD.
        return max(0.0, min(1.0, 0.3 + rpm * 0.7))

    def _publish_rpm(self, now, force=False):
        if not force and now < self._next_rpm_time:
            return False
        value = self._normalised_rpm()
        self._next_rpm_time = now + RPM_PRESENTATION_SECONDS
        if (not force and self._last_presented_rpm is not None and
                abs(value - self._last_presented_rpm) <= 0.01):
            return False
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        state = getattr(self._runtime, 'vehicle_view_state', None)
        invalidate = getattr(provider, 'invalidateVehicleState', None)
        if state is None or not callable(invalidate):
            raise RuntimeError(
                '#1513 RPM vehicle-state presentation is unavailable')
        invalidate(state.RPM, value)
        self._last_presented_rpm = value
        return True

    def local_damage_report(self):
        return self._local_damage_report

    def acknowledge_local_damage_report(self, base_revision, ack_seq,
                                        server_revision):
        """Retire only a checkpoint canonically acknowledged by the server."""
        base_revision = max(0, int(base_revision))
        ack_seq = max(0, int(ack_seq))
        server_revision = max(0, int(server_revision))
        if server_revision < self._local_critical_server_revision:
            return False
        self._local_critical_server_revision = server_revision
        if base_revision != self._local_critical_base_revision:
            self._local_critical_base_revision = base_revision
            self._local_critical_next_seq = 0
            self._local_critical_owned = False
            self._local_damage_report = None
            return False
        report = self._local_damage_report
        if (report is not None and
                ack_seq >= int(report.get('critical_seq', 0))):
            self._local_damage_report = None
            return True
        return False

    def _queue_local_damage_report(self, critical=None, reason=None,
                                   display_health=None,
                                   attribute_attacker=True):
        report = dict(self._local_damage_report or {})
        if isinstance(critical, dict):
            if critical != report.get('critical'):
                self._local_critical_next_seq += 1
            report['critical'] = critical
            report['critical_base_revision'] = (
                self._local_critical_base_revision)
            report['critical_seq'] = self._local_critical_next_seq
            self._local_critical_owned = True
        if reason is not None:
            report['reason'] = max(0, int(reason))
        if display_health is not None:
            report['display_health'] = max(0, int(display_health))
        if not attribute_attacker:
            report.pop('attacker', None)
            report.pop('attacker_bot', None)
        attacker = self._local_last_attacker if attribute_attacker else None
        if attacker is not None:
            if attacker[0] == 'bot':
                report['attacker_bot'] = max(0, int(attacker[1]))
                report.pop('attacker', None)
            else:
                report['attacker'] = max(0, int(attacker[1]))
                report.pop('attacker_bot', None)
        self._local_damage_report = report or None
        return self._local_damage_report

    def _resolve_descriptor(self, vehicle_name):
        try:
            descriptor = self._runtime.vehicles.VehicleDescr(
                typeName=vehicle_name)
        except Exception:
            descriptor = self._runtime.vehicles.VehicleDescr(
                typeName=self._config['vehicle'])
        if self._remote_factory is None:
            raise RuntimeError(
                '#1513 vehicle descriptor geometry owner is unavailable')
        return self._remote_factory.prepare_descriptor(descriptor)

    def _select_bot_vehicle(self, raw):
        requested = raw.get('vehicle')
        if requested:
            return requested
        return self._bot_vehicle_assignments.get(
            (int(raw.get('team', 1)), int(raw.get('slot', 0))),
            self._config['vehicle'])

    @staticmethod
    def _vehicle_excluded(entry):
        tags = _field(entry, 'tags', ()) or ()
        if 'secret' in tags:
            return True
        return _field(entry, 'name') == 'usa:T23'

    @staticmethod
    def _vehicle_class_order(entry):
        tags = _field(entry, 'tags', ()) or ()
        for tag, order in (('heavyTank', 0), ('mediumTank', 1),
                           ('AT-SPG', 2), ('lightTank', 3), ('SPG', 4)):
            if tag in tags:
                return order
        return 1

    @staticmethod
    def _vehicle_profile(entry):
        """Convert a #1513 vehicle item or descriptor type to AI data."""
        return {
            'name': str(_field(entry, 'name', '')),
            'level': int(_field(entry, 'level', 1) or 1),
            'tags': _field(entry, 'tags', ()) or (),
        }

    def _prepare_bot_vehicle_assignments(self, player_descriptor):
        """Build the mature mirrored 0.8.2 line-up afresh per battle.

        There is deliberately no process-wide vehicle pool.  The selected
        battle tiers and role template are shared by both teams, humans remove
        their matching slots, and bots fill the remainder from the complete
        eligible #1513 vehicle catalog.
        """
        try:
            player_profile = self._vehicle_profile(player_descriptor.type)
            tier = int(player_profile['level'])
            candidates = []
            for nation in self._runtime.nations.AVAILABLE_NAMES:
                nation_id = self._runtime.nations.INDICES[nation]
                values = self._runtime.vehicles.g_list.getList(nation_id)
                iterator = getattr(values, 'itervalues', None)
                entries = iterator() if callable(iterator) else values.values()
                for entry in entries:
                    if (bot_planner.vehicle_in_battle_tier_band(
                            tier, _field(entry, 'level')) and
                            not self._vehicle_excluded(entry)):
                        candidates.append(self._vehicle_profile(entry))
            if not candidates:
                return False

            roster = self._start_message.get('bots') or ()
            bots_by_team = dict((team, sorted(
                (raw for raw in roster if isinstance(raw, dict) and
                 int(raw.get('team', 0)) == team),
                key=lambda raw: int(raw.get('slot', 0))))
                for team in (1, 2))
            humans_by_team = {1: [], 2: []}
            for raw in self._start_message.get('players') or ():
                if not isinstance(raw, dict):
                    continue
                team = int(raw.get('team', 0) or 0)
                if team not in humans_by_team:
                    continue
                try:
                    if raw.get('id') == getattr(
                            self.client, 'player_id', None):
                        descriptor = player_descriptor
                    else:
                        descriptor = self._resolve_descriptor(
                            raw.get('vehicle'))
                    humans_by_team[team].append(
                        self._vehicle_profile(descriptor.type))
                except Exception:
                    pass
            if not humans_by_team[1] and not humans_by_team[2]:
                team = int(getattr(self.client, 'team', 1) or 1)
                humans_by_team[1 if team != 2 else 2].append(player_profile)

            available_tiers = sorted(set(
                int(candidate['level']) for candidate in candidates))
            match_tiers = list(bot_planner.choose_match_tiers(
                tier, random.random(), random.random(), available_tiers))
            for profiles in humans_by_team.values():
                for profile in profiles:
                    if profile['level'] not in match_tiers:
                        match_tiers.append(profile['level'])
                    if not any(
                            candidate['level'] == profile['level'] and
                            bot_planner.vehicle_match_class(candidate) ==
                            bot_planner.vehicle_match_class(profile)
                            for candidate in candidates):
                        candidates.append(profile)
            match_tiers = tuple(sorted(set(match_tiers)))
            team_size = max(
                len(humans_by_team[team]) + len(bots_by_team[team])
                for team in (1, 2))
            requirements = bot_planner.shared_human_requirements(
                humans_by_team)
            template = bot_planner.build_match_template(
                candidates, team_size, player_profile, match_tiers,
                random, requirements)

            assignments = {}
            for team in (1, 2):
                team_bots = bots_by_team[team]
                picked = bot_planner.remaining_match_template(
                    template, humans_by_team[team])
                if len(picked) < len(team_bots):
                    picked = bot_planner.select_bot_lineup(
                        picked or candidates, len(team_bots), 1, candidates)
                picked = list(picked[:len(team_bots)])
                random.shuffle(picked)
                picked.sort(key=self._vehicle_class_order)
                for raw, entry in zip(team_bots, picked):
                    assignments[(team, int(raw.get('slot', 0)))] = \
                        entry['name']
            self._bot_vehicle_assignments = assignments
            return True
        except Exception:
            # The local tank remains a valid descriptor fallback. The complete
            # roster table itself is a #1513 retail API and is ABI-audited.
            self._bot_vehicle_assignments = {}
            return False

    def _formation_pose(self, team, slot):
        key = (int(team), int(slot))
        cached = self._spawn_cache.get(key)
        if cached is not None:
            return cached
        if self._spawn_planner is None:
            self._spawn_planner = SpawnPlanner(
                self._arena_type,
                tactical_maps.get_tactical_map(self._config['map']),
                self._navigation_graph)
        result = self._spawn_planner.pose(key[0], key[1])
        self._spawn_cache[key] = result
        return result

    def _state_world_pose(self, state):
        if bool(state.get('world_pose', False)):
            position = (_number(state.get('x')), _number(state.get('y')),
                        _number(state.get('z')))
            yaw = _number(state.get('yaw'))
        else:
            return self._formation_pose(
                int(state.get('team', 1)), int(state.get('slot', 0)))
        ground = self._ground_y(
            position[0], position[2], position[1],
            allow_wide=self._navigation_graph is None)
        if ground is not None:
            position = (position[0], ground, position[2])
        return position, yaw

    def _vector(self, position):
        return self._runtime.math.Vector3(
            float(position[0]), float(position[1]), float(position[2]))

    def _ground_y(self, x, z, hint=0.0, allow_wide=False):
        """Use the 0.8.2 near-hull probe so roofs do not become terrain."""
        try:
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID,
                self._vector((x, hint + 8.0, z)),
                self._vector((x, hint - 30.0, z)), 128)
            if hit is not None:
                value = float(hit[0].y)
                if -14.0 < value - float(hint) < 6.0:
                    return value
        except Exception:
            pass
        if not allow_wide:
            return None
        from_y = max(1000.0, hint + 50.0)
        height = None
        for unused_layer in range(4):
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID, self._vector((x, from_y, z)),
                self._vector((x, -1000.0, z)), 128)
            if hit is None:
                return None
            height = float(hit[0].y)
            below = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID,
                self._vector((x, height - 0.4, z)),
                self._vector((x, -1000.0, z)), 128)
            if below is None or height - float(below[0].y) < 2.5:
                return height
            from_y = height - 0.4
        return height

    def _navigation_ground(self, x, z, hint_y=0.0):
        """Copy the 0.8.2 same-layer graph probe, including ford depth."""
        probe_top = float(hint_y) + 8.0
        probe_bottom = float(hint_y) - 18.0
        for unused_layer in range(3):
            try:
                hit = self._runtime.bigworld.wg_collideSegment(
                    self._avatar.spaceID,
                    self._vector((x, probe_top, z)),
                    self._vector((x, probe_bottom, z)), 128)
            except Exception:
                return None
            if hit is None:
                return None
            height = float(hit[0].y)
            if height <= float(hint_y) + 4.5:
                if self._water_depth((x, height, z)) > 1.0:
                    return None
                return height
            probe_top = height - 0.35
        return None

    def _baked_pose_safe(self, position):
        """Apply the validated map's fatal-hazard mask to prediction only."""
        return prebaked_navigation.pose_is_safe(
            self._navigation_graph, position, shoulder_cells=0)

    def _direction_probe(self, position, yaw, speed=0.0,
                         descriptor=None):
        """Copy the 0.8.2 dual-height, three-lane hull corridor probe."""
        x, y, z = _xyz(position)
        far_distance = 20.0 if abs(float(speed or 0.0)) > 5.0 else 15.0
        previous_y = y
        previous_distance = 0.0
        sine = math.sin(float(yaw))
        cosine = math.cos(float(yaw))
        lateral_x = cosine
        lateral_z = -sine
        maximum_slope = 0.0
        signed_speed = float(speed or 0.0)
        planned_impact_speed = abs(signed_speed)
        deferred = False
        planning_params = None
        if descriptor is not None:
            try:
                planning_params = vehicle_physics.derive_params(descriptor)
            except (AttributeError, KeyError, TypeError, ValueError):
                raise RuntimeError(
                    'bot destructible planning speed is unavailable')
        for height, distance in ((0.7, 8.0), (1.5, far_distance)):
            nx = x + sine * distance
            nz = z + cosine * distance
            run = distance - previous_distance
            probe_up = max(4.5, run * 0.52)
            probe_down = max(5.0, run * 0.45)
            try:
                ground = self._runtime.bigworld.wg_collideSegment(
                    self._avatar.spaceID,
                    self._vector((nx, previous_y + probe_up, nz)),
                    self._vector((nx, previous_y - probe_down, nz)), 128)
            except Exception:
                return {'clear': False, 'collision': True,
                        'water': False, 'slope': 99.0}
            if ground is None:
                return {'clear': False, 'collision': False,
                        'water': False, 'slope': 99.0}
            next_y = float(ground[0].y)
            water_depth = self._water_depth((nx, next_y, nz))
            if water_depth > 1.0:
                return {'clear': False, 'collision': False,
                        'water': True, 'slope': 0.0}
            delta = next_y - previous_y
            slope = delta / max(0.1, run)
            maximum_slope = max(maximum_slope, abs(slope))
            if delta > run * 0.48 or delta < -run * 0.38:
                return {'clear': False, 'collision': False,
                        'water': False, 'slope': slope}
            for offset in (-2.2, 0.0, 2.2):
                ray_start = self._vector((
                    x + lateral_x * offset, y + height,
                    z + lateral_z * offset))
                ray_end = self._vector((
                    nx + lateral_x * offset, next_y + height,
                    nz + lateral_z * offset))
                try:
                    collision = self._runtime.bigworld.wg_collideSegment(
                        self._avatar.spaceID, ray_start, ray_end, 128)
                except Exception:
                    collision = True
                if collision is not None:
                    ray_impact_speed = planned_impact_speed
                    if planning_params is not None:
                        try:
                            hull_bbox = self._destructibles._vehicle_hull_bbox(
                                descriptor)
                            minimum, maximum = hull_bbox[:2]
                            reversing = signed_speed < 0.0
                            hull_reach = max(
                                0.0,
                                (-float(minimum[2]) if reversing else
                                 float(maximum[2])))
                            hit_distance = max(
                                0.0,
                                (collision[0] - ray_start).length - hull_reach)
                            # Use the current copied traction law to estimate
                            # only the speed reachable before this far hit. The
                            # actual hull contact still owns the retail gate.
                            drive_sign = -1.0 if reversing else 1.0
                            acceleration = abs(
                                vehicle_physics.engine_force(
                                    planning_params,
                                    drive_sign * max(
                                        planned_impact_speed, 0.1),
                                    drive_sign, 0.0)) / max(
                                        planning_params['mass'], 1.0)
                            speed_limit = float(planning_params[
                                'speedBwd' if reversing else 'speedFwd'])
                            ray_impact_speed = min(
                                speed_limit,
                                math.sqrt(planned_impact_speed ** 2 +
                                          2.0 * acceleration * hit_distance))
                        except (AttributeError, KeyError, TypeError,
                                ValueError, ZeroDivisionError, RuntimeError):
                            return {'clear': False, 'collision': True,
                                    'water': False, 'slope': slope}
                    if descriptor is not None and self._destructibles is not None:
                        kinetic_speed = None
                        if planning_params is not None:
                            kinetic_speed = float(planning_params[
                                'speedBwd' if signed_speed < 0.0 else
                                'speedFwd'])
                        soft_status = (
                            self._destructibles._catalog_soft_static_path(
                                self._avatar.spaceID, ray_start, ray_end,
                                collision, ray_impact_speed, descriptor,
                                recast_budget=
                                self._soft_static_recast_budget,
                                allow_kinetic_first=True,
                                kinetic_speed=kinetic_speed))
                        if soft_status is True:
                            continue
                        if soft_status == 'kinetic':
                            # Planning may approach a contact that this vehicle can
                            # crush at its directional speed cap. The commit-side
                            # native ray and exact hull contact still own destruction.
                            continue
                        if soft_status == 'deferred':
                            # Budget exhaustion is not evidence of a wall. Keep
                            # checking the remaining lanes so any directly
                            # proved backing wall can still win this sample.
                            deferred = True
                            continue
                    return {'clear': False, 'collision': True,
                            'water': False, 'slope': slope}
            previous_y = next_y
            previous_distance = distance
        result = {'clear': True, 'collision': False,
                  'water': False, 'slope': maximum_slope}
        if deferred:
            result['deferred'] = True
        return result

    def _direction_world_receipt(self, position, travel_yaw, signed_speed,
                                 descriptor):
        """Prove the exact flat-ground 3x3 hull corridor without mutation."""
        try:
            planning_params = vehicle_physics.derive_params(descriptor)
            bbox = self._destructibles._vehicle_hull_bbox(descriptor)
            minimum, maximum = bbox[:2]
            half_width = max(
                abs(float(minimum[0])), abs(float(maximum[0]))) - 0.1
            leading = (-float(minimum[2]) if signed_speed < 0.0 else
                       float(maximum[2]))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return None
        if half_width <= 0.0 or leading <= 0.0:
            return None
        x, y, z = _xyz(position)
        sine = math.sin(float(travel_yaw))
        cosine = math.cos(float(travel_yaw))
        lateral_x = cosine
        lateral_z = -sine
        # This is a containment proof, not a planning distance.  Fifteen metres
        # covers the hull, the <=3.5 m cache drift and an ordinary rendered
        # frame at the copied speed limit.  A long/slow frame simply fails the
        # containment check and falls back to the authoritative world sweep.
        proof_distance = 15.0
        planned_impact_speed = abs(float(signed_speed or 0.0))
        cap_speed = None
        if planning_params is not None:
            cap_speed = float(planning_params[
                'speedBwd' if signed_speed < 0.0 else 'speedFwd'])
        for offset in (-half_width, 0.0, half_width):
            sx = x + lateral_x * offset - sine * 0.5
            sz = z + lateral_z * offset - cosine * 0.5
            ex = x + lateral_x * offset + sine * proof_distance
            ez = z + lateral_z * offset + cosine * proof_distance
            for height in (0.6, 1.1, 1.6):
                ray_start = self._vector((sx, y + height, sz))
                ray_end = self._vector((ex, y + height, ez))
                try:
                    collision = self._runtime.bigworld.wg_collideSegment(
                        self._avatar.spaceID, ray_start, ray_end, 128)
                except Exception:
                    return False
                if collision is None:
                    continue
                soft_status = self._destructibles._catalog_soft_static_path(
                    self._avatar.spaceID, ray_start, ray_end, collision,
                    planned_impact_speed, descriptor,
                    recast_budget=self._soft_static_recast_budget,
                    allow_kinetic_first=True, kinetic_speed=cap_speed)
                if soft_status in (True, 'kinetic'):
                    continue
                if soft_status == 'deferred':
                    return 'deferred'
                return False
        return {
            'distance': proof_distance,
            'half_width': half_width,
            'leading': leading,
            'origin': (x, y, z),
            'yaw': float(travel_yaw),
            'direction': (-1 if signed_speed < 0.0 else 1),
        }

    def _navigation_obstacle(self, start, end, half_width):
        """Exact 0.8.2 coarse graph sweep through the #1513 collision API."""
        dx = float(end[0]) - float(start[0])
        dz = float(end[2]) - float(start[2])
        length = math.sqrt(dx * dx + dz * dz)
        if length < 0.1:
            return False
        lateral_x, lateral_z = dz / length, -dx / length
        for offset in (-float(half_width), 0.0, float(half_width)):
            ray_start = self._vector((
                float(start[0]) + lateral_x * offset,
                float(start[1]) + 0.9,
                float(start[2]) + lateral_z * offset))
            ray_end = self._vector((
                float(end[0]) + lateral_x * offset,
                float(end[1]) + 0.9,
                float(end[2]) + lateral_z * offset))
            if self._runtime.bigworld.wg_collideSegment(
                    self._avatar.spaceID, ray_start, ray_end, 128) is not None:
                return True
        return False

    def _water_depth(self, point):
        collide = getattr(self._runtime.bigworld, 'wg_collideWater', None)
        if not callable(collide):
            return -1.0
        try:
            value = collide(
                self._vector((point[0], point[1] + 20.0, point[2])),
                self._vector((point[0], point[1] - 5.0, point[2])), False)
        except Exception:
            return -1.0
        if value is None or value < 0.0:
            return -1.0
        return 20.0 - float(value)

    def _present_drowning_level(self, level, now):
        status_group = getattr(
            self._runtime.constants, 'VEHICLE_MISC_STATUS', None)
        levels = getattr(self._runtime.constants, 'DROWN_WARNING_LEVEL', None)
        callback = getattr(self._avatar, 'updateVehicleMiscStatus', None)
        if (status_group is None or levels is None or
                not callable(callback) or self._server is None):
            return False
        status = status_group.VEHICLE_DROWN_WARNING
        if level == 2:
            warning_level = levels.DANGER
            started = (self._drown_started if self._drown_started is not None
                       else self._server_clock())
            period = (float(started), 10.0)
        elif level == 1:
            warning_level = levels.CAUTION
            period = (0.0, 0.0)
        else:
            warning_level = levels.SAFE
            period = (0.0, 0.0)
        callback(
            self._server.vehicle_id, int(status), int(warning_level), period)
        return True

    def _tick_drowning(self, dt, now):
        """Copy the 0.8.2 0.3 s water probe and 10 s drowning state."""
        if dt <= 0.0 or self._server is None:
            return False
        record = self._records.get('player:%s' % self.client.player_id)
        if record is None:
            return False
        authoritative = record.get('state') or {}
        if (not bool(authoritative.get('alive', True)) or
                _number(authoritative.get('health', 1.0)) <= 0.0):
            return False
        entity = self._server_entity(record['engine_id'])
        if (entity is None or bool(getattr(entity, '_drowned', False)) or
                _number(getattr(entity, 'health', 0.0)) <= 0.0):
            return False
        self._drown_check += dt
        if self._drown_check < 0.3:
            return False
        elapsed = min(self._drown_check, 0.5)
        self._drown_check = 0.0
        depth = self._water_depth(self.local_pose()[0])
        if depth > 1.6:
            if self._drown_level != 2:
                self._drown_started = self._server_clock()
            self._drown_time += elapsed
            level = 2
        elif depth > 0.5:
            self._drown_time = 0.0
            self._drown_started = None
            level = 1
        else:
            self._drown_time = 0.0
            self._drown_started = None
            level = 0
        self._avatar._offh_drowning = level == 2
        entity._offh_drowning = level == 2
        if level != self._drown_level:
            self._drown_level = level
            self._present_drowning_level(level, now)
        if depth <= 1.6 or self._drown_time <= 10.0:
            return False
        display_health = max(0, int(getattr(entity, 'health', 0) or 0))
        drowning_reason = self._attack_reason('DROWNING', 5)
        critical = critical_damage.apply_drowning(entity)
        entity._drowned = True
        entity._offh_drowning = False
        self._avatar._offh_drowning = False
        state = dict(record.get('state') or {})
        state['health'] = 0
        state['alive'] = False
        state['display_health'] = display_health
        state['death_reason'] = drowning_reason
        if isinstance(critical, dict):
            state['critical'] = self._critical_state(critical)
            record['critical_state'] = state['critical']
            self._present_critical(
                record, critical.get('events'), record['engine_id'])
        record['state'] = state
        self._queue_local_damage_report(
            critical=critical, reason=drowning_reason,
            display_health=display_health, attribute_attacker=False)
        self._apply_health(record, state, 0, drowning_reason)
        return True

    def _has_los(self, observer, target):
        start = self._vector((observer[0], observer[1] + 2.5,
                              observer[2]))
        for height in (1.5, 2.2):
            end = self._vector((target[0], target[1] + height, target[2]))
            if self._runtime.bigworld.wg_collideSegment(
                    self._avatar.spaceID, start, end, 128) is None:
                return True
        return False

    def _cover_ground(self, x, z, hint_y):
        return self._ground_y(x, z, hint_y)

    def _cover_slope(self, point):
        maximum = 0.0
        for offset_x, offset_z in ((2.5, 0.0), (-2.5, 0.0),
                                   (0.0, 2.5), (0.0, -2.5)):
            height = self._cover_ground(
                point[0] + offset_x, point[2] + offset_z, point[1])
            if height is None:
                return 90.0
            maximum = max(maximum, math.degrees(math.atan2(
                abs(height - point[1]), 2.5)))
        return maximum

    def _sample_bot_cover(self, source, target, route_position,
                          ally_positions, segment_clear):
        """Port the 0.8.2 four-point cover fan through #1513 ray probes."""
        current = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        dx = current[0] - float(target_position[0])
        dz = current[2] - float(target_position[2])
        length = math.sqrt(dx * dx + dz * dz)
        if length < 2.0 or not callable(segment_clear):
            return ()
        away_x, away_z = dx / length, dz / length
        right_x, right_z = away_z, -away_x
        route = _xyz(route_position)
        route_dx, route_dz = route[0] - current[0], route[2] - current[2]
        route_length = math.sqrt(route_dx * route_dx + route_dz * route_dz)
        candidates = []
        for away, lateral in ((0.0, 0.0), (14.0, 0.0),
                              (10.0, 13.0), (10.0, -13.0)):
            x = current[0] + away_x * away + right_x * lateral
            z = current[2] + away_z * away + right_z * lateral
            ground = self._cover_ground(x, z, current[1])
            if ground is None:
                continue
            point = (x, ground, z)
            water_depth = self._water_depth(point)
            if water_depth > 1.0 or not segment_clear(current, point):
                continue
            occluded = not self._has_los(point, target_position)
            if not occluded:
                continue
            slope = self._cover_slope(point)
            if slope > 24.0:
                continue
            peek = None
            for side in (-1.0, 1.0):
                peek_x = point[0] + right_x * side * 6.5 - away_x * 2.0
                peek_z = point[2] + right_z * side * 6.5 - away_z * 2.0
                peek_y = self._cover_ground(peek_x, peek_z, point[1])
                if peek_y is None:
                    continue
                peek_point = (peek_x, peek_y, peek_z)
                if (self._water_depth(peek_point) <= 1.0 and
                        segment_clear(point, peek_point) and
                        self._has_los(peek_point, target_position)):
                    peek = peek_point
                    break
            move_dx, move_dz = point[0] - current[0], point[2] - current[2]
            move_length = math.sqrt(move_dx * move_dx + move_dz * move_dz)
            alignment = 0.5
            if move_length > 0.1 and route_length > 0.1:
                dot = ((move_dx / move_length) * (route_dx / route_length) +
                       (move_dz / move_length) * (route_dz / route_length))
                alignment = max(0.0, min(1.0, (dot + 1.0) * 0.5))
            nearby = sum(1 for ally in (ally_positions or ())
                         if 0.5 < _distance_2d(point, ally) < 13.0)
            candidate = {
                'id': '%s:%d:%d' % (
                    source.get('id'), int(round(point[0] / 4.0)),
                    int(round(point[2] / 4.0))),
                'position': point,
                'travel_distance': _distance_2d(point, current),
                'route_alignment': alignment,
                'enemy_occlusion': 1.0,
                'exposure': 0.12,
                'slope': slope,
                'water': max(0.0, min(1.0, water_depth)),
                'ally_congestion': max(0.0, min(1.0, nearby / 3.0)),
                'peek_feasible': peek is not None,
                'escape_feasible': True,
            }
            if peek is not None:
                candidate['peek_position'] = peek
            candidates.append(candidate)
        ranked = score_candidates(candidates)
        for candidate in ranked:
            for key in ('breakdown', 'reasons', 'rank', 'score'):
                candidate.pop(key, None)
        return tuple(ranked)

    def local_pose(self):
        # The copied 0.8.2 integrator owns this pose.  #1513's stock camera,
        # gun and collision consumers see the same value through the narrow
        # compatibility overlay installed at the native model boundary.
        return self._local_position, self._local_yaw

    def _echo_local_gun_angles(self, turret_yaw=None, gun_pitch=None):
        """Publish #1513's current native rotator angle as the server echo."""
        if self._server is None or self._binding is None:
            raise RuntimeError('player gun-angle echo is not attached')
        rotator = getattr(self._avatar, 'gunRotator', None)
        if rotator is None:
            raise RuntimeError('#1513 gun rotator is unavailable')
        if turret_yaw is None:
            try:
                turret_yaw = float(rotator.turretYaw)
            except (AttributeError, TypeError, ValueError):
                raise RuntimeError(
                    '#1513 turret yaw is unavailable for server echo')
        if gun_pitch is None:
            try:
                gun_pitch = float(rotator.gunPitch)
            except (AttributeError, TypeError, ValueError):
                raise RuntimeError(
                    '#1513 gun pitch is unavailable for server echo')
        hull_yaw = float(self._local_yaw)
        self._binding.update_vehicle_aim(
            self._server.vehicle_id, hull_yaw,
            hull_yaw + float(turret_yaw), float(gun_pitch))
        return True

    def _prepare_local_presentation(self, entity):
        """Publish one canonical pose before stock local-vehicle startup."""
        if self._local_matrix is not None:
            raise RuntimeError('player pose was prepared more than once')
        native_attribute = getattr(
            self._runtime.compatibility, 'native_vehicle_attribute', None)
        if not callable(native_attribute):
            raise RuntimeError('native Vehicle matrix boundary is unavailable')
        native_matrix = native_attribute(entity, 'matrix')
        matrix = self._runtime.math.Matrix()
        matrix.setRotateYPR((self._local_yaw, 0.0, 0.0))
        position = self._vector(self._local_position)
        matrix.translation = position
        zero_motion = self._vector((0.0, 0.0, 0.0))
        self._runtime.compatibility.set_vehicle_pose_overlay(
            entity, position, self._local_yaw, matrix,
            self._local_speed, self._local_turn_speed,
            zero_motion, zero_motion)
        native_stabilised = getattr(
            getattr(entity, 'filter', None), 'stabilisedMatrix',
            native_matrix)
        self._local_matrix = matrix
        self._local_native_matrix = native_matrix
        self._local_native_stabilised_matrix = native_stabilised
        self._local_camera_velocity = zero_motion
        self._local_physics = vehicle_physics.derive_params(
            entity.typeDescriptor)
        return True

    def _attach_local_presentation(self):
        """Bind the prepared pose to the exact #1513 model/providers."""
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None:
            raise RuntimeError('player Vehicle is unavailable for presentation')
        if self._local_matrix is None:
            # Engine-free contract tests call this boundary directly. The
            # production path prepares it from vehicle_onEnterWorld before
            # AvatarInputHandler.start() can capture any native provider.
            self._prepare_local_presentation(entity)
        model = getattr(entity, 'model', None)
        if model is None:
            raise RuntimeError('player compound model is unavailable')
        model.matrix = self._local_matrix
        self._runtime.compatibility.bind_vehicle_pose_sources(
            self._avatar, entity)
        self._local_model = model
        return True

    def _update_local_presentation(self, entity, dt=0.0):
        if self._local_matrix is None or self._local_model is None:
            raise RuntimeError('player presentation is not attached')
        previous_position = _xyz(self._local_matrix.translation)
        position = self._vector(self._local_position)
        dt = max(0.0, float(dt))
        previous_velocity = _xyz(self._local_camera_velocity)
        if dt > 0.0:
            current_position = _xyz(position)
            velocity_tuple = tuple(
                (current_position[index] - previous_position[index]) / dt
                for index in range(3))
            acceleration_tuple = tuple(
                (velocity_tuple[index] - previous_velocity[index]) / dt
                for index in range(3))
        else:
            velocity_tuple = previous_velocity
            acceleration_tuple = (0.0, 0.0, 0.0)
        velocity = self._vector(velocity_tuple)
        acceleration = self._vector(acceleration_tuple)
        self._local_matrix.setRotateYPR((
            self._local_yaw, self._local_pitch, self._local_roll))
        self._local_matrix.translation = position
        # Exact #1513's CompoundAppearance.__linkCompound rebinds
        # ``compoundModel.matrix`` from Vehicle.matrix after every model
        # refresh.  Mutate the persistent provider only; even reading and
        # comparing a native PyCompoundModel provider every render frame can
        # create a fresh Python wrapper and spuriously relink the hierarchy.
        self._runtime.compatibility.set_vehicle_pose_overlay(
            entity, position, self._local_yaw, self._local_matrix,
            self._local_speed, self._local_turn_speed,
            velocity, acceleration)
        self._local_camera_velocity = velocity
        self._update_local_tracks(entity)
        return position

    def _local_engine_mode_value(self, alive):
        """Return the exact #1513 ``(power, movementFlags)`` engine mode."""
        forward = _number(getattr(self._sender, 'forward', 0.0))
        turn = _number(getattr(self._sender, 'turn', 0.0))
        flags = 0
        if forward > 0.0:
            flags |= 1
        elif forward < 0.0:
            flags |= 2
        if turn < 0.0:
            flags |= 4
        elif turn > 0.0:
            flags |= 8
        if not alive:
            return (ENGINE_MODE_OFF, 0)
        if flags or abs(self._local_speed) > 0.05:
            return (ENGINE_MODE_RUNNING, flags)
        return (ENGINE_MODE_IDLE, flags)

    def _update_local_tracks(self, entity):
        """Feed the native track, wheel, spline and trace animation.

        Retail drives this from the cell-owned
        ``Avatar.ownVehicleAuxPhysicsData``, which an offline client never
        receives.  ``updateTracksScroll`` reaches the same
        ``PyTrackScroll.setExternal`` boundary, but its native tick pins both
        belts to zero while ``engineMode[0]`` is at most 1.
        """
        appearance = getattr(entity, 'appearance', None)
        update_scroll = getattr(appearance, 'updateTracksScroll', None)
        change_mode = getattr(appearance, 'changeEngineMode', None)
        if not callable(update_scroll) or not callable(change_mode):
            raise RuntimeError('#1513 track animation boundary is unavailable')
        is_alive = getattr(entity, 'isAlive', None)
        alive = bool(is_alive() if callable(is_alive) else is_alive)
        mode = self._local_engine_mode_value(alive)
        if mode != self._local_engine_mode:
            entity.engineMode = mode
            change_mode(mode, True)
            self._local_engine_mode = mode
        if not alive:
            return False
        params = self._local_physics
        if params is None:
            return False
        left, right = vehicle_physics.track_scroll(
            params, self._local_speed, self._local_turn_speed)
        minimum, maximum = TRACK_SCROLL_LIMITS
        update_scroll(
            max(minimum, min(maximum, left)),
            max(minimum, min(maximum, right)))
        return True

    def _detach_local_presentation(self):
        if self._server is None:
            return False
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None:
            return False
        if self._local_model is not None and self._local_native_matrix is not None:
            self._local_model.matrix = self._local_native_matrix
        clear = getattr(
            self._runtime.compatibility, 'clear_vehicle_pose_overlay', None)
        if not callable(clear) or not clear(entity):
            raise RuntimeError('player pose overlay did not clear')
        self._runtime.compatibility.restore_vehicle_pose_sources(
            self._avatar, entity, self._local_native_matrix,
            self._local_native_stabilised_matrix)
        self._local_matrix = None
        self._local_model = None
        self._local_native_matrix = None
        self._local_native_stabilised_matrix = None
        self._local_camera_velocity = None
        self._local_engine_mode = None
        return True

    def _on_client_ready(self):
        self._client_ready_received = True
        if self.state == 'running':
            self._sender.send_current()
            if self._ammo_callback_token is None:
                self._ammo_tick()

    def _default_equipments(self):
        """Resolve the three copied 0.8.2 consumables from #1513 data."""
        try:
            values = self._runtime.vehicles.g_cache.equipments().values()
        except Exception:
            return []
        by_name = {}
        for descriptor in values:
            name = str(getattr(descriptor, 'name', '') or '').lower()
            if name:
                by_name[name] = descriptor
        result = []
        for name, kind in (
                ('smallrepairkit', 'repairkit'),
                ('smallmedkit', 'medkit'),
                ('handextinguishers', 'extinguisher')):
            descriptor = by_name.get(name)
            if descriptor is None:
                continue
            try:
                equipment_id = int(descriptor.id[1])
                compact_descr = int(descriptor.compactDescr)
            except (TypeError, ValueError, IndexError, AttributeError):
                continue
            result.append({
                'id': equipment_id, 'compact_descr': compact_descr,
                'name': name, 'kind': kind, 'used': False})
        return result

    def _present_equipments(self):
        if self._equipment_state is None:
            self._equipment_state = self._default_equipments()
        for equipment in self._equipment_state:
            self._avatar.updateVehicleAmmo(
                self._server.vehicle_id, equipment['compact_descr'],
                0 if equipment['used'] else 1, 0, 0)

    @staticmethod
    def _critical_name_from_extra_index(descriptor, extra_index):
        extras = getattr(descriptor, 'extras', None)
        if hasattr(extras, 'items'):
            iterator = extras.items()
        else:
            try:
                iterator = enumerate(extras or ())
            except Exception:
                iterator = ()
        for index, extra in iterator:
            try:
                matches = int(index) == int(extra_index)
            except (TypeError, ValueError):
                matches = False
            if not matches:
                continue
            name = str(getattr(extra, 'name', '') or '')
            return name[:-6] if name.endswith('Health') else name
        return None

    def _activate_equipment(self, activation_code):
        if self._equipment_state is None:
            self._equipment_state = self._default_equipments()
        try:
            activation_code = int(activation_code)
        except (TypeError, ValueError):
            return False
        equipment_id = activation_code & 65535
        extra_index = max(0, activation_code >> 16)
        equipment = next((value for value in self._equipment_state
                          if value['id'] == equipment_id), None)
        if equipment is None or equipment['used']:
            return False
        record = self._records.get('player:%s' % self.client.player_id)
        if record is None or self._server is None:
            return False
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return False
        if not self._record_alive(record, entity):
            return False
        selected = self._critical_name_from_extra_index(
            entity.typeDescriptor, extra_index)
        if equipment['kind'] == 'extinguisher':
            payload = critical_damage.use_extinguisher(entity)
        elif equipment['kind'] == 'repairkit':
            payload = critical_damage.repair_device(
                entity, selected, 'large' in equipment['name'])
        elif equipment['kind'] == 'medkit':
            payload = critical_damage.restore_crew(
                entity, selected, 'large' in equipment['name'])
        else:
            return False
        if payload is None:
            return False
        equipment['used'] = True
        canonical = self._critical_state(payload)
        record['critical_state'] = canonical
        state = dict(record.get('state') or {})
        state['critical'] = canonical
        record['state'] = state
        self._present_critical(
            record, payload.get('events'), record['engine_id'])
        self._queue_local_damage_report(critical=payload)
        self._present_equipments()
        return True

    def _publish_reload_event(self, time_left, base_time, force=False):
        """Send one #1513 reload edge and let the stock HUD interpolate it."""
        if self._server is None:
            return False
        event = (max(0.0, float(time_left)),
                 max(0.0, float(base_time)))
        if not force and self._reload_event == event:
            return False
        self._avatar.updateVehicleGunReloadTime(
            self._server.vehicle_id, event[0], event[1])
        self._reload_event = event
        return True

    def _publish_ammo_state(self, state, force=False):
        """Publish shell counts only when the copied gun state changes."""
        signature = (
            int(state.shot_index), tuple(int(value) for value in state.ammo),
            int(state.clip))
        if not force and signature == self._ammo_signature:
            return False
        current_shell = None
        for index, shot in enumerate(state.shots):
            shell = _field(shot, 'shell', {})
            compact = _field(shell, 'compactDescr', 0)
            quantity = state.ammo[index]
            quantity_in_clip = state.clip if index == state.shot_index else 0
            self._avatar.updateVehicleAmmo(
                self._server.vehicle_id, int(compact),
                max(0, min(quantity, 65535)),
                max(0, min(quantity_in_clip, 255)), 0)
            if index == state.shot_index:
                current_shell = compact
        if current_shell is not None:
            self._avatar.updateVehicleSetting(
                self._server.vehicle_id,
                self._runtime.constants.VEHICLE_SETTING.CURRENT_SHELLS,
                current_shell)
        self._present_equipments()
        self._ammo_signature = signature
        return True

    def _publish_targeting_info(self, entity=None, state=None):
        """Initialise #1513 gun-rotator parameters without ticking ammo."""
        if entity is None:
            if self._server is None:
                raise RuntimeError('player Vehicle server identity is unavailable')
            entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            raise RuntimeError('player Vehicle descriptor is unavailable')
        descriptor = entity.typeDescriptor
        turret = descriptor.turret
        gun = descriptor.gun
        if state is None:
            state = self._gun_state
        if state is None:
            raise RuntimeError('player gun state is unavailable')
        chassis_factors = _field(
            _field(descriptor, 'chassis', {}),
            'shotDispersionFactors', (0.0, 0.0))
        gun_factors = _field(gun, 'shotDispersionFactors', {}) or {}
        try:
            movement_factor = float(chassis_factors[0])
            rotation_factor = float(chassis_factors[1])
        except (TypeError, ValueError, IndexError):
            movement_factor = 0.0
            rotation_factor = 0.0
        turret_factor = _number(
            _field(gun_factors, 'turretRotation', 0.0), 0.0)
        base_dispersion = _number(
            _field(gun, 'shotDispersionAngle', 0.0), 0.0)
        if base_dispersion <= 0.0:
            raise RuntimeError('#1513 gun descriptor has no dispersion angle')
        shot_multiplier = (
            state.base_dispersion / base_dispersion *
            critical_damage.stat_factor(entity, 'dispersion'))
        aiming_time = (
            state.aim_time *
            critical_damage.stat_factor(entity, 'aim_time'))
        turret_speed = (
            _number(turret.rotationSpeed) *
            critical_damage.stat_factor(entity, 'turret_speed'))
        targeting_signature = (
            turret_speed,
            _number(gun.rotationSpeed), shot_multiplier,
            turret_factor, movement_factor, rotation_factor, aiming_time)
        if targeting_signature == self._targeting_signature:
            return False
        turret_yaw, gun_pitch = entity.getAimParams()
        self._avatar.updateTargetingInfo(
            turret_yaw, gun_pitch, targeting_signature[0],
            targeting_signature[1], targeting_signature[2],
            targeting_signature[3], targeting_signature[4],
            targeting_signature[5], targeting_signature[6])
        self._targeting_signature = targeting_signature
        return True

    def _ammo_tick(self):
        if self.state != 'running' or self._server is None:
            return
        try:
            entity = self._server_entity(self._server.vehicle_id)
            if entity is None or entity.typeDescriptor is None:
                raise RuntimeError('player Vehicle descriptor is unavailable')
            descriptor = entity.typeDescriptor
            state = self._gun_state
            if state is None:
                state = gun_mechanics.GunState(descriptor)
                self._gun_state = state
            now = self._clock()
            if self._gun_last_tick is None:
                self._gun_last_tick = now
            dt = max(0.0, now - self._gun_last_tick)
            self._gun_last_tick = now
            previous_reload = state.reload_time
            state.tick(
                dt, self._battle_live, self._local_speed,
                self._local_turn_speed, 0.0, descriptor,
                dispersion_factor=critical_damage.stat_factor(
                    entity, 'dispersion'),
                aim_time_factor=critical_damage.stat_factor(
                    entity, 'aim_time'))
            self._publish_ammo_state(state)
            if not self._battle_live:
                self._publish_reload_event(
                    0.0, state.reload_duration)
            elif self._reload_event is None:
                self._publish_reload_event(
                    state.reload_time, state.reload_duration)
            elif previous_reload > 0.0 and state.reload_time <= 0.0:
                self._publish_reload_event(
                    0.0, state.reload_duration, force=True)
            # #1513 updateTargetingInfo is a server-parameter update, not a
            # per-frame reticle publisher.  Publish only when descriptor,
            # crew or module parameters change; the stock rotator owns live
            # mouse aim and convergence after the native BATTLE transition.
            self._publish_targeting_info(entity, state)
            self._sync_local_server_marker()
        except Exception as error:
            self._fail(error)
            return
        self._schedule(AMMO_SECONDS, self._ammo_tick, ammo=True)

    def change_vehicle_setting(self, code, value):
        activate_equipment = getattr(
            self._runtime.constants.VEHICLE_SETTING,
            'ACTIVATE_EQUIPMENT', None)
        if code == activate_equipment:
            return self._activate_equipment(value)
        current_shells = getattr(
            self._runtime.constants.VEHICLE_SETTING, 'CURRENT_SHELLS', None)
        if code != current_shells or self._gun_state is None:
            return False
        for index, shot in enumerate(self._gun_state.shots):
            shell = _field(shot, 'shell', {})
            if int(_field(shell, 'compactDescr', 0)) == int(value):
                changed = self._gun_state.sync_shell_index(index)
                if changed:
                    self._publish_ammo_state(self._gun_state, force=True)
                    self._publish_reload_event(
                        self._gun_state.reload_time,
                        self._gun_state.reload_duration, force=True)
                return changed
        return False

    def on_snapshot(self, message):
        if self.state in ('failed', 'stopped'):
            return
        try:
            self._last_snapshot = dict(message or {})
            self._observe_projectile_message(self._last_snapshot)
            self._reconcile_projectile_snapshot(self._last_snapshot)
            if 'rules' in self._last_snapshot:
                self._apply_rules(self._last_snapshot.get('rules'))
            if self._last_snapshot.get('battle_result') is not None:
                self._apply_battle_result(
                    self._last_snapshot['battle_result'])
            if 'destructibles' in self._last_snapshot:
                self._apply_destructible_state(
                    self._last_snapshot.get('destructibles'))
            if self._bots is not None:
                if 'bot_authority_id' in self._last_snapshot:
                    self._reconcile_bot_authority(
                        self._last_snapshot.get('bot_authority_id'))
                self._bots.apply_snapshot(self._last_snapshot)
            if self._sync is not None:
                self._sync.snapshot(message)
        except Exception as error:
            self._fail(error)

    def on_roster(self, message):
        """Apply authority changes that can arrive before live snapshots.

        The server does not tick snapshots while #1513 clients are behind the
        native entity-load barrier, so a loading-phase roster is the only
        durable authority update channel.  A round that loses its server
        authority is ended by the server; this client never takes the bot
        simulation over.
        """
        if self.state in ('failed', 'stopped'):
            return False
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id')
        if (self._start_message is None or
                round_id != self._start_message.get('round_id')):
            return False
        if 'bot_authority_id' not in message:
            return False
        self._observe_projectile_message(message)
        player_id = message.get('bot_authority_id')
        self._start_message['bot_authority_id'] = player_id
        if 'authority_status' in message:
            self._start_message['authority_status'] = message.get(
                'authority_status')
        if 'authority_fallback_reason' in message:
            self._start_message['authority_fallback_reason'] = message.get(
                'authority_fallback_reason')
        if self._bots is None:
            return True
        return self._reconcile_bot_authority(player_id)

    def on_bot_observation(self, message):
        """Consume one server-admitted observation without relaying it."""
        if self.state != 'running':
            return False
        message = message if isinstance(message, dict) else {}
        if (self._start_message is None or
                message.get('round_id') !=
                self._start_message.get('round_id')):
            return False
        try:
            return self._observe_local_vehicle(message, self._clock())
        except Exception as error:
            self._fail(error)
            return False

    def _reconcile_bot_authority(self, player_id):
        """Recover authority changes even if the one-shot event was missed."""
        if (self._bots is None or
                getattr(self._bots, 'authority_id', None) == player_id):
            return False
        # Arc jobs and completed launch receipts are native-world proofs made
        # by one authority.  They must never survive an ownership handoff,
        # regardless of whether this client gains or loses simulation duty.
        if self._artillery is not None:
            self._artillery.reset()
        start = dict(self._start_message or {})
        start['bot_authority_id'] = player_id
        snapshot = self._last_snapshot or {}
        manifest = snapshot.get(
            'bot_manifest', start.get('bot_manifest', [])) or []
        live_by_id = {}
        for raw in snapshot.get('bots') or ():
            if isinstance(raw, dict) and raw.get('id') is not None:
                live_by_id[int(raw['id'])] = raw
        # The server manifest intentionally owns identity/profile/route while
        # snapshot.bots owns the canonical live pose and fire sequence.  Merge
        # both before promoting a new authority; using the manifest alone
        # respawned every bot at its formation slot during failover.
        takeover = []
        for raw in manifest:
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            merged = dict(raw)
            merged.update(live_by_id.get(int(raw['id']), {}))
            takeover.append(merged)
        if not takeover:
            takeover = [dict(raw) for raw in snapshot.get('bots') or ()
                        if isinstance(raw, dict)]
        start['bot_manifest'] = takeover
        if snapshot.get('battle_result') is not None:
            start['battle_result'] = snapshot.get('battle_result')
        for outgoing in self._bots.battle_start(start):
            self._send_bot_message(outgoing)
        if self._bots.is_authority():
            for state in snapshot.get('bots') or ():
                try:
                    self._bot_fire_seen[int(state['id'])] = max(
                        0, int(state.get('fire_seq', 0)))
                except (KeyError, TypeError, ValueError):
                    continue
        return True

    def on_events(self, message):
        if self.state in ('failed', 'stopped'):
            return False
        try:
            self._observe_projectile_message(message or {})
            for raw_event in (message or {}).get('events') or ():
                if not isinstance(raw_event, dict):
                    raise RuntimeError('ordered LAN event is malformed')
                event = dict(raw_event)
                event_id = event.get('event_id')
                if not event_id:
                    raise RuntimeError('ordered LAN event has no event_id')
                event_id = str(event_id)
                if event_id in self._accepted_event_ids:
                    continue
                self._prepare_ordered_event(event)
                self._accepted_event_ids.add(event_id)
                self._event_journal.append(event)
            self._drain_event_journal()
            return True
        except Exception as error:
            self._fail(error)
            return False

    @staticmethod
    def _event_entity_key(event, role):
        if role == 'attacker':
            if event.get('attacker_bot') is not None:
                return 'bot:%s' % event.get('attacker_bot')
            if event.get('attacker') is not None:
                return 'player:%s' % event.get('attacker')
            return None
        if event.get('target_bot') is not None:
            return 'bot:%s' % event.get('target_bot')
        if event.get('target') is not None:
            return 'player:%s' % event.get('target')
        return None

    def _known_event_state(self, key):
        record = self._records.get(key)
        if record is not None:
            return record, record.get('state') or {}
        pending = self._pending_bot_creates.get(key)
        if pending is not None:
            return pending, pending.get('state') or {}
        raise RuntimeError('ordered LAN event references unknown entity %s' %
                           key)

    def _merge_shot_event_state(self, event):
        key = self._event_entity_key(event, 'attacker')
        if key is None:
            raise RuntimeError('ordered shot event has no attacker')
        holder, state = self._known_event_state(key)
        deadline = self._clock() + spotting.SHOT_CAMOUFLAGE_SECONDS
        if holder is self._records.get(key):
            holder['shot_penalty_until'] = deadline
        else:
            state = dict(state)
            state['shot_penalty_until'] = deadline
            holder['state'] = state

    def _missing_projectile_attacker_allowed(self, event):
        """Allow a fired shell to outlive a disconnected shooter entity."""
        if (event.get('source') != 'shot' or
                event.get('kind') not in (
                    'hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit')):
            return False
        projectile_id = event.get('projectile_id')
        return (projectile_id is not None and
                str(projectile_id) in self._projectile_lineage)

    def _combat_event_state(self, event, state, target_key):
        if 'health' not in event:
            raise RuntimeError('ordered combat event has no health')
        if 'death_reason' not in event:
            raise RuntimeError('ordered combat event has no death_reason')
        state = dict(state or {})
        health = max(0, int(event.get('health', 0)))
        state['health'] = health
        state['alive'] = health > 0 and not bool(event.get('dead', False))
        # Canonical shot/fire/ram events normally omit ``display_health``.
        # Do not retain the preceding snapshot's value: that would make the
        # next snapshot look like another health edge and replay it without
        # the event's attacker.  Exceptional preserved-hull deaths carry an
        # explicit display value and continue to win here.
        state['display_health'] = max(
            0, int(event.get('display_health', health)))
        try:
            death_reason = int(event['death_reason'])
        except (TypeError, ValueError):
            raise RuntimeError('ordered combat event has invalid death_reason')
        if death_reason < 0:
            raise RuntimeError('ordered combat event has invalid death_reason')
        if state['alive'] and death_reason != 0:
            raise RuntimeError(
                'nonfatal combat event has nonzero death_reason')
        state['death_reason'] = death_reason
        attacker_key = self._event_entity_key(event, 'attacker')
        if attacker_key is not None:
            attacker_kind, attacker_id = attacker_key.split(':', 1)
            state['death_attacker_kind'] = attacker_kind
            state['death_attacker_id'] = int(attacker_id)
        critical = event.get('critical')
        if isinstance(critical, dict) and target_key.startswith('bot:'):
            state['critical'] = self._critical_state(critical)
        for name in ('critical_revision', 'critical_base_revision',
                     'critical_ack_seq'):
            if name in event:
                state[name] = event[name]
        for name in ('combat_revision', 'combat_base_revision',
                     'combat_ack_seq'):
            if name in event:
                state[name] = event[name]
        return state

    def _merge_combat_event_state(self, event):
        target_key = self._event_entity_key(event, 'target')
        if target_key is None:
            raise RuntimeError('ordered combat event has no target')
        holder, state = self._known_event_state(target_key)
        attacker_key = self._event_entity_key(event, 'attacker')
        if attacker_key is not None:
            try:
                self._known_event_state(attacker_key)
            except RuntimeError:
                if not self._missing_projectile_attacker_allowed(event):
                    raise
        holder['state'] = self._combat_event_state(
            event, state, target_key)

    def _prepare_ordered_event(self, event):
        kind = event.get('kind')
        if kind in _SHOT_EVENT_KINDS:
            self._merge_shot_event_state(event)
            normalized = self._projectile_wire_meta(event)
            if normalized is not None:
                self._projectile_lineage.add(normalized['projectile_id'])
        elif kind in _COMBAT_EVENT_KINDS:
            self._validate_combat_event_contract(event)
            self._merge_combat_event_state(event)
        elif kind not in (
                'authority', 'bot_manifest', 'vehicle_statistics',
                'destructible', 'projectile_impact', 'battle_result'):
            raise RuntimeError(
                'ordered LAN event kind is unsupported: %s' % kind)

    @staticmethod
    def _record_is_event_ready(record):
        if record is None:
            return False
        if not record.get('ready', True):
            return False
        if (record.get('presentation') and
                not record.get('arena_added', False)):
            return False
        return True

    def _event_is_ready(self, event):
        kind = event.get('kind')
        if kind in _SHOT_EVENT_KINDS:
            key = self._event_entity_key(event, 'attacker')
            record = self._records.get(key)
            if record is None and key not in self._pending_bot_creates:
                raise RuntimeError(
                    'ordered LAN event lost entity %s before apply' % key)
            return self._record_is_event_ready(record)
        if kind in _COMBAT_EVENT_KINDS:
            target_key = self._event_entity_key(event, 'target')
            target_record = self._records.get(target_key)
            if (target_record is None and
                    target_key not in self._pending_bot_creates):
                raise RuntimeError(
                    'ordered LAN event lost entity %s before apply' %
                    target_key)
            if not self._record_is_event_ready(target_record):
                return False
            attacker_key = self._event_entity_key(event, 'attacker')
            if attacker_key is None:
                return True
            attacker_record = self._records.get(attacker_key)
            if (attacker_record is None and
                    attacker_key not in self._pending_bot_creates):
                if self._missing_projectile_attacker_allowed(event):
                    return True
                raise RuntimeError(
                    'ordered LAN event lost entity %s before apply' %
                    attacker_key)
            return self._record_is_event_ready(attacker_record)
        return True

    def _apply_ordered_event(self, event):
        kind = event.get('kind')
        if kind == 'authority':
            self._set_projectile_epoch(
                event.get('authority_epoch'), self._clock())
            if self._bots is not None:
                changed = self._reconcile_bot_authority(
                    event.get('player_id'))
                if changed and self._last_snapshot is not None:
                    self._bots.apply_snapshot(self._last_snapshot)
        elif kind in _SHOT_EVENT_KINDS:
            self._show_shot(event, update_state=False)
            self._accept_projectile_event(event)
        elif kind in _COMBAT_EVENT_KINDS:
            self._apply_combat_event(event, update_state=False)
        elif kind == 'vehicle_statistics':
            self._apply_vehicle_statistics_event(event)
        elif kind == 'destructible':
            self._apply_destructible_event(event)
        elif kind == 'projectile_impact':
            self._apply_projectile_terminal_event(event)
        elif kind == 'battle_result':
            self._apply_battle_result(event)
        elif kind == 'bot_manifest':
            # Durable bot identities arrive in the same tick's snapshot.  The
            # event is an explicit ordering marker and has no native effect.
            pass
        else:
            raise RuntimeError(
                'ordered LAN event kind is unsupported: %s' % kind)

    def _drain_event_journal(self):
        while self._event_journal:
            event = self._event_journal[0]
            if not self._event_is_ready(event):
                return False
            self._apply_ordered_event(event)
            event_id = str(event['event_id'])
            self._applied_event_ids.add(event_id)
            self._event_journal.pop(0)
        return True

    def _pending_combat_for_record(self, record):
        for event in self._event_journal:
            if (event.get('kind') in _COMBAT_EVENT_KINDS and
                    self._records.get(
                        self._event_entity_key(event, 'target')) is record):
                return True
        return False

    def _pending_event_references(self, key):
        for event in self._event_journal:
            if (self._event_entity_key(event, 'target') == key or
                    self._event_entity_key(event, 'attacker') == key):
                return True
        return False

    def _report_destructible(self, event):
        context = self._projectile_destructible_context
        if context is not None:
            projectile_id = context
            meta = self._projectile_meta.get(projectile_id)
            if meta is None or not isinstance(event, dict) or \
                    event.get('is_shot') is not True:
                return False
            frozen = dict(event)
            key = (
                frozen.get('destructible_kind'), frozen.get('chunk_id'),
                frozen.get('item_index'), frozen.get('mat_kind'))
            pending = meta.setdefault('destructibles_pending', [])
            if key not in set(
                    (value.get('destructible_kind'), value.get('chunk_id'),
                     value.get('item_index'), value.get('mat_kind'))
                    for value in pending):
                if len(pending) >= 64:
                    raise RuntimeError(
                        'projectile destructible receipt limit exceeded')
                pending.append(frozen)
            return True
        if self.client is None:
            raise RuntimeError('LAN client is unavailable for destructible')
        sender = getattr(self.client, 'send_destructible', None)
        if not callable(sender):
            raise RuntimeError(
                'LAN client has no destructible report boundary')
        return bool(sender(event))

    def _apply_destructible_state(self, events):
        if not isinstance(events, (list, tuple)):
            raise RuntimeError('canonical destructible state is malformed')
        changed = False
        for event in events:
            if not isinstance(event, dict):
                raise RuntimeError(
                    'canonical destructible event is malformed')
            changed = self._apply_destructible_event(event) or changed
        return changed

    def _apply_destructible_event(self, event):
        if self._destructibles is None:
            raise RuntimeError('#1513 destructible runtime is unavailable')
        from gui.mods.offline_lan_0922 import destructibles_authority
        kind = str(event.get('destructible_kind', ''))
        if kind not in ('tree', 'column', 'fragile', 'module'):
            raise RuntimeError('canonical destructible kind is invalid')
        try:
            chunk_id = int(event['chunk_id'])
            item_index = int(event['item_index'])
            x = float(event['x'])
            y = float(event['y'])
            z = float(event['z'])
            fall_yaw = float(event.get('fall_yaw', 0.0))
            speed = float(event.get('speed', 0.0))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError('canonical destructible payload is invalid')
        for value in (x, y, z, fall_yaw, speed):
            if math.isnan(value) or math.isinf(value):
                raise RuntimeError(
                    'canonical destructible payload is non-finite')
        mat_kind = event.get('mat_kind')
        if mat_kind is not None:
            try:
                mat_kind = int(mat_kind)
            except (TypeError, ValueError, OverflowError):
                raise RuntimeError(
                    'canonical destructible material is invalid')
        if kind == 'module' and mat_kind is None:
            raise RuntimeError(
                'canonical destructible module has no material')
        is_shot = event.get('is_shot')
        if not isinstance(is_shot, bool):
            raise RuntimeError(
                'canonical destructible shot flag is invalid')
        if destructibles_authority.is_destroyed(
                chunk_id, item_index, mat_kind):
            return False
        position = self._vector((x, y, z))
        space_id = self._avatar.spaceID
        if kind == 'tree':
            applied = destructibles_authority.destroy_tree(
                space_id, chunk_id, item_index, fall_yaw, speed, position)
        elif kind == 'column':
            applied = destructibles_authority.destroy_column(
                space_id, chunk_id, item_index, fall_yaw, speed, position)
        elif kind == 'fragile':
            applied = destructibles_authority.destroy_fragile(
                space_id, chunk_id, item_index, position, is_shot)
        else:
            applied = destructibles_authority.destroy_module(
                space_id, chunk_id, item_index, mat_kind, position, is_shot)
        if (not applied and not destructibles_authority.is_destroyed(
                chunk_id, item_index, mat_kind)):
            raise RuntimeError(
                '#1513 failed to apply canonical destructible event')
        note_destroyed = getattr(
            self._destructibles, 'note_destroyed', None)
        if callable(note_destroyed):
            note_destroyed(
                kind, chunk_id, item_index, mat_kind, self._clock())
        return True

    def _apply_vehicle_statistics_event(self, event):
        actor_kind = event.get('actor_kind')
        try:
            actor_id = int(event.get('actor_id'))
        except (TypeError, ValueError):
            return False
        record = self._records.get('%s:%s' % (actor_kind, actor_id))
        if record is None:
            return False
        state = dict(record.get('state') or {})
        state['frags'] = int(event.get('frags', state.get('frags', 0)))
        state['team_killer'] = bool(event.get(
            'team_killer', state.get('team_killer', False)))
        record['state'] = state
        return self._apply_vehicle_statistics(record, state)

    def _record_position(self, record):
        if record.get('local'):
            return tuple(self._local_position)
        state = record.get('state') or {}
        if all(name in state for name in ('x', 'y', 'z')):
            return (_number(state['x']), _number(state['y']),
                    _number(state['z']))
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            raise RuntimeError('combat presentation entity is unavailable')
        return _xyz(entity.position)

    def _event_shell(self, attacker_record, event):
        entity = self._server_entity(attacker_record['engine_id'])
        if entity is None or entity.typeDescriptor is None:
            raise RuntimeError('combat attacker descriptor is unavailable')
        gun = _field(entity.typeDescriptor, 'gun', {})
        shots = tuple(_field(gun, 'shots', ()) or ())
        if not shots:
            raise RuntimeError('combat attacker has no shell descriptors')
        index = max(0, min(
            int(event.get('shell_index', 0) or 0), len(shots) - 1))
        shot = shots[index]
        shell = _field(shot, 'shell', None)
        if shell is None:
            raise RuntimeError('combat attacker shell is unavailable')
        return shot, shell

    def _present_combat_hit(self, event, target_record, attacker_record,
                            attacker_id):
        """Port the mature 0.8.2 hit feedback through exact #1513 APIs."""
        if (self._combat_event_source(event) != 'shot' or
                event.get('kind') not in (
                    'hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit')):
            return False
        if not event.get('world_pose'):
            raise RuntimeError('shot hit event has no world impact pose')
        if not (target_record.get('local') or attacker_record.get('local')):
            return False
        shot, shell = self._event_shell(attacker_record, event)
        attacker_position = self._record_position(attacker_record)
        target_position = self._record_position(target_record)
        direction = self._vector((
            target_position[0] - attacker_position[0],
            target_position[1] - attacker_position[1],
            target_position[2] - attacker_position[2]))
        if direction.length <= 0.001:
            raise RuntimeError('combat impact direction is degenerate')
        direction.normalise()
        damage = max(0, int(event.get('damage', 0) or 0))
        shot_result = max(0, min(int(event.get('shot_result', 2)), 2))
        if target_record.get('local'):
            # Preserve the empirically-correct 0.8.2 UI convention: the hit
            # indicator points from the player back toward the attacker.
            hit_yaw = math.atan2(
                -(attacker_position[0] - target_position[0]),
                -(attacker_position[2] - target_position[2]))
            self._avatar.showOwnVehicleHitDirection(
                hit_yaw, int(attacker_id or 0), damage, 0,
                damage <= 0, combat_rules.is_he(shot),
                int(target_record['engine_id']))

        effects_index = _field(shell, 'effectsIndex', None)
        if effects_index is None:
            raise RuntimeError('combat shell effects index is unavailable')
        effects_descr = self._runtime.vehicles.g_cache.shotEffects[
            effects_index]
        effect_group = ('armorRicochet', 'armorResisted', 'armorHit')[
            shot_result]
        stages, effects, unused = effects_descr[effect_group]
        hit_position = self._vector((
            _number(event.get('x')), _number(event.get('y')),
            _number(event.get('z'))))
        terrain_effects = getattr(self._avatar, 'terrainEffects', None)
        add_effect = getattr(terrain_effects, 'addNew', None)
        if not callable(add_effect):
            raise RuntimeError('#1513 terrain hit-effects boundary is unavailable')
        add_effect(
            hit_position, effects, stages, None, dir=direction,
            start=hit_position - direction.scale(0.4),
            end=hit_position + direction.scale(0.4),
            showShockWave=bool(target_record.get('local')),
            showFlashBang=bool(target_record.get('local')))
        return True

    @staticmethod
    def _combat_record_team(record):
        state = record.get('state') or {}
        if 'team' not in state:
            raise RuntimeError('combat feedback record has no team')
        try:
            team = int(state['team'])
        except (TypeError, ValueError):
            raise RuntimeError('combat feedback record has invalid team')
        if team <= 0:
            raise RuntimeError('combat feedback record has invalid team')
        return team

    @staticmethod
    def _combat_event_source(event):
        if 'source' not in event:
            raise RuntimeError('ordered combat event has no source')
        source = event['source']
        if source not in (
                'shot', 'fire', 'ram', 'client_simulation',
                'player_left'):
            raise RuntimeError(
                'ordered combat event has invalid source: %s' % source)
        return source

    def _combat_attack_reason(self, event):
        source = self._combat_event_source(event)
        if source == 'player_left':
            if ('attack_reason' not in event or
                    event['attack_reason'] is not None):
                raise RuntimeError(
                    'player_left event must have null attack_reason')
            if ('death_reason' not in event or
                    event['death_reason'] != 0):
                raise RuntimeError(
                    'player_left event must have zero death_reason')
            if ('attacker' in event or 'attacker_bot' in event):
                raise RuntimeError(
                    'player_left event must not have an attacker')
            return None
        if 'attack_reason' not in event:
            raise RuntimeError('ordered combat event has no attack_reason')
        try:
            reason_id = int(event['attack_reason'])
        except (TypeError, ValueError):
            raise RuntimeError('ordered combat event has invalid attack_reason')
        if reason_id < 0:
            raise RuntimeError('ordered combat event has invalid attack_reason')
        expected = {
            'shot': self._attack_reason('SHOT', 0),
            'fire': self._attack_reason('FIRE', 1),
            'ram': self._attack_reason('RAM', 2),
        }
        if source == 'client_simulation':
            return reason_id
        if reason_id != expected[source]:
            raise RuntimeError(
                'ordered combat event attack_reason does not match source: '
                '%s != %s' % (reason_id, expected[source]))
        return reason_id

    def _validate_combat_event_contract(self, event):
        source = self._combat_event_source(event)
        attack_reason = self._combat_attack_reason(event)
        kind = event.get('kind')
        valid_kinds = {
            'shot': ('hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit'),
            'fire': ('bot_hit', 'bot_human_hit', 'bot_bot_hit'),
            'ram': ('bot_hit', 'bot_human_hit', 'bot_bot_hit'),
            'client_simulation': ('health',),
            'player_left': ('health',),
        }
        if kind not in valid_kinds[source]:
            raise RuntimeError(
                'ordered combat event source %s does not allow kind %s' %
                (source, kind))
        attacker_key = self._event_entity_key(event, 'attacker')
        if source in ('shot', 'fire', 'ram') and attacker_key is None:
            raise RuntimeError(
                'ordered %s combat event has no attacker' % source)
        if source in ('client_simulation', 'player_left') and \
                attacker_key is not None:
            raise RuntimeError(
                'ordered %s combat event must not have an attacker' % source)
        return source, attack_reason

    def _present_combat_feedback(self, event, target_record,
                                 attacker_record, reason_id=None):
        """Feed accepted server combat through stock #1513 feedback RPCs."""
        feedback_common = getattr(
            self._runtime, 'battle_feedback_common', None)
        event_types = getattr(feedback_common, 'BATTLE_EVENT_TYPE', None)
        if event_types is None:
            raise RuntimeError('#1513 battle feedback constants are unavailable')
        damage = max(0, int(event.get('damage', 0) or 0))
        if reason_id is None:
            reason_id = self._combat_attack_reason(event)
        critical = event.get('critical')
        critical_count = len((critical or {}).get('events') or ())
        if attacker_record.get('local'):
            self._assert_player_identity(attacker_record['engine_id'])
        target_team = self._combat_record_team(target_record)
        attacker_team = self._combat_record_team(attacker_record)
        enemy = target_team != attacker_team
        if attacker_record.get('local') and enemy:
            assert_damage_type = getattr(
                self._runtime.compatibility,
                'assert_vehicle_marker_damage_type', None)
            if not callable(assert_damage_type):
                raise RuntimeError(
                    '#1513 vehicle-marker damage boundary is unavailable')
            assert_damage_type(
                self._avatar, int(attacker_record['engine_id']))
        output = []
        if attacker_record.get('local') and enemy:
            target_id = int(target_record['engine_id'])
            if damage > 0:
                output.append({
                    'eventType': int(event_types.DAMAGE),
                    'targetID': target_id, 'count': 1,
                    'details': int(event_types.packDamage(
                        damage, reason_id))})
            if critical_count > 0:
                output.append({
                    'eventType': int(event_types.CRIT),
                    'targetID': target_id, 'count': 1,
                    'details': int(event_types.packCrits(
                        critical_count, reason_id))})
            if bool(event.get('dead')):
                output.append({
                    'eventType': int(event_types.KILL),
                    'targetID': target_id, 'count': 1, 'details': 0})
        if attacker_record.get('local'):
            target_id = int(target_record['engine_id'])
            if (self._combat_event_source(event) == 'shot' and
                    event.get('kind') in (
                        'hit', 'bot_hit', 'bot_human_hit',
                        'bot_bot_hit')):
                flags_type = getattr(
                    self._runtime.constants, 'VEHICLE_HIT_FLAGS', None)
                if flags_type is None:
                    raise RuntimeError(
                        '#1513 VEHICLE_HIT_FLAGS are unavailable')
                flags = int(flags_type.ATTACK_IS_DIRECT_PROJECTILE)
                shot_result = max(
                    0, min(int(event.get('shot_result', 2)), 2))
                if shot_result == 2:
                    flags |= int(
                        flags_type.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE)
                elif shot_result == 1:
                    flags |= int(
                        flags_type.MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE)
                else:
                    flags |= int(flags_type.RICOCHET)
                if bool(event.get('dead')):
                    flags |= int(flags_type.VEHICLE_KILLED)
                callback = getattr(self._avatar, 'showShotResults', None)
                if not callable(callback):
                    raise RuntimeError(
                        '#1513 shot-result feedback boundary is unavailable')
                callback([(flags << 32) | target_id])
        if target_record.get('local') and enemy:
            attacker_id = int(attacker_record['engine_id'])
            if damage > 0:
                output.append({
                    'eventType': int(event_types.RECEIVED_DAMAGE),
                    'targetID': attacker_id, 'count': 1,
                    'details': int(event_types.packDamage(
                        damage, reason_id))})
            if critical_count > 0:
                output.append({
                    'eventType': int(event_types.RECEIVED_CRIT),
                    'targetID': attacker_id, 'count': 1,
                    'details': int(event_types.packCrits(
                        critical_count, reason_id))})
        if output:
            callback = getattr(self._avatar, 'onBattleEvents', None)
            if not callable(callback):
                raise RuntimeError(
                    '#1513 battle-event feedback boundary is unavailable')
            callback(output)
        return bool(output)

    def _apply_combat_event(self, event, update_state=True):
        source, attack_reason = self._validate_combat_event_contract(event)
        target_key = self._event_entity_key(event, 'target')
        if target_key is None:
            raise RuntimeError('ordered combat event has no target')
        record = self._records.get(target_key)
        if record is None:
            raise RuntimeError(
                'ordered combat event target is unavailable: %s' %
                target_key)
        latest_state = record.get('state') or {}
        state = self._combat_event_state(event, latest_state, target_key)
        if update_state:
            record['state'] = state
        attacker = event.get('attacker_bot')
        attacker_kind = 'bot'
        if attacker is None:
            attacker = event.get('attacker')
            attacker_kind = 'player'
        attacker_record = self._records.get(
            '%s:%s' % (attacker_kind, attacker))
        if attacker is not None and attacker_record is None:
            if not self._missing_projectile_attacker_allowed(event):
                raise RuntimeError(
                    'ordered combat event attacker is unavailable: %s:%s' %
                    (attacker_kind, attacker))
        attacker_id = (attacker_record.get('engine_id')
                       if attacker_record is not None else 0)
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            raise RuntimeError(
                'ordered combat event target has no native entity: %s' %
                target_key)
        if (attacker_record is not None and
                self._server_entity(attacker_record['engine_id']) is None):
            raise RuntimeError(
                'ordered combat event attacker has no native entity: %s:%s' %
                (attacker_kind, attacker))
        if (entity is not None and attacker is not None and
                not record.get('local')):
            entity.last_killer_id = int(attacker_id or 0)
        if record.get('local') and attacker is not None:
            self._local_last_attacker = (attacker_kind, int(attacker))
        if source == 'player_left' and attacker_record is not None:
            raise RuntimeError('player_left event has an attacker')
        if attacker_record is not None:
            self._present_combat_hit(
                event, record, attacker_record, attacker_id)
            self._present_combat_feedback(
                event, record, attacker_record, attack_reason)
        critical = event.get('critical')
        if isinstance(critical, dict):
            canonical = self._critical_state(critical)
            should_apply = self._reconcile_critical_authority(record, event)
            if (entity is not None and should_apply and
                    canonical != record.get('critical_state')):
                events = critical_damage.apply_payload(entity, critical)
                state['critical'] = canonical
                record['critical_state'] = canonical
                self._present_critical(record, events, attacker_id)
        if 'display_health' in event:
            state['display_health'] = max(
                0, int(event.get('display_health', state['health'])))
        death_reason = int(event['death_reason'])
        self._apply_health(
            record, state, attacker_id, death_reason, force_cause=True,
            attack_reason_id=(0 if attack_reason is None else attack_reason))
        if not update_state:
            record['state'] = latest_state
        return True

    @staticmethod
    def _critical_state(payload):
        if not isinstance(payload, dict):
            return None
        result = dict(payload)
        result['events'] = []
        return result

    @staticmethod
    def _critical_proposal_contract(record, critical, hull_damage):
        """Bind a firing-client proposal to the last canonical target state."""
        if not isinstance(critical, dict):
            return {}
        state = record.get('state') or {}
        if record.get('kind') == 'bot':
            base_name = 'combat_base_revision'
            ack_name = 'combat_ack_seq'
        elif record.get('kind') == 'player':
            base_name = 'critical_base_revision'
            ack_name = 'critical_ack_seq'
        else:
            raise RuntimeError('critical target kind is invalid')
        values = {}
        for wire_name, state_name in (
                ('critical_target_base_revision', base_name),
                ('critical_target_ack_seq', ack_name)):
            raw = state.get(state_name)
            try:
                parsed = int(raw)
                exact = float(raw) == parsed
            except (TypeError, ValueError, OverflowError):
                exact = False
                parsed = -1
            if isinstance(raw, bool) or not exact or parsed < 0:
                raise RuntimeError(
                    '#1513 critical target has no exact %s' % state_name)
            values[wire_name] = parsed
        values['hull_damage'] = hull_damage
        return values

    def _reconcile_critical_authority(self, record, source):
        if record.get('kind') != 'player':
            return True
        required = ('critical_revision', 'critical_base_revision',
                    'critical_ack_seq')
        if not all(name in source for name in required):
            raise RuntimeError(
                '#1513 player critical state has no revision contract')
        revision = max(0, int(source['critical_revision']))
        base_revision = max(0, int(source['critical_base_revision']))
        ack_seq = max(0, int(source['critical_ack_seq']))
        previous_revision = int(record.get('critical_revision', -1))
        if revision < previous_revision:
            return False
        record['critical_revision'] = revision
        record['critical_base_revision'] = base_revision
        record['critical_ack_seq'] = ack_seq
        if record.get('local'):
            self.acknowledge_local_damage_report(
                base_revision, ack_seq, revision)
            if (self._local_critical_owned and
                    base_revision == self._local_critical_base_revision):
                return False
        return revision > previous_revision

    def _apply_critical_state(self, record, payload, authority=None):
        canonical = self._critical_state(payload)
        if canonical is None:
            return False
        if authority is not None:
            should_apply = self._reconcile_critical_authority(
                record, authority)
            if not should_apply:
                if record.get('local') and record.get('critical_state'):
                    state = dict(record.get('state') or {})
                    state['critical'] = record['critical_state']
                    record['state'] = state
                return False
        if canonical == record.get('critical_state'):
            return False
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            return False
        events = critical_damage.apply_payload(entity, canonical)
        record['critical_state'] = canonical
        state = dict(record.get('state') or {})
        state['critical'] = canonical
        record['state'] = state
        self._present_critical(record, events, 0)
        return True

    def _apply_vehicle_statistics(self, record, state):
        """Feed server-owned frags/team-killer state to stock ClientArena."""
        try:
            frags = int(state.get('frags', 0))
        except (TypeError, ValueError):
            frags = 0
        changed = False
        if record.get('presented_frags') != frags:
            self._binding.arena_vehicle_statistics(
                record['engine_id'], frags)
            record['presented_frags'] = frags
            changed = True
        team_killer = bool(state.get('team_killer', False))
        if team_killer and not record.get('presented_team_killer'):
            self._binding.arena_team_killer(record['engine_id'])
            record['presented_team_killer'] = True
            changed = True
        return changed

    def _death_attacker_engine_id(self, state):
        """Resolve the durable server killer before a death snapshot wins."""
        kind = state.get('death_attacker_kind')
        try:
            network_id = int(state.get('death_attacker_id', 0))
        except (TypeError, ValueError):
            return 0
        record = self._records.get('%s:%s' % (kind, network_id))
        return int(record.get('engine_id', 0)) if record is not None else 0

    @staticmethod
    def _critical_extra_index(descriptor, name):
        """Resolve the exact descriptor extra index used by #1513 Avatar."""
        extra_name = str(name)
        if not extra_name.endswith('Health'):
            extra_name += 'Health'
        selected = None
        extras_dict = getattr(descriptor, 'extrasDict', None)
        if extras_dict is not None:
            selected = extras_dict.get(extra_name)
        extras = getattr(descriptor, 'extras', None)
        if hasattr(extras, 'items'):
            iterator = extras.items()
        else:
            iterator = enumerate(extras or ())
        for index, extra in iterator:
            if (extra is selected or
                    str(getattr(extra, 'name', '')) == extra_name):
                return int(index)
        selected_index = int(getattr(selected, 'index', 0) or 0)
        if selected_index <= 0:
            raise RuntimeError(
                '#1513 descriptor has no critical extra: %s' % extra_name)
        return selected_index

    def _present_critical(self, record, events, attacker_id):
        """Map copied state transitions to audited stock #1513 UI callbacks."""
        if not events:
            return False
        entity = self._server_entity(record['engine_id'])
        if entity is None or entity.typeDescriptor is None:
            return False
        shown = False
        for event in events:
            if (event.get('kind') == 'ammo_rack' and
                    event.get('state') == 'destroyed'):
                callback = getattr(entity, 'showAmmoBayEffect', None)
                if not callable(callback):
                    raise RuntimeError(
                        '#1513 ammo-bay effect boundary is unavailable')
                modes = getattr(
                    self._runtime.constants, 'AMMOBAY_DESTRUCTION_MODE', None)
                if modes is None or not hasattr(modes, 'HE_DETONATION'):
                    raise RuntimeError(
                        '#1513 ammo-bay destruction mode is unavailable')
                callback(int(modes.HE_DETONATION), 0.0, 0.0)
                shown = True
        if not record.get('local') or self._avatar is None:
            return shown
        indices = getattr(self._runtime.constants,
                          'DAMAGE_INFO_INDICES', {})
        suffixes = {
            'fire': '_AT_FIRE',
            'ramming': '_AT_RAMMING',
            'world_collision': '_AT_WORLD_COLLISION',
            'drowning': '_AT_DROWNING',
        }
        for event in events:
            kind = event.get('kind')
            state = event.get('state')
            cause = event.get('cause', 'shot')
            extra_index = 0
            if kind == 'device':
                extra_index = self._critical_extra_index(
                    entity.typeDescriptor, event.get('name'))
                if extra_index <= 0:
                    raise RuntimeError(
                        '#1513 device critical extra is invalid')
                if cause == 'repair':
                    code = ('DEVICE_REPAIRED' if state == 'normal' else
                            'DEVICE_REPAIRED_TO_CRITICAL')
                else:
                    base = ('DEVICE_DESTROYED' if state == 'destroyed' else
                            'DEVICE_CRITICAL')
                    code = base + suffixes.get(cause, '_AT_SHOT')
            elif kind == 'crew':
                extra_index = self._critical_extra_index(
                    entity.typeDescriptor, event.get('name'))
                if extra_index <= 0:
                    raise RuntimeError(
                        '#1513 crew critical extra is invalid')
                if state == 'normal':
                    code = 'TANKMAN_RESTORED'
                elif cause in ('world_collision', 'drowning'):
                    code = 'TANKMAN_HIT' + suffixes[cause]
                elif cause == 'fire':
                    code = 'TANKMAN_HIT'
                else:
                    code = 'TANKMAN_HIT_AT_SHOT'
            elif kind == 'fire':
                if bool(state):
                    code = ('DEVICE_STARTED_FIRE_AT_RAMMING'
                            if cause == 'ramming' else
                            'DEVICE_STARTED_FIRE_AT_SHOT')
                else:
                    code = 'FIRE_STOPPED'
            elif kind == 'ammo_rack':
                continue
            else:
                continue
            damage_index = indices.get(code)
            if damage_index is None:
                raise RuntimeError(
                    '#1513 damage-info index is unavailable: %s' % code)
            self._avatar.showVehicleDamageInfo(
                record['engine_id'], int(damage_index), extra_index,
                int(attacker_id or 0), 0)
            shown = True
        return shown

    def _tick_critical_states(self, dt):
        """Advance copied repair/fire laws only for the locally-owned human."""
        if dt <= 0.0:
            return
        record = self._records.get('player:%s' % self.client.player_id)
        if record is None:
            return
        entity = self._server_entity(record['engine_id'])
        if entity is None or entity.typeDescriptor is None:
            return
        if not self._record_alive(record, entity):
            return
        if not hasattr(entity, 'maxHealth'):
            entity.maxHealth = int(entity.typeDescriptor.maxHealth)
        now = self._clock()
        payload = critical_damage.tick_repair(entity, dt)
        if payload is not None:
            record['critical_state'] = self._critical_state(payload)
            state = dict(record.get('state') or {})
            state['critical'] = record['critical_state']
            record['state'] = state
            # Match the mature 0.8.2 lifecycle: close the repair timer before
            # publishing the repaired-device transition.
            self._present_repair_progress(entity)
            self._present_critical(
                record, payload.get('events'), record['engine_id'])
            if (payload.get('events') or
                    now >= self._next_critical_report_time):
                self._queue_local_damage_report(critical=payload)
                self._next_critical_report_time = (
                    now + CRITICAL_REPAIR_NETWORK_SECONDS)
        fire_reason = self._attack_reason('FIRE', 1)
        damage, fire_payload = critical_damage.tick_fire(
            entity, dt, now=now)
        if fire_payload is not None:
            record['critical_state'] = self._critical_state(fire_payload)
            state = dict(record.get('state') or {})
            state['critical'] = record['critical_state']
            record['state'] = state
            self._present_critical(
                record, fire_payload.get('events'), record['engine_id'])
            if (fire_payload.get('events') or
                    now >= self._next_critical_report_time):
                self._queue_local_damage_report(
                    critical=fire_payload, reason=fire_reason)
                self._next_critical_report_time = (
                    now + CRITICAL_REPAIR_NETWORK_SECONDS)
        if damage > 0:
            state = dict(record.get('state') or {})
            state['health'] = max(
                0, int(getattr(entity, 'health', 0)) - int(damage))
            state['alive'] = state['health'] > 0
            state['death_reason'] = fire_reason
            record['state'] = state
            self._queue_local_damage_report(reason=fire_reason)
            self._apply_health(
                record, state, getattr(entity, 'last_killer_id', 0),
                fire_reason)
        self._present_repair_progress(entity)

    def _present_repair_progress(self, entity):
        status = getattr(
            getattr(self._runtime.constants, 'VEHICLE_MISC_STATUS', None),
            'DESTROYED_DEVICE_IS_REPAIRING', None)
        callback = getattr(self._avatar, 'updateVehicleMiscStatus', None)
        if status is None or not callable(callback):
            return False
        destroyed = getattr(entity, '_destroyed_devices', None) or ()
        hp_map = getattr(entity, 'devices_hp', None) or {}
        cache = getattr(entity, '_offline_lan_repair_progress', None)
        if cache is None:
            cache = {}
            entity._offline_lan_repair_progress = cache
        for name in tuple(destroyed):
            if name in critical_damage._device_damage.NO_REPAIR_PROGRESS_DEVICES:
                continue
            cap = critical_damage._device_damage.device_regen_hp(
                entity.typeDescriptor, name)
            if not cap:
                continue
            hp = max(0.0, min(float(hp_map.get(name, 0.0)), float(cap)))
            progress = max(0, min(int(round(100.0 * hp / cap)), 100))
            if cache.get(name) == progress:
                continue
            cache[name] = progress
            extra_index = self._critical_extra_index(
                entity.typeDescriptor, name)
            if extra_index <= 0:
                continue
            seconds = critical_damage._device_damage.repair_seconds(
                name, entity.typeDescriptor)
            seconds_left = max(0.0, seconds * (1.0 - hp / cap))
            callback(entity.id, int(status),
                     int(extra_index) | (progress << 8),
                     (seconds_left,))
        for name in tuple(cache):
            if name not in destroyed:
                extra_index = self._critical_extra_index(
                    entity.typeDescriptor, name)
                if extra_index <= 0:
                    raise RuntimeError(
                        '#1513 repaired device has no extra index: %s' %
                        name)
                callback(entity.id, int(status), int(extra_index), (0.0,))
                cache.pop(name, None)
        return True

    def _apply_battle_result(self, result):
        if not isinstance(result, dict):
            return False
        self._battle_result = dict(result)
        if (self._round_finished_notified or self._avatar is None or
                self.state != 'running'):
            return False
        finish_reason = getattr(self._runtime.constants, 'FINISH_REASON', None)
        if finish_reason is None:
            raise RuntimeError('FINISH_REASON is unavailable')
        reason_name = str(result.get('reason', '')).lower()
        if ('eliminat' in reason_name or 'exterminat' in reason_name or
                reason_name == 'team_eliminated'):
            reason = finish_reason.EXTERMINATION
        elif 'base' in reason_name or 'captur' in reason_name:
            reason = finish_reason.BASE
        elif 'timeout' in reason_name or 'time_out' in reason_name:
            reason = finish_reason.TIMEOUT
        else:
            reason = finish_reason.UNKNOWN
        callback = getattr(self._avatar, 'onRoundFinished', None)
        if not callable(callback):
            raise RuntimeError('Avatar.onRoundFinished is unavailable')
        base_team = max(0, min(int(result.get('base_team', 0)), 2))
        if base_team in (1, 2):
            captured = getattr(self._avatar.arena, 'onTeamBaseCaptured', None)
            if callable(captured):
                captured(base_team, 0)
        callback(max(0, min(int(result.get('winner', 0)), 2)), reason)
        self._round_finished_notified = True
        return True

    def _apply_rules(self, rules):
        incoming = (rules or {}).get('bases') or {}
        arena = getattr(self._avatar, 'arena', None)
        callback = getattr(arena, 'onTeamBasePointsUpdate', None)
        if not callable(callback):
            return False
        changed = False
        stored = self._rules_state.setdefault('bases', {})
        for team in (1, 2):
            raw = incoming.get(str(team), incoming.get(team, {})) or {}
            current = {
                'points': max(0, min(int(raw.get('points', 0)), 100)),
                'time_left': max(
                    0.0, _number(raw.get('time_left'))),
                'invaders': max(
                    0, int(_number(raw.get('invaders')))),
                'stopped': bool(raw.get('stopped', False)),
            }
            if stored.get(str(team)) != current:
                stored[str(team)] = current
                callback(
                    team, 0, current['points'],
                    current['time_left'], current['invaders'],
                    current['stopped'])
                changed = True
        return changed

    def _show_shot(self, event, update_state=True):
        key = self._event_entity_key(event, 'attacker')
        if key is None:
            raise RuntimeError('ordered shot event has no attacker')
        record = self._records.get(key)
        if record is None:
            raise RuntimeError(
                'ordered shot event attacker is unavailable: %s' % key)
        if update_state:
            record['shot_penalty_until'] = (
                self._clock() + spotting.SHOT_CAMOUFLAGE_SECONDS)
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            raise RuntimeError(
                'ordered shot event attacker has no native entity: %s' % key)
        transient_names = []
        try:
            entity._offlineLANShotIndex = max(
                0, int(event.get('shell_index', 0) or 0))
            if ('shot_yaw' in event and 'shot_pitch' in event and
                    bool(getattr(
                        entity, '_offlineLANPresentation', False))):
                entity._offlineLANShotYaw = _number(
                    event.get('shot_yaw'))
                entity._offlineLANShotPitch = _number(
                    event.get('shot_pitch'))
                transient_names.extend((
                    '_offlineLANShotYaw', '_offlineLANShotPitch'))
            canonical = all(name in event for name in (
                'origin', 'velocity', 'gravity', 'maxDistance'))
            if canonical:
                projectile_id = event.get('projectile_id')
                origin = event.get('origin')
                velocity = event.get('velocity')
                gravity = _number(event.get('gravity'))
                elapsed = self._projectile_launch_age(event, self._clock())
                reference_origin = trajectory_position(
                    origin, velocity, (0.0, -gravity, 0.0), elapsed)
                reference_velocity = (
                    float(velocity[0]),
                    float(velocity[1]) - gravity * elapsed,
                    float(velocity[2]))
                if projectile_id is not None:
                    self._projectile_visual_meta[str(projectile_id)] = {
                        'origin': tuple(float(value) for value in origin),
                        'velocity': tuple(float(value) for value in velocity),
                        'gravity': gravity,
                    }
                for name, value in (
                        ('_offlineLANShotOrigin', origin),
                        ('_offlineLANShotVelocity', velocity),
                        ('_offlineLANShotGravity', gravity),
                        ('_offlineLANShotMaxDistance',
                         event.get('maxDistance')),
                        ('_offlineLANProjectileID', projectile_id),
                        ('_offlineLANShotReferenceOrigin', reference_origin),
                        ('_offlineLANShotReferenceVelocity',
                         reference_velocity)):
                    setattr(entity, name, value)
                    transient_names.append(name)
                # RemoteVehicle.showShooting delegates to the same factory
                # presenter and consumes the transient canonical values.  The
                # stock local Vehicle has no such delegate, so launch its
                # authoritative tracer explicitly from the event instead of
                # reconstructing it from a later muzzle pose.
                if (not bool(getattr(
                        entity, '_offlineLANPresentation', False)) and
                        self._remote_factory is not None):
                    self._remote_factory.play_projectile_tracer(
                        entity.typeDescriptor,
                        entity._offlineLANShotIndex,
                        origin, velocity, gravity,
                        event.get('maxDistance'), entity.id, projectile_id,
                        reference_origin, reference_velocity)
            # Exact #1513 uses gun.burst[0] for the predicted-shot fallback.
            # Zero is not a one-shot sentinel: it reaches the native shoot
            # extra as an unbounded burst.  A server event is authoritative,
            # so pass False explicitly; for the local vehicle that also
            # closes Avatar's isWaitingForShot/cancelWaitingForShot handshake.
            burst = _field(entity.typeDescriptor.gun, 'burst', (1,))
            try:
                burst_count = int(burst[0])
            except (TypeError, ValueError, IndexError):
                burst_count = 1
            entity.showShooting(max(1, burst_count), False)
        finally:
            for name in transient_names:
                try:
                    delattr(entity, name)
                except Exception:
                    pass
        return True

    def _projectile_is_authority(self):
        checker = getattr(self.client, 'is_bot_authority', None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _set_projectile_epoch(self, value, now):
        try:
            epoch = int(value)
            if isinstance(value, bool) or epoch < 0:
                return False
        except (TypeError, ValueError, OverflowError):
            return False
        if self._projectile_epoch == epoch:
            return True
        self._projectile_epoch = epoch
        self._projectile_meta = {}
        self._projectile_terminal_data = {}
        self._projectile_target_positions = {}
        self._projectile_position_history = []
        if self._projectiles is not None:
            reset_at = max(float(now), self._projectiles.now)
            if not self._projectiles.reset(reset_at):
                raise RuntimeError('projectile authority reset failed')
        self._next_projectile_progress_time = float(now)
        return True

    def _observe_projectile_message(self, message):
        """Anchor round-relative server time in this process clock domain."""
        if not isinstance(message, dict):
            return False
        now = self._clock()
        received = message.get('_client_received_time')
        local_anchor = now
        if received is not None:
            try:
                received = float(received)
                if not math.isnan(received) and not math.isinf(received):
                    # The network thread timestamps receipt with a monotonic
                    # clock.  Project its queueing delay into BigWorld time so
                    # a render stall cannot make every in-flight shell young.
                    process_now = getattr(time, 'monotonic', None)
                    if callable(process_now):
                        process_now = float(process_now())
                    else:
                        process_now = float(time.clock())
                    lag = max(0.0, min(60.0, process_now - received))
                    local_anchor = max(0.0, now - lag)
            except (TypeError, ValueError, OverflowError):
                return False
        if 'server_time_ms' in message:
            server_time = message.get('server_time_ms')
            try:
                server_time = int(server_time)
            except (TypeError, ValueError, OverflowError):
                return False
            if server_time < 0:
                return False
            if (self._projectile_server_time_ms is None or
                    server_time >= self._projectile_server_time_ms):
                one_way = 0.0
                try:
                    rtt_ms = getattr(self.client, 'rtt_ms', None)
                    if rtt_ms is not None:
                        one_way = max(
                            0.0, min(0.25, float(rtt_ms) / 2000.0))
                except (TypeError, ValueError, OverflowError):
                    one_way = 0.0
                self._projectile_server_time_ms = server_time
                self._projectile_server_local_time = max(
                    0.0, local_anchor - one_way)
        epoch = message.get(
            'authority_epoch', getattr(self.client, 'authority_epoch', None))
        if epoch is not None:
            self._set_projectile_epoch(epoch, now)
        return True

    def _projectile_estimated_server_time(self, now):
        if (self._projectile_server_time_ms is None or
                self._projectile_server_local_time is None):
            return None
        elapsed = max(
            0.0, float(now) - float(self._projectile_server_local_time))
        return int(self._projectile_server_time_ms + elapsed * 1000.0)

    def _projectile_local_launch_time(self, launch_server_time_ms, now):
        estimated = self._projectile_estimated_server_time(now)
        if estimated is None:
            return min(float(now), self._projectiles.now)
        age = max(
            0.0, float(estimated - int(launch_server_time_ms)) / 1000.0)
        return max(
            0.0, min(self._projectiles.now, float(now) - age))

    def _projectile_launch_age(self, raw, now):
        """Return the canonical launch age shared by simulation and tracer."""
        if not isinstance(raw, dict):
            return 0.0
        launch_time = raw.get('launch_server_time_ms')
        max_time_ms = raw.get('max_time_ms', PROJECTILE_MAX_TIME_MS)
        try:
            launch_time = int(launch_time)
            maximum = max(0.0, float(max_time_ms) / 1000.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        estimated = self._projectile_estimated_server_time(now)
        if estimated is None:
            return 0.0
        return max(
            0.0, min(maximum, float(estimated - launch_time) / 1000.0))

    @staticmethod
    def _projectile_wire_meta(raw):
        """Normalize the canonical-event and snapshot launch spellings."""
        if not isinstance(raw, dict):
            return None
        maximum = raw.get('max_distance', raw.get('maxDistance'))
        shooter_kind = raw.get('shooter_kind')
        shooter_id = raw.get('shooter_id')
        if shooter_kind is None:
            if raw.get('attacker_bot') is not None:
                shooter_kind = 'bot'
                shooter_id = raw.get('attacker_bot')
            elif raw.get('attacker') is not None:
                shooter_kind = 'player'
                shooter_id = raw.get('attacker')
        required = (
            'projectile_id', 'source_vehicle', 'shot_seq', 'shell_index', 'origin',
            'velocity', 'gravity', 'max_time_ms', 'is_he',
            'splash_radius', 'penetration_factor', 'launch_server_time_ms')
        if (maximum is None or shooter_kind is None or shooter_id is None or
                any(name not in raw for name in required)):
            return None
        try:
            origin = tuple(float(value) for value in raw['origin'])
            velocity = tuple(float(value) for value in raw['velocity'])
            if len(origin) != 3 or len(velocity) != 3:
                return None
            gravity = float(raw['gravity'])
            maximum = float(maximum)
            max_time_ms = int(raw['max_time_ms'])
            projectile_id = str(raw['projectile_id'])
            source_vehicle = str(raw['source_vehicle'])
            shooter_kind = str(shooter_kind)
            shooter_id = int(shooter_id)
            shot_seq = int(raw['shot_seq'])
            shell_index = int(raw['shell_index'])
            launch_server_time = int(raw['launch_server_time_ms'])
            splash_radius = float(raw['splash_radius'])
            penetration_factor = float(raw['penetration_factor'])
        except (TypeError, ValueError, OverflowError):
            return None
        values = (origin + velocity + (
            gravity, maximum, splash_radius, penetration_factor))
        if (not projectile_id or not source_vehicle or
                len(source_vehicle) > 128 or
                shooter_kind not in ('player', 'bot') or
                shooter_id <= 0 or shot_seq <= 0 or
                shell_index < 0 or shell_index > 9 or
                gravity <= 0.0 or maximum <= 0.0 or
                max_time_ms <= 0 or max_time_ms > PROJECTILE_MAX_TIME_MS or
                launch_server_time < 0 or splash_radius < 0.0 or
                penetration_factor < 0.0 or
                not isinstance(raw['is_he'], bool) or
                any(math.isnan(value) or math.isinf(value)
                    for value in values)):
            return None
        return {
            'projectile_id': projectile_id,
            'shooter_kind': shooter_kind,
            'shooter_id': shooter_id,
            'source_vehicle': source_vehicle,
            'shot_seq': shot_seq,
            'shell_index': shell_index,
            'origin': origin,
            'velocity': velocity,
            'gravity': gravity,
            'max_distance': maximum,
            'max_time_ms': max_time_ms,
            'is_he': bool(raw['is_he']),
            'splash_radius': splash_radius,
            'penetration_factor': penetration_factor,
            'launch_server_time_ms': launch_server_time,
            'base_checked_ms': max(
                0, int(raw.get('checked_through_ms', 0) or 0)),
            'checked_distance': max(
                0.0, _number(raw.get('checked_distance'), 0.0)),
            'piercing_loss': max(
                0.0, _number(raw.get('piercing_loss'), 0.0)),
        }

    def _install_projectile_meta(self, normalized):
        projectile_id = normalized['projectile_id']
        meta = self._projectile_meta.get(projectile_id)
        if meta is None:
            meta = dict(normalized)
            meta['destructibles_pending'] = []
            self._projectile_meta[projectile_id] = meta
        else:
            # Launch fields are immutable; only the server-acknowledged cursor
            # and accumulated penetration state may advance in snapshots.
            frozen = (
                'shooter_kind', 'shooter_id', 'source_vehicle',
                'shot_seq', 'shell_index',
                'origin', 'velocity', 'gravity', 'max_distance',
                'max_time_ms', 'is_he', 'splash_radius',
                'launch_server_time_ms')
            if any(meta.get(name) != normalized.get(name)
                   for name in frozen):
                raise RuntimeError('canonical projectile launch changed')
            if (normalized['base_checked_ms'] >=
                    meta.get('base_checked_ms', 0)):
                meta['base_checked_ms'] = normalized['base_checked_ms']
                meta['acked_distance'] = normalized['checked_distance']
                meta['acked_piercing_loss'] = normalized['piercing_loss']
                pending = meta.get('progress_pending')
                if (pending is not None and
                        normalized['base_checked_ms'] >=
                        pending['checked_through_ms']):
                    meta['progress_pending'] = None
                active = (self._projectiles is not None and
                          self._projectiles.contains(projectile_id))
                if (not active and
                        meta.get('pending_resolution') is None and
                        not meta.get('awaiting_resolution')):
                    meta['checked_distance'] = normalized[
                        'checked_distance']
                    meta['piercing_loss'] = normalized['piercing_loss']
                    meta['penetration_factor'] = normalized[
                        'penetration_factor']
        return meta

    def _accept_projectile_event(self, event):
        """Register one server-admitted launch on the elected simulator."""
        if not self._projectile_is_authority() or self._projectiles is None:
            return False
        epoch = event.get(
            'authority_epoch', getattr(self.client, 'authority_epoch', None))
        if not self._set_projectile_epoch(epoch, self._clock()):
            return False
        normalized = self._projectile_wire_meta(event)
        if normalized is None:
            raise RuntimeError('canonical projectile event is malformed')
        meta = self._install_projectile_meta(normalized)
        projectile_id = normalized['projectile_id']
        if self._projectiles.contains(projectile_id):
            return True
        now = self._clock()
        launch_time = self._projectile_local_launch_time(
            normalized['launch_server_time_ms'], now)
        accepted = self._projectiles.launch(
            projectile_id, normalized['origin'], normalized['velocity'],
            (0.0, -normalized['gravity'], 0.0), launch_time,
            float(normalized['max_time_ms']) / 1000.0,
            normalized['max_distance'], payload={
                'shooter_kind': normalized['shooter_kind'],
                'shooter_id': normalized['shooter_id'],
                'shot_seq': normalized['shot_seq'],
                'shell_index': normalized['shell_index'],
            })
        if not accepted:
            self._projectile_meta.pop(projectile_id, None)
            raise RuntimeError('canonical projectile launch was not admitted')
        meta['awaiting_resolution'] = False
        return True

    def _reconcile_projectile_snapshot(self, message):
        """Restore the authoritative cursor without rescanning elapsed time."""
        if self._projectiles is None or not isinstance(message, dict):
            return False
        rows = message.get('projectiles')
        if not isinstance(rows, (list, tuple)):
            return False
        now = self._clock()
        active_ids = set()
        for raw in rows:
            normalized = self._projectile_wire_meta(raw)
            if normalized is None:
                raise RuntimeError('active projectile snapshot is malformed')
            projectile_id = normalized['projectile_id']
            active_ids.add(projectile_id)
            self._ensure_projectile_visual(normalized, now)
        if not self._projectile_is_authority():
            return True
        for raw in rows:
            normalized = self._projectile_wire_meta(raw)
            projectile_id = normalized['projectile_id']
            meta = self._install_projectile_meta(normalized)
            if (self._projectiles.contains(projectile_id) or
                    meta.get('awaiting_resolution') or
                    meta.get('pending_resolution') is not None):
                continue
            source = self._projectile_source_entity(meta)
            source_descriptor = (getattr(source, 'typeDescriptor', None)
                                 if source is not None else None)
            if source_descriptor is None:
                source_descriptor = self._projectile_source_descriptor(meta)
            if source_descriptor is None:
                # A takeover snapshot can overtake delayed native entity
                # materialization.  The ledger freezes source_vehicle so a
                # shooter that disconnected after firing remains resolvable;
                # only wait when even that canonical descriptor is unavailable.
                continue
            launch_time = self._projectile_local_launch_time(
                normalized['launch_server_time_ms'], now)
            cursor_time = min(
                self._projectiles.now,
                launch_time + normalized['base_checked_ms'] / 1000.0)
            restored = self._projectiles.restore({
                'key': projectile_id,
                'start': normalized['origin'],
                'velocity': normalized['velocity'],
                'gravity': (0.0, -normalized['gravity'], 0.0),
                'launch_time': launch_time,
                'max_time': normalized['max_time_ms'] / 1000.0,
                'max_distance': normalized['max_distance'],
                'payload': {
                    'shooter_kind': normalized['shooter_kind'],
                    'shooter_id': normalized['shooter_id'],
                    'shot_seq': normalized['shot_seq'],
                    'shell_index': normalized['shell_index'],
                },
                'cursor_time': max(launch_time, cursor_time),
                'distance': normalized['checked_distance'],
            })
            if not restored:
                raise RuntimeError('active projectile restore failed')
        for projectile_id, meta in tuple(self._projectile_meta.items()):
            if (projectile_id not in active_ids and
                    (meta.get('awaiting_resolution') or
                     meta.get('pending_resolution') is not None)):
                self._projectile_meta.pop(projectile_id, None)
                self._projectile_terminal_data.pop(projectile_id, None)
        try:
            revision = int(message.get('projectile_revision', -1))
        except (TypeError, ValueError, OverflowError):
            revision = -1
        self._projectile_revision = max(
            self._projectile_revision, revision)
        return True

    def _apply_projectile_terminal_event(self, event):
        projectile_id = event.get('projectile_id')
        if projectile_id is None:
            raise RuntimeError('projectile terminal event has no id')
        projectile_id = str(projectile_id)
        self._stop_projectile_visual(projectile_id, event)
        if self._projectiles is not None:
            self._projectiles.remove(projectile_id)
        self._projectile_meta.pop(projectile_id, None)
        self._projectile_terminal_data.pop(projectile_id, None)
        return True

    def _ensure_projectile_visual(self, normalized, now):
        """Ensure late joiners and delayed snapshots see the live tracer."""
        if self._remote_factory is None or not isinstance(normalized, dict):
            return False
        descriptor = self._projectile_source_descriptor(normalized)
        if descriptor is None:
            return False
        elapsed = self._projectile_launch_age(normalized, now)
        gravity = normalized['gravity']
        reference_origin = trajectory_position(
            normalized['origin'], normalized['velocity'],
            (0.0, -gravity, 0.0), elapsed)
        reference_velocity = (
            normalized['velocity'][0],
            normalized['velocity'][1] - gravity * elapsed,
            normalized['velocity'][2])
        self._projectile_visual_meta[normalized['projectile_id']] = {
            'origin': tuple(normalized['origin']),
            'velocity': tuple(normalized['velocity']),
            'gravity': gravity,
        }
        record = self._records.get('%s:%s' % (
            normalized['shooter_kind'], normalized['shooter_id']))
        attacker_id = int(record.get('engine_id', 0) or 0) \
            if record is not None else 0
        if attacker_id <= 0:
            # ProjectileMover only uses the attacker id for presentation
            # attribution.  A disconnected shooter must not erase a live
            # projectile restored from the durable snapshot.
            attacker_id = int(normalized['shooter_id'])
        return bool(self._remote_factory.play_projectile_tracer(
            descriptor, normalized['shell_index'], normalized['origin'],
            normalized['velocity'], gravity, normalized['max_distance'],
            attacker_id, normalized['projectile_id'], reference_origin,
            reference_velocity))

    def _stop_projectile_visual(self, projectile_id, event):
        if self._remote_factory is None:
            return False
        impact = event.get('impact') if isinstance(event, dict) else None
        if impact is None:
            state = (self._projectiles.get(projectile_id)
                     if self._projectiles is not None else None)
            impact = state.get('position') if state is not None else None
        if impact is None:
            visual = self._projectile_visual_meta.get(projectile_id)
            try:
                elapsed = max(
                    0.0, float(event.get('resolved_time_ms')) / 1000.0)
            except (AttributeError, TypeError, ValueError, OverflowError):
                elapsed = None
            if visual is not None and elapsed is not None:
                impact = trajectory_position(
                    visual['origin'], visual['velocity'],
                    (0.0, -visual['gravity'], 0.0), elapsed)
        if impact is None:
            return False
        stopped = bool(self._remote_factory.stop_projectile_tracer(
            projectile_id, impact))
        self._projectile_visual_meta.pop(projectile_id, None)
        return stopped

    def _projectile_record_positions(self):
        result = {}
        for key, record in tuple(self._records.items()):
            if record.get('tombstone'):
                continue
            entity = self._server_entity(record.get('engine_id'))
            if entity is None or not getattr(entity, 'isStarted', False):
                continue
            if record.get('local'):
                result[key] = tuple(self._local_position)
            else:
                result[key] = _xyz(getattr(
                    entity, 'position', record.get('state', {})))
        return result

    def _sample_projectile_positions(self, now, positions):
        """Keep enough pose history for budgeted projectile catch-up."""
        sample = (float(now), dict(positions or {}))
        if (self._projectile_position_history and
                abs(self._projectile_position_history[-1][0] -
                    sample[0]) <= 1.0e-9):
            self._projectile_position_history[-1] = sample
        else:
            self._projectile_position_history.append(sample)
        # Twenty seconds is the protocol lifetime.  The small extra margin
        # retains the left interpolation endpoint during a delayed frame.
        floor = float(now) - PROJECTILE_MAX_TIME_MS / 1000.0 - 1.0
        while (len(self._projectile_position_history) > 2 and
               self._projectile_position_history[1][0] < floor):
            self._projectile_position_history.pop(0)

    def _projectile_historic_position(self, key, absolute_time, fallback):
        history = self._projectile_position_history
        if not history:
            return fallback
        wanted = float(absolute_time)
        first_time, first_positions = history[0]
        if wanted <= first_time:
            return first_positions.get(key, fallback)
        for index in range(1, len(history)):
            right_time, right_positions = history[index]
            if wanted > right_time:
                continue
            left_time, left_positions = history[index - 1]
            left = left_positions.get(key)
            right = right_positions.get(key)
            if left is None:
                return right if right is not None else fallback
            if right is None or right_time <= left_time + 1.0e-9:
                return left
            return lerp3(
                left, right,
                (wanted - left_time) / (right_time - left_time))
        return history[-1][1].get(key, fallback)

    def _prune_projectile_position_history(self):
        if not self._projectile_position_history or self._projectiles is None:
            return
        states = self._projectiles.snapshot()
        if not states:
            # A non-authority client may be elected before the next network
            # snapshot. Keep a short recent trail while canonical visual
            # projectiles prove that takeover work can still arrive.
            if not self._projectile_visual_meta:
                self._projectile_position_history = (
                    self._projectile_position_history[-1:])
                return
            floor = self._projectile_position_history[-1][0] - 1.0
            while (len(self._projectile_position_history) > 2 and
                   self._projectile_position_history[1][0] < floor):
                self._projectile_position_history.pop(0)
            return
        floor = min(float(state['cursor_time']) for state in states)
        while (len(self._projectile_position_history) > 2 and
               self._projectile_position_history[1][0] <= floor):
            self._projectile_position_history.pop(0)

    def _advance_projectiles(self, now):
        self._projectile_perf = {}
        self._projectile_scan_count = 0
        self._projectile_candidate_count = 0
        if self._projectiles is None:
            return False
        self._flush_pending_projectile_resolutions()
        previous = self._projectile_target_positions
        current = self._projectile_record_positions()
        if (len(self._projectiles) or self._projectile_visual_meta or
                self._projectile_is_authority()):
            self._sample_projectile_positions(now, current)
            self._prune_projectile_position_history()
        if not self._projectile_is_authority():
            self._projectile_target_positions = current
            return False
        self._projectile_previous_positions = previous
        self._projectile_current_positions = current
        self._projectile_frame_start = self._projectiles.now
        self._projectile_frame_end = max(
            self._projectile_frame_start, float(now))
        active = len(self._projectiles)
        sustainable = self._projectiles.sustainable_chord_budget(
            PROJECTILE_SUSTAIN_SECONDS)
        chord_budget = min(
            PROJECTILE_MAX_CHORDS_PER_FRAME,
            max(PROJECTILE_CHORDS_PER_FRAME, active * 2, sustainable))
        advance_start = _PROFILE_CLOCK()
        advanced = self._projectiles.advance(
            now, self._projectile_chord, self._projectile_terminal,
            maximum_chords=chord_budget)
        advance_seconds = max(0.0, _PROFILE_CLOCK() - advance_start)
        metrics = self._projectiles.last_advance_metrics()
        self._projectile_perf = {
            'active': metrics.get('active', active),
            'chords': metrics.get('chords', 0),
            'debt': metrics.get('debt_after', 0.0),
            'advance': advance_seconds,
            'terminals': metrics.get('terminals', 0),
            'scans': self._projectile_scan_count,
            'candidates': self._projectile_candidate_count,
        }
        self._prune_projectile_position_history()
        self._projectile_target_positions = current
        if now >= self._next_projectile_progress_time:
            self._next_projectile_progress_time = (
                now + PROJECTILE_PROGRESS_SECONDS)
            self._publish_projectile_progress()
        return advanced

    def _projectile_chord(self, state, start, end,
                          absolute_start, absolute_end):
        projectile_id = state.get('key')
        meta = self._projectile_meta.get(projectile_id)
        if meta is None:
            return {'reason': 'callback_error', 'fraction': 0.0}
        chord_length = math.sqrt(sum(
            (float(end[index]) - float(start[index])) ** 2
            for index in range(3)))
        if chord_length <= 0.000001:
            return None
        direction_tuple = tuple(
            (float(end[index]) - float(start[index])) / chord_length
            for index in range(3))
        direction = self._vector(direction_tuple)
        source_key = '%s:%s' % (
            meta['shooter_kind'], meta['shooter_id'])
        nearest_key = None
        nearest_collisions = None
        nearest_fraction = 1.0
        nearest_query = None
        broadphase_sq = PROJECTILE_BROADPHASE_RADIUS ** 2
        for key, record in tuple(self._records.items()):
            self._projectile_scan_count += 1
            if key == source_key or record.get('tombstone'):
                continue
            current_position = self._projectile_current_positions.get(key)
            if current_position is None:
                continue
            target_at_start = self._projectile_historic_position(
                key, absolute_start, current_position)
            target_at_end = self._projectile_historic_position(
                key, absolute_end, current_position)
            adjusted_start = tuple(
                float(start[index]) + float(current_position[index]) -
                float(target_at_start[index]) for index in range(3))
            adjusted_end = tuple(
                float(end[index]) + float(current_position[index]) -
                float(target_at_end[index]) for index in range(3))
            if not point_in_expanded_segment_bounds(
                    current_position, adjusted_start, adjusted_end,
                    PROJECTILE_BROADPHASE_RADIUS):
                continue
            if point_segment_distance_sq(
                    current_position, adjusted_start,
                    adjusted_end) > broadphase_sq:
                continue
            self._projectile_candidate_count += 1
            target = self._server_entity(record.get('engine_id'))
            if (target is None or not getattr(target, 'isStarted', False) or
                    not self._record_alive(record, target)):
                continue
            query_start = self._vector(adjusted_start)
            query_end = self._vector(adjusted_end)
            if record.get('local') and self._local_matrix is not None:
                collisions = collide_vehicle_at_matrix(
                    target, self._local_matrix, query_start, query_end,
                    self._runtime.math)
            else:
                collisions = target.collideSegmentExt(
                    query_start, query_end)
            if not collisions:
                continue
            collisions = tuple(collisions)
            nearest = min(collisions, key=lambda item: float(item.dist))
            query_length = (query_end - query_start).length
            if query_length <= 0.000001:
                continue
            fraction = max(
                0.0, min(1.0, float(nearest.dist) / query_length))
            if fraction < nearest_fraction:
                nearest_key = key
                nearest_collisions = collisions
                nearest_fraction = fraction
                nearest_query = (query_start, query_end)

        scene_end_tuple = lerp3(start, end, nearest_fraction)
        if self._projectile_destructible_context is not None:
            raise RuntimeError('nested projectile destructible context')
        self._projectile_destructible_context = projectile_id
        try:
            scene = self._resolve_shot_scene(
                self._vector(start), self._vector(scene_end_tuple), direction,
                self._projectile_shot(meta),
                penetration_factor=meta.get('penetration_factor'),
                initial_piercing_loss=meta.get('piercing_loss', 0.0),
                distance_offset=state.get('distance', 0.0))
        finally:
            self._projectile_destructible_context = None
        meta['piercing_loss'] = scene['piercing_loss']
        meta['penetration_factor'] = scene.get(
            'penetration_factor', meta.get('penetration_factor'))
        world_distance = scene['world_distance']
        cap_distance = chord_length * nearest_fraction
        world_blocks = (
            world_distance < 99999.0 and
            (nearest_key is None or
             bool(scene.get('stopped_by_destructible')) or
             cap_distance >
             world_distance + _SHOT_OCCLUSION_EPSILON))
        if world_blocks:
            fraction = max(
                0.0, min(1.0, world_distance / chord_length))
            self._projectile_terminal_data[projectile_id] = {
                'impact': lerp3(start, end, fraction),
                'target_key': None,
                'collisions': None,
                'query': None,
                'piercing_loss': meta['piercing_loss'],
                'penetration_factor': meta.get('penetration_factor'),
            }
            return {'reason': 'impact', 'fraction': fraction}
        if nearest_key is not None:
            self._projectile_terminal_data[projectile_id] = {
                'impact': lerp3(start, end, nearest_fraction),
                'target_key': nearest_key,
                'collisions': nearest_collisions,
                'query': nearest_query,
                'piercing_loss': meta['piercing_loss'],
                'penetration_factor': meta.get('penetration_factor'),
            }
            return {'reason': 'impact', 'fraction': nearest_fraction}
        return None

    def _projectile_shot(self, meta):
        source = self._projectile_source_entity(meta)
        descriptor = (getattr(source, 'typeDescriptor', None)
                      if source is not None else None)
        if descriptor is None:
            descriptor = self._projectile_source_descriptor(meta)
        if descriptor is None:
            return {}
        return self._descriptor_shot(
            descriptor, meta.get('shell_index'))

    def _projectile_source_descriptor(self, meta):
        descriptor = meta.get('source_descriptor')
        if descriptor is not None:
            return descriptor
        vehicle = meta.get('source_vehicle')
        if not vehicle:
            return None
        try:
            descriptor = self._resolve_descriptor(vehicle)
        except Exception:
            return None
        meta['source_descriptor'] = descriptor
        return descriptor

    def _projectile_source_entity(self, meta):
        key = '%s:%s' % (meta.get('shooter_kind'), meta.get('shooter_id'))
        record = self._records.get(key)
        if record is None:
            return None
        return self._server_entity(record.get('engine_id'))

    def _projectile_effect(self, record, damage, result, impact,
                           critical, hull_damage):
        target_kind = record.get('kind')
        if target_kind == 'human':
            target_kind = 'player'
        if target_kind not in ('player', 'bot'):
            raise RuntimeError('projectile target kind is invalid')
        effect = {
            'target_kind': target_kind,
            'target_id': int(record.get('network_id')),
            'damage': max(0, int(damage or 0)),
            'shot_result': max(0, min(2, int(result or 0))),
            'x': float(impact[0]), 'y': float(impact[1]),
            'z': float(impact[2]),
        }
        if isinstance(critical, dict):
            effect['critical'] = critical
            effect.update(self._critical_proposal_contract(
                record, critical, hull_damage))
        return effect

    def _projectile_direct_effect(self, meta, state, terminal_data):
        target_key = terminal_data.get('target_key')
        record = self._records.get(target_key)
        source = self._projectile_source_entity(meta)
        source_descriptor = (getattr(source, 'typeDescriptor', None)
                             if source is not None else None)
        if source_descriptor is None:
            source_descriptor = self._projectile_source_descriptor(meta)
        if record is None or source_descriptor is None:
            return None
        target = self._server_entity(record.get('engine_id'))
        collisions = terminal_data.get('collisions')
        query = terminal_data.get('query')
        if (target is None or not collisions or query is None or
                not self._record_alive(record, target)):
            return None
        factor = terminal_data.get('penetration_factor')
        damage, result = self._shell_damage(
            source_descriptor, collisions, state.get('distance', 0.0),
            shell_index=meta.get('shell_index'),
            pierce_loss=terminal_data.get('piercing_loss', 0.0),
            penetration_factor=factor,
            target_descriptor=getattr(target, 'typeDescriptor', None))
        hull_damage = damage
        damage, critical = self._critical_hit(
            target, source_descriptor, collisions,
            query[0], query[1], damage, result,
            int(getattr(source, 'id', meta.get('shooter_id', 0))),
            meta.get('shell_index'))
        return self._projectile_effect(
            record, damage, result, terminal_data['impact'],
            critical, hull_damage)

    def _projectile_splash_effects(self, meta, impact, direct_key):
        source = self._projectile_source_entity(meta)
        source_descriptor = (getattr(source, 'typeDescriptor', None)
                             if source is not None else None)
        if source_descriptor is None:
            source_descriptor = self._projectile_source_descriptor(meta)
        if source_descriptor is None:
            return []
        shot = self._descriptor_shot(
            source_descriptor, meta.get('shell_index'))
        radius = combat_rules.he_radius(shot)
        if radius <= 0.0:
            return []
        burst = self._vector(impact)
        legacy_shell = combat_rules.legacy_shot(shot).get('shell') or {}
        effects = []
        for key, record in tuple(self._records.items()):
            if key == direct_key or record.get('tombstone'):
                continue
            target = self._server_entity(record.get('engine_id'))
            if (target is None or target.typeDescriptor is None or
                    not getattr(target, 'isStarted', False) or
                    not self._record_alive(record, target)):
                continue
            position = _xyz(getattr(
                target, 'position', record.get('state', {})))
            delta = tuple(position[index] - impact[index]
                          for index in range(3))
            distance = math.sqrt(sum(value * value for value in delta))
            if distance > radius:
                continue
            aim = self._vector((
                position[0], position[1] + 1.0, position[2]))
            try:
                collisions = tuple(
                    target.collideSegmentExt(burst, aim) or ())
                nominal = combat_rules.he_nominal_armor(
                    collisions, target.typeDescriptor)
            except Exception:
                collisions = ()
                nominal = combat_rules.he_hull_armor(
                    target.typeDescriptor)
            damage = combat_rules.he_splash_damage(
                shot, nominal, distance / radius)
            if damage <= 0:
                continue
            hull_damage = damage
            damage, critical = critical_damage.propose_direct(
                target, combat_rules.collision_layers(collisions),
                burst, self._vector(position), damage, legacy_shell,
                int(getattr(source, 'id', meta.get('shooter_id', 0))),
                penetrated=False, by_explosion=True)
            effects.append(self._projectile_effect(
                record, damage, 2, impact, critical, hull_damage))
            if len(effects) >= 30:
                break
        return effects

    def _projectile_terminal(self, state, terminal):
        projectile_id = state.get('key')
        meta = self._projectile_meta.get(projectile_id)
        if meta is None:
            return False
        data = self._projectile_terminal_data.pop(projectile_id, None)
        reason = terminal.get('reason')
        outcome = ('impact' if reason == 'impact' and data is not None else
                   'miss' if reason == 'max_distance' else 'expired')
        impact = tuple(state.get('position') or meta.get('origin'))
        direct = None
        splash = []
        try:
            if outcome == 'impact':
                impact = tuple(data.get('impact', impact))
                direct = self._projectile_direct_effect(meta, state, data)
                if meta.get('is_he'):
                    splash = self._projectile_splash_effects(
                        meta, impact, data.get('target_key'))
        except Exception:
            # A malformed native collision/proposal cannot be allowed to
            # damage a different target. Retire the ledger entry without
            # effects so another authority never replays the same chord.
            outcome = 'expired'
            direct = None
            splash = []
        pending = {
            'state': state, 'outcome': outcome, 'impact': impact,
            'direct': direct, 'splash': splash,
        }
        meta['pending_resolution'] = pending
        return self._submit_projectile_resolution(meta)

    def _submit_projectile_resolution(self, meta):
        pending = meta.get('pending_resolution')
        if (pending is None or meta.get('progress_pending') is not None or
                not self._projectile_is_authority()):
            return False
        state = pending['state']
        elapsed_ms = max(
            int(meta.get('base_checked_ms', 0)),
            int(round(float(state.get('elapsed', 0.0)) * 1000.0)))
        sender = getattr(self.client, 'send_projectile_resolve', None)
        if not callable(sender):
            return False
        sent = sender(
            self._projectile_epoch, meta['projectile_id'],
            int(meta.get('base_checked_ms', 0)), pending['outcome'],
            elapsed_ms,
            (list(pending['impact'])
             if pending['outcome'] == 'impact' else None),
            pending['direct'],
            pending['splash'],
            checked_distance=float(state.get('distance', 0.0)),
            piercing_loss=float(meta.get('piercing_loss', 0.0)),
            penetration_factor=float(
                meta.get('penetration_factor', 1.0)),
            destructibles=[dict(value) for value in
                           meta.get('destructibles_pending', ())])
        if sent:
            meta['destructibles_pending'] = []
            meta['pending_resolution'] = None
            meta['awaiting_resolution'] = True
        return bool(sent)

    def _flush_pending_projectile_resolutions(self):
        if not self._projectile_is_authority():
            return False
        changed = False
        for meta in tuple(self._projectile_meta.values()):
            if meta.get('pending_resolution') is not None:
                changed = self._submit_projectile_resolution(meta) or changed
        return changed

    def _publish_projectile_progress(self):
        if not self._projectile_is_authority():
            return False
        sender = getattr(self.client, 'send_projectile_progress', None)
        if not callable(sender):
            return False
        cursors = []
        active_ids = set()
        for state in self._projectiles.snapshot():
            meta = self._projectile_meta.get(state.get('key'))
            if meta is None:
                continue
            active_ids.add(meta['projectile_id'])
            pending = meta.get('progress_pending')
            if pending is not None:
                cursors.append(dict(pending))
                continue
            base_checked = int(meta.get('base_checked_ms', 0))
            checked = max(
                base_checked,
                int(round(float(state.get('elapsed', 0.0)) * 1000.0)))
            cursors.append({
                'projectile_id': meta['projectile_id'],
                'base_checked_ms': base_checked,
                'checked_through_ms': min(
                    meta['max_time_ms'], checked),
                'checked_distance': float(state.get('distance', 0.0)),
                'piercing_loss': float(meta.get('piercing_loss', 0.0)),
                'penetration_factor': float(
                    meta.get('penetration_factor', 1.0)),
                'destructibles': [dict(value) for value in
                                  meta.get('destructibles_pending', ())],
            })
        # A projectile can reach its terminal while its preceding cursor is
        # still awaiting a canonical snapshot acknowledgement. Keep retrying
        # that exact CAS proposal even though the trajectory manager has
        # retired the projectile; resolution is submitted only after the
        # server echoes this base.
        for projectile_id, meta in tuple(self._projectile_meta.items()):
            pending = meta.get('progress_pending')
            if pending is not None and projectile_id not in active_ids:
                cursors.append(dict(pending))
        sent = False
        for index in range(0, len(cursors), 30):
            batch = cursors[index:index + 30]
            accepted = bool(sender(self._projectile_epoch, batch))
            if accepted:
                for cursor in batch:
                    meta = self._projectile_meta.get(
                        cursor['projectile_id'])
                    if meta is None:
                        continue
                    if meta.get('progress_pending') is None:
                        meta['progress_pending'] = dict(cursor)
                        meta['destructibles_pending'] = []
            sent = accepted or sent
        return sent

    def _authority_players(self):
        """Give local bot authority only real human world poses.

        A newly joined server player carries a formation placeholder until
        its first client pose reaches the server.  The authority already owns
        this client's render-frame pose, so replace its stale snapshot entry;
        omit other humans until their explicit ``world_pose`` sample arrives.
        """
        snapshot_players = (
            (self._last_snapshot or {}).get('players', ()) or ())
        if self.client is None:
            return list(snapshot_players)
        local_id = int(self.client.player_id)
        players = []
        local_found = False
        for raw in snapshot_players:
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            state = dict(raw)
            if int(state['id']) != local_id:
                if not bool(state.get('world_pose', False)):
                    continue
                players.append(state)
                continue
            local_found = True
            players.append(self._live_local_player_state(state))
        if not local_found:
            players.append(self._live_local_player_state(
                self._local_state()))
        return players

    def _live_local_player_state(self, state):
        """Overlay the copied local integrator on one protocol player row."""
        result = dict(state or {})
        position, yaw = self.local_pose()
        result.update({
            'id': int(self.client.player_id),
            'x': float(position[0]), 'y': float(position[1]),
            'z': float(position[2]), 'yaw': float(yaw),
            'speed': float(self._local_speed), 'world_pose': True,
        })
        health = self.local_health()
        if health is not None:
            result['health'] = int(health)
            result['alive'] = health > 0
        if self._sender is not None:
            result['aim_yaw'] = float(self._sender.aim_yaw)
            result['gun_pitch'] = float(self._sender.gun_pitch)
        return result

    def _frame(self):
        if self.state != 'running':
            return
        diagnostics = self._frame_diagnostics
        profiling = diagnostics is not None and diagnostics.enabled
        entry_wall = _PROFILE_CLOCK() if profiling else 0.0
        now = self._clock()
        raw_dt = (0.0 if self._last_frame_time is None else
                  now - self._last_frame_time)
        dt = max(0.0, min(raw_dt, 0.1))
        tick_dt = dt
        self._last_frame_time = now
        # Direction probes may recast through proved soft OBBs, but those
        # native queries share one hard frame budget across all 29 Bots.
        self._soft_static_recast_budget[0] = BOT_SOFT_RECAST_BUDGET
        frame_id = (diagnostics.begin(entry_wall, raw_dt)
                    if profiling else 0)
        stages = {}
        probes = dict((name, 0) for name in PROBE_KINDS)
        probe_durations = dict((name, 0.0) for name in PROBE_KINDS)
        pose_before = tuple(self._local_position)
        transitioned = False
        outgoing_messages = ()
        bot_count = 0
        projectile_perf = {}
        boundary = entry_wall
        try:
            self._flush_pending_bot_create(now)
            self._flush_pending_entities(now)
            self._drain_event_journal()
            self._maybe_send_battle_ready()
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['house'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._sync is not None:
                self._sync.advance(now)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['sync'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            self._tick_critical_states(dt)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['critical'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live:
                self._tick_drowning(dt, now)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['drown'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if (not self._battle_live and
                    self._prebattle_deadline is not None and
                    now >= self._prebattle_deadline):
                self._begin_battle()
                dt = 0.0
                transitioned = True
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['transition'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live:
                self._drive_local(dt)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['local'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live:
                self._update_target_outline(now)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['outline'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and self._bots is not None:
                self._advance_artillery_arcs(now)
                players = self._authority_players()
                probe_totals = getattr(self._bots, 'probe_totals', None)
                probe_duration_totals = getattr(
                    self._bots, 'probe_duration_totals', None)
                before_probes = None
                before_probe_durations = None
                if profiling and callable(probe_totals):
                    try:
                        before_probes = probe_totals()
                    except Exception:
                        before_probes = None
                if profiling and callable(probe_duration_totals):
                    try:
                        before_probe_durations = probe_duration_totals()
                    except Exception:
                        before_probe_durations = None
                outgoing_messages = self._bots.update(
                    dt, now, players=players)
                after_probes = None
                after_probe_durations = None
                if profiling and callable(probe_totals):
                    try:
                        after_probes = probe_totals()
                    except Exception:
                        after_probes = None
                if profiling and callable(probe_duration_totals):
                    try:
                        after_probe_durations = probe_duration_totals()
                    except Exception:
                        after_probe_durations = None
                if (isinstance(before_probes, (list, tuple)) and
                        isinstance(after_probes, (list, tuple)) and
                        len(before_probes) == len(PROBE_KINDS) and
                        len(after_probes) == len(PROBE_KINDS)):
                    try:
                        for index, name in enumerate(PROBE_KINDS):
                            probes[name] = max(
                                0, int(after_probes[index]) -
                                int(before_probes[index]))
                    except (TypeError, ValueError, OverflowError):
                        probes = dict((name, 0) for name in PROBE_KINDS)
                if (isinstance(before_probe_durations, (list, tuple)) and
                        isinstance(after_probe_durations, (list, tuple)) and
                        len(before_probe_durations) == len(PROBE_KINDS) and
                        len(after_probe_durations) == len(PROBE_KINDS)):
                    try:
                        for index, name in enumerate(PROBE_KINDS):
                            probe_durations[name] = max(
                                0.0, float(after_probe_durations[index]) -
                                float(before_probe_durations[index]))
                    except (TypeError, ValueError, OverflowError):
                        probe_durations = dict(
                            (name, 0.0) for name in PROBE_KINDS)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['bots_update'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and self._bots is not None:
                presentation_states = getattr(
                    self._bots, 'presentation_states', None)
                if not callable(presentation_states):
                    raise RuntimeError(
                        'authority bot presentation boundary is unavailable')
                # Authority poses are a render-frame surface.  LAN bot_state
                # is deliberately only a 30 Hz protocol publication; tying
                # the native filters to that message recreates visible steps
                # and also tempts transport code to drop strict combat_seq
                # proposals after they have already been formed.
                states = presentation_states()
                bot_count = len(states)
                self._apply_authority_bot_poses(states)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['bot_present'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and self._bots is not None:
                for outgoing in outgoing_messages:
                    if self._send_bot_message(outgoing):
                        self._resolve_bot_fire(outgoing)
            if (self._battle_live and
                    (self._projectile_is_authority() or
                     self._projectile_visual_meta)):
                self._advance_projectiles(now)
                projectile_perf = dict(self._projectile_perf)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['bot_events'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live:
                self._update_spotting(now)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['spot'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live:
                validate_lock = getattr(
                    self._runtime.compatibility,
                    'validate_target_lock', None)
                if not callable(validate_lock):
                    raise RuntimeError(
                        '#1513 target-lock lifecycle boundary is unavailable')
                validate_lock(self._avatar)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['lock'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
        except Exception as error:
            self._fail(error)
            return
        schedule_start = _PROFILE_CLOCK() if profiling else 0.0
        self._schedule(FRAME_SECONDS, self._frame)
        if profiling:
            schedule_end = _PROFILE_CLOCK()
            stages['schedule'] = max(0.0, schedule_end - schedule_start)
            camera_velocity = _xyz(self._local_camera_velocity)
            camera_speed = math.sqrt(
                camera_velocity[0] * camera_velocity[0] +
                camera_velocity[1] * camera_velocity[1] +
                camera_velocity[2] * camera_velocity[2])
            pose_step = _distance_2d(pose_before, self._local_position)
            authority = False
            is_authority = getattr(self._bots, 'is_authority', None)
            if callable(is_authority):
                try:
                    authority = bool(is_authority())
                except Exception:
                    authority = False
            diagnostics.finish(
                frame_id, entry_wall, tick_dt, dt, stages, probes, {
                    'round': (self._start_message or {}).get('round_id', '-'),
                    'map': (self._config or {}).get('map', '-'),
                    'phase': 'live' if self._battle_live else 'prebattle',
                    'role': 'authority' if authority else 'guest',
                    'bot_count': bot_count,
                    'outgoing_count': len(outgoing_messages),
                    'pose_step': pose_step,
                    'speed': float(self._local_speed),
                    'camera_speed': camera_speed,
                    'airborne': bool(self._local_airborne),
                    'grind': int(self._local_grind),
                    'transitioned': transitioned,
                }, probe_durations=probe_durations,
                projectile=projectile_perf)

    def _mutable_shot_ray(self):
        """Copy #1513's native gun ray before normalising or scattering it."""
        gun_rotator = getattr(self._avatar, 'gunRotator', None)
        get_shot = getattr(gun_rotator, 'getCurShotPosition', None)
        if not callable(get_shot):
            raise RuntimeError('#1513 gun shot-position provider is unavailable')
        native_start, native_direction = get_shot()
        start = self._vector(_xyz(native_start))
        direction = self._vector(_xyz(native_direction))
        direction.normalise()
        if direction.length <= 0.0:
            raise RuntimeError('#1513 gun shot direction is empty')
        return start, direction

    def _native_dispersion_angle(self):
        """Read the exact angle currently presented by #1513's gun rotator.

        The pinned Avatar already computes movement, traverse, turret and
        post-shot bloom in ``getOwnVehicleShotDispersionAngle``.  Replacing
        that method with the 0.8.2 shadow state produced a second, divergent
        reticle.  The read-only rotator property is the single source shared
        by the stock marker and the trusted-client shot ray.
        """
        gun_rotator = getattr(self._avatar, 'gunRotator', None)
        if gun_rotator is None:
            raise RuntimeError('#1513 gun rotator is unavailable')
        try:
            angle = float(gun_rotator.dispersionAngle)
        except (AttributeError, TypeError, ValueError):
            raise RuntimeError(
                '#1513 gun rotator dispersion angle is unavailable')
        if math.isnan(angle) or math.isinf(angle) or angle < 0.0:
            raise RuntimeError('#1513 gun rotator dispersion angle is invalid')
        return angle

    def _apply_native_shot_bloom(self):
        """Enter #1513's own post-shot convergence state exactly once.

        In a retail battle the cell-owned shot confirmation advances the
        Avatar aiming state.  Our trusted local cell confirms the same shot,
        so invoke the pinned producer with ``withShot=1``.  Subsequent native
        gun-rotator ticks use ``withShot=0`` and perform the stock exponential
        convergence; no parallel reticle state is written here.
        """
        gun_rotator = getattr(self._avatar, 'gunRotator', None)
        if gun_rotator is None:
            raise RuntimeError('#1513 gun rotator is unavailable for bloom')
        try:
            turret_speed = float(gun_rotator.turretRotationSpeed)
        except (AttributeError, TypeError, ValueError):
            raise RuntimeError(
                '#1513 turret rotation speed is unavailable for bloom')
        producer = getattr(
            self._avatar, 'getOwnVehicleShotDispersionAngle', None)
        if not callable(producer):
            raise RuntimeError('#1513 dispersion producer is unavailable')
        angles = producer(turret_speed, 1)
        if not isinstance(angles, list) or len(angles) != 2:
            raise RuntimeError(
                '#1513 shot-bloom producer returned an invalid shape')
        for value in angles:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise RuntimeError(
                    '#1513 shot-bloom producer returned a non-number')
            if math.isnan(value) or math.isinf(value) or value < 0.0:
                raise RuntimeError(
                    '#1513 shot-bloom producer returned an invalid angle')
        return angles

    def _sync_local_server_marker(self):
        """Echo the trusted client marker into #1513's server-aim channel.

        The 0.8.2 offline battle refreshes both gun-marker channels from the
        same current dispersion angle.  #1513 shows a second marker when the
        user's server-aim setting is enabled, but a real cell normally feeds
        that marker through ``VehicleGunRotator.setShotPosition``.  The local
        cell has no independent simulation, so echo the trusted native ray and
        angle instead of leaving the second marker at its initial size.  Keep
        PREBATTLE on the stock frozen boundary and begin this echo only after
        the native BATTLE transition starts the rotator.
        """
        if not self._battle_live:
            return False
        gun_rotator = getattr(self._avatar, 'gunRotator', None)
        if (gun_rotator is None or
                not bool(getattr(gun_rotator, 'showServerMarker', False))):
            return False
        get_shot = getattr(gun_rotator, 'getCurShotPosition', None)
        update_marker = getattr(self._avatar, 'updateGunMarker', None)
        if not callable(get_shot) or not callable(update_marker):
            raise RuntimeError(
                '#1513 server gun-marker boundary is unavailable')
        shot_position, shot_vector = get_shot()
        update_marker(
            self._server.vehicle_id, shot_position, shot_vector,
            self._native_dispersion_angle())
        return True

    def _update_target_outline(self, now):
        if now < self._next_outline_time:
            return
        self._next_outline_time = now + 0.125
        if self._remote_factory is None:
            self._clear_target_outline()
            return
        if not any(
                record.get('ready') and not record.get('local') and
                not record.get('tombstone')
                for record in self._records.values()):
            self._clear_target_outline()
            return
        start, direction = self._mutable_shot_ray()
        end = start + direction.scale(5000.0)
        world_distance = 5000.0
        collision = self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID, start, end, 128)
        if collision is not None:
            world_distance = (collision[0] - start).length
        nearest_id = None
        nearest_distance = 5000.0
        for record in self._records.values():
            if (record.get('local') or not record.get('ready') or
                    record.get('tombstone') or
                    not record.get('spot_visible', True)):
                continue
            vehicle = self._server_entity(record['engine_id'])
            if (vehicle is None or not vehicle.isAlive() or
                    vehicle.bw_entity is None):
                continue
            collisions = vehicle.collideSegmentExt(start, end)
            if not collisions:
                continue
            distance = min(float(item.dist) for item in collisions)
            if distance < nearest_distance:
                nearest_id = record['engine_id']
                nearest_distance = distance
        if nearest_id is None or nearest_distance > world_distance + 0.5:
            nearest_id = None
        if nearest_id == self._outlined_engine_id:
            return
        self._clear_target_outline()
        if nearest_id is None:
            return
        vehicle = self._remote_factory.get(nearest_id)
        if vehicle is None or vehicle.bw_entity is None:
            raise RuntimeError('outlined remote vehicle has no visual entity')
        color = 2 if int(vehicle.team) == int(self.client.team) else 1
        add_edge = getattr(
            self._runtime.bigworld, 'wgAddEdgeDetectEntity', None)
        if not callable(add_edge):
            raise RuntimeError('#1513 edge-detect add boundary is unavailable')
        add_edge(vehicle.bw_entity, color, 0, False)
        set_candidate = getattr(
            self._runtime.compatibility, 'set_target_lock_candidate', None)
        if not callable(set_candidate):
            raise RuntimeError(
                '#1513 target-lock candidate boundary is unavailable')
        set_candidate(vehicle)
        self._outlined_engine_id = nearest_id

    def _clear_target_outline(self):
        if self._outlined_engine_id is None:
            return
        vehicle = (self._remote_factory.get(self._outlined_engine_id)
                   if self._remote_factory is not None else None)
        self._outlined_engine_id = None
        set_candidate = getattr(
            self._runtime.compatibility, 'set_target_lock_candidate', None)
        if not callable(set_candidate):
            raise RuntimeError(
                '#1513 target-lock candidate boundary is unavailable')
        set_candidate(None)
        if vehicle is None or vehicle.bw_entity is None:
            return
        remove_edge = getattr(
            self._runtime.bigworld, 'wgDelEdgeDetectEntity', None)
        if not callable(remove_edge):
            raise RuntimeError(
                '#1513 edge-detect remove boundary is unavailable')
        remove_edge(vehicle.bw_entity)

    def _observe_local_vehicle(self, message, now):
        """Feed authority visibility into the native #1513 Sixth Sense HUD."""
        if (self._sixth_sense is None or
                message.get('type') != 'bot_observation'):
            return False
        local_id = int(self.client.player_id)
        local_team = int(self.client.team)
        visible = any(
            contact.get('target_kind') == 'human' and
            int(contact.get('target_id', -1)) == local_id and
            int(contact.get('observing_team', 0)) != local_team and
            bool(contact.get('visible'))
            for contact in (message.get('contacts') or ())
            if isinstance(contact, dict))
        self._sixth_sense.observe(visible, now)
        return visible

    def _maybe_send_battle_ready(self):
        """Open the shared countdown once all human vehicles have entered.

        The mature 0.8.2 flow deliberately materializes full bot models during
        the 15-second countdown.  Making all 29 bot entities part of the LAN
        loading barrier leaves the stock HUD at 00:00 for many seconds and
        only starts the countdown after the line-up is already complete.
        Humans and the authority manifest are the shared state boundary; the
        server separately refuses ``battle_live`` until that manifest arrives.
        A server-requested native destructible map is also a hard boundary:
        a transport refusal is retried next frame, while invalid baked data
        fails the battle.
        """
        if self._ready_sent or self._battle_live:
            return False
        expected_players = len(self._start_message.get('players') or ())
        player_records = [record for record in self._records.values()
                          if record.get('kind') == 'player' and
                          not record.get('tombstone')]
        if (len(player_records) != expected_players or
                any(not record.get('ready') for record in player_records)):
            return False
        ready = getattr(self.client, 'send_battle_ready', None)
        if not callable(ready):
            return False
        if (self._start_message.get('need_destructible_map') and
                not self._maybe_donate_destructible_map()):
            return False
        bases = getattr(self._spawn_planner, 'bases', None)
        if not ready(bases):
            raise RuntimeError('LAN server did not accept battle readiness')
        self._ready_sent = True
        return True

    def _maybe_donate_destructible_map(self):
        """Send the map's complete baked destructible identities.

        Invalid baked data fails the battle; only a transport refusal stays
        retryable on the next frame.
        """
        if not self._start_message.get('need_destructible_map'):
            return False
        sender = getattr(self.client, 'send_destructible_map', None)
        if not callable(sender) or self._destructibles is None:
            raise RuntimeError(
                'LAN server requires a destructible map donation this '
                'client cannot send')
        donation = self._destructibles.donation_rows_1513()
        if not donation:
            raise RuntimeError(
                'destructible map donation requires the baked catalog')
        rows = donation.pop('instances')
        part_size = 1000
        parts = max(1, (len(rows) + part_size - 1) // part_size)
        for part in range(parts):
            payload = dict(donation)
            payload['part'] = part
            payload['parts'] = parts
            payload['instances'] = rows[part * part_size:
                                        (part + 1) * part_size]
            if not sender(self._start_message.get('map'), payload):
                return False
        return True

    def _ground_pitch(self, position, yaw, descriptor=None):
        """Sample the copied 0.8.2 four-point suspension pose."""
        length = 5.0
        width = 3.0
        try:
            hit_tester = _field(_field(descriptor, 'hull', {}),
                                'hitTester', None)
            bbox = getattr(hit_tester, 'bbox', None)
            length = max(3.0, abs(float(bbox[0][2])) +
                         abs(float(bbox[1][2])))
            width = max(2.0, abs(float(bbox[0][0])) +
                        abs(float(bbox[1][0])))
        except (TypeError, ValueError, IndexError, AttributeError):
            pass
        half_length = length * 0.5
        half_width = width * 0.5
        sin_yaw, cos_yaw = math.sin(yaw), math.cos(yaw)
        front_y = self._ground_y(
            position[0] + sin_yaw * half_length,
            position[2] + cos_yaw * half_length, position[1])
        rear_y = self._ground_y(
            position[0] - sin_yaw * half_length,
            position[2] - cos_yaw * half_length, position[1])
        right_y = self._ground_y(
            position[0] + cos_yaw * half_width,
            position[2] - sin_yaw * half_width, position[1])
        left_y = self._ground_y(
            position[0] - cos_yaw * half_width,
            position[2] + sin_yaw * half_width, position[1])
        if None in (front_y, rear_y, right_y, left_y):
            self._local_downhill = (0.0, 0.0, 0.0)
            self._local_slope_tangent = 0.0
            return self._local_pitch
        pitch = -math.atan2(front_y - rear_y, length) * 0.9
        roll = math.atan2(right_y - left_y, width) * 0.9
        tilt = math.sqrt(pitch * pitch + roll * roll)
        if tilt > 0.61:
            scale = 0.61 / tilt
            pitch *= scale
            roll *= scale
        self._local_pitch += (pitch - self._local_pitch) * 0.5
        self._local_roll += (roll - self._local_roll) * 0.5
        gradient_forward = (rear_y - front_y) / length
        gradient_right = (left_y - right_y) / width
        slope_tangent = math.sqrt(
            gradient_forward * gradient_forward +
            gradient_right * gradient_right)
        downhill_x = (gradient_forward * sin_yaw +
                      gradient_right * cos_yaw)
        downhill_z = (gradient_forward * cos_yaw -
                      gradient_right * sin_yaw)
        downhill_length = math.sqrt(
            downhill_x * downhill_x + downhill_z * downhill_z)
        if downhill_length > 0.001:
            downhill_x /= downhill_length
            downhill_z /= downhill_length
        else:
            downhill_x = downhill_z = 0.0
        self._local_downhill = (downhill_x, 0.0, downhill_z)
        self._local_slope_tangent = slope_tangent
        return self._local_pitch

    def _drive_pitch(self, position, yaw):
        """Copy the 0.8.2 close-range drive slope probe exactly.

        This is deliberately separate from the four-point visual hull pose.
        The drive law skips bridge decks above the hull and clamps walls and
        cliff faces before their gradient reaches longitudinal physics.
        """
        sine, cosine = math.sin(yaw), math.cos(yaw)
        distance = 2.0
        wall_rise = distance * 1.43

        def ground_y(x, z):
            start_y = position[1] + 15.0
            for unused in range(3):
                try:
                    collision = self._runtime.bigworld.wg_collideSegment(
                        self._avatar.spaceID,
                        self._vector((x, start_y, z)),
                        self._vector((x, position[1] - 60.0, z)), 128)
                except Exception:
                    return None
                if collision is None:
                    return None
                value = float(collision[0].y)
                if value > position[1] + 3.5:
                    start_y = value - 0.5
                    continue
                return value
            return None

        front = ground_y(
            position[0] + sine * distance,
            position[2] + cosine * distance)
        rear = ground_y(
            position[0] - sine * distance,
            position[2] - cosine * distance)
        if front is None or rear is None:
            return 0.0
        front_delta = max(
            -wall_rise, min(wall_rise, front - position[1]))
        rear_delta = max(
            -wall_rise, min(wall_rise, rear - position[1]))
        pitch = -math.atan2(
            front_delta - rear_delta, 2.0 * distance)
        return max(-0.96, min(0.96, pitch))

    def _smoothed_drive_pitch(self, position, yaw):
        raw = self._drive_pitch(position, yaw)
        history = self._local_drive_pitch_history
        if history is None:
            history = [raw] * 5
            self._local_drive_pitch_history = history
        history.append(raw)
        del history[:-5]
        median = sorted(history)[2]
        previous = self._local_smooth_drive_pitch
        pitch = previous + (median - previous) * 0.5
        self._local_smooth_drive_pitch = pitch
        self._local_last_pitch = pitch
        return pitch

    def _motion_is_clear(self, entity, position, yaw, speed, dt,
                         allow_crush_drive=False):
        """Thin tuple-to-Vector adapter around the copied 0.8.2 probe."""
        self._local_motion_soft_block = False
        self._local_motion_cap_crushed = False
        kinetic_speed = None
        if allow_crush_drive and not self._local_airborne:
            params = self._local_physics or vehicle_physics.derive_params(
                entity.typeDescriptor)
            limit_name = 'speedBwd' if speed < 0.0 else 'speedFwd'
            kinetic_speed = (-float(params[limit_name]) if speed < 0.0 else
                             float(params[limit_name]))
        world_status = world_collision.check_horizontal_collision(
            self._runtime.bigworld, self._runtime.math,
            self._avatar.spaceID, self._vector(position), yaw, speed,
            entity.typeDescriptor, self._local_airborne, dt, True,
            bool(kinetic_speed is not None), kinetic_speed)
        if isinstance(world_status, bool):
            world_status = 'hard' if world_status else 'clear'
        if world_status == 'hard':
            if (self._destructibles is not None and
                    self._destructibles._catalog_pending_at_hull(
                        self._vector(position), yaw, speed,
                        entity.typeDescriptor, self._clock(), dt)):
                self._local_motion_soft_block = True
            return False
        if self._destructibles is None:
            return world_status == 'clear'
        detail = self._destructibles._catalog_motion_blocked(
            self._avatar.spaceID, self._vector(position), yaw,
            speed, entity.typeDescriptor, self._clock(),
            dt=dt, kinetic_speed=kinetic_speed,
            return_detail=True,
            kinetic_commit=bool(kinetic_speed is not None))
        # Keep injected legacy test/adaptor seams fail-closed.  Production's
        # exact #1513 sensor always returns the typed receipt above.
        if isinstance(detail, bool):
            detail = {'status': 'hard' if detail else 'clear'}
        elif isinstance(detail, str):
            detail = {'status': detail}
        if not isinstance(detail, dict):
            raise RuntimeError(
                'local motion resolver detail is unavailable')
        status = detail.get('status')
        if status not in ('clear', 'crushed', 'soft', 'hard', 'approach'):
            raise RuntimeError(
                'local motion resolver returned an invalid status')
        if status == 'hard':
            return False
        used_kinetic_speed = bool(detail.get('used_kinetic_speed', False))
        accepted_now = bool(detail.get('accepted_now', False))
        if used_kinetic_speed and not (
                accepted_now and status == 'crushed' and
                detail.get('token')):
            raise RuntimeError(
                'local cap-crush receipt is inconsistent')
        if accepted_now and status in ('clear', 'approach', 'soft'):
            raise RuntimeError(
                'local contact receipt is inconsistent')
        if status == 'approach':
            status = 'clear'
        if accepted_now and used_kinetic_speed:
            self._local_motion_cap_crushed = True
            return False
        if status == 'soft':
            self._local_motion_soft_block = True
        return status in ('clear', 'crushed')

    def _resolve_bot_motion(self, bot_id, position, yaw, speed,
                            descriptor, dt, now):
        """Commit-side Bot contact: static world first, then exact catalog."""
        pos = self._vector(position)
        bot_state = getattr(self._bots, 'states', {}).get(int(bot_id), {})
        airborne = bool(bot_state.get('airborne', False))
        movement_dir = int(_number(bot_state.get('movement_dir')))
        rotation_dir = int(_number(bot_state.get('rotation_dir')))
        turn_speed = _number(getattr(
            self._bots, '_turn_speeds', {}).get(int(bot_id), 0.0))
        # The staggered planner may reuse a typed, read-only proof only when
        # the current exact hull sweep is still contained by its 3x3 rays.
        # A generic path-clear answer is not sufficient: its lane geometry is
        # deliberately wider and can miss a narrow pillar at the real hull edge.
        receipt_reusable = getattr(
            self._bots, 'motion_world_receipt_reusable', None)
        travel_yaw = (float(yaw) if speed >= 0.0 else
                      float(yaw) + math.pi)
        if (self._destructibles is not None and not airborne and
                movement_dir * float(speed) > 0.0 and rotation_dir == 0 and
                abs(turn_speed) <= 0.01 and callable(receipt_reusable) and
                receipt_reusable(
                    bot_id, position, travel_yaw, speed, now, dt) and
                not self._destructibles._catalog_hull_contact(
                    pos, yaw, speed, descriptor, dt)):
            return 'clear'
        allow_crush_drive = (
            not airborne and
            movement_dir * float(speed) > 0.0)
        kinetic_speed = None
        if allow_crush_drive:
            params = vehicle_physics.derive_params(descriptor)
            limit_name = 'speedBwd' if speed < 0.0 else 'speedFwd'
            kinetic_speed = (-float(params[limit_name]) if speed < 0.0 else
                             float(params[limit_name]))
        world_status = world_collision.check_horizontal_collision(
            self._runtime.bigworld, self._runtime.math,
            self._avatar.spaceID, pos, yaw, speed, descriptor, airborne, dt,
            True, allow_crush_drive, kinetic_speed)
        if isinstance(world_status, bool):
            world_status = 'hard' if world_status else 'clear'
        if world_status == 'hard':
            if (self._destructibles is not None and
                    self._destructibles._catalog_pending_at_hull(
                        pos, yaw, speed, descriptor, now, dt)):
                return 'soft'
            return 'hard'
        if self._destructibles is None:
            return 'clear' if world_status == 'clear' else 'hard'
        if airborne:
            return 'clear' if world_status == 'clear' else 'hard'
        # A native kinetic result is only a forward candidate.  Always hand it
        # to the catalog's exact contact seam; its ``approach`` result keeps a
        # nearby but non-contact prop clear without granting a destroy.
        detail = self._destructibles._catalog_motion_blocked(
            self._avatar.spaceID, pos, yaw, speed, descriptor, now,
            dt=dt, kinetic_speed=kinetic_speed, return_detail=True,
            kinetic_commit=allow_crush_drive)
        if isinstance(detail, bool):
            detail = {'status': 'hard' if detail else 'clear'}
        elif isinstance(detail, str):
            detail = {'status': detail}
        if not isinstance(detail, dict):
            raise RuntimeError('bot motion resolver detail is unavailable')
        status = detail.get('status')
        if status not in ('clear', 'crushed', 'soft', 'hard', 'approach'):
            raise RuntimeError(
                'bot motion resolver returned an invalid status')
        if status == 'hard':
            return 'hard'
        used_kinetic_speed = bool(detail.get('used_kinetic_speed', False))
        accepted_now = bool(detail.get('accepted_now', False))
        if used_kinetic_speed and not (
                accepted_now and status == 'crushed' and
                detail.get('token')):
            raise RuntimeError('bot cap-crush receipt is inconsistent')
        if accepted_now and status in ('clear', 'approach', 'soft'):
            raise RuntimeError('bot contact receipt is inconsistent')
        if status == 'approach':
            return 'clear'
        if accepted_now and used_kinetic_speed:
            return 'cap_crushed'
        return status

    @staticmethod
    def _collision_shape(descriptor):
        """Return the current 0.8.2 chassis hit-tester body."""
        return tank_collision.chassis_shape(descriptor)

    def _contact_tanks(self):
        """Return current non-local chassis bodies for 0.8.2 contact physics."""
        result = []
        bot_states = getattr(self._bots, 'states', {}) if self._bots else {}
        for record in self._records.values():
            if (record.get('local') or record.get('tombstone') or
                    not record.get('ready')):
                continue
            state = record.get('state') or {}
            if record.get('kind') == 'bot':
                state = bot_states.get(record.get('network_id'), state)
            alive = bool(state.get('alive', True))
            remote = self._server_entity(record['engine_id'])
            descriptor = getattr(remote, 'typeDescriptor', None)
            yaw = _number(state.get('yaw'))
            speed = _number(state.get('speed')) if alive else 0.0
            mass = state.get('mass')
            if mass is None and descriptor is not None:
                mass = vehicle_physics.derive_params(descriptor).get('mass')
            shape = state.get('collision_shape')
            if shape is None:
                shape = self._collision_shape(descriptor)
            result.append({
                'id': 1000000 + int(record.get('engine_id', 0)),
                'alive': alive,
                'x': _number(state.get('x')),
                'y': _number(state.get('y')),
                'z': _number(state.get('z')),
                'yaw': yaw,
                'mass': _number(mass, 25000.0),
                'shape': shape,
                'vx': math.sin(yaw) * speed,
                'vz': math.cos(yaw) * speed,
            })
        return result

    def _resolve_local_tank_contacts(self, entity, position, yaw, dt):
        """Apply chassis OBB separation without pushing a tank into walls."""
        others = self._contact_tanks()
        own_mass = _number(
            (self._local_physics or {}).get('mass'), 25000.0)
        own = {
            'id': -1,
            'alive': True,
            'x': position[0], 'y': position[1], 'z': position[2],
            'yaw': yaw, 'mass': own_mass,
            'shape': self._collision_shape(entity.typeDescriptor),
            'vx': math.sin(yaw) * self._local_speed + self._local_push_x,
            'vz': math.cos(yaw) * self._local_speed + self._local_push_z,
        }
        contact = tank_collision.resolve_tank(own, others)
        delta_x, delta_z = contact['delta_velocity']
        forward_impulse = (delta_x * math.sin(yaw) +
                           delta_z * math.cos(yaw))
        applied_forward = 0.0
        if forward_impulse * self._local_speed < 0.0:
            applied_forward = (
                -self._local_speed if
                abs(forward_impulse) >= abs(self._local_speed)
                else forward_impulse)
            self._local_speed += applied_forward
        push_x = (self._local_push_x + delta_x -
                  applied_forward * math.sin(yaw))
        push_z = (self._local_push_z + delta_z -
                  applied_forward * math.cos(yaw))
        correction_x, correction_z = contact['correction']
        move_x = correction_x + push_x * dt
        move_z = correction_z + push_z * dt
        distance = math.sqrt(move_x * move_x + move_z * move_z)
        if distance > 0.0001:
            contact_yaw = math.atan2(move_x, move_z)
            contact_speed = distance / max(float(dt), 1.0 / 120.0)
            candidate = (position[0] + move_x, position[1],
                         position[2] + move_z)
            if (not self._motion_is_clear(
                    entity, position, contact_yaw, contact_speed, dt) or
                    not self._baked_pose_safe(candidate)):
                push_x = 0.0
                push_z = 0.0
            else:
                position = candidate
        self._local_push_x = push_x * 0.90
        self._local_push_z = push_z * 0.90
        return position

    def _terrain_support(self, position, yaw, descriptor=None):
        """Copy 0.8.2 front/centre/back support and CoM ground probes."""
        half_length = 2.5
        try:
            hit_tester = _field(
                _field(descriptor, 'hull', {}), 'hitTester', None)
            bbox = getattr(hit_tester, 'bbox', None)
            half_length = max(1.5, abs(float(bbox[1][2])))
        except (TypeError, ValueError, IndexError, AttributeError):
            pass
        sine, cosine = math.sin(yaw), math.cos(yaw)
        highest = None
        centre = None
        for distance in (half_length, 0.0, -half_length):
            x = position[0] + sine * distance
            z = position[2] + cosine * distance
            try:
                hit = self._runtime.bigworld.wg_collideSegment(
                    self._avatar.spaceID,
                    self._vector((x, position[1] + 2.0, z)),
                    self._vector((x, -1000.0, z)), 128)
            except Exception:
                hit = None
            if hit is None:
                continue
            value = float(hit[0].y)
            if highest is None or value > highest:
                highest = value
            if distance == 0.0:
                centre = value
        return highest, centre

    def _apply_fall_damage(self, entity, impact_speed):
        """Apply the exact copied fall-damage function through native HP."""
        maximum = max(1, int(getattr(
            entity.typeDescriptor, 'maxHealth', getattr(entity, 'health', 1))))
        damage = vehicle_physics.fall_damage(maximum, impact_speed)
        if damage <= 0:
            return 0
        local_record = self._records.get('player:%s' % self.client.player_id)
        if local_record is None:
            return 0
        state = dict(local_record.get('state') or {})
        state['health'] = max(0, int(getattr(entity, 'health', maximum)) - damage)
        state['alive'] = state['health'] > 0
        local_record['state'] = state
        reason = self._attack_reason('WORLD_COLLISION', 3)
        self._queue_local_damage_report(
            reason=reason, attribute_attacker=False)
        self._apply_health(local_record, state, 0, reason)
        return damage

    def _apply_landing_impact(self, entity, vertical_speed):
        """Copy combined vertical/lateral impact and retained landing skid."""
        lateral_x, lateral_z = self._local_air_lateral
        lateral_speed = math.sqrt(
            lateral_x * lateral_x + lateral_z * lateral_z)
        if lateral_speed > 0.01:
            self._local_slide_speed = max(
                self._local_slide_speed, lateral_speed)
        self._local_air_lateral = (0.0, 0.0)
        impact_speed = math.sqrt(
            vertical_speed * vertical_speed +
            lateral_speed * lateral_speed)
        return self._apply_fall_damage(entity, impact_speed)

    def _update_vertical_motion(self, entity, position, yaw, dt):
        """Copy vertical motion while rejecting false raised support."""
        self._local_support_rise_blocked = False
        highest, centre = self._terrain_support(
            position, yaw, entity.typeDescriptor)
        ground = centre if centre is not None else highest
        if ground is not None:
            snap_gap = max(
                0.8, min(2.5, abs(self._local_speed) * dt * 2.0 + 0.6))
            max_climb = max(0.6, abs(self._local_speed) * dt * 2.5)
            com_gap = snap_gap if centre is None else position[1] - centre
            land_y = ground if centre is None else centre
            if not self._local_fall_armed:
                position = (position[0], land_y, position[2])
                self._local_vertical_speed = 0.0
                self._local_airborne = False
                self._local_fall_armed = True
            elif tank_collision.support_rise_is_obstacle(
                    position[1], centre, max_climb):
                # Horizontal integration put the hull partly inside a wagon,
                # roof, or large prop and the centre ray hit its top. Treat the
                # rise as the hard obstacle it is; never lift the chassis onto
                # that surface. The caller applies the existing hard-wall
                # speed response after restoring this tick's starting pose.
                tick_pose = getattr(self, '_local_support_tick_pose', None)
                if tick_pose is not None:
                    position = tuple(tick_pose)
                self._local_vertical_speed = 0.0
                self._local_airborne = False
                self._local_support_rise_blocked = True
                return position
            elif (position[1] <= ground or
                  (com_gap <= snap_gap and not self._local_airborne)):
                if self._local_airborne and self._local_vertical_speed < 0.0:
                    self._apply_landing_impact(
                        entity, abs(self._local_vertical_speed))
                if position[1] < ground:
                    rise = ground - position[1]
                    next_y = position[1] + min(rise, max_climb)
                else:
                    next_y = position[1] + (
                        ground - position[1]) * min(1.0, dt * 15.0)
                    next_y = min(next_y, ground + 0.12)
                position = (position[0], next_y, position[2])
                self._local_vertical_speed = 0.0
                self._local_airborne = False
                self._local_fall_armed = True
            else:
                if not self._local_airborne:
                    self._local_vertical_speed = (
                        self._local_speed * math.sin(-self._local_last_pitch)
                        if self._local_last_pitch < 0.0 else 0.0)
                self._local_airborne = True
                substeps = min(8, max(
                    1, int(abs(self._local_vertical_speed * dt) / 0.5) + 1))
                sub_dt = dt / float(substeps)
                next_y = position[1]
                for unused_step in range(substeps):
                    self._local_vertical_speed -= (
                        vehicle_physics.GRAVITY * sub_dt)
                    next_y += self._local_vertical_speed * sub_dt
                    if next_y <= land_y:
                        next_y = land_y
                        self._apply_landing_impact(
                            entity, abs(self._local_vertical_speed))
                        self._local_vertical_speed = 0.0
                        self._local_airborne = False
                        self._local_fall_armed = True
                        break
                position = (position[0], next_y, position[2])
        elif self._local_fall_armed:
            self._local_airborne = True
            self._local_vertical_speed -= vehicle_physics.GRAVITY * dt
            position = (position[0],
                        position[1] + self._local_vertical_speed * dt,
                        position[2])
        else:
            # The first streamed terrain hit owns spawn placement.  Never turn
            # the temporary y=100 fallback into a damaging free fall.
            self._local_vertical_speed = 0.0
            self._local_airborne = False
        return position

    def _apply_slope_slide(self, position, yaw, dt, entity=None):
        """Copy 0.8.2 cross-heading slope slip and airborne carry."""
        if self._local_airborne:
            self._local_slide_speed = 0.0
            lateral_x, lateral_z = self._local_air_lateral
            if abs(lateral_x) > 0.0001 or abs(lateral_z) > 0.0001:
                next_position = (
                    position[0] + lateral_x * dt, position[1],
                    position[2] + lateral_z * dt)
                lateral_speed = math.sqrt(
                    lateral_x * lateral_x + lateral_z * lateral_z)
                lateral_yaw = math.atan2(lateral_x, lateral_z)
                if (entity is None or self._motion_is_clear(
                        entity, position, lateral_yaw, lateral_speed, dt)):
                    position = next_position
                self._local_air_lateral = (
                    lateral_x * 0.995, lateral_z * 0.995)
            return position
        self._local_slide_speed = vehicle_physics.slope_slide_speed(
            self._local_slide_speed, self._local_slope_tangent, dt)
        cross_x, cross_z = math.cos(yaw), -math.sin(yaw)
        slide_dot = (self._local_downhill[0] * cross_x +
                     self._local_downhill[2] * cross_z)
        slide_x, slide_z = cross_x * slide_dot, cross_z * slide_dot
        self._local_air_lateral = (
            slide_x * self._local_slide_speed,
            slide_z * self._local_slide_speed)
        if (self._local_slide_speed <= 0.01 or
                (abs(slide_x) <= 0.0001 and abs(slide_z) <= 0.0001)):
            return position
        next_x = position[0] + slide_x * self._local_slide_speed * dt
        next_z = position[2] + slide_z * self._local_slide_speed * dt
        ground = self._ground_y(next_x, next_z, position[1])
        if ground is None or position[1] - ground >= 4.0:
            return position
        lateral_speed = abs(slide_dot) * self._local_slide_speed
        lateral_yaw = math.atan2(slide_x, slide_z)
        if (entity is not None and not self._motion_is_clear(
                entity, position, lateral_yaw, lateral_speed, dt)):
            if not self._local_motion_soft_block:
                self._local_slide_speed = 0.0
                self._local_air_lateral = (0.0, 0.0)
            return position
        delta_y = max(-0.35, min(0.35, ground - position[1]))
        self._local_vertical_speed = 0.0
        self._local_airborne = False
        return (next_x, position[1] + delta_y, next_z)

    def _drive_local(self, dt):
        if self._sender is None or self._server is None:
            return
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return
        is_alive = getattr(entity, 'isAlive', None)
        stopped = (self._battle_result is not None or
                   (callable(is_alive) and not is_alive()) or
                   (not callable(is_alive) and
                    (_number(getattr(entity, 'health', 0.0)) <= 0.0 or
                     not bool(getattr(entity, 'isCrewActive', True)))) or
                   self._drown_level == 2)
        if stopped:
            dt = max(0.0, min(float(dt), 0.1))
            self._local_speed = 0.0
            self._local_turn_speed = 0.0
            self._sender.forward = 0.0
            self._sender.turn = 0.0
            vehicle_filter = getattr(entity, 'filter', None)
            stop_input = getattr(vehicle_filter, 'notifyInputKeysDown', None)
            if callable(stop_input):
                stop_input(0, 0)
            if (self._local_matrix is not None and
                    self._local_model is not None):
                self._update_local_presentation(entity, dt)
            if self._local_damage_report is not None or self._drown_level == 2:
                self._sender.send_current()
            return

        if self._local_physics is None:
            raise RuntimeError('player physics was not initialized')
        dt = max(0.0, min(float(dt), 0.1))
        position = self._local_position
        tick_pose = position
        yaw = self._local_yaw
        slope_pitch = (0.0 if self._local_airborne else
                       self._smoothed_drive_pitch(position, yaw))
        throttle = self._sender.forward
        turn = self._sender.turn
        is_tracked = bool(getattr(entity, 'is_tracked', False))
        is_engine_dead = bool(getattr(entity, 'is_engine_dead', False))
        if is_tracked or is_engine_dead:
            throttle = 0.0
        elif throttle != 0.0:
            throttle *= critical_damage.stat_factor(entity, 'mobility')
        # A thrown track is physically locked and must brake through the same
        # grip-limited path as the handbrake.  A dead engine only removes drive
        # torque, so existing momentum continues to coast.
        handbrake = bool(self._sender.handbrake) or is_tracked
        previous_speed = self._local_speed
        self._local_speed = vehicle_physics.longitudinal_step(
            self._local_physics, self._local_speed,
            throttle, turn != 0.0,
            slope_pitch, dt, self._local_airborne, 0,
            handbrake)

        if abs(self._local_speed) > 0.0001 and dt > 0.0:
            if self._destructibles is not None:
                self._destructibles._fell_trees_near(
                    self._avatar.spaceID, self._vector(position), yaw,
                    self._local_speed, entity.typeDescriptor)
            if self._motion_is_clear(
                    entity, position, yaw, self._local_speed, dt,
                    allow_crush_drive=(throttle * self._local_speed > 0.0 and
                                       not handbrake)):
                position = (
                    position[0] + math.sin(yaw) * self._local_speed * dt,
                    position[1],
                    position[2] + math.cos(yaw) * self._local_speed * dt)
                self._local_grind = max(0, self._local_grind - 1)
            elif not self._local_airborne:
                if self._local_motion_cap_crushed:
                    # The speed cap only proves that this vehicle may crush the
                    # exact item.  It is never copied vehicle momentum.  Keep
                    # this tick outside the accepted native skin and restore
                    # the real speed from before the longitudinal integration.
                    self._local_speed = previous_speed
                    self._local_grind = 1
                elif self._local_motion_soft_block:
                    # #1513 hides fragile/module geometry asynchronously.  Keep
                    # the pose outside its still-native skin, but retain impact
                    # momentum; the next clear tick advances normally and a
                    # newly exposed backing wall still uses the hard response.
                    self._local_grind = 1
                else:
                    deflected = False
                    for delta_yaw in (0.55, -0.55, 1.0, -1.0):
                        slide_yaw = yaw + delta_yaw
                        if self._motion_is_clear(
                                entity, position, slide_yaw,
                                self._local_speed, dt):
                            if self._local_grind <= 0:
                                self._local_speed *= 0.6
                            slide_speed = self._local_speed * (
                                0.85 ** (dt * 60.0))
                            position = (
                                position[0] + math.sin(slide_yaw) *
                                slide_speed * dt,
                                position[1],
                                position[2] + math.cos(slide_yaw) *
                                slide_speed * dt)
                            self._local_speed = slide_speed
                            self._local_grind = 4
                            deflected = True
                            break
                    if not deflected:
                        self._local_speed *= 0.35 ** (dt * 60.0)
                        if abs(self._local_speed) < 0.05:
                            self._local_speed = 0.0
                        self._local_grind = 4

        if is_tracked or is_engine_dead:
            turn = 0.0
            self._local_turn_speed = 0.0
        self._local_turn_speed = vehicle_physics.traverse_step(
            self._local_physics, self._local_turn_speed,
            turn, self._local_speed, dt,
            drive_intent=throttle)
        self._local_turn_speed *= critical_damage.stat_factor(
            entity, 'traverse')
        yaw += self._local_turn_speed * dt
        while yaw > math.pi:
            yaw -= 2.0 * math.pi
        while yaw < -math.pi:
            yaw += 2.0 * math.pi
        self._local_support_rise_blocked = False
        self._local_support_tick_pose = tick_pose
        try:
            position = self._update_vertical_motion(
                entity, position, yaw, dt)
        finally:
            self._local_support_tick_pose = None
        support_blocked = self._local_support_rise_blocked
        if support_blocked:
            self._local_speed *= 0.35 ** (dt * 60.0)
            if abs(self._local_speed) < 0.05:
                self._local_speed = 0.0
            self._local_grind = 4
        self._ground_pitch(position, yaw, entity.typeDescriptor)
        position = self._apply_slope_slide(position, yaw, dt, entity)
        position = self._resolve_local_tank_contacts(
            entity, position, yaw, dt)
        self._local_position, self._local_yaw = position, yaw
        presentation_position = self._update_local_presentation(entity, dt)
        self._avatar.updateOwnVehiclePosition(
            presentation_position,
            self._vector(_engine_rotation(yaw)),
            self._local_speed, self._local_turn_speed)
        self._publish_rpm(self._clock())
        self._input_accumulator += dt
        if self._input_accumulator >= NETWORK_INPUT_SECONDS:
            # Preserve the nominal 30 Hz phase at render rates that are not a
            # multiple of 30.  Do not burst stale samples after a slow frame.
            self._input_accumulator %= NETWORK_INPUT_SECONDS
            self._sender.send_current()

    def _bot_destructible_scan_due(self, state, now):
        """Rate-limit proximity enumeration without skipping hull travel."""
        bot_id = int(state['id'])
        position = (_number(state.get('x')), _number(state.get('y')),
                    _number(state.get('z')))
        previous = self._bot_destructible_samples.get(bot_id)
        if previous is not None:
            deadline, sampled_position = previous
            # A stopped Bot may be waiting for a blank native slot/catalog OBB
            # to stream.  Keep a low-frequency phase even without hull travel;
            # registration is read-only for type1/type2 catalog objects.
            if (float(now) < float(deadline) and
                    _distance_2d(position, sampled_position) <
                    BOT_DESTRUCTIBLE_TRAVEL_METRES):
                return False
            interval = (0.50 if abs(_number(state.get('speed'))) < 1.0
                        else BOT_DESTRUCTIBLE_SECONDS)
            deadline = float(now) + interval
        else:
            # Schedule the first enumeration instead of making all 29 bots
            # scan on the materialisation frame.  The 6 m forward sensor and
            # 3 m travel trigger keep the hull inside its contact volume while
            # this phase elapses.  The +1 caps the largest phase at 0.10 s.
            phase = (((abs(bot_id) * 17 + 5 * 11) % 29) + 1) / 29.0
            deadline = float(now) + BOT_DESTRUCTIBLE_SECONDS * phase
            self._bot_destructible_samples[bot_id] = (
                deadline, position)
            return False
        self._bot_destructible_samples[bot_id] = (deadline, position)
        return True

    def _apply_authority_bot_poses(self, states):
        """Present copied 0.8.2 bot poses through the remote filter."""
        applied = False
        now = self._clock()
        for state in states:
            if not isinstance(state, dict) or state.get('id') is None:
                continue
            record = self._records.get('bot:%s' % state['id'])
            if record is None or not record.get('ready'):
                continue
            x = _number(state.get('x'))
            y = _number(state.get('y'))
            z = _number(state.get('z'))
            position = self._vector((
                x, y, z))
            yaw = _number(state.get('yaw'))
            if (self._destructibles is not None and
                    self._bot_destructible_scan_due(state, now)):
                entity = self._server_entity(record['engine_id'])
                descriptor = getattr(entity, 'typeDescriptor', None)
                if descriptor is None:
                    raise RuntimeError(
                        'authority bot destructible descriptor is unavailable')
                self._destructibles._fell_trees_near(
                    self._avatar.spaceID, position, yaw,
                    _number(state.get('speed')), descriptor)
            self._binding.set_vehicle_pose(
                record['engine_id'], position,
                _engine_rotation(yaw, _number(state.get('pitch')),
                                 _number(state.get('roll'))))
            self._binding.update_vehicle_aim(
                record['engine_id'], yaw,
                _number(state.get('aim_yaw', yaw)),
                _number(state.get('gun_pitch')))
            applied = True
        return applied

    def _bot_visibility(self, source, target, fired_recently=False):
        source_position = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        start = self._vector((source_position[0], source_position[1] + 2.0,
                              source_position[2]))
        end = self._vector((target_position[0], target_position[1] + 1.5,
                            target_position[2]))
        hit = self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID, start, end, 128)
        line_of_sight = bool(
            hit is None or
            (hit[0] - start).length + 1.5 >= (end - start).length)
        foliage_bonus = 0.0
        if line_of_sight and self._foliage is not None:
            foliage_bonus = self._foliage.camouflage_bonus(
                source_position, target_position, fired_recently)
        return {
            'line_of_sight': line_of_sight,
            'foliage_bonus': foliage_bonus,
        }

    def _bot_firing_lane(self, source, target):
        """Probe static space between, rather than inside, two vehicle hulls."""
        profile = source.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        if str(profile.get('class_tag') or '') == 'SPG':
            if self._artillery is None or self._bots is None:
                return False
            descriptor = self._bots._descriptors.get(int(source.get('id')))
            shell_index = max(0, int(source.get('shell_index', 0) or 0))
            ready, solution = self._artillery.request(
                source, target, descriptor, shell_index, self._clock())
            return bool(ready and solution is not None)
        source_position = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        dx = target_position[0] - source_position[0]
        dz = target_position[2] - source_position[2]
        distance = math.sqrt(dx * dx + dz * dz)
        # Keep a short but real world segment between close hulls. Treating the
        # absence of the default eight-metre middle section as clear let tanks
        # on opposite sides of a thin wall enter engage/hold and fire forever.
        clearance = min(4.0, max(0.0, (distance - 0.75) * 0.5))
        for target_height in (1.5, 2.2):
            segment = bot_planner.trimmed_sight_segment(
                source_position, target_position, 2.5, target_height,
                clearance, clearance)
            if segment is None:
                return False
            if not segment:
                return False
            start, end = segment
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID,
                self._vector(start), self._vector(end), 128)
            if hit is None:
                return True
        return False

    def _bot_ballistic_solution(self, source, target, descriptor,
                                shell_index, now):
        """Return only a completed SPG arc; pending/blocked stays unshootable."""
        if self._artillery is None or target is None:
            return None
        return self._artillery.solution(
            source, target, descriptor, shell_index, now)

    def _bot_artillery_launch(
            self, source, target, descriptor, shell_index, fire_seq,
            shot_yaw, shot_pitch, flight_time, now):
        """Prove the exact dispersed SPG path from the live muzzle node."""
        if (self._artillery is None or not isinstance(source, dict) or
                not isinstance(target, dict)):
            return None
        try:
            bot_id = int(source.get('id'))
        except (TypeError, ValueError, OverflowError):
            return None
        record = self._records.get('bot:%s' % bot_id)
        if record is None:
            return None
        entity = self._server_entity(record.get('engine_id'))
        if (entity is None or not getattr(entity, 'isStarted', False) or
                getattr(entity, 'typeDescriptor', None) is None):
            return None
        try:
            gun_node = entity.model.node('HP_gunFire')
            origin = _xyz(self._runtime.math.Matrix(gun_node).translation)
        except Exception:
            # A logical pose is not a muzzle proof.  SPGs wait until the
            # native model exposes the exact launch transform.
            return None
        ready, receipt = self._artillery.request_launch(
            source, target, descriptor, int(shell_index), int(fire_seq),
            origin, float(shot_yaw), float(shot_pitch),
            float(flight_time), float(now))
        return receipt if ready and isinstance(receipt, dict) else None

    def _bot_artillery_cancel(self, source):
        """Discard bounded arc work for a cancelled frozen SPG intent."""
        if self._artillery is None or not isinstance(source, dict):
            return False
        return bool(self._artillery.cancel_launch(source))

    def _artillery_arc_probe(self, start, end):
        """Return the native world hit point, or None for one clear chord."""
        hit = self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID, self._vector(start), self._vector(end), 128)
        if hit is None:
            return None
        try:
            return _xyz(hit[0])
        except Exception:
            return False

    def _advance_artillery_arcs(self, now):
        if self._artillery is None:
            return 0
        return self._artillery.advance(
            now, ARTILLERY_ARC_RAYS_PER_FRAME,
            self._artillery_arc_probe)

    def _send_bot_message(self, message):
        kind = message.get('type')
        if kind == 'bot_manifest':
            return self.client.send_bot_manifest(message.get('bots'))
        if kind == 'bot_state':
            return self.client.send_bot_state(message.get('bots'))
        if kind == 'bot_observation':
            return self.client.send_bot_observation(
                message.get('contacts'), message.get('affordances'))
        if kind == 'bot_human_hit':
            return self.client.send_bot_human_hit(
                message.get('attacker_bot'), message.get('target'),
                message.get('shot_seq'), message.get('damage'),
                message.get('shot_result'), message.get('impact_position'))
        if kind == 'bot_ram':
            return self.client.send_bot_ram(
                message.get('bot_id'), message.get('target_kind'),
                message.get('target_id'), message.get('ram_seq'),
                message.get('damage_to_bot'),
                message.get('damage_to_target'))
        if kind == 'rules_state':
            rules = message.get('rules') or {}
            return self.client.send_rules_state(rules.get('bases'))
        if kind == 'battle_result':
            return self.client.send_battle_result(
                message.get('winner'), message.get('reason'),
                message.get('base_team'))
        return False

    def _resolve_bot_fire(self, message):
        if message.get('type') != 'bot_state':
            return
        for state in message.get('bots') or ():
            try:
                bot_id = int(state.get('id'))
                fire_seq = int(state.get('fire_seq', 0))
            except (TypeError, ValueError):
                continue
            previous = self._bot_fire_seen.get(bot_id, 0)
            if (fire_seq > previous and
                    self._launch_bot_projectile(state, fire_seq)):
                self._bot_fire_seen[bot_id] = fire_seq

    def _launch_bot_projectile(self, state, shot_seq):
        """Publish one Bot launch; damage waits for the canonical projectile."""
        try:
            bot_id = int(state.get('id'))
            shot_yaw = float(state.get('shot_yaw'))
            shot_pitch = float(state.get('shot_pitch'))
        except (TypeError, ValueError):
            return False
        source_record = self._records.get('bot:%s' % bot_id)
        if source_record is None:
            return False
        source = self._server_entity(source_record['engine_id'])
        if (source is None or source.typeDescriptor is None or
                not getattr(source, 'isStarted', False)):
            return False
        shot = self._descriptor_shot(
            source.typeDescriptor, state.get('shell_index'))
        speed = _number(_field(shot, 'speed'), -1.0)
        gravity = _number(_field(shot, 'gravity'), -1.0)
        maximum = _number(_field(shot, 'maxDistance'), -1.0)
        if speed <= 0.0 or gravity <= 0.0 or maximum <= 0.0:
            return False
        profile = state.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        is_spg = str(profile.get('class_tag') or '') == 'SPG'
        max_time_ms = PROJECTILE_MAX_TIME_MS
        if is_spg:
            proof_key = state.get('shot_proof_key')
            try:
                origin = tuple(float(value)
                               for value in state['shot_origin'])
                velocity = tuple(float(value)
                                 for value in state['shot_velocity'])
                receipt_gravity = float(state['shot_gravity'])
                receipt_maximum = float(state['shot_max_distance'])
                max_time_ms = int(state['shot_max_time_ms'])
                proof_origin = tuple(float(value)
                                     for value in proof_key[6])
                proof_flight = float(proof_key[12])
                if (len(origin) != 3 or len(velocity) != 3 or
                        not isinstance(proof_key, (list, tuple)) or
                        len(proof_key) < 13):
                    return False
                proof_values = (
                    proof_key[0], int(proof_key[1]), int(proof_key[4]),
                    int(proof_key[5]), float(proof_key[7]),
                    float(proof_key[8]), float(proof_key[9]),
                    float(proof_key[10]), float(proof_key[11]))
            except (KeyError, TypeError, ValueError, IndexError,
                    OverflowError):
                return False
            values = origin + velocity + (
                receipt_gravity, receipt_maximum, proof_flight)
            horizontal = math.cos(shot_pitch)
            expected_velocity = (
                math.sin(shot_yaw) * horizontal * speed,
                math.sin(shot_pitch) * speed,
                math.cos(shot_yaw) * horizontal * speed)
            if (any(math.isnan(value) or math.isinf(value)
                    for value in values) or
                    proof_values[0] != 'launch' or
                    proof_values[1] != bot_id or
                    proof_values[2] != max(
                        0, int(state.get('shell_index', 0) or 0)) or
                    proof_values[3] != int(shot_seq) or
                    proof_values[4] != shot_yaw or
                    proof_values[5] != shot_pitch or
                    proof_values[6] != speed or
                    proof_values[7] != gravity or
                    proof_values[8] != maximum or
                    proof_origin != origin or
                    receipt_gravity != gravity or
                    receipt_maximum != maximum or
                    max_time_ms <= 0 or
                    max_time_ms > PROJECTILE_MAX_TIME_MS or
                    proof_flight <= 0.0 or
                    proof_flight * 1000.0 > max_time_ms + 1e-6 or
                    any(abs(velocity[index] - expected_velocity[index]) >
                        1e-7 for index in range(3))):
                return False
        else:
            try:
                gun_node = source.model.node('HP_gunFire')
                origin = _xyz(
                    self._runtime.math.Matrix(gun_node).translation)
            except Exception:
                # A guessed hull-centre origin can start beyond nearby cover.
                # Every canonical shell must leave the native gun-fire node.
                return False
            horizontal = math.cos(shot_pitch)
            direction = (
                math.sin(shot_yaw) * horizontal,
                math.sin(shot_pitch),
                math.cos(shot_yaw) * horizontal)
            length = math.sqrt(sum(component * component
                                   for component in direction))
            if length <= 0.000001:
                return False
            velocity = tuple(
                component * speed / length for component in direction)
        is_he = combat_rules.is_he(shot)
        sender = getattr(self.client, 'send_projectile_launch', None)
        if not callable(sender):
            return False
        accepted = sender(
            'bot', bot_id, int(shot_seq),
            max(0, int(state.get('shell_index', 0) or 0)),
            list(origin), list(velocity), gravity, maximum,
            max_time_ms, is_he,
            combat_rules.he_radius(shot) if is_he else 0.0,
            authority_epoch=getattr(self.client, 'authority_epoch', None),
            penetration_factor=combat_rules.sample_penetration_factor())
        return accepted == int(shot_seq)

    def _resolve_bot_shot(self, state, shot_seq):
        try:
            bot_id = int(state.get('id'))
            target_kind = state.get('target_kind')
            target_id = int(state.get('target_id'))
        except (TypeError, ValueError):
            return False
        source_record = self._records.get('bot:%s' % bot_id)
        record_kind = 'player' if target_kind == 'human' else target_kind
        target_record = self._records.get('%s:%s' % (record_kind, target_id))
        if source_record is None or target_record is None:
            return False
        source = self._server_entity(source_record['engine_id'])
        target = self._server_entity(target_record['engine_id'])
        if (source is None or target is None or
                source.typeDescriptor is None or
                not getattr(target, 'isStarted', False)):
            return False
        source_position = _xyz(getattr(source, 'position', state))
        target_position = _xyz(getattr(
            target, 'position', target_record.get('state', {})))
        gun_node = source.model.node('HP_gunFire')
        start = self._vector(
            self._runtime.math.Matrix(gun_node).translation)
        destination = self._vector((
            target_position[0], target_position[1] + 1.2,
            target_position[2]))
        target_direction = destination - start
        target_distance = target_direction.length
        if target_distance <= 0.01:
            return False
        shot = self._descriptor_shot(
            source.typeDescriptor, state.get('shell_index'))
        maximum = max(0.01, _number(
            _field(shot, 'maxDistance', 5000.0), 5000.0))
        if 'shot_yaw' in state and 'shot_pitch' in state:
            shot_yaw = _number(state.get('shot_yaw'))
            shot_pitch = _number(state.get('shot_pitch'))
            horizontal = math.cos(shot_pitch)
            direction = self._vector((
                math.sin(shot_yaw) * horizontal,
                math.sin(shot_pitch),
                math.cos(shot_yaw) * horizontal))
            direction.normalise()
        else:
            # Compatibility fallback for recorded v5 fixtures and an authority
            # takeover snapshot created before shot angles were published.
            direction = target_direction
            direction.normalise()
        end = start + direction.scale(maximum)
        hit_record = None
        target_collisions = None
        distance = 999999.0
        for record in self._records.values():
            if record is source_record:
                continue
            candidate = self._server_entity(record['engine_id'])
            if candidate is None or not getattr(candidate, 'isStarted', False):
                continue
            if (record.get('local') and self._local_matrix is not None):
                candidate_collisions = collide_vehicle_at_matrix(
                    candidate, self._local_matrix, start, end,
                    self._runtime.math)
            else:
                candidate_collisions = candidate.collideSegmentExt(start, end)
            if not candidate_collisions:
                continue
            nearest = min(candidate_collisions,
                          key=lambda item: float(item.dist))
            if float(nearest.dist) < distance:
                hit_record = record
                target_collisions = tuple(candidate_collisions)
                distance = float(nearest.dist)
        scene_end = end
        if hit_record is not None and target_collisions is not None:
            scene_end = start + direction.scale(
                max(0.0, min(maximum, distance)))
        scene = self._resolve_shot_scene(
            start, scene_end, direction, shot)
        penetration_factor = scene.get('penetration_factor')
        world_distance = scene['world_distance']
        if hit_record is None or target_collisions is None:
            if (combat_rules.is_he(shot) and
                    world_distance < maximum):
                self._he_splash(
                    start + direction.scale(world_distance), shot, shot_seq,
                    None, 'bot', bot_id, source_record['engine_id'])
            return False
        if (scene.get('stopped_by_destructible') or
                distance > world_distance + _SHOT_OCCLUSION_EPSILON):
            if combat_rules.is_he(shot):
                self._he_splash(
                    start + direction.scale(world_distance), shot, shot_seq,
                    None, 'bot', bot_id, source_record['engine_id'])
            return False
        if penetration_factor is None:
            penetration_factor = combat_rules.sample_penetration_factor()
        hit_entity = self._server_entity(hit_record['engine_id'])
        damage, result = self._shell_damage(
            source.typeDescriptor, target_collisions, distance,
            shell_index=state.get('shell_index'),
            pierce_loss=scene['piercing_loss'],
            penetration_factor=penetration_factor,
            target_descriptor=getattr(hit_entity, 'typeDescriptor', None))
        impact = start + direction.scale(distance)
        hull_damage = damage
        damage, critical = self._critical_hit(
            hit_entity, source.typeDescriptor, target_collisions, start, end,
            damage, result, source.id, state.get('shell_index'))
        critical_contract = self._critical_proposal_contract(
            hit_record, critical, hull_damage)
        sent = False
        if hit_record.get('kind') == 'bot':
            sent = self.client.send_bot_bot_hit(
                bot_id, hit_record['network_id'], shot_seq,
                damage, result, _xyz(impact), critical,
                **critical_contract)
        elif hit_record.get('kind') == 'player':
            sent = self.client.send_bot_human_hit(
                bot_id, hit_record['network_id'], shot_seq,
                damage, result, _xyz(impact), critical,
                **critical_contract)
        if combat_rules.is_he(shot):
            self._he_splash(
                impact, shot, shot_seq, hit_record, 'bot', bot_id,
                source_record['engine_id'])
        return sent

    def _apply_sync_event(self, event):
        if self.state in ('failed', 'stopped'):
            return
        kind = event.get('type')
        if kind == 'create':
            if event.get('kind') == 'bot':
                self._queue_bot_create(event)
            else:
                self._create_remote(event)
        elif kind == 'update':
            if (event.get('kind') == 'bot' and
                    event.get('entity') not in self._records):
                self._queue_bot_create(event)
            else:
                if (event.get('kind') == 'bot' and
                        self._bots is not None and
                        self._bots.is_authority()):
                    # The copied 0.8.2 authority has already presented this
                    # bot's newest local pose.  A server snapshot is its older
                    # network echo; applying both alternately makes the hull
                    # visibly yaw left/right while it drives forward.
                    event = dict(event)
                    event.pop('pose', None)
                self._update_entity(event)
        elif kind == 'destroy':
            self._destroy_entity(event)

    def _queue_bot_create(self, event):
        """Coalesce one bot until its staggered native createEntity call.

        The 0.8.2 implementation deliberately spreads the line-up over time.
        Creating 29 HD Vehicle entities and their model prerequisites in one
        BigWorld callback is both visibly janky and unsafe in this 32-bit
        client.  Keep the newest snapshot pose while preserving roster order.
        """
        key = event.get('entity')
        if not key or key in self._records:
            return False
        state = dict(event.get('state') or {})
        pose = event.get('pose')
        if isinstance(pose, dict):
            for name in ('x', 'y', 'z', 'yaw', 'aim_yaw', 'gun_pitch'):
                if name in pose:
                    state[name] = pose[name]
        pending = self._pending_bot_creates.get(key)
        if pending is None:
            pending = {
                'type': 'create', 'entity': key, 'kind': 'bot',
                'id': event.get('id'), 'state': state,
                # A later fatal event must not create an already-dead native
                # Vehicle.  The stock arena must first observe the live
                # roster entry, then consume the journaled death transition.
                'initial_state': dict(state)}
            self._pending_bot_creates[key] = pending
            self._pending_bot_create_order.append(key)
        else:
            pending['state'].update(state)
        return True

    def _flush_pending_bot_create(self, now):
        if (not self._pending_bot_create_order or
                now < self._next_bot_create_time):
            return False
        key = self._pending_bot_create_order[0]
        # Alternate teams so both bases materialize together instead of one
        # full lineup appearing before the other.
        if self._last_bot_create_team is not None:
            for candidate in self._pending_bot_create_order:
                event = self._pending_bot_creates.get(candidate)
                team = ((event or {}).get('state') or {}).get('team')
                if (team is not None and
                        int(team) != self._last_bot_create_team):
                    key = candidate
                    break
        self._pending_bot_create_order.remove(key)
        event = self._pending_bot_creates.pop(key, None)
        self._next_bot_create_time = now + BOT_SPAWN_SECONDS
        if event is None or key in self._records:
            return False
        self._create_remote(event)
        created = key in self._records
        if created:
            team = (event.get('state') or {}).get('team')
            if team is not None:
                self._last_bot_create_team = int(team)
        return created

    def _create_remote(self, event):
        key = event.get('entity')
        if key in self._records:
            return
        state = dict(event.get('state') or {})
        initial_state = dict(event.get('initial_state') or state)
        if not all(name in state for name in ('team', 'slot')):
            return
        if event.get('kind') == 'bot' and not all(
                name in state for name in ('x', 'z')):
            return
        descriptor = self._resolve_descriptor(
            state.get('vehicle', self._config['vehicle']))
        properties = self._binding.properties_from_compact_descr(
            descriptor.makeCompactDescr(), int(state.get('team', 1)),
            state.get('name', 'Vehicle'))
        properties['health'] = max(0, min(
            int(initial_state.get('health', descriptor.maxHealth)),
            int(descriptor.maxHealth)))
        position, yaw = self._state_world_pose(state)
        if self._remote_factory is None:
            raise RuntimeError('remote vehicle factory is unavailable')
        engine_id = self._remote_factory.create(
            descriptor, properties, self._vector(position),
            _engine_rotation(yaw))
        if engine_id is None:
            raise RuntimeError('remote presentation returned no vehicle id')
        self._records[key] = {
            'engine_id': engine_id, 'state': state,
            'kind': event.get('kind'), 'network_id': event.get('id'),
            'local': False, 'presentation': True, 'ready': False,
            'arena_added': False, 'properties': properties,
            'spot_visible': bot_planner.bot_initially_visible(
                int(state.get('team', 1)),
                int(getattr(self.client, 'team', 1)), True),
            'spot_until': 0.0, 'spot_next': 0.0,
            'shot_penalty_until': float(
                state.get('shot_penalty_until', 0.0) or 0.0),
            'ready_deadline': self._clock() + float(
                self._config.get('startupTimeoutSeconds', 30.0))}
        self._materialize_record(self._records[key])

    def _update_entity(self, event):
        record = self._records.get(event.get('entity'))
        if record is not None and record.get('tombstone'):
            return
        if record is None:
            state = event.get('state') or {}
            self._create_remote({
                'type': 'create', 'entity': event.get('entity'),
                'kind': event.get('kind'), 'id': event.get('id'),
                'state': state})
            record = self._records.get(event.get('entity'))
            if record is None:
                return
        state = dict(record.get('state') or {})
        state.update(event.get('state') or {})
        record['state'] = state
        pose = event.get('pose')
        if pose is not None:
            record['pending_pose'] = dict(pose)
        self._materialize_record(record)

    def _materialize_record(self, record):
        if record.get('ready'):
            ready = True
        elif record.get('presentation'):
            error = self._remote_factory.error(record['engine_id'])
            if error is not None:
                raise RuntimeError(
                    'remote vehicle %s failed: %s' % (
                        record['engine_id'], error))
            ready = self._remote_factory.is_ready(record['engine_id'])
            if not ready:
                return False
            record['ready'] = True
        else:
            status = ('completed', None)
            status_getter = getattr(self._server, 'vehicleEnterStatus', None)
            if callable(status_getter):
                status = status_getter(record['engine_id'])
            if status[0] == 'failed':
                raise RuntimeError('Vehicle %s enter failed: %s' % (
                    record['engine_id'], status[1]))
            ready = (status[0] == 'completed' and
                     self._binding.is_vehicle_ready(record['engine_id']))
            if not ready:
                return False
            record['ready'] = True
        if record.get('presentation'):
            # ArenaVehiclesPlugin decides whether a roster entry is already
            # in AOI by reading BigWorld.entities during VEHICLE_ADDED. Set
            # the spotting gate before that event, otherwise every enemy is
            # permanently introduced on the minimap at battle load.
            vehicle = self._remote_factory.get(record['engine_id'])
            if vehicle is None or vehicle.model is None:
                raise RuntimeError('remote vehicle has no ready presentation')
            initially_visible = bool(record.get('spot_visible', True))
            vehicle._spot_visible = initially_visible
            vehicle.appearance.changeVisibility(initially_visible)
        if (record.get('presentation') and
                not record.get('arena_added')):
            self._binding.arena_vehicle_added(record['engine_id'], {
                'properties': record['properties'],
                'team_killer': bool(
                    (record.get('state') or {}).get('team_killer', False))})
            record['arena_added'] = True
        if (record.get('presentation') and record.get('arena_added') and
                record.get('spot_visible', True) and
                not record.get('visual_started')):
            self._binding.start_vehicle_visual(record['engine_id'], True)
            record['visual_started'] = True
        if record.get('presentation'):
            self._set_record_spot_visibility(
                record, record.get('spot_visible', True))
        pose = record.pop('pending_pose', None)
        if pose is not None:
            self._apply_record_pose(record, pose)
        state = record.get('state') or {}
        self._apply_vehicle_statistics(record, state)
        # Arena registration and the native Vehicle are now both complete.
        # Consume any ordered one-shot feedback before snapshot reconciliation
        # can make the same health/critical signature look already presented.
        self._drain_event_journal()
        state = record.get('state') or {}
        if self._pending_combat_for_record(record):
            return True
        critical = state.get('critical')
        if isinstance(critical, dict):
            self._apply_critical_state(record, critical, state)
        elif (record.get('kind') == 'player' and
              all(name in state for name in (
                  'critical_revision', 'critical_base_revision',
                  'critical_ack_seq'))):
            self._reconcile_critical_authority(record, state)
        self._apply_health(
            record, state, self._death_attacker_engine_id(state),
            max(0, int(state.get('death_reason', 0) or 0)))
        return True

    def _apply_record_pose(self, record, pose):
        state = record.get('state') or {}
        yaw = _number(pose.get('yaw'))
        if record.get('local'):
            # A snapshot is a delayed echo of the local native physics sample.
            # #1513 exposes no legal pose setter for a client-created Vehicle,
            # so reconciliation remains a server/rules concern rather than
            # rewinding the live C++ object.
            return
        else:
            self._binding.set_vehicle_pose(
                record['engine_id'], self._vector((
                    _number(pose.get('x')), _number(pose.get('y')),
                    _number(pose.get('z')))), _engine_rotation(yaw))
            self._binding.update_vehicle_aim(
                record['engine_id'], yaw,
                _number(pose.get('aim_yaw', yaw)),
                _number(pose.get('gun_pitch')))

    def _flush_pending_entities(self, now):
        for unused_key, record in list(self._records.items()):
            if record.get('tombstone'):
                self._flush_tombstone(record)
                continue
            if record.get('ready'):
                continue
            if self._materialize_record(record):
                continue
            deadline = record.get('ready_deadline')
            if deadline is not None and now >= deadline:
                raise RuntimeError(
                    'Vehicle %s did not enter world before timeout' %
                    record['engine_id'])

    def _flush_tombstone(self, record):
        """Destroy a remote Vehicle that entered after its network removal."""
        if record.get('presentation'):
            if self._remote_factory is not None:
                self._stop_remote_visual(record)
                self._remote_factory.destroy(record['engine_id'])
            record['visible_destroy_requested'] = True
            return
        if record.get('visible_destroy_requested'):
            return
        try:
            entity = self._server_entity(record['engine_id'])
        except ReferenceError:
            entity = None
        if entity is None:
            return
        try:
            self._binding.arena_vehicle_removed(record['engine_id'])
        finally:
            self._binding.destroy_entity(record['engine_id'])
        record['visible_destroy_requested'] = True

    def _set_record_spot_visibility(self, record, visible):
        """Keep the marker and minimap on one spotting boundary.

        A destroyed vehicle keeps its model drawn as cover once the spotting
        gate closes, which is what retail does with a wreck.
        """
        if not record.get('presentation') or not record.get('ready'):
            record['spot_visible'] = bool(visible)
            return bool(visible)
        vehicle = self._remote_factory.get(record['engine_id'])
        if vehicle is None or vehicle.model is None:
            raise RuntimeError('spotted remote vehicle has no model')
        visible = bool(visible)
        record['spot_visible'] = visible
        vehicle._spot_visible = visible
        vehicle.appearance.changeVisibility(
            visible or not self._record_alive(record, vehicle))
        if visible and not record.get('visual_started'):
            self._binding.start_vehicle_visual(record['engine_id'], True)
            record['visual_started'] = True
        elif not visible and record.get('visual_started'):
            self._stop_remote_visual(record)
        return visible

    def _present_direct_spot(self, record):
        """Publish the one stock ribbon and sound for a first direct spot."""
        if record.get('spot_feedback_sent'):
            return False
        feedback_common = getattr(
            self._runtime, 'battle_feedback_common', None)
        event_types = getattr(feedback_common, 'BATTLE_EVENT_TYPE', None)
        if event_types is None:
            raise RuntimeError(
                '#1513 spotting feedback constants are unavailable')
        pack_visibility = getattr(event_types, 'packVisibility', None)
        if not callable(pack_visibility):
            raise RuntimeError(
                '#1513 visibility feedback packer is unavailable')
        callback = getattr(self._avatar, 'onBattleEvents', None)
        if not callable(callback):
            raise RuntimeError(
                '#1513 battle-event feedback boundary is unavailable')
        target_id = int(record['engine_id'])
        callback([
            {
                'eventType': int(event_types.SPOTTED),
                'targetID': target_id, 'count': 1, 'details': 0,
            },
            {
                'eventType': int(event_types.TARGET_VISIBILITY),
                'targetID': target_id, 'count': 1,
                'details': int(pack_visibility(True, True)),
            },
        ])
        record['spot_feedback_sent'] = True
        return True

    @staticmethod
    def _record_alive(record, entity):
        state = record.get('state') or {}
        if 'alive' in state:
            return bool(state.get('alive')) and int(
                state.get('health', 1) or 0) > 0
        alive = getattr(entity, 'isAlive', None)
        return bool(alive() if callable(alive) else alive)

    @staticmethod
    def _vision_radius(descriptor, entity=None):
        turret = _field(descriptor, 'turret', {})
        misc = _field(descriptor, 'miscAttrs', {})
        damage_factor = 1.0
        if entity is not None:
            damage_factor = critical_damage._device_damage.\
                clamp_vision_factor(
                    critical_damage.stat_factor(entity, 'vision'))
        return spotting.effective_view_range(
            _field(turret, 'circularVisionRadius', 400.0),
            vision_factor=(
                _field(misc, 'circularVisionRadiusFactor', 1.0) *
                damage_factor))

    @staticmethod
    def _base_invisibility(descriptor):
        crew_factor = spotting.crew_camouflage_factor(0.0)
        calculator = getattr(descriptor, 'computeBaseInvisibility', None)
        if callable(calculator):
            try:
                values = calculator(crew_factor, None)
                if isinstance(values, (list, tuple)) and len(values) >= 2:
                    return (_number(values[0]), _number(values[1]))
            except Exception:
                pass
        vehicle_type = _field(descriptor, 'type', {})
        values = _field(vehicle_type, 'invisibility', (0.0, 0.0))
        if not isinstance(values, (list, tuple)) or len(values) < 2:
            values = (0.0, 0.0)
        misc = _field(descriptor, 'miscAttrs', {})
        return spotting.base_camouflage(
            values[0], values[1],
            invisibility_factor=_field(misc, 'invisibilityFactor', 1.0))

    @staticmethod
    def _shot_invisibility_factor(descriptor):
        gun = _field(descriptor, 'gun', {})
        return spotting.clamp(
            _field(gun, 'invisibilityFactorAtShot', 1.0), 0.0, 1.0)

    def _spot_line_of_sight(self, observer, target, target_descriptor,
                            target_moving=False, fired_recently=False):
        observer_position, observer_descriptor, observer_entity = observer
        distance = _distance_2d(observer_position, target)
        if distance <= spotting.PROXIMITY_SPOT_DISTANCE:
            return True
        if distance > spotting.MAX_SPOT_DISTANCE:
            return False
        for target_height in (1.5, 2.2):
            segment = bot_planner.trimmed_sight_segment(
                observer_position, target, 2.5, target_height)
            if segment is None:
                has_line_of_sight = True
                break
            if not segment:
                continue
            start, end = segment
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID,
                self._vector(start), self._vector(end), 128)
            if hit is None:
                has_line_of_sight = True
                break
        else:
            has_line_of_sight = False
        if not has_line_of_sight:
            return False
        foliage_bonus = 0.0
        if self._foliage is not None:
            foliage_bonus = self._foliage.camouflage_bonus(
                observer_position, target, fired_recently)
        camouflage = spotting.effective_camouflage(
            self._base_invisibility(target_descriptor),
            moving=target_moving,
            shot_factor=self._shot_invisibility_factor(target_descriptor),
            fired_recently=fired_recently,
            foliage_bonus=foliage_bonus)
        return spotting.is_detected(
            distance, self._vision_radius(
                observer_descriptor, observer_entity), camouflage,
            has_line_of_sight=True)

    def _spotting_observers(self):
        local_entity = None
        if self._server is not None:
            local_entity = self._server_entity(self._server.vehicle_id)
        if local_entity is None:
            raise RuntimeError('local spotting observer is unavailable')
        local_record = self._records.get(
            'player:%s' % self.client.player_id)
        direct_observer = None
        if self._record_alive(local_record or {}, local_entity):
            direct_observer = (
                self._local_position, self._local_descriptor, local_entity)
        observers = [direct_observer]
        for record in self._records.values():
            if (record.get('local') or not record.get('ready') or
                    record.get('tombstone') or
                    int((record.get('state') or {}).get('team', 0)) !=
                    int(self.client.team)):
                continue
            entity = self._server_entity(record['engine_id'])
            if entity is None or not self._record_alive(record, entity):
                continue
            observers.append((
                _xyz(entity.position), entity.typeDescriptor, entity))
        return tuple(observers)

    @staticmethod
    def _spotting_probe_phase(record):
        """Spread native LOS work across the five 0.10-second frames."""
        identity = record.get('network_id')
        if identity is None:
            identity = record.get('engine_id', 0)
        try:
            identity = abs(int(identity))
        except (TypeError, ValueError, OverflowError):
            identity = 0
        return ((identity * 17) % SPOTTING_PHASE_BUCKETS) * \
            SPOTTING_UPDATE_SECONDS

    @classmethod
    def _spotting_probe_due(cls, record, now):
        """Retain one 2 Hz deadline without synchronising late frames."""
        deadline = float(record.get('spot_next', 0.0) or 0.0)
        if deadline <= 0.0:
            cycle = math.floor(float(now) / SPOTTING_PROBE_SECONDS)
            deadline = (cycle * SPOTTING_PROBE_SECONDS +
                        cls._spotting_probe_phase(record))
            if deadline + 1e-9 < float(now):
                deadline += SPOTTING_PROBE_SECONDS
            record['spot_next'] = deadline
        if float(now) + 1e-9 < deadline:
            return False
        elapsed = max(0.0, float(now) - deadline)
        intervals = int(math.floor(
            (elapsed + 1e-9) / SPOTTING_PROBE_SECONDS)) + 1
        record['spot_next'] = (
            deadline + intervals * SPOTTING_PROBE_SECONDS)
        return True

    def _update_spotting(self, now):
        """Apply proximity, team relay, LOS and ten-second spot memory."""
        if now < self._next_spotting_time:
            return False
        if self._next_spotting_time <= 0.0:
            self._next_spotting_time = now
        elapsed = max(0.0, float(now) - self._next_spotting_time)
        intervals = int(math.floor(
            (elapsed + 1e-9) / SPOTTING_UPDATE_SECONDS)) + 1
        self._next_spotting_time += intervals * SPOTTING_UPDATE_SECONDS
        observers = self._spotting_observers()
        changed = False
        for record in self._records.values():
            state = record.get('state') or {}
            if (record.get('local') or not record.get('presentation') or
                    not record.get('ready') or record.get('tombstone') or
                    int(state.get('team', 0)) == int(self.client.team)):
                continue
            entity = self._server_entity(record['engine_id'])
            if entity is None:
                continue
            alive = self._record_alive(record, entity)
            direct_seen = False
            if alive and self._spotting_probe_due(record, now):
                target = _xyz(entity.position)
                target_moving = abs(_number(
                    state.get('speed'), self._local_speed
                    if record.get('local') else 0.0)) > (
                        spotting.MOVING_SPEED_EPSILON)
                fired_recently = now < float(
                    record.get('shot_penalty_until', 0.0))
                direct_seen = (
                    observers[0] is not None and
                    self._spot_line_of_sight(
                        observers[0], target, entity.typeDescriptor,
                        target_moving, fired_recently))
                seen = direct_seen or any(self._spot_line_of_sight(
                    observer, target, entity.typeDescriptor,
                    target_moving, fired_recently)
                    for observer in observers[1:])
                if seen:
                    record['spot_until'] = (
                        now + spotting.SPOT_MEMORY_SECONDS)
            visible = (alive and
                       now < float(record.get('spot_until', 0.0)))
            previous_visible = bool(record.get('spot_visible', False))
            if visible != previous_visible:
                changed = True
            self._set_record_spot_visibility(record, visible)
            if visible and not previous_visible and direct_seen:
                self._present_direct_spot(record)
        return changed

    def _stop_remote_visual(self, record):
        if not record.get('visual_started'):
            return False
        if self._outlined_engine_id == record.get('engine_id'):
            self._clear_target_outline()
        self._binding.stop_vehicle_visual(record['engine_id'], False)
        record['visual_started'] = False
        return True

    def _apply_health(self, record, state, attacker_id=0, reason_id=None,
                      force_cause=False, attack_reason_id=None):
        if 'health' not in state:
            return
        health = max(0, int(state.get('health', 0)))
        if reason_id is None:
            reason_id = max(0, int(state.get('death_reason', 0) or 0))
        else:
            reason_id = max(0, int(reason_id))
        if attack_reason_id is None:
            attack_reason_id = reason_id
        else:
            attack_reason_id = max(0, int(attack_reason_id))
        engine_id = record['engine_id']
        display_health = max(
            0, int(state.get('display_health', health) or 0))
        crew_active = bool(state.get('alive', health > 0)) and health > 0
        # ``attacker_id`` and ``attack_reason_id`` are one-shot presentation
        # causes, not snapshot state.  Ordered combat events force their
        # native notification; a following cause-free snapshot must not look
        # like a new health transition and overwrite FROM_PLAYER colouring.
        signature = (
            health, display_health, crew_active, int(reason_id))
        previous_signature = self._last_health.get(engine_id)
        durable_changed = previous_signature != signature
        if not durable_changed and not force_cause:
            return
        entity = self._server_entity(engine_id)
        if entity is None:
            return
        self._last_health[engine_id] = signature
        previous = getattr(entity, 'health', health)
        previous_dead = bool(
            previous_signature is not None and
            (previous_signature[0] <= 0 or not previous_signature[2]))
        if health <= 0 and not previous_dead:
            fire_reason = self._attack_reason('FIRE', 1)
            if reason_id == fire_reason:
                death_cause = 'fire'
            elif reason_id == self._attack_reason('RAM', 2):
                death_cause = 'ramming'
            elif reason_id == self._attack_reason('WORLD_COLLISION', 3):
                death_cause = 'world_collision'
            elif reason_id == self._attack_reason('DROWNING', 5):
                death_cause = 'drowning'
            else:
                death_cause = 'shot'
            death_payload = critical_damage.apply_death(
                entity, death_cause)
            if death_payload is not None:
                canonical = self._critical_state(death_payload)
                state['critical'] = canonical
                record['critical_state'] = canonical
                record['state'] = state
                self._present_critical(
                    record, death_payload.get('events'), attacker_id)
                if record.get('local'):
                    self._queue_local_damage_report(
                        critical=death_payload,
                        attribute_attacker=death_cause not in (
                            'drowning', 'world_collision'))
        preserve_drowned_hull = (
            health <= 0 and not crew_active and display_health > 0)
        native_health = display_health if preserve_drowned_hull else health
        entity.health = native_health
        health_changed = getattr(entity, 'onHealthChanged', None)
        if callable(health_changed):
            health_changed(
                native_health, int(attacker_id), int(attack_reason_id))
        else:
            notifier = getattr(entity, 'set_health', None)
            if callable(notifier):
                notifier(previous)
        if record.get('presentation'):
            provider = getattr(self._avatar, 'guiSessionProvider', None)
            present_health = getattr(provider, 'setVehicleHealth', None)
            if not callable(present_health):
                raise RuntimeError(
                    '#1513 remote vehicle health presenter is unavailable')
            present_health(
                False, engine_id, native_health,
                int(attacker_id), int(attack_reason_id))
        previous_crew_active = getattr(entity, 'isCrewActive', crew_active)
        entity.isCrewActive = crew_active
        if previous_crew_active != crew_active:
            crew_notifier = getattr(entity, 'set_isCrewActive', None)
            if callable(crew_notifier):
                crew_notifier(previous_crew_active)
        if record.get('local'):
            if health <= 0:
                self._local_speed = 0.0
                if self._sender is not None:
                    self._sender.forward = 0.0
                    self._sender.turn = 0.0
            self._avatar.updateVehicleHealth(
                engine_id, display_health, int(reason_id),
                crew_active, False)
        if previous > 0 and health <= 0:
            killed = getattr(self._binding, 'arena_vehicle_killed', None)
            if callable(killed):
                killed(engine_id, int(attacker_id), int(reason_id))
            if not record.get('local'):
                self._fallback_postmortem_viewpoint(engine_id)
        if (record.get('presentation') and self._remote_factory is not None and
                not self._record_alive(record, entity)):
            self._remote_factory.request_wreck(engine_id)

    def _destroy_entity(self, event):
        record = self._records.get(event.get('entity'))
        if record is not None and record.get('local'):
            state = dict(record.get('state') or {})
            state.update(event.get('state') or {})
            state['health'] = 0
            state['alive'] = False
            record['state'] = state
            self._materialize_record(record)
            return
        if record is None:
            key = event.get('entity')
            pending = self._pending_bot_creates.get(key)
            if pending is not None and event.get('keep_corpse'):
                state = dict(pending.get('state') or {})
                state.update(event.get('state') or {})
                state['health'] = 0
                state['alive'] = False
                pending['state'] = state
                return
            if pending is not None:
                if self._pending_event_references(key):
                    raise RuntimeError(
                        'pending entity %s was removed before its ordered '
                        'event applied' % key)
                self._pending_bot_creates.pop(key, None)
                try:
                    self._pending_bot_create_order.remove(key)
                except ValueError:
                    pass
            return
        self._fallback_postmortem_viewpoint(record['engine_id'])
        if event.get('keep_corpse'):
            state = dict(record.get('state') or {})
            state.update(event.get('state') or {})
            state['health'] = 0
            state['alive'] = False
            record['state'] = state
            self._materialize_record(record)
            return
        if record.get('presentation'):
            self._records.pop(event.get('entity'), None)
            if record.get('arena_added'):
                self._binding.arena_vehicle_removed(record['engine_id'])
            if self._remote_factory is not None:
                self._stop_remote_visual(record)
                self._remote_factory.destroy(record['engine_id'])
            return
        if record.get('ready'):
            self._records.pop(event.get('entity'), None)
            forget = getattr(self._server, 'forgetVehicleEnter', None)
            if callable(forget):
                forget(record['engine_id'])
            try:
                visible = self._server_entity(
                    record['engine_id']) is not None
            except ReferenceError:
                visible = False
            if visible:
                try:
                    self._binding.arena_vehicle_removed(record['engine_id'])
                finally:
                    self._binding.destroy_entity(record['engine_id'])
        else:
            # Never pass a pending id to BigWorld.destroyEntity.  The #1513
            # native registry does not own it yet; a second destroy after the
            # delayed onEnterWorld can cross the C++ boundary twice.  Keep a
            # tombstone and destroy exactly once when the id becomes visible.
            record['tombstone'] = True
            record.pop('pending_pose', None)
            try:
                visible = self._server_entity(
                    record['engine_id']) is not None
            except ReferenceError:
                visible = False
            record['visible_destroy_requested'] = False
            if visible:
                self._flush_tombstone(record)

    def shoot(self, aim_yaw, gun_pitch):
        if (self.state != 'running' or not self._battle_live or
                self._battle_result is not None or
                self._drown_level == 2):
            return False
        if self._server is None:
            return False
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return False
        is_alive = getattr(entity, 'isAlive', None)
        if ((callable(is_alive) and not is_alive()) or
                (not callable(is_alive) and
                 (_number(getattr(entity, 'health', 0.0)) <= 0.0 or
                  not bool(getattr(entity, 'isCrewActive', True))))):
            return False
        if getattr(entity, 'is_gun_destroyed', False):
            return False
        state = self._gun_state
        if state is None:
            state = gun_mechanics.GunState(entity.typeDescriptor)
            self._gun_state = state
            self._gun_last_tick = self._clock()
        if not state.can_fire(self._battle_live):
            return False
        dispersion_angle = self._native_dispersion_angle()
        shell_index = state.shot_index
        shot = self._descriptor_shot(entity.typeDescriptor, shell_index)
        speed = _number(_field(shot, 'speed'), -1.0)
        gravity = _number(_field(shot, 'gravity'), -1.0)
        maximum = _number(_field(shot, 'maxDistance'), -1.0)
        if speed <= 0.0 or gravity <= 0.0 or maximum <= 0.0:
            return False
        start, direction = self._mutable_shot_ray()
        state.scatter(
            direction,
            bool(self._config and self._config.get(
                'perfect_accuracy', False)),
            dispersion_angle=dispersion_angle)
        velocity = direction.scale(speed)
        penetration_factor = combat_rules.sample_penetration_factor()
        is_he = combat_rules.is_he(shot)
        shot_seq = self.client.send_fire(
            shell_index,
            position=list(_xyz(start)), velocity=list(_xyz(velocity)),
            gravity=gravity, max_distance=maximum,
            max_time_ms=PROJECTILE_MAX_TIME_MS,
            is_he=is_he,
            splash_radius=(combat_rules.he_radius(shot) if is_he else 0.0),
            penetration_factor=penetration_factor)
        if not shot_seq:
            return False
        self._apply_native_shot_bloom()
        local_record = self._records.get(
            'player:%s' % self.client.player_id)
        if local_record is not None:
            local_record['shot_penalty_until'] = (
                self._clock() + spotting.SHOT_CAMOUFLAGE_SECONDS)
        state.commit_fire(critical_damage.stat_factor(entity, 'reload'))
        self._publish_ammo_state(state, force=True)
        self._publish_reload_event(
            state.reload_time, state.reload_duration, force=True)
        return True

    def _resolve_hit(self, shot_seq, aim_yaw, gun_pitch, shell_index=None,
                     dispersion_angle=None):
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return
        start, direction = self._mutable_shot_ray()
        if self._gun_state is not None:
            if dispersion_angle is None:
                dispersion_angle = self._native_dispersion_angle()
            self._gun_state.scatter(
                direction,
                bool(self._config and self._config.get(
                    'perfect_accuracy', False)),
                dispersion_angle=dispersion_angle)
        end = start + direction.scale(5000.0)
        shot = self._descriptor_shot(entity.typeDescriptor, shell_index)
        target_record = None
        target_collisions = None
        distance = 999999.0
        for record in self._records.values():
            if record.get('local'):
                continue
            target = self._server_entity(record['engine_id'])
            if (target is None or not getattr(target, 'isStarted', False) or
                    not self._record_alive(record, target) or
                    (record.get('presentation') and
                     not bool(record.get('spot_visible', False)))):
                continue
            result = target.collideSegmentExt(start, end)
            if not result:
                continue
            nearest = min(result, key=lambda item: float(item.dist))
            if nearest.dist < distance:
                distance = float(nearest.dist)
                target_record = record
                target_collisions = tuple(result)
        # Destructible submission mutates the native scene immediately.  First
        # resolve the nearest vehicle without applying damage, then cap the
        # world/destructible ray at that vehicle.  A prop behind the target
        # must not be destroyed before the existing world/vehicle ordering
        # chooses which surface the shell actually reaches.
        scene_end = end
        if target_record is not None and target_collisions is not None:
            scene_end = start + direction.scale(
                max(0.0, min(5000.0, distance)))
        scene = self._resolve_shot_scene(
            start, scene_end, direction, shot)
        penetration_factor = scene.get('penetration_factor')
        world_distance = scene['world_distance']
        if (target_record is None or target_collisions is None or
                scene.get('stopped_by_destructible') or
                distance > world_distance + _SHOT_OCCLUSION_EPSILON):
            if (combat_rules.is_he(shot) and
                    world_distance < 4999.5):
                self._he_splash(
                    start + direction.scale(world_distance), shot, shot_seq,
                    None, 'player', self.client.player_id,
                    self._server.vehicle_id)
            return
        if penetration_factor is None:
            penetration_factor = combat_rules.sample_penetration_factor()
        target = self._server_entity(target_record['engine_id'])
        damage, result = self._shell_damage(
            entity.typeDescriptor, target_collisions, distance,
            shell_index=shell_index,
            pierce_loss=scene['piercing_loss'],
            penetration_factor=penetration_factor,
            target_descriptor=getattr(target, 'typeDescriptor', None))
        impact = start + direction.scale(distance)
        hull_damage = damage
        damage, critical = self._critical_hit(
            target, entity.typeDescriptor, target_collisions, start, end,
            damage, result, entity.id, shell_index)
        critical_contract = self._critical_proposal_contract(
            target_record, critical, hull_damage)
        if target_record.get('kind') == 'bot':
            self.client.send_bot_hit(
                target_record['network_id'], shot_seq, damage, result,
                _xyz(impact), critical, **critical_contract)
        else:
            self.client.send_hit(
                target_record['network_id'], shot_seq, damage, result,
                shell_index or 0,
                _xyz(impact), critical, **critical_contract)
        if combat_rules.is_he(shot):
            self._he_splash(
                impact, shot, shot_seq, target_record, 'player',
                self.client.player_id, self._server.vehicle_id)

    @staticmethod
    def _descriptor_shot(descriptor, shell_index=None):
        shots = tuple(descriptor.gun.shots or ())
        if shell_index is None:
            shell_index = getattr(descriptor, 'activeGunShotIndex', 0)
        index = max(0, min(int(shell_index), max(0, len(shots) - 1)))
        return shots[index] if shots else {}

    def _resolve_shot_scene(self, start, end, direction, shot,
                            penetration_factor=None,
                            initial_piercing_loss=0.0,
                            distance_offset=0.0):
        """Traverse exact destructibles in order before the capped endpoint."""
        maximum = (end - start).length
        initial_piercing_loss = max(
            0.0, _number(initial_piercing_loss, 0.0))
        distance_offset = max(0.0, _number(distance_offset, 0.0))
        if self._destructibles is None:
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID, start, end, 128)
            return {
                'world_distance': ((hit[0] - start).length
                                   if hit is not None else 999999.0),
                'piercing_loss': initial_piercing_loss,
                'stopped_by_destructible': False,
                'penetration_factor': penetration_factor,
            }
        travelled = 0.0
        piercing_loss = initial_piercing_loss
        for unused_index in range(64):
            cursor = start + direction.scale(travelled)
            result = self._destructibles.shot_world_distance(
                self._runtime.bigworld, self._avatar.spaceID,
                cursor, end, direction, shot)
            if not isinstance(result, dict):
                raise RuntimeError(
                    '#1513 destructible shot result must be a dictionary')
            added_loss = max(0.0, _number(
                result.get('piercing_loss'), 0.0))
            piercing_loss += added_loss
            if added_loss > 0.0 and penetration_factor is None:
                penetration_factor = (
                    combat_rules.sample_penetration_factor())
            # Range falloff is evaluated where the shell enters/hits the
            # destructible.  ``continue_from`` is the proved OBB exit and is
            # used only to advance the next ray; using it here would charge a
            # thick object extra range before applying its fixed 25 mm loss.
            loss_distance = result.get('loss_distance')
            if loss_distance is None:
                loss_distance = result.get('continue_from')
            obstacle_distance = travelled + max(
                0.0, _number(loss_distance, 0.0))
            if (result.get('continue_from') is not None and
                    penetration_factor is not None and
                    combat_rules.sampled_piercing(
                        shot, distance_offset + obstacle_distance,
                        penetration_factor,
                        piercing_loss) < 1.0):
                return {
                    'world_distance': obstacle_distance,
                    'piercing_loss': piercing_loss,
                    'stopped_by_destructible': True,
                    'penetration_factor': penetration_factor,
                }
            stop_distance = result.get('stop_distance')
            if stop_distance is not None:
                return {
                    'world_distance': travelled + max(
                        0.0, _number(stop_distance)),
                    'piercing_loss': piercing_loss,
                    'stopped_by_destructible': bool(
                        result.get('stopped_by_destructible')),
                    'penetration_factor': penetration_factor,
                }
            advance = result.get('continue_from')
            if advance is None:
                world_distance = _number(
                    result.get('world_distance'), 999999.0)
                return {
                    'world_distance': (travelled + world_distance
                                       if world_distance < 99999.0
                                       else 999999.0),
                    'piercing_loss': piercing_loss,
                    'stopped_by_destructible': False,
                    'penetration_factor': penetration_factor,
                }
            advance = _number(advance)
            if advance <= 0.0:
                raise RuntimeError(
                    '#1513 destructible shot traversal did not advance')
            travelled += advance
            if travelled >= maximum:
                return {'world_distance': 999999.0,
                        'piercing_loss': piercing_loss,
                        'stopped_by_destructible': False,
                        'penetration_factor': penetration_factor}
        raise RuntimeError('#1513 destructible shot traversal exceeded 64 hits')

    def _he_splash(self, burst_position, shot, shot_seq, direct_record,
                   attacker_kind, attacker_id, attacker_engine_id):
        """Port 0.8.2 `_offh_he_splash` through #1513 Vehicle rays."""
        radius = combat_rules.he_radius(shot)
        if radius <= 0.0 or burst_position is None:
            return 0
        hit_count = 0
        legacy_shell = combat_rules.legacy_shot(shot).get('shell') or {}
        for record in tuple(self._records.values()):
            if record is direct_record or record.get('tombstone'):
                continue
            target = self._server_entity(record['engine_id'])
            if (target is None or target.typeDescriptor is None or
                    not getattr(target, 'isStarted', False) or
                    _number(getattr(target, 'health', 0.0)) <= 0.0):
                continue
            position = _xyz(getattr(target, 'position', record.get('state', {})))
            dx = position[0] - burst_position.x
            dy = position[1] - burst_position.y
            dz = position[2] - burst_position.z
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if distance > radius:
                continue
            aim = self._vector((position[0], position[1] + 1.0,
                                position[2]))
            collisions = ()
            try:
                collisions = tuple(
                    target.collideSegmentExt(burst_position, aim) or ())
                nominal = combat_rules.he_nominal_armor(
                    collisions, target.typeDescriptor)
            except Exception:
                collisions = ()
                nominal = combat_rules.he_hull_armor(target.typeDescriptor)
            damage = combat_rules.he_splash_damage(
                shot, nominal, distance / radius)
            if damage <= 0:
                continue
            hull_damage = damage
            damage, critical = critical_damage.propose_direct(
                target, combat_rules.collision_layers(collisions),
                burst_position, self._vector(position), damage,
                legacy_shell, attacker_engine_id, penetrated=False,
                by_explosion=True)
            if self._send_splash_hit(
                    record, attacker_kind, attacker_id, shot_seq, damage,
                    hull_damage, burst_position, critical):
                hit_count += 1
        return hit_count

    def _send_splash_hit(self, target_record, attacker_kind, attacker_id,
                         shot_seq, damage, hull_damage, burst_position,
                         critical):
        impact = _xyz(burst_position)
        critical_contract = self._critical_proposal_contract(
            target_record, critical, hull_damage)
        if attacker_kind == 'player':
            if target_record.get('kind') == 'bot':
                return self.client.send_bot_hit(
                    target_record['network_id'], shot_seq, damage, 2,
                    impact, critical, splash=True, **critical_contract)
            return self.client.send_hit(
                target_record['network_id'], shot_seq, damage, 2,
                self._gun_state.shot_index if self._gun_state else 0,
                impact, critical, splash=True, **critical_contract)
        if target_record.get('kind') == 'bot':
            return self.client.send_bot_bot_hit(
                attacker_id, target_record['network_id'], shot_seq,
                damage, 2, impact, critical, splash=True,
                **critical_contract)
        return self.client.send_bot_human_hit(
            attacker_id, target_record['network_id'], shot_seq,
            damage, 2, impact, critical, splash=True,
            **critical_contract)

    def _critical_hit(self, target, source_descriptor, collisions,
                      start, end, damage, result, attacker_id,
                      shell_index=None):
        """Adapt #1513 collision objects to the copied 0.8.2 crit loop."""
        if target is None or getattr(target, 'typeDescriptor', None) is None:
            return damage, None
        shots = tuple(source_descriptor.gun.shots or ())
        if shell_index is None:
            shell_index = getattr(source_descriptor, 'activeGunShotIndex', 0)
        index = max(0, min(int(shell_index), max(0, len(shots) - 1)))
        shot = shots[index] if shots else {}
        shell = (combat_rules.legacy_shot(shot).get('shell') or {})
        return critical_damage.propose_direct(
            target, combat_rules.collision_layers(collisions),
            start, end, damage, shell, attacker_id,
            penetrated=int(result) == 2)

    def _shell_damage(self, descriptor, collisions, distance,
                      shell_index=None, pierce_loss=0.0,
                      penetration_factor=None, target_descriptor=None):
        shots = tuple(descriptor.gun.shots or ())
        if shell_index is None:
            shell_index = getattr(descriptor, 'activeGunShotIndex', 0)
        index = max(0, min(int(shell_index),
                           max(0, len(shots) - 1)))
        shot = shots[index] if shots else {}
        resolved = combat_rules.resolve_hull_hit(
            shot, distance, collisions, pierce_loss=pierce_loss,
            penetration_factor=penetration_factor)
        # 0.8.2 law: a round that never reaches structure is a non-penetration,
        # and an HE round still detonates on the part it did reach.
        result = 1 if resolved is None else resolved[0]
        armor = combat_rules.he_nominal_armor(collisions, target_descriptor)
        return combat_rules.damage(shot, result, armor), result

    def _defer_avatar_leave(self):
        """Finish the native leaveArena stack before retiring its Avatar."""
        generation = self._generation
        server = self._server
        on_local_leave = self._on_local_leave

        def leave_after_mailbox_returns():
            if (generation == self._generation and
                    server is self._server):
                if callable(on_local_leave):
                    on_local_leave()
                else:
                    self.stop(show_login=False)

        self._runtime.bigworld.callback(0.0, leave_after_mailbox_returns)

    def stop(self, show_login=False, restore_account=True):
        if self.state in ('idle', 'stopped'):
            return
        self._generation += 1
        self._cancel_callbacks()
        cleanup_error = None
        try:
            self._cleanup()
        except Exception as error:
            cleanup_error = error
        # Mark ownership closed even when native Account reconstruction fails;
        # otherwise this runtime rejects every later start as still running.
        self.state = 'stopped'
        if cleanup_error is not None:
            raise cleanup_error
        if restore_account:
            # A LAN transport failure is not a WoT account disconnect.
            # OfflineMapCreator.destroy() removed the Avatar and the fake
            # connection needs a replacement Account.  Account.showGUI owns
            # the eventual native showLobby transition after synchronization;
            # calling g_appLoader here would race and duplicate it.
            self._runtime.compatibility.restore_lobby_account()

    def _cancel_callbacks(self):
        self._callback_token = None
        self._ammo_callback_token = None
        for callback_id in (self._callback_id, self._ammo_callback_id):
            if callback_id is not None:
                try:
                    self._runtime.bigworld.cancelCallback(callback_id)
                except Exception:
                    pass
        self._callback_id = None
        self._ammo_callback_id = None

    def _cleanup(self):
        cleanup_error = None
        if self._projectiles is not None:
            try:
                self._projectiles.reset(max(
                    self._projectiles.now, self._clock()))
            except Exception as error:
                cleanup_error = error
        self._projectiles = None
        self._projectile_meta = {}
        self._projectile_visual_meta = {}
        self._projectile_terminal_data = {}
        self._projectile_target_positions = {}
        self._projectile_lineage = set()
        if self._artillery is not None:
            self._artillery.reset()
        self._artillery = None
        try:
            self._release_postmortem_visibility()
        except Exception as error:
            cleanup_error = error
        try:
            self._runtime.compatibility.set_control_mode_listener(None)
        except Exception as error:
            cleanup_error = error
        if self._sixth_sense is not None:
            try:
                self._sixth_sense.reset()
            except Exception as error:
                cleanup_error = error
            self._sixth_sense = None
        try:
            self._detach_local_presentation()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        # Remote presentations are separate OfflineEntity visuals and must be
        # detached before the stock Avatar tears down the battle space.  The
        # local stock Vehicle remains owned by Avatar/OfflineMapCreator.
        if self._remote_factory is not None:
            try:
                self._clear_target_outline()
                for record in tuple(self._records.values()):
                    if record.get('presentation'):
                        self._stop_remote_visual(record)
                self._remote_factory.destroy_all()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            self._remote_factory = None
        self._records = {}
        if self._map_create_attempted:
            creator = self._runtime.offline_map_creator
            retained_space_id = getattr(
                creator, '_OfflineMapCreator__spaceId', 0)
            retained_mapping_id = getattr(
                creator, '_OfflineMapCreator__spaceMappingId', 0)
            try:
                self._runtime.compatibility.retire_current_player()
            except Exception as error:
                cleanup_error = error
            # Native retirement and stock map ownership are independent
            # cleanup boundaries.  A partial onBecomeNonPlayer failure must
            # not prevent OfflineMapCreator from releasing its entity, space,
            # mapping and camera ids.
            try:
                creator.destroy()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            space_error = self._release_retained_client_space(
                retained_space_id, retained_mapping_id)
            if space_error is not None and cleanup_error is None:
                cleanup_error = space_error
            player, player_error = self._read_engine_player()
            if player_error is not None and cleanup_error is None:
                cleanup_error = player_error
            if player is not None:
                # Exact OfflineMapCreator.destroy() catches its own teardown
                # exception and calls cancel(), losing the ids while a zombie
                # Avatar may remain.  Retry the engine-owned clear directly
                # and verify the ownership boundary before restoring Account.
                clear_error = self._force_clear_engine_player(
                    'stock map teardown retained the Avatar')
                if clear_error is not None and cleanup_error is None:
                    cleanup_error = clear_error
        elif self._lobby_retire_started:
            # HangarSpace.destroy() is itself a destructive boundary.  A
            # later failure in the engine-wide clear must not leave the old
            # Account alive: restore_lobby_account() would treat it as valid
            # and skip rebuilding the now-destroyed HangarSpace.
            cleanup_error = self._force_clear_engine_player(
                'lobby teardown retained the Account')
        try:
            self._runtime.compatibility.deactivate_map()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        try:
            self._restore_battle_gui_guard()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        if self._destructibles is not None:
            try:
                self._destructibles.set_event_sink(None)
                self._destructibles.reset()
                self._destructibles.set_catalog(None)
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
        self._map_create_attempted = False
        self._lobby_retire_started = False
        self._avatar = None
        self._binding = None
        self._server = None
        self._remote_factory = None
        self._sender = None
        self._sync = None
        self._bots = None
        if self._frame_diagnostics is not None:
            self._frame_diagnostics.reset()
        self._has_sixth_sense = False
        self._last_snapshot = None
        self._last_frame_time = None
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._vehicle_ready_deadline = 0.0
        self._bot_fire_seen = {}
        self._bot_destructible_samples = {}
        self._local_speed = 0.0
        self._local_turn_speed = 0.0
        self._local_push_x = 0.0
        self._local_push_z = 0.0
        self._local_physics = None
        self._local_matrix = None
        self._local_model = None
        self._local_native_matrix = None
        self._local_native_stabilised_matrix = None
        self._local_camera_velocity = None
        self._local_engine_mode = None
        self._spectated_engine_id = None
        self._local_grind = 0
        self._local_vertical_speed = 0.0
        self._local_airborne = False
        self._local_fall_armed = False
        self._local_last_pitch = 0.0
        self._local_drive_pitch_history = None
        self._local_smooth_drive_pitch = 0.0
        self._local_slide_speed = 0.0
        self._local_downhill = (0.0, 0.0, 0.0)
        self._local_slope_tangent = 0.0
        self._local_air_lateral = (0.0, 0.0)
        self._local_pitch = 0.0
        self._local_roll = 0.0
        self._input_accumulator = 0.0
        self._gun_state = None
        self._gun_last_tick = None
        self._ammo_signature = None
        self._targeting_signature = None
        self._equipment_state = None
        self._battle_result = None
        self._round_finished_notified = False
        self._on_local_leave = None
        self._arena_type = None
        self._spawn_planner = None
        self._navigation_graph = None
        self._grounded_bot_ids = set()
        self._bot_vehicle_assignments = {}
        self._rules_state = {'bases': {}}
        self._destructibles = None
        self._local_damage_report = None
        self._local_critical_base_revision = 0
        self._local_critical_server_revision = 0
        self._local_critical_next_seq = 0
        self._local_critical_owned = False
        self._accepted_event_ids = set()
        self._applied_event_ids = set()
        self._seen_event_ids = self._applied_event_ids
        self._event_journal = []
        self._local_last_attacker = None
        self._next_critical_report_time = 0.0
        self._last_presented_rpm = None
        self._next_rpm_time = 0.0
        self._drown_check = 0.0
        self._drown_time = 0.0
        self._drown_level = 0
        self._drown_started = None
        self._battle_live = False
        self._prebattle_deadline = None
        self._next_spotting_time = 0.0
        self._foliage = None
        if cleanup_error is not None:
            raise cleanup_error

    def _release_retained_client_space(self, space_id, mapping_id=0):
        """Close the exact #1513 space even if stock destroy lost its id."""
        try:
            space_id = int(space_id or 0)
            mapping_id = int(mapping_id or 0)
        except (TypeError, ValueError):
            return RuntimeError('stock map teardown exposed invalid space ids')
        if space_id <= 0:
            return None
        is_client_space = getattr(
            self._runtime.bigworld, 'isClientSpace', None)
        if not callable(is_client_space):
            return None
        try:
            retained = bool(is_client_space(space_id))
        except Exception as error:
            return error
        if not retained:
            return None
        first_error = None
        if mapping_id > 0:
            remove_mapping = getattr(
                self._runtime.bigworld, 'delSpaceGeometryMapping', None)
            if callable(remove_mapping):
                try:
                    remove_mapping(space_id, mapping_id)
                except Exception as error:
                    first_error = error
        for name in ('clearSpace', 'releaseSpace'):
            function = getattr(self._runtime.bigworld, name, None)
            if not callable(function):
                if first_error is None:
                    first_error = RuntimeError(
                        'BigWorld.%s is unavailable' % name)
                continue
            try:
                function(space_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
        try:
            retained = bool(is_client_space(space_id))
        except Exception as error:
            if first_error is None:
                first_error = error
            retained = True
        if retained and first_error is None:
            first_error = RuntimeError(
                'stock map teardown retained client space %s' % space_id)
        return first_error

    def _read_engine_player(self):
        try:
            return self._runtime.bigworld.player(), None
        except ReferenceError:
            return None, None
        except Exception as error:
            return None, error

    def _force_clear_engine_player(self, retained_message):
        first_error = None
        found_clear = False
        player = None
        try:
            self._runtime.compatibility.retire_current_player()
        except Exception as error:
            first_error = error
        for name in ('clearEntitiesAndSpaces', 'clearAllSpaces'):
            clear = getattr(self._runtime.bigworld, name, None)
            if not callable(clear):
                continue
            found_clear = True
            succeeded = False
            try:
                clear()
                succeeded = True
            except Exception as error:
                if first_error is None:
                    first_error = error
            player, player_error = self._read_engine_player()
            if player_error is not None and first_error is None:
                first_error = player_error
            if succeeded and player_error is None and player is None:
                return first_error
        if not found_clear:
            return RuntimeError('no engine entity-clear boundary is available')
        if player is not None:
            return RuntimeError(retained_message)
        return first_error

    def _fail(self, error):
        active_traceback = None
        if sys.exc_info()[0] is not None:
            active_traceback = traceback.format_exc()
        self.error = str(error)
        self._generation += 1
        self._cancel_callbacks()
        cleanup_error = None
        try:
            self._cleanup()
        except Exception as cleanup_failure:
            cleanup_error = cleanup_failure
            self.error = '%s; cleanup failed: %s' % (
                self.error, cleanup_failure)
        self.state = 'failed'
        # Asynchronous map/entity failures happen after OfflineMapCreator has
        # replaced the lobby Account.  Recover the same boundary as a normal
        # round exit, but report it separately from a LAN transport failure so
        # the waiting-room socket can survive a local map construction error.
        lobby_restored = False
        if cleanup_error is None:
            try:
                self._runtime.compatibility.restore_lobby_account()
                lobby_restored = True
            except Exception as restore_failure:
                self.error = '%s; lobby restore failed: %s' % (
                    self.error, restore_failure)
        if not lobby_restored:
            # A failed cleanup/restore cannot remain LOGGED_ON without a
            # valid Account or Avatar.  Retire the fake WoT connection here;
            # LANSession owns only its socket/picker and must not recurse into
            # this native runtime boundary.
            try:
                self._runtime.compatibility.disconnect()
            except Exception as disconnect_failure:
                self.error = '%s; offline disconnect failed: %s' % (
                    self.error, disconnect_failure)
        callback = getattr(self.client, 'on_event', None)
        if callable(callback):
            try:
                callback('battle_failed', {
                    'message': self.error,
                    'round_id': (self._start_message or {}).get('round_id'),
                    'lobby_restored': lobby_restored,
                })
            except Exception:
                # A recovery notification is not allowed to replace the first
                # native failure or escape into the LAN poll callback.
                pass
        sys.stdout.write('[Offline LAN 0.9.22] battle failed: %s\n' %
                         self.error)
        if active_traceback is not None:
            sys.stdout.write(
                '[Offline LAN 0.9.22] battle traceback:\n%s' %
                active_traceback)


g_battle_runtime = BattleRuntime()
