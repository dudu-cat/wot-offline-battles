import importlib.util
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/bake_foliage.py"


def load_baker():
    spec = importlib.util.spec_from_file_location("offhangar_foliage_baker", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FoliageBakerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baker = load_baker()

    def test_taxonomy_matches_client_asset_name_fragments(self):
        tokens = ("bush", "cedar", "shrub")
        self.assertTrue(self.baker.is_bush_resource(
            "speedtree/01_Karelia/Juniper_bush.spt", tokens
        ))
        self.assertTrue(self.baker.is_bush_resource(
            "speedtree/31_airfield/Cedar_02.spt", tokens
        ))
        self.assertFalse(self.baker.is_bush_resource(
            "speedtree/15_Komarin/Oak.spt", tokens
        ))

    def test_ctree_header_supplies_exact_local_bounds(self):
        data = struct.pack("<I6f", 105, -2.0, -1.0, -3.0, 4.0, 5.0, 6.0)
        self.assertEqual(
            ((-2.0, -1.0, -3.0), (4.0, 5.0, 6.0)),
            self.baker.ctree_bounds(data),
        )

    def test_instance_transform_is_invertible_and_world_placed(self):
        transform = (2.0, 0.0, 0.0,
                     0.0, 1.0, 0.0,
                     0.0, 0.0, 3.0,
                     25.0, 4.0, 75.0)
        row, bounds = self.baker.foliage_instance(
            ((-1.0, 0.0, -1.0), (1.0, 4.0, 1.0)),
            transform, 2, -1,
        )
        self.assertEqual([225.0, 4.0, -25.0, 8.0], row[:4])
        self.assertAlmostEqual(0.5, row[4])
        self.assertAlmostEqual(1.0 / 3.0, row[7], places=4)
        self.assertEqual((223.0, -28.0, 227.0, -22.0), bounds)

    def test_sideways_authored_bush_uses_its_horizontal_yz_projection(self):
        transform = (0.0, 1.0, 0.0,
                     -1.0, 0.0, 0.0,
                     0.0, 0.0, 1.0,
                     0.0, 2.0, 0.0)
        row, bounds = self.baker.foliage_instance(
            ((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0)),
            transform, 0, 0,
        )
        self.assertEqual((-2.0, -3.0, 2.0, 3.0), bounds)
        self.assertEqual([-0.0, 1.0, 0.0, 3.0], row[:4])


if __name__ == "__main__":
    unittest.main()
