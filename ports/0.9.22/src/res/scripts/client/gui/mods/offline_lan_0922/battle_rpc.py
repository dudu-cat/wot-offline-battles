"""Explicit #1513 battle mailbox adapters that emit plain LAN commands.

Exact interface evidence: local #1513 scripts.pkg decompilation,
``Avatar.py`` (moveVehicle, shoot, leaveArena, Avatar sync/settings paths)
and ``Vehicle.py``/Avatar.def/Vehicle.def mailbox names.  Command shaping also
draws on tuxedo observer's AvatarServer.py and VehicleMover.py, but this file
does not create entities, run physics, or write BigWorld state.

Implemented calls emit one JSON-like dictionary to the injected ``sender``.
Any unimplemented mailbox attribute raises AttributeError deliberately.
"""


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _vector(value):
    try:
        return {'x': _number(value[0]), 'y': _number(value[1]),
                'z': _number(value[2])}
    except (TypeError, ValueError, IndexError):
        raise ValueError('expected a three-component vector')


class _Mailbox(object):
    def __init__(self, sender, vehicle_id_getter=None):
        if not callable(sender):
            raise TypeError('sender must be callable')
        self._sender = sender
        self._vehicle_id_getter = vehicle_id_getter

    def _emit(self, name, **fields):
        message = {'kind': 'battle_rpc', 'method': name}
        message.update(fields)
        self._sender(message)

    def _vehicle_id(self):
        if not callable(self._vehicle_id_getter):
            raise RuntimeError('no bound vehicle')
        vehicle_id = self._vehicle_id_getter()
        if vehicle_id is None:
            raise RuntimeError('no bound vehicle')
        return int(vehicle_id)


class AvatarBase(_Mailbox):
    """#1513 Avatar base calls: readiness, movement, gun, leave and sync."""
    def setClientReady(self):
        self._emit('setClientReady')

    def vehicle_moveWith(self, flags):
        self._emit('vehicle_moveWith', vehicle_id=self._vehicle_id(),
                   flags=int(flags))

    def setCruiseControlMode(self, mode):
        self._emit('setCruiseControlMode', vehicle_id=self._vehicle_id(),
                   mode=int(mode))

    def vehicle_trackWorldPointWithGun(self, point):
        self._emit('vehicle_trackWorldPointWithGun', vehicle_id=self._vehicle_id(),
                   point=_vector(point))

    def vehicle_trackRelativePointWithGun(self, point, vehicle_id):
        self._emit('vehicle_trackRelativePointWithGun', vehicle_id=self._vehicle_id(),
                   point=_vector(point), target_vehicle_id=int(vehicle_id))

    def vehicle_stopTrackingWithGun(self, turret_yaw, gun_pitch):
        self._emit('vehicle_stopTrackingWithGun', vehicle_id=self._vehicle_id(),
                   turret_yaw=_number(turret_yaw), gun_pitch=_number(gun_pitch))

    def vehicle_shoot(self, is_repeat=False):
        self._emit('vehicle_shoot', vehicle_id=self._vehicle_id(),
                   is_repeat=bool(is_repeat))

    def leaveArena(self, statistics=None):
        self._emit('leaveArena', statistics=statistics)

    def requestAvatarSync(self, request_id):
        self._emit('requestAvatarSync', request_id=int(request_id))

    def changeIntUserSettings(self, request_id, values, delete=False):
        self._emit('deleteIntUserSettings' if delete else 'addIntUserSettings',
                   request_id=int(request_id), values=list(values or ()))


class AvatarCell(_Mailbox):
    """#1513 Avatar cell calls used by binding and client-side control modes."""
    def bindToVehicle(self, vehicle_id):
        self._emit('bindToVehicle', vehicle_id=int(vehicle_id))

    def autoAim(self, vehicle_id):
        self._emit('autoAim', vehicle_id=int(vehicle_id))

    def switchObserverFPV(self, enabled):
        self._emit('switchObserverFPV', enabled=bool(enabled))


class AvatarBwProto(_Mailbox):
    """Minimal #1513 BW chat/VOIP surface needed during battle teardown."""
    def invalidateMicrophoneMute(self):
        self._emit('invalidateMicrophoneMute')


class VehicleCell(_Mailbox):
    """Vehicle cell adapter; no physics is performed locally in this layer."""
    def moveWith(self, flags):
        self._emit('moveWith', vehicle_id=self._vehicle_id(), flags=int(flags))

    def setCruiseControlMode(self, mode):
        self._emit('setCruiseControlMode', vehicle_id=self._vehicle_id(),
                   mode=int(mode))

    def trackWorldPointWithGun(self, point):
        self._emit('trackWorldPointWithGun', vehicle_id=self._vehicle_id(),
                   point=_vector(point))

    def stopTrackingWithGun(self, turret_yaw, gun_pitch):
        self._emit('stopTrackingWithGun', vehicle_id=self._vehicle_id(),
                   turret_yaw=_number(turret_yaw), gun_pitch=_number(gun_pitch))

    def shoot(self, is_repeat=False):
        self._emit('shoot', vehicle_id=self._vehicle_id(),
                   is_repeat=bool(is_repeat))


class BattleRpc(object):
    """Factory exposing explicit Avatar/Vehicle mailbox surfaces.

    ``bind`` changes only this adapter's current vehicle id; the caller must
    apply any entity/property change after its LAN authority acknowledges it.
    """
    def __init__(self, sender):
        self._bound_vehicle_id = None
        self.avatar_base = AvatarBase(sender, self.bound_vehicle_id)
        self.avatar_cell = AvatarCell(sender, self.bound_vehicle_id)
        self.avatar_bw_proto = AvatarBwProto(sender, self.bound_vehicle_id)
        self.vehicle_cell = VehicleCell(sender, self.bound_vehicle_id)

    def bound_vehicle_id(self):
        return self._bound_vehicle_id

    def bind(self, vehicle_id):
        self._bound_vehicle_id = int(vehicle_id)
        self.avatar_cell.bindToVehicle(self._bound_vehicle_id)
