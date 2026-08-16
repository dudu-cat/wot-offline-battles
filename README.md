# World of Tanks Offline Battles

Play standard battles with bots in two legacy Windows clients, alone or with
friends on a LAN:

| Port | Supported client |
| --- | --- |
| [`0.8.2`](0.8.2/) | World of Tanks 0.8.2 |
| [`0.9.22`](0.9.22/) | Chinese HD client 0.9.22.0.1 #1513 |

You supply your own client. The client still provides the maps, vehicles,
rendering, HUD and physics. This repository provides the client mod, the bot
and battle logic, a small LAN server and a launcher.

## Play

1. Download `WoT-Offline-Battles-Launcher-Windows.zip` from the releases,
   unpack it, and start `WoT-Offline-Battles-Launcher.exe`.
2. Select your World of Tanks folder. The launcher recognizes the client,
   removes any older mod files and installs the matching mod.
3. Select a mode:
   - **Single player**: you play alone against bots. The launcher runs the
     server for you; every battle is a server battle in both clients.
   - **Host a LAN battle**: other players join this PC. The launcher starts the
     server and prints the address to give them.
   - **Join a LAN battle**: type the host's address, for example
     `192.168.1.20`.
4. Click **Start game**. In the garage, select a tank and click **Battle!**.
   Everyone lands in the LAN waiting room; the host picks a map and clicks
   **START BATTLE**.

When you host, approve the UAC prompt that opens TCP 28782 for the launcher.
Run the server only on a network you trust.

## What is in the battle

- 15-versus-15 spawning, countdown, capture, elimination and timeout, then a
  clean next round.
- Same-era gunnery: shell flight time and gravity, dispersion, penetration by
  range, normalization, ricochet, overmatch, spaced armour, HE splash, ramming,
  module and crew damage, fires and repairs.
- Spotting with view range, camouflage, movement, firing, foliage, line of
  sight and last-known positions.
- Bots that use map geometry, terrain, water, firing lanes, team strength and
  shared contacts to route, take cover, pick targets and choose ammunition,
  including SPG arcs. Navigation and foliage data ship for all 33 supported
  0.8.2 maps and all 41 supported 0.9.22 maps.
- A LAN match is one shared battle: lineups, countdown, orders, projectiles,
  health, critical damage, destructibles, capture and results stay
  synchronized, and the match survives the loss of the current bot controller.

This is a reconstruction from the frozen clients and same-era mechanics, not
Wargaming's retail server. LAN play assumes trusted clients. Native rendering,
physics and frame pacing can only be judged in the Windows client.

## Build it yourself

```bash
# 0.8.2 client package
python3 0.8.2/tools/package_native_experiment.py --output-dir dist --version 1.8.58
# 0.9.22 client package, with CPython 2.7
python2.7 0.9.22/build_wotmod.py
# Windows launcher, after the 0.9.22 package exists
pwsh -NoProfile -File launcher/build_launcher.ps1
```

The launcher carries both LAN servers and both client mods. It writes the
server address into the file each port already reads at startup, installs the
mod, starts the game and stops the server when the game closes.

Tests:

```bash
cd 0.8.2 && python3 -m unittest discover -s tests
python3 -m unittest discover -s 0.9.22/tests
cd launcher && python3 -m unittest discover -s tests
```

Project code is distributed under [`GPL-3.0`](LICENSE). World of Tanks and its
assets are not included; this project is not affiliated with or endorsed by
Wargaming. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for lineage
and bundled runtimes.
