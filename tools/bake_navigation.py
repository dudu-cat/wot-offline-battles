#!/usr/bin/env python3
"""Bake a deterministic terrain navigation graph from WoT 0.8.2 packages.

This tool is intentionally offline.  It reads the map terrain, water planes,
static model transforms, visual bounds, and primitive vertices directly from
the pinned client packages.  The resulting JSON is small enough to ship with
the mod, so players never have to scan a map in game.

The first supported map is Lakeville.  Additional maps can be enabled after
their baked output passes the same connectivity and route-anchor validation.
"""

import argparse
import base64
import heapq
import io
import json
import math
import os
import struct
import sys
import zipfile
import zlib
import xml.etree.ElementTree as ElementTree


TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from build_navmesh_probe import (  # noqa: E402
    TYPE_COMPRESSED_STRING,
    TYPE_ELEMENT,
    TYPE_INTEGER,
    TYPE_STRING,
    TYPE_VECTOR,
    read_packed_xml,
)


FORMAT_NAME = "offhangar-navgraph"
FORMAT_VERSION = 1
GAME_VERSION = "0.8.2"
CHUNK_SIZE = 100.0
HEIGHTMAP_INNER_SIZE = 64
HEIGHTMAP_BORDER = 2
WATER_DEPTH_LIMIT = 0.12
VEHICLE_HALF_WIDTH = 2.15
VEHICLE_CLEARANCE_HEIGHT = 2.40
LOCAL_OBSTACLE_MAX_HEIGHT = 0.65
MAX_GRADE_UP = 0.38
MAX_GRADE_DOWN = 0.38
# Normal tank routes must be controllable in both directions.  A drop that can
# be slid down but not climbed back up is an emergency transition, not a route
# shortcut, so the retained graph uses the stricter directional limit.
MAX_GRADE = min(MAX_GRADE_UP, MAX_GRADE_DOWN)
EDGE_CLEARANCE_RADII = (3.0, 6.0)
HAZARD_WATER = 1
HAZARD_EDGE = 2

DIRECTIONS = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0),              (1, 0),
    (-1, 1),  (0, 1),     (1, 1),
)

LAKEVILLE_ROUTES = (
    ((-169.0, 319.0), (-314.0, 298.0), (-330.0, 189.0),
     (-331.0, 40.0), (-315.0, -101.0), (-278.0, -211.0),
     (-225.0, -273.0)),
    ((-169.0, 319.0), (-110.0, 268.0), (-76.0, 189.0),
     (-98.0, 74.0), (-90.0, -98.0), (-102.0, -211.0),
     (-165.0, -294.0)),
    ((-169.0, 319.0), (-9.0, 325.0), (164.0, 306.0),
     (289.0, 267.0), (322.0, 173.0), (314.0, 40.0),
     (284.0, -93.0), (218.0, -187.0), (70.0, -265.0),
     (-79.0, -297.0)),
)

MAPS = {
    "07_lakeville": {
        "bounds": (-400.0, -400.0, 400.0, 400.0),
        "bases": ((-169.5, 319.4), (-169.5, -319.0)),
        "routes": LAKEVILLE_ROUTES,
        "anchors": tuple(point for route in LAKEVILLE_ROUTES for point in route),
    },
}


def _packed_children(element, name):
    encoded = name.encode("ascii")
    return [value for child_name, value in element.children
            if child_name == encoded]


def _packed_child(element, name, required=True):
    values = _packed_children(element, name)
    if values:
        return values[0]
    if required:
        raise ValueError("missing Packed XML child %s" % name)
    return None


def _packed_text(value):
    if value.value_type == TYPE_COMPRESSED_STRING:
        return base64.b64encode(value.value).decode("ascii")
    if value.value_type != TYPE_STRING:
        raise ValueError("Packed XML value is not text")
    return value.value.decode("utf-8")


def _packed_vector(value):
    if value.value_type != TYPE_VECTOR or len(value.value) % 4:
        raise ValueError("Packed XML value is not a float vector")
    return struct.unpack("<%df" % (len(value.value) // 4), value.value)


def _packed_integer(value, default=None):
    if value is None:
        return default
    if value.value_type != TYPE_INTEGER:
        raise ValueError("Packed XML value is not an integer")
    return int(value.value)


def _signed_hex16(value):
    number = int(value, 16)
    return number - 65536 if number >= 32768 else number


def chunk_coordinates(name):
    base = os.path.basename(name).split(".", 1)[0]
    if len(base) < 8:
        raise ValueError("invalid outside chunk name %s" % name)
    return _signed_hex16(base[:4]), _signed_hex16(base[4:8])


class PackageResources(object):
    def __init__(self, package_paths):
        self.archives = []
        self.names = []
        for path in package_paths:
            archive = zipfile.ZipFile(path, "r")
            self.archives.append(archive)
            self.names.append(set(archive.namelist()))

    def close(self):
        for archive in self.archives:
            archive.close()

    def read(self, name):
        for archive, names in zip(self.archives, self.names):
            if name in names:
                return archive.read(name)
        raise KeyError(name)

    def contains(self, name):
        return any(name in names for names in self.names)

    def iter_names(self, suffix=None, prefix=None):
        seen = set()
        for names in self.names:
            for name in names:
                if name in seen:
                    continue
                if suffix is not None and not name.endswith(suffix):
                    continue
                if prefix is not None and not name.startswith(prefix):
                    continue
                seen.add(name)
                yield name


def soft_destructible_models(data):
    """Return models a tank can push through instead of routing around."""
    root = read_packed_xml(data)
    result = set()
    for category_name in ("fragiles", "fallingAtoms"):
        category_value = _packed_child(root, category_name, False)
        if category_value is None or category_value.value_type != TYPE_ELEMENT:
            continue
        for entry_value in _packed_children(category_value.value, "entry"):
            if entry_value.value_type != TYPE_ELEMENT:
                continue
            filename_value = _packed_child(entry_value.value, "filename", False)
            if filename_value is not None:
                result.add(_packed_text(filename_value))
    return result


def decode_png_rgba(png):
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid PNG signature")
    position = 8
    width = height = None
    compressed = []
    while position < len(png):
        length = struct.unpack_from(">I", png, position)[0]
        kind = png[position + 4:position + 8]
        data = png[position + 8:position + 8 + length]
        position += length + 12
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", data)
            )
            if (bit_depth, color_type, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise ValueError("unsupported height PNG encoding")
        elif kind == b"IDAT":
            compressed.append(data)
        elif kind == b"IEND":
            break
    if width is None or height is None:
        raise ValueError("PNG has no IHDR")
    raw = zlib.decompress(b"".join(compressed))
    bytes_per_pixel = 4
    stride = width * bytes_per_pixel
    rows = []
    previous = bytearray(stride)
    offset = 0
    for unused_y in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + stride])
        offset += stride
        for index in range(stride):
            left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                estimate = left + above - upper_left
                left_error = abs(estimate - left)
                above_error = abs(estimate - above)
                corner_error = abs(estimate - upper_left)
                if left_error <= above_error and left_error <= corner_error:
                    predictor = left
                elif above_error <= corner_error:
                    predictor = above
                else:
                    predictor = upper_left
            elif filter_type == 0:
                predictor = 0
            else:
                raise ValueError("unsupported PNG filter %d" % filter_type)
            row[index] = (row[index] + predictor) & 255
        rows.append(row)
        previous = row
    return width, height, rows


