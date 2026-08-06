import importlib.util
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BAKER_PATH = ROOT / "tools/bake_navigation.py"


def load_baker():
    spec = importlib.util.spec_from_file_location(
        "navigation_baker_under_test", BAKER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload +
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def rgba_png(width, height, pixel):
    raw = b"".join(b"\0" + pixel * width for unused in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header) +
            png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))


class NavigationBakerTest(unittest.TestCase):
    def setUp(self):
        self.baker = load_baker()

    def test_height_chunk_decodes_signed_millimetres(self):
        signed_height = -6482
        pixel = struct.pack("<i", signed_height)
        png = rgba_png(69, 69, pixel)
        header = (b"hmp\0" + struct.pack("<IIIIffI", 69, 69, 0, 4,
                                          -6.482, -6.482, 0) + b"gnpq")
        data = header + png

        chunk = self.baker.HeightChunk(data)

        self.assertAlmostEqual(-6.482, chunk.values[0][0], places=3)
        self.assertAlmostEqual(-6.482, chunk.sample(50.0, 50.0), places=3)

    def test_height_chunk_uses_all_four_png_bytes_above_int16_range(self):
        height = 205231
        png = rgba_png(69, 69, struct.pack("<i", height))
        header = (b"hmp\0" + struct.pack("<IIIIffI", 69, 69, 0, 4,
                                          205.231, 205.231, 0) + b"gnpq")

        chunk = self.baker.HeightChunk(header + png)

        self.assertAlmostEqual(205.231, chunk.sample(50.0, 50.0), places=3)

    def test_bsp_uses_collision_triangles_and_skips_no_collide_material(self):
        triangle = struct.pack(
            "<9fI",
            0.0, 0.0, 0.0,
            1.0, 0.0, 0.0,
            0.0, 0.0, 1.0,
            0,
        )
        section = struct.pack("<IIII", 0x00505342, 1, 1, 0) + triangle

        solid = self.baker._bsp_triangles(section, ["wall"], {"wall": 0})
        ignored = self.baker._bsp_triangles(section, ["wall"], {"wall": 16})

        self.assertEqual(1, len(solid))
        self.assertEqual([], ignored)

    def test_bake_prunes_islands_outside_the_two_base_component(self):
        # Nodes 0-2 connect both bases; nodes 8-9 are an isolated ledge.
        graph = {
            "width": 5,
            "height": 2,
            "cell_size": 4.0,
            "origin": [0.0, 0.0],
            "heights_mm": [0, 0, 0, None, None, None, None, None, 0, 0],
            "links": [16, 24, 8, 0, 0, 0, 0, 0, 16, 8],
            "bake": {},
        }
        config = {
            "bases": ((0.0, 0.0), (8.0, 0.0)),
            "anchors": ((4.0, 0.0),),
        }

        retained = self.baker.retain_base_component(
            graph, config, minimum_fraction=0.5
        )
        validation = self.baker.validate_graph(graph, config)

        self.assertEqual({0, 1, 2}, retained)
        self.assertIsNone(graph["heights_mm"][8])
        self.assertEqual(2, graph["bake"]["source_components"])
        self.assertEqual(2, graph["bake"]["pruned_nodes"])
        self.assertEqual(1, validation["components"])

    def test_base_selection_ignores_a_closer_tiny_boundary_island(self):
        graph = {
            "width": 4,
            "height": 1,
            "cell_size": 4.0,
            "origin": [0.0, 0.0],
            "heights_mm": [0, 0, 0, 0],
            "links": [0, 16, 24, 8],
            "bake": {},
        }
        config = {
            "bases": ((0.0, 0.0), (12.0, 0.0)),
            "anchors": ((4.0, 0.0),),
        }

        retained = self.baker.retain_base_component(
            graph, config, minimum_fraction=0.7
        )

        self.assertEqual({1, 2, 3}, retained)
        self.assertIsNone(graph["heights_mm"][0])

    def test_edge_clearance_rejects_water_and_one_way_drops(self):
        class Terrain:
            def __init__(self, water=False, drop=False):
                self.water = water
                self.drop = drop

            def height(self, x, z):
                if self.drop and x >= 3.0:
                    return -4.0
                return 0.0

            def water_depth(self, x, z, ground):
                return 1.0 if self.water and x >= 3.0 else 0.0

        class Surfaces:
            @staticmethod
            def surface_height(x, z):
                return None

        self.assertTrue(self.baker._has_safe_edge_clearance(
            Terrain(), Surfaces(), 0.0, 0.0, 0.0
        ))
        self.assertFalse(self.baker._has_safe_edge_clearance(
            Terrain(water=True), Surfaces(), 0.0, 0.0, 0.0
        ))
        self.assertFalse(self.baker._has_safe_edge_clearance(
            Terrain(drop=True), Surfaces(), 0.0, 0.0, 0.0
        ))

    def test_bridge_deck_uses_collision_rails_instead_of_water_erosion(self):
        class Terrain:
            @staticmethod
            def height(x, z):
                return 0.0

            @staticmethod
            def water_depth(x, z, ground):
                return 0.0 if ground >= 2.0 else 10.0

        class Surfaces:
            @staticmethod
            def surface_height(x, z):
                return 2.0

        self.assertTrue(self.baker._has_safe_edge_clearance(
            Terrain(), Surfaces(), 0.0, 0.0, 2.0
        ))

    def test_bridge_deck_rejects_a_surface_cell_without_a_safe_shoulder(self):
        class Terrain:
            @staticmethod
            def height(x, z):
                return 0.0

            @staticmethod
            def water_depth(x, z, ground):
                return 0.0 if ground >= 2.0 else 10.0

        class Surfaces:
            @staticmethod
            def surface_height(x, z):
                if abs(x) < 0.1 and abs(z) < 0.1:
                    return 2.0
                return None

        self.assertFalse(self.baker._has_safe_edge_clearance(
            Terrain(), Surfaces(), 0.0, 0.0, 2.0
        ))

    def test_bridge_resource_name_is_the_surface_semantic_boundary(self):
        self.assertTrue(self.baker._is_bridge_model(
            "content/Environment/env035_Bridge/normal/lod0/deck.model"
        ))
        self.assertFalse(self.baker._is_bridge_model(
            "content/Environment/city_house/normal/lod0/roof.model"
        ))

    def test_bridge_surface_replaces_submerged_terrain_height(self):
        class Terrain:
            @staticmethod
            def height(x, z):
                return -3.0

        field = object.__new__(self.baker.ObstacleField)
        field.raster_size = 1.0
        field.surface_cells = {(0, 0): 2.5}

        self.assertEqual(
            2.5, self.baker._ground_height(Terrain(), field, 0.5, 0.5)
        )

    def test_bridge_surface_keeps_walkable_approach_ramps(self):
        field = object.__new__(self.baker.ObstacleField)
        deck = ((0.0, 3.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 4.0))
        ramp = ((0.0, 0.0, 0.0), (4.0, 0.8, 0.0), (0.0, 0.0, 4.0))
        wall = ((0.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 4.0))

        selected = field._bridge_deck_triangles((deck, ramp, wall))

        self.assertIn(id(deck), selected)
        self.assertIn(id(ramp), selected)
        self.assertNotIn(id(wall), selected)

    def test_bridge_segment_uses_the_narrow_collision_rail_margin(self):
        class Terrain:
            @staticmethod
            def height(x, z):
                return 0.0

            @staticmethod
            def water_depth(x, z, ground):
                return 0.0

        class Surfaces:
            def __init__(self):
                self.margins = []

            @staticmethod
            def surface_height(x, z):
                return 0.0

            def blocked(self, x, z, ground, margin):
                self.margins.append(margin)
                return False

        surfaces = Surfaces()

        self.assertTrue(self.baker._segment_clear(
            Terrain(), surfaces, (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
        ))
        self.assertEqual(
            {self.baker.BRIDGE_OBSTACLE_MARGIN}, set(surfaces.margins)
        )

    def test_low_obstacle_is_owned_by_local_tank_locomotion(self):
        low = self.baker.ModelShape((), (), (0.0, 0.0, 0.0),
                                    (10.0, 0.4, 1.0), "curb")
        wall = self.baker.ModelShape((), (), (0.0, 0.0, 0.0),
                                     (10.0, 2.0, 1.0), "wall")

        self.assertTrue(self.baker._is_local_obstacle(low))
        self.assertFalse(self.baker._is_local_obstacle(wall))

    def test_ground_detail_does_not_sever_a_road_but_a_wall_still_does(self):
        field = object.__new__(self.baker.ObstacleField)
        field.raster_size = 1.0
        field.cells = {(0, 0): [0.0, 0.4]}
        field.surface_cells = {}

        self.assertFalse(field.blocked(0.5, 0.5, 0.0, margin=0.1))

        field.cells[(0, 0)] = [0.0, 2.0]
        self.assertTrue(field.blocked(0.5, 0.5, 0.0, margin=0.1))

    def test_vertical_triangle_rasterizes_as_a_line_not_its_bounding_box(self):
        diagonal_wall = ((0.0, 0.0), (5.0, 5.0), (10.0, 10.0))

        self.assertFalse(self.baker._point_in_convex_polygon(
            (2.0, 8.0), diagonal_wall
        ))
        self.assertGreater(
            self.baker._distance_to_polygon((2.0, 8.0), diagonal_wall),
            4.0,
        )

    def test_bake_bounds_include_a_base_just_outside_arena_bounds(self):
        config = {
            "bounds": (-300.0, -300.0, 400.0, 400.0),
            "bases": ((305.0, -306.4), (300.0, 301.0)),
            "anchors": (),
        }

        bounds = self.baker._expanded_bounds(config, 4.0)

        self.assertLessEqual(bounds[1], -322.4)
        self.assertEqual(0.0, bounds[1] % 4.0)

    def test_reads_vector_and_text_ctf_bases_from_packed_arena_xml(self):
        probe_spec = importlib.util.spec_from_file_location(
            "packed_xml_test_helpers", ROOT / "tools/build_navmesh_probe.py"
        )
        probe = importlib.util.module_from_spec(probe_spec)
        probe_spec.loader.exec_module(probe)

        def element(children):
            return probe.PackedValue(
                probe.TYPE_ELEMENT, probe.PackedElement(children=children)
            )

        vector = probe.PackedValue(
            probe.TYPE_VECTOR, struct.pack("<2f", -47.5, -302.6)
        )
        text_position = probe.PackedValue(probe.TYPE_STRING, b"17.1 300.0")
        root = probe.PackedElement(children=[
            (b"gameplayTypes", element([
                (b"ctf", element([
                    (b"teamBasePositions", element([
                        (b"team1", element([(b"position1", vector)])),
                        (b"team2", element([(b"position1", text_position)])),
                    ])),
                ])),
            ])),
        ])

        bases = self.baker.ctf_bases_from_arena_data(
            probe.write_packed_xml(root)
        )

        self.assertAlmostEqual(-47.5, bases[0][0], places=3)
        self.assertAlmostEqual(-302.6, bases[0][1], places=3)
        self.assertEqual((17.1, 300.0), bases[1])

    def test_tactical_base_validation_rejects_swapped_or_other_mode_data(self):
        config = {"bases": ((-47.5, -302.6), (17.1, 300.0))}

        self.baker.validate_tactical_bases(
            "04_himmelsdorf", config,
            ((-47.5, -302.6), (17.1, 300.0)),
        )
        with self.assertRaisesRegex(ValueError, "tactical team1 base differs"):
            self.baker.validate_tactical_bases(
                "04_himmelsdorf", config,
                ((305.2, -306.4), (300.7, 300.9)),
            )

    def test_route_anchor_beyond_server_arrival_radius_is_rejected(self):
        graph = {
            "width": 4, "height": 1, "cell_size": 4.0,
            "origin": [0.0, 0.0],
            "heights_mm": [0, 0, 0, 0],
            "links": [16, 24, 24, 8],
        }
        config = {
            "bases": ((0.0, 0.0), (12.0, 0.0)),
            "anchors": ((25.1, 0.0),),
        }

        with self.assertRaisesRegex(ValueError, "route anchor is too far"):
            self.baker.validate_graph(graph, config)

    def test_tactical_route_must_pass_through_representative_hold_gate(self):
        width = 5
        height = 5
        links = []
        for z in range(height):
            for x in range(width):
                mask = 0
                for index, (dx, dz) in enumerate(self.baker.DIRECTIONS):
                    if 0 <= x + dx < width and 0 <= z + dz < height:
                        mask |= 1 << index
                links.append(mask)
        graph = {
            "width": width, "height": height, "cell_size": 4.0,
            "origin": [0.0, 0.0], "heights_mm": [0] * (width * height),
            "links": links, "bake": {},
        }
        config = {
            "bases": ((0.0, 0.0), (16.0, 16.0)),
            "routes": ({
                "team": 1, "id": "perimeter", "capacity": 1,
                "risk": 0.5, "role_weights": {},
                "points": ((0.0, 0.0, False),
                           (0.0, 16.0, True),
                           (16.0, 16.0, False)),
            },),
        }

        routes = self.baker.bake_tactical_routes(graph, config)

        self.assertIn([0.0, 16.0, True], routes["1"][0]["waypoints"])

    def test_route_geometry_rejects_hairpins_and_self_intersections(self):
        self.assertIsNone(self.baker._route_geometry_issue(
            ((0.0, 0.0, False), (8.0, 0.0, False),
             (16.0, 8.0, False))
        ))
        self.assertIn("hairpin", self.baker._route_geometry_issue(
            ((0.0, 0.0, False), (8.0, 0.0, False),
             (1.0, 1.0, False))
        ))
        self.assertIn("self-intersection", self.baker._route_geometry_issue(
            ((0.0, 0.0, False), (8.0, 8.0, False),
             (0.0, 8.0, False), (8.0, 0.0, False))
        ))

    def test_route_sampling_preserves_a_tight_path_bend(self):
        graph = {
            "width": 5, "height": 3, "cell_size": 4.0,
            "origin": [0.0, 0.0], "heights_mm": [0] * 15,
        }
        path = (0, 5, 10, 11, 12, 13, 14, 9, 4)

        sampled = self.baker._sample_route_path(
            graph, path, set(), maximum_points=3
        )

        self.assertTrue(any(point[1] == 8.0 for point in sampled[1:-1]))

    def test_shipped_routes_have_safe_sampled_geometry(self):
        graph_dir = ROOT / "scripts/client/gui/mods/offhangar/navgraphs"
        for graph_path in sorted(graph_dir.glob("*.json")):
            if graph_path.name == "manifest.json":
                continue
            graph = json.loads(graph_path.read_text())
            for team in ("1", "2"):
                for route in graph["routes"][team]:
                    self.assertIsNone(
                        self.baker._route_geometry_issue(route["waypoints"]),
                        (graph["map"], team, route["id"]),
                    )

    def test_batch_publish_writes_a_checksum_manifest_after_all_graphs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            target = root / "target"
            staging.mkdir()
            target.mkdir()
            (staging / "01_karelia.json").write_text("one\n")
            (staging / "02_malinovka.json").write_text("two\n")
            self.baker.default_output = lambda name: str(target / (name + ".json"))

            self.baker._publish_staged_batch(
                str(staging), ["01_karelia", "02_malinovka"]
            )

            manifest = json.loads((target / "manifest.json").read_text())
            self.assertEqual(
                ["01_karelia", "02_malinovka"],
                [record["map"] for record in manifest["maps"]],
            )
            self.assertTrue((target / "01_karelia.json").is_file())
            self.assertTrue((target / "02_malinovka.json").is_file())


if __name__ == "__main__":
    unittest.main()
