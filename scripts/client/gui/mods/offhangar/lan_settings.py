# -*- coding: utf-8 -*-
"""Small in-game LAN settings panel for the 0.8.2 offline hangar.

The client already embeds Python 2, so this module deliberately uses that
runtime and does not require a separate Python installation. The garage entry
is clickable; F11 and keyboard navigation remain available as fallbacks.
"""

import io
import json


# This texture is used by the GUI tests bundled with the exact 0.8.2 client.
PANEL_TEXTURE = 'system/maps/col_white.bmp'

_active = False
_field = 0
_host = ''
_port = ''
_mode_enabled = False
_status = ''
_panel = None
_text = None
_controls = {}
_labels = {}
_hover_control = None
_replace_on_type = False
_cursor_acquired = False
_diagnostic_logged = False
_entry_panel = None
_entry_text = None
_entry_script = None
_entry_poll_scheduled = False
_game_key_hook_installed = False


def _safe_set(obj, name, value):
	try:
		setattr(obj, name, value)
		return True
	except Exception:
		return False


def _make_simple():
	"""Create a solid component; 0.8.2 requires an explicit texture name."""
	import GUI
	return GUI.Simple(PANEL_TEXTURE)


def _make_window():
	"""Create a real parent window so its children share one coordinate space."""
	import GUI
	return GUI.Window(PANEL_TEXTURE)


def _log(message):
	try:
		from gui.mods.offhangar.logging import LOG_DEBUG
		LOG_DEBUG(message)
	except Exception:
		pass


def _log_note(message):
	try:
		from gui.mods.offhangar.logging import LOG_NOTE
		LOG_NOTE(message)
	except Exception:
		pass


def _log_error(message):
	try:
		from gui.mods.offhangar.logging import LOG_ERROR
		LOG_ERROR(message)
	except Exception:
		pass


def _notify(message, level='information'):
	"""Use the stock 0.8.2 lower-right system-message channel."""
	try:
		from gui.SystemMessages import SM_TYPE, pushMessage
		if level == 'error':
			message_type = SM_TYPE.Error
		elif level == 'warning':
			message_type = SM_TYPE.Warning
		else:
			message_type = SM_TYPE.Information
		text = str(message)
		try:
			text = text.encode('utf-8')
		except Exception:
			pass
		pushMessage(text, message_type)
		return True
	except Exception:
		return False


def _acquire_cursor():
	global _cursor_acquired
	if _cursor_acquired:
		return
	try:
		# gui.Cursor.showCursor is the exact reference-counted API used by the
		# 0.8.2 client. It switches BigWorld to GUI.mcursor and makes it visible.
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
			_log_error('LAN settings could not show the mouse cursor')


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


def _key(Keys, *names):
	for name in names:
		value = getattr(Keys, name, None)
		if value is not None:
			return value
	return -999999


def _is_down(event):
	try:
		return bool(event.isKeyDown())
	except Exception:
		return True


class _EntryScript(object):
	"""Mouse target for the persistent garage entry."""

	def __init__(self, component):
		self.component = component

	def handleMouseClickEvent(self, component):
		_log_note('LAN settings garage entry clicked')
		open()
		return True

	def handleMouseEnterEvent(self, component):
		return True

	def handleMouseLeaveEvent(self, component):
		return True

	def handleMouseButtonEvent(self, component, event):
		return True

	def handleKeyEvent(self, event):
		return False


class _PanelScript(object):
	"""Forward keyboard input while the settings panel owns GUI focus."""

	def __init__(self, component):
		self.component = component

	def handleKeyEvent(self, event):
		return handle_key_event(event)


class _ControlScript(object):
	"""Clickable and hoverable field/button target."""

	def __init__(self, role, component):
		self.role = role
		self.component = component

	def handleMouseClickEvent(self, component):
		_activate_control(self.role)
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
		return handle_key_event(event)


def _in_battle():
	try:
		from gui import WindowsManager
		return getattr(WindowsManager.g_windowsManager, 'battleWindow', None) is not None
	except Exception:
		return False


def _offline_hangar_ready():
	try:
		import BigWorld
		player = BigWorld.player()
		return bool(player is not None and getattr(player, 'isOffline', False) and not _in_battle())
	except Exception:
		return False


def _resort_gui():
	try:
		import GUI
		method = getattr(GUI, 'reSort', None)
		if callable(method):
			method()
	except Exception:
		pass


