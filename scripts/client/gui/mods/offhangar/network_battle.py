# -*- coding: utf-8 -*-
"""LAN transport for the offhangar network MVP.

The 0.8.2 client embeds Python 2, so this module intentionally uses only
Python-2-compatible syntax and the standard library.  Socket I/O happens on a
worker thread; BigWorld objects are touched only from the main-thread poll
callback.  The original garage and offline battle path remain untouched until
``network_mode`` is enabled.
"""

import json
import math
import socket
import threading
import time

import BigWorld

from gui.mods.offhangar.logging import LOG_DEBUG, LOG_ERROR, LOG_NOTE


PROTOCOL_VERSION = 6
POLL_INTERVAL = 1.0 / 60.0
INPUT_INTERVAL = 1.0 / 30.0
BOT_STATE_INTERVAL = 1.0 / 30.0
PING_INTERVAL = 1.0
MAX_MESSAGE_BYTES = 256 * 1024

try:
	_TEXT_TYPES = (basestring,)
except NameError:
	_TEXT_TYPES = (str,)


def _system_message(message, level='information'):
	"""Send a visible stock lower-right notification from the game thread."""
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


class _NetworkSpawnEvent(object):
	"""Small BigWorld input-event stand-in for the existing P-key spawn path."""

	def __init__(self):
		import Keys
		self.key = Keys.KEY_P

	def isKeyDown(self):
		return True

	def isRepeatedEvent(self):
		return False

	def isShiftDown(self):
		return False

	def isCtrlDown(self):
		return False

	def isAltDown(self):
		return False


def _finite_float(value, fallback=0.0):
	try:
		value = float(value)
	except (TypeError, ValueError):
		return float(fallback)
	try:
		if math.isnan(value) or math.isinf(value):
			return float(fallback)
	except Exception:
		pass
	return value


def _safe_text(value, limit=80):
	try:
		if not isinstance(value, _TEXT_TYPES):
			value = str(value)
		return value[:limit]
	except Exception:
		return ''


def _safe_position(value):
	try:
		if isinstance(value, dict):
			return (_finite_float(value.get('x')), _finite_float(value.get('y')),
					_finite_float(value.get('z')))
		if isinstance(value, (tuple, list)) and len(value) >= 3:
			return (_finite_float(value[0]), _finite_float(value[1]),
					_finite_float(value[2]))
	except Exception:
		pass
	return None


def _protocol_bool(value, default=False):
	if value is True or (value == 1 and not isinstance(value, _TEXT_TYPES)):
		return True
	if value is False or (value == 0 and not isinstance(value, _TEXT_TYPES)):
		return False
	return bool(default) if value is None else False


def _protocol_position(value):
	if isinstance(value, dict):
		if not all(key in value for key in ('x', 'y', 'z')):
			return None
		values = (value.get('x'), value.get('y'), value.get('z'))
	elif isinstance(value, (tuple, list)) and len(value) >= 3:
		values = (value[0], value[1], value[2])
	else:
		return None
	result = []
	for item in values:
		try:
			number = float(item)
		except (TypeError, ValueError):
			return None
		try:
			if math.isnan(number) or math.isinf(number):
				return None
		except Exception:
			pass
		result.append(number)
	return tuple(result)


