import importlib.util
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "scripts/client/gui/mods/offhangar/spawn_streaming_bootstrap.py"
)


def load_module():
    if not SOURCE.is_file():
        raise AssertionError(
            "missing pure spawn-streaming bootstrap helper: %s" % SOURCE
        )
    spec = importlib.util.spec_from_file_location(
        "spawn_streaming_bootstrap_under_test", SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProjection(object):
    def __init__(self, far_plane):
        self._far_plane = float(far_plane)
        self.writes = []

    @property
    def farPlane(self):
        return self._far_plane

    @farPlane.setter
    def farPlane(self, value):
        self._far_plane = float(value)
        self.writes.append(float(value))


class FlakyRestoreProjection(FakeProjection):
    def __init__(self, far_plane, restore_outcomes):
        super(FlakyRestoreProjection, self).__init__(far_plane)
        self.original_far_plane = float(far_plane)
        self.restore_outcomes = list(restore_outcomes)

    @FakeProjection.farPlane.setter
    def farPlane(self, value):
        value = float(value)
        self.writes.append(value)
        if value == self.original_far_plane and self.restore_outcomes:
            outcome = self.restore_outcomes.pop(0)
            if outcome == "raise":
                raise RuntimeError("projection setter rejected restore")
            if outcome == "mismatch":
                return
        self._far_plane = value


class SilentExpansionProjection(FakeProjection):
    @FakeProjection.farPlane.setter
    def farPlane(self, value):
        value = float(value)
        self.writes.append(value)
        if value > self._far_plane:
            return
        self._far_plane = value


def job(team, slot, x, y, z):
    """Return the frozen nine-field lineup tuple used by offline_battle."""
    return (
        int(team), int(slot), int(team * 100 + slot),
        float(x), float(y), float(z), 0.0,
        "vehicle_%d_%d" % (team, slot), "Bot-%d-%d" % (team, slot),
    )


class SpawnStreamingBootstrapContractTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def bootstrap(self, projection, jobs, probe, **overrides):
        options = {
            "origin": (0.0, 50.0, 0.0),
            "now": 10.0,
            "timeout": 8.0,
            "margin": 0.0,
        }
        options.update(overrides)
        return self.module.SpawnStreamingBootstrap(
            projection, jobs, probe=probe, **options
        )

    def test_playable_bounds_convert_to_camera_invariant_coverage_target(self):
        self.assertAlmostEqual(
            1000.0,
            self.module.coverage_target_from_bounds(
                (-500.0, -500.0, 500.0, 500.0)
            ),
        )
        self.assertAlmostEqual(
            math.sqrt((1200.0 * 1200.0 + 800.0 * 800.0) / 2.0),
            self.module.coverage_target_from_bounds(
                (-600.0, -400.0, 600.0, 400.0)
            ),
        )

    def test_explicit_coverage_target_does_not_overexpand_from_camera_to_job(self):
        projection = FakeProjection(350.0)
        jobs = (job(1, 0, 500.0, 50.0, 500.0),)

        bootstrap = self.bootstrap(
            projection,
            jobs,
            lambda lineup_job: lineup_job[4],
            origin=(-500.0, 50.0, -500.0),
            coverage_target=1000.0,
        )

        self.assertEqual([1000.0], projection.writes)
        self.assertEqual(1000.0, projection.farPlane)
        self.assertEqual(
            self.module.PLACEMENT_READY,
            bootstrap.poll(10.1, active_count=0),
        )

    def test_silent_expansion_mismatch_fails_before_placement_unlocks(self):
        projection = SilentExpansionProjection(350.0)
        jobs = (job(1, 0, 490.0, 55.0, 475.0),)

        bootstrap = self.bootstrap(
            projection,
            jobs,
            lambda lineup_job: lineup_job[4],
            coverage_target=1000.0,
        )

        self.assertEqual(350.0, projection.farPlane)
        self.assertEqual("projection_error", bootstrap.failure_reason)
        self.assertFalse(bootstrap.placement_ready)
        self.assertEqual(
            self.module.FAILED,
            bootstrap.poll(10.1, active_count=1),
        )

    def test_missing_coverage_target_preserves_custom_map_job_fallback(self):
        projection = FakeProjection(350.0)
        jobs = (job(1, 0, 500.0, 50.0, 500.0),)

        self.bootstrap(
            projection,
            jobs,
            lambda lineup_job: lineup_job[4],
            origin=(-500.0, 50.0, -500.0),
            coverage_target=None,
        )

        self.assertAlmostEqual(math.sqrt(2000000.0), projection.farPlane)

    def test_existing_far_plane_is_not_reassigned_when_it_already_covers_jobs(self):
        projection = FakeProjection(900.0)
        jobs = (
            job(1, 0, 100.0, 50.0, 0.0),
            job(2, 0, -200.0, 50.0, 0.0),
        )

        bootstrap = self.bootstrap(
            projection, jobs, lambda lineup_job: lineup_job[4], margin=25.0
        )

        self.assertEqual([], projection.writes)
        self.assertEqual(
            self.module.PLACEMENT_READY, bootstrap.poll(10.0, active_count=0)
        )
        self.assertEqual(900.0, projection.farPlane)
        self.assertEqual(
            self.module.COMPLETE, bootstrap.poll(10.1, active_count=2)
        )
        self.assertEqual([], projection.writes)

    def test_far_plane_expands_to_cover_farthest_canonical_spawn_and_waits(self):
        projection = FakeProjection(350.0)
        jobs = (
            job(1, 0, 30.0, 50.0, 40.0),
            job(2, 0, 300.0, 50.0, 400.0),
        )
        live = set()

        def probe(lineup_job):
            key = (lineup_job[0], lineup_job[1])
            return lineup_job[4] if key in live else None

        bootstrap = self.bootstrap(
            projection, jobs, probe, margin=25.0
        )

        self.assertEqual([525.0], projection.writes)
        self.assertEqual(
            self.module.WAITING_SUPPORT, bootstrap.poll(10.1, active_count=0)
        )
        self.assertEqual(525.0, projection.farPlane)

        live.add((1, 0))
        self.assertEqual(
            self.module.WAITING_SUPPORT, bootstrap.poll(10.2, active_count=0)
        )
        self.assertEqual(525.0, projection.farPlane)

    def test_every_live_probe_unlocks_placement_and_range_survives_completion(self):
        projection = FakeProjection(100.0)
        jobs = (
            job(1, 0, 120.0, 50.0, 0.0),
            job(2, 0, -160.0, 50.0, 0.0),
        )
        probed = []

        def probe(lineup_job):
            probed.append((lineup_job[0], lineup_job[1]))
            return lineup_job[4]

        bootstrap = self.bootstrap(projection, jobs, probe, margin=10.0)

        self.assertEqual(
            self.module.PLACEMENT_READY, bootstrap.poll(10.1, active_count=0)
        )
        self.assertEqual([(1, 0), (2, 0)], probed)
        self.assertEqual(170.0, projection.farPlane)

        # Entity assembly is not enough. The temporary streaming radius must
        # remain until every corresponding C++ native body reports active.
        self.assertEqual(
            self.module.PLACEMENT_READY, bootstrap.poll(10.2, active_count=1)
        )
        self.assertEqual([(1, 0), (2, 0)], probed)
        self.assertEqual(170.0, projection.farPlane)
        self.assertEqual(
            self.module.COMPLETE, bootstrap.poll(10.3, active_count=2)
        )
        self.assertEqual(170.0, projection.farPlane)
        self.assertEqual([170.0], projection.writes)
        self.assertEqual(
            self.module.COMPLETE, bootstrap.poll(10.4, active_count=2)
        )
        self.assertEqual(170.0, projection.farPlane)
        self.assertEqual([170.0], projection.writes)

        # Active native bodies do not pin a remote 0.8.2 terrain chunk. Keep
        # the expanded streaming range for the battle and restore only during
        # explicit lifecycle cleanup.
        bootstrap.stop()
        self.assertEqual(100.0, projection.farPlane)
        self.assertEqual([170.0, 100.0], projection.writes)

    def test_finite_baked_height_never_substitutes_for_a_live_collision_hit(self):
        projection = FakeProjection(100.0)
        baked_job = job(1, 7, 300.0, 53.844, 400.0)
        calls = []

        def missing_live_collision(lineup_job):
            calls.append(lineup_job[4])
            return None

        bootstrap = self.bootstrap(
            projection, (baked_job,), missing_live_collision
        )

        self.assertEqual(
            self.module.WAITING_SUPPORT, bootstrap.poll(10.1, active_count=1)
        )
        self.assertEqual([53.844], calls)
        self.assertEqual(500.0, projection.farPlane)
        self.assertFalse(bootstrap.placement_ready)

    def test_live_hit_outside_canonical_height_tolerance_does_not_unlock(self):
        projection = FakeProjection(100.0)
        jobs = (
            job(1, 0, 300.0, 50.0, 400.0),
            job(2, 0, -300.0, 50.0, -400.0),
        )
        offsets = {1: 0.25, 2: 0.50}
        calls = []

        def probe(lineup_job):
            calls.append((lineup_job[0], lineup_job[1]))
            return lineup_job[4] + offsets[lineup_job[0]]

        bootstrap = self.bootstrap(projection, jobs, probe)

        self.assertEqual(
            self.module.WAITING_SUPPORT, bootstrap.poll(10.1, active_count=2)
        )
        self.assertFalse(bootstrap.placement_ready)
        self.assertEqual(500.0, projection.farPlane)
        self.assertEqual([(1, 0), (2, 0)], calls)

        # A valid live hit is cached. Only the still-mismatched job is probed
        # again, and placement unlocks only after that exact job is supported.
        offsets[2] = 0.25
        self.assertEqual(
            self.module.PLACEMENT_READY, bootstrap.poll(10.2, active_count=0)
        )
        self.assertEqual([(1, 0), (2, 0), (2, 0)], calls)
        self.assertEqual(500.0, projection.farPlane)

    def test_timeout_fails_closed_and_restores_the_exact_original_far_plane(self):
        projection = FakeProjection(333.25)
        jobs = (job(1, 0, 600.0, 50.0, 800.0),)
        bootstrap = self.bootstrap(
            projection, jobs, lambda _job: None, timeout=2.0
        )

        self.assertEqual(
            self.module.WAITING_SUPPORT, bootstrap.poll(11.99, active_count=0)
        )
        self.assertEqual(1000.0, projection.farPlane)
        self.assertEqual(
            self.module.FAILED, bootstrap.poll(12.0, active_count=0)
        )
        self.assertEqual("timeout", bootstrap.failure_reason)
        self.assertFalse(bootstrap.placement_ready)
        self.assertEqual(333.25, projection.farPlane)
        self.assertEqual([1000.0, 333.25], projection.writes)

    def test_probe_exception_fails_closed_and_restores_far_plane(self):
        projection = FakeProjection(200.0)
        jobs = (job(2, 3, 300.0, 50.0, 400.0),)

        def broken_probe(_lineup_job):
            raise RuntimeError("collision space unavailable")

        bootstrap = self.bootstrap(projection, jobs, broken_probe)

        self.assertEqual(
            self.module.FAILED, bootstrap.poll(10.1, active_count=0)
        )
        self.assertEqual("support_probe_error", bootstrap.failure_reason)
        self.assertFalse(bootstrap.placement_ready)
        self.assertEqual(200.0, projection.farPlane)
        self.assertEqual([500.0, 200.0], projection.writes)

    def test_activation_timeout_holds_range_until_explicit_owner_teardown(self):
        projection = FakeProjection(150.0)
        jobs = (job(1, 0, 300.0, 50.0, 400.0),)
        bootstrap = self.bootstrap(
            projection, jobs, lambda lineup_job: lineup_job[4], timeout=2.0
        )

        self.assertEqual(
            self.module.PLACEMENT_READY, bootstrap.poll(10.1, active_count=0)
        )
        self.assertEqual(500.0, projection.farPlane)
        self.assertEqual(
            self.module.FAILED, bootstrap.poll(12.0, active_count=0)
        )
        self.assertEqual("timeout", bootstrap.failure_reason)
        self.assertEqual(500.0, projection.farPlane)
        self.assertEqual([500.0], projection.writes)

        self.assertTrue(bootstrap.stop())
        self.assertEqual(150.0, projection.farPlane)
        self.assertEqual([500.0, 150.0], projection.writes)

    def test_explicit_stop_restores_on_first_call_after_placement_unlock(self):
        projection = FakeProjection(150.0)
        jobs = (job(1, 0, 300.0, 50.0, 400.0),)
        bootstrap = self.bootstrap(
            projection, jobs, lambda lineup_job: lineup_job[4]
        )

        self.assertEqual(
            self.module.PLACEMENT_READY,
            bootstrap.poll(10.1, active_count=0),
        )
        self.assertEqual(500.0, projection.farPlane)

        self.assertTrue(bootstrap.stop())
        self.assertEqual(150.0, projection.farPlane)
        self.assertEqual([500.0, 150.0], projection.writes)
        self.assertEqual("stopped", bootstrap.failure_reason)

    def test_stop_is_fail_closed_idempotent_and_restores_far_plane(self):
        projection = FakeProjection(250.0)
        jobs = (job(1, 0, 0.0, 50.0, 600.0),)
        bootstrap = self.bootstrap(
            projection, jobs, lambda lineup_job: lineup_job[4]
        )

        self.assertEqual(600.0, projection.farPlane)
        bootstrap.stop()
        bootstrap.stop()

        self.assertEqual(250.0, projection.farPlane)
        self.assertEqual([600.0, 250.0], projection.writes)
        self.assertFalse(bootstrap.placement_ready)
        self.assertEqual(
            self.module.FAILED, bootstrap.poll(10.1, active_count=1)
        )
        self.assertEqual("stopped", bootstrap.failure_reason)

    def test_restore_setter_exception_remains_retryable(self):
        projection = FlakyRestoreProjection(250.0, ["raise", "success"])
        jobs = (job(1, 0, 0.0, 50.0, 600.0),)
        bootstrap = self.bootstrap(
            projection, jobs, lambda lineup_job: lineup_job[4]
        )

        self.assertFalse(bootstrap.stop())
        self.assertEqual(600.0, projection.farPlane)
        self.assertFalse(bootstrap._restored)

        self.assertTrue(bootstrap.stop())
        self.assertEqual(250.0, projection.farPlane)
        self.assertTrue(bootstrap._restored)
        self.assertEqual([600.0, 250.0, 250.0], projection.writes)

    def test_restore_readback_mismatch_remains_retryable(self):
        projection = FlakyRestoreProjection(250.0, ["mismatch", "success"])
        jobs = (job(1, 0, 0.0, 50.0, 600.0),)
        bootstrap = self.bootstrap(
            projection, jobs, lambda lineup_job: lineup_job[4]
        )

        self.assertFalse(bootstrap.stop())
        self.assertEqual(600.0, projection.farPlane)
        self.assertFalse(bootstrap._restored)

        self.assertTrue(bootstrap.stop())
        self.assertEqual(250.0, projection.farPlane)
        self.assertTrue(bootstrap._restored)
        self.assertEqual([600.0, 250.0, 250.0], projection.writes)

    def test_mutating_the_source_lineup_cannot_change_the_frozen_contract(self):
        projection = FakeProjection(100.0)
        mutable_jobs = [job(1, 0, 300.0, 50.0, 400.0)]
        observed = []

        def probe(lineup_job):
            observed.append(lineup_job)
            return lineup_job[4]

        bootstrap = self.bootstrap(projection, mutable_jobs, probe)
        mutable_jobs[0] = job(1, 0, 30.0, 50.0, 40.0)
        mutable_jobs.append(job(2, 0, 900.0, 50.0, 1200.0))

        self.assertEqual(500.0, projection.farPlane)
        self.assertEqual(
            self.module.PLACEMENT_READY, bootstrap.poll(10.1, active_count=1)
        )
        self.assertEqual(1, len(observed))
        self.assertEqual((300.0, 50.0, 400.0), observed[0][3:6])


if __name__ == "__main__":
    unittest.main()
