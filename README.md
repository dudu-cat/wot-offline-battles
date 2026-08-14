offline/LAN battles - World of Tanks 0.8.2 and 0.9.22.0.1
================================================================

## World of Tanks 0.9.22.0.1 #1513

The `0.9.22` release targets the frozen Chinese HD client. It is a
separate CPython 2.7 `.wotmod`, not a copy of the 0.8.2 scripts. Start its
Python 3 LAN server at `0.9.22/server/lan_battle_server.py` even for a
one-player battle, then click the stock **Battle!** button to join its waiting
room. The first waiting player becomes
room host and receives the native training-settings window as a local map
picker; later players click the same button and remain in the garage while
they wait. Before the server accepts a client, clicking **Battle!** again
explicitly opens the same native window to edit `LAN SERVER: host:port`. Once
the room is waiting, only the host can open that picker, choose a standard map
and start the shared round. There is no F12 or `0` key workflow.

The 0.9.22 runtime uses native map, Avatar, Vehicle and HUD objects, fills each
team to 15 vehicles, and supports movement, aiming, firing, damage, tactical
bots, elimination, return to the picker and another round. See
`0.9.22/INSTALL.txt` for the copy-ready package instructions and
`0.9.22/COMPATIBILITY_REVIEW.md` for the exact `#1513` review and honest
runtime boundary.

Release `0.4.0` turns the frozen #1513 port into a copy-ready product layout.
The release now lives directly under `0.9.22`, defaults to
`127.0.0.1:28782`, and stores an address changed in the in-game window in
`mods/configs/offline_lan_0922/server_endpoint.json`; replacing the release
overlay does not overwrite that user-owned file. On first garage entry it
suppresses the stock CN automatic server-announcement browser before creation,
leaving browsers explicitly opened by the player untouched. The waiting-room text
uses player-facing host/connect instructions instead of implementation terms.
A CI-built x64 Windows server can be started by double-clicking its executable;
it always listens on `0.0.0.0:28782` with the `server_random` map policy.

Release `0.3.76` restored the exact #1513 countdown aiming lifecycle. Startup
normalizes the camera and gun to the vehicle direction once, then PREBATTLE
leaves the physical gun, stock reticle and optional server marker frozen. The
single native BATTLE period transition starts stock aiming and opens the
existing movement/fire fence.

Neutral coast drag uses a conservative `0.65` share of recovered track grip,
up from `0.55`. Exact resources provide mass, speed and terrain inputs, but the
native C++ W-release curve is not claimed as exactly reproduced. The Type 62
regression is frame-rate invariant at 30, 60 and 120 FPS; final stopping feel
still requires Windows #1513 acceptance.

Release `0.3.75` starts the exact #1513 `VehicleGunRotator` during PREBATTLE
after publishing its native targeting parameters. Only the rotator's native
`PlayerAvatar.isOnArena()` guard is admitted and the private flag is restored
immediately; the existing `_battle_live` fence continues to block movement and
fire. The stock reticle and physical turret can therefore track together during
the countdown instead of snapping together only when BATTLE starts.

Shared strategic A* route legs now apply a small preference for baked cells
with more independently proved exits, and smoothing may not undo that
clearance gain. This moves Lakeville's narrow-road traffic toward the usable
centre where the graph has room, without adding a link, relaxing collision,
water or grade checks, or changing an unavoidable one-cell passage. Full-pair
Bot observations now run every 0.40 seconds, with their firing-lane refreshes
spread across the final 0.20 seconds and ordinary queries capped at 585 m. A
selected target still receives the independent 0.20-second final-fire lane
check, so the lower periodic cost does not let a stale lane authorize a shot.