class HeightChunk(object):
    def __init__(self, data):
        if len(data) < 36 or data[:4] != b"hmp\0":
            raise ValueError("invalid terrain2 height header")
        width, height = struct.unpack_from("<II", data, 4)
        png_width, png_height, rows = decode_png_rgba(data[36:])
        if (width, height) != (png_width, png_height):
            raise ValueError("height header and PNG dimensions differ")
        if width < HEIGHTMAP_INNER_SIZE + HEIGHTMAP_BORDER * 2 + 1:
            raise ValueError("height map has no expected terrain border")
        self.width = width
        self.height = height
        self.values = []
        for row in rows:
            values = []
            for x in range(width):
                red = row[x * 4]
                green = row[x * 4 + 1]
                millimetres = red + green * 256
                if millimetres >= 32768:
                    millimetres -= 65536
                values.append(millimetres / 1000.0)
            self.values.append(values)

    def sample(self, local_x, local_z):
        image_x = HEIGHTMAP_BORDER + local_x * HEIGHTMAP_INNER_SIZE / CHUNK_SIZE
        image_z = HEIGHTMAP_BORDER + local_z * HEIGHTMAP_INNER_SIZE / CHUNK_SIZE
        image_x = max(0.0, min(self.width - 1.0, image_x))
        image_z = max(0.0, min(self.height - 1.0, image_z))
        x0 = int(math.floor(image_x))
        z0 = int(math.floor(image_z))
        x1 = min(self.width - 1, x0 + 1)
        z1 = min(self.height - 1, z0 + 1)
        fx = image_x - x0
        fz = image_z - z0
        lower = self.values[z0][x0] * (1.0 - fx) + self.values[z0][x1] * fx
        upper = self.values[z1][x0] * (1.0 - fx) + self.values[z1][x1] * fx
        return lower * (1.0 - fz) + upper * fz


class WaterPlane(object):
    def __init__(self, position, size, orientation):
        self.x = float(position[0])
        self.y = float(position[1])
        self.z = float(position[2])
        self.half_x = abs(float(size[0])) * 0.5
        self.half_z = abs(float(size[2])) * 0.5
        self.angle = float(orientation[0]) if orientation else 0.0
        self.cosine = math.cos(-self.angle)
        self.sine = math.sin(-self.angle)

    def contains(self, x, z):
        dx = float(x) - self.x
        dz = float(z) - self.z
        local_x = dx * self.cosine - dz * self.sine
        local_z = dx * self.sine + dz * self.cosine
        return abs(local_x) <= self.half_x and abs(local_z) <= self.half_z


class Terrain(object):
    def __init__(self, resources, map_name):
        self.resources = resources
        self.map_name = map_name
        self.prefix = "spaces/%s/" % map_name
        self.chunks = {}
        self.waters = []
        self._load()

    def _load(self):
        for name in self.resources.iter_names(suffix=".cdata", prefix=self.prefix):
            chunk_name = os.path.basename(name).split(".", 1)[0]
            try:
                coordinates = chunk_coordinates(chunk_name)
            except ValueError:
                continue
            with zipfile.ZipFile(io.BytesIO(self.resources.read(name)), "r") as archive:
                if "terrain2/heights" not in archive.namelist():
                    continue
                self.chunks[coordinates] = HeightChunk(archive.read("terrain2/heights"))
        for name in self.resources.iter_names(suffix=".vlo", prefix=self.prefix):
            root = read_packed_xml(self.resources.read(name))
            for value in _packed_children(root, "water"):
                if value.value_type != TYPE_ELEMENT:
                    continue
                element = value.value
                position = _packed_vector(_packed_child(element, "position"))
                size = _packed_vector(_packed_child(element, "size"))
                orientation_value = _packed_child(element, "orientation", False)
                orientation = _packed_vector(orientation_value) if orientation_value else (0.0,)
                self.waters.append(WaterPlane(position, size, orientation))
        if not self.chunks:
            raise ValueError("no terrain chunks found for %s" % self.map_name)

    def height(self, x, z):
        chunk_x = int(math.floor(float(x) / CHUNK_SIZE))
        chunk_z = int(math.floor(float(z) / CHUNK_SIZE))
        chunk = self.chunks.get((chunk_x, chunk_z))
        if chunk is None:
            return None
        local_x = float(x) - chunk_x * CHUNK_SIZE
        local_z = float(z) - chunk_z * CHUNK_SIZE
        return chunk.sample(local_x, local_z)

    def water_depth(self, x, z, height=None):
        if height is None:
            height = self.height(x, z)
        if height is None:
            return None
        depth = 0.0
        for water in self.waters:
            if water.contains(x, z):
                depth = max(depth, water.y - float(height))
        return max(0.0, depth)


