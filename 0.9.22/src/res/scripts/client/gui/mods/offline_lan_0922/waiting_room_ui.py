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
- The lobby's own arrow is Flash, not a native shape: ``Cursor.attachCursor``
  in ``gui/Scaleform/managers/Cursor.pyc`` sets ``mcursor.visible = False`` and
  calls ``BigWorld.setCursor(mcursor)``, then ``Cursor.show`` draws the arrow
  through ``as_showCursorS`` inside ``gui/flash/Cursor.swf``. So the native
  cursor is only an input source here, and ``gui/mouse_cursors.xml`` is 12
  bytes in this build. This room activates the native cursor with the stock
  ``Scaleform.showCursor`` pair and draws its own arrow.
- The drawn arrow has never been seen. Two fixed diagnostic marks decide why:
  ``parked`` is a panel child at the panel centre, ``root`` is a GUI root. A
  child is clipped to the parent rect, so a pointer placed at a cursor outside
  the 680x300 panel would be culled; whichever mark the player sees names the
  mechanism.
"""

import sys
import time

# An untextured GUI.Simple/GUI.Window draws nothing on this client: the room
# rendered only its GUI.Text while every rectangle stayed invisible.  This
# misc.pkg member is the one texture proved to render here, untinted white.
PANEL_TEXTURE = ''
CONTROL_TEXTURE = 'system/maps/col_white.dds'
CONTROL_TEXT_COLOUR = (16, 26, 36, 255)
PANEL_FONT = 'default_small.font'
OVERLAY_Z = 0.1
# Smaller z draws in front. The buttons render at CONTROL_Z and the pointer at
# z=0 did not, so the pointer keeps the same 0.01 step inside that band.
CONTROL_Z = 0.05
CONTROL_FRAME_OFFSET = 0.01
PANEL_WIDTH = 680
PANEL_HEIGHT = 300
POINTER_TICK_SECONDS = 0.03

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

    def simple(self, texture=PANEL_TEXTURE):
        return self._gui.Simple(texture)

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
        """Run the exact #1513 ``Scaleform.showCursor`` sequence.

        0.8.2 shows its room pointer this way and #1513 has no ``gui.Cursor``
        module, so the stock helper is the closest equivalent.  The room still
        draws its own arrow on top, because this build's
        ``gui/mouse_cursors.xml`` is 12 bytes and defines no shape.
        """
        import BigWorld
        try:
            import Scaleform
            Scaleform.showCursor()
            return True
        except Exception as error:
            _log('LAN room fell back from Scaleform.showCursor: %s' % error)
        cursor = self._gui.mcursor()
        cursor.visible = 1
        BigWorld.setCursor(cursor)
        return True

    def cursor_state(self):
        """Return the live native cursor state for the pointer diagnostic."""
        cursor = self._gui.mcursor()
        return {
            'active': getattr(cursor, 'active', None),
            'visible': getattr(cursor, 'visible', None),
            'position': tuple(getattr(cursor, 'position', ())),
        }

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
        """Undo the acquire the way 0.8.2 does, device cursor included."""
        import BigWorld
        self._gui.mcursor().visible = False
        restore = getattr(BigWorld, 'dcursor', None)
        if callable(restore):
            BigWorld.setCursor(restore())
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
        self._pointer_parts = []
        self._pointer_probes = {}
        self._pointer_tick = None
        self._pointer_logged = None
        self._pointer_moves = 0
        self._pointer_ticks = 0
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
        self._set(panel, 'width', PANEL_WIDTH)
        self._set(panel, 'height', PANEL_HEIGHT)
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
        self._make_control('previous', (-0.72, 0.05, CONTROL_Z), 0.20, 0.20)
        self._make_control('map', (0.0, 0.05, CONTROL_Z), 1.15, 0.20)
        self._make_control('next', (0.72, 0.05, CONTROL_Z), 0.20, 0.20)
        self._make_control('start', (0.0, -0.40, CONTROL_Z), 1.20, 0.22)
        self._make_control('close', (0.0, -0.78, CONTROL_Z), 0.50, 0.18)
        self._make_label('title', 'LAN WAITING ROOM', (-0.86, 0.82, 0.0), 1.72,
                         0.12, colour=(232, 244, 255, 255))
        self._make_label('room', '', (-0.86, 0.62, 0.0), 1.72, 0.11)
        self._make_label('players', '', (-0.86, 0.44, 0.0), 1.72, 0.11)
        # These five sit on a textured button, which renders white until a
        # tint is proved, so their text has to be dark to stay readable.
        self._make_label('previous', '<', (-0.72, 0.05, 0.0), 0.18, 0.12,
                         anchor='CENTER', colour=CONTROL_TEXT_COLOUR)
        self._make_label('map', '', (0.0, 0.05, 0.0), 1.10, 0.12,
                         anchor='CENTER', colour=CONTROL_TEXT_COLOUR)
        self._make_label('next', '>', (0.72, 0.05, 0.0), 0.18, 0.12,
                         anchor='CENTER', colour=CONTROL_TEXT_COLOUR)
        self._make_label('start', 'START BATTLE', (0.0, -0.40, 0.0), 1.16,
                         0.12, anchor='CENTER', colour=CONTROL_TEXT_COLOUR)
        self._make_label('close', 'LEAVE', (0.0, -0.78, 0.0), 0.46, 0.12,
                         anchor='CENTER', colour=CONTROL_TEXT_COLOUR)
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
                ('position', (position[0], position[1],
                              position[2] + CONTROL_FRAME_OFFSET)),
                ('width', width + 0.014), ('height', height + 0.030),
                ('materialFX', 'SOLID'), ('colour', (120, 158, 186, 245)),
                ('focus', False), ('mouseButtonFocus', False),
                ('crossFocus', False), ('moveFocus', False),
                ('visible', False)):
            self._set(frame, name, value)
        self._panel.addChild(frame)
        self._frames[role] = frame
        component = self._surface.simple(CONTROL_TEXTURE)
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
        self._pointer_logged = None
        self._pointer_moves = 0
        self._pointer_ticks = 0
        self._build_pointer()
        self._move_pointer()
        self._start_pointer_tick()
        self.refresh()
        _log('LAN waiting room opened')
        return True

    # One small quad over a slightly larger dark one reads as a pointer.
    # mouse_cursors.xml is 12 bytes and no arrow bitmap ships outside the
    # lobby SWF, so the room draws its own.  Every property below is copied
    # from the room buttons, which Peng can see and click.
    POINTER_WIDTH = 14
    POINTER_HEIGHT = 20
    POINTER_BORDER = 4
    POINTER_SHADOW = (0, 0, 0, 230)
    POINTER_FILL = (255, 255, 255, 255)

    def move_pointer(self):
        """Public move hook: the control scripts call this on every move."""
        if not self._open:
            return False
        self._pointer_moves += 1
        return self._move_pointer()

    def _panel_clip_step(self):
        """Return one pixel in the panel's own clip units.

        A child's CLIP size spans the parent, so the buttons' 1.20 width is
        1.20 half-panels, not 1.20 screens.
        """
        return (2.0 / float(PANEL_WIDTH), 2.0 / float(PANEL_HEIGHT))

    def _build_pointer(self):
        """Create the drawn pointer once, as children of the room panel.

        The buttons render and the standalone roots did not, so the pointer
        now takes the buttons' exact property set: a panel child, CLIP size,
        CLIP position, CENTER anchors and the flat SOLID material.
        ``PyGUI.Utils.absoluteClipPosition`` sums a position up the parent
        chain, so a child offset is the cursor's clip position minus the
        panel's.
        """
        if self._panel is None:
            return False
        step_x, step_y = self._panel_clip_step()
        if not self._pointer_probes:
            self._build_pointer_probes(step_x, step_y)
        if self._pointer_parts:
            return False
        parts = []
        for colour, grow, depth in (
                (self.POINTER_SHADOW, self.POINTER_BORDER,
                 CONTROL_Z - CONTROL_FRAME_OFFSET),
                (self.POINTER_FILL, 0, CONTROL_Z - 2 * CONTROL_FRAME_OFFSET)):
            part = self._surface.simple(CONTROL_TEXTURE)
            for name, value in (
                    ('horizontalPositionMode', 'CLIP'),
                    ('verticalPositionMode', 'CLIP'),
                    ('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
                    ('horizontalAnchor', 'CENTER'),
                    ('verticalAnchor', 'CENTER'),
                    ('position', (0.0, 0.0, depth)),
                    ('width', (self.POINTER_WIDTH + grow) * step_x),
                    ('height', (self.POINTER_HEIGHT + grow) * step_y),
                    ('materialFX', 'SOLID'), ('colour', colour),
                    ('focus', False), ('mouseButtonFocus', False),
                    ('crossFocus', False), ('moveFocus', False),
                    ('visible', False)):
                self._set(part, name, value)
            self._panel.addChild(part)
            parts.append((part, depth))
        self._pointer_parts = parts
        _log('LAN room pointer built parts=%d size=%dx%d' % (
            len(parts), self.POINTER_WIDTH, self.POINTER_HEIGHT))
        return True

    def _remove_pointer_probes(self):
        """Drop the tint marks; every one of them is a GUI root."""
        for name, probe in sorted(self._pointer_probes.items()):
            try:
                self._surface.remove_root(probe)
            except Exception as error:
                _log('LAN room tint probe %s not removed: %s' % (name, error))
        self._pointer_probes = {}
        return True

    def _build_pointer_probes(self, unused_step_x, unused_step_y):
        """Draw one labelled square per tint candidate, left to right.

        Every square is a GUI root so no parent rect can clip it, and each
        varies exactly one of texture, materialFX and colour.  One screenshot
        then names the combination that tints instead of drawing white.
        """
        white = (255, 255, 255, 255)
        dark = (5, 12, 20, 245)
        green = (40, 118, 64, 245)
        dds = CONTROL_TEXTURE
        bmp = 'system/maps/col_white.bmp'
        for index, (name, texture, effect, colour) in enumerate((
                ('1-dds-solid-white', dds, 'SOLID', white),
                ('2-dds-solid-dark', dds, 'SOLID', dark),
                ('3-dds-blend-dark', dds, 'BLEND', dark),
                ('4-dds-add-dark', dds, 'ADD', dark),
                ('5-dds-blend-green', dds, 'BLEND', green),
                ('6-bmp-solid-dark', bmp, 'SOLID', dark),
                ('7-bmp-blend-dark', bmp, 'BLEND', dark),
                ('8-none-solid-dark', PANEL_TEXTURE, 'SOLID', dark))):
            probe = self._surface.simple(texture)
            for property_name, value in (
                    ('horizontalPositionMode', 'CLIP'),
                    ('verticalPositionMode', 'CLIP'),
                    ('widthMode', 'PIXEL'), ('heightMode', 'PIXEL'),
                    ('horizontalAnchor', 'CENTER'),
                    ('verticalAnchor', 'CENTER'),
                    ('position', (-0.70 + index * 0.20, 0.90,
                                  CONTROL_Z - 2 * CONTROL_FRAME_OFFSET)),
                    ('width', 48.0), ('height', 48.0),
                    ('materialFX', effect), ('colour', colour),
                    ('focus', False), ('mouseButtonFocus', False),
                    ('crossFocus', False), ('moveFocus', False),
                    ('visible', True)):
                self._set_optional(probe, property_name, value)
            self._surface.add_root(probe)
            self._pointer_probes[name] = probe
        resort = getattr(self._surface, 'resort', None)
        if callable(resort):
            resort()
        return True

    def _move_pointer(self):
        """Follow ``mcursor.position`` with the drawn pointer."""
        if not self._pointer_parts or self._panel is None:
            return False
        position = getattr(self._surface, 'cursor_position', None)
        if not callable(position):
            return False
        try:
            x, y = position()
        except Exception as error:
            _log('LAN room pointer read failed: %s' % error)
            return False
        panel = getattr(self._panel, 'position', (0.0, 0.0, 0.0))
        offset_x = x - float(panel[0])
        offset_y = y - float(panel[1])
        for part, depth in self._pointer_parts:
            self._set(part, 'position', (offset_x, offset_y, depth))
            self._set(part, 'visible', True)
        self._report_pointer(x, y)
        return True

    _REPORTED_PROPERTIES = (
        'materialFX', 'widthMode', 'heightMode', 'horizontalPositionMode',
        'verticalPositionMode', 'horizontalAnchor', 'verticalAnchor',
        'position', 'width', 'height', 'colour', 'visible')

    def _describe(self, component):
        if component is None:
            return 'missing'
        pairs = ['parent=%s' % self._parent_of(component),
                 'texture=%r' % getattr(component, 'texture', None)]
        for name in self._REPORTED_PROPERTIES:
            pairs.append('%s=%r' % (name, getattr(component, name, None)))
        return ' '.join(pairs)

    def _parent_of(self, component):
        """Report whether a component is a panel child or a GUI root."""
        children = getattr(self._panel, 'children', None)
        try:
            values = list(children.values()) if hasattr(children, 'values') \
                else list(children or ())
        except Exception:
            return 'unknown'
        for value in values:
            if value is component or (isinstance(value, tuple) and
                                      len(value) == 2 and
                                      value[1] is component):
                return 'panel-child'
        return 'root'

    def _report_pointer(self, x, y):
        """Log the pointer beside a button the player can definitely see."""
        now = time.time()
        if self._pointer_logged is not None and (
                now - self._pointer_logged) < 1.0:
            return False
        self._pointer_logged = now
        _log('LAN room pointer mcursor=(%.4f, %.4f) moves=%d ticks=%d' % (
            x, y, self._pointer_moves, self._pointer_ticks))
        state = getattr(self._surface, 'cursor_state', None)
        if callable(state):
            try:
                _log('LAN room pointer native: %r' % (state(),))
            except Exception as error:
                _log('LAN room pointer native state failed: %s' % error)
        for role in sorted(self._pointer_probes):
            _log('LAN room tint %-18s: %s' % (
                role, self._describe(self._pointer_probes[role])))
        _log('LAN room pointer   part: %s' % self._describe(
            self._pointer_parts[-1][0]))
        _log('LAN room pointer button: %s' % self._describe(
            self._controls.get('close')))
        _log('LAN room pointer  panel: %s' % self._describe(self._panel))
        return True

    def _start_pointer_tick(self):
        """Follow the mouse from a callback instead of a GUI move event."""
        tick = getattr(self._surface, 'tick', None)
        if self._pointer_tick is not None or not callable(tick):
            return False
        self._pointer_tick = tick(POINTER_TICK_SECONDS, self._pointer_step)
        return True

    def _pointer_step(self):
        self._pointer_tick = None
        if not self._open:
            return False
        self._pointer_ticks += 1
        self._move_pointer()
        return self._start_pointer_tick()

    def _stop_pointer_tick(self):
        handle = self._pointer_tick
        self._pointer_tick = None
        cancel = getattr(self._surface, 'cancel_tick', None)
        if handle is None or not callable(cancel):
            return False
        try:
            cancel(handle)
        except Exception:
            return False
        return True

    def _hide_pointer(self):
        for part, unused_depth in self._pointer_parts:
            self._set(part, 'visible', False)
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
        self._stop_pointer_tick()
        self._hide_pointer()
        self._set(self._panel, 'visible', False)
        self._surface.remove_root(self._panel)
        self._remove_pointer_probes()
        _log('LAN waiting room closed')
        return True

    def uninstall(self):
        self.close()
        self._panel = None
        self._controls = {}
        self._frames = {}
        self._labels = {}
        self._pointer_parts = []
        self._pointer_probes = {}
        return True
