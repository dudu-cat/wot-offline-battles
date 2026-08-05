"""Small, explicit entity lifecycle adapter.

This module deliberately has no BigWorld imports.  A future #1513 runtime
binding must supply the exact Vehicle.def property names and the calls which
perform each stage.  Until that binding is verified, this adapter refuses to
invent entity properties.

The lifecycle skeleton follows the observable Vehicle creation, arena update,
client-ready, avatar-ready, battle-period, and tick boundaries in
``AvatarServer.py`` and ``VehicleMover.py`` at
https://github.com/the-tuxedo-cat/WoT-Offline-Server/tree/c0bc550c46deac980194b7b860ee8781d53ec97b
(GPL project; no source code is copied here).  This is an independently
implemented adapter: it retains the compatible phase boundaries and strict
validation, while the #1513 binding remains authority for property names,
payload encoding and private APIs.
"""

from __future__ import print_function


class EntityStageError(RuntimeError):
    pass


class EntityPropertyBuilder(object):
    """Validate an exact, caller-supplied Vehicle property contract.

    The 0.9.22 Vehicle.def has not been accepted as a safe assignment schema
    yet.  ``required_names`` is therefore a capability result supplied by the
    eventual #1513 binding, not a list of guessed defaults.
    """

    def __init__(self, required_names):
        names = tuple(required_names or ())
        if not names or len(set(names)) != len(names):
            raise ValueError('required_names must be a non-empty unique sequence')
        self._required_names = names

    @property
    def required_names(self):
        return self._required_names

    def build(self, snapshot):
        if not isinstance(snapshot, dict):
            raise EntityStageError('vehicle snapshot must be a dict')
        properties = snapshot.get('properties')
        if not isinstance(properties, dict):
            raise EntityStageError('vehicle properties must be a dict')
        missing = [name for name in self._required_names
                   if name not in properties]
        if missing:
            raise EntityStageError('missing exact Vehicle properties: %s' %
                                   ', '.join(missing))
        extras = [name for name in properties if name not in self._required_names]
        if extras:
            raise EntityStageError('unverified Vehicle properties: %s' %
                                   ', '.join(sorted(extras)))
        return dict((name, properties[name]) for name in self._required_names)


class ArenaVehicleRuntime(object):
    """Drive one local Vehicle through the verified client-facing phases.

    ``runtime`` is an injected binding with these methods:
    ``create_vehicle(properties, position, rotation)``, ``destroy_entity(id)``,
    ``arena_vehicle_added(id, snapshot)``, ``arena_vehicle_removed(id)``,
    ``avatar_bind_vehicle(id)``, ``avatar_client_ready()``, ``avatar_ready()``,
    ``arena_period(period)``, ``update_vehicle(id, position, rotation)`` and
    ``send_vehicle_input(id, command)``.  The binding owns all BigWorld,
    ARENA_UPDATE payload encoding, and private API calls.
    """

    IDLE = 'idle'
    VEHICLE_CREATED = 'vehicle_created'
    ARENA_VEHICLE_ADDED = 'arena_vehicle_added'
    CLIENT_READY = 'client_ready'
    AVATAR_READY = 'avatar_ready'
    BATTLE = 'battle'
    FAILED = 'failed'
    DESTROYED = 'destroyed'

    def __init__(self, runtime, property_builder):
        self._runtime = runtime
        self._property_builder = property_builder
        self._state = self.IDLE
        self._vehicle_id = None
        self._arena_added = False
        self._client_ready = False
        self._avatar_ready = False
        self._bound = False

    @property
    def state(self):
        return self._state

    @property
    def vehicle_id(self):
        return self._vehicle_id

    def start(self, snapshot):
        if self._state not in (self.IDLE, self.DESTROYED, self.FAILED):
            raise EntityStageError('cannot start from %s' % self._state)
        self._reset_for_start()
        try:
            properties = self._property_builder.build(snapshot)
            position = self._required_value(snapshot, 'position')
            rotation = self._required_value(snapshot, 'rotation')
            self._vehicle_id = self._runtime.create_vehicle(
                properties, position, rotation)
            if self._vehicle_id is None:
                raise EntityStageError('Vehicle creation returned no entity id')
            self._state = self.VEHICLE_CREATED
            self._runtime.arena_vehicle_added(self._vehicle_id, snapshot)
            self._arena_added = True
            self._state = self.ARENA_VEHICLE_ADDED
            self._runtime.avatar_bind_vehicle(self._vehicle_id)
            self._bound = True
            self._runtime.avatar_client_ready()
            self._client_ready = True
            self._state = self.CLIENT_READY
            self._runtime.avatar_ready()
            self._avatar_ready = True
            self._state = self.AVATAR_READY
            self._runtime.arena_period(self._required_value(snapshot, 'period'))
            self._state = self.BATTLE
            return self._vehicle_id
        except Exception:
            self._rollback()
            self._state = self.FAILED
            raise

    def apply_snapshot(self, snapshot):
        if self._state not in (self.AVATAR_READY, self.BATTLE):
            raise EntityStageError('snapshot is not accepted in %s' % self._state)
        if not isinstance(snapshot, dict):
            raise EntityStageError('snapshot must be a dict')
        self._runtime.update_vehicle(
            self._vehicle_id,
            self._required_value(snapshot, 'position'),
            self._required_value(snapshot, 'rotation'))
        if 'period' in snapshot:
            self._runtime.arena_period(snapshot['period'])
            self._state = self.BATTLE

    def apply_input(self, command):
        if self._state != self.BATTLE:
            raise EntityStageError('input is not accepted in %s' % self._state)
        if not isinstance(command, dict):
            raise EntityStageError('input command must be a dict')
        self._runtime.send_vehicle_input(self._vehicle_id, dict(command))

    def destroy(self):
        if self._state == self.DESTROYED:
            return False
        try:
            self._rollback()
        finally:
            self._state = self.DESTROYED
        return True

    def _required_value(self, values, name):
        if not isinstance(values, dict) or name not in values:
            raise EntityStageError('missing %s' % name)
        return values[name]

    def _reset_for_start(self):
        self._vehicle_id = None
        self._arena_added = False
        self._client_ready = False
        self._avatar_ready = False
        self._bound = False

    def _rollback(self):
        vehicle_id = self._vehicle_id
        if vehicle_id is None:
            return
        if self._arena_added:
            try:
                self._runtime.arena_vehicle_removed(vehicle_id)
            finally:
                self._arena_added = False
                try:
                    self._runtime.destroy_entity(vehicle_id)
                finally:
                    self._vehicle_id = None
            return
        try:
            self._runtime.destroy_entity(vehicle_id)
        finally:
            self._vehicle_id = None