def _primitive_sections(data):
    if data[:4] != b"\x65\x4e\xa1\x42":
        raise ValueError("invalid primitives magic")
    if len(data) < 8:
        raise ValueError("truncated primitives file")
    table_length = struct.unpack_from("<I", data, len(data) - 4)[0]
    position = len(data) - 4 - table_length
    section_offset = 4
    remaining = table_length
    sections = {}
    while remaining:
        if remaining < 24:
            raise ValueError("truncated primitives section table")
        section_length = struct.unpack_from("<I", data, position)[0]
        name_length = struct.unpack_from("<I", data, position + 20)[0]
        name_start = position + 24
        name = data[name_start:name_start + name_length].decode("utf-8")
        name_padding = (-name_length) % 4
        record_length = 24 + name_length + name_padding
        sections[name] = data[section_offset:section_offset + section_length]
        position += record_length
        remaining -= record_length
        section_offset += section_length + (-section_length) % 4
    return sections


def _cstring(data, start, length):
    raw = data[start:start + length]
    return raw.split(b"\0", 1)[0].decode("ascii")


def _vertex_positions(section):
    vertex_type = _cstring(section, 0, 64)
    count = struct.unpack_from("<I", section, 64)[0]
    position = 68
    if vertex_type.startswith("BPVT"):
        vertex_type = _cstring(section, position, 64)
        count = struct.unpack_from("<I", section, position + 64)[0]
        position += 68
    strides = {
        "xyznuv": 24,
        "xyznuvtb": 32,
    }
    stride = strides.get(vertex_type)
    if stride is None:
        raise ValueError("unsupported static vertex type %s" % vertex_type)
    vertices = []
    for unused_index in range(count):
        if position + stride > len(section):
            raise ValueError("truncated vertex section")
        vertices.append(struct.unpack_from("<fff", section, position))
        position += stride
    return vertices


