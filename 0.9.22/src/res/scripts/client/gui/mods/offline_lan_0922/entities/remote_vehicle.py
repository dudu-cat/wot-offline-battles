from __future__ import print_function

"""0.8.2-style authoritative remote vehicles on the #1513 renderer.

The retail ``Vehicle`` entity is the local player's physics carrier.  A
remote retail Vehicle expects server-owned filter snapshots that an offline
client cannot manufacture through the public #1513 API.  The mature 0.8.2
battle therefore kept a Python vehicle object for gameplay and attached its
model to a separate ``OfflineEntity``.  This module preserves that boundary,
while using #1513's verified compound-model assembler.
"""

import math
import weakref
from collections import namedtuple

from gui.mods.offline_lan_0922 import tank_collision

try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)


# Exact value contract returned by #1513 ``Vehicle.collideSegment``.  The
# stock ProjectileMover uses both tuple indexing and these named fields.
_SegmentCollisionResult = namedtuple(
    'SegmentCollisionResult', ('dist', 'hitAngleCos', 'armor'))

# Exact value contract returned by #1513 ``Vehicle.collideSegmentExt``.
# ``AvatarInputHandler.gun_marker_ctrl`` reads all four named fields on every
# gun-rotator tick.  A Python object carrying the component descriptor under a
# private adapter name is not equivalent: the missing ``compName`` aborts the
# native tick and freezes mouse aim, dispersion and target-lock feedback.
_SegmentCollisionResultExt = namedtuple(
    'SegmentCollisionResultExt',
    ('dist', 'hitAngleCos', 'matInfo', 'compName'))


class _AliveFlag(object):

    def __init__(self, value=True):
        self.value = bool(value)

    def __call__(self):
        return self.value

    def __nonzero__(self):
        return self.value

    def __bool__(self):
        return self.value


class _Signal(object):
    """Small Event-compatible signal for marker consumers."""

    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        if handler not in self._handlers:
            self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass
        return self

    def __call__(self, *args, **kwargs):
        for handler in tuple(self._handlers):
            handler(*args, **kwargs)


class _ModelsDescription(object):

    def __init__(self, owner):
        self._owner = owner

    def __getitem__(self, unused_name):
        return {'model': self._owner.model}


class _DamageState(object):

    isCurrentModelDamaged = False


class _RemoteEngineAudition(object):
    """Exact sound-object boundary consumed by #1513 shot effects."""

    def __init__(self, owner):
        self._owner = owner
        self._objects = {}

    def getSoundObject(self, index):
        model = self._owner.model
        if model is None:
            raise RuntimeError('remote vehicle sound requested without model')
        index = int(index)
        sound_object = self._objects.get(index)
        if sound_object is not None:
            return sound_object
        import SoundGroups
        factory = getattr(SoundGroups.g_instance, 'WWgetSoundObject', None)
        if not callable(factory):
            raise RuntimeError('#1513 WWgetSoundObject is unavailable')
        node = model.node('HP_gunFire')
        sound_object = factory(
            'offline_lan_vehicle_%d_sound_%d' % (self._owner.id, index),
            node)
        if sound_object is None:
            raise RuntimeError('#1513 remote vehicle sound object is missing')
        self._objects[index] = sound_object
        return sound_object

    def detach(self):
        self._objects.clear()


class _RemoteAppearance(object):

    def __init__(self, math_module, owner):
        self._math = math_module
        self._owner = owner
        self.onModelChanged = _Signal()
        self.turretMatrix = math_module.Matrix()
        self.turretMatrix.setIdentity()
        self.gunMatrix = math_module.Matrix()
        self.gunMatrix.setIdentity()
        self.compoundModel = None
        self.models = []
        self.modelsDesc = _ModelsDescription(owner)
        # setupTurretRotations reads this exact CompoundAppearance contract.
        self.damageState = _DamageState()
        self.isLoaded = False
        self.isInWater = False
        self.gunRecoil = None
        self.engineAudition = _RemoteEngineAudition(owner)

    @property
    def typeDescriptor(self):
        return self._owner.typeDescriptor

    def attach(self, model):
        self.compoundModel = model
        self.models = [model]
        self.isLoaded = True
        self.onModelChanged()

    def detach(self):
        self.engineAudition.detach()
        self.compoundModel = None
        self.models = []
        self.isLoaded = False
        self.onModelChanged()

    def changeVisibility(self, visible):
        """Expose the exact #1513 CompoundAppearance visibility boundary."""
        if self.compoundModel is None:
            raise RuntimeError('remote compound model is unavailable')
        self.compoundModel.visible = bool(visible)
        return True

    def showDamageFromShot(self, *unused_args, **unused_kwargs):
        return None

    def showDamageFromExplosion(self, *unused_args, **unused_kwargs):
        return None

    def recoil(self):
        recoil = self.gunRecoil
        callback = getattr(recoil, 'recoil', None)
        if callable(callback):
            callback()


