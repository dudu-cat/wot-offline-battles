# -*- coding: utf-8 -*-
"""Deterministic World of Tanks 0.8.2 spotting calculations.

This module deliberately has no BigWorld imports.  The battle adapter supplies
vehicle/resource data while the formula remains testable on modern Python and
loadable by the embedded Python 2.6 runtime.
"""


PROXIMITY_SPOT_DISTANCE = 50.0
MAX_SPOT_DISTANCE = 500.0
SPOT_MEMORY_SECONDS = 5.0
SIXTH_SENSE_DELAY_SECONDS = 3.0
STILL_DEVICE_DELAY_SECONDS = 3.0
MOVING_SPEED_EPSILON = 0.5
SHOT_CAMOUFLAGE_SECONDS = 5.0

# The release client omits the server-owned base invisibility coefficients.
# These conservative nominal values preserve the characteristic class ordering
# until a value is available from an unstripped descriptor/resource.
CLASS_CAMOUFLAGE = {
	'lightTank': (0.18, 0.18),
	'mediumTank': (0.11, 0.14),
	'heavyTank': (0.045, 0.075),
	'AT-SPG': (0.12, 0.20),
	'SPG': (0.08, 0.13),
}


def clamp(value, minimum, maximum):
	return max(float(minimum), min(float(maximum), float(value)))


def qualification_factor(role_level):
	"""Normalize the old 0.5 + 0.00375 * level curve to 100% crew."""
	return (0.5 + 0.00375 * clamp(role_level, 0.0, 150.0)) / 0.875


def effective_view_range(base_range, commander_level=100.0,
		vision_factor=1.0, recon_level=0.0, situational_level=0.0,
		still_device_factor=1.0, still_device_active=False):
	"""Return uncapped view range; excess range still counters camouflage."""
	result = max(PROXIMITY_SPOT_DISTANCE, float(base_range or 0.0))
	result *= qualification_factor(commander_level)
	result *= max(0.0, float(vision_factor or 0.0))
	result *= 1.0 + 0.0002 * clamp(recon_level, 0.0, 100.0)
	result *= 1.0 + 0.0003 * clamp(situational_level, 0.0, 100.0)
	if still_device_active:
		result *= max(1.0, float(still_device_factor or 1.0))
	return max(PROXIMITY_SPOT_DISTANCE, result)


def crew_camouflage_factor(skill_level):
	"""Old-style multiplicative contribution of the common Camouflage skill."""
	return 1.0 + 0.00375 * clamp(skill_level, 0.0, 100.0)


def effective_camouflage(moving_base, still_base, moving=False,
		crew_skill_level=0.0, turret_factor=1.0, paint_factor=1.0,
		camouflage_net_factor=1.0, camouflage_net_active=False,
		shot_factor=1.0, fired_recently=False, foliage_bonus=0.0):
	"""Return the target's concealment coefficient in the 0..1 range.

	0.8.2 camouflage paint and the camouflage net were multiplicative.  Foliage
	is additive and capped separately so stacked bushes cannot make a vehicle
	undetectable outside the unconditional 50 metre proximity circle.
	"""
	base = float(moving_base if moving else still_base)
	result = max(0.0, base)
	result *= crew_camouflage_factor(crew_skill_level)
	result *= max(0.0, float(turret_factor or 0.0))
	result *= max(0.0, float(paint_factor or 0.0))
	if camouflage_net_active and not moving:
		result *= max(1.0, float(camouflage_net_factor or 1.0))
	if fired_recently:
		result *= clamp(shot_factor, 0.0, 1.0)
	result += clamp(foliage_bonus, 0.0, 0.60)
	return clamp(result, 0.0, 0.95)


def detection_distance(view_range, camouflage):
	"""Apply the historical 50 m floor and 500 m 0.8.2 spotting ceiling."""
	view_range = max(PROXIMITY_SPOT_DISTANCE, float(view_range or 0.0))
	camouflage = clamp(camouflage, 0.0, 0.95)
	distance = view_range - (view_range - PROXIMITY_SPOT_DISTANCE) * camouflage
	return clamp(distance, PROXIMITY_SPOT_DISTANCE, MAX_SPOT_DISTANCE)


def is_detected(distance, view_range, camouflage, has_line_of_sight=True):
	distance = max(0.0, float(distance or 0.0))
	if distance <= PROXIMITY_SPOT_DISTANCE:
		return True
	return bool(has_line_of_sight and
	            distance <= detection_distance(view_range, camouflage))


def class_camouflage(tags):
	for class_tag in ('lightTank', 'mediumTank', 'heavyTank', 'AT-SPG', 'SPG'):
		if class_tag in (tags or ()):
			return CLASS_CAMOUFLAGE[class_tag]
	return CLASS_CAMOUFLAGE['mediumTank']
