"""Isolated exact-#1513 experiment using stock remote ``Vehicle`` entities.

The retail entity owns its CompoundAppearance, WGVehicleFilter and
PyTrackScroll.  The LAN server still owns gameplay poses: #1513 exposes no
legal transform setter for a client-created remote Vehicle, so the copied
physics pose is published through the same narrow compatibility overlay used
by the local tank.  This module is selected only by the explicit
``native_remote_vehicles`` experiment flag.
"""

from __future__ import print_function

import math

from gui.mods.offline_lan_0922.entities.remote_vehicle import \
    _RemoteShotPresenter, _blend_angle


_MINIMUM_KEYFRAME_SECONDS = 0.001


class _AimTarget(object):

    def __init__(self, math_module):
        self.turretMatrix = math_module.Matrix()
        self.turretMatrix.setIdentity()
        self.gunMatrix = math_module.Matrix()
        self.gunMatrix.setIdentity()


class _NativeRemoteState(object):

    def __init__(self, math_module, compatibility, position, rotation):
        self._math = math_module
        self._compatibility = compatibility
        self.position = math_module.Vector3(position)
        self.roll = float(rotation[0])
        self.pitch = float(rotation[1])
        self.yaw = float(rotation[2])
        self.matrix = math_module.Matrix()
        self._write_matrix(self.matrix)
        self._key_from = math_module.Matrix(self.matrix)
        self._key_to = math_module.Matrix(self.matrix)
        self._render_pose = (
            float(self.position.x), float(self.position.y),
            float(self.position.z), self.yaw, self.pitch, self.roll)
        self._render_from = None
        self._render_to = None
        self._render_started = 0.0
        self._render_duration = 0.0
        animation_factory = getattr(math_module, 'MatrixAnimation', None)
        self.animation = (animation_factory()
                          if callable(animation_factory) else None)
        self.provider = self.animation or self.matrix
        if self.animation is not None:
            self._rekey(_MINIMUM_KEYFRAME_SECONDS)
        self.aim = _AimTarget(math_module)
        self.entity = None
        self.model_changed = None
        self.track_scroll = None
        self.track_mode = None

    def _write_matrix(self, matrix):
        matrix.setRotateYPR((self.yaw, self.pitch, self.roll))
        matrix.translation = self.position

    def _rekey(self, relax_time):
        if self.animation is None:
            return False
        try:
            self.animation.keyframes = (
                (0.0, self._key_from),
                (max(float(relax_time), _MINIMUM_KEYFRAME_SECONDS),
                 self._key_to))
            self.animation.time = 0.0
        except Exception:
            self.animation = None
            self.provider = self.matrix
            return False
        return True

    @staticmethod
    def _write_pose(matrix, pose):
        matrix.setRotateYPR((pose[3], pose[4], pose[5]))
        matrix.translation = (pose[0], pose[1], pose[2])

    def _mirror_pose(self, now):
        target = self._render_to
        if target is None:
            return self._render_pose
        source = self._render_from
        if source is None or now is None or self._render_duration <= 0.0:
            return target
        ratio = (float(now) - self._render_started) / self._render_duration
        if ratio >= 1.0:
            return target
        ratio = max(0.0, ratio)
        return (
            source[0] + (target[0] - source[0]) * ratio,
            source[1] + (target[1] - source[1]) * ratio,
            source[2] + (target[2] - source[2]) * ratio,
            _blend_angle(source[3], target[3], ratio),
            _blend_angle(source[4], target[4], ratio),
            _blend_angle(source[5], target[5], ratio))

    def _retarget(self, relax_time, now):
        target = (
            float(self.position.x), float(self.position.y),
            float(self.position.z), self.yaw, self.pitch, self.roll)
        relax_time = float(relax_time or 0.0)
        if self.animation is None:
            self._render_pose = target
            self._write_pose(self._key_to, target)
            return False
        current = self._mirror_pose(now)
        if relax_time <= 0.0 or current is None:
            self._render_from = None
            self._render_to = None
            self._render_pose = target
            self._write_pose(self._key_from, target)
            self._write_pose(self._key_to, target)
            return self._rekey(0.0)
        if current == target:
            return False
        self._write_pose(self._key_from, current)
        self._write_pose(self._key_to, target)
        self._render_from = current
        self._render_to = target
        self._render_pose = current
        self._render_started = float(now or 0.0)
        self._render_duration = relax_time
        return self._rekey(relax_time)

    def attach(self, entity):
        self.entity = entity
        entity._offlineNativeRemote = True
        entity._offlineNativeMarkerVisible = True
        entity._aim_yaw = self.yaw
        entity._gun_pitch = 0.0
        entity.team = int(entity.publicInfo['team'])
        entity.bw_entity_id = int(entity.id)
        entity.set_pose = self.set_pose
        entity.set_aim = self.set_aim
        entity.update_tracks = self.update_tracks
        entity.track_scroll_readback = self.track_scroll_readback
        entity.model.matrix = self.provider
        self._compatibility.set_vehicle_pose_overlay(
            entity, self.position, self.yaw, self.provider)
        appearance = entity.appearance
        self.track_scroll = getattr(
            appearance, '_CompoundAppearance__trackScrollCtl', None)
        entity.track_scroll = self.track_scroll
        setup = getattr(appearance, 'setupGunMatrixTargets', None)
        if not callable(setup):
            raise RuntimeError(
                '#1513 CompoundAppearance aim-target boundary is unavailable')
        setup(self.aim)

        def on_model_changed(*unused_args, **unused_kwargs):
            if self.entity is not None and self.entity.appearance is not None:
                self.entity.appearance.setupGunMatrixTargets(self.aim)

        changed = getattr(appearance, 'onModelChanged', None)
        if changed is not None:
            changed += on_model_changed
            self.model_changed = on_model_changed
        return entity

    def set_pose(self, position, rotation, relax_time=None, now=None):
        unused_now = now
        self.position = self._math.Vector3(position)
        self.roll = float(rotation[0])
        self.pitch = float(rotation[1])
        self.yaw = float(rotation[2])
        self._write_matrix(self.matrix)
        self._retarget(relax_time, now)
        entity = self.entity
        if entity is not None:
            entity._aim_yaw = getattr(entity, '_aim_yaw', self.yaw)
            entity.model.matrix = self.provider
            self._compatibility.set_vehicle_pose_overlay(
                entity, self.position, self.yaw, self.provider)
        return True

    def set_aim(self, hull_yaw, aim_yaw, gun_pitch):
        relative = ((float(aim_yaw) - float(hull_yaw) + math.pi) %
                    (2.0 * math.pi) - math.pi)
        self.aim.turretMatrix.setRotateYPR((relative, 0.0, 0.0))
        self.aim.gunMatrix.setRotateYPR((0.0, float(gun_pitch), 0.0))
        if self.entity is not None:
            self.entity._aim_yaw = float(aim_yaw)
            self.entity._gun_pitch = float(gun_pitch)
        return True

    def update_tracks(self, left, right, mode):
        entity = self.entity
        if entity is None or entity.appearance is None:
            return False
        if mode != self.track_mode:
            entity.appearance.changeEngineMode(mode, True)
            self.track_mode = mode
        entity.appearance.updateTracksScroll(float(left), float(right))
        return True

    def track_scroll_readback(self):
        if self.track_scroll is None:
            return None
        result = []
        for name in ('leftScroll', 'rightScroll', 'leftContact',
                     'rightContact'):
            reader = getattr(self.track_scroll, name, None)
            result.append(reader() if callable(reader) else reader)
        return tuple(result)

    def detach(self):
        entity = self.entity
        self.entity = None
        if entity is None:
            return False
        appearance = getattr(entity, 'appearance', None)
        changed = getattr(appearance, 'onModelChanged', None)
        if changed is not None and self.model_changed is not None:
            try:
                changed -= self.model_changed
            except Exception:
                pass
        self.model_changed = None
        self._compatibility.clear_vehicle_pose_overlay(entity)
        return True