class LANClient(object):
	def __init__(self, player, host, port, name, vehicle, max_health=1000):
		self.player = player
		self.host = str(host or '127.0.0.1')
		self.port = int(port or 28782)
		self.name = str(name or 'Player')
		self.vehicle = str(vehicle or 'ussr:MS-1')
		self.max_health = max(1, int(max_health or 1000))
		self.sock = None
		self.thread = None
		self.running = False
		self.connected = False
		self.ready = False
		self.player_id = None
		self.team = None
		self.slot = 0
		self.map_name = None
		self.available_maps = []
		self.spawn = None
		self.phase = 'connecting'
		self.round_id = None
		self.waiting_count = 0
		self.start_requested = False
		self.battle_started = False
		self.combat_deadline = None
		self.combat_end_deadline = None
		self.combat_duration = 900.0
		self._send_lock = threading.Lock()
		self._pending_lock = threading.Lock()
		self._pending = []
		self._recv_buffer = ''
		self._last_input = 0.0
		self._last_bot_state = 0.0
		self._last_bot_observation = 0.0
		self._fire_seq = 0
		self._ping_seq = 0
		self._last_ping = 0.0
		self._last_receive = 0.0
		self._last_snapshot = 0.0
		self.rtt_ms = None
		self._diag_window_start = time.time()
		self._diag_chunks = 0
		self._diag_messages = 0
		self._diag_snapshots = 0
		self._diag_bot_updates = 0
		self._diag_last_chunk = 0.0
		self._diag_last_snapshot = 0.0
		self._diag_last_bot_update = 0.0
		self._diag_last_bot_revision = -1
		self._diag_max_socket_gap = 0.0
		self._diag_max_snapshot_gap = 0.0
		self._diag_max_bot_update_gap = 0.0
		self._diag_max_queue_age = 0.0
		self._diag_max_pending = 0
		self.bot_authority_id = None
		self.bot_order_revision = 0
		self.bot_orders = {}
		self._last_order_resync = 0.0
		self._last_error = None
		self._error_notified = False
		self._stop_requested = False
		self._poll_scheduled = False

	def start(self):
		if self.running:
			return True
		self.running = True
		self.thread = threading.Thread(target=self._worker, name='offhangar-lan-client')
		self.thread.setDaemon(True)
		self.thread.start()
		self._schedule_poll()
		return True

	def _worker(self):
		try:
			LOG_NOTE('LAN connecting to %s:%s' % (self.host, self.port))
			sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			sock.settimeout(3.0)
			sock.connect((self.host, self.port))
			LOG_NOTE('LAN TCP connected to %s:%s' % (self.host, self.port))
			try:
				sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
			except Exception:
				pass
			sock.settimeout(0.5)
			self.sock = sock
			# The server requires hello to be the first wire message.  Do not expose
			# the socket to the main-thread poller before that message is sent: its
			# initial ping could otherwise win the race and be rejected as a protocol
			# mismatch.
			hello = {
				'type': 'hello',
				'protocol': PROTOCOL_VERSION,
				'name': self.name,
				'vehicle': self.vehicle,
				'max_health': self.max_health,
			}
			payload = (json.dumps(hello, separators=(',', ':')) + '\n').encode('utf-8')
			with self._send_lock:
				sock.sendall(payload)
			self.connected = True
			LOG_NOTE('LAN hello sent (protocol %s)' % PROTOCOL_VERSION)
			while self.running:
				try:
					chunk = sock.recv(8192)
				except socket.timeout:
					continue
				if not chunk:
					if self.connected and not self._stop_requested:
						self._last_error = 'server closed the connection'
					break
				received_time = time.time()
				self._last_receive = received_time
				with self._pending_lock:
					self._diag_chunks += 1
					previous_receive = self._diag_last_chunk
					if previous_receive > 0.0:
						self._diag_max_socket_gap = max(
							self._diag_max_socket_gap,
							received_time - previous_receive)
					self._diag_last_chunk = received_time
				try:
					self._recv_buffer += chunk.decode('utf-8')
				except UnicodeError:
					continue
				if len(self._recv_buffer) > 512 * 1024:
					self._last_error = 'server message buffer exceeded limit'
					break
				while '\n' in self._recv_buffer:
					line, self._recv_buffer = self._recv_buffer.split('\n', 1)
					if not line:
						continue
					try:
						message = json.loads(line)
					except (TypeError, ValueError):
						continue
					if isinstance(message, dict):
						# This private timestamp lets RTT and diagnostics distinguish actual
						# socket delay from a busy BigWorld game thread draining the queue late.
						message['_client_received_time'] = received_time
					with self._pending_lock:
						self._pending.append(message)
						self._diag_messages += 1
						if isinstance(message, dict) and message.get('type') == 'snapshot':
							if self._diag_last_snapshot > 0.0:
								self._diag_max_snapshot_gap = max(
									self._diag_max_snapshot_gap,
									received_time - self._diag_last_snapshot)
							self._diag_last_snapshot = received_time
							self._diag_snapshots += 1
							try:
								bot_revision = int(message.get('bot_state_revision', -1))
							except (TypeError, ValueError):
								bot_revision = -1
							if (bot_revision >= 0 and
									bot_revision != self._diag_last_bot_revision):
								if self._diag_last_bot_update > 0.0:
									self._diag_max_bot_update_gap = max(
										self._diag_max_bot_update_gap,
										received_time - self._diag_last_bot_update)
								self._diag_last_bot_update = received_time
								self._diag_last_bot_revision = bot_revision
								self._diag_bot_updates += 1
						self._diag_max_pending = max(
							self._diag_max_pending, len(self._pending))
		except Exception as error:
			if not self._stop_requested:
				self._last_error = str(error)
				LOG_ERROR('LAN connection failed to %s:%s: %s' % (self.host, self.port, self._last_error))
		finally:
			self.connected = False
			self.running = False
			try:
				if self.sock is not None:
					self.sock.close()
			except Exception:
				pass

	def _send(self, message):
		if not self.connected or self.sock is None:
			return False
		try:
			payload = (json.dumps(message, separators=(',', ':')) + '\n').encode('utf-8')
			if len(payload) > MAX_MESSAGE_BYTES:
				return False
			with self._send_lock:
				self.sock.sendall(payload)
			return True
		except Exception as error:
			self._last_error = str(error)
			return False

	def send_input(self, forward, turn, aim_yaw, gun_pitch, position=None, yaw=None,
			reported_health=None):
		now = time.time()
		if now - self._last_input < INPUT_INTERVAL:
			return
		self._last_input = now
		message = {
			'type': 'input',
			'forward': max(-1.0, min(1.0, _finite_float(forward))),
			'turn': max(-1.0, min(1.0, _finite_float(turn))),
			'aim_yaw': _finite_float(aim_yaw),
			'gun_pitch': _finite_float(gun_pitch),
			'fire_seq': self._fire_seq,
		}
		if position is not None:
			message['x'] = _finite_float(position[0])
			message['y'] = _finite_float(position[1])
			message['z'] = _finite_float(position[2])
			message['yaw'] = _finite_float(yaw)
		if reported_health is not None:
			message['reported_health'] = max(0, int(reported_health))
		self._send(message)

	def send_fire(self, shell_index=0, position=None, yaw=None, aim_yaw=None,
			gun_pitch=None):
		self._fire_seq += 1
		message = {
			'type': 'input',
			'fire_seq': self._fire_seq,
			'shell_index': max(0, min(int(shell_index or 0), 9)),
		}
		if position is not None:
			message['x'] = _finite_float(position[0])
			message['y'] = _finite_float(position[1])
			message['z'] = _finite_float(position[2])
			message['yaw'] = _finite_float(yaw)
		if aim_yaw is not None:
			message['aim_yaw'] = _finite_float(aim_yaw)
		if gun_pitch is not None:
			message['gun_pitch'] = _finite_float(gun_pitch)
		if not self._send(message):
			return None
		return self._fire_seq

	def send_hit(self, target_id, shot_seq, damage, shot_result, shell_index,
			impact_position=None):
		message = {
			'type': 'hit_report',
			'target': int(target_id),
			'shot_seq': int(shot_seq),
			'damage': max(0, int(damage or 0)),
			'shot_result': max(0, min(int(shot_result or 0), 2)),
			'shell_index': max(0, min(int(shell_index or 0), 9)),
		}
		if impact_position is not None:
			message['x'] = _finite_float(impact_position[0])
			message['y'] = _finite_float(impact_position[1])
			message['z'] = _finite_float(impact_position[2])
		return self._send(message)

	def send_bot_manifest(self, bots):
		return self._send({'type': 'bot_manifest', 'bots': bots[:30]})

	def send_bot_states(self, bots):
		now = time.time()
		if now - self._last_bot_state < BOT_STATE_INTERVAL:
			return False
		self._last_bot_state = now
		return self._send({'type': 'bot_state', 'bots': bots[:30]})

	def send_bot_observation(self, contacts, affordances=None, navigation=None):
		now = time.time()
		if now - self._last_bot_observation < 0.45:
			return False
		message = {
			'type': 'bot_observation',
			'contacts': contacts[:64],
			'affordances': (affordances or ())[:16],
		}
		if navigation is not None:
			message['navigation'] = navigation
		sent = self._send(message)
		if sent:
			self._last_bot_observation = now
		return sent

	def request_bot_orders(self):
		"""Rate-limited application-level recovery for a missing bot order."""
		now = time.time()
		if now - self._last_order_resync < 0.5:
			return False
		self._last_order_resync = now
		return self._send({
			'type': 'bot_order_resync',
			'revision': int(self.bot_order_revision or 0),
			'loaded': len(self.bot_orders or {}),
		})

	def send_bot_hit(self, target_id, shot_seq, damage, shot_result,
			impact_position=None):
		message = {
			'type': 'bot_hit_report',
			'target': int(target_id),
			'shot_seq': int(shot_seq),
			'damage': max(0, int(damage or 0)),
			'shot_result': max(0, min(int(shot_result or 0), 2)),
		}
		if impact_position is not None:
			message['x'] = _finite_float(impact_position[0])
			message['y'] = _finite_float(impact_position[1])
			message['z'] = _finite_float(impact_position[2])
		return self._send(message)

	def send_bot_human_hit(self, bot_id, target_id, shot_seq, damage,
			shot_result, impact_position=None):
		message = {
			'type': 'bot_human_hit',
			'attacker_bot': int(bot_id),
			'target': int(target_id),
			'shot_seq': int(shot_seq or 0),
			'damage': max(0, int(damage or 0)),
			'shot_result': max(0, min(int(shot_result or 0), 2)),
		}
		if impact_position is not None:
			message['x'] = _finite_float(impact_position[0])
			message['y'] = _finite_float(impact_position[1])
			message['z'] = _finite_float(impact_position[2])
		return self._send(message)

	def send_rules(self, rules):
		return self._send({'type': 'rules_state', 'rules': rules})

	def send_battle_result(self, winner, reason, base_team=0):
		return self._send({
			'type': 'battle_result',
			'winner': int(winner),
			'reason': str(reason or 'battle finished'),
			'base_team': int(base_team or 0),
		})

	def _set_authority(self, authority_id):
		try:
			authority_id = int(authority_id) if authority_id is not None else None
		except (TypeError, ValueError):
			authority_id = None
		changed = authority_id != self.bot_authority_id
		self.bot_authority_id = authority_id
		self.player._offhangar_network_authority_id = authority_id
		was_authority = bool(getattr(
			self.player, '_offhangar_network_is_authority', False))
		self.player._offhangar_network_is_authority = (
			authority_id is not None and authority_id == self.player_id)
		if self.player._offhangar_network_is_authority and not was_authority:
			# The next canonical snapshot must be applied once before this client
			# begins simulating. Otherwise a relay promotes its interpolated pose and
			# zero local speed instead of the server's final authority state.
			self.player._offhangar_network_authority_handoff_pending = True
		if changed and self.phase == 'battle':
			role = 'simulation authority' if self.player._offhangar_network_is_authority else 'relay client'
			LOG_NOTE('LAN bot authority=%s; local role=%s' % (authority_id, role))
			_system_message('LAN battle authority: player %s (%s).' % (authority_id, role))

	def _load_bot_orders(self, message):
		"""Apply a complete revision body and acknowledge game-thread delivery."""
		if not isinstance(message, dict) or 'bot_orders' not in message:
			return False
		try:
			revision = int(message.get('bot_order_revision', 0) or 0)
		except (TypeError, ValueError):
			return False
		if revision < self.bot_order_revision:
			return False
		if (revision > self.bot_order_revision or
				(revision == 0 and not self.bot_orders)):
			orders = {}
			for order in message.get('bot_orders') or ():
				try:
					orders[int(order.get('id'))] = order
				except Exception:
					continue
			self.bot_order_revision = revision
			self.bot_orders = orders
			self.player._offhangar_network_bot_order_revision = revision
			self.player._offhangar_network_bot_orders = orders
		self._send({'type': 'bot_order_ack', 'revision': revision})
		return True

	def request_start(self, map_name=None):
		if not self.ready or self.phase != 'waiting':
			return False
		self.start_requested = True
		message = {'type': 'start_battle'}
		if map_name:
			message['map'] = str(map_name)
		if not self._send(message):
			LOG_ERROR('LAN could not send battle start request')
			return False
		LOG_NOTE('LAN battle start requested map=%s; waiting for server broadcast' % (
			str(map_name or self.map_name)))
		return True

	def stop(self):
		self._stop_requested = True
		self.running = False
		self._send({'type': 'leave'})
		try:
			if self.sock is not None:
				self.sock.close()
		except Exception:
			pass

	def _schedule_poll(self):
		if self._poll_scheduled:
			return
		self._poll_scheduled = True
		try:
			BigWorld.callback(POLL_INTERVAL, self._poll)
		except Exception:
			self._poll_scheduled = False

	def _reset_transport_diagnostics(self, now=None):
		now = time.time() if now is None else float(now)
		with self._pending_lock:
			self._diag_window_start = now
			self._diag_chunks = 0
			self._diag_messages = 0
			self._diag_snapshots = 0
			self._diag_bot_updates = 0
			self._diag_last_chunk = 0.0
			self._diag_last_snapshot = 0.0
			self._diag_last_bot_update = 0.0
			self._diag_last_bot_revision = -1
			self._diag_max_socket_gap = 0.0
			self._diag_max_snapshot_gap = 0.0
			self._diag_max_bot_update_gap = 0.0
			self._diag_max_queue_age = 0.0
			self._diag_max_pending = 0

	def _transport_diagnostic_snapshot(self, now=None, minimum_window=5.0):
		"""Return one bounded transport summary and reset its rolling counters."""
		now = time.time() if now is None else float(now)
		with self._pending_lock:
			window = max(0.0, now - self._diag_window_start)
			if window < float(minimum_window):
				return None
			result = {
				'window': window,
				'chunks': self._diag_chunks,
				'messages': self._diag_messages,
				'snapshots': self._diag_snapshots,
				'bot_updates': self._diag_bot_updates,
				'max_socket_gap': self._diag_max_socket_gap,
				'max_snapshot_gap': self._diag_max_snapshot_gap,
				'max_bot_update_gap': self._diag_max_bot_update_gap,
				'max_queue_age': self._diag_max_queue_age,
				'max_pending': self._diag_max_pending,
			}
			self._diag_window_start = now
			self._diag_chunks = 0
			self._diag_messages = 0
			self._diag_snapshots = 0
			self._diag_bot_updates = 0
			self._diag_max_socket_gap = 0.0
			self._diag_max_snapshot_gap = 0.0
			self._diag_max_bot_update_gap = 0.0
			self._diag_max_queue_age = 0.0
			self._diag_max_pending = len(self._pending)
			return result

	def _poll(self):
		self._poll_scheduled = False
		now = time.time()
		if self.connected and now - self._last_ping >= PING_INTERVAL:
			self._last_ping = now
			self._ping_seq += 1
			self._send({'type': 'ping', 'seq': self._ping_seq, 'client_time': now})
		messages = []
		with self._pending_lock:
			if self._pending:
				messages = self._pending
				self._pending = []
			for message in messages:
				if isinstance(message, dict):
					received_time = _finite_float(
						message.get('_client_received_time'), now)
					self._diag_max_queue_age = max(
						self._diag_max_queue_age, now - received_time)
		# Snapshots are level-triggered.  If the game thread was busy loading a
		# remote tank, applying every stale 30 Hz snapshot would create an
		# unbounded queue and make the visual state increasingly lag behind.
		latest_snapshot = None
		order_payloads = {}
		coalesced = []
		for message in messages:
			if isinstance(message, dict) and message.get('type') == 'snapshot':
				if 'bot_orders' in message:
					try:
						order_revision = int(message.get('bot_order_revision', 0) or 0)
					except (TypeError, ValueError):
						order_revision = 0
					order_payloads[order_revision] = message.get('bot_orders') or []
				latest_snapshot = message
			else:
				coalesced.append(message)
		if latest_snapshot is not None:
			# The server sends an order body only once per revision, while snapshots
			# are coalesced to the newest state on the game thread. Preserve the body
			# from an earlier snapshot of the same revision; otherwise a busy frame
			# keeps the new revision number but silently clears every bot order.
			try:
				latest_revision = int(
					latest_snapshot.get('bot_order_revision', 0) or 0)
			except (TypeError, ValueError):
				latest_revision = 0
			if ('bot_orders' not in latest_snapshot and
					latest_revision in order_payloads):
				latest_snapshot = dict(latest_snapshot)
				latest_snapshot['bot_orders'] = order_payloads[latest_revision]
			coalesced.append(latest_snapshot)
		messages = coalesced
		for message in messages:
			try:
				self._handle_message(message)
			except Exception:
				LOG_ERROR('LAN client message error:', repr(message))
		if self.phase == 'battle':
			diagnostic = self._transport_diagnostic_snapshot(now)
			if diagnostic is not None:
				window = max(0.001, diagnostic['window'])
				LOG_NOTE(
					'LAN NET window=%.1fs chunks=%.1f/s messages=%.1f/s snapshots=%.1f/s '
					'bot_updates=%.1f/s max_socket_gap=%dms max_snapshot_gap=%dms '
					'max_bot_gap=%dms max_queue_age=%dms '
					'max_pending=%d rtt=%s' % (
						diagnostic['window'], diagnostic['chunks'] / window,
						diagnostic['messages'] / window,
						diagnostic['snapshots'] / window,
						diagnostic['bot_updates'] / window,
						int(round(diagnostic['max_socket_gap'] * 1000.0)),
						int(round(diagnostic['max_snapshot_gap'] * 1000.0)),
						int(round(diagnostic['max_bot_update_gap'] * 1000.0)),
						int(round(diagnostic['max_queue_age'] * 1000.0)),
						diagnostic['max_pending'],
						'pending' if self.rtt_ms is None else '%dms' % int(round(self.rtt_ms))))
		if self._last_error and not self._error_notified:
			self._error_notified = True
			_system_message('LAN connection error: %s' % self._last_error, 'error')
		if self.running:
			self._schedule_poll()

	def _load_server_timing(self, message):
		"""Project server-relative battle timing onto this client's receive clock."""
		timing = message.get('timing') if isinstance(message, dict) else None
		if not isinstance(timing, dict):
			return False
		received = _finite_float(message.get('_client_received_time'), time.time())
		# Relative server time avoids requiring synchronized Windows/macOS clocks.
		# Half the smoothed RTT approximates the packet's one-way transit time.
		one_way = 0.0
		if self.rtt_ms is not None:
			one_way = max(0.0, min(0.25, float(self.rtt_ms) / 2000.0))
		phase = str(timing.get('phase') or 'loading')
		start_in = max(0.0, _finite_float(timing.get('start_in_ms'), 0.0) / 1000.0)
		remaining = max(0.0, _finite_float(timing.get('remaining_ms'), 0.0) / 1000.0)
		duration = max(1.0, _finite_float(timing.get('duration_ms'), 900000.0) / 1000.0)
		if phase == 'prebattle':
			projected_start = received + start_in - one_way
			if self.combat_deadline is None or abs(self.combat_deadline - projected_start) > 0.25:
				self.combat_deadline = projected_start
			else:
				self.combat_deadline = self.combat_deadline * 0.8 + projected_start * 0.2
			projected_end = self.combat_deadline + duration
		elif phase == 'battle':
			if self.combat_deadline is None:
				self.combat_deadline = received - one_way
			projected_end = received + remaining - one_way
		else:
			projected_end = received - one_way
		self.combat_duration = duration
		if self.combat_end_deadline is None or abs(self.combat_end_deadline - projected_end) > 0.25:
			self.combat_end_deadline = projected_end
		else:
			self.combat_end_deadline = self.combat_end_deadline * 0.8 + projected_end * 0.2
		self.player._offhangar_network_combat_phase = phase
		self.player._offhangar_network_combat_deadline = self.combat_deadline
		self.player._offhangar_network_combat_end_deadline = self.combat_end_deadline
		self.player._offhangar_network_combat_duration = self.combat_duration
		return True

	def _handle_message(self, message):
		kind = message.get('type') if isinstance(message, dict) else None
		if kind == 'welcome':
			self.ready = True
			self.player_id = message.get('player_id')
			self.name = str(message.get('name') or self.name)
			self.vehicle = str(message.get('vehicle') or self.vehicle)
			self.team = message.get('team')
			self.slot = int(message.get('slot', 0) or 0)
			self.max_health = int(message.get('max_health', self.max_health) or self.max_health)
			self.map_name = message.get('map')
			self.available_maps = list(message.get('map_pool') or self.available_maps)
			self.spawn = message.get('spawn') or {}
			self.phase = message.get('phase') or 'waiting'
			self.round_id = message.get('round_id')
			self._set_authority(message.get('bot_authority_id'))
			self.player._offhangar_network_id = self.player_id
			self.player._offhangar_network_name = self.name
			self.player._offhangar_network_vehicle = self.vehicle
			self.player._offhangar_network_team = self.team
			self.player._offhangar_network_slot = self.slot
			self.player._offhangar_network_spawn = self.spawn
			self.player._offhangar_network_map_name = self.map_name
			self.player._offhangar_network_ready = True
			LOG_NOTE('LAN welcome id=%s name=%s vehicle=%s team=%s slot=%s map=%s phase=%s' % (
				self.player_id, self.name, self.vehicle, self.team, self.slot,
				self.map_name, self.phase))
			_system_message('Connected to LAN server as %s (team %s).' % (
				self.name, self.team))
			if self.phase == 'waiting':
				try:
					from gui.mods.offhangar.offline_battle import show_network_waiting_queue_from_server
					show_network_waiting_queue_from_server(self.player)
					from gui.mods.offhangar.lan_waiting_room import open as open_waiting_room
					open_waiting_room(self.player)
					# loadPrebattle creates its event listeners asynchronously. Repeat the
					# roster update once after the Flash page has populated.
					BigWorld.callback(0.25, lambda: _publish_queue_count(
						self.player, self.waiting_count) if self.phase == 'waiting' else None)
				except Exception:
					LOG_ERROR('LAN could not open the queue screen after welcome')
		elif kind == 'roster':
			players = message.get('players') or []
			count = len(players)
			self.phase = message.get('phase') or self.phase
			self.map_name = message.get('map') or self.map_name
			self.available_maps = list(message.get('map_pool') or self.available_maps)
			self.waiting_count = count
			self.player._offhangar_network_roster = players
			_publish_queue_count(self.player, count)
			try:
				from gui.mods.offhangar.lan_waiting_room import update as update_waiting_room
				update_waiting_room(self.player)
			except Exception:
				pass
			if count != getattr(self.player, '_offhangar_network_roster_count', -1):
				self.player._offhangar_network_roster_count = count
				LOG_NOTE('LAN waiting room: %d player(s); choose a map and click START BATTLE' % count)
		elif kind == 'battle_start':
			self._load_bot_orders(message)
			self._load_server_timing(message)
			if self.battle_started:
				return
			self.battle_started = True
			self.phase = 'battle'
			self._reset_transport_diagnostics()
			self.map_name = message.get('map') or self.map_name
			self.round_id = message.get('round_id', self.round_id)
			self.player._offhangar_network_map_name = self.map_name
			self.player._offhangar_network_roster = message.get('players') or []
			self.player._offhangar_network_bot_roster = message.get('bots') or []
			self.player._offhangar_network_bot_manifest = message.get('bot_manifest') or []
			try:
				from gui.mods.offhangar.lan_waiting_room import close as close_waiting_room
				close_waiting_room()
			except Exception:
				pass
			self._set_authority(message.get('bot_authority_id'))
			delay = max(0.0, min(5.0, _finite_float(message.get('delay'), 0.0)))
			LOG_NOTE('LAN BATTLE START received: map=%s players=%d delay=%.2f' % (
				self.map_name, len(message.get('players') or []), delay))
			_system_message('LAN battle starting: %s, %d player(s).' % (
				self.map_name, len(message.get('players') or [])))

			def _start_from_server():
				try:
					from gui.mods.offhangar.offline_battle import start_network_battle_from_server
					start_network_battle_from_server(self.player, self.map_name, self.team)
				except Exception:
					LOG_ERROR('LAN failed to enter battle after server start')

			BigWorld.callback(delay, _start_from_server)
		elif kind == 'start_denied':
			self.start_requested = False
			LOG_NOTE('LAN start denied: %s (players=%s)' % (
				message.get('code'), message.get('players')))
			_system_message('LAN battle could not start: %s.' % (
				message.get('code') or 'request denied'), 'warning')
			try:
				from gui.mods.offhangar.lan_waiting_room import set_status
				set_status('Start denied: %s' % (message.get('code') or 'request denied'))
			except Exception:
				pass
		elif kind == 'snapshot':
			self._last_snapshot = time.time()
			self._load_server_timing(message)
			self._set_authority(message.get('bot_authority_id'))
			self._load_bot_orders(message)
			self.player._offhangar_network_snapshot = message
			_apply_snapshot(self.player, message)
		elif kind == 'events':
			self.player._offhangar_network_events = message.get('events') or []
			_handle_events(self.player, message.get('events') or [])
		elif kind == 'pong':
			client_time = _finite_float(message.get('client_time'), 0.0)
			if client_time > 0.0:
				received_time = _finite_float(
					message.get('_client_received_time'), time.time())
				sample = max(0.0, (received_time - client_time) * 1000.0)
				self.rtt_ms = sample if self.rtt_ms is None else self.rtt_ms * 0.75 + sample * 0.25
		elif kind == 'error':
			self._last_error = message.get('message') or message.get('code') or 'server error'
			LOG_ERROR('LAN server error:', self._last_error)
			if not self._error_notified:
				self._error_notified = True
				_system_message('LAN server error: %s' % self._last_error, 'error')


