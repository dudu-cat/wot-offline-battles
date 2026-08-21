from __future__ import print_function

import json
import os


try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


CONFIG_PATH = os.path.join(
    '.', 'mods', 'configs', 'offline_lan_0922', 'config.json')
ENDPOINT_FILE_NAME = 'server_endpoint.json'
LEGACY_USER_DATA_DIR = os.path.dirname(CONFIG_PATH)


def _default_user_data_dir(environ=None):
    """Keep player state outside ``mods`` when Windows exposes APPDATA."""
    environ = os.environ if environ is None else environ
    appdata = environ.get('APPDATA')
    if isinstance(appdata, string_types):
        appdata = appdata.strip()
    if appdata:
        return os.path.join(
            appdata, 'Wargaming.net', 'WorldOfTanks', 'offline_lan_0922')
    # Tests, portable Wine setups and unusual launchers may not expose
    # APPDATA.  Retain the old writable location instead of disabling state.
    return LEGACY_USER_DATA_DIR


USER_DATA_DIR = _default_user_data_dir()
ENDPOINT_PATH = os.path.join(USER_DATA_DIR, ENDPOINT_FILE_NAME)
LEGACY_ENDPOINT_PATH = os.path.join(
    LEGACY_USER_DATA_DIR, ENDPOINT_FILE_NAME)
ENDPOINT_PREFIX = 'LAN SERVER:'
CLIENT_MODE_ENV = 'OFFLINE_LAN_0922_CLIENT_MODE'
SERVER_HOST_ENV = 'OFFLINE_LAN_0922_SERVER_HOST'
SERVER_PORT_ENV = 'OFFLINE_LAN_0922_SERVER_PORT'
PLAYER_MODE = 'player'
SIMULATION_WORKER_MODE = 'simulation_worker'
CLIENT_MODES = frozenset((PLAYER_MODE, SIMULATION_WORKER_MODE))

DEFAULT_CONFIG = {
    'schema': 1,
    'enabled': True,
    # The normal package remains a player client. A separate copied game
    # directory may opt into the native-space simulation worker explicitly.
    'client_mode': PLAYER_MODE,
    'host': '127.0.0.1',
    'port': 28782,
    'name': 'Player',
    'vehicle': 'ussr:R11_MS-1',
    'max_health': 90,
    'startupTimeoutSeconds': 30.0,
    'prebattleCountdownSeconds': 15.0,
    'battleDurationSeconds': 900.0,
    'physics_tuning': {},
    'he_tuning': {},
    'perfect_accuracy': False,
    # Native belt animation for bots.  Off: a client-only vehicle gets no
    # engine-owned filter, so the belts cannot turn.  See
    # 0.9.22/COMPATIBILITY_REVIEW.md.
    'bot_track_animation': False,
    # Exact-build experiment: stock remote Vehicle entities provide native
    # compounds, stickers and engine-owned belt animation. Copied physics
    # remains authoritative because #1513 has no legal remote pose setter.
    'native_remote_vehicles': False,
    # One-shot measurement for a future non-rendering authority process.
    # This is intentionally off and does not change LAN authority or launch a
    # second client. The hidden-window phase also requires the external tools/
    # authority_worker_probe_supervisor.py process.
    'authority_worker_probe': {
        'enabled': False,
        'stageSeconds': 15.0,
    },
    # Per-chunk destructible and bot-steering traces. PERF summaries are
    # always published; these are the noisy per-event lines.
    'debug_logging': False,
}


def _copy_defaults():
    return dict((key, (value[:] if isinstance(value, list) else
                       dict(value) if isinstance(value, dict) else value))
                for key, value in DEFAULT_CONFIG.items())


def _quarantine_invalid_config(path):
    """Move an unreadable startup config aside without overwriting evidence."""
    candidate = path + '.invalid'
    suffix = 1
    while os.path.exists(candidate):
        candidate = path + '.invalid.%d' % suffix
        suffix += 1
    try:
        os.rename(path, candidate)
    except (IOError, OSError):
        return None
    return candidate


