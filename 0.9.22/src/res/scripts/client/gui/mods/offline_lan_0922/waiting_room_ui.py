from __future__ import print_function

"""Self-drawn LAN waiting room for #1513.

The room presents the reviewed 0.8.2 waiting-room law: a live room status, one
map selector and one start button. The launcher owns the server address, so
this room never edits it.

Exact #1513 evidence for the native surface used here:

- ``GUI.Simple``, ``GUI.Window`` and ``GUI.Text`` with the property names below:
  ``scripts/client/PostProcessing/ChainView.pyc`` and
  ``scripts/client/bwobsolete_tests/GUITest.pyc``.
- Untextured flat colour: ``EffectView.createPhase`` constructs
  ``GUI.Window('')`` and ``ChainView.displayAlpha`` sets ``textureName = ''``
  with SOLID, both in ``scripts/client/PostProcessing/ChainView.pyc``.
  ``col_white`` texture names (packed ``.dds`` and source ``.bmp``) rendered
  the panel untinted white on the real #1513 client, so this room draws no
  texture at all. The flat-colour result still needs visual confirmation.
- Font ``system/fonts/default_small.font``: package member.
- ``GUI.addRoot`` / ``GUI.delRoot`` / ``GUI.reSort`` and an overlay at
  ``position.z = 0.1`` with ``focus``, ``moveFocus`` and ``wg_inputKeyMode``:
  ``scripts/client/new_year/fade_window.pyc``.
- Mouse script methods ``handleMouseClickEvent``, ``handleMouseEnterEvent``,
  ``handleMouseLeaveEvent`` and ``handleMouseButtonEvent``: ``ChainView.pyc``.
- The pointer is the native GUI mouse cursor, and making it visible is not
  enough: it also has to become the active cursor. ``Scaleform.showCursor`` in
  ``scripts/client/Scaleform/__init__.pyc`` is exactly
  ``c = GUI.mcursor(); c.visible = 1; BigWorld.setCursor(c)``, and
  ``helpers/OfflineMode.launch`` uses the same pair. Setting only ``visible``
  leaves the device cursor active, which is what showed the OS pointer.
  ``Cursor.attachCursor``/``detachCursor`` in
  ``gui/Scaleform/managers/Cursor.pyc`` supply the ownership rule this room
  follows: acquire only while ``GUI.mcursor().active`` is False, and release
  with ``BigWorld.setCursor(None)``.
"""

import sys

PANEL_TEXTURE = ''
PANEL_FONT = 'default_small.font'
OVERLAY_Z = 0.1

_HOST_CONTROLS = ('previous', 'map', 'next', 'start')


def _LEFT_MOUSE_KEY():
    """Return this client's left-mouse key constant, or None."""
    try:
        import Keys
    except ImportError:
        return None
    return getattr(Keys, 'KEY_LEFTMOUSE', None)


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

    def cursor_position(self):
        """Clip-space mouse position, the coordinate system ChainView reads."""
        position = self._gui.mcursor().position
        return float(position[0]), float(position[1])

    def cursor_is_active(self):
        return bool(self._gui.mcursor().active)

    def show_cursor(self):
        """Make the native cursor active but keep it unpainted.

        ``gui/mouse_cursors.xml`` in this build is 12 bytes and defines no
        shapes, so a visible mcursor falls back to the OS default and the
        player sees the Windows busy pointer.  Activating it still makes
        ``mcursor.position`` track the mouse, which is what the room's own
        drawn arrow follows.
        """
        import BigWorld
        cursor = self._gui.mcursor()
        BigWorld.setCursor(cursor)
        cursor.visible = False
        return True

    def screen_size(self):
        """Return the screen size in pixels, or None when unavailable."""
        resolution = getattr(self._gui, 'screenResolution', None)
        if not callable(resolution):
            return None
        try:
            width, height = resolution()
            width, height = float(width), float(height)
        except (TypeError, ValueError):
            return None
        if width <= 0.0 or height <= 0.0:
            return None
        return width, height

    def hide_cursor(self):
        """Leave the cursor as the lobby keeps it: active and unpainted."""
        self._gui.mcursor().visible = False
        return True

    def tick(self, delay, function):
        import BigWorld
        return BigWorld.callback(delay, function)

    def cancel_tick(self, handle):
        import BigWorld
        BigWorld.cancelCallback(handle)


