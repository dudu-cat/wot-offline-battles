# World of Tanks 0.9.22.0.1 LAN/offline port

This directory contains the version-local client layer for the pinned Chinese
HD client:

- client version: `0.9.22.0.1 #1513`
- executable architecture: 32-bit x86
- embedded runtime: CPython 2.7.7
- release entry format: `mod_*.pyc`
- package format: Store-only ZIP-compatible `.wotmod`

Version `0.3.11` replaces the old compatibility slice. It is a server-backed
standard-battle implementation with a stock map picker, native Avatar and
Vehicle entities, a playable local vehicle, LAN state, damage, 15 vehicles per
team, tactical bots and repeatable rounds. The removed `vertical_slice.py`
runtime is not packaged as a fallback.

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
initialization order. The offline-only physics seam also suppresses the one
initial `WGVehicleFilter.syncGunAngles` call that dereferences an unavailable
retail filter dependency for client-created entities; all other stock physics
initialization remains active. A returned client-only entity id is still not
treated as a materialized tank: client ready, `AVATAR_READY` and battle period
wait for native `onEnterWorld`, registry presence, `inWorld`, `isStarted` and a
descriptor.

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
8. A team-elimination result freezes the round. After three seconds the server
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
health, accepted shot events, active-round retirement, elimination result and round reset. It
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

The player's vehicle uses bounded kinematic movement on a real `Vehicle`
entity. It is grounded with client collision queries and vetoes water, steep
slopes and solid obstacles. This is intentionally not a reimplementation of
the retail server's authoritative vehicle physics.

Current battle completion is by team elimination. Base capture, retail
matchmaking, reconnect recovery, internet authentication, anti-cheat, alternate
module purchases and persistent garage mutations are outside this release.

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
  "startupTimeoutSeconds": 30.0
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

## Build

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
the exact `AccountCommands` constants, runs the complete raw `serverSettings`
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

Outputs are written to `dist/`:

```text
org.peng.offline_lan_0922_0.3.11.wotmod
org.peng.offline_lan_0922_0.3.11.wotmod.sha256
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
