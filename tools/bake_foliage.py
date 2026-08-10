#!/usr/bin/env python3
"""Bake WoT 0.8.2 concealment vegetation into a runtime spatial index."""

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile


TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from bake_navigation import (
    CHUNK_SIZE,
    MAPS,
    PackageResources,
    _packed_child,
    _packed_children,
    _packed_text,
    _packed_vector,
    _transform_point,
    chunk_coordinates,
    read_packed_xml,
)
from build_navmesh_probe import TYPE_ELEMENT


FORMAT_NAME = "offhangar-foliage"
FORMAT_VERSION = 1
GAME_VERSION = "0.8.2"
CELL_SIZE = 32.0
CAMOUFLAGE_PER_VOLUME = 0.15
CTREE_VERSION = 105


class CaseFoldResources(object):
    def __init__(self, resources):
        self.resources = resources
        self.names = dict((name.lower(), name) for name in resources.iter_names())

    def read(self, name):
        actual = self.names.get(str(name).lower())
        if actual is None:
            raise KeyError(name)
        return self.resources.read(actual)


def bush_tokens(data):
    root = read_packed_xml(data)
    result = []
    for name, unused_value in root.children:
        token = name.decode("ascii").strip().lower()
        if token:
            result.append(token)
    if not result:
        raise ValueError("speedtree/bushes.xml contains no bush taxonomy")
    return tuple(sorted(set(result), key=lambda value: (-len(value), value)))


def ctree_bounds(data):
    if len(data) < 28:
        raise ValueError("truncated ctree resource")
    version, min_x, min_y, min_z, max_x, max_y, max_z = struct.unpack_from(
        "<I6f", data, 0
    )
    if version != CTREE_VERSION:
        raise ValueError("unsupported ctree version %d" % version)
    if not (min_x < max_x and min_y < max_y and min_z < max_z):
        raise ValueError("invalid ctree bounds")
    return (min_x, min_y, min_z), (max_x, max_y, max_z)


def is_bush_resource(resource, tokens):
    stem = os.path.splitext(os.path.basename(str(resource)))[0].lower()
    return any(token in stem for token in tokens)


def _round(value):
    return round(float(value), 4)


def foliage_instance(bounds, transform, chunk_x, chunk_z):
    minimum, maximum = bounds
    centre = tuple((minimum[index] + maximum[index]) * 0.5 for index in range(3))
    world_centre = _transform_point(transform, centre, chunk_x, chunk_z)
    half_sizes = tuple((maximum[index] - minimum[index]) * 0.5
                       for index in range(3))
    projected_axes = (
        (float(transform[0]) * half_sizes[0],
         float(transform[2]) * half_sizes[0]),
        (float(transform[3]) * half_sizes[1],
         float(transform[5]) * half_sizes[1]),
        (float(transform[6]) * half_sizes[2],
         float(transform[8]) * half_sizes[2]),
    )
    # Most bushes stand upright, so local X/Z form the horizontal box. A map
    # may also place an authored bush on its side; choose the two projected
    # local axes with the strongest 2-D basis instead of assuming upright Y.
    basis_candidates = []
    for first_index, second_index in ((0, 1), (0, 2), (1, 2)):
        first = projected_axes[first_index]
        second = projected_axes[second_index]
        determinant = first[0] * second[1] - first[1] * second[0]
        basis_candidates.append((abs(determinant), determinant, first, second))
    unused_area, determinant, axis_first, axis_second = max(basis_candidates)
    if abs(determinant) <= 1e-8:
        raise ValueError("degenerate foliage transform")
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
                corners.append(_transform_point(
                    transform, (x, y, z), chunk_x, chunk_z
                ))
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
            extent_first, abs(raw_inverse[0] * dx + raw_inverse[1] * dz)
        )
        extent_second = max(
            extent_second, abs(raw_inverse[2] * dx + raw_inverse[3] * dz)
        )
    if extent_first <= 1e-8 or extent_second <= 1e-8:
        raise ValueError("degenerate projected foliage bounds")
    inverse = (
        raw_inverse[0] / extent_first,
        raw_inverse[1] / extent_first,
        raw_inverse[2] / extent_second,
        raw_inverse[3] / extent_second,
    )
    radius = max(
        math.hypot(point[0] - world_centre[0], point[2] - world_centre[2])
        for point in corners
    )
    row = [
        _round(world_centre[0]), _round(minimum_y), _round(world_centre[2]),
        _round(maximum_y), _round(inverse[0]), _round(inverse[1]),
        _round(inverse[2]), _round(inverse[3]), CAMOUFLAGE_PER_VOLUME,
        _round(radius),
    ]
    return row, (minimum_x, minimum_z, maximum_x, maximum_z)


