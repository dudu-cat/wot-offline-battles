import importlib.util
import re
import sys
import textwrap
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
STREAMING = ROOT / "scripts/client/gui/mods/offhangar/spawn_streaming_bootstrap.py"


_MISSING = object()


def extract_nested_function(name):
    source = BATTLE.read_text()
    lines = source.splitlines(True)
    marker = "def %s(" % name
    start = None
    indent = None
    for index, line in enumerate(lines):
        stripped = line.lstrip("\t")
        if stripped.startswith(marker):
            start = index
            indent = line[:len(line) - len(stripped)]
            break
    if start is None:
        raise AssertionError("missing nested function %s" % name)
    end = len(lines)
    body_indent = indent + "\t"
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith(body_indent):
            end = index
            break
    return textwrap.dedent("".join(lines[start:end]))


def extract_streaming_construction_block():
    source = BATTLE.read_text()
    comment = source.index("# The authority's collision space is camera-streamed")
    statement = source.index(
        "_spawn_stream_role = _offh_network_bot_role(_pl)", comment
    )
    start = source.rfind("\n", 0, statement) + 1
    next_function = source.index("def _lineup_job_key(", statement)
    end = source.rfind("\n", 0, next_function) + 1
    return textwrap.dedent(source[start:end])


def execute_function(source, namespace):
    exec(source, namespace)
    name = source[source.index("def ") + 4:source.index("(", source.index("def "))]
    return namespace[name]


def execute_statement_block(source, namespace):
    wrapped = "def _streaming_construction_under_test():\n" + textwrap.indent(
        source, "\t"
    )
    exec(wrapped, namespace)
    return namespace["_streaming_construction_under_test"]


