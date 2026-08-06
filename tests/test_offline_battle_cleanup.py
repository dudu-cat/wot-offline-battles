import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
NETWORK = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"
DEFAULTS = ROOT / "scripts/client/gui/mods/offhangar/config_defaults.json"


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


class OfflineBattleCleanupTest(unittest.TestCase):
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
        self.assertIn("_event_stubs.clear()", source)
        self.assertIn("_offh_battle_entity_ids(_cached_entities, _world_entities)", source)
        self.assertIn("globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]", source)
        self.assertIn("residual models=%d mocks=%d hitBSP=%d callbacks=%d", source)
        self.assertEqual(1, source.count("player._offhangar_stickers.append(stickers)"))
        self.assertNotIn("Account.shoot = _mock_shoot", source)

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
