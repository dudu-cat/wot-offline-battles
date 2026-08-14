import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

# Fix the import mess at the top
bad_import = """import BigWorld
from AvatarInputHandler.cameras import SniperCamera
SniperCamera._USE_SWINGING = False"""

if bad_import in content:
    content = content.replace(bad_import, "import BigWorld")

# Fix the import mess in _force_camera_to_model
bad_force = """import BigWorld
from AvatarInputHandler.cameras import SniperCamera
SniperCamera._USE_SWINGING = False, Math"""

if bad_force in content:
    content = content.replace(bad_force, "import BigWorld, Math")

# Add the CORRECT global _USE_SWINGING = False at the top
if "SniperCamera._USE_SWINGING = False" not in content:
    good_import = """import BigWorld
from AvatarInputHandler.cameras import SniperCamera
SniperCamera._USE_SWINGING = False
"""
    content = content.replace("import BigWorld", good_import, 1)

with open(file_path, 'w') as f:
    f.write(content)

print("Fixed offline_battle.py!")