Authority Bots also receive finite per-battle ammunition from the installed
gun's real capacity. Ordinary vehicles distribute available standard,
higher-penetration and HE categories at `3:2:1`; SPGs use an HE-led `1:1:4`,
with unavailable categories redistributed. The server normally plans standard
ammunition, requests HE for a sufficiently fragile or finishable target and a
higher-penetration round when standard penetration is inadequate. Human target
armor comes from the installed vehicle descriptor cached by vehicle identity,
not from a mutable armor value in the live player record. Current and next
rounds are distinct: the next choice becomes loaded only at a completed reload
boundary, the actual fired category is decremented, and the atomic inventory/
loaded/next/reload-pending state survives authority takeover. Player ammunition
and reload admission remain client-owned.

Lakeville's compiled space contains separate CTF and assault-mode base visuals,
selected by native visibility bits. Exact #1513 can overwrite the selected CTF
bit late in client readiness, which made the other mode's base visible as an
apparent duplicate. The runtime now idempotently reapplies CTF bit `1` after
that stock boundary: the CTF base stays visible and the assault-mode base stays
hidden. XML, capture rules, minimap, teams and the one-base-per-team CTF record
are unchanged. Native prebattle turret tracking, sustained frame pacing,
realised narrow-road traffic, base visibility and ammunition presentation still
require acceptance in the exact Windows `0.9.22.0.1 #1513` client.

Release `0.3.74` reduces the authority client's per-frame Bot overhead without
changing simulation ownership. `BotRuntime` still returns its complete local
states so the same frame can resolve projectile launches and other client-side
laws, while `LANClient.send_bot_state` projects only the fields consumed by the
v5 server sanitizer before the immutable sender snapshots the payload. Exact
3x3 straight-motion receipts also survive the shorter 0.0975-second planning
refresh only while their own origin, yaw, travel sign and actual-frame hull
sweep remain strictly contained; any drift or exhausted coverage restores a
native proof.

Spawn traffic now uses one movement tolerance throughout: navigation will not
select a point within the driver's 1.5-metre arrival radius when the requested
goal is still distant. An intentional right-of-way wait suppresses stuck
recovery for at most 1.5 seconds; a continuously blocked follower can then use
the existing finite recovery path instead of waiting forever. Native Windows
`#1513` frame pacing and realised traffic remain release acceptance items.

Release `0.3.73` repairs the load-barrier message produced from the local spawn
plan. `SpawnPlanner` intentionally indexes its two team formations with the
integer keys `1` and `2`; the asynchronous reliable sender accepts only
already-canonical JSON mappings. The client now converts those two keys to
`"1"` and `"2"` at the `battle_ready` wire boundary, preventing the local
sender from rejecting readiness immediately after the Bot manifest. Formation,
team assignment and server authority are unchanged.

Release `0.3.72` replaces instant shot rays with one elapsed-time projectile
path for every player and Bot shell. The firing client launches the shot into a
server-retained round ledger, and the elected projectile authority advances its
gravity curve through collision chords no longer than 25 ms. Vehicle sweeps use
their motion over the same interval, so a target may move clear after the shot
and both direct fire and artillery must lead moving targets. Active shots
survive shooter disconnects and authority takeover; a terminal resolution
atomically commits its direct/splash HP effects and any destructible receipts.

SPGs keep their server-selected rear anchor, then evaluate exact low- and
high-arc candidates through a fair client queue capped at four native collision
rays per rendered frame. A moving-target launch freezes one exact aim/flight
intent while the native muzzle proof runs, instead of chasing a different
solution every tick; if the target has moved more than the 1.5 m terminal
tolerance by completion, that receipt is rejected and a corrected frozen proof
starts. The exact #1513 descriptor survey covers 52 SPGs, 133 installed shell
entries and 43 distinct physical tuples: shell speeds are 265--510 m/s and
gravity is 125--190 m/s2. With the baked maps' 89.106 m maximum terrain drop,
the longest reachable grounded case is the FV3805's 440 m/s, 146 m/s2,
70-degree high arc at 5.872907831 seconds. Stun remains disabled because this
port still lacks a complete canonical penalty, duration and medical-kit
recovery loop. Native
tracer appearance, artillery feel, sustained performance and repeated-round
cleanup still require acceptance in the exact Windows `0.9.22.0.1 #1513`
client.

