import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_swinging = """SniperCamera._USE_SWINGING = False"""
good_swinging = """SniperCamera._USE_SWINGING = False
# AvatarInputHandler resets _USE_SWINGING on every mode switch!
# We MUST patch the C++ getter to prevent it from reverting to True.
if not hasattr(BigWorld, '_orig_wg_isSniperModeSwingingEnabled'):
    BigWorld._orig_wg_isSniperModeSwingingEnabled = getattr(BigWorld, 'wg_isSniperModeSwingingEnabled', None)
BigWorld.wg_isSniperModeSwingingEnabled = lambda: False"""

if bad_swinging in content:
    content = content.replace(bad_swinging, good_swinging)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Fixed BigWorld.wg_isSniperModeSwingingEnabled!")
else:
    print("SniperCamera._USE_SWINGING = False not found!")
