import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = (ROOT / 'ports' / '0.9.22' / 'tools' /
          'bake_destructibles_0922.py')
DATA_ROOT = ROOT / 'ports' / '0.9.22' / 'destructibles'
CLIENT_SCRIPTS = (ROOT / 'ports' / '0.9.22' / 'src' / 'res' /
                  'scripts' / 'client')


def load_baker():
    spec = importlib.util.spec_from_file_location(
        'offline_lan_0922_destructibles_baker', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DestructiblesBaker0922Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baker = load_baker()

    def test_contract_is_pinned_to_client_1513(self):
        self.assertEqual('offline-lan-0922-destructible-catalog',
                         self.baker.FORMAT_NAME)
        self.assertEqual(3, self.baker.FORMAT_VERSION)
        self.assertEqual(
            'offline-lan-0922-destructible-catalog-manifest',
            self.baker.MANIFEST_FORMAT)
        self.assertEqual('0.9.22.0.1-cn-1513', self.baker.GAME_VERSION)
        self.assertEqual(73, self.baker.NORMAL_MATERIAL_KIND_MIN)
        self.assertEqual(1000, self.baker.LOCATOR_QUANTIZATION)

    def test_locator_signature_is_world_origin_plus_basis_and_symmetric(self):
        transform = (
            2.0, 0.25, 0.0, 0.0,
            -0.5, 3.0, 0.0, 0.0,
            0.0, 0.0, -4.0, 0.0,
            12.3456, 0.0, -7.8904, 1.0,
        )
        self.assertEqual(
            (12346, 0, -7890, 2000, 250, 0, -500, 3000, 0,
             0, 0, -4000),
            self.baker._locator_signature(transform))
        self.assertEqual(1, self.baker._quantize_locator_value(0.0005))
        self.assertEqual(-1, self.baker._quantize_locator_value(-0.0005))

    def test_conflicting_locator_signature_fails_closed(self):
        first = (0.0,) * 16
        second = list(first)
        second[12] = 0.0004
        signature = self.baker._locator_signature(first)
        self.assertEqual(signature, self.baker._locator_signature(second))
        located = {}
        self.baker._record_locator(located, signature, 0, 'fragile.model')
        # An indistinguishable placement sharing the same box is safe.
        self.baker._record_locator(located, signature, 0, 'fragile.model')
        with self.assertRaisesRegex(ValueError, 'multiple boxes'):
            self.baker._record_locator(
                located, signature, 1, 'fragile.model')

    def test_model_filename_join_is_separator_only_and_fail_closed(self):
        self.assertEqual(
            'content/Test/normal/lod0/Test.model',
            self.baker.model_filename_from_primitive(
                r'content\Test\normal\lod0\Test.primitives'))
        self.assertEqual(
            'content/Test/normal/lod0/Test.model',
            self.baker.model_filename_from_primitive(
                'content/Test/normal/lod2/Test.primitives'))
        with self.assertRaisesRegex(ValueError, 'not a primitives resource'):
            self.baker.model_filename_from_primitive(
                'content/Test/normal/lod0/Test.visual')
        with self.assertRaisesRegex(ValueError, 'invalid destructible'):
            self.baker.normalize_model_filename('../Test.model')

    def test_complete_real_batch_matches_manifest_checksums_and_schema(self):
        manifest_path = DATA_ROOT / 'manifest.json'
        self.assertTrue(manifest_path.is_file(),
                        'complete #1513 destructible batch is missing')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(self.baker.MANIFEST_FORMAT, manifest['format'])
        self.assertEqual(self.baker.GAME_VERSION, manifest['game_version'])
        self.assertEqual(self.baker.LOCATOR_QUANTIZATION,
                         manifest['locator_quantization'])
        self.assertEqual(len(self.baker.SUPPORTED_MAPS),
                         manifest['census']['maps'])
        self.assertEqual(
            list(self.baker.SUPPORTED_MAPS),
            [record['map'] for record in manifest['maps']])
        aggregate = {
            key: 0 for key in (
                'resources', 'falling_resources', 'fragile_resources',
                'structure_resources', 'boxes', 'falling_boxes',
                'fragile_boxes', 'structure_boxes', 'instances',
                'falling_instances', 'fragile_instances',
                'structure_instances', 'variant_resources',
                'locator_resources', 'locators',
                'falling_locator_resources', 'falling_locators',
                'fragile_locator_resources', 'fragile_locators')}
        for key in (
                'instance_signatures', 'falling_instance_signatures',
                'fragile_instance_signatures',
                'structure_instance_signatures',
                'ambiguous_instance_signatures',
                'ambiguous_instance_candidates'):
            aggregate[key] = 0
        max_boxes = 0
        fragile_locator_instance_count = 0
        falling_locator_instance_count = 0
        for record in manifest['maps']:
            path = DATA_ROOT / record['file']
            self.assertTrue(path.is_file(), record['map'])
            self.assertEqual(record['sha256'],
                             hashlib.sha256(path.read_bytes()).hexdigest())
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(record['map'], data['map'])
            self.assertEqual(self.baker.FORMAT_NAME, data['format'])
            self.assertEqual(self.baker.GAME_VERSION, data['game_version'])
            self.assertEqual(self.baker.LOCATOR_QUANTIZATION,
                             data['locator_quantization'])
            self.assertEqual(
                manifest['destructibles_xml_sha256'],
                data['source']['destructibles_xml_sha256'])
            self.assertEqual(record['map_package_sha256'],
                             data['source']['map_package_sha256'])
            self.assertEqual(record['space_bin_sha256'],
                             data['source']['space_bin_sha256'])
            self.assertGreater(len(data['resources']), 0)
            for filename, resource in data['resources'].items():
                self.assertTrue(filename.endswith('.model'))
                self.assertIn(resource['kind'],
                              ('falling', 'fragile', 'structure'))
                self.assertGreater(resource['instance_count'], 0)
                self.assertEqual(sorted(resource['bsmo_model_ids']),
                                 resource['bsmo_model_ids'])
                self.assertGreater(len(resource['boxes']), 0)
                for box in resource['boxes']:
                    self.assertEqual(7, len(box))
                    self.assertLess(box[0], box[3])
                    self.assertLess(box[1], box[4])
                    self.assertLess(box[2], box[5])
                    if resource['kind'] != 'structure':
                        self.assertIsNone(box[6])
                    else:
                        self.assertIsInstance(box[6], int)
                        self.assertGreaterEqual(box[6], 73)
                locators = resource.get('locators')
                if (resource['kind'] != 'structure' and
                        len(resource['boxes']) > 1):
                    self.assertTrue(locators)
                    if resource['kind'] == 'fragile':
                        fragile_locator_instance_count += resource[
                            'instance_count']
                    else:
                        falling_locator_instance_count += resource[
                            'instance_count']
                else:
                    self.assertIsNone(locators)
                seen_signatures = set()
                for locator in locators or ():
                    self.assertEqual(13, len(locator))
                    self.assertTrue(all(type(value) is int
                                        for value in locator))
                    self.assertNotIn(tuple(locator[:12]), seen_signatures)
                    seen_signatures.add(tuple(locator[:12]))
                    self.assertGreaterEqual(locator[12], 0)
                    self.assertLess(locator[12], len(resource['boxes']))
            seen_instance_signatures = set()
            instance_kinds = {kind: 0 for kind in (
                'falling', 'fragile', 'structure')}
            self.assertEqual(
                sorted(data['instances'], key=lambda row: tuple(row[:12])),
                data['instances'])
            for row in data['instances']:
                self.assertEqual(14, len(row))
                self.assertTrue(all(type(value) is int
                                    for value in row[:12]))
                signature = tuple(row[:12])
                self.assertNotIn(signature, seen_instance_signatures)
                seen_instance_signatures.add(signature)
                resource = data['resources'][row[12]]
                if resource['kind'] == 'structure':
                    self.assertIsNone(row[13])
                else:
                    self.assertIsInstance(row[13], int)
                    self.assertGreaterEqual(row[13], 0)
                    self.assertLess(row[13], len(resource['boxes']))
                instance_kinds[resource['kind']] += 1
            self.assertEqual(
                sorted(data['ambiguous_instances'],
                       key=lambda row: tuple(row[:12])),
                data['ambiguous_instances'])
            ambiguous_candidates = 0
            for row in data['ambiguous_instances']:
                self.assertEqual(13, len(row))
                signature = tuple(row[:12])
                self.assertNotIn(signature, seen_instance_signatures)
                seen_instance_signatures.add(signature)
                self.assertGreaterEqual(len(row[12]), 2)
                self.assertEqual(
                    sorted(row[12], key=lambda candidate: (
                        candidate[0], -1 if candidate[1] is None
                        else candidate[1])), row[12])
                ambiguous_candidates += len(row[12])
                for filename, box_index in row[12]:
                    resource = data['resources'][filename]
                    if resource['kind'] == 'structure':
                        self.assertIsNone(box_index)
                    else:
                        self.assertIsInstance(box_index, int)
                        self.assertGreaterEqual(box_index, 0)
                        self.assertLess(box_index, len(resource['boxes']))
            self.assertEqual(len(data['instances']),
                             data['census']['instance_signatures'])
            self.assertEqual(instance_kinds['falling'],
                             data['census']['falling_instance_signatures'])
            self.assertEqual(instance_kinds['fragile'],
                             data['census']['fragile_instance_signatures'])
            self.assertEqual(instance_kinds['structure'],
                             data['census']['structure_instance_signatures'])
            self.assertEqual(len(data['ambiguous_instances']),
                             data['census'][
                                 'ambiguous_instance_signatures'])
            self.assertEqual(ambiguous_candidates,
                             data['census'][
                                 'ambiguous_instance_candidates'])
            for key in aggregate:
                aggregate[key] += data['census'][key]
            max_boxes = max(max_boxes,
                            data['census']['max_boxes_per_resource'])
        self.assertEqual(aggregate, dict(
            (key, manifest['census'][key]) for key in aggregate))
        self.assertEqual(max_boxes,
                         manifest['census']['max_boxes_per_resource'])
        self.assertEqual(18,
                         manifest['census']['fragile_locator_resources'])
        self.assertEqual(534, manifest['census']['fragile_locators'])
        self.assertEqual(1,
                         manifest['census']['falling_locator_resources'])
        self.assertEqual(103, manifest['census']['falling_locators'])
        # One D-Day haystack placement is duplicated at the same transform
        # (world Y differs only 7.6e-06) and safely shares the same box index.
        self.assertEqual(535, fragile_locator_instance_count)
        self.assertEqual(103, falling_locator_instance_count)
        self.assertEqual(61625, manifest['census']['instance_signatures'])
        self.assertEqual(5754,
                         manifest['census']['falling_instance_signatures'])
        self.assertEqual(52853,
                         manifest['census']['fragile_instance_signatures'])
        self.assertEqual(3018,
                         manifest['census']['structure_instance_signatures'])
        self.assertEqual(11,
                         manifest['census'][
                             'ambiguous_instance_signatures'])
        self.assertEqual(28,
                         manifest['census'][
                             'ambiguous_instance_candidates'])

        eiffel = json.loads(
            (DATA_ROOT / '112_eiffel_tower_ctf.json').read_text(
                encoding='utf-8'))
        self.assertEqual(1, len(eiffel['ambiguous_instances']))
        self.assertEqual(5, len(eiffel['ambiguous_instances'][0][12]))
        self.assertTrue(all(
            'env_112_04_TrocaderoFountain_' in candidate[0]
            for candidate in eiffel['ambiguous_instances'][0][12]))

        dday = json.loads(
            (DATA_ROOT / '101_dday.json').read_text(encoding='utf-8'))
        repeated_structure = next(
            row for row in dday['ambiguous_instances']
            if len(row[12]) == 2 and row[12][0] == row[12][1] and
            dday['resources'][row[12][0][0]]['kind'] == 'structure')
        self.assertIsNone(repeated_structure[12][0][1])

    def test_highway_contains_exact_poles_fence_truck_and_shed(self):
        data = json.loads(
            (DATA_ROOT / '45_north_america.json').read_text(
                encoding='utf-8'))
        resources = data['resources']
        pole = resources[
            'content/Environment/envAM_009_Poles/normal/lod0/'
            'envAM_009_Poles_01.model']
        self.assertEqual('falling', pole['kind'])
        self.assertEqual([1], pole['bsmo_model_ids'])
        self.assertEqual(88, pole['instance_count'])
        self.assertGreater(pole['boxes'][0][4], 9.04)
        self.assertIsNone(pole['boxes'][0][6])
        fence = resources[
            'content/GatesAndFences/gafNW_001_VillageFance/normal/lod0/'
            'gafNW_001_VillageFance_gate_new.model']
        truck = resources[
            'content/Environment/envAM_011_Truck/normal/lod0/'
            'envAM_011_Truck01.model']
        shed = resources[
            'content/Buildings/bldAM_002_SmallShed/normal/lod0/'
            'bldAM_002_SmallShed.model']
        self.assertEqual('fragile', fence['kind'])
        self.assertEqual('fragile', truck['kind'])
        self.assertEqual('fragile', shed['kind'])
        self.assertEqual(277, fence['instance_count'])
        self.assertEqual(12, truck['instance_count'])
        self.assertEqual(9, shed['instance_count'])

    def test_ensk_contains_exact_shed_modules_and_long_fence(self):
        data = json.loads(
            (DATA_ROOT / '06_ensk.json').read_text(encoding='utf-8'))
        resources = data['resources']
        shed = resources[
            'content/Buildings/bld002_MiddleWoodShed/normal/lod0/'
            'bld002_MiddleWoodShed.model']
        self.assertEqual('structure', shed['kind'])
        self.assertEqual([73, 74], [box[6] for box in shed['boxes']])
        self.assertEqual([354, 356], shed['bsmo_model_ids'])
        little_shed = resources[
            'content/Buildings/bld003_LittleWoodShed/normal/lod0/'
            'bld003_LittleWoodShed.model']
        self.assertEqual([73], [box[6] for box in little_shed['boxes']])
        fence = resources[
            'content/GatesAndFences/gaf011_Fence/normal/lod0/'
            'gaf011_FenceTile1.model']
        self.assertEqual('fragile', fence['kind'])
        self.assertLess(fence['boxes'][0][2], -8.29)
        self.assertIsNone(fence['boxes'][0][6])

    def test_runtime_loader_accepts_real_ensk_and_rejects_bad_hash_and_box(self):
        package_names = ('gui', 'gui.mods', 'gui.mods.offline_lan_0922')
        saved = dict((name, sys.modules.get(name)) for name in package_names)
        saved_schema = sys.modules.get(
            'gui.mods.offline_lan_0922.navigation_graph_schema')
        saved_navigation = sys.modules.get(
            'gui.mods.offline_lan_0922.prebaked_navigation')
        try:
            for name in package_names:
                module = types.ModuleType(name)
                module.__path__ = []
                sys.modules[name] = module
            schema_path = (CLIENT_SCRIPTS / 'gui' / 'mods' /
                           'offline_lan_0922' /
                           'navigation_graph_schema.py')
            schema_spec = importlib.util.spec_from_file_location(
                'gui.mods.offline_lan_0922.navigation_graph_schema',
                schema_path)
            schema = importlib.util.module_from_spec(schema_spec)
            schema_spec.loader.exec_module(schema)
            sys.modules[schema_spec.name] = schema
            with tempfile.TemporaryDirectory() as directory:
                navigation = types.ModuleType(
                    'gui.mods.offline_lan_0922.prebaked_navigation')
                navigation.mod_dir = lambda: directory
                sys.modules[navigation.__name__] = navigation
                loader_path = (CLIENT_SCRIPTS / 'gui' / 'mods' /
                               'offline_lan_0922' /
                               'prebaked_destructibles.py')
                loader_spec = importlib.util.spec_from_file_location(
                    'gui.mods.offline_lan_0922.prebaked_destructibles_test',
                    loader_path)
                loader = importlib.util.module_from_spec(loader_spec)
                loader_spec.loader.exec_module(loader)
                target = Path(directory) / 'destructibles'
                target.mkdir()
                for path in DATA_ROOT.glob('*.json'):
                    (target / path.name).write_bytes(path.read_bytes())
                loaded = loader.load_catalog('spaces/06_ensk')
                self.assertEqual('06_ensk', loaded['map'])
                self.assertIn(
                    'content/GatesAndFences/gaf011_Fence/normal/lod0/'
                    'gaf011_FenceTile1.model', loaded['resources'])

                manifest_path = target / 'manifest.json'
                manifest = json.loads(manifest_path.read_text())
                manifest['maps'][0]['sha256'] = '0' * 64
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    loader.load_catalog('01_karelia')

                manifest['maps'][0]['sha256'] = hashlib.sha256(
                    (target / '01_karelia.json').read_bytes()).hexdigest()
                manifest['locator_quantization'] = 999
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError,
                                             'manifest is incompatible'):
                    loader.load_catalog('01_karelia')

                manifest_path.unlink()
                with self.assertRaisesRegex(ValueError,
                                             'manifest is missing'):
                    loader.load_catalog('06_ensk')

                # A wholly absent optional catalog remains a normal None;
                # battle startup owns the supported-map requirement.
                for path in target.glob('*.json'):
                    path.unlink()
                target.rmdir()
                self.assertIsNone(loader.load_catalog('06_ensk'))

                # Direct validation also fails closed on a degenerate box.
                bad = copy.deepcopy(loaded)
                first = next(iter(bad['resources'].values()))
                first['boxes'][0][3] = first['boxes'][0][0]
                with self.assertRaisesRegex(ValueError, 'box is invalid'):
                    loader._validate(bad, '06_ensk')

                ambiguous = json.loads(
                    (DATA_ROOT / '35_steppes.json').read_text())
                bad_locator = copy.deepcopy(ambiguous)
                target_resource = next(
                    resource for resource in
                    bad_locator['resources'].values()
                    if resource.get('locators'))
                target_resource['locators'][0] = [0] * 12 + [999]
                with self.assertRaisesRegex(ValueError,
                                             'locator is invalid'):
                    loader._validate(bad_locator, '35_steppes')

                missing_locator = copy.deepcopy(ambiguous)
                target_resource = next(
                    resource for resource in
                    missing_locator['resources'].values()
                    if resource.get('locators'))
                del target_resource['locators']
                with self.assertRaisesRegex(
                        ValueError, 'has no instance locators'):
                    loader._validate(missing_locator, '35_steppes')
        finally:
            for name, value in saved.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value
            for name, value in (
                    ('gui.mods.offline_lan_0922.navigation_graph_schema',
                     saved_schema),
                    ('gui.mods.offline_lan_0922.prebaked_navigation',
                     saved_navigation)):
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value


if __name__ == '__main__':
    unittest.main()
