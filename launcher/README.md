# Desktop launcher

The launcher prepares one battle before the client starts. It writes the LAN
server address that the installed port already reads at startup, runs the LAN
server for a host, starts `WorldOfTanks.exe`, and stops that server when the
game closes. In the game the player then only clicks the battle button.

One launcher serves both ports. It never opens the client's connection: the
client still connects when the player clicks the battle button.

## What the launcher writes

| Port | File | Keys |
| --- | --- | --- |
| `0.8.2` | `<game root>\offhangar_user\config.json` | `network_mode`, `network_server_host`, `network_server_port`, `network_map_name`, `nickname` |
| `0.9.22` | `<game root>\mods\configs\offline_lan_0922\server_endpoint.json` | `host`, `port` |
| `0.9.22` | `<game root>\mods\configs\offline_lan_0922\config.json` | `name`, only when it already exists |

Both ports read these files once, while the client starts. The launcher keeps
every other key in those files unchanged.

## Modes

| Mode | 0.8.2 | 0.9.22 |
| --- | --- | --- |
| Single player | No server | Local server, because this port is server-backed |
| Host a LAN battle | Local server on `0.0.0.0:28782` | Local server on `0.0.0.0:28782` |
| Join a LAN battle | No server; the typed address is written | No server; the typed address is written |

## Run from this checkout

```bash
python3 launcher/wot_launcher.py
```

The same entry point runs one bundled server in a child process:

```bash
python3 launcher/wot_launcher.py --serve 0.8.2
```

## Build the Windows executable

```powershell
pwsh -NoProfile -File launcher/build_launcher.ps1
```

The build first stages the server payload with `stage_payload.py`. The 0.8.2
navigation graphs stay out of that payload; the launcher points the 0.8.2
server at the graphs installed with the client through
`WOT_OFFLINE_NAVGRAPH_DIR`, so the server and the client always read the same
graph files.

## Tests

```bash
cd launcher && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p "test_*.py"
```

The suite covers pure logic only. Running a real server, launching the client
and the packaged executable require the exact Windows clients.
