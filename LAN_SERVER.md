# LAN battle server

This repository contains version-matched Python 3 LAN server entries for the
0.8.2 offline client and the pinned Chinese 0.9.22.0.1 `#1513` port. A
server-backed battle works with one connected player. The server owns the room,
teams and slots, canonical health, deaths, accepted shot events and round
phase. Clients retain the proprietary map queries, entity presentation and
collision/armor work.

## Start the server

For the 0.9.22 port, run its version-local server on the machine that hosts the
battle:

```bash
python3 ports/0.9.22/server/lan_battle_server.py --host 0.0.0.0 --port 28782 --map server_random
```

The legacy 0.8.2 entry remains `python3 lan_battle_server.py ...`. Keep each
client on the server entry from the same version checkpoint.

`server_random` gives the room an initial random map.
`--map 04_himmelsdorf` changes that initial selection. In 0.8.2, a waiting
player can replace it through the custom queue panel. In 0.9.22, only the
elected room host can replace it through the native map picker.

Allow TCP port `28782` through the host firewall if clients are on another
machine. Use the host machine's LAN address, for example `192.168.1.20`, in
the client configuration.

## Install a client

### World of Tanks 0.9.22.0.1 #1513

Use the hash-named folder built under `ports/0.9.22/dist/`, or the corresponding
copy-ready deliverable. Close the game, remove older
`org.peng.offline_lan_0922_*.wotmod` files, and merge its `mods` directory into
the client root. Configure `mods/configs/offline_lan_0922/config.json` with the
server's LAN address. Click the stock **Battle!** button to connect and join
the waiting room. If the client is still connecting or retrying, click
**Battle!** again to open the native settings window explicitly and edit the
endpoint. The first accepted player becomes room host and receives that window
as a local map picker. Later players remain in the garage; once the room is
waiting, only the host selects the map and uses the window's primary action.
See `ports/0.9.22/INSTALL.txt` for details.

The 0.9.22 port always uses this server when enabled, including for one player.
It does not have an old-AI or local-only fallback.

### World of Tanks 0.8.2

Close the game and refresh each Windows client from this repository:

```bat
refresh_client.bat "C:\Games\World_of_Tanks_0.8.2"
```

This also removes `mod_offhangar.pyc` from the installed mod. The 0.8.2
`CameraNode.pyc` loader scans bytecode before source, so an old copy of that
one file would silently hide all newer LAN code. Do not delete
`scripts/client/CameraNode.pyc`; it is the loader itself.

In the offline hangar, click the visible `LAN SETTINGS` entry in the
upper-right. The in-game panel lets you enter the server IP and TCP port,
toggle LAN mode, and save with `Enter`. `F11` remains available as a keyboard
fallback. The client does not need a separate Python installation: the network
module runs inside the embedded Python 2 runtime shipped with the 0.8.2 client.

## Enter one battle together

### World of Tanks 0.9.22.0.1 #1513

1. Start the server and leave its terminal visible.
2. Set every client's `mods/configs/offline_lan_0922/config.json` to the same
   server IP and port.
3. Click **Battle!** on every client. Each click joins the shared LAN waiting
   room; it does not contact retail matchmaking or create a retail training
   room.
4. If a client is still connecting or retrying, click **Battle!** again to
   open the native settings window and edit its endpoint. This pre-welcome
   surface does not grant room-host authority.
5. Confirm that the server printed one `JOIN` line for every client.
6. The first waiting player is the room host and receives the native
   training-settings map picker. Guests remain in the garage and wait.
7. The host chooses a standard map and uses the picker's primary action. The
   server prints `BATTLE START` and broadcasts that map and roster to every
   waiting client. A single connected player may also start.

If the waiting host disconnects, the server transfers ownership to the lowest
connected player id and that client receives the picker. Guests cannot change
the selected map or start the round. Closing the host picker leaves it closed;
the host can reopen it by clicking **Battle!** again.

### World of Tanks 0.8.2