def _network_config():
	try:
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		return CONFIG_OPTIONS
	except Exception:
		return {}


def queue_info_for_player(player):
	"""Build the exact 0.8.2 Prebattle queue payload from the LAN roster."""
	cfg = _network_config()
	if not bool(cfg.get('network_mode', False)):
		return None
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	count = int(getattr(client, 'waiting_count', 0) or 0) if client is not None else 0
	count = max(0, min(count, 999))
	roster = list(getattr(player, '_offhangar_network_roster', None) or []) if player is not None else []
	if not roster and count > 0:
		# Welcome can reach Flash before the first roster broadcast. Keep the
		# displayed total truthful using the selected vehicle until that roster
		# replaces these temporary entries.
		vehicle_name = getattr(client, 'vehicle', 'ussr:MS-1') if client is not None else 'ussr:MS-1'
		roster = [{'vehicle': vehicle_name} for unused in range(count)]
	classes = [0, 0, 0, 0, 0]
	levels = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
	try:
		import constants
		from items import vehicles
		class_indices = constants.VEHICLE_CLASS_INDICES
		max_level = int(getattr(constants, 'MAX_VEHICLE_LEVEL', 10) or 10)
		for entry in roster[:999]:
			try:
				vehicle_name = str(entry.get('vehicle') or getattr(client, 'vehicle', 'ussr:MS-1'))
				descriptor = vehicles.VehicleDescr(typeName=vehicle_name)
				vehicle_type = descriptor.type
				class_index = None
				for tag in getattr(vehicle_type, 'tags', ()):
					if tag in class_indices:
						class_index = int(class_indices[tag])
						break
				level = max(1, min(int(getattr(vehicle_type, 'level', 1) or 1), max_level, 10))
				if class_index is None or class_index < 0 or class_index >= len(classes):
					raise ValueError('vehicle class is missing for %s' % vehicle_name)
				classes[class_index] += 1
				levels[level] += 1
			except Exception as error:
				LOG_ERROR('LAN queue vehicle classification failed:', str(error))
				classes[0] += 1
				levels[1] += 1
	except Exception as error:
		# A malformed/custom vehicle must not make the queue page fail to open.
		# Preserve the true total when the descriptor subsystem itself is absent.
		LOG_ERROR('LAN queue vehicle classification failed:', str(error))
		classes = [0, 0, 0, 0, 0]
		levels = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
		fallback_count = min(len(roster), 999)
		classes[0] = fallback_count
		levels[1] = fallback_count
	# Prebattle.onQueueInfoReceived displays sum(levels) as the player count.
	# It reverses levels and drops original element zero; indices 1..10 are tiers.
	return {
		'classes': classes,
		'levels': levels,
	}


def _publish_queue_count(player, count):
	if player is None or not hasattr(player, 'receiveQueueInfo'):
		return False
	qinfo = queue_info_for_player(player)
	if qinfo is None:
		return False
	try:
		player.receiveQueueInfo(qinfo, {})
		LOG_NOTE('LAN queue UI updated: %d connected player(s)' % int(count))
		return True
	except Exception:
		LOG_ERROR('LAN queue UI update failed')
		return False


def _descriptor_details(descriptor):
	if descriptor is None:
		return None
	type_name = getattr(descriptor, 'typeName', None)
	if not type_name:
		type_name = getattr(getattr(descriptor, 'type', None), 'name', None)
	if not type_name:
		return None
	return str(type_name), int(getattr(descriptor, 'maxHealth', 1000) or 1000)


def _selected_vehicle_details(player):
	"""Resolve the selected 0.8.2 garage vehicle and its real maximum HP."""
	descriptor = None
	compact_descr = 0
	try:
		from CurrentVehicle import g_currentVehicle
		# CurrentVehicle.py in the 0.8.2 client exposes the selected garage
		# Vehicle through .vehicle.  Its descriptor.type.name is the canonical
		# value accepted by vehicles.VehicleDescr(typeName=...).
		garage_vehicle = getattr(g_currentVehicle, 'vehicle', None)
		descriptor = getattr(garage_vehicle, 'descriptor', None)
		details = _descriptor_details(descriptor)
		if details is not None:
			return details

		# .item is populated asynchronously by ItemsRequester. Keep it as a
		# second exact source because the offline inventory shim already uses it.
		item = getattr(g_currentVehicle, 'item', None)
		descriptor = getattr(item, 'descriptor', None) if item is not None else None
		details = _descriptor_details(descriptor)
		if details is not None:
			return details

		for source in (garage_vehicle, item):
			if source is None:
				continue
			source_descriptor = getattr(source, 'descriptor', None)
			try:
				compact_descr = source_descriptor.makeCompactDescr()
			except Exception:
				compact_descr = 0
			if not compact_descr:
				compact_descr = (getattr(source, 'intCD', 0) or
					getattr(source, 'typeCompDescr', 0) or
					getattr(getattr(source_descriptor, 'type', None), 'compactDescr', 0) or
					getattr(source_descriptor, 'typeCompDescr', 0) or 0)
			if compact_descr:
				break
	except Exception as exc:
		LOG_DEBUG('LAN CurrentVehicle resolution failed:', str(exc))
	if not compact_descr:
		try:
			selected_id = getattr(player, '_offhangar_network_pending_veh_id', 0) or 0
			cache = getattr(getattr(player, 'inventory', None), '_Inventory__cache', None) or {}
			vehicle_data = cache.get('inventory', {}).get(1, {})
			comp_descrs = vehicle_data.get('compDescr', {})
			compact_descr = (comp_descrs.get(selected_id, 0) or 0)
			if not compact_descr and comp_descrs:
				try:
					compact_descr = comp_descrs.values()[0] or 0
				except Exception:
					compact_descr = 0
		except Exception as exc:
			LOG_DEBUG('LAN inventory vehicle resolution failed:', str(exc))
			compact_descr = 0
	if compact_descr:
		try:
			from items import vehicles
			descriptor = vehicles.VehicleDescr(compactDescr=compact_descr)
			details = _descriptor_details(descriptor)
			if details is not None:
				return details
		except Exception as exc:
			LOG_DEBUG('LAN compact descriptor resolution failed:', str(exc))
	try:
		descriptor = getattr(player, 'vehicleTypeDescriptor', None)
		details = _descriptor_details(descriptor)
		if details is not None:
			return details
	except Exception:
		pass
	LOG_ERROR('LAN selected vehicle could not be resolved; using MS-1 fallback')
	_system_message('LAN could not read the selected tank; using MS-1 with 100 HP.', 'error')
	return 'ussr:MS-1', 100