def load_streaming_module():
    spec = importlib.util.spec_from_file_location(
        "spawn_streaming_bootstrap_under_test", STREAMING
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Vector3(object):
    def __init__(self, x, y=None, z=None):
        if y is None:
            x, y, z = x
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class FakeBootstrap(object):
    def __init__(self, jobs, phases):
        self.jobs = tuple(jobs)
        self.phases = list(phases)
        self.polls = []
        self.stops = 0
        self.failure_reason = "contract failure"

    def poll(self, now, active_count):
        self.polls.append((float(now), int(active_count)))
        if self.phases:
            return self.phases.pop(0)
        return "placement_ready"

    def stop(self):
        self.stops += 1
        return True


def job(team, slot, bot_id, x=0.0, y=50.0, z=0.0):
    return (
        int(team), int(slot), bot_id, float(x), float(y), float(z), 0.0,
        "vehicle", "Bot",
    )


class OfflineBattleSpawnStreamingIntegrationTests(unittest.TestCase):
    def install_modules(self, replacements):
        previous = {
            name: sys.modules.get(name, _MISSING) for name in replacements
        }

        def restore():
            for name, module in previous.items():
                if module is _MISSING:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.addCleanup(restore)
        sys.modules.update(replacements)

    def install_spawn_helper(self, bootstrap_type):
        gui = types.ModuleType("gui")
        mods = types.ModuleType("gui.mods")
        offhangar = types.ModuleType("gui.mods.offhangar")
        helper = types.ModuleType(
            "gui.mods.offhangar.spawn_streaming_bootstrap"
        )
        helper.SpawnStreamingBootstrap = bootstrap_type
        helper.coverage_target_from_bounds = (
            load_streaming_module().coverage_target_from_bounds
        )
        self.install_modules({
            "gui": gui,
            "gui.mods": mods,
            "gui.mods.offhangar": offhangar,
            helper.__name__: helper,
        })

    def promotion_namespace(self, role, collision_mode):
        streaming = load_streaming_module()
        self.install_spawn_helper(streaming.SpawnStreamingBootstrap)
        clock = [100.0]
        projection_calls = []
        collision_calls = []

        class Projection(object):
            farPlane = 500.0

        projection = Projection()
        player = types.SimpleNamespace(
            _offhangar_network_bot_manifest=[{
                "team": 1,
                "slot": 0,
                "id": 7,
                "x": 480.0,
                "y": 53.0,
                "z": 480.0,
                "yaw": 0.4,
                "vehicle": "ussr:T-34",
                "name": "Bot-7",
            }],
            _offh_spawn_streaming_bootstrap=None,
            _offhangar_network_is_authority=(role in ("authority", "handoff")),
            _offhangar_network_authority_handoff_pending=(role == "handoff"),
            position=Vector3(-490.0, 50.0, -490.0),
        )

        def collide(_space, start, _end, _mask):
            collision_calls.append((start.x, start.y, start.z))
            if collision_mode[0] == "error":
                raise RuntimeError("collision unavailable")
            if collision_mode[0] == "waiting":
                return None
            return (Vector3(start.x, start.y - 3.0, start.z), None)

        network = types.ModuleType("gui.mods.offhangar.network_battle")
        network._world_from_server = lambda _player, entry: Vector3(
            float(entry["x"]) + 10.0,
            float(entry["y"]) + 2.0,
            float(entry["z"]) - 5.0,
        )
        network._world_yaw_from_server = lambda _player, entry: (
            float(entry["yaw"]) + 0.1
        )
        self.install_modules({network.__name__: network})
        namespace = {
            "_pl": player,
            "_offh_network_bot_role": lambda _player: role,
            "BigWorld": types.SimpleNamespace(
                player=lambda: player,
                projection=lambda: projection_calls.append("projection") or projection,
                wg_collideSegment=collide,
            ),
            "Math": types.SimpleNamespace(Vector3=Vector3),
            "_offh_bspace": lambda: 1,
            "veh_pos": (-490.0, 50.0, -490.0),
            "g_offh_baked_navigation_graph": {
                "bounds": [-500.0, -500.0, 500.0, 500.0],
            },
            "time": types.SimpleNamespace(time=lambda: clock[0]),
            "_auto_spawn_delay": 10.0,
            "LOG_ERROR": lambda *_args: None,
        }
        return (
            namespace,
            player,
            clock,
            projection_calls,
            collision_calls,
        )

    def test_promotion_callback_converts_manifest_and_reuses_live_gate(self):
        collision_mode = ["waiting"]
        namespace, player, clock, projection_calls, collision_calls = (
            self.promotion_namespace("handoff", collision_mode)
        )
        prepare = execute_function(
            extract_nested_function("_prepare_native_authority_streaming"),
            namespace,
        )

        self.assertFalse(prepare())
        bootstrap = player._offh_spawn_streaming_bootstrap
        self.assertIsNotNone(bootstrap)
        original_deadline = bootstrap._deadline
        self.assertEqual((
            (1, 0, 7, 490.0, 55.0, 475.0, 0.5, "ussr:T-34", "Bot-7"),
        ), bootstrap.jobs)
        self.assertEqual(1000.0, bootstrap._projection.farPlane)

        collision_mode[0] = "ready"
        clock[0] = 101.0
        self.assertTrue(prepare())

        self.assertIs(bootstrap, player._offh_spawn_streaming_bootstrap)
        self.assertEqual(original_deadline, bootstrap._deadline)
        self.assertEqual(["projection"], projection_calls)
        self.assertGreaterEqual(len(collision_calls), 2)

    def test_promotion_callback_fails_closed_on_streaming_probe_failure(self):
        collision_mode = ["error"]
        namespace, player, _clock, projection_calls, _collision_calls = (
            self.promotion_namespace("handoff", collision_mode)
        )
        prepare = execute_function(
            extract_nested_function("_prepare_native_authority_streaming"),
            namespace,
        )

        self.assertFalse(prepare())

        self.assertEqual(["projection"], projection_calls)
        self.assertEqual(
            "support_probe_error",
            player._offh_spawn_streaming_bootstrap.failure_reason,
        )

    def test_replica_promotion_callback_never_constructs_projection_bootstrap(self):
        collision_mode = ["ready"]
        namespace, player, _clock, projection_calls, collision_calls = (
            self.promotion_namespace("replica", collision_mode)
        )
        prepare = execute_function(
            extract_nested_function("_prepare_native_authority_streaming"),
            namespace,
        )

        self.assertFalse(prepare())

        self.assertIsNone(player._offh_spawn_streaming_bootstrap)
        self.assertEqual([], projection_calls)
        self.assertEqual([], collision_calls)

    def test_promotion_streaming_helper_is_installed_as_player_callback(self):
        source = BATTLE.read_text()

        self.assertIsNotNone(
            re.search(
                r"_offhangar_prepare_native_authority_streaming\s*=\s*"
                r"_prepare_native_authority_streaming",
                source,
            ),
            "promotion streaming helper is not installed on the player",
        )

    def construction_namespace(self, role):
        projection_calls = []
        projection = object()
        player = types.SimpleNamespace(
            _offh_spawn_streaming_bootstrap=None,
        )
        jobs = [job(1, 0, 7, 300.0, 50.0, 400.0)]
        created = []

        class RecordingBootstrap(object):
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.jobs = tuple(args[1])
                created.append(self)

            def stop(self):
                pass

        self.install_spawn_helper(RecordingBootstrap)
        bigworld = types.SimpleNamespace(
            projection=lambda: projection_calls.append("projection") or projection,
            wg_collideSegment=lambda *_args: None,
        )
        namespace = {
            "_pl": player,
            "_jobs": jobs,
            "_offh_network_bot_role": lambda _player: role,
            "BigWorld": bigworld,
            "Math": types.SimpleNamespace(Vector3=Vector3),
            "_offh_bspace": lambda: 1,
            "veh_pos": (10.0, 50.0, 20.0),
            "time": types.SimpleNamespace(time=lambda: 100.0),
            "_auto_spawn_delay": 10.0,
            "LOG_ERROR": lambda *_args: None,
        }
        return namespace, player, jobs, projection_calls, created

    def test_only_local_or_authority_constructs_the_projection_bootstrap(self):
        block = extract_streaming_construction_block()

        for role in ("local", "authority"):
            with self.subTest(role=role):
                namespace, player, jobs, projection_calls, created = (
                    self.construction_namespace(role)
                )
                execute_statement_block(block, namespace)()

                self.assertEqual(["projection"], projection_calls)
                self.assertEqual(1, len(created))
                self.assertIs(player._offh_spawn_streaming_bootstrap, created[0])
                self.assertEqual(tuple(jobs),
                                 player._offh_spawn_streaming_bootstrap.jobs)

        for role in ("replica", "unknown", "handoff"):
            with self.subTest(role=role):
                namespace, player, _jobs, projection_calls, created = (
                    self.construction_namespace(role)
                )
                execute_statement_block(block, namespace)()

                self.assertEqual([], projection_calls)
                self.assertEqual([], created)
                self.assertIsNone(player._offh_spawn_streaming_bootstrap)

    def placement_namespace(self, phase):
        events = []
        callbacks = []
        jobs = [job(1, 0, None)]
        bootstrap = FakeBootstrap(jobs, [phase])
        player = types.SimpleNamespace(
            _offh_spawn_streaming_bootstrap=bootstrap,
            _offh_spawn_streaming_wait_logged=100.0,
            _offh_spawn_streaming_monitor_active=False,
            _offh_lineup_prefetch_ready=True,
            _offh_lineup_prefetch_wait_logged=100.0,
            _offh_lineup_model_refs={},
            _offh_lineup_prefetch_started_at=90.0,
        )

        def schedule(delay, callback):
            events.append(("schedule", float(delay), callback.__name__))
            callbacks.append((float(delay), callback))

        namespace = {
            "g_offh_battle_gen": 4,
            "_offh_my_gen": [4],
            "_battle_finished": [False],
            "BigWorld": types.SimpleNamespace(player=lambda: player),
            "time": types.SimpleNamespace(time=lambda: 100.0),
            "_offh_battle_callback": schedule,
            "_spawn_next": lambda prepared: events.append(
                ("spawn", tuple(prepared))
            ),
            "_poll_spawn_streaming_activation": lambda: None,
            "_n_per_team": 15,
            "LOG_DEBUG": lambda *_args: None,
            "LOG_ERROR": lambda *_args: events.append(("error",)),
        }
        original_poll = bootstrap.poll

        def ordered_poll(now, active_count):
            events.append(("poll", int(active_count)))
            return original_poll(now, active_count)

        bootstrap.poll = ordered_poll
        return namespace, player, bootstrap, events, callbacks, jobs

    def test_waiting_support_retries_without_creating_an_entity(self):
        namespace, _player, bootstrap, events, callbacks, jobs = (
            self.placement_namespace("waiting_support")
        )
        begin = execute_function(
            extract_nested_function("_begin_bot_placement"), namespace
        )

        begin(jobs)

        self.assertEqual([(100.0, 0)], bootstrap.polls)
        self.assertEqual("poll", events[0][0])
        self.assertFalse(any(event[0] == "spawn" for event in events))
        self.assertEqual(1, len(callbacks))
        self.assertAlmostEqual(0.10, callbacks[0][0])

    def test_failed_support_gate_never_creates_or_retries_an_entity(self):
        namespace, _player, bootstrap, events, callbacks, jobs = (
            self.placement_namespace("failed")
        )
        begin = execute_function(
            extract_nested_function("_begin_bot_placement"), namespace
        )

        begin(jobs)

        self.assertEqual([(100.0, 0)], bootstrap.polls)
        self.assertFalse(any(event[0] == "spawn" for event in events))
        self.assertEqual([], callbacks)
        self.assertTrue(any(event[0] == "error" for event in events))

    def test_ready_gate_is_polled_before_spawn_and_starts_activation_monitor(self):
        namespace, player, bootstrap, events, callbacks, jobs = (
            self.placement_namespace("placement_ready")
        )
        begin = execute_function(
            extract_nested_function("_begin_bot_placement"), namespace
        )

        begin(jobs)

        self.assertEqual([(100.0, 0)], bootstrap.polls)
        self.assertEqual(["poll", "spawn", "schedule"], [
            event[0] for event in events
        ])
        self.assertTrue(player._offh_spawn_streaming_monitor_active)
        self.assertEqual(1, len(callbacks))
        self.assertAlmostEqual(0.10, callbacks[0][0])

    def spawn_namespace(self, role):
        spawns = []
        callbacks = []
        player = types.SimpleNamespace(
            _offh_lineup_model_refs={},
            _offh_spawn_batch_started_at=100.0,
            _offh_auto_spawn_expected=1,
        )
        bigworld = types.SimpleNamespace(
            player=lambda: player,
            wg_collideSegment=lambda *_args: None,
        )

        class FakeSpawnEvent(object):
            def __init__(self, key):
                self.key = key

        namespace = {
            "g_offh_battle_gen": 8,
            "_offh_my_gen": [8],
            "_battle_finished": [False],
            "BigWorld": bigworld,
            "Math": types.SimpleNamespace(Vector3=Vector3),
            "Keys": types.SimpleNamespace(KEY_P=80),
            "_FakeSpawnEvent": FakeSpawnEvent,
            "_offh_bspace": lambda: 1,
            "_offh_network_bot_role": lambda _player: role,
            "_mock_handleKeyEvent": lambda _event: spawns.append(
                player._forced_spawn_pos
            ),
            "_offh_battle_callback": lambda delay, callback: callbacks.append(
                (float(delay), callback)
            ),
            "time": types.SimpleNamespace(time=lambda: 101.0),
            "veh_pos": (0.0, 50.0, 0.0),
            "LOG_DEBUG": lambda *_args: None,
        }
        return namespace, player, spawns, callbacks

    def test_authority_never_uses_baked_y_when_live_collision_is_missing(self):
        spawn_source = extract_nested_function("_spawn_next")
        lineup_job = job(1, 7, 8, 300.0, 53.844, 400.0)

        for role in ("local", "authority"):
            with self.subTest(role=role):
                namespace, _player, spawns, callbacks = self.spawn_namespace(role)
                spawn_next = execute_function(spawn_source, namespace)

                spawn_next([lineup_job])

                self.assertEqual([], spawns)
                self.assertEqual(1, len(callbacks))
                self.assertAlmostEqual(0.25, callbacks[0][0])

    def test_replica_can_use_manifest_y_for_presentation_without_projection(self):
        namespace, _player, spawns, callbacks = self.spawn_namespace("replica")
        spawn_next = execute_function(
            extract_nested_function("_spawn_next"), namespace
        )
        lineup_job = job(2, 4, 5, 362.0, 55.730, 410.0)

        spawn_next([lineup_job])

        self.assertEqual([(362.0, 55.730, 410.0)], spawns)
        self.assertEqual([], callbacks)

    def test_unknown_or_handoff_role_never_uses_baked_y_to_create_an_entity(self):
        spawn_source = extract_nested_function("_spawn_next")
        lineup_job = job(1, 0, 1, 300.0, 50.0, 400.0)

        for role in ("unknown", "handoff"):
            with self.subTest(role=role):
                namespace, _player, spawns, _callbacks = self.spawn_namespace(role)
                execute_function(spawn_source, namespace)([lineup_job])
                self.assertEqual([], spawns)

    def install_native_manager(self):
        gui = types.ModuleType("gui")
        mods = types.ModuleType("gui.mods")
        offhangar = types.ModuleType("gui.mods.offhangar")
        native = types.ModuleType("gui.mods.offhangar.native_bot_physics")
        native.is_active = lambda mock: bool(getattr(mock, "active", False))
        offhangar.native_bot_physics = native
        self.install_modules({
            "gui": gui,
            "gui.mods": mods,
            "gui.mods.offhangar": offhangar,
            native.__name__: native,
        })

    def install_release_manager(self, manager):
        gui = types.ModuleType("gui")
        mods = types.ModuleType("gui.mods")
        offhangar = types.ModuleType("gui.mods.offhangar")
        offhangar.native_bot_physics = manager
        self.install_modules({
            "gui": gui,
            "gui.mods": mods,
            "gui.mods.offhangar": offhangar,
            manager.__name__: manager,
        })

    def test_replica_demotion_stops_streaming_only_after_every_owner_releases(self):
        events = []
        bootstrap = FakeBootstrap((), [])

        def record_bootstrap_stop():
            events.append(("bootstrap_stop",))
            bootstrap.stops += 1
            return True

        bootstrap.stop = record_bootstrap_stop
        player = types.SimpleNamespace(
            _offh_spawn_streaming_bootstrap=bootstrap,
            _offh_spawn_streaming_monitor_active=True,
        )
        first = types.SimpleNamespace(
            id=1001,
            _network_shared_bot=True,
            _offh_attach_bot_fashion=lambda: events.append(("refresh", 1001)),
        )
        second = types.SimpleNamespace(
            id=1002,
            _network_shared_bot=True,
            _offh_attach_bot_fashion=lambda: events.append(("refresh", 1002)),
        )
        manager = types.ModuleType("gui.mods.offhangar.native_bot_physics")
        manager.claims_movement = lambda _mock: True
        manager.is_prepared = lambda _mock: True

        def stop_mock(mock, restore_filter):
            events.append(("owner_stop", mock.id, bool(restore_filter)))
            return True

        manager.stop_mock = stop_mock
        self.install_release_manager(manager)
        namespace = {
            "G_MOCK_VEHICLES": {1002: second, 1001: first},
        }
        release = execute_function(
            extract_nested_function("release_native_bots_for_replica"),
            namespace,
        )

        self.assertTrue(release(player))

        self.assertEqual([
            ("owner_stop", 1001, True),
            ("owner_stop", 1002, True),
        ], [event for event in events if event[0] == "owner_stop"])
        self.assertGreater(
            events.index(("bootstrap_stop",)),
            events.index(("owner_stop", 1002, True)),
        )
        self.assertEqual(1, bootstrap.stops)
        self.assertIsNone(getattr(
            player, "_offh_spawn_streaming_bootstrap", None
        ))
        self.assertFalse(getattr(
            player, "_offh_spawn_streaming_monitor_active", False
        ))

    def test_replica_demotion_keeps_bootstrap_when_restore_is_not_confirmed(self):
        bootstrap = FakeBootstrap((), [])

        def rejected_restore():
            bootstrap.stops += 1
            return False

        bootstrap.stop = rejected_restore
        player = types.SimpleNamespace(
            _offh_spawn_streaming_bootstrap=bootstrap,
            _offh_spawn_streaming_monitor_active=True,
        )
        manager = types.ModuleType("gui.mods.offhangar.native_bot_physics")
        manager.claims_movement = lambda _mock: False
        manager.is_prepared = lambda _mock: False
        manager.stop_mock = lambda _mock, _restore_filter: True
        self.install_release_manager(manager)
        release = execute_function(
            extract_nested_function("release_native_bots_for_replica"),
            {"G_MOCK_VEHICLES": {}},
        )

        self.assertFalse(release(player))
        self.assertEqual(1, bootstrap.stops)
        self.assertIs(bootstrap, player._offh_spawn_streaming_bootstrap)
        self.assertTrue(player._offh_spawn_streaming_monitor_active)
        self.assertFalse(getattr(
            player, "_offhangar_native_replica_released", False
        ))

    def test_failed_replica_demotion_keeps_streaming_for_remaining_owner(self):
        events = []
        bootstrap = FakeBootstrap((), [])
        original_stop = bootstrap.stop

        def record_bootstrap_stop():
            events.append(("bootstrap_stop",))
            original_stop()

        bootstrap.stop = record_bootstrap_stop
        player = types.SimpleNamespace(
            _offh_spawn_streaming_bootstrap=bootstrap,
            _offh_spawn_streaming_monitor_active=True,
        )
        first = types.SimpleNamespace(
            id=1001,
            _network_shared_bot=True,
            _offh_attach_bot_fashion=lambda: events.append(("refresh", 1001)),
        )
        blocked = types.SimpleNamespace(
            id=1002,
            _network_shared_bot=True,
            _offh_attach_bot_fashion=lambda: events.append(("refresh", 1002)),
        )
        manager = types.ModuleType("gui.mods.offhangar.native_bot_physics")
        manager.claims_movement = lambda _mock: True
        manager.is_prepared = lambda _mock: True

        def stop_mock(mock, restore_filter):
            events.append(("owner_stop", mock.id, bool(restore_filter)))
            return mock is not blocked

        manager.stop_mock = stop_mock
        self.install_release_manager(manager)
        namespace = {
            "G_MOCK_VEHICLES": {1002: blocked, 1001: first},
        }
        release = execute_function(
            extract_nested_function("release_native_bots_for_replica"),
            namespace,
        )

        self.assertFalse(release(player))

        self.assertEqual([
            ("owner_stop", 1001, True),
            ("owner_stop", 1002, True),
        ], events)
        self.assertEqual(0, bootstrap.stops)
        self.assertIs(
            bootstrap, player._offh_spawn_streaming_bootstrap
        )
        self.assertTrue(player._offh_spawn_streaming_monitor_active)

    def test_activation_poll_counts_only_exact_frozen_lineup_members(self):
        self.install_native_manager()
        jobs = (
            job(1, 0, None),
            job(2, 0, None),
        )
        bootstrap = FakeBootstrap(jobs, [])
        player = types.SimpleNamespace(
            _offh_spawn_streaming_bootstrap=bootstrap,
            _offh_spawn_streaming_monitor_active=True,
        )
        callbacks = []
        current_active = types.SimpleNamespace(
            _bot_team=1, _network_bot_slot=0, _network_bot_id=None,
            active=True,
        )
        current_waiting = types.SimpleNamespace(
            _bot_team=2, _network_bot_slot=0, _network_bot_id=None,
            active=False,
        )
        unrelated_active = types.SimpleNamespace(
            _bot_team=1, _network_bot_slot=99, _network_bot_id=None,
            active=True,
        )
        class PlayerProxy(object):
            def __getattr__(self, _name):
                return None

        player_mock = PlayerProxy()
        namespace = {
            "g_offh_battle_gen": 9,
            "_offh_my_gen": [9],
            "BigWorld": types.SimpleNamespace(player=lambda: player),
            "G_MOCK_VEHICLES": {
                250: player_mock,
                1000: current_active,
                1001: current_waiting,
                1999: unrelated_active,
            },
            "time": types.SimpleNamespace(time=lambda: 200.0),
            "_offh_battle_callback": lambda delay, callback: callbacks.append(
                (float(delay), callback)
            ),
            "LOG_ERROR": lambda *_args: None,
        }
        poll_activation = execute_function(
            extract_nested_function("_poll_spawn_streaming_activation"),
            namespace,
        )

        poll_activation()

        self.assertEqual([(200.0, 1)], bootstrap.polls)
        self.assertTrue(player._offh_spawn_streaming_monitor_active)
        self.assertEqual(1, len(callbacks))
        self.assertAlmostEqual(0.10, callbacks[0][0])

    def test_complete_activation_does_not_stop_the_battle_long_streaming_hold(self):
        self.install_native_manager()
        jobs = (job(1, 0, 1),)
        bootstrap = FakeBootstrap(jobs, ["complete"])
        player = types.SimpleNamespace(
            _offh_spawn_streaming_bootstrap=bootstrap,
            _offh_spawn_streaming_monitor_active=True,
        )
        mock = types.SimpleNamespace(
            _bot_team=1, _network_bot_slot=0, _network_bot_id=1,
            active=True,
        )
        namespace = {
            "g_offh_battle_gen": 10,
            "_offh_my_gen": [10],
            "BigWorld": types.SimpleNamespace(player=lambda: player),
            "G_MOCK_VEHICLES": {1000: mock},
            "time": types.SimpleNamespace(time=lambda: 300.0),
            "_offh_battle_callback": lambda *_args: self.fail(
                "complete activation must not keep polling"
            ),
            "LOG_ERROR": lambda *_args: None,
        }
        poll_activation = execute_function(
            extract_nested_function("_poll_spawn_streaming_activation"),
            namespace,
        )

        poll_activation()

        self.assertEqual([(300.0, 1)], bootstrap.polls)
        self.assertEqual(0, bootstrap.stops)
        self.assertFalse(player._offh_spawn_streaming_monitor_active)

    def test_sweep_stops_streaming_and_restore_tracks_all_player_attributes(self):
        source = BATTLE.read_text()
        sweep_start = source.index("def _offh_battle_sweep(")
        sweep_end = source.index("\nimport BigWorld", sweep_start)
        sweep = source[sweep_start:sweep_end]
        attrs_start = source.index("_OFFH_PLAYER_BATTLE_ATTRS = (")
        attrs_end = source.index("\n\n\ndef _offh_capture_player_battle_attrs", attrs_start)
        attrs = source[attrs_start:attrs_end]

        self.assertIn("_streaming_bootstrap.stop()", sweep)
        self.assertLess(
            sweep.index("_stop_native_bot_physics(_mvd)"),
            sweep.index("_streaming_bootstrap.stop()"),
        )
        self.assertLess(
            sweep.index("_streaming_bootstrap.stop()"),
            sweep.index("BigWorld.delSpaceGeometryMapping"),
        )
        self.assertLess(
            sweep.index("_streaming_bootstrap.stop()"),
            sweep.index("BigWorld.clearSpace"),
        )
        for name in (
            "_offh_spawn_streaming_bootstrap",
            "_offh_spawn_streaming_monitor_active",
            "_offh_spawn_streaming_wait_logged",
        ):
            self.assertIn("'%s'" % name, attrs)


if __name__ == "__main__":
    unittest.main()