def _index_groups(section):
    index_type = _cstring(section, 0, 64)
    index_width = 4 if index_type == "list32" else 2 if index_type == "list" else None
    if index_width is None:
        raise ValueError("unsupported index type %s" % index_type)
    index_count, group_count = struct.unpack_from("<II", section, 64)
    if index_count % 3:
        raise ValueError("triangle index count is not divisible by three")
    position = 72
    primitives = []
    index_format = "<I" if index_width == 4 else "<H"
    for unused_index in range(index_count // 3):
        triangle = []
        for unused_vertex in range(3):
            triangle.append(struct.unpack_from(index_format, section, position)[0])
            position += index_width
        primitives.append(tuple(triangle))
    groups = []
    for unused_index in range(group_count):
        groups.append(struct.unpack_from("<IIII", section, position))
        position += 16
    return primitives, groups


def _bsp_triangles(section, material_names, material_flags):
    if len(section) < 16:
        raise ValueError("truncated BSP2 header")
    magic, triangle_count, unused_max_triangles, unused_node_count = struct.unpack_from(
        "<IIII", section, 0
    )
    if magic & 0x00FFFFFF != 0x00505342:
        raise ValueError("invalid BSP2 magic")
    required = 16 + triangle_count * 40
    if len(section) < required:
        raise ValueError("truncated BSP2 triangle array")
    triangles = []
    for index in range(triangle_count):
        offset = 16 + index * 40
        values = struct.unpack_from("<9f", section, offset)
        material_index = struct.unpack_from("<I", section, offset + 36)[0]
        material_name = (material_names[material_index]
                         if material_index < len(material_names) else "")
        collision_flags = int(material_flags.get(material_name, 0))
        if collision_flags & 16:  # TRIANGLE_NOCOLLIDE
            continue
        triangles.append((values[0:3], values[3:6], values[6:9]))
    return triangles


def _bsp_material_names(section):
    root = ElementTree.fromstring(section.decode("utf-8"))
    return [element.text.strip() if element.text else ""
            for element in root.findall("id")]


def _convex_hull(points):
    unique = sorted(set((float(point[0]), float(point[1])) for point in points))
    if len(unique) <= 1:
        return tuple(unique)

    def cross(origin, first, second):
        return ((first[0] - origin[0]) * (second[1] - origin[1]) -
                (first[1] - origin[1]) * (second[0] - origin[0]))

    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


class ModelShape(object):
    def __init__(self, hull, triangles, minimum, maximum, source):
        self.hull = tuple(hull)
        self.triangles = tuple(triangles)
        self.minimum = tuple(minimum)
        self.maximum = tuple(maximum)
        self.source = source


def _is_local_obstacle(shape):
    return (float(shape.maximum[1]) - float(shape.minimum[1]) <=
            LOCAL_OBSTACLE_MAX_HEIGHT)


class ModelLibrary(object):
    def __init__(self, resources):
        self.resources = resources
        self.cache = {}

    def load(self, model_name):
        if model_name in self.cache:
            return self.cache[model_name]
        model_root = read_packed_xml(self.resources.read(model_name))
        model_children = dict(model_root.children)
        visual_value = (model_children.get(b"nodelessVisual") or
                        model_children.get(b"nodefullVisual"))
        if visual_value is None:
            raise ValueError("model has no visual: %s" % model_name)
        visual_base = _packed_text(visual_value)
        visual_root = read_packed_xml(self.resources.read(visual_base + ".visual"))
        bounding = _packed_child(visual_root, "boundingBox").value
        minimum = _packed_vector(_packed_child(bounding, "min"))
        maximum = _packed_vector(_packed_child(bounding, "max"))
        points = []
        render_triangles = []
        material_flags = {}
        primitives_name = visual_base + ".primitives"
        if self.resources.contains(primitives_name):
            sections = _primitive_sections(self.resources.read(primitives_name))
            for render_value in _packed_children(visual_root, "renderSet"):
                if render_value.value_type != TYPE_ELEMENT:
                    continue
                geometry_value = _packed_child(render_value.value, "geometry", False)
                if geometry_value is None or geometry_value.value_type != TYPE_ELEMENT:
                    continue
                for group_value in _packed_children(geometry_value.value, "primitiveGroup"):
                    if group_value.value_type != TYPE_ELEMENT:
                        continue
                    material_value = _packed_child(group_value.value, "material", False)
                    if material_value is None or material_value.value_type != TYPE_ELEMENT:
                        continue
                    identifier_value = _packed_child(material_value.value, "identifier", False)
                    if identifier_value is None:
                        continue
                    identifier = _packed_text(identifier_value)
                    flags_value = _packed_child(material_value.value, "collisionFlags", False)
                    material_flags[identifier] = _packed_integer(flags_value, 0)
                vertices_value = _packed_child(geometry_value.value, "vertices", False)
                indices_value = _packed_child(geometry_value.value, "primitive", False)
                if vertices_value is None or indices_value is None:
                    continue
                vertices_name = _packed_text(vertices_value)
                indices_name = _packed_text(indices_value)
                if vertices_name not in sections or indices_name not in sections:
                    continue
                vertices = _vertex_positions(sections[vertices_name])
                primitives, groups = _index_groups(sections[indices_name])
                points.extend((vertex[0], vertex[2]) for vertex in vertices)
                group_values = _packed_children(geometry_value.value, "primitiveGroup")
                group_indices = []
                for group_value in group_values:
                    if group_value.value_type == TYPE_ELEMENT:
                        group_indices.append(_packed_integer(group_value.value.value, 0))
                if not group_indices:
                    group_indices = list(range(len(groups)))
                for group_index in group_indices:
                    if group_index < 0 or group_index >= len(groups):
                        continue
                    primitive_offset, primitive_count, vertex_offset, vertex_count = groups[group_index]
                    group_vertices = vertices[vertex_offset:vertex_offset + vertex_count]
                    for primitive in primitives[primitive_offset:primitive_offset + primitive_count]:
                        if max(primitive) >= len(group_vertices):
                            continue
                        render_triangles.append(tuple(group_vertices[index] for index in primitive))
            if "bsp2" in sections and "bsp2_materials" in sections:
                triangles = _bsp_triangles(
                    sections["bsp2"],
                    _bsp_material_names(sections["bsp2_materials"]),
                    material_flags,
                )
            else:
                triangles = render_triangles
        else:
            triangles = []
        if not points:
            points = (
                (minimum[0], minimum[2]), (minimum[0], maximum[2]),
                (maximum[0], maximum[2]), (maximum[0], minimum[2]),
            )
        shape = ModelShape(_convex_hull(points), triangles, minimum, maximum, model_name)
        self.cache[model_name] = shape
        return shape


def _transform_point(transform, point, chunk_x, chunk_z):
    x, y, z = point
    return (
        transform[0] * x + transform[3] * y + transform[6] * z + transform[9] + chunk_x * CHUNK_SIZE,
        transform[1] * x + transform[4] * y + transform[7] * z + transform[10],
        transform[2] * x + transform[5] * y + transform[8] * z + transform[11] + chunk_z * CHUNK_SIZE,
    )


def _distance_to_segment(point, first, second):
    dx = second[0] - first[0]
    dz = second[1] - first[1]
    length_squared = dx * dx + dz * dz
    if length_squared <= 1e-10:
        return math.hypot(point[0] - first[0], point[1] - first[1])
    fraction = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dz) / length_squared
    fraction = max(0.0, min(1.0, fraction))
    x = first[0] + dx * fraction
    z = first[1] + dz * fraction
    return math.hypot(point[0] - x, point[1] - z)


def _point_in_convex_polygon(point, polygon):
    if len(polygon) < 3:
        return False
    sign = None
    previous = polygon[-1]
    for current in polygon:
        cross = ((current[0] - previous[0]) * (point[1] - previous[1]) -
                 (current[1] - previous[1]) * (point[0] - previous[0]))
        if abs(cross) > 1e-8:
            current_sign = cross > 0.0
            if sign is None:
                sign = current_sign
            elif sign != current_sign:
                return False
        previous = current
    return True


def _distance_to_polygon(point, polygon):
    if _point_in_convex_polygon(point, polygon):
        return 0.0
    if not polygon:
        return float("inf")
    return min(_distance_to_segment(point, polygon[index - 1], polygon[index])
               for index in range(len(polygon)))


