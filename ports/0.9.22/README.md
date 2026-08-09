# World of Tanks 0.9.22.0.1 LAN/offline port

This directory contains the version-local client layer for the pinned Chinese
HD client:

- client version: `0.9.22.0.1 #1513`
- executable architecture: 32-bit x86
- embedded runtime: CPython 2.7.7
- release entry format: `mod_*.pyc`
- package format: Store-only ZIP-compatible `.wotmod`

Version `0.3.44` replaces the old compatibility slice. It is a server-backed
standard-battle implementation with a stock map picker, native Avatar and
Vehicle entities, a playable local vehicle, LAN state, damage, 15 vehicles per
team, the copied tactical-bot stack and repeatable rounds. The removed `vertical_slice.py`
runtime is not packaged as a fallback.

`0.3.44` installs one copied pose before native input startup and feeds that
same provider to attached, own, camera, gun, sniper and minimap consumers. It
gates native entity lookup by spotted/alive state, uses visible-pose descriptor
collision for incoming fire, restores the native RPM channel, and presents
server-admitted hit direction, health, stock shot results and battle events.
Internal bot pose and aim updates use the private authority registry, so an
unspotted vehicle keeps simulating without leaking back into the native AOI.
Critical hits are proposed on detached state and committed only by revisioned
server events. Accepted and natively applied ids are separate, so the ordered
event journal waits for staged Vehicles and snapshot echoes cannot swallow
fatal feedback or rewind repairs. The reticle and physical shot still share one dispersion angle;
when server aim is enabled, the local cell echoes that same ray and angle into
the second marker instead of leaving a stale oversized circle on screen.
Tank contact now uses the current 0.8.2 descriptor-sized chassis OBB law rather
than a hull circle chain, and native R/F cruise keeps its 25%/50% flags and HUD.
The measured 0.8.2 performance structure is also carried over without its
temporary profiler output: one spatial broadphase and one traffic snapshot are
shared across each bot tick, expensive planning/steering is staggered near
10 Hz while motion, gun and publication remain at 30 Hz, and navigation work
uses a bounded search slice with once-per-second cache housekeeping.
This release also separates shared spotting from each Bot's current firing
lane: occluded Bots approach the contact, while focus slots prefer vehicles
that can actually shoot. Spawn-to-route joins are Bot-scoped rather than
reusing the first tank's egress path. Player and authority-Bot motion run the
retained tree, column and fragile contact sensor before publishing a pose, and
native destruction is committed only after #1513 accepts it. Combat events
carry an explicit shot, fire, ram or non-attack cause; the marker receives the
verified local vehicle identity, and friendly ramming no longer emits a
projectile-hit or enemy-efficiency notification.

The battle foundation now reuses the proven 0.8.2 behavior instead of a
separate simplified implementation: the native countdown receives an advancing
offline server clock, roster entries have unique ready identities, standard
ArenaType spawn points (or the wide CTF base formation) are shared by players
and bots. Each spawn slot is resolved and grounded once, then reused by both
the manifest and entity creation; local structure probes keep it near its
retail anchor without falling back to map centre. Player input still enters
through the exact #1513 `PlayerAvatar.moveVehicle` mailbox, but the copied
0.8.2 force, traverse, terrain and pose integrator owns movement because a
client-only Vehicle has no retail game-server transform stream. Its pose is
published through the audited #1513 `Vehicle.model.matrix`, camera and speed
boundaries without calling the forbidden `Entity.teleport`. Authority bots use
the same copied 0.8.2 force, traverse and pose integrator. Remote gameplay vehicles
again use the mature 0.8.2 split between a synthetic vehicle identity and a
separate `OfflineEntity` visual; #1513's verified compound-model assembler
supplies the visual resources.

