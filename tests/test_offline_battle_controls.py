import re
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"


class OfflineBattleControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_stock_cruise_modes_drive_throttle_and_damage_panel(self):
        source = self.source
        helper_start = source.index("def _set_cruise_mode(mode):")
        helper_end = source.index("def _play_autoaim_sound", helper_start)
        helpers = source[helper_start:helper_end]
        movement_start = source.index("# --- WoT-style Hull Physics ---")
        movement_end = source.index("# LAN MVP:", movement_start)
        movement = source[movement_start:movement_end]

        self.assertIn("CMD_INCREMENT_CRUISE_MODE", helpers)
        self.assertIn("CMD_DECREMENT_CRUISE_MODE", helpers)
        self.assertIn("_now - _last_time < 0.35", helpers)
        self.assertIn("_mode = 3 if _double_press", helpers)
        self.assertIn("_mode = -2 if _double_press", helpers)
        self.assertIn("_panel.setCruiseMode(mode)", helpers)
        self.assertIn("1: 0.25, 2: 0.50, 3: 1.0", movement)
        self.assertIn("-1: -0.50, -2: -1.0", movement)
        self.assertEqual(1, movement.count("_set_cruise_mode(0)"))
        self.assertIn("A manual movement key-down cancels cruise", movement)

    def test_cruise_key_can_be_armed_while_manual_move_is_held(self):
        source = self.source
        helper_start = source.index("def _handle_cruise_key(event):")
        helper_end = source.index("def _play_autoaim_sound", helper_start)
        helpers = source[helper_start:helper_end]
        movement_start = source.index("# --- WoT-style Hull Physics ---")
        movement_end = source.index("# LAN MVP:", movement_start)
        movement = source[movement_start:movement_end]

        self.assertIn("if _is_manual and event.isKeyDown():", helpers)
        self.assertIn("_set_cruise_mode(0)", helpers)
        self.assertIn("if _manual_forward:", movement)
        self.assertNotIn("if _manual_forward or _manual_backward:", movement)

    def test_steering_uses_drive_intent_instead_of_signed_velocity(self):
        source = self.source
        physics_start = source.index("# --- WoT-style Hull Physics ---")
        physics_end = source.index("# --- Ground contact:", physics_start)
        movement = source[physics_start:physics_end]

        self.assertIn("drive_intent=throttle", movement)
        self.assertNotIn("drive_intent=_veh_velocity[0]", movement)

    def test_mock_vehicle_yaw_is_ready_before_stock_camera_creation(self):
        source = self.source
        matrix_start = source.index("veh_matrix_static = Math.Matrix()")
        input_start = source.index("g_offline_aih = AvatarInputHandler.AvatarInputHandler()")
        startup = source[matrix_start:input_start]

        self.assertIn("veh_matrix_static.setRotateY(spawn_dir.z)", startup)
        self.assertIn("self.matrix.setRotateY(spawn_dir.z)", startup)
        self.assertIn("self.yaw = spawn_dir.z", startup)

    def test_bot_model_removes_entity_default_motor_before_pose_servo(self):
        source = self.source
        helper_start = source.index("def _offh_assign_entity_model_root")
        helper_end = source.index("\n\ndef _offh_cursor_shown", helper_start)
        namespace = {}
        exec(source[helper_start:helper_end], namespace)

        events = []
        default_motor = object()

        class Model(object):
            def __init__(self):
                self.motors = []

            def delMotor(self, motor):
                events.append(("delete", motor))
                self.motors.remove(motor)

        class Entity(object):
            @property
            def model(self):
                return self._model

            @model.setter
            def model(self, model):
                events.append(("assign", model))
                self._model = model
                model.motors.insert(0, default_motor)

        entity = Entity()
        model = Model()
        namespace["_offh_assign_entity_model_root"](entity, model)

        self.assertEqual([
            ("assign", model), ("delete", default_motor)
        ], events)
        self.assertEqual([], model.motors)

        # A transient delMotor failure retries the same captured default motor. It
        # must not assign the entity model twice and create a second default owner.
        events[:] = []
        retries = {"count": 0}

        class RetryModel(Model):
            def delMotor(self, motor):
                events.append(("delete", motor))
                retries["count"] += 1
                if retries["count"] == 1:
                    raise RuntimeError("not ready")
                self.motors.remove(motor)

        retry_entity = Entity()
        retry_model = RetryModel()
        handoff = {}
        self.assertFalse(namespace["_offh_assign_entity_model_root"](
            retry_entity, retry_model, handoff
        ))
        self.assertTrue(namespace["_offh_assign_entity_model_root"](
            retry_entity, retry_model, handoff
        ))
        self.assertEqual(1, len([
            event for event in events if event[0] == "assign"
        ]))
        self.assertEqual([], retry_model.motors)

        # Some native PyModel shims report success from delMotor without changing
        # the model's owner list. Treat the readback, not the API return, as the
        # ownership contract and leave the handoff retryable.
        events[:] = []

        class SilentNoOpModel(Model):
            def delMotor(self, motor):
                events.append(("delete", motor))

        no_op_entity = Entity()
        no_op_model = SilentNoOpModel()
        no_op_handoff = {}
        self.assertFalse(namespace["_offh_assign_entity_model_root"](
            no_op_entity, no_op_model, no_op_handoff
        ))
        self.assertTrue(no_op_handoff["assigned"])
        self.assertFalse(no_op_handoff.get("complete", False))
        self.assertIs(default_motor, no_op_handoff["default_motor"])
        self.assertEqual([default_motor], no_op_model.motors)

        spawn_start = source.index("def _assign_model_when_ready")
        spawn_end = source.index("elif retries > 0:", spawn_start)
        spawn = source[spawn_start:spawn_end]
        self.assertLess(
            spawn.index("_offh_assign_entity_model_root"),
            spawn.index("_prepare_native_bot_physics"),
        )
        self.assertLess(
            spawn.index("_offh_assign_entity_model_root"),
            spawn.index("_VP.commit_pose"),
        )

    def test_bot_mock_starts_with_model_root_handoff_not_ready(self):
        source = self.source
        create_mock = source.index("e_mock = _MockVeh()")
        create_entity = source.index(
            "_eid = BigWorld.createEntity('OfflineEntity'", create_mock
        )
        assign_callback = source.index(
            "def _assign_model_when_ready", create_entity
        )
        creation = source[create_mock:assign_callback]

        self.assertIn(
            "e_mock._offh_native_model_root_ready = False", creation
        )

    def test_autoaim_uses_stock_aiming_mode_and_notification_events(self):
        source = self.source
        helper_start = source.index("def _play_autoaim_sound(event_name):")
        helper_end = source.index("_orig_handleKeyEvent", helper_start)
        helpers = source[helper_start:helper_end]
        key_start = source.index("if event.key == Keys.KEY_RIGHTMOUSE:", helper_end)
        key_end = source.index("# An OPEN equipment fly-out", key_start)
        key_handler = source[key_start:key_end]

        self.assertIn("IngameSoundNotifications", helpers)
        self.assertIn("_notifications.play(event_name)", helpers)
        self.assertIn("_AutoAimMode.TARGET_LOCK", helpers)
        self.assertIn("target_captured", helpers)
        self.assertIn("target_unlocked", helpers)
        self.assertIn("_set_autoaim_target(curr_target)", key_handler)

    def test_mouse_wheel_defaults_to_up_for_zooming_in(self):
        source = self.source
        mouse_start = source.index("def _offh_mouse_delta(args):")
        mouse_end = source.index("# Route the flash cursor callback", mouse_start)
        mouse = source[mouse_start:mouse_end]

        self.assertIn("def _offh_zoom_wheel_delta(dz):", mouse)
        self.assertIn("return float(dz)", mouse)
        self.assertIn("orig_game_convertMouseEvent = game.convertMouseEvent", mouse)
        self.assertIn("game.convertMouseEvent = _offh_convertMouseEvent", mouse)
        self.assertIn("dz = _offh_zoom_wheel_delta(dz)", mouse)
        self.assertIn(
            "dz = _offh_zoom_wheel_delta(getattr(event, 'dz', 0.0))",
            mouse,
        )

    def test_eaten_arcade_wheel_reenters_control_fixed_point_zoom(self):
        source = self.source
        player_handler_start = source.index("def _mock_handleMouse(*args):")
        player_handler_end = source.index(
            "player.handleMouseEvent = _mock_handleMouse", player_handler_start
        )
        player_handler = source[player_handler_start:player_handler_end]
        # Reaching the player does not mean the AIH accepted the event. A false
        # result is the started/detach gate and must not fall through to direct
        # camera mutation; only the outer handler may redeliver an event which
        # never reached the player at all.
        self.assertNotIn("_offh_mouse_cam_fallback", player_handler)
        self.assertNotIn("_offh_wheel_delivery_serial", player_handler)

        # Retail game.py delivers ordinary mouse input straight to the current
        # AvatarInputHandler instance. Account.handleMouseEvent is not on that
        # path, so the exact-once signal must wrap this AIH instance itself.
        delivery_assignment = re.search(
            r"(?:g_offline_aih|aih)\.handleMouseEvent\s*=\s*(\w+)",
            source,
        )
        self.assertIsNotNone(delivery_assignment)
        delivery_name = delivery_assignment.group(1)
        delivery_start = source.rfind(
            "def %s(" % delivery_name, 0, delivery_assignment.start()
        )
        self.assertGreaterEqual(delivery_start, 0)
        delivery = source[delivery_start:delivery_assignment.end()]
        self.assertIn("_offh_wheel_delivery_serial", delivery)
        self.assertIn("abs(", delivery)

        handler_start = source.index("def _mock_game_handleMouseEvent(event):")
        handler_end = source.index(
            "# Route the flash cursor callback", handler_start
        )
        handler = source[handler_start:handler_end]

        # ArcadeControlMode owns _TargetPointCalculator.__fixedPointZoom: it
        # captures the world point under the reticle before moving the camera.
        # Calling ArcadeCamera.update directly changes distance without that
        # correction, so a wheel-only input drifts off the original target.
        fallback_start = handler.index("if pre is not None:")
        fallback = handler[fallback_start:]
        self.assertIn("aih.handleMouseEvent(0.0, 0.0, dzc)", fallback)
        self.assertNotIn("cam.update(0, 0, dzc)", fallback)
        self.assertNotIn("_SniperCamera__dxdydz", fallback)
        self.assertNotIn("float(stored[0])", fallback)

    def test_arcade_reticle_centering_updates_aim_and_camera_together(self):
        source = self.source
        helper_start = source.index("def _offh_center_arcade_aim(ctrl):")
        helper_end = source.index("\n\t\tdef ", helper_start + 1)
        namespace = {}
        exec(textwrap.dedent(source[helper_start:helper_end]), namespace)
        center = namespace["_offh_center_arcade_aim"]

        class Aim(object):
            def __init__(self):
                self.value = (0.0, 0.15)

            def offset(self, value=None):
                if value is not None:
                    self.value = tuple(value)
                return self.value

        class Camera(object):
            def __init__(self):
                self.value = (0.0, 0.15)

            def cursorOffset(self, value):
                self.value = tuple(value)

        class ArcadeControlMode(object):
            def __init__(self):
                self.aim = Aim()
                self.camera = Camera()

            def getAim(self):
                return self.aim

        class SniperControlMode(ArcadeControlMode):
            pass

        class StrategicControlMode(ArcadeControlMode):
            pass

        arcade = ArcadeControlMode()
        sniper = SniperControlMode()
        strategic = StrategicControlMode()

        self.assertTrue(center(arcade))
        self.assertEqual((0.0, 0.0), arcade.aim.value)
        self.assertEqual((0.0, 0.0), arcade.camera.value)

        self.assertFalse(center(sniper))
        self.assertFalse(center(strategic))
        self.assertEqual((0.0, 0.15), sniper.aim.value)
        self.assertEqual((0.0, 0.15), sniper.camera.value)
        self.assertEqual((0.0, 0.15), strategic.aim.value)
        self.assertEqual((0.0, 0.15), strategic.camera.value)

    def test_arcade_reticle_is_centered_after_create_before_enable(self):
        source = self.source
        startup_start = source.index(
            "for control in g_offline_aih._AvatarInputHandler__ctrls.itervalues():"
        )
        arcade_enable = source.index(
            "g_offline_aih.onControlModeChanged('arcade')", startup_start
        )
        startup = source[startup_start:arcade_enable]

        create = startup.index("control.create()")
        center = startup.index("_offh_center_arcade_aim(control)")
        self.assertLess(create, center)

        # The centering helper is intentionally an arcade presentation policy.
        # Ballistic targeting still consumes the stock desired world point, and
        # sniper/strategic controls retain their native offsets and projections.
        aim_start = source.index("# 2. Get exact 3D point the crosshair is looking at")
        aim_end = source.index("# 3. Calculate target yaw and pitch", aim_start)
        aim = source[aim_start:aim_end]
        self.assertIn("shot_point = aih.getDesiredShotPoint()", aim)
        self.assertNotIn("shot_point.y =", aim)
        self.assertNotIn("shot_point.y +=", aim)
        self.assertNotIn("gun_target_pos", startup)

    def test_wheel_delivery_serial_counts_the_actual_aih_entrypoint(self):
        source = self.source
        self.assertTrue(
            "def _offh_wrap_aih_mouse_delivery(aih, wheel_player):" in source,
            "the delivery serial must wrap game.py's direct AIH entrypoint",
        )
        helper_start = source.index(
            "def _offh_wrap_aih_mouse_delivery(aih, wheel_player):"
        )
        helper_end = source.index("\n\t\tdef ", helper_start + 1)
        namespace = {}
        exec(textwrap.dedent(source[helper_start:helper_end]), namespace)
        wrap = namespace["_offh_wrap_aih_mouse_delivery"]

        calls = []

        class InputHandler(object):
            def handleMouseEvent(self, dx, dy, dz):
                calls.append((dx, dy, dz))
                return "stock-result"

        class Player(object):
            _offh_wheel_delivery_serial = 0

        aih = InputHandler()
        player = Player()
        wrap(aih, player)

        self.assertEqual("stock-result", aih.handleMouseEvent(2.0, 3.0, 0.0))
        self.assertEqual(0, player._offh_wheel_delivery_serial)
        self.assertEqual("stock-result", aih.handleMouseEvent(4.0, 5.0, 1.0))
        self.assertEqual(1, player._offh_wheel_delivery_serial)
        self.assertEqual([(2.0, 3.0, 0.0), (4.0, 5.0, 1.0)], calls)

        # Installing the guard twice must not nest counters around one stock call.
        wrap(aih, player)
        aih.handleMouseEvent(6.0, 7.0, -1.0)
        self.assertEqual(2, player._offh_wheel_delivery_serial)
        self.assertEqual((6.0, 7.0, -1.0), calls[-1])

        create_start = source.index(
            "g_offline_aih = AvatarInputHandler.AvatarInputHandler()"
        )
        assign_end = source.index("player.inputHandler = g_offline_aih", create_start)
        install = source[create_start:assign_end]
        self.assertIn(
            "_offh_wrap_aih_mouse_delivery(g_offline_aih, player)", install
        )

    def test_outer_wheel_fallback_delivers_exactly_once_through_aih(self):
        source = self.source
        self.assertTrue(
            "def _offh_wrap_aih_mouse_delivery(aih, wheel_player):" in source,
            "outer fallback needs an AIH-level delivery serial",
        )
        helper_start = source.index(
            "def _offh_wrap_aih_mouse_delivery(aih, wheel_player):"
        )
        helper_end = source.index("\n\t\tdef ", helper_start + 1)
        handler_start = source.index("def _mock_game_handleMouseEvent(event):")
        handler_end = source.index(
            "\n\t\t\tgame.handleMouseEvent = _mock_game_handleMouseEvent",
            handler_start,
        )

        class Event(object):
            dz = 1.0

        class Camera(object):
            _SniperCamera__dxdydz = (9.0, -7.0, 0.0)

            def update(self, *args):
                raise AssertionError("wheel fallback must not mutate camera directly")

        class Control(object):
            def __init__(self):
                self.camera = Camera()

        class Player(object):
            isOffline = True
            _is_dead = False
            _offh_spectating = False
            _offh_wheel_delivery_serial = 0

        class BigWorldStub(object):
            current_player = None

            @classmethod
            def player(cls):
                return cls.current_player

        namespace = {
            "BigWorld": BigWorldStub,
            "_offh_zoom_wheel_delta": float,
            "orig_game_handleMouseEvent": lambda event: True,
        }
        exec(textwrap.dedent(source[helper_start:helper_end]), namespace)
        exec(textwrap.dedent(source[handler_start:handler_end]), namespace)
        wrap = namespace["_offh_wrap_aih_mouse_delivery"]
        outer = namespace["_mock_game_handleMouseEvent"]

        def make_started_handler(result=True):
            deliveries = []

            class InputHandler(object):
                _AvatarInputHandler__isStarted = True
                _AvatarInputHandler__detachCount = 0

                def __init__(self):
                    self.ctrl = Control()

                def handleMouseEvent(self, dx, dy, dz):
                    deliveries.append((dx, dy, dz))
                    return result

            player = Player()
            player.inputHandler = InputHandler()
            wrap(player.inputHandler, player)
            BigWorldStub.current_player = player
            return player, deliveries

        # Stock game.py calls player.inputHandler directly after GUI declines it.
        # That first delivery must advance the same serial observed by the outer
        # wrapper, otherwise the outer wrapper sends the wheel a second time.
        player, deliveries = make_started_handler()
        namespace["orig_game_handleMouseEvent"] = lambda event: (
            player.inputHandler.handleMouseEvent(0.25, -0.5, event.dz)
        )
        self.assertTrue(outer(Event()))
        self.assertEqual([(0.25, -0.5, 1.0)], deliveries)
        self.assertEqual(1, player._offh_wheel_delivery_serial)

        # If Flash consumes the event before stock game reaches the AIH, the outer
        # wrapper supplies one missing delivery through the same guarded AIH path.
        # A false result is an AIH-level gate, not permission to touch the camera.
        player, deliveries = make_started_handler(False)
        namespace["orig_game_handleMouseEvent"] = lambda event: True
        self.assertTrue(outer(Event()))
        self.assertEqual([(0.0, 0.0, 1.0)], deliveries)
        self.assertEqual(1, player._offh_wheel_delivery_serial)

        # A stopped AIH remains stopped: fallback detection must not bypass its
        # lifecycle gate or reach a camera object directly.
        player, deliveries = make_started_handler()
        player.inputHandler._AvatarInputHandler__isStarted = False
        namespace["orig_game_handleMouseEvent"] = lambda event: True
        self.assertTrue(outer(Event()))
        self.assertEqual([], deliveries)
        self.assertEqual(0, player._offh_wheel_delivery_serial)

    def test_direct_fire_marker_uses_the_live_projectile_collision_contract(self):
        source = self.source

        # The marker is a preview of the projectile, not a ray projected from
        # the elevated barrel. Keep the chord collision primitive shared so
        # gravity, muzzle-to-world collision and nearest-hit ordering cannot
        # drift apart from the shell that is launched on click.
        self.assertIn("def _offh_projectile_chord_impact(", source)
        self.assertIn("def _offh_player_gun_marker_impact(", source)

        chord_start = source.index("def _offh_projectile_chord_impact(")
        chord_end = source.index("\ndef ", chord_start + 1)
        chord = source[chord_start:chord_end]
        self.assertIn("_offh_live_projectile_world_hit(", chord)
        self.assertIn("collideSegment(", chord)

        live_start = source.index("def _offh_live_projectile_advance(")
        live_end = source.index("\ndef ", live_start + 1)
        live = source[live_start:live_end]
        self.assertIn("_offh_projectile_chord_impact(", live)

        preview_start = source.index("def _offh_player_gun_marker_impact(")
        preview_end = source.index("\ndef ", preview_start + 1)
        preview = source[preview_start:preview_end]
        self.assertIn("from gui.mods.offhangar import projectile_runtime", preview)
        self.assertIn("trajectory_position(", preview)
        self.assertIn("substep_boundaries(", preview)
        self.assertIn("_offh_projectile_chord_impact(", preview)

        marker_start = source.index(
            "# Calculate perfectly synchronous math_gun_world for raycast"
        )
        marker_end = source.index("# UPDATE CROSSHAIR", marker_start)
        marker = source[marker_start:marker_end]
        self.assertIn("_offh_player_gun_marker_impact(", marker)
        self.assertNotIn(
            "_end_gun = math_gun_world + gun_dir.scale(10000.0)", marker
        )
        self.assertNotIn("BigWorld.wg_collideSegment(", marker)
        self.assertLess(
            marker.index(
                "player.vehicleTypeDescriptor.activeGunShotIndex = "
                "_marker_index"
            ),
            marker.index(
                "player.gunRotator._VehicleGunRotator__getCurShotPosition()"
            ),
        )

        update_start = source.index("# UPDATE CROSSHAIR", marker_end)
        update_end = source.index("# Synchronize ammo UI", update_start)
        update = source[update_start:update_end]
        # The AIH public entry updates both the Flash gun marker and the central
        # Aim marker position. Calling the current control directly skips the
        # latter and can leave the visible centre behind the ballistic preview.
        self.assertIn("g_offline_aih.updateGunMarker(", update)
        self.assertNotIn("g_offline_aih.ctrl.updateGunMarker(", update)

    def test_fake_rotator_starts_with_the_stock_marker_info_shape(self):
        source = self.source
        rotator_start = source.index("class FakeGunRotator(object):")
        rotator_end = source.index("player.gunRotator = FakeGunRotator()")
        rotator = source[rotator_start:rotator_end]

        # The retail rotator exposes (position, direction, size). StrategicAim
        # reads the position and control-mode state transfer preserves the full
        # marker, so the offline stub must not start as a two-element list.
        self.assertIn("self.markerInfo = (", rotator)
        marker_assignment = rotator[rotator.index("self.markerInfo = (") :]
        marker_assignment = marker_assignment[: marker_assignment.index("\n")]
        self.assertIn("Math.Vector3(0.0, 0.0, 0.0)", marker_assignment)
        self.assertIn("Math.Vector3(0.0, 1.0, 0.0)", marker_assignment)
        self.assertIn("1.0", marker_assignment)

    def test_prebattle_camera_motion_does_not_bloom_the_reticle(self):
        source = self.source
        state_start = source.index("# --- GUN MECHANICS STATE ---")
        state_end = source.index("_engine_state =", state_start)
        state = source[state_start:state_end]
        dispersion_start = source.index(
            "# Real dispersion model (Avatar.getOwnVehicleShotDispersionAngle):"
        )
        dispersion_end = source.index("# 2. Reload logic", dispersion_start)
        dispersion = source[dispersion_start:dispersion_end]
        period_start = source.rindex(
            "_period_g = getattr(getattr(player, 'arena', None), 'period', 3)",
            0,
            dispersion_start,
        )
        period_setup = source[period_start:dispersion_start]
        marker_start = source.index("# UPDATE CROSSHAIR", dispersion_end)
        marker_end = source.index("# Synchronize ammo UI", marker_start)
        marker = source[marker_start:marker_end]

        self.assertIn("'prebattle_marker_seeded': False", state)
        self.assertIn("'marker_in_prebattle': False", state)
        self.assertIn("_in_prebattle_g = _period_g < 3", period_setup)
        self.assertIn(
            "if _period_g == 3 or (_in_prebattle_g and not "
            "_gun_state.get('marker_in_prebattle', False)):",
            period_setup,
        )
        self.assertIn(
            "_gun_state['prebattle_marker_seeded'] = False",
            period_setup,
        )
        self.assertIn(
            "_gun_state['marker_in_prebattle'] = _in_prebattle_g",
            period_setup,
        )
        self.assertIn("if _period_g == 3:", dispersion)
        self.assertIn("_mv = 0.0", dispersion)
        self.assertIn("_rv = 0.0", dispersion)
        self.assertIn("_tv = 0.0", dispersion)
        self.assertEqual(2, dispersion.count("if _period_g == 3:"))
        self.assertIn(
            "_refresh_gun_marker = (_marker_preview_fresh and",
            marker,
        )
        self.assertIn("_period_g == 3 or not _gun_state.get(", marker)
        self.assertIn("'prebattle_marker_seeded', False)))", marker)
        self.assertIn("if _refresh_gun_marker:", marker)
        self.assertEqual(2, marker.count("if _refresh_gun_marker:"))
        self.assertIn("_gun_state['prebattle_marker_seeded'] = True", marker)
        self.assertIn(
            "dist_m = (gun_target_pos - _marker_distance_origin).length",
            marker,
        )
        self.assertIn(
            "size_m = _gun_state['dispersion'] * dist_m * 2.0",
            marker,
        )


if __name__ == "__main__":
    unittest.main()
