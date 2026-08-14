#!/usr/bin/env python3
"""Bake #1513 SpeedTree concealment volumes into a runtime spatial index.

The pinned client stores SpeedTree instances in the compiled ``space.bin``
``SpTr`` section and resolves their resource paths through ``BWST``.  This
tool keeps the mature 0.8.2 foliage row/cell format while decoding those
version-specific inputs directly from the 0.9.22 client packages.
"""

import argparse
import hashlib
import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import zipfile


TOOL_ROOT = os.path.dirname(os.path.abspath(__file__))
PORT_ROOT = os.path.dirname(TOOL_ROOT)
SCHEMA_ROOT = os.path.join(
    PORT_ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
    'offline_lan_0922')
VENDOR_ROOT = os.path.join(TOOL_ROOT, 'vendor')
for path in (TOOL_ROOT, SCHEMA_ROOT, VENDOR_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from packed_xml import read_packed_xml
from wot_space_bin_utils import CompiledSpace
import navigation_graph_schema


FORMAT_NAME = 'offline-lan-0922-foliage'
FORMAT_VERSION = 1
MANIFEST_FORMAT = FORMAT_NAME + '-manifest'
GAME_VERSION = '0.9.22.0.1-cn-1513'
DECODER_VERSION = '0.9.22.0.1'
DECODER_REGION = 'RU'
CELL_SIZE = 32.0
CAMOUFLAGE_PER_VOLUME = 0.15
CTREE_VERSION = 106
SUPPORTED_MAPS = navigation_graph_schema.SUPPORTED_MAPS
DEFAULT_OUTPUT_ROOT = os.path.join(PORT_ROOT, 'foliage')


class CaseFoldZipResources(object):
    """Read a case-insensitive VFS assembled from ordered ZIP packages."""

    def __init__(self, paths):
        self.packages = []
        self.names = {}
        try:
            for path in paths:
                package = zipfile.ZipFile(path, 'r')
                package_index = len(self.packages)
                self.packages.append(package)
                for name in package.namelist():
                    key = name.replace('\\', '/').lower()
                    # The map package is passed first and owns any override.
                    if key not in self.names:
                        self.names[key] = (package_index, name)
        except Exception:
            self.close()
            raise

    def read(self, name):
        key = str(name).replace('\\', '/').lower()
        location = self.names.get(key)
        if location is None:
            raise KeyError(name)
        package_index, actual = location
        return self.packages[package_index].read(actual)

    def close(self):
        for package in self.packages:
            package.close()
        self.packages = []
        self.names = {}

    def __enter__(self):
        return self

    def __exit__(self, unused_type, unused_value, unused_traceback):
        self.close()


def bush_tokens(data):
    """Return exact client taxonomy tokens from speedtree/bushes.xml."""
    root = read_packed_xml(data)
    result = []
    for name, unused_value in root.children:
        token = name.decode('ascii').strip().lower()
        if token:
            result.append(token)
    if not result:
        raise ValueError('speedtree/bushes.xml contains no bush taxonomy')
    return tuple(sorted(set(result), key=lambda value: (-len(value), value)))


def ctree_bounds(data):
    """Decode the #1513 ctree header's exact local-space bounding box."""
    if len(data) < 28:
        raise ValueError('truncated ctree resource')
    version, min_x, min_y, min_z, max_x, max_y, max_z = \
        struct.unpack_from('<I6f', data, 0)
    if version != CTREE_VERSION:
        raise ValueError('unsupported ctree version %d' % version)
    if not (min_x < max_x and min_y < max_y and min_z < max_z):
        raise ValueError('invalid ctree bounds')
    return (min_x, min_y, min_z), (max_x, max_y, max_z)


def is_bush_resource(resource, tokens):
    normalized = str(resource).replace('\\', '/')
    stem = os.path.splitext(os.path.basename(normalized))[0].lower()
    return any(token in stem for token in tokens)


def _round(value):
    return round(float(value), 4)


def _transform_point(transform, point):
    """Apply the column-major 4x4 matrix stored by #1513 SpTr rows."""
    if len(transform) != 16:
        raise ValueError('SpeedTree transform must contain 16 floats')
    x, y, z = point
    return (
        transform[0] * x + transform[4] * y +
        transform[8] * z + transform[12],
        transform[1] * x + transform[5] * y +
        transform[9] * z + transform[13],
        transform[2] * x + transform[6] * y +
        transform[10] * z + transform[14],
    )


def foliage_instance(bounds, transform):
    """Convert local ctree bounds and a world transform to one 10-value row."""
    if len(transform) != 16:
        raise ValueError('SpeedTree transform must contain 16 floats')
    minimum, maximum = bounds
    centre = tuple((minimum[index] + maximum[index]) * 0.5
                   for index in range(3))
    world_centre = _transform_point(transform, centre)
    half_sizes = tuple((maximum[index] - minimum[index]) * 0.5
                       for index in range(3))
    projected_axes = (
        (float(transform[0]) * half_sizes[0],
         float(transform[2]) * half_sizes[0]),
        (float(transform[4]) * half_sizes[1],
         float(transform[6]) * half_sizes[1]),
        (float(transform[8]) * half_sizes[2],
         float(transform[10]) * half_sizes[2]),
    )
    basis_candidates = []
    for first_index, second_index in ((0, 1), (0, 2), (1, 2)):
        first = projected_axes[first_index]
        second = projected_axes[second_index]
        determinant = first[0] * second[1] - first[1] * second[0]
        basis_candidates.append((abs(determinant), determinant, first, second))
    unused_area, determinant, axis_first, axis_second = max(basis_candidates)
    if abs(determinant) <= 1e-8:
        raise ValueError('degenerate foliage transform')
    raw_inverse = (
        axis_second[1] / determinant,
        -axis_second[0] / determinant,
        -axis_first[1] / determinant,
        axis_first[0] / determinant,
    )
    corners = []
    for x in (minimum[0], maximum[0]):
        for y in (minimum[1], maximum[1]):
            for z in (minimum[2], maximum[2]):
                corners.append(_transform_point(transform, (x, y, z)))
    minimum_y = min(point[1] for point in corners)
    maximum_y = max(point[1] for point in corners)
    minimum_x = min(point[0] for point in corners)
    maximum_x = max(point[0] for point in corners)
    minimum_z = min(point[2] for point in corners)
    maximum_z = max(point[2] for point in corners)
    extent_first = 0.0
    extent_second = 0.0
    for point in corners:
        dx = point[0] - world_centre[0]
        dz = point[2] - world_centre[2]
        extent_first = max(
            extent_first, abs(raw_inverse[0] * dx + raw_inverse[1] * dz))
        extent_second = max(
            extent_second, abs(raw_inverse[2] * dx + raw_inverse[3] * dz))
    if extent_first <= 1e-8 or extent_second <= 1e-8:
        raise ValueError('degenerate projected foliage bounds')
    inverse = (
        raw_inverse[0] / extent_first,
        raw_inverse[1] / extent_first,
        raw_inverse[2] / extent_second,
        raw_inverse[3] / extent_second,
    )
    radius = max(math.hypot(point[0] - world_centre[0],
                            point[2] - world_centre[2])
                 for point in corners)
    row = [
        _round(world_centre[0]), _round(minimum_y),
        _round(world_centre[2]), _round(maximum_y),
        _round(inverse[0]), _round(inverse[1]),
        _round(inverse[2]), _round(inverse[3]),
        CAMOUFLAGE_PER_VOLUME, _round(radius),
    ]
    return row, (minimum_x, minimum_z, maximum_x, maximum_z)


def decode_speedtrees(space_data):
    """Decode ordered SpTr rows and their BWST resource names."""
    compiled = CompiledSpace(io.BytesIO(space_data), DECODER_VERSION,
                             DECODER_REGION, ['BWST', 'SpTr'])
    missing = [name for name in ('BWST', 'SpTr')
               if name not in compiled.sections]
    if missing:
        raise ValueError('compiled space omitted %s' % ', '.join(missing))
    strings = compiled.sections['BWST']
    result = []
    for index, row in enumerate(
            compiled.sections['SpTr']._data['speedtree_list']):
        resource = strings.get(row['spt_fnv'])
        if not resource:
            raise ValueError('SpTr row %d has no BWST resource' % index)
        transform = tuple(float(value) for value in row['transform'])
        if len(transform) != 16:
            raise ValueError('SpTr row %d has an invalid transform' % index)
        result.append((resource, transform))
    return result


def bake_speedtrees(resources, map_name, tokens, speedtrees,
                    cell_size=CELL_SIZE):
    """Bake already-decoded SpeedTree rows; kept pure for deterministic tests."""
    if float(cell_size) <= 0.0:
        raise ValueError('cell size must be positive')
    instances = []
    cells = {}
    asset_counts = {}
    bush_source_count = 0
    for source_index, (resource, transform) in enumerate(speedtrees):
        if not is_bush_resource(resource, tokens):
            continue
        bush_source_count += 1
        ctree = os.path.splitext(resource)[0] + '.ctree'
        try:
            bounds = ctree_bounds(resources.read(ctree))
            row, world_bounds = foliage_instance(bounds, transform)
        except (KeyError, ValueError, struct.error) as error:
            raise ValueError('bush resource failed %s at SpTr row %d: %s' %
                             (resource, source_index, error))
        instance_id = len(instances)
        instances.append(row)
        normalized = str(resource).replace('\\', '/')
        asset = os.path.splitext(os.path.basename(normalized))[0].lower()
        asset_counts[asset] = asset_counts.get(asset, 0) + 1
        min_cell_x = int(math.floor(world_bounds[0] / float(cell_size)))
        min_cell_z = int(math.floor(world_bounds[1] / float(cell_size)))
        max_cell_x = int(math.floor(world_bounds[2] / float(cell_size)))
        max_cell_z = int(math.floor(world_bounds[3] / float(cell_size)))
        for cell_x in range(min_cell_x, max_cell_x + 1):
            for cell_z in range(min_cell_z, max_cell_z + 1):
                key = '%d,%d' % (cell_x, cell_z)
                cells.setdefault(key, []).append(instance_id)
    if not instances:
        raise ValueError('no concealment vegetation found for %s' % map_name)
    return {
        'format': FORMAT_NAME,
        'version': FORMAT_VERSION,
        'game_version': GAME_VERSION,
        'map': map_name,
        'cell_size': float(cell_size),
        'instances': instances,
        'cells': cells,
        'bake': {
            'taxonomy': list(tokens),
            'matching': 'case-insensitive asset-name substring',
            'source_speedtrees': len(speedtrees),
            'source_bushes': bush_source_count,
            'foliage_instances': len(instances),
            'spatial_cells': len(cells),
            'camouflage_per_volume': CAMOUFLAGE_PER_VOLUME,
            'ctree_version': CTREE_VERSION,
            'resource_packages': [
                map_name + '.pkg', 'shared_content.pkg',
                'shared_content_sandbox.pkg',
            ],
            'asset_counts': asset_counts,
        },
    }


def _package_paths(client_root, map_name):
    packages = os.path.join(os.path.abspath(client_root), 'res', 'packages')
    map_path = os.path.join(packages, map_name + '.pkg')
    shared_path = os.path.join(packages, 'shared_content.pkg')
    sandbox_path = os.path.join(packages, 'shared_content_sandbox.pkg')
    misc_path = os.path.join(packages, 'misc.pkg')
    for path in (map_path, shared_path, sandbox_path, misc_path):
        if not os.path.isfile(path):
            raise ValueError('required client package not found: %s' % path)
    return map_path, shared_path, sandbox_path, misc_path


def read_taxonomy(client_root):
    unused_map, unused_shared, unused_sandbox, misc_path = _package_paths(
        client_root, SUPPORTED_MAPS[0])
    with CaseFoldZipResources((misc_path,)) as resources:
        return bush_tokens(resources.read('speedtree/bushes.xml'))


def bake_map(client_root, map_name, tokens=None, cell_size=CELL_SIZE):
    if map_name not in SUPPORTED_MAPS:
        raise ValueError('unsupported standard map: %s' % map_name)
    map_path, shared_path, sandbox_path, misc_path = _package_paths(
        client_root, map_name)
    if tokens is None:
        with CaseFoldZipResources((misc_path,)) as misc:
            tokens = bush_tokens(misc.read('speedtree/bushes.xml'))
    space_member = 'spaces/%s/space.bin' % map_name
    with zipfile.ZipFile(map_path, 'r') as package:
        try:
            space_data = package.read(space_member)
        except KeyError:
            raise ValueError('compiled space missing: %s' % space_member)
    speedtrees = decode_speedtrees(space_data)
    # A small set of stock winter foliage (for example CaneReeds1) is stored
    # in shared_content_sandbox.pkg rather than either the map package or
    # shared_content.pkg in the pinned Chinese HD client.
    with CaseFoldZipResources(
            (map_path, shared_path, sandbox_path)) as resources:
        return bake_speedtrees(resources, map_name, tokens, speedtrees,
                               cell_size)


def write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8', newline='\n') as output:
        json.dump(data, output, sort_keys=True, separators=(',', ':'))
        output.write('\n')
    os.replace(temporary, path)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _write_manifest(output_root, digests):
    records = []
    for map_name in SUPPORTED_MAPS:
        records.append({
            'map': map_name,
            'file': map_name + '.json',
            'sha256': digests[map_name],
        })
    write_json(os.path.join(output_root, 'manifest.json'), {
        'format': MANIFEST_FORMAT,
        'version': FORMAT_VERSION,
        'game_version': GAME_VERSION,
        'maps': records,
    })


def bake_all(client_root, output_root=DEFAULT_OUTPUT_ROOT,
             cell_size=CELL_SIZE):
    """Bake all supported maps, then publish data first and manifest last."""
    output_root = os.path.abspath(output_root)
    parent = os.path.dirname(output_root)
    if not os.path.isdir(parent):
        raise ValueError('foliage output parent does not exist: %s' % parent)
    if not os.path.isdir(output_root):
        os.makedirs(output_root)
    expected = set(map_name + '.json' for map_name in SUPPORTED_MAPS)
    actual = set(name for name in os.listdir(output_root)
                 if name.endswith('.json') and name != 'manifest.json')
    if actual and actual != expected:
        raise ValueError('existing foliage output set is incomplete or extra')
    tokens = read_taxonomy(client_root)
    with tempfile.TemporaryDirectory(
            prefix='offline-lan-0922-foliage-', dir=parent) as staging:
        digests = {}
        for map_name in SUPPORTED_MAPS:
            data = bake_map(client_root, map_name, tokens, cell_size)
            path = os.path.join(staging, map_name + '.json')
            write_json(path, data)
            digests[map_name] = _sha256(path)
            print('baked %s: %d foliage volumes in %d cells' % (
                map_name, len(data['instances']), len(data['cells'])),
                flush=True)
        if set(digests) != set(SUPPORTED_MAPS):
            raise ValueError('foliage batch did not produce every map')
        _write_manifest(staging, digests)
        for map_name in SUPPORTED_MAPS:
            os.replace(os.path.join(staging, map_name + '.json'),
                       os.path.join(output_root, map_name + '.json'))
        os.replace(os.path.join(staging, 'manifest.json'),
                   os.path.join(output_root, 'manifest.json'))
    return digests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True,
                        help='Pinned #1513 client root')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--map', choices=SUPPORTED_MAPS)
    selection.add_argument('--all', action='store_true')
    parser.add_argument('--cell-size', type=float, default=CELL_SIZE)
    parser.add_argument('--output',
                        help='Single-map output JSON path')
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_ROOT,
                        help='Complete batch destination')
    args = parser.parse_args(argv)
    if args.all and args.output:
        parser.error('--output can only be used with one --map')
    try:
        if args.all:
            digests = bake_all(args.client, args.output_dir, args.cell_size)
            print('validated foliage batch: %d standard maps' % len(digests))
        else:
            map_name = args.map or '07_lakeville'
            data = bake_map(args.client, map_name, cell_size=args.cell_size)
            output = args.output or os.path.join(
                args.output_dir, map_name + '.json')
            write_json(output, data)
            print('baked %s: %d foliage volumes in %d cells' % (
                map_name, len(data['instances']), len(data['cells'])))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print('FAILED foliage bake: %s' % error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
