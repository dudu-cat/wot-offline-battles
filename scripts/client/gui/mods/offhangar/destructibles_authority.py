# -*- coding: utf-8 -*-
"""Authoritative offline record of destroyed map objects.

Mirrors what the real BigWorld server kept in the AreaDestructibles
entity's ALL_CLIENTS properties (fallenTrees, fallenColumns,
destroyedFragiles, destroyedModules). Sensors (collision probes, the
proximity registry, later shot impacts) REPORT contacts here; this
module decides, encodes, records and pushes the update to the
per-chunk client entity like a server property push, so the game's
own set_* callbacks drive the visuals.

State lives per space: a new battle (new spaceID) resets everything,
which also makes cross-battle chunk-ID collisions harmless.
"""

import math

import BigWorld
import Math

_state = {
	'spaceID': None,
	'chunks': {},
	'entities': set(),
	'collisionHealth': {},
}

try:
	_INTEGER_TYPES = (int, long)
except NameError:
	_INTEGER_TYPES = (int,)

_PROP_BY_KIND = {
	'tree': 'fallenTrees',
	'column': 'fallenColumns',
	'fragile': 'destroyedFragiles',
	'module': 'destroyedModules',
}

_PREV_BY_PROP = {
	'fallenTrees': '_AreaDestructibles__prevFallenTrees',
	'fallenColumns': '_AreaDestructibles__prevFallenColumns',
	'destroyedFragiles': '_AreaDestructibles__prevDestroyedFragiles',
	'destroyedModules': '_AreaDestructibles__prevDestroyedModules',
}


def _log(*args):
	try:
		from gui.mods.offhangar.logging import LOG_DEBUG
		LOG_DEBUG(*args)
	except Exception:
		pass


def reset(spaceID=None):
	"""Return _state to its initial shape. Callers (the battle sweep)
	MUST use this instead of _state.clear(): clear() removes the
	'spaceID'/'chunks'/'entities' keys, and every later access then
	raises KeyError, killing destructibles for the whole battle."""
	_state['spaceID'] = spaceID
	_state['chunks'] = {}
	_state['entities'] = set()
	_state['collisionHealth'] = {}


def _ensure_shape():
	# Defensive: if anything wiped _state (e.g. a stray clear()), rebuild
	# the required keys so no access below can KeyError.
	if 'spaceID' not in _state:
		_state['spaceID'] = None
	if 'chunks' not in _state:
		_state['chunks'] = {}
	if 'entities' not in _state:
		_state['entities'] = set()
	if 'collisionHealth' not in _state:
		_state['collisionHealth'] = {}


def _reset_if_new_space(spaceID):
	_ensure_shape()
	if _state['spaceID'] != spaceID:
		reset(spaceID)


def _chunk(chunkID):
	_ensure_shape()
	c = _state['chunks'].get(chunkID)
	if c is None:
		c = {'fallenTrees': [], 'fallenColumns': [], 'destroyedFragiles': [], 'destroyedModules': [], 'keys': set(), 'faults': set()}
		_state['chunks'][chunkID] = c
	elif 'faults' not in c:
		c['faults'] = set()
	return c


def is_destroyed(chunkID, itemIndex, matKind=None):
	c = _state.get('chunks', {}).get(chunkID)
	if c is None:
		return False
	return (itemIndex, matKind) in c['keys'] or (itemIndex, None) in c['keys']


def can_crush(owner, spaceID, chunkID, itemIndex, matKind, itemFilename,
		vehicleSpeed):
	"""Apply the retail mass/speed/health test to one resolved contact."""
	try:
		import AreaDestructibles
		import DestructiblesCache
		if is_destroyed(chunkID, itemIndex, matKind):
			return True
		desc = AreaDestructibles.g_cache.getDescByFilename(itemFilename)
		if desc is None:
			return False
		scale = _destructible_scale(spaceID, chunkID, itemIndex)
		if scale is None:
			return False
		mass = float(owner.typeDescriptor.physics['weight'])
		speed = float(vehicleSpeed)
		if mass <= 0.0:
			return False
		instantDamage = 0.5 * mass * speed * speed * 0.00015
		if desc['type'] == AreaDestructibles.DESTR_TYPE_STRUCTURE:
			module = desc.get('modules', {}).get(matKind)
			if module is None:
				return False
			referenceHealth = module['health']
		else:
			unitMass = float(AreaDestructibles.g_cache.unitVehicleMass)
			if unitMass <= 0.0:
				return False
			instantDamage *= math.pow(
				mass / unitMass, desc['kineticDamageCorrection'])
			referenceHealth = desc['health']
		return (DestructiblesCache.scaledDestructibleHealth(
			scale, referenceHealth) < instantDamage)
	except Exception:
		return False


