from __future__ import print_function

import json
import os


try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


CONFIG_PATH = os.path.join(
    '.', 'mods', 'configs', 'offline_lan_0922', 'config.json')

DEFAULT_CONFIG = {
    'schema': 1,
    'enabled': True,
    'host': '127.0.0.1',
    'port': 28782,
    'name': 'Player',
    'vehicle': 'ussr:R11_MS-1',
    'max_health': 90,
    'startupTimeoutSeconds': 30.0,
}


def _copy_defaults():
    return dict((key, value[:] if isinstance(value, list) else value)
                for key, value in DEFAULT_CONFIG.items())


def write_json(path, value):
    output_dir = os.path.dirname(path)
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    temporary_path = path + '.tmp'
    with open(temporary_path, 'wb') as stream:
        payload = json.dumps(value, indent=2, sort_keys=True) + '\n'
        stream.write(payload.encode('utf-8'))
    if os.path.exists(path):
        os.unlink(path)
    os.rename(temporary_path, path)


# Backward-compatible private name for older extracted packages.
_write = write_json


def load(path=CONFIG_PATH):
    config = _copy_defaults()
    if not os.path.isfile(path):
        write_json(path, config)
        return config
    with open(path, 'rb') as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError('config root must be an object')
    for key in DEFAULT_CONFIG:
        if key in value:
            config[key] = value[key]
    config['startupTimeoutSeconds'] = max(
        1.0, float(config['startupTimeoutSeconds']))
    config['port'] = max(1, min(65535, int(config['port'])))
    config['max_health'] = max(1, int(config['max_health']))
    if not isinstance(config.get('enabled'), bool):
        raise ValueError('enabled must be true or false')
    if not isinstance(config.get('vehicle'), string_types) or not config['vehicle']:
        raise ValueError('vehicle must be a non-empty string')
    if not isinstance(config.get('host'), string_types) or not config['host']:
        raise ValueError('host must be a non-empty string')
    if not isinstance(config.get('name'), string_types) or not config['name']:
        raise ValueError('name must be a non-empty string')
    return config
