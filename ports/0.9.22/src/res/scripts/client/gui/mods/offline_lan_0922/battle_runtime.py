from __future__ import print_function

"""Playable #1513 battle runtime built on stock Avatar and Vehicle entities."""

import math
import random
import sys
import time

from gui.mods.offline_lan_0922.ai import maps as tactical_maps
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
from gui.mods.offline_lan_0922.entities.avatar_server import AvatarServerBridge
from gui.mods.offline_lan_0922.entities.bigworld_binding import \
    BigWorldVehicleBinding
from gui.mods.offline_lan_0922.entities.runtime import EntityPropertyBuilder
from gui.mods.offline_lan_0922.snapshot_sync import SnapshotSync


FRAME_SECONDS = 1.0 / 60.0
AMMO_SECONDS = 0.10
NETWORK_INPUT_SECONDS = 1.0 / 30.0
STANDARD_GAMEPLAY = 'ctf'

BOT_VEHICLE_CANDIDATES = (
    'ussr:R05_KV', 'germany:G04_PzVI_Tiger_I',
    'ussr:R04_T-34', 'usa:A05_M4_Sherman',
    'ussr:R02_SU-85', 'germany:G09_Hetzer',
    'ussr:R03_BT-7', 'usa:A03_M3_Stuart',
    'germany:G02_Hummel', 'usa:A107_T1_HMC')


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


def _load_runtime():
    import AccountCommands
    import ArenaType
    import BigWorld
    import Math
    import constants
    import game
    from OfflineMapCreator import g_offlineMapCreator
    from gun_rotation_shared import encodeGunAngles
    from gui.app_loader import g_appLoader
    from gui.app_loader.settings import GUI_GLOBAL_SPACE_ID
    from gui.mods.offline_lan_0922.compat import g_compatibility
    from gui.shared.utils import HangarSpace
    from items import vehicles

    class Runtime(object):
        pass

    runtime = Runtime()
    runtime.account_commands = AccountCommands
    runtime.app_loader = g_appLoader
    runtime.arena_cache = ArenaType.g_cache
    runtime.bigworld = BigWorld
    runtime.compatibility = g_compatibility
    runtime.constants = constants
    runtime.encode_gun_angles = encodeGunAngles
    runtime.game = game
    runtime.gui_global_space_id = GUI_GLOBAL_SPACE_ID
    runtime.hangar_space = HangarSpace
    runtime.math = Math
    runtime.offline_map_creator = g_offlineMapCreator
    runtime.vehicles = vehicles
    return runtime