def _callback_int(value, name):
	if isinstance(value, bool) or not isinstance(value, _INTEGER_TYPES):
		raise ValueError('%s must be an integer' % name)
	return int(value)


def _finite_float(value, name):
	try:
		value = float(value)
	except Exception:
		raise ValueError('%s must be finite' % name)
	if math.isnan(value) or math.isinf(value):
		raise ValueError('%s must be finite' % name)
	return value


def _validate_point(pos):
	try:
		values = (pos.x, pos.y, pos.z)
	except Exception:
		try:
			values = (pos[0], pos[1], pos[2])
		except Exception:
			raise ValueError('hitPoint must contain three finite values')
	for value in values:
		_finite_float(value, 'hitPoint')


def _destructible_scale(spaceID, chunkID, itemIndex):
	matrix_value = BigWorld.wg_getDestructibleMatrix(
		spaceID, chunkID, itemIndex)
	if matrix_value is None:
		return None
	matrix = Math.Matrix(matrix_value)
	axis = matrix.applyVector(Math.Vector3(0.0, 1.0, 0.0))
	scale = _finite_float(axis.length, 'destructible scale')
	if scale <= 0.0:
		raise ValueError('destructible scale must be positive')
	return scale


def _collision_record(spaceID, chunkID, itemIndex):
	key = (chunkID, itemIndex)
	record = _state['collisionHealth'].get(key)
	if record is not None:
		return record

	import AreaDestructibles
	import DestructiblesCache
	desc = AreaDestructibles.g_cache.getDestructibleDesc(
		spaceID, chunkID, itemIndex)
	if desc is None:
		return None
	scale = _destructible_scale(spaceID, chunkID, itemIndex)
	if scale is None:
		return None

	destructible_type = int(desc['type'])
	values = {}
	dependencies = {}
	if destructible_type == AreaDestructibles.DESTR_TYPE_STRUCTURE:
		import constants
		normal_min = int(constants.DESTRUCTIBLE_MATKIND.NORMAL_MIN)
		normal_max = int(constants.DESTRUCTIBLE_MATKIND.NORMAL_MAX)
		modules = desc.get('modules')
		if not isinstance(modules, dict) or not modules:
			raise ValueError('structure has no destructible modules')
		for raw_kind, module in modules.items():
			mat_kind = _callback_int(raw_kind, 'module matKind')
			if mat_kind < normal_min or mat_kind >= normal_max:
				raise ValueError('structure matKind is outside the normal range')
			if mat_kind in values:
				raise ValueError('structure contains duplicate matKind')
			values[mat_kind] = int(
				DestructiblesCache.scaledDestructibleHealth(
					scale, int(module['health'])))
		for raw_root, raw_depends in desc.get(
				'destroyDepends', {}).items():
			root = _callback_int(raw_root, 'destroyDepends matKind')
			if root not in values:
				continue
			depends = set()
			for raw_kind in raw_depends:
				mat_kind = _callback_int(
					raw_kind, 'destroyDepends dependent matKind')
				if mat_kind in values and mat_kind != root:
					depends.add(mat_kind)
			if depends:
				dependencies[root] = tuple(sorted(depends))
	else:
		allowed_types = (
			AreaDestructibles.DESTR_TYPE_TREE,
			AreaDestructibles.DESTR_TYPE_FALLING_ATOM,
			AreaDestructibles.DESTR_TYPE_FRAGILE,
		)
		if destructible_type not in allowed_types:
			raise ValueError('unsupported destructible type')
		values[None] = int(
			DestructiblesCache.scaledDestructibleHealth(
				scale, int(desc['health'])))

	record = {
		'type': destructible_type,
		'values': values,
		'dependencies': dependencies,
	}
	_state['collisionHealth'][key] = record
	return record


def collision_health(spaceID, chunkID, itemIndex):
	"""Return the exact WGVehiclePhysics2 health callback payload.

	Non-structure objects use one scalar health value. Structures use all of
	their normal material kinds; omitted dictionary entries are interpreted as
	zero by the retail executable. ``None`` means the streamed descriptor or
	matrix is not available yet and preserves solid collision in native code.
	"""
	spaceID = _callback_int(spaceID, 'spaceID')
	chunkID = _callback_int(chunkID, 'chunkID')
	itemIndex = _callback_int(itemIndex, 'itemIndex')
	_reset_if_new_space(spaceID)
	record = _collision_record(spaceID, chunkID, itemIndex)
	if record is None:
		return None
	values = record['values']
	if None in values:
		if is_destroyed(chunkID, itemIndex, None):
			return 0
		return int(values[None])
	result = {}
	for mat_kind, health in values.items():
		if is_destroyed(chunkID, itemIndex, mat_kind):
			result[int(mat_kind)] = 0
		else:
			result[int(mat_kind)] = int(health)
	return result