class _ControlScript(object):
    """Mouse target for one room control."""

    def __init__(self, room, role):
        self._room = room
        self._role = role

    def handleMouseEvent(self, unused_component, unused_event):
        """#1513 delivers mouse MOVE here; there is no move-specific method.

        A component with ``moveFocus`` set but no ``handleMouseEvent`` never
        completes the engine's move path.  Returning False keeps the event
        propagating so the tooltip and drag managers still see it.
        """
        self._room.move_pointer()
        return False

    def handleMouseClickEvent(self, unused_component):
        self._room.activate(self._role)
        return True

    def handleMouseEnterEvent(self, unused_component):
        self._room.hover(self._role)
        # Do not consume the crossing: swallowing it would also cut the stock
        # mouse-event chain below the native GUI for this event.
        return False

    def handleMouseLeaveEvent(self, unused_component):
        self._room.hover(None)
        return False

    def handleMouseButtonEvent(self, unused_component, event):
        return bool(getattr(event, 'key', None) == _LEFT_MOUSE_KEY())

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
        self._frames = {}
        self._labels = {}
        self._cursor_acquired = False
        self._pointer_rows = []
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
        # Empty texture plus SOLID renders the flat vertex colour.
        self._set(panel, 'materialFX', 'SOLID')
        self._set(panel, 'colour', (5, 12, 20, 245))
        self._set(panel, 'position', (0.0, 0.0, OVERLAY_Z))
        # Every stock root that hosts a live pointer sets focus AND moveFocus
        # (GUI.Flash, createMovieGUI, FadeWindow, ChainView).  focus alone is
        # the keyboard list and leaves the root out of the move path entirely.
        self._set(panel, 'focus', True)
        self._set(panel, 'mouseButtonFocus', False)
        self._set(panel, 'crossFocus', False)
        self._set(panel, 'moveFocus', True)
        self._set(panel, 'script', _ControlScript(self, None))
        # wg_inputKeyMode belongs to FlashGUIComponent in this build, so a
        # GUI.Window can never accept it.  Setting it only logged a skip.
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
        self._make_label('close', 'LEAVE', (0.0, -0.78, 0.0), 0.46, 0.12,
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
        # A slightly lighter frame behind each control separates it from the
        # panel; the button body draws over it, leaving a thin border.
        frame = self._surface.simple()
        for name, value in (
                ('horizontalPositionMode', 'CLIP'),
                ('verticalPositionMode', 'CLIP'),
                ('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
                ('horizontalAnchor', 'CENTER'), ('verticalAnchor', 'CENTER'),
                ('position', (position[0], position[1], position[2] + 0.01)),
                ('width', width + 0.014), ('height', height + 0.030),
                ('materialFX', 'SOLID'), ('colour', (120, 158, 186, 245)),
                ('focus', False), ('mouseButtonFocus', False),
                ('crossFocus', False), ('moveFocus', False),
                ('visible', False)):
            self._set(frame, name, value)
        self._panel.addChild(frame)
        self._frames[role] = frame
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
        self._acquire_cursor()
        self._build_pointer()
        self._move_pointer()
        self.refresh()
        _log('LAN waiting room opened')
        return True

    # A staircase of one-pixel rows approximates a pointer without a texture:
    # mouse_cursors.xml defines no shapes and no arrow bitmap ships outside the
    # lobby SWF, so the room draws its own from flat quads.
    POINTER_ROWS = 11
    POINTER_SHADOW = (0, 0, 0, 200)
    POINTER_FILL = (255, 255, 255, 255)

    def move_pointer(self):
        """Public move hook: the control scripts call this on every move."""
        if not self._open:
            return False
        return self._move_pointer()

    def _build_pointer(self):
        """Create the drawn arrow once, as children of the room panel."""
        if self._pointer_rows or self._panel is None:
            return False
        rows = []
        for layer, colour, offset in (
                ('shadow', self.POINTER_SHADOW, 1),
                ('fill', self.POINTER_FILL, 0)):
            for index in range(self.POINTER_ROWS):
                row = self._surface.simple()
                for name, value in (
                        ('horizontalPositionMode', 'CLIP'),
                        ('verticalPositionMode', 'CLIP'),
                        ('widthMode', 'PIXEL'), ('heightMode', 'PIXEL'),
                        ('horizontalAnchor', 'LEFT'),
                        ('verticalAnchor', 'TOP'),
                        # Row i is i+2 pixels wide, which reads as a pointer.
                        ('width', index + 2), ('height', 1),
                        ('materialFX', 'SOLID'), ('colour', colour),
                        ('focus', False), ('mouseButtonFocus', False),
                        ('crossFocus', False), ('moveFocus', False),
                        ('visible', False)):
                    self._set(row, name, value)
                self._panel.addChild(row)
                rows.append((row, index, offset))
        self._pointer_rows = rows
        return True

    def _move_pointer(self):
        """Follow ``mcursor.position`` with the drawn arrow."""
        if not self._pointer_rows:
            return False
        size = getattr(self._surface, 'screen_size', None)
        position = getattr(self._surface, 'cursor_position', None)
        if not callable(size) or not callable(position):
            return False
        try:
            screen = size()
            x, y = position()
        except Exception:
            return False
        if screen is None:
            return False
        # Clip space spans -1..1 across the screen, so one pixel is 2/size.
        step_x = 2.0 / screen[0]
        step_y = 2.0 / screen[1]
        for row, index, offset in self._pointer_rows:
            self._set(row, 'position', (
                x + offset * step_x,
                y - (index + offset) * step_y,
                OVERLAY_Z - 0.05))
            self._set(row, 'visible', True)
        return True

    def _hide_pointer(self):
        for row, unused_index, unused_offset in self._pointer_rows:
            self._set(row, 'visible', False)
        return True

    def _acquire_cursor(self):
        """Make the native cursor visible while this room owns the screen.

        ``Cursor.attachCursor`` leaves the lobby's mcursor ACTIVE but with
        ``visible`` False on purpose, because the arrow the player normally
        sees is ``gui/flash/Cursor.swf`` drawn inside the lobby movie at
        z 0.5 - behind this room at z 0.1.  So an already-active cursor is not
        evidence that a visible pointer exists, and skipping the show on
        ``active`` left the room with no pointer of its own.
        """
        surface = self._surface
        show = getattr(surface, 'show_cursor', None)
        if not callable(show):
            return False
        try:
            show()
        except Exception as error:
            _log('LAN waiting room could not show the cursor: %s' % error)
            return False
        self._cursor_acquired = True
        return True

    def _release_cursor(self):
        if not self._cursor_acquired:
            return False
        self._cursor_acquired = False
        hide = getattr(self._surface, 'hide_cursor', None)
        if not callable(hide):
            return False
        try:
            hide()
        except Exception as error:
            _log('LAN waiting room could not release the cursor: %s' % error)
            return False
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
            frame = self._frames.get(role)
            if frame is not None:
                self._set(frame, 'visible', visible)
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
        self._release_cursor()
        self._hide_pointer()
        self._set(self._panel, 'visible', False)
        self._surface.remove_root(self._panel)
        _log('LAN waiting room closed')
        return True

    def uninstall(self):
        self.close()
        self._panel = None
        self._controls = {}
        self._frames = {}
        self._labels = {}
        self._pointer_rows = []
        return True
