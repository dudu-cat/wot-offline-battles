from __future__ import print_function

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
    import BigWorld
    import ChatManager
    import Math
    import SoundGroups
    import Vehicle
    import constants
    from OfflineMapCreator import g_offlineMapCreator
    from PlayerEvents import g_playerEvents
    from connection_mgr import LOGIN_STATUS
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
    runtime.bigworld = BigWorld
    runtime.chat_manager = ChatManager.chatManager
    runtime.constants = constants
    runtime.connection_manager = dependency.instance(IConnectionManager)
    runtime.login_status = LOGIN_STATUS
    runtime.math = Math
    runtime.offline_map_creator = g_offlineMapCreator
    runtime.player_events = g_playerEvents
    runtime.predefined_hosts = g_preDefinedHosts
    runtime.prb_loader = g_prbLoader
    runtime.sound_groups_module = SoundGroups
    runtime.vehicle_module = Vehicle
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


class _InitialGunSyncFilterProxy(object):
    """Delegate WGVehicleFilter except for the unsafe first gun sync."""

    __slots__ = ('_vehicle_filter',)

    def __init__(self, vehicle_filter):
        self._vehicle_filter = vehicle_filter

    def __getattr__(self, name):
        return getattr(self._vehicle_filter, name)

    def syncGunAngles(self, yaw, pitch):
        # A client-only Vehicle has no retail interpolation filter behind its
        # initial gun-angle sample.  Exact #1513 asserts a null native filter
        # here; later LAN state updates own gun synchronization.
        return None


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
        self._original_avatar_prereqs_loaded = None
        self._original_vehicle_getattribute = None
        self._original_vehicle_start_wg_physics = None
        self._original_connect = None
        self._original_disconnect = None
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
        self._vehicle_getattribute_wrapper = None
        self._vehicle_start_wg_physics_wrapper = None
        self._vehicle_starting_wg_physics = None
        self._connect_wrapper = None
        self._disconnect_wrapper = None

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
        self._original_avatar_prereqs_loaded = avatar_type.__dict__.get(
            'onPrereqsLoaded', getattr(avatar_type, 'onPrereqsLoaded', None))
        if vehicle_type is not None:
            self._original_vehicle_getattribute = vehicle_type.__dict__.get(
                '__getattribute__', vehicle_type.__getattribute__)
            self._original_vehicle_start_wg_physics = (
                vehicle_type.__dict__.get(
                    '_Vehicle__startWGPhysics',
                    getattr(vehicle_type, '_Vehicle__startWGPhysics', None)))
        self._original_connect = runtime.bigworld.connect
        self._original_disconnect = runtime.bigworld.disconnect
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
                accept = getattr(server, 'acceptVehicleEnter', None)
                if callable(accept):
                    try:
                        accept(vehicle.id)
                    except Exception as error:
                        fail = getattr(server, 'failVehicleEnter', None)
                        if callable(fail):
                            fail(vehicle.id, error)
                        raise
            try:
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

        def vehicle_getattribute(vehicle, name):
            if (name == 'filter' and compatibility._battle_active and
                    compatibility._vehicle_starting_wg_physics is vehicle):
                vehicle_filter = (
                    compatibility._original_vehicle_getattribute(
                        vehicle, name))
                return _InitialGunSyncFilterProxy(vehicle_filter)
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
        self._vehicle_getattribute_wrapper = vehicle_getattribute
        self._vehicle_start_wg_physics_wrapper = vehicle_start_wg_physics
        self._connect_wrapper = connect
        self._disconnect_wrapper = disconnect
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
            if vehicle_type is not None:
                vehicle_type.__getattribute__ = vehicle_getattribute
                if self._original_vehicle_start_wg_physics is not None:
                    vehicle_type._Vehicle__startWGPhysics = (
                        vehicle_start_wg_physics)
            runtime.bigworld.connect = connect
            runtime.bigworld.disconnect = disconnect
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
        if (vehicle_type is not None and
                self._original_vehicle_start_wg_physics is not None and
                vehicle_type.__dict__.get('_Vehicle__startWGPhysics') is
                self._vehicle_start_wg_physics_wrapper):
            vehicle_type._Vehicle__startWGPhysics = (
                self._original_vehicle_start_wg_physics)
        if (vehicle_type is not None and
                vehicle_type.__dict__.get('__getattribute__') is
                self._vehicle_getattribute_wrapper):
            vehicle_type.__getattribute__ = self._original_vehicle_getattribute
        if runtime.bigworld.connect is self._connect_wrapper:
            runtime.bigworld.connect = self._original_connect
        if runtime.bigworld.disconnect is self._disconnect_wrapper:
            runtime.bigworld.disconnect = self._original_disconnect
        if self._host_added and self._host is not None:
            try:
                runtime.predefined_hosts._hosts.remove(self._host)
            except ValueError:
                pass
        self._host = None
        self._host_added = False
        self._vehicle_starting_wg_physics = None
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
        self._native_battle = True
        self._battle_gui_type = gui_type
        self._battle_bonus_type = bonus_type
        if player_name is not None:
            self._battle_player_name = player_name
        if player_team is not None:
            self._battle_player_team = player_team
        self.activate_map()

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
