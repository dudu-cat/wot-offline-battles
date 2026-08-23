import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


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

    def simple(self, texture=''):
        return _Component('simple', texture)

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

    def _root_count(self, room=None):
        """The room panel plus one root per arrow row."""
        return 1 + len((room or self.room)._pointer_parts)

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
        self.assertEqual('MAP: Random', self._label('map'))
        self.assertEqual('LAN SERVER: 10.0.0.5:28782', self._label('room'))
        self.assertEqual('PLAYERS (2): Host, Guest', self._label('players'))

    def test_random_is_the_first_map_option_and_starts_with_its_wire_name(self):
        self.room.open()

        self.assertEqual(self.module.RANDOM_MAP_OPTION,
                         self.room._selected_map)
        self.assertTrue(self.room.activate('start'))
        self.assertEqual([self.module.RANDOM_MAP_OPTION], self.started)

        self.assertTrue(self.room.activate('next'))
        self.assertEqual('MAP: 01 - Karelia', self._label('map'))

    def test_random_is_hidden_when_the_server_does_not_advertise_it(self):
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: True,
            surface=self.surface, random_supported=lambda: False)

        self.assertTrue(room.open())
        self.assertEqual('01_karelia', room._selected_map)
        self.assertTrue(room.activate('start'))
        self.assertEqual(['01_karelia'], self.started)

    def test_every_player_can_select_a_team_and_see_capacity(self):
        selected = []
        team_state = {
            'team': 1, 'sizes': {1: 2, 2: 5},
            'counts': {1: 1, 2: 3}, 'supported': True,
        }
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: False,
            surface=self.surface,
            request_team=lambda team: selected.append(team) or True,
            team_status=lambda: dict(team_state))

        self.assertTrue(room.open())
        self.assertTrue(room._controls['team1'].properties['visible'])
        self.assertTrue(room._controls['team2'].properties['visible'])
        self.assertIn('1/2', room._labels['team1'].properties['text'])
        self.assertIn('(YOU)', room._labels['team1'].properties['text'])
        self.assertIn('3/5', room._labels['team2'].properties['text'])
        self.assertTrue(room.activate('team2'))
        self.assertEqual([2], selected)

    def test_host_can_adjust_both_team_sizes_without_an_extra_row(self):
        requested = []
        team_state = {
            'team': 1, 'sizes': {1: 2, 2: 5},
            'counts': {1: 1, 2: 3}, 'supported': True,
            'size_supported': True,
        }
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: True,
            surface=self.surface, request_team=lambda team: True,
            request_team_size=lambda team, size: (
                requested.append((team, size)) or True),
            team_status=lambda: dict(team_state))

        self.assertTrue(room.open())
        for role in self.module._TEAM_SIZE_CONTROLS:
            self.assertTrue(room._controls[role].properties['visible'])
        self.assertTrue(room.activate('team1_down'))
        self.assertTrue(room.activate('team2_up'))
        # A second click uses the optimistic target, so the host need not wait
        # for the roster echo between every step.
        self.assertTrue(room.activate('team2_up'))
        self.assertEqual([(1, 1), (2, 6), (2, 7)], requested)
        self.assertIn('3/7', room._labels['team2'].properties['text'])

    def test_non_host_sees_capacities_but_not_size_controls(self):
        team_state = {
            'team': 2, 'sizes': {1: 4, 2: 6},
            'counts': {1: 1, 2: 2}, 'supported': True,
            'size_supported': True,
        }
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: False,
            surface=self.surface, request_team=lambda team: True,
            request_team_size=lambda team, size: True,
            team_status=lambda: dict(team_state))

        self.assertTrue(room.open())
        self.assertIn('1/4', room._labels['team1'].properties['text'])
        self.assertIn('2/6', room._labels['team2'].properties['text'])
        for role in self.module._TEAM_SIZE_CONTROLS:
            self.assertFalse(room._controls[role].properties['visible'])
            self.assertFalse(room.activate(role))

    def test_team_size_denial_retires_the_optimistic_value(self):
        team_state = {
            'team': 1, 'sizes': {1: 3, 2: 3},
            'counts': {1: 1, 2: 1}, 'supported': True,
            'size_supported': True,
        }
        room = self.module.WaitingRoomUI(
            self._request_start, lambda: list(self.pool),
            status=lambda: self.status, host=lambda: True,
            surface=self.surface, request_team=lambda team: True,
            request_team_size=lambda team, size: True,
            team_status=lambda: dict(team_state))

        room.open()
        room.activate('team1_up')
        self.assertEqual({1: 4}, room._pending_team_sizes)
        self.assertTrue(room.reject_team_size(1, 'Refused.'))
        self.assertEqual({}, room._pending_team_sizes)
        self.assertEqual('Refused.', self._label_for(room, 'message'))

    @staticmethod
    def _label_for(room, role):
        return room._labels[role].properties['text']

    def test_panel_geometry_is_raised_and_bounded_at_common_resolutions(self):
        for screen in ((800, 600), (1024, 768), (1280, 720),
                       (1920, 1080), (2560, 1080), (3840, 2160)):
            width, height, y = self.module.panel_geometry(screen)
            center_y = y * screen[1] * 0.5
            horizontal_margin = (screen[0] - width) * 0.5
            top_margin = (screen[1] - height) * 0.5 - center_y
            bottom_margin = (screen[1] - height) * 0.5 + center_y
            self.assertGreaterEqual(
                horizontal_margin, self.module.PANEL_SAFE_MARGIN)
            self.assertGreaterEqual(top_margin,
                                    self.module.PANEL_SAFE_MARGIN)
            self.assertGreaterEqual(bottom_margin,
                                    self.module.PANEL_SAFE_MARGIN)
            self.assertGreater(y, 0.0)

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
        self.assertEqual(self._root_count(room), len(surface.roots))

        room.close()
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

        rows = self.module.WaitingRoomUI.POINTER_ROWS
        self.assertEqual(2 * len(rows), len(room._pointer_parts))
        tip, tip_x, tip_y, unused_z = room._pointer_parts[len(rows)]
        self.assertTrue(tip.properties['visible'])
        # A child's CLIP position is relative to the parent rect, which made
        # the arrow track at half speed; every row is a root instead.
        self.assertIn(tip, surface.roots)
        self.assertNotIn(tip, room._panel.children)
        self.assertEqual('PIXEL', tip.properties['widthMode'])
        self.assertEqual(self.module.CONTROL_TEXTURE, tip.texture)
        outline = room._pointer_parts[0][0]
        self.assertEqual(self.module.OUTLINE_TEXTURE, outline.texture)
        self.assertGreater(outline.properties['width'],
                           tip.properties['width'])
        self.assertGreater(outline.properties['position'][2],
                           tip.properties['position'][2])

        surface.cursor = (-0.5, 0.25)
        room.move_pointer()
        moved = tip.properties

        # The tip sits at the cursor; a 1000x500 screen makes one pixel
        # 0.002 clip units wide, so the offsets stay sub-pixel small.
        self.assertAlmostEqual(-0.5 + tip_x, moved['position'][0])
        self.assertAlmostEqual(0.25 + tip_y, moved['position'][1])
        self.assertLess(abs(tip_x), 0.01)

        room.close()
        self.assertFalse(tip.properties['visible'])

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

        part, offset_x, offset_y, unused_z = room._pointer_parts[1]
        self.assertAlmostEqual(0.4 + offset_x, part.properties['position'][0])
        self.assertAlmostEqual(-0.6 + offset_y, part.properties['position'][1])
        # The tick reschedules itself for as long as the room is open.
        self.assertEqual(2, len(surface.ticks))

        room.close()
        self.assertEqual([2], surface.cancelled)
        surface.ticks[-1][1]()
        self.assertEqual(2, len(surface.ticks))

    def test_the_pointer_draws_in_front_of_the_room_controls(self):
        self.room.open()
        self.room._surface.cursor_position = lambda: (0.0, 0.0)
        self.room.move_pointer()
        button_z = self.room._controls['close'].properties['position'][2]
        arrow_z = self.room._pointer_parts[0][0].properties['position'][2]
        self.assertLess(arrow_z, button_z)
        # z=0 is the one depth the client refused to draw.
        self.assertGreater(arrow_z, 0.0)

    def test_the_pointer_never_takes_mouse_focus(self):
        self.room.open()
        self.room._build_pointer()

        for part, unused_x, unused_y, unused_z in self.room._pointer_parts:
            self.assertFalse(part.properties['focus'])
            self.assertFalse(part.properties['mouseButtonFocus'])
            self.assertFalse(part.properties['crossFocus'])

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
        self.assertEqual(
            'Random', self.module.friendly_map_name(
                self.module.RANDOM_MAP_OPTION))
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


class NativeCursorSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.module = _load('waiting_room_ui')

    def _surface(self):
        cursor = types.SimpleNamespace(
            active=False, visible=True, position=(0.25, -0.5))
        calls = []
        bigworld = types.ModuleType('BigWorld')
        bigworld.setCursor = lambda value: calls.append(('setCursor', value))
        bigworld.dcursor = lambda: 'device'
        gui = types.SimpleNamespace(mcursor=lambda: cursor)
        gui_module = types.ModuleType('GUI')
        gui_module.mcursor = gui.mcursor
        modules = {'BigWorld': bigworld, 'GUI': gui_module}
        with mock.patch.dict(sys.modules, modules):
            surface = self.module.NativeSurface()
        surface._gui = gui
        return surface, cursor, calls, modules

    def test_show_cursor_activates_without_painting_the_os_pointer(self):
        surface, cursor, calls, modules = self._surface()

        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(surface.show_cursor())

        # A painted mcursor is the Windows pointer beside our own arrow.
        self.assertFalse(cursor.visible)
        self.assertEqual([('setCursor', cursor)], calls)

    def test_hide_cursor_puts_back_what_the_lobby_had(self):
        surface, cursor, calls, modules = self._surface()
        cursor.active, cursor.visible = True, False

        with mock.patch.dict(sys.modules, modules):
            surface.show_cursor()
            del calls[:]
            self.assertTrue(surface.hide_cursor())

        # Handing back the device cursor left the garage with no pointer.
        self.assertFalse(cursor.visible)
        self.assertEqual([('setCursor', cursor)], calls)

    def test_hide_cursor_detaches_when_the_lobby_had_no_cursor(self):
        surface, cursor, calls, modules = self._surface()
        cursor.active = False

        with mock.patch.dict(sys.modules, modules):
            surface.show_cursor()
            del calls[:]
            surface.hide_cursor()

        self.assertEqual([('setCursor', None)], calls)

    def test_cursor_state_reports_what_the_player_should_see(self):
        surface, cursor, unused_calls, modules = self._surface()
        cursor.active = True

        with mock.patch.dict(sys.modules, modules):
            state = surface.cursor_state()

        self.assertTrue(state['active'])
        self.assertEqual((0.25, -0.5), state['position'])