def start_for_player(player):
	if player is None:
		return None
	old = getattr(player, '_offhangar_network_client', None)
	if old is not None and old.running:
		return old
	cfg = _network_config()
	host = cfg.get('network_server_host', '127.0.0.1')
	port = cfg.get('network_server_port', 28782)
	name = cfg.get('nickname', 'Player')
	vehicle, max_health = _selected_vehicle_details(player)
	LOG_NOTE('LAN selected vehicle resolved: %s (%s HP)' % (vehicle, max_health))
	_system_message('Connecting to LAN server %s:%s with %s...' % (host, port, vehicle))
	client = LANClient(player, host, port, name, vehicle, max_health)
	player._offhangar_network_client = client
	player._offhangar_network_ready = False
	player._offhangar_network_snapshot = None
	player._offhangar_network_events = []
	player._offhangar_network_pending_remote_ids = {}
	player._offhangar_network_server_health = None
	player._offhangar_network_authority_id = None
	player._offhangar_network_is_authority = False
	player._offhangar_network_bot_manifest = []
	player._offhangar_network_result_applied = False
	player._offhangar_network_combat_phase = 'loading'
	player._offhangar_network_combat_deadline = None
	player._offhangar_network_combat_end_deadline = None
	player._offhangar_network_combat_duration = 900.0
	client.start()
	return client


def request_battle_start(player, map_name=None):
	if player is None:
		return False
	cfg = _network_config()
	if not bool(cfg.get('network_mode', False)):
		return False
	client = getattr(player, '_offhangar_network_client', None)
	if client is None or not client.running:
		LOG_NOTE('LAN start button ignored: click Battle! and wait for JOIN first')
		_system_message('Click Battle! and wait for the LAN JOIN before starting.', 'warning')
		return True
	if not client.ready:
		LOG_NOTE('LAN start button ignored while still connecting')
		_system_message('Still connecting to the LAN server.', 'warning')
		return True
	if client.phase == 'waiting':
		client.request_start(map_name)
		return True
	if client.phase == 'battle':
		return True
	return True


def stop_for_player(player):
	try:
		from gui.mods.offhangar.lan_waiting_room import close as close_waiting_room
		close_waiting_room()
	except Exception:
		pass
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is not None:
		client.stop()
	if player is not None:
		player._offhangar_network_client = None
		player._offhangar_network_ready = False
		player._offhangar_network_arena_starting = False
		player._offhangar_network_snapshot = None
		player._offhangar_network_events = []
		player._offhangar_network_pending_remote_ids = {}
		player._offhangar_network_server_health = None
		player._offhangar_network_authority_id = None
		player._offhangar_network_is_authority = False
		player._offhangar_network_bot_manifest = []
		player._offhangar_network_combat_phase = None
		player._offhangar_network_combat_deadline = None
		player._offhangar_network_combat_end_deadline = None
		player._offhangar_network_bot_orders = {}
		player._offhangar_network_bot_order_revision = 0
		player._offhangar_network_result_applied = False
		# These are per-battle closures installed by offline_battle. Keeping them
		# on the persistent account pins the finished battle's models and mocks.
		player._offhangar_apply_network_rules_state = None
		player._offhangar_apply_network_battle_result = None
		player._offhangar_network_spawn_remote = None
		player._offhangar_network_formation = None


def _server_pose_from_world(player, world_x, world_y, world_z, world_yaw):
	"""Convert the loaded map coordinates back into the shared server frame."""
	try:
		formation = getattr(player, '_offhangar_network_formation', None)
		if formation is None:
			return (world_x, world_y, world_z), world_yaw
		base1 = formation(1, 0)
		base2 = formation(2, 0)
		b1x, b1z = float(base1[0]), float(base1[1])
		b2x, b2z = float(base2[0]), float(base2[1])
		dx, dz = b2x - b1x, b2z - b1z
		length = math.sqrt(dx * dx + dz * dz) or 1.0
		axis_x, axis_z = dx / length, dz / length
		right_x, right_z = axis_z, -axis_x
		world_dx = float(world_x) - b1x
		world_dz = float(world_z) - b1z
		travel = world_dx * axis_x + world_dz * axis_z
		lateral = world_dx * right_x + world_dz * right_z
		axis_yaw = math.atan2(dx, dz)
		return ((lateral, _finite_float(world_y), travel),
			_finite_float(world_yaw) - axis_yaw)
	except Exception:
		return None, _finite_float(world_yaw)


def send_local_input(player, forward, turn, aim_yaw, gun_pitch,
		world_x=None, world_y=None, world_z=None, hull_yaw=None):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is not None and client.ready:
		position = None
		server_hull_yaw = hull_yaw
		server_aim_yaw = aim_yaw
		if world_x is not None and world_z is not None and hull_yaw is not None:
			position, server_hull_yaw = _server_pose_from_world(
				player, world_x, world_y or 0.0, world_z, hull_yaw)
			_, server_aim_yaw = _server_pose_from_world(
				player, world_x, world_y or 0.0, world_z, aim_yaw)
		reported_health = None
		try:
			mock = _local_mock(player)
			if mock is not None:
				reported_health = int(getattr(mock, 'health', client.max_health) or 0)
		except Exception:
			pass
		client.send_input(forward, turn, server_aim_yaw, gun_pitch,
			position=position, yaw=server_hull_yaw, reported_health=reported_health)


def send_local_fire(player, shell_index=0, aim_yaw=None, gun_pitch=None,
		world_x=None, world_y=None, world_z=None, hull_yaw=None):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is not None and client.ready:
		position = None
		server_hull_yaw = hull_yaw
		server_aim_yaw = aim_yaw
		if world_x is not None and world_z is not None and hull_yaw is not None:
			position, server_hull_yaw = _server_pose_from_world(
				player, world_x, world_y or 0.0, world_z, hull_yaw)
			_, server_aim_yaw = _server_pose_from_world(
				player, world_x, world_y or 0.0, world_z, aim_yaw)
		shot_seq = client.send_fire(shell_index, position, server_hull_yaw,
			server_aim_yaw, gun_pitch)
		if shot_seq is not None:
			player._offhangar_network_last_fire_seq = shot_seq
			player._offhangar_network_last_shell_index = int(shell_index or 0)
		return shot_seq
	return None


def send_local_hit(player, target_id, shot_seq, damage, shot_result,
		shell_index=0, impact_position=None):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is None or not client.ready or shot_seq is None or target_id is None:
		return False
	server_impact = None
	if impact_position is not None:
		try:
			server_impact, unused_yaw = _server_pose_from_world(player,
				impact_position.x, impact_position.y, impact_position.z, 0.0)
		except Exception:
			server_impact = None
	return client.send_hit(target_id, shot_seq, damage, shot_result,
		shell_index, server_impact)


def network_is_authority(player):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	return bool(client is not None and client.ready and client.phase == 'battle' and
		getattr(player, '_offhangar_network_is_authority', False))


def publish_bot_manifest(player, jobs):
	"""Publish the authority-selected lineup before any client creates bots."""
	if not network_is_authority(player):
		return False
	client = getattr(player, '_offhangar_network_client', None)
	manifest = []
	for job in jobs[:30]:
		try:
			bot_id, team, slot, vehicle, name, max_health, world_x, world_z, world_yaw = job
			profile = {}
			route_payload = {}
			try:
				from items import vehicles
				from gui.mods.offhangar.bot_ai import build_vehicle_profile
				descriptor = vehicles.VehicleDescr(typeName=str(vehicle))
				profile = build_vehicle_profile(descriptor)
			except Exception:
				profile = {}
			try:
				import sys
				offline = sys.modules.get('gui.mods.offhangar.offline_battle')
				director_getter = getattr(offline, '_offh_ai_director', None)
				director = director_getter(player) if callable(director_getter) else None
				if director is not None:
					import Math
					agent = director.register_profile(bot_id, team, profile, name)
					route = agent.get('route') or {}
					waypoints = []
					for point in route.get('waypoints', ()):
						grounded = _ground_world_point(
							Math.Vector3(float(point[0]), 0.0, float(point[1])))
						world_y = float(grounded.y) if grounded is not None else 0.0
						shared, unused_yaw = _server_pose_from_world(
							player, point[0], world_y, point[1], 0.0)
						waypoints.append({
							'x': shared[0], 'y': shared[1], 'z': shared[2],
							'hold': bool(point[2]) if len(point) > 2 else False,
						})
					route_payload = {
						'id': route.get('id', 'server_route'),
						'waypoints': waypoints,
					}
			except Exception:
				route_payload = {}
			server_pos, server_yaw = _server_pose_from_world(
				player, world_x, 0.0, world_z, world_yaw)
			manifest.append({
				'id': int(bot_id), 'team': int(team), 'slot': int(slot),
				'vehicle': str(vehicle), 'name': str(name),
				'max_health': max(1, int(max_health)),
				'health': max(1, int(max_health)),
				'x': server_pos[0], 'y': server_pos[1], 'z': server_pos[2],
				'yaw': server_yaw, 'aim_yaw': server_yaw,
				'profile': profile,
				'route': route_payload,
			})
		except Exception:
			continue
	if not manifest:
		return False
	player._offhangar_network_bot_manifest = manifest
	return client.send_bot_manifest(manifest)


