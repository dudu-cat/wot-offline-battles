import importlib.util
import json
import os
from pathlib import Path
import gc
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock
import weakref
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PORT_ROOT = ROOT / 'ports' / '0.9.22'
CLIENT_SCRIPTS = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))


def _load_tool(name):
    path = PORT_ROOT / 'tools' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_port_source(name):
    path = (PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
            'mods' / 'offline_lan_0922' / (name + '.py'))
    spec = importlib.util.spec_from_file_location('port0922_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fake_executable(path, machine=0x014C):
    payload = bytearray(128)
    payload[0:2] = b'MZ'
    payload[0x3C:0x40] = struct.pack('<I', 0x60)
    payload[0x60:0x64] = b'PE\0\0'
    payload[0x64:0x66] = struct.pack('<H', machine)
    path.write_bytes(payload)


def _write_fake_client(root, inspector, build='1513'):
    (root / 'res' / 'packages').mkdir(parents=True)
    _write_fake_executable(root / 'WorldOfTanks.exe')
    (root / 'version.xml').write_text(
        '<version.xml><version>v.0.9.22.0.1 #%s</version></version.xml>' %
        build, encoding='utf-8')
    (root / 'paths.xml').write_text(
        '<root><Paths>'
        '<Path>./res_mods/0.9.22.0.1</Path>'
        '<Path>./mods/0.9.22.0.1</Path>'
        '</Paths></root>', encoding='utf-8')
    with zipfile.ZipFile(root / 'res' / 'packages' / 'scripts.pkg', 'w') as archive:
        for member in inspector.PROBE_MEMBERS:
            archive.writestr(member, b'\x03\xf3\r\n' + b'payload')
        for member in inspector.REQUIRED_SCRIPT_MEMBERS:
            archive.writestr(member, b'payload')
    for package_name, members in inspector.REQUIRED_RESOURCE_MEMBERS.items():
        with zipfile.ZipFile(
                root / 'res' / 'packages' / package_name, 'w') as archive:
            for member in members:
                archive.writestr(member, b'payload')


class ClientInspectorTests(unittest.TestCase):
    def test_reads_version_paths_and_python_27_magic(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_client(root, inspector)

            report = inspector.inspect_client(root)

        self.assertEqual('0.9.22.0.1', report['version'])
        self.assertEqual('1513', report['build'])
        self.assertEqual('./mods/0.9.22.0.1', report['wotmodPath'])
        self.assertEqual('x86', report['architecture'])
        runtimes = {
            value['runtime'] for value in report['bytecode'].values()
        }
        self.assertEqual({'CPython 2.7'}, runtimes)

    def test_rejects_incomplete_client(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                inspector.inspect_client(directory)

    def test_rejects_wrong_client_build(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_client(root, inspector, build='788')
            with self.assertRaisesRegex(ValueError, 'build must be #1513'):
                inspector.inspect_client(root)


class WotmodValidatorTests(unittest.TestCase):
    def _write_archive(self, path, compression, include_directories,
                       pyc_members=None, extras=None):
        entry = 'res/scripts/client/gui/mods/mod_offline_lan_0922.pyc'
        pyc_members = set(pyc_members or (entry,))
        extras = dict(extras or {})
        file_names = set(pyc_members) | set(extras) | {'meta.xml'}
        directories = set()
        for name in file_names:
            parts = name.split('/')[:-1]
            for index in range(1, len(parts) + 1):
                directories.add('/'.join(parts[:index]) + '/')
        meta = (
            '<root><id>org.peng.offline_lan_0922</id>'
            '<version>0.3.2</version></root>')
        with zipfile.ZipFile(path, 'w', compression) as archive:
            if include_directories:
                for directory in sorted(directories):
                    info = zipfile.ZipInfo(directory)
                    info.compress_type = zipfile.ZIP_STORED
                    archive.writestr(info, b'')
            archive.writestr('meta.xml', meta)
            for member in sorted(pyc_members):
                archive.writestr(member, b'\x03\xf3\r\n' + b'payload')
            for member, payload in sorted(extras.items()):
                archive.writestr(member, payload)

    def test_accepts_only_fully_stored_archive_with_parent_directories(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'good.wotmod'
            expected = validator.expected_pyc_members()
            self._write_archive(
                path, zipfile.ZIP_STORED, True, pyc_members=expected)
            with zipfile.ZipFile(path) as archive:
                archive_member_count = len(archive.namelist())
            self.assertEqual(
                archive_member_count, validator.validate(path))

    def test_rejects_missing_directory_members(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing-dirs.wotmod'
            self._write_archive(
                path, zipfile.ZIP_STORED, False,
                pyc_members=validator.expected_pyc_members())
            with self.assertRaisesRegex(ValueError, 'directory members'):
                validator.validate(path)

    def test_rejects_deflated_file_member(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deflated.wotmod'
            self._write_archive(
                path, zipfile.ZIP_DEFLATED, True,
                pyc_members=validator.expected_pyc_members())
            with self.assertRaisesRegex(ValueError, 'not stored'):
                validator.validate(path)

    def test_rejects_stale_package_missing_current_source_module(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stale.wotmod'
            expected = validator.expected_pyc_members()
            missing = next(iter(expected - {validator.ENTRY}))
            self._write_archive(
                path, zipfile.ZIP_STORED, True,
                pyc_members=expected - {missing})
            with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
                validator.validate(path)

    def test_rejects_optimized_or_python3_cache_files(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unwanted.wotmod'
            self._write_archive(
                path, zipfile.ZIP_STORED, True,
                pyc_members=validator.expected_pyc_members(),
                extras={'res/module.pyo': b'optimized'})
            with self.assertRaisesRegex(ValueError, 'unwanted Python files'):
                validator.validate(path)

    def test_rejects_duplicate_archive_members(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.wotmod'
            self._write_archive(
                path, zipfile.ZIP_STORED, True,
                pyc_members=validator.expected_pyc_members())
            with zipfile.ZipFile(path, 'a') as archive:
                with self.assertWarns(UserWarning):
                    archive.writestr('meta.xml', b'duplicate')
            with self.assertRaisesRegex(ValueError, 'duplicate archive members'):
                validator.validate(path)


class PortSourceTests(unittest.TestCase):
    def test_release_entry_and_meta_are_present(self):
        self.assertTrue((PORT_ROOT / 'meta.xml').is_file())
        self.assertTrue((
            PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
            'mods' / 'mod_offline_lan_0922.py').is_file())

    def test_port_sources_are_python_2_compatible_syntax(self):
        source_root = PORT_ROOT / 'src'
        for path in source_root.rglob('*.py'):
            compile(path.read_text(encoding='utf-8'), str(path), 'exec')

    def test_packager_removes_python_3_cache_before_python_2_compile(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'package' / '__pycache__'
            cache.mkdir(parents=True)
            (cache / 'module.cpython-314.pyc').write_bytes(b'python3')
            (root / 'package' / 'stale.pyc').write_bytes(b'python3')
            (root / 'package' / 'keep.py').write_text(
                'value = 1\n', encoding='utf-8')

            packager._remove_stale_bytecode(str(root))

            self.assertFalse(cache.exists())
            self.assertFalse((root / 'package' / 'stale.pyc').exists())
            self.assertTrue((root / 'package' / 'keep.py').exists())

    def test_copy_ready_overlay_contains_pinned_lan_endpoint(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_overlay_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'mod.wotmod'
            checksum = root / 'mod.wotmod.sha256'
            package.write_bytes(b'mod')
            checksum.write_text('checksum\n', encoding='ascii')
            with mock.patch.dict(os.environ, {
                    'OFFLINE_LAN_RELEASE_HOST': '192.168.1.164',
                    'OFFLINE_LAN_RELEASE_PORT': '28782'}):
                overlay, archive = packager._write_client_overlay(
                    str(root), str(package), str(checksum), 'a' * 64)

            config_path = (Path(overlay) / 'mods' / 'configs' /
                           'offline_lan_0922' / 'config.json')
            config = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertEqual('192.168.1.164', config['host'])
            self.assertEqual(28782, config['port'])
            self.assertTrue(Path(archive).is_file())

class PortConfigTests(unittest.TestCase):
    def test_writes_default_and_reads_override(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'config.json')
            config = config_module.load(path)
            self.assertTrue(config['enabled'])
            self.assertEqual('127.0.0.1', config['host'])
            self.assertTrue(Path(path).is_file())
            Path(path).write_text(
                '{"enabled": false, "host": "192.168.1.20"}',
                encoding='utf-8')
            config = config_module.load(path)
            self.assertFalse(config['enabled'])
            self.assertEqual('192.168.1.20', config['host'])

    def test_native_picker_endpoint_round_trip_and_validation(self):
        config_module = _load_port_source('config')

        value = config_module.format_endpoint('192.168.1.164', 28782)
        self.assertEqual('LAN SERVER: 192.168.1.164:28782', value)
        self.assertEqual(
            ('192.168.1.164', 28782),
            config_module.parse_endpoint(value))
        self.assertEqual(
            ('wot-host.local', 28782),
            config_module.parse_endpoint('wot-host.local'))
        for invalid in ('', 'host:0', 'host:65536', 'bad host:28782',
                        'http://host:28782'):
            with self.assertRaises(ValueError, msg=invalid):
                config_module.parse_endpoint(invalid)


class _Vector3(object):
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class _Matrix(object):
    def __init__(self):
        self.translation = None

    def setRotateYPR(self, value):
        self.rotation = value

    def setTranslate(self, value):
        self.translation = value


class _Entity(object):
    pass


class _Model(object):
    pass


class _Resources(dict):
    failedIDs = ()


class _BigWorld(object):
    def __init__(self, auto_load_resources=True):
        self.entities = {}
        self._callbacks = []
        self._next_callback = 1
        self._now = 0.0
        self._next_entity = 100
        self.keys_down = set()
        self.operations = []
        self.compatibility = None
        self.auto_load_resources = auto_load_resources
        self.pending_resource_callback = None
        self._player = None

    def time(self):
        return self._now

    def callback(self, delay, function):
        callback_id = self._next_callback
        self._next_callback += 1
        self._callbacks.append((callback_id, delay, function))
        return callback_id

    def cancelCallback(self, callback_id):
        self._callbacks = [item for item in self._callbacks
                           if item[0] != callback_id]

    def run_next(self):
        callback_id, delay, function = self._callbacks.pop(0)
        self._now += delay
        function()

    def worldDrawEnabled(self, enabled):
        self.operations.append(('draw', enabled))

    def setWatcher(self, name, enabled):
        self.operations.append(('watcher', enabled))

    def createSpace(self):
        self.operations.append(('create_space', 7))
        return 7

    def addSpaceGeometryMapping(self, space_id, matrix, path):
        self.operations.append(('add_mapping', space_id, path))
        return 9

    def delSpaceGeometryMapping(self, space_id, mapping_id):
        self.operations.append(('del_mapping', space_id, mapping_id))

    def createEntity(self, entity_type, space_id, client_only, position,
                     orientation, properties):
        entity_id = self._next_entity
        self._next_entity += 1
        entity = _Entity()
        if entity_type == 'Avatar':
            if self.compatibility is not None:
                if not self.compatibility.map_active:
                    raise AssertionError(
                        'offline map must be active before Avatar creation')
            entity.playerVehicleID = 0
        self.entities[entity_id] = entity
        self.operations.append(('create_entity', entity_type))
        return entity_id

    def player(self, value=None):
        if value is not None:
            self._player = value
        return self._player

    def CursorCamera(self):
        return _Entity()

    def camera(self, value):
        self.operations.append(('camera', value))

    def cameraSpaceID(self, value):
        self.operations.append(('camera_space', value))

    def spaceLoadStatus(self):
        return 1.0

    def wg_collideSegment(self, space_id, start, end, flags):
        if start.x == end.x and start.z == end.z:
            return (_Vector3(start.x, 0.0, start.z), None)
        return None

    def loadResourceListBG(self, resources, callback):
        if self.auto_load_resources:
            callback(_Resources(tank_model=_Model()))
        else:
            self.pending_resource_callback = callback

    def isKeyDown(self, key):
        return key in self.keys_down

    def isClientSpace(self, space_id):
        return True

    def clearEntitiesAndSpaces(self):
        self.operations.append(('clear_entities_spaces',))

    def clearAllSpaces(self):
        self.operations.append(('clear_all_spaces',))

    def clearSpace(self, space_id):
        self.operations.append(('clear_space', space_id))

    def releaseSpace(self, space_id):
        self.operations.append(('release_space', space_id))


class _Compatibility(object):
    def __init__(self, bigworld):
        self.bigworld = bigworld
        self.map_active = False
        self.connected = True
        bigworld.compatibility = self

    def is_ready(self):
        return self.connected

    def activate_map(self):
        self.map_active = True
        self.bigworld.operations.append(('activate_map',))

    def prepare_avatar(self, avatar):
        avatar.inputHandler = object()
        avatar.playLimits = {}
        self.bigworld.operations.append(('prepare_avatar',))

    def deactivate_map(self):
        self.map_active = False
        self.bigworld.operations.append(('deactivate_map',))

    def disconnect(self):
        self.connected = False
        self.bigworld.operations.append(('disconnect',))


class _VerticalOfflineMapCreator(object):
    def __init__(self, bigworld, compatibility, app_loader):
        self.bigworld = bigworld
        self.compatibility = compatibility
        self.app_loader = app_loader
        self.active = False
        self.space_id = None
        self.mapping_id = None

    def Active(self):
        return self.active

    def create(self, map_name):
        self.app_loader.showBattlePage()
        self.space_id = self.bigworld.createSpace()
        self.mapping_id = self.bigworld.addSpaceGeometryMapping(
            self.space_id, None, 'spaces/' + map_name)
        self.compatibility.activate_map()
        avatar_id = self.bigworld.createEntity(
            'Avatar', self.space_id, 0, _Vector3(50.0, 0.0, 50.0),
            (0.0, 0.0, 0.0), {})
        avatar = self.bigworld.entities[avatar_id]
        avatar.id = avatar_id
        avatar.spaceID = self.space_id
        self.bigworld.player(avatar)
        self.active = True

    def destroy(self):
        self.bigworld.operations.append(('offline_map_destroy',))
        if self.space_id is not None and self.mapping_id is not None:
            self.bigworld.delSpaceGeometryMapping(
                self.space_id, self.mapping_id)
        self.bigworld.clearEntitiesAndSpaces()
        self.compatibility.deactivate_map()
        self.app_loader.destroyBattle()
        self.active = False


class _AppLoader(object):
    def __init__(self, operations):
        self.operations = operations

    def createBattle(self, arena_gui_type):
        self.operations.append(('create_battle', arena_gui_type))

    def showBattleLoading(self):
        self.operations.append(('show_battle_loading',))
        return True

    def showBattlePage(self):
        self.operations.append(('show_battle_page',))
        return True

    def destroyBattle(self):
        self.operations.append(('destroy_battle',))
        return True

    def showLogin(self):
        self.operations.append(('show_login',))
        return True


class _CompatEvent(object):
    def __init__(self, operations, name):
        self.operations = operations
        self.name = name

    def __call__(self, *args):
        self.operations.append((self.name,) + args)


class _CompatBigWorld(object):
    _MISSING = object()

    def __init__(self, operations):
        self.operations = operations
        self.account_type = None
        self._callbacks = []
        self.entities = {}
        self._player = None
        self._next_entity = 1

    def connect(self, server, login_params, progress):
        self.operations.append(('original_connect', server))
        return 'online-connect'

    def disconnect(self):
        self.operations.append(('original_disconnect',))
        return 'online-disconnect'

    def createSpace(self):
        self.operations.append(('account_space',))
        return 21

    def createEntity(self, entity_type, space_id, client_only, position,
                     orientation, properties):
        self.operations.append(('account_entity', entity_type))
        if entity_type != 'Account':
            raise AssertionError(entity_type)
        entity_id = self._next_entity
        self._next_entity += 1
        self.entities[entity_id] = self.account_type()
        return entity_id

    def AvatarFilter(self):
        return _CompatAvatarFilter(self.operations)

    def player(self, value=_MISSING):
        if value is not self._MISSING:
            self._player = value
            self.operations.append(('player', value))
            on_become_player = getattr(value, 'onBecomePlayer', None)
            if callable(on_become_player):
                on_become_player()
        return self._player

    def clearAllSpaces(self):
        self.operations.append(('clear_all_spaces',))
        self.entities.clear()
        self._player = None

    def WGC_onServerResponse(self, accepted):
        self.operations.append(('wgc', accepted))

    def callback(self, delay, function):
        self._callbacks.append((delay, function))
        return len(self._callbacks)


class _CompatConnectionManager(object):
    def __init__(self, bigworld, statuses, operations):
        self.bigworld = bigworld
        self.statuses = statuses
        self.operations = operations
        self._ConnectionManager__connectionStatus = statuses.NOT_SET
        self.onLoggedOn = _CompatEvent(operations, 'logged_on')
        self.onConnected = _CompatEvent(operations, 'connected')
        self.onDisconnected = _CompatEvent(operations, 'disconnected')

    def initiateConnection(self, params, password, server):
        self.operations.append(('initiate', server))

        def progress(stage, status, response):
            self._ConnectionManager__connectionStatus = status
            self.operations.append(('progress', stage, status))
            if stage == 1 and status == self.statuses.LOGGED_ON:
                self.bigworld.WGC_onServerResponse(True)
                self.onLoggedOn({})
                self.onConnected()

        return self.bigworld.connect(server, params, progress)

    def disconnect(self):
        self.operations.append(('manager_disconnect',))
        return self.bigworld.disconnect()

    def isConnected(self):
        return (self._ConnectionManager__connectionStatus ==
                self.statuses.LOGGED_ON)


class _OfflineMapCreator(object):
    def __init__(self, operations):
        self.active = False
        self.operations = operations

    def SetActive(self, active):
        self.active = active
        self.operations.append(('map_active', active))


class _CompatAvatarFilter(object):
    def __init__(self, operations):
        self.operations = operations

    def enableLagDetection(self, enabled):
        self.operations.append(('avatar_filter_lag', enabled))

    def syncVector3(self, *args):
        return None

    def getVector3(self, *args):
        return None

    def resetVector3(self, *args):
        return None

    def setInterpolationType(self, *args):
        return None


class _Hosts(object):
    def __init__(self, existing=None, fail=False):
        self._hosts = list(existing or ())
        self.fail = fail

    def _makeHostItem(self, name, short_name, url):
        if self.fail:
            raise RuntimeError('host creation failed')
        return types.SimpleNamespace(name=name, shortName=short_name, url=url)


class OfflineCompatibilityTests(unittest.TestCase):
    def test_native_ready_is_deferred_until_runtime_bridge_attaches(self):
        compatibility_module = _load_port_source('compat')
        deferred = compatibility_module._DeferredAvatarServer()
        target = mock.Mock()

        deferred.setClientReady()
        deferred.autoAim(0)
        deferred.attach(target)

        self.assertEqual(
            [mock.call.setClientReady(), mock.call.autoAim(0)],
            target.mock_calls)

    def _runtime(self, existing_hosts=None, host_failure=False):
        operations = []
        statuses = types.SimpleNamespace(NOT_SET=0, LOGGED_ON=1)
        bigworld = _CompatBigWorld(operations)

        class PlayerAccount(object):
            def __init__(self):
                operations.append(('original_account_init',))
                required_name, required_value = account_module._CLIENT_SERVER_VERSION
                if getattr(self, required_name) != required_value:
                    raise AssertionError('client/server version was not injected')
                if self.name != 'offline_account':
                    raise AssertionError('offline account name was not injected')
                if 'file_server' not in self.initialServerSettings:
                    raise AssertionError('file server settings were not injected')
                regional = self.initialServerSettings['regional_settings']
                if 'starting_time_of_a_new_game_day' not in regional:
                    raise AssertionError('game-day start was not injected')
                if 'voipDomain' in self.initialServerSettings:
                    raise AssertionError('offline settings must not enable VOIP')
                ranked = self.initialServerSettings['ranked_config']
                if ranked.get('isEnabled') is not False:
                    raise AssertionError('ranked battles must be disabled')
                self._ClientChat__chatActionCallbacks = {}
                self._PlayerAccount__onCmdResponse = {}
                self._PlayerAccount__onStreamComplete = {}
                self.isLongDisconnectedFromCenter = False
                self._idGen = object()

            def onBecomePlayer(self):
                operations.append(('original_account_become_player',))
                bigworld.clearAllSpaces()

            def showGUI(self, context):
                operations.append(('show_gui', context))

        class AvatarObserver(object):
            @staticmethod
            def onEnterWorld(avatar):
                required = (
                    'syncVector3', 'getVector3', 'resetVector3',
                    'setInterpolationType')
                if not all(hasattr(avatar.filter, name)
                           for name in required):
                    raise AssertionError('native AvatarFilter is incomplete')
                operations.append(('avatar_observer_enter_world',
                                   avatar.filter))

        class PlayerAvatar(object):
            def __init__(self):
                operations.append(('original_avatar_init',))

            def onBecomePlayer(self):
                operations.append(('original_avatar_become_player',))
                self.filter = bigworld.AvatarFilter()
                self.arena = types.SimpleNamespace(arenaType=object())

            def onPrereqsLoaded(self, resource_names, resource_refs):
                operations.append(
                    ('avatar_prereqs_loaded', resource_names, resource_refs))

        class Vehicle(object):
            pass

        account_module = types.SimpleNamespace(
            PlayerAccount=PlayerAccount,
            _CLIENT_SERVER_VERSION=('requiredVersion_92200', '0.9.22'))
        avatar_module = types.SimpleNamespace(
            PlayerAvatar=PlayerAvatar, AvatarObserver=AvatarObserver)
        bigworld.account_type = PlayerAccount
        manager = _CompatConnectionManager(bigworld, statuses, operations)
        player_events = types.SimpleNamespace(
            onDisconnected=_CompatEvent(operations, 'player_disconnected'))
        runtime = types.SimpleNamespace(
            account_module=account_module,
            avatar_module=avatar_module,
            bigworld=bigworld,
            connection_manager=manager,
            login_status=statuses,
            offline_map_creator=_OfflineMapCreator(operations),
            player_events=player_events,
            predefined_hosts=_Hosts(existing_hosts, host_failure),
            vehicle_module=types.SimpleNamespace(Vehicle=Vehicle))
        return runtime, operations

    def test_connects_account_in_native_event_order_and_disconnects_once(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        original_init = runtime.account_module.PlayerAccount.__dict__['__init__']
        original_account_getattribute = \
            runtime.account_module.PlayerAccount.__getattribute__
        original_avatar_getattribute = \
            runtime.avatar_module.PlayerAvatar.__getattribute__
        original_vehicle_getattribute = \
            runtime.vehicle_module.Vehicle.__getattribute__
        original_connect = runtime.bigworld.connect
        original_disconnect = runtime.bigworld.disconnect
        original_clear_all_spaces = runtime.bigworld.clearAllSpaces
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        compatibility.connect(show_lobby=True)

        self.assertTrue(compatibility.is_ready())
        account = runtime.bigworld.player()
        self.assertTrue(account.isOffline)
        self.assertIs(account.fakeServer, account.base)
        names = [item[0] for item in operations]
        self.assertLess(names.index('progress'), names.index('account_entity'))
        self.assertLess(names.index('connected'), names.index('account_entity'))
        self.assertLess(names.index('account_entity'),
                        names.index('original_account_init'))
        self.assertLess(names.index('original_account_init'),
                        names.index('player'))
        self.assertLess(names.index('player'),
                        names.index('original_account_become_player'))
        self.assertNotIn('clear_all_spaces', names)
        self.assertIn(account, runtime.bigworld.entities.values())
        self.assertTrue(hasattr(account, '_ClientChat__chatActionCallbacks'))
        self.assertTrue(hasattr(account, '_PlayerAccount__onCmdResponse'))
        self.assertEqual(1, names.count('show_gui'))
        self.assertEqual(original_clear_all_spaces,
                         runtime.bigworld.clearAllSpaces)
        self.assertIs(account, account.fakeServer._player())
        runtime.bigworld._player = object()
        self.assertIsNone(account.fakeServer._player())
        runtime.bigworld._player = account
        self.assertFalse(compatibility._connecting)

        avatar = runtime.avatar_module.PlayerAvatar()
        self.assertFalse(avatar.isObserverFPV)
        self.assertEqual(0, avatar.observerFPVControlMode)
        self.assertEqual(0, avatar.numOfObservers)
        self.assertEqual(0.0, avatar.remoteCamera.time)
        self.assertIsNone(avatar.remoteCamera.shotPoint)
        self.assertEqual(0, avatar.remoteCamera.zoom)
        first_filter = avatar.filter
        original_filter_factory = runtime.bigworld.AvatarFilter
        runtime.avatar_module.AvatarObserver.onEnterWorld(avatar)
        avatar.onBecomePlayer()
        self.assertIs(first_filter, avatar.filter)
        observer_events = [item for item in operations
                           if item[0] == 'avatar_observer_enter_world']
        self.assertEqual(1, len(observer_events))
        self.assertIs(avatar.filter, observer_events[-1][1])
        self.assertEqual(original_filter_factory,
                         runtime.bigworld.AvatarFilter)

        compatibility.disconnect()
        compatibility.disconnect()
        names = [item[0] for item in operations]
        self.assertEqual(1, names.count('manager_disconnect'))
        self.assertEqual(1, names.count('disconnected'))
        self.assertEqual(1, names.count('player_disconnected'))
        self.assertFalse(runtime.offline_map_creator.active)

        compatibility.fini()
        self.assertIs(
            original_init,
            runtime.account_module.PlayerAccount.__dict__['__init__'])
        self.assertIs(
            original_account_getattribute,
            runtime.account_module.PlayerAccount.__getattribute__)
        self.assertIs(
            original_avatar_getattribute,
            runtime.avatar_module.PlayerAvatar.__getattribute__)
        self.assertIs(
            original_vehicle_getattribute,
            runtime.vehicle_module.Vehicle.__getattribute__)
        self.assertEqual(original_connect, runtime.bigworld.connect)
        self.assertEqual(original_disconnect, runtime.bigworld.disconnect)
        self.assertEqual([], runtime.predefined_hosts._hosts)

    def test_manual_offline_host_login_prepares_account_properties(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        property_name, property_value = \
            runtime.account_module._CLIENT_SERVER_VERSION
        # Exact #1513 supplies entity-definition properties before Python
        # __init__, but does not supply Account.name while the offline map is
        # inactive.  This mirrors the second login after accepting the EULA.
        setattr(account_type, property_name, property_value)
        account_type.initialServerSettings = dict(
            compatibility_module._SERVER_SETTINGS)
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        runtime.connection_manager.initiateConnection(
            {}, '', compatibility_module.OFFLINE_SERVER_ADDRESS)

        account = runtime.bigworld.player()
        self.assertTrue(compatibility.is_ready())
        self.assertEqual('offline_account', account.name)
        self.assertTrue(account.isOffline)
        self.assertIs(account.fakeServer, account.base)
        self.assertFalse(compatibility._connecting)
        compatibility.fini()

    def test_retired_account_drops_callbacks_even_if_player_identity_lingers(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        account = runtime.bigworld.player()
        server = account.fakeServer

        server.doCmdInt3(91, 999999, 0, 0, 0)
        self.assertEqual(1, len(runtime.bigworld._callbacks))
        account.__dict__.clear()
        self.assertIs(account, runtime.bigworld.player())
        unused_delay, callback = runtime.bigworld._callbacks.pop(0)

        callback()

        self.assertIsNone(server._player())
        compatibility.disconnect()
        compatibility.fini()

    def test_relogin_cache_can_read_retired_offline_account_name(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        native_init = account_type.__init__
        cache_keys = []
        repository_creations = [0]

        class PersistentCache(object):
            def __init__(self):
                self.account = None

            def setAccount(self, account):
                self.account = (weakref.proxy(account)
                                if account is not None else None)

            def save(self):
                if self.account is not None:
                    cache_keys.append('%s_%s_data' % (
                        self.account.name,
                        self.account.__class__.__name__))

        class SyncData(object):
            def __init__(self):
                self.account = None
                self._AccountSyncData__persistentCache = PersistentCache()

            def setAccount(self, account):
                # Exact #1513 saves through the old cache proxy before it
                # normally rebinds that proxy to the replacement Account.
                self.account = account
                self._AccountSyncData__persistentCache.save()
                if account is not None:
                    self._AccountSyncData__persistentCache.setAccount(account)

        class Repository(object):
            def __init__(self):
                self.className = account_type.__name__
                self.syncData = SyncData()

        def repository_init(account):
            native_init(account)
            repository = runtime.account_module.g_accountRepository
            if repository is None:
                repository_creations[0] += 1
                repository = Repository()
                runtime.account_module.g_accountRepository = repository
            account.syncData = repository.syncData
            account.syncData.setAccount(account)

        runtime.account_module.g_accountRepository = None
        account_type.__init__ = repository_init
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        first_account = runtime.bigworld.player()
        repository = runtime.account_module.g_accountRepository
        persistent_cache = (
            repository.syncData._AccountSyncData__persistentCache)

        # PyEntity::onEntityDestroyed clears the complete instance dictionary,
        # while #1513's shared persistent cache still holds its weak proxy.
        first_account.__dict__.clear()
        with self.assertRaises(AttributeError):
            unused = persistent_cache.account.name
        runtime.bigworld.entities.clear()
        runtime.bigworld._player = None

        restored = compatibility.restore_lobby_account()

        self.assertEqual(1, repository_creations[0])
        self.assertEqual(
            ['offline_account_PlayerAccount_data'], cache_keys)
        self.assertFalse(compatibility._connecting)
        restored.name = 'native_name'
        self.assertEqual('native_name', restored.name)

        # A dead weak proxy fails before any attribute getter can run.  The
        # prebind must replace it without dereferencing the retired object.
        class RetiredAccount(object):
            pass

        retired = RetiredAccount()
        retired.name = 'retired'
        persistent_cache.setAccount(retired)
        dead_proxy = persistent_cache.account
        del retired
        gc.collect()
        with self.assertRaises(ReferenceError):
            unused = dead_proxy.name
        runtime.bigworld.entities.clear()
        runtime.bigworld._player = None

        compatibility.restore_lobby_account()

        self.assertEqual(
            ['offline_account_PlayerAccount_data'] * 2, cache_keys)
        compatibility.fini()

    def test_relogin_does_not_prebind_a_different_account_repository(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        persistent_cache = mock.Mock()
        runtime.account_module.g_accountRepository = types.SimpleNamespace(
            className='DifferentPlayerAccount',
            syncData=types.SimpleNamespace(
                _AccountSyncData__persistentCache=persistent_cache))
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        compatibility.connect(show_lobby=True)

        persistent_cache.setAccount.assert_not_called()
        compatibility.fini()

    def test_avatar_properties_and_mailboxes_exist_during_native_init(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        observed = {}

        def strict_avatar_init(avatar):
            observed['name'] = avatar.name
            observed['vehicle_id'] = avatar.playerVehicleID
            observed['mailboxes'] = (
                avatar.base, avatar.cell, avatar.server, avatar.bwProto)

        runtime.avatar_module.PlayerAvatar.__init__ = strict_avatar_init
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        compatibility.configure_battle()

        avatar = runtime.avatar_module.PlayerAvatar()

        self.assertEqual('OfflinePlayer', observed['name'])
        self.assertEqual(0, observed['vehicle_id'])
        self.assertEqual(
            (avatar.fakeServer,) * 4, observed['mailboxes'])
        compatibility.fini()

    def test_avatar_normal_return_without_arena_is_not_marked_ready(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        runtime.avatar_module.PlayerAvatar.onBecomePlayer = (
            lambda avatar: None)
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()

        with self.assertRaisesRegex(RuntimeError,
                                    'no initialized arena type'):
            avatar.onBecomePlayer()

        self.assertFalse(getattr(
            avatar, '_offlineLANPlayerReady', False))
        compatibility.fini()

    def test_retired_avatar_drops_uncancellable_resource_callback(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        runtime.bigworld._player = avatar
        avatar.onBecomePlayer()
        delayed = avatar.onPrereqsLoaded

        delayed(('tank',), {'tank': object()})
        self.assertEqual(
            1, len([item for item in operations
                   if item[0] == 'avatar_prereqs_loaded']))
        avatar.__dict__.clear()
        self.assertIs(avatar, runtime.bigworld.player())

        delayed(('tank',), {'tank': object()})

        self.assertEqual(
            1, len([item for item in operations
                   if item[0] == 'avatar_prereqs_loaded']))
        compatibility.fini()

    def test_vehicle_cell_uses_only_vehicle_or_avatar_mailbox(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        account = runtime.bigworld.player()
        compatibility.configure_battle()
        vehicle = runtime.vehicle_module.Vehicle()

        explicit_cell = object()
        vehicle.fakeCell = explicit_cell
        self.assertIs(explicit_cell, vehicle.cell)
        del vehicle.fakeCell

        runtime.bigworld._player = account
        with self.assertRaises(AttributeError):
            unused = vehicle.cell

        avatar = runtime.avatar_module.PlayerAvatar()
        runtime.bigworld._player = avatar
        self.assertIs(avatar.fakeServer, vehicle.cell)

        compatibility.deactivate_map()
        with self.assertRaises(AttributeError):
            unused = vehicle.cell
        compatibility.fini()

    def test_delegates_real_server_and_preserves_existing_offline_host(self):
        compatibility_module = _load_port_source('compat')
        existing = types.SimpleNamespace(
            url=compatibility_module.OFFLINE_SERVER_ADDRESS)
        runtime, operations = self._runtime(existing_hosts=[existing])
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        result = runtime.bigworld.connect('real.example:2000', {}, mock.Mock())
        self.assertEqual('online-connect', result)
        compatibility.fini()

        self.assertEqual([existing], runtime.predefined_hosts._hosts)
        self.assertIn(('original_connect', 'real.example:2000'), operations)

    def test_restores_account_after_offline_map_clears_all_entities(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        first_account = runtime.bigworld.player()

        runtime.bigworld.entities.clear()
        runtime.bigworld._player = None
        restored = compatibility.restore_lobby_account()

        self.assertIsNot(first_account, restored)
        self.assertIs(restored, runtime.bigworld.player())
        self.assertTrue(restored.isOffline)
        self.assertIs(restored.fakeServer, restored.base)
        self.assertTrue(compatibility.is_ready())
        names = [item[0] for item in operations]
        self.assertEqual(2, names.count('account_space'))
        self.assertEqual(2, names.count('account_entity'))
        self.assertEqual(2, names.count('original_account_init'))
        self.assertFalse(compatibility._connecting)

    def test_account_promotion_restores_clear_all_spaces_after_failure(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        original_clear_all_spaces = runtime.bigworld.clearAllSpaces

        def failing_become_player(account):
            runtime.bigworld.clearAllSpaces()
            raise RuntimeError('native account promotion failed')

        runtime.account_module.PlayerAccount.onBecomePlayer = \
            failing_become_player
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(
                RuntimeError, 'native account promotion failed'):
            compatibility.connect(show_lobby=True)

        self.assertEqual(original_clear_all_spaces,
                         runtime.bigworld.clearAllSpaces)
        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual(
            2, runtime.bigworld.operations.count(('clear_all_spaces',)))
        self.assertIn(('wgc', False), runtime.bigworld.operations)
        self.assertEqual(
            1, runtime.bigworld.operations.count(('disconnected',)))
        self.assertEqual(
            1, runtime.bigworld.operations.count(('player_disconnected',)))
        self.assertFalse(compatibility._connecting)
        runtime.bigworld.clearAllSpaces()
        self.assertEqual(
            3, runtime.bigworld.operations.count(('clear_all_spaces',)))

    def test_logged_on_listener_failure_rolls_back_entire_connection(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        deleted = []
        runtime.account_module.g_accountRepository = object()

        def delete_repository():
            deleted.append(True)
            runtime.account_module.g_accountRepository = None

        def fail_logged_on(unused_context):
            operations.append(('logged_on_failed',))
            raise RuntimeError('logged-on listener failed')

        runtime.account_module._delAccountRepository = delete_repository
        runtime.connection_manager.onLoggedOn = fail_logged_on
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(RuntimeError,
                                    'logged-on listener failed'):
            compatibility.connect(show_lobby=True)

        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual(runtime.login_status.NOT_SET,
                         runtime.connection_manager.
                         _ConnectionManager__connectionStatus)
        self.assertEqual([True], deleted)
        self.assertNotIn('account_entity', [item[0] for item in operations])
        self.assertIn(('wgc', False), operations)
        self.assertIn(('disconnected',), operations)
        self.assertIn(('player_disconnected',), operations)
        self.assertFalse(compatibility._fake_connected)
        self.assertFalse(compatibility._connecting)
        compatibility.fini()

    def test_swallowed_account_init_failure_is_never_promoted(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        deleted = []

        def fail_native_init(account):
            operations.append(('partial_account_init',))
            account.partial = True
            raise RuntimeError('native Account init failed')

        account_type.__init__ = fail_native_init

        def swallow_create_error(entity_type, unused_space_id,
                                 unused_client_only, unused_position,
                                 unused_orientation, unused_properties):
            self.assertEqual('Account', entity_type)
            entity_id = runtime.bigworld._next_entity
            runtime.bigworld._next_entity += 1
            account = account_type.__new__(account_type)
            try:
                account_type.__init__(account)
            except RuntimeError:
                pass
            runtime.bigworld.entities[entity_id] = account
            return entity_id

        def delete_repository():
            deleted.append(True)
            runtime.account_module.g_accountRepository = None

        runtime.bigworld.createEntity = swallow_create_error
        runtime.account_module.g_accountRepository = object()
        runtime.account_module._delAccountRepository = delete_repository
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(
                RuntimeError, 'partial offline Account'):
            compatibility.connect(show_lobby=True)

        names = [item[0] for item in operations]
        self.assertNotIn('player', names)
        self.assertNotIn('show_gui', names)
        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual([True, True], deleted)
        self.assertFalse(compatibility._fake_connected)
        compatibility.fini()

    def test_failed_lobby_restore_retires_partial_connection_and_reconnects(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        native_init = account_type.__init__
        constructions = [0]
        deleted = []

        def fail_second_construction(account):
            constructions[0] += 1
            native_init(account)
            if constructions[0] == 2:
                raise RuntimeError('replacement Account init failed')

        def delete_repository():
            deleted.append(True)
            runtime.account_module.g_accountRepository = None

        account_type.__init__ = fail_second_construction
        runtime.account_module._delAccountRepository = delete_repository
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        runtime.account_module.g_accountRepository = object()
        runtime.bigworld.entities.clear()
        runtime.bigworld._player = None

        with self.assertRaisesRegex(
                RuntimeError, 'replacement Account init failed'):
            compatibility.restore_lobby_account()

        self.assertEqual({}, runtime.bigworld.entities)
        self.assertIsNone(runtime.bigworld.player())
        self.assertIsNone(runtime.account_module.g_accountRepository)
        self.assertFalse(compatibility._fake_connected)
        self.assertGreaterEqual(len(deleted), 1)

        compatibility.connect(show_lobby=True)
        self.assertTrue(compatibility.is_ready())
        self.assertEqual(1, len(runtime.bigworld.entities))
        compatibility.fini()

    def test_disconnect_listener_failure_still_cleans_every_boundary(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        deleted = []

        def fail_disconnected():
            operations.append(('disconnected_failed',))
            raise RuntimeError('disconnect listener failed')

        def delete_repository():
            deleted.append(True)

        runtime.connection_manager.onDisconnected = fail_disconnected
        runtime.account_module._delAccountRepository = delete_repository
        runtime.offline_map_creator.active = True

        with self.assertRaisesRegex(RuntimeError,
                                    'disconnect listener failed'):
            compatibility.disconnect()

        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual([True], deleted)
        self.assertIn(('player_disconnected',), operations)
        self.assertFalse(runtime.offline_map_creator.active)
        self.assertFalse(compatibility._fake_connected)
        compatibility.fini()

    def test_fini_restores_all_patches_even_when_disconnect_listener_fails(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        avatar_type = runtime.avatar_module.PlayerAvatar
        vehicle_type = runtime.vehicle_module.Vehicle
        originals = (
            account_type.__dict__['__init__'],
            account_type.__getattribute__,
            avatar_type.__dict__['__init__'],
            avatar_type.__getattribute__,
            vehicle_type.__getattribute__,
            runtime.bigworld.connect,
            runtime.bigworld.disconnect,
        )
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        compatibility.configure_battle()

        def fail_disconnected():
            raise RuntimeError('disconnect listener failed')

        runtime.connection_manager.onDisconnected = fail_disconnected
        with self.assertRaisesRegex(RuntimeError,
                                    'disconnect listener failed'):
            compatibility.fini()

        self.assertFalse(compatibility._installed)
        self.assertFalse(compatibility._battle_active)
        self.assertFalse(compatibility._native_battle)
        self.assertEqual([], runtime.predefined_hosts._hosts)
        self.assertIs(originals[0], account_type.__dict__['__init__'])
        self.assertIs(originals[1], account_type.__getattribute__)
        self.assertIs(originals[2], avatar_type.__dict__['__init__'])
        self.assertIs(originals[3], avatar_type.__getattribute__)
        self.assertIs(originals[4], vehicle_type.__getattribute__)
        self.assertIs(originals[5].__func__,
                      runtime.bigworld.connect.__func__)
        self.assertIs(originals[5].__self__,
                      runtime.bigworld.connect.__self__)
        self.assertIs(originals[6].__func__,
                      runtime.bigworld.disconnect.__func__)
        self.assertIs(originals[6].__self__,
                      runtime.bigworld.disconnect.__self__)

    def test_lobby_restore_does_not_replace_an_existing_player(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        first_account = runtime.bigworld.player()

        restored = compatibility.restore_lobby_account()

        self.assertIs(first_account, restored)
        names = [item[0] for item in operations]
        self.assertEqual(1, names.count('account_entity'))

    def test_failed_install_rolls_back_without_leaving_patches(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime(host_failure=True)
        original_init = runtime.account_module.PlayerAccount.__dict__['__init__']
        original_avatar_init = (
            runtime.avatar_module.PlayerAvatar.__dict__['__init__'])
        original_avatar_become_player = (
            runtime.avatar_module.PlayerAvatar.__dict__['onBecomePlayer'])
        original_connect = runtime.bigworld.connect
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(RuntimeError, 'host creation failed'):
            compatibility.install()

        self.assertIs(
            original_init,
            runtime.account_module.PlayerAccount.__dict__['__init__'])
        self.assertIs(
            original_avatar_init,
            runtime.avatar_module.PlayerAvatar.__dict__['__init__'])
        self.assertIs(
            original_avatar_become_player,
            runtime.avatar_module.PlayerAvatar.__dict__['onBecomePlayer'])
        self.assertEqual(original_connect, runtime.bigworld.connect)
        self.assertFalse(compatibility._installed)

    def test_fini_does_not_overwrite_later_third_party_wrappers(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        def later_account_init(account):
            account.later = True

        def later_connect(server, params, progress):
            return 'later'

        runtime.account_module.PlayerAccount.__init__ = later_account_init
        runtime.bigworld.connect = later_connect
        compatibility.fini()

        self.assertIs(
            later_account_init,
            runtime.account_module.PlayerAccount.__dict__['__init__'])
        self.assertIs(later_connect, runtime.bigworld.connect)


class _LANSocket(object):
    def __init__(self):
        self.payloads = []
        self.closed = False

    def sendall(self, payload):
        self.payloads.append(payload)

    def close(self):
        self.closed = True


class _LANBigWorld(object):
    def __init__(self):
        self.callbacks = []
        self.cancelled = []

    def callback(self, delay, function):
        self.callbacks.append((delay, function))
        return len(self.callbacks)

    def cancelCallback(self, callback_id):
        self.cancelled.append(callback_id)


class LANClientTests(unittest.TestCase):
    def _client(self):
        module = _load_port_source('lan_client')
        events = []
        bigworld = _LANBigWorld()
        client = module.LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:MS-1', 100,
            on_event=lambda kind, message: events.append((kind, message)),
            bigworld=bigworld)
        return module, client, events, bigworld

    def test_welcome_roster_and_server_validated_start_request(self):
        module, client, events, _ = self._client()
        client.connected = True
        client.running = True
        client.sock = _LANSocket()
        client._handle_message({
            'type': 'welcome',
            'protocol': module.PROTOCOL_VERSION,
            'player_id': 7,
            'name': 'Player',
            'vehicle': 'ussr:MS-1',
            'max_health': 100,
            'team': 1,
            'slot': 0,
            'map': '01_karelia',
            'map_pool': ['01_karelia', '04_himmelsdorf'],
            'phase': 'waiting',
            'round_id': 3,
            'spawn': {'x': 0, 'y': 0, 'z': 0, 'yaw': 0},
        })
        client._handle_message({
            'type': 'roster',
            'protocol': module.PROTOCOL_VERSION,
            'phase': 'waiting',
            'round_id': 3,
            'map': '01_karelia',
            'map_pool': ['01_karelia', '04_himmelsdorf'],
            'players': [{'id': 7}, {'id': 8}],
        })

        self.assertTrue(client.ready)
        self.assertEqual(7, client.player_id)
        self.assertEqual(2, len(client.roster))
        self.assertFalse(client.request_start('99_missing'))
        self.assertTrue(client.request_start('04_himmelsdorf'))
        sent = client.sock.payloads[-1].decode('utf-8')
        self.assertIn('"type":"start_battle"', sent)
        self.assertIn('"map":"04_himmelsdorf"', sent)
        self.assertEqual(['welcome', 'roster'],
                         [item[0] for item in events])

    def test_poll_coalesces_snapshots_without_crossing_state_barriers(self):
        _, client, events, bigworld = self._client()
        client.running = True
        client.round_id = 4
        client._queue_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 0,
            'players': [], 'bots': []})
        client._queue_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 1,
            'players': [], 'bots': []})
        client._queue_message({
            'type': 'roster', 'protocol': 5,
            'round_id': 4, 'phase': 'battle',
            'map': '01_karelia', 'players': []})
        client._queue_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 2,
            'players': [], 'bots': []})
        client._queue_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 3,
            'players': [], 'bots': []})
        client._poll()

        self.assertEqual(3, client.last_snapshot['server_tick'])
        self.assertEqual(['snapshot', 'roster', 'snapshot'],
                         [item[0] for item in events])
        self.assertIsNotNone(client._poll_callback)
        callback_id = client._poll_callback
        client.stop()
        self.assertEqual([callback_id], bigworld.cancelled)
        self.assertIsNone(client._poll_callback)

    def test_protocol_mismatch_stops_without_raising(self):
        _, client, _, _ = self._client()
        client.running = True
        client._handle_message({'type': 'welcome', 'protocol': 'invalid'})
        self.assertFalse(client.running)
        self.assertEqual('protocol mismatch', client.last_error)

    def test_round_barriers_drop_stale_snapshot_and_clear_terminal_cache(self):
        _, client, events, _ = self._client()
        client.round_id = 5
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 99,
            'players': [], 'bots': []})
        client._handle_message({
            'type': 'events', 'protocol': 5,
            'round_id': 4, 'server_tick': 99, 'events': [
                {'kind': 'battle_result'}]})
        current = {'type': 'snapshot', 'protocol': 5,
                   'round_id': 5, 'server_tick': 3,
                   'players': [], 'bots': []}
        client._handle_message(current)

        self.assertIs(current, client.last_snapshot)
        self.assertEqual(['snapshot'], [value[0] for value in events])

        client._fire_seq = 9
        client._handle_message({
            'type': 'roster', 'protocol': 5,
            'round_id': 6, 'phase': 'waiting',
            'map': '01_karelia', 'players': []})
        self.assertEqual(6, client.round_id)
        self.assertIsNone(client.last_snapshot)
        self.assertEqual(0, client._fire_seq)

    def test_pending_overflow_preserves_state_transition_barrier(self):
        module, client, _, _ = self._client()
        for tick in range(module.MAX_PENDING_MESSAGES):
            client._queue_message({
                'type': 'snapshot', 'round_id': 1, 'server_tick': tick})

        client._queue_message({
            'type': 'roster', 'round_id': 2, 'phase': 'waiting',
            'players': []})

        self.assertEqual(module.MAX_PENDING_MESSAGES, len(client._pending))
        self.assertEqual('roster', client._pending[-1]['type'])

    def test_malformed_required_server_messages_fail_closed(self):
        _, client, _, _ = self._client()
        client.running = True

        client._handle_message({
            'type': 'welcome', 'protocol': 5, 'player_id': 'bad',
            'team': 1, 'round_id': 1})

        self.assertFalse(client.running)
        self.assertFalse(client.ready)
        self.assertEqual('invalid welcome message', client.last_error)

    def test_malformed_current_round_snapshot_fails_closed(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3

        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 4,
            'players': 'not-a-list', 'bots': []})

        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

    def test_state_message_without_protocol_fails_closed(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3

        client._handle_message({
            'type': 'snapshot', 'round_id': 3, 'server_tick': 4,
            'players': [], 'bots': []})

        self.assertFalse(client.running)
        self.assertEqual('protocol mismatch', client.last_error)

    def test_malformed_server_order_batch_fails_closed(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3

        client._handle_message({
            'type': 'snapshot', 'protocol': 5, 'round_id': 3,
            'server_tick': 4, 'players': [], 'bots': [],
            'bot_order_revision': 2, 'bot_orders': {'id': 11}})

        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

    def test_stale_start_denied_does_not_cross_round_barrier(self):
        _, client, events, _ = self._client()
        client.running = True
        client.round_id = 4

        client._handle_message({
            'type': 'start_denied', 'protocol': 5,
            'round_id': 3, 'code': 'already_started'})

        self.assertEqual([], events)


class BootstrapContractTests(unittest.TestCase):
    def test_entry_delegates_init_and_fini(self):
        entry = (PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
                 'mods' / 'mod_offline_lan_0922.py')
        bootstrap = types.SimpleNamespace(init=mock.Mock(), fini=mock.Mock())
        package = types.ModuleType('gui.mods.offline_lan_0922')
        package.bootstrap = bootstrap
        modules = {
            'gui': types.ModuleType('gui'),
            'gui.mods': types.ModuleType('gui.mods'),
            'gui.mods.offline_lan_0922': package,
        }
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('entry0922', entry)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.init()
            module.fini()
        bootstrap.init.assert_called_once_with()
        bootstrap.fini.assert_called_once_with()

    def test_bootstrap_schedules_once_starts_lan_session_and_stops_cleanly(self):
        bootstrap_path = (
            PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
            'mods' / 'offline_lan_0922' / 'bootstrap.py')
        bigworld = _BigWorld()
        package = types.ModuleType('gui.mods.offline_lan_0922')
        package.PORT_VERSION = '0.3.2'
        package.TARGET_CLIENT_VERSION = '0.9.22.0.1'
        package.TARGET_CLIENT_BUILD = '1513'
        package.__path__ = []
        config = types.ModuleType('gui.mods.offline_lan_0922.config')
        config.load = mock.Mock(return_value={
            'enabled': True,
            'vehicle': 'ussr:R11_MS-1',
            'serverHost': '127.0.0.1',
            'serverPort': 28782,
            'playerName': 'OfflinePlayer',
            'maxHealth': 880,
            'startupTimeoutSeconds': 30.0,
        })
        session = types.SimpleNamespace(
            start=mock.Mock(return_value=True), stop=mock.Mock())
        lan_session = types.ModuleType(
            'gui.mods.offline_lan_0922.lan_session')
        lan_session.LANSession = mock.Mock(return_value=session)
        compatibility = types.SimpleNamespace(
            connect=mock.Mock(), is_ready=mock.Mock(return_value=True),
            fini=mock.Mock())
        compatibility_module = types.ModuleType(
            'gui.mods.offline_lan_0922.compat')
        compatibility_module.g_compatibility = compatibility
        account_state = object()
        state_module = types.ModuleType(
            'gui.mods.offline_lan_0922.account_rpc.state')
        state_module.AccountState = mock.Mock(return_value=account_state)
        class EventBus(object):
            def __init__(self):
                self.listeners = {}

            def addListener(self, event_type, handler):
                self.listeners.setdefault(event_type, []).append(handler)

            def removeListener(self, event_type, handler):
                self.listeners[event_type].remove(handler)

            def fire(self, event_type):
                for handler in list(self.listeners.get(event_type, ())):
                    handler(object())

        event_bus = EventBus()
        lobby_loaded = 'lobby_view_loaded'
        gui_shared = types.ModuleType('gui.shared')
        gui_shared.events = types.SimpleNamespace(
            GUICommonEvent=types.SimpleNamespace(
                LOBBY_VIEW_LOADED=lobby_loaded))
        gui_shared.g_eventBus = event_bus

        app_loader = types.ModuleType('gui.app_loader')
        lobby = types.SimpleNamespace(initialized=False)
        loader = types.SimpleNamespace(
            getDefLobbyApp=mock.Mock(return_value=lobby),
            getSpaceID=mock.Mock(return_value=3))
        app_loader.g_appLoader = loader
        app_loader_settings = types.ModuleType('gui.app_loader.settings')
        app_loader_settings.GUI_GLOBAL_SPACE_ID = types.SimpleNamespace(
            LOGIN=3, LOBBY=4)

        hangar_vehicle = types.SimpleNamespace(model=None)
        hangar_space = types.SimpleNamespace(
            inited=True, spaceInited=False,
            getVehicleEntity=mock.Mock(return_value=hangar_vehicle))
        hangar_module = types.ModuleType('gui.shared.utils.HangarSpace')
        hangar_module.g_hangarSpace = hangar_space
        current_vehicle = types.SimpleNamespace(
            isPresent=mock.Mock(return_value=True))
        current_vehicle_module = types.ModuleType('CurrentVehicle')
        current_vehicle_module.g_currentVehicle = current_vehicle
        modules = {
            'BigWorld': bigworld,
            'gui': types.ModuleType('gui'),
            'gui.shared': gui_shared,
            'gui.shared.utils': types.ModuleType('gui.shared.utils'),
            'gui.shared.utils.HangarSpace': hangar_module,
            'gui.mods': types.ModuleType('gui.mods'),
            'gui.mods.offline_lan_0922': package,
            'gui.mods.offline_lan_0922.compat': compatibility_module,
            'gui.mods.offline_lan_0922.config': config,
            'gui.mods.offline_lan_0922.account_rpc.state': state_module,
            'gui.mods.offline_lan_0922.lan_session': lan_session,
            'gui.app_loader': app_loader,
            'gui.app_loader.settings': app_loader_settings,
            'CurrentVehicle': current_vehicle_module,
        }
        package.config = config
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                'bootstrap0922', bootstrap_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module._selected_vehicle = lambda value: {
                'id': 1, 'compDescr': 12345}
            module.init()
            module.init()
            self.assertEqual(
                [module._on_lobby_view_loaded],
                event_bus.listeners[lobby_loaded])
            self.assertEqual(1, len(bigworld._callbacks))
            bigworld.run_next()
            self.assertEqual(1, len(bigworld._callbacks))
            module._deadline = 1.0
            with mock.patch.object(module.time, 'time', return_value=1000.0):
                # Every native readiness condition except the public lobby
                # event is true.  The session must still not start, and the
                # stale deadline must not turn first-run EULA time into a
                # startup failure.
                lobby.initialized = True
                loader.getSpaceID.return_value = 4
                hangar_space.spaceInited = True
                hangar_vehicle.model = object()
                bigworld.run_next()
                lan_session.LANSession.assert_not_called()
                self.assertEqual(1, len(bigworld._callbacks))
                module._deadline = 0.0
                loader.getSpaceID.return_value = 3
                hangar_space.spaceInited = False
                hangar_vehicle.model = None
                event_bus.fire(lobby_loaded)
                self.assertEqual(0.0, module._deadline)
                # First stable LOGIN observation deliberately waits one more
                # engine tick so LoginState's entity clear cannot race the
                # client-only Account construction.
                bigworld.run_next()
                self.assertEqual(0.0, module._deadline)
                compatibility.connect.assert_not_called()
                bigworld.run_next()
                self.assertEqual(0.0, module._deadline)
                bigworld.run_next()
                self.assertEqual(1030.0, module._deadline)
                lan_session.LANSession.assert_not_called()
                self.assertEqual(1, len(bigworld._callbacks))
                loader.getSpaceID.return_value = 4
                bigworld.run_next()
                lan_session.LANSession.assert_not_called()
                self.assertEqual(1, len(bigworld._callbacks))
                hangar_space.spaceInited = True
                bigworld.run_next()
                lan_session.LANSession.assert_not_called()
                self.assertEqual(1, len(bigworld._callbacks))
                hangar_vehicle.model = object()
                bigworld.run_next()
            module.fini()
            self.assertFalse(module._started)

            # A lobby-stage timeout must fully undo the connection adapter
            # and listener, then allow a clean init.  Keep the hangar not
            # ready so no second LANSession can be constructed.
            hangar_space.spaceInited = False
            loader.getSpaceID.return_value = 3
            module.init()
            self.assertEqual(1, len(bigworld._callbacks))
            self.assertEqual(
                [module._on_lobby_view_loaded],
                event_bus.listeners[lobby_loaded])
            bigworld.run_next()
            bigworld.run_next()
            event_bus.fire(lobby_loaded)
            loader.getSpaceID.return_value = 4
            clock = [1000.0]
            with mock.patch.object(
                    module.time, 'time', side_effect=lambda: clock[0]):
                bigworld.run_next()
                self.assertEqual(1030.0, module._deadline)
                self.assertEqual(1, len(bigworld._callbacks))
                clock[0] = 1031.0
                bigworld.run_next()
            self.assertFalse(module._started)
            self.assertIsNone(module._callback_id)
            self.assertEqual([], bigworld._callbacks)
            self.assertEqual([], event_bus.listeners[lobby_loaded])

            # Fini while the initial callback is still pending cancels it and
            # removes the one reinstalled listener.
            module.init()
            self.assertEqual(1, len(bigworld._callbacks))
            self.assertEqual(1, len(event_bus.listeners[lobby_loaded]))
            module.fini()
            self.assertFalse(module._started)
            self.assertEqual([], bigworld._callbacks)
        lan_session.LANSession.assert_called_once_with(
            config.load.return_value,
            lobby_ready=module._native_lobby_is_ready,
            callback=bigworld.callback,
            cancel_callback=bigworld.cancelCallback)
        session.start.assert_called_once_with()
        session.stop.assert_called_once_with(
            show_login=False, restore_account=False)
        expected_connect = mock.call(
            show_lobby=True,
            account_context={'selected_vehicle': {
                'id': 1, 'compDescr': 12345},
                'account_state': account_state})
        self.assertEqual([expected_connect, expected_connect],
                         compatibility.connect.call_args_list)
        self.assertEqual(2, state_module.AccountState.call_count)
        self.assertEqual(3, compatibility.fini.call_count)
        self.assertEqual([], event_bus.listeners[lobby_loaded])


if __name__ == '__main__':
    unittest.main()
