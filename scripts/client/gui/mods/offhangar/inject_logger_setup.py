import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

hook_str = """		# Patch __cameraUpdate to catch and log any crashes!
		_orig_cameraUpdate = getattr(SniperCamera, '_SniperCamera__cameraUpdate', None)"""

patched_hook = """		# Patch __setupCamera to catch and log any crashes!
		_orig_setupCamera = getattr(SniperCamera, '_SniperCamera__setupCamera', None)
		if _orig_setupCamera is not None and not getattr(_orig_setupCamera, '__offhangar_patched', False):
			def _patched_setupCamera(self, *a, **kw):
				try:
					return _orig_setupCamera(self, *a, **kw)
				except Exception as e:
					import traceback
					LOG_DEBUG('SniperCamera.__setupCamera CRASHED!', traceback.format_exc())
			_patched_setupCamera.__offhangar_patched = True
			setattr(SniperCamera, '_SniperCamera__setupCamera', _patched_setupCamera)
			
		# Patch __cameraUpdate to catch and log any crashes!
		_orig_cameraUpdate = getattr(SniperCamera, '_SniperCamera__cameraUpdate', None)"""

if hook_str in content:
    content = content.replace(hook_str, patched_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Injected __setupCamera crash logger!")
else:
    print("Could not find hook to inject logger!")
