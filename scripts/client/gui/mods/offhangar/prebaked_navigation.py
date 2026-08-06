# -*- coding: utf-8 -*-
"""Load versioned navigation graphs shipped with the offline-battle mod."""

import json
import os

from gui.mods.offhangar.paths import mod_dir


FORMAT_NAME = 'offhangar-navgraph'
FORMAT_VERSION = 1
GAME_VERSION = '0.8.2'


def _short_map_name(map_name):
	return str(map_name or '').replace('\\', '/').rstrip('/').split('/')[-1]


def _validate(graph, map_name):
	if not isinstance(graph, dict):
		raise ValueError('navigation graph root is not an object')
	if graph.get('format') != FORMAT_NAME:
		raise ValueError('unsupported navigation graph format')
	if int(graph.get('version', -1)) != FORMAT_VERSION:
		raise ValueError('unsupported navigation graph version')
	if str(graph.get('game_version', '')) != GAME_VERSION:
		raise ValueError('navigation graph belongs to a different client version')
	if _short_map_name(graph.get('map')) != map_name:
		raise ValueError('navigation graph map does not match the battle')
	width = int(graph.get('width', 0))
	height = int(graph.get('height', 0))
	if width <= 0 or height <= 0:
		raise ValueError('navigation graph dimensions are invalid')
	if len(graph.get('heights_mm') or ()) != width * height:
		raise ValueError('navigation graph height array is incomplete')
	if len(graph.get('links') or ()) != width * height:
		raise ValueError('navigation graph link array is incomplete')
	if len(graph.get('origin') or ()) != 2:
		raise ValueError('navigation graph origin is invalid')
	if float(graph.get('cell_size', 0.0)) <= 0.0:
		raise ValueError('navigation graph cell size is invalid')
	return graph


def load_graph(map_name):
	"""Return a validated graph, or None when this map has not been baked."""
	short_name = _short_map_name(map_name)
	if not short_name:
		return None
	path = os.path.join(mod_dir(), 'navgraphs', short_name + '.json')
	if not os.path.isfile(path):
		return None
	handle = open(path, 'r')
	try:
		graph = json.load(handle)
	finally:
		handle.close()
	return _validate(graph, short_name)