def _network_enabled():
	try:
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		return bool(CONFIG_OPTIONS.get('network_mode', False))
	except Exception:
		return False


def _refresh_entry():
	if _entry_text is None:
		return
	mode = 'ON' if _network_enabled() else 'OFF'
	_safe_set(_entry_text, 'text', 'LAN: %s   |   LAN SETTINGS' % mode)


def _set_entry_visible(value):
	if _entry_panel is not None:
		_safe_set(_entry_panel, 'visible', bool(value))
	if _entry_text is not None:
		_safe_set(_entry_text, 'visible', bool(value))
	if value:
		_refresh_entry()


def _make_entry():
	global _entry_panel, _entry_text, _entry_script
	try:
		import GUI
		_entry_panel = _make_simple()
		_safe_set(_entry_panel, 'horizontalPositionMode', 'CLIP')
		_safe_set(_entry_panel, 'verticalPositionMode', 'CLIP')
		_safe_set(_entry_panel, 'widthMode', 'CLIP')
		_safe_set(_entry_panel, 'heightMode', 'CLIP')
		_safe_set(_entry_panel, 'horizontalAnchor', 'LEFT')
		_safe_set(_entry_panel, 'verticalAnchor', 'TOP')
		# The lobby Scaleform root is at z=0.5. In this client a smaller z value
		# is in front (see its bundled GUITest.localReSort test).
		_safe_set(_entry_panel, 'position', (0.52, 0.88, 0.10))
		_safe_set(_entry_panel, 'width', 0.42)
		_safe_set(_entry_panel, 'height', 0.07)
		_safe_set(_entry_panel, 'materialFX', 'SOLID')
		_safe_set(_entry_panel, 'colour', (12, 32, 52, 255))
		_safe_set(_entry_panel, 'focus', True)
		_safe_set(_entry_panel, 'mouseButtonFocus', True)
		_safe_set(_entry_panel, 'crossFocus', True)
		_safe_set(_entry_panel, 'moveFocus', True)
		_entry_script = _EntryScript(_entry_panel)
		_safe_set(_entry_panel, 'script', _entry_script)
		_safe_set(_entry_panel, 'visible', False)
		GUI.addRoot(_entry_panel)

		_entry_text = GUI.Text()
		_safe_set(_entry_text, 'horizontalPositionMode', 'CLIP')
		_safe_set(_entry_text, 'verticalPositionMode', 'CLIP')
		_safe_set(_entry_text, 'horizontalAnchor', 'LEFT')
		_safe_set(_entry_text, 'verticalAnchor', 'TOP')
		_safe_set(_entry_text, 'position', (0.545, 0.86, 0.05))
		_safe_set(_entry_text, 'font', 'default_small.font')
		_safe_set(_entry_text, 'colour', (255, 255, 255, 255))
		_safe_set(_entry_text, 'multiline', False)
		_safe_set(_entry_text, 'widthMode', 'CLIP')
		_safe_set(_entry_text, 'heightMode', 'CLIP')
		_safe_set(_entry_text, 'width', 0.37)
		_safe_set(_entry_text, 'height', 0.04)
		_safe_set(_entry_text, 'shadow', True)
		_safe_set(_entry_text, 'dropShadow', True)
		# A decorative Text root must never sit in front of the clickable panel.
		_safe_set(_entry_text, 'focus', False)
		_safe_set(_entry_text, 'mouseButtonFocus', False)
		_safe_set(_entry_text, 'crossFocus', False)
		_safe_set(_entry_text, 'moveFocus', False)
		_safe_set(_entry_text, 'visible', False)
		GUI.addRoot(_entry_text)
		_refresh_entry()
		_resort_gui()
		_log_note('LAN settings garage entry created')
		return True
	except Exception:
		_entry_panel = None
		_entry_text = None
		_entry_script = None
		try:
			import traceback
			_log_error('LAN settings garage entry creation failed: %s' % traceback.format_exc())
		except Exception:
			_log_error('LAN settings garage entry creation failed')
		return False


def _entry_tick():
	global _entry_poll_scheduled
	_entry_poll_scheduled = False
	try:
		ready = _offline_hangar_ready()
		if ready and _entry_panel is None:
			_make_entry()
		_set_entry_visible(ready and not _active)
		if _active:
			_refresh()
	except Exception:
		_log_error('LAN settings garage entry update failed')
	try:
		import BigWorld
		_entry_poll_scheduled = True
		BigWorld.callback(0.5, _entry_tick)
	except Exception:
		_entry_poll_scheduled = False


