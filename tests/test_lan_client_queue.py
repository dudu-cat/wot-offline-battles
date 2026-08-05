import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NETWORK_PATH = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"


def load_network_module():
    bigworld = types.ModuleType("BigWorld")
    bigworld.callback = lambda delay, callback: None
    bigworld.time = lambda: 100.0
    bigworld.wg_collideSegment = lambda *args: None
    sys.modules["BigWorld"] = bigworld

    math_module = types.ModuleType("Math")

    class Vector3:
        def __init__(self, x, y=None, z=None):
            if y is None and z is None:
                x, y, z = x
            self.x = float(x)
            self.y = float(y)
            self.z = float(z)

    math_module.Vector3 = Vector3
    sys.modules["Math"] = math_module

    keys_module = types.ModuleType("Keys")
    keys_module.KEY_P = 25
    sys.modules["Keys"] = keys_module

    for name in ("gui", "gui.mods", "gui.mods.offhangar"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    logging_module = types.ModuleType("gui.mods.offhangar.logging")
    logging_module.LOG_DEBUG = lambda *args: None
    logging_module.LOG_ERROR = lambda *args: None
    logging_module.LOG_NOTE = lambda *args: None
    sys.modules[logging_module.__name__] = logging_module

    notices = []
    system_messages = types.ModuleType("gui.SystemMessages")
    system_messages.SM_TYPE = types.SimpleNamespace(
        Error="error", Warning="warning", Information="information"
    )
    system_messages.pushMessage = lambda message, level: notices.append((message, level))
    sys.modules[system_messages.__name__] = system_messages

    constants_module = types.ModuleType("gui.mods.offhangar._constants")
    constants_module.CONFIG_OPTIONS = {"network_mode": True}
    sys.modules[constants_module.__name__] = constants_module

    shown = []
    shot_presentations = []
    hit_presentations = []
    offline_module = types.ModuleType("gui.mods.offhangar.offline_battle")
    offline_module.show_network_waiting_queue_from_server = lambda player: shown.append(player)
    offline_module.play_network_remote_shot = lambda *args: shot_presentations.append(args)
    offline_module.play_network_hit_feedback = lambda *args: hit_presentations.append(args)
    sys.modules[offline_module.__name__] = offline_module

    spec = importlib.util.spec_from_file_location("lan_client_under_test", NETWORK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._test_notices = notices
    module._test_shot_presentations = shot_presentations
    module._test_hit_presentations = hit_presentations
    return module, shown


def install_vehicle_descriptors(specs):
    constants = types.ModuleType("constants")
    constants.VEHICLE_CLASS_INDICES = {
        "lightTank": 0,
        "mediumTank": 1,
        "heavyTank": 2,
        "SPG": 3,
        "AT-SPG": 4,
    }
    constants.MAX_VEHICLE_LEVEL = 10
    sys.modules["constants"] = constants

    items = types.ModuleType("items")

    def vehicle_descr(typeName=None, compactDescr=None):
        tags, level = specs[typeName]
        return types.SimpleNamespace(
            type=types.SimpleNamespace(tags=set(tags), level=level, name=typeName),
            maxHealth=1000,
        )

    items.vehicles = types.SimpleNamespace(VehicleDescr=vehicle_descr)
    sys.modules["items"] = items


class Player:
    def __init__(self):
        self.queue_updates = []

    def receiveQueueInfo(self, randoms, companies):
        self.queue_updates.append((randoms, companies))


class LANClientQueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network, cls.shown = load_network_module()

    def test_client_input_and_bot_state_run_at_thirty_hz(self):
        self.assertAlmostEqual(1.0 / 30.0, self.network.INPUT_INTERVAL)
        self.assertAlmostEqual(1.0 / 30.0, self.network.BOT_STATE_INTERVAL)
        self.assertAlmostEqual(1.0 / 60.0, self.network.POLL_INTERVAL)

    def test_start_button_does_not_replace_battle_join(self):
        player = Player()

        self.assertTrue(self.network.request_battle_start(player))
        self.assertFalse(hasattr(player, "_offhangar_network_start_when_ready"))

    def test_start_button_during_connection_is_not_queued(self):
        player = Player()
        player._offhangar_network_client = types.SimpleNamespace(
            running=True, ready=False, phase="connecting"
        )

        self.assertTrue(self.network.request_battle_start(player))
        self.assertFalse(hasattr(player, "_offhangar_network_start_when_ready"))

    def test_clickable_start_sends_the_selected_map(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        sent = []
        client.ready = True
        client.phase = "waiting"
        client._send = lambda message: sent.append(message) or True

        self.assertTrue(client.request_start("31_airfield"))

        self.assertEqual({"type": "start_battle", "map": "31_airfield"}, sent[-1])

    def test_bot_observation_carries_bounded_cover_affordances(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        sent = []
        client._last_bot_observation = 0.0
        client._send = lambda message: sent.append(message) or True
        affordances = [{
            "bot_id": 3,
            "target_id": 7,
            "target_kind": "bot",
            "candidates": [{"id": "rock"}],
        }]

        navigation = {
            "total": {"safe_direct": 2, "safe_local": 3, "reactive": 1},
            "active": {"safe_direct": 0, "safe_local": 1, "reactive": 1},
            "recovered": 4,
        }
        self.assertTrue(client.send_bot_observation(
            [{"observing_team": 1, "target_id": 7, "target_kind": "bot",
              "target_team": 2, "position": (0, 0, 1)}], affordances, navigation
        ))

        self.assertEqual("bot_observation", sent[-1]["type"])
        self.assertEqual(affordances, sent[-1]["affordances"])
        self.assertEqual(1, len(sent[-1]["contacts"]))
        self.assertEqual(navigation, sent[-1]["navigation"])

    def test_observation_failed_send_does_not_consume_throttle_window(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")
        old_time = self.network.time.time
        self.network.time.time = lambda: 10.0
        try:
            client._send = lambda message: False
            self.assertFalse(client.send_bot_observation([], []))
            self.assertEqual(0.0, client._last_bot_observation)
            client._send = lambda message: True
            self.assertTrue(client.send_bot_observation([], []))
            self.assertEqual(10.0, client._last_bot_observation)
        finally:
            self.network.time.time = old_time

    def test_publish_observation_caps_before_conversion_and_whitelists_candidates(self):
        player = Player()
        captured = []
        player._offhangar_network_is_authority = True
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle",
            send_bot_observation=lambda contacts, affordances, navigation: captured.append(
                (contacts, affordances, navigation)) or True,
        )
        contact = {
            "observing_team": 1, "target_id": 7, "target_kind": "bot", "target_team": 2,
            "position": (1, 2, 3), "health": 100, "max_health": 200,
            "class_tag": "heavyTank", "armor": 80, "visible": True,
        }
        candidate = {
            "id": "rock", "position": (4, 5, 6), "peek_position": (7, 8, 9),
            "travel_distance": 10, "route_alignment": 0.5, "enemy_occlusion": 0.8,
            "exposure": 0.1, "slope": 4, "water": 0, "ally_congestion": 0.2,
            "peek_feasible": True, "escape_feasible": True, "not_json": object(),
        }
        reports = [{"bot_id": index, "target_id": 7, "target_kind": "bot",
                    "candidates": [candidate] * 13} for index in range(20)]

        raw_navigation = {
            "total": {"safe_direct": 2, "safe_local": 3, "reactive": -5,
                      "ignored": 999},
            "active": {"safe_direct": 0, "safe_local": 1, "reactive": 2},
            "recovered": 4,
            "search": {"pending": 13, "completed": 4, "failed": 2,
                       "oldest_ms": 12345, "ignored": 999},
            "aim": {"alive": 29, "targeted": 15, "aligned": 4,
                    "traversing": 11, "limited": 7, "ignored": 999},
            "driver": {"moving": 9, "drive": 8, "avoid": 3,
                       "blocked": 6, "recovery": 7, "arrived": 5,
                       "ignored": 999},
        }
        self.assertTrue(self.network.publish_bot_observation(
            player, [contact] * 65, reports, raw_navigation
        ))
        contacts, affordances, navigation = captured[0]
        self.assertEqual(64, len(contacts))
        self.assertEqual(16, len(affordances))
        self.assertEqual(12, len(affordances[0]["candidates"]))
        self.assertNotIn("not_json", affordances[0]["candidates"][0])
        self.assertEqual(0, navigation["total"]["reactive"])
        self.assertEqual(3, navigation["total"]["safe_local"])
        self.assertNotIn("ignored", navigation["total"])
        self.assertEqual(4, navigation["recovered"])
        self.assertEqual(13, navigation["search"]["pending"])
        self.assertEqual(12345, navigation["search"]["oldest_ms"])
        self.assertNotIn("ignored", navigation["search"])
        self.assertEqual(15, navigation["aim"]["targeted"])
        self.assertEqual(11, navigation["aim"]["traversing"])
        self.assertNotIn("ignored", navigation["aim"])
        self.assertEqual(9, navigation["driver"]["moving"])
        self.assertEqual(6, navigation["driver"]["blocked"])
        self.assertNotIn("ignored", navigation["driver"])

        captured[:] = []
        malformed_contact = dict(contact, position={"x": 1})
        strict_contact = dict(contact, visible="false")
        malformed_candidate = dict(candidate, position={"x": 4})
        strict_candidate = dict(
            candidate, peek_feasible="false", escape_feasible="true"
        )
        strict_report = [{
            "bot_id": 1, "target_id": 7, "target_kind": "bot",
            "candidates": [malformed_candidate, strict_candidate],
        }]

        self.assertTrue(self.network.publish_bot_observation(
            player, [malformed_contact, strict_contact], strict_report
        ))
        contacts, affordances, navigation = captured[0]
        self.assertEqual(1, len(contacts))
        self.assertFalse(contacts[0]["visible"])
        self.assertEqual(1, len(affordances[0]["candidates"]))
        self.assertFalse(affordances[0]["candidates"][0]["peek_feasible"])
        self.assertFalse(affordances[0]["candidates"][0]["escape_feasible"])
        self.assertIsNone(navigation)

    def test_send_rejects_client_message_over_server_limit(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")
        client.connected = True
        client.sock = types.SimpleNamespace(sendall=lambda payload: None)
        self.assertFalse(client._send({"type": "oversized", "data": "x" * self.network.MAX_MESSAGE_BYTES}))

    def test_roster_updates_native_queue_payload(self):
        install_vehicle_descriptors({
            "china:Type_59": ({"mediumTank"}, 8),
            "ussr:IS": ({"heavyTank"}, 7),
            "usa:M37": ({"SPG"}, 3),
        })
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")
        player._offhangar_network_client = client

        client._handle_message({
            "type": "roster",
            "phase": "waiting",
            "players": [
                {"vehicle": "china:Type_59"},
                {"vehicle": "ussr:IS"},
                {"vehicle": "usa:M37"},
            ],
        })

        randoms, companies = player.queue_updates[-1]
        self.assertEqual({}, companies)
        self.assertEqual(3, sum(randoms["levels"]))
        self.assertEqual(3, sum(randoms["classes"]))
        self.assertEqual([0, 1, 1, 1, 0], randoms["classes"])
        self.assertEqual(1, randoms["levels"][3])
        self.assertEqual(1, randoms["levels"][7])
        self.assertEqual(1, randoms["levels"][8])

    def test_queue_screen_opens_only_after_waiting_welcome(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")
        player._offhangar_network_client = client

        client._handle_message({
            "type": "welcome",
            "player_id": 1,
            "name": "Player-158",
            "vehicle": "ussr:T-34",
            "team": 1,
            "slot": 2,
            "max_health": 880,
            "map": "04_himmelsdorf",
            "phase": "waiting",
            "round_id": 1,
        })

        self.assertIs(player, self.shown[-1])
        self.assertEqual("Player-158", player._offhangar_network_name)
        self.assertEqual("ussr:T-34", player._offhangar_network_vehicle)
        self.assertEqual(2, player._offhangar_network_slot)

    def test_server_errors_use_the_stock_system_message_channel(self):
        self.network._test_notices[:] = []
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")

        client._handle_message({"type": "error", "message": "protocol mismatch"})

        message, level = self.network._test_notices[-1]
        self.assertIn(b"protocol mismatch", message)
        self.assertEqual("error", level)

    def test_selected_vehicle_comes_from_current_garage_item(self):
        current_vehicle = types.ModuleType("CurrentVehicle")
        descriptor = types.SimpleNamespace(
            type=types.SimpleNamespace(name="china:Type_59"),
            maxHealth=1300,
        )
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            vehicle=types.SimpleNamespace(descriptor=descriptor),
            item=None,
        )
        sys.modules["CurrentVehicle"] = current_vehicle

        self.assertEqual(
            ("china:Type_59", 1300),
            self.network._selected_vehicle_details(Player()),
        )

    def test_selected_vehicle_falls_back_to_selected_inventory_descriptor(self):
        current_vehicle = types.ModuleType("CurrentVehicle")
        current_vehicle.g_currentVehicle = types.SimpleNamespace(vehicle=None, item=None)
        sys.modules["CurrentVehicle"] = current_vehicle

        items = types.ModuleType("items")
        items.vehicles = types.SimpleNamespace(
            VehicleDescr=lambda compactDescr: types.SimpleNamespace(
                type=types.SimpleNamespace(name="usa:M4_Sherman"),
                maxHealth=460,
            )
        )
        sys.modules["items"] = items
        player = Player()
        player._offhangar_network_pending_veh_id = 42
        player.inventory = types.SimpleNamespace(
            _Inventory__cache={"inventory": {1: {"compDescr": {42: 12345}}}}
        )

        self.assertEqual(
            ("usa:M4_Sherman", 460),
            self.network._selected_vehicle_details(player),
        )

    def test_server_coordinates_use_the_remote_players_slot(self):
        player = Player()
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, slot * 10.0, 0.0) if team == 1 else (100.0, slot * 10.0, 3.14)
        )
        point = self.network._world_from_server(player, {
            "team": 1,
            "slot": 2,
            "spawn_x": 24.0,
            "spawn_z": -35.0,
            "x": 24.0,
            "y": 0.0,
            "z": -35.0,
        })

        self.assertEqual((0.0, 0.0, 20.0), (point.x, point.y, point.z))

    def test_world_pose_round_trips_into_the_forward_server_axis(self):
        player = Player()
        player._offhangar_network_team = 1
        player._offhangar_network_slot = 0
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, slot * 10.0, math.pi / 2.0)
            if team == 1
            else (100.0, slot * 10.0, -math.pi / 2.0)
        )

        position, yaw = self.network._server_pose_from_world(
            player, 0.0, 0.0, 0.0, math.pi / 2.0
        )

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertAlmostEqual(0.0, yaw)

        remote = self.network._world_from_server(player, {
            "world_pose": True,
            "x": 0.0,
            "y": 0.0,
            "z": 100.0,
        })
        self.assertEqual((100.0, 0.0, 0.0), (remote.x, remote.y, remote.z))

    def test_remote_spawn_is_deduplicated_while_models_load(self):
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {}
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, slot * 10.0, 0.0) if team == 1 else (100.0, slot * 10.0, 3.14)
        )
        calls = []
        player._offhangar_network_spawn_remote = lambda event: calls.append(event)
        state = {
            "id": 2,
            "name": "Bravo",
            "vehicle": "ussr:T-34",
            "team": 2,
            "slot": 0,
            "spawn_x": 0.0,
            "spawn_z": 35.0,
            "x": 0.0,
            "y": 0.0,
            "z": 35.0,
            "yaw": 3.14,
        }

        self.network._apply_remote_state(player, state)
        self.network._apply_remote_state(player, state)

        self.assertEqual(1, len(calls))

    def test_local_hit_event_and_snapshot_do_not_double_apply_damage(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_server_health = 880
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        mock = types.SimpleNamespace(
            id=1,
            health=880,
            maxHealth=880,
            isAlive=True,
            publicInfo={"isAlive": True},
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {1: mock}

        self.network._handle_events(player, [{
            "kind": "hit",
            "attacker": 2,
            "target": 1,
            "damage": 100,
            "health": 780,
            "dead": False,
        }])
        self.network._apply_snapshot(player, {"players": [{
            "id": 1,
            "health": 780,
            "max_health": 880,
            "alive": True,
        }]})

        self.assertEqual(780, mock.health)

    def test_remote_shot_and_local_hit_use_offline_battle_effects(self):
        self.network._test_shot_presentations[:] = []
        self.network._test_hit_presentations[:] = []
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        player.playerVehicleID = 1
        player._offhangar_network_server_health = 880
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            position=vector3(0, 0, 0), publicInfo={"isAlive": True},
        )
        remote = types.SimpleNamespace(
            id=1001, health=880, maxHealth=880, isAlive=True,
            position=vector3(0, 0, -50), publicInfo={"isAlive": True},
            _network_server_id=2,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local, 1001: remote,
        }

        self.network._handle_events(player, [{
            "kind": "shot", "attacker": 2, "shot_seq": 4,
            "shell_index": 1, "world_pose": True,
            "x": 0, "y": 0, "z": -50, "aim_yaw": 0, "gun_pitch": 0,
        }, {
            "kind": "hit", "attacker": 2, "target": 1,
            "shot_seq": 4, "shell_index": 1, "shot_result": 2,
            "damage": 120, "health": 760, "dead": False,
            "world_pose": True, "x": 0, "y": 1, "z": 0,
        }])

        self.assertEqual(1, len(self.network._test_shot_presentations))
        self.assertEqual(1, len(self.network._test_hit_presentations))
        self.assertTrue(self.network._test_hit_presentations[0][7])
        self.assertEqual(760, local.health)

    def test_remote_death_notifies_the_arena_only_once(self):
        player = Player()
        player._offhangar_network_id = 1
        player.playerVehicleID = 1
        deaths = []
        player.arena = types.SimpleNamespace(
            onVehicleKilled=lambda *args: deaths.append(args)
        )
        mock = types.SimpleNamespace(
            id=1001,
            health=100,
            maxHealth=880,
            isAlive=True,
            publicInfo={"isAlive": True},
            _network_server_id=2,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {1001: mock}
        state = {
            "id": 2,
            "health": 0,
            "max_health": 880,
            "alive": False,
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "yaw": 0.0,
        }

        self.network._apply_remote_state(player, state)
        self.network._apply_remote_state(player, state)

        self.assertEqual(0, mock.health)
        self.assertEqual(1, len(deaths))

    def test_remote_snapshot_moves_the_render_model_and_aim_matrices(self):
        class Matrix:
            def __init__(self):
                self.rotation = None
                self.translation = None

            def setRotateYPR(self, rotation):
                self.rotation = rotation

        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        model = types.SimpleNamespace(position=vector3(0, 100, 0), yaw=0.0)
        stale_player_filter = types.SimpleNamespace(position=vector3(0, 0, 0))
        mock = types.SimpleNamespace(
            id=1001,
            health=880,
            maxHealth=880,
            isAlive=True,
            publicInfo={"isAlive": True},
            _network_server_id=2,
            position=vector3(0, 100, 0),
            yaw=0.0,
            pitch=0.0,
            roll=0.0,
            matrix=Matrix(),
            model=model,
            _chassis_model=model,
            filter=stale_player_filter,
            bw_entity=None,
            _t_mat=Matrix(),
            _g_mat=Matrix(),
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {1001: mock}

        self.network._apply_remote_state(player, {
            "id": 2,
            "world_pose": True,
            "x": 4.0,
            "y": 5.0,
            "z": 12.0,
            "yaw": 0.75,
            "aim_yaw": 1.0,
            "gun_pitch": -0.1,
            "health": 880,
            "max_health": 880,
            "alive": True,
        })

        self.assertEqual((4.0, 5.0, 12.0), (model.position.x, model.position.y, model.position.z))
        self.assertAlmostEqual(0.75, model.yaw)
        self.assertEqual((0.75, 0.0, 0.0), mock.matrix.rotation)
        self.assertEqual((0.25, 0, 0), mock._t_mat.rotation)
        self.assertEqual((0, -0.1, 0), mock._g_mat.rotation)
        self.assertEqual((0.0, 0.0, 0.0), (
            stale_player_filter.position.x,
            stale_player_filter.position.y,
            stale_player_filter.position.z,
        ))

    def test_shared_bot_snapshot_moves_and_replays_only_new_shots(self):
        class Matrix:
            def __init__(self):
                self.rotation = None
                self.translation = None

            def setRotateYPR(self, rotation):
                self.rotation = rotation

        self.network._test_shot_presentations[:] = []
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player.playerVehicleID = 1
        player._offhangar_team = 1
        player._offhangar_network_id = 1
        player._offhangar_network_is_authority = False
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        model = types.SimpleNamespace(
            position=vector3(0, 0, 0), yaw=0.0,
            visible=True, visibleAttachments=True,
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, _network_shared_bot=True,
            _bot_team=2, health=500, maxHealth=500, isAlive=True,
            publicInfo={"team": 2, "isAlive": True},
            position=vector3(0, 0, 0), yaw=0.0, pitch=0.0, roll=0.0,
            matrix=Matrix(), _t_mat=Matrix(), _g_mat=Matrix(),
            model=model, _chassis_model=model, bw_entity=None,
            marker=None, proxy=None,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {1016: bot}
        state = {
            "id": 16, "team": 2, "slot": 0, "world_pose": True,
            "x": 5.0, "y": 2.0, "z": 20.0, "yaw": 0.5,
            "aim_yaw": 0.75, "gun_pitch": -0.1, "fire_seq": 3,
            "shell_index": 1, "health": 500, "max_health": 500, "alive": True,
        }

        self.network._apply_bot_state(player, state)
        self.network._apply_bot_state(player, state)

        self.assertEqual((5.0, 2.0, 20.0), (
            model.position.x, model.position.y, model.position.z,
        ))
        self.assertEqual((0.25, 0, 0), bot._t_mat.rotation)
        self.assertEqual((0, -0.1, 0), bot._g_mat.rotation)
        self.assertEqual(3, bot._network_bot_fire_seq)
        self.assertEqual(1, bot._network_bot_shell_index)
        self.assertEqual(1, len(self.network._test_shot_presentations))

    def test_snapshot_stores_revisioned_server_order_and_converts_coordinates(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        player._offhangar_network_client = client
        client.player_id = 1
        client.ready = True
        client.phase = "battle"

        client._handle_message({
            "type": "snapshot", "bot_authority_id": 1,
            "players": [], "bots": [], "bot_order_revision": 7,
            "bot_orders": [{
                "id": 16, "target_id": 2, "target_kind": "human",
                "move_position": {"x": 5.0, "y": 2.0, "z": 20.0},
                "aim_position": {"x": -4.0, "y": 1.0, "z": 80.0},
            }],
        })

        mock = types.SimpleNamespace(_network_bot_id=16)
        order = self.network.authoritative_bot_order(player, mock)
        self.assertEqual(7, client.bot_order_revision)
        self.assertEqual(2, order["target_id"])
        self.assertEqual((5.0, 2.0, 20.0), order["move_position"])
        self.assertEqual((-4.0, 1.0, 80.0), order["aim_position"])

        client._handle_message({
            "type": "snapshot", "bot_authority_id": 1,
            "players": [], "bots": [], "bot_order_revision": 6,
            "bot_orders": [{"id": 16, "target_id": 999}],
        })
        self.assertEqual(2, client.bot_orders[16]["target_id"])

    def test_visible_server_target_aims_at_its_live_authority_pose(self):
        player = Player()
        player._offhangar_network_is_authority = True
        player._offhangar_network_id = 2
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle",
            bot_orders={16: {
                "id": 16, "target_id": 2, "target_kind": "human",
                "fire_allowed": True,
                "aim_position": {"x": -4.0, "y": 1.0, "z": 80.0},
                "face_position": {"x": -4.0, "y": 1.0, "z": 80.0},
            }}
        )
        bot = types.SimpleNamespace(_network_bot_id=16)
        local_target = types.SimpleNamespace(
            isAlive=True, health=880,
            position=types.SimpleNamespace(x=12.0, y=3.0, z=-7.0),
        )
        original_local_mock = self.network._local_mock
        self.network._local_mock = lambda unused_player: local_target
        try:
            order = self.network.authoritative_bot_order(player, bot)
        finally:
            self.network._local_mock = original_local_mock

        self.assertEqual((12.0, 3.0, -7.0), order["aim_position"])
        self.assertEqual(order["aim_position"], order["face_position"])

    def test_snapshot_same_revision_does_not_replace_orders_but_initial_zero_does(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")
        client._handle_message({
            "type": "snapshot", "bot_authority_id": 1, "players": [], "bots": [],
            "bot_order_revision": 0, "bot_orders": [{"id": 16, "target_id": 2}],
        })
        self.assertEqual(2, client.bot_orders[16]["target_id"])
        client._handle_message({
            "type": "snapshot", "bot_authority_id": 1, "players": [], "bots": [],
            "bot_order_revision": 0, "bot_orders": [{"id": 16, "target_id": 999}],
        })
        self.assertEqual(2, client.bot_orders[16]["target_id"])

    def test_remote_pose_is_interpolated_between_thirty_hz_snapshots(self):
        class Matrix:
            def setRotateYPR(self, rotation):
                self.rotation = rotation

        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        model = types.SimpleNamespace(position=vector3(0, 1, 0), yaw=0.0)
        mock = types.SimpleNamespace(
            id=1001, _network_server_id=2, _network_remote=True,
            health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
            position=vector3(0, 1, 0), yaw=0.0, pitch=0.0, roll=0.0,
            matrix=Matrix(), _t_mat=Matrix(), _g_mat=Matrix(),
            model=model, _chassis_model=model, bw_entity=None,
        )
        mocks = {1001: mock}
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = mocks

        base = {
            "id": 2, "world_pose": True, "y": 1.0,
            "yaw": 0.0, "aim_yaw": 0.0, "gun_pitch": 0.0,
            "health": 880, "max_health": 880, "alive": True,
        }
        first = dict(base, x=2.0, z=0.0)
        second = dict(base, x=12.0, z=0.0)
        self.network._apply_remote_state(player, first)
        self.network._apply_remote_state(player, second)

        self.assertEqual(2.0, model.position.x)
        self.assertTrue(self.network.advance_network_smoothing(player, mocks, 1.0 / 60.0))
        self.assertGreater(model.position.x, 2.0)
        self.assertLess(model.position.x, 13.0)

    def test_snapshot_applies_shared_rules_and_result_once(self):
        player = Player()
        rules = []
        results = []
        player._offhangar_network_id = 1
        player._offhangar_apply_network_rules_state = lambda value: rules.append(value)
        player._offhangar_apply_network_battle_result = lambda value: results.append(value)
        message = {
            "players": [], "bots": [],
            "rules": {"bases": {"1": {"points": 42, "stopped": False}}},
            "battle_result": {"winner": 2, "reason": "base captured", "base_team": 1},
        }

        self.network._apply_snapshot(player, message)
        self.network._apply_snapshot(player, message)

        self.assertEqual(2, len(rules))
        self.assertEqual(1, len(results))
        self.assertEqual(42, rules[-1]["bases"]["1"]["points"])

    def test_pong_updates_real_round_trip_time(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")

        client._handle_message({"type": "pong", "client_time": self.network.time.time() - 0.04})

        self.assertGreaterEqual(client.rtt_ms, 30.0)
        self.assertLess(client.rtt_ms, 200.0)

    def test_remote_enemy_uses_local_spotting_instead_of_always_visible(self):
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player.playerVehicleID = 1
        player._offhangar_team = 1
        local = types.SimpleNamespace(
            id=1,
            position=vector3(0, 0, 0),
            isAlive=True,
            typeDescriptor=types.SimpleNamespace(
                turret={"circularVisionRadius": 100.0}
            ),
            publicInfo={"team": 1},
        )
        model = types.SimpleNamespace(visible=True, visibleAttachments=True)
        remote = types.SimpleNamespace(
            id=1001,
            position=vector3(0, 0, 200),
            isAlive=True,
            health=880,
            _bot_team=2,
            _chassis_model=model,
            model=model,
            publicInfo={"team": 2},
            marker=None,
            proxy=None,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local, 1001: remote,
        }

        self.assertFalse(self.network.update_remote_spotting(player, remote, True))
        self.assertFalse(model.visible)

        remote.position = vector3(0, 0, 40)
        self.assertTrue(self.network.update_remote_spotting(player, remote, True))
        self.assertTrue(model.visible)

    def test_dead_remote_snapshots_do_not_move_the_wreck_anchor(self):
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        model = types.SimpleNamespace(position=vector3(4, 0, 12), yaw=0.75)
        mock = types.SimpleNamespace(
            id=1001,
            health=0,
            maxHealth=880,
            isAlive=False,
            publicInfo={"isAlive": False},
            _network_server_id=2,
            _network_death_notified=True,
            position=vector3(4, 0, 12),
            yaw=0.75,
            model=model,
            _chassis_model=model,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {1001: mock}

        self.network._apply_remote_state(player, {
            "id": 2,
            "world_pose": True,
            "x": 400.0,
            "y": 0.0,
            "z": 1200.0,
            "yaw": 2.0,
            "health": 0,
            "max_health": 880,
            "alive": False,
        })

        self.assertEqual((4.0, 0.0, 12.0), (
            mock.position.x, mock.position.y, mock.position.z,
        ))
        self.assertEqual((4.0, 0.0, 12.0), (
            model.position.x, model.position.y, model.position.z,
        ))

    def test_capture_uses_the_local_network_team(self):
        source = (ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py").read_text()

        self.assertNotIn("vehs_by_team[1].append(player)", source)
        self.assertIn("vehs_by_team[_player_team].append(player)", source)
        self.assertIn("send_authoritative_rules(player, g_base_capture)", source)
        self.assertIn("send_authoritative_result", source)
        self.assertIn("if not network_is_authority(player):", source)

    def test_initial_spawn_uses_the_actual_lan_team(self):
        source = (ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py").read_text()

        self.assertIn("_player_spawn_team = int(getattr(player, '_offhangar_team', 1) or 1)", source)
        self.assertIn("gp['teamSpawnPoints/team%d' % _player_spawn_team]", source)
        self.assertIn("get(_player_spawn_team, [])", source)
        self.assertIn("g_offline_bases.get(_player_spawn_team, [])", source)
        self.assertIn("send_local_hit(player", source)
        self.assertIn("LAN HE splash reported", source)
        forced_block = source[source.index("_forced_pos = getattr(player, '_forced_spawn_pos'"):]
        forced_block = forced_block[:900]
        self.assertIn("else:\n\t\t\t\t\t\t\t# Manual O/P/L", forced_block)

    def test_network_wreck_rebinds_the_marker_proxy_to_destroyed_model(self):
        source = (ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py").read_text()
        wreck = source[source.index("def _fire_wreck_swap"):]
        wreck = wreck[:7000]

        self.assertIn("_mv.position = Math.Vector3(_wpos)", wreck)
        self.assertIn("_mv.matrix.translation = _wpos", wreck)
        self.assertIn("_mv.model = _d_ch", wreck)
        self.assertIn("_mv._chassis_model = _d_ch", wreck)
        self.assertIn("update_remote_spotting(player, m_veh)", source)

    def test_queue_start_shortcut_keys_are_removed(self):
        account_hook = (ROOT / "scripts/client/gui/mods/mod_offhangar.py").read_text()
        global_hook = (ROOT / "scripts/client/gui/mods/offhangar/lan_settings.py").read_text()

        for source in (account_hook, global_hook):
            self.assertNotIn("KEY_F12", source)
            self.assertNotIn("KEY_0", source)
            self.assertNotIn("KEY_NUMPAD0", source)

        waiting_room = (
            ROOT / "scripts/client/gui/mods/offhangar/lan_waiting_room.py"
        ).read_text()
        self.assertIn("START BATTLE", waiting_room)
        self.assertIn("request_battle_start(_player, _selected_map)", waiting_room)


if __name__ == "__main__":
    unittest.main()