class NativeRemoteVehicleFactory(object):
    """Create only real #1513 Vehicle entities; never synthetic compounds."""

    native_entities = True

    def __init__(self, bigworld, math_module, model_assembler, space_id,
                 binding, compatibility, **unused_kwargs):
        unused_space_id = space_id
        self._bigworld = bigworld
        self._math = math_module
        self._binding = binding
        self._compatibility = compatibility
        self._states = {}
        self._vehicles = {}
        self._descriptors = {}
        self._hit_testers = {}
        self.track_animation_error = None
        self._shot_presenter = _RemoteShotPresenter(
            bigworld, math_module, model_assembler)

    def prepare_descriptor(self, descriptor):
        # Stock Vehicle.prerequisites/CompoundAppearance own BSP references.
        self._descriptors[id(descriptor)] = descriptor
        return descriptor

    def create(self, descriptor, properties, position, rotation):
        self.prepare_descriptor(descriptor)
        entity_id = self._binding.create_vehicle(
            properties, position, rotation)
        if entity_id is None:
            raise RuntimeError('native remote createEntity returned no id')
        # Exact #1513 enters client-created Vehicles asynchronously.  A
        # re-entrant entry would let stock startVisual consume a roster entry
        # which cannot yet exist, so reject rather than complete half a tank.
        if self._bigworld.entity(entity_id) is not None:
            self._binding.destroy_entity(entity_id)
            raise RuntimeError(
                'native remote Vehicle entered before createEntity returned')
        self._states[int(entity_id)] = _NativeRemoteState(
            self._math, self._compatibility, position, rotation)
        self._vehicles[int(entity_id)] = None
        self._binding.arena_vehicle_added(entity_id, {
            'properties': properties, 'team_killer': False})
        return int(entity_id)

    def get(self, entity_id):
        entity_id = int(entity_id)
        entity = self._vehicles.get(entity_id)
        if entity is not None:
            return entity
        return self._bigworld.entity(entity_id)

    def is_ready(self, entity_id):
        entity_id = int(entity_id)
        entity = self._bigworld.entity(entity_id)
        if not (entity is not None and
                bool(getattr(entity, 'inWorld', False)) and
                bool(getattr(entity, 'isStarted', False)) and
                getattr(entity, 'model', None) is not None and
                getattr(entity, 'appearance', None) is not None and
                getattr(entity, 'typeDescriptor', None) is not None):
            return False
        if self._vehicles.get(entity_id) is None:
            state = self._states.get(entity_id)
            if state is None:
                return False
            state.attach(entity)
            self._vehicles[entity_id] = entity
        return True

    def error(self, unused_entity_id):
        return None

    def request_wreck(self, unused_entity_id):
        # Vehicle.onHealthChanged owns stock damaged-model replacement.
        return False

    def play_projectile_tracer(self, descriptor, shell_index, origin,
                               velocity, gravity, max_distance, attacker_id,
                               projectile_id=None, reference_position=None,
                               reference_velocity=None):
        return self._shot_presenter.play_canonical(
            descriptor, shell_index, origin, velocity, gravity,
            max_distance, attacker_id, projectile_id,
            reference_position, reference_velocity)

    def stop_projectile_tracer(self, projectile_id, end_position,
                               explosion=None):
        return self._shot_presenter.stop_canonical(
            projectile_id, end_position, explosion)

    def engine_owns(self, entity_id):
        entities = getattr(self._bigworld, 'entities', None)
        lookup = getattr(entities, 'get', None)
        return bool(callable(lookup) and lookup(int(entity_id)) is not None)

    def engine_active(self):
        return any(self.engine_owns(entity_id)
                   for entity_id in self._states)

    def destroy(self, entity_id):
        entity_id = int(entity_id)
        state = self._states.pop(entity_id, None)
        self._vehicles.pop(entity_id, None)
        if state is None:
            return False
        state.detach()
        if self.engine_owns(entity_id):
            self._binding.destroy_entity(entity_id)
        return True

    def destroy_all(self):
        first_error = None
        for entity_id in tuple(self._states):
            try:
                self.destroy(entity_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
        self._descriptors = {}
        try:
            self._shot_presenter.destroy()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error