def ensure_entry():
	"""Create the visible garage entry and keep it out of battles."""
	global _entry_poll_scheduled
	ready = _offline_hangar_ready()
	if ready and _entry_panel is None:
		_make_entry()
	_set_entry_visible(ready and not _active)
	if not _entry_poll_scheduled:
		try:
			import BigWorld
			_entry_poll_scheduled = True
			BigWorld.callback(0.5, _entry_tick)
		except Exception:
			_entry_poll_scheduled = False


def _load_values():
	global _host, _port, _mode_enabled, _field, _status, _replace_on_type
	try:
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		host = CONFIG_OPTIONS.get('network_server_host', '127.0.0.1')
		port = CONFIG_OPTIONS.get('network_server_port', 28782)
		_mode_enabled = bool(CONFIG_OPTIONS.get('network_mode', False))
	except Exception:
		host, port = '127.0.0.1', 28782
		_mode_enabled = False
	_host = str(host or '127.0.0.1')
	try:
		_port = str(int(port or 28782))
	except Exception:
		_port = '28782'
	_field = 0
	_status = 'Select a blue row with the mouse, or press TAB.'
	_replace_on_type = True


def _add_panel_child(component):
	_panel.addChild(component)
	return component


def _make_label(role, text, position, width, height, anchor='LEFT',
		colour=(255, 255, 255, 255)):
	import GUI
	component = GUI.Text()
	for name, value in (
			('text', text),
			('horizontalPositionMode', 'CLIP'),
			('verticalPositionMode', 'CLIP'),
			('widthMode', 'CLIP'), ('heightMode', 'CLIP'),
			('horizontalAnchor', anchor), ('verticalAnchor', 'CENTER'),
			('position', position), ('width', width), ('height', height),
			('font', 'default_small.font'), ('colour', colour),
			('multiline', False), ('shadow', True), ('dropShadow', True),
			('focus', False), ('mouseButtonFocus', False),
			('crossFocus', False), ('moveFocus', False), ('visible', True)):
		_safe_set(component, name, value)
	_add_panel_child(component)
	_labels[role] = component
	return component


def _make_control(role, position, width, height):
	import GUI
	component = _make_simple()
	_safe_set(component, 'horizontalPositionMode', 'CLIP')
	_safe_set(component, 'verticalPositionMode', 'CLIP')
	_safe_set(component, 'widthMode', 'CLIP')
	_safe_set(component, 'heightMode', 'CLIP')
	_safe_set(component, 'horizontalAnchor', 'CENTER')
	_safe_set(component, 'verticalAnchor', 'CENTER')
	_safe_set(component, 'position', position)
	_safe_set(component, 'width', width)
	_safe_set(component, 'height', height)
	_safe_set(component, 'materialFX', 'SOLID')
	_safe_set(component, 'colour', (24, 55, 78, 235))
	_safe_set(component, 'focus', True)
	_safe_set(component, 'mouseButtonFocus', True)
	_safe_set(component, 'crossFocus', True)
	_safe_set(component, 'moveFocus', True)
	_safe_set(component, 'script', _ControlScript(role, component))
	_safe_set(component, 'visible', False)
	_add_panel_child(component)
	_controls[role] = component
	return component


def _set_controls_visible(value):
	for component in _controls.values():
		_safe_set(component, 'visible', bool(value))
	for component in _labels.values():
		_safe_set(component, 'visible', bool(value))


def _paint_controls():
	for role, component in _controls.items():
		if role == _hover_control:
			colour = (62, 137, 190, 245)
		elif role == 'save':
			colour = (40, 118, 64, 240)
		elif role == 'cancel':
			colour = (110, 48, 48, 240)
		elif ((role == 'host' and _field == 0) or
				(role == 'port' and _field == 1) or
				(role == 'mode' and _field == 2)):
			colour = (38, 104, 154, 245)
		else:
			colour = (24, 55, 78, 235)
		_safe_set(component, 'colour', colour)


