import sys
import re

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

pattern_func = re.compile(r'def _force_camera_to_model\(\):.*?LOG_DEBUG\(\'OfflineBattle\.force_camera ERROR:\', traceback\.format_exc\(\)\)', re.DOTALL)

good_func = """def _force_camera_to_model(mode_name=None):
			try:
				import BigWorld, Math
				cam = BigWorld.camera()
				is_sniper = (mode_name == 'sniper')
				if cam is not None and hasattr(cam, 'target') and not is_sniper:
					# Set cam.target to a translation-only provider tracking veh_matrix.
					# This prevents the camera from turning when the tank hull turns.
					mp = Math.WGTranslationOnlyMP()
					mp.source = veh_matrix
					cam.target = mp
					LOG_DEBUG('OfflineBattle.force_camera: set target to', veh_pos[0], veh_pos[1], veh_pos[2])
				else:
					if is_sniper and cam is not None and hasattr(cam, 'target'):
						cam.target = None
					LOG_DEBUG('OfflineBattle.force_camera: cam=', cam, 'has target=', hasattr(cam, 'target') if cam else False, 'is_sniper=', is_sniper)
			except Exception as e:
				import traceback
				LOG_DEBUG('OfflineBattle.force_camera ERROR:', traceback.format_exc())"""

content = pattern_func.sub(good_func, content)

pattern_call = re.compile(r'g_offline_aih\.onControlModeChanged\(e_mode\.value\)\s+_force_camera_to_model\(\)', re.DOTALL)
good_call = """g_offline_aih.onControlModeChanged(e_mode.value)
										_force_camera_to_model(e_mode.value)"""

content = pattern_call.sub(good_call, content)

with open(file_path, 'w') as f:
    f.write(content)
print("Regex replace successful!")