This is not yet a claim of complete 0.8.2 battle parity. The finalized 0.8.2
spawn-congestion recovery, reverse-steering correction, route planner and
server macro coordinator are now migrated and pinned by the source audit. All
41 supported standard maps use build-specific graphs baked from #1513 terrain,
static collision and water resources; the same safe graph anchors and heights
drive player and bot formation slots. Player-visible spotting now copies the
0.8.2 50-metre proximity, 500-metre ceiling, two-point LOS, allied relay and
five-second memory law across model, marker and minimap presentation. It also
uses exact #1513 moving/still descriptor camouflage, shot penalties and a
freshly baked 41-map pair-specific foliage index rather than 0.8.2 map data.
Passive equipment/skill
modifiers and detailed post-battle statistics remain explicit
open items in `BATTLE_SOURCE_AUDIT.md`; the current server does own live frags,
human team-killer state and the terminal winner/reason.

`SERVER_RESPONSIBILITY_AUDIT.md` separately records the retail game-server
surface and the current authority boundary. In particular, visibility,
reload/ammunition legality, movement validation, assist/first-detection and
detailed results are not mislabeled as server-owned merely because a client
can currently present or report them. Player-caused destructible results are
now server-deduplicated and replayed to every client; bot-to-object contact is
still called out separately.

The offline account discovers every complete standard-battle vehicle definition
from the pinned client at startup instead of maintaining a hard-coded tank list.
Each garage vehicle receives its stock configuration, a full trained crew and
default ammunition. Event, IGR-only and observer definitions are excluded. The
account starts with 100,000,000 credits, 100,000,000 free XP, 1,000,000 gold,
2,000 garage slots and 2,000 barracks berths.

This release fixes both halves of the real `#1513` battle-entry boundary. The
`VEHICLE_ADDED` roster is published before selecting the local Vehicle id, and
the compatibility wrapper no longer repeats the player-id notifier inside the
native `vehicle_onEnterWorld` stack. This preserves the stock own-vehicle matrix
initialization order. Full Windows dumps identified two unsafe native methods
that dereference the unavailable retail server/filter chain on client-created
entities: `WGVehicleFilter.syncGunAngles` and
`WGVehicleFilter.syncStabilisedYPR`. A complete `scripts.pkg` scan finds exactly
four Python call sites: initial Vehicle physics, packed gun-angle updates,
damaged-model refresh and the Avatar auxiliary-physics handler. Scoped adapters
suppress only those calls inside their exact stock handlers. Vehicle physics,
speed providers, model refresh, track/RPM updates and filter identity everywhere
else remain stock. A returned
client-only entity id is still not treated as a materialized tank: client
ready, `AVATAR_READY` and battle period wait for native `onEnterWorld`, registry
presence, `inWorld`, `isStarted` and a descriptor.

Exact `#1513` applies the battle `PERIOD` update synchronously and immediately
sends its initial `moveVehicle` mailbox from `PlayerAvatar.__setIsOnArena`.
The bridge therefore opens its input gate after every Vehicle readiness check
but immediately before publishing `PERIOD`; a failed publication closes the
gate and remains latched as a startup failure.

LAN JSON text is Unicode on the embedded Python 2 runtime, while BigWorld's
Avatar and Vehicle `STRING` properties require byte strings. The release now
normalizes those entity-bound values to UTF-8 and suppresses only the stock
world callbacks emitted for an Avatar whose constructor already failed. A
Python 2 build audit covers both Avatar and Vehicle string boundaries.

Map-start failure now restores the stock prebattle dispatcher before rebuilding
the Account lobby, while final process shutdown guards the exact late
`SoundGroups.destroy` lookup against a retired Account. Callback ownership is
generation-safe, so a late callback from an earlier attempt cannot cancel or
overwrite the next round.

## User flow

1. Start `lan_battle_server.py`. One client is sufficient for an offline round;
   additional clients can join the same waiting room over LAN.
2. Start the frozen client. After the native intro/login state finishes its
   destructive cleanup, the mod creates its local Account and enters Lobby.
