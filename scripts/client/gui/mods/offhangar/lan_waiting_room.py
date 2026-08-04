# -*- coding: utf-8 -*-
"""Clickable LAN waiting-room overlay for the 0.8.2 Prebattle page."""


PANEL_TEXTURE = 'system/maps/col_white.bmp'

_active = False
_player = None
_panel = None
_text = None
_controls = {}
_selected_map = None
_status = ''
_hover_control = None
_cursor_acquired = False


def _safe_set(obj, name, value):
	try:
		setattr(obj, name, value)
		return True
	except Exception:
		return False


def _log(message):
	try:
		from gui.mods.offhangar.logging import LOG_NOTE
		LOG_NOTE(message)
	except Exception:
		pass


def _make_simple():
	import GUI
	return GUI.Simple(PANEL_TEXTURE)


def _acquire_cursor():
	global _cursor_acquired
	if _cursor_acquired:
		return
	try:
		from gui.Cursor import showCursor
		showCursor(True)
		_cursor_acquired = True
	except Exception:
		try:
			import BigWorld, GUI
			BigWorld.setCursor(GUI.mcursor())
			GUI.mcursor().visible = True
			_cursor_acquired = True
		except Exception:
			pass


def _release_cursor():
	global _cursor_acquired
	if not _cursor_acquired:
		return
	try:
		from gui.Cursor import showCursor
		showCursor(False)
	except Exception:
		try:
			import BigWorld, GUI
			GUI.mcursor().visible = False
			BigWorld.setCursor(BigWorld.dcursor())
		except Exception:
			pass
	_cursor_acquired = False


def _friendly_map_name(map_name):
	parts = str(map_name or '').split('_')
	prefix = ''
	if parts and parts[0].isdigit():
		prefix = parts[0] + ' - '
		parts = parts[1:]
	return prefix + (' '.join([part.capitalize() for part in parts]) or 'Unknown')


def _client():
	try:
		return getattr(_player, '_offhangar_network_client', None)
	except Exception:
		return None


def _map_options():
	client = _client()
	options = list(getattr(client, 'available_maps', None) or []) if client else []
	if not options and client is not None and getattr(client, 'map_name', None):
		options = [client.map_name]
	return options


class _PanelScript(object):
	def __init__(self, component):
		self.component = component

	def handleKeyEvent(self, event):
		return False


class _ControlScript(object):
	def __init__(self, role, component):
		self.role = role
		self.component = component

	def handleMouseClickEvent(self, component):
		_activate(self.role)
		return True

	def handleMouseEnterEvent(self, component):
		global _hover_control
		_hover_control = self.role
		_paint_controls()
		return True

	def handleMouseLeaveEvent(self, component):
		global _hover_control
		if _hover_control == self.role:
			_hover_control = None
		_paint_controls()
		return True

	def handleMouseButtonEvent(self, component, event):
		return True

	def handleKeyEvent(self, event):
		return False


