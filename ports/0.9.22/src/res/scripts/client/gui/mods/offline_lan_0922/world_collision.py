# -*- coding: utf-8 -*-
"""Dedented 0.8.2 horizontal world-collision law."""

from gui.mods.offline_lan_0922.destructibles_sensor import (
	_try_destroy_destructible, _try_destroy_solid_hit, _vehicle_hull_bbox)


def check_horizontal_collision(bigworld, math_module, *args):
	"""Supply the engine modules formerly captured by the 0.8.2 closure."""
	import sys
	missing = object()
	old_bigworld = sys.modules.get('BigWorld', missing)
	old_math = sys.modules.get('Math', missing)
	sys.modules['BigWorld'] = bigworld
	sys.modules['Math'] = math_module
	try:
		return _check_horizontal_collision(*args)
	finally:
		if old_bigworld is missing:
			sys.modules.pop('BigWorld', None)
		else:
			sys.modules['BigWorld'] = old_bigworld
		if old_math is missing:
			sys.modules.pop('Math', None)
		else:
			sys.modules['Math'] = old_math


def _check_horizontal_collision(spaceID, pos, yaw, vel, td=None, airborne=False, dt=0.04):
	import math, BigWorld, Math
	try:
		hw = 1.5
		hl_front = 3.5
		hl_back = 3.5

		bbox = _vehicle_hull_bbox(td)
		if bbox is not None:
			try:
				hw = max(abs(bbox[0][0]), abs(bbox[1][0])) - 0.1
				hl_back = abs(bbox[0][2])
				hl_front = abs(bbox[1][2])
			except (AttributeError, KeyError, TypeError, IndexError):
				raise RuntimeError('#1513 hull hit tester bbox is invalid')

		# Look-ahead beyond the hull. The old flat +2.0 m made an invisible
		# wall 2 m before every obstacle, and DURING A FALL it saw the cliff
		# face below-ahead and zeroed the speed mid-air - the tank then hugged
		# the wall and trickled down instead of flying a ballistic arc.
		# Grounded: just enough to not tunnel at speed. Airborne: only the
		# distance actually travelled this tick - contact stops, proximity not.
		if airborne:
			_ahead = abs(vel) * dt + 0.2
		else:
			_ahead = min(1.2, max(0.4, abs(vel) * dt * 2.0))
		back_margin = -0.5 if vel > 0 else 0.5
		front_margin = (hl_front + _ahead) if vel > 0 else -(hl_back + _ahead)
		
		cos_y = math.cos(yaw)
		sin_y = math.sin(yaw)

		# DRIVABLE-SLOPE GUARD: a rising HILL is not a wall. Sample the ground under
		# the hull and at the hull front along the heading; only a real, gradual rise
		# may bypass the wall rays. The old check returned early for every smooth
		# profile, including level city streets, so ordinary walls were never tested.
		try:
			_fw = 1.0 if vel >= 0 else -1.0
			_look = (hl_front if vel > 0 else hl_back) + _ahead
			# Walk the ground along the heading in small steps. A drivable HILL rises
			# gradually at EVERY step -> climb it (no collision). A big rock / step /
			# wall SPIKES one step (rises more than a climbable amount over its short
			# length) -> leave it to the ray wall-check so the hull is BLOCKED.
			_seg_n = 6
			_seg = _look / _seg_n
			_smooth = True
			_first_y = None
			_prev_y = None
			for _si in range(_seg_n + 1):
				_dd = _seg * _si
				_px = pos.x + sin_y * _dd * _fw
				_pz = pos.z + cos_y * _dd * _fw
				_gg = BigWorld.wg_collideSegment(spaceID, Math.Vector3(_px, pos.y + 12.0, _pz), Math.Vector3(_px, pos.y - 5.0, _pz), 128)
				if _gg is None:
					_smooth = False
					break
				if _first_y is None:
					_first_y = _gg[0].y
				if _prev_y is not None and (_gg[0].y - _prev_y) > _seg * 1.28:
					_smooth = False   # step rises steeper than ~52 deg = rock/step, not a hill
					break
				_prev_y = _gg[0].y
			if (_smooth and _first_y is not None and _prev_y is not None and
					_prev_y - _first_y > 0.15):
				return False
		except Exception:
			raise

		for offset_x in (-hw, 0, hw):
			sx = pos.x + cos_y * offset_x
			sz = pos.z - sin_y * offset_x
			
			x1 = sx + sin_y * back_margin
			z1 = sz + cos_y * back_margin
			x2 = sx + sin_y * front_margin
			z2 = sz + cos_y * front_margin
			
			# Independent centre-lane scan for trees and fences.  A native
			# destructible failure is a hard movement failure, never permission to
			# pass through an object that is still visually intact.
			if offset_x == 0:
				seg_start = Math.Vector3(sx, pos.y + 0.5, sz)
				seg_stop = Math.Vector3(x2, pos.y + 0.5, z2)
				matInfo = BigWorld.wg_getMatInfoNearPoint(spaceID, seg_start, seg_stop, seg_stop, lambda *a: False)
				_try_destroy_destructible(
					spaceID, matInfo, yaw, vel)
			
			# Spodní paprsek pro pevnou geometrii (0.6m nad zemí)
			start_bot = Math.Vector3(x1, pos.y + 0.6, z1)
			end_bot = Math.Vector3(x2, pos.y + 0.6, z2)
			col_bot = BigWorld.wg_collideSegment(spaceID, start_bot, end_bot, 128)
			
			if col_bot is not None:
				d_bot = (col_bot[0] - start_bot).length
				target_len = abs(back_margin) + (hl_front if vel > 0 else hl_back) + _ahead
				if d_bot < target_len:
					# Něco jsme trefili, zkontrolujeme horní paprsek (1.6m nad zemí)
					start_top = Math.Vector3(x1, pos.y + 1.6, z1)
					end_top = Math.Vector3(x2, pos.y + 1.6, z2)
					col_top = BigWorld.wg_collideSegment(spaceID, start_top, end_top, 128)
					
					if col_top is not None:
						d_top = (col_top[0] - start_top).length
						if (d_top - d_bot) < 0.5:
							if _try_destroy_solid_hit(spaceID, col_bot[0], col_bot[1], yaw, vel): pass
							else: return True
					else:
						start_mid = Math.Vector3(x1, pos.y + 1.1, z1)
						end_mid = Math.Vector3(x2, pos.y + 1.1, z2)
						col_mid = BigWorld.wg_collideSegment(spaceID, start_mid, end_mid, 128)
						if col_mid is not None:
							d_mid = (col_mid[0] - start_mid).length
							if (d_mid - d_bot) < 0.25:
								if _try_destroy_solid_hit(spaceID, col_bot[0], col_bot[1], yaw, vel): pass
								else: return True
						else:
							# Low object (<1.1m): only the bottom ray caught it. Crush it if
							# it's a destructible (fence / small prop) so the tank drives
							# THROUGH, not over it. Non-destructibles (low rocks) stay drivable.
							_try_destroy_solid_hit(
								spaceID, col_bot[0], col_bot[1], yaw, vel)
	except Exception:
		raise
	return False
