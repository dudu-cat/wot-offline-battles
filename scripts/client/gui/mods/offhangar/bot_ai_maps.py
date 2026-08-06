# -*- coding: utf-8 -*-
"""Tactical route data for the offline bot director.

Coordinates in this file belong to the pinned 0.8.2 client, not a modern WoT
map. Himmelsdorf was traced against the original ``04_himmelsdorf.pkg``
minimap and its packed arena definition:

* bounds: (-300, -300) -> (400, 400)
* team 1 base: south, approximately (305, -306)
* team 2 base: north, approximately (301, 301)

The routes are intentionally sparse macro waypoints. The existing multi-ray
driver remains responsible for local obstacle avoidance between them.
"""


HIMMELSDORF = {
	'name': '04_himmelsdorf',
	'bounds': (-300.0, -300.0, 400.0, 400.0),
	'bases': {
		1: (305.2, -306.4),
		2: (300.7, 300.9),
	},
	'routes': {
		1: (
			{
				'id': 'banana',
				'capacity': 6,
				'risk': 0.62,
				'role_weights': {
					'brawler': 1.00, 'support': 0.58, 'flanker': 0.30,
					'sniper': 0.10, 'scout': 0.18, 'artillery': 0.00,
				},
				'waypoints': (
					(300.0, -260.0, 0), (235.0, -220.0, 0),
					(205.0, -150.0, 0), (185.0, -82.0, 1),
					(178.0, 18.0, 1), (195.0, 112.0, 1),
					(225.0, 218.0, 1), (278.0, 274.0, 0),
				),
			},
			{
				'id': 'hill',
				'capacity': 4,
				'risk': 0.78,
				'role_weights': {
					'brawler': 0.35, 'support': 0.58, 'flanker': 1.00,
					'sniper': 0.18, 'scout': 0.72, 'artillery': 0.00,
				},
				'waypoints': (
					(320.0, -260.0, 0), (346.0, -205.0, 0),
					(365.0, -125.0, 0), (368.0, -42.0, 1),
					(354.0, 52.0, 1), (335.0, 133.0, 1),
					(320.0, 205.0, 1), (307.0, 265.0, 0),
				),
			},
			{
				'id': 'rail',
				'capacity': 4,
				'risk': 0.42,
				'role_weights': {
					'brawler': 0.18, 'support': 0.62, 'flanker': 0.58,
					'sniper': 1.00, 'scout': 0.88, 'artillery': 0.12,
				},
				'waypoints': (
					(275.0, -264.0, 0), (170.0, -254.0, 0),
					(42.0, -254.0, 0), (-92.0, -248.0, 0),
					(-205.0, -220.0, 0), (-238.0, -142.0, 1),
					(-248.0, -42.0, 1), (-250.0, 68.0, 1),
					(-242.0, 184.0, 1), (-215.0, 276.0, 0),
				),
			},
			{
				'id': 'rear_guard',
				'capacity': 2,
				'risk': 0.08,
				'role_weights': {
					'brawler': 0.00, 'support': 0.18, 'flanker': 0.00,
					'sniper': 0.28, 'scout': 0.00, 'artillery': 1.00,
				},
				'waypoints': ((278.0, -270.0, 1),),
			},
		),
		2: (),
	},
}


def _reverse_route(route):
	"""Build the north-to-south route without sharing mutable containers."""
	result = dict(route)
	result['role_weights'] = dict(route.get('role_weights', {}))
	result['waypoints'] = tuple(reversed(route.get('waypoints', ())))
	return result


# All three fighting corridors are bidirectional on this version of the map.
# The rear guard is anchored separately because each base needs its own cover.
_north_routes = []
for _route in HIMMELSDORF['routes'][1]:
	if _route['id'] == 'rear_guard':
		_north = _reverse_route(_route)
		_north['waypoints'] = ((280.0, 278.0, 1),)
	else:
		_north = _reverse_route(_route)
	_north_routes.append(_north)
HIMMELSDORF['routes'][2] = tuple(_north_routes)
del _north_routes
del _route
del _north


TACTICAL_MAPS = {
	'04_himmelsdorf': HIMMELSDORF,
}

# The stock 0.8.2 map set is split into data-only modules so this registry
# stays reviewable. In the legacy arena data, "ctf" names the normal
# base-capture battle type; assault and encounter variants are not annotated.
from gui.mods.offhangar import bot_ai_maps_group_a
from gui.mods.offhangar import bot_ai_maps_group_b
from gui.mods.offhangar import bot_ai_maps_group_c
from gui.mods.offhangar import bot_ai_maps_extra

TACTICAL_MAPS.update(bot_ai_maps_group_a.TACTICAL_MAPS_GROUP_A)
TACTICAL_MAPS.update(bot_ai_maps_group_b.TACTICAL_MAPS_GROUP_B)
TACTICAL_MAPS.update(bot_ai_maps_group_c.TACTICAL_MAPS_GROUP_C)
TACTICAL_MAPS.update(bot_ai_maps_extra.TACTICAL_MAPS_EXTRA)


def normalize_map_name(map_name):
	name = str(map_name or '').replace('\\', '/').split('/')[-1]
	if name.endswith('.xml'):
		name = name[:-4]
	return name.lower()


def get_tactical_map(map_name):
	return TACTICAL_MAPS.get(normalize_map_name(map_name))
