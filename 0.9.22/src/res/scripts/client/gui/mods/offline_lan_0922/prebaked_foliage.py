# -*- coding: utf-8 -*-
"""Load versioned #1513 foliage volumes shipped beside config.json."""

import hashlib
import json
import os

from gui.mods.offline_lan_0922.foliage import FoliageMap
from gui.mods.offline_lan_0922.navigation_graph_schema import (
	GAME_VERSION, SUPPORTED_MAPS, short_map_name,
)
from gui.mods.offline_lan_0922.prebaked_navigation import mod_dir


FORMAT_NAME = 'offline-lan-0922-foliage'
FORMAT_VERSION = 1
MANIFEST_FORMAT = FORMAT_NAME + '-manifest'


def _sha256(path):
	digest = hashlib.sha256()
	handle = open(path, 'rb')
	try:
		while True:
			block = handle.read(1024 * 1024)
			if not block:
				break
			digest.update(block)
	finally:
		handle.close()
	return digest.hexdigest()


def _manifest_entry(directory, map_name):
	path = os.path.join(directory, 'manifest.json')
	if not os.path.isfile(path):
		return None
	handle = open(path, 'r')
	try:
		manifest = json.load(handle)
	finally:
		handle.close()
	if (not isinstance(manifest, dict) or
			manifest.get('format') != MANIFEST_FORMAT or
			int(manifest.get('version', -1)) != FORMAT_VERSION or
			str(manifest.get('game_version', '')) != GAME_VERSION):
		raise ValueError('foliage manifest is incompatible')
	records = manifest.get('maps') or ()
	if len(records) != len(SUPPORTED_MAPS):
		raise ValueError('foliage manifest is incomplete')
	expected = set(SUPPORTED_MAPS)
	seen = set()
	selected = None
	for record in records:
		if not isinstance(record, dict):
			raise ValueError('foliage manifest record is invalid')
		name = short_map_name(record.get('map'))
		filename = str(record.get('file') or '')
		digest = str(record.get('sha256') or '')
		if (name not in expected or name in seen or
				filename != name + '.json' or len(digest) != 64):
			raise ValueError('foliage manifest record is invalid')
		seen.add(name)
		if not os.path.isfile(os.path.join(directory, filename)):
			raise ValueError('foliage batch is incomplete')
		if name == map_name:
			selected = record
	if seen != expected:
		raise ValueError('foliage manifest is incomplete')
	return selected


def _validate(data, map_name):
	if not isinstance(data, dict):
		raise ValueError('foliage root is not an object')
	if data.get('format') != FORMAT_NAME:
		raise ValueError('unsupported foliage format')
	if int(data.get('version', -1)) != FORMAT_VERSION:
		raise ValueError('unsupported foliage version')
	if str(data.get('game_version', '')) != GAME_VERSION:
		raise ValueError('foliage belongs to a different client version')
	if short_map_name(data.get('map')) != map_name:
		raise ValueError('foliage map does not match the battle')
	if float(data.get('cell_size', 0.0)) <= 0.0:
		raise ValueError('foliage cell size is invalid')
	instances = data.get('instances') or ()
	for instance in instances:
		if not isinstance(instance, (list, tuple)) or len(instance) != 10:
			raise ValueError('foliage instance is invalid')
	for ids in (data.get('cells') or {}).values():
		for instance_id in ids:
			if int(instance_id) < 0 or int(instance_id) >= len(instances):
				raise ValueError(
					'foliage cell references an invalid instance')
	return data


def load_foliage(map_name):
	short_name = short_map_name(map_name)
	if not short_name:
		return None
	directory = os.path.join(mod_dir(), 'foliage')
	entry = _manifest_entry(directory, short_name)
	path = os.path.join(directory, short_name + '.json')
	if not os.path.isfile(path):
		return None
	handle = open(path, 'r')
	try:
		data = json.load(handle)
	finally:
		handle.close()
	return FoliageMap(_validate(data, short_name))
