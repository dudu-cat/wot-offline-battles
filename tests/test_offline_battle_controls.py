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
        self.assertIn("_set_cruise_mode(0)", movement)

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


if __name__ == "__main__":
    unittest.main()
