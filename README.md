offline-battle mod - World of Tanks 0.8.2
===================================================

> [!IMPORTANT]
> This project targets the original Windows 0.8.2 client and its embedded
> Python 2.6 runtime. It does not provide the game client or a standalone game.

Play World of Tanks 0.8.2 offline: no login, no server. You go straight to the
hangar, pick a tank and fight bots on the real maps.
Bots use vehicle roles, stable individual personalities and shared team spots;
all 33 stock maps have dedicated standard-battle tactical routes. Strategic
route anchors are connected at runtime by a cached terrain-aware A* layer,
then local feelers handle cliffs, solid obstacles, nearby tanks and recovery
from congestion. This AI is used in both normal offline play and LAN-authority
simulation. Assault and encounter variants are intentionally outside the
supported mode.


Install
-------
Close the game first. On Windows, the simplest update is:

    refresh_client.bat "C:\Games\World_of_Tanks_0.8.2"

The batch file copies the client files and removes one stale entry bytecode
file that otherwise hides updated source code in this old client.

For manual installation, this zip starts at  scripts/  and  gui/ .
Extract it INTO:  <WoT 0.8.2 game root>/res_mods/0.8.2/
so the files land at:  res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.py

When updating an existing installation, also delete:

    res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.pyc

Do not delete scripts/client/CameraNode.pyc; it is the old client's mod loader.

Start the game. If the mod loaded you go straight to the offline hangar
instead of the login screen.

To uninstall, delete  res_mods/0.8.2/scripts  and  res_mods/0.8.2/gui .
Your settings survive in  <game root>/offhangar_user/  - delete that folder too
if you want a clean slate.


Settings
--------
Your editable copy is created on first launch at

    <game root>/offhangar_user/config.json

It is OUTSIDE res_mods, so updating or deleting the mod never touches it.
Options that a mod update adds later fall back to their defaults until you add
them to your file; the shipped defaults live in config_defaults.json next to
the mod and are documented there.

The ones people change most:

  nickname                your in-game name
  bots_per_team           15  (15 vs 15)
  spotting_enabled        enemies must be spotted to be seen
  perfect_accuracy        shells always land in the centre of the circle
  prebattle_countdown_seconds / auto_spawn_delay_seconds

LAN setup
---------
For the optional LAN mode, click the visible `LAN SETTINGS` entry in the
upper-right of the offline hangar. If mouse input is unavailable, `F11` remains
a fallback. Enter the server IP and TCP port, toggle LAN battle, and press
`Enter` to save. Start `lan_battle_server.py`, then click `Battle!` on every
client to join its single waiting room. The queue screen opens only after the
server accepts the connection, and its displayed player total follows the real
server roster. The server terminal prints one `JOIN` line per client. Use the
clickable waiting-room panel to choose a map and click `START BATTLE`; the server
broadcasts one start with that map to every client. LAN mode never starts a
local random-map timer while it is waiting or after a failed connection. A
client that connects after the round starts joins that same running round.

The first client in the battle is elected as bot/rules authority. All clients
receive the same bot names, tanks, positions, movement, firing, HP and deaths,
plus shared capture progress and one shared battle result. If that client
disconnects, the server elects another connected client. Human input, bot
state and server snapshots use 30 Hz updates; remote vehicles are interpolated
every render frame with a short bounded prediction. The stock ping and
connection indicator show the measured LAN connection rather than placeholders.

Only the server process needs an external Python 3 installation; the client
mod uses the Python 2 runtime already embedded in the 0.8.2 client. See
LAN_SERVER.md for the server command, diagnostics and Parallels network notes.


In battle
---------
  O / P / L   spawn a bot where you aim - your own tank as an enemy clone /
              a random enemy / a random ally
  K           leave the battle


Module and crew damage
----------------------
Shells damage modules and crew the way the era did: every device has its own HP
pool from the vehicle descriptor, its own hit chance from the game's material
table, and repairs itself back to roughly half over time. Damaged modules cost
performance, destroyed ones stop working. Fires start from the engine or a
holed fuel tank and burn out on their own. Repair kits, med kits and the fire
extinguisher work, and the damage panel and crew voice lines follow.

Interior modules and crew have no collision geometry in the 0.8.2 client, so
their hit boxes come from a per-vehicle profile set covering 251 vehicles.
Switch it off with  internal_layout_profiles: false  to fall back to a coarser
compartment model.

  module_test_mode: true    bot shells roll every module and crew crit but take
                            no hull HP off you, so you can watch the system work
                            without dying. Turn it off for normal play.


Notes
-----
This is an unofficial compatibility mod and is not affiliated with or endorsed
by Wargaming. World of Tanks and related names are trademarks of their
respective owners. You must supply your own lawfully obtained 0.8.2 client and
remain responsible for complying with its terms and the laws that apply to you.

No open-source license has been selected for this repository. Public visibility
does not by itself grant permission to redistribute or reuse its contents.

Debug logging is OFF by default. Turn on  debug_logging  in your config.json if
you want the mod to write diagnostics into python.log.

The current source build always writes this startup marker to python.log:

    Offline Battles source loader active; LAN settings module enabled

If that line is absent, the updated mod entry did not load.

0.8.2 is a 32-bit client. Long non-stop sessions across many different maps
slowly grow memory - if it gets sluggish after a lot of battles, restart the
client.

The X-ray overlay (internal_xray_overlay) draws module and crew boxes through
the armour. It is a debug view for offline battles and is OFF by default; the
module is not even loaded while it is off. Do not turn it on in a client you
log into a live server with.
