import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_hook = """  				setattr(cam_self, '_SniperCamera__turretJointMat', None)
  				setattr(cam_self, '_SniperCamera__chassisMat', None)
  				
  				res = _orig_sniper_enable(cam_self, *a, **kw)"""

good_hook = """  				setattr(cam_self, '_SniperCamera__turretJointMat', None)
  				setattr(cam_self, '_SniperCamera__chassisMat', None)
  				
  				# AvatarInputHandler occasionally resets _USE_SWINGING to True on mode switch.
  				# Force it to False RIGHT BEFORE __setupCamera runs!
  				cam_self.__class__._USE_SWINGING = False
  				
  				res = _orig_sniper_enable(cam_self, *a, **kw)"""

if bad_hook in content:
    content = content.replace(bad_hook, good_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Injected _USE_SWINGING guard!")
else:
    print("Could not find sniper_enable hook!")
