import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/client/gui/mods/offhangar/artillery_arc_queue.py"


def load_module():
    spec = importlib.util.spec_from_file_location("artillery_arc_queue", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ArtilleryArcQueueTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    @staticmethod
    def candidate(name, points):
        return {"name": name, "path": tuple(points)}

    def test_long_arc_never_exceeds_the_per_frame_ray_budget(self):
        queue = self.module.ArcProbeQueue()
        candidate = self.candidate(
            "low", [(float(index), 2.0, 0.0) for index in range(11)]
        )
        queue.request("shot", (candidate,), (10.0, 2.0, 0.0), 1.0)

        self.assertEqual(4, queue.advance(1.0, 4, lambda _a, _b: None))
        self.assertEqual(4, queue.advance(1.1, 4, lambda _a, _b: None))
        self.assertEqual(2, queue.advance(1.2, 4, lambda _a, _b: None))

        ready, result = queue.result("shot", 1.2)
        self.assertTrue(ready)
        self.assertIs(candidate, result)

    def test_blocked_low_arc_continues_with_the_high_arc(self):
        queue = self.module.ArcProbeQueue()
        low = self.candidate("low", ((0, 0, 0), (1, 0, 0), (2, 0, 0)))
        high = self.candidate("high", ((0, 5, 0), (1, 6, 0), (2, 5, 0)))
        queue.request("shot", (low, high), (2, 5, 0), 2.0)

        def probe(first, _second):
            if first[1] == 0:
                return (0.5, 0.0, 20.0)
            return None

        self.assertEqual(3, queue.advance(2.0, 4, probe))
        ready, result = queue.result("shot", 2.0)
        self.assertTrue(ready)
        self.assertIs(high, result)

    def test_world_hit_at_target_accepts_the_arc_immediately(self):
        queue = self.module.ArcProbeQueue(target_slop=7.0)
        candidate = self.candidate(
            "low", ((0, 0, 0), (10, 0, 0), (20, 0, 0))
        )
        queue.request("shot", (candidate,), (20, 0, 0), 3.0)

        used = queue.advance(3.0, 4, lambda _a, _b: (18.0, 0.0, 0.0))
        ready, result = queue.result("shot", 3.0)

        self.assertEqual(1, used)
        self.assertTrue(ready)
        self.assertIs(candidate, result)

    def test_queue_limit_rejects_new_work_without_displacing_old_work(self):
        queue = self.module.ArcProbeQueue(max_jobs=1)
        candidate = self.candidate("low", ((0, 0, 0), (1, 0, 0)))

        queue.request("first", (candidate,), (1, 0, 0), 4.0)
        ready, result = queue.request(
            "second", (candidate,), (1, 0, 0), 4.0
        )

        self.assertFalse(ready)
        self.assertIsNone(result)
        self.assertTrue(queue.is_pending("first", 4.0))
        self.assertFalse(queue.is_pending("second", 4.0))

    def test_empty_solution_is_cached_briefly(self):
        queue = self.module.ArcProbeQueue(failure_ttl=0.5)

        ready, result = queue.request("shot", (), (0, 0, 0), 5.0)

        self.assertTrue(ready)
        self.assertIsNone(result)
        self.assertEqual((True, None), queue.result("shot", 5.4))
        self.assertEqual((False, None), queue.result("shot", 5.6))

    def test_lazy_request_does_not_build_candidates_when_queue_is_full(self):
        queue = self.module.ArcProbeQueue(max_jobs=1)
        candidate = self.candidate("low", ((0, 0, 0), (1, 0, 0)))
        queue.request("first", (candidate,), (1, 0, 0), 6.0)
        builds = []

        ready, result = queue.request_lazy(
            "second",
            lambda: builds.append("built") or (candidate,),
            (1, 0, 0),
            6.0,
        )

        self.assertFalse(ready)
        self.assertIsNone(result)
        self.assertEqual([], builds)
        self.assertTrue(queue.is_pending("first", 6.0))
        self.assertFalse(queue.is_pending("second", 6.0))

    def test_lazy_request_builds_candidates_once_when_capacity_exists(self):
        queue = self.module.ArcProbeQueue()
        candidate = self.candidate("low", ((0, 0, 0), (1, 0, 0)))
        builds = []

        ready, result = queue.request_lazy(
            "shot",
            lambda: builds.append("built") or (candidate,),
            (1, 0, 0),
            7.0,
        )

        self.assertFalse(ready)
        self.assertIsNone(result)
        self.assertEqual(["built"], builds)
        self.assertEqual(1, queue.advance(7.0, 4, lambda _a, _b: None))
        self.assertEqual((True, candidate), queue.result("shot", 7.0))

    def test_lazy_request_does_not_rebuild_pending_or_cached_result(self):
        queue = self.module.ArcProbeQueue()
        candidate = self.candidate("low", ((0, 0, 0), (1, 0, 0)))
        builds = []
        factory = lambda: builds.append("built") or (candidate,)

        self.assertEqual(
            (False, None),
            queue.request_lazy("shot", factory, (1, 0, 0), 8.0),
        )
        self.assertEqual(
            (False, None),
            queue.request_lazy("shot", factory, (1, 0, 0), 8.1),
        )
        self.assertEqual(["built"], builds)

        queue.advance(8.1, 4, lambda _a, _b: None)
        self.assertEqual(
            (True, candidate),
            queue.request_lazy("shot", factory, (1, 0, 0), 8.2),
        )
        self.assertEqual(["built"], builds)

    def test_lazy_request_does_not_rebuild_cached_empty_solution(self):
        queue = self.module.ArcProbeQueue(failure_ttl=0.5)
        builds = []
        factory = lambda: builds.append("built") or ()

        self.assertEqual(
            (True, None),
            queue.request_lazy("shot", factory, (0, 0, 0), 9.0),
        )
        self.assertEqual(
            (True, None),
            queue.request_lazy("shot", factory, (0, 0, 0), 9.1),
        )
        self.assertEqual(["built"], builds)


if __name__ == "__main__":
    unittest.main()