def _make_panel():
	global _panel, _text, _controls, _labels
	try:
		import GUI
		_panel = _make_window()
		_safe_set(_panel, 'horizontalPositionMode', 'CLIP')
		_safe_set(_panel, 'verticalPositionMode', 'CLIP')
		_safe_set(_panel, 'widthMode', 'PIXEL')
		_safe_set(_panel, 'heightMode', 'PIXEL')
		_safe_set(_panel, 'horizontalAnchor', 'CENTER')
		_safe_set(_panel, 'verticalAnchor', 'CENTER')
		_safe_set(_panel, 'position', (0.0, 0.0, 0.10))
		_safe_set(_panel, 'width', 720)
		_safe_set(_panel, 'height', 360)
		_safe_set(_panel, 'materialFX', 'SOLID')
		_safe_set(_panel, 'colour', (5, 12, 20, 245))
		_safe_set(_panel, 'visible', False)
		_safe_set(_panel, 'focus', False)
		_safe_set(_panel, 'mouseButtonFocus', False)
		_safe_set(_panel, 'crossFocus', False)
		_safe_set(_panel, 'moveFocus', False)
		_safe_set(_panel, 'script', _PanelScript(_panel))
		_controls = {}
		_labels = {}
		_make_control('host', (0.0, 0.40, 0.05), 1.76, 0.15)
		_make_control('port', (0.0, 0.20, 0.05), 1.76, 0.15)
		_make_control('mode', (0.0, 0.00, 0.05), 1.76, 0.15)
		_make_control('save', (-0.46, -0.72, 0.05), 0.78, 0.18)
		_make_control('cancel', (0.46, -0.72, 0.05), 0.78, 0.18)

		_make_label('title', 'LAN MULTIPLAYER SETTINGS',
			(-0.88, 0.82, 0.00), 1.76, 0.12,
			colour=(232, 244, 255, 255))
		_make_label('help', '', (-0.88, 0.65, 0.00), 1.76, 0.11)
		_make_label('host', '', (-0.82, 0.40, 0.00), 1.62, 0.12)
		_make_label('port', '', (-0.82, 0.20, 0.00), 1.62, 0.12)
		_make_label('mode', '', (-0.82, 0.00, 0.00), 1.62, 0.12)
		_make_label('map', '', (-0.88, -0.19, 0.00), 1.76, 0.10)
		_make_label('connection', '', (-0.88, -0.34, 0.00), 1.76, 0.10)
		_make_label('status', '', (-0.88, -0.49, 0.00), 1.76, 0.10)
		_make_label('save', 'SAVE', (-0.46, -0.72, 0.00), 0.70, 0.12,
			anchor='CENTER')
		_make_label('cancel', 'CANCEL', (0.46, -0.72, 0.00), 0.70, 0.12,
			anchor='CENTER')
		_make_label('keyboard',
			'TAB select  |  SPACE toggle  |  ENTER save  |  ESC cancel',
			(-0.88, -0.91, 0.00), 1.76, 0.08,
			colour=(184, 205, 222, 255))
		_text = _labels['status']
		GUI.addRoot(_panel)
		_resort_gui()
		return True
	except Exception:
		_panel = None
		_text = None
		try:
			import traceback
			_log_error('LAN settings GUI creation failed: %s' % traceback.format_exc())
		except Exception:
			_log_error('LAN settings GUI creation failed')
		_notify('LAN settings window could not be created. See python.log.', 'error')
		return False


def _refresh():
	if _panel is None or not _labels:
		return
	marker_host = '>' if _field == 0 else ' '
	marker_port = '>' if _field == 1 else ' '
	marker_mode = '>' if _field == 2 else ' '
	mode = 'ON' if _mode_enabled else 'OFF'
	connection = 'Not connected - save, then click Battle!'
	try:
		import BigWorld
		client = getattr(BigWorld.player(), '_offhangar_network_client', None)
		if client is not None:
			if getattr(client, '_last_error', None):
				connection = 'ERROR: %s' % str(client._last_error)
			elif getattr(client, 'ready', False):
				connection = '%s - %s player(s), team %s' % (
					str(getattr(client, 'phase', 'connected')).upper(),
					getattr(client, 'waiting_count', 0), getattr(client, 'team', '?'))
			elif getattr(client, 'running', False):
				connection = 'Connecting to %s:%s...' % (client.host, client.port)
	except Exception:
		pass
	_safe_set(_labels['help'], 'text',
		'Click a row to edit. The first typed digit replaces its value.')
	_safe_set(_labels['host'], 'text',
		'%s  SERVER IP       %s' % (marker_host, _host))
	_safe_set(_labels['port'], 'text',
		'%s  TCP PORT        %s' % (marker_port, _port))
	_safe_set(_labels['mode'], 'text',
		'%s  LAN BATTLE      %s' % (marker_mode, mode))
	_safe_set(_labels['map'], 'text',
		'MAP: choose it in the waiting room after joining')
	_safe_set(_labels['connection'], 'text', 'CONNECTION: %s' % connection)
	_safe_set(_labels['status'], 'text', 'STATUS: %s' % _status)
	_safe_set(_panel, 'visible', True)
	_set_controls_visible(True)
	_paint_controls()


