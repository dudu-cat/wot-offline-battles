import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_hook = """		player.handleKey = lambda key, isDown, mods=0: None
		player.getAutorotation = lambda: False"""

good_hook = """		player.handleKey = lambda key, isDown, mods=0: None
		player.getAutorotation = lambda: False
		
		# INJECT onControlModeChanged logger
		import AvatarInputHandler
		_orig_aih_onControlModeChanged = getattr(AvatarInputHandler.AvatarInputHandler, 'onControlModeChanged', None)
		if _orig_aih_onControlModeChanged is not None and not getattr(_orig_aih_onControlModeChanged, '__offhangar_patched', False):
			def _patched_aih_onControlModeChanged(self, eMode, **args):
				LOG_DEBUG('AIH onControlModeChanged CALLED:', eMode, args)
				try:
					return _orig_aih_onControlModeChanged(self, eMode, **args)
				except Exception as e:
					import traceback
					LOG_DEBUG('AIH onControlModeChanged CRASHED:', traceback.format_exc())
			_patched_aih_onControlModeChanged.__offhangar_patched = True
			AvatarInputHandler.AvatarInputHandler.onControlModeChanged = _patched_aih_onControlModeChanged
"""

if bad_hook in content:
    content = content.replace(bad_hook, good_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Injected AIH logger!")
else:
    print("Could not find AIH hook!")
