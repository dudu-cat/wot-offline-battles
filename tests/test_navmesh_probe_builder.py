import importlib.util
import io
import struct
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "tools/build_navmesh_probe.py"


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_navmesh_probe_under_test", BUILDER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NavmeshProbeBuilderTest(unittest.TestCase):
    def setUp(self):
        self.builder = load_builder()

    def test_packed_xml_round_trip_preserves_supported_value_types(self):
        b = self.builder
        nested = b.PackedElement(children=[
            (b"enabled", b.PackedValue(b.TYPE_BOOLEAN, True)),
            (b"resource", b.PackedValue(b.TYPE_STRING, b"chunk.cdata/worldNavmesh")),
            (b"vec2", b.PackedValue(b.TYPE_VECTOR, struct.pack("<ff", 1.0, 2.0))),
            (b"compressed", b.PackedValue(b.TYPE_COMPRESSED_STRING, b"abc")),
        ])
        root = b.PackedElement(children=[
            (b"zero", b.PackedValue(b.TYPE_INTEGER, 0)),
            (b"negative", b.PackedValue(b.TYPE_INTEGER, -300)),
            (b"nested", b.PackedValue(b.TYPE_ELEMENT, nested)),
        ])

        encoded = b.write_packed_xml(root)
        decoded = b.read_packed_xml(encoded)
        reencoded = b.write_packed_xml(decoded)

        self.assertEqual(encoded, reencoded)

    def test_probe_navmesh_uses_version_zero_clockwise_polygon(self):
        b = self.builder
        data = b.build_probe_navmesh((-199.5, 300.5, -100.5, 399.5))
        version, girth, polygons, edges = struct.unpack_from("<ifii", data, 0)
        min_height, max_height, vertex_count = struct.unpack_from("<ffi", data, 16)
        vertices = [
            struct.unpack_from("<ffi", data, 28 + index * 12)
            for index in range(vertex_count)
        ]

        self.assertEqual(0, version)
        self.assertAlmostEqual(0.5, girth)
        self.assertEqual((1, 4), (polygons, edges))
        self.assertEqual((-1000.0, 1000.0, 4), (min_height, max_height, vertex_count))
        self.assertEqual(
            [(-100.5, 300.5), (-199.5, 300.5), (-199.5, 399.5), (-100.5, 399.5)],
            [(x, z) for x, z, unused_neighbour in vertices],
        )
        self.assertEqual([-1] * 4, [neighbour for x, z, neighbour in vertices])

    def test_cdata_overlay_preserves_entries_and_adds_world_navmesh(self):
        b = self.builder
        original = io.BytesIO()
        with zipfile.ZipFile(original, "w") as archive:
            archive.writestr("terrain2/heights", b"terrain")
            archive.writestr("navmeshDirty", b"dirty")
        navmesh = b.build_probe_navmesh((-10.0, -10.0, 10.0, 10.0))

        result = b.add_world_navmesh_to_cdata(original.getvalue(), navmesh)

        with zipfile.ZipFile(io.BytesIO(result), "r") as archive:
            self.assertEqual(b"terrain", archive.read("terrain2/heights"))
            self.assertEqual(b"dirty", archive.read("navmeshDirty"))
            self.assertEqual(navmesh, archive.read("worldNavmesh"))

    def test_settings_and_chunk_nodes_are_injected(self):
        b = self.builder
        settings = b.write_packed_xml(b.PackedElement(children=[
            (b"bounds", b.PackedValue(b.TYPE_ELEMENT, b.PackedElement())),
        ]))
        chunk = b.write_packed_xml(b.PackedElement(children=[
            (b"model", b.PackedValue(b.TYPE_ELEMENT, b.PackedElement())),
        ]))

        settings_root = b.read_packed_xml(b.enable_client_navigation(settings))
        chunk_root = b.read_packed_xml(b.add_world_navmesh_reference(chunk, "fffe0003o"))

        navigation = dict(settings_root.children)[b"clientNavigation"].value
        self.assertTrue(dict(navigation.children)[b"enable"].value)
        navmesh = dict(chunk_root.children)[b"worldNavmesh"].value
        self.assertEqual(
            b"fffe0003o.cdata/worldNavmesh",
            dict(navmesh.children)[b"resource"].value,
        )


if __name__ == "__main__":
    unittest.main()
