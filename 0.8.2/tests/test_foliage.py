import importlib.util
import math
import random
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

    def test_cap_is_applied_before_visiting_later_segment_cells(self):
        """A saturated near cell must make the rest of the ray irrelevant."""
        volumes = [instance(cx=value) for value in (-47.0, -46.0, -45.0, -44.0)]
        foliage_map = self.make_map(volumes, {"-2,0": list(range(4))})

        class FailAfterSaturatedCell(dict):
            def get(self, key, default=None):
                if key != (-2, 0):
                    raise AssertionError(
                        "foliage traversal continued after reaching the cap"
                    )
                return dict.get(self, key, default)

        foliage_map.cells = FailAfterSaturatedCell(foliage_map.cells)

        self.assertEqual(
            self.foliage.FOLIAGE_CAMOUFLAGE_LIMIT,
            foliage_map.camouflage_bonus(
                (-48.0, 0.0, 0.0), (48.0, 0.0, 0.0)
            ),
        )

    def test_single_pass_result_matches_the_previous_two_pass_reference(self):
        """Optimizing traversal must preserve every pair-specific result."""
        rng = random.Random(0x8251)
        volumes = []
        cells = {}
        for unused_index in range(96):
            cx = rng.uniform(-96.0, 96.0)
            cz = rng.uniform(-96.0, 96.0)
            radius = rng.uniform(1.0, 5.0)
            row = instance(cx=cx, cz=cz, radius=radius)
            row[1] = rng.uniform(-2.0, 1.0)
            row[3] = row[1] + rng.uniform(2.0, 8.0)
            row[8] = rng.choice((0.05, 0.10, 0.15, 0.20))
            instance_id = len(volumes)
            volumes.append(row)
            for cell_x in range(
                int(math.floor((cx - radius) / 32.0)),
                int(math.floor((cx + radius) / 32.0)) + 1,
            ):
                for cell_z in range(
                    int(math.floor((cz - radius) / 32.0)),
                    int(math.floor((cz + radius) / 32.0)) + 1,
                ):
                    cells.setdefault("%d,%d" % (cell_x, cell_z), []).append(
                        instance_id
                    )
        foliage_map = self.make_map(volumes, cells)

        def previous_two_pass(observer, target, fired_recently):
            start = (
                float(observer[0]),
                float(observer[1]) + self.foliage.OBSERVER_EYE_HEIGHT,
                float(observer[2]),
            )
            end = (
                float(target[0]),
                float(target[1]) + self.foliage.TARGET_CHECK_HEIGHT,
                float(target[2]),
            )
            candidate_ids = []
            seen = set()
            for cell in self.foliage._segment_cells(
                start, end, foliage_map.cell_size
            ):
                for instance_id in foliage_map.cells.get(cell, ()):
                    instance_id = int(instance_id)
                    if instance_id not in seen:
                        seen.add(instance_id)
                        candidate_ids.append(instance_id)
            bonus = 0.0
            for instance_id in candidate_ids:
                row = foliage_map.instances[instance_id]
                if fired_recently:
                    dx = float(target[0]) - float(row[0])
                    dz = float(target[2]) - float(row[2])
                    if math.sqrt(dx * dx + dz * dz) <= (
                        self.foliage.FIRE_TRANSPARENCY_DISTANCE
                        + float(row[9])
                    ):
                        continue
                if self.foliage._intersects(row, start, end):
                    bonus += float(row[8])
                    if bonus >= self.foliage.FOLIAGE_CAMOUFLAGE_LIMIT:
                        return self.foliage.FOLIAGE_CAMOUFLAGE_LIMIT
            return min(
                self.foliage.FOLIAGE_CAMOUFLAGE_LIMIT, max(0.0, bonus)
            )

        pairs = [
            ((-96.0, 0.0, 0.0), (96.0, 0.0, 0.0)),
            ((0.0, 6.0, -96.0), (0.0, 6.0, 96.0)),
            ((-64.0, -1.0, -64.0), (64.0, 2.0, 64.0)),
        ]
        for unused_index in range(80):
            pairs.append(
                (
                    (
                        rng.uniform(-110.0, 110.0),
                        rng.uniform(-2.0, 8.0),
                        rng.uniform(-110.0, 110.0),
                    ),
                    (
                        rng.uniform(-110.0, 110.0),
                        rng.uniform(-2.0, 8.0),
                        rng.uniform(-110.0, 110.0),
                    ),
                )
            )
        for observer, target in pairs:
            for fired_recently in (False, True):
                self.assertEqual(
                    previous_two_pass(observer, target, fired_recently),
                    foliage_map.camouflage_bonus(
                        observer, target, fired_recently
                    ),
                    (observer, target, fired_recently),
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