class _RemoteShotPresenter(object):
    """Shared #1513 tracer and gun-recoil resources for remote vehicles.

    Muzzle flash and shot sound stay on the stock ``ShowShooting`` extra.
    The retail client receives a separate tracer message online, so the
    offline relay must also feed ``ProjectileMover`` explicitly.  This is the
    #1513 adaptation of the mature 0.8.2 remote-shot presentation path.
    """

    def __init__(self, bigworld, math_module, model_assembler):
        self._bigworld = bigworld
        self._math = math_module
        self._model_assembler = model_assembler
        self._mover = None
        self._next_shot_id = 1000000
        self._projectile_shots = {}
        self._closed = False

    def setup_recoil(self, vehicle):
        if vehicle.model is None or vehicle.typeDescriptor is None:
            return None
        assemble = getattr(self._model_assembler, 'assembleRecoil', None)
        if not callable(assemble):
            return None
        try:
            # #1513 replaced 0.8.2's WGGunRecoil fashion with the compound
            # model assembler's Vehicular.RecoilAnimator.  ``None`` is the
            # same valid no-LOD-link value accepted by createGunAnimator.
            assemble(vehicle.appearance, None)
            recoil = vehicle.appearance.gunRecoil
            vehicle._gun_recoil = recoil
            return recoil
        except Exception:
            vehicle._gun_recoil = None
            return None

    def setup_turret_rotations(self, vehicle):
        setup = getattr(self._model_assembler, 'setupTurretRotations', None)
        if not callable(setup):
            raise RuntimeError(
                '#1513 model assembler has no setupTurretRotations')
        # Updating Matrix values alone does not move compound-model nodes.
        # This is the exact binding used by CompoundAppearance after refresh.
        setup(vehicle.appearance)

    def play_tracer(self, vehicle):
        if self._closed or vehicle.model is None:
            return False
        canonical_names = (
            '_offlineLANShotOrigin', '_offlineLANShotVelocity',
            '_offlineLANShotGravity', '_offlineLANShotMaxDistance')
        if all(hasattr(vehicle, name) for name in canonical_names):
            shot_id = self.play_canonical(
                vehicle.typeDescriptor, vehicle._offlineLANShotIndex,
                vehicle._offlineLANShotOrigin,
                vehicle._offlineLANShotVelocity,
                vehicle._offlineLANShotGravity,
                vehicle._offlineLANShotMaxDistance, vehicle.id,
                getattr(vehicle, '_offlineLANProjectileID', None),
                getattr(
                    vehicle, '_offlineLANShotReferenceOrigin', None),
                getattr(
                    vehicle, '_offlineLANShotReferenceVelocity', None))
            return bool(shot_id)
        shot = self._active_shot(vehicle)
        speed = _component_value(shot, 'speed')
        gravity = _component_value(shot, 'gravity')
        if shot is None or speed is None or gravity is None:
            return False
        try:
            start = self._muzzle_position(vehicle)
            direction = self._direction(vehicle)
            velocity = direction.scale(float(speed))
            maximum = _component_value(shot, 'maxDistance', 5000.0)
            shot_id = self.play_canonical(
                vehicle.typeDescriptor, vehicle._offlineLANShotIndex,
                start, velocity, gravity, maximum or 5000.0, vehicle.id)
            # Preserve the pre-ledger RemoteVehicle.showShooting contract.
            # Canonical callers receive the stable visual id itself.
            return bool(shot_id)
        except Exception:
            return False

    def play_canonical(self, descriptor, shell_index, origin, velocity,
                       gravity, max_distance, attacker_id,
                       projectile_id=None, reference_position=None,
                       reference_velocity=None):
        """Present one canonical launch through the exact #1513 mover ABI.

        The origin and velocity belong to the authoritative launch event.
        They must never be recomputed from the vehicle's presentation pose,
        which may already have advanced by the time a relay event arrives.
        Invalid native-boundary values fail closed before ProjectileMover is
        constructed or called.
        """
        if self._closed:
            return False
        shot = self._shot_at(descriptor, shell_index)
        shell = _component_value(shot, 'shell')
        effects_index = _component_value(shell, 'effectsIndex')
        if shot is None or shell is None or effects_index is None:
            return False
        visual_start = self._finite_vector(origin)
        reference_start = (visual_start if reference_position is None else
                           self._finite_vector(reference_position))
        reference_velocity = self._finite_vector(
            velocity if reference_velocity is None else reference_velocity)
        gravity = self._finite_float(gravity)
        maximum = self._finite_float(max_distance)
        try:
            attacker_id = int(attacker_id)
        except (TypeError, ValueError, OverflowError):
            return False
        if projectile_id is not None:
            try:
                projectile_id = str(projectile_id)
            except Exception:
                return False
            if not projectile_id or len(projectile_id) > 128:
                return False
            existing = self._projectile_shots.get(projectile_id)
            if existing is not None:
                return existing
            if len(self._projectile_shots) >= 128:
                return False
        if (visual_start is None or reference_start is None or
                reference_velocity is None or gravity is None or
                maximum is None or gravity < 0.0 or maximum <= 0.0 or
                attacker_id <= 0):
            return False
        try:
            from items import vehicles
            effects_descr = vehicles.g_cache.shotEffects[effects_index]
            if effects_descr is None:
                return False
            mover = self._projectile_mover()
            if mover is None:
                return False
            camera = getattr(self._bigworld, 'camera', None)
            camera = camera() if callable(camera) else None
            camera_position = self._finite_vector(
                getattr(camera, 'position', None))
            if camera_position is None:
                camera_position = reference_start
            shot_id = self._next_shot_id
            self._next_shot_id += 1
            # Exact #1513 ABI:
            # add(id, effects, gravity, refStart, refVelocity, start,
            #     maxDistance, attackerID, tracerCameraPos)
            mover.add(
                shot_id, effects_descr, gravity, reference_start,
                reference_velocity, visual_start, maximum, attacker_id,
                camera_position)
            if projectile_id is not None:
                self._projectile_shots[projectile_id] = shot_id
            return shot_id
        except Exception:
            return False

    def stop_canonical(self, projectile_id, end_position,
                       explosion=None):
        """Hide one authoritative tracer at its canonical terminal point.

        ``explosion`` carries ``(effectsDescr, effectMaterial, velocityDir)``
        for a terminal on the world.  Retail plays the ground explosion from
        ``ProjectileMover.explode``; ``hide`` deliberately clears
        ``showExplosion``, so hiding alone can never produce one.  A vehicle
        terminal passes None, because retail plays only the ``armorHit`` family
        through the vehicle's own bound effects there.
        """
        if self._closed or projectile_id is None:
            return False
        try:
            projectile_id = str(projectile_id)
        except Exception:
            return False
        shot_id = self._projectile_shots.get(projectile_id)
        end = self._finite_vector(end_position)
        if shot_id is None or end is None:
            return False
        self._projectile_shots.pop(projectile_id, None)
        mover = self._mover
        callback = getattr(mover, 'hide', None) if mover is not None else None
        if not callable(callback):
            return False
        try:
            callback(shot_id, end)
        except Exception:
            return False
        self._explode_canonical(mover, shot_id, end, explosion)
        return True

    def _explode_canonical(self, mover, shot_id, end, explosion):
        """Play the retail ground explosion for one world terminal.

        ``hide`` re-keys the entry, so ``explode`` no longer finds the
        projectile and takes its own synthetic-record branch, which reaches
        ``__addExplosionEffect`` immediately.  The only difference from a
        server-driven explosion is that the effect carries no attacker id.
        """
        if not explosion:
            return False
        explode = getattr(mover, 'explode', None)
        if not callable(explode):
            return False
        try:
            effects_descr, effect_material, velocity = explosion
        except (TypeError, ValueError):
            return False
        if not effects_descr or not effect_material:
            return False
        # An artillery-strike descriptor makes ProjectileMover.explode return
        # before it plays anything; never pretend that produced an effect.
        try:
            if 'artilleryID' in effects_descr:
                return False
        except TypeError:
            return False
        direction = self._finite_vector(velocity)
        if direction is None:
            return False
        try:
            if direction.length <= 0.0:
                return False
            direction.normalise()
            explode(shot_id, effects_descr, str(effect_material), end,
                    direction)
        except Exception:
            return False
        return True

    @staticmethod
    def _finite_float(value):
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    def _finite_vector(self, value):
        if value is None:
            return None
        try:
            components = (value.x, value.y, value.z)
        except AttributeError:
            try:
                components = (value[0], value[1], value[2])
            except (TypeError, IndexError, KeyError):
                return None
        components = tuple(self._finite_float(item) for item in components)
        if None in components:
            return None
        try:
            return self._math.Vector3(*components)
        except Exception:
            return None

    def _projectile_mover(self):
        if self._mover is None and not self._closed:
            try:
                from ProjectileMover import ProjectileMover
                self._mover = ProjectileMover()
            except Exception:
                return None
        return self._mover

    def _muzzle_position(self, vehicle):
        try:
            node = vehicle.model.node('HP_gunFire')
            return self._math.Vector3(self._math.Matrix(node).translation)
        except Exception:
            position = self._math.Vector3(vehicle.position)
            position.y += 1.5
            return position

    def _direction(self, vehicle):
        # The regular aim matrix uses negative pitch to raise the barrel.
        # A bot_shot event can temporarily provide the already-dispersed
        # physical ray, whose pitch is positive upward.  The tracer and
        # authority collision resolver must consume that same ray.
        shot_pitch = getattr(vehicle, '_offlineLANShotPitch', None)
        if shot_pitch is None:
            pitch = -float(getattr(vehicle, '_gun_pitch', 0.0) or 0.0)
        else:
            pitch = float(shot_pitch)
        yaw = float(getattr(
            vehicle, '_offlineLANShotYaw',
            getattr(vehicle, '_aim_yaw', vehicle.yaw)) or 0.0)
        horizontal = math.cos(pitch)
        direction = self._math.Vector3(
            math.sin(yaw) * horizontal, math.sin(pitch),
            math.cos(yaw) * horizontal)
        direction.normalise()
        return direction

    @staticmethod
    def _active_shot(vehicle):
        descriptor = vehicle.typeDescriptor
        gun = getattr(descriptor, 'gun', None)
        shots = tuple(_component_value(gun, 'shots', ()) or ())
        if not shots:
            return None
        try:
            index = int(getattr(
                vehicle, '_offlineLANShotIndex',
                getattr(descriptor, 'activeGunShotIndex', 0)) or 0)
        except (TypeError, ValueError):
            index = 0
        return shots[max(0, min(index, len(shots) - 1))]

    @staticmethod
    def _shot_at(descriptor, shell_index):
        gun = getattr(descriptor, 'gun', None)
        shots = tuple(_component_value(gun, 'shots', ()) or ())
        if not shots:
            return None
        try:
            index = int(shell_index)
        except (TypeError, ValueError, OverflowError):
            return None
        if index < 0 or index >= len(shots):
            return None
        return shots[index]

    def destroy(self):
        self._closed = True
        self._projectile_shots = {}
        mover = self._mover
        self._mover = None
        if mover is not None:
            callback = getattr(mover, 'destroy', None)
            if callable(callback):
                callback()


