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
            "_refresh_gun_marker = _period_g == 3 or not "
            "_gun_state.get('prebattle_marker_seeded', False)",
            marker,
        )
        self.assertIn("if _refresh_gun_marker:", marker)
        self.assertEqual(2, marker.count("if _refresh_gun_marker:"))
        self.assertIn("_gun_state['prebattle_marker_seeded'] = True", marker)
        self.assertIn("dist_m = (gun_target_pos - math_gun_world).length", marker)
        self.assertIn(
            "size_m = _gun_state['dispersion'] * dist_m * 2.0",
            marker,
        )


if __name__ == "__main__":
    unittest.main()
