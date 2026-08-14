import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"
NETWORK = ROOT / "scripts/client/gui/mods/offhangar/network_battle.py"


class Vector3(object):
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


def load_contract():
    source = BATTLE.read_text(encoding="utf-8")
    start = source.index("def _offh_native_mode_enabled():")
    end = source.index("# Temporary low-overhead battle profiler", start)
    namespace = {}
    exec(source[start:end], namespace)
    return namespace


def offhangar_modules(config_options, manager=None, errors=None):
    gui = types.ModuleType("gui")
    mods = types.ModuleType("gui.mods")
    offhangar = types.ModuleType("gui.mods.offhangar")
    constants = types.ModuleType("gui.mods.offhangar._constants")
    logging = types.ModuleType("gui.mods.offhangar.logging")
    constants.CONFIG_OPTIONS = config_options
    error_sink = errors if errors is not None else []
    logging.LOG_ERROR = lambda message: error_sink.append(message)
    gui.mods = mods
    mods.offhangar = offhangar
    offhangar._constants = constants
    offhangar.logging = logging
    modules = {
        "gui": gui,
        "gui.mods": mods,
        "gui.mods.offhangar": offhangar,
        "gui.mods.offhangar._constants": constants,
        "gui.mods.offhangar.logging": logging,
    }
    if manager is not None:
        offhangar.native_bot_physics = manager
        modules["gui.mods.offhangar.native_bot_physics"] = manager
    return modules


def network_client(player_id=7, authority_id=7, phase="battle",
                   running=True, connected=True, ready=True):
    return types.SimpleNamespace(
        player_id=player_id,
        bot_authority_id=authority_id,
        phase=phase,
        running=running,
        connected=connected,
        ready=ready,
    )


