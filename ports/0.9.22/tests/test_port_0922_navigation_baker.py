import importlib.util
import hashlib
import json
import shutil
import struct
import sys
import tempfile
import types
import math
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / 'ports' / '0.9.22' / 'tools'
sys.path.insert(0, str(TOOLS))


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


space = load_module('space_bin_0922')
baker = load_module('bake_navigation_0922')
packed = load_module('packed_xml')


def section(name, version, offset, size, rows=0):
    return struct.pack('<4s5I', name.encode('ascii'), version, offset, 0, size, rows)


def compiled_space(sections):
    header_size = 24 * (1 + len(sections))
    offset = header_size
    directory = []
    payloads = []
    for name, version, payload in sections:
        directory.append(section(name, version, offset, len(payload)))
        payloads.append(payload)
        offset += len(payload)
    return (struct.pack('<4s5I', b'BWTB', 1, header_size, 0, 0, len(sections)) +
            b''.join(directory) + b''.join(payloads))


class CompiledSpace0922Test(unittest.TestCase):

    def test_mature_navigation_baker_is_pinned_inside_the_port(self):
        path = Path(baker.LEGACY_BAKER)
        self.assertTrue(path.is_file())
        self.assertEqual(
            Path(baker.LEGACY_BASELINE_ROOT) / 'tools' /
            'bake_navigation.py', path)
        self.assertEqual(
            baker.LEGACY_BAKER_SHA256,
            hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(
            'f5b0173c296cd36753a5866ba5e6f2119e3edb25',
            baker.LEGACY_BAKER_COMMIT)
        self.assertEqual(7, len(baker.LEGACY_BASELINE_SHA256))
        baseline_root = Path(baker.LEGACY_BASELINE_ROOT)
        self.assertEqual(
            set(baker.LEGACY_BASELINE_SHA256),
            {str(path.relative_to(baseline_root))
             for path in baseline_root.rglob('*') if path.is_file()})
        for relative_path, digest in baker.LEGACY_BASELINE_SHA256.items():
            baseline_path = baseline_root / Path(relative_path)
            self.assertEqual(
                digest, hashlib.sha256(baseline_path.read_bytes()).hexdigest())

    @staticmethod
    def _vehicle_chassis_visual(minimum, maximum):
        bounds = packed.PackedElement(children=[
            (b'min', packed.PackedValue(
                packed.TYPE_VECTOR, struct.pack('<3f', *minimum))),
            (b'max', packed.PackedValue(
                packed.TYPE_VECTOR, struct.pack('<3f', *maximum))),
        ])
        root = packed.PackedElement(children=[
            (b'boundingBox', packed.PackedValue(
                packed.TYPE_ELEMENT, bounds)),
        ])
        return packed.write_packed_xml(root)

    def test_vehicle_spawn_envelope_is_measured_from_pinned_chassis_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            packages = Path(temporary) / 'res' / 'packages'
            packages.mkdir(parents=True)
            with zipfile.ZipFile(packages / 'vehicles_level_06.pkg', 'w') as archive:
                archive.writestr(
                    'vehicles/british/Long/collision_client/'
                    'Chassis.visual_processed',
                    self._vehicle_chassis_visual(
                        (-1.4, 0.0, -5.46), (1.4, 1.6, 4.7)))
            with zipfile.ZipFile(packages / 'vehicles_level_07.pkg', 'w') as archive:
                archive.writestr(
                    'vehicles/japan/Wide/collision_client/'
                    'Chassis.visual_processed',
                    self._vehicle_chassis_visual(
                        (-2.24, 0.0, -4.9), (2.24, 1.7, 4.6)))
            # HD render packages do not own the collision-client body and must
            # not change the deterministic standard-resource measurement.
            with zipfile.ZipFile(
                    packages / 'vehicles_level_10_hd.pkg', 'w') as archive:
                archive.writestr('ignored', b'')

            envelope = baker.representative_vehicle_chassis_envelope(
                temporary)

        self.assertAlmostEqual(2.24, envelope['half_width'], places=5)
        self.assertAlmostEqual(5.46, envelope['half_length'], places=5)
        self.assertIn('/Wide/', envelope['width_source'])
        self.assertIn('/Long/', envelope['length_source'])
        self.assertEqual(2, envelope['resources_scanned'])

    def test_spawn_clearance_uses_yaw_oriented_maximum_chassis_obb(self):
        obstacles = types.SimpleNamespace(
            raster_size=1.0, cells={(0, 4): [0.65, 2.4]})
        legacy = types.SimpleNamespace(
            VEHICLE_GROUND_CLEARANCE=0.65,
            VEHICLE_CLEARANCE_HEIGHT=2.4)

        self.assertTrue(baker.spawn_obstacle_obb_blocked(
            obstacles, 0.0, 0.0, 0.0, 0.0, 2.24, 5.46, legacy))
        self.assertFalse(baker.spawn_obstacle_obb_blocked(
            obstacles, 0.0, 0.0, 0.0, math.pi / 2.0,
            2.24, 5.46, legacy))

    def test_spawn_clearance_rejects_overlapping_maximum_chassis_obbs(self):
        first = (0.0, 0.0, 0.0, 0.0)
        self.assertTrue(baker.spawn_obbs_overlap(
            first, (0.0, 0.0, 10.0, 0.0), 2.24, 5.46))
        self.assertFalse(baker.spawn_obbs_overlap(
            first, (0.0, 0.0, 12.0, 0.0), 2.24, 5.46))
        self.assertFalse(baker.spawn_obbs_overlap(
            first, (14.0, 0.0, 0.0, 0.0), 2.24, 5.46))

    def test_real_ensk_graph_loads_with_0922_loader(self):
        graph = ROOT / 'ports' / '0.9.22' / 'navgraphs' / '06_ensk.json'
        self.assertTrue(graph.is_file(), 'baked Ensk graph is missing')
        loader_path = (ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' /
                       'client' / 'gui' / 'mods' / 'offline_lan_0922' /
                       'prebaked_navigation.py')
        spec = importlib.util.spec_from_file_location('prebaked_navigation_test',
                                                      loader_path)
        loader = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'navgraphs'
            directory.mkdir()
            shutil.copy2(graph, directory / graph.name)
            package_names = ('gui', 'gui.mods',
                             'gui.mods.offline_lan_0922')
            config_name = 'gui.mods.offline_lan_0922.config'
            schema_name = 'gui.mods.offline_lan_0922.navigation_graph_schema'
            names = package_names + (config_name, schema_name)
            saved = {name: sys.modules.get(name) for name in names}
            try:
                for name in package_names:
                    package = types.ModuleType(name)
                    package.__path__ = []
                    sys.modules[name] = package
                sys.modules[package_names[-1]].__path__ = [
                    str(loader_path.parent)]
                config = types.ModuleType(config_name)
                config.CONFIG_PATH = str(Path(temporary) / 'config.json')
                sys.modules[config_name] = config
                spec.loader.exec_module(loader)
                loaded = loader.load_graph('06_ensk')
            finally:
                for name, previous in saved.items():
                    if previous is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous
        self.assertEqual('offline-lan-0922-navgraph', loaded['format'])
        self.assertGreater(len(loaded['routes']['1']), 0)
        self.assertGreater(loaded['validation']['route_segments'], 0)

    def test_real_lakeville_graph_marks_compiled_water_cells_fatal(self):
        graph = ROOT / 'ports' / '0.9.22' / 'navgraphs' / '07_lakeville.json'
        self.assertTrue(graph.is_file(), 'baked Lakeville graph is missing')
        data = json.loads(graph.read_text())
        cells = data['bake']['water_cell_bounds']
        self.assertEqual(15, len(cells))
        self.assertEqual('BWWa-cell-surface-depth', data['bake']['water_mode'])
        water_indexes = [index for index, hazard in enumerate(data['hazards'])
                         if hazard & 1]
        self.assertGreater(len(water_indexes), 0)
        self.assertEqual(len(water_indexes), data['bake']['rejected_water_nodes'])
        self.assertTrue(all(data['heights_mm'][index] is None
                            for index in water_indexes))
        self.assertGreater(data['validation']['route_segments'], 0)

    def test_real_himmelsdorf_rear_guard_has_connected_locomotion(self):
        graph = ROOT / 'ports' / '0.9.22' / 'navgraphs' / '04_himmelsdorf.json'
        self.assertTrue(graph.is_file(), 'baked Himmelsdorf graph is missing')
        data = json.loads(graph.read_text())

        for team in ('1', '2'):
            route = next(value for value in data['routes'][team]
                         if value['id'] == 'rear_guard')
            self.assertGreaterEqual(len(route['waypoints']), 2)
            self.assertLessEqual(len(route['waypoints']), 16)
            self.assertTrue(route['waypoints'][-1][2])
            start = route['waypoints'][0]
            anchor = data['spawn_anchors'][int(team) - 1]
            self.assertLessEqual(
                ((start[0] - anchor[0]) ** 2 +
                 (start[1] - anchor[1]) ** 2) ** 0.5,
                data['cell_size'] * 1.5)

    def test_real_dday_graph_uses_packed_ctf_bases_and_valid_routes(self):
        graph = ROOT / 'ports' / '0.9.22' / 'navgraphs' / '101_dday.json'
        self.assertTrue(graph.is_file(), 'baked D-Day graph is missing')
        data = json.loads(graph.read_text())

        self.assertEqual([-400.0, -500.0, 600.0, 500.0], data['bounds'])
        self.assertAlmostEqual(149.9971923828125,
                               data['objective_bases'][0][0], places=5)
        self.assertAlmostEqual(-403.4408264160156,
                               data['objective_bases'][0][1], places=5)
        self.assertAlmostEqual(149.6625213623047,
                               data['objective_bases'][1][0], places=5)
        self.assertAlmostEqual(400.3866271972656,
                               data['objective_bases'][1][1], places=5)
        self.assertEqual([[], []], data['ctf_spawn_points'])
        self.assertEqual('ctf objectives projected onto validated graph',
                         data['spawn_anchor_source'])
        self.assertLess(data['bake']['maximum_route_projection'], 8.0)
        self.assertEqual([], data['bake']['soft_route_fallbacks'])
        self.assertGreater(data['bake']['rejected_obstacle_nodes'], 0)
        self.assertGreater(data['bake']['rejected_water_nodes'], 0)
        self.assertEqual(90, data['validation']['route_segments'])
        for team, start, finish in (
                ('1', data['spawn_anchors'][0], data['spawn_anchors'][1]),
                ('2', data['spawn_anchors'][1], data['spawn_anchors'][0])):
            self.assertEqual({'beach', 'village', 'cliff'},
                             {route['id'] for route in data['routes'][team]})
            for route in data['routes'][team]:
                self.assertEqual(16, len(route['waypoints']))
                self.assertEqual(start, route['waypoints'][0][:2])
                self.assertEqual(finish, route['waypoints'][-1][:2])

    def test_real_thepit_routes_start_after_verified_one_way_ingress(self):
        graph = ROOT / 'ports' / '0.9.22' / 'navgraphs' / '100_thepit.json'
        self.assertTrue(graph.is_file(), 'baked The Pit graph is missing')
        data = json.loads(graph.read_text())

        self.assertTrue(data['bake']['directed_spawn_ingress'])
        self.assertEqual([], data['bake']['soft_route_fallbacks'])
        for team in ('1', '2'):
            ingress = data['validation']['spawn_ingress'][team]
            self.assertTrue(ingress['forward_connected'])
            self.assertTrue(ingress['reverse_links_absent'])
            self.assertGreater(ingress['one_way_links'], 0)
            start = data['spawn_anchors'][int(team) - 1]
            finish = data['spawn_anchors'][2 - int(team)]
            self.assertEqual(
                {'rim_west', 'pit', 'rim_east'},
                {route['id'] for route in data['routes'][team]})
            for route in data['routes'][team]:
                self.assertEqual(16, len(route['waypoints']))
                self.assertEqual(start, route['waypoints'][0][:2])
                self.assertEqual(finish, route['waypoints'][-1][:2])

    def test_real_eiffel_graph_uses_ctf_objectives_and_mature_obstacle_rules(self):
        graph = (ROOT / 'ports' / '0.9.22' / 'navgraphs' /
                 '112_eiffel_tower_ctf.json')
        self.assertTrue(graph.is_file(), 'baked Eiffel graph is missing')
        data = json.loads(graph.read_text())

        self.assertEqual([-400.0, -400.0, 400.0, 400.0], data['bounds'])
        self.assertAlmostEqual(-346.07440185546875,
                               data['objective_bases'][0][0], places=5)
        self.assertAlmostEqual(-22.52288055419922,
                               data['objective_bases'][0][1], places=5)
        self.assertAlmostEqual(341.26910400390625,
                               data['objective_bases'][1][0], places=5)
        self.assertAlmostEqual(-19.86382293701172,
                               data['objective_bases'][1][1], places=5)
        self.assertEqual([[], []], data['ctf_spawn_points'])
        self.assertEqual('ctf objectives projected onto validated graph',
                         data['spawn_anchor_source'])
        self.assertGreater(data['bake']['soft_model_instances'], 0)
        self.assertGreater(data['bake']['local_obstacle_instances'], 0)
        self.assertGreater(data['bake']['bridge_model_instances'], 0)
        self.assertGreater(data['bake']['bridge_surface_triangles'], 0)
        self.assertGreaterEqual(data['bake']['retained_fraction'], 0.90)
        self.assertEqual(1, data['validation']['components'])
        self.assertEqual(90, data['validation']['route_segments'])
        self.assertLessEqual(max(data['validation']['spawn_start_reach_metres']),
                             data['cell_size'])
        for team, start, finish in (
                ('1', data['spawn_anchors'][0], data['spawn_anchors'][1]),
                ('2', data['spawn_anchors'][1], data['spawn_anchors'][0])):
            self.assertEqual({'tower_west', 'center', 'tower_east'},
                             {route['id'] for route in data['routes'][team]})
            for route in data['routes'][team]:
                self.assertEqual(16, len(route['waypoints']))
                self.assertEqual(start, route['waypoints'][0][:2])
                self.assertEqual(finish, route['waypoints'][-1][:2])

    def test_every_shipped_map_has_a_complete_validated_spawn_formation(self):
        graph_root = ROOT / 'ports' / '0.9.22' / 'navgraphs'
        paths = sorted(path for path in graph_root.glob('*.json')
                       if path.name != 'manifest.json')
        self.assertEqual(41, len(paths))
        for path in paths:
            with self.subTest(map=path.stem):
                data = json.loads(path.read_text())
                self.assertEqual(2, data['version'])
                self.assertEqual({'1', '2'}, set(data['spawn_formations']))
                self.assertEqual(15, len(data['spawn_formations']['1']))
                self.assertEqual(15, len(data['spawn_formations']['2']))
                self.assertTrue(all(len(point) == 4
                                    for team in data['spawn_formations'].values()
                                    for point in team))
                validation = data['validation']
                self.assertEqual(15, validation['spawn_slots_per_team'])
                self.assertGreaterEqual(
                    validation['spawn_minimum_spacing_metres'], 10.5)
                self.assertGreaterEqual(
                    validation['spawn_minimum_team_separation_metres'], 80.0)
                self.assertLessEqual(
                    validation['spawn_maximum_projection_metres'], 32.0)
                self.assertIs(
                    True, validation['spawn_compiled_bsp_obb_clearance'])
                self.assertIs(
                    True, validation['spawn_pairwise_obb_clearance'])
                self.assertAlmostEqual(
                    2.239622,
                    validation['spawn_vehicle_half_width_metres'], places=6)
                self.assertAlmostEqual(
                    5.462265,
                    validation['spawn_vehicle_half_length_metres'], places=6)
                self.assertEqual(
                    534, validation['spawn_vehicle_resources_scanned'])
                self.assertEqual(
                    'vehicles/japan/J24_Mi_To_130_tons/collision_client/'
                    'Chassis.visual_processed',
                    validation['spawn_vehicle_width_source'])
                self.assertEqual(
                    'vehicles/british/GB63_TOG_II/collision_client/'
                    'Chassis.visual_processed',
                    validation['spawn_vehicle_length_source'])
                self.assertNotIn(
                    'fallback', data['spawn_formation_source'].lower())

    def test_reads_0920_bwt2_chunk_vector(self):
        settings = struct.pack('<f4i3I', 100.0, -4, 3, -4, 3, 0, 0, 0)
        chunks = struct.pack('<II', 8, 2)
        chunks += struct.pack('<Ihh', 1, -4, -4)
        chunks += struct.pack('<Ihh', 2, 3, 3)
        data = compiled_space((('BWT2', 2, struct.pack('<I', 32) + settings + chunks),))

        terrain = space.CompiledSpace(data).terrain_info_0920()

        self.assertEqual(100.0, terrain.chunk_size)
        self.assertEqual((-4, 3, -4, 3), terrain.bounds)
        self.assertEqual(((1, -4, -4), (2, 3, 3)), terrain.chunks)

    def test_truncated_or_unknown_terrain_layout_fails_closed(self):
        bad = compiled_space((('BWT2', 2, struct.pack('<II', 31, 0)),))
        with self.assertRaises(space.CompiledSpaceError):
            space.CompiledSpace(bad).terrain_info_0920()

    def test_navigation_requires_decoded_collision_and_water(self):
        data = compiled_space(tuple((name, 2, b'') for name in
                                    ('BWSG', 'BSGD', 'BWWa', 'WTCP')))
        with self.assertRaises(space.UnsafeBakeInputError) as error:
            space.CompiledSpace(data).require_safe_navigation_sources()
        self.assertIn('refusing', str(error.exception))

    def test_compiled_soft_destructibles_skip_only_falling_and_fragile(self):
        transforms = [tuple([float(index)] + [0.0] * 15)
                      for index in range(4)]

        class ModelInstances(object):
            _data = {'transforms': transforms}

            @staticmethod
            def model_ids():
                return iter((0, 1, 2, 3))

        class Strings(object):
            @staticmethod
            def get(value):
                return 'objects/type%d.primitives/indices' % value

        model_data = {
            'model_info_items': [
                {'type': 0}, {'type': 1}, {'type': 2}, {'type': 3}],
            'models_loddings': [{'lod_begin': index}
                                for index in range(4)],
            'lod_renders': [
                {'render_set_begin': index, 'render_set_end': index}
                for index in range(4)],
            'renders': [{'prims_name_fnv': index} for index in range(4)],
        }
        compiled = types.SimpleNamespace(sections={
            'BSMI': ModelInstances(),
            'BSMO': types.SimpleNamespace(_data=model_data),
            'BWST': Strings(),
        })

        keys, counts = baker.compiled_soft_destructible_instances(compiled)

        self.assertEqual({
            ('objects/type1.primitives_processed', transforms[1]),
            ('objects/type2.primitives_processed', transforms[2]),
        }, keys)
        self.assertEqual({
            'falling': 1,
            'fragile': 1,
            'structures_preserved': 1,
            'primitive_transform_keys': 2,
        }, counts)

    def test_compiled_local_collision_bounds_preserve_low_obstacle_rule(self):
        transforms = [tuple([float(index)] + [0.0] * 15)
                      for index in range(2)]

        class ModelInstances(object):
            _data = {'transforms': transforms}

            @staticmethod
            def model_ids():
                return iter((0, 1))

        class Strings(object):
            @staticmethod
            def get(value):
                return 'objects/type%d.primitives/indices' % value

        model_data = {
            'models_colliders': [
                {'collision_bounds_min': (0.0, -0.1, 0.0),
                 'collision_bounds_max': (2.0, 0.5, 2.0)},
                {'collision_bounds_min': (0.0, -0.1, 0.0),
                 'collision_bounds_max': (2.0, 0.56, 2.0)},
            ],
            'models_loddings': [{'lod_begin': index}
                                for index in range(2)],
            'lod_renders': [
                {'render_set_begin': index, 'render_set_end': index}
                for index in range(2)],
            'renders': [{'prims_name_fnv': index} for index in range(2)],
        }
        compiled = types.SimpleNamespace(sections={
            'BSMI': ModelInstances(),
            'BSMO': types.SimpleNamespace(_data=model_data),
            'BWST': Strings(),
        })

        keys, counts = baker.compiled_local_obstacle_instances(compiled, 0.65)

        self.assertEqual({
            ('objects/type0.primitives_processed', transforms[0]),
        }, keys)
        self.assertEqual({
            'instances': 1,
            'primitive_transform_keys': 1,
            'maximum_local_height': 0.65,
        }, counts)

    def test_compiled_bridge_keeps_walkable_deck_and_blocks_body(self):
        deck = ((0.0, 2.0, 0.0), (4.0, 2.0, 0.0), (0.0, 2.0, 4.0))
        body = ((0.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 4.0))

        class Obstacles(object):
            def __init__(self):
                self.bridge_instance_count = 0
                self.bridge_surface_triangle_count = 0
                self.surfaces = []
                self.blockers = []

            def _bridge_deck_triangles(self, triangles):
                return {id(triangles[0])}

            def _raster_surface_triangle(self, triangle):
                self.surfaces.append(triangle)

            def _raster_triangle(self, triangle):
                self.blockers.append(triangle)

        obstacles = Obstacles()
        legacy = types.SimpleNamespace(
            _is_bridge_model=lambda name: 'bridge' in name.lower())

        baker._raster_compiled_collision_instance(
            obstacles, 'content/WideBridge.primitives_processed',
            [deck, body], legacy)

        self.assertEqual(1, obstacles.bridge_instance_count)
        self.assertEqual(1, obstacles.bridge_surface_triangle_count)
        self.assertEqual([deck], obstacles.surfaces)
        self.assertEqual([body], obstacles.blockers)

    def test_bwwa_cell_ranges_are_half_open_at_exact_boundary(self):
        record = {'start_id': 0, 'end_id': 2}
        self.assertEqual([(record, 'a'), (record, 'b')],
                         baker.bwwa_regions([record], ['a', 'b']))
        with self.assertRaises(space.UnsafeBakeInputError):
            baker.bwwa_regions([{'start_id': 0, 'end_id': 3}], ['a', 'b'])

    def test_bwwa_cells_transform_from_record_local_to_world_space(self):
        record = {'start_id': 0, 'end_id': 1, 'position': (10.0, -2.0, 20.0),
                  'orientation': 0.0}
        self.assertEqual([(record, (10.0, 0.0, 20.0, 14.0, 0.0, 26.0))],
                         baker.bwwa_world_regions([record], [(0, 0, 0, 4, 0, 6)]))

    def test_bwwa_rotated_aabb_corner_is_not_water(self):
        record = {'start_id': 0, 'end_id': 1, 'position': (0.0, 0.0, 0.0),
                  'orientation': math.pi / 4.0}
        cell = (0.0, 0.0, 0.0, 2.0, 0.0, 2.0)
        unused_record, bounds = baker.bwwa_world_regions([record], [cell])[0]
        self.assertFalse(baker.bwwa_contains(record, cell, bounds[0], bounds[5]))
        self.assertTrue(baker.bwwa_contains(record, cell, 0.0, 0.0))

    def test_stock_spawn_ingress_is_downhill_and_one_way(self):
        directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                      (1, 0), (-1, 1), (0, 1), (1, 1))
        graph = {
            'origin': [0.0, 0.0], 'cell_size': 1.0,
            'width': 5, 'height': 1,
            'heights_mm': [None, None, None, None, 0],
            'links': [0, 0, 0, 0, 0],
            'hazards': [2, 2, 2, 2, 0],
        }

        class Terrain(object):
            def height(self, x, unused_z):
                return 4.0 - float(x)

            def water_depth(self, unused_x, unused_z, unused_height):
                return 0.0

        class Obstacles(object):
            def surface_height(self, unused_x, unused_z):
                return None

            def blocked(self, unused_x, unused_z, unused_height, margin):
                return False

        legacy = types.SimpleNamespace(
            DIRECTIONS=directions,
            WATER_DEPTH_LIMIT=0.9,
            VEHICLE_HALF_WIDTH=2.15,
            HAZARD_WATER=1,
            HAZARD_EDGE=2,
            _ground_height=lambda terrain, unused_obstacles, x, z:
                terrain.height(x, z),
            _node_point=lambda value, index: (
                value['origin'][0] + index % value['width'] * value['cell_size'],
                value['origin'][1] + index // value['width'] * value['cell_size']),
        )

        record = baker.find_downhill_spawn_ingress(
            graph, Terrain(), Obstacles(), (0.0, 0.0), (10.0, 0.0), legacy)
        validation = baker.install_downhill_spawn_ingress(graph, record, legacy)

        self.assertEqual([[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]],
                         record['cells'])
        self.assertEqual([4000, 3000, 2000, 1000, 0], graph['heights_mm'])
        self.assertEqual(4, validation['one_way_links'])
        self.assertTrue(validation['forward_connected'])
        self.assertTrue(validation['reverse_links_absent'])
        east = 1 << directions.index((1, 0))
        west = 1 << directions.index((-1, 0))
        self.assertTrue(all(graph['links'][index] & east for index in range(4)))
        self.assertTrue(all(not graph['links'][index] & west for index in range(1, 5)))

    def test_stock_spawn_ingress_rejects_an_uphill_step(self):
        graph = {
            'origin': [0.0, 0.0], 'cell_size': 1.0,
            'width': 3, 'height': 1,
            'heights_mm': [None, None, 0],
            'links': [0, 0, 0], 'hazards': [2, 2, 0],
        }

        class Terrain(object):
            def height(self, x, unused_z):
                return (2.0, 3.0, 0.0)[int(x)]

            def water_depth(self, unused_x, unused_z, unused_height):
                return 0.0

        class Obstacles(object):
            def surface_height(self, unused_x, unused_z):
                return None

            def blocked(self, unused_x, unused_z, unused_height, margin):
                return False

        directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                      (1, 0), (-1, 1), (0, 1), (1, 1))
        legacy = types.SimpleNamespace(
            DIRECTIONS=directions, WATER_DEPTH_LIMIT=0.9,
            VEHICLE_HALF_WIDTH=2.15, HAZARD_WATER=1, HAZARD_EDGE=2,
            _ground_height=lambda terrain, unused_obstacles, x, z:
                terrain.height(x, z),
            _node_point=lambda value, index: (float(index), 0.0),
        )
        with self.assertRaises(space.UnsafeBakeInputError):
            baker.find_downhill_spawn_ingress(
                graph, Terrain(), Obstacles(), (0.0, 0.0), (10.0, 0.0), legacy)


if __name__ == '__main__':
    unittest.main()
