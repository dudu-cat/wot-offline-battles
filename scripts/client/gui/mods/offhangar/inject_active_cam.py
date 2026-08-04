import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_hook = """					# Update the static matrix snapshot so it's fresh for this frame!
					cm = getattr(self, '_SniperCamera__chassisMat', None)
					if cm is not None and hasattr(cm, 'set'):
						cm.set(veh_matrix)
					return _orig_cameraUpdate(self, *a, **kw)"""

good_hook = """					# Update the static matrix snapshot so it's fresh for this frame!
					cm = getattr(self, '_SniperCamera__chassisMat', None)
					if cm is not None and hasattr(cm, 'set'):
						cm.set(veh_matrix)
					res = _orig_cameraUpdate(self, *a, **kw)
					
					import BigWorld
					cam = BigWorld.camera()
					if getattr(cam, 'className', '') == 'CursorCamera':
						# If our sniper camera is the active camera, dump its source matrix
						try:
							mat = cam.source
							if mat is not None:
								global _tick_counter
								if _tick_counter[0] % 50 == 0:
									import Math
									m = Math.Matrix(mat)
									LOG_DEBUG('SniperCamera ACTIVE: camMat translation=', m.translation, 'yaw=', m.yaw, 'pitch=', m.pitch)
							else:
								global _tick_counter
								if _tick_counter[0] % 50 == 0:
									LOG_DEBUG('SniperCamera ACTIVE: cam.source is None!')
						except:
							pass
					
					return res"""

if bad_hook in content:
    content = content.replace(bad_hook, good_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Injected active camera logger!")
else:
    print("Could not find cameraUpdate hook!")
