import sys
import math
import textwrap
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
NETWORK = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"
LOADER = ROOT / "scripts/client/gui/mods/mod_offhangar.py"
PEN_INDICATOR = ROOT / "scripts/client/gui/mods/offhangar/pen_indicator.py"
PROJECTILE_RUNTIME = (
    ROOT / "scripts/client/gui/mods/offhangar/projectile_runtime.py"
)


class _Vector3(object):
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __add__(self, other):
        return _Vector3(
            self.x + other.x, self.y + other.y, self.z + other.z
        )

    def __sub__(self, other):
        return _Vector3(
            self.x - other.x, self.y - other.y, self.z - other.z
        )

    @property
    def length(self):
        return math.sqrt(
            self.x * self.x + self.y * self.y + self.z * self.z
        )

    def normalise(self):
        length = self.length
        self.x /= length
        self.y /= length
        self.z /= length

    def scale(self, scalar):
        return _Vector3(
            self.x * scalar, self.y * scalar, self.z * scalar
        )


class OfflineBattleFeedbackIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.battle_source = BATTLE.read_text()
        cls.network_source = NETWORK.read_text()
        cls.loader_source = LOADER.read_text()
        cls.pen_indicator_source = PEN_INDICATOR.read_text()
        cls.projectile_source = PROJECTILE_RUNTIME.read_text()

    def _load_terrain_support(self, destroyed, continuation="ground"):
        function_start = self.battle_source.index(
            "\t\t\t\tdef _terrain_support("
        )
        function_end = self.battle_source.index(
            "\n\t\t\t\tdef _try_destroy_destructible(", function_start
        )
        function_source = textwrap.dedent(
            self.battle_source[function_start:function_end]
        )

        class Vector3(object):
            def __init__(self, x, y, z):
                self.x = float(x)
                self.y = float(y)
                self.z = float(z)

        collision_calls = []

        def collide_segment(space_id, start, end, mask):
            collision_calls.append((space_id, start.y, end.y, mask))
            # The first ray sees the still-streamed top of a 0.55 m prop. A
            # continuation from below that hit reaches the actual terrain.
            if continuation == "none" and start.y <= 0.55:
                return None
            if continuation == "same":
                hit_y = 0.55
                return (Vector3(start.x, hit_y, start.z),
                        Vector3(0.0, 1.0, 0.0))
            hit_y = 0.55 if start.y > 0.55 else 0.0
            return (Vector3(start.x, hit_y, start.z), Vector3(0.0, 1.0, 0.0))

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = collide_segment
        math_module = types.ModuleType("Math")
        math_module.Vector3 = Vector3

        class Authority(object):
            @staticmethod
            def is_destroyed(chunk_id, item_index, mat_kind=None):
                return bool(destroyed)

        def mat_info(unused_space_id, hit_point, unused_normal):
            if hit_point.y <= 0.0:
                return None
            return (
                hit_point, Vector3(0.0, 1.0, 0.0), 100, 7, 73,
                "objects/fragile.model",
            )

        namespace = {
            "_get_destr_authority": lambda: Authority(),
            "_offh_mat_info_for_segment_hit": mat_info,
            "_offh_perf_count": lambda *args: None,
            "math": math,
        }
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            exec(function_source, namespace)
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math
        return namespace["_terrain_support"], collision_calls, bigworld, math_module

    def _run_terrain_support(self, destroyed, continuation="ground"):
        support, collision_calls, bigworld, math_module = (
            self._load_terrain_support(destroyed, continuation)
        )
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        try:
            result = support(7, 0.0, 0.0, 0.0, 0.0, 2.5)
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math
        return result, collision_calls

    def test_stock_sixth_sense_and_scout_message_paths_are_used(self):
        self.assertIn("battle.showSixthSenseIndicator(True)", self.battle_source)
        self.assertIn("'SPOTTED': 'ENEMY_SPOTTED'", self.battle_source)
        self.assertIn("panel.showMessage(message_type", self.battle_source)

    def test_kill_and_scout_messages_use_the_stock_full_vehicle_label(self):
        helper_start = self.battle_source.index(
            "def _offh_vehicle_message_label("
        )
        helper_end = self.battle_source.index(
            "\ndef _offh_scout_event(", helper_start
        )
        namespace = {}
        exec(self.battle_source[helper_start:helper_end], namespace)
        label = namespace["_offh_vehicle_message_label"]

        vehicle_type = types.SimpleNamespace(
            type=types.SimpleNamespace(shortUserString="IS-3")
        )
        vehicle_info = {
            "name": "Shark-10",
            "vehicleType": vehicle_type,
        }
        player = types.SimpleNamespace(
            arena=types.SimpleNamespace(vehicles={17: vehicle_info})
        )
        calls = []

        class BattleContext(object):
            @staticmethod
            def getFullPlayerName(**kwargs):
                calls.append(kwargs)
                return "Shark-10 (IS-3)"

        gui = types.ModuleType("gui")
        gui.BattleContext = types.SimpleNamespace(
            g_battleContext=BattleContext()
        )
        previous_gui = sys.modules.get("gui")
        sys.modules["gui"] = gui
        try:
            self.assertEqual("Shark-10 (IS-3)", label(player, 17))
        finally:
            if previous_gui is None:
                sys.modules.pop("gui", None)
            else:
                sys.modules["gui"] = previous_gui

        self.assertEqual(1, len(calls))
        self.assertIs(vehicle_info, calls[0]["vData"])
        self.assertFalse(calls[0]["showClan"])

        scout_start = self.battle_source.index("def _offh_scout_event(")
        scout_end = self.battle_source.index(
            "\ndef _offh_record_direct_spot(", scout_start
        )
        scout_source = self.battle_source[scout_start:scout_end]
        self.assertIn(
            "_offh_vehicle_message_label(player, target_id, 'Enemy')",
            scout_source,
        )

        kill_start = self.battle_source.index("# Kill feed, ONCE per victim")
        kill_end = self.battle_source.index(
            "# Grey out the players-panel icon", kill_start
        )
        kill_source = self.battle_source[kill_start:kill_end]
        self.assertIn(
            "_offh_vehicle_message_label(_pl, victimID)", kill_source
        )
        self.assertIn(
            "_offh_vehicle_message_label(_pl, killerID)", kill_source
        )

    def test_vehicle_message_label_falls_back_without_mutating_the_roster(self):
        helper_start = self.battle_source.index(
            "def _offh_vehicle_message_label("
        )
        helper_end = self.battle_source.index(
            "\ndef _offh_scout_event(", helper_start
        )
        namespace = {}
        exec(self.battle_source[helper_start:helper_end], namespace)
        label = namespace["_offh_vehicle_message_label"]

        vehicle_info = {
            "name": "Shark-10",
            "vehicleType": types.SimpleNamespace(
                type=types.SimpleNamespace(shortUserString="IS-3")
            ),
        }
        player = types.SimpleNamespace(
            arena=types.SimpleNamespace(vehicles={17: vehicle_info})
        )
        original_name = vehicle_info["name"]
        original_vehicle_type = vehicle_info["vehicleType"]
        previous_gui = sys.modules.get("gui")
        try:
            gui_without_context = types.ModuleType("gui")
            sys.modules["gui"] = gui_without_context
            self.assertEqual("Shark-10", label(player, 17))

            class FailingBattleContext(object):
                @staticmethod
                def getFullPlayerName(**kwargs):
                    raise RuntimeError("unavailable")

            gui_with_failing_context = types.ModuleType("gui")
            gui_with_failing_context.BattleContext = types.SimpleNamespace(
                g_battleContext=FailingBattleContext()
            )
            sys.modules["gui"] = gui_with_failing_context
            self.assertEqual("Shark-10", label(player, 17))
            self.assertEqual("Missing", label(player, 999, "Missing"))
        finally:
            if previous_gui is None:
                sys.modules.pop("gui", None)
            else:
                sys.modules["gui"] = previous_gui

        self.assertEqual(original_name, vehicle_info["name"])
        self.assertIs(original_vehicle_type, vehicle_info["vehicleType"])
        self.assertIs(vehicle_info, player.arena.vehicles[17])

    def test_ai_and_foliage_runtime_loggers_are_bound_globally(self):
        self.assertIn(
            "from gui.mods.offhangar.logging import LOG_DEBUG, LOG_ERROR, LOG_NOTE",
            self.battle_source,
        )
        self.assertIn(
            "LOG_NOTE('OfflineBattle BUILD %s' % _OFFH_BUILD)",
            self.battle_source,
        )
        foliage_start = self.battle_source.index("def _offh_spot_foliage(")
        foliage_end = self.battle_source.index(
            "def _offh_spot_detection_range(", foliage_start
        )
        foliage_source = self.battle_source[foliage_start:foliage_end]
        self.assertIn("LOG_NOTE('SPOTTING:", foliage_source)
        self.assertIn("LOG_ERROR('SPOTTING:", foliage_source)

    def test_result_screen_uses_observed_feedback_values(self):
        self.assertIn("_offh_feedback_results.result_values(", self.battle_source)
        self.assertIn("'damageDealt': total_dmg_dealt", self.battle_source)
        self.assertIn("'damageAssisted': (_feedback_values", self.battle_source)

    def test_lan_events_feed_the_same_local_statistics(self):
        self.assertGreaterEqual(
            self.network_source.count("record_network_combat_stats"), 3
        )
        self.assertIn("record_network_spot_assist", self.network_source)

    def test_capture_progress_is_owned_per_vehicle_and_reset_by_real_damage(self):
        self.assertIn("'capture_rules'", self.loader_source)
        self.assertIn("_capture_rules_tick.advance(", self.battle_source)
        self.assertIn("def _offh_drop_capture_for_vehicle(", self.battle_source)
        self.assertIn("'module or crew damage'", self.battle_source)
        self.assertIn("apply_network_capture_damage", self.network_source)
        self.assertIn("'critical': bool(critical)", self.network_source)

    def test_bot_hit_callback_resolves_player_in_its_own_scope(self):
        callback = self.battle_source.index(
            "def _resolve_bot_projectile_hit("
        )
        next_callback = self.battle_source.index(
            "def _resolve_player_projectile_hit(", callback
        )
        callback_source = self.battle_source[callback:next_callback]
        binding = callback_source.index(
            "player_mock = mock_vehicles.get(\n\t\t\t\t\tgetattr(player, 'playerVehicleID', -1))"
        )
        hit_branch = callback_source.index("if hit_veh == player_mock")

        self.assertLess(binding, hit_branch)
        self.assertIn(
            "send_authoritative_bot_human_hit(",
            callback_source[hit_branch:],
        )

    def test_bot_accuracy_uses_the_installed_gun_dispersion(self):
        self.assertIn(
            "_bot_gun.get('shotDispersionAngle', 0.03)",
            self.battle_source,
        )
        self.assertNotIn("sigma = 0.03 / 3.0", self.battle_source)

    def test_player_and_bot_shells_share_arrival_time_collision_runtime(self):
        self.assertIn(
            "from projectile_trajectory import computeProjectileTrajectory",
            self.battle_source,
        )
        self.assertGreaterEqual(
            self.battle_source.count("_offh_launch_live_projectile("), 3
        )
        self.assertIn("_offh_live_projectile_advance(", self.battle_source)
        self.assertIn(
            "compensate_segment_for_moving_target(", self.battle_source
        )
        self.assertIn("trajectory_position(", self.battle_source)
        self.assertIn("substep_boundaries(", self.battle_source)
        self.assertIn(
            "PROJECTILE_MAX_SUBSTEP_SECONDS = 0.025",
            self.projectile_source,
        )
        self.assertIn(
            "_bp_want = float(_artillery_solution['pitch'])",
            self.battle_source,
        )
        self.assertIn(
            "_bp_want = float(_direct_fire_solution['pitch'])",
            self.battle_source,
        )
        self.assertIn(
            "from projectile_trajectory import getShotAngles",
            self.battle_source,
        )
        active_shell = self.battle_source.index(
            "descr.activeGunShotIndex = _gun_state.get('shot_index', 0)"
        )
        native_solution = self.battle_source.index(
            "(shotTurretYaw, shotGunPitch) = getShotAngles(", active_shell
        )
        self.assertLess(active_shell, native_solution)
        self.assertIn("StrategicControlMode", self.battle_source)
        self.assertIn("_fired_bot_gravity = Math.Vector3(", self.battle_source)
        self.assertIn("_fired_gravity = Math.Vector3(", self.battle_source)
        self.assertNotIn(
            "return (r0 + v0.scale(2000.0), 2000.0 / speed)",
            self.battle_source,
        )

    def test_spawn_hides_components_and_uses_baked_ground_layer(self):
        self.assertIn("for _loaded_component in (ch, hu, tu, gu):", self.battle_source)
        self.assertIn("nearest_ground_point(_spawn_graph, _x, _z, 3)", self.battle_source)
        self.assertIn(
            "_gy = _gc[0].y if _gc is not None else _baked_y",
            self.battle_source,
        )

    def test_bot_pose_is_committed_before_native_consumers_are_registered(self):
        commit = self.battle_source.index("_VP.commit_pose(e_mock, e_mock.position")
        obstacle = self.battle_source.index("e_mock._collision_obstacle =")
        minimap = self.battle_source.index("minimap.notifyVehicleStart(e_mock.id)")

        self.assertLess(commit, obstacle)
        self.assertLess(commit, minimap)
        self.assertNotIn("m_veh.position = m_veh.model.position", self.battle_source)
        self.assertNotIn("ch.position = e_mock.position", self.battle_source)
        self.assertNotIn("m.position = m.model.position", self.pen_indicator_source)

    def test_bot_rejects_an_unclimbable_support_rise_without_popping_up(self):
        bot_support = self.battle_source.index("_bsup = _offh_perf_call(")
        branch = self.battle_source.index(
            "_VC.support_rise_is_obstacle(", bot_support
        )
        end = self.battle_source.index(
            "elif m_veh.position.y <= _bg_y", branch
        )
        branch_source = self.battle_source[branch:end]

        self.assertIn("_offh_ai_tick_dry_pose", branch_source)
        self.assertIn("remember_failure(eid, target_yaw, 5.0)", branch_source)
        self.assertIn("_offh_ai_probe_reject(m_veh, 'obstacle')", branch_source)
        self.assertNotIn("Math.Vector3(m_veh.position.x, _bc_y", branch_source)

    def test_destroyed_support_hit_continues_down_to_real_terrain(self):
        support, collision_calls = self._run_terrain_support(True)

        self.assertEqual((0.0, 0.0, 0.0, 0.0), support)
        self.assertEqual(6, len(collision_calls))

    def test_intact_support_hit_keeps_the_object_top(self):
        support, collision_calls = self._run_terrain_support(False)

        self.assertEqual((0.55, 0.55, 0.55, 0.55), support)
        self.assertEqual(3, len(collision_calls))

    def test_destroyed_support_without_a_lower_hit_is_not_accepted(self):
        support, collision_calls = self._run_terrain_support(True, "none")

        self.assertEqual((None, None, None, None), support)
        self.assertEqual(6, len(collision_calls))

    def test_repeated_destroyed_support_exhaustion_is_not_accepted(self):
        support, collision_calls = self._run_terrain_support(True, "same")

        self.assertEqual((None, None, None, None), support)
        self.assertEqual(12, len(collision_calls))

    def test_destructible_material_probe_uses_the_stock_surface_normal(self):
        resolver_start = self.battle_source.index(
            "def _offh_mat_info_for_segment_hit("
        )
        resolver_end = self.battle_source.index(
            "\ndef _offh_destructible_mat_passable(", resolver_start
        )
        resolver_source = self.battle_source[resolver_start:resolver_end]
        material_calls = []

        def material_probe(space_id, segment_start, segment_end, query_point,
                           callback):
            material_calls.append(
                (space_id, segment_start, segment_end, query_point, callback)
            )
            return "material-info"

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_getMatInfoNearPoint = material_probe
        previous_bigworld = sys.modules.get("BigWorld")
        sys.modules["BigWorld"] = bigworld
        try:
            namespace = {}
            exec(resolver_source, namespace)
            resolver = namespace["_offh_mat_info_for_segment_hit"]
            hit_point = _Vector3(2.0, 0.0, 2.0)
            # Deliberately oblique: the travel direction is diagonal while the
            # authored contact surface normal is purely horizontal.
            surface_normal = _Vector3(1.0, 0.0, 0.0)
            result = resolver(7, hit_point, surface_normal)
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld

        self.assertEqual("material-info", result)
        self.assertEqual(1, len(material_calls))
        space_id, segment_start, segment_end, query_point, callback = (
            material_calls[0]
        )
        self.assertEqual(7, space_id)
        self.assertEqual((-1.0, 0.0, 2.0), (
            segment_start.x, segment_start.y, segment_start.z
        ))
        self.assertEqual((4.0, 0.0, 2.0), (
            segment_end.x, segment_end.y, segment_end.z
        ))
        self.assertIs(hit_point, query_point)
        self.assertFalse(callback())

    def test_each_horizontal_hull_lane_passes_hit_and_normal_to_resolver(self):
        collision_start = self.battle_source.index(
            "\t\t\t\tdef _check_horizontal_collision("
        )
        collision_end = self.battle_source.index(
            "\n\t\t\t\tdef _offh_land_impact(", collision_start
        )
        collision_source = textwrap.dedent(
            self.battle_source[collision_start:collision_end]
        )
        lane_contacts = {}
        resolver_calls = []
        material_calls = []

        def collide_segment(unused_space_id, start, end, unused_mask):
            if start.y != end.y:
                return (_Vector3(start.x, start.y - 1.0, start.z),
                        _Vector3(0.0, 1.0, 0.0))
            if ((abs(start.y - 0.6) < 0.01 or
                    abs(start.y - 1.6) < 0.01) and
                    start.z <= 2.0 <= end.z):
                lane = -1 if start.x < -1.0 else (1 if start.x > 1.0 else 0)
                hit = _Vector3(start.x, start.y, 2.0)
                normal = _Vector3(-1.0 if lane < 0 else 1.0, 0.0, 0.0)
                # The production resolver consumes the lower swept contact;
                # retain that exact object identity when the upper probe runs.
                lane_contacts.setdefault(lane, (hit, normal))
                return (hit, normal)
            return None

        def material_probe(space_id, segment_start, segment_end, query_point,
                           callback):
            material_calls.append(query_point)
            return None

        def resolve_hit(*args):
            resolver_calls.append(args)
            return True

        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = collide_segment
        bigworld.wg_getMatInfoNearPoint = material_probe
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        namespace = {
            "_offh_perf_count": lambda *unused: None,
            "_VC": types.SimpleNamespace(
                drivable_rising_profile=lambda *unused: False
            ),
            "_try_destroy_solid_hit": resolve_hit,
            "math": math,
        }
        crush_vehicle = object()
        try:
            exec(collision_source, namespace)
            blocked = namespace["_check_horizontal_collision"](
                crush_vehicle, 7, _Vector3(0.0, 0.0, 0.0), 0.0, 5.0,
                None, False, 0.04
            )
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math

        self.assertEqual(3, len(resolver_calls))
        for lane in (-1, 0, 1):
            hit, normal = lane_contacts[lane]
            self.assertTrue(any(
                len(args) == 6 and args[0] is crush_vehicle and
                args[2] is hit and args[3] is normal
                for args in resolver_calls
            ))
        # Any direct material lookup retained in this path must query the
        # collision hit itself, never the ray endpoint beyond the fence.
        valid_hits = tuple(hit for hit, unused in lane_contacts.values())
        self.assertTrue(all(point in valid_hits for point in material_calls))

    def test_fragiles_are_not_destroyed_by_item_origin_proximity(self):
        proximity_start = self.battle_source.index(
            "\t\t\t\tdef _fell_trees_near("
        )
        proximity_end = self.battle_source.index(
            "\n\t\t\t\tdef _try_destroy_solid_hit(", proximity_start
        )
        proximity_source = textwrap.dedent(
            self.battle_source[proximity_start:proximity_end]
        )
        destroyed_fragiles = []

        class Matrix(object):
            def __init__(self, unused=None):
                self.translation = _Vector3(0.0, 0.0, 1.0)

        class Manager(object):
            @staticmethod
            def getSpaceID():
                return 7

        class Authority(object):
            @staticmethod
            def destroy_fragile(*args):
                destroyed_fragiles.append(args)
                return True

            @staticmethod
            def destroy_tree(*unused):
                return True

            @staticmethod
            def destroy_column(*unused):
                return True

        area = types.ModuleType("AreaDestructibles")
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.g_destructiblesManager = Manager()
        area.chunkIDFromPosition = lambda unused: 100
        area.g_cache = types.SimpleNamespace(
            getDescByFilename=lambda unused: {
                "type": area.DESTR_TYPE_FRAGILE,
                "health": 3,
                "mass": 1,
            }
        )
        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_getChunkDestrFilenames = (
            lambda *unused: ["objects/long_fence.model"]
        )
        bigworld.wg_getChunkMatrix = lambda *unused: Matrix()
        bigworld.wg_getDestructibleMatrix = lambda *unused: Matrix()
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        math_module.Matrix = Matrix
        previous_area = sys.modules.get("AreaDestructibles")
        sys.modules["AreaDestructibles"] = area
        namespace = {
            "BigWorld": bigworld,
            "Math": math_module,
            "LOG_DEBUG": lambda *unused: None,
            "_get_destr_authority": lambda: Authority(),
            "xrange": range,
        }
        try:
            exec(proximity_source, namespace)
            namespace["_fell_trees_near"](
                object(), 7, _Vector3(0.0, 0.0, 0.0), 0.0, 5.0, None
            )
        finally:
            if previous_area is None:
                sys.modules.pop("AreaDestructibles", None)
            else:
                sys.modules["AreaDestructibles"] = previous_area

        self.assertEqual([], destroyed_fragiles)

    def test_tree_proximity_still_requires_the_retail_crush_gate(self):
        proximity_start = self.battle_source.index(
            "\t\t\t\tdef _fell_trees_near("
        )
        proximity_end = self.battle_source.index(
            "\n\t\t\t\tdef _try_destroy_solid_hit(", proximity_start
        )
        proximity = self.battle_source[proximity_start:proximity_end]

        crush = proximity.index("_auth.can_crush(")
        destroy_tree = proximity.index("_auth.destroy_tree(")
        self.assertLess(crush, destroy_tree)
        self.assertIn(
            "crush_vehicle, spaceID, cid, _ti, 0, _tfn, vel", proximity
        )

        player_call = self.battle_source.index(
            "_fell_trees_near(mock_veh, _offh_bspace()"
        )
        bot_call = self.battle_source.index(
            "'tree_scan', _fell_trees_near,"
        )
        self.assertIn(
            "m_veh, _ai_space_id", self.battle_source[bot_call:bot_call + 180]
        )
        self.assertGreater(player_call, 0)

    def test_horizontal_collision_errors_remain_blocked(self):
        collision_start = self.battle_source.index(
            "\t\t\t\tdef _check_horizontal_collision("
        )
        collision_end = self.battle_source.index(
            "\n\t\t\t\tdef _offh_land_impact(", collision_start
        )
        collision_source = textwrap.dedent(
            self.battle_source[collision_start:collision_end]
        )
        bigworld = types.ModuleType("BigWorld")
        bigworld.wg_collideSegment = lambda *unused: (_ for _ in ()).throw(
            RuntimeError("collision API failed")
        )
        math_module = types.ModuleType("Math")
        math_module.Vector3 = _Vector3
        previous_bigworld = sys.modules.get("BigWorld")
        previous_math = sys.modules.get("Math")
        sys.modules["BigWorld"] = bigworld
        sys.modules["Math"] = math_module
        namespace = {
            "_offh_perf_count": lambda *unused: None,
            "_VC": types.SimpleNamespace(
                drivable_rising_profile=lambda *unused: False
            ),
            "_try_destroy_solid_hit": lambda *unused: False,
            "math": math,
        }
        try:
            exec(collision_source, namespace)
            self.assertTrue(namespace["_check_horizontal_collision"](
                object(), 7, _Vector3(0.0, 0.0, 0.0), 0.0, 5.0,
                None, False, 0.04,
            ))
        finally:
            if previous_bigworld is None:
                sys.modules.pop("BigWorld", None)
            else:
                sys.modules["BigWorld"] = previous_bigworld
            if previous_math is None:
                sys.modules.pop("Math", None)
            else:
                sys.modules["Math"] = previous_math

    def test_player_rejects_an_unclimbable_support_rise_before_ground_follow(self):
        player_support = self.battle_source.index("_sup = _terrain_support(")
        player_ground_end = self.battle_source.index(
            "# --- Drowning:", player_support
        )
        player_ground = self.battle_source[player_support:player_ground_end]

        self.assertIn("_VC.support_rise_is_obstacle(", player_ground)
        guard = player_ground.index("_VC.support_rise_is_obstacle(")
        buried = player_ground.index(
            "elif _centre_y is not None and veh_pos[1] < _centre_y"
        )
        self.assertLess(guard, buried)

    def test_realised_wall_and_tank_contacts_invalidate_driver_orders(self):
        wall = self.battle_source.index("if _hit_wall:")
        wall_end = self.battle_source.index("else:", wall)
        wall_source = self.battle_source[wall:wall_end]
        self.assertIn("_offh_ai_probe_reject(m_veh, 'obstacle')", wall_source)
        self.assertIn("eid, target_yaw, 5.0", wall_source)

        tank = self.battle_source.index("if abs(_btr[0]) + abs(_btr[1]) > 0.01:")
        tank_source = self.battle_source[tank:tank + 900]
        self.assertIn("m_veh._offh_ai_driver_cache = None", tank_source)
        self.assertIn("eid, target_yaw, 0.8", tank_source)

    def test_bot_spawn_stages_cosmetic_stickers_and_batches_roster_refresh(self):
        self.assertIn("_sticker_setup_done = False", self.battle_source)
        self.assertIn("_offh_queue_sticker_warmup(player, e_mock)", self.battle_source)
        self.assertIn("_offh_battle_callback(0.03, _drain_one)", self.battle_source)
        self.assertIn("_target_sticker_map(target_mock, component_name=None)", self.battle_source)
        self.assertIn("_offh_auto_spawn_completed >= int(getattr(", self.battle_source)

    def test_lan_countdown_and_duration_use_server_deadlines(self):
        self.assertIn("_offhangar_network_combat_deadline", self.battle_source)
        self.assertIn("_offh_server_battle_remaining(player, 900.0)", self.battle_source)
        self.assertIn("self._load_server_timing(message)", self.network_source)

    def test_lan_prepares_lineup_behind_loading_page_without_gating_countdown(self):
        self.assertIn("_auto_spawn_not_before = time.time() + _auto_spawn_delay", self.battle_source)
        self.assertIn("0.25, _auto_spawn_teams", self.battle_source)
        self.assertIn("_place_delay = max(0.0, _spawn_not_before - time.time())", self.battle_source)
        self.assertNotIn("_offh_local_lineup_ready", self.battle_source)
        self.assertNotIn("loading screen waiting for local bot resources", self.battle_source)

    def test_forced_lineup_vehicle_skips_random_candidate_scan(self):
        forced = self.battle_source.index("if _fv:")
        candidate_scan = self.battle_source.index("for nation in nations.AVAILABLE_NAMES", forced)
        fallback = self.battle_source.rfind("else:", forced, candidate_scan)
        self.assertGreater(fallback, forced)


if __name__ == "__main__":
    unittest.main()
