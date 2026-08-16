from __future__ import print_function

"""Self-drawn LAN waiting room for #1513.

The room presents the reviewed 0.8.2 waiting-room law: a live room status, one
map selector and one start button. The launcher owns the server address, so
this room never edits it.

Exact #1513 evidence for the native surface used here:

- ``GUI.Simple``, ``GUI.Window`` and ``GUI.Text`` with the property names below:
  ``scripts/client/PostProcessing/ChainView.pyc`` and
  ``scripts/client/bwobsolete_tests/GUITest.pyc``.
- Texture ``system/maps/col_white.dds``: ``misc.pkg`` member, used by
  ``ChainView.EffectView.__init__``.
- Font ``system/fonts/default_small.font``: package member.
- ``GUI.addRoot`` / ``GUI.delRoot`` / ``GUI.reSort`` and an overlay at
  ``position.z = 0.1`` with ``focus``, ``moveFocus`` and ``wg_inputKeyMode``:
  ``scripts/client/new_year/fade_window.pyc``.
- Mouse script methods ``handleMouseClickEvent``, ``handleMouseEnterEvent``,
  ``handleMouseLeaveEvent`` and ``handleMouseButtonEvent``: ``ChainView.pyc``.
- The lobby already attaches ``GUI.mcursor`` through
  ``gui/Scaleform/managers/Cursor.pyc``, so this room does not take cursor
  ownership.
"""

import sys

PANEL_TEXTURE = 'system/maps/col_white.dds'
PANEL_FONT = 'default_small.font'
OVERLAY_Z = 0.1
INPUT_KEY_MODE = 2

_HOST_CONTROLS = ('previous', 'map', 'next', 'start')


def _log(message):
    sys.stdout.write('[Offline LAN 0.9.22] %s\n' % message)


def friendly_map_name(map_name):
    """Turn a server geometry name into a readable room label."""
    parts = str(map_name or '').split('_')
    prefix = ''
    if parts and parts[0].isdigit():
        prefix = parts[0] + ' - '
        parts = parts[1:]
    return prefix + (' '.join([part.capitalize() for part in parts]) or
                     'Unknown')


class NativeSurface(object):
    """The native GUI calls this room needs from the exact client."""

    def __init__(self, gui_module=None):
        if gui_module is None:
            import GUI as gui_module
        self._gui = gui_module

    def window(self):
        return self._gui.Window(PANEL_TEXTURE)

    def simple(self):
        return self._gui.Simple(PANEL_TEXTURE)

    def text(self):
        return self._gui.Text('')

    def add_root(self, component):
        self._gui.addRoot(component)

    def remove_root(self, component):
        self._gui.delRoot(component)

    def resort(self):
        self._gui.reSort()


class _ControlScript(object):
    """Mouse target for one room control."""

    def __init__(self, room, role):
        self._room = room
        self._role = role

    def handleMouseClickEvent(self, unused_component):
        self._room.activate(self._role)
        return True

    def handleMouseEnterEvent(self, unused_component):
        self._room.hover(self._role)
        return True

    def handleMouseLeaveEvent(self, unused_component):
        self._room.hover(None)
        return True

    def handleMouseButtonEvent(self, unused_component, unused_event):
        return True

    def handleKeyEvent(self, unused_event):
        return False