def _load_config_file(path):
    config = _copy_defaults()
    with open(path, 'rb') as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError('config root must be an object')
    for key in DEFAULT_CONFIG:
        if key in value:
            config[key] = value[key]
    config['startupTimeoutSeconds'] = max(
        1.0, float(config['startupTimeoutSeconds']))
    config['prebattleCountdownSeconds'] = max(
        0.0, float(config['prebattleCountdownSeconds']))
    config['battleDurationSeconds'] = max(
        1.0, float(config['battleDurationSeconds']))
    try:
        base_endpoint = _validate_endpoint(
            config.get('host'), config.get('port'))
    except ValueError:
        base_endpoint = (
            DEFAULT_CONFIG['host'], DEFAULT_CONFIG['port'])
    config['host'], config['port'] = base_endpoint
    config['max_health'] = max(1, int(config['max_health']))
    if not isinstance(config.get('enabled'), bool):
        raise ValueError('enabled must be true or false')
    if (not isinstance(config.get('client_mode'), string_types) or
            config.get('client_mode') not in CLIENT_MODES):
        raise ValueError(
            'client_mode must be player or simulation_worker')
    if not isinstance(config.get('vehicle'), string_types) or not config['vehicle']:
        raise ValueError('vehicle must be a non-empty string')
    if not isinstance(config.get('name'), string_types) or not config['name']:
        raise ValueError('name must be a non-empty string')
    if not isinstance(config.get('physics_tuning'), dict):
        raise ValueError('physics_tuning must be an object')
    if not isinstance(config.get('he_tuning'), dict):
        raise ValueError('he_tuning must be an object')
    if not isinstance(config.get('perfect_accuracy'), bool):
        raise ValueError('perfect_accuracy must be true or false')
    if not isinstance(config.get('native_remote_vehicles'), bool):
        raise ValueError('native_remote_vehicles must be true or false')
    probe = config.get('authority_worker_probe')
    if not isinstance(probe, dict):
        raise ValueError('authority_worker_probe must be an object')
    if not isinstance(probe.get('enabled'), bool):
        raise ValueError(
            'authority_worker_probe.enabled must be true or false')
    try:
        stage_seconds = float(probe.get('stageSeconds'))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(
            'authority_worker_probe.stageSeconds must be a number')
    if (stage_seconds != stage_seconds or
            stage_seconds in (float('inf'), float('-inf')) or
            stage_seconds < 15.0 or stage_seconds > 60.0):
        raise ValueError(
            'authority_worker_probe.stageSeconds must be 15-60 seconds')
    config['authority_worker_probe'] = {
        'enabled': probe['enabled'],
        'stageSeconds': stage_seconds,
    }
    return config, base_endpoint


def client_mode(config, environ=None):
    """Resolve one process-local mode without rewriting shared config.

    A worker launch batch sets the environment only for its child process.
    This lets an ordinary player package keep the default wire and bootstrap
    even when both copies use otherwise identical config files.
    """
    environ = os.environ if environ is None else environ
    configured = (config or {}).get('client_mode', PLAYER_MODE)
    override = environ.get(CLIENT_MODE_ENV)
    mode = override.strip() if isinstance(override, string_types) else configured
    if mode not in CLIENT_MODES:
        raise ValueError(
            '%s must be player or simulation_worker' % CLIENT_MODE_ENV)
    return mode


def _replace(temporary_path, path, write_through=True):
    """Move a finished temporary file over ``path`` without losing both.

    Python 2 has no ``os.replace``, and Windows ``os.rename`` refuses an
    existing destination.  Unlinking first leaves a window where a crash loses
    the file entirely, so prefer the Windows atomic replace and fall back to a
    recoverable backup rather than to a gap.
    """
    try:
        os.rename(temporary_path, path)
        return
    except OSError:
        pass
    try:
        import ctypes
        move = ctypes.windll.kernel32.MoveFileExW
    except (AttributeError, ImportError, OSError):
        move = None
    if move is not None:
        MOVEFILE_REPLACE_EXISTING = 0x1
        MOVEFILE_WRITE_THROUGH = 0x8
        flags = MOVEFILE_REPLACE_EXISTING
        if write_through:
            flags |= MOVEFILE_WRITE_THROUGH
        if move(_text(temporary_path), _text(path), flags):
            return
    backup_path = path + '.bak'
    if os.path.exists(backup_path):
        os.unlink(backup_path)
    os.rename(path, backup_path)
    try:
        os.rename(temporary_path, path)
    except OSError:
        os.rename(backup_path, path)
        raise
    os.unlink(backup_path)


def _text(value):
    try:
        return value.decode('utf-8') if isinstance(value, bytes) else value
    except (AttributeError, UnicodeDecodeError):
        return value


def write_json(path, value, durable=True):
    output_dir = os.path.dirname(path)
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    temporary_path = path + '.tmp'
    with open(temporary_path, 'wb') as stream:
        payload = json.dumps(value, indent=2, sort_keys=True) + '\n'
        stream.write(payload.encode('utf-8'))
        if durable:
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except (AttributeError, OSError):
                pass
    _replace(temporary_path, path, write_through=durable)


def migrate_legacy_user_file(path, legacy_path):
    """Copy one old ``mods/configs`` state file to its external owner path.

    The old file is intentionally retained for rollback to an earlier build.
    A failed migration returns the readable legacy path, so an APPDATA
    permission problem never turns a valid saved garage into a login failure.
    """
    if (path is None or legacy_path is None or
            os.path.normcase(os.path.abspath(path)) ==
            os.path.normcase(os.path.abspath(legacy_path)) or
            os.path.isfile(path) or not os.path.isfile(legacy_path)):
        return path
    output_dir = os.path.dirname(path)
    temporary_path = path + '.migrate.tmp'
    try:
        if output_dir and not os.path.isdir(output_dir):
            os.makedirs(output_dir)
        with open(legacy_path, 'rb') as source:
            payload = source.read()
        with open(temporary_path, 'wb') as destination:
            destination.write(payload)
            destination.flush()
            try:
                os.fsync(destination.fileno())
            except (AttributeError, OSError):
                pass
        _replace(temporary_path, path)
        return path
    except (IOError, OSError):
        try:
            if os.path.isfile(temporary_path):
                os.unlink(temporary_path)
        except (IOError, OSError):
            pass
        return legacy_path


