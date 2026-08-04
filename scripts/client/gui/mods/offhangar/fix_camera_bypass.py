import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_hook = """		_orig_sniper_enable = getattr(SniperCamera, 'enable', None)
		if _orig_sniper_enable is not None and not getattr(_orig_sniper_enable, '__offhangar_patched', False):
			def _patched_sniper_enable(cam_self, *a, **kw):
				# Force __setupCamera to recreate the matrices from scratch every time
				# This handles cases where the user restarted from backup and cached a PyModelNode
				setattr(cam_self, '_SniperCamera__turretJointMat', None)
				setattr(cam_self, '_SniperCamera__chassisMat', None)
				
				res = _orig_sniper_enable(cam_self, *a, **kw)
				
				# Also enforce our veh_matrix just in case
				tm = getattr(cam_self, '_SniperCamera__turretJointMat', None)
				if tm is not None and hasattr(tm, 'b'): tm.b = veh_matrix
				
				# Replace __chassisMat with a static Math.Matrix so it has .inverse and doesn't crash __rotate.
				# We will update this matrix manually every frame in our __cameraUpdate hook.
				cam_self._SniperCamera__chassisMat = Math.Matrix(veh_matrix)
				
				return res
			_patched_sniper_enable.__offhangar_patched = True
			SniperCamera.enable = _patched_sniper_enable"""

good_hook = """		_orig_sniper_enable = getattr(SniperCamera, 'enable', None)
		if _orig_sniper_enable is not None and not getattr(_orig_sniper_enable, '__offhangar_patched', False):
			def _patched_sniper_enable(cam_self, *a, **kw):
				# Force __setupCamera to recreate the matrices from scratch every time
				# This handles cases where the user restarted from backup and cached a PyModelNode
				setattr(cam_self, '_SniperCamera__turretJointMat', None)
				setattr(cam_self, '_SniperCamera__chassisMat', None)
				
				res = _orig_sniper_enable(cam_self, *a, **kw)
				
				# BigWorld.entity(id) returns None in offline battle because mock_veh is not in C++ memory!
				# __setupCamera skips BigWorld.camera(self.__cam) if it thinks the vehicle doesn't exist!
				# We MUST manually force the camera switch here!
				try:
					import BigWorld
					BigWorld.camera(cam_self._SniperCamera__cam)
				except:
					pass
				
				# Also enforce our veh_matrix just in case
				tm = getattr(cam_self, '_SniperCamera__turretJointMat', None)
				if tm is not None and hasattr(tm, 'b'): tm.b = veh_matrix
				
				# Replace __chassisMat with a static Math.Matrix so it has .inverse and doesn't crash __rotate.
				# We will update this matrix manually every frame in our __cameraUpdate hook.
				cam_self._SniperCamera__chassisMat = Math.Matrix(veh_matrix)
				
				return res
			_patched_sniper_enable.__offhangar_patched = True
			SniperCamera.enable = _patched_sniper_enable"""

if bad_hook in content:
    content = content.replace(bad_hook, good_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Fixed BigWorld.camera bypass!")
else:
    print("Could not find sniper enable hook!")
