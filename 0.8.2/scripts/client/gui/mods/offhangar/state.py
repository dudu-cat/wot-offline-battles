import cPickle
import os

from debug_utils import LOG_CURRENT_EXCEPTION
from items import ITEM_TYPE_INDICES

from gui.mods.offhangar.logging import LOG_DEBUG
from gui.mods.offhangar import paths as _paths


# Lives outside res_mods so mod updates never touch the garage save.
STATE_FILE = _paths.USER_STATE_FILE
_paths.ensure_user_dir()
STATE_VERSION = 1

VEHICLE_FIELDS = (
	'compDescr',
	'eqs',
	'eqsLayout',
	'shells',
	'shellsLayout',
	'settings'
)


def _default_state():
	return {'version': STATE_VERSION, 'vehicles': {}, 'items': {}}


def load_state():
	if not os.path.exists(STATE_FILE):
		return _default_state()
	try:
		f = open(STATE_FILE, 'rb')
		try:
			state = cPickle.load(f)
		finally:
			f.close()
		if not isinstance(state, dict) or state.get('version') != STATE_VERSION:
			return _default_state()
		state.setdefault('vehicles', {})
		state.setdefault('items', {})
		return state
	except Exception:
		LOG_CURRENT_EXCEPTION()
	return _default_state()


def save_state(state):
	try:
		tmpPath = STATE_FILE + '.tmp'
		f = open(tmpPath, 'wb')
		try:
			cPickle.dump(state, f, cPickle.HIGHEST_PROTOCOL)
		finally:
			f.close()
		if os.path.exists(STATE_FILE):
			os.remove(STATE_FILE)
		os.rename(tmpPath, STATE_FILE)
		return True
	except Exception:
		LOG_CURRENT_EXCEPTION()
	return False


def apply_state_to_inventory(inventory):
	state = load_state()
	vehData = inventory.get(ITEM_TYPE_INDICES['vehicle'], {})
	for vehInvID, saved in state.get('vehicles', {}).iteritems():
		for fieldName in VEHICLE_FIELDS:
			if fieldName in saved and fieldName in vehData:
				vehData[fieldName][vehInvID] = saved[fieldName]
	for itemTypeIdx, savedItems in state.get('items', {}).iteritems():
		bucket = inventory.setdefault(itemTypeIdx, {})
		if isinstance(bucket, dict):
			bucket.update(savedItems)
	LOG_DEBUG('State.apply', len(state.get('vehicles', {})), 'vehicles')
	return inventory


def save_vehicle_state(inventory, vehInvID):
	state = load_state()
	vehData = inventory.get(ITEM_TYPE_INDICES['vehicle'], {})
	saved = {}
	for fieldName in VEHICLE_FIELDS:
		field = vehData.get(fieldName, {})
		if isinstance(field, dict) and vehInvID in field:
			saved[fieldName] = field[vehInvID]
	state.setdefault('vehicles', {})[vehInvID] = saved
	ok = save_state(state)
	if ok:
		LOG_DEBUG('State.saveVehicle', vehInvID)
	return ok


def save_item_state(inventory, itemTypeIdx):
	state = load_state()
	bucket = inventory.get(itemTypeIdx, {})
	if isinstance(bucket, dict):
		state.setdefault('items', {})[itemTypeIdx] = bucket.copy()
		ok = save_state(state)
		if ok:
			LOG_DEBUG('State.saveItems', itemTypeIdx, len(bucket))
		return ok
	return False
