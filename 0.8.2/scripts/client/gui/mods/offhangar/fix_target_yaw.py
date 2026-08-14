import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_snippet = """					target_yaw = veh_yaw[0]
					cam = BigWorld.camera()
					if hasattr(cam, '_SniperCamera__angles'):
						# SniperCamera angles are relative to the hull, so add veh_yaw for world yaw
						target_yaw = cam._SniperCamera__angles[0] + veh_yaw[0]
					elif hasattr(cam, '_ArcadeCamera__yaw'):
						target_yaw = cam._ArcadeCamera__yaw
					elif hasattr(cam, 'yaw'):
						target_yaw = cam.yaw
					elif hasattr(g_offline_aih, 'aim') and hasattr(g_offline_aih.aim, 'offset'):
						target_yaw = _getDesiredShotPoint(g_offline_aih.aim.offset())[1]		
"""

content = content.replace(bad_snippet, '')

good_snippet = """					target_yaw = veh_yaw[0]
					if hasattr(cam, '_SniperCamera__angles'):
						# SniperCamera angles are relative to the hull, so add veh_yaw for world yaw
						target_yaw = cam._SniperCamera__angles[0] + veh_yaw[0]
					elif hasattr(cam, '_ArcadeCamera__yaw'):
						target_yaw = cam._ArcadeCamera__yaw
					elif hasattr(cam, 'yaw'):
						target_yaw = cam.yaw
					elif hasattr(g_offline_aih, 'aim') and hasattr(g_offline_aih.aim, 'offset'):
						target_yaw = _getDesiredShotPoint(g_offline_aih.aim.offset())[1]
"""

old_target_yaw_block = """					target_yaw = veh_yaw[0]
					if hasattr(cam, '_SniperCamera__angles'):
						target_yaw = cam._SniperCamera__angles[0]
					elif hasattr(cam, '_ArcadeCamera__yaw'):
						target_yaw = cam._ArcadeCamera__yaw
					elif hasattr(cam, 'yaw'):
						target_yaw = cam.yaw
					elif hasattr(g_offline_aih, 'aim') and hasattr(g_offline_aih.aim, 'offset'):
						target_yaw = _getDesiredShotPoint(g_offline_aih.aim.offset())[1]"""

content = content.replace(old_target_yaw_block, good_snippet)

with open(file_path, 'w') as f:
    f.write(content)

print("Fixed target_yaw calculation!")
