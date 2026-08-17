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
        return _Component('window', 'system/maps/col_white.bmp')

    def simple(self):
        return _Component('simple', 'system/maps/col_white.bmp')

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

    def test_the_room_only_uses_properties_this_client_has(self):
        self.room.install()
        self.assertTrue(self.room.open())
        self.assertEqual(1, len(self.surface.roots))
        self.assertEqual(1, self.surface.resorts)

    def test_the_panel_uses_the_stock_white_texture(self):
        self.room.install()
        self.assertEqual('system/maps/col_white.bmp',
                         self.room._panel.texture)

    def test_the_host_sees_the_map_selector_and_start_button(self):
        self.room.open()
        for role in ('previous', 'map', 'next', 'start'):
            self.assertTrue(self._visible(role), role)
        self.assertEqual('MAP: 01 - Karelia', self._label('map'))
        self.assertEqual('LAN SERVER: 10.0.0.5:28782', self._label('room'))
        self.assertEqual('PLAYERS (2): Host, Guest', self._label('players'))

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
        self.assertEqual(1, len(self.surface.roots))

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
            ('Window', 'system/maps/col_white.bmp'),
            ('Simple', 'system/maps/col_white.bmp'),
            ('Text', ''),
            ('addRoot', 'window'),
            ('delRoot', 'window'),
            ('reSort',),
        ])

    def test_a_client_without_the_native_gui_reports_it(self):
        self.assertRaises(ImportError, self.module.NativeSurface)


if __name__ == '__main__':
    unittest.main()
