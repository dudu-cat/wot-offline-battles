import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_chassis = """				# Math.MatrixProduct does not have .inverse, which crashes __cameraUpdate!
				# We must provide an object that dynamically returns the inverted snapshot matrix.
				class ChassisMatWrapper(object):
					@property
					def inverse(self):
						m = Math.Matrix(veh_matrix)
						m.invert()
						return m
				cam_self._SniperCamera__chassisMat = ChassisMatWrapper()"""

good_chassis = """				# Replace __chassisMat with a static Math.Matrix so it has .inverse and doesn't crash __rotate.
				# We will update this matrix manually every frame in our __cameraUpdate hook.
				cam_self._SniperCamera__chassisMat = Math.Matrix(veh_matrix)"""

bad_hook2 = """		# Patch __cameraUpdate to catch and log any crashes!
		_orig_cameraUpdate = getattr(SniperCamera, '_SniperCamera__cameraUpdate', None)
		if _orig_cameraUpdate is not None and not getattr(_orig_cameraUpdate, '__offhangar_patched', False):
			def _patched_cameraUpdate(self, *a, **kw):
				try:
					return _orig_cameraUpdate(self, *a, **kw)
				except Exception as e:
					import traceback
					LOG_DEBUG('SniperCamera.__cameraUpdate CRASHED!', traceback.format_exc())
			_patched_cameraUpdate.__offhangar_patched = True
			setattr(SniperCamera, '_SniperCamera__cameraUpdate', _patched_cameraUpdate)"""

good_hook2 = """		# Patch __cameraUpdate to keep our static __chassisMat updated!
		_orig_cameraUpdate = getattr(SniperCamera, '_SniperCamera__cameraUpdate', None)
		if _orig_cameraUpdate is not None and not getattr(_orig_cameraUpdate, '__offhangar_patched', False):
			def _patched_cameraUpdate(self, *a, **kw):
				try:
					# Update the static matrix snapshot so it's fresh for this frame!
					cm = getattr(self, '_SniperCamera__chassisMat', None)
					if cm is not None and hasattr(cm, 'set'):
						cm.set(veh_matrix)
					return _orig_cameraUpdate(self, *a, **kw)
				except Exception as e:
					import traceback
					LOG_DEBUG('SniperCamera.__cameraUpdate CRASHED!', traceback.format_exc())
			_patched_cameraUpdate.__offhangar_patched = True
			setattr(SniperCamera, '_SniperCamera__cameraUpdate', _patched_cameraUpdate)"""


if bad_chassis in content and bad_hook2 in content:
    content = content.replace(bad_chassis, good_chassis)
    content = content.replace(bad_hook2, good_hook2)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Fixed chassisMat perfectly!")
else:
    print("Could not find targets to replace!")
