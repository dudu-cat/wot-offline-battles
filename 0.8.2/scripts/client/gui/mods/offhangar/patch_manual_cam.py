import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if 'if hasattr(aim, \'setClipParams\'): aim.setClipParams(_gun_state[\'clip_size\'], 1)' in line:
        new_lines.append(line)
        new_lines.append('\t\t\t\t\t\t\t\t\ttry:\n')
        new_lines.append('\t\t\t\t\t\t\t\t\t\tfrom gui import WindowsManager\n')
        new_lines.append('\t\t\t\t\t\t\t\t\t\tpanel = WindowsManager.g_windowsManager.battleWindow.consumablesPanel\n')
        new_lines.append('\t\t\t\t\t\t\t\t\t\tif panel:\n')
        new_lines.append('\t\t\t\t\t\t\t\t\t\t\tpanel.setCurrentShell(0)\n')
        new_lines.append('\t\t\t\t\t\t\t\t\t\t\tpanel.setShellQuantityInSlot(0, _gun_state[\'ammo_0\'], _gun_state[\'clip\'])\n')
        new_lines.append('\t\t\t\t\t\t\t\t\texcept Exception: pass\n')
    elif 'loaded_models[\'gun_node_matrix\'].set(gunJointMatrix)' in line:
        new_lines.append(line)
        new_lines.append('\n')
        new_lines.append('\t\t\t\t\t\t# --- FORCE SNIPER CAMERA TO GUN MATRIX ---\n')
        new_lines.append('\t\t\t\t\t\tif is_sniper and hasattr(g_offline_aih, \'ctrl\') and hasattr(g_offline_aih.ctrl, \'camera\'):\n')
        new_lines.append('\t\t\t\t\t\t\tcam = g_offline_aih.ctrl.camera\n')
        new_lines.append('\t\t\t\t\t\t\tif cam and hasattr(cam, \'_SniperCamera__cam\'):\n')
        new_lines.append('\t\t\t\t\t\t\t\tif not getattr(cam, \'_offhangar_patched_update\', False):\n')
        new_lines.append('\t\t\t\t\t\t\t\t\tcam._SniperCamera__cameraUpdate = lambda *args, **kwargs: None\n')
        new_lines.append('\t\t\t\t\t\t\t\t\tcam._offhangar_patched_update = True\n')
        new_lines.append('\t\t\t\t\t\t\t\tcam._SniperCamera__cam.set(gunWorldMatrix)\n')
    else:
        new_lines.append(line)

with open(file_path, 'w') as f:
    f.writelines(new_lines)

print("Patch manual camera successful!")
