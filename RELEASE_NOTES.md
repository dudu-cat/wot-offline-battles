# World of Tanks Offline / LAN Battles 0.6.0 Alpha 1

This prerelease is intended for a limited round of testing. It includes both
supported clients:

- World of Tanks 0.8.2 #335
- World of Tanks 0.9.22.0.1 #1513 (Chinese HD)

Download `WoT-Offline-Battles-Launcher-Windows.zip`, extract the whole folder,
and run `WoT-Offline-Battles-Launcher.exe`. The changes below are primarily for
the 0.9.22 client.

## Hidden single-player simulation

- The 0.9.22 single-player mode now runs bot simulation in a separate hidden
  game client. The visible client renders the battle without also owning the
  complete bot update loop.
- The launcher starts the local server, hidden simulation worker and visible
  game in the required order, then closes the hidden processes when the game
  exits.
- The worker runs without a visible window, intro video or game audio.
- Snapshot timing and interpolation were reworked to remove the initial
  several-second stall, large corrections and most recurring bot jitter.
- Battle startup now waits for the real roster to become ready. Joining bots no
  longer restart a second 15-second countdown.

## Launcher and user data

- The main flow is split into clear **Single player** and **Online** tabs.
  Hosting, joining, and explicitly starting or stopping a persistent LAN server
  no longer overlap.
- The launcher UI supports English and Chinese, follows the operating-system
  language by default, and falls back to English.
- Only one launcher instance can run at a time.
- Client, server and hidden-worker lifecycle detection is more reliable,
  including a game closed from its taskbar or after a failed launch.
- Server logs are written beside the launcher and kept to a bounded size. Noisy
  routine messages were removed from both client and server logs.
- Repair and reset actions recover broken offline configuration without
  deleting unrelated mods or the normal game profile.
- Offline configuration, garage state, results and vehicle profiles now live in
  a user-owned application-data directory. Existing release data is migrated
  automatically and retained as a fallback if migration cannot complete.

## Vehicle profiles

- A single-player vehicle-data editor is included in the launcher. Named
  profiles can change safe numeric values exposed by the installed 0.9.22
  vehicle data.
- Profiles store logical edits and rebuild launcher-owned `res_mods` overlays
  from the stock package. They never modify `scripts.pkg` or unrelated mods.
- Profile application, restoration and crash recovery are transactional, and
  the original vehicle data is restored when a modified single-player session
  ends.

## Battle behavior

- Team size is configurable from one to fifteen tanks per side.
- Bot movement cadence, path dispersion and start-of-round publication are
  smoother. Bots no longer spend the opening seconds visually frozen and then
  jump ahead.
- Bots avoid firing through friendly vehicles and reposition when a teammate
  blocks the firing line.
- Ramming uses vehicle mass and one damage event per continuous contact.
  Fire, module, crew and drowning damage now remain synchronized with the HUD.
- Terrain resistance, engine power, crew skills and fitted equipment feed the
  movement and view-range calculations used in battle and during the countdown.
- Turretless vehicle aiming follows the stock movement, sniper-mode and
  handbrake rules. Postmortem teammate camera switching works again.
- Switching shell types now resets both the real reload and every reload
  progress indicator.
- Fixed inactive control-point presentation, including the extra neutral
  capture circle on Malinovka, and reapplied the exact #1513 native gameplay
  visibility mask before the map is shown.
- Destructible presentation is prepared during loading, avoiding the first-hit
  collision pause without delaying each vehicle separately.

## Round lifecycle and results

- Repeated battles keep the hidden worker and server synchronized instead of
  stalling at login, returning early to the garage or rejecting a valid map.
- Natural battle endings now produce durable results, rewards, team rows and a
  garage notification that opens the stock battle-results window.
- Result delivery is idempotent across reconnects and client restarts, and
  abandoned bot-only battles are resolved so the single local server does not
  block the next player.
- Battle outcome logic chooses a winner from the final situation when time
  remains, while genuine time-limit and simultaneous-destruction cases can
  still draw.

## Testing notes

This is an alpha build. When reporting a reproducible problem, please include
the launcher activity text, `server.log` beside the launcher, and the game's
`python.log`, together with the selected client build and map.

This project is released under the GNU GPL v3. It contains no World of Tanks
content and is not affiliated with Wargaming. Run the LAN server only on a
network you trust.