class _RemoteFilter(object):

    # ProjectileMover only uses this as a broad-phase rejection before asking
    # RemoteVehicle.collideSegment for the descriptor hit-test.  Twenty metres
    # encloses every #1513 tank, including its gun, without making the precise
    # collision result less authoritative.
    _BROAD_PHASE_RADIUS_SQUARED = 20.0 * 20.0

    def __init__(self, math_module, position):
        self._math = math_module
        self.position = math_module.Vector3(position)
        self.velocity = math_module.Vector3(0.0, 0.0, 0.0)
        self.speed = 0.0

    def update(self, position, velocity):
        self.position = self._math.Vector3(position)
        self.velocity = self._math.Vector3(velocity)
        try:
            self.speed = float(self.velocity.length)
        except Exception:
            self.speed = 0.0

    def segmentMayHitEntity(self, startPoint, endPoint, skipGun):
        """Implement the exact three-argument #1513 vehicle-filter ABI."""
        unused_skip_gun = skipGun
        dx = float(endPoint.x) - float(startPoint.x)
        dy = float(endPoint.y) - float(startPoint.y)
        dz = float(endPoint.z) - float(startPoint.z)
        px = float(self.position.x) - float(startPoint.x)
        py = float(self.position.y) - float(startPoint.y)
        pz = float(self.position.z) - float(startPoint.z)
        length_squared = dx * dx + dy * dy + dz * dz
        if length_squared <= 1e-9:
            fraction = 0.0
        else:
            fraction = (px * dx + py * dy + pz * dz) / length_squared
            fraction = max(0.0, min(1.0, fraction))
        offset_x = px - dx * fraction
        offset_y = py - dy * fraction
        offset_z = pz - dz * fraction
        return (offset_x * offset_x + offset_y * offset_y +
                offset_z * offset_z <= self._BROAD_PHASE_RADIUS_SQUARED)


