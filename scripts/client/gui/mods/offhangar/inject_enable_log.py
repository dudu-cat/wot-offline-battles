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
				
				res = _orig_sniper_enable(cam_self, *a, **kw)"""

good_hook = """		_orig_sniper_enable = getattr(SniperCamera, 'enable', None)
		if _orig_sniper_enable is not None and not getattr(_orig_sniper_enable, '__offhangar_patched', False):
			def _patched_sniper_enable(cam_self, *a, **kw):
				LOG_DEBUG('SniperCamera.enable is RUNNING!!!')
				# Force __setupCamera to recreate the matrices from scratch every time
				# This handles cases where the user restarted from backup and cached a PyModelNode
				setattr(cam_self, '_SniperCamera__turretJointMat', None)
				setattr(cam_self, '_SniperCamera__chassisMat', None)
				
				res = _orig_sniper_enable(cam_self, *a, **kw)"""

if bad_hook in content:
    content = content.replace(bad_hook, good_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Injected sniper enable logger!")
else:
    print("Could not find sniper enable hook!")