def bake_map(resources, map_name, tokens, cell_size=CELL_SIZE):
    folded = CaseFoldResources(resources)
    prefix = "spaces/%s/" % map_name
    instances = []
    cells = {}
    asset_counts = {}
    tree_count = 0
    skipped = 0
    for chunk_name in resources.iter_names(suffix=".chunk", prefix=prefix):
        chunk_x, chunk_z = chunk_coordinates(chunk_name)
        root = read_packed_xml(resources.read(chunk_name))
        for value in _packed_children(root, "speedtree"):
            tree_count += 1
            if value.value_type != TYPE_ELEMENT:
                skipped += 1
                continue
            resource_value = _packed_child(value.value, "spt", False)
            if resource_value is None:
                skipped += 1
                continue
            resource = _packed_text(resource_value)
            if not is_bush_resource(resource, tokens):
                continue
            transform_value = _packed_child(value.value, "transform", False)
            if transform_value is None:
                raise ValueError("bush instance has no transform: %s" % resource)
            transform = _packed_vector(transform_value)
            if len(transform) != 12:
                raise ValueError("bush transform is invalid: %s" % resource)
            ctree = os.path.splitext(resource)[0] + ".ctree"
            try:
                bounds = ctree_bounds(folded.read(ctree))
                row, world_bounds = foliage_instance(
                    bounds, transform, chunk_x, chunk_z
                )
            except (KeyError, ValueError, struct.error) as error:
                raise ValueError("bush resource failed %s in %s: %s" % (
                    resource, chunk_name, error
                ))
            instance_id = len(instances)
            instances.append(row)
            asset = os.path.splitext(os.path.basename(resource))[0].lower()
            asset_counts[asset] = asset_counts.get(asset, 0) + 1
            min_cell_x = int(math.floor(world_bounds[0] / float(cell_size)))
            min_cell_z = int(math.floor(world_bounds[1] / float(cell_size)))
            max_cell_x = int(math.floor(world_bounds[2] / float(cell_size)))
            max_cell_z = int(math.floor(world_bounds[3] / float(cell_size)))
            for cell_x in range(min_cell_x, max_cell_x + 1):
                for cell_z in range(min_cell_z, max_cell_z + 1):
                    key = "%d,%d" % (cell_x, cell_z)
                    cells.setdefault(key, []).append(instance_id)
    if not instances:
        raise ValueError("no concealment vegetation found for %s" % map_name)
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "game_version": GAME_VERSION,
        "map": map_name,
        "cell_size": float(cell_size),
        "instances": instances,
        "cells": cells,
        "bake": {
            "taxonomy": list(tokens),
            "matching": "case-insensitive asset-name substring",
            "source_speedtrees": tree_count,
            "foliage_instances": len(instances),
            "spatial_cells": len(cells),
            "camouflage_per_volume": CAMOUFLAGE_PER_VOLUME,
            "skipped_instances": skipped,
            "asset_counts": asset_counts,
        },
    }


def write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as output:
        json.dump(data, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
    os.replace(temporary, path)


def default_output(map_name):
    return os.path.join(
        REPO_ROOT, "scripts", "client", "gui", "mods", "offhangar",
        "foliage", map_name + ".json"
    )


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _publish_batch(staging_dir, map_names):
    target = os.path.dirname(default_output(map_names[0]))
    if not os.path.isdir(target):
        os.makedirs(target)
    records = []
    for map_name in map_names:
        path = os.path.join(staging_dir, map_name + ".json")
        records.append({
            "map": map_name,
            "file": map_name + ".json",
            "sha256": _sha256(path),
        })
    write_json(os.path.join(staging_dir, "manifest.json"), {
        "format": FORMAT_NAME + "-manifest",
        "version": FORMAT_VERSION,
        "game_version": GAME_VERSION,
        "maps": records,
    })
    for map_name in map_names:
        os.replace(os.path.join(staging_dir, map_name + ".json"),
                   default_output(map_name))
    os.replace(os.path.join(staging_dir, "manifest.json"),
               os.path.join(target, "manifest.json"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--map", choices=sorted(MAPS))
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--cell-size", type=float, default=CELL_SIZE)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.all and args.output:
        parser.error("--output can only be used with one --map")
    root = os.path.abspath(args.client)
    packages = os.path.join(root, "res", "packages")
    misc_path = os.path.join(packages, "misc.pkg")
    shared_path = os.path.join(packages, "shared_content.pkg")
    for path in (misc_path, shared_path):
        if not os.path.isfile(path):
            parser.error("required client package not found: %s" % path)
    misc = PackageResources((misc_path,))
    try:
        tokens = bush_tokens(misc.read("speedtree/bushes.xml"))
    finally:
        misc.close()
    map_names = sorted(MAPS) if args.all else [args.map or "07_lakeville"]
    staging = tempfile.mkdtemp(prefix="offhangar-foliage-") if args.all else None
    failures = []
    for map_name in map_names:
        map_package = os.path.join(packages, map_name + ".pkg")
        if not os.path.isfile(map_package):
            failures.append((map_name, "map package not found"))
            continue
        resources = PackageResources((map_package, shared_path))
        try:
            data = bake_map(resources, map_name, tokens, args.cell_size)
        except Exception as error:
            failures.append((map_name, str(error)))
            print("FAILED %s: %s" % (map_name, error))
            continue
        finally:
            resources.close()
        output = (os.path.join(staging, map_name + ".json")
                  if staging else args.output or default_output(map_name))
        write_json(output, data)
        print("Baked %s: %d foliage volumes in %d cells" % (
            map_name, len(data["instances"]), len(data["cells"])
        ))
    if failures:
        if staging:
            shutil.rmtree(staging)
        for map_name, error in failures:
            print("FAILED %s: %s" % (map_name, error))
        return 1
    if staging:
        _publish_batch(staging, map_names)
        shutil.rmtree(staging)
    print("Foliage bake completed for %d map(s)." % len(map_names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
