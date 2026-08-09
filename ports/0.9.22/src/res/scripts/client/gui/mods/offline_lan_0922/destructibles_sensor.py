# -*- coding: utf-8 -*-
"""0.8.2 destructible contact sensors on the #1513 engine boundary.

The three sensor bodies below are dedented copies from ``offline_battle.py``.
Only their former closure dependencies are supplied at module scope.
"""

_event_sink = None

def LOG_DEBUG(*unused_args):
	# The user requested no trace-heavy battle logging.
	pass


def _get_destr_authority():
	from gui.mods.offline_lan_0922 import destructibles_authority
	return destructibles_authority


def set_event_sink(callback):
	global _event_sink
	if callback is not None and not callable(callback):
		raise TypeError('destructible event sink must be callable')
	_event_sink = callback


def _position_payload(pos):
	try:
		return float(pos.x), float(pos.y), float(pos.z)
	except AttributeError:
		return float(pos[0]), float(pos[1]), float(pos[2])


def _publish_destroyed(kind, chunkID, itemIndex, pos, fallYaw=0.0,
		speed=0.0, matKind=None, isShotDamage=False):
	if _event_sink is None:
		return True
	x, y, z = _position_payload(pos)
	event = {
		'destructible_kind': str(kind),
		'chunk_id': int(chunkID),
		'item_index': int(itemIndex),
		'x': x, 'y': y, 'z': z,
		'fall_yaw': float(fallYaw),
		'speed': float(speed),
		'is_shot': bool(isShotDamage),
	}
	if matKind is not None:
		event['mat_kind'] = int(matKind)
	if not _event_sink(event):
		raise RuntimeError('destructible event was not admitted by LAN client')
	return True


def reset(spaceID=None):
	for name in ('g_offh_destr_seen', 'g_offh_destr_nodesc',
			'g_offh_tree_state', 'g_offh_destr_ordered',
			'g_offh_destr_chunks'):
		globals().pop(name, None)
	_get_destr_authority().reset(spaceID)


def _try_destroy_destructible(spaceID, matInfo, yaw, vel,
		isShotDamage=False):
	import AreaDestructibles, BigWorld, constants
	try:
		if not hasattr(AreaDestructibles, 'g_destructiblesManager') or not AreaDestructibles.g_destructiblesManager:
			return False
			
		hitPt, surfNormal, chunkID, itemIndex, matKind, fname = matInfo
		_dseen = globals().setdefault('g_offh_destr_seen', set())
		_dkey = (matKind, fname)
		if _dkey not in _dseen:
			_dseen.add(_dkey); LOG_DEBUG('Destr hit: matKind=', matKind, 'fname=', repr(fname), 'chunk=', chunkID, 'idx=', itemIndex)
		# Widened band: the strict 71-100 range rejected spawn barriers/props at
		# matKind 102. getDescByFilename below is the real filter, so a wider band
		# only lets more candidates reach the authoritative desc check.
		if matKind < 71 or matKind > 130:
			return False
		desc = AreaDestructibles.g_cache.getDescByFilename(fname)
		if not desc:
			_dnd = globals().setdefault('g_offh_destr_nodesc', set())
			if _dkey not in _dnd:
				_dnd.add(_dkey); LOG_DEBUG('Destr no desc: matKind=', matKind, 'fname=', repr(fname), 'chunk=', chunkID, 'idx=', itemIndex)
			return False
		
		# Data-driven vegetation gate: soft vegetation (bush/shrub/fern)
		# ships with health <= 5; real fallable trees start at 10.
		if desc['type'] in (AreaDestructibles.DESTR_TYPE_TREE, AreaDestructibles.DESTR_TYPE_FALLING_ATOM):
			_hp_gate = desc.get('health', 0)
			if _hp_gate < 10 or _hp_gate > 1000:
				return False
		# All bookkeeping (chunk bootstrap, dedup, encoding) lives in
		# the authority - this path is now just a contact sensor.
		_auth = _get_destr_authority()
		
		typ = desc['type']
		# STRUCTURE (buildings) now falls through to the module-destroy
		# path: online, small buildings crumble module by module as the
		# tank pushes through. Requires the working effects pipeline
		# (terrainEffects + real fake_model), else it raises mid-destroy.
		if _auth.is_destroyed(chunkID, itemIndex, matKind):
			LOG_DEBUG('Destr: already broken')
			return True
			
		if typ == AreaDestructibles.DESTR_TYPE_TREE:
			_destr_ok = _auth.destroy_tree(spaceID, chunkID, itemIndex, yaw, vel, hitPt)
		elif typ == AreaDestructibles.DESTR_TYPE_FALLING_ATOM:
			_destr_ok = _auth.destroy_column(spaceID, chunkID, itemIndex, yaw, vel, hitPt)
		elif typ == AreaDestructibles.DESTR_TYPE_FRAGILE:
			_destr_ok = _auth.destroy_fragile(spaceID, chunkID, itemIndex, hitPt)
		else:
			# STRUCTURE: buildings crumble module by module
			_destr_ok = _auth.destroy_module(
				spaceID, chunkID, itemIndex, matKind, hitPt, isShotDamage)
			
		if _destr_ok:
			_publish_destroyed(
				('tree' if typ == AreaDestructibles.DESTR_TYPE_TREE else
				 'column' if typ == AreaDestructibles.DESTR_TYPE_FALLING_ATOM else
				 'fragile' if typ == AreaDestructibles.DESTR_TYPE_FRAGILE else
				 'module'),
				chunkID, itemIndex, hitPt, yaw, vel,
				matKind if typ == AreaDestructibles.DESTR_TYPE_STRUCTURE else None,
				isShotDamage)
			LOG_DEBUG('Destr SUCCESS!', typ)
		return True
	except Exception as e:
		LOG_DEBUG('Destr Exception:', str(e))
	return False