class ObstacleField(object):
    def __init__(self, resources, map_name, raster_size=1.0, soft_models=None):
        self.resources = resources
        self.map_name = map_name
        self.raster_size = float(raster_size)
        self.soft_models = set(soft_models or ())
        self.cells = {}
        self.instance_count = 0
        self.soft_instance_count = 0
        self.local_instance_count = 0
        self.model_library = ModelLibrary(resources)
        self.skipped = 0
        self._load()

    def _mark_cell(self, cell_x, cell_z, minimum_y, maximum_y):
        key = (int(cell_x), int(cell_z))
        previous = self.cells.get(key)
        if previous is None:
            self.cells[key] = [float(minimum_y), float(maximum_y)]
        else:
            previous[0] = min(previous[0], float(minimum_y))
            previous[1] = max(previous[1], float(maximum_y))

    def _raster_triangle(self, triangle):
        polygon = tuple((point[0], point[2]) for point in triangle)
        minimum_y = min(point[1] for point in triangle)
        maximum_y = max(point[1] for point in triangle)
        radius = self.raster_size * math.sqrt(2.0) * 0.5
        minimum_x = min(point[0] for point in polygon) - radius
        maximum_x = max(point[0] for point in polygon) + radius
        minimum_z = min(point[1] for point in polygon) - radius
        maximum_z = max(point[1] for point in polygon) + radius
        min_cell_x = int(math.floor(minimum_x / self.raster_size))
        max_cell_x = int(math.floor(maximum_x / self.raster_size))
        min_cell_z = int(math.floor(minimum_z / self.raster_size))
        max_cell_z = int(math.floor(maximum_z / self.raster_size))
        for cell_x in range(min_cell_x, max_cell_x + 1):
            x = (cell_x + 0.5) * self.raster_size
            for cell_z in range(min_cell_z, max_cell_z + 1):
                z = (cell_z + 0.5) * self.raster_size
                if _distance_to_polygon((x, z), polygon) <= radius:
                    self._mark_cell(cell_x, cell_z, minimum_y, maximum_y)

    def _raster_shape_fallback(self, shape, transform, chunk_x, chunk_z):
        polygon = []
        for x, z in shape.hull:
            world = _transform_point(transform, (x, 0.0, z), chunk_x, chunk_z)
            polygon.append((world[0], world[2]))
        polygon = _convex_hull(polygon)
        if len(polygon) < 2:
            return False
        corners = []
        for x in (shape.minimum[0], shape.maximum[0]):
            for y in (shape.minimum[1], shape.maximum[1]):
                for z in (shape.minimum[2], shape.maximum[2]):
                    corners.append(_transform_point(transform, (x, y, z), chunk_x, chunk_z))
        minimum_y = min(point[1] for point in corners)
        maximum_y = max(point[1] for point in corners)
        if maximum_y - minimum_y < 0.45:
            return True
        radius = self.raster_size * math.sqrt(2.0) * 0.5
        min_cell_x = int(math.floor((min(point[0] for point in polygon) - radius) /
                                    self.raster_size))
        max_cell_x = int(math.floor((max(point[0] for point in polygon) + radius) /
                                    self.raster_size))
        min_cell_z = int(math.floor((min(point[1] for point in polygon) - radius) /
                                    self.raster_size))
        max_cell_z = int(math.floor((max(point[1] for point in polygon) + radius) /
                                    self.raster_size))
        for cell_x in range(min_cell_x, max_cell_x + 1):
            x = (cell_x + 0.5) * self.raster_size
            for cell_z in range(min_cell_z, max_cell_z + 1):
                z = (cell_z + 0.5) * self.raster_size
                if _distance_to_polygon((x, z), polygon) <= radius:
                    self._mark_cell(cell_x, cell_z, minimum_y, maximum_y)
        return True

    def _load(self):
        prefix = "spaces/%s/" % self.map_name
        for chunk_name in self.resources.iter_names(suffix=".chunk", prefix=prefix):
            chunk_x, chunk_z = chunk_coordinates(chunk_name)
            root = read_packed_xml(self.resources.read(chunk_name))
            for model_value in _packed_children(root, "model"):
                if model_value.value_type != TYPE_ELEMENT:
                    continue
                model = model_value.value
                resource_value = _packed_child(model, "resource", False)
                transform_value = _packed_child(model, "transform", False)
                if resource_value is None or transform_value is None:
                    self.skipped += 1
                    continue
                model_name = _packed_text(resource_value)
                transform = _packed_vector(transform_value)
                if len(transform) != 12:
                    self.skipped += 1
                    continue
                if model_name in self.soft_models:
                    self.soft_instance_count += 1
                    continue
                try:
                    shape = self.model_library.load(model_name)
                except (KeyError, ValueError, struct.error, zipfile.BadZipFile):
                    self.skipped += 1
                    continue
                # Curbs, low borders and similar props belong to locomotion,
                # not the strategic graph. A tank can cross them and their
                # tilted world AABB otherwise erases an entire four-metre road
                # cell. Explicit destructibles were already skipped above.
                if _is_local_obstacle(shape):
                    self.local_instance_count += 1
                    continue
                self.instance_count += 1
                if shape.triangles:
                    for triangle in shape.triangles:
                        transformed = tuple(_transform_point(transform, point, chunk_x, chunk_z)
                                            for point in triangle)
                        self._raster_triangle(transformed)
                elif not self._raster_shape_fallback(shape, transform, chunk_x, chunk_z):
                    self.skipped += 1

    def blocked(self, x, z, ground_y, margin=VEHICLE_HALF_WIDTH):
        centre_x = int(math.floor(float(x) / self.raster_size))
        centre_z = int(math.floor(float(z) / self.raster_size))
        cell_radius = int(math.ceil(float(margin) / self.raster_size))
        vehicle_minimum_y = float(ground_y) + 0.10
        vehicle_maximum_y = float(ground_y) + VEHICLE_CLEARANCE_HEIGHT
        for cell_x in range(centre_x - cell_radius, centre_x + cell_radius + 1):
            sample_x = (cell_x + 0.5) * self.raster_size
            for cell_z in range(centre_z - cell_radius, centre_z + cell_radius + 1):
                interval = self.cells.get((cell_x, cell_z))
                if interval is None:
                    continue
                sample_z = (cell_z + 0.5) * self.raster_size
                if math.hypot(sample_x - float(x), sample_z - float(z)) > (
                        float(margin) + self.raster_size * math.sqrt(2.0) * 0.5):
                    continue
                if interval[1] < vehicle_minimum_y or interval[0] > vehicle_maximum_y:
                    continue
                return True
        return False