def open():
	global _active
	if _in_battle():
		_log('LAN settings open ignored: battle window is active')
		_notify('LAN settings are only available in the garage.', 'warning')
		return False
	_load_values()
	if _panel is None and not _make_panel():
		return False
	_active = True
	_acquire_cursor()
	_safe_set(_panel, 'focus', True)
	_set_entry_visible(False)
	_refresh()
	_log_note('LAN settings panel opened')
	return True


def close():
	global _active
	_active = False
	if _panel is not None:
		_safe_set(_panel, 'visible', False)
		_safe_set(_panel, 'focus', False)
	_set_controls_visible(False)
	_release_cursor()
	_set_entry_visible(not _in_battle())


def _activate_control(role):
	global _field, _status, _replace_on_type
	if not _active:
		return False
	if role == 'host':
		_field = 0
		_replace_on_type = True
		_status = 'Server IP selected. Type an address such as 192.168.1.10.'
	elif role == 'port':
		_field = 1
		_replace_on_type = True
		_status = 'TCP port selected. The default is 28782.'
	elif role == 'mode':
		_field = 2
		_replace_on_type = False
		if _toggle_mode():
			_status = 'LAN battle mode changed. Click SAVE to keep it.'
		else:
			_status = 'Could not change LAN battle mode.'
			_notify(_status, 'error')
	elif role == 'save':
		if _save():
			close()
		else:
			_refresh()
		return True
	elif role == 'cancel':
		close()
		return True
	else:
		return False
	_refresh()
	return True


def _save():
	global _status
	host = str(_host or '').strip()
	parts = host.split('.')
	valid_ip = bool(host) and len(parts) == 4 and all(part.isdigit() and int(part) <= 255 for part in parts)
	if not valid_ip:
		_status = 'Invalid IP: use digits and dots.'
		_notify('LAN settings error: %s' % _status, 'error')
		return False
	try:
		port = int(_port)
	except Exception:
		_status = 'Invalid TCP port.'
		_notify('LAN settings error: %s' % _status, 'error')
		return False
	if port < 1 or port > 65535:
		_status = 'TCP port must be 1-65535.'
		_notify('LAN settings error: %s' % _status, 'error')
		return False
	try:
		from gui.mods.offhangar import paths
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		paths.ensure_user_dir()
		CONFIG_OPTIONS['network_mode'] = bool(_mode_enabled)
		CONFIG_OPTIONS['network_server_host'] = host
		CONFIG_OPTIONS['network_server_port'] = port
		# The server welcome message is authoritative. This value is only the
		# local fallback used when LAN mode cannot connect.
		CONFIG_OPTIONS['network_map_name'] = 'server_random'
		payload = json.dumps(dict(CONFIG_OPTIONS), indent=4, sort_keys=False)
		try:
			payload = payload.decode('utf-8')
		except AttributeError:
			pass
		with io.open(paths.USER_CONFIG_FILE, 'w', encoding='utf-8') as output:
			output.write(payload)
		_log_note('LAN settings saved: mode=%s server=%s:%s' % (
			'ON' if CONFIG_OPTIONS.get('network_mode', False) else 'OFF', host, port))
		_status = 'Saved. Restart battle after changing LAN mode.'
		_notify('LAN settings saved: %s, server %s:%s.' % (
			'ON' if CONFIG_OPTIONS.get('network_mode', False) else 'OFF', host, port))
		return True
	except Exception as error:
		_status = 'Save failed: %s' % str(error)
		_log('LAN settings save failed: %s' % str(error))
		_notify('LAN settings could not be saved: %s' % str(error), 'error')
		return False


def _toggle_mode():
	global _mode_enabled
	_mode_enabled = not bool(_mode_enabled)
	return True


