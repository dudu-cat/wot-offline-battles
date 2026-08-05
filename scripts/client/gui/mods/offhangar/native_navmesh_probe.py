# -*- coding: utf-8 -*-
"""One-shot runtime check for the Lakeville native-navmesh experiment."""

import BigWorld
import Math

from gui.mods.offhangar.logging import LOG_ERROR, LOG_NOTE


PROBE_MAP = '07_lakeville'
PROBE_GIRTH = 0.5
PROBE_DISTANCE = 8.0


def _system_message(message, level='information'):
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


def _short_map_name(map_name):
	try:
		return str(map_name or '').replace('\\', '/').rstrip('/').split('/')[-1]
	except Exception:
		return ''


def _point_text(point):
	try:
		return '(%.1f, %.1f, %.1f)' % (
			float(point.x), float(point.y), float(point.z))
	except Exception:
		return str(point)


def maybe_run(player, map_name, position, battle_generation):
	"""Run once after the live player has been placed on streamed terrain."""
	if player is None or _short_map_name(map_name) != PROBE_MAP:
		return False
	try:
		if getattr(getattr(player, 'arena', None), 'period', 0) != 3:
			return False
		if not getattr(player, '_offh_spawn_fixed', False):
			return False
		marker = int(battle_generation or 0)
		if getattr(player, '_offh_native_navmesh_probe_generation', None) == marker:
			return False
		player._offh_native_navmesh_probe_generation = marker
	except Exception:
		return False

	try:
		navigate = getattr(BigWorld, 'navigatePathPoints', None)
		if not callable(navigate):
			raise RuntimeError('BigWorld.navigatePathPoints is unavailable')
		source = Math.Vector3(float(position.x), float(position.y), float(position.z))
		# Stay in the same synthetic chunk polygon. The player formation is well
		# inside its X bounds, but choose the direction defensively for later tests.
		delta_x = PROBE_DISTANCE if float(source.x) < -150.0 else -PROBE_DISTANCE
		destination = Math.Vector3(
			float(source.x) + delta_x, float(source.y), float(source.z))
		path = navigate(source, destination, 100.0, PROBE_GIRTH)
		if path is None or len(path) == 0:
			raise RuntimeError('native navigator returned an empty path')
		LOG_NOTE(
			'NAVMESH_PROBE PASS map=%s points=%d src=%s dst=%s first=%s last=%s' % (
				PROBE_MAP, len(path), _point_text(source), _point_text(destination),
				_point_text(path[0]), _point_text(path[-1])))
		_system_message(
			'Native navmesh loaded: %d path point(s).' % len(path), 'information')
		return True
	except Exception as error:
		LOG_ERROR(
			'NAVMESH_PROBE FAIL map=%s pos=%s error=%s' % (
				PROBE_MAP, _point_text(position), str(error)))
		_system_message('Native navmesh probe failed: %s' % str(error), 'error')
		return False
