import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_hook = """				# Also enforce our veh_matrix just in case
				cm = getattr(cam_self, '_SniperCamera__chassisMat', None)
				if cm is not None and hasattr(cm, 'b'): cm.b = veh_matrix
				tm = getattr(cam_self, '_SniperCamera__turretJointMat', None)
				if tm is not None and hasattr(tm, 'b'): tm.b = veh_matrix
				return res"""

good_hook = """				# Also enforce our veh_matrix just in case
				tm = getattr(cam_self, '_SniperCamera__turretJointMat', None)
				if tm is not None and hasattr(tm, 'b'): tm.b = veh_matrix
				
				# Math.MatrixProduct does not have .inverse, which crashes __cameraUpdate!
				# We must provide an object that dynamically returns the inverted snapshot matrix.
				class ChassisMatWrapper(object):
					@property
					def inverse(self):
						m = Math.Matrix(veh_matrix)
						m.invert()
						return m
				cam_self._SniperCamera__chassisMat = ChassisMatWrapper()
				
				return res"""

if bad_hook in content:
    content = content.replace(bad_hook, good_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Fixed chassisMat crash!")
else:
    print("Hook not found!")
