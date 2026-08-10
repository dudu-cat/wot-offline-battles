from __future__ import print_function

import compileall
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile


_SCHEMA_ROOT = os.path.join(
    os.path.dirname(__file__), 'src', 'res', 'scripts', 'client', 'gui',
    'mods', 'offline_lan_0922')
if _SCHEMA_ROOT not in sys.path:
    sys.path.insert(0, _SCHEMA_ROOT)
import navigation_graph_schema as _navigation_schema


MOD_ID = 'org.peng.offline_lan_0922'
MOD_VERSION = '0.3.49'
PYTHON_MAGIC = '\x03\xf3\r\n'
FOLIAGE_FORMAT = 'offline-lan-0922-foliage'
FOLIAGE_VERSION = 1
FOLIAGE_MANIFEST_FORMAT = FOLIAGE_FORMAT + '-manifest'
PROJECT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..'))
LEGAL_FILES = (
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    os.path.join('licenses', 'Boost-1.0.txt'),
)


def _copy_legal_files(destination_root):
    for relative_path in LEGAL_FILES:
        source = os.path.join(PROJECT_ROOT, relative_path)
        if not os.path.isfile(source):
            raise SystemExit('required legal file is missing: %s' % relative_path)
        destination = os.path.join(destination_root, relative_path)
        parent = os.path.dirname(destination)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        shutil.copy2(source, destination)


def _remove_stale_bytecode(root):
    for current_root, dirs, files in os.walk(root):
        for dirname in list(dirs):
            if dirname == '__pycache__':
                shutil.rmtree(os.path.join(current_root, dirname))
                dirs.remove(dirname)
        for filename in files:
            if filename.endswith(('.pyc', '.pyo')):
                os.unlink(os.path.join(current_root, filename))


def _remove_sources(root):
    for current_root, _, files in os.walk(root):
        for filename in files:
            if filename.endswith('.py'):
                os.unlink(os.path.join(current_root, filename))


def _archive_tree(source_root, destination):
    archive = zipfile.ZipFile(destination, 'w', zipfile.ZIP_STORED)
    try:
        for current_root, dirs, files in os.walk(source_root):
            dirs.sort()
            files.sort()
            for dirname in dirs:
                absolute_path = os.path.join(current_root, dirname)
                relative_path = os.path.relpath(absolute_path, source_root)
                info = zipfile.ZipInfo(
                    relative_path.replace(os.sep, '/').rstrip('/') + '/')
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 0
                info.external_attr = 16
                archive.writestr(info, '')
            for filename in files:
                absolute_path = os.path.join(current_root, filename)
                relative_path = os.path.relpath(absolute_path, source_root)
                info = zipfile.ZipInfo(
                    relative_path.replace(os.sep, '/'))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 0
                # DOS archive bit.  Keeping this non-zero also prevents
                # zipfile.writestr from injecting host-specific permissions.
                info.external_attr = 32
                with open(absolute_path, 'rb') as stream:
                    archive.writestr(info, stream.read())
    finally:
        archive.close()


def _release_config():
    host = os.environ.get('OFFLINE_LAN_RELEASE_HOST', '127.0.0.1').strip()
    if (not host or any(character.isspace() for character in host) or
            '/' in host or ':' in host):
        raise SystemExit('OFFLINE_LAN_RELEASE_HOST is invalid')
    try:
        port = int(os.environ.get('OFFLINE_LAN_RELEASE_PORT', '28782'))
    except ValueError:
        raise SystemExit('OFFLINE_LAN_RELEASE_PORT is invalid')
    if port < 1 or port > 65535:
        raise SystemExit('OFFLINE_LAN_RELEASE_PORT must be 1-65535')
    return {
        'schema': 1,
        'enabled': True,
        'host': host,
        'port': port,
        'name': 'Player',
        'vehicle': 'ussr:R11_MS-1',
        'max_health': 90,
        'startupTimeoutSeconds': 30.0,
        'prebattleCountdownSeconds': 15.0,
        'battleDurationSeconds': 900.0,
        'physics_tuning': {},
        'he_tuning': {},
        'perfect_accuracy': False,
    }