class NativeMovementContractTests(unittest.TestCase):
    def test_native_mode_is_latched_per_battle_generation(self):
        contract = load_contract()
        options = {
            "experimental_native_bot_physics": True,
            "network_mode": False,
        }
        with mock.patch.dict(sys.modules, offhangar_modules(options)):
            contract["g_offh_battle_gen"] = 4
            self.assertTrue(contract["_offh_native_mode_enabled"]())
            options["experimental_native_bot_physics"] = False
            self.assertTrue(contract["_offh_native_mode_enabled"]())
            contract["g_offh_battle_gen"] = 5
            self.assertTrue(contract["_offh_native_mode_enabled"]())
            self.assertEqual((5, True), contract["g_offh_native_mode_latch"])

    def test_native_mode_config_error_fails_closed(self):
        class BrokenOptions(object):
            def get(self, name, default=None):
                raise RuntimeError("config unavailable")

        contract = load_contract()
        errors = []
        with mock.patch.dict(
                sys.modules, offhangar_modules(BrokenOptions(), errors=errors)):
            contract["g_offh_battle_gen"] = 9
            self.assertTrue(contract["_offh_native_mode_enabled"]())
            self.assertEqual(0, len(errors))
            self.assertTrue(contract["_offh_native_mode_enabled"]())
            self.assertEqual(0, len(errors))

    def test_network_bot_role_is_fail_closed_until_authority_is_known(self):
        contract = load_contract()
        options = {"network_mode": True}
        role = contract["_offh_network_bot_role"]
        with mock.patch.dict(sys.modules, offhangar_modules(options)):
            player = types.SimpleNamespace()
            self.assertEqual("unknown", role(player))

            player._offhangar_network_client = network_client(ready=False)
            self.assertEqual("unknown", role(player))

            player._offhangar_network_client = network_client(authority_id=8)
            self.assertEqual("replica", role(player))

            player._offhangar_network_client = network_client(authority_id=7)
            player._offhangar_network_authority_demotion_pending = True
            self.assertEqual("unknown", role(player))

            player._offhangar_network_authority_demotion_pending = False
            player._offhangar_network_authority_handoff_pending = True
            self.assertEqual("handoff", role(player))

            player._offhangar_network_authority_handoff_pending = False
            self.assertEqual("authority", role(player))

            player._offhangar_network_fallback_local = True
            self.assertEqual("local", role(player))

        with mock.patch.dict(
                sys.modules, offhangar_modules({"network_mode": False})):
            self.assertEqual("local", role(types.SimpleNamespace()))

    def test_python_motion_requires_native_disabled_and_owned_role(self):
        mock_vehicle = types.SimpleNamespace()
        for enabled in (True, False):
            contract = load_contract()
            options = {
                "experimental_native_bot_physics": enabled,
                "network_mode": False,
            }
            with mock.patch.dict(sys.modules, offhangar_modules(options)):
                allowed = contract["_offh_python_movement_allowed"]
                self.assertFalse(allowed(None, mock_vehicle, "local"))
                self.assertFalse(allowed(None, mock_vehicle, "authority"))
                for role in ("unknown", "replica", "handoff"):
                    self.assertFalse(allowed(None, mock_vehicle, role))

                remote_human = types.SimpleNamespace(
                    _network_remote=True,
                    _network_shared_bot=False,
                )
                self.assertFalse(allowed(None, remote_human, "authority"))

    def test_required_native_pose_is_cached_and_never_reauthorized(self):
        contract = load_contract()
        options = {
            "experimental_native_bot_physics": True,
            "network_mode": False,
        }
        vehicle = types.SimpleNamespace(
            position=Vector3(12, 3, -8),
            yaw=0.4,
            pitch=0.1,
            roll=-0.2,
            _collision_obstacle=object(),
        )
        with mock.patch.dict(sys.modules, offhangar_modules(options)):
            required = contract["_offh_native_movement_required"]
            self.assertTrue(required(None, vehicle, "local"))
            self.assertIsNone(vehicle._collision_obstacle)
            vehicle.position = Vector3(99, 99, 99)
            pose = contract["_offh_native_failed_pose"](vehicle)
            self.assertEqual((12.0, 3.0, -8.0), pose["position"])
            self.assertEqual(0.0, pose["velocity"])
            self.assertEqual(0.0, pose["turn_velocity"])
            self.assertTrue(pose["failed"])

            options["experimental_native_bot_physics"] = False
            self.assertTrue(required(None, vehicle, "replica"))

    def test_spawn_prepares_only_a_required_native_owner(self):
        source = BATTLE.read_text(encoding="utf-8")
        spawn = source.rindex("def _install_live_collision_obstacle")
        end = source.index("elif retries > 0:", spawn)
        block = source[spawn:end]
        required = block.index("_native_body_required =")
        callback = block.index("def _assign_model_when_ready", required)
        gate = block.index("if _native_body_required:", required)
        prepare = block.index("_prepare_native_bot_physics(", gate)
        fallback = block.index(
            "if not _native_body_prepared and not _native_body_required:",
            prepare,
        )

        self.assertLess(required, gate)
        self.assertLess(required, callback)
        self.assertLess(gate, prepare)
        self.assertLess(prepare, fallback)

    def test_required_manager_failure_cannot_enter_legacy_kinematics(self):
        source = BATTLE.read_text(encoding="utf-8")
        start = source.index("_native_body_pose = None")
        end = source.index("_offh_perf_call(\n\t\t\t\t\t\t\t\t'pose_commit'", start)
        movement = source[start:end]
        fail_closed = movement.index(
            "if _native_body_pose is None and _native_movement_required:"
        )
        native = movement.index("if _native_body_pose is not None:", fail_closed)
        legacy = movement.index("elif _python_movement_allowed:", native)
        frozen = movement.index("else:", legacy)

        self.assertIn("_offh_native_failed_pose(m_veh)", movement)
        self.assertIn("continue", movement[fail_closed:native])
        self.assertLess(fail_closed, native)
        self.assertLess(native, legacy)
        self.assertLess(legacy, frozen)

    def test_native_collision_owner_is_independent_of_shared_bot_ai_branch(self):
        source = BATTLE.read_text(encoding="utf-8")
        start = source.index("_native_frame_required =")
        end = source.index("_collision_bodies[_frame_eid]", start)
        traffic = source[start:end]
        shared_ai_gate = traffic.index("_python_frame_allowed and")
        collision_owner = traffic.index("_native_collision_owner = bool(")
        collision_body = traffic.index("'native_owner': _native_collision_owner")

        self.assertLess(shared_ai_gate, collision_owner)
        self.assertLess(collision_owner, collision_body)
        self.assertIn("_offh_native_movement_required(",
                      traffic[collision_owner:collision_body])
        self.assertNotIn("elif", traffic[shared_ai_gate:collision_owner])

    def test_native_pitch_and_roll_bypass_legacy_smoothing(self):
        source = BATTLE.read_text(encoding="utf-8")
        start = source.index(
            "# Native pose is already the canonical rigid-body orientation."
        )
        end = source.index("_offh_perf_call(\n\t\t\t\t\t\t\t\t'pose_commit'", start)
        smoothing = source[start:end]
        native_gate = smoothing.index("if _native_body_pose is not None:")
        legacy_else = smoothing.index("else:", native_gate)
        blend = smoothing.index("_b_blend =", legacy_else)

        self.assertIn("m_veh.pitch = float(_b_ypr[1])",
                      smoothing[native_gate:legacy_else])
        self.assertIn("m_veh.roll = float(_b_ypr[2])",
                      smoothing[native_gate:legacy_else])
        self.assertNotIn("_b_blend", smoothing[native_gate:legacy_else])
        self.assertLess(legacy_else, blend)

    def test_native_pose_updates_mock_matrix_before_authority_publish(self):
        source = BATTLE.read_text(encoding="utf-8")
        native_start = source.index("if _native_body_pose is not None:")
        commit = source.index(
            "_offh_perf_call(\n\t\t\t\t\t\t\t\t'pose_commit'",
            native_start,
        )
        publish = source.index("publish_authoritative_bots", commit)
        native_frame = source[native_start:publish]

        self.assertIn("m_veh.position = Math.Vector3(", native_frame)
        self.assertIn("_VP.commit_pose, m_veh,", native_frame)
        self.assertLess(
            source.index("_VP.commit_pose, m_veh,", native_start),
            publish,
        )

    def test_authority_demotion_releases_native_before_snapshot_apply(self):
        source = NETWORK.read_text(encoding="utf-8")
        setter_start = source.index("\tdef _set_authority(self, authority_id):")
        setter_end = source.index("\n\tdef ", setter_start + 2)
        setter = source[setter_start:setter_end]
        release = setter.index("release_native_bots_for_replica(self.player)")
        publish_role = setter.index("self.bot_authority_id = authority_id")
        self.assertLess(release, publish_role)

        snapshot_start = source.index("\t\telif kind == 'snapshot':")
        snapshot_end = source.index("\n\t\telif kind ==", snapshot_start + 2)
        snapshot = source[snapshot_start:snapshot_end]
        authority = snapshot.index("self._set_authority(")
        apply_snapshot = snapshot.index("_apply_snapshot(self.player, message)")
        self.assertLess(authority, apply_snapshot)
        self.assertIn("is False", snapshot[authority:apply_snapshot])


if __name__ == "__main__":
    unittest.main()
