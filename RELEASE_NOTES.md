# World of Tanks Offline / LAN Battles 0.6.0 Alpha 2

This prerelease is intended for a limited round of testing. It supports only
World of Tanks 0.9.22.0.1 #1513 (Chinese HD).

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
- Periodic game-process checks use the Windows process API instead of launching
  `tasklist`, so they no longer flash a console over a full-screen battle.
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
- Each projectile freezes the fitted gun and shell values at launch. Damage,
  module damage, penetration and range falloff, caliber, shell kind and HE
  radius no longer fall back to a vehicle's stock gun on an authority peer.
- Penetration now uses the pinned client's P100/P500 slope, one random factor
  per shell, material flags, AP/APCR two- and three-caliber rules, APHE rules,
  and HEAT's 85-degree ricochet and spaced-armor loss.
- A shell can trace modules only within ten calibers of its first vehicle
  contact. Hidden module damage stays hidden above the 50-percent threshold;
  explicit repaired-yellow state, duplicate hit-box suppression, engine and
  fuel-tank fire rules, and the Deadeye bonus now persist across peers.
- HE direct hits and splash use a finite 45-degree internal cone instead of a
  solid full-vehicle ray. Tanks without a validated internal profile no longer
  receive a fabricated fallback module hit.
- Shot dispersion stays inside the visible aiming circle. Ground and wall hits
  use the stock surface effects, destructible props consume or stop shells as
  appropriate, and wrecks block both shells and outlines.
- A hit on an unspotted enemy remains presentation-silent: no commander voice,
  ribbon, floating damage, critical/kill cue or vehicle-impact effect leaks the
  hidden target, while authoritative damage and results still apply.
- Manual cassette reload, direct and splash critical-hit feedback, fire and
  ammunition-rack presentation, and the Expert damaged-device view work through
  the exact 0.9.22 client callbacks.
- Bot movement cadence, path dispersion and start-of-round publication are
  smoother. Bots no longer spend the opening seconds visually frozen and then
  jump ahead.
- Bots avoid firing through friendly vehicles and reposition when a teammate
  blocks the firing line.
- Ramming uses vehicle mass and one damage event per continuous contact.
  Fire, module, crew and drowning damage now remain synchronized with the HUD.
- Bots already in deep water can choose a bounded shallower escape corridor;
  if they remain beyond 1.6 metres for over 10 seconds, they drown like the
  player. For the local player, an already-overturned pose publishes the stock
  warning and follows the 30-second destruction path without crediting a false
  attacker. The copied movement model does not generate rigid-body rollovers.
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

- An invader keeps capturing while a defender is inside the same base; capture
  resets only when the invader leaves, dies, or takes qualifying damage.
- Damage and kill totals are no longer counted once in both vehicle and avatar
  result blocks. The battle HUD also receives damage, assistance and blocked
  totals, and the detailed result view receives per-target interactions.
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
