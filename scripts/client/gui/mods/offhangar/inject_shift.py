import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_hook = """				if BigWorld.isKeyDown(Keys.KEY_A): turn_dir = -1
				if BigWorld.isKeyDown(Keys.KEY_D): turn_dir = 1"""

good_hook = """				if BigWorld.isKeyDown(Keys.KEY_A): turn_dir = -1
				if BigWorld.isKeyDown(Keys.KEY_D): turn_dir = 1
				
				# Manually handle SHIFT key to toggle sniper mode
				global _last_shift_state
				if '_last_shift_state' not in globals():
					_last_shift_state = False
				
				is_shift = BigWorld.isKeyDown(Keys.KEY_LSHIFT) or BigWorld.isKeyDown(Keys.KEY_RSHIFT)
				if is_shift and not _last_shift_state:
					# Shift was just pressed
					if hasattr(g_offline_aih, 'ctrl'):
						is_currently_sniper = g_offline_aih.ctrl.__class__.__name__ == 'SniperControlMode'
						new_mode = 'arcade' if is_currently_sniper else 'sniper'
						LOG_DEBUG('OfflineBattle: SHIFT pressed! Manually forcing control mode to:', new_mode)
						try:
							g_offline_aih.onControlModeChanged(new_mode)
						except Exception as e:
							import traceback
							LOG_DEBUG('OfflineBattle: Failed to switch mode:', traceback.format_exc())
				_last_shift_state = is_shift
"""

if bad_hook in content:
    content = content.replace(bad_hook, good_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Injected Shift key manual handler!")
else:
    print("Could not find Keys hook!")