def _make_control(role, position, width, height):
	import GUI
	component = _make_simple()
	for name, value in (
			('horizontalPositionMode', 'CLIP'), ('verticalPositionMode', 'CLIP'),
			('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
			('horizontalAnchor', 'LEFT'), ('verticalAnchor', 'TOP'),
			('position', position), ('width', width), ('height', height),
			('materialFX', 'SOLID'), ('colour', (24, 55, 78, 245)),
			('focus', True), ('mouseButtonFocus', True), ('crossFocus', True),
			('visible', False)):
		_safe_set(component, name, value)
	_safe_set(component, 'script', _ControlScript(role, component))
	GUI.addRoot(component)
	_controls[role] = component


def _make_panel():
	global _panel, _text, _controls
	try:
		import GUI
		_panel = _make_simple()
		for name, value in (
				('horizontalPositionMode', 'CLIP'), ('verticalPositionMode', 'CLIP'),
				('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
				('horizontalAnchor', 'LEFT'), ('verticalAnchor', 'TOP'),
				('position', (-0.70, 0.66, 0.02)), ('width', 1.40),
				('height', 0.42), ('materialFX', 'SOLID'),
				('colour', (5, 12, 20, 245)), ('focus', True),
				('visible', False)):
			_safe_set(_panel, name, value)
		_safe_set(_panel, 'script', _PanelScript(_panel))
		GUI.addRoot(_panel)

		_text = GUI.Text()
		for name, value in (
				('horizontalPositionMode', 'CLIP'), ('verticalPositionMode', 'CLIP'),
				('horizontalAnchor', 'LEFT'), ('verticalAnchor', 'TOP'),
				('position', (-0.65, 0.615, 0.01)), ('font', 'default_small.font'),
				('colour', (255, 255, 255, 255)), ('multiline', True),
				('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
				('width', 1.30), ('height', 0.34), ('shadow', True),
				('dropShadow', True), ('visible', False)):
			_safe_set(_text, name, value)
		GUI.addRoot(_text)

		_controls = {}
		_make_control('previous', (-0.64, 0.385, 0.00), 0.18, 0.075)
		_make_control('map', (-0.43, 0.385, 0.00), 0.86, 0.075)
		_make_control('next', (0.46, 0.385, 0.00), 0.18, 0.075)
		_make_control('start', (-0.43, 0.215, 0.00), 0.86, 0.095)
		method = getattr(GUI, 'reSort', None)
		if callable(method):
			method()
		return True
	except Exception:
		_panel = None
		_text = None
		return False


def _set_visible(value):
	if _panel is not None:
		_safe_set(_panel, 'visible', bool(value))
	if _text is not None:
		_safe_set(_text, 'visible', bool(value))
	for component in _controls.values():
		_safe_set(component, 'visible', bool(value))


def _paint_controls():
	for role, component in _controls.items():
		if role == _hover_control:
			colour = (62, 137, 190, 245)
		elif role == 'start':
			colour = (40, 118, 64, 245)
		elif role == 'map':
			colour = (38, 104, 154, 245)
		else:
			colour = (24, 55, 78, 235)
		_safe_set(component, 'colour', colour)


def _refresh():
	if not _active or _text is None:
		return
	client = _client()
	count = int(getattr(client, 'waiting_count', 0) or 0) if client else 0
	map_name = _friendly_map_name(_selected_map)
	message = (
		'LAN WAITING ROOM\n'
		'%d player(s) connected. Choose the battlefield, then click START.\n\n'
		' <              MAP: %-28s              >\n\n'
		'                 START BATTLE\n'
		'Status: %s'
	) % (count, map_name, _status or 'Ready.')
	_safe_set(_text, 'text', message)
	_paint_controls()


def _cycle(step):
	global _selected_map, _status
	options = _map_options()
	if not options:
		_status = 'The server did not provide a map list.'
		_refresh()
		return
	try:
		index = options.index(_selected_map)
	except Exception:
		index = 0
	_selected_map = options[(index + int(step)) % len(options)]
	_status = 'Selected %s.' % _friendly_map_name(_selected_map)
	_refresh()


def _activate(role):
	global _status
	if not _active:
		return False
	if role == 'previous':
		_cycle(-1)
	elif role in ('next', 'map'):
		_cycle(1)
	elif role == 'start':
		client = _client()
		if client is None or getattr(client, 'phase', None) != 'waiting':
			_status = 'The waiting room is no longer active.'
		else:
			from gui.mods.offhangar.network_battle import request_battle_start
			_status = 'Starting %s...' % _friendly_map_name(_selected_map)
			request_battle_start(_player, _selected_map)
			_refresh()
	else:
		return False
	return True


def open(player):
	global _active, _player, _selected_map, _status
	client = getattr(player, '_offhangar_network_client', None) if player else None
	if client is None or not getattr(client, 'ready', False) or client.phase != 'waiting':
		return False
	if _panel is None and not _make_panel():
		return False
	_player = player
	options = list(getattr(client, 'available_maps', None) or [])
	if _selected_map not in options:
		_selected_map = client.map_name if client.map_name in options else (
			options[0] if options else client.map_name)
	_status = 'Ready.'
	_active = True
	_set_visible(True)
	_acquire_cursor()
	_refresh()
	_log('LAN clickable waiting-room panel opened')
	return True


def update(player=None):
	global _player
	if player is not None:
		_player = player
	client = _client()
	if client is None or getattr(client, 'phase', None) != 'waiting':
		close()
		return False
	if not _active:
		return open(_player)
	_refresh()
	return True


def set_status(message):
	global _status
	_status = str(message or '')
	_refresh()


def selected_map():
	return _selected_map


def close():
	global _active, _player
	_active = False
	_set_visible(False)
	_release_cursor()
	_player = None
