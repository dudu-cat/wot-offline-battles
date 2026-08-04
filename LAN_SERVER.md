# LAN battle MVP

This repository contains an optional LAN battle path for the 0.8.2 offline client.
The normal offline mode remains available. The server-backed path also works
with one connected player and reuses the existing garage, map loading, tank
models, HUD and local driving code. A separate Python 3 process owns the shared
roster, health, deaths, battle rules, global bot orders and the relay for
human/bot movement, firing and client-resolved armor impacts.

## Start the server

Run this on the machine that hosts the battle:

```bash
python3 lan_battle_server.py --host 0.0.0.0 --port 28782
```

With no `--map` argument, the server chooses the map initially highlighted in
the waiting room. `--map 04_himmelsdorf` changes that initial selection; any
waiting player can still choose another stock map before clicking start.

Allow TCP port `28782` through the host firewall if clients are on another
machine. Use the host machine's LAN address, for example `192.168.1.20`, in
the client configuration.

## Enable the client path

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
A client that connects after `BATTLE START` receives a `LATE JOIN` message and
enters the current round on the same map.

There is no independent client-side LAN countdown. A failed LAN connection
does not silently fall back to a local random battle. Use the queue screen's
cancel button to leave the waiting room.

With normal logging settings, each client writes these milestones to
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
protocol: verify LAN mode is ON, the configured IP, Parallels network mode and
the server firewall. If it prints a TCP connection followed by `protocol
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
process and a server-authoritative `battle_start` barrier. It synchronizes
player identity, selected vehicle, opposing team, position, hull/turret aim,
shell selection, firing, impact outcome, health and death.
The firing client reuses the existing 0.8.2 map collision, shell and armor
calculation and reports that result; the server validates and owns the shared
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
advances uploaded routes, chooses combat mode and shell, and emits monotonic
revisioned bot orders. The authority client executes those orders with the real
map collision, local driver, armor and shell systems, then publishes canonical
pose/fire/HP state. Every client therefore renders the same population and
combat result. If the authority disconnects, the server elects the next player
and preserves the latest shared state. The same authority publishes
base-capture progress, capture interruption and the final winner/reason for
capture, team elimination or timer expiry. The server remains the shared
source of truth for HP and the final battle result.

### Bot planner portability boundary

`server_bot_ai.py` is intentionally pure data: it imports no BigWorld or client
module. Its inputs and outputs are JSON-compatible dictionaries carried by
protocol v5:

- `bot_manifest`: identity, team, vehicle profile, shell profiles and sparse
  route waypoints;
- `bot_observation`: authority-reported visible/hidden contacts with explicit
  `target_kind` and shared coordinates;
- `bot_state`: the latest authority-executed bot pose, health and fire state;
- `bot_orders`: route index, movement/aim points, target reservation, combat
  mode, throttle override and shell index, guarded by `bot_order_revision`.

This boundary is suitable for replacing the Python server planner with Go
without moving proprietary map queries or BigWorld entity control off the
client. A Go implementation must preserve non-omniscient contact handling,
stable orders between revisions and target identity as `(target_kind, id)`.

This is an implementation checkpoint, not a complete replacement for the
retail battle server. One elected client still owns map collision, short-range
driving, shell/armor resolution and the original client physics. Authority
failover preserves server-side contacts, routes and orders but cannot preserve
every client-local recovery or reload timer. It does not provide the retail
server's authoritative physics, complete cross-client module/crew state,
reconnection recovery, anti-cheat, NAT traversal or internet-safe
authentication. Keep it on a trusted LAN while testing.

## Disable or roll back

Set `network_mode` back to `false` to return to the original offline path. The
Git baseline before the LAN changes is:

```text
d58ed2e chore: baseline offline battles release
```
