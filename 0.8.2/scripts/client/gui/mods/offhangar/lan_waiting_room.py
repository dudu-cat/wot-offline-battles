# -*- coding: utf-8 -*-
"""Clickable LAN waiting-room overlay for the 0.8.2 Prebattle page."""


PANEL_TEXTURE = 'system/maps/col_white.bmp'

_active = False
_player = None
_offline = False
_on_start = None
_on_cancel = None
_offline_options = ()
_panel = None
_text = None
_controls = {}
_labels = {}
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


def _make_window():
	import GUI
	return GUI.Window(PANEL_TEXTURE)


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
	if _offline:
		return list(_offline_options)
	client = _client()
	options = list(getattr(client, 'available_maps', None) or []) if client else []
	if not options and client is not None and getattr(client, 'map_name', None):
		options = [client.map_name]
	return options


def offline_map_options():
	"""Return the maps this client can drive bots on."""
	try:
		from gui.mods.offhangar.prebaked_navigation import STOCK_MAPS
		return list(STOCK_MAPS)
	except Exception:
		return []


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
	component = _make_simple()
	for name, value in (
			('horizontalPositionMode', 'CLIP'), ('verticalPositionMode', 'CLIP'),
			('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
			('horizontalAnchor', 'CENTER'), ('verticalAnchor', 'CENTER'),
			('position', position), ('width', width), ('height', height),
			('materialFX', 'SOLID'), ('colour', (24, 55, 78, 245)),
			('focus', True), ('mouseButtonFocus', True), ('crossFocus', True),
			('moveFocus', True),
			('visible', False)):
		_safe_set(component, name, value)
	_safe_set(component, 'script', _ControlScript(role, component))
	_panel.addChild(component)
	_controls[role] = component
	return component


def _make_label(role, text, position, width, height, anchor='LEFT',
		colour=(255, 255, 255, 255)):
	import GUI
	component = GUI.Text()
	for name, value in (
			('text', text),
			('horizontalPositionMode', 'CLIP'), ('verticalPositionMode', 'CLIP'),
			('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
			('horizontalAnchor', anchor), ('verticalAnchor', 'CENTER'),
			('position', position), ('width', width), ('height', height),
			('font', 'default_small.font'), ('colour', colour),
			('multiline', False), ('shadow', True), ('dropShadow', True),
			('focus', False), ('mouseButtonFocus', False),
			('crossFocus', False), ('moveFocus', False), ('visible', True)):
		_safe_set(component, name, value)
	_panel.addChild(component)
	_labels[role] = component
	return component


def _make_panel():
	global _panel, _text, _controls, _labels
	try:
		import GUI
		_panel = _make_window()
		for name, value in (
				('horizontalPositionMode', 'CLIP'), ('verticalPositionMode', 'CLIP'),
				('widthMode', 'PIXEL'), ('heightMode', 'PIXEL'),
				('horizontalAnchor', 'CENTER'), ('verticalAnchor', 'CENTER'),
				('position', (0.0, 0.0, 0.10)), ('width', 680),
				('height', 280), ('materialFX', 'SOLID'),
				('colour', (5, 12, 20, 245)), ('focus', True),
				('mouseButtonFocus', False), ('crossFocus', False),
				('moveFocus', False),
				('visible', False)):
			_safe_set(_panel, name, value)
		_safe_set(_panel, 'script', _PanelScript(_panel))
		_controls = {}
		_labels = {}
		_make_control('previous', (-0.72, 0.15, 0.05), 0.20, 0.22)
		_make_control('map', (0.0, 0.15, 0.05), 1.15, 0.22)
		_make_control('next', (0.72, 0.15, 0.05), 0.20, 0.22)
		_make_control('start', (0.0, -0.32, 0.05), 1.62, 0.24)
		_make_control('cancel', (0.0, -0.68, 0.05), 0.60, 0.20)
		_make_label('title', 'LAN WAITING ROOM', (-0.84, 0.78, 0.00),
			1.68, 0.12, colour=(232, 244, 255, 255))
		_make_label('count', '', (-0.84, 0.54, 0.00), 1.68, 0.11)
		_make_label('previous', '<', (-0.72, 0.15, 0.00), 0.18, 0.12,
			anchor='CENTER')
		_make_label('map', '', (0.0, 0.15, 0.00), 1.10, 0.12,
			anchor='CENTER')
		_make_label('next', '>', (0.72, 0.15, 0.00), 0.18, 0.12,
			anchor='CENTER')
		_make_label('start', 'START BATTLE', (0.0, -0.32, 0.00),
			1.58, 0.12, anchor='CENTER')
		_make_label('cancel', 'LEAVE', (0.0, -0.68, 0.00), 0.56, 0.12,
			anchor='CENTER')
		_make_label('status', '', (-0.84, -0.90, 0.00), 1.68, 0.12,
			colour=(184, 205, 222, 255))
		_text = _labels['status']
		GUI.addRoot(_panel)
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
	for component in _controls.values():
		_safe_set(component, 'visible', bool(value))
	for component in _labels.values():
		_safe_set(component, 'visible', bool(value))


def _paint_controls():
	for role, component in _controls.items():
		if role == _hover_control:
			colour = (62, 137, 190, 245)
		elif role == 'start':
			colour = (40, 118, 64, 245)
		elif role == 'cancel':
			colour = (110, 48, 48, 240)
		elif role == 'map':
			colour = (38, 104, 154, 245)
		else:
			colour = (24, 55, 78, 235)
		_safe_set(component, 'colour', colour)


def _refresh():
	if not _active or not _labels:
		return
	client = _client()
	count = int(getattr(client, 'waiting_count', 0) or 0) if client else 0
	map_name = _friendly_map_name(_selected_map)
	if _offline:
		_safe_set(_labels['count'], 'text',
			'Single player. Choose the battlefield, then click START.')
	else:
		_safe_set(_labels['count'], 'text',
			'%d player(s) connected. Choose the battlefield, then click START.' % count)
	_safe_set(_labels['map'], 'text', 'MAP: %s' % map_name)
	_safe_set(_labels['status'], 'text', 'STATUS: %s' % (_status or 'Ready.'))
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
		if _offline:
			start = _on_start
			_status = 'Starting %s...' % _friendly_map_name(_selected_map)
			_refresh()
			close()
			if callable(start):
				start(_selected_map)
			return True
		client = _client()
		if client is None or getattr(client, 'phase', None) != 'waiting':
			_status = 'The waiting room is no longer active.'
		else:
			from gui.mods.offhangar.network_battle import request_battle_start
			_status = 'Starting %s...' % _friendly_map_name(_selected_map)
			request_battle_start(_player, _selected_map)
			_refresh()
	elif role == 'cancel':
		cancel = _on_cancel
		player = _player
		offline = _offline
		close()
		if callable(cancel):
			cancel()
		elif not offline:
			from gui.mods.offhangar.network_battle import stop_for_player
			stop_for_player(player)
		return True
	else:
		return False
	return True


def open_offline(player, on_start=None, on_cancel=None, options=None):
	"""Show the same room for a single-player queue."""
	global _offline, _on_start, _on_cancel, _offline_options
	global _active, _player, _selected_map, _status
	_offline_options = tuple(options if options is not None
		else offline_map_options())
	if not _offline_options:
		return False
	if _panel is None and not _make_panel():
		return False
	_offline = True
	_on_start = on_start
	_on_cancel = on_cancel
	_player = player
	if _selected_map not in _offline_options:
		_selected_map = _offline_options[0]
	_status = 'Ready.'
	_active = True
	_set_visible(True)
	_acquire_cursor()
	_refresh()
	_log('offline map room opened')
	return True


def open(player):
	global _active, _player, _selected_map, _status, _offline
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
	_offline = False
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
	global _active, _player, _offline, _on_start, _on_cancel
	_active = False
	_offline = False
	_on_start = None
	_on_cancel = None
	_set_visible(False)
	_release_cursor()
	_player = None
