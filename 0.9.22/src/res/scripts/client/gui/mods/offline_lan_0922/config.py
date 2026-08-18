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
ENDPOINT_PATH = os.path.join(
    os.path.dirname(CONFIG_PATH), ENDPOINT_FILE_NAME)
ENDPOINT_PREFIX = 'LAN SERVER:'

DEFAULT_CONFIG = {
    'schema': 1,
    'enabled': True,
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
    # Per-chunk destructible and bot-steering traces. PERF summaries are
    # always published; these are the noisy per-event lines.
    'debug_logging': False,
}


def _copy_defaults():
    return dict((key, (value[:] if isinstance(value, list) else
                       dict(value) if isinstance(value, dict) else value))
                for key, value in DEFAULT_CONFIG.items())


def _replace(temporary_path, path):
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
        if move(_text(temporary_path), _text(path),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH):
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


def write_json(path, value):
    output_dir = os.path.dirname(path)
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    temporary_path = path + '.tmp'
    with open(temporary_path, 'wb') as stream:
        payload = json.dumps(value, indent=2, sort_keys=True) + '\n'
        stream.write(payload.encode('utf-8'))
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except (AttributeError, OSError):
            pass
    _replace(temporary_path, path)


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


def load(path=CONFIG_PATH, endpoint_path=None):
    config = _copy_defaults()
    if not os.path.isfile(path):
        write_json(path, config)
    else:
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

    if endpoint_path is None:
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
    return config