def _fell_trees_near(spaceID, pos, yaw, vel, td=None):
	# Offline tree/pole felling. Online the SERVER detected tank-vs-tree
	# contact; the client-side collision probes never return tree/column
	# materials, so trees could never fall offline. Instead: enumerate
	# each chunk's destructibles once (filename + world matrix), then
	# fell TREE / FALLING_ATOM items that intersect the moving hull.
	import math
	import AreaDestructibles
	import BigWorld
	import Math
	try:
		if abs(vel) < 1.0:
			return
		mgr = getattr(AreaDestructibles, 'g_destructiblesManager', None)
		if not mgr:
			return
		if mgr.getSpaceID() is None:
			mgr.startSpace(spaceID)
		_st = globals().setdefault('g_offh_tree_state', {'chunks': {}, 'felled': set(), 'spaceID': None})
		if _st.get('spaceID') != spaceID:
			# New battle/space: chunk IDs collide between maps and the
			# dedup sets would suppress destruction of fresh objects.
			_st['chunks'] = {}
			_st['felled'] = set()
			_st['spaceID'] = spaceID
			globals()['g_offh_destr_ordered'] = set()
			globals()['g_offh_destr_chunks'] = set()
			globals()['g_offh_destr_seen'] = set()
		cos_y = math.cos(yaw); sin_y = math.sin(yaw)
		cids = set()
		for _pf in (0.0, 6.0 if vel >= 0 else -6.0):
			try:
				cids.add(AreaDestructibles.chunkIDFromPosition(Math.Vector3(pos.x + sin_y * _pf, pos.y, pos.z + cos_y * _pf)))
			except Exception:
				pass
		hw = 1.6; hl_f = 3.6; hl_b = 3.6
		try:
			if td is not None and hasattr(td, 'hull') and 'hitTester' in td.hull:
				bbox = td.hull['hitTester'].bbox
				hw = max(abs(bbox[0][0]), abs(bbox[1][0]))
				hl_b = abs(bbox[0][2])
				hl_f = abs(bbox[1][2])
		except Exception:
			pass
		for cid in cids:
			trees = _st['chunks'].get(cid)
			if trees is None:
				_dfn = None
				try:
					_dfn = BigWorld.wg_getChunkDestrFilenames(spaceID, cid)
				except Exception:
					pass
				if _dfn is None:
					continue # chunk not streamed in yet; retry next tick
				trees = []
				_cm_t = None
				try:
					_cm_t = BigWorld.wg_getChunkMatrix(spaceID, cid).translation
				except Exception:
					pass
				if _cm_t is None:
					continue
				for _ti in xrange(len(_dfn)):
					try:
						desc = AreaDestructibles.g_cache.getDescByFilename(_dfn[_ti])
						if desc is None:
							continue
						if desc['type'] not in (AreaDestructibles.DESTR_TYPE_TREE, AreaDestructibles.DESTR_TYPE_FALLING_ATOM, AreaDestructibles.DESTR_TYPE_FRAGILE):
							continue
						# Data-driven vegetation gate: destructibles.xml gives
						# soft vegetation (bushes/shrubs/ferns/weeds) health<=5
						# (or -2); real fallable trees start at health 10.
						# ChristmasTree sentinels use 40000 = unrammable.
						if desc['type'] != AreaDestructibles.DESTR_TYPE_FRAGILE:
							_hp_gate = desc.get('health', 0)
							if _hp_gate < 10 or _hp_gate > 1000:
								continue
						# Destructible matrices are CHUNK-LOCAL: world pos =
						# chunk translation + destructible translation
						# (see AreaDestructibles.__launchEffect)
						_m = Math.Matrix(BigWorld.wg_getDestructibleMatrix(spaceID, cid, _ti))
						trees.append((_ti, _cm_t.x + _m.translation.x, _cm_t.z + _m.translation.z, desc['type'], _dfn[_ti], desc.get('health', 0), desc.get('mass', 0)))
					except Exception:
						continue
				_st['chunks'][cid] = trees
				LOG_DEBUG('DestrTree: chunk registry', cid, len(trees), 'trees/poles')
				if trees:
					LOG_DEBUG('DestrTree: sample world pos', trees[0][1], trees[0][2], 'tank at', pos.x, pos.z)
			if not trees:
				continue
			reach_f = hl_f + 0.8 + min(abs(vel) * 0.25, 1.2)
			for (_ti, _tx, _tz, _ttyp, _tfn, _thp, _tmass) in trees:
				dx = _tx - pos.x; dz = _tz - pos.z
				if dx * dx + dz * dz > 64.0:
					continue
				fwd = dx * sin_y + dz * cos_y
				lat = dx * cos_y - dz * sin_y
				if vel < 0:
					in_reach = -(hl_b + 0.8) <= fwd <= hl_f
				else:
					in_reach = -hl_b <= fwd <= reach_f
				if abs(lat) > hw + 0.5 or not in_reach:
					continue
				_key = (cid, _ti)
				if _key in _st['felled']:
					continue
				_st['felled'].add(_key)
				fall_yaw = yaw if vel >= 0 else (yaw + math.pi)
				_auth = _get_destr_authority()
				if _ttyp == AreaDestructibles.DESTR_TYPE_FRAGILE:
					# Haybales, barrels, wire fences: collision skins often
					# resolve to no item in the probes; crush by proximity.
					_ok = _auth.destroy_fragile(spaceID, cid, _ti, pos)
				elif _ttyp == AreaDestructibles.DESTR_TYPE_TREE:
					_ok = _auth.destroy_tree(spaceID, cid, _ti, fall_yaw, vel, pos)
				else:
					_ok = _auth.destroy_column(spaceID, cid, _ti, fall_yaw, vel, pos)
				if _ok:
					_publish_destroyed(
						('fragile' if _ttyp == AreaDestructibles.DESTR_TYPE_FRAGILE
						 else 'tree' if _ttyp == AreaDestructibles.DESTR_TYPE_TREE
						 else 'column'),
						cid, _ti, pos, fall_yaw, vel)
					LOG_DEBUG('DestrTree: FELLED', cid, _ti, 'type', _ttyp, 'hp', _thp, 'mass', _tmass, _tfn)
	except Exception:
		import traceback
		LOG_DEBUG('DestrTree error:', traceback.format_exc())