class WaitingRoomUI(object):
    """A reversible native room used instead of the stock map picker."""

    # The stock picker can only present the elected host.  This room also
    # presents the players who wait for that host.
    guest_view = True

    def __init__(self, request_start, map_pool, status=None, on_close=None,
                 host=None, surface=None):
        self._request_start = request_start
        self._map_pool = map_pool
        self._status = status or (lambda: '')
        self._on_close = on_close
        self._host = host or (lambda: False)
        self._surface = surface
        self._panel = None
        self._controls = {}
        self._labels = {}
        self._open = False
        self._hovered = None
        self._selected_map = None
        self._message = ''

    def install(self):
        """Build the native components without showing them."""
        if self._panel is not None:
            return True
        surface = self._surface
        if surface is None:
            surface = NativeSurface()
            self._surface = surface
        panel = surface.window()
        self._set(panel, 'horizontalPositionMode', 'CLIP')
        self._set(panel, 'verticalPositionMode', 'CLIP')
        self._set(panel, 'widthMode', 'PIXEL')
        self._set(panel, 'heightMode', 'PIXEL')
        self._set(panel, 'horizontalAnchor', 'CENTER')
        self._set(panel, 'verticalAnchor', 'CENTER')
        self._set(panel, 'width', 680)
        self._set(panel, 'height', 300)
        self._set(panel, 'materialFX', 'SOLID')
        self._set(panel, 'colour', (5, 12, 20, 245))
        self._set(panel, 'position', (0.0, 0.0, OVERLAY_Z))
        # The children own every mouse target, matching the reviewed 0.8.2 room.
        self._set(panel, 'focus', True)
        self._set(panel, 'mouseButtonFocus', False)
        self._set(panel, 'crossFocus', False)
        self._set(panel, 'moveFocus', False)
        # Proven on this client only for the Scaleform overlay component.
        self._set_optional(panel, 'wg_inputKeyMode', INPUT_KEY_MODE)
        self._set(panel, 'visible', False)
        self._panel = panel
        self._make_control('previous', (-0.72, 0.05, 0.05), 0.20, 0.20)
        self._make_control('map', (0.0, 0.05, 0.05), 1.15, 0.20)
        self._make_control('next', (0.72, 0.05, 0.05), 0.20, 0.20)
        self._make_control('start', (0.0, -0.40, 0.05), 1.20, 0.22)
        self._make_control('close', (0.0, -0.78, 0.05), 0.50, 0.18)
        self._make_label('title', 'LAN WAITING ROOM', (-0.86, 0.82, 0.0), 1.72,
                         0.12, colour=(232, 244, 255, 255))
        self._make_label('room', '', (-0.86, 0.62, 0.0), 1.72, 0.11)
        self._make_label('players', '', (-0.86, 0.44, 0.0), 1.72, 0.11)
        self._make_label('previous', '<', (-0.72, 0.05, 0.0), 0.18, 0.12,
                         anchor='CENTER')
        self._make_label('map', '', (0.0, 0.05, 0.0), 1.10, 0.12,
                         anchor='CENTER')
        self._make_label('next', '>', (0.72, 0.05, 0.0), 0.18, 0.12,
                         anchor='CENTER')
        self._make_label('start', 'START BATTLE', (0.0, -0.40, 0.0), 1.16,
                         0.12, anchor='CENTER')
        self._make_label('close', 'CLOSE', (0.0, -0.78, 0.0), 0.46, 0.12,
                         anchor='CENTER')
        self._make_label('message', '', (-0.86, -0.60, 0.0), 1.72, 0.11,
                         colour=(184, 205, 222, 255))
        return True

    @staticmethod
    def _set(component, name, value):
        setattr(component, name, value)

    @staticmethod
    def _set_optional(component, name, value):
        try:
            setattr(component, name, value)
        except (AttributeError, TypeError, ValueError):
            _log('LAN waiting room skipped the %s property' % name)

    def _make_control(self, role, position, width, height):
        component = self._surface.simple()
        for name, value in (
                ('horizontalPositionMode', 'CLIP'),
                ('verticalPositionMode', 'CLIP'),
                ('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
                ('horizontalAnchor', 'CENTER'), ('verticalAnchor', 'CENTER'),
                ('position', position), ('width', width), ('height', height),
                ('materialFX', 'SOLID'), ('colour', (24, 55, 78, 245)),
                ('focus', True), ('mouseButtonFocus', True),
                ('crossFocus', True), ('moveFocus', True),
                ('visible', False)):
            self._set(component, name, value)
        self._set(component, 'script', _ControlScript(self, role))
        self._panel.addChild(component)
        self._controls[role] = component
        return component

    def _make_label(self, role, text, position, width, height, anchor='LEFT',
                    colour=(255, 255, 255, 255)):
        component = self._surface.text()
        for name, value in (
                ('text', text),
                ('horizontalPositionMode', 'CLIP'),
                ('verticalPositionMode', 'CLIP'),
                ('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
                ('horizontalAnchor', anchor), ('verticalAnchor', 'CENTER'),
                ('position', position), ('width', width), ('height', height),
                ('font', PANEL_FONT), ('colour', colour), ('multiline', False),
                ('focus', False), ('mouseButtonFocus', False),
                ('crossFocus', False), ('moveFocus', False),
                ('visible', False)):
            self._set(component, name, value)
        self._panel.addChild(component)
        self._labels[role] = component
        return component

    def _options(self):
        return [name for name in (self._map_pool() or ()) if name]

    def _sync_selection(self):
        options = self._options()
        if not options:
            self._selected_map = None
        elif self._selected_map not in options:
            self._selected_map = options[0]
        return options

    def open(self):
        if self._open:
            self.refresh()
            return True
        if self._panel is None:
            self.install()
        self._sync_selection()
        self._message = ''
        self._open = True
        self._surface.add_root(self._panel)
        self._surface.resort()
        self.refresh()
        _log('LAN waiting room opened')
        return True

    def refresh(self):
        if not self._open:
            return False
        options = self._sync_selection()
        is_host = bool(self._host())
        lines = str(self._status() or '').splitlines()
        self._set_text('room', lines[0] if lines else '')
        self._set_text('players', lines[1] if len(lines) > 1 else '')
        if is_host:
            self._set_text('map', 'MAP: %s' % (
                friendly_map_name(self._selected_map) if options else
                'waiting for the server map list'))
        else:
            self._set_text('map', lines[2] if len(lines) > 2 else
                           'The room host starts the battle.')
        self._set_text('message', self._message)
        for role, component in self._controls.items():
            visible = role == 'close' or is_host
            self._set(component, 'visible', visible)
            label = self._labels.get(role)
            if label is not None:
                self._set(label, 'visible', visible)
        for role in ('title', 'room', 'players', 'map', 'message'):
            self._set(self._labels[role], 'visible', True)
        self._paint()
        self._set(self._panel, 'visible', True)
        return True

    def _set_text(self, role, value):
        label = self._labels.get(role)
        if label is not None:
            self._set(label, 'text', value)

    def _paint(self):
        for role, component in self._controls.items():
            if role == self._hovered:
                colour = (62, 137, 190, 245)
            elif role == 'start':
                colour = (40, 118, 64, 245)
            elif role == 'map':
                colour = (38, 104, 154, 245)
            elif role == 'close':
                colour = (78, 46, 46, 240)
            else:
                colour = (24, 55, 78, 235)
            self._set(component, 'colour', colour)

    def hover(self, role):
        if not self._open:
            return False
        self._hovered = role
        self._paint()
        return True

    def activate(self, role):
        if not self._open:
            return False
        if role == 'close':
            self.close()
            if callable(self._on_close):
                self._on_close()
            return True
        if not self._host():
            return False
        if role == 'previous':
            return self._cycle(-1)
        if role in ('next', 'map'):
            return self._cycle(1)
        if role == 'start':
            return self._start()
        return False

    def _cycle(self, step):
        options = self._options()
        if not options:
            self._message = 'The server has not published its map list yet.'
            self.refresh()
            return False
        try:
            index = options.index(self._selected_map)
        except ValueError:
            index = 0
        self._selected_map = options[(index + int(step)) % len(options)]
        self._message = ''
        self.refresh()
        return True

    def _start(self):
        if not self._selected_map:
            self._message = 'Choose a map first.'
            self.refresh()
            return False
        self._message = 'Starting %s...' % friendly_map_name(
            self._selected_map)
        self.refresh()
        accepted = self._request_start(self._selected_map)
        if accepted is False:
            self._message = 'The server did not accept that map.'
            self.refresh()
            return False
        return True

    def close(self):
        if not self._open:
            return False
        self._open = False
        self._hovered = None
        self._set(self._panel, 'visible', False)
        self._surface.remove_root(self._panel)
        _log('LAN waiting room closed')
        return True

    def uninstall(self):
        self.close()
        self._panel = None
        self._controls = {}
        self._labels = {}
        return True
