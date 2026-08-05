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

    class MouseCursor:
        def __init__(self):
            self._visible = False

        @property
        def visible(self):
            return self._visible

        @visible.setter
        def visible(self, value):
            self._visible = bool(value)
            cursor_calls.append(bool(value))

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
    bigworld = types.ModuleType("BigWorld")
    bigworld.player = lambda: player
    bigworld.callback = lambda delay, callback: None
    bigworld.setCursor = lambda cursor: None
    bigworld.dcursor = lambda: object()
    sys.modules["BigWorld"] = bigworld

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

    cursor = types.ModuleType("gui.Cursor")
    cursor.showCursor = lambda visible: None
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
    return module, config.CONFIG_OPTIONS, cursor_calls, notices, roots


class LANSettingsTest(unittest.TestCase):
    def setUp(self):
        (
            self.settings,
            self.config,
            self.cursor_calls,
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
        self.assertFalse(self.settings._panel.focus)
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
        self.assertTrue(all(not control.visible for control in self.settings._controls.values()))
        self.assertTrue(all(not label.visible for label in self.settings._labels.values()))

    def test_garage_entry_shows_a_native_cursor_only_while_hovered(self):
        self.assertTrue(self.settings._make_entry())
        script = self.settings._entry_panel.script

        script.handleMouseEnterEvent(self.settings._entry_panel)
        self.assertEqual([True], self.cursor_calls)

        script.handleMouseLeaveEvent(self.settings._entry_panel)
        self.assertEqual([True, False], self.cursor_calls)

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


if __name__ == "__main__":
    unittest.main()