def _try_destroy_solid_hit(spaceID, seg_start, hit_pt, yaw, vel):
	# wg_collideSegment returns no material info: probe the hit point for a
	# destructible (fence/wall segment) before treating it as solid
	import BigWorld
	try:
		# Probe along the SURFACE NORMAL like Vehicle.onStaticCollision: the
		# forward probe grazed the solid collision skin (matKind 101/109, empty
		# fname); crossing the surface perpendicular resolves the destructible
		# mesh's real chunk/index/fname. dir points into the surface; normal = -dir,
		# so segStart = point - normal*3 = point + dir*3, segStop = point - dir*2.
		_dirv = hit_pt - seg_start
		if _dirv.length > 0.001:
			_dirv.normalise()
		else:
			return False
		_seg_a = hit_pt + _dirv.scale(3.0)
		_seg_b = hit_pt - _dirv.scale(2.0)
		_mi = BigWorld.wg_getMatInfoNearPoint(spaceID, _seg_a, _seg_b, hit_pt, lambda *a: False)
		if _mi is not None:
			return _try_destroy_destructible(spaceID, _mi, yaw, vel)
	except Exception:
		pass
	return False


def shot_world_distance(bigworld, spaceID, start_pos, end_pos, dir_vec):
	"""Extract the 0.8.2 shell-break-and-recast boundary."""
	import math
	world_dist = 99999.0
	world_collision = bigworld.wg_collideSegment(
		spaceID, start_pos, end_pos, 128)
	if world_collision is None:
		return world_dist
	world_dist = (world_collision[0] - start_pos).length
	shot_yaw = math.atan2(dir_vec.x, dir_vec.z)
	mat_info = None
	try:
		mat_info = bigworld.wg_getMatInfoNearPoint(
			spaceID, start_pos,
			world_collision[0] + dir_vec.scale(0.3),
			world_collision[0], lambda *unused: False)
	except Exception:
		mat_info = None
	if (mat_info is not None and
			_try_destroy_destructible(
				spaceID, mat_info, shot_yaw, 12.0, True)):
		# Destructible broken by the shell: re-cast past the debris.
		second = bigworld.wg_collideSegment(
			spaceID, world_collision[0] + dir_vec.scale(0.6),
			end_pos, 128)
		world_dist = ((second[0] - start_pos).length + 0.6
			if second is not None else 99999.0)
	return world_dist