# Backward-compatible private name for older extracted packages.
_write = write_json


def format_endpoint(host, port):
    return '%s %s:%s' % (ENDPOINT_PREFIX, host, int(port))


def _validate_endpoint(host, raw_port):
    if not isinstance(host, string_types):
        raise ValueError('LAN server host must be text')
    host = host.strip()
    if (not host or any(character.isspace() for character in host) or
            '/' in host or ':' in host):
        raise ValueError('LAN server host is invalid')
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise ValueError('LAN server port is invalid')
    if port < 1 or port > 65535:
        raise ValueError('LAN server port must be 1-65535')
    return (host, port)


def parse_endpoint(value, default_port=28782):
    """Parse the native training window's editable LAN endpoint field."""
    if not isinstance(value, string_types):
        raise ValueError('LAN server must be text')
    value = value.strip()
    if value.upper().startswith(ENDPOINT_PREFIX):
        value = value[len(ENDPOINT_PREFIX):].strip()
    if not value:
        raise ValueError('LAN server is empty')
    if ':' in value:
        host, raw_port = value.rsplit(':', 1)
    else:
        host, raw_port = value, default_port
    return _validate_endpoint(host, raw_port)


def save_endpoint(host, port, path=ENDPOINT_PATH):
    """Persist the user-owned endpoint without losing the previous value."""
    host, port = _validate_endpoint(host, port)
    value = {
        'schema': 1,
        'host': host,
        'port': port,
    }
    output_dir = os.path.dirname(path)
    temporary_path = path + '.tmp'
    backup_path = path + '.bak'
    previous_moved = False
    try:
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)
        with open(temporary_path, 'wb') as stream:
            payload = json.dumps(value, indent=2, sort_keys=True) + '\n'
            stream.write(payload.encode('utf-8'))
        if os.path.exists(backup_path):
            os.unlink(backup_path)
        if os.path.exists(path):
            os.rename(path, backup_path)
            previous_moved = True
        try:
            os.rename(temporary_path, path)
        except (IOError, OSError):
            if previous_moved and not os.path.exists(path):
                os.rename(backup_path, path)
                previous_moved = False
            raise
        if previous_moved and os.path.exists(backup_path):
            try:
                os.unlink(backup_path)
            except (IOError, OSError):
                pass
    except (IOError, OSError):
        if os.path.exists(temporary_path):
            try:
                os.unlink(temporary_path)
            except (IOError, OSError):
                pass
        return False
    return True


def _load_endpoint(path):
    with open(path, 'rb') as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError('LAN server settings are invalid')
    return _validate_endpoint(value.get('host'), value.get('port'))


def _endpoint_path_for_config(path):
    return os.path.join(os.path.dirname(path), ENDPOINT_FILE_NAME)


def _environment_endpoint(host, port, environ=None):
    """Apply one process-local endpoint without changing the saved LAN room."""
    environ = os.environ if environ is None else environ
    override_host = environ.get(SERVER_HOST_ENV)
    override_port = environ.get(SERVER_PORT_ENV)
    if override_host is None and override_port is None:
        return host, port
    if override_host is None:
        override_host = host
    if override_port is None:
        override_port = port
    return _validate_endpoint(override_host, override_port)


def load(path=CONFIG_PATH, endpoint_path=None, environ=None):
    if not os.path.isfile(path):
        config = _copy_defaults()
        write_json(path, config)
        base_endpoint = (config['host'], config['port'])
    else:
        try:
            config, base_endpoint = _load_config_file(path)
        except (IOError, OSError, OverflowError, TypeError, ValueError):
            _quarantine_invalid_config(path)
            config = _copy_defaults()
            base_endpoint = (config['host'], config['port'])
            try:
                write_json(path, config)
            except (IOError, OSError):
                # Login must remain available even when this directory became
                # read-only after the previous client exit.
                pass

    if endpoint_path is None:
        if path == CONFIG_PATH:
            endpoint_path = migrate_legacy_user_file(
                ENDPOINT_PATH, LEGACY_ENDPOINT_PATH)
        else:
            # Tests and embedded consumers with an explicit config path keep
            # their endpoint beside that caller-owned config.
            endpoint_path = _endpoint_path_for_config(path)
    if os.path.isfile(endpoint_path):
        try:
            config['host'], config['port'] = _load_endpoint(endpoint_path)
        except (IOError, OSError, TypeError, ValueError):
            # User data is optional.  A truncated or hand-edited file must not
            # prevent login or redirect the client to an uncertain endpoint.
            config['host'] = DEFAULT_CONFIG['host']
            config['port'] = DEFAULT_CONFIG['port']
    elif base_endpoint != (DEFAULT_CONFIG['host'], DEFAULT_CONFIG['port']):
        # Migrate the endpoint written by older packages before a later
        # overlay refresh restores config.json to the product defaults.
        save_endpoint(base_endpoint[0], base_endpoint[1], endpoint_path)
    config['host'], config['port'] = _environment_endpoint(
        config['host'], config['port'], environ)
    return config
