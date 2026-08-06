"""Verified #1513-facing BigWorld operations for :mod:`entities.runtime`.

The phase layout and wire shapes are an original implementation informed by
the observable flow in AvatarServer.py and VehicleMover.py from
https://github.com/the-tuxedo-cat/WoT-Offline-Server/tree/c0bc550c46deac980194b7b860ee8781d53ec97b
(Boost Software License 1.0; no source code copied).  In particular, local movement physics is
intentionally not migrated: pose snapshots and input are passed to the
server-facing owner instead.

The #1513 Vehicle.def, Vehicle.py and ClientArena.py establish the property
names, the 18-item vehicle tuple, and its compression behavior.  BigWorld's
native entity creation result remains a runtime capability: ``self_check``
must pass against the target client before this binding may create an entity.
"""

from __future__ import print_function

import math
try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle
import zlib

try:
    _integer_types = (int, long)
    _unicode_types = (unicode,)
except NameError:
    _integer_types = (int,)
    _unicode_types = ()

# BigWorld ``STRING`` is Python 2 ``str``, not ``unicode``.  LAN messages are
# decoded as UTF-8 before json.loads(), so every server-provided name reaches
# this boundary as unicode on the embedded 2.7 runtime.  Normalize all STRING
# members before handing the property dictionary to createEntity().
_entity_string_types = (str,)


def _entity_string(value):
    if _unicode_types and isinstance(value, _unicode_types):
        return value.encode('utf-8')
    return value


class CapabilityError(RuntimeError):
    pass