def publish_bot_observation(player, contacts, affordances=None, navigation=None):
	"""Send the authority's team-visibility report to the global planner."""
	if not network_is_authority(player):
		return False
	client = getattr(player, '_offhangar_network_client', None)
	if client is None or not client.ready:
		return False
	payload = []
	for raw in (contacts or ())[:64]:
		try:
			point = _protocol_position(raw.get('position'))
			if point is None:
				continue
			server_pos, unused_yaw = _server_pose_from_world(
				player, point[0], point[1], point[2], 0.0)
			if server_pos is None:
				continue
			item = {
				'observing_team': int(raw.get('observing_team')),
				'target_id': int(raw.get('target_id')),
				'target_kind': _safe_text(raw.get('target_kind'), 16),
				'target_team': int(raw.get('target_team')),
				'x': server_pos[0], 'y': server_pos[1], 'z': server_pos[2],
				'health': max(0, int(raw.get('health', 0))),
				'max_health': max(1, int(raw.get('max_health', 1))),
				'class_tag': _safe_text(raw.get('class_tag'), 24),
				'armor': max(0.0, _finite_float(raw.get('armor'))),
				'visible': _protocol_bool(raw.get('visible'), True),
			}
			# Team spotting and local firing lanes are separate facts. Always send
			# the current client's bounded list, including [] when no bot can shoot;
			# omission is reserved for genuinely older protocol-v5 packages.
			shootable = []
			seen_bot_ids = set()
			for raw_bot_id in (raw.get('shootable_by_bot_ids') or ())[:64]:
				try:
					bot_id = int(raw_bot_id)
				except Exception:
					continue
				if bot_id <= 0 or bot_id in seen_bot_ids:
					continue
				seen_bot_ids.add(bot_id)
				shootable.append(bot_id)
			item['shootable_by_bot_ids'] = shootable
			payload.append(item)
		except Exception:
			continue
	shared_affordances = []
	for raw in (affordances or ())[:16]:
		try:
			item = {
				'bot_id': int(raw.get('bot_id')),
				'target_id': int(raw.get('target_id')),
				'target_kind': str(raw.get('target_kind') or ''),
				'candidates': [],
			}
			for candidate in (raw.get('candidates') or ())[:12]:
				position = _protocol_position(candidate.get('position'))
				if position is None:
					continue
				server_position, unused_yaw = _server_pose_from_world(
					player, position[0], position[1], position[2], 0.0)
				if server_position is None:
					continue
				value = {
					'id': _safe_text(candidate.get('id'), 80),
					'position': {
					'x': server_position[0], 'y': server_position[1],
					'z': server_position[2],
					},
					'travel_distance': max(0.0, _finite_float(candidate.get('travel_distance'))),
					'route_alignment': max(0.0, min(1.0, _finite_float(candidate.get('route_alignment')))),
					'enemy_occlusion': max(0.0, min(1.0, _finite_float(candidate.get('enemy_occlusion')))),
					'exposure': max(0.0, min(1.0, _finite_float(candidate.get('exposure'), 1.0))),
					'slope': max(0.0, _finite_float(candidate.get('slope'))),
					'water': max(0.0, min(1.0, _finite_float(candidate.get('water')))),
					'ally_congestion': max(0.0, min(1.0, _finite_float(candidate.get('ally_congestion')))),
					'peek_feasible': _protocol_bool(candidate.get('peek_feasible')),
					'escape_feasible': _protocol_bool(candidate.get('escape_feasible')),
				}
				peek = _protocol_position(candidate.get('peek_position'))
				if peek is not None:
					server_peek, unused_yaw = _server_pose_from_world(
						player, peek[0], peek[1], peek[2], 0.0)
					if server_peek is not None:
						value['peek_position'] = {
							'x': server_peek[0], 'y': server_peek[1],
							'z': server_peek[2],
						}
					else:
						value['peek_feasible'] = False
				else:
					value['peek_feasible'] = False
				item['candidates'].append(value)
			if item['candidates']:
				shared_affordances.append(item)
		except Exception:
			continue
	shared_navigation = None
	if isinstance(navigation, dict):
		shared_navigation = {
			'graph': {'source': 'none', 'cell_mm': 0, 'nodes': 0},
			'total': {}, 'active': {}, 'recovered': 0, 'search': {}}
		raw_graph = navigation.get('graph')
		if isinstance(raw_graph, dict):
			source = str(raw_graph.get('source') or 'none')
			if source not in ('baked', 'runtime'):
				source = 'none'
			shared_navigation['graph']['source'] = source
			for name, maximum in (('cell_mm', 100000), ('nodes', 100000)):
				try:
					value = int(raw_graph.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['graph'][name] = max(0, min(value, maximum))
		for group in ('total', 'active'):
			raw_group = navigation.get(group)
			if not isinstance(raw_group, dict):
				continue
			for name in ('safe_direct', 'safe_local', 'reactive'):
				try:
					value = int(raw_group.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation[group][name] = max(0, min(value, 100000))
		try:
			value = int(navigation.get('recovered', 0) or 0)
		except Exception:
			value = 0
		shared_navigation['recovered'] = max(0, min(value, 100000))
		raw_search = navigation.get('search')
		if isinstance(raw_search, dict):
			for name in ('pending', 'completed', 'failed', 'oldest_ms',
					'tick_age_ms'):
				try:
					value = int(raw_search.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['search'][name] = max(0, min(value, 3600000))
		raw_orders = navigation.get('orders')
		if isinstance(raw_orders, dict):
			shared_navigation['orders'] = {}
			for name, maximum in (('revision', 1000000000), ('loaded', 30)):
				try:
					value = int(raw_orders.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['orders'][name] = max(0, min(value, maximum))
		raw_aim = navigation.get('aim')
		if isinstance(raw_aim, dict):
			shared_navigation['aim'] = {}
			for name in ('alive', 'targeted', 'aligned', 'traversing', 'limited'):
				try:
					value = int(raw_aim.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['aim'][name] = max(0, min(value, 30))
		raw_driver = navigation.get('driver')
		if isinstance(raw_driver, dict):
			shared_navigation['driver'] = {}
			for name in ('moving', 'drive', 'avoid', 'blocked', 'recovery', 'arrived',
					'server_wait', 'water_guard'):
				try:
					value = int(raw_driver.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['driver'][name] = max(0, min(value, 30))
		raw_safety = navigation.get('safety')
		if isinstance(raw_safety, dict):
			shared_navigation['safety'] = {}
			for name in ('water_guard_total', 'water_guard_active',
					'edge_guard_total', 'edge_guard_active', 'veto_water',
					'veto_terrain', 'veto_obstacle', 'veto_error'):
				try:
					value = int(raw_safety.get(name, 0) or 0)
				except Exception:
					value = 0
				maximum = 100000 if name.endswith('_total') else 30
				shared_navigation['safety'][name] = max(0, min(value, maximum))
	return client.send_bot_observation(
		payload, shared_affordances, shared_navigation)


def authoritative_bot_order(player, mock):
	"""Return one server order converted from shared to loaded-map coordinates."""
	if not network_is_authority(player) or mock is None:
		return None
	bot_id = getattr(mock, '_network_bot_id', None)
	if bot_id is None:
		return None
	client = getattr(player, '_offhangar_network_client', None)
	if client is None:
		return None
	raw = client.bot_orders.get(int(bot_id))
	if raw is None:
		try:
			client.request_bot_orders()
		except Exception:
			pass
		return None
	order = dict(raw)
	for key in ('aim_position', 'face_position', 'move_position', 'route_anchor'):
		point = raw.get(key)
		if not isinstance(point, dict):
			continue
		world = _world_from_server(player, dict(point, world_pose=True))
		if world is not None:
			order[key] = (float(world.x), float(world.y), float(world.z))
	# The server selects who the bot may engage; the authority client owns the
	# rendered simulation and therefore has the freshest exact pose. Aim a
	# currently visible target at its live local mock, as the original offline AI
	# did, instead of at the last 2 Hz contact-report coordinate. Last-known
	# investigate orders retain the server coordinate and cannot see through fog.
	if bool(raw.get('fire_allowed')) and raw.get('target_id') is not None:
		target = None
		try:
			target_id = int(raw.get('target_id'))
			if raw.get('target_kind') == 'human':
				if target_id == getattr(player, '_offhangar_network_id', None):
					target = _local_mock(player)
				else:
					target = _find_mock(player, target_id)
			elif raw.get('target_kind') == 'bot':
				target = _find_bot(target_id)
		except Exception:
			target = None
		if (target is not None and getattr(target, 'isAlive', True) and
				getattr(target, 'health', 1) > 0):
			try:
				position = target.position
				live_position = (
					float(position.x), float(position.y), float(position.z))
				order['aim_position'] = live_position
				order['face_position'] = live_position
				if order.get('combat_mode') == 'advance_contact':
					order['move_position'] = live_position
			except Exception:
				order['fire_allowed'] = False
		else:
			# A visible order is permission to attempt a shot, but only the authority
			# client can prove the rendered target still exists at a live pose.
			order['fire_allowed'] = False
	return order


def publish_authoritative_bots(player, mocks):
	"""Send canonical bot pose, gun, shot and HP state at 30 Hz."""
	if not network_is_authority(player):
		return False
	entity_to_bot = {}
	for candidate in (mocks or {}).values():
		candidate_bot_id = getattr(candidate, '_network_bot_id', None)
		candidate_entity_id = getattr(candidate, 'id', None)
		if candidate_bot_id is not None and candidate_entity_id is not None:
			entity_to_bot[candidate_entity_id] = int(candidate_bot_id)
	states = []
	for mock in (mocks or {}).values():
		bot_id = getattr(mock, '_network_bot_id', None)
		if bot_id is None:
			continue
		try:
			pos = mock.position
			killer_bot_id = entity_to_bot.get(
				getattr(mock, 'last_killer_id', None), 0)
			yaw = _finite_float(getattr(mock, 'yaw', 0.0))
			server_pos, server_yaw = _server_pose_from_world(
				player, pos.x, pos.y, pos.z, yaw)
			unused_pos, server_aim_yaw = _server_pose_from_world(
				player, pos.x, pos.y, pos.z,
				yaw + _finite_float(getattr(mock, '_turret_yaw', 0.0)))
			states.append({
				'id': int(bot_id),
				'x': server_pos[0], 'y': server_pos[1], 'z': server_pos[2],
				'yaw': server_yaw, 'aim_yaw': server_aim_yaw,
				'gun_pitch': _finite_float(getattr(mock, '_gun_pitch', 0.0)),
				'speed': _finite_float(getattr(mock, '_veh_velocity', 0.0)),
				'turn_velocity': _finite_float(
					getattr(mock, '_veh_turn_velocity', 0.0)),
				'fire_seq': int(getattr(mock, '_network_bot_fire_seq', 0) or 0),
				'shell_index': int(getattr(mock, '_network_bot_shell_index', 0) or 0),
				'health': max(0, int(getattr(mock, 'health', 0) or 0)),
				'killer_bot_id': int(killer_bot_id or 0),
				'killer_kind': 'bot' if killer_bot_id else '',
				'killer_id': int(killer_bot_id or 0),
				'alive': bool(getattr(mock, 'isAlive', False)) and int(getattr(mock, 'health', 0) or 0) > 0,
			})
		except Exception:
			continue
	client = getattr(player, '_offhangar_network_client', None)
	return client.send_bot_states(states) if states else False


def send_local_bot_hit(player, bot_id, shot_seq, damage, shot_result,
		impact_position=None):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is None or not client.ready or bot_id is None or shot_seq is None:
		return False
	server_impact = None
	if impact_position is not None:
		try:
			server_impact, unused_yaw = _server_pose_from_world(player,
				impact_position.x, impact_position.y, impact_position.z, 0.0)
		except Exception:
			pass
	return client.send_bot_hit(bot_id, shot_seq, damage, shot_result, server_impact)


def send_authoritative_bot_human_hit(player, bot_id, target_id, shot_seq,
		damage, shot_result, impact_position=None):
	if not network_is_authority(player) or target_id is None:
		return False
	server_impact = None
	if impact_position is not None:
		try:
			server_impact, unused_yaw = _server_pose_from_world(player,
				impact_position.x, impact_position.y, impact_position.z, 0.0)
		except Exception:
			pass
	return player._offhangar_network_client.send_bot_human_hit(
		bot_id, target_id, shot_seq, damage, shot_result, server_impact)


def send_authoritative_rules(player, bases):
	if not network_is_authority(player):
		return False
	rules = {'bases': {}}
	for team in (1, 2):
		state = bases.get(team, {}) if bases is not None else {}
		rules['bases'][str(team)] = {
			'points': max(0, min(int(state.get('points', 0) or 0), 100)),
			'stopped': bool(state.get('stopped', False)),
		}
	return player._offhangar_network_client.send_rules(rules)


def send_authoritative_result(player, winner, reason, base_team=0):
	if not network_is_authority(player):
		return False
	return player._offhangar_network_client.send_battle_result(
		winner, reason, base_team)


def install_network_hud_metrics():
	"""Replace only ping/lag arguments of the stock 0.8.2 debug panel."""
	try:
		import gui.Scaleform.Battle as battle_module
		stats_class = getattr(battle_module, '_PerformanceStats', None)
		if stats_class is None or getattr(stats_class, '_offhangar_network_metrics', False):
			return stats_class is not None
		original = stats_class.updateDebugInfo
		def _network_update(stats, fps, ping, lag, recorded_fps):
			try:
				player = BigWorld.player()
				client = getattr(player, '_offhangar_network_client', None) if player is not None else None
				if client is not None and client.phase == 'battle':
					now = time.time()
					ping = 999 if client.rtt_ms is None else max(0, min(int(round(client.rtt_ms)), 999))
					lag = (not client.connected or client._last_snapshot <= 0.0 or
						now - client._last_snapshot > 2.5 or now - client._last_receive > 2.5)
			except Exception:
				pass
			return original(stats, fps, ping, lag, recorded_fps)
		stats_class.updateDebugInfo = _network_update
		stats_class._offhangar_network_metrics = True
		LOG_NOTE('LAN native ping/lag HUD metrics installed')
		return True
	except Exception as error:
		LOG_ERROR('LAN native ping/lag HUD metrics install failed:', str(error))
		return False


def _world_from_server(player, state):
	"""Map the server's small synthetic arena onto the loaded WoT map.

	The server deliberately does not know the proprietary map coordinates.  The
	client already parsed both team anchors, so it converts the shared x/z frame
	into the current map's real coordinates here.
	"""
	try:
		import Math
		formation = getattr(player, '_offhangar_network_formation', None)
		if formation is None:
			return Math.Vector3(_finite_float(state.get('x')), _finite_float(state.get('y')), _finite_float(state.get('z')))
		base1 = formation(1, 0)
		base2 = formation(2, 0)
		b1x, b1z = float(base1[0]), float(base1[1])
		b2x, b2z = float(base2[0]), float(base2[1])
		dx, dz = b2x - b1x, b2z - b1z
		length = math.sqrt(dx * dx + dz * dz) or 1.0
		axis_x, axis_z = dx / length, dz / length
		right_x, right_z = axis_z, -axis_x
		if bool(state.get('world_pose', False)):
			x = b1x + axis_x * _finite_float(state.get('z')) + right_x * _finite_float(state.get('x'))
			z = b1z + axis_z * _finite_float(state.get('z')) + right_z * _finite_float(state.get('x'))
			return Math.Vector3(x, _finite_float(state.get('y')), z)
		team = int(state.get('team', 1) or 1)
		slot = int(state.get('slot', 0) or 0)
		spawn_x = _finite_float(state.get('spawn_x', slot * 12.0))
		spawn_z = _finite_float(state.get('spawn_z', -35.0 if team == 1 else 35.0))
		base = formation(team, slot)
		base_x, base_z = float(base[0]), float(base[1])
		travel = _finite_float(state.get('z')) - spawn_z
		lateral = _finite_float(state.get('x')) - spawn_x
		x = base_x + axis_x * travel + right_x * lateral
		z = base_z + axis_z * travel + right_z * lateral
		y = _finite_float(state.get('y'))
		return Math.Vector3(x, y, z)
	except Exception:
		return None


def _ground_world_point(point):
	"""Resolve terrain height in the battle space, walking below roofs."""
	if point is None:
		return None
	try:
		import Math
		import sys
		module = sys.modules.get('gui.mods.offhangar.offline_battle')
		space_getter = getattr(module, '_offh_bspace', None) if module is not None else None
		if not callable(space_getter):
			return point
		ground_y = None
		from_y = 1000.0
		for unused in range(4):
			hit = BigWorld.wg_collideSegment(space_getter(),
				Math.Vector3(point.x, from_y, point.z),
				Math.Vector3(point.x, -1000.0, point.z), 128)
			if hit is None:
				break
			ground_y = hit[0].y
			below = BigWorld.wg_collideSegment(space_getter(),
				Math.Vector3(point.x, ground_y - 0.4, point.z),
				Math.Vector3(point.x, -1000.0, point.z), 128)
			if below is None or (ground_y - below[0].y) < 2.5:
				break
			from_y = ground_y - 0.4
		if ground_y is not None:
			point.y = ground_y
	except Exception:
		pass
	return point


def _find_mock(player, server_id):
	try:
		import sys
		module = sys.modules.get('gui.mods.offhangar.offline_battle')
		mocks = getattr(module, 'G_MOCK_VEHICLES', {}) if module is not None else {}
		for mock in (mocks or {}).values():
			if getattr(mock, '_network_server_id', None) == server_id:
				try:
					getattr(player, '_offhangar_network_pending_remote_ids', {}).pop(server_id, None)
				except Exception:
					pass
				return mock
	except Exception:
		pass
	return None


def _find_bot(bot_id):
	try:
		bot_id = int(bot_id)
	except (TypeError, ValueError):
		return None
	for mock in (_offline_mocks() or {}).values():
		if getattr(mock, '_network_bot_id', None) == bot_id:
			return mock
	return None


def _world_yaw_from_server(player, state):
	"""Convert the server's synthetic yaw into the loaded map's yaw frame."""
	try:
		formation = getattr(player, '_offhangar_network_formation', None)
		if formation is None:
			return _finite_float(state.get('yaw'))
		base1 = formation(1, 0)
		base2 = formation(2, 0)
		axis_yaw = math.atan2(float(base2[0]) - float(base1[0]), float(base2[1]) - float(base1[1]))
		return _finite_float(state.get('yaw')) + axis_yaw
	except Exception:
		return _finite_float(state.get('yaw'))


def _offline_mocks():
	try:
		import sys
		module = sys.modules.get('gui.mods.offhangar.offline_battle')
		return getattr(module, 'G_MOCK_VEHICLES', {}) if module is not None else {}
	except Exception:
		return {}


def _local_mock(player):
	try:
		return (_offline_mocks() or {}).get(getattr(player, 'playerVehicleID', -1))
	except Exception:
		return None


def _local_entity_id_for_server(player, server_id):
	if server_id == getattr(player, '_offhangar_network_id', None):
		return getattr(player, 'playerVehicleID', -1)
	mock = _find_mock(player, server_id)
	return getattr(mock, 'id', -1) if mock is not None else -1


def _local_killer_id_from_state(player, state):
	"""Resolve the server's stable killer identity into this client's entity id."""
	kind = str(state.get('killer_kind') or '')
	try:
		killer_id = int(state.get('killer_id', 0) or 0)
	except (TypeError, ValueError):
		killer_id = 0
	if kind == 'human' and killer_id:
		return _local_entity_id_for_server(player, killer_id)
	if kind == 'bot' and killer_id:
		mock = _find_bot(killer_id)
		return getattr(mock, 'id', -1) if mock is not None else -1
	# Protocol-5 compatibility with servers that only relay bot killers.
	legacy_bot_id = state.get('killer_bot_id')
	if legacy_bot_id not in (None, 0, '0'):
		mock = _find_bot(legacy_bot_id)
		return getattr(mock, 'id', -1) if mock is not None else -1
	return -1


def _set_remote_spot_visibility(player, mock, visible):
	"""Keep a remote human's model, marker and minimap state in lockstep."""
	if mock is None:
		return False
	visible = bool(visible)
	previous = bool(getattr(mock, '_spot_visible', False))
	mock._spot_visible = visible
	model = getattr(mock, '_chassis_model', None) or getattr(mock, 'model', None)
	if model is not None and getattr(mock, 'health', 0) > 0:
		try:
			model.visible = visible
			model.visibleAttachments = visible
		except Exception:
			pass
	try:
		from gui import WindowsManager
		battle = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
		markers = getattr(battle, 'vMarkersManager', None) if battle is not None else None
		marker = getattr(mock, 'marker', None)
		if markers is not None:
			if visible and marker in (None, -1) and getattr(mock, 'proxy', None) is not None:
				mock.marker = markers.createMarker(mock.proxy)
			elif not visible and marker not in (None, -1):
				markers.destroyMarker(marker)
				mock.marker = None
		if previous != visible:
			minimap = getattr(battle, 'minimap', None) if battle is not None else None
			if minimap is not None:
				if visible:
					minimap.notifyVehicleStart(mock.id)
				else:
					minimap.notifyVehicleStop(mock.id)
	except Exception:
		pass
	return visible


def update_remote_spotting(player, mock, force=False):
	"""Apply the offline battle's local spotting rules to one LAN human.

	LAN humans do not run bot driving AI, but opposing humans still need the same
	50 m proximity spot, view-range/static-LOS check, team vision and five-second
	spot memory used by locally simulated opponents.
	"""
	if player is None or mock is None:
		return False
	alive = bool(getattr(mock, 'isAlive', True)) and int(getattr(mock, 'health', 0) or 0) > 0
	if not alive:
		return bool(getattr(mock, '_spot_visible', False))
	player_team = int(getattr(player, '_offhangar_team',
		getattr(player, '_offhangar_network_team', 1)) or 1)
	remote_team = int(getattr(mock, '_bot_team',
		(getattr(mock, 'publicInfo', None) or {}).get('team', 2)) or 2)
	if remote_team == player_team or not bool(_network_config().get('spotting_enabled', True)):
		return _set_remote_spot_visibility(player, mock, True)
	try:
		now = float(BigWorld.time())
	except Exception:
		now = time.time()
	next_check = float(getattr(mock, '_network_spot_next', 0.0) or 0.0)
	if not force and now < next_check:
		visible = now < float(getattr(mock, '_spot_until', 0.0) or 0.0)
		return _set_remote_spot_visibility(player, mock, visible)
	mock._network_spot_next = now + 0.5
	local = _local_mock(player)
	if local is None or getattr(local, 'position', None) is None or getattr(mock, 'position', None) is None:
		return _set_remote_spot_visibility(player, mock, False)

	view_range = 400.0
	try:
		view_range = float(local.typeDescriptor.turret.get('circularVisionRadius', 400.0))
	except Exception:
		pass
	try:
		import sys
		offline = sys.modules.get('gui.mods.offhangar.offline_battle')
		crew_factor = getattr(offline, '_crew_factor', None) if offline is not None else None
		module_factor = getattr(offline, '_module_factor', None) if offline is not None else None
		if callable(crew_factor) and callable(module_factor):
			view_range *= float(crew_factor(local, 'vision')) * float(module_factor(local, 'vision'))
	except Exception:
		pass
	view_range = max(50.0, view_range)

	def _has_line_of_sight(observer, target, turret_sample=False):
		dx = target.position.x - observer.position.x
		dz = target.position.z - observer.position.z
		distance_sq = dx * dx + dz * dz
		if distance_sq <= 2500.0:
			return True
		if distance_sq > view_range * view_range:
			return False
		try:
			import Math, sys
			offline = sys.modules.get('gui.mods.offhangar.offline_battle')
			space_getter = getattr(offline, '_offh_bspace', None) if offline is not None else None
			space_id = space_getter() if callable(space_getter) else getattr(player, 'spaceID', 0)
			start = Math.Vector3(observer.position.x, observer.position.y + 2.5, observer.position.z)
			end = Math.Vector3(target.position.x, target.position.y + 1.5, target.position.z)
			if BigWorld.wg_collideSegment(space_id, start, end, 128) is None:
				return True
			if turret_sample:
				end = Math.Vector3(target.position.x, target.position.y + 2.2, target.position.z)
				return BigWorld.wg_collideSegment(space_id, start, end, 128) is None
		except Exception:
			pass
		return False

	seen = _has_line_of_sight(local, mock, True)
	if not seen:
		# Team vision: any living allied local bot or LAN human can relay the spot.
		for ally in (_offline_mocks() or {}).values():
			if ally is local or ally is mock or not bool(getattr(ally, 'isAlive', True)):
				continue
			ally_team = int(getattr(ally, '_bot_team',
				(getattr(ally, 'publicInfo', None) or {}).get('team', 2)) or 2)
			if ally_team == player_team and _has_line_of_sight(ally, mock, False):
				seen = True
				break
	if seen:
		mock._spot_until = now + 5.0
	visible = now < float(getattr(mock, '_spot_until', 0.0) or 0.0)
	return _set_remote_spot_visibility(player, mock, visible)


def _notify_network_death(player, mock, killer_id=-1):
	if mock is None or getattr(mock, '_network_death_notified', False):
		return False
	if killer_id in (None, -1):
		killer_id = getattr(mock, 'last_killer_id', -1)
	if killer_id is None:
		killer_id = -1
	mock._network_death_pending = False
	mock._network_death_notified = True
	try:
		player.arena.onVehicleKilled(mock.id, killer_id, 0)
	except Exception:
		try:
			mock.isAlive = False
		except Exception:
			pass
	return True


def _push_mock_health(player, mock, health, max_health, alive, killer_id=-1, is_local=False):
	if mock is None:
		return
	old_health = int(getattr(mock, 'health', health) or 0)
	old_alive = bool(getattr(mock, 'isAlive', old_health > 0))
	health = max(0, int(health or 0))
	max_health = max(1, int(max_health or getattr(mock, 'maxHealth', 1) or 1))
	if health > max_health:
		health = max_health
	mock.health = health
	mock.maxHealth = max_health
	if killer_id not in (None, -1):
		try:
			mock.last_killer_id = killer_id
		except Exception:
			pass
	if getattr(mock, 'publicInfo', None) is not None:
		mock.publicInfo['isAlive'] = bool(alive)
	try:
		from gui import WindowsManager
		battle = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
		if is_local:
			try:
				if getattr(player, 'vehicle', None) is not None:
					player.vehicle.health = health
			except Exception:
				pass
			damage_panel = getattr(battle, 'damagePanel', None) if battle is not None else None
			if damage_panel is not None and old_health != health:
				damage_panel.updateHealth(health)
			try:
				player.guiSessionProvider.invalidateVehicleState(
					1, player.playerVehicleID, health, health)
			except Exception:
				pass
		else:
			markers = getattr(battle, 'vMarkersManager', None) if battle is not None else None
			marker = getattr(mock, 'marker', None)
			if markers is not None and marker not in (None, -1) and old_health != health:
				markers.onVehicleHealthChanged(marker, health, killer_id, 0)
	except Exception:
		pass
	if not alive or health <= 0:
		mock.health = 0
		if not getattr(mock, '_network_death_notified', False):
			if killer_id not in (None, -1):
				_notify_network_death(player, mock, killer_id)
			elif not getattr(mock, '_network_death_pending', False):
				# Snapshots and combat events are separate messages. The server sends the
				# snapshot first, so leave one short window for the following event to
				# supply its killer instead of permanently posting "lost in battle".
				mock._network_death_pending = True
				try:
					BigWorld.callback(0.5, lambda: _notify_network_death(player, mock, -1))
				except Exception:
					_notify_network_death(player, mock, -1)
	elif not old_alive:
		try:
			mock.isAlive = True
		except Exception:
			pass


def _apply_remote_transform(player, mock, world, yaw):
	"""Move both the mock state and the BigWorld model driven by it."""
	if mock is None or world is None:
		return
	mock.position = world
	mock.yaw = yaw
	pitch = float(getattr(mock, 'pitch', 0.0) or 0.0)
	roll = float(getattr(mock, 'roll', 0.0) or 0.0)
	matrix = getattr(mock, 'matrix', None)
	if matrix is not None:
		try:
			matrix.setRotateYPR((yaw, pitch, roll))
			matrix.translation = world
		except Exception:
			pass

	# Remote mocks used to retain the local player's AvatarFilter. Updating that
	# object moved no remote model (and risked perturbing the local one). Drive the
	# filter owned by the remote BigWorld entity, exactly as the bot loop does.
	entity = getattr(mock, 'bw_entity', None)
	entity_filter = getattr(entity, 'filter', None) if entity is not None else None
	if entity_filter is not None:
		try:
			import sys
			offline = sys.modules.get('gui.mods.offhangar.offline_battle')
			space_getter = getattr(offline, '_offh_bspace', None) if offline is not None else None
			if callable(space_getter):
				entity_filter.set(BigWorld.time(), space_getter(), entity.id,
					world, (roll, pitch, yaw), 0)
			mock.filter = entity_filter
		except Exception:
			pass

	chassis = getattr(mock, '_chassis_model', None) or getattr(mock, 'model', None)
	if chassis is not None:
		# Set the root immediately so the first snapshot fixes a sky-high async
		# spawn even before the entity/filter or Servo has finished attaching.
		try:
			chassis.position = world
			chassis.yaw = yaw
		except Exception:
			pass
		if matrix is not None and not getattr(mock, '_servo_added', False):
			try:
				chassis.addMotor(BigWorld.Servo(matrix))
				mock._servo_added = True
			except Exception:
				pass


def _queue_network_transform(player, mock, world, hull_yaw, aim_yaw,
		gun_pitch, snap=False, longitudinal_speed=None, turn_velocity=None):
	"""Queue a 30 Hz pose; the battle frame loop renders between packets."""
	if mock is None or world is None:
		return
	now = time.time()
	previous_target = getattr(mock, '_network_target_position', None)
	previous_time = float(getattr(mock, '_network_target_time', 0.0) or 0.0)
	if longitudinal_speed is not None:
		speed = max(-80.0, min(80.0, _finite_float(longitudinal_speed, 0.0)))
		mock._network_target_velocity = (
			math.sin(hull_yaw) * speed, 0.0, math.cos(hull_yaw) * speed)
	elif previous_target is not None and previous_time > 0.0 and now > previous_time:
		delta = max(0.01, min(now - previous_time, 0.25))
		vx = (world.x - previous_target.x) / delta
		vy = (world.y - previous_target.y) / delta
		vz = (world.z - previous_target.z) / delta
		speed = math.sqrt(vx * vx + vy * vy + vz * vz)
		if speed > 80.0:
			scale = 80.0 / speed
			vx *= scale
			vy *= scale
			vz *= scale
		mock._network_target_velocity = (vx, vy, vz)
	else:
		mock._network_target_velocity = (0.0, 0.0, 0.0)
	mock._network_target_position = world
	mock._network_target_yaw = hull_yaw
	mock._network_target_aim_yaw = aim_yaw
	mock._network_target_gun_pitch = gun_pitch
	if turn_velocity is not None:
		mock._network_target_turn_velocity = max(
			-10.0, min(10.0, _finite_float(turn_velocity, 0.0)))
	mock._network_target_time = now
	if snap or not getattr(mock, '_network_smoothing_ready', False):
		_apply_remote_transform(player, mock, world, hull_yaw)
		mock._turret_yaw = aim_yaw - hull_yaw
		mock._gun_pitch = gun_pitch
		try:
			mock._t_mat.setRotateYPR((mock._turret_yaw, 0, 0))
		except Exception:
			pass
		try:
			mock._g_mat.setRotateYPR((0, mock._gun_pitch, 0))
		except Exception:
			pass
		mock._network_smoothing_ready = True


def _short_angle_delta(target, current):
	delta = target - current
	while delta > math.pi:
		delta -= math.pi * 2.0
	while delta < -math.pi:
		delta += math.pi * 2.0
	return delta


def advance_network_smoothing(player, mocks, frame_dt):
	"""Interpolate and briefly predict remote humans/bots every render frame."""
	try:
		import Math
		dt = max(0.001, min(_finite_float(frame_dt, 0.016), 0.1))
		alpha = 1.0 - math.exp(-20.0 * dt)
		now = time.time()
		for mock in (mocks or {}).values():
			is_human = bool(getattr(mock, '_network_remote', False))
			is_bot = bool(getattr(mock, '_network_shared_bot', False))
			if not is_human and not (is_bot and not network_is_authority(player)):
				continue
			if getattr(mock, '_network_death_notified', False) or not getattr(mock, 'isAlive', True):
				continue
			target = getattr(mock, '_network_target_position', None)
			current = getattr(mock, 'position', None)
			if target is None or current is None:
				continue
			vx, vy, vz = getattr(mock, '_network_target_velocity', (0.0, 0.0, 0.0))
			# Explicit authority velocity makes prediction useful even when the
			# authority renders below 30 FPS. Keep the LAN horizon bounded so a lost
			# packet cannot make a tank coast indefinitely.
			predict = max(0.0, min(
				now - float(getattr(mock, '_network_target_time', now)), 0.12))
			px = target.x + vx * predict
			py = target.y + vy * predict
			pz = target.z + vz * predict
			if is_bot and predict > 0.0:
				# Prediction is presentation only. Never extrapolate a shared bot from
				# its last authoritative safe pose across a baked water/cliff cell; the
				# next zero-speed guard packet would otherwise make it rubber-band back.
				# If the authority itself has already fallen, its target pose still wins
				# and the physical consequence remains visible on every client.
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					pose_safe = getattr(offline, '_offh_ai_baked_pose_safe', None)
					if callable(pose_safe) and not pose_safe((px, py, pz)):
						px, py, pz = target.x, target.y, target.z
				except Exception:
					pass
			dx, dy, dz = px - current.x, py - current.y, pz - current.z
			distance_sq = dx * dx + dy * dy + dz * dz
			if distance_sq > 625.0:
				world = Math.Vector3(px, py, pz)
				yaw = _finite_float(getattr(mock, '_network_target_yaw', mock.yaw))
			else:
				world = Math.Vector3(current.x + dx * alpha,
					current.y + dy * alpha, current.z + dz * alpha)
				yaw = mock.yaw + _short_angle_delta(
					_finite_float(getattr(mock, '_network_target_yaw', mock.yaw)), mock.yaw) * alpha
			_apply_remote_transform(player, mock, world, yaw)
			target_aim = _finite_float(getattr(mock, '_network_target_aim_yaw', yaw))
			desired_turret = _short_angle_delta(target_aim, yaw)
			current_turret = _finite_float(getattr(mock, '_turret_yaw', desired_turret))
			mock._turret_yaw = current_turret + _short_angle_delta(desired_turret, current_turret) * alpha
			current_pitch = _finite_float(getattr(mock, '_gun_pitch', 0.0))
			target_pitch = _finite_float(getattr(mock, '_network_target_gun_pitch', current_pitch))
			mock._gun_pitch = current_pitch + (target_pitch - current_pitch) * alpha
			try:
				mock._t_mat.setRotateYPR((mock._turret_yaw, 0, 0))
			except Exception:
				pass
			try:
				mock._g_mat.setRotateYPR((0, mock._gun_pitch, 0))
			except Exception:
				pass
		return True
	except Exception:
		return False


def _apply_local_state(player, state):
	mock = _local_mock(player)
	if mock is None:
		return
	server_health = int(state.get('health', getattr(mock, 'health', 0)) or 0)
	previous = getattr(player, '_offhangar_network_server_health', None)
	player._offhangar_network_server_health = server_health
	if previous is None:
		target_health = min(int(getattr(mock, 'health', server_health) or 0), server_health)
	else:
		delta = max(0, int(previous) - server_health)
		target_health = max(0, int(getattr(mock, 'health', server_health) or 0) - delta)
	if not bool(state.get('alive', True)):
		target_health = 0
	_push_mock_health(player, mock, target_health,
		state.get('max_health', getattr(mock, 'maxHealth', 1)),
		bool(state.get('alive', True)), _local_killer_id_from_state(player, state), True)


def _apply_remote_state(player, state):
	server_id = state.get('id')
	if server_id is None or server_id == getattr(player, '_offhangar_network_id', None):
		return
	mock = _find_mock(player, server_id)
	if mock is None:
		pending = getattr(player, '_offhangar_network_pending_remote_ids', None)
		if pending is None:
			pending = {}
			player._offhangar_network_pending_remote_ids = pending
		pending_since = pending.get(server_id)
		if pending_since is not None and time.time() - pending_since < 30.0:
			return
		pending[server_id] = time.time()
		spawn = getattr(player, '_offhangar_network_spawn_remote', None)
		formation = getattr(player, '_offhangar_network_formation', None)
		if callable(spawn) and callable(formation):
			team = int(state.get('team', 2) or 2)
			slot = int(state.get('slot', 0) or 0)
			sx, sz, syaw = formation(team, slot)
			try:
				import Math
				# Map the server's synthetic coordinates onto this client's real
				# arena.  y is resolved by the existing ground probe in the spawn
				# helper, so a stale/missing terrain height cannot float the tank.
				point = _world_from_server(player, state)
				if point is None:
					point = Math.Vector3(float(sx), 0.0, float(sz))
				point = _ground_world_point(point)
			except Exception:
				point = None
			if point is not None:
				player._offhangar_network_forced_id = server_id
				player._offhangar_network_forced_state = state
				player._offhangar_network_forced_name = state.get('name') or ('Remote_%s' % server_id)
				player._forced_spawn_pos = (point.x, point.y, point.z)
				player._forced_spawn_team = team
				player._forced_spawn_yaw = _world_yaw_from_server(player, state)
				player._forced_spawn_vehname = state.get('vehicle') or 'ussr:MS-1'
				try:
					spawn_result = spawn(_NetworkSpawnEvent())
					if spawn_result is False:
						pending.pop(server_id, None)
				except Exception:
					pending.pop(server_id, None)
					LOG_ERROR('LAN remote spawn failed:', server_id)
				finally:
					player._forced_spawn_pos = None
					player._forced_spawn_team = None
					player._forced_spawn_yaw = None
					player._forced_spawn_vehname = None
					player._offhangar_network_forced_id = None
					player._offhangar_network_forced_name = None
					player._offhangar_network_forced_state = None
			return
		pending.pop(server_id, None)
		return
	try:
		death_locked = bool(getattr(mock, '_network_death_notified', False))
		target_alive = bool(state.get('alive', True))
		world_yaw = _world_yaw_from_server(player, state)
		world_aim_yaw = _world_yaw_from_server(player, dict(state, yaw=state.get('aim_yaw')))
		world = _world_from_server(player, state)
		if world is not None and abs(_finite_float(state.get('y'))) < 0.001:
			# The server has no map terrain. Retry the local ground probe instead of
			# preserving a failed sky-high async spawn forever.
			world = _ground_world_point(world)
		# Apply the final death snapshot once, then freeze both mock and model.
		# The input sender can run for a few frames after death; moving only the
		# marker proxy while the destroyed model stays put split them apart.
		if not death_locked:
			_queue_network_transform(player, mock, world, world_yaw, world_aim_yaw,
				_finite_float(state.get('gun_pitch'), getattr(mock, '_gun_pitch', 0.0)),
				not target_alive)
		_push_mock_health(player, mock, state.get('health', mock.health),
			state.get('max_health', mock.maxHealth), target_alive,
			_local_killer_id_from_state(player, state))
	except Exception:
		pass


def _apply_bot_state(player, state, force_authority_pose=False):
	mock = _find_bot(state.get('id'))
	if mock is None:
		return
	try:
		old_health = max(0, int(getattr(mock, 'health', 0) or 0))
		target_alive = bool(state.get('alive', True))
		is_authority = network_is_authority(player)
		if not is_authority or force_authority_pose:
			previous_fire = int(getattr(mock, '_network_seen_fire_seq', 0) or 0)
			fire_seq = int(state.get('fire_seq', previous_fire) or 0)
			death_locked = bool(getattr(mock, '_network_death_notified', False))
			world = _world_from_server(player, state)
			world_yaw = _world_yaw_from_server(player, state)
			world_aim_yaw = _world_yaw_from_server(player,
				dict(state, yaw=state.get('aim_yaw')))
			if not death_locked:
				_queue_network_transform(player, mock, world, world_yaw, world_aim_yaw,
					_finite_float(state.get('gun_pitch'), getattr(mock, '_gun_pitch', 0.0)),
					force_authority_pose or not target_alive,
					state.get('speed'), state.get('turn_velocity'))
			if force_authority_pose:
				mock._veh_velocity = max(-80.0, min(
					80.0, _finite_float(state.get('speed'), 0.0)))
				mock._veh_turn_velocity = max(-10.0, min(
					10.0, _finite_float(state.get('turn_velocity'), 0.0)))
			if not is_authority and fire_seq > previous_fire:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					present = getattr(offline, 'play_network_remote_shot', None) if offline is not None else None
					if callable(present) and world is not None:
						present(mock, world, world_aim_yaw, mock._gun_pitch,
							state.get('shell_index', 0))
				except Exception:
					LOG_ERROR('LAN bot shot presentation failed:', state.get('id'))
			mock._network_seen_fire_seq = fire_seq
			mock._network_bot_fire_seq = max(
				int(getattr(mock, '_network_bot_fire_seq', 0) or 0), fire_seq)
			mock._network_bot_shell_index = int(state.get('shell_index', 0) or 0)
		killer_id = _local_killer_id_from_state(player, state)
		_push_mock_health(player, mock, state.get('health', mock.health),
			state.get('max_health', mock.maxHealth), target_alive, killer_id)
		if not is_authority:
			new_health = max(0, int(getattr(mock, 'health', 0) or 0))
			if old_health > new_health:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					record_assist = getattr(offline, 'record_network_spot_assist', None) if offline is not None else None
					if callable(record_assist):
						record_assist(player, mock, old_health - new_health, not target_alive)
				except Exception:
					LOG_ERROR('LAN spotting-assist statistics failed:', state.get('id'))
			update_remote_spotting(player, mock)
	except Exception:
		LOG_ERROR('LAN bot state apply failed:', state.get('id'))


def _apply_snapshot(player, message):
	for state in message.get('players') or []:
		if state.get('id') == getattr(player, '_offhangar_network_id', None):
			_apply_local_state(player, state)
		else:
			_apply_remote_state(player, state)
	handoff = bool(getattr(
		player, '_offhangar_network_authority_handoff_pending', False))
	for state in message.get('bots') or []:
		_apply_bot_state(player, state, handoff)
	if handoff and message.get('bots'):
		player._offhangar_network_authority_handoff_pending = False
	rules = message.get('rules')
	if rules is not None:
		callback = getattr(player, '_offhangar_apply_network_rules_state', None)
		if callable(callback):
			try:
				callback(rules)
			except Exception:
				LOG_ERROR('LAN rules state apply failed')
	result = message.get('battle_result')
	if result is not None and not getattr(player, '_offhangar_network_result_applied', False):
		callback = getattr(player, '_offhangar_apply_network_battle_result', None)
		if callable(callback):
			player._offhangar_network_result_applied = True
			callback(result)


def _handle_events(player, events):
	for event in events:
		kind = event.get('kind')
		if kind == 'authority':
			client = getattr(player, '_offhangar_network_client', None)
			if client is not None:
				client._set_authority(event.get('player_id'))
		elif kind == 'bot_manifest':
			player._offhangar_network_bot_manifest = event.get('bots') or []
			LOG_NOTE('LAN bot manifest received: %d bot(s)' % len(
				player._offhangar_network_bot_manifest))
		elif kind == 'battle_result':
			if not getattr(player, '_offhangar_network_result_applied', False):
				callback = getattr(player, '_offhangar_apply_network_battle_result', None)
				if callable(callback):
					player._offhangar_network_result_applied = True
					callback(event)
		elif kind == 'shot':
			attacker_server_id = event.get('attacker')
			if attacker_server_id == getattr(player, '_offhangar_network_id', None):
				continue
			attacker_mock = _find_mock(player, attacker_server_id)
			if attacker_mock is None:
				continue
			try:
				import sys
				offline = sys.modules.get('gui.mods.offhangar.offline_battle')
				present = getattr(offline, 'play_network_remote_shot', None) if offline is not None else None
				start = _world_from_server(player, event)
				aim_yaw = _world_yaw_from_server(player, dict(event, yaw=event.get('aim_yaw')))
				if callable(present) and start is not None:
					present(attacker_mock, start, aim_yaw,
						_finite_float(event.get('gun_pitch')), event.get('shell_index', 0))
			except Exception:
				LOG_ERROR('LAN remote shot presentation failed')
		elif kind == 'hit':
			LOG_DEBUG('LAN hit attacker=%s target=%s damage=%s' % (
				event.get('attacker'), event.get('target'), event.get('damage')))
			target_id = event.get('target')
			attacker_server_id = event.get('attacker')
			attacker_id = _local_entity_id_for_server(player, event.get('attacker'))
			if target_id == getattr(player, '_offhangar_network_id', None):
				mock = _local_mock(player)
				is_local = True
			else:
				mock = _find_mock(player, target_id)
				is_local = False
			if mock is not None:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					present = getattr(offline, 'play_network_hit_feedback', None) if offline is not None else None
					attacker_mock = (_local_mock(player) if attacker_server_id == getattr(
						player, '_offhangar_network_id', None) else _find_mock(player, attacker_server_id))
					hit_pos = _world_from_server(player, event)
					if callable(present):
						present(player, attacker_mock, mock, hit_pos,
							event.get('shot_result', 2), event.get('damage', 0),
							event.get('shell_index', 0), is_local,
							attacker_server_id == getattr(player, '_offhangar_network_id', None),
							bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN hit presentation failed')
				try:
					record_stats = getattr(offline, 'record_network_combat_stats', None) if offline is not None else None
					if callable(record_stats):
						record_stats(player,
							attacker_server_id == getattr(player, '_offhangar_network_id', None),
							is_local, mock, event.get('damage', 0),
							event.get('shot_result', 2), bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN hit statistics failed')
				server_health = int(event.get('health', getattr(mock, 'health', 0)) or 0)
				if is_local:
					previous = getattr(player, '_offhangar_network_server_health', None)
					if previous is None:
						health = max(0, int(getattr(mock, 'health', 0) or 0) - int(event.get('damage', 0) or 0))
					else:
						health = max(0, int(getattr(mock, 'health', 0) or 0) - max(0, int(previous) - server_health))
					player._offhangar_network_server_health = server_health
				else:
					health = server_health
				_push_mock_health(player, mock, health,
					getattr(mock, 'maxHealth', max(1, health)),
					not bool(event.get('dead', False)), attacker_id, is_local)
		elif kind == 'health':
			target_id = event.get('target')
			server_health = int(event.get('health', 0) or 0)
			if target_id == getattr(player, '_offhangar_network_id', None):
				# The local simulation already applied this damage and its effects.
				player._offhangar_network_server_health = server_health
				mock = _local_mock(player)
				if mock is not None:
					_push_mock_health(player, mock,
						min(int(getattr(mock, 'health', server_health) or 0), server_health),
						getattr(mock, 'maxHealth', max(1, server_health)),
						not bool(event.get('dead', False)), -1, True)
			else:
				mock = _find_mock(player, target_id)
				if mock is not None:
					_push_mock_health(player, mock, server_health,
						getattr(mock, 'maxHealth', max(1, server_health)),
						not bool(event.get('dead', False)), -1, False)
		elif kind == 'bot_hit':
			mock = _find_bot(event.get('target_bot'))
			attacker_server_id = event.get('attacker')
			attacker_mock = (_local_mock(player) if attacker_server_id == getattr(
				player, '_offhangar_network_id', None) else _find_mock(player, attacker_server_id))
			if mock is not None:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					present = getattr(offline, 'play_network_hit_feedback', None) if offline is not None else None
					if callable(present):
						present(player, attacker_mock, mock, _world_from_server(player, event),
							event.get('shot_result', 2), event.get('damage', 0),
							event.get('shell_index', 0), False,
							attacker_server_id == getattr(player, '_offhangar_network_id', None),
							bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN bot hit presentation failed')
				try:
					record_stats = getattr(offline, 'record_network_combat_stats', None) if offline is not None else None
					if callable(record_stats):
						record_stats(player,
							attacker_server_id == getattr(player, '_offhangar_network_id', None),
							False, mock, event.get('damage', 0),
							event.get('shot_result', 2), bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN bot-hit statistics failed')
				_push_mock_health(player, mock, event.get('health', mock.health),
					mock.maxHealth, not bool(event.get('dead', False)),
					_local_entity_id_for_server(player, attacker_server_id), False)
		elif kind == 'bot_human_hit':
			target_id = event.get('target')
			is_local = target_id == getattr(player, '_offhangar_network_id', None)
			target_mock = _local_mock(player) if is_local else _find_mock(player, target_id)
			attacker_mock = _find_bot(event.get('attacker_bot'))
			if target_mock is not None:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					present = getattr(offline, 'play_network_hit_feedback', None) if offline is not None else None
					if callable(present):
						present(player, attacker_mock, target_mock, _world_from_server(player, event),
							event.get('shot_result', 2), event.get('damage', 0), 0,
							is_local, False, bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN bot-human hit presentation failed')
				try:
					record_stats = getattr(offline, 'record_network_combat_stats', None) if offline is not None else None
					if callable(record_stats):
						record_stats(player, False, is_local, target_mock,
							event.get('damage', 0), event.get('shot_result', 2),
							bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN bot-human statistics failed')
				_push_mock_health(player, target_mock, event.get('health', target_mock.health),
					target_mock.maxHealth, not bool(event.get('dead', False)),
					getattr(attacker_mock, 'id', -1), is_local)
