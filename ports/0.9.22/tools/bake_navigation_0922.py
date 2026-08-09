#!/usr/bin/env python3
"""Inspect strict navigation-bake inputs from the pinned WoT 0.9.22 client.

This is the #1513 input adapter companion to the proven 0.8.2
``tools/bake_navigation.py`` algorithm.  It reads the newer packed resources:

* arena definitions and destructibles from ``scripts.pkg``;
* terrain height blocks from ``*.cdata_processed``; and
* compiled-space metadata from ``space.bin``.

The shared 0.8.2 baker supplies the proven graph construction and validation
algorithm.  This adapter decodes #1513 collision, destructible, bridge and
water semantics before baking any supported standard-mode map.  A terrain-only
graph would silently route tanks through buildings, bridges or water.
"""

import argparse
import importlib.util
import io
import json
import math
import os
import struct
import sys
import zipfile

from packed_xml import (TYPE_ELEMENT, TYPE_STRING, TYPE_VECTOR, read_packed_xml)
from space_bin_0922 import (CompiledSpace, CompiledSpaceError,
                            UnsafeBakeInputError, describe_space)


GAME_VERSION = '0.9.22.0.1-cn-1513'
VENDOR_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')
_DOWNLOADS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
LEGACY_BAKER = os.path.join(_DOWNLOADS_ROOT, 'WoT-0.8.2-release-v1.0.1-worktree',
                            'tools', 'bake_navigation.py')


def decode_scene_indices(space_data):
    """Decode the #1513 scene indices with the pinned 0.9.22 decoder.

    This covers transforms, models, BWSG/BSGD references and water/control
    sections.  It intentionally returns only counts here: the next adapter
    step must turn the verified references into collision triangles before a
    graph can be emitted.
    """
    if VENDOR_ROOT not in sys.path:
        sys.path.insert(0, VENDOR_ROOT)
    from wot_space_bin_utils import CompiledSpace as VendorCompiledSpace
    required = ('BWST', 'BWT2', 'BWSG', 'BSGD', 'BSMI', 'BSMO', 'BSMA',
                'BWWa', 'WTCP')
    decoded = VendorCompiledSpace(io.BytesIO(space_data), '0.9.22.0.1', 'RU',
                                  list(required))
    missing = [name for name in required if name not in decoded.sections]
    if missing:
        raise CompiledSpaceError('versioned scene decoder omitted %s' %
                                 ', '.join(missing))
    return {
        'decoder': 'wot-space.bin-utils 0.9.22.0.1 section set',
        'static_instances': len(decoded.sections['BSMI']._data['transforms']),
        'static_models': len(decoded.sections['BSMO']._data['models_loddings']),
        'static_geometry_blocks': len(decoded.sections['BWSG']._data['positions']),
        'static_geometry_bytes': len(decoded.sections['BSGD']._data),
        'water_records': len(decoded.sections['BWWa']._data['1']),
        'control_points': len(decoded.sections['WTCP']._data['control_points']),
    }


def _children(element, name):
    encoded = name.encode('ascii')
    return [value for child, value in element.children if child == encoded]


def _child(element, name):
    values = _children(element, name)
    if not values:
        raise ValueError('missing packed XML child %s' % name)
    return values[0]