class RoomTextureTests(unittest.TestCase):
    def setUp(self):
        self.module = _load('waiting_room_ui')
        self.surface = _Surface()
        self.room = self.module.WaitingRoomUI(
            lambda name: True, lambda: ['01_karelia'],
            status=lambda: '', host=lambda: True, surface=self.surface)
        self.room.install()
        self.room.open()

    def test_every_drawn_rectangle_carries_the_proven_texture(self):
        # An untextured GUI.Simple draws nothing here, so anything meant to
        # be seen needs the one texture this client renders.
        texture = self.module.CONTROL_TEXTURE
        self.assertEqual(texture, self.room._controls['start'].texture)
        drawn = set(part.texture
                    for part, unused_x, unused_y, unused_z
                    in self.room._pointer_parts)
        # The arrow is a white shape over a black one, so it reads over the
        # hangar and over the white buttons alike.
        self.assertEqual(
            set([texture, self.module.OUTLINE_TEXTURE]), drawn)

    def test_button_labels_are_dark_enough_to_read_on_white(self):
        colour = self.module.CONTROL_TEXT_COLOUR
        self.assertEqual(
            colour, self.room._labels['start'].properties['colour'])
        # The free-floating labels stay light: they sit over the garage.
        self.assertNotEqual(
            colour, self.room._labels['title'].properties['colour'])