1. Start the server and leave its terminal visible.
2. On every client, enable LAN mode with the same server IP and port.
3. Click `Battle!` on every client. The queue screen opens only after the
   server accepts that client and sends `welcome`.
4. Confirm that the server printed a `JOIN` line for every client.
   The queue screen's player count, vehicle classes and tiers come from the
   real server roster and update whenever another client joins or leaves.
5. In the clickable waiting-room panel, choose a map and click `START BATTLE`.
   The server prints `BATTLE START` and broadcasts that map and roster to every
   client currently in the waiting room. A single connected player may also
   start.

The start button appears after the server accepts the client and sends the
`welcome` message.
A connection made after `BATTLE START` is rejected until the round returns to
the waiting room. This keeps the manifest at exactly 15 occupied human or bot
slots per team.

There is no independent client-side LAN countdown. A failed LAN connection
does not silently fall back to a local random battle. Use the queue screen's
cancel button to leave the waiting room.

With normal 0.8.2 logging settings, each client writes these milestones to
`python.log`:

```text
LAN connecting to 192.168.1.20:28782
LAN TCP connected to 192.168.1.20:28782
LAN hello sent (protocol 5)
LAN welcome id=1 name=Player-158 vehicle=china:Type_59 team=1 slot=0 map=... phase=waiting
LAN JOIN confirmed; queue screen is now server-backed
LAN queue UI updated: 2 connected player(s)
LAN waiting room: 2 player(s); choose a map and click START BATTLE
LAN BATTLE START received: map=... players=2 delay=0.75
LAN bot authority: player_id=1 local=True
LAN bot manifest received: 30 bot(s)
```

If the server prints no `TCP connection` line, the problem is before the
protocol. For 0.9.22, verify that the client clicked **Battle!** and that its
`config.json` contains the correct IP and port; clicking **Battle!** again while
it retries opens the native endpoint editor. For 0.8.2, verify that LAN mode is
ON. For either version, also check Parallels network mode and the server
firewall. If the server prints a TCP connection followed by `protocol
mismatch`, the client and server packages are from different builds.

In battle, opposing LAN humans use the same local 50 m proximity spot,
view-range/terrain line-of-sight check, allied vision and five-second spot
memory as NPC opponents. Allied humans remain visible. When a human dies, the
server freezes that player's final pose and the client rebinds the marker proxy
to the grounded wreck so late input packets cannot separate the two.

Human input, bot state and server snapshots run at 30 Hz. Between packets the
client advances remote humans and shared bots every render frame with
exponential interpolation and up to 50 ms of bounded velocity prediction.
Corrections larger than 25 metres snap immediately. The stock battle HUD's
ping and connection indicator are fed by the measured LAN round-trip time and
snapshot freshness instead of fixed placeholder values.

If you prefer to prepare a config file manually, use these values:

```json
{
    "network_mode": true,
    "network_server_host": "192.168.1.20",
    "network_server_port": 28782,
    "network_map_name": "server_random"
}
```

The actual file contains the complete existing configuration; merge these
values into it rather than replacing the file. The server supplies the valid
map list; the clicked waiting-room selection is authoritative for the round.

## Current protocol boundary

The server speaks a small newline-delimited JSON protocol, not the original
Wargaming/BigWorld server protocol. Protocol v5 has one waiting room per server
process and a server-authoritative `battle_start` barrier. The first client in
an empty room pins that room to either the legacy `wot-0.8.2` profile or the
exact `wot-0.9.22.0.1-cn-1513` profile. Existing 0.8.2 packages send no
`client_build` field and remain supported; the 0.9.22 package declares its
build explicitly. A different build is rejected until the room is empty. This
is required because the two clients use different coordinate frames, vehicle
names and installed map sets. The server advertises and validates the pinned
build's map pool (33 maps for 0.8.2, 42 for 0.9.22), so neither client can select
a map resource it does not have. Run a second server process on another port if
both builds must host rooms simultaneously. It synchronizes
player identity, selected vehicle, opposing team, position, hull/turret aim,
shell selection, firing, impact outcome, health and death.
The firing client reuses its local map collision, shell and armor calculation
and reports that result; the server validates and owns the shared
HP result. Damage caused by local bots, fire, drowning and collisions is
reported downward by the affected client so other players see the resulting
health and death state. Generic
`Defaultplayer` names become `Player-<IP suffix>` and receive a numeric suffix
if necessary. Remote tanks are rendered through the existing offline
mock-vehicle resource path. Vehicle movement still uses the existing client
physics and is relayed through the server; this trusted-LAN checkpoint is not
anti-cheat authoritative. Local garage data remains client-side.