def _vector2(value):
    if value.value_type == TYPE_VECTOR:
        if len(value.value) < 8 or len(value.value) % 4:
            raise ValueError('invalid packed XML vector')
        result = struct.unpack('<%df' % (len(value.value) // 4), value.value)
        return float(result[0]), float(result[1])
    if value.value_type == TYPE_STRING:
        fields = value.value.decode('utf-8').split()
        if len(fields) >= 2:
            return float(fields[0]), float(fields[1])
    raise ValueError('base position is neither vector nor text')


def ctf_bases(arena_data):
    root = read_packed_xml(arena_data)
    gameplay = _child(root, 'gameplayTypes')
    if gameplay.value_type != TYPE_ELEMENT:
        raise ValueError('gameplayTypes is not an element')
    ctf = _child(gameplay.value, 'ctf')
    if ctf.value_type != TYPE_ELEMENT:
        raise ValueError('ctf is not an element')
    positions = _child(ctf.value, 'teamBasePositions')
    if positions.value_type != TYPE_ELEMENT:
        raise ValueError('teamBasePositions is not an element')
    result = []
    for team in (1, 2):
        entry = _child(positions.value, 'team%d' % team)
        if entry.value_type != TYPE_ELEMENT:
            raise ValueError('team%d bases are not an element' % team)
        candidates = [value for name, value in entry.value.children
                      if name.startswith(b'position')]
        if not candidates:
            raise ValueError('team%d has no CTF base' % team)
        result.append(_vector2(candidates[0]))
    return tuple(result)


def ctf_spawn_points(arena_data):
    """Return stock CTF spawn points, if that gameplay definition provides any.

    Ensk's CTF definition deliberately has none; its ``domination`` entry has
    separate spawn coordinates that must never be reused for standard battle.
    """
    root = read_packed_xml(arena_data)
    gameplay = _child(root, 'gameplayTypes')
    ctf = _child(gameplay.value, 'ctf')
    values = _children(ctf.value, 'teamSpawnPoints')
    if not values:
        return ((), ())
    if values[0].value_type != TYPE_ELEMENT:
        raise ValueError('ctf teamSpawnPoints is not an element')
    result = []
    for team in (1, 2):
        entries = _children(values[0].value, 'team%d' % team)
        points = []
        if entries and entries[0].value_type == TYPE_ELEMENT:
            for name, value in entries[0].value.children:
                if name.startswith(b'position'):
                    points.append(_vector2(value))
        result.append(tuple(points))
    return tuple(result)


def arena_bounds(arena_data):
    """Return the stock playable rectangle from the packed arena definition."""
    root = read_packed_xml(arena_data)
    bounding_box = _child(root, 'boundingBox')
    if bounding_box.value_type != TYPE_ELEMENT:
        raise ValueError('boundingBox is not an element')
    bottom_left = _vector2(_child(bounding_box.value, 'bottomLeft'))
    upper_right = _vector2(_child(bounding_box.value, 'upperRight'))
    bounds = (bottom_left[0], bottom_left[1],
              upper_right[0], upper_right[1])
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError('arena boundingBox is empty or inverted')
    return bounds


def _signed_hex16(value):
    number = int(value, 16)
    return number - 65536 if number >= 65536 // 2 else number


def chunk_coordinates(name):
    base = os.path.basename(name).split('.', 1)[0]
    if len(base) != 9 or not base.endswith('o'):
        raise ValueError('invalid compiled terrain chunk %s' % name)
    return _signed_hex16(base[:4]), _signed_hex16(base[4:8])


def processed_height_chunks(package, map_name):
    prefix = 'spaces/%s/' % map_name
    result = []
    for name in sorted(package.namelist()):
        if not name.startswith(prefix) or not name.endswith('.cdata_processed'):
            continue
        location = chunk_coordinates(name)
        nested = zipfile.ZipFile(io.BytesIO(package.read(name)), 'r')
        try:
            if 'terrain2/heights' not in nested.namelist():
                continue
            result.append((location, name, len(nested.read('terrain2/heights'))))
        finally:
            nested.close()
    return tuple(result)


def inspect_client_map(client_root, map_name):
    root = os.path.abspath(client_root)
    packages = os.path.join(root, 'res', 'packages')
    script_path = os.path.join(packages, 'scripts.pkg')
    map_path = os.path.join(packages, '%s.pkg' % map_name)
    if not os.path.isfile(script_path):
        raise ValueError('scripts.pkg not found: %s' % script_path)
    if not os.path.isfile(map_path):
        raise ValueError('map package not found: %s' % map_path)
    arena_name = 'scripts/arena_defs/%s.xml' % map_name
    with zipfile.ZipFile(script_path, 'r') as scripts:
        if arena_name not in scripts.namelist():
            raise ValueError('arena definition missing from scripts.pkg: %s' % arena_name)
        arena_data = scripts.read(arena_name)
        bases = ctf_bases(arena_data)
        spawns = ctf_spawn_points(arena_data)
        bounds = arena_bounds(arena_data)
        destructibles_member = 'scripts/destructibles.xml'
        if destructibles_member not in scripts.namelist():
            raise ValueError('destructibles.xml missing from scripts.pkg')
        destructibles_size = len(scripts.read(destructibles_member))
    with zipfile.ZipFile(map_path, 'r') as package:
        space_name = 'spaces/%s/space.bin' % map_name
        if space_name not in package.namelist():
            raise ValueError('compiled space missing: %s' % space_name)
        chunks = processed_height_chunks(package, map_name)
        if not chunks:
            raise ValueError('no usable .cdata_processed terrain blocks')
        space_data = package.read(space_name)
        metadata = describe_space(space_data)
        metadata['scene_indices'] = decode_scene_indices(space_data)
    metadata.update({
        'game_version': GAME_VERSION,
        'map': map_name,
        'ctf_bases': [list(base) for base in bases],
        'ctf_spawn_points': [[list(point) for point in team] for team in spawns],
        'bounding_box': list(bounds),
        'destructibles_bytes': destructibles_size,
        'processed_height_chunks': {
            'count': len(chunks),
            'coordinates': [list(location) for location, unused, unused_size in chunks],
            'height_payload_bytes': sum(size for unused, unused_name, size in chunks),
        },
    })
    return metadata


def require_safe_bake(client_root, map_name):
    """Validate every discovered input, then fail until safety decoders exist."""
    result = inspect_client_map(client_root, map_name)
    map_path = os.path.join(os.path.abspath(client_root), 'res', 'packages',
                            '%s.pkg' % map_name)
    with zipfile.ZipFile(map_path, 'r') as package:
        space = CompiledSpace(package.read('spaces/%s/space.bin' % map_name))
    space.require_safe_navigation_sources()
    return result


def _legacy_baker():
    """Load the proven 0.8.2 graph/raster implementation without copying it.

    The target adapter deliberately supplies only #1513 resource readers.  All
    clearance, grade, reversible-link and base-connectivity invariants remain
    the mature baker's code rather than a second reimplementation.
    """
    spec = importlib.util.spec_from_file_location('_offline_lan_082_baker',
                                                  LEGACY_BAKER)
    if spec is None or spec.loader is None:
        raise ValueError('mature 0.8.2 baker not found: %s' % LEGACY_BAKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target_tactical_config(legacy, map_name, bases):
    """Load the #1513 tactical registry without importing BigWorld runtime."""
    package_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src',
                               'res', 'scripts', 'client', 'gui', 'mods',
                               'offline_lan_0922')
    package_names = ('gui', 'gui.mods', 'gui.mods.offline_lan_0922',
                     'gui.mods.offline_lan_0922.ai')
    saved = dict((name, sys.modules.get(name)) for name in package_names)
    loaded = []
    try:
        paths = {
            'gui.mods.offline_lan_0922': package_dir,
            'gui.mods.offline_lan_0922.ai': os.path.join(package_dir, 'ai'),
        }
        for name in package_names:
            module = type(sys)(name)
            module.__path__ = [paths[name]] if name in paths else []
            sys.modules[name] = module
        name = 'gui.mods.offline_lan_0922.ai.maps'
        spec = importlib.util.spec_from_file_location(name,
            os.path.join(package_dir, 'ai', 'maps.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loaded.append(name)
        spec.loader.exec_module(module)
        tactical = module.TACTICAL_MAPS.get(map_name)
        if tactical is None:
            raise ValueError('no #1513 tactical routes for %s' % map_name)
        config = legacy._bake_map_config(tactical)
        if map_name == '100_thepit':
            # The original coarse sketch put rim_west behind the western city
            # collision and projected to a 164-degree hairpin.  Keep the lane
            # distinct, but move its three soft holds onto the validated west
            # road.  This affects only the baked artifact; runtime code stays
            # unchanged and the mature route validator remains authoritative.
            corrected = []
            # Do not retain the coarse sketch's objective endpoints here.
            # The Pit's verified one-way ramp ingress selects safer main-graph
            # anchors after this config is built; the mature route orienter
            # adds those exact anchors.  Keeping both endpoint pairs made the
            # projected corridor visit the same ramp node twice and generated
            # a false 166-degree reversal.
            west_points = ((-70.0, -90.0, True),
                           (-70.0, 0.0, True),
                           (-70.0, 90.0, True))
            for route in config['routes']:
                route = dict(route)
                if route['id'] == 'rim_west':
                    route['points'] = (west_points if route['team'] == 1
                                       else tuple(reversed(west_points)))
                corrected.append(route)
            config['routes'] = tuple(corrected)
            config['anchors'] = tuple(
                (point[0], point[1])
                for route in config['routes']
                for point in route['points']) + tuple(bases)
        config['bases'] = tuple(bases)
        return config
    finally:
        for name in list(sys.modules):
            if name.startswith('gui.mods.offline_lan_0922.ai.maps'):
                sys.modules.pop(name, None)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class PackageVFS(object):
    """Case-insensitive, deterministic view over #1513 package resources."""
    def __init__(self, packages_dir):
        self.packages_dir = packages_dir
        self.index = {}
        self.archives = {}
        for filename in sorted(os.listdir(packages_dir)):
            if not filename.endswith('.pkg'):
                continue
            path = os.path.join(packages_dir, filename)
            try:
                with zipfile.ZipFile(path, 'r') as archive:
                    for member in archive.namelist():
                        self.index.setdefault(member.lower(), (filename, member))
            except zipfile.BadZipFile:
                continue

    def close(self):
        for archive in self.archives.values():
            archive.close()
        self.archives = {}

    def read(self, name):
        entry = self.index.get(name.lower())
        if entry is None:
            raise KeyError(name)
        filename, member = entry
        archive = self.archives.get(filename)
        if archive is None:
            archive = zipfile.ZipFile(os.path.join(self.packages_dir, filename), 'r')
            self.archives[filename] = archive
        return archive.read(member)


def _vertex_positions_0922(section):
    """Decode #1513 static positions; attributes after xyz are intentionally ignored."""
    vertex_type = section[:64].split(b'\0', 1)[0].decode('ascii')
    count = struct.unpack_from('<I', section, 64)[0]
    position = 68
    if vertex_type.startswith('BPVT'):
        vertex_type = section[position:position + 64].split(b'\0', 1)[0].decode('ascii')
        count = struct.unpack_from('<I', section, position + 64)[0]
        position += 68
    # 0.9.22's set3 formats add packed colour/skin attributes but preserve xyz
    # as three little-endian floats at the start of every static vertex.
    strides = {
        'xyznuv': 24, 'xyznuvtb': 32,
        'set3/xyznuvpc': 24, 'set3/xyznuvtbpc': 32,
    }
    stride = strides.get(vertex_type)
    if stride is None:
        raise ValueError('unsupported #1513 static vertex type %s' % vertex_type)
    if position + count * stride > len(section):
        raise ValueError('truncated #1513 vertex section')
    return [struct.unpack_from('<fff', section, position + index * stride)
            for index in range(count)]


def _transform_0922(matrix, point):
    if len(matrix) != 16:
        raise ValueError('invalid #1513 static transform')
    x, y, z = point
    return (matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
            matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
            matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14])


def _compiled_models(space_data):
    if VENDOR_ROOT not in sys.path:
        sys.path.insert(0, VENDOR_ROOT)
    from wot_space_bin_utils import CompiledSpace as VendorCompiledSpace
    from wot_space_bin_utils.universal_space import UniversalSpace
    compiled = VendorCompiledSpace(io.BytesIO(space_data), '0.9.22.0.1', 'RU',
                                   ['BWST', 'BWT2', 'BSMI', 'BSMO', 'BSMA',
                                    'BWSG', 'BSGD', 'BWWa', 'WTCP'])
    return UniversalSpace._from_compiled_space(compiled), compiled


def _processed_primitives_name(strings, render):
    """Return the processed primitive archive used by ``UniversalSpace``."""
    name = strings.get(render['prims_name_fnv'])
    if not name or '/' not in name:
        raise UnsafeBakeInputError(
            'BSMO render has no resolvable primitive resource')
    return name[:name.rindex('/')].replace(
        '.primitives', '.primitives_processed')


def compiled_soft_destructible_instances(compiled):
    """Identify the falling/fragile instances skipped by the mature baker.

    The 0.8.2 input path gets this classification from ``destructibles.xml``.
    Compiled #1513 spaces already resolve the same classification per BSMO
    model: type 1 is falling, type 2 is fragile, and type 3 is a solid
    structure.  Preserve structures and return exact primitive/transform keys
    so a shared render resource does not make unrelated placements soft.
    """
    bsmo = compiled.sections['BSMO']._data
    bsmi = compiled.sections['BSMI']
    strings = compiled.sections['BWST']
    transforms = bsmi._data['transforms']
    model_ids = list(bsmi.model_ids())
    if len(transforms) != len(model_ids):
        raise UnsafeBakeInputError(
            'BSMI model ids do not match static transforms')
    model_infos = bsmo['model_info_items']
    loddings = bsmo['models_loddings']
    lod_renders = bsmo['lod_renders']
    renders = bsmo['renders']
    keys = set()
    counts = {'falling': 0, 'fragile': 0, 'structures_preserved': 0}
    for transform, model_id in zip(transforms, model_ids):
        if model_id < 0 or model_id >= len(model_infos):
            raise UnsafeBakeInputError('BSMI references an invalid BSMO model')
        model_type = int(model_infos[model_id]['type'])
        if model_type == 3:
            counts['structures_preserved'] += 1
            continue
        if model_type not in (1, 2):
            continue
        counts['falling' if model_type == 1 else 'fragile'] += 1
        if model_id >= len(loddings):
            raise UnsafeBakeInputError('BSMO model has no LOD record')
        lod_id = int(loddings[model_id]['lod_begin'])
        if lod_id < 0 or lod_id >= len(lod_renders):
            raise UnsafeBakeInputError('BSMO model has an invalid LOD record')
        first = int(lod_renders[lod_id]['render_set_begin'])
        last = int(lod_renders[lod_id]['render_set_end'])
        if first < 0 or last < first or last >= len(renders):
            raise UnsafeBakeInputError('BSMO model has an invalid render range')
        for render in renders[first:last + 1]:
            keys.add((_processed_primitives_name(strings, render),
                      tuple(float(value) for value in transform)))
    counts['primitive_transform_keys'] = len(keys)
    return keys, counts


def compiled_local_obstacle_instances(compiled, maximum_height):
    """Identify low compiled collision shapes ignored by the mature baker."""
    bsmo = compiled.sections['BSMO']._data
    bsmi = compiled.sections['BSMI']
    strings = compiled.sections['BWST']
    transforms = bsmi._data['transforms']
    model_ids = list(bsmi.model_ids())
    if len(transforms) != len(model_ids):
        raise UnsafeBakeInputError(
            'BSMI model ids do not match static transforms')
    colliders = bsmo['models_colliders']
    loddings = bsmo['models_loddings']
    lod_renders = bsmo['lod_renders']
    renders = bsmo['renders']
    keys = set()
    low_instances = 0
    for transform, model_id in zip(transforms, model_ids):
        if model_id < 0 or model_id >= len(colliders):
            raise UnsafeBakeInputError('BSMI references an invalid BSMO collider')
        collider = colliders[model_id]
        minimum = collider['collision_bounds_min']
        maximum = collider['collision_bounds_max']
        if len(minimum) != 3 or len(maximum) != 3:
            raise UnsafeBakeInputError('BSMO collider has invalid local bounds')
        local_height = float(maximum[1]) - float(minimum[1])
        if not math.isfinite(local_height) or local_height < 0.0:
            raise UnsafeBakeInputError('BSMO collider has invalid local height')
        if local_height > float(maximum_height):
            continue
        low_instances += 1
        if model_id >= len(loddings):
            raise UnsafeBakeInputError('BSMO model has no LOD record')
        lod_id = int(loddings[model_id]['lod_begin'])
        if lod_id < 0 or lod_id >= len(lod_renders):
            raise UnsafeBakeInputError('BSMO model has an invalid LOD record')
        first = int(lod_renders[lod_id]['render_set_begin'])
        last = int(lod_renders[lod_id]['render_set_end'])
        if first < 0 or last < first or last >= len(renders):
            raise UnsafeBakeInputError('BSMO model has an invalid render range')
        for render in renders[first:last + 1]:
            keys.add((_processed_primitives_name(strings, render),
                      tuple(float(value) for value in transform)))
    return keys, {
        'instances': low_instances,
        'primitive_transform_keys': len(keys),
        'maximum_local_height': float(maximum_height),
    }


def _raster_compiled_collision_instance(obstacles, model_name, triangles,
                                        legacy):
    """Raster one compiled instance with the mature bridge-deck semantics."""
    bridge_deck = set()
    if legacy._is_bridge_model(model_name):
        obstacles.bridge_instance_count += 1
        bridge_deck = obstacles._bridge_deck_triangles(triangles)
    for triangle in triangles:
        if id(triangle) in bridge_deck:
            obstacles._raster_surface_triangle(triangle)
            obstacles.bridge_surface_triangle_count += 1
        else:
            obstacles._raster_triangle(triangle)


def _collision_linkage_summary(compiled):
    """Expose the BSMO collider -> BSP material table used by this bake."""
    bsmo = compiled.sections['BSMO']._data
    bwst = compiled.sections['BWST']
    colliders = bsmo['models_colliders']
    materials = bsmo['bsp_material_kinds']
    linked = 0
    ranges = 0
    flags = set()
    for collider in colliders:
        if bwst.get(collider['bsp_section_name_fnv']):
            linked += 1
        first = int(collider['bsp_material_kind_begin'])
        last = int(collider['bsp_material_kind_end'])
        if first <= last and last < len(materials):
            ranges += 1
            flags.update(int(item['flags']) for item in materials[first:last + 1])
    return {
        'model_colliders': len(colliders),
        'collider_resources_resolved': linked,
        'collider_material_ranges': ranges,
        'material_kind_records': len(materials),
        'material_flag_values': sorted(flags),
    }


def bwwa_regions(records, cells):
    """Associate BWWa records to their half-open ``[start_id, end_id)`` cells."""
    result = []
    for record in records:
        first, last = int(record['start_id']), int(record['end_id'])
        if first < 0 or last < first or last > len(cells):
            raise UnsafeBakeInputError('BWWa record has invalid water-cell range')
        result.extend((record, cell) for cell in cells[first:last])
    return result


def bwwa_world_regions(records, cells):
    """Transform record-local BWWa cell rectangles into world-space bounds."""
    result = []
    for record, cell in bwwa_regions(records, cells):
        position = record['position']
        angle = float(record.get('orientation', 0.0))
        cosine, sine = math.cos(angle), math.sin(angle)
        corners = []
        for local_x in (float(cell[0]), float(cell[3])):
            for local_z in (float(cell[2]), float(cell[5])):
                corners.append((float(position[0]) + local_x * cosine - local_z * sine,
                                float(position[2]) + local_x * sine + local_z * cosine))
        result.append((record, (min(point[0] for point in corners), float(cell[1]),
                                min(point[1] for point in corners), max(point[0] for point in corners),
                                float(cell[4]), max(point[1] for point in corners))))
    return result


def bwwa_contains(record, cell, x, z):
    """Exact water-cell predicate; do not use the rotated world AABB here."""
    angle = float(record.get('orientation', 0.0))
    cosine, sine = math.cos(angle), math.sin(angle)
    dx, dz = float(x) - float(record['position'][0]), float(z) - float(record['position'][2])
    local_x = dx * cosine + dz * sine
    local_z = -dx * sine + dz * cosine
    return (float(cell[0]) <= local_x <= float(cell[3]) and
            float(cell[2]) <= local_z <= float(cell[5]))


def _graph_cell(graph, point):
    origin_x, origin_z = graph['origin']
    size = float(graph['cell_size'])
    return (int(math.floor((float(point[0]) - origin_x) / size + 0.5)),
            int(math.floor((float(point[1]) - origin_z) / size + 0.5)))


def _graph_index(graph, cell):
    x, z = cell
    if x < 0 or x >= graph['width'] or z < 0 or z >= graph['height']:
        return None
    return z * graph['width'] + x


def _graph_cell_point(graph, cell):
    return (graph['origin'][0] + cell[0] * graph['cell_size'],
            graph['origin'][1] + cell[1] * graph['cell_size'])


def _grid_line(start, end):
    """Return an ordered 8-connected integer-cell line including both ends."""
    x, z = start
    target_x, target_z = end
    dx, dz = abs(target_x - x), abs(target_z - z)
    step_x = 1 if x < target_x else -1
    step_z = 1 if z < target_z else -1
    error = dx - dz
    result = [(x, z)]
    while x != target_x or z != target_z:
        twice = error * 2
        if twice > -dz:
            error -= dz
            x += step_x
        if twice < dx:
            error += dx
            z += step_z
        result.append((x, z))
    return tuple(result)


def _probe_downhill_ingress(graph, terrain, obstacles, stock_spawn,
                            enemy_base, main_nodes, target_cell, legacy):
    """Prove one collision-free, water-free downhill line into ``main_nodes``."""
    start_cell = _graph_cell(graph, stock_spawn)
    cells = list(_grid_line(start_cell, target_cell))
    # The first retained cell is the real entry. Anything after it belongs to
    # the already validated reversible graph and must not become one-way.
    for index, cell in enumerate(cells):
        flat = _graph_index(graph, cell)
        if flat in main_nodes:
            cells = cells[:index + 1]
            break
    if len(cells) < 2:
        return None

    forward_x = float(enemy_base[0]) - float(stock_spawn[0])
    forward_z = float(enemy_base[1]) - float(stock_spawn[1])
    forward_length = math.hypot(forward_x, forward_z)
    if forward_length <= 0.001:
        return None
    forward_x, forward_z = forward_x / forward_length, forward_z / forward_length
    heights = []
    for cell in cells:
        x, z = _graph_cell_point(graph, cell)
        ground = legacy._ground_height(terrain, obstacles, x, z)
        if ground is None or not math.isfinite(float(ground)):
            return None
        if terrain.water_depth(x, z, ground) > legacy.WATER_DEPTH_LIMIT:
            return None
        if obstacles.blocked(x, z, ground, margin=legacy.VEHICLE_HALF_WIDTH):
            return None
        heights.append(float(ground))

    maximum_downhill_grade = 0.0
    for index, (first, second) in enumerate(zip(cells, cells[1:])):
        dx, dz = second[0] - first[0], second[1] - first[1]
        if (dx, dz) not in legacy.DIRECTIONS:
            return None
        world_dx = dx * graph['cell_size']
        world_dz = dz * graph['cell_size']
        if world_dx * forward_x + world_dz * forward_z <= 0.0:
            return None
        # This exception exists only for stock starts placed on a smooth ramp.
        # It may descend into the reversible graph, never climb or traverse it
        # in reverse. A one-millimetre tolerance covers JSON/mm quantisation.
        delta = heights[index + 1] - heights[index]
        if delta > 0.001:
            return None
        run = math.hypot(world_dx, world_dz)
        maximum_downhill_grade = max(maximum_downhill_grade, -delta / run)

    final_index = _graph_index(graph, cells[-1])
    if final_index not in main_nodes:
        return None
    start_x, start_z = _graph_cell_point(graph, cells[0])
    end_x, end_z = _graph_cell_point(graph, cells[-1])
    return {
        'stock_spawn': [round(float(stock_spawn[0]), 3),
                        round(float(stock_spawn[1]), 3)],
        'graph_start': [round(start_x, 3), round(start_z, 3)],
        'main_anchor': [round(end_x, 3), round(end_z, 3)],
        'cells': [list(cell) for cell in cells],
        'cell_indices': [_graph_index(graph, cell) for cell in cells],
        'heights_mm': [int(round(value * 1000.0)) for value in heights],
        'one_way_links': len(cells) - 1,
        'forward_progress_metres': round(
            (end_x - start_x) * forward_x + (end_z - start_z) * forward_z, 3),
        'maximum_downhill_grade': round(maximum_downhill_grade, 5),
        'maximum_uphill_metres': 0.0,
        'water_clear': True,
        'collision_clear': True,
        'direction': 'toward_enemy_base',
        'reversible': False,
    }


def find_downhill_spawn_ingress(graph, terrain, obstacles, stock_spawn,
                                enemy_base, legacy):
    """Choose the nearest proved downhill entry into the retained main graph."""
    main_nodes = set(index for index, height in enumerate(graph['heights_mm'])
                     if height is not None)
    if not main_nodes:
        raise UnsafeBakeInputError('spawn ingress has no retained main graph')
    forward_x = float(enemy_base[0]) - float(stock_spawn[0])
    forward_z = float(enemy_base[1]) - float(stock_spawn[1])
    forward_length = math.hypot(forward_x, forward_z)
    if forward_length <= 0.001:
        raise UnsafeBakeInputError('spawn ingress has no enemy-facing direction')
    candidates = []
    for index in main_nodes:
        point = legacy._node_point(graph, index)
        dx = point[0] - float(stock_spawn[0])
        dz = point[1] - float(stock_spawn[1])
        progress = (dx * forward_x + dz * forward_z) / forward_length
        lateral = abs(dx * forward_z - dz * forward_x) / forward_length
        if progress <= 0.0 or lateral > progress:
            continue
        candidates.append((math.hypot(dx, dz), lateral, index,
                           (index % graph['width'], index // graph['width'])))
    candidates.sort()
    for unused_distance, unused_lateral, unused_index, cell in candidates:
        record = _probe_downhill_ingress(
            graph, terrain, obstacles, stock_spawn, enemy_base,
            main_nodes, cell, legacy)
        if record is not None:
            return record
    raise UnsafeBakeInputError(
        'stock spawn has no collision-free downhill ingress into the main graph')


def install_downhill_spawn_ingress(graph, record, legacy):
    """Install a proved ingress without adding any uphill/reverse edge."""
    cells = [tuple(cell) for cell in record['cells']]
    indices = list(record['cell_indices'])
    heights = list(record['heights_mm'])
    if len(cells) < 2 or len(cells) != len(indices) or len(cells) != len(heights):
        raise UnsafeBakeInputError('invalid downhill spawn ingress record')
    direction_bits = dict((direction, index)
                          for index, direction in enumerate(legacy.DIRECTIONS))
    for index, height in zip(indices[:-1], heights[:-1]):
        graph['heights_mm'][index] = int(height)
        graph['links'][index] = 0
        graph['hazards'][index] &= ~legacy.HAZARD_EDGE
    for first, second, first_index, second_index in zip(
            cells, cells[1:], indices, indices[1:]):
        direction = (second[0] - first[0], second[1] - first[1])
        reverse = (-direction[0], -direction[1])
        graph['links'][first_index] |= 1 << direction_bits[direction]
        graph['links'][second_index] &= ~(1 << direction_bits[reverse])
    return validate_downhill_spawn_ingress(graph, record, legacy)


def validate_downhill_spawn_ingress(graph, record, legacy):
    cells = [tuple(cell) for cell in record['cells']]
    indices = list(record['cell_indices'])
    direction_bits = dict((direction, index)
                          for index, direction in enumerate(legacy.DIRECTIONS))
    maximum_grade = 0.0
    for position, (first, second, first_index, second_index) in enumerate(zip(
            cells, cells[1:], indices, indices[1:])):
        direction = (second[0] - first[0], second[1] - first[1])
        if direction not in direction_bits:
            raise UnsafeBakeInputError('spawn ingress cells are not adjacent')
        forward_bit = 1 << direction_bits[direction]
        reverse_bit = 1 << direction_bits[(-direction[0], -direction[1])]
        if not graph['links'][first_index] & forward_bit:
            raise UnsafeBakeInputError('spawn ingress is not connected forward')
        if graph['links'][second_index] & reverse_bit:
            raise UnsafeBakeInputError('spawn ingress contains an uphill reverse edge')
        first_height = graph['heights_mm'][first_index]
        second_height = graph['heights_mm'][second_index]
        if first_height is None or second_height is None:
            raise UnsafeBakeInputError('spawn ingress has no terrain support')
        delta = int(second_height) - int(first_height)
        if delta > 1:
            raise UnsafeBakeInputError('spawn ingress climbs away from the stock start')
        run = graph['cell_size'] * (math.sqrt(2.0)
                                    if direction[0] and direction[1] else 1.0)
        maximum_grade = max(maximum_grade, -delta / 1000.0 / run)
        if graph['hazards'][first_index] & legacy.HAZARD_WATER:
            raise UnsafeBakeInputError('spawn ingress crosses deep water')
    if abs(maximum_grade - float(record['maximum_downhill_grade'])) > 0.001:
        raise UnsafeBakeInputError('spawn ingress slope metadata does not match graph')
    return {
        'cells': len(cells),
        'one_way_links': len(cells) - 1,
        'maximum_downhill_grade': round(maximum_grade, 5),
        'forward_connected': True,
        'reverse_links_absent': True,
    }


def expand_stationary_routes(graph, routes, bases, legacy):
    """Turn a tactical hold anchor into a graph-connected locomotion route.

    Himmelsdorf's mature ``rear_guard`` annotation is intentionally one point:
    it describes an artillery hold, not the drive from spawn.  A baked route is
    a different contract and must contain that drive.  Join the team's safe
    spawn anchor to the projected hold over validated graph links instead of
    shipping a one-point route or weakening the release gate.
    """
    for team in (1, 2):
        team_routes = routes.get(str(team), ())
        for route in team_routes:
            waypoints = route.get('waypoints') or ()
            if len(waypoints) != 1:
                continue
            start, unused_start_offset = legacy._nearest_node(
                graph, bases[team - 1])
            goal, unused_goal_offset = legacy._nearest_node(
                graph, waypoints[0])
            if start is None or goal is None:
                raise ValueError(
                    'stationary route has no retained navigation node')
            if start == goal:
                mask = int(graph['links'][start])
                x_index = start % graph['width']
                z_index = start // graph['width']
                goal = None
                for direction_index, (dx, dz) in enumerate(legacy.DIRECTIONS):
                    if not mask & (1 << direction_index):
                        continue
                    nx, nz = x_index + dx, z_index + dz
                    if (0 <= nx < graph['width'] and
                            0 <= nz < graph['height']):
                        goal = nz * graph['width'] + nx
                        break
                if goal is None:
                    raise ValueError(
                        'stationary route spawn anchor has no connected edge')
            path, unused_distance = legacy._graph_path(graph, start, goal)
            if len(path) < 2:
                raise ValueError(
                    'stationary route cannot connect its spawn and hold nodes')
            hold_nodes = set([goal]) if bool(waypoints[0][2]) else set()
            route['waypoints'] = legacy._sample_route_path(
                graph, path, hold_nodes, 16, set((start, goal)))
            if len(route['waypoints']) < 2:
                raise ValueError('stationary route expansion is incomplete')
    return routes


SPAWN_COLUMNS = (0, -1, 1, -2, 2)
SPAWN_ROW_DEPTHS = (20.0, 36.0, 52.0)
SPAWN_LATERAL_SPACING = 14.0
SPAWN_MAXIMUM_PROJECTION = 32.0
SPAWN_MINIMUM_SPACING = 10.5


def bake_spawn_formations(graph, anchors, map_name):
    """Emit two complete formations or reject the entire map bake.

    The runtime is intentionally not allowed to invent, project or nudge a
    slot.  This offline step selects 15 open nodes near each CTF home, records
    their exact height and facing, and proves the full formation before it is
    published.
    """
    if len(anchors) != 2:
        raise UnsafeBakeInputError('%s: two CTF spawn anchors are required' %
                                   map_name)
    origin_x, origin_z = graph['origin']
    width = int(graph['width'])
    cell_size = float(graph['cell_size'])
    heights = graph['heights_mm']
    hazards = graph['hazards']
    links = graph['links']
    nodes = []
    for index, height in enumerate(heights):
        if (height is None or int(hazards[index]) != 0 or
                bin(int(links[index])).count('1') < 3):
            continue
        nodes.append((
            index,
            float(origin_x) + (index % width) * cell_size,
            float(height) / 1000.0,
            float(origin_z) + (index // width) * cell_size,
        ))
    if not nodes:
        raise UnsafeBakeInputError('%s: no open spawn nodes exist' % map_name)

    formations = {'1': [], '2': []}
    projections = []
    all_selected = []
    for team in (1, 2):
        anchor_x, anchor_z = anchors[team - 1]
        enemy_x, enemy_z = anchors[2 - team]
        delta_x = float(enemy_x) - float(anchor_x)
        delta_z = float(enemy_z) - float(anchor_z)
        if abs(delta_x) + abs(delta_z) < 0.001:
            raise UnsafeBakeInputError(
                '%s: CTF spawn anchors overlap' % map_name)
        yaw = math.atan2(delta_x, delta_z)
        selected = []
        for row_depth in SPAWN_ROW_DEPTHS:
            for column in SPAWN_COLUMNS:
                lateral = float(column) * SPAWN_LATERAL_SPACING
                desired_x = (float(anchor_x) + math.sin(yaw) * row_depth +
                             math.cos(yaw) * lateral)
                desired_z = (float(anchor_z) + math.cos(yaw) * row_depth -
                             math.sin(yaw) * lateral)
                candidates = sorted(
                    nodes,
                    key=lambda value: (
                        (value[1] - desired_x) ** 2 +
                        (value[3] - desired_z) ** 2,
                        -bin(int(links[value[0]])).count('1'), value[0]))
                chosen = None
                chosen_projection = None
                for candidate in candidates:
                    projection = math.hypot(
                        candidate[1] - desired_x,
                        candidate[3] - desired_z)
                    if projection > SPAWN_MAXIMUM_PROJECTION:
                        break
                    if any(math.hypot(
                            candidate[1] - other[1],
                            candidate[3] - other[3]) < SPAWN_MINIMUM_SPACING
                           for other in selected):
                        continue
                    chosen = candidate
                    chosen_projection = projection
                    break
                if chosen is None:
                    raise UnsafeBakeInputError(
                        '%s: team %d slot %d has no validated spawn node '
                        'within %.1f m' %
                        (map_name, team, len(selected),
                         SPAWN_MAXIMUM_PROJECTION))
                selected.append(chosen)
                all_selected.append((team, len(selected) - 1, chosen))
                projections.append(chosen_projection)
                formations[str(team)].append([
                    round(chosen[1], 4), round(chosen[2], 4),
                    round(chosen[3], 4), round(yaw, 6),
                ])

    same_team_distances = []
    other_team_distances = []
    for index, (team, unused_slot, point) in enumerate(all_selected):
        for other_team, unused_other_slot, other in all_selected[index + 1:]:
            distance = math.hypot(point[1] - other[1], point[3] - other[3])
            if team == other_team:
                same_team_distances.append(distance)
            else:
                other_team_distances.append(distance)
    minimum_spacing = min(same_team_distances)
    if minimum_spacing < SPAWN_MINIMUM_SPACING:
        raise UnsafeBakeInputError(
            '%s: baked spawn slots are only %.2f m apart' %
            (map_name, minimum_spacing))
    minimum_team_separation = min(other_team_distances)
    if minimum_team_separation < 80.0:
        raise UnsafeBakeInputError(
            '%s: opposing spawn formations are only %.2f m apart' %
            (map_name, minimum_team_separation))
    return formations, {
        'spawn_slots_per_team': 15,
        'spawn_minimum_spacing_metres': round(minimum_spacing, 3),
        'spawn_minimum_team_separation_metres': round(
            minimum_team_separation, 3),
        'spawn_maximum_projection_metres': round(max(projections), 3),
    }


def bake_map_graph(client_root, map_name, output=None, cell_size=4.0):
    """Bake a #1513 CTF map with compiled BSP collision and BWWa water cells."""
    if os.path.basename(LEGACY_BAKER) != 'bake_navigation.py':
        raise AssertionError('unexpected mature baker path')
    inspection = inspect_client_map(client_root, map_name)
    packages = os.path.join(os.path.abspath(client_root), 'res', 'packages')
    vfs = PackageVFS(packages)
    legacy = _legacy_baker()
    map_path = os.path.join(packages, map_name + '.pkg')
    try:
        with zipfile.ZipFile(map_path, 'r') as package:
            space_data = package.read('spaces/%s/space.bin' % map_name)
            terrain_payloads = []
            for coordinates, name, unused_size in processed_height_chunks(package, map_name):
                nested = zipfile.ZipFile(io.BytesIO(package.read(name)), 'r')
                try:
                    terrain_payloads.append((coordinates, nested.read('terrain2/heights')))
                finally:
                    nested.close()
        world, compiled = _compiled_models(space_data)
        soft_instance_keys, soft_destructible_counts = \
            compiled_soft_destructible_instances(compiled)
        local_instance_keys, local_obstacle_counts = \
            compiled_local_obstacle_instances(
                compiled, legacy.LOCAL_OBSTACLE_MAX_HEIGHT)
        water_records = compiled.sections['BWWa']._data['1']
        water_cells = compiled.sections['BWWa']._data['2']
        if water_records and not water_cells:
            raise UnsafeBakeInputError('BWWa water records have no bounded water cells')
        water_regions = bwwa_regions(water_records, water_cells)

        class TargetHeightChunk(object):
            def __init__(self, payload):
                if len(payload) < 36 or payload[:4] != b'hmp\0':
                    raise ValueError('invalid #1513 terrain height payload')
                width, height = struct.unpack_from('<II', payload, 4)
                png_width, png_height, rows = legacy.decode_png_rgba(payload[36:])
                if (width, height) != (png_width, png_height) or width < 5:
                    raise ValueError('invalid #1513 terrain height dimensions')
                self.width, self.height = width, height
                self.inner = width - 5  # #1513: 32 samples plus two-pixel borders.
                if self.inner <= 0:
                    raise ValueError('invalid #1513 terrain height border')
                self.values = [[struct.unpack('<i', bytes(row[index * 4:index * 4 + 4]))[0] / 1000.0
                                for index in range(width)] for row in rows]
            def sample(self, local_x, local_z):
                ix = 2.0 + float(local_x) * self.inner / 100.0
                iz = 2.0 + float(local_z) * self.inner / 100.0
                ix = max(0.0, min(self.width - 1.0, ix))
                iz = max(0.0, min(self.height - 1.0, iz))
                x0, z0 = int(math.floor(ix)), int(math.floor(iz))
                x1, z1 = min(self.width - 1, x0 + 1), min(self.height - 1, z0 + 1)
                fx, fz = ix - x0, iz - z0
                lower = self.values[z0][x0] * (1.0 - fx) + self.values[z0][x1] * fx
                upper = self.values[z1][x0] * (1.0 - fx) + self.values[z1][x1] * fx
                return lower * (1.0 - fz) + upper * fz

        class TargetTerrain(object):
            def __init__(self):
                self.chunks = dict((coordinates, TargetHeightChunk(payload))
                                   for coordinates, payload in terrain_payloads)
                self.waters = ()
            def height(self, x, z):
                cx = int(math.floor(float(x) / 100.0))
                cz = int(math.floor(float(z) / 100.0))
                chunk = self.chunks.get((cx, cz))
                return None if chunk is None else chunk.sample(x - cx * 100.0,
                                                               z - cz * 100.0)
            def water_depth(self, x, z, height=None):
                # ``BWWa/2`` maps each water record to its bounded 100m cells.
                # It is a coverage index, not a flat deep-water mask: stock
                # Tundra deliberately covers the whole space at water_y=-.289.
                # The record's water surface minus decoded terrain is the
                # authoritative depth used by the mature thresholds below.
                # Unparsed .odata can only refine alpha/edge visuals; it never
                # grants a path through a positive-depth BWWa cell.
                ground = float(height) if height is not None else self.height(x, z)
                if ground is None:
                    return 0.0
                depth = 0.0
                for record, cell in water_regions:
                    if bwwa_contains(record, cell, x, z):
                        depth = max(depth, float(record['position'][1]) - ground)
                return max(0.0, depth)

        class TargetObstacles(legacy.ObstacleField):
            def __init__(self):
                self.raster_size = 1.0
                self.cells = {}
                self.surface_cells = {}
                self.instance_count = 0
                self.bridge_instance_count = 0
                self.bridge_surface_triangle_count = 0
                self.soft_instance_count = 0
                self.local_instance_count = 0
                self.skipped = 0
                self.model_library = type('LibraryStats', (), {'cache': {}})()
                self.unsupported = []
                self.invalid_triangles = 0
                self._load_target()
            def _load_target(self):
                for model in world.models:
                    try:
                        sections = legacy._primitive_sections(vfs.read(model.prims_name))
                        # BSP2 is the authored collision mesh, not a render proxy.
                        # Prefer it whenever present; its triangles are already local
                        # positions and do not depend on render-set index offsets.
                        if 'bsp2' in sections:
                            try:
                                triangles = legacy._bsp_triangles(sections['bsp2'], (), {})
                            except ValueError:
                                triangles = None
                            if triangles is not None:
                                mesh_triangles = [triangles]
                            else:
                                mesh_triangles = []
                        else:
                            mesh_triangles = []
                        if not mesh_triangles:
                            vertices = _vertex_positions_0922(sections[model.verts_dataname])
                            primitives, groups = legacy._index_groups(sections[model.prims_dataname])
                            mesh_triangles = []
                            for mesh_group in model.instances:
                                for mesh in mesh_group.meshes:
                                    if mesh.pg_idx < 0 or mesh.pg_idx >= len(groups):
                                        raise ValueError('primitive group %s' % mesh.pg_idx)
                                    primitive_offset, primitive_count, vertex_offset, vertex_count = groups[mesh.pg_idx]
                                    local_vertices = vertices[vertex_offset:vertex_offset + vertex_count]
                                    selected = []
                                    for primitive in primitives[primitive_offset:primitive_offset + primitive_count]:
                                        if max(primitive) >= len(local_vertices):
                                            raise ValueError('invalid primitive index')
                                        selected.append(tuple(local_vertices[index] for index in primitive))
                                    mesh_triangles.append(selected)
                    except (KeyError, ValueError, struct.error, zipfile.BadZipFile) as error:
                        self.unsupported.append('%s: %s' % (model.prims_name, error))
                        # This is the old baker's narrow fallback policy: a
                        # malformed *render* group without a usable BSP2 mesh
                        # is omitted, while every successfully decoded BSP2
                        # collision mesh remains authoritative.  The count is
                        # emitted and is a review gate, never silently hidden.
                        self.skipped += 1
                        continue
                    self.model_library.cache[model.prims_name] = True
                    for instance_group in model.instances:
                        for triangles in mesh_triangles:
                            for transform in instance_group.transforms:
                                instance_key = (
                                    model.prims_name,
                                    tuple(float(value) for value in transform))
                                if instance_key in soft_instance_keys:
                                    self.soft_instance_count += 1
                                    continue
                                if instance_key in local_instance_keys:
                                    self.local_instance_count += 1
                                    continue
                                self.instance_count += 1
                                world_triangles = []
                                for triangle in triangles:
                                    world_triangle = tuple(_transform_0922(transform, point)
                                                           for point in triangle)
                                    if not all(math.isfinite(value) for point in world_triangle
                                               for value in point):
                                        # BWSG/BSP2 contains sentinel triangles for
                                        # disabled collision pages.  They have no
                                        # spatial extent and are not geometry a tank
                                        # could collide with.
                                        self.invalid_triangles += 1
                                        continue
                                    world_triangles.append(world_triangle)
                                _raster_compiled_collision_instance(
                                    self, model.prims_name, world_triangles,
                                    legacy)

        terrain = TargetTerrain()
        obstacles = TargetObstacles()
        bases = tuple(tuple(point) for point in inspection['ctf_bases'])
        stock_spawns = tuple(tuple(team) for team in inspection['ctf_spawn_points'])
        navigation_starts = tuple((team[0] if team else bases[index])
                                  for index, team in enumerate(stock_spawns))
        # The flags select the fully reversible main component. Stock spawn
        # ramps are validated and attached separately below; using their raw
        # coordinates here would force the mature component selector to treat
        # a deliberate one-way descent as an ordinary two-way route.
        config = _target_tactical_config(legacy, map_name, bases)
        # BWT2 describes all packaged terrain, including non-playable scenery.
        # Component selection must use the stock arena rectangle or a large
        # peripheral terrain island can defeat the objective-serving graph.
        # First prove that the arena rectangle is actually backed by BWT2.
        terrain_bounds = world.terrain.bounds
        terrain_world_bounds = (
            terrain_bounds[0] * 100.0,
            terrain_bounds[2] * 100.0,
            (terrain_bounds[1] + 1) * 100.0,
            (terrain_bounds[3] + 1) * 100.0,
        )
        arena_world_bounds = tuple(float(value)
                                   for value in inspection['bounding_box'])
        if (arena_world_bounds[0] < terrain_world_bounds[0] or
                arena_world_bounds[1] < terrain_world_bounds[1] or
                arena_world_bounds[2] > terrain_world_bounds[2] or
                arena_world_bounds[3] > terrain_world_bounds[3]):
            raise UnsafeBakeInputError(
                'stock arena boundingBox is outside compiled terrain bounds')
        config['bounds'] = arena_world_bounds
        original_maps, original_game_version = legacy.MAPS, legacy.GAME_VERSION
        original_format_name, original_format_version = legacy.FORMAT_NAME, legacy.FORMAT_VERSION
        try:
            legacy.MAPS = {map_name: config}
            legacy.GAME_VERSION = GAME_VERSION
            legacy.FORMAT_NAME = 'offline-lan-0922-navgraph'
            legacy.FORMAT_VERSION = 2
            original_terrain, original_obstacles = legacy.Terrain, legacy.ObstacleField
            original_validate = legacy.validate_graph
            original_bake_routes = legacy.bake_tactical_routes
            original_retain = legacy.retain_base_component
            legacy.Terrain, legacy.ObstacleField = (lambda unused_resources, unused_map: terrain), (lambda unused_resources, unused_map, soft_models=None: obstacles)
            ingress_records = {}
            # The stock CTF XML has no teamSpawnPoints: the client forms a
            # safe line around the flag.  Retain the *real* flags while choosing
            # the component (the mature baker's 56m objective reach check), then
            # use the first safe graph cell as the formation anchor.  This is
            # intentionally not a relaxation of validation: the second pass
            # below validates both safe anchors at <= 12m and their complete
            # two-way path.
            def safe_config(graph):
                anchors = []
                for index, start in enumerate(navigation_starts):
                    # The Pit is the verified #1513 exception: both stock
                    # starts sit atop a smooth ramp whose inward descent is
                    # steeper than the mature graph's reversible 0.38 limit.
                    # Prove that one-way entry instead of weakening every map.
                    if map_name == '100_thepit' and stock_spawns[index]:
                        team = index + 1
                        record = ingress_records.get(team)
                        if record is None:
                            record = find_downhill_spawn_ingress(
                                graph, terrain, obstacles, start,
                                bases[1 - index], legacy)
                            ingress_records[team] = record
                        anchors.append(tuple(record['main_anchor']))
                    else:
                        node, offset = legacy._nearest_node(
                            graph, start, max_distance=56.0)
                        if node is None:
                            raise UnsafeBakeInputError(
                                'CTF objective has no reachable safe spawn anchor')
                        anchors.append(legacy._node_point(graph, node))
                adjusted = dict(config)
                adjusted['bases'] = tuple(anchors)
                adjusted['anchors'] = tuple(anchors) + tuple(config.get('anchors', ()))
                return adjusted
            def bake_routes(graph, unused_config):
                adjusted = safe_config(graph)
                routes = original_bake_routes(graph, adjusted)
                return expand_stationary_routes(
                    graph, routes, adjusted['bases'], legacy)
            legacy.bake_tactical_routes = bake_routes
            legacy.validate_graph = lambda graph, unused_config: original_validate(graph, safe_config(graph))
            # Lakeville's decoded water volumes intentionally split off the
            # inaccessible lake-side scenery.  The base-serving component is
            # 59.3% (not a tiny shortcut); keep a conservative majority guard
            # while preserving the old validator's post-prune single-component
            # and route-connectivity checks.
            legacy.retain_base_component = lambda graph, unused_config: original_retain(
                graph, config, minimum_fraction=0.55)
            graph = legacy.bake_graph(None, map_name, cell_size)
            spawn_anchors = []
            spawn_offsets = []
            adjusted = safe_config(graph)
            for start, anchor in zip(navigation_starts, adjusted['bases']):
                spawn_anchors.append(tuple(anchor))
                spawn_offsets.append(math.hypot(float(anchor[0]) - float(start[0]),
                                                float(anchor[1]) - float(start[1])))
            spawn_config = dict(config)
            spawn_config['bases'] = tuple(spawn_anchors)
            spawn_config['anchors'] = tuple(spawn_anchors)
            graph['bases'] = [list(point) for point in spawn_anchors]
            graph['objective_bases'] = [list(point) for point in bases]
            graph['ctf_spawn_points'] = [[list(point) for point in team]
                                         for team in stock_spawns]
            graph['spawn_anchors'] = [list(point) for point in spawn_anchors]
            graph['spawn_anchor_source'] = (
                'ctf teamSpawnPoints projected onto validated graph'
                if all(stock_spawns) else
                'ctf objectives projected onto validated graph')
            graph['validation'] = original_validate(graph, spawn_config)
            graph['validation']['spawn_start_reach_metres'] = [round(value, 3)
                                                               for value in spawn_offsets]
            if ingress_records:
                ingress_validation = {}
                graph['spawn_ingress'] = {}
                for team in sorted(ingress_records):
                    record = ingress_records[team]
                    ingress_validation[str(team)] = install_downhill_spawn_ingress(
                        graph, record, legacy)
                    graph['spawn_ingress'][str(team)] = record
                graph['validation']['spawn_ingress'] = ingress_validation
                graph['bake']['directed_spawn_ingress'] = True
                graph['bake']['reversible_links_except_spawn_ingress'] = True
            formations, formation_validation = bake_spawn_formations(
                graph, spawn_anchors, map_name)
            graph['spawn_formations'] = formations
            graph['spawn_formation_source'] = (
                'ctf teamSpawnPoints plus validated 15-slot layout'
                if all(stock_spawns) else
                'ctf objective geometry plus validated 15-slot layout')
            graph['validation'].update(formation_validation)
        finally:
            legacy.MAPS, legacy.GAME_VERSION = original_maps, original_game_version
            legacy.FORMAT_NAME, legacy.FORMAT_VERSION = original_format_name, original_format_version
            legacy.Terrain, legacy.ObstacleField = original_terrain, original_obstacles
            legacy.validate_graph = original_validate
            legacy.bake_tactical_routes = original_bake_routes
            legacy.retain_base_component = original_retain
        graph['bake']['source'] = '0.9.22 BSMI/BSMO transformed BSP2 collision; BWSG/BSGD decoded and present'
        graph['bake']['water_mode'] = ('BWWa-cell-surface-depth' if water_cells else
                                       'verified-empty-BWWa')
        graph['bake']['water_cell_volumes'] = len(water_cells)
        graph['bake']['water_records'] = len(water_records)
        graph['bake']['water_surface_heights'] = [round(float(record['position'][1]), 4)
                                                  for record in water_records]
        graph['bake']['water_cell_bounds'] = [[round(float(value), 3) for value in cell]
                                             for cell in water_cells]
        graph['bake']['compiled_static_geometry_bytes'] = len(compiled.sections['BSGD']._data)
        graph['bake']['collision_linkage'] = _collision_linkage_summary(compiled)
        graph['bake']['compiled_soft_destructibles'] = soft_destructible_counts
        graph['bake']['compiled_local_obstacles'] = local_obstacle_counts
        if output:
            legacy.write_graph(output, graph)
        return graph
    finally:
        vfs.close()


def bake_ensk_graph(client_root, output=None, cell_size=4.0):
    """Compatibility spelling for the first verified #1513 map."""
    return bake_map_graph(client_root, '06_ensk', output, cell_size)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True, help='Pinned #1513 client root')
    parser.add_argument('--map', default='06_ensk', help='CTF geometry map name')
    parser.add_argument('--output', help='Write deterministic inspection JSON')
    parser.add_argument('--bake', action='store_true',
                        help='Bake and strictly validate the selected standard map')
    args = parser.parse_args(argv)
    try:
        if args.bake:
            result = bake_map_graph(args.client, args.map, args.output)
        else:
            result = inspect_client_map(args.client, args.map)
    except (CompiledSpaceError, ValueError, zipfile.BadZipFile) as error:
        print('FAILED %s: %s' % (args.map, error), file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output and not args.bake:
        with open(args.output, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(rendered)
    print(rendered, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
