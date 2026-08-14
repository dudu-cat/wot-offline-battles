# World of Tanks Offline Battles

An unofficial compatibility project for playing standard battles with bots in
two legacy Windows clients:

| Port | Supported client | Play modes |
| --- | --- | --- |
| [`0.8.2`](0.8.2/) | World of Tanks 0.8.2 | Server-free single-player or trusted LAN |
| [`0.9.22`](0.9.22/) | Chinese HD client 0.9.22.0.1 #1513 | Server-backed single-player or trusted LAN |

The current repository head is a pre-release test candidate. A formal release
will follow validation on two Windows PCs.

The original client still provides the maps, vehicles, rendering, HUD, physics
and other proprietary runtime data. This repository provides the client mods,
bot and battle logic, a small LAN coordinator, build tools and tests. It does
not include the game client or its assets, and it is not a replacement for the
original BigWorld server.

## What makes this repository different

The 0.8.2 port extends the offline-hangar and early battle work descended from
[`mod_offhangar_legacy`](https://github.com/SigmaTel71/mod_offhangar_legacy).
The 0.9.22 port uses
[`wot-offline-server`](https://github.com/the-tuxedo-cat/wot-offline-server)
as a limited map-picker and entity-lifecycle reference. Compared with those
projects, this repository adds:

- **An end-to-end battle implementation.** Both ports implement 15-versus-15
  standard-battle flows with movement, aiming, firing, damage, base capture,
  battle results and repeated rounds.
- **Two separate, version-locked ports.** The 0.8.2 and 0.9.22 clients have
  different embedded Python runtimes and native contracts; each has its own
  implementation rather than sharing transplanted bytecode.
- **Tactical bots and trusted-LAN play.** Bots use vehicle roles, shared
  contacts, terrain-aware navigation and combat positioning. The 0.8.2 port
  includes validated routes and prebaked navigation data for all 33 stock maps
  and can also run without a server. Both ports coordinate shared LAN battles.

See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for exact project
lineage and licensing.

## Installation

You must supply your own compatible Windows client. Close the game before
copying files, and use the client and server from the same repository revision.

### World of Tanks 0.8.2

1. Delete or move aside the existing `<game root>\res_mods\0.8.2` directory.
2. From this repository, run:

   ```bat
   0.8.2\refresh_client.bat "C:\Games\World_of_Tanks_0.8.2"
   ```

   Alternatively, copy `0.8.2/scripts/` and `0.8.2/gui/` into
   `<game root>\res_mods\0.8.2\`.
3. Start the game. A successful installation goes directly to the offline
   garage; select a tank and click **Battle!** for a local battle.

For LAN play, install Python 3 on one computer and double-click
`0.8.2\RUN_SERVER.bat`. On every client, open **LAN SETTINGS**, enter that
computer's LAN address and port `28782`, enable LAN Battle, then click
**Battle!**. The waiting-room host chooses a map and starts the battle.

The current 0.8.2 native-physics build accepts only its pinned executable. See
[`0.8.2/START_HERE.txt`](0.8.2/START_HERE.txt) if it does not load.

### World of Tanks 0.9.22.0.1 #1513

1. Use the exact frozen Chinese HD `0.9.22.0.1 #1513` client.
2. Obtain the matching client ZIP and extract it directly into the game root.
   The archive already contains the correct `mods` layout. Before the formal
   release, this ZIP is supplied as a separate test artifact; it is not stored
   in the source repository.
3. Install Python 3 on the computer that will host the battle, then run from
   this repository:

   ```bat
   py -3 0.9.22\server\lan_battle_server.py --host 0.0.0.0 --port 28782
   ```

4. Allow TCP port `28782` through the host firewall. In each client, use the
   stock **Battle!** flow and edit the `LAN SERVER: host:port` line in the
   native window to `<host LAN IP>:28782`. The first waiting player chooses a
   map and starts the shared round.

See [`0.9.22/INSTALL.txt`](0.9.22/INSTALL.txt) for troubleshooting and the
exact package boundary.

Project code is distributed under [`GPL-3.0`](LICENSE). World of Tanks and its
assets are not included; this project is not affiliated with or endorsed by
Wargaming.