def _mark_collision_destroyed(chunkID, itemIndex, matKinds):
	c = _chunk(chunkID)
	for mat_kind in matKinds:
		c['keys'].add((itemIndex, mat_kind))


def apply_collision_damage(spaceID, chunkID, itemIndex, matKind, damage,
		pos, fallYaw, impactSpeed):
	"""Apply one six-argument native damage event to authoritative health.

	Returns False for partial damage and True for an already or newly destroyed
	object/module. Invalid native data and terminal delivery failures raise so
	the owning body can fail closed after the shared physics batch completes.
	"""
	spaceID = _callback_int(spaceID, 'spaceID')
	chunkID = _callback_int(chunkID, 'chunkID')
	itemIndex = _callback_int(itemIndex, 'itemIndex')
	matKind = _callback_int(matKind, 'matKind')
	damage = _callback_int(damage, 'damage')
	if damage <= 0:
		raise ValueError('damage must be positive')
	_validate_point(pos)
	fallYaw = _finite_float(fallYaw, 'fallYaw')
	impactSpeed = _finite_float(impactSpeed, 'impactSpeed')

	_reset_if_new_space(spaceID)
	record = _collision_record(spaceID, chunkID, itemIndex)
	if record is None:
		raise RuntimeError('destructible descriptor is unavailable')

	import AreaDestructibles
	destructible_type = record['type']
	value_key = None
	if destructible_type == AreaDestructibles.DESTR_TYPE_STRUCTURE:
		value_key = matKind
		if value_key not in record['values']:
			raise ValueError('structure matKind is unavailable')
	if is_destroyed(chunkID, itemIndex, value_key):
		record['values'][value_key] = 0
		return True

	current = int(record['values'][value_key])
	if current <= 0:
		raise RuntimeError('damage reported for non-positive health')
	remaining = max(0, current - damage)
	if remaining > 0:
		record['values'][value_key] = remaining
		return False

	if destructible_type == AreaDestructibles.DESTR_TYPE_TREE:
		applied = destroy_tree(
			spaceID, chunkID, itemIndex, fallYaw, impactSpeed, pos)
	elif destructible_type == AreaDestructibles.DESTR_TYPE_FALLING_ATOM:
		applied = destroy_column(
			spaceID, chunkID, itemIndex, fallYaw, impactSpeed, pos)
	elif destructible_type == AreaDestructibles.DESTR_TYPE_FRAGILE:
		applied = destroy_fragile(spaceID, chunkID, itemIndex, pos)
	elif destructible_type == AreaDestructibles.DESTR_TYPE_STRUCTURE:
		applied = destroy_module(
			spaceID, chunkID, itemIndex, matKind, pos, False)
	else:
		raise RuntimeError('unsupported destructible type')
	if not applied:
		raise RuntimeError('terminal destructible update was not delivered')

	record['values'][value_key] = 0
	destroyed_kinds = [value_key]
	if value_key is not None:
		for dependent in record['dependencies'].get(value_key, ()):
			record['values'][dependent] = 0
			destroyed_kinds.append(dependent)
	_mark_collision_destroyed(chunkID, itemIndex, destroyed_kinds)
	return True


def _ensure_chunk(spaceID, chunkID, pos):
	"""Start the manager space, spawn the chunk controller entity once
	(the real server did this) and register the chunk. Returns the
	controller, or None while the entity is still spawning."""
	import AreaDestructibles
	mgr = AreaDestructibles.g_destructiblesManager
	if mgr is None:
		return None
	# Keep the manager's space in sync with the CURRENT battle. Starting only
	# when None left a STALE spaceID after battle 1: mgr kept the previous,
	# now-RELEASED space, so the engine's __launchFallEffect later called
	# getDestructibleDesc(self.__spaceID, ...) -> wg_getDestructibleFilename
	# with a dead/None spaceID = "argument 1 must be set to an int", aborting
	# the tree/column fall (seen on a later-battle Fjords). startSpace() does
	# clear() first, so re-starting on a space change is safe and fires once.
	_cur_sid = int(spaceID) if spaceID is not None else None
	try:
		_mgr_sid = mgr.getSpaceID()
	except Exception:
		_mgr_sid = None
	if _cur_sid is not None and _mgr_sid != _cur_sid:
		mgr.startSpace(_cur_sid)
	if chunkID not in _state['entities']:
		c = _chunk(chunkID)
		try:
			entityID = BigWorld.createEntity('AreaDestructibles', spaceID, 0, Math.Vector3(pos[0], pos[1], pos[2]), (0.0, 0.0, 0.0), {
				'fallenTrees': list(c['fallenTrees']),
				'fallenColumns': list(c['fallenColumns']),
				'destroyedFragiles': list(c['destroyedFragiles']),
				'destroyedModules': list(c['destroyedModules']),
			})
			if entityID is None or int(entityID) <= 0:
				raise RuntimeError('AreaDestructibles entity was not created')
			_state['entities'].add(chunkID)
		except Exception:
			_log('DestrAuth: createEntity failed for chunk', chunkID)
	try:
		if not mgr.isChunkLoaded(chunkID):
			fnames = BigWorld.wg_getChunkDestrFilenames(spaceID, chunkID)
			if fnames is not None:
				mgr.onChunkLoad(chunkID, len(fnames))
	except Exception:
		_log('DestrAuth: onChunkLoad failed for chunk', chunkID)
	return mgr.getController(chunkID)