3. Select the tank you want to use, then click the garage's native **Battle!**
   button. The LAN handshake uses that tank's exact type name and descriptor
   health; it does not silently fall back to MS-1. If no valid tank is selected,
   the client stays in the garage and displays a native warning. The button
   joins the LAN waiting room; it does not call retail matchmaking or a retail
   training-room service.
4. If the endpoint cannot be reached, the stock settings window opens
   automatically while the client retries. Clicking **Battle!** again while
   connecting also opens it. Any not-yet-accepted client can edit
   `LAN SERVER: host:port` there; this does not grant host authority.
5. The first waiting player is the room host. Only that client opens the stock
   training settings window as a local map picker. Its Description field shows
   the editable `LAN SERVER: host:port` endpoint on the first line, the live
   player list and `CREATE = START BATTLE FOR EVERYONE`. Later players click
   the same **Battle!** button, remain in the garage and wait for the host.
   If the host closes the picker, it remains closed until that client clicks
   **Battle!** again.
6. The host chooses one server-offered standard map and uses the window's
   primary action. The server is authoritative for the host, selected map and
   start; guest start requests are rejected before they can change the map.
7. The server fills vacant slots with bots and broadcasts the same round to
   every waiting client.
8. A team-elimination result freezes the round. After five seconds the server
   returns connected players to the waiting room and the stock picker opens for
   the current host. If the waiting host leaves, ownership passes to the lowest
   connected player id and that client receives the picker.
9. Returning to the garage during a round retires that player, transfers bot
   authority when necessary and retains the LAN connection until the server's
   next waiting-room barrier.

No F12, `0`, or other battle-start hotkey is used. Setting `enabled` to `false`
disables this port; it does not select a second or legacy AI path.

Only arena definitions whose gameplay name is `ctf` are exposed. In this
client, `ctf` is the internal name of standard battle, not a separate
capture-the-flag variant. Assault and encounter definitions are excluded.

## Runtime ownership

The Python 3 server owns the room host, selected map, teams and slots, canonical
health, accepted shot events, frags, human team-killer state, active-round
retirement, terminal result and round reset. It
exposes a newline-delimited JSON protocol rather than emulating the original
BigWorld game server.

One elected client owns bot simulation because only the client can query the
proprietary map collision, water, terrain and tank resources. Bots receive
role-specific routes and stable randomized personalities. Local steering
checks nearby terrain, slope, water, obstacles and other vehicles; combat
includes role-dependent range, aiming, angling, peeking and optional
forward/backward jiggle. The server contains a portable planner boundary, but
the 0.9.22 runtime now consumes its revisioned global `bot_orders` for macro
targets. The elected authority client still owns terrain visibility probes and
local collision/water/slope avoidance, with its local planner as a fallback
when no server order is available.

Those bot components are wired and covered by pure-logic tests. The finalized
0.8.2 spawn/OBB and route corrections are migrated, and the authority client
does not reapply its older server echo over a locally integrated Bot pose. The
real #1513 client still has to validate the resulting movement visually.
Player spotting uses the copied proximity/LOS/team-relay law and starts or
stops the model, marker and minimap together.

The player's real `Vehicle` retains the stock #1513 input, gun, appearance and
HUD lifecycle. `PlayerAvatar.moveVehicle` sends the exact movement flags to
the LAN mailbox and native filter. The copied 0.8.2 integrator is the sole
authoritative pose owner and publishes position, yaw and speed through the
verified compound-model and camera providers. It does not call
`BigWorld.Entity.teleport`, which this client rejects with `Operation is not
allowed` after the entity enters the world.

Remote humans and bots are Python gameplay vehicles backed by separate
`OfflineEntity` compound models, descriptor hit testers and the copied 0.8.2
pose/health surface. The authority integrates and publishes the copied bot
poses; every client applies those samples directly to the presentation. Only
the local player uses `WGVehicleFilter.notifyInputKeysDown`. This distinction
is required because a retail remote `WGVehicleFilter` expects game-server pose
samples that the offline connection does not produce.

