import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WAITING_PATH = ROOT / "scripts/client/gui/mods/offhangar/lan_waiting_room.py"


def load_waiting_room():
    roots = []

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
    gui_engine.mcursor = lambda: types.SimpleNamespace(visible=False)
    sys.modules["GUI"] = gui_engine

    for name in ("gui", "gui.mods", "gui.mods.offhangar"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    cursor_calls = []
    cursor = types.ModuleType("gui.Cursor")
    cursor.showCursor = lambda visible: cursor_calls.append(visible)
    sys.modules[cursor.__name__] = cursor

    logging = types.ModuleType("gui.mods.offhangar.logging")
    logging.LOG_NOTE = lambda *args: None
    sys.modules[logging.__name__] = logging

    starts = []
    network = types.ModuleType("gui.mods.offhangar.network_battle")
    network.request_battle_start = (
        lambda player, map_name=None: starts.append((player, map_name)) or True
    )
    sys.modules[network.__name__] = network

    spec = importlib.util.spec_from_file_location(
        "gui.mods.offhangar.lan_waiting_room", WAITING_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, starts, cursor_calls, roots


class WaitingRoomUITest(unittest.TestCase):
    def setUp(self):
        self.ui, self.starts, self.cursor_calls, self.roots = load_waiting_room()
        self.client = types.SimpleNamespace(
            ready=True,
            phase="waiting",
            waiting_count=2,
            map_name="04_himmelsdorf",
            available_maps=["04_himmelsdorf", "31_airfield", "06_ensk"],
        )
        self.player = types.SimpleNamespace(_offhangar_network_client=self.client)

    def test_panel_selects_a_map_and_starts_with_mouse_controls(self):
        self.assertTrue(self.ui.open(self.player))
        self.assertEqual(
            {"previous", "map", "next", "start"}, set(self.ui._controls)
        )
        self.assertEqual([True], self.cursor_calls)
        self.assertIn("2 player(s)", self.ui._labels["count"].text)
        self.assertEqual("MAP: 04 - Himmelsdorf", self.ui._labels["map"].text)
        self.assertEqual("PIXEL", self.ui._panel.widthMode)
        self.assertEqual((680, 280), (self.ui._panel.width, self.ui._panel.height))
        self.assertEqual([self.ui._panel], self.roots)
        self.assertTrue(
            all(control.parent is self.ui._panel for control in self.ui._controls.values())
        )
        self.assertTrue(
            all(label.parent is self.ui._panel for label in self.ui._labels.values())
        )
        for role in ("previous", "map", "next", "start"):
            self.assertEqual("CENTER", self.ui._controls[role].horizontalAnchor)
            self.assertEqual("CENTER", self.ui._controls[role].verticalAnchor)
            self.assertEqual(
                self.ui._controls[role].position[1],
                self.ui._labels[role].position[1],
            )
        self.assertTrue(
            all(
                not label.focus
                and not label.mouseButtonFocus
                and not label.crossFocus
                and not label.moveFocus
                for label in self.ui._labels.values()
            )
        )

        self.ui._controls["next"].script.handleMouseClickEvent(
            self.ui._controls["next"]
        )
        self.assertEqual("31_airfield", self.ui.selected_map())
        self.ui._controls["start"].script.handleMouseClickEvent(
            self.ui._controls["start"]
        )

        self.assertEqual([(self.player, "31_airfield")], self.starts)
        self.assertIn("Starting 31 - Airfield", self.ui._labels["status"].text)

    def test_roster_update_refreshes_count_and_battle_transition_closes(self):
        self.ui.open(self.player)
        self.client.waiting_count = 4

        self.assertTrue(self.ui.update(self.player))
        self.assertIn("4 player(s)", self.ui._labels["count"].text)

        self.client.phase = "battle"
        self.assertFalse(self.ui.update(self.player))
        self.assertFalse(self.ui._active)
        self.assertEqual([True, False], self.cursor_calls)
        self.assertTrue(all(not control.visible for control in self.ui._controls.values()))


class OfflineMapRoomTest(WaitingRoomUITest):
    """The same room lets a single player pick a map or leave the queue."""

    def test_offline_room_starts_the_selected_map(self):
        started = []
        self.assertTrue(self.ui.open_offline(
            self.player, on_start=started.append,
            options=["01_karelia", "06_ensk"]))
        self.assertIn("Single player", self.ui._labels["count"].text)
        self.assertEqual("MAP: 01 - Karelia", self.ui._labels["map"].text)

        self.ui._activate("next")
        self.assertEqual("MAP: 06 - Ensk", self.ui._labels["map"].text)
        self.ui._activate("start")

        self.assertEqual(["06_ensk"], started)
        self.assertFalse(self.ui._active)

    def test_offline_room_needs_a_map_pool(self):
        self.assertFalse(self.ui.open_offline(
            self.player, on_start=lambda unused: None, options=[]))


if __name__ == "__main__":
    unittest.main()
