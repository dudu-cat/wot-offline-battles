import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _install_package_modules():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module


def _load(name):
    _install_package_modules()
    full_name = 'gui.mods.offline_lan_0922.' + name
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(
        full_name, PACKAGE_ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


class _Component(object):
    """A fake native component that rejects properties this client lacks."""

    ALLOWED = frozenset((
        'horizontalPositionMode', 'verticalPositionMode', 'widthMode',
        'heightMode', 'horizontalAnchor', 'verticalAnchor', 'position',
        'width', 'height', 'materialFX', 'colour', 'focus', 'moveFocus',
        'mouseButtonFocus', 'crossFocus', 'visible', 'script', 'text', 'font',
        'multiline'))

    def __init__(self, kind, texture=None, text=''):
        object.__setattr__(self, 'kind', kind)
        object.__setattr__(self, 'texture', texture)
        object.__setattr__(self, 'children', [])
        object.__setattr__(self, 'properties', {'text': text})

    def __setattr__(self, name, value):
        if name not in self.ALLOWED:
            raise AttributeError(name)
        self.properties[name] = value

    def __getattr__(self, name):
        try:
            return self.properties[name]
        except KeyError:
            raise AttributeError(name)

    def addChild(self, component):
        self.children.append(component)


class _Surface(object):
    def __init__(self):
        self.roots = []
        self.resorts = 0

    def window(self):
        return _Component('window', '')

    def simple(self):
        return _Component('simple', '')

    def text(self):
        return _Component('text', text='')

    def add_root(self, component):
        self.roots.append(component)

    def remove_root(self, component):
        if component in self.roots:
            self.roots.remove(component)

    def resort(self):
        self.resorts += 1


class WaitingRoomTests(unittest.TestCase):
    def setUp(self):
        self.module = _load('waiting_room_ui')
        self.surface = _Surface()
        self.started = []
        self.closed = []
        self.pool = ['01_karelia', '05_prohorovka']
        self.is_host = True
        self.status = u'LAN SERVER: 10.0.0.5:28782\nPLAYERS (2): Host, Guest'
        self.room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, on_close=lambda: self.closed.append(1),
            host=lambda: self.is_host, surface=self.surface)

    def _request_start(self, map_name):
        self.started.append(map_name)
        return True

    def _label(self, role):
        return self.room._labels[role].properties['text']

    def _visible(self, role):
        return self.room._controls[role].properties['visible']

    def _root_count(self):
        """The panel plus the pointer's outline and body."""
        return 3

    def test_the_room_only_uses_properties_this_client_has(self):
        self.room.install()
        self.assertTrue(self.room.open())
        self.assertEqual(self._root_count(), len(self.surface.roots))
        self.assertEqual(2, self.surface.resorts)

    def test_the_panel_draws_untextured_flat_colour(self):
        self.room.install()
        self.assertEqual('', self.room._panel.texture)

    def test_the_host_sees_the_map_selector_and_start_button(self):
        self.room.open()
        for role in ('previous', 'map', 'next', 'start'):
            self.assertTrue(self._visible(role), role)
        self.assertEqual('MAP: 01 - Karelia', self._label('map'))
        self.assertEqual('LAN SERVER: 10.0.0.5:28782', self._label('room'))
        self.assertEqual('PLAYERS (2): Host, Guest', self._label('players'))

    def test_the_room_takes_and_releases_the_native_cursor(self):
        class _CursorSurface(_Surface):
            def __init__(self):
                _Surface.__init__(self)
                self.active = False
                self.shown = 0
                self.hidden = 0

            def cursor_is_active(self):
                return self.active

            def show_cursor(self):
                self.shown += 1
                self.active = True
                return True

            def hide_cursor(self):
                self.hidden += 1
                self.active = False
                return True

        surface = _CursorSurface()
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: True, surface=surface)

        self.assertTrue(room.open())
        self.assertEqual(1, surface.shown)
        self.assertTrue(room._cursor_acquired)
        self.assertEqual(self._root_count(), len(surface.roots))

        room.close()
        self.assertEqual([], surface.roots)
        self.assertEqual(1, surface.hidden)
        self.assertFalse(room._cursor_acquired)
        self.assertFalse(surface.active)

    def test_an_already_active_cursor_is_still_made_visible(self):
        """The lobby keeps mcursor active but invisible while Scaleform draws
        the arrow, so an active cursor is not a visible one."""
        class _ActiveCursorSurface(_Surface):
            def __init__(self):
                _Surface.__init__(self)
                self.shown = 0
                self.hidden = 0

            def cursor_is_active(self):
                return True

            def show_cursor(self):
                self.shown += 1
                return True

            def hide_cursor(self):
                self.hidden += 1
                return True

        surface = _ActiveCursorSurface()
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: True, surface=surface)

        self.assertTrue(room.open())
        self.assertEqual(1, surface.shown)

        room.close()
        self.assertEqual(1, surface.hidden)

    def test_cursor_is_skipped_on_a_surface_without_cursor_calls(self):
        self.room.open()
        self.assertFalse(self.room._cursor_acquired)
        self.assertEqual(self._root_count(), len(self.surface.roots))
        self.room.close()

    def test_the_room_handles_mouse_move_so_the_pointer_tracks(self):
        """#1513 delivers move as handleMouseEvent; there is no move method."""
        self.room.install()
        script = self.room._controls['start'].properties.get('script')

        self.assertTrue(hasattr(script, 'handleMouseEvent'))
        self.assertFalse(script.handleMouseEvent(None, None))

    def test_the_panel_takes_move_focus_and_a_script(self):
        self.room.install()
        panel = self.room._panel.properties

        # focus alone is the keyboard list: without moveFocus the root is
        # never offered a mouse-move event at all.
        self.assertTrue(panel['focus'])
        self.assertTrue(panel['moveFocus'])
        self.assertTrue(hasattr(panel.get('script'), 'handleMouseEvent'))

    def test_crossings_are_not_swallowed(self):
        self.room.install()
        script = self.room._controls['start'].properties['script']

        # Returning True here would cut the stock mouse chain below the
        # native GUI for that event.
        self.assertFalse(script.handleMouseEnterEvent(None))
        self.assertFalse(script.handleMouseLeaveEvent(None))

    def test_the_drawn_pointer_follows_the_native_cursor(self):
        """No arrow bitmap ships outside the lobby SWF, so the room draws one."""
        class _PointerSurface(_Surface):
            def __init__(self):
                _Surface.__init__(self)
                self.cursor = (0.0, 0.0)

            def screen_size(self):
                return (1000.0, 500.0)

            def cursor_position(self):
                return self.cursor

            def show_cursor(self):
                return True

            def hide_cursor(self):
                return True

        surface = _PointerSurface()
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: True, surface=surface)
        self.assertTrue(room.open())

        self.assertEqual(2, len(room._pointer_parts))
        outline, body = (part for part, unused in room._pointer_parts)
        self.assertTrue(body.properties['visible'])
        self.assertEqual(0.0, body.properties['position'][0])
        # The pointer is its own root: a child reads its position as an offset
        # from the panel, not as a screen coordinate.
        self.assertIn(body, surface.roots)
        self.assertNotIn(body, room._panel.children)
        # A pixel-sized quad with the flat material is what the panel and its
        # buttons already render with; a one-pixel row did not rasterize.
        self.assertEqual('PIXEL', body.properties['widthMode'])
        self.assertEqual('PIXEL', body.properties['heightMode'])
        self.assertEqual(room.POINTER_WIDTH, body.properties['width'])
        self.assertEqual(room.POINTER_HEIGHT, body.properties['height'])
        self.assertGreater(outline.properties['width'],
                           body.properties['width'])
        self.assertGreater(outline.properties['height'],
                           body.properties['height'])
        tip_row = body

        surface.cursor = (-0.5, 0.25)
        room.move_pointer()
        moved = body.properties

        self.assertAlmostEqual(-0.5, moved['position'][0])
        self.assertAlmostEqual(0.25, moved['position'][1])

        room.close()
        self.assertEqual([], room._pointer_parts)
        self.assertNotIn(tip_row, surface.roots)

    def test_the_pointer_follows_without_any_mouse_event(self):
        """The room owns a callback tick, so the arrow moves even when the
        engine delivers no move event to the panel."""
        class _TickSurface(_Surface):
            def __init__(self):
                _Surface.__init__(self)
                self.cursor = (0.0, 0.0)
                self.ticks = []
                self.cancelled = []

            def screen_size(self):
                return (1000.0, 500.0)

            def cursor_position(self):
                return self.cursor

            def show_cursor(self):
                return True

            def hide_cursor(self):
                return True

            def tick(self, delay, function):
                self.ticks.append((delay, function))
                return len(self.ticks)

            def cancel_tick(self, handle):
                self.cancelled.append(handle)

        surface = _TickSurface()
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: True, surface=surface)
        room.open()

        self.assertEqual(1, len(surface.ticks))
        self.assertEqual(self.module.POINTER_TICK_SECONDS,
                         surface.ticks[0][0])

        surface.cursor = (0.4, -0.6)
        surface.ticks[-1][1]()

        tip = room._pointer_parts[1][0].properties
        self.assertAlmostEqual(0.4, tip['position'][0])
        self.assertAlmostEqual(-0.6, tip['position'][1])
        # The tick reschedules itself for as long as the room is open.
        self.assertEqual(2, len(surface.ticks))

        room.close()
        self.assertEqual([2], surface.cancelled)
        surface.ticks[-1][1]()
        self.assertEqual(2, len(surface.ticks))

    def test_the_pointer_draws_in_front_of_the_room_panel(self):
        self.assertLess(self.module.POINTER_Z, self.module.OVERLAY_Z)
        self.room.open()
        self.room._surface.screen_size = lambda: (1000.0, 500.0)
        self.room._surface.cursor_position = lambda: (0.0, 0.0)
        self.room.move_pointer()
        panel_z = self.room._panel.properties['position'][2]
        row_z = self.room._pointer_parts[0][0].properties['position'][2]
        self.assertLess(row_z, panel_z)

    def test_the_pointer_never_takes_mouse_focus(self):
        self.room.open()
        self.room._build_pointer()

        for part, unused_grow in self.room._pointer_parts:
            self.assertFalse(part.properties['focus'])
            self.assertFalse(part.properties['mouseButtonFocus'])
            self.assertFalse(part.properties['crossFocus'])

    def test_controls_carry_a_border_frame_behind_the_body(self):
        self.room.open()
        frame = self.room._frames['start'].properties
        control = self.room._controls['start'].properties
        self.assertTrue(frame['visible'])
        self.assertGreater(frame['width'], control['width'])
        self.assertGreater(frame['height'], control['height'])
        self.assertGreater(frame['position'][2], control['position'][2])
        self.assertFalse(frame['focus'])

        self.is_host = False
        self.room.refresh()
        self.assertFalse(self.room._frames['start'].properties['visible'])
        self.assertTrue(self.room._frames['close'].properties['visible'])

    def test_a_guest_sees_the_room_without_controls(self):
        self.is_host = False
        self.status += u'\nWAITING FOR Host TO START THE BATTLE'
        self.room.open()
        for role in ('previous', 'map', 'next', 'start'):
            self.assertFalse(self._visible(role), role)
        self.assertTrue(self._visible('close'))
        self.assertEqual('WAITING FOR Host TO START THE BATTLE',
                         self._label('map'))

    def test_the_selector_cycles_the_server_map_pool(self):
        self.room.open()
        self.room.activate('next')
        self.assertEqual('MAP: 05 - Prohorovka', self._label('map'))
        self.room.activate('next')
        self.assertEqual('MAP: 01 - Karelia', self._label('map'))
        self.room.activate('previous')
        self.assertEqual('MAP: 05 - Prohorovka', self._label('map'))

    def test_start_sends_the_selected_map(self):
        self.room.open()
        self.room.activate('next')
        self.assertTrue(self.room.activate('start'))
        self.assertEqual(['05_prohorovka'], self.started)

    def test_a_denied_start_reports_the_refusal(self):
        self.room = self.module.WaitingRoomUI(
            lambda map_name: False, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: True,
            surface=self.surface)
        self.room.open()
        self.assertFalse(self.room.activate('start'))
        self.assertIn('did not accept', self._label('message'))

    def test_a_guest_cannot_start_or_change_the_map(self):
        self.is_host = False
        self.room.open()
        self.assertFalse(self.room.activate('start'))
        self.assertFalse(self.room.activate('next'))
        self.assertEqual([], self.started)

    def test_an_empty_pool_refuses_the_start(self):
        self.pool = []
        self.room.open()
        self.assertFalse(self.room.activate('start'))
        self.assertEqual([], self.started)
        self.assertIn('Choose a map', self._label('message'))

    def test_a_removed_map_falls_back_to_the_current_pool(self):
        self.room.open()
        self.room.activate('next')
        self.pool = ['19_monastery']
        self.room.refresh()
        self.assertEqual('MAP: 19 - Monastery', self._label('map'))

    def test_close_removes_the_room_and_reports_it_once(self):
        self.room.open()
        self.assertTrue(self.room.activate('close'))
        self.assertEqual([], self.surface.roots)
        self.assertEqual([1], self.closed)
        self.assertFalse(self.room.close())

    def test_reopening_shows_the_room_again(self):
        self.room.open()
        self.room.close()
        self.assertTrue(self.room.open())
        self.assertEqual(self._root_count(), len(self.surface.roots))

    def test_hover_repaints_only_while_the_room_is_open(self):
        self.assertFalse(self.room.hover('start'))
        self.room.open()
        self.assertTrue(self.room.hover('start'))
        self.assertEqual((62, 137, 190, 245),
                         self.room._controls['start'].properties['colour'])

    def test_uninstall_drops_every_component(self):
        self.room.open()
        self.room.uninstall()
        self.assertEqual([], self.surface.roots)
        self.assertEqual({}, self.room._controls)

    def test_friendly_names_keep_the_map_number(self):
        self.assertEqual('01 - Karelia',
                         self.module.friendly_map_name('01_karelia'))
        self.assertEqual('Himmelsdorf Winter',
                         self.module.friendly_map_name('himmelsdorf_winter'))
        self.assertEqual('Unknown', self.module.friendly_map_name(''))


class NativeSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.module = _load('waiting_room_ui')

    def test_the_surface_uses_the_reviewed_client_calls(self):
        calls = []

        class _GUI(object):
            @staticmethod
            def Window(texture):
                calls.append(('Window', texture))
                return 'window'

            @staticmethod
            def Simple(texture):
                calls.append(('Simple', texture))
                return 'simple'

            @staticmethod
            def Text(value):
                calls.append(('Text', value))
                return 'text'

            @staticmethod
            def addRoot(component):
                calls.append(('addRoot', component))

            @staticmethod
            def delRoot(component):
                calls.append(('delRoot', component))

            @staticmethod
            def reSort():
                calls.append(('reSort',))

        surface = self.module.NativeSurface(_GUI)
        surface.window()
        surface.simple()
        surface.text()
        surface.add_root('window')
        surface.remove_root('window')
        surface.resort()
        self.assertEqual(calls, [
            ('Window', ''),
            ('Simple', ''),
            ('Text', ''),
            ('addRoot', 'window'),
            ('delRoot', 'window'),
            ('reSort',),
        ])

    def test_a_client_without_the_native_gui_reports_it(self):
        self.assertRaises(ImportError, self.module.NativeSurface)


if __name__ == '__main__':
    unittest.main()
