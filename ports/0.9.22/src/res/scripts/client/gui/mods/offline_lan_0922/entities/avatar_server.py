"""Strict local server surface for the #1513 Avatar and Vehicle entities.

The exact client calls these mailboxes while entering a battle:

* ``Avatar.base.setClientReady`` after all four native init steps;
* ``Avatar.cell.autoAim`` and ``switchObserverFPV``;
* Avatar sync and integer-setting commands; and
* ``Vehicle.cell.sendStateToOwnClient`` on the player's Vehicle.

The public 0.9.22 observer implementation at tuxedo commit
``c0bc550c46deac980194b7b860ee8781d53ec97b`` confirms the same lifecycle.
This implementation is intentionally explicit.  Unknown mailboxes still raise
``AttributeError`` instead of turning client errors into silent success.
"""

try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle


class AvatarBridgeError(RuntimeError):
    pass


class DeferredAvatarServer(object):
    """Exist before ``PlayerAvatar.onBecomePlayer`` and attach before spawn.

    The stock Avatar asks for VOIP state and Avatar sync synchronously while
    becoming the player.  Entity binding cannot exist until the Avatar itself
    exists, so only those exact early requests are queued/accepted here.
    """

    def __init__(self):
        self._target = None
        self._pending = []

    @property
    def voipController(self):
        return self

    def attach(self, target):
        if self._target is not None and self._target is not target:
            raise AvatarBridgeError('Avatar server is already attached')
        self._target = target
        pending = self._pending
        self._pending = []
        for name, args in pending:
            getattr(target, name)(*args)

    def invalidateMicrophoneMute(self):
        if self._target is not None:
            return self._target.invalidateMicrophoneMute()
        return None

    def switchObserverFPV(self, enabled):
        if self._target is not None:
            return self._target.switchObserverFPV(enabled)
        return None

    def setClientReady(self):
        return self._defer('setClientReady', ())

    def autoAim(self, vehicle_id):
        return self._defer('autoAim', (vehicle_id,))

    def doCmdStr(self, *args):
        return self._defer('doCmdStr', args)

    def doCmdIntArr(self, *args):
        return self._defer('doCmdIntArr', args)

    def _defer(self, name, args):
        if self._target is not None:
            return getattr(self._target, name)(*args)
        self._pending.append((name, args))
        return None

    def __getattr__(self, name):
        target = self._target
        if target is None:
            raise AttributeError(
                'Avatar server is not attached for mailbox %s' % name)
        return getattr(target, name)