def _append_key(key, Keys):
	global _host, _port, _replace_on_type
	if _field == 0:
		for index in range(10):
			if key == _key(Keys, 'KEY_%d' % index):
				if _replace_on_type:
					_host = ''
					_replace_on_type = False
				_host += str(index)
				return True
		if key == _key(Keys, 'KEY_PERIOD', 'KEY_DOT', 'KEY_DECIMAL'):
			if _replace_on_type:
				_host = ''
				_replace_on_type = False
			_host += '.'
			return True
	elif _field == 1:
		for index in range(10):
			if key == _key(Keys, 'KEY_%d' % index):
				if _replace_on_type:
					_port = ''
					_replace_on_type = False
				_port += str(index)
				return True
	return False


def _backspace():
	global _host, _port, _replace_on_type
	_replace_on_type = False
	if _field == 0:
		_host = _host[:-1]
		return True
	if _field == 1:
		_port = _port[:-1]
		return True
	return False


def handle_key_event(event):
	"""Return True when the panel consumes the event."""
	global _active, _field, _status, _diagnostic_logged, _replace_on_type
	try:
		import Keys
		key = event.key
		# DirectInput DIK_F11 is 87. Keep it as a fallback for old Keys modules
		# that do not expose KEY_F11 by name.
		f11 = getattr(Keys, 'KEY_F11', 87)
		if not _diagnostic_logged:
			_log_note('LAN settings key hook received key=%s down=%s KEY_F11=%s' % (
				key, _is_down(event), getattr(Keys, 'KEY_F11', '<missing>')))
			_diagnostic_logged = True
		if key == f11 and _is_down(event):
			_log_note('LAN settings F11 matched')
			if _active:
				close()
			else:
				open()
			return True
		if not _active:
			return False
		if _in_battle():
			close()
			return False
		if not _is_down(event):
			return True
		if key == _key(Keys, 'KEY_ESCAPE'):
			close()
			return True
		if key == _key(Keys, 'KEY_TAB'):
			_field = (_field + 1) % 3
			_replace_on_type = _field in (0, 1)
			_status = 'Selected %s.' % ('Server IP' if _field == 0 else
				'TCP port' if _field == 1 else 'LAN battle mode')
			_refresh()
			return True
		if key == _key(Keys, 'KEY_BACKSPACE', 'KEY_DELETE'):
			_backspace()
			_refresh()
			return True
		if key == _key(Keys, 'KEY_SPACE') and _field == 2:
			_toggle_mode()
			_refresh()
			return True
		if key == _key(Keys, 'KEY_RETURN', 'KEY_ENTER'):
			if _save():
				close()
			else:
				_refresh()
			return True
		if _append_key(key, Keys):
			_status = ''
			_refresh()
			return True
		return True
	except Exception:
		try:
			import traceback
			_log_error('LAN settings key handling failed: %s' % traceback.format_exc())
		except Exception:
			_log_error('LAN settings key handling failed')
		_notify('LAN settings input failed. See python.log for details.', 'error')
		return _active


def install():
	"""Install the 0.8.2 global key hook and start the garage-entry poll."""
	global _game_key_hook_installed
	try:
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		_log_note('LAN config loaded: mode=%s server=%s:%s' % (
			'ON' if CONFIG_OPTIONS.get('network_mode', False) else 'OFF',
			CONFIG_OPTIONS.get('network_server_host', '127.0.0.1'),
			CONFIG_OPTIONS.get('network_server_port', 28782)))
	except Exception:
		_log_error('LAN config could not be read')
	if not _game_key_hook_installed:
		try:
			import game
			base_handler = game.handleKeyEvent
			if getattr(base_handler, '_offhangar_lan_settings_hook', False):
				_game_key_hook_installed = True
			else:
				def _game_handle_key_event(event):
					if handle_key_event(event):
						return True
					return base_handler(event)
				_game_handle_key_event._offhangar_lan_settings_hook = True
				game.handleKeyEvent = _game_handle_key_event
				_game_key_hook_installed = True
				_log_note('LAN settings global game key hook installed')
		except Exception:
			try:
				import traceback
				_log_error('LAN settings key-hook install failed: %s' % traceback.format_exc())
			except Exception:
				_log_error('LAN settings key-hook install failed')
	ensure_entry()
	return _game_key_hook_installed
