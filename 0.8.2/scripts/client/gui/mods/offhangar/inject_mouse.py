import sys

file_path = r'c:\Games\World_of_Tanks_0.08.02.00.00_EU_0543_SD\res_mods\0.8.2\scripts\client\gui\mods\offhangar\offline_battle.py'
with open(file_path, 'r') as f:
    content = f.read()

bad_hook = """		player.handleKey = lambda key, isDown, mods=0: None
		player.getAutorotation = lambda: False"""

good_hook = """		
		# In Offline mode, the player is Account, which lacks handleMouseEvent/handleKeyEvent.
		# This breaks the entire AvatarInputHandler event chain (scrolling, zooming, mouse look in Sniper mode).
		# We must inject these handlers into the Account class directly so game.py routes events to our AIH!
		
		def _account_handleKeyEvent(self, event):
			if hasattr(self, 'inputHandler') and self.inputHandler is not None:
				return self.inputHandler.handleKeyEvent(event)
			return False
			
		def _account_handleMouseEvent(self, event):
			if hasattr(self, 'inputHandler') and self.inputHandler is not None:
				return self.inputHandler.handleMouseEvent(event.dx, event.dy, event.dz)
			return False
			
		import Account
		Account.Account.handleKeyEvent = _account_handleKeyEvent
		Account.Account.handleMouseEvent = _account_handleMouseEvent
		
		# Also map player.handleKey so native calls don't crash if they bypass game.py
		player.handleKey = lambda key, isDown, mods=0: None
		player.getAutorotation = lambda: False"""

if bad_hook in content:
    content = content.replace(bad_hook, good_hook)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Injected Account event forwarders!")
else:
    print("Could not find event hook!")
