#!/usr/bin/env python3
"""Bake solid static occluders for the server authority from #1513 spaces.

Every BSMI instance whose BSMO model is a plain static (type 0) or a
preserved structure (type 3) contributes its authored collision bounding
box, transformed into world space. Falling/fragile/structure destructibles
(types 1 and 2) are excluded: the destructible catalog already carries
them with per-instance destroyed state.
"""

import argparse
import hashlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import zipfile

TOOLS_ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR_ROOT = os.path.join(TOOLS_ROOT, 'vendor')
PORT_ROOT = os.path.dirname(TOOLS_ROOT)
CLIENT_SCRIPT_ROOT = os.path.join(
    PORT_ROOT, 'src', 'res', 'scripts', 'client')
for path in (VENDOR_ROOT, CLIENT_SCRIPT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from gui.mods.offline_lan_0922.navigation_graph_schema import (  # noqa: E402
    GAME_VERSION, SUPPORTED_MAPS)

FORMAT_NAME = 'offline-lan-0922-occluders'
FORMAT_VERSION = 1
MANIFEST_FORMAT = 'offline-lan-0922-occluder-manifest'
MODEL_TYPE_FALLING = 1
MODEL_TYPE_DESTRUCTIBLE = 2
CAPTURE_THE_FLAG_MASK = None


def _capture_the_flag_mask():
    global CAPTURE_THE_FLAG_MASK
    if CAPTURE_THE_FLAG_MASK is None:
        from wot_space_bin_utils.universal_space import VisbilityFlags
        CAPTURE_THE_FLAG_MASK = int(VisbilityFlags.CAPTURE_THE_FLAG)
    return CAPTURE_THE_FLAG_MASK


def _client_space(client_root, map_name):
    packages = os.path.join(
        os.path.abspath(client_root), 'res', 'packages')
    map_path = os.path.join(packages, map_name + '.pkg')
    if not os.path.isfile(map_path):
        raise ValueError('required client package not found: %s' % map_path)
    with zipfile.ZipFile(map_path, 'r') as package:
        member = 'spaces/%s/space.bin' % map_name
        try:
            return package.read(member)
        except KeyError:
            raise ValueError('compiled space missing: %s' % member)


def _validated_bounds(collider, model_id):
    values = (tuple(collider['collision_bounds_min']) +
              tuple(collider['collision_bounds_max']))
    if (len(values) != 6 or not all(
            math.isfinite(float(value)) for value in values)):
        raise ValueError(
            'BSMO model %d has non-finite collision bounds' % model_id)
    if any(float(values[index]) > float(values[index + 3])
           for index in range(3)):
        raise ValueError(
            'BSMO model %d has inverted collision bounds' % model_id)
    if all(abs(float(values[index]) - float(values[index + 3])) <= 1e-9
           for index in range(3)):
        return None
    return tuple(round(float(value), 6) for value in values)


def bake_map(client_root, map_name):
    from wot_space_bin_utils import CompiledSpace
    space_data = _client_space(client_root, map_name)
    compiled = CompiledSpace(
        io.BytesIO(space_data), '0.9.22.0.1', 'RU',
        ['BWST', 'BSMI', 'BSMO'])
    for header in ('BSMI', 'BSMO'):
        if header not in compiled.sections:
            raise ValueError('%s section missing for %s' % (
                header, map_name))
    bsmi = compiled.sections['BSMI']
    bsmo = compiled.sections['BSMO']._data
    transforms = bsmi._data['transforms']
    model_ids = list(bsmi.model_ids())
    visibility = bsmi._data['visibility_masks']
    if len(transforms) != len(model_ids) or len(visibility) != len(model_ids):
        raise ValueError('BSMI arrays are inconsistent for %s' % map_name)
    model_infos = bsmo['model_info_items']
    colliders = bsmo['models_colliders']
    mask = _capture_the_flag_mask()
    instances = []
    for index, (transform, model_id) in enumerate(
            zip(transforms, model_ids)):
        if not int(visibility[index]) & mask:
            continue
        if model_id < 0 or model_id >= len(model_infos):
            raise ValueError('BSMI references an invalid BSMO model')
        model_type = int(model_infos[model_id]['type'])
        if model_type in (MODEL_TYPE_FALLING, MODEL_TYPE_DESTRUCTIBLE):
            continue
        if model_id >= len(colliders):
            raise ValueError('BSMI references an invalid BSMO collider')
        bounds = _validated_bounds(colliders[model_id], model_id)
        if bounds is None:
            continue
        if len(transform) != 16:
            raise ValueError('invalid #1513 static transform')
        row = (
            [round(float(transform[i]), 6) for i in (12, 13, 14)] +
            [round(float(transform[i]), 6)
             for i in (0, 1, 2, 4, 5, 6, 8, 9, 10)] +
            list(bounds))
        instances.append(row)
    instances.sort()
    return {
        'format': FORMAT_NAME,
        'version': FORMAT_VERSION,
        'game_version': GAME_VERSION,
        'map': map_name,
        'instances': instances,
    }


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def bake_all(client_root, output_root):
    staging = tempfile.mkdtemp(prefix='occluders-', dir=os.path.dirname(
        os.path.abspath(output_root)) or '.')
    try:
        records = []
        for map_name in sorted(SUPPORTED_MAPS):
            document = bake_map(client_root, map_name)
            path = os.path.join(staging, map_name + '.json')
            with open(path, 'w') as handle:
                json.dump(document, handle, separators=(',', ':'),
                          sort_keys=True)
            records.append({
                'map': map_name,
                'file': map_name + '.json',
                'sha256': _sha256(path),
                'instances': len(document['instances']),
            })
            print('baked %s: %d occluders' % (
                map_name, len(document['instances'])))
        manifest = {
            'format': MANIFEST_FORMAT,
            'version': FORMAT_VERSION,
            'game_version': GAME_VERSION,
            'maps': records,
        }
        with open(os.path.join(staging, 'manifest.json'), 'w') as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
        if os.path.isdir(output_root):
            shutil.rmtree(output_root)
        os.replace(staging, output_root)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client_root')
    parser.add_argument('--output', default=os.path.join(
        PORT_ROOT, 'occluders'))
    parser.add_argument('--map', dest='single_map')
    arguments = parser.parse_args()
    if arguments.single_map:
        document = bake_map(arguments.client_root, arguments.single_map)
        json.dump(document, sys.stdout, indent=2, sort_keys=True)
        print()
        return 0
    bake_all(arguments.client_root, arguments.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