def _component_value(component, name, default=None):
    if isinstance(component, dict):
        return component.get(name, default)
    return getattr(component, name, default)


def _pose_components(vehicle, math_module):
    """Build descriptor hit-test transforms from one visible vehicle pose."""
    descriptor = vehicle.typeDescriptor
    result = []
    identity = math_module.Matrix()
    identity.setIdentity()
    result.append((descriptor.chassis, identity))

    hull_offset = _component_value(
        descriptor.chassis, 'hullPosition',
        math_module.Vector3(0.0, 0.0, 0.0))
    hull = math_module.Matrix()
    hull.setTranslate(-hull_offset)
    result.append((descriptor.hull, hull))

    turret_positions = _component_value(
        descriptor.hull, 'turretPositions', ())
    turret_offset = (turret_positions[0] if turret_positions else
                     math_module.Vector3(0.0, 0.0, 0.0))
    turret = math_module.Matrix()
    turret.setTranslate(-hull_offset - turret_offset)
    rotation = math_module.Matrix()
    turret_yaw = math_module.Matrix(vehicle.appearance.turretMatrix).yaw
    rotation.setRotateY(-turret_yaw)
    turret.postMultiply(rotation)
    result.append((descriptor.turret, turret))

    gun_offset = _component_value(
        descriptor.turret, 'gunPosition',
        math_module.Vector3(0.0, 0.0, 0.0))
    gun = math_module.Matrix()
    gun.setTranslate(-gun_offset)
    rotation = math_module.Matrix()
    gun_pitch = math_module.Matrix(vehicle.appearance.gunMatrix).pitch
    rotation.setRotateX(-gun_pitch)
    gun.postMultiply(rotation)
    gun.preMultiply(turret)
    result.append((descriptor.gun, gun))
    return result


def collide_vehicle_at_matrix(vehicle, vehicle_matrix, start_point,
                              end_point, math_module):
    """Run precise descriptor collision at the supplied visible matrix.

    #1513's native ``Vehicle.collideSegmentExt`` first rejects rays through
    the retail ``WGVehicleFilter``. Copied 0.8.2 physics deliberately leaves
    that filter at the spawn pose, so local incoming shots must use the live
    presentation matrix instead. Remote vehicles use the same routine to
    keep outgoing and incoming collision geometry identical.
    """
    world_to_vehicle = math_module.Matrix(vehicle_matrix)
    world_to_vehicle.invert()
    start = world_to_vehicle.applyPoint(start_point)
    end = world_to_vehicle.applyPoint(end_point)
    hits = []
    for component, component_matrix in _pose_components(
            vehicle, math_module):
        tester = _component_value(component, 'hitTester')
        local_hit_test = getattr(tester, 'localHitTest', None)
        if not callable(local_hit_test):
            continue
        collisions = local_hit_test(
            component_matrix.applyPoint(start),
            component_matrix.applyPoint(end))
        for collision in collisions or ():
            try:
                dist, unused_triangle, angle_cos, material_kind = collision
            except (TypeError, ValueError):
                continue
            materials = _component_value(component, 'materials', {}) or {}
            material = materials.get(material_kind)
            component_name = _component_value(component, 'itemTypeName')
            if not isinstance(component_name, _STRING_TYPES):
                raise RuntimeError(
                    '#1513 collision component has no itemTypeName')
            hits.append(_SegmentCollisionResultExt(
                float(dist), float(angle_cos), material, component_name))
    hits.sort(key=lambda item: item.dist)
    return hits