def _nearest_node(graph, point, max_distance=56.0):
    best = None
    best_distance = float(max_distance)
    width = graph["width"]
    origin_x, origin_z = graph["origin"]
    cell_size = graph["cell_size"]
    for index, height in enumerate(graph["heights_mm"]):
        if height is None:
            continue
        x = origin_x + (index % width) * cell_size
        z = origin_z + (index // width) * cell_size
        distance = math.hypot(x - point[0], z - point[1])
        if distance < best_distance:
            best_distance = distance
            best = index
    return best, best_distance


def _graph_path(graph, start, goal):
    width = graph["width"]
    height = graph["height"]
    cell_size = graph["cell_size"]
    links = graph["links"]
    queue = [(0.0, start)]
    costs = {start: 0.0}
    previous = {}
    while queue:
        cost, current = heapq.heappop(queue)
        if cost != costs.get(current):
            continue
        if current == goal:
            break
        x = current % width
        z = current // width
        mask = links[current]
        for direction_index, (dx, dz) in enumerate(DIRECTIONS):
            if not (mask & (1 << direction_index)):
                continue
            nx = x + dx
            nz = z + dz
            if nx < 0 or nx >= width or nz < 0 or nz >= height:
                continue
            neighbour = nz * width + nx
            new_cost = cost + cell_size * (math.sqrt(2.0) if dx and dz else 1.0)
            if new_cost < costs.get(neighbour, float("inf")):
                costs[neighbour] = new_cost
                previous[neighbour] = current
                heapq.heappush(queue, (new_cost, neighbour))
    if goal not in costs:
        return (), float("inf")
    path = [goal]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return tuple(path), costs[goal]


def _reachable_nodes(graph, root):
    reachable = set((root,))
    stack = [root]
    width = graph["width"]
    height = graph["height"]
    while stack:
        current = stack.pop()
        x = current % width
        z = current // width
        for direction_index, (dx, dz) in enumerate(DIRECTIONS):
            if not (graph["links"][current] & (1 << direction_index)):
                continue
            nx = x + dx
            nz = z + dz
            if nx < 0 or nx >= width or nz < 0 or nz >= height:
                continue
            neighbour = nz * width + nx
            if (graph["heights_mm"][neighbour] is not None and
                    neighbour not in reachable):
                reachable.add(neighbour)
                stack.append(neighbour)
    return reachable


def _connected_components(graph):
    remaining = set(index for index, value in enumerate(graph["heights_mm"])
                    if value is not None)
    components = []
    width = graph["width"]
    height = graph["height"]
    while remaining:
        root = remaining.pop()
        stack = [root]
        count = 0
        while stack:
            current = stack.pop()
            count += 1
            x = current % width
            z = current // width
            for direction_index, (dx, dz) in enumerate(DIRECTIONS):
                if not (graph["links"][current] & (1 << direction_index)):
                    continue
                nx = x + dx
                nz = z + dz
                if nx < 0 or nx >= width or nz < 0 or nz >= height:
                    continue
                neighbour = nz * width + nx
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    stack.append(neighbour)
        components.append(count)
    components.sort(reverse=True)
    return components


def retain_base_component(graph, map_config, minimum_fraction=0.72):
    """Remove isolated ledges and pockets that cannot reach both team bases."""
    source_components = _connected_components(graph)
    if not source_components:
        raise ValueError("baked graph has no navigable nodes")
    source_nodes = sum(source_components)
    start, unused_start_offset = _nearest_node(graph, map_config["bases"][0])
    goal, unused_goal_offset = _nearest_node(graph, map_config["bases"][1])
    if start is None or goal is None:
        raise ValueError("a team base has no nearby navigable node")
    retained = _reachable_nodes(graph, start)
    if goal not in retained:
        raise ValueError("team bases are in disconnected components")
    retained_fraction = float(len(retained)) / float(source_nodes)
    if retained_fraction < float(minimum_fraction):
        raise ValueError("base graph component is unexpectedly small: %.1f%%" %
                         (retained_fraction * 100.0))
    width = graph["width"]
    height = graph["height"]
    for index in range(len(graph["heights_mm"])):
        if index not in retained:
            graph["heights_mm"][index] = None
            graph["links"][index] = 0
            continue
        x = index % width
        z = index // width
        mask = graph["links"][index]
        for direction_index, (dx, dz) in enumerate(DIRECTIONS):
            if not (mask & (1 << direction_index)):
                continue
            nx = x + dx
            nz = z + dz
            if (nx < 0 or nx >= width or nz < 0 or nz >= height or
                    nz * width + nx not in retained):
                mask &= ~(1 << direction_index)
        graph["links"][index] = mask
    graph["bake"].update({
        "source_components": len(source_components),
        "source_navigable_nodes": source_nodes,
        "retained_nodes": len(retained),
        "retained_fraction": round(retained_fraction, 5),
        "pruned_nodes": source_nodes - len(retained),
    })
    return retained


def validate_graph(graph, map_config):
    components = _connected_components(graph)
    if not components:
        raise ValueError("baked graph has no navigable nodes")
    start, start_offset = _nearest_node(graph, map_config["bases"][0])
    goal, goal_offset = _nearest_node(graph, map_config["bases"][1])
    if start is None or goal is None:
        raise ValueError("a team base has no nearby navigable node")
    path, distance = _graph_path(graph, start, goal)
    if not path:
        raise ValueError("team bases are in disconnected components")
    anchor_offsets = []
    for anchor in map_config.get("anchors", ()):
        unused_node, offset = _nearest_node(graph, anchor)
        if unused_node is None:
            raise ValueError("route anchor has no nearby navigable node: %r" % (anchor,))
        anchor_offsets.append(offset)
    maximum_anchor_offset = max(anchor_offsets or [0.0])
    if maximum_anchor_offset > 24.0:
        raise ValueError("route anchor is too far from the retained graph: %.1f m" %
                         maximum_anchor_offset)
    route_detours = []
    route_segments = 0
    maximum_opening_regression = 0.0
    enemy_base = map_config["bases"][1]
    for route in map_config.get("routes", ()):
        if len(route) > 1:
            start_to_enemy = math.hypot(
                route[0][0] - enemy_base[0], route[0][1] - enemy_base[1])
            next_to_enemy = math.hypot(
                route[1][0] - enemy_base[0], route[1][1] - enemy_base[1])
            maximum_opening_regression = max(
                maximum_opening_regression, next_to_enemy - start_to_enemy)
        for first, second in zip(route, route[1:]):
            first_node, first_offset = _nearest_node(graph, first)
            second_node, second_offset = _nearest_node(graph, second)
            segment_path, segment_distance = _graph_path(
                graph, first_node, second_node)
            if not segment_path:
                raise ValueError("route segment is disconnected: %r -> %r" %
                                 (first, second))
            direct_distance = max(
                graph["cell_size"],
                math.hypot(second[0] - first[0], second[1] - first[1]),
            )
            detour = (segment_distance + first_offset + second_offset) / direct_distance
            route_detours.append(detour)
            route_segments += 1
    maximum_route_detour = max(route_detours or [1.0])
    if maximum_route_detour > 2.0:
        raise ValueError("route segment detour is implausible: %.2fx" %
                         maximum_route_detour)
    # A flank may legitimately move laterally or slightly away from the enemy
    # base to enter its lane. Keep the metric visible and reject only a gross
    # reversal; route-specific visual audits catch smaller tactical oddities.
    if maximum_opening_regression > 60.0:
        raise ValueError("route opening moves away from the objective: %.1f m" %
                         maximum_opening_regression)
    navigable = sum(components)
    largest_fraction = float(components[0]) / float(navigable)
    if largest_fraction < 0.72:
        raise ValueError("largest graph component is unexpectedly small: %.1f%%" %
                         (largest_fraction * 100.0))
    return {
        "components": len(components),
        "largest_component": components[0],
        "largest_fraction": round(largest_fraction, 5),
        "base_offsets": [round(start_offset, 3), round(goal_offset, 3)],
        "base_path_nodes": len(path),
        "base_path_metres": round(distance, 3),
        "maximum_anchor_offset": round(maximum_anchor_offset, 3),
        "route_segments": route_segments,
        "maximum_route_detour": round(maximum_route_detour, 3),
        "maximum_opening_regression": round(maximum_opening_regression, 3),
    }


def _segment_clear(terrain, obstacles, start, end):
    distance = math.hypot(end[0] - start[0], end[2] - start[2])
    steps = max(1, int(math.ceil(distance / 2.0)))
    previous = start
    for step in range(1, steps + 1):
        fraction = float(step) / float(steps)
        x = start[0] + (end[0] - start[0]) * fraction
        z = start[2] + (end[2] - start[2]) * fraction
        y = terrain.height(x, z)
        if y is None or terrain.water_depth(x, z, y) > WATER_DEPTH_LIMIT:
            return False
        horizontal = math.hypot(x - previous[0], z - previous[2])
        if horizontal > 0.0:
            delta = y - previous[1]
            if (delta > horizontal * MAX_GRADE_UP or
                    delta < -horizontal * MAX_GRADE_DOWN):
                return False
        if obstacles.blocked(x, z, y):
            return False
        previous = (x, y, z)
    return True


def _has_safe_edge_clearance(terrain, x, z, ground_y):
    """Reject cells whose hull shoulder can fall into water or off a steep lip.

    A route centre can be dry while collision avoidance places one track over a
    shoreline or cliff.  Sampling an eroded shoulder around every baked node
    gives the runtime driver room to deviate without relying on map-specific
    forbidden polygons.
    """
    directions = (
        (-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0),
        (-math.sqrt(0.5), -math.sqrt(0.5)),
        (math.sqrt(0.5), -math.sqrt(0.5)),
        (-math.sqrt(0.5), math.sqrt(0.5)),
        (math.sqrt(0.5), math.sqrt(0.5)),
    )
    for radius in EDGE_CLEARANCE_RADII:
        maximum_drop = radius * MAX_GRADE
        for direction_x, direction_z in directions:
            sample_x = float(x) + direction_x * radius
            sample_z = float(z) + direction_z * radius
            sample_y = terrain.height(sample_x, sample_z)
            if sample_y is None:
                return False
            if terrain.water_depth(sample_x, sample_z, sample_y) > WATER_DEPTH_LIMIT:
                return False
            if float(ground_y) - float(sample_y) > maximum_drop:
                return False
    return True


def bake_graph(resources, map_name, cell_size=4.0, soft_models=None):
    map_config = MAPS[map_name]
    bounds = map_config["bounds"]
    terrain = Terrain(resources, map_name)
    obstacles = ObstacleField(resources, map_name, soft_models=soft_models)
    width = int(math.ceil((bounds[2] - bounds[0]) / cell_size))
    height = int(math.ceil((bounds[3] - bounds[1]) / cell_size))
    origin_x = bounds[0] + cell_size * 0.5
    origin_z = bounds[1] + cell_size * 0.5
    heights = [None] * (width * height)
    hazards = [0] * (width * height)
    rejected_water = 0
    rejected_obstacle = 0
    rejected_edge = 0
    for z_index in range(height):
        z = origin_z + z_index * cell_size
        for x_index in range(width):
            x = origin_x + x_index * cell_size
            index = z_index * width + x_index
            ground = terrain.height(x, z)
            if ground is None:
                continue
            if terrain.water_depth(x, z, ground) > WATER_DEPTH_LIMIT:
                hazards[index] |= HAZARD_WATER
                rejected_water += 1
                continue
            if not _has_safe_edge_clearance(terrain, x, z, ground):
                hazards[index] |= HAZARD_EDGE
                rejected_edge += 1
                continue
            if obstacles.blocked(x, z, ground):
                rejected_obstacle += 1
                continue
            heights[index] = int(round(ground * 1000.0))
    links = [0] * (width * height)
    for z_index in range(height):
        for x_index in range(width):
            index = z_index * width + x_index
            if heights[index] is None:
                continue
            start = (origin_x + x_index * cell_size,
                     heights[index] / 1000.0,
                     origin_z + z_index * cell_size)
            for direction_index, (dx, dz) in enumerate(DIRECTIONS):
                nx = x_index + dx
                nz = z_index + dz
                if nx < 0 or nx >= width or nz < 0 or nz >= height:
                    continue
                neighbour = nz * width + nx
                if heights[neighbour] is None:
                    continue
                if dx and dz:
                    side_a = z_index * width + nx
                    side_b = nz * width + x_index
                    if heights[side_a] is None or heights[side_b] is None:
                        continue
                end = (origin_x + nx * cell_size,
                       heights[neighbour] / 1000.0,
                       origin_z + nz * cell_size)
                if _segment_clear(terrain, obstacles, start, end):
                    links[index] |= 1 << direction_index
    # ``links`` are stored per source node and therefore support one-way edges.
    # Ordinary tanks must not intentionally use a one-way fall, though: retain
    # an edge only when the reverse traversal was independently validated too.
    reverse_directions = dict((direction, index)
                              for index, direction in enumerate(DIRECTIONS))
    for z_index in range(height):
        for x_index in range(width):
            index = z_index * width + x_index
            mask = links[index]
            for direction_index, (dx, dz) in enumerate(DIRECTIONS):
                if not (mask & (1 << direction_index)):
                    continue
                neighbour = (z_index + dz) * width + (x_index + dx)
                reverse_index = reverse_directions[(-dx, -dz)]
                if not (links[neighbour] & (1 << reverse_index)):
                    mask &= ~(1 << direction_index)
            links[index] = mask
    graph = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "game_version": GAME_VERSION,
        "map": map_name,
        "cell_size": float(cell_size),
        "bounds": list(bounds),
        "origin": [origin_x, origin_z],
        "width": width,
        "height": height,
        "directions": [list(direction) for direction in DIRECTIONS],
        "heights_mm": heights,
        "links": links,
        # Hazard cells are distinct from ordinary non-navigable obstacle cells.
        # Runtime rollback may reject water/cliff entry without treating every
        # building footprint as a fatal map edge.
        "hazards": hazards,
        "bases": [list(base) for base in map_config["bases"]],
        "bake": {
            "water_depth_limit": WATER_DEPTH_LIMIT,
            "vehicle_half_width": VEHICLE_HALF_WIDTH,
            "vehicle_clearance_height": VEHICLE_CLEARANCE_HEIGHT,
            "max_grade": MAX_GRADE,
            "max_grade_up": MAX_GRADE_UP,
            "max_grade_down": MAX_GRADE_DOWN,
            "reversible_links": True,
            "edge_clearance_radii": list(EDGE_CLEARANCE_RADII),
            "terrain_chunks": len(terrain.chunks),
            "water_planes": len(terrain.waters),
            "model_shapes": len(obstacles.model_library.cache),
            "model_instances": obstacles.instance_count,
            "soft_model_instances": obstacles.soft_instance_count,
            "local_obstacle_instances": obstacles.local_instance_count,
            "local_obstacle_max_height": LOCAL_OBSTACLE_MAX_HEIGHT,
            "obstacle_raster_cells": len(obstacles.cells),
            "skipped_models": obstacles.skipped,
            "rejected_water_nodes": rejected_water,
            "rejected_obstacle_nodes": rejected_obstacle,
            "rejected_edge_nodes": rejected_edge,
        },
    }
    retain_base_component(graph, map_config)
    graph["validation"] = validate_graph(graph, map_config)
    return graph


