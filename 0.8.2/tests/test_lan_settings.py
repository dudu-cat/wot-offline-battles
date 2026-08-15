import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "scripts/client/gui/mods/offhangar/lan_settings.py"


def load_settings_module():
    roots = []
    cursor_calls = []
    cursor_state = {"refcount": 0, "current": None}

    class MouseCursor:
        def __init__(self):
            self._visible = False

        @property
        def visible(self):
            return self._visible

        @visible.setter
        def visible(self, value):
            self._visible = bool(value)

    class Component:
        def __init__(self, texture=None):
            self.texture = texture
            self.visible = False
            self.children = []
            self.parent = None

        def addChild(self, child):
            child.parent = self
            self.children.append(child)

    gui_engine = types.ModuleType("GUI")
    gui_engine.Simple = Component
    gui_engine.Window = Component
    gui_engine.Text = Component
    gui_engine.addRoot = lambda component: roots.append(component)
    gui_engine.reSort = lambda: None
    mouse_cursor = MouseCursor()
    gui_engine.mcursor = lambda: mouse_cursor
    sys.modules["GUI"] = gui_engine

    player = types.SimpleNamespace(isOffline=True, _offhangar_network_client=None)
    direct_cursor = object()
    bigworld = types.ModuleType("BigWorld")
    bigworld.player = lambda: player
    bigworld.callback = lambda delay, callback: None
    bigworld.setCursor = lambda cursor: cursor_state.__setitem__("current", cursor)
    bigworld.dcursor = lambda: direct_cursor
    sys.modules["BigWorld"] = bigworld
    cursor_state["current"] = mouse_cursor
    cursor_state["mouse"] = mouse_cursor
    cursor_state["direct"] = direct_cursor

    keys = types.ModuleType("Keys")
    keys.KEY_F11 = 87
    keys.KEY_LEFTMOUSE = 256
    keys.KEY_ESCAPE = 1
    keys.KEY_TAB = 15
    keys.KEY_BACKSPACE = 14
    keys.KEY_SPACE = 57
    keys.KEY_RETURN = 28
    keys.KEY_1 = 2
    sys.modules["Keys"] = keys

    for name in ("gui", "gui.mods", "gui.mods.offhangar"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    config = types.ModuleType("gui.mods.offhangar._constants")
    config.CONFIG_OPTIONS = {
        "network_mode": False,
        "network_server_host": "127.0.0.1",
        "network_server_port": 28782,
    }
    sys.modules[config.__name__] = config

    logging = types.ModuleType("gui.mods.offhangar.logging")
    logging.LOG_DEBUG = lambda *args: None
    logging.LOG_NOTE = lambda *args: None
    logging.LOG_ERROR = lambda *args: None
    sys.modules[logging.__name__] = logging

    def show_cursor(visible):
        visible = bool(visible)
        cursor_calls.append(visible)
        cursor_state["refcount"] += 1 if visible else -1
        if visible:
            bigworld.setCursor(mouse_cursor)
            mouse_cursor.visible = True
        elif cursor_state["refcount"] == 0:
            bigworld.setCursor(direct_cursor)
            mouse_cursor.visible = False

    cursor = types.ModuleType("gui.Cursor")
    cursor.showCursor = show_cursor
    sys.modules[cursor.__name__] = cursor

    notices = []
    system_messages = types.ModuleType("gui.SystemMessages")
    system_messages.SM_TYPE = types.SimpleNamespace(
        Error="error", Warning="warning", Information="information"
    )
    system_messages.pushMessage = lambda message, level: notices.append((message, level))
    sys.modules[system_messages.__name__] = system_messages

    spec = importlib.util.spec_from_file_location("lan_settings_under_test", SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._in_battle = lambda: False
    return (
        module,
        config.CONFIG_OPTIONS,
        cursor_calls,
        cursor_state,
        notices,
        roots,
    )


class LANSettingsTest(unittest.TestCase):
    def setUp(self):
        (
            self.settings,
            self.config,
            self.cursor_calls,
            self.cursor_state,
            self.notices,
            self.roots,
        ) = load_settings_module()

    def test_panel_has_clickable_controls_and_owns_a_visible_cursor(self):
        self.assertTrue(self.settings.open())

        self.assertEqual([True], self.cursor_calls)
        self.assertEqual({"host", "port", "mode", "save", "cancel"}, set(self.settings._controls))
        self.assertTrue(all(control.visible for control in self.settings._controls.values()))
        self.assertIn("Click a row", self.settings._labels["help"].text)
        self.assertEqual("PIXEL", self.settings._panel.widthMode)
        self.assertEqual((720, 360), (self.settings._panel.width, self.settings._panel.height))
        self.assertEqual([self.settings._panel], self.roots)
        self.assertTrue(self.settings._panel.focus)
        self.assertTrue(
            all(control.parent is self.settings._panel for control in self.settings._controls.values())
        )
        self.assertTrue(
            all(label.parent is self.settings._panel for label in self.settings._labels.values())
        )
        for role in ("host", "port", "mode", "save", "cancel"):
            self.assertEqual("CENTER", self.settings._controls[role].horizontalAnchor)
            self.assertEqual("CENTER", self.settings._controls[role].verticalAnchor)
            self.assertEqual(
                self.settings._controls[role].position[1],
                self.settings._labels[role].position[1],
            )
        self.assertTrue(
            all(
                not label.focus
                and not label.mouseButtonFocus
                and not label.crossFocus
                and not label.moveFocus
                for label in self.settings._labels.values()
            )
        )

        self.settings.close()

        self.assertEqual([True, False], self.cursor_calls)
        self.assertEqual(0, self.cursor_state["refcount"])
        self.assertIs(self.cursor_state["mouse"], self.cursor_state["current"])
        self.assertFalse(self.cursor_state["mouse"].visible)
        self.assertTrue(all(not control.visible for control in self.settings._controls.values()))
        self.assertTrue(all(not label.visible for label in self.settings._labels.values()))

    def test_garage_entry_shows_a_native_cursor_only_while_hovered(self):
        self.assertTrue(self.settings._make_entry())
        script = self.settings._entry_panel.script

        script.handleMouseEnterEvent(self.settings._entry_panel)
        self.assertEqual([True], self.cursor_calls)

        script.handleMouseLeaveEvent(self.settings._entry_panel)
        self.assertEqual([True, False], self.cursor_calls)
        self.assertIs(self.cursor_state["mouse"], self.cursor_state["current"])
        self.assertFalse(self.cursor_state["mouse"].visible)

    def test_clicking_hovered_entry_transfers_one_cursor_lease_to_panel(self):
        self.assertTrue(self.settings._make_entry())
        script = self.settings._entry_panel.script

        script.handleMouseEnterEvent(self.settings._entry_panel)
        script.handleMouseClickEvent(self.settings._entry_panel)

        self.assertEqual([True], self.cursor_calls)
        self.settings.close()
        self.assertEqual([True, False], self.cursor_calls)
        self.assertIs(self.cursor_state["mouse"], self.cursor_state["current"])
        self.assertFalse(self.cursor_state["mouse"].visible)

    def test_closing_during_battle_does_not_restore_the_lobby_cursor(self):
        self.assertTrue(self.settings.open())
        self.settings._in_battle = lambda: True

        self.settings.close()

        self.assertEqual([True, False], self.cursor_calls)
        self.assertIs(self.cursor_state["direct"], self.cursor_state["current"])
        self.assertFalse(self.cursor_state["mouse"].visible)

    def test_clicking_a_field_makes_the_first_typed_digit_replace_it(self):
        self.settings.open()
        self.settings._controls["host"].script.handleMouseClickEvent(
            self.settings._controls["host"]
        )
        keys = types.SimpleNamespace(KEY_1=101)

        self.assertTrue(self.settings._append_key(101, keys))

        self.assertEqual("1", self.settings._host)
        self.assertFalse(self.settings._replace_on_type)

    def test_mode_button_toggles_and_invalid_ip_uses_error_notification(self):
        self.settings.open()
        self.settings._controls["mode"].script.handleMouseClickEvent(
            self.settings._controls["mode"]
        )
        self.assertTrue(self.settings._mode_enabled)
        self.assertFalse(self.config["network_mode"])

        self.settings._host = "not-an-ip"
        self.assertFalse(self.settings._save())

        message, level = self.notices[-1]
        self.assertIn(b"Invalid IP", message)
        self.assertEqual("error", level)

    def test_active_panel_only_consumes_keyboard_actions_it_handles(self):
        class Event:
            def __init__(self, key, down=True):
                self.key = key
                self.down = down

            def isKeyDown(self):
                return self.down

        self.settings.open()

        # BigWorld reports mouse buttons through the same global key callback.
        # They must continue to GUI.Simple so its click script can run.
        self.assertFalse(self.settings.handle_key_event(Event(256, True)))
        self.assertFalse(self.settings.handle_key_event(Event(256, False)))
        self.assertFalse(self.settings.handle_key_event(Event(9999, True)))

        self.assertTrue(self.settings.handle_key_event(Event(15, True)))
        self.assertEqual(1, self.settings._field)


if __name__ == "__main__":
    unittest.main()