At battle start the server elects one connected client as map-simulation/rules
authority. That client chooses one exact bot manifest and uploads each tank's
vehicle profile plus the assigned standard-battle route. It reports only
contacts observed through client-side range and terrain line-of-sight checks.
The server retains last-known contacts, reserves targets across the team,
advances uploaded routes, shifts at most one adaptable tank toward a pressured
lane, chooses combat mode and shell, and emits monotonic revisioned bot orders.
For nearby visible contacts the authority also probes a bounded fan of
drivable, dry, low-slope cover and peek points. The server validates those
points against the bot's shared pose, scores them by role/personality, reserves
them across the team, and controls the approach/hold/peek/return cycle. The
authority client executes those orders with its real map collision, local
driver, armor and shell systems, then publishes canonical pose/fire/HP state.
Every client therefore renders the same population and combat result. If the
authority disconnects, the server elects the next player, preserves canonical
bot/HP/rules state, and clears the departed client's short-lived contacts and
cover probes until the new authority reports its own observations. The same
authority publishes
base-capture progress, capture interruption and the final winner/reason for
capture, team elimination or timer expiry. The server remains the shared
source of truth for HP and the final battle result.

The 0.9.22 authority now reports bounded visibility observations and consumes
the server planner's revisioned global `bot_orders` as macro targets. It still
owns BigWorld terrain queries and local collision/water/slope avoidance, and
falls back to its local planner when no server order is available. Its standard
battles end by team elimination and reset to the waiting room after three
seconds; base capture is not implemented there.

### Bot planner portability boundary

`ports/0.9.22/server/server_bot_ai.py` and the cover scorer it shares with the
port client are
intentionally pure data: neither imports BigWorld or touches a client runtime.
Their inputs and outputs are JSON-compatible dictionaries carried by protocol
v5:

- `bot_manifest`: identity, team, vehicle profile, shell profiles and sparse
  route waypoints;
- `bot_observation`: authority-reported visible/hidden contacts with explicit
  `target_kind` and shared coordinates, plus bounded client-probed cover and
  peek affordances for visible contacts;
- `bot_state`: the latest authority-executed bot pose, health and fire state;
- `bot_orders`: route index, movement/aim points, target and cover reservation,
  combat mode, throttle override and shell index, guarded by
  `bot_order_revision`. A snapshot includes the order list only when that
  revision is new for its recipient.

This boundary is suitable for replacing the Python server planner with Go
without moving proprietary map queries or BigWorld entity control off the
client. A Go implementation must preserve non-omniscient contact handling,
stable orders between revisions and target identity as `(target_kind, id)`.

This is an implementation checkpoint, not a complete replacement for the
retail battle server. One elected client still owns map collision, short-range
driving, shell/armor resolution and the original client physics. Authority
failover preserves canonical bot poses, HP, rules and uploaded routes, but
intentionally discards authority-derived contacts/cover probes and cannot
preserve every client-local recovery or reload timer. It does not provide the
retail server's authoritative physics, complete cross-client module/crew
state, reconnection recovery, anti-cheat, NAT traversal or internet-safe
authentication. Keep it on a trusted LAN while testing.

## Disable or roll back 0.8.2

Set `network_mode` back to `false` to return to the original offline path. The
Git baseline before the LAN changes is:

```text
d58ed2e chore: baseline offline battles release
```
