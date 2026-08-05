# World of Tanks 0.9.22.0.1 LAN/offline port

This directory contains the version-local client layer for the pinned Chinese
HD client:

- client version: `0.9.22.0.1 #1513`
- executable architecture: 32-bit x86
- embedded runtime: CPython 2.7.7
- release entry format: `mod_*.pyc`
- package format: Store-only ZIP-compatible `.wotmod`

Version `0.3.0` replaces the old compatibility slice. It is a server-backed
standard-battle implementation with a stock map picker, native Avatar and
Vehicle entities, a playable local vehicle, LAN state, damage, 15 vehicles per
team, tactical bots and repeatable rounds. The removed `vertical_slice.py`
runtime is not packaged as a fallback.

## User flow

1. Start `lan_battle_server.py`. A single connected client is supported.
2. Start the frozen client. The mod creates its local Account and enters Lobby.
3. The stock training settings window opens with the server's map list.
4. Choose a map and activate the window's normal primary button.
5. The server fills vacant slots with bots and starts the same round for every
   waiting client.
6. A team-elimination result freezes the round. After three seconds the server
   returns connected players to the waiting room and the stock picker opens for
   the next round.

No F12, `0`, or other battle-start hotkey is used. Setting `enabled` to `false`
disables this port; it does not select a second or legacy AI path.

Only arena definitions whose gameplay name is `ctf` are exposed. In this
client, `ctf` is the internal name of standard battle, not a separate
capture-the-flag variant. Assault and encounter definitions are excluded.

## Runtime ownership

The Python 3 server owns the room, teams and slots, canonical health, accepted
shot events, elimination result and round reset. It exposes a newline-delimited
JSON protocol rather than emulating the original BigWorld game server.

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
and `lan_battle_server.py` from the same checkout; an older v5 client or server
may reject the newer bot manifest without a visible protocol-version error.

## Configuration

On first load the client writes:

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

The server supplies the selected map and spawn. The release does not write a
capability trace or vertical-slice status file; normal failures are reported in
`python.log` without verbose per-frame logging.

## Build

The loader ignores source `.py` files, so release bytecode must be compiled by
CPython 2.7. Verify the exact client and build the package together:

```bash
./build_for_client.sh \
  ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD
```

The script uses local CPython 2.7 when available and otherwise the pinned
Docker build. Before compiling, it reads code objects directly from the pinned
client's `scripts.pkg`, rejects any mismatch in the 82 stock method signatures,
18 direct-consumer literals and 16 lifecycle code names used by this port,
verifies 11 exact `AccountCommands` constants, and runs the complete raw `serverSettings`
subscript inventory against the local producers. Signature checks include
`*args`/`**kwargs` flags at the stock picker boundary, rather than comparing
only positional argument counts. It then validates the package
version, exact source-to-PYC module manifest, CPython 2.7 magic, absence of
Python source or optimized/cache bytecode, CRC, unique archive members,
explicit directory entries and Store compression for every member.

Signature matching is only one gate. The tests also validate the exact
Vehicle/Avatar property and mailbox schemas, the Account/Lobby direct-key and
tuple contracts recorded in `tools/account_lobby_consumer_contract.json`, and
LAN round/message state transitions. A conventional static type checker cannot
replace these checks because BigWorld injects dynamic Entity properties and
mailboxes at runtime.

Outputs are written to `dist/`:

```text
org.peng.offline_lan_0922_0.3.0.wotmod
org.peng.offline_lan_0922_0.3.0.wotmod.sha256
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