Current standard battles finish by team elimination, the copied standard-base
capture rule, or the server-owned 15-minute timeout. Retail matchmaking,
reconnect recovery, internet authentication, anti-cheat, alternate
module purchases and persistent garage mutations are outside this release.
Small repair kits, small medkits and hand extinguishers are exposed through the
exact #1513 activation-code path during battle.

Protocol v5 messages are now strictly round-scoped. Keep the client package
and `lan_battle_server.py` from the same checkout; this client rejects an older
server before entering the waiting room when its welcome lacks the build or
room-host contract.
The #1513 client also declares `wot-0.9.22.0.1-cn-1513` in its handshake. The
server pins each non-empty room to one client build and its exact map pool,
preventing 0.8.2 synthetic coordinates from being mixed with 0.9.22 world
coordinates. Separate server ports are required for simultaneous rooms using
different client versions.

## Configuration

The copy-ready overlay installs, and the client subsequently updates:

```text
mods/configs/offline_lan_0922/config.json
```

The supported fields are:

```json
{
  "schema": 1,
  "enabled": true,
  "host": "127.0.0.1",
  "port": 28782,
  "name": "Player",
  "vehicle": "ussr:R11_MS-1",
  "max_health": 90,
  "startupTimeoutSeconds": 30.0,
  "prebattleCountdownSeconds": 15.0,
  "battleDurationSeconds": 900.0
}
```

Before receiving a server welcome, a failed connection opens the native
training settings window automatically; **Battle!** also reopens it while the
client is connecting. Once waiting,
only the room host owns that window as the map picker; guests continue to use
their saved `config.json`. The server supplies the selected map and spawn. The
`vehicle` field seeds the initial offline garage record; the tank selected in
the carousel when **Battle!** is clicked is authoritative for the LAN join. The
legacy `max_health` field remains accepted for configuration compatibility, but
battle health is read from that selected vehicle's descriptor. The release does
not write a capability trace or vertical-slice status file; normal failures are
reported in `python.log` without verbose per-frame logging.

The real Avatar enters `PREBATTLE` for the server-owned 15-second countdown
copied from the 0.8.2 battle flow before the runtime accepts movement or
shooting, then receives a timed `BATTLE` period. The configuration value is an
engine-free fallback only; a connected battle uses `battle_live`. That message
is an ordered tick-zero wire barrier before the first snapshot. Relative
server timing is projected from the network-thread receive timestamp on a
monotonic clock (with half the measured RTT), so a stalled render frame or a
wall-clock adjustment cannot restart or skew the countdown.
Leaving during native loading removes that participant and re-evaluates the
barrier atomically. If a transition write fails, the server repairs membership
and sends survivors a newer authoritative roster before continuing.
Authority bot poses are materialized locally without waiting for their server
echo. Bot Vehicle creation is staggered across the countdown, matching the
0.8.2 loading pattern and avoiding a burst of 29 HD model prerequisites in one
32-bit BigWorld callback.

Authoritative shot events use the selected gun's finite burst count. This
closes the stock Avatar shot-acknowledgement wait and prevents one click from
leaving the native firing effect active indefinitely. During an offline LAN
battle the stock debug panel displays the independent LAN socket's measured
round-trip time and connection state instead of the absent retail transport's
`999`/red result.

Critical-hit proposals are compare-and-swap updates against the target's
canonical module revision. They also carry the pre-critical hull damage: if a
delayed proposal targets a state that has since repaired or extinguished, the
server still applies the ordinary hit and feedback but rejects the stale module
state and any obsolete ammo-rack damage amplification. Bot repair and fire use
the same copied 0.8.2 constants under a revisioned server ledger. Fire elapsed
time, one-second tick phase and igniter identity survive authority transfer, so
the ten-second burnout neither restarts nor loses its final-kill attribution.