class AvatarServerBridge(object):
    """Bridge native Avatar/Vehicle mailbox calls to entities and LAN input."""

    def __init__(self, avatar, entity_binding, property_builder, lan_sender,
                 account_commands=None, on_ready=None, on_leave=None):
        self._avatar = avatar
        self._binding = entity_binding
        self._builder = property_builder
        self._lan_sender = lan_sender
        self._account_commands = tuple(account_commands or ())
        self._on_ready = on_ready
        self._on_leave = on_leave
        self._vehicle_id = None
        self._bound_vehicle_id = None
        self._arena_vehicle_added = False
        self._ready_requested = False
        self._client_ready = False
        self._client_context = ''

    @property
    def vehicle_id(self):
        return self._vehicle_id

    @property
    def voipController(self):
        return self

    def addVehicleToArena(self, snapshot):
        if self._vehicle_id is not None:
            raise AvatarBridgeError('Vehicle already exists')
        properties = self._builder.build(snapshot)
        created_id = self._binding.create_vehicle(
            properties, self._required(snapshot, 'position'),
            self._required(snapshot, 'rotation'))
        if created_id is None:
            raise AvatarBridgeError('createEntity returned no Vehicle id')
        # BigWorld may call Vehicle.onEnterWorld before createEntity returns.
        # acceptVehicleEnter pre-binds that same entity in the Avatar wrapper.
        if self._vehicle_id is None:
            self._vehicle_id = created_id
        elif self._vehicle_id != created_id:
            self._binding.destroy_entity(created_id)
            raise AvatarBridgeError('BigWorld entered a different Vehicle')
        try:
            self._binding.arena_vehicle_added(self._vehicle_id, snapshot)
            self._arena_vehicle_added = True
            self._flush_client_ready()
        except Exception:
            try:
                self._binding.destroy_entity(self._vehicle_id)
            finally:
                self._vehicle_id = None
                self._bound_vehicle_id = None
                self._arena_vehicle_added = False
                self._ready_requested = False
                self._client_ready = False
            raise
        return self._vehicle_id

    def acceptVehicleEnter(self, vehicle_id):
        """Bind the first locally-created Vehicle before stock enter handling."""
        vehicle_id = int(vehicle_id)
        if self._vehicle_id is None:
            self._vehicle_id = vehicle_id
        elif self._vehicle_id != vehicle_id:
            return False
        self._bind_avatar_once(vehicle_id)
        return True

    def bindToVehicle(self, vehicle_id):
        vehicle_id = int(vehicle_id)
        if self._vehicle_id is None:
            self._vehicle_id = vehicle_id
        if vehicle_id != self._vehicle_id:
            raise AvatarBridgeError('cannot bind unknown Vehicle')
        self._bind_avatar_once(vehicle_id)
        return True

    def _bind_avatar_once(self, vehicle_id):
        if self._bound_vehicle_id == vehicle_id:
            return False
        if self._bound_vehicle_id is not None:
            raise AvatarBridgeError('Avatar is already bound to another Vehicle')
        self._binding.avatar_bind_vehicle(vehicle_id)
        self._bound_vehicle_id = vehicle_id
        self._flush_client_ready()
        return True

    def setClientReady(self):
        # Native createEntity may synchronously enter Vehicle.onEnterWorld and
        # call this mailbox before createEntity returns.  Accept the first
        # request, but do not publish AVATAR_READY/PERIOD until ClientArena has
        # received VEHICLE_ADDED and the Avatar is bound to that same entity.
        if self._client_ready or self._ready_requested:
            return False
        self._ready_requested = True
        self._flush_client_ready()
        return True

    def _flush_client_ready(self):
        if (not self._ready_requested or self._client_ready or
                self._vehicle_id is None or not self._arena_vehicle_added or
                self._bound_vehicle_id != self._vehicle_id):
            return False
        self._binding.avatar_client_ready()
        self._binding.avatar_ready()
        self._binding.arena_period('battle')
        self._ready_requested = False
        self._client_ready = True
        if callable(self._on_ready):
            self._on_ready()
        return True

    def sendStateToOwnClient(self):
        """Vehicle properties already came from the local createEntity call."""
        if self._vehicle_id is None:
            raise AvatarBridgeError('Vehicle state requested before binding')
        return None

    def syncVehicleAttrs(self, attrs):
        if not isinstance(attrs, dict):
            raise AvatarBridgeError('attrs must be a dict')
        self._avatar.syncVehicleAttrs(dict(attrs))

    def vehicle_moveWith(self, flags):
        self._send_input('move', {'flags': int(flags)})

    def setCruiseControlMode(self, mode):
        self._send_input('cruise', {'mode': int(mode)})

    def vehicle_changeSetting(self, code, value):
        updater = getattr(self._avatar, 'updateVehicleSetting', None)
        if updater is None:
            raise AttributeError('Avatar.updateVehicleSetting')
        updater(self._vehicle_id, code, value)

    def vehicle_trackWorldPointWithGun(self, point):
        self._send_input('track_world', {'point': point})

    def trackRelativePointWithGun(self, point):
        """Handle the exact #1513 Vehicle.cell gun-tracking mailbox."""
        self._send_input('track_relative', {'point': point})

    def vehicle_trackRelativePointWithGun(self, point):
        self._send_input('track_relative', {'point': point})

    def vehicle_stopTrackingWithGun(self, turret_yaw, gun_pitch):
        self._send_input('stop_tracking', {
            'turret_yaw': float(turret_yaw),
            'gun_pitch': float(gun_pitch)})

    def vehicle_shoot(self):
        self._send_input('shoot', {})

    def setDevelopmentFeature(self, name, value, data):
        if name == 'pickup':
            self._send_input('development', {
                'name': name, 'args': (value, data)})
            return
        if name == 'server_marker':
            return None
        raise AttributeError('unsupported development feature: %s' % name)

    def setVehicleDevelopmentFeature(self, vehicle_id, name, value, data):
        # Release #1513 does not expose development controls.  Keep the exact
        # mailbox shape explicit so an accidental dev-resource path is safe.
        return None

    def controlAnotherVehicle(self, vehicle_id, stage):
        return None

    def vehicle_teleport(self, position, yaw):
        return None

    def vehicle_replenishAmmo(self):
        # This slice presents a stable ammo count and has no consumable stock.
        return None

    def confirmBattleResultsReceiving(self):
        return None

    def makeDenunciation(self, violator_id, topic_id, violator_kind):
        return None

    def banUnbanUser(self, account_dbid, restriction_type, ban_period,
                     reason, is_ban):
        return None

    def requestToken(self, request_id, token_type):
        callback = getattr(self._avatar, 'onTokenReceived', None)
        if callable(callback):
            callback(request_id, token_type, '')

    def sendAccountStats(self, request_id, names):
        callback = getattr(self._avatar, 'receiveAccountStats', None)
        if callable(callback):
            values = dict((name, 0) for name in names)
            callback(request_id, _pickle.dumps(values))

    def logStreamCorruption(self, stream_id, original_length, packet_length,
                            original_crc32, crc32):
        return None

    def autoAim(self, vehicle_id):
        # Target selection is already applied by the local Avatar.
        return None

    def switchObserverFPV(self, enabled):
        return None

    def switchObserverFPVControlMode(self, control_mode):
        # RemoteCameraSender emits this for ordinary players whenever the
        # stock control mode changes, even when no observer is connected.
        return None

    def setRemoteCamera(self, data):
        return None

    def activateEquipment(self, equipment_id):
        # No equipment is provisioned by the current standard-battle slice.
        return None

    def monitorVehicleDamagedDevices(self, vehicle_id):
        return None

    def invalidateMicrophoneMute(self):
        return None

    def setMicrophoneMute(self, muted):
        return None

    def setClientCtx(self, value):
        self._client_context = value

    def leaveArena(self, statistics):
        if callable(self._on_leave):
            self._on_leave()

    def doCmdStr(self, request_id, command, string):
        self._ack_command(request_id, command)

    def doCmdIntArr(self, request_id, command, values):
        self._ack_command(request_id, command)

    def destroy(self):
        if self._vehicle_id is None:
            return False
        vehicle_id = self._vehicle_id
        self._vehicle_id = None
        self._bound_vehicle_id = None
        self._arena_vehicle_added = False
        self._ready_requested = False
        self._client_ready = False
        try:
            self._binding.arena_vehicle_removed(vehicle_id)
        finally:
            self._binding.destroy_entity(vehicle_id)
        return True

    def _ack_command(self, request_id, command):
        if command not in self._account_commands:
            raise AttributeError('unsupported account command: %s' % command)
        callback = getattr(self._avatar, 'onCmdResponse', None)
        if callback is None:
            raise AttributeError('Avatar.onCmdResponse')
        callback(request_id, 0, '')

    def _send_input(self, kind, payload):
        if self._vehicle_id is None or not self._client_ready:
            raise AvatarBridgeError('Vehicle is not ready')
        sender = getattr(self._lan_sender, 'send_avatar_input', None)
        if sender is None:
            raise AttributeError('LAN sender.send_avatar_input')
        return sender(self._vehicle_id, kind, payload)

    def _required(self, values, name):
        if not isinstance(values, dict) or name not in values:
            raise AvatarBridgeError('missing %s' % name)
        return values[name]