def _write_client_overlay(dist_root, package_path, checksum_path, digest,
                          graph_source=None, foliage_source=None):
    release_config = _release_config()
    release_seed = '%s\n%s:%s' % (
        digest, release_config['host'], release_config['port'])
    release_digest = hashlib.sha256(release_seed.encode('utf-8')).hexdigest()
    release_name = 'WoT-0.9.22-LAN-Client-%s' % release_digest[:7]
    overlay_root = os.path.join(dist_root, release_name)
    mod_root = os.path.join(
        overlay_root, 'mods', '0.9.22.0.1')
    os.makedirs(mod_root)
    shutil.copy2(package_path, mod_root)
    shutil.copy2(checksum_path, mod_root)
    config_root = os.path.join(
        overlay_root, 'mods', 'configs', 'offline_lan_0922')
    os.makedirs(config_root)
    with open(os.path.join(config_root, 'config.json'), 'wb') as stream:
        payload = json.dumps(
            release_config, indent=2, sort_keys=True) + '\n'
        stream.write(payload.encode('utf-8'))
    graph_source = graph_source or os.path.join(
        os.path.dirname(__file__), 'navgraphs')
    _validate_navigation_graphs(graph_source)
    shutil.copytree(graph_source, os.path.join(config_root, 'navgraphs'))
    foliage_source = foliage_source or os.path.join(
        os.path.dirname(__file__), 'foliage')
    _validate_foliage(foliage_source)
    shutil.copytree(foliage_source, os.path.join(config_root, 'foliage'))
    shutil.copy2(os.path.join(os.path.dirname(__file__), 'INSTALL.txt'),
                 overlay_root)
    _copy_legal_files(overlay_root)
    zip_path = os.path.join(
        dist_root,
        release_name + '.zip')
    _archive_tree(overlay_root, zip_path)
    print('client endpoint=%s:%s' % (
        release_config['host'], release_config['port']))
    return overlay_root, zip_path


def _validate_navigation_graphs(graph_root):
    manifest_path = os.path.join(graph_root, 'manifest.json')
    if not os.path.isfile(manifest_path):
        raise SystemExit('complete #1513 navigation graph batch is missing')
    with open(manifest_path, 'rb') as stream:
        manifest = json.load(stream)
    records = manifest.get('maps') if isinstance(manifest, dict) else None
    try:
        version = int(manifest.get('version', -1))
    except (AttributeError, TypeError, ValueError):
        version = -1
    expected_maps = set(_navigation_schema.SUPPORTED_MAPS)
    if (not isinstance(manifest, dict) or
            manifest.get('format') != _navigation_schema.MANIFEST_FORMAT or
            version != _navigation_schema.FORMAT_VERSION or
            manifest.get('game_version') != _navigation_schema.GAME_VERSION or
            not isinstance(records, list) or
            len(records) != len(expected_maps)):
        raise SystemExit('complete #1513 navigation graph manifest is invalid')
    seen = set()
    expected_files = set(name + '.json' for name in expected_maps)
    for record in records:
        if not isinstance(record, dict):
            raise SystemExit(
                'complete #1513 navigation graph batch is invalid')
        name = str(record.get('map') or '')
        filename = str(record.get('file') or '')
        expected_hash = str(record.get('sha256') or '')
        path = os.path.join(graph_root, filename)
        valid_hash = (
            len(expected_hash) == 64 and
            all(character in '0123456789abcdef'
                for character in expected_hash))
        if (name not in expected_maps or name in seen or
                filename != name + '.json' or not valid_hash or
                not os.path.isfile(path)):
            raise SystemExit('complete #1513 navigation graph batch is invalid')
        with open(path, 'rb') as stream:
            payload = stream.read()
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != expected_hash:
            raise SystemExit('navigation graph checksum mismatch: %s' % name)
        try:
            graph = json.loads(payload.decode('utf-8'))
            _navigation_schema.validate_graph(graph, name)
        except (TypeError, ValueError) as error:
            raise SystemExit(
                'navigation graph is invalid for %s: %s' % (name, error))
        seen.add(name)
    actual_files = set(
        filename for filename in os.listdir(graph_root)
        if filename.endswith('.json') and filename != 'manifest.json')
    if seen != expected_maps or actual_files != expected_files:
        raise SystemExit('complete #1513 navigation graph batch is invalid')