def write_graph(path, graph):
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as output:
        json.dump(graph, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
    os.replace(temporary, path)


def default_output(map_name):
    return os.path.join(
        REPO_ROOT,
        "scripts", "client", "gui", "mods", "offhangar", "navgraphs",
        map_name + ".json",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True,
                        help="Path to the pinned World of Tanks 0.8.2 client")
    parser.add_argument("--map", default="07_lakeville", choices=sorted(MAPS))
    parser.add_argument("--cell-size", type=float, default=4.0)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    packages = os.path.join(os.path.abspath(args.client), "res", "packages")
    map_package = os.path.join(packages, args.map + ".pkg")
    shared_package = os.path.join(packages, "shared_content.pkg")
    destructibles_path = os.path.join(os.path.abspath(args.client),
                                      "res", "scripts", "destructibles.xml")
    for path in (map_package, shared_package, destructibles_path):
        if not os.path.isfile(path):
            parser.error("required client resource not found: %s" % path)
    output = args.output or default_output(args.map)
    resources = PackageResources((map_package, shared_package))
    try:
        with open(destructibles_path, "rb") as destructibles_file:
            soft_models = soft_destructible_models(destructibles_file.read())
        graph = bake_graph(resources, args.map, args.cell_size, soft_models)
    finally:
        resources.close()
    write_graph(output, graph)
    validation = graph["validation"]
    print("Baked %s: %d/%d navigable nodes, %d model instances" % (
        args.map,
        sum(value is not None for value in graph["heights_mm"]),
        len(graph["heights_mm"]),
        graph["bake"]["model_instances"],
    ))
    print("Validated: %d components, largest %.1f%%, base route %.1f m" % (
        validation["components"],
        validation["largest_fraction"] * 100.0,
        validation["base_path_metres"],
    ))
    print("Output: %s" % os.path.abspath(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