Release `0.3.71` selectively carries forward the mature 0.8.2 recovery and
transport seams that fit the #1513 architecture. LAN calls now freeze each
plain-JSON payload and enqueue it on a bounded reliable FIFO; the connection
hello remains the synchronous first wire message, ordinary messages are never
coalesced, and overflow or sender failure closes the generation-isolated
transport instead of silently dropping ordered combat state. A newly raised
centre support that exceeds the bounded climb/step limit restores the current
tick's player or Bot pose instead of lifting a hull onto a wagon, roof or large
prop. Realised hard contacts and both rollback paths discard stale Bot decision
and motion proofs, choose one finite escape side, and then replan. Bots align
at the foot of a meaningful climb before applying forward torque, while route
smoothing, live reach, lookahead and partial-path continuation preserve the
turning setup immediately before a climb.

This release does not port the 0.8.2 native `WGVehicleFilter`/physics
experiment. It also keeps the existing SPG rear-anchor staging only: no
open-sky proof, ballistic-arc probe, indirect-hit resolver or stun loop is
claimed. Native motion, contact, camera and repeated-round behavior still
require acceptance in the exact Windows `0.9.22.0.1 #1513` client.

Release `0.3.70` separates terrain, light props and walls that previously shared
one response. A continuous bounded ground profile is drivable in either
direction. Neutral coasting retains the established flat-road drag, then
progressively unloads only its drivetrain share as the vehicle travels farther
downhill; uphill coasting receives no such relief. A fragile/module hit at real
stock-legal speed still crushes through. At low speed or from rest, commanded
drive may use the directional top speed only to prove the unchanged stock gate,
and only when the exact leading hull face plus this frame's real travel reaches
the item. That native submission holds the pose for the current tick and keeps
the speed cap out of vehicle, network and ram state. The following pending-skin
path advances only through the item's exact registered OBB exit and a real
backing-ray recast. Authority Bots retain their staggered 15/20-metre corridor
probe and its ordinary six horizontal planning rays. Only the finally selected
flat, straight, powered motion sample adds an exact read-only 3x3 hull proof
over 15 metres. Per-frame world rays are skipped only while the current sweep,
including actual-frame travel, remains inside that origin/yaw/direction-bound
receipt and no catalog item touches the hull. A hard proof blocks; a deferred
proof is not cached. Missing/stale proof, drift, contact, coasting, braking,
turning and airborne motion remain world-first. Receipt work is capped at 13
jobs per render frame. The waiting rotation retains only Bots that actually
reached this eligible final-motion request; idle, hard-blocked, turning or
airborne Bots drop out. Unattempted receiptless work keeps initial-backlog
priority over refreshes. Once its native callback itself defers, it loses that
priority and rotates behind the other enrolled requests, so neither a persistent
callback deferral nor a refresh can starve the other. A deferred eligible Bot
pauses that frame at its real pre-step speed without route-failure recovery,
caching or a world-query fallback. At most four adjacent soft items may be
skipped in one ray chain; a fifth fails closed pending Windows #1513 acceptance.
SPGs now
deploy to a stable rear-side route anchor and hold there. That server staging
does not yet claim open-sky validation or client-side ballistic arc fire.
Windows #1513 remains the
acceptance boundary for slope feel, crush presentation, Bot clearance and SPG
placement.

Release `0.3.69` adds two bounded battle behaviors. When the server-owned
capture state reports invaders on a team's own base, the macro planner keeps a
stable group of one to three nearby/fast eligible Bots moving to that specific
threatened base while preserving ordinary visible, shootable targets along the
route; contributor identity never exposes an unspotted vehicle. After the
local vehicle dies and the stock postmortem delay ends, the native viewpoint
controls may attach to living friendly vehicles, and the client falls back to
the nearest living ally if the observed vehicle dies or disappears. Both
native camera attachment and in-battle behavior still require Windows #1513
acceptance.