def _apply(spaceID, chunkID, pos, kind, destrData, dedupKey):
	import AreaDestructibles
	_reset_if_new_space(spaceID)
	c = _chunk(chunkID)
	if dedupKey in c['keys']:
		return False
	if dedupKey in c['faults']:
		return False
	ctrl = _ensure_chunk(spaceID, chunkID, pos)
	prop = _PROP_BY_KIND[kind]
	# Use exactly one irreversible native delivery boundary. Calling a controller
	# setter here would call this manager again internally, and an exception after
	# that nested call cannot be rolled back safely.
	dmgTypes = {
		'tree': AreaDestructibles._DAMAGE_TYPE_TREE,
		'column': AreaDestructibles._DAMAGE_TYPE_COLUMN,
		'fragile': AreaDestructibles._DAMAGE_TYPE_FRAGILE,
		'module': AreaDestructibles._DAMAGE_TYPE_MODULE,
	}
	try:
		AreaDestructibles.g_destructiblesManager.orderDestructibleDestroy(
			chunkID, dmgTypes[kind], destrData, True)
	except Exception:
		# The native method is irreversible and offers no receipt on exception.
		# Latch this contact as a terminal fault instead of risking a duplicate
		# delivery on the next physics frame.
		c['faults'].add(dedupKey)
		_log('DestrAuth: terminal direct order failure', chunkID, kind)
		return False
	# Commit replay and dedup state only after native delivery accepted it.
	c[prop].append(destrData)
	c['keys'].add(dedupKey)
	if ctrl is not None:
		# Mirror the accepted authoritative state without firing set_* and therefore
		# without issuing a second native order.
		try:
			values = getattr(ctrl, prop)
			if destrData not in values:
				values.append(destrData)
			setattr(ctrl, _PREV_BY_PROP[prop], frozenset(values))
		except Exception:
			_log('DestrAuth: controller ledger sync failed', chunkID, kind)
	return True


def destroy_tree(spaceID, chunkID, itemIndex, fallYaw, speed, pos):
	import AreaDestructibles
	# Native getDestructibleDesc (called by the game's __launchFallEffect)
	# demands a plain int destrID; a float/long index reaching it raised
	# 'argument 1 must be set to an int'. Coerce here.
	chunkID = int(chunkID); itemIndex = int(itemIndex)
	pitch = math.pi / 2.0
	try:
		pc = BigWorld.wg_getDestructibleFallPitchConstr(spaceID, chunkID, itemIndex, fallYaw)
		if pc is not None and pc[0] is not None:
			pitch = pc[0]
	except Exception:
		pass
	speed = max(1, min(3, int(abs(speed))))
	data = AreaDestructibles.encodeFallenTree(itemIndex, fallYaw, pitch, speed)
	return _apply(spaceID, chunkID, pos, 'tree', data, (itemIndex, None))


def destroy_column(spaceID, chunkID, itemIndex, fallYaw, speed, pos):
	import AreaDestructibles
	chunkID = int(chunkID); itemIndex = int(itemIndex)
	speed = max(1, min(3, int(abs(speed))))
	data = AreaDestructibles.encodeFallenColumn(itemIndex, fallYaw, speed)
	return _apply(spaceID, chunkID, pos, 'column', data, (itemIndex, None))


def destroy_fragile(spaceID, chunkID, itemIndex, pos):
	# Fragiles take the RAW item index: __destroyFragile does no decode.
	chunkID = int(chunkID); itemIndex = int(itemIndex)
	return _apply(spaceID, chunkID, pos, 'fragile', itemIndex, (itemIndex, None))


def destroy_module(spaceID, chunkID, itemIndex, matKind, pos, isShotDamage=False):
	import AreaDestructibles
	chunkID = int(chunkID); itemIndex = int(itemIndex)
	if matKind is not None:
		matKind = int(matKind)
	data = AreaDestructibles.encodeDestructibleModule(itemIndex, matKind, isShotDamage)
	return _apply(spaceID, chunkID, pos, 'module', data, (itemIndex, matKind))
