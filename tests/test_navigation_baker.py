import importlib.util
import struct
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
        encoded = signed_height & 0xFFFF
        pixel = bytes((encoded & 255, encoded >> 8, 0, 255))
        png = rgba_png(69, 69, pixel)
        data = b"hmp\0" + struct.pack("<II", 69, 69) + b"\0" * 24 + png

        chunk = self.baker.HeightChunk(data)

        self.assertAlmostEqual(-6.482, chunk.values[0][0], places=3)
        self.assertAlmostEqual(-6.482, chunk.sample(50.0, 50.0), places=3)

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


if __name__ == "__main__":
    unittest.main()