class BigWorldVehicleBinding(object):
    """Concrete binding used by the asynchronous ``BattleRuntime``.

    Dependencies are injected so the capability contract can be tested
    outside BigWorld.  In the game loader pass ``BigWorld``, the player Avatar,
    constants, ``VehicleDescr`` and ``encodeGunAngles`` explicitly.
    """

    PROPERTY_NAMES = (
        'publicInfo', 'gunAnglesPacked', 'health', 'isCrewActive',
        'steeringAngle', 'isStrafing', 'physicsMode', 'siegeState',
        'engineMode', 'damageStickers', 'publicStateModifiers', 'stunInfo')

    def __init__(self, bigworld, avatar, constants, vehicle_descr_class,
                 encode_gun_angles, server_input=None, outfit_provider=None):
        self._bigworld = bigworld
        self._avatar = avatar
        self._constants = constants
        self._vehicle_descr_class = vehicle_descr_class
        self._encode_gun_angles = encode_gun_angles
        self._server_input = server_input
        self._outfit_provider = outfit_provider

    def self_check(self):
        self._need(self._bigworld, 'createEntity')
        self._need(self._bigworld, 'destroyEntity')
        self._need(self._bigworld, 'entity')
        self._need(self._avatar, 'spaceID')
        self._need(self._avatar, 'updateArena')
        self._need(self._avatar, 'syncVehicleAttrs')
        self._need(self._avatar, 'onVehicleChanged')
        self._need(self._constants, 'ARENA_UPDATE')
        self._need(self._constants, 'ARENA_PERIOD')
        self._need(self._constants, 'VEHICLE_PHYSICS_MODE')
        self._need(self._constants, 'VEHICLE_SIEGE_STATE')
        for name in ('VEHICLE_ADDED', 'VEHICLE_KILLED', 'AVATAR_READY',
                     'PERIOD'):
            self._need(self._constants.ARENA_UPDATE, name)
        self._need(self._constants.ARENA_PERIOD, 'BATTLE')
        self._need(self._constants.VEHICLE_PHYSICS_MODE, 'STANDARD')
        self._need(self._constants.VEHICLE_SIEGE_STATE, 'DISABLED')
        if not callable(self._vehicle_descr_class):
            raise CapabilityError('VehicleDescr factory is unavailable')
        if not callable(self._encode_gun_angles):
            raise CapabilityError('encodeGunAngles is unavailable')
        if not callable(self._outfit_provider):
            raise CapabilityError('verified outfit provider is unavailable')
        return True

    def properties_from_compact_descr(self, compact_descr, team, name):
        self.self_check()
        descriptor = self._vehicle_descr_class(compactDescr=compact_descr)
        return self._properties_from_descriptor(descriptor, team, name)

    def _properties_from_descriptor(self, descriptor, team, name):
        self._need(descriptor, 'makeCompactDescr')
        self._need(descriptor, 'maxHealth')
        self._need(descriptor, 'gun')
        self._need(descriptor, 'turret')
        self._need(descriptor.gun, 'pitchLimits')
        self._need(descriptor.turret, 'circularVisionRadius')
        pitch_limits = descriptor.gun.pitchLimits
        if not isinstance(pitch_limits, dict) or 'absolute' not in pitch_limits:
            raise CapabilityError('VehicleDescr.gun.pitchLimits.absolute unavailable')
        return {
            'publicInfo': {
                'compDescr': _entity_string(descriptor.makeCompactDescr()),
                'name': _entity_string(name),
                'team': team,
                'prebattleID': 0,
                'marksOnGun': 0,
                'index': 0,
                'outfit': _entity_string(self._outfit_provider(descriptor))},
            'gunAnglesPacked': self._encode_gun_angles(
                0, 0, pitch_limits['absolute']),
            'health': descriptor.maxHealth,
            'isCrewActive': True,
            'steeringAngle': 0.0,
            'isStrafing': False,
            'physicsMode': self._constants.VEHICLE_PHYSICS_MODE.STANDARD,
            'siegeState': self._constants.VEHICLE_SIEGE_STATE.DISABLED,
            'engineMode': (0, 0),
            'damageStickers': [],
            'publicStateModifiers': (),
            'stunInfo': 0.0}

    def create_vehicle(self, properties, position, rotation):
        self.self_check()
        self._validate_properties(properties)
        return self._bigworld.createEntity(
            'Vehicle', self._avatar.spaceID, 0, position, rotation, properties)

    def arena_vehicle_added(self, entity_id, snapshot):
        properties = self._snapshot_properties(snapshot)
        self._avatar.updateArena(
            self._constants.ARENA_UPDATE.VEHICLE_ADDED,
            self._pack_vehicle_arena_info(entity_id, properties))

    def arena_vehicle_removed(self, entity_id):
        # #1513 has no ARENA_UPDATE.VEHICLE_REMOVED and ClientArena has no
        # corresponding update handler.  Entity destruction (or the complete
        # arena teardown) is the only exact removal boundary in this build.
        return None

    def arena_vehicle_killed(self, entity_id, attacker_id=0, reason=0):
        """Publish the exact uncompressed #1513 ClientArena kill tuple."""
        payload = (int(entity_id), int(attacker_id), 0, int(reason))
        self._avatar.updateArena(self._constants.ARENA_UPDATE.VEHICLE_KILLED,
                                 _pickle.dumps(payload))

    def avatar_select_vehicle(self, entity_id):
        """Select the local id before stock PlayerAvatar enter handling."""
        self._set_avatar_property('playerVehicleID', entity_id)

    def avatar_vehicle_entered(self):
        """Notify consumers only after the Vehicle is visible in-world."""
        self._avatar.onVehicleChanged()

    def avatar_client_ready(self):
        self._set_avatar_property('isGunLocked', False)
        self._set_avatar_property('ownVehicleAuxPhysicsData', 0)
        self._set_avatar_property('ownVehicleGear', 0)
        entity = self._entity_or_fail(self._avatar.playerVehicleID)
        self._need(entity, 'typeDescriptor')
        self._need(entity.typeDescriptor, 'turret')
        self._need(entity.typeDescriptor.turret, 'circularVisionRadius')
        self._avatar.syncVehicleAttrs({'circularVisionRadius':
            entity.typeDescriptor.turret.circularVisionRadius})

    def avatar_ready(self):
        self._avatar.updateArena(self._constants.ARENA_UPDATE.AVATAR_READY,
                                 _pickle.dumps(self._avatar.playerVehicleID))

    def arena_period(self, period):
        if period != 'battle':
            raise CapabilityError('only explicit battle period is supported')
        payload = (self._constants.ARENA_PERIOD.BATTLE, 0, 0, [])
        self._avatar.updateArena(self._constants.ARENA_UPDATE.PERIOD,
                                 zlib.compress(_pickle.dumps(payload)))

    def update_vehicle(self, entity_id, position, rotation):
        entity = self._entity_or_fail(entity_id)
        self._need(entity, 'teleport')
        entity.teleport(position, rotation)

    def update_vehicle_aim(self, entity_id, hull_yaw, aim_yaw, gun_pitch):
        """Apply a network world aim to the exact packed Vehicle property."""
        entity = self._entity_or_fail(entity_id)
        self._need(entity, 'gunAnglesPacked')
        self._need(entity, 'typeDescriptor')
        self._need(entity.typeDescriptor, 'gun')
        self._need(entity.typeDescriptor.gun, 'pitchLimits')
        pitch_limits = entity.typeDescriptor.gun.pitchLimits
        if not isinstance(pitch_limits, dict) or 'absolute' not in pitch_limits:
            raise CapabilityError('Vehicle gun pitch limits are unavailable')
        relative_yaw = ((float(aim_yaw) - float(hull_yaw) + math.pi) %
                        (2.0 * math.pi) - math.pi)
        packed = self._encode_gun_angles(
            relative_yaw, float(gun_pitch), pitch_limits['absolute'])
        self._require_int('gunAnglesPacked', packed, 0, 65535)
        previous = entity.gunAnglesPacked
        entity.gunAnglesPacked = packed
        notifier = getattr(entity, 'set_gunAnglesPacked', None)
        if callable(notifier):
            notifier(previous)

    def send_vehicle_input(self, entity_id, command):
        if self._server_input is None:
            raise CapabilityError('server input bridge is unavailable')
        self._server_input(entity_id, dict(command))

    def destroy_entity(self, entity_id):
        self._bigworld.destroyEntity(entity_id)

    def is_vehicle_ready(self, entity_id):
        """Return only after BigWorld has materialized the Vehicle in-world.

        ``createEntity`` returns the client-only id before Vehicle resource
        prerequisites finish.  During ``Vehicle.onEnterWorld`` the id can
        already be bound to the Avatar while ``BigWorld.entity(id)`` is still
        unavailable.  Native consumers use the same entity + inWorld gate.
        """
        try:
            entity = self._bigworld.entity(entity_id)
            return (entity is not None and
                    bool(getattr(entity, 'inWorld', False)) and
                    bool(getattr(entity, 'isStarted', False)) and
                    getattr(entity, 'typeDescriptor', None) is not None)
        except ReferenceError:
            return False

    def _pack_vehicle_arena_info(self, entity_id, properties):
        """Exact #1513 ClientArena 18-item vehicle-list shape."""
        public_info = properties['publicInfo']
        is_alive = (int(properties.get('health', 0)) > 0 and
                    bool(properties.get('isCrewActive', True)))
        values = [entity_id, public_info['compDescr'], public_info['name'],
                  public_info['team'], is_alive, False, False, 1, '', 0,
                  public_info['prebattleID'], False, False, {}, 0, [], 0, {}]
        return zlib.compress(_pickle.dumps(values))

    def _snapshot_properties(self, snapshot):
        if not isinstance(snapshot, dict) or 'properties' not in snapshot:
            raise CapabilityError('Vehicle snapshot properties are required')
        self._validate_properties(snapshot['properties'])
        return snapshot['properties']

    def _validate_properties(self, properties):
        if not isinstance(properties, dict):
            raise CapabilityError('Vehicle properties must be a dict')
        names = set(properties)
        expected = set(self.PROPERTY_NAMES)
        if names != expected:
            raise CapabilityError('Vehicle property contract mismatch')
        public_info = properties['publicInfo']
        if not isinstance(public_info, dict):
            raise CapabilityError('Vehicle publicInfo must be a dict')
        required = set(('compDescr', 'name', 'team', 'prebattleID', 'marksOnGun',
                        'index', 'outfit'))
        if set(public_info) != required:
            raise CapabilityError('Vehicle publicInfo contract mismatch')
        for name in ('compDescr', 'name', 'outfit'):
            if not isinstance(public_info[name], _entity_string_types):
                raise CapabilityError('Vehicle publicInfo.%s must be STRING' %
                                      name)
        for name in ('team', 'marksOnGun', 'index'):
            self._require_int('publicInfo.' + name, public_info[name], 0, 255)
        self._require_int('publicInfo.prebattleID',
                          public_info['prebattleID'], 0, 2147483647)
        self._require_int('gunAnglesPacked', properties['gunAnglesPacked'],
                          0, 65535)
        self._require_int('health', properties['health'], -32768, 32767)
        for name in ('isCrewActive', 'isStrafing'):
            if not isinstance(properties[name], bool):
                raise CapabilityError('Vehicle %s must be BOOL' % name)
        self._require_number('steeringAngle', properties['steeringAngle'])
        self._require_int('physicsMode', properties['physicsMode'], 0, 255)
        self._require_int('siegeState', properties['siegeState'], 0, 255)
        engine_mode = properties['engineMode']
        if not isinstance(engine_mode, tuple) or len(engine_mode) != 2:
            raise CapabilityError('Vehicle engineMode must be a 2-item TUPLE')
        for index, value in enumerate(engine_mode):
            self._require_int('engineMode[%d]' % index, value, 0, 255)
        for name in ('damageStickers', 'publicStateModifiers'):
            if not isinstance(properties[name], (list, tuple)):
                raise CapabilityError('Vehicle %s must be an ARRAY' % name)
        self._require_number('stunInfo', properties['stunInfo'])

    def _require_int(self, name, value, minimum, maximum):
        if (isinstance(value, bool) or
                not isinstance(value, _integer_types) or
                value < minimum or value > maximum):
            raise CapabilityError('Vehicle %s is outside integer schema' % name)

    def _require_number(self, name, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise CapabilityError('Vehicle %s must be numeric' % name)
        if math.isnan(value) or math.isinf(value):
            raise CapabilityError('Vehicle %s must be finite' % name)

    def _set_avatar_property(self, name, value):
        self._need(self._avatar, name)
        previous = getattr(self._avatar, name)
        setattr(self._avatar, name, value)
        notifier = getattr(self._avatar, 'set_' + name, None)
        if notifier is not None:
            notifier(previous)

    def _entity_or_fail(self, entity_id):
        entity = self._bigworld.entity(entity_id)
        if entity is None:
            raise CapabilityError('Vehicle entity %s is unavailable' % entity_id)
        return entity

    def _need(self, value, name):
        if not hasattr(value, name):
            raise CapabilityError('required #1513 capability missing: %s' % name)
        return getattr(value, name)