## Build

The per-module 0.8.2/#1513 provenance and permitted differences are recorded
in [`BATTLE_SOURCE_AUDIT.md`](BATTLE_SOURCE_AUDIT.md). The release build runs
`tools/audit_battle_sources.py` and fails when a module is undocumented or a
copied 0.8.2 law/data file drifts.

The loader ignores source `.py` files, so release bytecode must be compiled by
CPython 2.7. Verify the exact client and build the package together:

```bash
./build_for_client.sh \
  ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD
```

To produce a copy-ready overlay for a server on another machine, pin its LAN
address at build time:

```bash
OFFLINE_LAN_RELEASE_HOST=192.168.1.164 ./build_for_client.sh \
  ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD
```

The script uses local CPython 2.7 when available and otherwise the pinned
Docker build. Before compiling, it reads code objects directly from the pinned
client's `scripts.pkg`, rejects mismatches in the stock method signatures,
direct-consumer literals and lifecycle code paths used by this port, verifies
the exact `AccountCommands` constants, inventories every exact-client call site
for the guarded Vehicle filter syncs, runs the complete raw `serverSettings`
subscript inventory against the local producers, and checks the ordered
lifecycle contracts plus the complete Account-helper binding inventory.
Signature checks include
`*args`/`**kwargs` flags at the stock picker boundary, rather than comparing
only positional argument counts. It then validates the package
version, exact source-to-PYC module manifest, CPython 2.7 magic, absence of
Python source or optimized/cache bytecode, CRC, unique archive members,
explicit directory entries and Store compression for every member.

Signature matching is only one gate. The tests also validate the exact
Vehicle/Avatar property and mailbox schemas, the Account/Lobby direct-key and
tuple contracts recorded in `tools/account_lobby_consumer_contract.json`, and
LAN round/message state transitions, including active leave and a next battle
arriving before native Lobby/Hangar recovery. A conventional static type
checker cannot replace these checks because BigWorld injects dynamic Entity
properties and mailboxes at runtime.

To regenerate the complete strict standard-mode navigation set from the pinned
client, use the atomic batch baker. It validates all 41 graphs before replacing
the previous set and writes the checksum manifest last:

```bash
python3 tools/bake_all_navigation_0922.py \
  --client ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD \
  --output-dir navgraphs --jobs 4
```

The matching foliage baker decodes the exact `SpTr`/`BWST` scene tables and
ctree-v106 bounds, then atomically publishes the complete 41-map checksum set:

```bash
python3 tools/bake_foliage_0922.py \
  --client ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD \
  --all --output-dir foliage
```

Outputs are written to `dist/`:

```text
org.peng.offline_lan_0922_0.3.44.wotmod
org.peng.offline_lan_0922_0.3.44.wotmod.sha256
WoT-0.9.22-LAN-Client-<release hash>/
WoT-0.9.22-LAN-Client-<release hash>.zip
```

The hash-named directory is directly mergeable into the game root. Each build
removes only older outputs produced by this port from `dist/`.

## Exact-client review boundary

The implementation was checked against bytecode extracted from the local
Chinese `#1513` client, not only against public 0.9.22 source trees from other
builds. `COMPATIBILITY_REVIEW.md` records the reviewed interfaces, adopted
reference patterns and remaining Windows runtime checks.

Do not install this package into a current/online client or use it to log into
a live account. It targets only the frozen offline test installation.

## License and unofficial status

Project code is distributed under the GNU General Public License version 3;
see the repository-level `LICENSE` and `THIRD_PARTY_NOTICES.md`. That license
does not grant rights to the World of Tanks client, assets, trademarks or
other Wargaming property. Users must supply their own lawfully obtained pinned
client.

This work includes trademarks and/or copyrighted works that are the exclusive
property of Wargaming. All rights reserved by Wargaming. This work is
unofficial and is not endorsed by Wargaming.