Release `0.3.68` repairs case-sensitive non-tree destructible lookup and gives
player and authority-Bot shots the same ordered scene traversal. For the pinned
build, AP/APCR/APHE may continue through scale-adjusted destructibles of at most
19 HP with a cumulative fixed 25 mm penetration loss per item and unchanged
shell damage; old-mechanics HE/HEAT stop at the first item, with HE exploding
there. Static walls and the nearest vehicle cap traversal, and continuation
uses the exact item OBB exit. These constants come from the exact #1513
resources; the private retail server's precise RNG/range/reduction operation
order is not published, so its lazy one-factor ordering is documented as a high-confidence
same-family reconstruction rather than exact server-source identity. Windows
#1513 remains required for destruction presentation and sustained straight-line
frame-pacing acceptance; the visible hitch is not claimed fixed.

## World of Tanks 0.8.2

> [!IMPORTANT]
> This project targets the original Windows 0.8.2 client and its embedded
> Python 2.6 runtime. It does not provide the game client or a standalone game.
> The `experiment/native-vehicle-physics` branch is a separately packaged,
> version-locked native bot-physics experiment. Read
> `START_NATIVE_TEST_HERE.txt` before installing that branch.

Play World of Tanks 0.8.2 offline: no login, no server. You go straight to the
hangar, pick a tank and fight bots on the real maps.
Bots use vehicle roles, stable individual personalities and shared team spots;
all 33 stock maps ship with validated standard-battle tactical routes and
prebaked terrain navigation graphs. The baker understands terrain height,
bridge decks and penalized shallow fords; runtime pathfinding rejects cliffs,
deep water and solid obstacles. A
short-horizon oriented-hull predictor separates nearby tanks, and failed
terrain segments are remembered briefly so a stalled bot replans instead of
repeating the same bad edge. In combat, client-probed cover candidates feed a
hold/peek/fire/return cycle; cautious and aggressive personalities weight them
differently, while some armoured drivers also jiggle forward and backward.
This AI is used in both normal offline play and LAN-authority simulation.
Assault and encounter variants are intentionally outside the supported mode.

The same AI can run through the companion server with only one connected
player. In that mode the server owns global route progress, target reservations,
last-known contacts, lane-pressure rebalancing, cover reservations and
revisioned bot orders. The elected authority client keeps only the work that
depends on proprietary client data: spotting observations, bounded terrain and
cover probes, local steering, shell collision and BigWorld entity control.
Normal offline mode uses the same new AI locally as a server-free fallback; it
does not switch back to the legacy chase logic.


Install
-------
For the native 1.8.56 experiment, read `START_NATIVE_TEST_HERE.txt` and then
open `START_HERE.txt`: close the game, delete or move aside the old
`res_mods\0.8.2`, then drag the package's complete `0.8.2` folder into the
game's `res_mods` folder. The package also includes double-clickable Windows
and macOS LAN-server launchers.

For a source checkout, the Windows refresh helper is:

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

LAN setup (0.8.2)
-----------------
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
client that connects after the round starts joins the current round.

The first client in the battle is elected as map-simulation/rules authority.
It uploads vehicle profiles, standard-battle route anchors and limited spotting
observations plus a bounded set of drivable cover candidates. The server
assigns targets, advances or rebalances routes, reserves cover and sends
monotonic revisioned orders back; unchanged orders are omitted from later
snapshots. The authority client executes those orders against the real map.
All clients receive the same bot names, tanks, positions, movement, firing, HP
and deaths, plus shared capture progress and one shared battle result. If that
client disconnects, the server elects another connected client and discards the
old authority's short-lived spotting and cover observations before reacquiring
them.
Human input, bot state and server snapshots use 30 Hz updates; remote vehicles
are interpolated every render frame with a short bounded prediction. The stock
ping and connection indicator show the measured LAN connection rather than
placeholders.

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

Project code is distributed under the GNU General Public License version 3;
see `LICENSE` and `THIRD_PARTY_NOTICES.md`. That license does not grant rights
to the World of Tanks client, assets, trademarks or other Wargaming property.

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
