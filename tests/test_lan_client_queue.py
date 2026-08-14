import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NETWORK_PATH = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"
VEHICLE_POSE_PATH = ROOT / "scripts/client/gui/mods/offhangar/vehicle_pose.py"
BOT_AI_DRIVER_PATH = ROOT / "scripts/client/gui/mods/offhangar/bot_ai_driver.py"


def load_network_module():
    bigworld = types.ModuleType("BigWorld")
    bigworld.callback = lambda delay, callback: None
    bigworld.time = lambda: 100.0
    bigworld.wg_collideSegment = lambda *args: None
    bigworld.Servo = lambda provider: ("servo", provider)
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

    pose_spec = importlib.util.spec_from_file_location(
        "gui.mods.offhangar.vehicle_pose", VEHICLE_POSE_PATH
    )
    pose_module = importlib.util.module_from_spec(pose_spec)
    sys.modules[pose_spec.name] = pose_module
    pose_spec.loader.exec_module(pose_module)

    driver_spec = importlib.util.spec_from_file_location(
        "gui.mods.offhangar.bot_ai_driver", BOT_AI_DRIVER_PATH
    )
    driver_module = importlib.util.module_from_spec(driver_spec)
    sys.modules[driver_spec.name] = driver_module
    driver_spec.loader.exec_module(driver_module)

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
        # A promoted authority must pass the native terrain-streaming gate
        # before network_battle opens its movement-ownership fence. Individual
        # tests override this callback to exercise waiting and failure paths.
        self._offhangar_prepare_native_authority_streaming = lambda: True

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

    def test_capture_reset_bridge_accepts_hull_or_module_damage(self):
        calls = []
        offline = sys.modules["gui.mods.offhangar.offline_battle"]
        previous = getattr(offline, "apply_network_capture_damage", None)
        offline.apply_network_capture_damage = lambda *args: calls.append(args)
        try:
            player = Player()
            target = types.SimpleNamespace(id=7)
            self.network._apply_capture_reset_event(
                player, target, {"kind": "hit", "damage": 0}, 3
            )
            self.network._apply_capture_reset_event(
                player, target,
                {"kind": "hit", "damage": 0, "critical": True,
                 "capture_reset": True},
                3,
            )
            self.network._apply_capture_reset_event(
                player, target,
                {"kind": "hit", "damage": 25, "capture_reset": True},
                3,
            )
        finally:
            if previous is None:
                del offline.apply_network_capture_damage
            else:
                offline.apply_network_capture_damage = previous

        self.assertEqual(2, len(calls))
        self.assertEqual((0, True), (calls[0][3], calls[0][4]))
        self.assertEqual((25, False), (calls[1][3], calls[1][4]))

    def test_bot_snapshot_due_check_skips_rejected_render_frames(self):
        client = self.network.LANClient(
            Player(), "127.0.0.1", 28782, "Alpha", "china:Ch01_Type59"
        )
        client._last_bot_state = self.network.time.time()

        self.assertFalse(client.bot_states_due())

    def test_authority_snapshot_reuses_one_server_coordinate_frame(self):
        calls = []
        sent = []
        player = Player()
        player._offhangar_network_is_authority = True
        player._offhangar_network_formation = lambda team, slot: (
            calls.append((team, slot)) or
            ((0.0, 0.0, 0.0) if team == 1 else (100.0, 0.0, 3.14))
        )
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True,
            phase="battle",
            bot_states_due=lambda: True,
            send_bot_states=lambda states: sent.extend(states) or True,
        )
        mocks = {}
        for index in range(3):
            mocks[index] = types.SimpleNamespace(
                id=index,
                _network_bot_id=index + 1,
                position=types.SimpleNamespace(x=float(index), y=0.0, z=10.0),
                yaw=0.0,
                _turret_yaw=0.1,
                _gun_pitch=0.0,
                _veh_velocity=0.0,
                _veh_turn_velocity=0.0,
                _network_bot_fire_seq=0,
                _network_bot_shell_index=0,
                health=100,
                isAlive=True,
                last_killer_id=None,
				is_tracked=(index == 0),
                is_engine_dead=False,
				_destroyed_devices=set(),
				typeDescriptor=None,
            )

        self.assertTrue(self.network.publish_authoritative_bots(player, mocks))
        self.assertEqual([(1, 0), (2, 0)], calls)
        self.assertEqual(3, len(sent))
        self.assertAlmostEqual(sent[0]["yaw"] + 0.1, sent[0]["aim_yaw"])
        self.assertTrue(sent[0]["mobility_disabled"])
        self.assertFalse(sent[1]["mobility_disabled"])
        self.assertGreater(sent[0]["mobility_repair_seconds"], 0.0)

    def test_mobility_handoff_holds_then_expires_without_self_echo(self):
        mock = types.SimpleNamespace(
            is_tracked=False, is_engine_dead=False,
            _destroyed_devices=set(), devices_hp={}, typeDescriptor=None,
        )

        self.assertTrue(self.network._apply_mobility_snapshot(mock, {
            "mobility_disabled": True, "mobility_repair_seconds": 2.0,
        }, True, 100.0))
        self.assertEqual(102.0, mock._network_mobility_carry_until)
        disabled, remaining = self.network._mobility_report(mock, 101.0)
        self.assertTrue(disabled)
        self.assertAlmostEqual(1.0, remaining)

        disabled, remaining = self.network._mobility_report(mock, 102.01)
        self.assertFalse(disabled)
        self.assertEqual(0.0, remaining)
        self.assertFalse(mock._network_mobility_disabled)

    def test_repeated_full_mobility_handoff_does_not_restart_repair_clock(self):
        mock = types.SimpleNamespace(
            is_tracked=False, is_engine_dead=False,
            _destroyed_devices=set(), devices_hp={}, typeDescriptor=None,
        )
        state = {"mobility_disabled": True, "mobility_repair_seconds": 5.0}

        self.network._apply_mobility_snapshot(mock, state, True, 100.0)
        self.network._apply_mobility_snapshot(mock, state, True, 102.0)
        self.assertEqual(105.0, mock._network_mobility_carry_until)

        self.network._apply_mobility_snapshot(mock, state, True, 106.0)
        self.assertEqual(105.0, mock._network_mobility_carry_until)
        disabled, remaining = self.network._mobility_report(mock, 106.0)
        self.assertFalse(disabled)
        self.assertEqual(0.0, remaining)

    def test_new_authority_tenure_clears_old_mobility_handoff_seed(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Bravo", "ussr:T-34"
        )
        client.player_id = 2
        client.bot_authority_id = 1
        player._offhangar_network_bot_manifest = [{"id": 16}]
        mock = types.SimpleNamespace(
            _network_mobility_handoff_seeded=True,
            _network_mobility_carry_until=105.0,
            _network_mobility_disabled=True,
            _network_mobility_repair_seconds=5.0,
        )
        offline = sys.modules["gui.mods.offhangar.offline_battle"]
        previous = getattr(offline, "G_MOCK_VEHICLES", None)
        offline.G_MOCK_VEHICLES = {1016: mock}
        try:
            self.assertTrue(client._set_authority(2))
        finally:
            if previous is None:
                del offline.G_MOCK_VEHICLES
            else:
                offline.G_MOCK_VEHICLES = previous

        self.assertFalse(hasattr(mock, "_network_mobility_handoff_seeded"))
        self.assertFalse(hasattr(mock, "_network_mobility_carry_until"))

    def test_worker_sends_hello_before_exposing_connected_socket(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "china:Ch01_Type59"
        )
        sent = []

        class FakeSocket:
            def setsockopt(self, *args):
                pass

            def settimeout(self, timeout):
                pass

            def connect(self, address):
                pass

            def sendall(self, payload):
                self_outer.assertFalse(client.connected)
                sent.append(self_outer.network.json.loads(payload.decode("utf-8")))

            def recv(self, size):
                return b""

            def close(self):
                pass

        self_outer = self
        fake = FakeSocket()
        original_socket = self.network.socket.socket
        self.network.socket.socket = lambda *args: fake
        client.running = True
        try:
            client._worker()
        finally:
            self.network.socket.socket = original_socket

        self.assertEqual("hello", sent[0]["type"])
        self.assertEqual(self.network.PROTOCOL_VERSION, sent[0]["protocol"])
        self.assertEqual(self.network.CLIENT_BUILD, sent[0]["client_build"])
        self.assertEqual("china:Ch01_Type59", sent[0]["vehicle"])

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

    def test_observation_due_avoids_rebuilding_throttled_payloads(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        old_time = self.network.time.time
        self.network.time.time = lambda: 10.0
        try:
            client._last_bot_observation = 9.70
            self.assertFalse(client.bot_observation_due())
            client._last_bot_observation = 9.50
            self.assertTrue(client.bot_observation_due())
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
            "shootable_by_bot_ids": [16, 16, -1, "17", "bad"],
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
            "graph": {"source": "baked", "cell_mm": 4000, "nodes": 16808,
                      "ignored": 999},
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
                       "traffic_wait": 4, "water_guard": 2,
                       "full": 12, "cruise": 10,
                       "speed_pct": 341, "slow": 3, "ignored": 999},
            "safety": {"water_guard_total": 100005, "water_guard_active": 2,
                       "edge_guard_total": 17, "edge_guard_active": 1,
                       "veto_water": 4, "veto_terrain": 3,
                       "veto_obstacle": 2, "veto_error": 1,
                       "ignored": 999},
        }
        self.assertTrue(self.network.publish_bot_observation(
            player, [contact] * 65, reports, raw_navigation
        ))
        self.assertEqual([16, 17], captured[0][0][0]["shootable_by_bot_ids"])
        contacts, affordances, navigation = captured[0]
        self.assertEqual(64, len(contacts))
        self.assertEqual(16, len(affordances))
        self.assertEqual(12, len(affordances[0]["candidates"]))
        self.assertNotIn("not_json", affordances[0]["candidates"][0])
        self.assertEqual(0, navigation["total"]["reactive"])
        self.assertEqual(3, navigation["total"]["safe_local"])
        self.assertNotIn("ignored", navigation["total"])
        self.assertEqual(4, navigation["recovered"])
        self.assertEqual("baked", navigation["graph"]["source"])
        self.assertEqual(16808, navigation["graph"]["nodes"])
        self.assertNotIn("ignored", navigation["graph"])
        self.assertEqual(13, navigation["search"]["pending"])
        self.assertEqual(12345, navigation["search"]["oldest_ms"])
        self.assertNotIn("ignored", navigation["search"])
        self.assertEqual(15, navigation["aim"]["targeted"])
        self.assertEqual(11, navigation["aim"]["traversing"])
        self.assertNotIn("ignored", navigation["aim"])
        self.assertEqual(9, navigation["driver"]["moving"])
        self.assertEqual(6, navigation["driver"]["blocked"])
        self.assertEqual(4, navigation["driver"]["traffic_wait"])
        self.assertEqual(2, navigation["driver"]["water_guard"])
        self.assertEqual(12, navigation["driver"]["full"])
        self.assertEqual(10, navigation["driver"]["cruise"])
        self.assertEqual(200, navigation["driver"]["speed_pct"])
        self.assertEqual(3, navigation["driver"]["slow"])
        self.assertNotIn("ignored", navigation["driver"])
        self.assertEqual(100000, navigation["safety"]["water_guard_total"])
        self.assertEqual(17, navigation["safety"]["edge_guard_total"])
        self.assertEqual(4, navigation["safety"]["veto_water"])
        self.assertNotIn("ignored", navigation["safety"])

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

    def test_outbound_queue_coalesces_state_without_reordering_reliable_events(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")
        client.connected = True
        client.sock = types.SimpleNamespace(sendall=lambda payload: None)

        self.assertTrue(client._send({"type": "input", "x": 1}, "input"))
        self.assertTrue(client._send({"type": "hit_report", "shot_seq": 2}))
        self.assertTrue(client._send({"type": "input", "x": 3}, "input"))

        self.assertEqual("hit_report", client._dequeue_outbound()["type"])
        self.assertEqual(3, client._dequeue_outbound()["x"])
        self.assertIsNone(client._dequeue_outbound())

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

    def test_world_pose_basis_is_computed_once_per_battle_formation(self):
        calls = []
        player = Player()

        def formation(team, slot):
            calls.append((team, slot))
            return (0.0, 0.0, 0.0) if team == 1 else (100.0, 0.0, 3.14)

        player._offhangar_network_formation = formation
        state = {"world_pose": True, "x": 4.0, "y": 0.0, "z": 20.0}

        self.network._world_from_server(player, state)
        self.network._world_from_server(player, state)
        self.network._world_yaw_from_server(
            player, {"world_pose": True, "yaw": 0.5}
        )

        self.assertEqual([(1, 0), (2, 0)], calls)

    def test_spawn_yaw_uses_each_formation_slot_until_world_pose_arrives(self):
        player = Player()
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, slot * 10.0, 0.35 + slot * 0.1)
            if team == 1
            else (100.0, slot * 10.0, -2.4 - slot * 0.1)
        )

        self.assertAlmostEqual(
            0.55,
            self.network._world_yaw_from_server(
                player, {"team": 1, "slot": 2, "yaw": 0.0}
            ),
        )
        self.assertAlmostEqual(
            -2.25,
            self.network._world_yaw_from_server(
                player,
                {"team": 2, "slot": 1, "yaw": math.pi + 0.25},
            ),
        )
        self.assertAlmostEqual(
            math.pi / 2.0 + 0.5,
            self.network._world_yaw_from_server(
                player,
                {
                    "world_pose": True,
                    "team": 1,
                    "slot": 2,
                    "yaw": 0.5,
                },
            ),
        )

    def test_mock_indexes_refresh_when_a_staged_bot_is_added(self):
        offline = sys.modules["gui.mods.offhangar.offline_battle"]
        offline.g_offh_battle_gen = 42
        first = types.SimpleNamespace(
            _network_bot_id=1, _network_server_id=None
        )
        second = types.SimpleNamespace(
            _network_bot_id=2, _network_server_id=None
        )
        offline.G_MOCK_VEHICLES = {1001: first}
        self.network.__dict__.pop("_g_network_mock_indexes", None)

        self.assertIs(first, self.network._find_bot(1))
        offline.G_MOCK_VEHICLES[1002] = second
        self.assertIs(second, self.network._find_bot(2))

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

    def test_bot_human_event_before_snapshot_does_not_double_apply_damage(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_is_authority = True
        player._offhangar_network_server_health = 880
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        player._offhangar_apply_network_rules_state = lambda rules: True
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, isAlive=True,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local, 1016: bot,
        }

        self.network._handle_events(player, [{
            "kind": "bot_human_hit", "attacker_bot": 16, "target": 1,
            "shot_seq": 4, "shot_result": 2, "damage": 125,
            "health": 755, "dead": False,
        }])
        self.network._apply_snapshot(player, {"players": [{
            "id": 1, "health": 755, "max_health": 880, "alive": True,
        }]})

        self.assertEqual(755, local.health)
        self.assertEqual(755, player._offhangar_network_server_health)

    def test_bot_human_snapshot_before_event_does_not_double_apply_damage(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_is_authority = True
        player._offhangar_network_server_health = 880
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, isAlive=True,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local, 1016: bot,
        }

        self.network._apply_snapshot(player, {"players": [{
            "id": 1, "health": 755, "max_health": 880, "alive": True,
        }]})
        self.network._handle_events(player, [{
            "kind": "bot_human_hit", "attacker_bot": 16, "target": 1,
            "shot_seq": 4, "shot_result": 2, "damage": 125,
            "health": 755, "dead": False,
        }])

        self.assertEqual(755, local.health)
        self.assertEqual(755, player._offhangar_network_server_health)

    def test_cumulative_bot_events_after_newer_snapshot_do_not_rewind_health(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_is_authority = True
        player._offhangar_network_server_health = 880
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, isAlive=True,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local, 1016: bot,
        }
        events = [
            {
                "kind": "bot_human_hit", "attacker_bot": 16,
                "target": 1, "shot_seq": 4, "shot_result": 2,
                "damage": 125, "health": 755, "dead": False,
            },
            {
                "kind": "bot_human_hit", "attacker_bot": 16,
                "target": 1, "shot_seq": 5, "shot_result": 2,
                "damage": 125, "health": 630, "dead": False,
            },
        ]

        self.network._apply_snapshot(player, {"players": [{
            "id": 1, "health": 630, "max_health": 880, "alive": True,
        }]})
        self.network._handle_events(player, events)

        self.assertEqual(630, local.health)
        self.assertEqual(630, player._offhangar_network_server_health)

    def test_stale_health_events_cannot_rewind_lethal_snapshot(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_server_health = 880
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local,
        }

        self.network._apply_snapshot(player, {"players": [{
            "id": 1, "health": 0, "max_health": 880, "alive": False,
        }]})
        self.network._handle_events(player, [
            {
                "kind": "health", "target": 1, "damage": 100,
                "health": 100, "dead": False,
            },
            {
                "kind": "hit", "attacker": 2, "target": 1,
                "damage": 100, "health": 100, "dead": False,
            },
        ])

        self.assertEqual(0, local.health)
        self.assertFalse(local.publicInfo["isAlive"])
        self.assertEqual(0, player._offhangar_network_server_health)

    def test_local_damage_ack_event_prevents_snapshot_double_application(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_health_round_id = 7
        player._offhangar_network_server_health = 880
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=800, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local,
        }

        self.network._handle_events(player, [{
            "kind": "health", "target": 1, "damage": 80,
            "health": 800, "dead": False,
            "source": "client_simulation",
        }])
        self.network._apply_snapshot(player, {
            "round_id": 7,
            "players": [{
                "id": 1, "health": 800, "max_health": 880,
                "alive": True,
            }],
        })

        self.assertEqual(800, local.health)
        self.assertEqual(800, player._offhangar_network_server_health)

    def test_new_round_resets_monotonic_local_health_baseline(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_health_round_id = 1
        player._offhangar_network_server_health = 0
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local,
        }

        self.network._apply_snapshot(player, {
            "round_id": 2,
            "players": [{
                "id": 1, "health": 880, "max_health": 880,
                "alive": True,
            }],
        })
        self.network._apply_snapshot(player, {
            "round_id": 2,
            "players": [{
                "id": 1, "health": 755, "max_health": 880,
                "alive": True,
            }],
        })

        self.assertEqual(755, local.health)
        self.assertEqual(755, player._offhangar_network_server_health)
        self.assertEqual(2, player._offhangar_network_health_round_id)

    def test_stale_round_snapshot_cannot_damage_current_round_player(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_health_round_id = 2
        player._offhangar_network_server_health = 880
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local,
        }

        self.network._apply_snapshot(player, {
            "round_id": 1,
            "players": [{
                "id": 1, "health": 0, "max_health": 880,
                "alive": False,
            }],
        })

        self.assertEqual(880, local.health)
        self.assertTrue(local.publicInfo["isAlive"])
        self.assertEqual(880, player._offhangar_network_server_health)
        self.assertEqual(2, player._offhangar_network_health_round_id)

    def test_stale_round_messages_are_rejected_before_any_client_side_effect(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_health_round_id = 2
        player._offhangar_network_server_health = 880
        player._offhangar_network_authority_id = 77
        player._offhangar_network_snapshot = {"round_id": 2, "server_tick": 10}
        player._offhangar_network_events = [{"kind": "current_round"}]
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local,
        }
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        client.round_id = 2
        client.battle_started = True
        client._last_snapshot = 50.0
        client.bot_authority_id = 77
        client.bot_order_revision = 88
        client.bot_orders = {16: {"id": 16, "target_id": 2}}
        client.combat_duration = 321.0

        client._handle_message({
            "type": "battle_start", "round_id": 1,
            "bot_order_revision": 99,
            "bot_orders": [{"id": 16, "target_id": 999}],
            "timing": {"phase": "finished", "duration_ms": 1000},
        })
        client._handle_message({
            "type": "snapshot", "round_id": 1, "server_tick": 999,
            "bot_authority_id": 3, "bot_order_revision": 99,
            "bot_orders": [{"id": 16, "target_id": 999}],
            "timing": {"phase": "finished", "duration_ms": 1000},
            "players": [{
                "id": 1, "health": 0, "max_health": 880,
                "alive": False,
            }],
        })
        client._handle_message({
            "type": "events", "round_id": 1, "server_tick": 999,
            "events": [{
                "kind": "health", "target": 1, "damage": 880,
                "health": 0, "dead": True,
            }],
        })

        self.assertEqual(50.0, client._last_snapshot)
        self.assertEqual(77, client.bot_authority_id)
        self.assertEqual(88, client.bot_order_revision)
        self.assertEqual(2, client.bot_orders[16]["target_id"])
        self.assertEqual(321.0, client.combat_duration)
        self.assertEqual(2, player._offhangar_network_snapshot["round_id"])
        self.assertEqual([{"kind": "current_round"}],
                         player._offhangar_network_events)
        self.assertEqual(880, local.health)
        self.assertEqual(880, player._offhangar_network_server_health)

    def test_local_input_reports_only_unacknowledged_local_damage(self):
        player = Player()
        player._offhangar_network_server_health = 700
        player.playerVehicleID = 1
        mock = types.SimpleNamespace(health=700)
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: mock,
        }
        sent = []
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True,
            max_health=880,
            send_input=lambda *args, **kwargs: sent.append((args, kwargs)),
        )

        self.network.send_local_input(player, 0.0, 0.0, 0.0, 0.0)
        self.assertIsNone(sent[-1][1]["reported_health"])

        mock.health = 650
        self.network.send_local_input(player, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(650, sent[-1][1]["reported_health"])

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
            "killer_kind": "human",
            "killer_id": 1,
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "yaw": 0.0,
        }

        self.network._apply_remote_state(player, state)
        self.network._apply_remote_state(player, state)

        self.assertEqual(0, mock.health)
        self.assertEqual(1, len(deaths))
        self.assertEqual(1, deaths[0][1])

    def test_unknown_snapshot_death_waits_for_following_killer_event(self):
        player = Player()
        deaths = []
        player.arena = types.SimpleNamespace(
            onVehicleKilled=lambda *args: deaths.append(args)
        )
        mock = types.SimpleNamespace(
            id=1001, health=100, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        callbacks = []
        bigworld = sys.modules["BigWorld"]
        original_callback = bigworld.callback
        bigworld.callback = lambda delay, callback: callbacks.append(callback)
        try:
            self.network._push_mock_health(
                player, mock, 0, 880, False, -1, False
            )
            self.assertEqual([], deaths)
            self.network._push_mock_health(
                player, mock, 0, 880, False, 1003, False
            )
            self.assertEqual(1, len(deaths))
            self.assertEqual(1003, deaths[0][1])
            callbacks[0]()
        finally:
            bigworld.callback = original_callback

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
        model = types.SimpleNamespace(
            position=vector3(0, 100, 0), yaw=0.0, motors=[]
        )
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
            _offh_native_model_root_ready=True,
            _servo_added=False,
            _pose_servo=None,
        )
        model.addMotor = lambda motor: model.motors.append(motor)
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
            motors=[],
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, _network_shared_bot=True,
            _bot_team=2, health=500, maxHealth=500, isAlive=True,
            publicInfo={"team": 2, "isAlive": True},
            position=vector3(0, 0, 0), yaw=0.0, pitch=0.0, roll=0.0,
            matrix=Matrix(), _t_mat=Matrix(), _g_mat=Matrix(),
            model=model, _chassis_model=model, bw_entity=None,
            marker=None, proxy=None,
            _offh_native_model_root_ready=True,
            _servo_added=False, _pose_servo=None,
        )
        model.addMotor = lambda motor: model.motors.append(motor)
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {1016: bot}
        state = {
            "id": 16, "team": 2, "slot": 0, "world_pose": True,
            "x": 5.0, "y": 2.0, "z": 20.0, "yaw": 0.5,
            "aim_yaw": 0.75, "gun_pitch": -0.1, "fire_seq": 3,
            "shell_index": 1, "health": 500, "max_health": 500, "alive": True,
            "speed": 6.0, "turn_velocity": 0.2,
            "nav_source": "server_baked", "nav_order_revision": 12,
            "nav_x": 7.0, "nav_y": 3.0, "nav_z": 24.0,
        }

        self.network._apply_bot_state(player, state, sample_time=123.5)
        self.network._apply_bot_state(player, state, sample_time=123.5)

        self.assertEqual((5.0, 2.0, 20.0), (
            model.position.x, model.position.y, model.position.z,
        ))
        self.assertEqual((0.25, 0, 0), bot._t_mat.rotation)
        self.assertEqual((0, -0.1, 0), bot._g_mat.rotation)
        self.assertEqual(3, bot._network_bot_fire_seq)
        self.assertEqual(1, bot._network_bot_shell_index)
        self.assertEqual(1, len(self.network._test_shot_presentations))
        self.assertAlmostEqual(math.sin(0.5) * 6.0,
                               bot._network_target_velocity[0], places=5)
        self.assertEqual((7.0, 3.0, 24.0), bot._network_navigation_target)
        self.assertEqual("server_baked", bot._network_navigation_source)
        self.assertEqual(12, bot._network_navigation_revision)
        self.assertEqual(123.5, bot._network_navigation_time)
        self.assertEqual(123.5, player._offhangar_network_server_navigation_at)

        # Promotion applies one final canonical relay snapshot before local
        # simulation starts, including the motion state needed for continuity.
        player._offhangar_network_is_authority = True
        player._offhangar_network_authority_handoff_pending = True
        player._offhangar_network_bot_manifest = [{"id": 16}]
        promoted = dict(state, x=9.0, speed=7.5, turn_velocity=-0.3)
        player._offhangar_apply_network_rules_state = lambda rules: True
        self.network._apply_snapshot(player, {
            "bots": [promoted], "players": [], "rules": {"bases": {}},
        })
        self.assertFalse(player._offhangar_network_authority_handoff_pending)
        self.assertEqual(9.0, bot.position.x)
        self.assertEqual(7.5, bot._veh_velocity)
        self.assertEqual(-0.3, bot._veh_turn_velocity)

    def test_compact_authority_snapshot_cannot_clear_full_pose_handoff(self):
        player = Player()
        player._offhangar_network_authority_handoff_pending = True
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = [{"id": 16}]
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        player._offhangar_apply_network_rules_state = lambda rules: True
        applied = []
        original_apply = self.network._apply_bot_state
        original_indexes = self.network._network_mock_indexes
        self.network._apply_bot_state = lambda *args: applied.append(args) or True
        self.network._network_mock_indexes = lambda: ({}, {16: object()})
        try:
            self.network._apply_snapshot(player, {
                "bot_snapshot_mode": "authority",
                "players": [],
                "bots": [{
                    "id": 16, "health": 500, "alive": True,
                    "nav_source": "server_baked", "nav_order_revision": 7,
                    "nav_x": 1.0, "nav_y": 2.0, "nav_z": 3.0,
                }],
            })
            self.assertTrue(
                player._offhangar_network_authority_handoff_pending)
            self.assertFalse(applied[-1][2])

            self.network._apply_snapshot(player, {
                "bot_snapshot_mode": "full",
                "players": [],
                "rules": {"bases": {}},
                "bots": [{
                    "id": 16, "world_pose": True,
                    "x": 4.0, "y": 2.0, "z": 8.0,
                    "yaw": 0.25, "aim_yaw": 0.5,
                    "gun_pitch": -0.1, "speed": 7.0,
                    "turn_velocity": 0.2,
                    "health": 500, "alive": True,
                    "nav_source": "server_baked", "nav_order_revision": 7,
                    "nav_x": 1.0, "nav_y": 2.0, "nav_z": 3.0,
                }],
            })
        finally:
            self.network._apply_bot_state = original_apply
            self.network._network_mock_indexes = original_indexes

        self.assertFalse(player._offhangar_network_authority_handoff_pending)
        self.assertTrue(applied[-1][2])

    def test_promoted_authority_applies_rules_before_releasing_handoff(self):
        player = Player()
        player._offhangar_network_authority_handoff_pending = True
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = []
        player._offhangar_network_formation = (
            lambda team, slot: (float(slot), float(team * 100), 0.0)
        )
        sent = []
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle", send_rules=lambda rules: sent.append(rules) or True
        )
        local_bases = {1: {"points": 49}, 2: {"points": 0}}
        callback_authority = []

        def apply_rules(rules):
            callback_authority.append(self.network.network_is_authority(player))
            if callback_authority[-1]:
                return
            for team in (1, 2):
                local_bases[team] = dict(rules["bases"][str(team)])
            return True

        player._offhangar_apply_network_rules_state = apply_rules
        canonical = {"bases": {
            "1": {"points": 50, "stopped": False,
                  "contributors": {"human:2": 50},
                  "active_contributors": ["human:2"],
                  "invaders": 1, "cursor": 1},
            "2": {"points": 0, "stopped": False,
                  "contributors": {}, "active_contributors": [],
                  "invaders": 0, "cursor": 0},
        }}

        self.network._apply_snapshot(player, {
            "bot_snapshot_mode": "full", "players": [], "bots": [],
            "rules": canonical,
        })

        self.assertEqual([False], callback_authority)
        self.assertFalse(player._offhangar_network_authority_handoff_pending)
        self.assertEqual(50, local_bases[1]["points"])
        self.assertTrue(self.network.send_authoritative_rules(player, local_bases))
        self.assertEqual(50, sent[-1]["bases"]["1"]["points"])

    def test_promoted_authority_streaming_gate_runs_after_full_state_apply(self):
        player = Player()
        player._offhangar_network_authority_handoff_pending = True
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = [{"id": 16}]
        events = []

        def apply_rules(_rules):
            events.append((
                "rules",
                player._offhangar_network_authority_handoff_pending,
            ))
            return True

        def prepare_streaming():
            events.append((
                "streaming",
                player._offhangar_network_authority_handoff_pending,
            ))
            return True

        player._offhangar_apply_network_rules_state = apply_rules
        player._offhangar_prepare_native_authority_streaming = prepare_streaming
        original_apply = self.network._apply_bot_state
        original_indexes = self.network._network_mock_indexes
        self.network._network_mock_indexes = lambda: ({}, {16: object()})

        def apply_bot(*_args):
            events.append((
                "bot",
                player._offhangar_network_authority_handoff_pending,
            ))
            return True

        self.network._apply_bot_state = apply_bot
        try:
            self.network._apply_snapshot(player, {
                "bot_snapshot_mode": "full",
                "players": [],
                "bots": [{"id": 16, "world_pose": True}],
                "rules": {"bases": {}},
            })
        finally:
            self.network._apply_bot_state = original_apply
            self.network._network_mock_indexes = original_indexes

        self.assertEqual([
            ("rules", True),
            ("bot", True),
            ("streaming", True),
        ], events)
        self.assertFalse(player._offhangar_network_authority_handoff_pending)

    def test_promoted_authority_streaming_wait_or_error_keeps_handoff_fence(self):
        callbacks = (
            lambda: False,
            lambda: (_ for _ in ()).throw(RuntimeError("streaming failed")),
        )
        for callback in callbacks:
            with self.subTest(callback=callback):
                player = Player()
                player._offhangar_network_authority_handoff_pending = True
                player._offhangar_network_is_authority = True
                player._offhangar_network_bot_manifest = []
                player._offhangar_apply_network_rules_state = lambda _rules: True
                player._offhangar_prepare_native_authority_streaming = callback

                self.network._apply_snapshot(player, {
                    "bot_snapshot_mode": "full",
                    "players": [],
                    "bots": [],
                    "rules": {"bases": {}},
                })

                self.assertTrue(
                    player._offhangar_network_authority_handoff_pending
                )
                self.assertFalse(self.network.network_is_authority(player))

    def test_steady_authority_snapshot_does_not_run_promotion_streaming_gate(self):
        player = Player()
        player._offhangar_network_authority_handoff_pending = False
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = []
        player._offhangar_network_client = types.SimpleNamespace(
            running=True, connected=True, ready=True, phase="battle"
        )
        player._offhangar_prepare_native_authority_streaming = lambda: (
            _ for _ in ()
        ).throw(AssertionError("steady snapshot must not run promotion gate"))

        self.network._apply_snapshot(player, {
            "bot_snapshot_mode": "authority",
            "players": [],
            "bots": [],
        })

        self.assertTrue(self.network.network_is_authority(player))

    def test_promoted_authority_keeps_handoff_fence_when_rules_apply_fails(self):
        canonical = {"bases": {"1": {"points": 50}, "2": {"points": 0}}}
        for callback in (
                lambda rules: False,
                lambda rules: (_ for _ in ()).throw(RuntimeError("apply failed"))):
            player = Player()
            player._offhangar_network_authority_handoff_pending = True
            player._offhangar_network_is_authority = True
            player._offhangar_network_bot_manifest = []
            player._offhangar_apply_network_rules_state = callback

            self.network._apply_snapshot(player, {
                "bot_snapshot_mode": "full", "players": [], "bots": [],
                "rules": canonical,
            })

            self.assertTrue(
                player._offhangar_network_authority_handoff_pending)
            self.assertFalse(self.network.network_is_authority(player))

    def test_partial_full_snapshot_cannot_clear_authority_handoff(self):
        player = Player()
        player._offhangar_network_authority_handoff_pending = True
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = [{"id": 16}, {"id": 17}]
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        original_apply = self.network._apply_bot_state
        original_indexes = self.network._network_mock_indexes
        self.network._apply_bot_state = lambda *args: True
        self.network._network_mock_indexes = lambda: (
            {}, {16: object(), 17: object()}
        )
        try:
            self.network._apply_snapshot(player, {
                "bot_snapshot_mode": "full", "players": [],
                "bots": [{"id": 16, "world_pose": True}],
            })
        finally:
            self.network._apply_bot_state = original_apply
            self.network._network_mock_indexes = original_indexes

        self.assertTrue(player._offhangar_network_authority_handoff_pending)

    def test_missing_mock_cannot_clear_authority_handoff(self):
        player = Player()
        player._offhangar_network_authority_handoff_pending = True
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = [{"id": 16}]
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        original_indexes = self.network._network_mock_indexes
        self.network._network_mock_indexes = lambda: ({}, {})
        try:
            self.network._apply_snapshot(player, {
                "bot_snapshot_mode": "full", "players": [],
                "bots": [{"id": 16, "world_pose": True}],
            })
        finally:
            self.network._network_mock_indexes = original_indexes

        self.assertTrue(player._offhangar_network_authority_handoff_pending)

    def test_full_handoff_requires_world_conversion_and_pose_commit_success(self):
        class Matrix:
            def setRotateYPR(self, rotation):
                self.rotation = rotation

        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_authority_handoff_pending = True
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = [{"id": 16}]
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        player._offhangar_apply_network_rules_state = lambda rules: True
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        pose_servo = object()
        model = types.SimpleNamespace(
            position=vector3(0, 0, 0), yaw=0.0,
            visible=True, visibleAttachments=True,
            motors=[pose_servo],
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, _network_shared_bot=True,
            _bot_team=1, health=500, maxHealth=500, isAlive=True,
            publicInfo={"team": 1, "isAlive": True},
            position=vector3(0, 0, 0), yaw=0.0, pitch=0.0, roll=0.0,
            matrix=Matrix(), _t_mat=Matrix(), _g_mat=Matrix(),
            model=model, _chassis_model=model, bw_entity=None,
            marker=None, proxy=None, _servo_added=True,
            _pose_servo=pose_servo,
            _offh_native_model_root_ready=True,
        )
        state = {
            "id": 16, "world_pose": True,
            "x": 4.0, "y": 2.0, "z": 8.0,
            "yaw": 0.25, "aim_yaw": 0.5, "gun_pitch": -0.1,
            "speed": 7.0, "turn_velocity": 0.2,
            "health": 500, "max_health": 500, "alive": True,
        }
        original_indexes = self.network._network_mock_indexes
        original_world = self.network._world_from_server
        original_commit = self.network.vehicle_pose.commit_pose
        self.network._network_mock_indexes = lambda: ({}, {16: bot})
        outcomes = [
            (lambda unused_player, unused_state: None),
            (lambda *args, **kwargs: False),
            (lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("commit refused")
            )),
            (lambda *args, **kwargs: True),
        ]
        try:
            self.network._world_from_server = outcomes[0]
            self.network._apply_snapshot(player, {
                "bot_snapshot_mode": "full", "players": [], "bots": [state],
                "rules": {"bases": {}},
            })
            self.assertTrue(
                player._offhangar_network_authority_handoff_pending
            )

            self.network._world_from_server = original_world
            for commit in outcomes[1:]:
                self.network.vehicle_pose.commit_pose = commit
                self.network._apply_snapshot(player, {
                    "bot_snapshot_mode": "full",
                    "players": [],
                    "bots": [state],
                    "rules": {"bases": {}},
                })
                if commit is outcomes[-1]:
                    self.assertFalse(
                        player._offhangar_network_authority_handoff_pending
                    )
                else:
                    self.assertTrue(
                        player._offhangar_network_authority_handoff_pending
                    )
        finally:
            self.network._network_mock_indexes = original_indexes
            self.network._world_from_server = original_world
            self.network.vehicle_pose.commit_pose = original_commit

    def test_authority_does_not_publish_bot_state_before_full_handoff(self):
        player = Player()
        player._offhangar_network_is_authority = True
        player._offhangar_network_authority_handoff_pending = True
        client = types.SimpleNamespace(
            ready=True,
            phase="battle",
            bot_states_due=lambda: (_ for _ in ()).throw(
                AssertionError("handoff must stop before state construction")),
        )
        player._offhangar_network_client = client

        self.assertFalse(
            self.network.publish_authoritative_bots(player, {}))

    def test_only_a_promoted_client_waits_for_full_authority_handoff(self):
        initial_player = Player()
        initial_client = self.network.LANClient(
            initial_player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        initial_client.player_id = 1

        initial_client._set_authority(1)

        self.assertTrue(initial_player._offhangar_network_is_authority)
        self.assertFalse(
            initial_player._offhangar_network_authority_handoff_pending
        )

        promoted_player = Player()
        promoted_client = self.network.LANClient(
            promoted_player, "127.0.0.1", 28782, "Bravo", "ussr:T-34"
        )
        promoted_client.player_id = 2
        promoted_client._set_authority(1)
        promoted_player._offhangar_network_bot_manifest = [{"id": 16}]

        promoted_client._set_authority(2)

        self.assertTrue(promoted_player._offhangar_network_is_authority)
        self.assertTrue(
            promoted_player._offhangar_network_authority_handoff_pending
        )

    def test_repeated_authority_snapshot_cannot_bypass_failed_handoff(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Bravo", "ussr:T-34"
        )
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = "battle"
        client.round_id = 7
        client.player_id = 2
        client.bot_authority_id = 1
        player._offhangar_network_client = client
        player._offhangar_network_is_authority = False
        player._offhangar_network_bot_manifest = [{"id": 16}]
        snapshot = {
            "type": "snapshot",
            "round_id": 7,
            "bot_authority_id": 2,
            "bot_snapshot_mode": "full",
            "players": [],
            "bots": [{
                "id": 16, "health": 500,
                "max_health": 500, "alive": True,
            }],
        }

        # No local mock is mapped, so neither complete snapshot can satisfy the
        # handoff. The repeated authority id must not open the ownership fence.
        client._handle_message(snapshot)
        self.assertTrue(
            player._offhangar_network_authority_handoff_pending
        )
        self.assertFalse(self.network.network_is_authority(player))

        client._handle_message(snapshot)
        self.assertTrue(
            player._offhangar_network_authority_handoff_pending
        )
        self.assertFalse(self.network.network_is_authority(player))

    def test_failover_before_manifest_can_publish_after_empty_full_snapshot(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Bravo", "ussr:T-34"
        )
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = "battle"
        client.round_id = 7
        client.player_id = 2
        client.bot_authority_id = 1
        player._offhangar_network_client = client
        player._offhangar_network_is_authority = False
        player._offhangar_network_bot_manifest = []
        player._offhangar_network_formation = (
            lambda team, slot: (float(slot), float(team * 100), 0.0)
        )

        client._handle_message({
            "type": "snapshot",
            "bot_authority_id": 2,
            "bot_snapshot_mode": "full",
            "players": [],
            "bots": [],
        })

        self.assertFalse(
            player._offhangar_network_authority_handoff_pending
        )
        self.assertTrue(self.network.network_is_authority(player))

        published = []
        sent = []
        client.send_bot_manifest = lambda manifest, map_frame=None, manifest_nonce=None, round_id=None: (
            sent.append((manifest, map_frame, manifest_nonce, round_id)) or True
        )
        jobs = [
            (16, 1, 0, "ussr:T-34", "Bot", 1000, 0.0, 0.0, 0.0, 0.0),
        ]
        self.assertFalse(self.network.publish_bot_manifest(player, jobs))
        self.assertEqual([], player._offhangar_network_bot_manifest)
        nonce = sent[-1][2]
        client._handle_message({
            "type": "bot_manifest_result", "round_id": 7,
            "manifest_nonce": nonce, "accepted": True, "bot_ids": [16],
            "bots": sent[-1][0],
        })
        self.assertTrue(self.network.publish_bot_manifest(player, jobs))
        published.extend(player._offhangar_network_bot_manifest)
        self.assertEqual([16], [entry["id"] for entry in published])

    def test_manifest_ack_rejects_wrong_round_nonce_and_id_set(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        client.running = client.connected = client.ready = True
        client.phase = "battle"
        client.player_id = 1
        client.round_id = 7
        client.bot_authority_id = 1
        player._offhangar_network_client = client
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = []
        player._offhangar_network_formation = lambda *unused: (0.0, 0.0, 0.0)
        client.send_bot_manifest = lambda *unused, **unused_kw: True
        jobs = [(16, 1, 0, "ussr:T-34", "Bot", 1000,
                 0.0, 0.0, 0.0, 0.0)]

        self.assertFalse(self.network.publish_bot_manifest(player, jobs))
        pending = player._offhangar_network_bot_manifest_pending
        for message in (
            {"round_id": 6, "manifest_nonce": pending["nonce"],
             "accepted": True, "bot_ids": [16], "bots": pending["manifest"]},
            {"round_id": 7, "manifest_nonce": "wrong",
             "accepted": True, "bot_ids": [16], "bots": pending["manifest"]},
            {"round_id": 7, "manifest_nonce": pending["nonce"],
             "accepted": True, "bot_ids": [17], "bots": pending["manifest"]},
        ):
            message["type"] = "bot_manifest_result"
            client._handle_message(message)
            self.assertEqual([], player._offhangar_network_bot_manifest)
            self.assertFalse(self.network.publish_bot_manifest(player, jobs))

        client._handle_message({
            "type": "bot_manifest_result", "round_id": 7,
            "manifest_nonce": pending["nonce"], "accepted": False,
            "bot_ids": [], "code": "rejected",
        })
        self.assertEqual("rejected", pending["state"])
        self.assertFalse(self.network.publish_bot_manifest(player, jobs))

    def test_manifest_ack_is_ignored_after_round_or_authority_changes(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        client.running = client.connected = client.ready = True
        client.phase = "battle"
        client.player_id = 1
        client.round_id = 7
        client.bot_authority_id = 1
        player._offhangar_network_client = client
        player._offhangar_network_is_authority = True
        player._offhangar_network_bot_manifest = []
        player._offhangar_network_formation = lambda *unused: (0.0, 0.0, 0.0)
        client.send_bot_manifest = lambda *unused, **unused_kw: True
        jobs = [(16, 1, 0, "ussr:T-34", "Bot", 1000,
                 0.0, 0.0, 0.0, 0.0)]

        self.assertFalse(self.network.publish_bot_manifest(player, jobs))
        pending = player._offhangar_network_bot_manifest_pending
        result = {
            "type": "bot_manifest_result", "round_id": 7,
            "manifest_nonce": pending["nonce"], "accepted": True,
            "bot_ids": [16], "bots": pending["manifest"],
        }

        client.round_id = 8
        client._handle_message(result)
        self.assertEqual([], player._offhangar_network_bot_manifest)
        self.assertEqual("pending", pending["state"])

        client.round_id = 7
        client.bot_authority_id = 2
        player._offhangar_network_is_authority = False
        client._handle_message(result)
        self.assertEqual([], player._offhangar_network_bot_manifest)
        self.assertEqual("pending", pending["state"])

    def test_authority_event_alone_does_not_change_snapshot_owned_role(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Bravo", "ussr:T-34"
        )
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = "battle"
        client.player_id = 2
        client.bot_authority_id = 1
        player._offhangar_network_client = client
        player._offhangar_network_is_authority = False
        player._offhangar_network_bot_manifest = [{"id": 16}]

        self.network._handle_events(player, [{
            "kind": "authority", "player_id": 2,
        }])

        self.assertEqual(1, client.bot_authority_id)
        self.assertFalse(player._offhangar_network_is_authority)
        self.assertFalse(self.network.network_is_authority(player))
        self.assertEqual(
            2, player._offhangar_network_announced_authority_id
        )

        client._set_authority(2)

        self.assertEqual(2, client.bot_authority_id)
        self.assertTrue(player._offhangar_network_is_authority)
        self.assertTrue(
            player._offhangar_network_authority_handoff_pending
        )
        self.assertFalse(self.network.network_is_authority(player))

    def test_partial_native_demotion_stays_unknown_until_retry_completes(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        client.running = True
        client.connected = True
        player._offhangar_network_client = client
        player._offhangar_network_is_authority = True
        client.player_id = 1
        client.bot_authority_id = 1
        client.ready = True
        client.phase = "battle"
        bots = [
            types.SimpleNamespace(id=1016, claimed=True),
            types.SimpleNamespace(id=1017, claimed=True),
        ]
        releases = []
        second_failures = [1]

        def release_native_bots(unused_player):
            for bot in bots:
                if not bot.claimed:
                    continue
                releases.append(bot.id)
                if bot.id == 1017 and second_failures[0]:
                    second_failures[0] -= 1
                    return False
                bot.claimed = False
            return True

        offline = sys.modules["gui.mods.offhangar.offline_battle"]
        old_release = getattr(offline, "release_native_bots_for_replica", None)
        old_apply = self.network._apply_snapshot
        applied = []
        offline.release_native_bots_for_replica = release_native_bots
        self.network._apply_snapshot = (
            lambda target, message: applied.append((target, message))
        )
        snapshot = {
            "type": "snapshot",
            "bot_authority_id": 2,
            "players": [],
            "bots": [],
        }
        try:
            client._handle_message(snapshot)

            self.assertTrue(
                player._offhangar_network_authority_demotion_pending
            )
            self.assertFalse(player._offhangar_network_is_authority)
            self.assertFalse(self.network.network_is_authority(player))
            self.assertFalse(self.network.publish_authoritative_bots(
                player, {bot.id: bot for bot in bots}
            ))
            self.assertEqual([], applied)
            self.assertEqual([1016, 1017], releases)
            self.assertFalse(bots[0].claimed)
            self.assertTrue(bots[1].claimed)

            client._handle_message(snapshot)
        finally:
            self.network._apply_snapshot = old_apply
            if old_release is None:
                del offline.release_native_bots_for_replica
            else:
                offline.release_native_bots_for_replica = old_release

        self.assertFalse(
            player._offhangar_network_authority_demotion_pending
        )
        self.assertFalse(player._offhangar_network_is_authority)
        self.assertFalse(self.network.network_is_authority(player))
        self.assertEqual(2, client.bot_authority_id)
        self.assertEqual([1016, 1017, 1017], releases)
        self.assertFalse(bots[1].claimed)
        self.assertEqual(1, len(applied))

    def test_authority_reuses_unchanged_bot_navigation_and_health(self):
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        player.playerVehicleID = 7
        player._offhangar_network_is_authority = True
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle", running=True, connected=True,
        )
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        deaths = []
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, maxHealth=500,
            isAlive=True, publicInfo={"isAlive": True},
            position=vector3(0, 0, 0), yaw=0.0, pitch=0.0, roll=0.0,
        )
        def on_killed(vehicle_id, killer_id, reason):
            deaths.append((vehicle_id, killer_id, reason))
            bot.isAlive = False
        player.arena = types.SimpleNamespace(onVehicleKilled=on_killed)
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            bot.id: bot,
        }
        state = {
            "id": 16, "health": 500, "max_health": 500, "alive": True,
            "nav_source": "server_baked", "nav_order_revision": 12,
            "nav_x": 7.0, "nav_y": 3.0, "nav_z": 24.0,
        }
        conversions = []
        health_pushes = []
        original_world = self.network._world_from_server
        original_push = self.network._push_mock_health
        self.network._world_from_server = lambda target, value: (
            conversions.append(dict(value)) or
            vector3(value.get("x"), value.get("y"), value.get("z"))
        )
        self.network._push_mock_health = lambda *args: (
            health_pushes.append(args) or original_push(*args)
        )
        try:
            self.network._apply_bot_state(player, state, sample_time=100.0)
            self.network._apply_bot_state(player, state, sample_time=101.0)
            self.assertEqual(1, len(conversions))
            self.assertEqual(0, len(health_pushes))
            self.assertEqual(101.0, bot._network_navigation_time)
            self.assertEqual(
                101.0, player._offhangar_network_server_navigation_at
            )

            moved_waypoint = dict(state, nav_x=8.0)
            self.network._apply_bot_state(
                player, moved_waypoint, sample_time=102.0
            )
            self.assertEqual(2, len(conversions))

            bot.health = 450
            self.network._apply_bot_state(
                player, moved_waypoint, sample_time=103.0
            )
            self.assertEqual(0, len(health_pushes))
            self.assertEqual(450, bot.health)

            damaged = dict(moved_waypoint, health=400)
            self.network._apply_bot_state(
                player, damaged, sample_time=104.0
            )
            self.assertEqual(1, len(health_pushes))
            self.assertEqual(400, bot.health)
        finally:
            self.network._world_from_server = original_world
            self.network._push_mock_health = original_push

    def test_authority_health_changes_and_handoff_bypass_snapshot_cache(self):
        vector3 = sys.modules["Math"].Vector3
        deaths = []
        player = Player()
        player._offhangar_network_id = 1
        player.playerVehicleID = 7
        player._offhangar_network_is_authority = True
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle", running=True, connected=True,
        )
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, maxHealth=500,
            isAlive=True, publicInfo={"isAlive": True},
            position=vector3(0, 0, 0), yaw=0.0, pitch=0.0, roll=0.0,
        )
        killer = types.SimpleNamespace(id=1002, _network_bot_id=2)

        def on_killed(vehicle_id, killer_id, reason):
            deaths.append((vehicle_id, killer_id, reason))
            bot.isAlive = False

        player.arena = types.SimpleNamespace(onVehicleKilled=on_killed)
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            bot.id: bot, killer.id: killer,
        }
        alive = {
            "id": 16, "health": 500, "max_health": 500, "alive": True,
            "nav_source": "server_baked", "nav_order_revision": 12,
            "nav_x": 7.0, "nav_y": 3.0, "nav_z": 24.0,
            "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0,
            "aim_yaw": 0.0, "gun_pitch": 0.0,
        }
        pushes = []
        transforms = []
        original_world = self.network._world_from_server
        original_push = self.network._push_mock_health
        original_queue = self.network._queue_network_transform
        self.network._world_from_server = lambda target, value: vector3(
            value.get("x", 0.0), value.get("y", 0.0), value.get("z", 0.0)
        )
        self.network._push_mock_health = lambda *args: (
            pushes.append(args) or original_push(*args)
        )
        self.network._queue_network_transform = (
            lambda *args: transforms.append(args) or True
        )
        try:
            self.network._apply_bot_state(player, alive, sample_time=100.0)
            self.network._apply_bot_state(player, alive, sample_time=101.0)
            self.assertEqual(0, len(pushes))

            dead = dict(
                alive, health=0, alive=False,
                killer_kind="human", killer_id=1,
            )
            self.network._apply_bot_state(player, dead, sample_time=102.0)
            self.network._apply_bot_state(player, dead, sample_time=103.0)
            self.assertEqual(2, len(pushes))
            self.assertEqual([(1016, 7, 0)], deaths)

            changed_killer = dict(dead, killer_kind="bot", killer_id=2)
            self.network._apply_bot_state(
                player, changed_killer, sample_time=104.0
            )
            self.assertEqual(3, len(pushes))
            self.assertEqual(killer.id, bot.last_killer_id)

            bot.health = 500
            bot.isAlive = True
            bot.publicInfo["isAlive"] = True
            bot._network_death_notified = False
            self.network._apply_bot_state(
                player, alive, True, bot, 105.0
            )
            self.network._apply_bot_state(
                player, alive, True, bot, 106.0
            )
            self.assertEqual(5, len(pushes))
            self.assertEqual(2, len(transforms))
        finally:
            self.network._world_from_server = original_world
            self.network._push_mock_health = original_push
            self.network._queue_network_transform = original_queue

    def test_authority_snapshot_cannot_heal_or_resurrect_local_bot(self):
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        player.playerVehicleID = 7
        player._offhangar_network_is_authority = True
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle", running=True, connected=True,
        )
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        deaths = []
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=300, maxHealth=500,
            isAlive=True, publicInfo={"isAlive": True},
            position=vector3(0, 0, 0), yaw=0.0, pitch=0.0, roll=0.0,
        )
        def on_killed(vehicle_id, killer_id, reason):
            deaths.append((vehicle_id, killer_id, reason))
            bot.isAlive = False
        player.arena = types.SimpleNamespace(onVehicleKilled=on_killed)
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            bot.id: bot,
        }
        stale_alive = {
            "id": 16, "health": 500, "max_health": 500, "alive": True,
        }

        self.assertTrue(self.network._apply_bot_state(player, stale_alive))
        self.assertEqual(300, bot.health)
        self.assertTrue(bot.isAlive)

        bot.health = 0
        bot.isAlive = False
        bot.publicInfo["isAlive"] = False
        bot._network_death_notified = True
        self.assertTrue(self.network._apply_bot_state(player, stale_alive))
        self.assertEqual(0, bot.health)
        self.assertFalse(bot.isAlive)

        # Promotion handoff restores exact HP only while the bot is still alive.
        # A formally settled death cannot be resurrected without rolling back
        # its frag/score/wreck transaction, so that inconsistent fence stays shut.
        original_world = self.network._world_from_server
        original_queue = self.network._queue_network_transform
        transforms = []
        self.network._world_from_server = lambda unused_player, unused_state: vector3(
            0, 0, 0
        )
        self.network._queue_network_transform = lambda *args: (
            transforms.append(args) or True
        )
        try:
            self.assertFalse(self.network._apply_bot_state(
                player, stale_alive, True, bot, 105.0
            ))
            self.assertEqual([], transforms)

            bot.health = 300
            bot.isAlive = True
            bot.publicInfo["isAlive"] = True
            bot._network_death_notified = False
            self.assertTrue(self.network._apply_bot_state(
                player, stale_alive, True, bot, 106.0
            ))
        finally:
            self.network._world_from_server = original_world
            self.network._queue_network_transform = original_queue
        self.assertEqual(500, bot.health)
        self.assertTrue(bot.isAlive)
        self.assertEqual(1, len(transforms))

        dead = dict(
            stale_alive, health=0, alive=False,
            killer_kind="human", killer_id=1,
        )
        self.assertTrue(self.network._apply_bot_state(
            player, dead, sample_time=107.0
        ))
        self.assertEqual([(1016, 7, 0)], deaths)
        self.assertEqual(0, bot.health)
        self.assertFalse(bot.isAlive)

    def test_replica_never_uses_authority_bot_snapshot_cache(self):
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_is_authority = False
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, maxHealth=500,
            isAlive=True, publicInfo={"isAlive": True},
            position=vector3(0, 0, 0), yaw=0.0, pitch=0.0, roll=0.0,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            bot.id: bot,
        }
        state = {
            "id": 16, "health": 500, "max_health": 500, "alive": True,
            "nav_source": "server_baked", "nav_order_revision": 12,
            "nav_x": 7.0, "nav_y": 3.0, "nav_z": 24.0,
            "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0,
            "aim_yaw": 0.0, "gun_pitch": 0.0,
        }
        conversions = []
        pushes = []
        original_world = self.network._world_from_server
        original_push = self.network._push_mock_health
        original_queue = self.network._queue_network_transform
        self.network._world_from_server = lambda target, value: (
            conversions.append(dict(value)) or
            vector3(value.get("x", 0.0), value.get("y", 0.0), value.get("z", 0.0))
        )
        self.network._push_mock_health = lambda *args: pushes.append(args)
        self.network._queue_network_transform = lambda *args: None
        try:
            self.network._apply_bot_state(player, state, sample_time=100.0)
            self.network._apply_bot_state(player, state, sample_time=101.0)
        finally:
            self.network._world_from_server = original_world
            self.network._push_mock_health = original_push
            self.network._queue_network_transform = original_queue

        # Each replica apply converts both its navigation and rendered poses.
        self.assertEqual(4, len(conversions))
        self.assertEqual(2, len(pushes))

    def test_snapshot_requires_complete_server_navigation_before_suppressing_fallback(self):
        player = Player()
        player._offhangar_network_authority_handoff_pending = False
        player._offhangar_network_is_authority = True
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        player._offhangar_apply_network_rules_state = None
        player._offhangar_apply_network_battle_result = None

        self.network._apply_snapshot(player, {"bots": [
            {"id": 1, "nav_source": "server_baked"},
            {"id": 2, "nav_source": "client_fallback"},
        ]})
        self.assertFalse(player._offhangar_network_server_navigation_complete)

        self.network._apply_snapshot(player, {"bots": [
            {"id": 1, "nav_source": "server_baked"},
            {"id": 2, "nav_source": "server_hold"},
        ]})
        self.assertTrue(player._offhangar_network_server_navigation_complete)

    def test_dead_bot_does_not_reactivate_local_navigation(self):
        player = Player()
        player._offhangar_network_authority_handoff_pending = False
        player._offhangar_network_is_authority = True
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        player._offhangar_apply_network_rules_state = None
        player._offhangar_apply_network_battle_result = None
        player._offhangar_network_server_navigation_complete = False

        self.network._apply_snapshot(player, {
            "players": [],
            "bots": [
                {"id": 1, "alive": True, "nav_source": "server_baked"},
                {"id": 2, "alive": False},
            ],
        })

        self.assertTrue(player._offhangar_network_server_navigation_complete)

    def test_snapshot_stores_revisioned_server_order_and_converts_coordinates(self):
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_formation = lambda team, slot: (
            (0.0, 0.0, 0.0) if team == 1 else (0.0, 100.0, math.pi)
        )
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        client.running = True
        client.connected = True
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
        conversions = []
        original_world_from_server = self.network._world_from_server
        def counting_world_from_server(current_player, state):
            conversions.append(state)
            return original_world_from_server(current_player, state)
        self.network._world_from_server = counting_world_from_server
        try:
            order = self.network.authoritative_bot_order(player, mock)
            cached_order = self.network.authoritative_bot_order(player, mock)
        finally:
            self.network._world_from_server = original_world_from_server
        self.assertEqual(7, client.bot_order_revision)
        self.assertEqual(2, order["target_id"])
        self.assertEqual((5.0, 2.0, 20.0), order["move_position"])
        self.assertEqual((-4.0, 1.0, 80.0), order["aim_position"])
        self.assertEqual(order, cached_order)
        self.assertEqual(2, len(conversions))

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
                "combat_mode": "advance_contact",
                "aim_position": {"x": -4.0, "y": 1.0, "z": 80.0},
                "face_position": {"x": -4.0, "y": 1.0, "z": 80.0},
                "move_position": {"x": -4.0, "y": 1.0, "z": 80.0},
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
        self.assertEqual(order["aim_position"], order["move_position"])

    def test_visible_moving_target_uses_shell_speed_for_lead(self):
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_is_authority = True
        player._offhangar_network_id = 2
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle",
            bot_orders={16: {
                "id": 16, "target_id": 2, "target_kind": "human",
                "fire_allowed": True, "combat_mode": "advance_contact",
                "aim_position": {"x": 0.0, "y": 0.0, "z": 100.0},
                "face_position": {"x": 0.0, "y": 0.0, "z": 100.0},
                "move_position": {"x": 0.0, "y": 0.0, "z": 100.0},
            }},
        )
        bot = types.SimpleNamespace(
            _network_bot_id=16,
            _network_bot_shell_index=0,
            position=vector3(0.0, 0.0, 0.0),
            typeDescriptor=types.SimpleNamespace(
                gun={"shots": [{"speed": 100.0}]}
            ),
        )
        target = types.SimpleNamespace(
            isAlive=True, health=880,
            position=vector3(0.0, 0.0, 100.0),
            _veh_velocity=20.0, yaw=math.pi / 2.0,
        )
        original_local_mock = self.network._local_mock
        self.network._local_mock = lambda unused_player: target
        try:
            order = self.network.authoritative_bot_order(player, bot)
        finally:
            self.network._local_mock = original_local_mock

        self.assertGreater(order["aim_position"][0], 20.0)
        self.assertEqual((0.0, 0.0, 100.0), order["face_position"])
        self.assertEqual((0.0, 0.0, 100.0), order["move_position"])

    def test_authority_does_not_replay_its_own_bot_human_impact(self):
        self.network._test_hit_presentations[:] = []
        player = Player()
        player._offhangar_network_id = 1
        player._offhangar_network_is_authority = True
        player._offhangar_network_server_health = 880
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        player.playerVehicleID = 1
        player.arena = types.SimpleNamespace(onVehicleKilled=lambda *args: None)
        local = types.SimpleNamespace(
            id=1, health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
        )
        bot = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, isAlive=True,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            1: local, 1016: bot,
        }

        self.network._handle_events(player, [{
            "kind": "bot_human_hit", "attacker_bot": 16, "target": 1,
            "shot_seq": 4, "shot_result": 2, "damage": 180,
            "health": 700, "dead": False,
        }])

        self.assertEqual(700, local.health)
        self.assertEqual(700, player._offhangar_network_server_health)
        self.assertEqual([], self.network._test_hit_presentations)

    def test_visible_order_fails_closed_when_live_target_is_missing(self):
        player = Player()
        player._offhangar_network_is_authority = True
        player._offhangar_network_id = 2
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle",
            bot_orders={16: {
                "id": 16, "target_id": 999, "target_kind": "human",
                "fire_allowed": True,
                "aim_position": {"x": -4.0, "y": 1.0, "z": 80.0},
            }},
        )

        order = self.network.authoritative_bot_order(
            player, types.SimpleNamespace(_network_bot_id=16)
        )

        self.assertFalse(order["fire_allowed"])

    def test_battle_start_loads_and_acknowledges_initial_orders(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        sent = []
        client._send = lambda message: sent.append(message) or True

        client._handle_message({
            "type": "battle_start", "map": "04_himmelsdorf", "round_id": 3,
            "players": [], "bots": [], "bot_manifest": [],
            "bot_authority_id": 1, "bot_order_revision": 6,
            "bot_orders": [{"id": 16, "target_id": 2}],
        })

        self.assertEqual(6, client.bot_order_revision)
        self.assertEqual(2, client.bot_orders[16]["target_id"])
        self.assertIn({"type": "bot_order_ack", "revision": 6}, sent)

    def test_published_route_waypoints_include_probed_ground_height(self):
        player = Player()
        player._offhangar_network_is_authority = True
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = "battle"
        player._offhangar_network_client = client
        player._offhangar_network_formation = (
            lambda team, slot: (float(slot), float(team * 100), 0.0)
        )
        captured = []
        client.send_bot_manifest = lambda manifest, map_frame=None, manifest_nonce=None, round_id=None: captured.extend(manifest) or True
        director = types.SimpleNamespace(register_profile=lambda *args: {
            "route": {"id": "ridge", "waypoints": [(10.0, 20.0, False)]}
        })
        offline = sys.modules["gui.mods.offhangar.offline_battle"]
        old_director = getattr(offline, "_offh_ai_director", None)
        old_space = getattr(offline, "_offh_bspace", None)
        old_collide = sys.modules["BigWorld"].wg_collideSegment
        vector3 = sys.modules["Math"].Vector3
        offline._offh_ai_director = lambda unused: director
        offline._offh_bspace = lambda: 1
        sys.modules["BigWorld"].wg_collideSegment = (
            lambda space, start, end, flags: (vector3(start.x, 42.0, start.z),)
        )
        try:
            result = self.network.publish_bot_manifest(player, [
                (1, 1, 0, "ussr:T-34", "Bot", 1000,
                 0.0, 7.25, 0.0, 0.0)
            ])
        finally:
            sys.modules["BigWorld"].wg_collideSegment = old_collide
            if old_director is None:
                del offline._offh_ai_director
            else:
                offline._offh_ai_director = old_director
            if old_space is None:
                del offline._offh_bspace
            else:
                offline._offh_bspace = old_space

        self.assertFalse(result)
        self.assertEqual(7.25, captured[0]["y"])
        self.assertEqual(42.0, captured[0]["route"]["waypoints"][0]["y"])

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

    def test_snapshot_coalescing_preserves_order_body_for_latest_revision(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        player._offhangar_network_client = client
        with client._pending_lock:
            client._pending = [
                {
                    "type": "snapshot", "bot_authority_id": 1,
                    "players": [], "bots": [], "bot_order_revision": 12,
                    "bot_orders": [{"id": 16, "target_id": 2}],
                },
                {
                    "type": "snapshot", "bot_authority_id": 1,
                    "players": [], "bots": [], "bot_order_revision": 12,
                    "server_tick": 101,
                },
            ]

        client._poll()

        self.assertEqual(12, client.bot_order_revision)
        self.assertEqual(2, client.bot_orders[16]["target_id"])
        self.assertEqual(101, player._offhangar_network_snapshot["server_tick"])

    def test_snapshot_coalescing_never_carries_orders_across_rounds(self):
        player = Player()
        player._offhangar_network_health_round_id = 2
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        player._offhangar_network_client = client
        with client._pending_lock:
            client._pending = [
                {
                    "type": "snapshot", "round_id": 1,
                    "bot_authority_id": 1, "players": [], "bots": [],
                    "bot_order_revision": 0,
                    "bot_orders": [{"id": 16, "target_id": 999}],
                },
                {
                    "type": "snapshot", "round_id": 2,
                    "bot_authority_id": 1, "players": [], "bots": [],
                    "bot_order_revision": 0, "server_tick": 101,
                },
            ]

        client._poll()

        self.assertEqual({}, client.bot_orders)
        self.assertNotIn("bot_orders", player._offhangar_network_snapshot)
        self.assertEqual(2, player._offhangar_network_snapshot["round_id"])

    def test_revision_without_order_body_keeps_last_executable_orders(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        client.bot_order_revision = 7
        client.bot_orders = {16: {"id": 16, "target_id": 2}}

        client._handle_message({
            "type": "snapshot", "bot_authority_id": 1,
            "players": [], "bots": [], "bot_order_revision": 8,
        })

        self.assertEqual(7, client.bot_order_revision)
        self.assertEqual(2, client.bot_orders[16]["target_id"])

    def test_cross_revision_coalesce_recovers_from_server_retransmit(self):
        player = Player()
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        player._offhangar_network_client = client
        client.bot_order_revision = 7
        client.bot_orders = {16: {"id": 16, "target_id": 2}}
        sent = []
        client._send = lambda message: sent.append(message) or True
        with client._pending_lock:
            client._pending = [
                {
                    "type": "snapshot", "bot_authority_id": 1,
                    "players": [], "bots": [], "bot_order_revision": 8,
                    "bot_orders": [{"id": 16, "target_id": 3}],
                },
                {
                    "type": "snapshot", "bot_authority_id": 1,
                    "players": [], "bots": [], "bot_order_revision": 9,
                    "server_tick": 102,
                },
            ]

        client._poll()
        self.assertEqual(7, client.bot_order_revision)
        self.assertEqual(2, client.bot_orders[16]["target_id"])

        client._handle_message({
            "type": "snapshot", "bot_authority_id": 1,
            "players": [], "bots": [], "bot_order_revision": 9,
            "bot_orders": [{"id": 16, "target_id": 4}],
        })
        self.assertEqual(9, client.bot_order_revision)
        self.assertEqual(4, client.bot_orders[16]["target_id"])
        self.assertIn({"type": "bot_order_ack", "revision": 9}, sent)

    def test_missing_bot_order_requests_rate_limited_resync(self):
        player = Player()
        player._offhangar_network_is_authority = True
        player._offhangar_network_id = 1
        client = self.network.LANClient(
            player, "127.0.0.1", 28782, "Alpha", "ussr:T-34"
        )
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = "battle"
        client.player_id = 1
        player._offhangar_network_client = client
        sent = []
        client._send = lambda message: sent.append(message) or True
        bot = types.SimpleNamespace(_network_bot_id=16)

        self.assertIsNone(self.network.authoritative_bot_order(player, bot))
        self.assertIsNone(self.network.authoritative_bot_order(player, bot))

        requests = [message for message in sent
                    if message.get("type") == "bot_order_resync"]
        self.assertEqual(1, len(requests))

    def test_remote_pose_is_interpolated_between_thirty_hz_snapshots(self):
        class Matrix:
            def __init__(self, model):
                self.model = model

            def setRotateYPR(self, rotation):
                self.rotation = rotation

            @property
            def translation(self):
                return self.model.position

            @translation.setter
            def translation(self, value):
                self.model.position = value

        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        pose_servo = object()
        model = types.SimpleNamespace(
            position=vector3(0, 1, 0), yaw=0.0, motors=[pose_servo]
        )
        mock = types.SimpleNamespace(
            id=1001, _network_server_id=2, _network_remote=True,
            health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
            position=vector3(0, 1, 0), yaw=0.0, pitch=0.0, roll=0.0,
            matrix=Matrix(model),
            _t_mat=types.SimpleNamespace(setRotateYPR=lambda value: None),
            _g_mat=types.SimpleNamespace(setRotateYPR=lambda value: None),
            model=model, _chassis_model=model, bw_entity=None,
            _offh_native_model_root_ready=True,
            _pose_servo=pose_servo,
            _servo_added=True,
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

    def test_render_smoothing_rate_limits_native_filter_commits(self):
        class Matrix:
            def setRotateYPR(self, rotation):
                self.rotation = rotation

        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        pose_servo = object()
        model = types.SimpleNamespace(
            position=vector3(0, 1, 0), yaw=0.0, motors=[pose_servo]
        )
        mock = types.SimpleNamespace(
            id=1016, _network_shared_bot=True,
            health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
            position=vector3(0, 1, 0), yaw=0.0, pitch=0.0, roll=0.0,
            matrix=Matrix(), _t_mat=Matrix(), _g_mat=Matrix(),
            model=model, _chassis_model=model, bw_entity=None,
            _network_target_position=vector3(10, 1, 0),
            _network_target_velocity=(0.0, 0.0, 0.0),
            _network_target_time=self.network.time.time(),
            _network_target_yaw=0.0,
            _network_target_aim_yaw=0.0,
            _network_target_gun_pitch=0.0,
            _network_filter_sync_at=self.network.time.time() + 1.0,
            _offh_native_model_root_ready=True,
            _pose_servo=pose_servo,
            _servo_added=True,
        )
        calls = []
        original = self.network.vehicle_pose.commit_pose
        self.addCleanup(
            lambda: setattr(self.network.vehicle_pose, "commit_pose", original)
        )
        self.network.vehicle_pose.commit_pose = (
            lambda *args, **kwargs: calls.append(kwargs) or True
        )

        self.assertTrue(self.network.advance_network_smoothing(
            player, {1016: mock}, 1.0 / 60.0
        ))
        self.assertFalse(calls[-1]["sync_filter"])

        mock._network_filter_sync_at = 0.0
        self.assertTrue(self.network.advance_network_smoothing(
            player, {1016: mock}, 1.0 / 60.0
        ))
        self.assertTrue(calls[-1]["sync_filter"])

    def test_replica_pose_waits_for_async_model_root_before_one_servo_attach(self):
        class Matrix:
            def setRotateYPR(self, rotation):
                self.rotation = rotation

        class Model:
            def __init__(self):
                self.motors = []
                self.add_calls = []

            def addMotor(self, motor):
                self.add_calls.append(motor)
                self.motors.append(motor)

        vector3 = sys.modules["Math"].Vector3
        player = Player()
        model = Model()
        mock = types.SimpleNamespace(
            id=1016,
            position=vector3(0, 1, 0),
            yaw=0.0,
            pitch=0.0,
            roll=0.0,
            matrix=Matrix(),
            model=model,
            _chassis_model=model,
            bw_entity=None,
            _offh_native_model_root_ready=False,
            _servo_added=False,
            _pose_servo=None,
        )
        target = vector3(4, 1, 8)

        self.assertFalse(self.network._apply_remote_transform(
            player, mock, target, 0.25, sync_filter=False
        ))
        self.assertEqual([], model.add_calls)
        self.assertEqual([], model.motors)
        self.assertFalse(mock._servo_added)
        self.assertIsNone(mock._pose_servo)

        mock.bw_entity = types.SimpleNamespace(id=901, filter=None)
        mock._offh_native_model_root_ready = True
        self.assertTrue(self.network._apply_remote_transform(
            player, mock, target, 0.25, sync_filter=False
        ))
        self.assertEqual(1, len(model.add_calls))
        self.assertEqual([mock._pose_servo], model.motors)
        self.assertTrue(mock._servo_added)

        self.assertTrue(self.network._apply_remote_transform(
            player, mock, target, 0.25, sync_filter=False
        ))
        self.assertEqual(1, len(model.add_calls))
        self.assertEqual([mock._pose_servo], model.motors)

    def test_replica_pose_retries_after_silent_servo_add_no_op(self):
        class Matrix:
            def setRotateYPR(self, rotation):
                self.rotation = rotation

        class Model:
            def __init__(self):
                self.motors = []
                self.add_attempts = 0

            def addMotor(self, motor):
                self.add_attempts += 1
                if self.add_attempts > 1:
                    self.motors.append(motor)

        vector3 = sys.modules["Math"].Vector3
        player = Player()
        model = Model()
        mock = types.SimpleNamespace(
            id=1016,
            position=vector3(0, 1, 0),
            yaw=0.0,
            pitch=0.0,
            roll=0.0,
            matrix=Matrix(),
            model=model,
            _chassis_model=model,
            bw_entity=types.SimpleNamespace(id=901, filter=None),
            _offh_native_model_root_ready=True,
            _servo_added=False,
            _pose_servo=None,
        )
        target = vector3(4, 1, 8)

        self.assertFalse(self.network._apply_remote_transform(
            player, mock, target, 0.25, sync_filter=False
        ))
        self.assertEqual([], model.motors)
        self.assertFalse(mock._servo_added)
        self.assertIsNone(mock._pose_servo)

        self.assertTrue(self.network._apply_remote_transform(
            player, mock, target, 0.25, sync_filter=False
        ))
        self.assertEqual(2, model.add_attempts)
        self.assertEqual(1, len(model.motors))
        self.assertIs(mock._pose_servo, model.motors[0])
        self.assertTrue(mock._servo_added)

    def test_remote_bot_prediction_does_not_cross_a_baked_hazard(self):
        class Matrix:
            def setRotateYPR(self, rotation):
                self.rotation = rotation

        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player._offhangar_network_id = 1
        model = types.SimpleNamespace(position=vector3(4, 1, 0), yaw=0.0)
        mock = types.SimpleNamespace(
            id=1016, _network_shared_bot=True,
            health=880, maxHealth=880, isAlive=True,
            publicInfo={"isAlive": True},
            position=vector3(4, 1, 0), yaw=0.0, pitch=0.0, roll=0.0,
            matrix=Matrix(), _t_mat=Matrix(), _g_mat=Matrix(),
            model=model, _chassis_model=model, bw_entity=None,
            _network_target_position=vector3(4, 1, 0),
            _network_target_velocity=(20.0, 0.0, 0.0),
            _network_target_time=self.network.time.time() - 0.1,
            _network_target_yaw=0.0,
            _network_target_aim_yaw=0.0,
            _network_target_gun_pitch=0.0,
        )
        offline = sys.modules["gui.mods.offhangar.offline_battle"]
        old_pose_safe = getattr(offline, "_offh_ai_baked_pose_safe", None)
        self.addCleanup(
            lambda: setattr(offline, "_offh_ai_baked_pose_safe", old_pose_safe)
        )
        offline._offh_ai_baked_pose_safe = lambda pose: pose[0] < 5.0

        self.assertTrue(self.network.advance_network_smoothing(
            player, {1016: mock}, 1.0 / 60.0
        ))

        self.assertEqual(4.0, model.position.x)

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

    def test_pong_uses_network_receive_time_not_delayed_main_thread_time(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")

        client._handle_message({
            "type": "pong", "client_time": 10.0,
            "_client_received_time": 10.025,
        })

        self.assertAlmostEqual(25.0, client.rtt_ms, places=3)

    def test_server_timing_uses_network_receive_time_and_half_rtt(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")
        client.rtt_ms = 40.0

        loaded = client._load_server_timing({
            "_client_received_time": 100.0,
            "timing": {
                "phase": "prebattle",
                "start_in_ms": 25000,
                "remaining_ms": 900000,
                "duration_ms": 900000,
            },
        })

        self.assertTrue(loaded)
        self.assertAlmostEqual(124.98, client.combat_deadline, places=3)
        self.assertAlmostEqual(1024.98, client.combat_end_deadline, places=3)
        self.assertEqual(client.combat_deadline, player._offhangar_network_combat_deadline)

    def test_battle_snapshot_corrects_the_shared_end_deadline(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")

        client._load_server_timing({
            "_client_received_time": 200.0,
            "timing": {
                "phase": "battle",
                "start_in_ms": 0,
                "remaining_ms": 750000,
                "duration_ms": 900000,
            },
        })

        self.assertEqual("battle", player._offhangar_network_combat_phase)
        self.assertAlmostEqual(950.0, player._offhangar_network_combat_end_deadline)

    def test_transport_diagnostics_report_socket_and_game_queue_delays_separately(self):
        player = Player()
        client = self.network.LANClient(player, "127.0.0.1", 28782, "Alpha", "ussr:T-34")
        client._diag_window_start = 10.0
        client._diag_chunks = 50
        client._diag_messages = 120
        client._diag_snapshots = 95
        client._diag_bot_updates = 62
        client._diag_max_socket_gap = 0.08
        client._diag_max_snapshot_gap = 0.12
        client._diag_max_bot_update_gap = 0.19
        client._diag_max_queue_age = 0.9
        client._diag_max_pending = 17

        diagnostic = client._transport_diagnostic_snapshot(now=15.0)

        self.assertEqual(50, diagnostic["chunks"])
        self.assertEqual(95, diagnostic["snapshots"])
        self.assertEqual(62, diagnostic["bot_updates"])
        self.assertAlmostEqual(0.08, diagnostic["max_socket_gap"])
        self.assertAlmostEqual(0.19, diagnostic["max_bot_update_gap"])
        self.assertAlmostEqual(0.9, diagnostic["max_queue_age"])
        self.assertEqual(17, diagnostic["max_pending"])
        self.assertEqual(0, client._diag_chunks)
        self.assertIsNone(client._transport_diagnostic_snapshot(now=16.0))

    def test_shared_bot_death_uses_relayed_bot_killer(self):
        player = Player()
        player._offhangar_network_is_authority = True
        victim = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, maxHealth=500,
            isAlive=True,
        )
        killer = types.SimpleNamespace(id=1003, _network_bot_id=3)
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            victim.id: victim, killer.id: killer,
        }
        pushed = []
        original_push = self.network._push_mock_health
        self.network._push_mock_health = lambda *args: pushed.append(args)
        try:
            self.network._apply_bot_state(player, {
                "id": 16, "health": 0, "max_health": 500,
                "alive": False, "killer_bot_id": 3,
            })
        finally:
            self.network._push_mock_health = original_push

        self.assertEqual(1003, pushed[0][5])

    def test_shared_bot_death_resolves_relayed_human_killer(self):
        player = Player()
        player._offhangar_network_id = 7
        player.playerVehicleID = 77
        victim = types.SimpleNamespace(
            id=1016, _network_bot_id=16, health=500, maxHealth=500,
            isAlive=True,
        )
        sys.modules["gui.mods.offhangar.offline_battle"].G_MOCK_VEHICLES = {
            victim.id: victim,
        }
        pushed = []
        original_push = self.network._push_mock_health
        self.network._push_mock_health = lambda *args: pushed.append(args)
        try:
            self.network._apply_bot_state(player, {
                "id": 16, "health": 0, "max_health": 500,
                "alive": False, "killer_kind": "human", "killer_id": 7,
            })
        finally:
            self.network._push_mock_health = original_push

        self.assertEqual(77, pushed[0][5])

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

    def test_remote_human_spotting_refreshes_on_spawn_and_snapshots(self):
        network_source = NETWORK_PATH.read_text()
        apply_start = network_source.index("def _apply_remote_state")
        apply_end = network_source.index("def _apply_bot_state", apply_start)
        apply_source = network_source[apply_start:apply_end]
        battle_source = (
            ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
        ).read_text()
        spawn_start = battle_source.index("mock_vehicles[e_id] = e_mock")
        spawn_source = battle_source[spawn_start:spawn_start + 1800]

        self.assertIn("update_remote_spotting(player, mock, force_spot)", apply_source)
        self.assertIn("mock._network_spot_initialized = True", apply_source)
        self.assertIn("update_remote_spotting(player, e_mock, True)", spawn_source)
        self.assertIn("LAN remote human ready", spawn_source)
        self.assertIn("LAN remote human visibility", network_source)

    def test_replica_defers_bot_snapshots_until_native_lineup_is_complete(self):
        player = Player()
        player._offhangar_network_client = types.SimpleNamespace(
            ready=True, phase="battle"
        )
        player._offhangar_network_is_authority = False
        player._offh_auto_spawn_expected = 28
        player._offh_auto_spawn_completed = 5
        applied = []
        remote_applied = []
        original_apply = self.network._apply_bot_state
        original_remote = self.network._apply_remote_state
        original_indexes = self.network._network_mock_indexes
        self.network._apply_bot_state = lambda *args: applied.append(args)
        self.network._apply_remote_state = lambda *args: remote_applied.append(args)
        self.network._network_mock_indexes = lambda: ({}, {})
        try:
            self.network._apply_snapshot(
                player,
                {"players": [{"id": 2}], "bots": [{"id": 1}]},
            )
            self.assertEqual([], applied)
            self.assertEqual(1, len(remote_applied))
            self.assertTrue(player._offhangar_network_bot_snapshots_deferred)

            player._offh_auto_spawn_completed = 28
            self.network._apply_snapshot(
                player, {"players": [], "bots": [{"id": 1}]}
            )
        finally:
            self.network._apply_bot_state = original_apply
            self.network._apply_remote_state = original_remote
            self.network._network_mock_indexes = original_indexes

        self.assertEqual(1, len(applied))
        self.assertFalse(player._offhangar_network_bot_snapshots_deferred)

    def test_spotting_exception_keeps_native_fifty_metre_proximity_rule(self):
        vector3 = sys.modules["Math"].Vector3
        player = Player()
        player.playerVehicleID = 1
        player._offhangar_team = 1
        local = types.SimpleNamespace(
            id=1, position=vector3(0, 0, 0), isAlive=True,
            publicInfo={"team": 1},
        )
        model = types.SimpleNamespace(visible=False, visibleAttachments=False)
        remote = types.SimpleNamespace(
            id=1001, position=vector3(0, 0, 40), isAlive=True,
            health=880, _bot_team=2, _chassis_model=model, model=model,
            publicInfo={"team": 2}, marker=None, proxy=None,
            _network_server_id=2,
        )
        offline = sys.modules["gui.mods.offhangar.offline_battle"]
        offline.G_MOCK_VEHICLES = {1: local, 1001: remote}
        previous = getattr(offline, "_offh_spot_visible_for_player", None)
        offline._offh_spot_visible_for_player = lambda *args: (_ for _ in ()).throw(
            TypeError("broken spotting state")
        )
        try:
            self.assertTrue(
                self.network.update_remote_spotting(player, remote, True)
            )
        finally:
            if previous is None:
                del offline._offh_spot_visible_for_player
            else:
                offline._offh_spot_visible_for_player = previous

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
        self.assertIn("def _capture_xz(entity):", source)
        self.assertIn("position = getattr(_pm, 'position', None)", source)
        self.assertIn("LAN capture check base_team=%d", source)
        self.assertIn("LAN capture tick failed", source)
        self.assertIn("'tactical_fallback'", source)
        self.assertIn("send_authoritative_rules(player, g_base_capture)", source)
        self.assertIn("send_authoritative_result", source)
        self.assertIn("if not network_is_authority(player):", source)

    def test_authoritative_capture_report_includes_active_invader_identity(self):
        player = Player()
        sent = []
        client = types.SimpleNamespace(
            running=True,
            connected=True,
            ready=True,
            phase="battle",
            send_rules=lambda rules: sent.append(rules) or True,
        )
        player._offhangar_network_client = client
        player._offhangar_network_is_authority = True
        player._offhangar_network_authority_handoff_pending = False
        player._offhangar_network_authority_demotion_pending = False

        self.assertTrue(self.network.send_authoritative_rules(player, {
            1: {
                "points": 1,
                "stopped": False,
                "contributors": {"human:2": 1, "bot:17": 0},
                "active_contributors": ["human:2", "bot:17"],
                "invaders": 2,
                "cursor": 1,
            },
            2: {
                "points": 0,
                "stopped": False,
                "contributors": {},
                "active_contributors": [],
                "invaders": 0,
                "cursor": 0,
            },
        }))

        self.assertEqual(2, sent[0]["bases"]["1"]["invaders"])
        self.assertEqual(
            ["bot:17", "human:2"],
            sent[0]["bases"]["1"]["active_contributors"],
        )

    def test_bot_observation_failures_are_visible_once_per_battle(self):
        source = (ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py").read_text()

        self.assertIn("g_offh_observation_error_gen", source)
        self.assertIn("LAN bot observation publish failed", source)

    def test_every_death_refreshes_the_canonical_team_frag_score(self):
        source = (ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py").read_text()

        self.assertIn("def _offh_refresh_team_score(player):", source)
        self.assertIn("correlation.updateFrags(our_score, enemy_score)", source)
        wrapper = source[source.index("class _KillEventWrapper(object):"):]
        wrapper = wrapper[:8000]
        self.assertIn("_offh_refresh_team_score(_pl)", wrapper)
        self.assertIn("_offh_battle_callback(0.0", wrapper)

    def test_initial_spawn_uses_the_actual_lan_team(self):
        source = (ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py").read_text()

        self.assertIn("_player_spawn_team = int(getattr(player, '_offhangar_team', 1) or 1)", source)
        self.assertIn("gp['teamSpawnPoints/team%d' % _player_spawn_team]", source)
        self.assertNotIn("getattr(at, 'teamSpawnPoints'", source)
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