class RemoteVehicle(object):
    """Python gameplay identity separated from its OfflineEntity visual."""

    _offlineLANPresentation = True

    def __init__(self, entity_id, descriptor, properties, position, rotation,
                 math_module, shot_presenter=None):
        self.id = int(entity_id)
        self.typeDescriptor = descriptor
        self.vehicleTypeDescriptor = descriptor
        self.publicInfo = dict(properties.get('publicInfo') or {})
        self.team = int(self.publicInfo.get('team', 0) or 0)
        self.health = int(properties.get('health', descriptor.maxHealth))
        self.maxHealth = int(descriptor.maxHealth)
        self.isCrewActive = bool(properties.get('isCrewActive', True))
        self.isAlive = _AliveFlag(self.health > 0 and self.isCrewActive)
        self.isPlayerVehicle = False
        self.isStarted = False
        self.inWorld = False
        self.isObserver = False
        self.isStrafing = False
        self.steeringAngle = 0.0
        self.gunAnglesPacked = int(properties.get('gunAnglesPacked', 0))
        self.physicsMode = properties.get('physicsMode', 0)
        self.siegeState = properties.get('siegeState', 0)
        self.engineMode = properties.get('engineMode', (0, 0))
        self.damageStickers = properties.get('damageStickers', [])
        self.publicStateModifiers = properties.get(
            'publicStateModifiers', ())
        self.stunInfo = properties.get('stunInfo', 0.0)
        self.last_killer_id = 0
        self.last_shot = None
        self.last_shot_effect = None
        self.model = None
        self.bw_entity = None
        self.bw_entity_id = None
        self.load_error = None
        self._math = math_module
        self._shot_presenter = shot_presenter
        self._gun_recoil = None
        self._collision_obstacle = None
        self._offlineLANShotIndex = int(
            getattr(descriptor, 'activeGunShotIndex', 0) or 0)
        # Stock BigWorld.entity()/entities are presentation/AOI lookups. The
        # LAN authority uses RemoteVehicleFactory.get() explicitly, so an
        # unspotted enemy never leaks into native aiming or ProjectileMover.
        self._spot_visible = False
        self._postmortem_visible = False
        # helpers.EntityExtra stores each running stock extra on the entity.
        # Without this dictionary the #1513 shoot extra cannot start.
        self.extras = {}
        self.position = math_module.Vector3(position)
        self.yaw = float(rotation[2])
        self.pitch = float(rotation[1])
        self.roll = float(rotation[0])
        self.matrix = math_module.Matrix()
        self.filter = _RemoteFilter(math_module, self.position)
        self.appearance = _RemoteAppearance(math_module, self)
        self.proxy = weakref.proxy(self)
        self._aim_yaw = self.yaw
        self._gun_pitch = 0.0
        self._last_pose_time = None
        self._update_matrix()

    def _update_matrix(self):
        self.matrix.setRotateYPR((self.yaw, self.pitch, self.roll))
        self.matrix.translation = self.position

    def attach_visual(self, entity, entity_id, model):
        self.bw_entity = entity
        self.bw_entity_id = int(entity_id)
        self.model = model
        # PyCompoundModel has no ordinary Model position/yaw/pitch/roll
        # attributes.  Exact #1513 CompoundAppearance links the compound to
        # the vehicle's live matrix provider and then mutates that provider.
        self.model.matrix = self.matrix
        self.appearance.attach(model)
        if self._shot_presenter is not None:
            self._shot_presenter.setup_turret_rotations(self)
            self._shot_presenter.setup_recoil(self)
        self.set_pose(self.position, (self.roll, self.pitch, self.yaw))
        self.isStarted = True
        self.inWorld = True

    def attach_wreck_model(self, model):
        """Swap this vehicle onto its loaded #1513 destroyed compound."""
        self._stop_shooting_effect()
        previous = self.model
        if previous is not None:
            previous.matrix = self._math.Matrix()
        self.appearance.detach()
        self.appearance.gunRecoil = None
        self._gun_recoil = None
        self.bw_entity.model = model
        self.model = model
        self.model.matrix = self.matrix
        self.appearance.attach(model)
        self.appearance.damageState.isCurrentModelDamaged = True
        if self._shot_presenter is not None:
            self._shot_presenter.setup_turret_rotations(self)
        return True

    def detach_visual(self):
        self._stop_shooting_effect()
        self._collision_obstacle = None
        self.isStarted = False
        self.inWorld = False
        model = self.model
        entity = self.bw_entity
        first_error = None
        try:
            if model is not None:
                # Match CompoundAppearance.deactivate(): sever the live world
                # provider before the OfflineEntity releases the model.
                model.matrix = self._math.Matrix()
        except Exception as error:
            first_error = error
        try:
            if entity is not None:
                entity.model = None
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            self.appearance.detach()
        except Exception as error:
            if first_error is None:
                first_error = error
        self.appearance.gunRecoil = None
        self.bw_entity = None
        self.bw_entity_id = None
        self.model = None
        self._gun_recoil = None
        if first_error is not None:
            raise first_error

    def set_pose(self, position, rotation):
        previous = self.position
        self.position = self._math.Vector3(position)
        self.roll = float(rotation[0])
        self.pitch = float(rotation[1])
        self.yaw = float(rotation[2])
        self._update_matrix()
        velocity = self.position - previous
        self.filter.update(self.position, velocity)

    def set_aim(self, hull_yaw, aim_yaw, gun_pitch):
        relative = ((float(aim_yaw) - float(hull_yaw) + math.pi) %
                    (2.0 * math.pi) - math.pi)
        self._aim_yaw = float(aim_yaw)
        self._gun_pitch = float(gun_pitch)
        self.appearance.turretMatrix.setRotateYPR((relative, 0.0, 0.0))
        self.appearance.gunMatrix.setRotateYPR(
            (0.0, self._gun_pitch, 0.0))

    def set_health(self, previous):
        self.isAlive.value = self.health > 0 and self.isCrewActive

    def set_isCrewActive(self, previous):
        self.isAlive.value = self.health > 0 and self.isCrewActive

    def onHealthChanged(self, health, attacker_id=0, reason_id=0):
        self.health = int(health)
        self.last_killer_id = int(attacker_id or 0)
        self.isAlive.value = self.health > 0 and self.isCrewActive

    def set_gunAnglesPacked(self, unused_previous):
        return None

    def showShooting(self, burst_count=1, is_predicted=False):
        if (not self.isStarted or not self.inWorld or self.model is None or
                not self.isAlive()):
            return False
        self.last_shot = (int(burst_count), bool(is_predicted))
        native_started = self._start_shooting_effect(max(1, int(burst_count)))
        tracer_started = bool(
            self._shot_presenter is not None and
            self._shot_presenter.play_tracer(self))
        self.last_shot_effect = (native_started, tracer_started)
        return native_started or tracer_started

    def _shoot_extra(self):
        extras = getattr(self.typeDescriptor, 'extrasDict', None)
        if extras is None:
            return None
        try:
            return extras.get('shoot')
        except AttributeError:
            try:
                return extras['shoot']
            except (KeyError, TypeError):
                return None

    def _start_shooting_effect(self, burst_count):
        extra = self._shoot_extra()
        if extra is None:
            return False
        extra.stopFor(self)
        extra.startFor(self, int(burst_count))
        return True

    def _stop_shooting_effect(self):
        extra = self._shoot_extra()
        if extra is not None:
            try:
                extra.stopFor(self)
            except Exception:
                pass
        self.extras.clear()

    def showAmmoBayEffect(self, *unused_args):
        return None

    def getSpeed(self):
        return float(self.filter.speed)

    def getAutorotation(self):
        return False

    def getComponents(self):
        return _pose_components(self, self._math)

    def collideSegmentExt(self, start_point, end_point):
        return collide_vehicle_at_matrix(
            self, self.matrix, start_point, end_point, self._math)

    def collideSegment(self, start_point, end_point, skipGun=False):
        hits = self.collideSegmentExt(start_point, end_point)
        if skipGun:
            hits = [item for item in hits
                    if item.compName != 'vehicleGun']
        if not hits:
            return None
        closest = hits[0]
        armor = getattr(closest.matInfo, 'armor', 0)
        return _SegmentCollisionResult(
            closest.dist, closest.hitAngleCos, armor)


