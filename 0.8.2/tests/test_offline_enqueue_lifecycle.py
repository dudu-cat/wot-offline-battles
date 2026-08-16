import types
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "scripts/client/gui/mods/offhangar/offline_battle.py"


def load_begin_queue():
    text = SOURCE.read_text()
    start = text.index("def begin_offline_battle_queue(")
    end = text.index("\ndef schedule_random_battle_flow_after_enqueue", start)
    callbacks = []
    steps = []

    class BigWorldStub:
        @staticmethod
        def player():
            return player

        @staticmethod
        def callback(delay, callback):
            callbacks.append((delay, callback))

    player = types.SimpleNamespace(isOffline=True)
    scope = {
        "OFFLINE_BATTLE_ENABLED": True,
        "_BATTLE_BOOT_DEBOUNCE_SEC": 0.1,
        "BigWorld": BigWorldStub,
        "LOG_DEBUG": lambda *args: None,
        "time": types.SimpleNamespace(time=lambda: 100.0),
        "_resolve_vehicle_inv_id": lambda unused, fallback: fallback,
        "_network_mode_enabled": lambda: False,
        "_join_network_waiting_room": lambda *args: steps.append(("lan", args)),
        "_step_on_enqueued": lambda *args: steps.append(("enqueue", args)),
        "_schedule_arena_created_resilient": lambda *args: steps.append(("schedule", args)),
        # The map room is covered by its own test; this one drives the queue.
        "_open_offline_map_room": lambda *args: False,
    }
    exec(compile(text[start:end], str(SOURCE), "exec"), scope)
    return scope["begin_offline_battle_queue"], player, callbacks, steps


class OfflineEnqueueLifecycleTest(unittest.TestCase):
    def test_duplicate_enqueue_has_one_pending_transition(self):
        begin, player, callbacks, steps = load_begin_queue()

        self.assertTrue(begin(player, 17, "CMD_ENQUEUE_RANDOM"))
        self.assertFalse(begin(player, 17, "CMD_ENQUEUE_RANDOM"))
        self.assertEqual(1, len(callbacks))

        callbacks[0][1]()
        self.assertEqual(["enqueue", "schedule"], [item[0] for item in steps])
        self.assertFalse(player._offhangar_queue_pending)

    def test_first_click_is_not_discarded_during_recent_account_boot(self):
        begin, player, callbacks, unused_steps = load_begin_queue()
        player._offline_boot_time = 99.9

        self.assertTrue(begin(player, 17, "CMD_ENQUEUE_RANDOM"))
        self.assertEqual(1, len(callbacks))

    def test_cancelled_generation_cannot_boot_from_stale_callback(self):
        begin, player, callbacks, steps = load_begin_queue()

        self.assertTrue(begin(player, 17, "CMD_ENQUEUE_RANDOM"))
        player._offhangar_queue_cancelled = True
        player._offhangar_queue_pending = False
        player._offhangar_queue_generation += 1
        callbacks[0][1]()

        self.assertEqual([], steps)
