import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/client/gui/mods/offhangar/foliage.py"


def load_foliage():
    spec = importlib.util.spec_from_file_location("offhangar_foliage", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def instance(cx=0.0, cz=0.0, strength=0.15, radius=2.83):
    # Four-metre square horizontal volume, 0..5 m high.
    return [cx, 0.0, cz, 5.0, 0.5, 0.0, 0.0, 0.5, strength, radius]


class FoliageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.foliage = load_foliage()

    def make_map(self, instances, cells=None):
        return self.foliage.FoliageMap({
            "map": "test",
            "cell_size": 32.0,
            "instances": instances,
            "cells": cells or {"0,0": list(range(len(instances)))},
        })

    def test_intersecting_bush_adds_pair_specific_camouflage(self):
        foliage_map = self.make_map([instance()])
        self.assertAlmostEqual(
            0.15,
            foliage_map.camouflage_bonus((-10.0, 0.0, 0.0),
                                          (10.0, 0.0, 0.0)),
        )
        self.assertEqual(
            0.0,
            foliage_map.camouflage_bonus((-10.0, 0.0, 5.0),
                                          (10.0, 0.0, 5.0)),
        )

    def test_stacked_bushes_stop_at_historical_sixty_percent_limit(self):
        volumes = [instance(cx=value) for value in (-8.0, -4.0, 0.0, 4.0, 8.0)]
        foliage_map = self.make_map(volumes, {"0,0": list(range(5)),
                                              "-1,0": list(range(5))})
        self.assertAlmostEqual(
            self.foliage.FOLIAGE_CAMOUFLAGE_LIMIT,
            foliage_map.camouflage_bonus((-12.0, 0.0, 0.0),
                                          (12.0, 0.0, 0.0)),
        )

    def test_shot_only_suppresses_foliage_within_fifteen_metres(self):
        near = self.make_map([instance(cx=0.0)])
        self.assertEqual(
            0.0,
            near.camouflage_bonus((-20.0, 0.0, 0.0),
                                   (10.0, 0.0, 0.0), True),
        )
        far = self.make_map([instance(cx=0.0)], {"0,0": [0], "-1,0": [0]})
        self.assertAlmostEqual(
            0.15,
            far.camouflage_bonus((-20.0, 0.0, 0.0),
                                  (30.0, 0.0, 0.0), True),
        )

    def test_height_must_cover_the_sight_segment(self):
        low = instance()
        low[3] = 1.0
        foliage_map = self.make_map([low])
        self.assertEqual(
            0.0,
            foliage_map.camouflage_bonus((-10.0, 10.0, 0.0),
                                          (10.0, 10.0, 0.0)),
        )


if __name__ == "__main__":
    unittest.main()
