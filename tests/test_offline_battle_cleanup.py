import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
LOADER = ROOT / "scripts/client/gui/mods/mod_offhangar.py"
NETWORK = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"
DEFAULTS = ROOT / "scripts/client/gui/mods/offhangar/config_defaults.json"


_MISSING_MODULE = object()


def install_test_modules(test_case, replacements):
    previous = {
        name: sys.modules.get(name, _MISSING_MODULE)
        for name in replacements
    }

    def restore():
        for name, module in previous.items():
            if module is _MISSING_MODULE:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    test_case.addCleanup(restore)
    sys.modules.update(replacements)


def load_cleanup_helpers():
    source = BATTLE.read_text()
    start = source.index("def _offh_load_hit_testers(")
    end = source.index("def _offh_proc_mem_mb(", start)
    namespace = {}
    exec(source[start:end], namespace)
    return namespace


class HitTester:
    def __init__(self, fail_load=False):
        self.loads = 0
        self.releases = 0
        self.fail_load = fail_load

    def loadBspModel(self):
        self.loads += 1
        if self.fail_load:
            raise RuntimeError("load failed")

    def releaseBspModel(self):
        self.releases += 1


class TypeDescriptor:
    def __init__(self, testers):
        self.testers = testers

    def getHitTesters(self):
        return self.testers


class Sticker:
    def __init__(self):
        self.detaches = 0

    def detachStickers(self):
        self.detaches += 1


class Player:
    def getOwnVehicleMatrix(self):
        return "class-method"


class ArenaEvent:
    def __init__(self, *delegates):
        self.delegates = list(delegates)


class Arena:
    pass


