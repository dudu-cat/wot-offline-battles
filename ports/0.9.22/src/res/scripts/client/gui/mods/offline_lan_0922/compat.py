from __future__ import print_function

import sys

try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle


OFFLINE_SERVER_ADDRESS = 'offline-lan.local:0'
_OFFLINE_ACCOUNT_NAME = 'offline_account'
_OFFLINE_INIT_COMPLETE = '_offlineLANInitComplete'
_OFFLINE_PLAYER_READY = '_offlineLANPlayerReady'
_OFFLINE_RETIRE_PENDING = '_offlineLANRetirePending'


def _entity_bytes(value, default=''):
    """Return the exact byte-string shape expected by BigWorld STRING."""
    if value is None:
        value = default
    if isinstance(value, bytes):
        return value
    try:
        return value.encode('utf-8')
    except AttributeError:
        return str(value)

_SERVER_SETTINGS = {
    'file_server': {
        'clan_emblems': {'url_template': '', 'cache_life_time': 0},
        'clan_emblems_small': {'url_template': '', 'cache_life_time': 0},
    },
    'regional_settings': {
        'starting_time_of_a_new_day': 0,
        'starting_day_of_a_new_week': 0,
        'starting_time_of_a_new_game_day': 3,
    },
    # ServerSettings consumes the first three values, while the exact #1513
    # predefined-host list directly indexes the fourth roaming-host list.
    'roaming': (0, 0, [], []),
    'wallet': (1, 1),
    # ClientRanked indexes this setting directly even when ranked battles are
    # disabled.  Presence, rather than truthiness, is the native contract.
    'ranked_config': {'isEnabled': False},
    # The exact #1513 default is enabled when this section is absent.  The
    # event-board client then starts HTTP-backed clan/event synchronization,
    # which has no producer in the offline account service.
    'elenSettings': {
        'isElenEnabled': False,
        'elenUpdateInterval': 60,
    },
    'isEncyclopediaEnabled': 'all',
    'isVehiclesCompareEnabled': True,
    'isCustomizationEnabled': True,
    # The retail default is enabled.  Offline accounts have no tutorial
    # service, and letting the hints player start leaves weak GUI proxies that
    # raise during game.fini().  Public 0.9.x offline servers disable the same
    # server-owned feature and publish a completed tutorial bitmask.
    'isTutorialEnabled': False,
}

_LOBBY_GUI_CONTEXT = {
    'databaseID': 1,
    'logUXEvents': False,
    'aogasStartedAt': 0,
    'sessionStartedAt': 0,
    'isAogasEnabled': False,
    'collectUiStats': False,
    'isLongDisconnectedFromCenter': False,
}


def _load_runtime():
    import Account
    import Avatar
    import AvatarInputHandler
    import AvatarPositionControl
    import BigWorld
    import ChatManager
    import Math
    import SoundGroups
    import Vehicle
    import VehicleGunRotator
    import AvatarInputHandler.AimingSystems.steady_vehicle_matrix as \
        SteadyVehicleMatrix
    import vehicle_systems.CompoundAppearance as CompoundAppearanceModule
    import constants
    from OfflineMapCreator import g_offlineMapCreator
    from PlayerEvents import g_playerEvents
    from connection_mgr import LOGIN_STATUS
    from gui.Scaleform.daapi.view.battle.shared.debug_panel import DebugPanel
    from gui.prb_control.dispatcher import g_prbLoader
    from helpers import dependency
    from predefined_hosts import g_preDefinedHosts
    from skeletons.connection_mgr import IConnectionManager

    class Runtime(object):
        pass

    runtime = Runtime()
    runtime.account_module = Account
    runtime.avatar_module = Avatar
    runtime.avatar_input_handler = AvatarInputHandler
    runtime.avatar_position_control = AvatarPositionControl
    runtime.bigworld = BigWorld
    runtime.chat_manager = ChatManager.chatManager
    runtime.compound_appearance_module = CompoundAppearanceModule
    runtime.constants = constants
    runtime.connection_manager = dependency.instance(IConnectionManager)
    runtime.debug_panel_type = DebugPanel
    runtime.login_status = LOGIN_STATUS
    runtime.math = Math
    runtime.offline_map_creator = g_offlineMapCreator
    runtime.player_events = g_playerEvents
    runtime.predefined_hosts = g_preDefinedHosts
    runtime.prb_loader = g_prbLoader
    runtime.sound_groups_module = SoundGroups
    runtime.steady_vehicle_matrix = SteadyVehicleMatrix
    runtime.vehicle_module = Vehicle
    runtime.vehicle_gun_rotator = VehicleGunRotator
    return runtime


class _FallbackServer(object):

    def __getattr__(self, name):
        def ignored(*args, **kwargs):
            return None

        return ignored


class _DeferredAvatarServer(object):
    """Exact early Avatar requests before the entity binding is available."""

    def __init__(self):
        self._target = None
        self._pending = []

    @property
    def voipController(self):
        return self

    def attach(self, target):
        if self._target is not None and self._target is not target:
            raise RuntimeError('Avatar server is already attached')
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
        # BigWorld may finish Vehicle prerequisites and the stock Avatar init
        # from inside createEntity(), before BattleRuntime receives the id and
        # attaches its concrete bridge.  Preserve that readiness barrier.
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
        if self._target is None:
            raise AttributeError(
                'Avatar server is not attached for mailbox %s' % name)
        return getattr(self._target, name)


class _OfflineInputHandler(object):

    def prerequisites(self):
        return []

    def start(self):
        return None

    def stop(self):
        return None

    def handleKeyEvent(self, event):
        return False

    def handleMouseEvent(self, dx, dy, dz):
        return False


class _OfflineCameraColliderHandler(object):
    """Late #1513 appearance teardown after AvatarInputHandler.stop()."""

    def __init__(self):
        self.onCameraChanged = _OfflineEventSink()

    def addVehicleToCameraCollider(self, vehicle):
        return None

    def removeVehicleFromCameraCollider(self, vehicle):
        return None


class _OfflineEventSink(object):
    """Minimal Event surface used by exact #1513 native teardown."""

    def __iadd__(self, callback):
        return self

    def __isub__(self, callback):
        return self


class _OfflineVehicleFilterSyncProxy(object):
    """Delegate WGVehicleFilter except for unsafe retail-only syncs."""

    __slots__ = ('_vehicle_filter', '_pose_matrix')

    def __init__(self, vehicle_filter, pose_matrix=None):
        self._vehicle_filter = vehicle_filter
        self._pose_matrix = pose_matrix

    def __getattr__(self, name):
        return getattr(self._vehicle_filter, name)

    def syncGunAngles(self, yaw, pitch):
        # A client-only Vehicle has no retail interpolation filter behind its
        # initial gun-angle sample.  Exact #1513 asserts a null native filter
        # here, so the sample cannot be submitted through this native path.
        return None

    def syncStabilisedYPR(self, yaw, pitch, roll):
        # PlayerAvatar's auxiliary-physics property handler forwards its
        # first stabilised sample into the same missing retail server/filter
        # chain.  Keep the stock handler, including track and RPM updates, but
        # omit only this native sync while that exact handler is running.
        return None

    def interpolateStabilisedMatrix(self, timestamp):
        """Expose the canonical copied pose to the fixed-turret aim path."""
        if self._pose_matrix is not None:
            return self._pose_matrix
        return self._vehicle_filter.interpolateStabilisedMatrix(timestamp)