def _validate_foliage(foliage_root):
    """Reject partial, stale or tampered #1513 concealment data."""
    manifest_path = os.path.join(foliage_root, 'manifest.json')
    if not os.path.isfile(manifest_path):
        raise SystemExit('complete #1513 foliage batch is missing')
    with open(manifest_path, 'rb') as stream:
        manifest = json.load(stream)
    records = manifest.get('maps') if isinstance(manifest, dict) else None
    try:
        version = int(manifest.get('version', -1))
    except (AttributeError, TypeError, ValueError):
        version = -1
    expected_maps = set(_navigation_schema.SUPPORTED_MAPS)
    if (not isinstance(manifest, dict) or
            manifest.get('format') != FOLIAGE_MANIFEST_FORMAT or
            version != FOLIAGE_VERSION or
            manifest.get('game_version') != _navigation_schema.GAME_VERSION or
            not isinstance(records, list) or
            len(records) != len(expected_maps)):
        raise SystemExit('complete #1513 foliage manifest is invalid')
    seen = set()
    expected_files = set(name + '.json' for name in expected_maps)
    for record in records:
        if not isinstance(record, dict):
            raise SystemExit('complete #1513 foliage batch is invalid')
        name = str(record.get('map') or '')
        filename = str(record.get('file') or '')
        expected_hash = str(record.get('sha256') or '')
        path = os.path.join(foliage_root, filename)
        if (name not in expected_maps or name in seen or
                filename != name + '.json' or
                len(expected_hash) != 64 or not os.path.isfile(path)):
            raise SystemExit('complete #1513 foliage batch is invalid')
        with open(path, 'rb') as stream:
            payload = stream.read()
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise SystemExit('foliage checksum mismatch: %s' % name)
        try:
            data = json.loads(payload.decode('utf-8'))
            instances = data.get('instances')
            cells = data.get('cells')
            if (data.get('format') != FOLIAGE_FORMAT or
                    int(data.get('version', -1)) != FOLIAGE_VERSION or
                    data.get('game_version') != _navigation_schema.GAME_VERSION or
                    data.get('map') != name or
                    float(data.get('cell_size', 0.0)) <= 0.0 or
                    not isinstance(instances, list) or not instances or
                    not isinstance(cells, dict) or
                    any(not isinstance(row, list) or len(row) != 10
                        for row in instances)):
                raise ValueError('invalid foliage data')
        except (AttributeError, TypeError, ValueError) as error:
            raise SystemExit(
                'foliage data is invalid for %s: %s' % (name, error))
        seen.add(name)
    actual_files = set(
        filename for filename in os.listdir(foliage_root)
        if filename.endswith('.json') and filename != 'manifest.json')
    if seen != expected_maps or actual_files != expected_files:
        raise SystemExit('complete #1513 foliage batch is invalid')


def _remove_stale_outputs(dist_root):
    for filename in os.listdir(dist_root):
        is_mod = (filename.startswith(MOD_ID + '_') and
                  (filename.endswith('.wotmod') or
                   filename.endswith('.wotmod.sha256')))
        is_client_release = filename.startswith('WoT-0.9.22-LAN-Client-')
        is_old_overlay = filename == 'client-overlay'
        is_old_zip = filename.startswith(
            'WoT-0.9.22.0.1-Offline-LAN-Vertical-Slice-')
        output_path = os.path.join(dist_root, filename)
        if is_mod or is_client_release or is_old_overlay or is_old_zip:
            if os.path.isdir(output_path):
                shutil.rmtree(output_path)
            else:
                os.unlink(output_path)


def _validate_python():
    if sys.version_info[:2] != (2, 7):
        raise SystemExit('build_wotmod.py requires Python 2.7')
    if hasattr(sys, 'subversion') and sys.subversion[0] != 'CPython':
        raise SystemExit('build_wotmod.py requires CPython 2.7 bytecode')


def _validate_entry(staging_root):
    entry = os.path.join(
        staging_root,
        'res', 'scripts', 'client', 'gui', 'mods',
        'mod_offline_lan_0922.pyc')
    if not os.path.isfile(entry):
        raise SystemExit('compiled mod entry is missing: %s' % entry)
    with open(entry, 'rb') as stream:
        magic = stream.read(4)
    if magic != PYTHON_MAGIC:
        raise SystemExit('unexpected Python bytecode magic: %r' % magic)


def build():
    _validate_python()
    port_root = os.path.abspath(os.path.dirname(__file__))
    source_root = os.path.join(port_root, 'src')
    dist_root = os.path.join(port_root, 'dist')
    if not os.path.isdir(dist_root):
        os.makedirs(dist_root)
    _remove_stale_outputs(dist_root)
    staging_parent = tempfile.mkdtemp(prefix='offline-lan-0922-')
    try:
        staging_root = os.path.join(staging_parent, 'package')
        shutil.copytree(source_root, staging_root)
        shutil.copy2(os.path.join(port_root, 'meta.xml'), staging_root)
        _copy_legal_files(staging_root)
        _remove_stale_bytecode(staging_root)
        # A random temporary build path in code.co_filename changes every PYC
        # and defeats the content-hash release name.  Compile against a stable
        # package-relative root so identical sources produce identical bytes.
        if not compileall.compile_dir(
                staging_root, ddir='.', force=1, quiet=1):
            raise SystemExit('Python 2.7 compilation failed')
        _remove_sources(staging_root)
        _validate_entry(staging_root)
        filename = '%s_%s.wotmod' % (MOD_ID, MOD_VERSION)
        destination = os.path.join(dist_root, filename)
        _archive_tree(staging_root, destination)
        with open(destination, 'rb') as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        checksum_path = destination + '.sha256'
        with open(checksum_path, 'wb') as stream:
            stream.write(('%s  %s\n' % (digest, filename)).encode('ascii'))
        overlay_root, overlay_zip = _write_client_overlay(
            dist_root, destination, checksum_path, digest)
        print(destination)
        print('sha256=%s' % digest)
        print(overlay_root)
        print(overlay_zip)
    finally:
        shutil.rmtree(staging_parent)


if __name__ == '__main__':
    build()
