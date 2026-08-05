# World of Tanks 0.9.22.0.1 LAN/offline port

This directory contains the version-local client layer for the pinned Chinese
HD client:

- client version: `0.9.22.0.1 #1513`
- executable architecture: 32-bit x86
- embedded runtime: CPython 2.7.7
- release entry format: `mod_*.pyc`
- package format: Store-only ZIP-compatible `.wotmod`

Version `0.3.6` replaces the old compatibility slice. It is a server-backed
standard-battle implementation with a stock map picker, native Avatar and
Vehicle entities, a playable local vehicle, LAN state, damage, 15 vehicles per
team, tactical bots and repeatable rounds. The removed `vertical_slice.py`
runtime is not packaged as a fallback.

This release fixes the asynchronous Vehicle-entry boundary exposed by the real
`#1513` client. A returned client-only entity id is no longer treated as a
materialized tank: startup waits for native `onEnterWorld`, registry presence,
`inWorld`, `isStarted` and a descriptor before publishing client ready,
`AVATAR_READY` and battle period. Pending remote updates are coalesced until the
same boundary, and a late entity whose network record was already removed is
destroyed through a retained tombstone instead of becoming an orphan.

Map-start failure now restores the stock prebattle dispatcher before rebuilding
the Account lobby, while final process shutdown guards the exact late
`SoundGroups.destroy` lookup against a retired Account. Callback ownership is
generation-safe, so a late callback from an earlier attempt cannot cancel or
overwrite the next round.

## User flow

1. Start `lan_battle_server.py`. A single connected client is supported.
2. Start the frozen client. After the native intro/login state finishes its
   destructive cleanup, the mod creates its local Account and enters Lobby.
3. The stock training settings window opens immediately. Its Description field
   is the editable `LAN SERVER: host:port` endpoint, and its map list contains
   locally installed standard maps while the connection is pending.
   This reuses only the stock window as a local settings/map picker; it is not
   a retail training room and does not use the original prebattle service.
4. Choose a map and activate the window's normal primary button. The endpoint
   is saved, connection failures remain visible and retryable, and the server's
   map pool is checked before the start request is sent.
5. The server fills vacant slots with bots and starts the same round for every
   waiting client.
6. A team-elimination result freezes the round. After three seconds the server
   returns connected players to the waiting room and the stock picker opens for
   the next round.
7. Returning to the garage during a round retires that player, transfers bot
   authority when necessary and retains the LAN connection until the server's
   next waiting-room barrier.

No F12, `0`, or other battle-start hotkey is used. Setting `enabled` to `false`
disables this port; it does not select a second or legacy AI path.

Only arena definitions whose gameplay name is `ctf` are exposed. In this
client, `ctf` is the internal name of standard battle, not a separate
capture-the-flag variant. Assault and encounter definitions are excluded.

## Runtime ownership

The Python 3 server owns the room, teams and slots, canonical health, accepted
shot events, active-round retirement, elimination result and round reset. It
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
matchmaking, reconnect recovery, internet authentication, anti-cheat and full
module/crew replication are outside this release.

Protocol v5 messages are now strictly round-scoped. Keep the client package
and `lan_battle_server.py` from the same checkout; this client rejects an older
server before entering the waiting room when its welcome lacks the build tag.
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

The same endpoint is editable in the native training settings window. The
server supplies the selected map and spawn. The release does not write a
capability trace or vertical-slice status file; normal failures are reported in
`python.log` without verbose per-frame logging.

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
org.peng.offline_lan_0922_0.3.6.wotmod
org.peng.offline_lan_0922_0.3.6.wotmod.sha256
WoT-0.9.22-LAN-Client-<package hash>/
WoT-0.9.22-LAN-Client-<package hash>.zip
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