class OfflineCompatibility(object):

    def __init__(self, runtime=None):
        self._runtime = runtime
        self._installed = False
        self._connecting = False
        self._fake_connected = False
        self._host = None
        self._host_added = False
        self._account_context = {}
        self._account_state = None
        self._show_lobby = False
        self._battle_active = False
        self._native_battle = False
        self._battle_gui_type = None
        self._battle_bonus_type = None
        self._battle_player_name = 'OfflinePlayer'
        self._battle_player_team = 1
        self._battle_network_client = None
        self._original_account_init = None
        self._original_account_getattribute = None
        self._original_account_become_player = None
        self._original_account_become_non_player = None
        self._original_avatar_init = None
        self._original_avatar_getattribute = None
        self._original_avatar_become_player = None
        self._original_avatar_become_non_player = None
        self._original_avatar_enter_world = None
        self._original_avatar_leave_world = None
        self._original_avatar_vehicle_enter = None
        self._avatar_vehicle_enter_code = None
        self._avatar_start_vehicle_visual_code = None
        self._original_avatar_prereqs_loaded = None
        self._original_avatar_aux_physics = None
        self._original_avatar_get_speeds = None
        self._original_avatar_auto_aim = None
        self._original_control_mode_changed = None
        self._original_consistent_link_own_vehicle = None
        self._original_steady_relink_sources = None
        self._avatar_aux_physics_code = None
        self._original_vehicle_getattribute = None
        self._original_vehicle_setattr = None
        self._original_vehicle_get_speed = None
        self._original_vehicle_leave_world = None
        self._original_vehicle_start_wg_physics = None
        self._vehicle_start_wg_physics_code = None
        self._original_vehicle_set_gun_angles = None
        self._vehicle_set_gun_angles_code = None
        self._gun_rotator_stabilised_code = None
        self._original_compound_getattribute = None
        self._original_compound_deactivate = None
        self._original_compound_models_refresh = None
        self._compound_models_refresh_code = None
        self._original_connect = None
        self._original_disconnect = None
        self._original_server_time = None
        self._original_target = None
        self._original_debug_update = None
        self._account_init_wrapper = None
        self._account_getattribute_wrapper = None
        self._account_become_player_wrapper = None
        self._account_become_non_player_wrapper = None
        self._avatar_init_wrapper = None
        self._avatar_getattribute_wrapper = None
        self._avatar_become_player_wrapper = None
        self._avatar_become_non_player_wrapper = None
        self._avatar_enter_world_wrapper = None
        self._avatar_leave_world_wrapper = None
        self._avatar_vehicle_enter_wrapper = None
        self._avatar_prereqs_loaded_wrapper = None
        self._avatar_aux_physics_wrapper = None
        self._avatar_get_speeds_wrapper = None
        self._avatar_auto_aim_wrapper = None
        self._control_mode_changed_wrapper = None
        self._consistent_link_own_vehicle_wrapper = None
        self._steady_relink_sources_wrapper = None
        self._vehicle_getattribute_wrapper = None
        self._vehicle_setattr_wrapper = None
        self._vehicle_get_speed_wrapper = None
        self._vehicle_leave_world_wrapper = None
        self._vehicle_start_wg_physics_wrapper = None
        self._vehicle_set_gun_angles_wrapper = None
        self._compound_getattribute_wrapper = None
        self._compound_deactivate_wrapper = None
        self._compound_models_refresh_wrapper = None
        self._vehicle_starting_wg_physics = None
        self._vehicle_syncing_gun_angles = None
        self._avatar_syncing_aux_physics = None
        self._avatar_entering_vehicle = None
        self._compound_refreshing_models = None
        self._connect_wrapper = None
        self._disconnect_wrapper = None
        self._server_time_wrapper = None
        self._target_wrapper = None
        self._debug_update_wrapper = None
        self._battle_server_time_origin = None
        self._battle_clock_origin = None
        self._vehicle_property_overlays = {}
        self._control_mode_listener = None
        self._target_lock_candidate = None

    def install(self):
        if self._installed:
            return
        self._runtime = self._runtime or _load_runtime()
        if self._account_state is None:
            from gui.mods.offline_lan_0922.account_rpc.state import AccountState
            self._account_state = AccountState()
        runtime = self._runtime
        account_type = runtime.account_module.PlayerAccount
        avatar_type = runtime.avatar_module.PlayerAvatar
        vehicle_type = getattr(
            getattr(runtime, 'vehicle_module', None), 'Vehicle', None)
        compound_type = getattr(
            getattr(runtime, 'compound_appearance_module', None),
            'CompoundAppearance', None)
        debug_panel_type = getattr(runtime, 'debug_panel_type', None)
        input_handler_type = getattr(
            getattr(runtime, 'avatar_input_handler', None),
            'AvatarInputHandler', None)
        consistent_matrices_type = getattr(
            getattr(runtime, 'avatar_position_control', None),
            'ConsistentMatrices', None)
        steady_matrix_type = getattr(
            getattr(runtime, 'steady_vehicle_matrix', None),
            'SteadyVehicleMatrixCalculator', None)
        gun_rotator_type = getattr(
            getattr(runtime, 'vehicle_gun_rotator', None),
            'VehicleGunRotator', None)
        self._original_account_init = account_type.__dict__.get(
            '__init__', account_type.__init__)
        self._original_account_getattribute = account_type.__dict__.get(
            '__getattribute__', account_type.__getattribute__)
        self._original_account_become_player = account_type.__dict__.get(
            'onBecomePlayer', getattr(account_type, 'onBecomePlayer', None))
        self._original_account_become_non_player = account_type.__dict__.get(
            'onBecomeNonPlayer',
            getattr(account_type, 'onBecomeNonPlayer', None))
        self._original_avatar_init = avatar_type.__dict__.get(
            '__init__', avatar_type.__init__)
        self._original_avatar_getattribute = avatar_type.__dict__.get(
            '__getattribute__', avatar_type.__getattribute__)
        self._original_avatar_become_player = avatar_type.__dict__.get(
            'onBecomePlayer', avatar_type.onBecomePlayer)
        self._original_avatar_become_non_player = avatar_type.__dict__.get(
            'onBecomeNonPlayer',
            getattr(avatar_type, 'onBecomeNonPlayer', None))
        self._original_avatar_enter_world = avatar_type.__dict__.get(
            'onEnterWorld', getattr(avatar_type, 'onEnterWorld', None))
        self._original_avatar_leave_world = avatar_type.__dict__.get(
            'onLeaveWorld', getattr(avatar_type, 'onLeaveWorld', None))
        self._original_avatar_vehicle_enter = avatar_type.__dict__.get(
            'vehicle_onEnterWorld',
            getattr(avatar_type, 'vehicle_onEnterWorld', None))
        if self._original_avatar_vehicle_enter is not None:
            self._avatar_vehicle_enter_code = getattr(
                self._original_avatar_vehicle_enter,
                'func_code', getattr(
                    self._original_avatar_vehicle_enter, '__code__', None))
        avatar_start_visual = avatar_type.__dict__.get(
            '_PlayerAvatar__startVehicleVisual', getattr(
                avatar_type, '_PlayerAvatar__startVehicleVisual', None))
        if avatar_start_visual is not None:
            self._avatar_start_vehicle_visual_code = getattr(
                avatar_start_visual, 'func_code', getattr(
                    avatar_start_visual, '__code__', None))
        self._original_avatar_prereqs_loaded = avatar_type.__dict__.get(
            'onPrereqsLoaded', getattr(avatar_type, 'onPrereqsLoaded', None))
        self._original_avatar_get_speeds = avatar_type.__dict__.get(
            'getOwnVehicleSpeeds',
            getattr(avatar_type, 'getOwnVehicleSpeeds', None))
        self._original_avatar_auto_aim = avatar_type.__dict__.get(
            'autoAim', getattr(avatar_type, 'autoAim', None))
        if self._original_avatar_auto_aim is None:
            raise RuntimeError('#1513 Avatar.autoAim is unavailable')
        self._original_avatar_aux_physics = avatar_type.__dict__.get(
            '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData',
            getattr(
                avatar_type,
                '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData', None))
        if self._original_avatar_aux_physics is not None:
            self._avatar_aux_physics_code = getattr(
                self._original_avatar_aux_physics,
                'func_code', getattr(
                    self._original_avatar_aux_physics, '__code__', None))
        if input_handler_type is None:
            raise RuntimeError('#1513 AvatarInputHandler is unavailable')
        self._original_control_mode_changed = (
            input_handler_type.__dict__.get(
                'onControlModeChanged',
                getattr(input_handler_type, 'onControlModeChanged', None)))
        if self._original_control_mode_changed is None:
            raise RuntimeError(
                '#1513 control-mode transition boundary is unavailable')
        if consistent_matrices_type is None:
            raise RuntimeError('#1513 ConsistentMatrices is unavailable')
        if steady_matrix_type is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix calculator is unavailable')
        self._original_consistent_link_own_vehicle = (
            consistent_matrices_type.__dict__.get(
                '_ConsistentMatrices__linkOwnVehicle',
                getattr(
                    consistent_matrices_type,
                    '_ConsistentMatrices__linkOwnVehicle', None)))
        self._original_steady_relink_sources = (
            steady_matrix_type.__dict__.get(
                'relinkSources',
                getattr(steady_matrix_type, 'relinkSources', None)))
        if self._original_consistent_link_own_vehicle is None:
            raise RuntimeError(
                '#1513 own-vehicle matrix link boundary is unavailable')
        if self._original_steady_relink_sources is None:
            raise RuntimeError(
                '#1513 steady matrix relink boundary is unavailable')
        if gun_rotator_type is None:
            raise RuntimeError('#1513 VehicleGunRotator is unavailable')
        gun_rotator_stabilised = getattr(
            gun_rotator_type, 'getAvatarOwnVehicleStabilisedMatrix', None)
        if gun_rotator_stabilised is None:
            raise RuntimeError(
                '#1513 fixed-turret stabilised matrix boundary is unavailable')
        self._gun_rotator_stabilised_code = getattr(
            gun_rotator_stabilised, 'func_code', getattr(
                gun_rotator_stabilised, '__code__', None))
        if self._gun_rotator_stabilised_code is None:
            raise RuntimeError(
                '#1513 fixed-turret stabilised matrix code is unavailable')
        if vehicle_type is not None:
            self._original_vehicle_getattribute = vehicle_type.__dict__.get(
                '__getattribute__', vehicle_type.__getattribute__)
            self._original_vehicle_setattr = vehicle_type.__dict__.get(
                '__setattr__', vehicle_type.__setattr__)
            self._original_vehicle_get_speed = vehicle_type.__dict__.get(
                'getSpeed', getattr(vehicle_type, 'getSpeed', None))
            self._original_vehicle_leave_world = (
                vehicle_type.__dict__.get(
                    'onLeaveWorld',
                    getattr(vehicle_type, 'onLeaveWorld', None)))
            self._original_vehicle_start_wg_physics = (
                vehicle_type.__dict__.get(
                    '_Vehicle__startWGPhysics',
                    getattr(vehicle_type, '_Vehicle__startWGPhysics', None)))
            if self._original_vehicle_start_wg_physics is not None:
                self._vehicle_start_wg_physics_code = getattr(
                    self._original_vehicle_start_wg_physics,
                    'func_code', getattr(
                        self._original_vehicle_start_wg_physics,
                        '__code__', None))
            self._original_vehicle_set_gun_angles = (
                vehicle_type.__dict__.get(
                    'set_gunAnglesPacked',
                    getattr(vehicle_type, 'set_gunAnglesPacked', None)))
            if self._original_vehicle_set_gun_angles is not None:
                self._vehicle_set_gun_angles_code = getattr(
                    self._original_vehicle_set_gun_angles,
                    'func_code', getattr(
                        self._original_vehicle_set_gun_angles,
                        '__code__', None))
        if compound_type is not None:
            self._original_compound_getattribute = (
                compound_type.__dict__.get(
                    '__getattribute__', compound_type.__getattribute__))
            self._original_compound_deactivate = (
                compound_type.__dict__.get(
                    'deactivate', getattr(compound_type, 'deactivate', None)))
            self._original_compound_models_refresh = (
                compound_type.__dict__.get(
                    '_CompoundAppearance__onModelsRefresh',
                    getattr(
                        compound_type,
                        '_CompoundAppearance__onModelsRefresh', None)))
            if self._original_compound_models_refresh is not None:
                self._compound_models_refresh_code = getattr(
                    self._original_compound_models_refresh,
                    'func_code', getattr(
                        self._original_compound_models_refresh,
                        '__code__', None))
        self._original_connect = runtime.bigworld.connect
        self._original_disconnect = runtime.bigworld.disconnect
        self._original_server_time = runtime.bigworld.serverTime
        self._original_target = getattr(runtime.bigworld, 'target', None)
        if not callable(self._original_target):
            raise RuntimeError('#1513 BigWorld.target is unavailable')
        if debug_panel_type is not None:
            self._original_debug_update = debug_panel_type.__dict__.get(
                'updateDebugInfo',
                getattr(debug_panel_type, 'updateDebugInfo', None))
        compatibility = self

        def account_init(account):
            offline_initializing = compatibility._connecting
            if offline_initializing:
                account.isOffline = True
                account.name = _OFFLINE_ACCOUNT_NAME
                account.initialServerSettings = dict(_SERVER_SETTINGS)
                property_name, property_value = (
                    runtime.account_module._CLIENT_SERVER_VERSION)
                setattr(account, property_name, property_value)
                context = dict(compatibility._account_context)
                context['account_state'] = compatibility._account_state
                receive_stats = getattr(account, 'receiveServerStats', None)
                if callable(receive_stats):
                    context['receive_server_stats'] = receive_stats
                callback = getattr(runtime.bigworld, 'callback', None)
                if callback is None:
                    account.fakeServer = _FallbackServer()
                else:
                    from gui.mods.offline_lan_0922.account_rpc.server import \
                        FakeServer

                    def active_account():
                        try:
                            player = runtime.bigworld.player()
                        except ReferenceError:
                            return None
                        if (player is account and
                                getattr(account, _OFFLINE_INIT_COMPLETE,
                                        False) and
                                getattr(account, _OFFLINE_PLAYER_READY,
                                        False)):
                            return player
                        return None

                    account.fakeServer = FakeServer(
                        active_account, callback=callback, context=context)
                # Exact #1513 reuses g_accountRepository across Account
                # entities.  AccountSyncData.setAccount() saves its cache
                # before rebinding the cache's weak proxy, but BigWorld clears
                # the retired Entity's entire __dict__.  Point that one cache
                # at the replacement first so neither an empty nor a dead old
                # Entity is dereferenced during the native constructor.
                repository = getattr(
                    runtime.account_module, 'g_accountRepository', None)
                if (repository is not None and
                        getattr(repository, 'className', None) ==
                        account.__class__.__name__):
                    persistent_cache = getattr(
                        repository.syncData,
                        '_AccountSyncData__persistentCache')
                    persistent_cache.setAccount(account)
            compatibility._original_account_init(account)
            if offline_initializing:
                setattr(account, _OFFLINE_INIT_COMPLETE, True)

        def avatar_init(avatar):
            offline_initializing = compatibility._fake_connected
            if offline_initializing:
                compatibility._prepare_avatar_properties(avatar)
            compatibility._original_avatar_init(avatar)
            if offline_initializing:
                avatar.filter = runtime.bigworld.AvatarFilter()
                avatar.filter.enableLagDetection(True)
                setattr(avatar, _OFFLINE_INIT_COMPLETE, True)

        def control_mode_changed(handler, eMode, **args):
            result = compatibility._original_control_mode_changed(
                handler, eMode, **args)
            listener = compatibility._control_mode_listener
            if listener is not None:
                current = getattr(
                    handler, '_AvatarInputHandler__ctrlModeName', None)
                if current == eMode:
                    listener(handler, eMode)
            return result

        def consistent_link_own_vehicle(matrices, vehicle):
            overlay = compatibility._vehicle_property_overlays.get(
                id(vehicle))
            if (compatibility._battle_active and overlay is not None and
                    overlay.get('_pose_active')):
                provider = getattr(
                    matrices, '_ConsistentMatrices__ownVehicleMProv', None)
                if provider is None:
                    raise RuntimeError(
                        '#1513 own-vehicle matrix provider is unavailable')
                provider.target = overlay['matrix']
                if provider.target is not overlay['matrix']:
                    raise RuntimeError(
                        '#1513 own-vehicle matrix rejected live pose')
                return None
            return compatibility._original_consistent_link_own_vehicle(
                matrices, vehicle)

        def steady_relink_sources(calculator):
            if compatibility._battle_active:
                try:
                    player = runtime.bigworld.player()
                except ReferenceError:
                    player = None
                vehicle = (player.getVehicleAttached()
                           if player is not None else None)
                overlay = compatibility._vehicle_property_overlays.get(
                    id(vehicle)) if vehicle is not None else None
                if overlay is not None and overlay.get('_pose_active'):
                    matrix = overlay['matrix']
                    output = getattr(
                        calculator,
                        '_SteadyVehicleMatrixCalculator__outputMProv', None)
                    stabilised = getattr(
                        calculator,
                        '_SteadyVehicleMatrixCalculator__stabilisedMProv',
                        None)
                    if output is None or stabilised is None:
                        raise RuntimeError(
                            '#1513 steady vehicle providers are unavailable')
                    output.rotationSrc = matrix
                    output.translationSrc = matrix
                    stabilised.target = matrix
                    if (output.rotationSrc is not matrix or
                            output.translationSrc is not matrix or
                            stabilised.target is not matrix):
                        raise RuntimeError(
                            '#1513 steady vehicle providers rejected live '
                            'pose')
                    return None
            return compatibility._original_steady_relink_sources(calculator)

        def avatar_become_player(avatar):
            if not compatibility._fake_connected:
                return compatibility._original_avatar_become_player(avatar)
            if not getattr(avatar, _OFFLINE_INIT_COMPLETE, False):
                raise RuntimeError(
                    'offline Avatar initialization did not complete')
            compatibility.prepare_avatar(avatar)
            offline_filter = avatar.filter
            original_filter_factory = runtime.bigworld.AvatarFilter
            active = getattr(runtime.offline_map_creator, 'Active', None)
            if callable(active):
                map_was_active = bool(active())
            else:
                # Test doubles and a few unpacked client variants expose the
                # same state as a field.  The #1513 runtime uses Active().
                map_was_active = bool(getattr(
                    runtime.offline_map_creator, 'active', False))

            if compatibility._battle_gui_type is not None:
                avatar.arenaGuiType = compatibility._battle_gui_type
            if compatibility._battle_bonus_type is not None:
                avatar.arenaBonusType = compatibility._battle_bonus_type

            def reuse_offline_filter():
                return offline_filter

            runtime.bigworld.AvatarFilter = reuse_offline_filter
            if compatibility._native_battle:
                # The stock offline viewer intentionally skips the battle
                # session and real AvatarInputHandler.  A playable LAN battle
                # needs the normal #1513 initialization branches instead.
                runtime.offline_map_creator.SetActive(False)
            # Native onBecomePlayer attaches ChatManager, player events and
            # battle controllers before every later validation has finished.
            # Open the retirement token before entering stock code so a
            # partial promotion is still torn down exactly once.
            setattr(avatar, _OFFLINE_RETIRE_PENDING, True)
            try:
                result = compatibility._original_avatar_become_player(avatar)
            finally:
                if runtime.bigworld.AvatarFilter is reuse_offline_filter:
                    runtime.bigworld.AvatarFilter = original_filter_factory
                runtime.offline_map_creator.SetActive(map_was_active)
            arena = getattr(avatar, 'arena', None)
            if arena is None or getattr(arena, 'arenaType', None) is None:
                # Exact PlayerAvatar.onBecomePlayer can abort and return
                # normally when the arena type is missing.  A successful
                # Python return therefore is not by itself a ready Avatar.
                raise RuntimeError(
                    'offline Avatar has no initialized arena type')
            setattr(avatar, _OFFLINE_PLAYER_READY, True)
            return result

        def retire_offline_player(player, original):
            """Run a client-only player's native retirement exactly once."""
            if not getattr(player, _OFFLINE_INIT_COMPLETE, False):
                if compatibility._fake_connected:
                    # A failed constructor or an already-cleared PyEntity has
                    # no complete native lifecycle left to detach.  Never call
                    # stock teardown against its missing instance fields.
                    return None
                return original(player)
            if not getattr(player, _OFFLINE_RETIRE_PENDING, False):
                return None
            # BigWorld.clear* can invoke onBecomeNonPlayer again after our
            # explicit lifecycle boundary.  Close the token before entering
            # stock code so that second delivery cannot detach global owners
            # twice, even if the first call raises part-way through.
            setattr(player, _OFFLINE_RETIRE_PENDING, False)
            setattr(player, _OFFLINE_PLAYER_READY, False)
            try:
                result = original(player)
            except Exception:
                # Account/Avatar teardown can itself fail before reaching the
                # late ChatManager detach.  Preserve that first exception, but
                # never leave the global proxy pointing at an Entity whose
                # instance dictionary will be cleared next.
                try:
                    chat_manager = getattr(runtime, 'chat_manager', None)
                    if (chat_manager is not None and
                            getattr(chat_manager, 'playerProxy', None) is not
                            None):
                        chat_manager.switchPlayerProxy(None)
                except Exception:
                    pass
                raise
            chat_manager = getattr(runtime, 'chat_manager', None)
            if (chat_manager is not None and
                    getattr(chat_manager, 'playerProxy', None) is not None):
                chat_manager.switchPlayerProxy(None)
            return result

        def account_become_non_player(account):
            return retire_offline_player(
                account, compatibility._original_account_become_non_player)

        def avatar_become_non_player(avatar):
            return retire_offline_player(
                avatar, compatibility._original_avatar_become_non_player)

        def avatar_enter_world(avatar, prereqs):
            if (compatibility._fake_connected and
                    not getattr(avatar, _OFFLINE_INIT_COMPLETE, False)):
                # BigWorld still delivers world callbacks after a Python
                # constructor raises.  Stock PlayerAvatar.onEnterWorld then
                # dereferences fields that its interrupted __init__ never
                # created, obscuring the first property/constructor error.
                return None
            return compatibility._original_avatar_enter_world(
                avatar, prereqs)

        def avatar_leave_world(avatar):
            if (compatibility._fake_connected and
                    not getattr(avatar, _OFFLINE_INIT_COMPLETE, False)):
                # The matching entity clear can arrive for the same partial
                # PyEntity.  It has no native ConsistentMatrices owner to
                # notify and therefore no stock leave lifecycle to run.
                return None
            return compatibility._original_avatar_leave_world(avatar)

        def avatar_getattribute(avatar, name):
            if (name in ('base', 'cell', 'server', 'bwProto') and
                    compatibility._battle_active):
                try:
                    return compatibility._original_avatar_getattribute(
                        avatar, 'fakeServer')
                except AttributeError:
                    pass
            return compatibility._original_avatar_getattribute(avatar, name)

        def avatar_vehicle_enter(avatar, vehicle):
            server = None
            if compatibility._battle_active:
                try:
                    server = compatibility._original_avatar_getattribute(
                        avatar, 'fakeServer')
                except AttributeError:
                    server = None
                prepare = getattr(server, 'prepareVehicleEnter', None)
                accept = getattr(server, 'acceptVehicleEnter', None)
                if callable(accept):
                    try:
                        if callable(prepare):
                            prepare(vehicle)
                        accept(vehicle.id)
                    except Exception as error:
                        fail = getattr(server, 'failVehicleEnter', None)
                        if callable(fail):
                            fail(vehicle.id, error)
                        raise
            try:
                previous_entering = compatibility._avatar_entering_vehicle
                compatibility._avatar_entering_vehicle = vehicle
                if compatibility._original_avatar_vehicle_enter is not None:
                    result = compatibility._original_avatar_vehicle_enter(
                        avatar, vehicle)
                else:
                    result = None
            except Exception as error:
                fail = getattr(server, 'failVehicleEnter', None)
                if callable(fail):
                    fail(vehicle.id, error)
                raise
            finally:
                compatibility._avatar_entering_vehicle = previous_entering
            complete = getattr(server, 'completeVehicleEnter', None)
            if callable(complete):
                complete(vehicle.id)
            return result

        def avatar_prereqs_loaded(avatar, resource_names, resource_refs):
            if compatibility._fake_connected:
                try:
                    player = runtime.bigworld.player()
                except ReferenceError:
                    player = None
                if (player is not avatar or
                        not getattr(avatar, _OFFLINE_INIT_COMPLETE, False) or
                        not getattr(avatar, _OFFLINE_PLAYER_READY, False)):
                    # BigWorld resource callbacks cannot be cancelled.  Drop
                    # one retained callback after the PyEntity has left the
                    # player boundary instead of invoking a cleared instance.
                    return None
            return compatibility._original_avatar_prereqs_loaded(
                avatar, resource_names, resource_refs)

        def avatar_aux_physics(avatar, previous):
            original = compatibility._original_avatar_aux_physics
            if not compatibility._battle_active:
                return original(avatar, previous)
            outer_avatar = compatibility._avatar_syncing_aux_physics
            compatibility._avatar_syncing_aux_physics = avatar
            try:
                return original(avatar, previous)
            finally:
                compatibility._avatar_syncing_aux_physics = outer_avatar

        def vehicle_getattribute(vehicle, name):
            if (compatibility._battle_active and
                    name in ('health', 'isCrewActive',
                             'position', 'yaw', 'matrix')):
                overlay = compatibility._vehicle_property_overlays.get(
                    id(vehicle))
                if overlay is not None and name in overlay:
                    return overlay[name]
            caller_code = None
            if (compatibility._vehicle_starting_wg_physics is not None or
                    compatibility._vehicle_syncing_gun_angles is not None or
                    compatibility._avatar_syncing_aux_physics is not None or
                    compatibility._avatar_entering_vehicle is not None or
                    name == 'filter'):
                try:
                    caller_code = sys._getframe(1).f_code
                except (AttributeError, ValueError):
                    pass
            direct_start_filter = (
                compatibility._vehicle_starting_wg_physics is vehicle and
                caller_code is compatibility._vehicle_start_wg_physics_code)
            direct_gun_sync = (
                compatibility._vehicle_syncing_gun_angles is vehicle and
                caller_code is compatibility._vehicle_set_gun_angles_code)
            direct_avatar_aux_sync = False
            if compatibility._avatar_syncing_aux_physics is not None:
                direct_avatar_aux_sync = (
                    caller_code is compatibility._avatar_aux_physics_code)
            direct_avatar_pose_init = (
                compatibility._avatar_entering_vehicle is vehicle and
                caller_code in (
                    compatibility._avatar_vehicle_enter_code,
                    compatibility._avatar_start_vehicle_visual_code))
            overlay = compatibility._vehicle_property_overlays.get(
                id(vehicle))
            direct_fixed_turret_pose = (
                caller_code is compatibility._gun_rotator_stabilised_code and
                overlay is not None and overlay.get('_pose_active'))
            if (name == 'filter' and compatibility._battle_active and
                    (direct_start_filter or direct_gun_sync or
                     direct_avatar_aux_sync or direct_avatar_pose_init or
                     direct_fixed_turret_pose)):
                vehicle_filter = (
                    compatibility._original_vehicle_getattribute(
                        vehicle, name))
                pose_matrix = (overlay['matrix']
                               if direct_fixed_turret_pose else None)
                return _OfflineVehicleFilterSyncProxy(
                    vehicle_filter, pose_matrix)
            if name == 'cell' and compatibility._battle_active:
                try:
                    return compatibility._original_vehicle_getattribute(
                        vehicle, 'fakeCell')
                except AttributeError:
                    player = runtime.bigworld.player()
                    if isinstance(player, avatar_type):
                        try:
                            return compatibility._original_avatar_getattribute(
                                player, 'fakeServer')
                        except AttributeError:
                            pass
            return compatibility._original_vehicle_getattribute(vehicle, name)

        def vehicle_setattr(vehicle, name, value):
            if (compatibility._battle_active and
                    name in ('health', 'isCrewActive')):
                compatibility._vehicle_property_overlays.setdefault(
                    id(vehicle), {})[name] = value
                return None
            if (compatibility._battle_active and
                    name in ('position', 'yaw', 'matrix')):
                overlay = compatibility._vehicle_property_overlays.get(
                    id(vehicle))
                if overlay is not None and overlay.get('_pose_active'):
                    overlay[name] = value
                    return None
            return compatibility._original_vehicle_setattr(
                vehicle, name, value)

        def vehicle_get_speed(vehicle):
            if compatibility._battle_active:
                overlay = compatibility._vehicle_property_overlays.get(
                    id(vehicle))
                if (overlay is not None and
                        overlay.get('_pose_active') and
                        'speed' in overlay):
                    return overlay['speed']
            return compatibility._original_vehicle_get_speed(vehicle)

        def avatar_get_speeds(avatar, get_instantaneous=False):
            """Expose copied local physics to stock speed/dispersion users."""
            if compatibility._battle_active:
                try:
                    vehicle_id = compatibility._original_avatar_getattribute(
                        avatar, 'playerVehicleID')
                    vehicle = runtime.bigworld.entity(vehicle_id)
                    overlay = compatibility._vehicle_property_overlays.get(
                        id(vehicle))
                except (AttributeError, ReferenceError, TypeError):
                    overlay = None
                if (overlay is not None and overlay.get('_pose_active') and
                        'speed' in overlay and 'turn_speed' in overlay):
                    return (float(overlay['speed']),
                            float(overlay['turn_speed']))
            return compatibility._original_avatar_get_speeds(
                avatar, get_instantaneous)

        def avatar_auto_aim(avatar, target):
            """Admit the private remote Vehicle to the stock lock lifecycle.

            ``BigWorld.target()`` cannot return our Python gameplay adapter:
            its rendered owner is an ``OfflineEntity``.  The battle runtime
            therefore publishes the vehicle selected by the same precise ray
            that owns the outline.  Once admitted, keep #1513's native state,
            aiming mode, gun-rotator mode and sound notification sequence.
            """
            if not compatibility._battle_active:
                return compatibility._original_avatar_auto_aim(
                    avatar, target)
            current_id = compatibility._original_avatar_getattribute(
                avatar, '_PlayerAvatar__autoAimVehID')
            candidate = compatibility._target_lock_candidate
            if (candidate is not None and
                    target is getattr(candidate, 'bw_entity', None)):
                target = candidate
            if not bool(getattr(
                    target, '_offlineLANPresentation', False)):
                return compatibility._original_avatar_auto_aim(
                    avatar, target)

            alive = getattr(target, 'isAlive')
            alive = alive() if callable(alive) else bool(alive)
            rejected = (
                int(getattr(target, 'id')) == int(current_id) or
                int(getattr(target, 'team')) == int(getattr(avatar, 'team')) or
                not alive)
            if rejected:
                if current_id:
                    # Let stock #1513 own the full unlock transition,
                    # including its aimingInfo convergence timestamp/factor.
                    return compatibility._original_avatar_auto_aim(
                        avatar, None)
                return None

            vehicle_id = int(target.id)
            setattr(avatar, '_PlayerAvatar__autoAimVehID', vehicle_id)
            avatar.cell.autoAim(vehicle_id)
            aiming_mode = runtime.constants.AIMING_MODE.TARGET_LOCK
            avatar.inputHandler.setAimingMode(True, aiming_mode)
            avatar.gunRotator.clientMode = False
            aim_sound = runtime.avatar_module.AimSound
            avatar.onLockTarget(aim_sound.TARGET_LOCKED, True)
            runtime.avatar_module.TriggersManager.g_manager.activateTrigger(
                runtime.avatar_module.TRIGGER_TYPE.AUTO_AIM_AT_VEHICLE,
                vehicleId=vehicle_id)
            return None

        def vehicle_leave_world(vehicle):
            original = compatibility._original_vehicle_leave_world
            if not compatibility._battle_active:
                return original(vehicle)
            try:
                player = runtime.bigworld.player()
            except ReferenceError:
                player = None
            callback = getattr(player, 'vehicle_onLeaveWorld', None)
            if callable(callback):
                return original(vehicle)

            # PlayerAvatar.onBecomeNonPlayer normally stopped every Vehicle
            # before the engine clears its PyEntities.  Exact #1513 then calls
            # Vehicle.onLeaveWorld after BigWorld.player() has already become
            # None, although the stock method dereferences it unconditionally.
            # Finish only the two remaining Vehicle-owned stages here.
            stop_extras = getattr(vehicle, '_Vehicle__stopExtras', None)
            if callable(stop_extras):
                stop_extras()
            if bool(getattr(vehicle, 'isStarted', False)):
                stop_visual = getattr(vehicle, 'stopVisual', None)
                if not callable(stop_visual):
                    raise RuntimeError(
                        'retired offline Vehicle cannot stop its visual')
                stop_visual(False)
            if bool(getattr(vehicle, 'isStarted', False)):
                raise RuntimeError(
                    'retired offline Vehicle remained visually started')
            return None

        def vehicle_start_wg_physics(vehicle):
            original = compatibility._original_vehicle_start_wg_physics
            if not compatibility._battle_active:
                return original(vehicle)
            previous = compatibility._vehicle_starting_wg_physics
            compatibility._vehicle_starting_wg_physics = vehicle
            try:
                # Keep the pinned client's complete physics setup.  The
                # scoped filter proxy suppresses only its unsafe initial
                # syncGunAngles native call.
                return original(vehicle)
            finally:
                compatibility._vehicle_starting_wg_physics = previous

        def vehicle_set_gun_angles(vehicle, previous):
            original = compatibility._original_vehicle_set_gun_angles
            if not compatibility._battle_active:
                return original(vehicle, previous)
            outer_vehicle = compatibility._vehicle_syncing_gun_angles
            compatibility._vehicle_syncing_gun_angles = vehicle
            try:
                return original(vehicle, previous)
            finally:
                compatibility._vehicle_syncing_gun_angles = outer_vehicle

        def compound_getattribute(appearance, name):
            value = compatibility._original_compound_getattribute(
                appearance, name)
            if (name == '_CompoundAppearance__filter' and
                    compatibility._battle_active and
                    compatibility._compound_refreshing_models is appearance):
                # __onModelsRefresh calls deactivate()/activate() before its
                # final gun-angle restore.  Those nested methods also read the
                # private filter and must retain its real identity; activate()
                # stores it back on Vehicle.  Return the proxy only to the
                # direct LOAD_ATTR in the original refresh code object.
                try:
                    caller_code = sys._getframe(1).f_code
                except (AttributeError, ValueError):
                    caller_code = None
                if caller_code is compatibility._compound_models_refresh_code:
                    return _OfflineVehicleFilterSyncProxy(value)
            return value

        def compound_deactivate(appearance, stopEffects=True):
            original = compatibility._original_compound_deactivate
            if not compatibility._battle_active:
                return original(appearance, stopEffects)
            try:
                player = runtime.bigworld.player()
            except ReferenceError:
                player = None
            handler = None
            if player is not None:
                try:
                    handler = getattr(player, 'inputHandler', None)
                except ReferenceError:
                    handler = None
            if handler is not None:
                return original(appearance, stopEffects)

            # PlayerAvatar.__destroyGUI clears inputHandler before its later
            # Vehicle.stopVisual loop.  CompoundAppearance.deactivate still
            # calls removeVehicleFromCameraCollider in that window.  Supply a
            # no-op collider owner for exactly this native call and restore
            # the original object/function even if another teardown stage
            # raises.
            fallback = _OfflineCameraColliderHandler()
            if player is not None:
                try:
                    player.inputHandler = fallback
                except Exception:
                    return original(appearance, stopEffects)
                try:
                    return original(appearance, stopEffects)
                finally:
                    if getattr(player, 'inputHandler', None) is fallback:
                        player.inputHandler = None

            bigworld_dict = getattr(runtime.bigworld, '__dict__', {})
            had_player = 'player' in bigworld_dict
            raw_player = bigworld_dict.get('player')
            original_player = runtime.bigworld.player
            surrogate_arena = type('_OfflineArenaOwner', (object,), {
                'onPeriodChange': _OfflineEventSink()})()
            surrogate = type('_OfflineColliderOwner', (object,), {
                'inputHandler': fallback, 'arena': surrogate_arena})()

            def collider_owner(*unused_args, **unused_kwargs):
                return surrogate

            runtime.bigworld.player = collider_owner
            try:
                return original(appearance, stopEffects)
            finally:
                if runtime.bigworld.player is collider_owner:
                    if had_player:
                        runtime.bigworld.player = raw_player
                    else:
                        delattr(runtime.bigworld, 'player')

        def compound_models_refresh(appearance, model_state, resource_list):
            original = compatibility._original_compound_models_refresh
            if not compatibility._battle_active:
                return original(appearance, model_state, resource_list)
            outer_appearance = compatibility._compound_refreshing_models
            compatibility._compound_refreshing_models = appearance
            try:
                return original(appearance, model_state, resource_list)
            finally:
                compatibility._compound_refreshing_models = outer_appearance

        def account_getattribute(account, name):
            if name in ('base', 'cell', 'server'):
                try:
                    is_offline = compatibility._original_account_getattribute(
                        account, 'isOffline')
                except AttributeError:
                    is_offline = False
                if is_offline:
                    return compatibility._original_account_getattribute(
                        account, 'fakeServer')
            return compatibility._original_account_getattribute(account, name)

        def account_become_player(account):
            is_offline = bool(getattr(account, 'isOffline', False))
            if not is_offline:
                return compatibility._original_account_become_player(account)
            if not getattr(account, _OFFLINE_INIT_COMPLETE, False):
                raise RuntimeError(
                    'offline Account initialization did not complete')

            # PlayerAccount.onBecomePlayer in exact build #1513 starts by
            # calling BigWorld.clearAllSpaces().  Our client-only Account is
            # itself hosted in a newly-created space, so the native call would
            # retire the Account that is currently becoming the player.  Skip
            # only that one destructive call and restore the engine function
            # before any lobby code runs.
            original_clear_all_spaces = getattr(
                runtime.bigworld, 'clearAllSpaces', None)

            def preserve_offline_account_space():
                return None

            if callable(original_clear_all_spaces):
                runtime.bigworld.clearAllSpaces = \
                    preserve_offline_account_space
            # See the Avatar wrapper above: a native Account can bind helpers,
            # chat and global events and then fail before the lobby is ready.
            setattr(account, _OFFLINE_RETIRE_PENDING, True)
            try:
                result = compatibility._original_account_become_player(
                    account)
            finally:
                if (callable(original_clear_all_spaces) and
                        getattr(runtime.bigworld, 'clearAllSpaces', None) is
                        preserve_offline_account_space):
                    runtime.bigworld.clearAllSpaces = \
                        original_clear_all_spaces

            if compatibility._show_lobby:
                show_gui = getattr(account, 'showGUI', None)
                if callable(show_gui):
                    show_gui(_pickle.dumps(
                        dict(_LOBBY_GUI_CONTEXT), _pickle.HIGHEST_PROTOCOL))
            setattr(account, _OFFLINE_PLAYER_READY, True)
            return result

        def retire_fake_connection():
            """Run every native disconnect boundary and return its first error."""
            compatibility._connecting = False
            first_error = None

            # A native disconnect retires the current player and its spaces.
            # The fake transport has no engine connection to perform that
            # cleanup for us, so do it before repository listeners run.
            try:
                compatibility.retire_current_player()
            except Exception as error:
                first_error = error
            clear_all_spaces = getattr(runtime.bigworld, 'clearAllSpaces', None)
            if callable(clear_all_spaces):
                try:
                    clear_all_spaces()
                except Exception as error:
                    if first_error is None:
                        first_error = error
            compatibility._fake_connected = False

            try:
                setattr(runtime.connection_manager,
                        '_ConnectionManager__connectionStatus',
                        runtime.login_status.NOT_SET)
            except Exception as error:
                if first_error is None:
                    first_error = error

            notifications = (
                (getattr(runtime.bigworld,
                         'WGC_onServerResponse', None), (False,)),
                (getattr(runtime.connection_manager,
                         'onDisconnected', None), ()),
                (getattr(runtime.player_events,
                         'onDisconnected', None), ()),
            )
            for notification, arguments in notifications:
                if not callable(notification):
                    continue
                try:
                    notification(*arguments)
                except Exception as error:
                    if first_error is None:
                        first_error = error

            # Exact Event dispatch stops at the first failing listener.  Do
            # not let a retained repository outlive a failed listener or a
            # partially-created PyEntity.
            delete_repository = getattr(
                runtime.account_module, '_delAccountRepository', None)
            if callable(delete_repository):
                try:
                    delete_repository()
                except Exception as error:
                    if first_error is None:
                        first_error = error
                finally:
                    # A partial repository can fail inside its own close path
                    # before exact _delAccountRepository clears the global.
                    # Never make the next Account reuse that object.
                    if hasattr(runtime.account_module,
                               'g_accountRepository'):
                        runtime.account_module.g_accountRepository = None
            return first_error

        def connect(server, login_params, progress):
            if server != OFFLINE_SERVER_ADDRESS:
                return compatibility._original_connect(
                    server, login_params, progress)
            compatibility._fake_connected = True
            try:
                # The progress callback mutates connection state and invokes
                # arbitrary native listeners.  It belongs to the same
                # transaction as Account construction, not before rollback.
                progress(1, runtime.login_status.LOGGED_ON, '{}')
                compatibility._create_account_player()
                compatibility._connecting = False
            except Exception:
                retire_fake_connection()
                raise
            return None

        def disconnect():
            if not compatibility._fake_connected:
                return compatibility._original_disconnect()
            first_error = retire_fake_connection()
            if first_error is not None:
                raise first_error
            return None

        def server_time():
            """Advance the native battle clock on the client-only connection.

            The retail server owns ``BigWorld.serverTime()``.  It remains
            frozen on our fake connection, while #1513's stock period
            controller subtracts it from ``periodEndTime`` once per second.
            Reuse the 0.8.2 offline clock law, scoped to an active LAN battle,
            and preserve the original epoch so every native deadline remains
            in one coordinate system.
            """
            if (compatibility._battle_active and
                    compatibility._battle_server_time_origin is not None and
                    compatibility._battle_clock_origin is not None):
                clock = getattr(runtime.bigworld, 'time', None)
                if callable(clock):
                    try:
                        elapsed = (float(clock()) -
                                   compatibility._battle_clock_origin)
                        return (compatibility._battle_server_time_origin +
                                max(0.0, elapsed))
                    except Exception:
                        pass
            return compatibility._original_server_time()

        def target():
            """Expose only the exact outlined visual to native input code.

            #1513's arcade and sniper lock commands call
            ``avatar.autoAim(BigWorld.target())`` while the explicit lock-off
            command calls ``avatar.autoAim(None)``.  Preserve that distinction:
            only replace an empty engine target lookup here, never reinterpret
            a literal ``None`` inside ``Avatar.autoAim``.
            """
            current = compatibility._original_target()
            candidate = compatibility._target_lock_candidate
            if (current is None and compatibility._battle_active and
                    candidate is not None):
                return candidate.bw_entity
            return current

        def debug_update(panel, ping, fps, isLaggingNow, fpsReplay=-1):
            """Render LAN transport health during a client-only battle.

            Exact #1513's DebugController reads BigWorld.statPing() and
            statLagDetected(), which describe the absent retail game-server
            transport.  Keep the stock panel and replace only those two
            values while the explicit LAN battle client is attached.
            """
            client = compatibility._battle_network_client
            if compatibility._battle_active and client is not None:
                connected = bool(getattr(client, 'connected', False))
                sample = getattr(client, 'rtt_ms', None)
                if sample is None:
                    ping = 0 if connected else 999
                else:
                    try:
                        sample = float(sample)
                        if sample != sample:
                            raise ValueError('NaN LAN RTT')
                        ping = int(round(max(0.0, min(sample, 999.0))))
                    except (TypeError, ValueError, OverflowError):
                        ping = 0 if connected else 999
                isLaggingNow = not connected
            return compatibility._original_debug_update(
                panel, ping, fps, isLaggingNow, fpsReplay)

        self._account_init_wrapper = account_init
        self._account_getattribute_wrapper = account_getattribute
        self._account_become_player_wrapper = account_become_player
        self._account_become_non_player_wrapper = account_become_non_player
        self._avatar_init_wrapper = avatar_init
        self._avatar_getattribute_wrapper = avatar_getattribute
        self._avatar_become_player_wrapper = avatar_become_player
        self._avatar_become_non_player_wrapper = avatar_become_non_player
        self._avatar_enter_world_wrapper = avatar_enter_world
        self._avatar_leave_world_wrapper = avatar_leave_world
        self._avatar_vehicle_enter_wrapper = avatar_vehicle_enter
        self._avatar_prereqs_loaded_wrapper = avatar_prereqs_loaded
        self._avatar_aux_physics_wrapper = avatar_aux_physics
        self._avatar_get_speeds_wrapper = avatar_get_speeds
        self._avatar_auto_aim_wrapper = avatar_auto_aim
        self._control_mode_changed_wrapper = control_mode_changed
        self._consistent_link_own_vehicle_wrapper = \
            consistent_link_own_vehicle
        self._steady_relink_sources_wrapper = steady_relink_sources
        self._vehicle_getattribute_wrapper = vehicle_getattribute
        self._vehicle_setattr_wrapper = vehicle_setattr
        self._vehicle_get_speed_wrapper = vehicle_get_speed
        self._vehicle_leave_world_wrapper = vehicle_leave_world
        self._vehicle_start_wg_physics_wrapper = vehicle_start_wg_physics
        self._vehicle_set_gun_angles_wrapper = vehicle_set_gun_angles
        self._compound_getattribute_wrapper = compound_getattribute
        self._compound_deactivate_wrapper = compound_deactivate
        self._compound_models_refresh_wrapper = compound_models_refresh
        self._connect_wrapper = connect
        self._disconnect_wrapper = disconnect
        self._server_time_wrapper = server_time
        self._target_wrapper = target
        self._debug_update_wrapper = debug_update
        try:
            self._install_host()
            account_type.__init__ = account_init
            account_type.__getattribute__ = account_getattribute
            if self._original_account_become_player is not None:
                account_type.onBecomePlayer = account_become_player
            if self._original_account_become_non_player is not None:
                account_type.onBecomeNonPlayer = account_become_non_player
            avatar_type.__init__ = avatar_init
            avatar_type.__getattribute__ = avatar_getattribute
            avatar_type.onBecomePlayer = avatar_become_player
            if self._original_avatar_become_non_player is not None:
                avatar_type.onBecomeNonPlayer = avatar_become_non_player
            if self._original_avatar_enter_world is not None:
                avatar_type.onEnterWorld = avatar_enter_world
            if self._original_avatar_leave_world is not None:
                avatar_type.onLeaveWorld = avatar_leave_world
            if self._original_avatar_vehicle_enter is not None:
                avatar_type.vehicle_onEnterWorld = avatar_vehicle_enter
            if self._original_avatar_prereqs_loaded is not None:
                avatar_type.onPrereqsLoaded = avatar_prereqs_loaded
            if self._original_avatar_aux_physics is not None:
                avatar_type._PlayerAvatar__onSetOwnVehicleAuxPhysicsData = (
                    avatar_aux_physics)
            if self._original_avatar_get_speeds is not None:
                avatar_type.getOwnVehicleSpeeds = avatar_get_speeds
            avatar_type.autoAim = avatar_auto_aim
            input_handler_type.onControlModeChanged = control_mode_changed
            consistent_matrices_type._ConsistentMatrices__linkOwnVehicle = \
                consistent_link_own_vehicle
            steady_matrix_type.relinkSources = steady_relink_sources
            if vehicle_type is not None:
                vehicle_type.__getattribute__ = vehicle_getattribute
                vehicle_type.__setattr__ = vehicle_setattr
                if self._original_vehicle_get_speed is not None:
                    vehicle_type.getSpeed = vehicle_get_speed
                if self._original_vehicle_leave_world is not None:
                    vehicle_type.onLeaveWorld = vehicle_leave_world
                if self._original_vehicle_start_wg_physics is not None:
                    vehicle_type._Vehicle__startWGPhysics = (
                        vehicle_start_wg_physics)
                if self._original_vehicle_set_gun_angles is not None:
                    vehicle_type.set_gunAnglesPacked = vehicle_set_gun_angles
            if compound_type is not None:
                compound_type.__getattribute__ = compound_getattribute
                if self._original_compound_deactivate is not None:
                    compound_type.deactivate = compound_deactivate
                if self._original_compound_models_refresh is not None:
                    compound_type._CompoundAppearance__onModelsRefresh = (
                        compound_models_refresh)
            runtime.bigworld.connect = connect
            runtime.bigworld.disconnect = disconnect
            runtime.bigworld.serverTime = server_time
            runtime.bigworld.target = target
            if self._original_debug_update is not None:
                debug_panel_type.updateDebugInfo = debug_update
            self._installed = True
        except Exception:
            self._rollback_install()
            raise

    def _install_host(self):
        hosts = self._runtime.predefined_hosts
        for host in hosts._hosts:
            if getattr(host, 'url', None) == OFFLINE_SERVER_ADDRESS:
                self._host = host
                self._host_added = False
                return
        self._host = hosts._makeHostItem(
            OFFLINE_SERVER_ADDRESS,
            OFFLINE_SERVER_ADDRESS,
            OFFLINE_SERVER_ADDRESS)
        hosts._hosts.append(self._host)
        self._host_added = True

    def _rollback_install(self):
        runtime = self._runtime
        account_type = runtime.account_module.PlayerAccount
        avatar_type = runtime.avatar_module.PlayerAvatar
        vehicle_type = getattr(
            getattr(runtime, 'vehicle_module', None), 'Vehicle', None)
        compound_type = getattr(
            getattr(runtime, 'compound_appearance_module', None),
            'CompoundAppearance', None)
        debug_panel_type = getattr(runtime, 'debug_panel_type', None)
        input_handler_type = getattr(
            getattr(runtime, 'avatar_input_handler', None),
            'AvatarInputHandler', None)
        consistent_matrices_type = getattr(
            getattr(runtime, 'avatar_position_control', None),
            'ConsistentMatrices', None)
        steady_matrix_type = getattr(
            getattr(runtime, 'steady_vehicle_matrix', None),
            'SteadyVehicleMatrixCalculator', None)
        if (account_type.__dict__.get('__init__') is
                self._account_init_wrapper):
            account_type.__init__ = self._original_account_init
        if (account_type.__dict__.get('__getattribute__') is
                self._account_getattribute_wrapper):
            account_type.__getattribute__ = (
                self._original_account_getattribute)
        if (self._original_account_become_player is not None and
                account_type.__dict__.get('onBecomePlayer') is
                self._account_become_player_wrapper):
            account_type.onBecomePlayer = self._original_account_become_player
        if (self._original_account_become_non_player is not None and
                account_type.__dict__.get('onBecomeNonPlayer') is
                self._account_become_non_player_wrapper):
            account_type.onBecomeNonPlayer = \
                self._original_account_become_non_player
        if (avatar_type.__dict__.get('__init__') is
                self._avatar_init_wrapper):
            avatar_type.__init__ = self._original_avatar_init
        if (avatar_type.__dict__.get('__getattribute__') is
                self._avatar_getattribute_wrapper):
            avatar_type.__getattribute__ = self._original_avatar_getattribute
        if (avatar_type.__dict__.get('onBecomePlayer') is
                self._avatar_become_player_wrapper):
            avatar_type.onBecomePlayer = self._original_avatar_become_player
        if (self._original_avatar_become_non_player is not None and
                avatar_type.__dict__.get('onBecomeNonPlayer') is
                self._avatar_become_non_player_wrapper):
            avatar_type.onBecomeNonPlayer = \
                self._original_avatar_become_non_player
        if (self._original_avatar_enter_world is not None and
                avatar_type.__dict__.get('onEnterWorld') is
                self._avatar_enter_world_wrapper):
            avatar_type.onEnterWorld = self._original_avatar_enter_world
        if (self._original_avatar_leave_world is not None and
                avatar_type.__dict__.get('onLeaveWorld') is
                self._avatar_leave_world_wrapper):
            avatar_type.onLeaveWorld = self._original_avatar_leave_world
        if (self._original_avatar_vehicle_enter is not None and
                avatar_type.__dict__.get('vehicle_onEnterWorld') is
                self._avatar_vehicle_enter_wrapper):
            avatar_type.vehicle_onEnterWorld = (
                self._original_avatar_vehicle_enter)
        if (self._original_avatar_prereqs_loaded is not None and
                avatar_type.__dict__.get('onPrereqsLoaded') is
                self._avatar_prereqs_loaded_wrapper):
            avatar_type.onPrereqsLoaded = (
                self._original_avatar_prereqs_loaded)
        if (self._original_avatar_aux_physics is not None and
                avatar_type.__dict__.get(
                    '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData') is
                self._avatar_aux_physics_wrapper):
            avatar_type._PlayerAvatar__onSetOwnVehicleAuxPhysicsData = (
                self._original_avatar_aux_physics)
        if (self._original_avatar_get_speeds is not None and
                avatar_type.__dict__.get('getOwnVehicleSpeeds') is
                self._avatar_get_speeds_wrapper):
            avatar_type.getOwnVehicleSpeeds = (
                self._original_avatar_get_speeds)
        if (self._original_avatar_auto_aim is not None and
                avatar_type.__dict__.get('autoAim') is
                self._avatar_auto_aim_wrapper):
            avatar_type.autoAim = self._original_avatar_auto_aim
        if (input_handler_type is not None and
                self._original_control_mode_changed is not None and
                input_handler_type.__dict__.get('onControlModeChanged') is
                self._control_mode_changed_wrapper):
            input_handler_type.onControlModeChanged = (
                self._original_control_mode_changed)
        if (consistent_matrices_type is not None and
                self._original_consistent_link_own_vehicle is not None and
                consistent_matrices_type.__dict__.get(
                    '_ConsistentMatrices__linkOwnVehicle') is
                self._consistent_link_own_vehicle_wrapper):
            consistent_matrices_type._ConsistentMatrices__linkOwnVehicle = (
                self._original_consistent_link_own_vehicle)
        if (steady_matrix_type is not None and
                self._original_steady_relink_sources is not None and
                steady_matrix_type.__dict__.get('relinkSources') is
                self._steady_relink_sources_wrapper):
            steady_matrix_type.relinkSources = (
                self._original_steady_relink_sources)
        if (vehicle_type is not None and
                self._original_vehicle_start_wg_physics is not None and
                vehicle_type.__dict__.get('_Vehicle__startWGPhysics') is
                self._vehicle_start_wg_physics_wrapper):
            vehicle_type._Vehicle__startWGPhysics = (
                self._original_vehicle_start_wg_physics)
        if (vehicle_type is not None and
                self._original_vehicle_leave_world is not None and
                vehicle_type.__dict__.get('onLeaveWorld') is
                self._vehicle_leave_world_wrapper):
            vehicle_type.onLeaveWorld = self._original_vehicle_leave_world
        if (vehicle_type is not None and
                vehicle_type.__dict__.get('__getattribute__') is
                self._vehicle_getattribute_wrapper):
            vehicle_type.__getattribute__ = self._original_vehicle_getattribute
        if (vehicle_type is not None and
                vehicle_type.__dict__.get('__setattr__') is
                self._vehicle_setattr_wrapper):
            vehicle_type.__setattr__ = self._original_vehicle_setattr
        if (vehicle_type is not None and
                self._original_vehicle_get_speed is not None and
                vehicle_type.__dict__.get('getSpeed') is
                self._vehicle_get_speed_wrapper):
            vehicle_type.getSpeed = self._original_vehicle_get_speed
        if (vehicle_type is not None and
                self._original_vehicle_set_gun_angles is not None and
                vehicle_type.__dict__.get('set_gunAnglesPacked') is
                self._vehicle_set_gun_angles_wrapper):
            vehicle_type.set_gunAnglesPacked = (
                self._original_vehicle_set_gun_angles)
        if (compound_type is not None and
                self._original_compound_models_refresh is not None and
                compound_type.__dict__.get(
                    '_CompoundAppearance__onModelsRefresh') is
                self._compound_models_refresh_wrapper):
            compound_type._CompoundAppearance__onModelsRefresh = (
                self._original_compound_models_refresh)
        if (compound_type is not None and
                self._original_compound_deactivate is not None and
                compound_type.__dict__.get('deactivate') is
                self._compound_deactivate_wrapper):
            compound_type.deactivate = self._original_compound_deactivate
        if (compound_type is not None and
                compound_type.__dict__.get('__getattribute__') is
                self._compound_getattribute_wrapper):
            compound_type.__getattribute__ = (
                self._original_compound_getattribute)
        if runtime.bigworld.connect is self._connect_wrapper:
            runtime.bigworld.connect = self._original_connect
        if runtime.bigworld.disconnect is self._disconnect_wrapper:
            runtime.bigworld.disconnect = self._original_disconnect
        if runtime.bigworld.serverTime is self._server_time_wrapper:
            runtime.bigworld.serverTime = self._original_server_time
        if runtime.bigworld.target is self._target_wrapper:
            runtime.bigworld.target = self._original_target
        if (debug_panel_type is not None and
                self._original_debug_update is not None and
                debug_panel_type.__dict__.get('updateDebugInfo') is
                self._debug_update_wrapper):
            debug_panel_type.updateDebugInfo = self._original_debug_update
        if self._host_added and self._host is not None:
            try:
                runtime.predefined_hosts._hosts.remove(self._host)
            except ValueError:
                pass
        self._host = None
        self._host_added = False
        self._vehicle_starting_wg_physics = None
        self._vehicle_start_wg_physics_code = None
        self._vehicle_syncing_gun_angles = None
        self._vehicle_set_gun_angles_code = None
        self._gun_rotator_stabilised_code = None
        self._avatar_syncing_aux_physics = None
        self._avatar_aux_physics_code = None
        self._avatar_entering_vehicle = None
        self._avatar_vehicle_enter_code = None
        self._avatar_start_vehicle_visual_code = None
        self._compound_refreshing_models = None
        self._compound_models_refresh_code = None
        self._battle_network_client = None
        self._battle_server_time_origin = None
        self._battle_clock_origin = None
        self._vehicle_property_overlays = {}
        self._control_mode_listener = None
        self._target_lock_candidate = None
        self._installed = False

    def connect(self, show_lobby=False, account_context=None):
        self.install()
        if self.is_ready() or self._connecting:
            return
        self._show_lobby = bool(show_lobby)
        self._account_context = dict(account_context or {})
        provided_state = self._account_context.get('account_state')
        if provided_state is not None:
            self._account_state = provided_state
        self._connecting = True
        params = {
            'login': 'offline',
            'auth_method': 'basic',
            'session': '',
            'token2': '',
        }
        try:
            self._runtime.connection_manager.initiateConnection(
                params, '', OFFLINE_SERVER_ADDRESS)
        except Exception:
            self._connecting = False
            raise

    def is_ready(self):
        if not self._installed:
            return False
        try:
            player = self._runtime.bigworld.player()
            return (self._runtime.connection_manager.isConnected() and
                    player is not None and
                    bool(getattr(player, 'isOffline', False)))
        except Exception:
            return False

    def _discard_partial_account(self):
        runtime = self._runtime
        clear_all_spaces = getattr(runtime.bigworld, 'clearAllSpaces', None)
        if callable(clear_all_spaces):
            try:
                clear_all_spaces()
            except Exception:
                pass
        delete_repository = getattr(
            runtime.account_module, '_delAccountRepository', None)
        if callable(delete_repository):
            try:
                delete_repository()
            except Exception:
                pass
            finally:
                if hasattr(runtime.account_module, 'g_accountRepository'):
                    runtime.account_module.g_accountRepository = None

    def _create_account_player(self):
        runtime = self._runtime
        was_connecting = self._connecting
        # The login screen can call the patched low-level BigWorld.connect
        # directly after the first-run EULA.  Keep every client-only Account
        # construction inside the same property-injection scope, rather than
        # relying on connect() having been entered through our public helper.
        self._connecting = True
        try:
            try:
                space_id = runtime.bigworld.createSpace()
                account_id = runtime.bigworld.createEntity(
                    'Account', space_id, 0, (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0), {})
                account = runtime.bigworld.entities[account_id]
                if not getattr(account, _OFFLINE_INIT_COMPLETE, False):
                    raise RuntimeError(
                        'BigWorld returned a partial offline Account')
                runtime.bigworld.player(account)
                if not getattr(account, _OFFLINE_PLAYER_READY, False):
                    raise RuntimeError(
                        'BigWorld did not promote the offline Account')
                return account
            except Exception:
                self._discard_partial_account()
                raise
        finally:
            self._connecting = was_connecting

    def restore_lobby_account(self):
        """Recreate the fake Account after #1513 clears battle entities.

        ``OfflineMapCreator.destroy()`` calls ``clearEntitiesAndSpaces()``, so
        merely switching the application back to lobby leaves no player
        mailbox.  Creating another Account through the normal patched
        constructor rebinds the retained native account repository and starts
        its synchronization lifecycle again.
        """
        self.install()
        if (not self._fake_connected or
                not self._runtime.connection_manager.isConnected()):
            raise RuntimeError('offline connection is not active')
        player = self._runtime.bigworld.player()
        if player is not None:
            if bool(getattr(player, 'isOffline', False)):
                return player
            raise RuntimeError('another player entity is still active')

        was_connecting = self._connecting
        self._connecting = True
        try:
            try:
                # Avatar.onBecomePlayer removes the battle dispatcher.  During
                # Account.showGUI, #1513 broadcasts IGR state before the normal
                # lobby path recreates it; Hangar remains subscribed and reads
                # the dispatcher in that window.  Pre-create the idempotent
                # default dispatcher so the native coroutine can complete.
                create_dispatcher = getattr(
                    self._runtime.prb_loader,
                    'createBattleDispatcher', None)
                if not callable(create_dispatcher):
                    raise RuntimeError(
                        'prebattle dispatcher restore is unavailable')
                create_dispatcher()
                get_dispatcher = getattr(
                    self._runtime.prb_loader, 'getDispatcher', None)
                if (not callable(get_dispatcher) or
                        get_dispatcher() is None):
                    raise RuntimeError(
                        'prebattle dispatcher restore did not complete')
                return self._create_account_player()
            except Exception:
                # A constructor may have partially rebound the shared native
                # repository before failing.  End the fake connection rather
                # than advertise LOGGED_ON with no valid Account mailbox.
                if (self._fake_connected and
                        self._disconnect_wrapper is not None):
                    try:
                        self._disconnect_wrapper()
                    except Exception:
                        pass
                raise
        finally:
            self._connecting = was_connecting

    def retire_current_player(self):
        """Detach the current offline Account or Avatar before engine clear.

        BigWorld removes a PyEntity's complete instance dictionary.  Native
        Account and Avatar retirement must therefore first release ChatManager,
        account helpers, battle controllers and GUI-space listeners that retain
        the object.  The patched onBecomeNonPlayer methods make this boundary
        idempotent when the later engine clear delivers the callback again.
        """
        self.install()
        try:
            player = self._runtime.bigworld.player()
        except ReferenceError:
            return False
        if player is None:
            return False
        account_type = self._runtime.account_module.PlayerAccount
        avatar_type = self._runtime.avatar_module.PlayerAvatar
        if not isinstance(player, (account_type, avatar_type)):
            raise RuntimeError('unsupported player entity is active')
        if not getattr(player, _OFFLINE_INIT_COMPLETE, False):
            # The wrapper opens its retirement token only after construction
            # has completed and immediately before native onBecomePlayer.
            # A partial constructor therefore has no stock player lifecycle
            # to detach; its owning map cleanup must clear it directly.
            return False
        if not getattr(player, _OFFLINE_RETIRE_PENDING, False):
            return False
        retire = getattr(player, 'onBecomeNonPlayer', None)
        if not callable(retire):
            raise RuntimeError('player retirement boundary is unavailable')
        retire()
        if getattr(player, _OFFLINE_RETIRE_PENDING, False):
            raise RuntimeError('player retirement did not finish')
        if getattr(player, _OFFLINE_PLAYER_READY, False):
            raise RuntimeError('retired player is still marked ready')
        chat_manager = getattr(self._runtime, 'chat_manager', None)
        if (chat_manager is not None and
                getattr(chat_manager, 'playerProxy', None) is not None):
            raise RuntimeError('chat manager retained the retired player')
        return True

    def activate_map(self):
        if self._runtime is None:
            raise RuntimeError('offline compatibility is not installed')
        self._runtime.offline_map_creator.SetActive(True)

    def configure_battle(self, gui_type=None, bonus_type=None,
                         player_name=None, player_team=None):
        """Enable the normal battle UI/input path for the next native Avatar."""
        if player_name is not None:
            player_name = _entity_bytes(player_name, 'OfflinePlayer')
        if player_team is not None:
            player_team = int(player_team)
            if player_team not in (1, 2):
                raise ValueError('Avatar team must be 1 or 2')
        self.install()
        self._battle_active = True
        self._vehicle_property_overlays = {}
        self._target_lock_candidate = None
        self._native_battle = True
        self._battle_gui_type = gui_type
        self._battle_bonus_type = bonus_type
        if player_name is not None:
            self._battle_player_name = player_name
        if player_team is not None:
            self._battle_player_team = player_team
        self._battle_server_time_origin = float(
            self._original_server_time())
        self._battle_clock_origin = float(self._runtime.bigworld.time())
        self.activate_map()

    def set_battle_network_client(self, client):
        """Attach the LAN transport whose RTT should drive the battle HUD."""
        self.install()
        self._battle_network_client = client

    def set_control_mode_listener(self, listener):
        """Publish exact #1513 control-mode transitions to the battle owner."""
        if listener is not None and not callable(listener):
            raise TypeError('control-mode listener must be callable')
        self.install()
        self._control_mode_listener = listener

    def set_target_lock_candidate(self, vehicle):
        """Publish the exact synthetic Vehicle under the native crosshair."""
        if vehicle is not None:
            if not self._battle_active:
                raise RuntimeError('target-lock candidate requires a battle')
            if not bool(getattr(
                    vehicle, '_offlineLANPresentation', False)):
                raise TypeError('target-lock candidate is not a remote Vehicle')
            if getattr(vehicle, 'bw_entity', None) is None:
                raise ValueError(
                    'target-lock candidate has no visual entity')
        self._target_lock_candidate = vehicle
        return True

    def validate_target_lock(self, avatar):
        """Release a stock lock when its private remote target leaves AOI."""
        if not self._battle_active:
            return False
        current_id = self._original_avatar_getattribute(
            avatar, '_PlayerAvatar__autoAimVehID')
        if not current_id:
            return False
        target = self._runtime.bigworld.entity(current_id)
        if target is not None:
            alive = getattr(target, 'isAlive')
            alive = alive() if callable(alive) else bool(alive)
            if alive and bool(getattr(target, '_spot_visible', True)):
                return False
        # Stock owns target-lost state cleanup and convergence bookkeeping.
        self._original_avatar_auto_aim(avatar, None)
        return True

    def set_vehicle_pose_overlay(self, vehicle, position, yaw, matrix,
                                 speed=0.0, turn_speed=0.0):
        """Publish one copied-physics pose through the stock Vehicle API.

        #1513's client-only ``Vehicle`` has no retail cell stream, so its
        native entity transform never advances.  The copied 0.8.2 integrator
        owns the pose; this narrow overlay lets stock camera, gun and
        collision consumers read that same pose without mutating the native
        BigWorld entity or calling the forbidden ``teleport`` operation.
        """
        if not self._battle_active:
            raise RuntimeError('vehicle pose overlay requires a battle')
        overlay = self._vehicle_property_overlays.setdefault(id(vehicle), {})
        overlay['_pose_active'] = True
        overlay['position'] = position
        overlay['yaw'] = float(yaw)
        overlay['matrix'] = matrix
        overlay['speed'] = float(speed)
        overlay['turn_speed'] = float(turn_speed)
        return True

    def bind_vehicle_pose_sources(self, avatar, vehicle):
        """Bind every stock #1513 pose provider to one live matrix.

        Python ``Vehicle.__getattribute__`` is not a complete server-state
        boundary: native matrix providers bypass it.  Bind the exact sources
        consumed by the minimap, camera, aiming systems and gun rotator only
        after the copied-physics overlay has established its canonical pose.
        """
        overlay = self._vehicle_property_overlays.get(id(vehicle))
        if (not self._battle_active or overlay is None or
                not overlay.get('_pose_active')):
            raise RuntimeError('player pose source requires a live overlay')
        matrix = overlay['matrix']
        matrices = getattr(avatar, 'consistentMatrices', None)
        if matrices is None:
            raise RuntimeError('#1513 ConsistentMatrices is unavailable')
        link = getattr(
            matrices, '_ConsistentMatrices__linkOwnVehicle', None)
        attached = getattr(matrices, '_ConsistentMatrices__setTarget', None)
        if not callable(link) or not callable(attached):
            raise RuntimeError(
                '#1513 vehicle matrix binding methods are unavailable')
        link(vehicle)
        attached(matrix, False)

        stabilised = getattr(
            avatar, '_PlayerAvatar__ownVehicleStabMProv', None)
        if stabilised is None:
            raise RuntimeError(
                '#1513 player stabilised matrix provider is unavailable')
        stabilised.target = matrix
        if stabilised.target is not matrix:
            raise RuntimeError(
                '#1513 player stabilised matrix rejected live pose')

        handler = getattr(avatar, 'inputHandler', None)
        calculator = getattr(
            handler, 'steadyVehicleMatrixCalculator', None)
        relink = getattr(calculator, 'relinkSources', None)
        if not callable(relink):
            raise RuntimeError(
                '#1513 steady vehicle matrix relink is unavailable')
        relink()
        return True

    def restore_vehicle_pose_sources(self, avatar, vehicle, native_matrix,
                                     native_stabilised_matrix):
        """Restore the stock providers after the live overlay is cleared."""
        if self._vehicle_property_overlays.get(id(vehicle), {}).get(
                '_pose_active'):
            raise RuntimeError(
                'player pose overlay must be cleared before source restore')
        matrices = getattr(avatar, 'consistentMatrices', None)
        if matrices is None:
            raise RuntimeError('#1513 ConsistentMatrices is unavailable')
        attached = getattr(matrices, '_ConsistentMatrices__setTarget', None)
        if not callable(attached):
            raise RuntimeError(
                '#1513 attached vehicle matrix boundary is unavailable')
        self._original_consistent_link_own_vehicle(matrices, vehicle)
        attached(native_matrix, False)

        stabilised = getattr(
            avatar, '_PlayerAvatar__ownVehicleStabMProv', None)
        if stabilised is None:
            raise RuntimeError(
                '#1513 player stabilised matrix provider is unavailable')
        stabilised.target = native_stabilised_matrix

        handler = getattr(avatar, 'inputHandler', None)
        calculator = getattr(
            handler, 'steadyVehicleMatrixCalculator', None)
        if calculator is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix calculator is unavailable')
        self._original_steady_relink_sources(calculator)
        return True

    def clear_vehicle_pose_overlay(self, vehicle):
        overlay = self._vehicle_property_overlays.get(id(vehicle))
        if overlay is None:
            return False
        for name in ('_pose_active', 'position', 'yaw', 'matrix',
                     'speed', 'turn_speed'):
            overlay.pop(name, None)
        if not overlay:
            self._vehicle_property_overlays.pop(id(vehicle), None)
        return True

    def native_vehicle_attribute(self, vehicle, name):
        """Read a native Vehicle member while a pose overlay is installed."""
        if self._original_vehicle_getattribute is None:
            raise RuntimeError('native Vehicle attribute boundary is unavailable')
        return self._original_vehicle_getattribute(vehicle, name)

    def attach_avatar_server(self, avatar, server):
        proxy = getattr(avatar, 'fakeServer', None)
        attach = getattr(proxy, 'attach', None)
        if not callable(attach):
            raise RuntimeError('Avatar deferred server is unavailable')
        attach(server)
        # Vehicle.cell is resolved through this same strict bridge.
        for entity in getattr(self._runtime.bigworld, 'entities', {}).values():
            if entity is avatar:
                continue
            try:
                entity.fakeCell = proxy
            except Exception:
                pass

    def _prepare_avatar_properties(self, avatar):
        # A client-only BigWorld Entity accepts its typed properties during
        # Python construction, but its STRING converter accepts Python-2
        # ``str`` only.  LAN JSON values are ``unicode``; normalize them here
        # before any property setter runs.  Public 0.9.22 offline layers use
        # this same pre-super property boundary.
        avatar.fakeServer = _DeferredAvatarServer()
        values = {
            # These are server properties in a retail battle.  Seed the exact
            # LAN roster identity before PlayerAvatar.onBecomePlayer creates
            # ArenaDataProvider; a later name fallback must never disagree
            # with the VEHICLE_ADDED record.
            'name': _entity_bytes(
                self._battle_player_name, 'OfflinePlayer'),
            'team': self._battle_player_team,
            'playerVehicleID': 0,
            'ownVehicleAuxPhysicsData': 0,
            'ownVehicleGear': 0,
            'denunciationsLeft': 10,
            'tkillIsSuspected': False,
            'clientCtx': _entity_bytes(''),
            'isObserverBothTeams': False,
            'isGunLocked': False,
            'arenaUniqueID': 0,
            'arenaTypeID': 0,
            'arenaBonusType': 0,
            'arenaGuiType': 0,
            'arenaExtraData': {},
            'weatherPresetID': 0,
            'playLimits': {},
            # These four OWN_CLIENT properties come from AvatarObserver.def,
            # not the root Avatar.def.  A client-only Avatar created with an
            # empty property dictionary does not receive server defaults.
            'remoteCamera': {
                'time': 0.0,
                'shotPoint': self._runtime.math.Vector3(0.0, 0.0, 0.0),
                'zoom': 0,
            },
            'isObserverFPV': False,
            'observerFPVControlMode': 0,
            'numOfObservers': 0,
        }
        for name, value in values.items():
            if not hasattr(avatar, name):
                setattr(avatar, name, value)

    def prepare_avatar(self, avatar):
        if not self._native_battle:
            avatar.inputHandler = _OfflineInputHandler()
        if not hasattr(avatar, 'playLimits'):
            avatar.playLimits = {}

    def deactivate_map(self):
        try:
            if self._runtime is not None:
                self._runtime.offline_map_creator.SetActive(False)
        finally:
            self._battle_active = False
            self._native_battle = False
            self._battle_gui_type = None
            self._battle_bonus_type = None
            self._battle_player_name = 'OfflinePlayer'
            self._battle_player_team = 1
            self._battle_network_client = None
            self._target_lock_candidate = None
            self._battle_server_time_origin = None
            self._battle_clock_origin = None
            self._vehicle_property_overlays = {}

    def disconnect(self):
        if self._runtime is None:
            return
        self._connecting = False
        first_error = None
        try:
            if self._fake_connected:
                if (self._runtime.bigworld.disconnect is
                        self._disconnect_wrapper):
                    self._runtime.connection_manager.disconnect()
                else:
                    self._disconnect_wrapper()
        except Exception as error:
            first_error = error
        try:
            self.deactivate_map()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def fini(self):
        if not self._installed:
            return
        try:
            self.disconnect()
        finally:
            self._arm_sound_shutdown_guard()
            self._rollback_install()

    def _arm_sound_shutdown_guard(self):
        """Protect exact #1513's late SoundGroups.destroy zombie lookup.

        game.fini clears the player Entity before guiModsFini calls this mod,
        but invokes SoundGroups.destroy only afterward.  The engine can retain
        a PlayerAccount identity whose instance dictionary is already empty;
        stock destroy then directly reads its deleted inputHandler.  Arm a
        one-shot instance wrapper that hides only that zombie for the duration
        of the original destroy call and then removes itself.
        """
        module = getattr(self._runtime, 'sound_groups_module', None)
        instance = getattr(module, 'g_instance', None)
        if instance is None:
            return False
        instance_dict = getattr(instance, '__dict__', None)
        if instance_dict is None:
            return False
        current_destroy = getattr(instance, 'destroy', None)
        if not callable(current_destroy):
            return False
        runtime = self._runtime
        player_types = (runtime.account_module.PlayerAccount,
                        runtime.avatar_module.PlayerAvatar)

        def guarded_destroy():
            player_owner_dict = getattr(runtime.bigworld, '__dict__', {})
            had_player_attribute = 'player' in player_owner_dict
            raw_player_attribute = player_owner_dict.get('player')
            original_player = runtime.bigworld.player
            temporary_player = None
            try:
                try:
                    player = original_player()
                except ReferenceError:
                    player = None
                if isinstance(player, player_types):
                    try:
                        player.inputHandler
                    except (AttributeError, ReferenceError):
                        def no_player(*unused_args, **unused_kwargs):
                            return None

                        temporary_player = no_player
                        runtime.bigworld.player = temporary_player
                return current_destroy()
            finally:
                if (temporary_player is not None and
                        runtime.bigworld.player is temporary_player):
                    if had_player_attribute:
                        runtime.bigworld.player = raw_player_attribute
                    else:
                        delattr(runtime.bigworld, 'player')
                if instance.__dict__.get('destroy') is guarded_destroy:
                    delattr(instance, 'destroy')

        instance.destroy = guarded_destroy
        return True


g_compatibility = OfflineCompatibility()