class OfflineBattleCleanupTest(unittest.TestCase):
    def test_sweep_defers_without_destroying_or_retrying_native_owners(self):
        source = BATTLE.read_text()
        start = source.index("def _offh_battle_sweep(")
        end = source.index("\nimport BigWorld", start)
        namespace = {}
        exec(source[start:end], namespace)

        callbacks = []
        stop_results = [-1, 1, 1]
        stop_calls = []
        streaming_stop_results = [False, True]
        streaming_stop_calls = []

        class StreamingBootstrap(object):
            def stop(self):
                streaming_stop_calls.append("stop")
                return streaming_stop_results.pop(0)

        streaming_bootstrap = StreamingBootstrap()
        model = object()
        entity = types.SimpleNamespace(model=model)
        mock = types.SimpleNamespace(
            id=1016,
            bw_entity=entity,
            model=model,
            _chassis_model=model,
            marker=None,
        )
        mocks = {1016: mock}
        player = types.SimpleNamespace(
            _outlined_bot=None,
            _autoaim_target=None,
            inputHandler=None,
            _offh_spawn_streaming_bootstrap=streaming_bootstrap,
        )

        bigworld = types.ModuleType("BigWorld")
        bigworld.player = lambda: player
        bigworld.cancelCallback = lambda unused_callback: None
        bigworld.callback = lambda delay, callback: callbacks.append(
            (delay, callback)
        )
        bigworld.entities = {}
        bigworld.cachedEntities = lambda: {}

        gui = types.ModuleType("gui")
        gui.WindowsManager = types.SimpleNamespace(
            g_windowsManager=types.SimpleNamespace(
                battleWindow=types.SimpleNamespace(
                    vMarkersManager=None, minimap=None
                )
            )
        )

        native = types.ModuleType("gui.mods.offhangar.native_bot_physics")

        def stop_all(targets):
            stop_calls.append(targets)
            return stop_results.pop(0)

        native.stop_all = stop_all
        install_test_modules(self, {
            "BigWorld": bigworld,
            "gui": gui,
            native.__name__: native,
        })
        namespace.update({
            "G_MOCK_VEHICLES": mocks,
            "g_offline_models": [model],
            "g_offline_enemies": [mock],
            "g_offh_battle_callbacks": {},
            "_offh_proc_mem_mb": lambda: (0, 0, 0),
            "_offh_battle_callback": lambda delay, callback: callbacks.append(
                (delay, callback)
            ),
            "LOG_ERROR": lambda *args: None,
            "LOG_CURRENT_EXCEPTION": lambda: None,
        })

        self.assertFalse(namespace["_offh_battle_sweep"]("exit"))

        self.assertEqual([mocks], stop_calls)
        self.assertIs(entity, mock.bw_entity)
        self.assertIs(model, entity.model)
        self.assertIs(model, mock.model)
        self.assertIs(model, mock._chassis_model)
        self.assertIs(mocks, namespace["G_MOCK_VEHICLES"])
        self.assertEqual([], callbacks)
        self.assertEqual([], streaming_stop_calls)

        self.assertFalse(namespace["_offh_battle_sweep"]("exit"))
        self.assertEqual([mocks, mocks], stop_calls)
        self.assertEqual(["stop"], streaming_stop_calls)
        self.assertIs(entity, mock.bw_entity)
        self.assertIs(model, entity.model)
        self.assertIs(model, mock.model)
        self.assertIs(model, mock._chassis_model)
        self.assertIs(streaming_bootstrap,
                      player._offh_spawn_streaming_bootstrap)
        self.assertIs(mocks, namespace["G_MOCK_VEHICLES"])

        self.assertTrue(namespace["_offh_battle_sweep"]("exit"))
        self.assertEqual([mocks, mocks, mocks], stop_calls)
        self.assertEqual(["stop", "stop"], streaming_stop_calls)
        self.assertIsNone(mock.bw_entity)
        self.assertIsNone(entity.model)
        self.assertIsNone(mock.model)
        self.assertIsNone(mock._chassis_model)
        self.assertEqual({}, namespace["G_MOCK_VEHICLES"])

    def test_sweep_retry_barrier_resumes_continuation_exactly_once(self):
        source = BATTLE.read_text()
        start = source.index("def _offh_sweep_or_retry(")
        end = source.index("\nimport BigWorld", start)
        namespace = {}
        exec(source[start:end], namespace)

        callbacks = []
        sweep_results = [False, True]
        sweep_calls = []
        continuations = []
        bigworld = types.ModuleType("BigWorld")
        bigworld.callback = lambda delay, callback: callbacks.append(
            (delay, callback)
        )
        install_test_modules(self, {"BigWorld": bigworld})

        def sweep(tag):
            sweep_calls.append(tag)
            return sweep_results.pop(0)

        def caller():
            if not namespace["_offh_sweep_or_retry"]("exit", caller):
                return
            continuations.append("continued")

        namespace.update({
            "_offh_battle_sweep": sweep,
            "LOG_CURRENT_EXCEPTION": lambda: None,
        })

        caller()

        self.assertEqual(["exit"], sweep_calls)
        self.assertEqual([], continuations)
        self.assertEqual(1, len(callbacks))
        delay, retry = callbacks.pop(0)
        self.assertAlmostEqual(0.1, delay)

        retry()

        self.assertEqual(["exit", "exit"], sweep_calls)
        self.assertEqual(["continued"], continuations)
        self.assertEqual([], callbacks)

    def test_all_battle_transitions_use_the_sweep_continuation_barrier(self):
        source = BATTLE.read_text()
        spawn = source[source.index("def _try_spawn_battle_avatar_stub("):
                       source.index("def start_network_battle_from_server(")]
        leave = spawn[spawn.index("\t\tdef _leaveArena("):
                      spawn.index("\n\t\tplayer.leaveArena = _leaveArena")]
        capture = spawn[spawn.index("\t\tdef _capture_tick("):
                        spawn.index("\n\t\tg_capture_tick_ref = _capture_tick")]
        aih = spawn[spawn.index("\t\tdef _aih_tick("):
                    spawn.index("\n\t\tglobals()['g_offh_aih_callback_id'] =", spawn.index("\t\tdef _aih_tick("))]
        death = spawn[spawn.index("\t\t\t\t\t\tdef _exit_battle("):
                      spawn.index("\n\t\t\t\t\t\t_offh_battle_callback", spawn.index("\t\t\t\t\t\tdef _exit_battle("))]

        start_gate = "if not _offh_sweep_or_retry('start'"
        self.assertIn(start_gate, spawn)
        self.assertLess(spawn.index(start_gate), spawn.index("g_offh_battle_gen"))

        quit_gate = "if not _offh_sweep_or_retry('quit', _leaveArena):"
        self.assertIn(quit_gate, leave)
        self.assertLess(leave.index(quit_gate), leave.index("_battle_finished[0] = True"))

        for gui_loss in (capture, aih):
            lost = gui_loss[gui_loss.index("elif _offh_seen_bw[0]:"):]
            self.assertIn("_leaveArena()", lost)
            self.assertLess(lost.index("_leaveArena()"), lost.index("return"))
            self.assertNotIn("_offh_battle_sweep('esc')", lost)

        exit_gate = "if not _offh_sweep_or_retry('exit', _exit_battle):"
        self.assertIn(exit_gate, death)
        self.assertLess(death.index(exit_gate), death.index("_exit_done[0] = True"))

    def test_hit_testers_are_loaded_and_released_once_per_unique_object(self):
        helpers = load_cleanup_helpers()
        first = HitTester()
        second = HitTester()
        descriptor = TypeDescriptor([first, first, second])

        self.assertEqual(2, helpers["_offh_load_hit_testers"](descriptor))
        self.assertEqual(0, helpers["_offh_load_hit_testers"](descriptor))
        self.assertEqual((2, 0), helpers["_offh_release_hit_testers"]())
        self.assertEqual((1, 1), (first.loads, first.releases))
        self.assertEqual((1, 1), (second.loads, second.releases))

    def test_failed_hit_tester_load_is_never_released(self):
        helpers = load_cleanup_helpers()
        failed = HitTester(fail_load=True)

        self.assertEqual(
            0, helpers["_offh_load_hit_testers"](TypeDescriptor([failed]))
        )
        self.assertEqual((0, 0), helpers["_offh_release_hit_testers"]())
        self.assertEqual((1, 0), (failed.loads, failed.releases))

    def test_battle_entity_scan_merges_cache_and_live_world(self):
        helpers = load_cleanup_helpers()
        OfflineEntity = type("OfflineEntity", (), {})
        AreaDestructibles = type("AreaDestructibles", (), {})
        OtherEntity = type("OtherEntity", (), {})
        cached = {1: OtherEntity(), 2: OfflineEntity()}
        world = {2: OfflineEntity(), 3: AreaDestructibles()}

        self.assertEqual(
            {2, 3}, set(helpers["_offh_battle_entity_ids"](cached, world))
        )

    def test_stickers_are_detached_once_across_list_and_map_owners(self):
        helpers = load_cleanup_helpers()
        first = Sticker()
        second = Sticker()
        seen = {}

        count = helpers["_offh_detach_stickers"]([first, second], seen)
        count += helpers["_offh_detach_stickers"](
            {"hull": (first, object()), "turret": (second, object())}, seen
        )

        self.assertEqual(2, count)
        self.assertEqual((1, 1), (first.detaches, second.detaches))

    def test_persistent_player_restores_original_attrs_and_drops_closures(self):
        helpers = load_cleanup_helpers()
        player = Player()
        original_shoot = object()
        player.shoot = original_shoot

        captured = helpers["_offh_capture_player_battle_attrs"](player)
        player.shoot = lambda: "battle"
        player.leaveArena = lambda: "battle"
        player.getOwnVehicleMatrix = lambda: "battle"
        player._autoaim_target = object()
        player._outlined_bot = object()
        restored, failed = helpers["_offh_restore_player_battle_attrs"](player)

        self.assertEqual(captured, restored)
        self.assertEqual(0, failed)
        self.assertIs(original_shoot, player.shoot)
        self.assertFalse("leaveArena" in player.__dict__)
        self.assertFalse("getOwnVehicleMatrix" in player.__dict__)
        self.assertFalse("_autoaim_target" in player.__dict__)
        self.assertFalse("_outlined_bot" in player.__dict__)
        self.assertEqual("class-method", player.getOwnVehicleMatrix())

    def test_arena_cleanup_clears_registered_and_materialized_events(self):
        helpers = load_cleanup_helpers()
        arena = Arena()
        shared = ArenaEvent(object(), object())
        direct = ArenaEvent(object())
        arena._event_stubs = {"onPeriodChange": shared}
        # Simulate the old augmented-assignment bug: the same registered event
        # was also materialized as a direct arena attribute.
        arena.onPeriodChange = shared
        arena.onVehicleAdded = direct

        self.assertEqual(3, helpers["_offh_clear_arena_events"](arena))
        self.assertEqual({}, arena._event_stubs)
        self.assertEqual([], shared.delegates)
        self.assertEqual([], direct.delegates)
        self.assertNotIn("onPeriodChange", arena.__dict__)
        self.assertNotIn("onVehicleAdded", arena.__dict__)

    def test_arena_augmented_assignment_stays_in_event_registry(self):
        source = LOADER.read_text()
        start = source.index("class _OfflineArenaStub(object):")
        end = source.index("class _OfflineVehicleStub(object):", start)
        namespace = {}
        exec(source[start:end], namespace)
        arena = namespace["_OfflineArenaStub"]()
        handler = lambda *args: None

        arena.onPeriodChange += handler

        self.assertNotIn("onPeriodChange", arena.__dict__)
        self.assertIs(arena.onPeriodChange, arena._event_stubs["onPeriodChange"])
        self.assertEqual([handler], arena.onPeriodChange.delegates)

    def test_every_bsp_load_uses_the_tracked_lifecycle(self):
        source = BATTLE.read_text()
        self.assertEqual(1, source.count(".loadBspModel()"))
        self.assertEqual(1, source.count(".releaseBspModel()"))
        self.assertGreaterEqual(source.count("_offh_load_hit_testers("), 3)
        self.assertIn("_offh_release_hit_testers()", source)
        self.assertIn("g_cache.clearPrereqs()", source)

    def test_visual_children_and_owned_callbacks_are_torn_down(self):
        source = BATTLE.read_text()
        self.assertIn("_marker_manager.destroyMarker(_marker)", source)
        self.assertIn("_minimap.notifyVehicleStop", source)
        self.assertIn("_offh_detach_stickers(", source)
        self.assertIn("_m._collision_obstacle = None", source)
        self.assertIn("BigWorld.cancelCallback(_callback_id)", source)
        self.assertIn("_offh_restore_player_battle_attrs(BigWorld.player())", source)
        self.assertIn("BigWorld.wgDelEdgeDetectEntity(_outlined_bot.bw_entity)", source)
        self.assertIn("_offh_clear_arena_events(_arena)", source)
        self.assertIn("_offh_battle_entity_ids(_cached_entities, _world_entities)", source)
        self.assertIn("globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]", source)
        self.assertIn("residual models=%d mocks=%d hitBSP=%d callbacks=%d", source)
        self.assertEqual(1, source.count("player._offhangar_stickers.append(stickers)"))
        self.assertNotIn("Account.shoot = _mock_shoot", source)

    def test_battle_generation_is_initialized_on_the_normal_spawn_path(self):
        source = BATTLE.read_text()
        spawn = source[source.index("def _try_spawn_battle_avatar_stub("):
                       source.index("def start_network_battle_from_server(")]
        generation_line = next(
            line for line in spawn.splitlines()
            if "globals()['g_offh_battle_gen'] = (" in line
        )
        capture_line = next(
            line for line in spawn.splitlines()
            if "_offh_capture_player_battle_attrs(player)" in line
        )

        # Both statements belong to the outer spawn try (two tabs). Three tabs
        # would leave them inside the sweep exception handler, making the normal
        # first-battle path reach model callbacks with _offh_my_gen undefined.
        self.assertTrue(generation_line.startswith("\t\tglobals()"))
        self.assertFalse(generation_line.startswith("\t\t\t"))
        self.assertTrue(capture_line.startswith("\t\t_offh_capture"))
        self.assertLess(
            spawn.index("_offh_my_gen ="),
            spawn.index("def _add_models_when_ready("),
        )

    def test_pending_space_is_only_cleared_after_native_release(self):
        source = BATTLE.read_text()
        start = source.index("_prev = globals().get('g_offh_pending_release'")
        end = source.index("import gc as _gcp", start)
        release = source[start:end]

        native_release = release.index("BigWorld.releaseSpace(_prev)")
        clear_pending = release.index("globals()['g_offh_pending_release'] = 0")
        self.assertLess(native_release, clear_pending)
        self.assertIn("OfflineBattle.released prev space", release)
        self.assertEqual(1, release.count("OfflineBattle.released prev space"))
        self.assertIn("OfflineBattle.release prev FAILED", release)

    def test_memory_probe_supports_windows_without_wmic(self):
        source = BATTLE.read_text()
        probe = source[source.index("def _offh_proc_mem_mb("):
                       source.index("def _offh_gc_census_line(")]
        self.assertIn("powershell -NoProfile -NonInteractive", probe)
        self.assertIn("WorkingSet64", probe)
        self.assertIn("VirtualMemorySize64", probe)
        self.assertIn("PrivateMemorySize64", probe)

    def test_network_battle_closures_are_released(self):
        source = NETWORK.read_text()
        stop = source[source.index("def stop_for_player("):
                      source.index("def _server_pose_from_world(")]
        self.assertIn("player._offhangar_network_spawn_remote = None", stop)
        self.assertIn("player._offhangar_network_formation = None", stop)
        self.assertIn("player._offhangar_apply_network_rules_state = None", stop)
        self.assertIn("player._offhangar_apply_network_battle_result = None", stop)

    def test_normal_matchmaking_has_no_vehicle_name_cap(self):
        defaults = json.loads(DEFAULTS.read_text())
        self.assertNotIn("bot_model_pool_size", defaults)
        self.assertNotIn("bot_variety", defaults)
        self.assertNotIn("preload_bots", defaults)
        source = BATTLE.read_text()
        self.assertNotIn("def _preload_bot_pool", source)
        self.assertNotIn("def _offh_bot_pool", source)
        self.assertNotIn("g_offh_bot_pool", source)


if __name__ == "__main__":
    unittest.main()
