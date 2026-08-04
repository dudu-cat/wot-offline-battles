import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_force = """				from AvatarInputHandler.cameras import SniperCamera
				if cam is not None and hasattr(cam, 'target') and not isinstance(cam, SniperCamera):"""

good_force = """				is_sniper = False
				if hasattr(g_offline_aih, 'ctrlModeName'):
					is_sniper = (g_offline_aih.ctrlModeName == 'sniper')
				if cam is not None and hasattr(cam, 'target') and not is_sniper:"""

if bad_force in content:
    content = content.replace(bad_force, good_force)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Fixed _force_camera_to_model again!")
else:
    print("_force_camera_to_model not found!")
