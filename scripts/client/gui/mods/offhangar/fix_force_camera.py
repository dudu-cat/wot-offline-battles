import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_func = """		def _force_camera_to_model():
			try:
				import BigWorld, Math
				cam = BigWorld.camera()
				is_sniper = False
				if hasattr(g_offline_aih, 'ctrlModeName'):
					is_sniper = (g_offline_aih.ctrlModeName == 'sniper')
				if cam is not None and hasattr(cam, 'target') and not is_sniper:
					# Set cam.target to a translation-only provider tracking veh_matrix.
					# This prevents the camera from turning when the tank hull turns.
					mp = Math.WGTranslationOnlyMP()
					mp.source = veh_matrix
					cam.target = mp
					LOG_DEBUG('OfflineBattle.force_camera: set target to', veh_pos[0], veh_pos[1], veh_pos[2])
				else:
					LOG_DEBUG('OfflineBattle.force_camera: cam=', cam, 'has target=', hasattr(cam, 'target') if cam else False)
			except Exception as e:
				import traceback
				LOG_DEBUG('OfflineBattle.force_camera error:', traceback.format_exc())"""

good_func = """		def _force_camera_to_model(mode_name=None):
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
				LOG_DEBUG('OfflineBattle.force_camera error:', traceback.format_exc())"""

bad_call1 = """										g_offline_aih.onControlModeChanged(e_mode.value)
										_force_camera_to_model()"""

good_call1 = """										g_offline_aih.onControlModeChanged(e_mode.value)
										_force_camera_to_model(e_mode.value)"""

if bad_func in content and bad_call1 in content:
    content = content.replace(bad_func, good_func)
    content = content.replace(bad_call1, good_call1)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Fixed force_camera!")
else:
    print("Could not find blocks to replace!")
