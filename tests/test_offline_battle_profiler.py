import unittest
import sys
import types
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"


class FakeTime:
    def __init__(self):
        self.wall = 100.0
        self.precise = 10.0

    def time(self):
        return self.wall

    def clock(self):
        return self.precise


def load_profiler(fake_time):
    source = SOURCE.read_text()
    start = source.index("_OFFH_PERF_SAMPLE_EVERY =")
    end = source.index("def _get_destr_authority", start)
    namespace = {"time": fake_time}
    exec(source[start:end], namespace)
    return namespace


class OfflineBattleProfilerTests(unittest.TestCase):
    def test_profiler_samples_one_frame_in_four(self):
        fake_time = FakeTime()
        profiler = load_profiler(fake_time)
        profiler["g_offh_battle_gen"] = 7

        self.assertIsNone(profiler["_offh_perf_frame_begin"](29))
        self.assertIsNone(profiler["_offh_perf_frame_begin"](29))
        self.assertIsNone(profiler["_offh_perf_frame_begin"](29))
        started = profiler["_offh_perf_frame_begin"](29)
        self.assertEqual(10.0, started)

        fake_time.precise += 0.012
        profiler["_offh_perf_stop"]("driver", started)
        state = profiler["g_offh_perf_state"]
        self.assertAlmostEqual(0.012, state["times"]["driver"], places=6)
        self.assertEqual(1, state["calls"]["driver"])

    def test_profiler_wraps_the_known_bot_hot_paths(self):
        source = SOURCE.read_text()
        for stage in (
            "network_smoothing",
            "contacts",
            "nav_tick",
            "nav_target",
            "bot_loop",
            "traffic_snapshot",
            "driver",
            "direction",
            "physics",
            "kinematics",
            "pose_water",
            "terrain_support",
            "terrain_tilt",
            "tree_scan",
            "wall_collision",
            "tank_collision",
            "visibility",
            "los",
            "network_publish",
        ):
            self.assertIn("'%s'" % stage, source)
        self.assertIn("PERF window=%.1fs role=%s bots=%d fps=%.1f", source)
        self.assertIn("_OFFH_PERF_REPORT_SECONDS = 5.0", source)

    def test_capture_rules_do_not_log_every_vehicle_distance_tick(self):
        source = SOURCE.read_text()

        self.assertNotIn("LOUD: Capture tick started running!", source)
        self.assertNotIn("LOUD: Distance to base", source)

    def test_profiler_emits_one_compact_note_after_five_seconds(self):
        fake_time = FakeTime()
        profiler = load_profiler(fake_time)
        profiler["g_offh_battle_gen"] = 9
        notes = []
        logging_module = types.ModuleType("gui.mods.offhangar.logging")
        logging_module.LOG_NOTE = notes.append
        modules = {
            "gui": types.ModuleType("gui"),
            "gui.mods": types.ModuleType("gui.mods"),
            "gui.mods.offhangar": types.ModuleType("gui.mods.offhangar"),
            "gui.mods.offhangar.logging": logging_module,
        }

        with mock.patch.dict(sys.modules, modules):
            for index in range(4):
                started = profiler["_offh_perf_frame_begin"](29)
                fake_time.precise += 0.010
                fake_time.wall += 1.3
                profiler["_offh_perf_frame_end"](
                    started, 0.050, types.SimpleNamespace()
                )

        self.assertEqual(1, len(notes))
        self.assertIn("role=offline bots=29", notes[0])
        self.assertIn("samples=1", notes[0])
        self.assertIn("callback=10.00ms", notes[0])


if __name__ == "__main__":
    unittest.main()
