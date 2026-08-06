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
