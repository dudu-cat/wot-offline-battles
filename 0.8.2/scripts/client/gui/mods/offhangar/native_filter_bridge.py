# -*- coding: utf-8 -*-
"""Version-locked loader for the optional 0.8.2 native filter bridge."""

import hashlib
import math
import os
import sys

from gui.mods.offhangar.logging import LOG_ERROR, LOG_NOTE


EXPECTED_EXE_SHA256 = (
	'8b3fe162117d2bc40aef2209a0cadbafe5ef4e9479410c12cd6ac6efde6deabd')
MAX_ABS_WORLD_COORDINATE = 12000.0
MAX_FLOAT32 = 3.402823466e38

_LOAD_STATE = [None]


def _world_of_tanks_exe():
	candidates = (
		getattr(sys, 'executable', ''),
		os.path.join(os.getcwd(), 'WorldOfTanks.exe'),
		getattr(sys, 'argv', [''])[0] if getattr(sys, 'argv', None) else '',
	)
	for candidate in candidates:
		try:
			path = os.path.abspath(candidate)
			if (os.path.basename(path).lower() == 'worldoftanks.exe' and
					os.path.isfile(path)):
				return path
		except Exception:
			pass
	return os.path.abspath(os.path.join(os.getcwd(), 'WorldOfTanks.exe'))


def _sha256_file(path):
	digest = hashlib.sha256()
	stream = open(path, 'rb')
	try:
		while True:
			chunk = stream.read(1024 * 1024)
			if not chunk:
				break
			digest.update(chunk)
	finally:
		stream.close()
	return digest.hexdigest()


def load():
	"""Return the bridge module, or ``None`` after one logged failure."""
	if _LOAD_STATE[0] is not None:
		return _LOAD_STATE[0] or None
	_LOAD_STATE[0] = False
	try:
		executable = _world_of_tanks_exe()
		actual_hash = _sha256_file(executable)
		if actual_hash.lower() != EXPECTED_EXE_SHA256:
			raise RuntimeError(
				'WorldOfTanks.exe SHA-256 mismatch: expected=%s actual=%s' % (
					EXPECTED_EXE_SHA256, actual_hash))
		from gui.mods.offhangar import offhangar_native_seed
		for entry_point in ('seed_filter', 'output_filter',
				'filter_has_physics', 'publish_physics_root'):
			if not hasattr(offhangar_native_seed, entry_point):
				raise RuntimeError(
					'native module has no %s entry point' % entry_point)
		_LOAD_STATE[0] = offhangar_native_seed
		LOG_NOTE(
			'NATIVE_FILTER_BRIDGE loaded exe_sha256=%s' % actual_hash)
		return offhangar_native_seed
	except Exception as error:
		LOG_ERROR('NATIVE_FILTER_BRIDGE unavailable: %s' % str(error))
		return None


def seed_filter(vehicle_filter, timestamp, space_id, position, direction):
	"""Inject one world-space filter input for an unmounted vehicle.

	``direction`` follows the native Filter::input history contract exactly:
	yaw, pitch, roll. This differs from some Python Entity helpers which accept
	roll, pitch, yaw and reorder before entering the native filter.

	The third integer in the native ``Filter::input`` contract is the ID of the
	vehicle carrying this entity, not this entity's own ID. Offline tanks are in
	world space, so it must be zero. Passing the entity's own ID makes the engine
	attach the entity to itself and recurse until the main-thread stack is full.
	"""
	try:
		values = (
			float(timestamp),
			float(position[0]), float(position[1]), float(position[2]),
			float(direction[0]), float(direction[1]), float(direction[2]))
		if any(math.isnan(value) or math.isinf(value) for value in values):
			raise ValueError('non-finite Filter::input value')
		if any(abs(value) > MAX_FLOAT32 for value in values[1:]):
			raise ValueError('Filter::input vector exceeds float32 range')
		if any(abs(value) > MAX_ABS_WORLD_COORDINATE for value in values[1:4]):
			raise ValueError('Filter::input position exceeds world bounds')
		space_id = int(space_id)
	except Exception as error:
		LOG_ERROR('NATIVE_FILTER_BRIDGE seed rejected: %s' % str(error))
		return False
	bridge = load()
	if bridge is None:
		return False
	try:
		bridge.seed_filter(
			vehicle_filter, values[0], space_id, 0,
			values[1], values[2], values[3],
			values[4], values[5], values[6])
		return True
	except Exception as error:
		LOG_ERROR('NATIVE_FILTER_BRIDGE seed failed: %s' % str(error))
		return False


def output_filter(vehicle_filter, timestamp):
	"""Publish one attached native body through WGVehicleFilter2::output."""
	try:
		timestamp = float(timestamp)
		if math.isnan(timestamp) or math.isinf(timestamp):
			raise ValueError('non-finite Filter::output timestamp')
	except Exception as error:
		LOG_ERROR('NATIVE_FILTER_BRIDGE output rejected: %s' % str(error))
		return False
	bridge = load()
	if bridge is None:
		return False
	try:
		bridge.output_filter(vehicle_filter, timestamp)
		return True
	except Exception as error:
		LOG_ERROR('NATIVE_FILTER_BRIDGE output failed: %s' % str(error))
		return False


def filter_has_physics(vehicle_filter, vehicle_physics):
	"""Prove the filter owns the exact rigid body installed by this adapter."""
	bridge = load()
	if bridge is None:
		return False
	try:
		bridge.filter_has_physics(vehicle_filter, vehicle_physics)
		return True
	except Exception as error:
		LOG_ERROR('NATIVE_FILTER_BRIDGE owner check failed: %s' % str(error))
		return False


def publish_physics_root(vehicle_filter, vehicle_physics, timestamp,
		space_id):
	"""Publish the solved C++ root matrix through its attached filter.

	Retail ``WGVehicleFilter2.output`` uses its input history as the root pose;
	it does not copy the attached ``WGVehiclePhysics2`` rigid transform. The
	version-locked bridge reads that solved transform, submits it as the current
	filter input in yaw, pitch, roll order and then performs exactly one output
	at the same timestamp.
	"""
	try:
		timestamp = float(timestamp)
		space_id = int(space_id)
		if math.isnan(timestamp) or math.isinf(timestamp):
			raise ValueError('non-finite physics root timestamp')
	except Exception as error:
		LOG_ERROR('NATIVE_FILTER_BRIDGE root publish rejected: %s' % str(error))
		return False
	bridge = load()
	if bridge is None:
		return False
	try:
		bridge.publish_physics_root(
			vehicle_filter, vehicle_physics, timestamp, space_id)
		return True
	except Exception as error:
		LOG_ERROR('NATIVE_FILTER_BRIDGE root publish failed: %s' % str(error))
		return False