class _LANInputSender(object):

    def __init__(self, owner):
        self.owner = owner
        self.forward = 0.0
        self.turn = 0.0
        self.aim_yaw = 0.0
        self.gun_pitch = 0.0

    def send_avatar_input(self, vehicle_id, kind, payload):
        payload = payload if isinstance(payload, dict) else {}
        if kind == 'move':
            flags = int(payload.get('flags', 0))
            self.forward = 1.0 if flags & 1 else (-1.0 if flags & 2 else 0.0)
            self.turn = 1.0 if flags & 8 else (-1.0 if flags & 4 else 0.0)
            return self.send_current()
        if kind == 'cruise':
            mode = int(payload.get('mode', 0))
            self.forward = 1.0 if mode > 0 else (-1.0 if mode < 0 else 0.0)
            return self.send_current()
        if kind in ('track_world', 'track_relative'):
            self._track(payload.get('point'), kind == 'track_relative')
            return self.send_current()
        if kind == 'stop_tracking':
            unused_position, vehicle_yaw = self.owner.local_pose()
            self.aim_yaw = vehicle_yaw + _number(payload.get('turret_yaw'))
            self.gun_pitch = _number(payload.get('gun_pitch'))
            return self.send_current()
        if kind == 'shoot':
            return self.owner.shoot(self.aim_yaw, self.gun_pitch)
        if kind == 'development':
            return True
        return False

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
        return self.owner.client.send_input(
            self.forward, self.turn, self.aim_yaw, self.gun_pitch,
            position, yaw)


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
        self._sender = None
        self._sync = None
        self._bots = None
        self._records = {}
        self._last_snapshot = None
        self._last_frame_time = None
        self._local_position = (0.0, 0.0, 0.0)
        self._local_yaw = 0.0
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._bot_fire_seen = {}
        self._local_speed = 0.0
        self._input_accumulator = 0.0
        self._player_reload_until = 0.0
        self._player_reload_time = 0.0
        self._battle_result = None
        self._round_finished_notified = False
        self._on_local_leave = None

    def start(self, config, message=None, lan_client=None,
              on_local_leave=None):
        if self.state not in ('idle', 'stopped', 'failed'):
            return False
        if lan_client is None:
            raise ValueError('LAN client is required')
        self._runtime = self._runtime or _load_runtime()
        self._config = dict(config or {})
        self._start_message = dict(message or {})
        self.client = lan_client
        self._last_snapshot = None
        self._last_frame_time = None
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._bot_fire_seen = {}
        self._local_speed = 0.0
        self._input_accumulator = 0.0
        self._player_reload_until = 0.0
        self._player_reload_time = 0.0
        self._battle_result = self._start_message.get('battle_result')
        self._round_finished_notified = False
        self._on_local_leave = on_local_leave
        self._lobby_retire_started = False
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
            constants = self._runtime.constants
            local_identity = self._local_state()
            self._runtime.compatibility.configure_battle(
                getattr(constants.ARENA_GUI_TYPE, 'RANDOM', 0),
                getattr(constants.ARENA_BONUS_TYPE, 'REGULAR', 0),
                local_identity.get('name', self.client.name),
                int(local_identity.get('team', self.client.team)))
            self._retire_lobby_entities()
            self._install_battle_gui_guard()
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
            if not getattr(
                    self._avatar, '_offlineLANInitComplete', False):
                raise RuntimeError(
                    'stock OfflineMapCreator returned a partial Avatar')
            if not getattr(
                    self._avatar, '_offlineLANPlayerReady', False):
                raise RuntimeError(
                    'stock OfflineMapCreator did not promote its Avatar')
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

    def _retire_lobby_entities(self):
        """Cross the same Account-to-Avatar boundary as the #1513 observer.

        BigWorld cannot safely promote a client-only Avatar while the lobby
        Account and hangar space are still alive.  The public 0.9.22 observer
        clears them before creating its Avatar; retaining the Account here can
        terminate the native process before Python gets a traceback.
        """
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
            # Exact #1513 getSpaceID() returns __ctx.guiSpaceID.  changeSpace()
            # writes that requested id *before* asking the current state to
            # accept it, so a rejected transition leaves the public value
            # polluted.  The state object is the authoritative boundary.
            state = getattr(loader, '_AppLoader__state', None)
            get_state_space_id = getattr(state, 'getSpaceID', None)
            if not callable(get_state_space_id):
                raise RuntimeError('actual battle GUI state is unavailable')
            return get_state_space_id()

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
        for unused_id, arena_type in self._runtime.arena_cache.items():
            if (getattr(arena_type, 'geometryName', None) == map_name and
                    getattr(arena_type, 'gameplayName', None) ==
                    STANDARD_GAMEPLAY):
                return arena_type
        return None

    def _clock(self):
        function = getattr(self._runtime.bigworld, 'time', None)
        if callable(function):
            try:
                return float(function())
            except Exception:
                pass
        return time.time()

    def _schedule(self, delay, function, ammo=False):
        generation = self._generation
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
                outfit_provider=lambda unused_descriptor: '')
            builder = EntityPropertyBuilder(
                BigWorldVehicleBinding.PROPERTY_NAMES)
            self._sender = _LANInputSender(self)
            commands = self._runtime.account_commands
            self._server = AvatarServerBridge(
                self._avatar, self._binding, builder, self._sender,
                account_commands=(commands.CMD_GET_AVATAR_SYNC,
                                  commands.CMD_ADD_INT_USER_SETTINGS,
                                  commands.CMD_DEL_INT_USER_SETTINGS),
                on_ready=self._on_client_ready,
                on_leave=self._defer_avatar_leave)
            self._runtime.compatibility.attach_avatar_server(
                self._avatar, self._server)
            position, yaw = self._state_world_pose(local)
            properties = self._binding.properties_from_compact_descr(
                descriptor.makeCompactDescr(), int(local.get('team', 1)),
                local.get('name', self._config.get('name', 'Player')))
            properties['health'] = max(1, min(
                int(local.get('health', descriptor.maxHealth)),
                int(descriptor.maxHealth)))
            snapshot = {
                'properties': properties,
                'position': self._vector(position),
                'rotation': (yaw, 0.0, 0.0),
                'period': 'battle',
            }
            vehicle_id = self._server.addVehicleToArena(snapshot)
            self._invalidate_native_arena_info()
            local_key = 'player:%s' % self.client.player_id
            self._records[local_key] = {
                'engine_id': vehicle_id, 'state': dict(local),
                'kind': 'player', 'network_id': self.client.player_id,
                'local': True, 'ready': False}
            self._local_position = position
            self._local_yaw = yaw
            self._local_descriptor = descriptor
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
            record['ready'] = True
            self._sync = SnapshotSync(
                self.client.player_id, on_event=self._apply_sync_event,
                clock=self._clock)
            self._sync.manifest(self._start_message)
            if self._last_snapshot is not None:
                self._sync.snapshot(self._last_snapshot)
            self._bots = BotRuntime(
                self.client.player_id,
                descriptor_resolver=self._resolve_descriptor,
                direction_probe=self._direction_probe,
                vehicle_selector=self._select_bot_vehicle,
                visibility_probe=self._bot_visibility)
            for outgoing in self._bots.battle_start(self._start_message):
                self._send_bot_message(outgoing)
            if self._last_snapshot is not None:
                self._bots.apply_snapshot(self._last_snapshot)
            self.state = 'running'
            self._last_frame_time = self._clock()
            self._sender.send_current()
            self._ammo_tick()
            if self.state != 'running':
                return
            if self._battle_result is not None:
                self._apply_battle_result(self._battle_result)
            if self.state != 'running':
                return
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

    def _resolve_descriptor(self, vehicle_name):
        try:
            return self._runtime.vehicles.VehicleDescr(typeName=vehicle_name)
        except Exception:
            return self._runtime.vehicles.VehicleDescr(
                typeName=self._config['vehicle'])

    def _select_bot_vehicle(self, raw):
        requested = raw.get('vehicle')
        if requested:
            return requested
        slot = int(raw.get('slot', 0))
        offset = 0 if int(raw.get('team', 1)) == 1 else 3
        for index in range(len(BOT_VEHICLE_CANDIDATES)):
            candidate = BOT_VEHICLE_CANDIDATES[
                (slot + offset + index) % len(BOT_VEHICLE_CANDIDATES)]
            try:
                self._runtime.vehicles.VehicleDescr(typeName=candidate)
                return candidate
            except Exception:
                continue
        return self._config['vehicle']

    def _formation_pose(self, team, slot):
        data = tactical_maps.get_tactical_map(self._config['map']) or {}
        bases = data.get('bases', {})
        base = bases.get(team)
        enemy = bases.get(2 if team == 1 else 1)
        if base is None:
            base = (0.0, -35.0 if team == 1 else 35.0)
        if enemy is None:
            enemy = (base[0], base[1] + (70.0 if team == 1 else -70.0))
        dx = float(enemy[0]) - float(base[0])
        dz = float(enemy[1]) - float(base[1])
        length = max(1.0, math.sqrt(dx * dx + dz * dz))
        forward_x, forward_z = dx / length, dz / length
        side_x, side_z = forward_z, -forward_x
        column = int(slot) % 5 - 2
        row = int(slot) // 5
        x = float(base[0]) + side_x * column * 5.5 - forward_x * row * 7.0
        z = float(base[1]) + side_z * column * 5.5 - forward_z * row * 7.0
        return (x, 0.0, z), math.atan2(forward_x, forward_z)

    def _state_world_pose(self, state):
        if bool(state.get('world_pose', False)):
            position = (_number(state.get('x')), _number(state.get('y')),
                        _number(state.get('z')))
            yaw = _number(state.get('yaw'))
        else:
            position, yaw = self._formation_pose(
                int(state.get('team', 1)), int(state.get('slot', 0)))
        ground = self._ground_y(position[0], position[2], position[1])
        if ground is not None:
            position = (position[0], ground, position[2])
        return position, yaw

    def _vector(self, position):
        return self._runtime.math.Vector3(
            float(position[0]), float(position[1]), float(position[2]))

    def _ground_y(self, x, z, hint=0.0):
        hit = self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID,
            self._vector((x, max(300.0, hint + 50.0), z)),
            self._vector((x, min(-500.0, hint - 100.0), z)), 128)
        if hit is None:
            return None
        return float(hit[0].y)

    def _direction_probe(self, position, yaw):
        x, y, z = _xyz(position)
        distance = 5.0
        nx = x + math.sin(yaw) * distance
        nz = z + math.cos(yaw) * distance
        collision = self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID, self._vector((x, y + 1.1, z)),
            self._vector((nx, y + 1.1, nz)), 128) is not None
        next_y = self._ground_y(nx, nz, y)
        slope = 99.0 if next_y is None else (next_y - y) / distance
        water = False
        collide_water = getattr(self._runtime.bigworld, 'wg_collideWater', None)
        if callable(collide_water) and next_y is not None:
            try:
                water_value = collide_water(
                    self._vector((nx, next_y + 20.0, nz)),
                    self._vector((nx, next_y - 5.0, nz)), False)
                water = water_value is not None and water_value >= 0.0
            except Exception:
                water = True
        return {'clear': next_y is not None, 'collision': collision,
                'water': water, 'slope': slope}

    def local_pose(self):
        entity = None
        if self._server is not None and self._server.vehicle_id is not None:
            entity = self._runtime.bigworld.entity(self._server.vehicle_id)
        if entity is not None:
            position = _xyz(getattr(entity, 'position', self._local_position))
            try:
                yaw = float(entity.yaw)
            except Exception:
                yaw = self._local_yaw
            self._local_position, self._local_yaw = position, yaw
        return self._local_position, self._local_yaw

    def _on_client_ready(self):
        self._client_ready_received = True
        if self.state == 'running':
            self._sender.send_current()
            self._ammo_tick()

    def _ammo_tick(self):
        if self.state != 'running' or self._server is None:
            return
        try:
            entity = self._runtime.bigworld.entity(self._server.vehicle_id)
            if entity is None or entity.typeDescriptor is None:
                raise RuntimeError('player Vehicle descriptor is unavailable')
            descriptor = entity.typeDescriptor
            turret = descriptor.turret
            gun = descriptor.gun
            shots = tuple(gun.shots or ())
            current_shell = None
            for shot in shots:
                shell = _field(shot, 'shell', {})
                compact = _field(shell, 'compactDescr', 0)
                quantity = max(1, int(float(gun.maxAmmo) / max(1, len(shots))))
                clip = _field(gun, 'clip', (1,))
                clip_size = int(clip[0]) if clip else 1
                self._avatar.updateVehicleAmmo(
                    self._server.vehicle_id, int(compact),
                    max(0, min(quantity, 65535)),
                    max(0, min(clip_size, 255)),
                    max(-32768, min(int(round(float(gun.reloadTime))),
                                    32767)))
                if current_shell is None:
                    current_shell = compact
            if current_shell is not None:
                self._avatar.updateVehicleSetting(
                    self._server.vehicle_id,
                    self._runtime.constants.VEHICLE_SETTING.CURRENT_SHELLS,
                    current_shell)
            turret_yaw, gun_pitch = entity.getAimParams()
            self._avatar.updateTargetingInfo(
                turret_yaw, gun_pitch, turret.rotationSpeed,
                gun.rotationSpeed, 1.0, 0.0, 0.0, 0.0,
                gun.aimingTime)
            if self._player_reload_until > 0.0:
                remaining = max(0.0, self._player_reload_until - self._clock())
                self._avatar.updateVehicleGunReloadTime(
                    self._server.vehicle_id, remaining,
                    self._player_reload_time)
                if remaining <= 0.0:
                    self._player_reload_until = 0.0
        except Exception as error:
            self._fail(error)
            return
        self._schedule(AMMO_SECONDS, self._ammo_tick, ammo=True)

    def on_snapshot(self, message):
        if self.state in ('failed', 'stopped'):
            return
        try:
            self._last_snapshot = dict(message or {})
            if self._last_snapshot.get('battle_result') is not None:
                self._apply_battle_result(
                    self._last_snapshot['battle_result'])
            if self._bots is not None:
                if 'bot_authority_id' in self._last_snapshot:
                    self._reconcile_bot_authority(
                        self._last_snapshot.get('bot_authority_id'))
                self._bots.apply_snapshot(self._last_snapshot)
            if self._sync is not None:
                self._sync.snapshot(message)
        except Exception as error:
            self._fail(error)

    def _reconcile_bot_authority(self, player_id):
        """Recover authority changes even if the one-shot event was missed."""
        if (self._bots is None or
                getattr(self._bots, 'authority_id', None) == player_id):
            return False
        start = dict(self._start_message or {})
        start['bot_authority_id'] = player_id
        snapshot = self._last_snapshot or {}
        start['bot_manifest'] = snapshot.get(
            'bots', start.get('bot_manifest', []))
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
        for event in (message or {}).get('events') or ():
            if not isinstance(event, dict):
                continue
            kind = event.get('kind')
            if kind == 'authority' and self._bots is not None:
                changed = self._reconcile_bot_authority(
                    event.get('player_id'))
                if changed and self._last_snapshot is not None:
                    self._bots.apply_snapshot(self._last_snapshot)
            elif kind in ('shot', 'bot_shot'):
                self._show_shot(event)
            elif kind == 'battle_result':
                self._apply_battle_result(event)

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
        callback(max(0, min(int(result.get('winner', 0)), 2)), reason)
        self._round_finished_notified = True
        return True

    def _show_shot(self, event):
        attacker = event.get('attacker', event.get('attacker_bot'))
        for kind in ('player', 'bot'):
            record = self._records.get('%s:%s' % (kind, attacker))
            if record is None:
                continue
            entity = self._runtime.bigworld.entity(record['engine_id'])
            if entity is not None:
                try:
                    # Vehicle.def transports only burstCount.  The Python
                    # implementation's optional prediction flag is not part
                    # of the #1513 mailbox schema.
                    entity.showShooting(0)
                except Exception:
                    pass
            return

    def _frame(self):
        if self.state != 'running':
            return
        now = self._clock()
        dt = max(0.0, min(now - self._last_frame_time, 0.1))
        self._last_frame_time = now
        try:
            self._flush_pending_entities(now)
            self._drive_local(dt)
            if self._sync is not None:
                self._sync.advance(now)
            if self._bots is not None:
                players = (self._last_snapshot or {}).get('players', [])
                for outgoing in self._bots.update(dt, now, players=players):
                    for state in outgoing.get('bots') or ():
                        ground = self._ground_y(
                            state.get('x', 0.0), state.get('z', 0.0),
                            state.get('y', 0.0))
                        if ground is not None:
                            state['y'] = ground
                    self._send_bot_message(outgoing)
                    self._resolve_bot_fire(outgoing)
        except Exception as error:
            self._fail(error)
            return
        self._schedule(FRAME_SECONDS, self._frame)

    def _vehicle_speed_limits(self, descriptor):
        physics = _field(descriptor, 'physics', {}) or {}
        limits = _field(physics, 'speedLimits', None)
        try:
            forward = abs(float(limits[0]))
            backward = abs(float(limits[1]))
        except (TypeError, ValueError, IndexError):
            forward, backward = 14.0, 7.0
        return max(2.0, min(forward, 35.0)), max(1.0, min(backward, 20.0))

    def _drive_local(self, dt):
        if self._sender is None or self._server is None:
            return
        entity = self._runtime.bigworld.entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return
        is_alive = getattr(entity, 'isAlive', None)
        if (self._battle_result is not None or
                (callable(is_alive) and not is_alive()) or
                (not callable(is_alive) and
                 (_number(getattr(entity, 'health', 0.0)) <= 0.0 or
                  not bool(getattr(entity, 'isCrewActive', True))))):
            self._local_speed = 0.0
            self._sender.forward = 0.0
            self._sender.turn = 0.0
            return
        position, yaw = self.local_pose()
        forward_limit, backward_limit = self._vehicle_speed_limits(
            entity.typeDescriptor)
        target_speed = (self._sender.forward * forward_limit
                        if self._sender.forward >= 0.0 else
                        self._sender.forward * backward_limit)
        acceleration = max(3.5, forward_limit * 0.75)
        delta = max(-acceleration * dt, min(
            acceleration * dt, target_speed - self._local_speed))
        self._local_speed += delta
        if abs(target_speed) < 0.01 and abs(self._local_speed) < 0.20:
            self._local_speed = 0.0
        turn_rate = 0.85 * (0.42 + min(
            1.0, abs(self._local_speed) / max(forward_limit, 0.1)) * 0.58)
        yaw += self._sender.turn * turn_rate * dt
        travel_yaw = yaw if self._local_speed >= 0.0 else yaw + math.pi
        probe = self._direction_probe(position, travel_yaw)
        blocked = (not probe.get('clear', False) or
                   probe.get('collision', False) or
                   probe.get('water', False) or
                   abs(_number(probe.get('slope'))) > 0.65)
        if blocked and abs(self._local_speed) > 0.0:
            self._local_speed = 0.0
        nx = position[0] + math.sin(yaw) * self._local_speed * dt
        nz = position[2] + math.cos(yaw) * self._local_speed * dt
        ground = self._ground_y(nx, nz, position[1])
        if ground is None:
            nx, nz, ground = position[0], position[2], position[1]
            self._local_speed = 0.0
        next_position = (nx, ground, nz)
        entity.teleport(self._vector(next_position), (yaw, 0.0, 0.0))
        self._local_position, self._local_yaw = next_position, yaw
        self._avatar.updateOwnVehiclePosition(
            self._vector(next_position), self._vector((yaw, 0.0, 0.0)),
            self._local_speed, self._sender.turn * turn_rate)
        self._input_accumulator += dt
        if self._input_accumulator >= NETWORK_INPUT_SECONDS:
            self._input_accumulator = 0.0
            self._sender.send_current()

    def _bot_visibility(self, source, target):
        source_position = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        start = self._vector((source_position[0], source_position[1] + 2.0,
                              source_position[2]))
        end = self._vector((target_position[0], target_position[1] + 1.5,
                            target_position[2]))
        hit = self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID, start, end, 128)
        if hit is None:
            return True
        return (hit[0] - start).length + 1.5 >= (end - start).length

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
            self._bot_fire_seen[bot_id] = max(previous, fire_seq)
            if fire_seq > previous:
                self._resolve_bot_shot(state, fire_seq)

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
        source = self._runtime.bigworld.entity(source_record['engine_id'])
        target = self._runtime.bigworld.entity(target_record['engine_id'])
        if (source is None or target is None or
                source.typeDescriptor is None or
                not getattr(target, 'isStarted', False)):
            return False
        source_position = _xyz(getattr(source, 'position', state))
        target_position = _xyz(getattr(
            target, 'position', target_record.get('state', {})))
        start = self._vector((source_position[0], source_position[1] + 2.0,
                              source_position[2]))
        destination = self._vector((
            target_position[0], target_position[1] + 1.2,
            target_position[2]))
        direction = destination - start
        maximum = direction.length
        if maximum <= 0.01:
            return False
        direction.normalise()
        end = start + direction.scale(maximum + 8.0)
        world_distance = 999999.0
        try:
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID, start, end, 128)
            if hit is not None:
                world_distance = (hit[0] - start).length
        except Exception:
            pass
        try:
            collisions = target.collideSegmentExt(start, end)
        except Exception:
            return False
        if not collisions:
            return False
        collision = min(collisions, key=lambda item: float(item.dist))
        distance = float(collision.dist)
        if distance > world_distance + 0.5:
            return False
        damage, result = self._shell_damage(
            source.typeDescriptor, collision, distance,
            shell_index=state.get('shell_index'))
        impact = start + direction.scale(distance)
        if target_kind == 'bot':
            return self.client.send_bot_bot_hit(
                bot_id, target_id, shot_seq, damage, result, _xyz(impact))
        if target_kind == 'human':
            return self.client.send_bot_human_hit(
                bot_id, target_id, shot_seq, damage, result, _xyz(impact))
        return False

    def _apply_sync_event(self, event):
        if self.state in ('failed', 'stopped'):
            return
        kind = event.get('type')
        if kind == 'create':
            self._create_remote(event)
        elif kind == 'update':
            self._update_entity(event)
        elif kind == 'destroy':
            self._destroy_entity(event)

    def _create_remote(self, event):
        key = event.get('entity')
        if key in self._records:
            return
        state = dict(event.get('state') or {})
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
            int(state.get('health', descriptor.maxHealth)),
            int(descriptor.maxHealth)))
        position, yaw = self._state_world_pose(state)
        engine_id = self._binding.create_vehicle(
            properties, self._vector(position), (yaw, 0.0, 0.0))
        if engine_id is None:
            raise RuntimeError('createEntity returned no remote Vehicle id')
        self._records[key] = {
            'engine_id': engine_id, 'state': state,
            'kind': event.get('kind'), 'network_id': event.get('id'),
            'local': False, 'ready': False,
            'ready_deadline': self._clock() + float(
                self._config.get('startupTimeoutSeconds', 30.0))}
        self._binding.arena_vehicle_added(engine_id, {
            'properties': properties})
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
        pose = record.pop('pending_pose', None)
        if pose is not None:
            self._apply_record_pose(record, pose)
        self._apply_health(record, record.get('state') or {})
        return True

    def _apply_record_pose(self, record, pose):
        state = record.get('state') or {}
        position = (_number(pose.get('x')), _number(pose.get('y')),
                    _number(pose.get('z')))
        ground = self._ground_y(position[0], position[2], position[1])
        if ground is not None and abs(position[1] - ground) < 8.0:
            position = (position[0], ground, position[2])
        yaw = _number(pose.get('yaw'))
        if record.get('local'):
            # The server pose is an echo of this client's 30 Hz input.  Do not
            # rewind the locally driven tank on every delayed snapshot.
            local, unused_yaw = self.local_pose()
            dx = position[0] - local[0]
            dz = position[2] - local[2]
            if dx * dx + dz * dz > 25.0:
                self._binding.update_vehicle(
                    record['engine_id'], self._vector(position),
                    (yaw, 0.0, 0.0))
                self._local_position, self._local_yaw = position, yaw
                self._avatar.updateOwnVehiclePosition(
                    self._vector(position), self._vector((yaw, 0.0, 0.0)),
                    _number(state.get('speed')), 0.0)
        else:
            self._binding.update_vehicle(
                record['engine_id'], self._vector(position),
                (yaw, 0.0, 0.0))
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
        if record.get('visible_destroy_requested'):
            return
        try:
            entity = self._runtime.bigworld.entity(record['engine_id'])
        except ReferenceError:
            entity = None
        if entity is None:
            return
        self._binding.destroy_entity(record['engine_id'])
        record['visible_destroy_requested'] = True

    def _apply_health(self, record, state):
        if 'health' not in state:
            return
        health = max(0, int(state.get('health', 0)))
        engine_id = record['engine_id']
        if self._last_health.get(engine_id) == health:
            return
        entity = self._runtime.bigworld.entity(engine_id)
        if entity is None:
            return
        self._last_health[engine_id] = health
        previous = getattr(entity, 'health', health)
        entity.health = health
        health_changed = getattr(entity, 'onHealthChanged', None)
        if callable(health_changed):
            health_changed(health, 0, 0)
        else:
            notifier = getattr(entity, 'set_health', None)
            if callable(notifier):
                notifier(previous)
        if record.get('local'):
            if health <= 0:
                self._local_speed = 0.0
                if self._sender is not None:
                    self._sender.forward = 0.0
                    self._sender.turn = 0.0
            self._avatar.updateVehicleHealth(
                engine_id, health, 0, health > 0, False)
        if previous > 0 and health <= 0:
            killed = getattr(self._binding, 'arena_vehicle_killed', None)
            if callable(killed):
                killed(engine_id, 0, 0)

    def _destroy_entity(self, event):
        record = self._records.get(event.get('entity'))
        if record is None or record.get('local'):
            return
        if event.get('keep_corpse'):
            state = dict(record.get('state') or {})
            state.update(event.get('state') or {})
            state['health'] = 0
            state['alive'] = False
            record['state'] = state
            self._materialize_record(record)
            return
        if record.get('ready'):
            self._records.pop(event.get('entity'), None)
            forget = getattr(self._server, 'forgetVehicleEnter', None)
            if callable(forget):
                forget(record['engine_id'])
        else:
            # destroyEntity may be called before an asynchronous Vehicle has
            # entered BigWorld's registry.  Keep a tombstone for the rest of
            # the round so a late onEnterWorld cannot leave an orphan entity.
            record['tombstone'] = True
            record.pop('pending_pose', None)
            try:
                visible = self._runtime.bigworld.entity(
                    record['engine_id']) is not None
            except ReferenceError:
                visible = False
            record['visible_destroy_requested'] = visible
        try:
            self._binding.arena_vehicle_removed(record['engine_id'])
        finally:
            self._binding.destroy_entity(record['engine_id'])

    def shoot(self, aim_yaw, gun_pitch):
        if self.state != 'running' or self._battle_result is not None:
            return False
        if self._server is None:
            return False
        entity = self._runtime.bigworld.entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return False
        is_alive = getattr(entity, 'isAlive', None)
        if ((callable(is_alive) and not is_alive()) or
                (not callable(is_alive) and
                 (_number(getattr(entity, 'health', 0.0)) <= 0.0 or
                  not bool(getattr(entity, 'isCrewActive', True))))):
            return False
        if self._player_reload_until > self._clock():
            return False
        position, yaw = self.local_pose()
        shot_seq = self.client.send_fire(
            0, position, yaw, aim_yaw, gun_pitch)
        if not shot_seq:
            return False
        reload_time = max(0.1, _number(
            _field(entity.typeDescriptor.gun, 'reloadTime', 1.0), 1.0))
        self._player_reload_time = reload_time
        self._player_reload_until = self._clock() + reload_time
        self._avatar.updateVehicleGunReloadTime(
            self._server.vehicle_id, reload_time, reload_time)
        self._resolve_hit(shot_seq, aim_yaw, gun_pitch)
        return True

    def _resolve_hit(self, shot_seq, aim_yaw, gun_pitch):
        entity = self._runtime.bigworld.entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return
        start = None
        direction = None
        try:
            start, direction = self._avatar.gunRotator.getCurShotPosition()
            direction.normalise()
        except Exception:
            position, unused_yaw = self.local_pose()
            start = self._vector((position[0], position[1] + 2.0,
                                  position[2]))
            horizontal = math.cos(gun_pitch)
            direction = self._vector((math.sin(aim_yaw) * horizontal,
                                      math.sin(gun_pitch),
                                      math.cos(aim_yaw) * horizontal))
        end = start + direction.scale(2200.0)
        world_distance = 999999.0
        try:
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID, start, end, 128)
            if hit is not None:
                world_distance = (hit[0] - start).length
        except Exception:
            pass
        target_record = None
        collision = None
        distance = 999999.0
        local_team = int(self._local_state().get('team', 1))
        for record in self._records.values():
            if record.get('local') or int(record['state'].get('team', 0)) == local_team:
                continue
            target = self._runtime.bigworld.entity(record['engine_id'])
            if target is None or not getattr(target, 'isStarted', False):
                continue
            try:
                result = target.collideSegmentExt(start, end)
            except Exception:
                continue
            if not result:
                continue
            nearest = min(result, key=lambda item: float(item.dist))
            if nearest.dist < distance:
                distance = float(nearest.dist)
                target_record = record
                collision = nearest
        if (target_record is None or collision is None or
                distance > world_distance + 0.5):
            return
        damage, result = self._shell_damage(
            entity.typeDescriptor, collision, distance)
        impact = start + direction.scale(distance)
        if target_record.get('kind') == 'bot':
            self.client.send_bot_hit(
                target_record['network_id'], shot_seq, damage, result,
                _xyz(impact))
        else:
            self.client.send_hit(
                target_record['network_id'], shot_seq, damage, result, 0,
                _xyz(impact))

    def _shell_damage(self, descriptor, collision, distance,
                      shell_index=None):
        shots = tuple(descriptor.gun.shots or ())
        if shell_index is None:
            shell_index = getattr(descriptor, 'activeGunShotIndex', 0)
        index = max(0, min(int(shell_index),
                           max(0, len(shots) - 1)))
        shot = shots[index] if shots else {}
        shell = _field(shot, 'shell', {})
        damage_value = _field(shell, 'damage', (100.0,))
        penetration_value = _field(shell, 'piercingPower', (100.0,))
        try:
            damage_average = float(damage_value[0])
        except (TypeError, IndexError):
            damage_average = _number(damage_value, 100.0)
        try:
            penetration = float(penetration_value[0])
        except (TypeError, IndexError):
            penetration = _number(penetration_value, 100.0)
        material = getattr(collision, 'matInfo', None)
        armor = _number(getattr(material, 'armor', 0.0))
        angle_cos = abs(_number(getattr(collision, 'hitAngleCos', 1.0), 1.0))
        if angle_cos < math.cos(math.radians(70.0)):
            return 0, 0
        effective = armor / max(angle_cos, 0.087)
        penetration *= random.uniform(0.75, 1.25)
        if penetration < effective:
            return 0, 1
        return max(1, int(random.uniform(
            damage_average * 0.75, damage_average * 1.25))), 2

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
        if self._binding is not None:
            for key, record in list(self._records.items()):
                if record.get('local'):
                    continue
                try:
                    self._binding.arena_vehicle_removed(record['engine_id'])
                except Exception:
                    pass
                try:
                    self._binding.destroy_entity(record['engine_id'])
                except Exception:
                    pass
            if self._server is not None:
                try:
                    self._server.destroy()
                except Exception:
                    pass
        self._records = {}
        if self._map_create_attempted:
            try:
                self._runtime.compatibility.retire_current_player()
            except Exception as error:
                cleanup_error = error
            # Native retirement and stock map ownership are independent
            # cleanup boundaries.  A partial onBecomeNonPlayer failure must
            # not prevent OfflineMapCreator from releasing its entity, space,
            # mapping and camera ids.
            try:
                self._runtime.offline_map_creator.destroy()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
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
        self._map_create_attempted = False
        self._lobby_retire_started = False
        self._avatar = None
        self._binding = None
        self._server = None
        self._sender = None
        self._sync = None
        self._bots = None
        self._last_snapshot = None
        self._last_frame_time = None
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._vehicle_ready_deadline = 0.0
        self._bot_fire_seen = {}
        self._local_speed = 0.0
        self._input_accumulator = 0.0
        self._player_reload_until = 0.0
        self._player_reload_time = 0.0
        self._battle_result = None
        self._round_finished_notified = False
        self._on_local_leave = None
        if cleanup_error is not None:
            raise cleanup_error

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


g_battle_runtime = BattleRuntime()