class Vehicle(RemoteVehicle):
    """#1513 ProjectileMover ABI identity for remote presentations.

    EntityCollisionData classifies vehicles by the exact Python class name.
    Keeping the adapter as a real subclass preserves our private registry API
    while exposing the stock collision identity expected by gun markers.
    """

    pass


def _native_visible(vehicle):
    if vehicle is None:
        return False
    alive = getattr(vehicle, 'isAlive', None)
    alive = alive() if callable(alive) else bool(alive)
    return bool(
        getattr(vehicle, '_spot_visible', False) and
        getattr(vehicle, 'isStarted', False) and
        getattr(vehicle, 'inWorld', False) and
        getattr(vehicle, 'model', None) is not None and alive)


def _postmortem_visible(vehicle):
    if vehicle is None:
        return False
    alive = getattr(vehicle, 'isAlive', None)
    alive = alive() if callable(alive) else bool(alive)
    return bool(
        getattr(vehicle, '_postmortem_visible', False) and
        getattr(vehicle, 'isStarted', False) and
        getattr(vehicle, 'inWorld', False) and
        getattr(vehicle, 'model', None) is not None and alive)


class _EntitiesView(object):

    def __init__(self, original, registry):
        self._original = original
        self._registry = registry

    def __getitem__(self, key):
        try:
            return self._original[key]
        except KeyError:
            vehicle = self._registry.get(key)
            if (_native_visible(vehicle) or
                    _postmortem_visible(vehicle)):
                return vehicle
            raise

    def __setitem__(self, key, value):
        self._original[key] = value

    def __delitem__(self, key):
        del self._original[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        if key in self._original:
            return True
        vehicle = self._registry.get(key)
        return (_native_visible(vehicle) or
                _postmortem_visible(vehicle))

    def keys(self):
        result = list(self._original.keys())
        # PostMortemControlMode.__changeVehicle checks ``entities.keys()``
        # before publishing its camera-change event.  Expose only the one
        # runtime-validated observed ally; ordinary AOI enumeration remains
        # the native registry and does not leak other synthetic identities.
        for entity_id, vehicle in self._registry.items():
            if (_postmortem_visible(vehicle) and
                    entity_id not in self._original):
                result.append(entity_id)
        return result

    def values(self):
        return self._original.values()

    def items(self):
        return self._original.items()

    def iteritems(self):
        return self._original.iteritems()

    def itervalues(self):
        return self._original.itervalues()

    def __iter__(self):
        return iter(self._original)

    def __len__(self):
        return len(self._original)

    def __getattr__(self, name):
        return getattr(self._original, name)


class RemoteVehicleFactory(object):
    """Load, register and destroy authoritative remote presentations."""

    def __init__(self, bigworld, math_module, model_assembler, space_id):
        self._bigworld = bigworld
        self._math = math_module
        self._model_assembler = model_assembler
        self._space_id = int(space_id)
        self._vehicles = {}
        self._next_id = 1000
        self._original_entity = None
        self._original_entities = None
        self._entity_wrapper = None
        self._entities_wrapper = None
        self._hit_testers = {}
        self._descriptors = {}
        self._shot_presenter = _RemoteShotPresenter(
            bigworld, math_module, model_assembler)
        self.install()

    def install(self):
        if self._original_entity is not None:
            return
        self._original_entity = self._bigworld.entity
        self._original_entities = self._bigworld.entities
        factory = self

        def entity(entity_id):
            original = factory._original_entity(entity_id)
            if original is not None:
                return original
            vehicle = factory._vehicles.get(entity_id)
            if (_native_visible(vehicle) or
                    _postmortem_visible(vehicle)):
                return vehicle
            return None

        self._entity_wrapper = entity
        self._entities_wrapper = _EntitiesView(
            self._original_entities, self._vehicles)
        self._bigworld.entity = entity
        self._bigworld.entities = self._entities_wrapper

    def _allocate_id(self):
        while True:
            entity_id = self._next_id
            self._next_id += 1
            if (entity_id not in self._vehicles and
                    self._original_entity(entity_id) is None):
                return entity_id

    def prepare_descriptor(self, descriptor):
        """Own the BSP testers before any #1513 bbox consumer runs."""
        get_hit_testers = getattr(descriptor, 'getHitTesters', None)
        if not callable(get_hit_testers):
            raise RuntimeError(
                '#1513 vehicle descriptor hit testers are unavailable')
        for tester in get_hit_testers():
            if tester is None:
                raise RuntimeError('#1513 vehicle hit tester is unavailable')
            key = id(tester)
            owned = self._hit_testers.get(key)
            if owned is tester:
                continue
            if owned is not None:
                raise RuntimeError('#1513 vehicle hit tester identity collided')
            load = getattr(tester, 'loadBspModel', None)
            release = getattr(tester, 'releaseBspModel', None)
            if not callable(load) or not callable(release):
                raise RuntimeError(
                    '#1513 vehicle hit tester lifecycle is unavailable')
            try:
                load()
            except Exception as error:
                try:
                    release()
                except Exception as cleanup_error:
                    raise RuntimeError(
                        '#1513 vehicle hit tester BSP load failed: %s; '
                        'cleanup failed: %s' % (error, cleanup_error))
                raise RuntimeError(
                    '#1513 vehicle hit tester BSP load failed: %s' % error)
            if getattr(tester, 'bbox', None) is None:
                try:
                    release()
                finally:
                    raise RuntimeError(
                        '#1513 vehicle hit tester bbox did not load')
            self._hit_testers[key] = tester
        key = id(descriptor)
        owned = self._descriptors.get(key)
        if owned is not None and owned is not descriptor:
            raise RuntimeError('#1513 vehicle descriptor identity collided')
        self._descriptors[key] = descriptor
        return descriptor

    def create(self, descriptor, properties, position, rotation):
        entity_id = self._allocate_id()
        vehicle = Vehicle(
            entity_id, descriptor, properties, position, rotation, self._math,
            self._shot_presenter)
        self._vehicles[entity_id] = vehicle
        try:
            self.prepare_descriptor(descriptor)
            assembler = self._model_assembler.prepareCompoundAssembler(
                descriptor, 'undamaged', self._space_id, False)
            self._bigworld.loadResourceListBG(
                (assembler,), lambda resources:
                self._loaded(entity_id, descriptor, resources))
        except Exception as error:
            vehicle.load_error = error
        return entity_id

    @staticmethod
    def _resource(resources, name):
        if name in getattr(resources, 'failedIDs', ()):
            return None
        try:
            return resources[name]
        except Exception:
            return None

    def _loaded(self, entity_id, descriptor, resources):
        vehicle = self._vehicles.get(entity_id)
        if vehicle is None:
            return
        visual_id = None
        visual = None
        try:
            model = self._resource(resources, descriptor.name)
            if model is None:
                model = self._resource(resources, 'chassis')
            if model is None:
                raise RuntimeError('compound model resource is missing')
            visual_id = self._bigworld.createEntity(
                'OfflineEntity', self._space_id, 0, vehicle.position,
                (vehicle.roll, vehicle.pitch, vehicle.yaw), {})
            try:
                visual = self._original_entities[visual_id]
            except Exception:
                visual = self._original_entity(visual_id)
            if visual is None:
                raise RuntimeError('OfflineEntity did not enter the space')
            visual.model = model
            vehicle.attach_visual(visual, visual_id, model)
        except Exception as error:
            # createEntity succeeded before the Python presentation took
            # ownership. Roll that operation back transactionally or the
            # callback leaves an untracked OfflineEntity in the battle space.
            try:
                if vehicle.bw_entity_id == visual_id:
                    vehicle.detach_visual()
                elif visual is not None:
                    visual.model = None
            except Exception:
                vehicle.bw_entity = None
                vehicle.bw_entity_id = None
                vehicle.model = None
                vehicle.isStarted = False
                vehicle.inWorld = False
            if visual_id is not None:
                try:
                    self._bigworld.destroyEntity(visual_id)
                except Exception:
                    pass
            vehicle.load_error = error

    def request_wreck(self, entity_id):
        """Reload one destroyed remote vehicle on its native wreck models.

        #1513 selects the wreck through ``VehicleDamageState`` model state
        ``destroyed``; the hit testers are read from the descriptor, so shell
        collision survives the swap.
        """
        vehicle = self._vehicles.get(entity_id)
        if (vehicle is None or vehicle.model is None or
                vehicle.typeDescriptor is None or
                vehicle.appearance.damageState.isCurrentModelDamaged):
            return False
        # Claim the state before the asynchronous load so a repeated health
        # event cannot queue a second refresh for the same vehicle.
        vehicle.appearance.damageState.isCurrentModelDamaged = True
        descriptor = vehicle.typeDescriptor
        try:
            assembler = self._model_assembler.prepareCompoundAssembler(
                descriptor, 'destroyed', self._space_id, False)
            self._bigworld.loadResourceListBG(
                (assembler,), lambda resources:
                self._wreck_loaded(entity_id, descriptor, resources))
        except Exception as error:
            vehicle.appearance.damageState.isCurrentModelDamaged = False
            vehicle.load_error = error
            return False
        return True

    def _wreck_loaded(self, entity_id, descriptor, resources):
        vehicle = self._vehicles.get(entity_id)
        if (vehicle is None or vehicle.bw_entity is None or
                vehicle.model is None):
            return
        model = self._resource(resources, descriptor.name)
        if model is None:
            model = self._resource(resources, 'chassis')
        if model is None:
            # Keep the undamaged compound rather than lose the wreck cover.
            vehicle.appearance.damageState.isCurrentModelDamaged = False
            return
        try:
            vehicle.attach_wreck_model(model)
        except Exception as error:
            vehicle.load_error = error

    def get(self, entity_id):
        return self._vehicles.get(entity_id)

    def is_ready(self, entity_id):
        vehicle = self._vehicles.get(entity_id)
        return bool(vehicle is not None and vehicle.isStarted and
                    vehicle.inWorld and vehicle.model is not None)

    def error(self, entity_id):
        vehicle = self._vehicles.get(entity_id)
        return getattr(vehicle, 'load_error', None)

    def play_projectile_tracer(self, descriptor, shell_index, origin,
                               velocity, gravity, max_distance, attacker_id,
                               projectile_id=None, reference_position=None,
                               reference_velocity=None):
        """Play one authoritative launch without consulting a vehicle pose."""
        return self._shot_presenter.play_canonical(
            descriptor, shell_index, origin, velocity, gravity,
            max_distance, attacker_id, projectile_id,
            reference_position, reference_velocity)

    def stop_projectile_tracer(self, projectile_id, end_position,
                               explosion=None):
        """Retire one canonical tracer after a server terminal event."""
        return self._shot_presenter.stop_canonical(
            projectile_id, end_position, explosion)

    def destroy(self, entity_id):
        vehicle = self._vehicles.pop(entity_id, None)
        if vehicle is None:
            return False
        visual_id = vehicle.bw_entity_id
        first_error = None
        try:
            vehicle.detach_visual()
        except Exception as error:
            first_error = error
            vehicle.bw_entity = None
            vehicle.bw_entity_id = None
            vehicle.model = None
            vehicle.isStarted = False
            vehicle.inWorld = False
        if visual_id is not None:
            try:
                self._bigworld.destroyEntity(visual_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
        return True

    def destroy_all(self):
        first_error = None
        for entity_id in tuple(self._vehicles):
            try:
                self.destroy(entity_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
        for descriptor in tuple(self._descriptors.values()):
            try:
                tank_collision.forget_chassis_shape(descriptor)
            except Exception as error:
                if first_error is None:
                    first_error = error
        self._descriptors = {}
        for tester in tuple(self._hit_testers.values()):
            try:
                tester.releaseBspModel()
            except Exception as error:
                if first_error is None:
                    first_error = error
        self._hit_testers = {}
        try:
            self._shot_presenter.destroy()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            self.restore()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def restore(self):
        if self._original_entity is None:
            return
        if self._bigworld.entity is self._entity_wrapper:
            self._bigworld.entity = self._original_entity
        if self._bigworld.entities is self._entities_wrapper:
            self._bigworld.entities = self._original_entities
        self._original_entity = None
        self._original_entities = None
        self._entity_wrapper = None
        self._entities_wrapper = None
