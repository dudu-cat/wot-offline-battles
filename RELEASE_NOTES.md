# World of Tanks Offline / LAN Battles 0.6.1

This release is built for and supported on World of Tanks 0.9.22.0.1 #1513
(Chinese HD).

Download `WoT-Offline-Battles-Launcher-Windows.zip`, extract the whole folder,
and run `WoT-Offline-Battles-Launcher.exe`.

## Stability and recovery

- Compatible 0.9.22 clients and simulation workers now recover from transient
  stalls, duplicate or reordered updates, queue pressure, and timing drift
  without unnecessarily ending the battle. Identity, round, roster, and
  message-shape boundaries remain enforced.
- Rapid-fire projectile, muzzle, impact, and terrain visuals are bounded and
  fail independently. Visual overload no longer interrupts authoritative
  shots, hits, damage, statistics, or results.
- Clean victories, draws, garage returns, and intentional worker shutdowns are
  no longer reported as crashes, while genuine client failures still produce
  diagnostic reports.
- Fallen-tree foliage refreshes now stop before querying unloaded chunks or
  animator bodies that have already disappeared, closing a hidden-worker
  native crash path during chunk streaming.
- The launcher and bundled map-data loaders accept structurally compatible
  deployed 0.9.22 data and recover when optional version metadata is missing
  or unreadable.

## Battle behavior

- Destroying an unspotted enemy now finalizes its death immediately. Its wreck
  remains visible independently of spotting in normal, sniper, artillery, and
  overhead views, subject only to normal rendering distance and view culling.
- Bots align their hull before committing to a route, reducing base spinning
  and stop-start movement. Discovered artillery is now a priority target
  without making every bot focus the same vehicle.
- The 0.9.22 bot roster now draws from 40 Chinese player names styled for the
  2017 client era.
- Authoritative artillery stun is enabled and presented through the stock
  0.9.22 client interface.

## Ammunition, vehicles, and the editor

- Repeated or double ammunition switches no longer leave the gun permanently
  reloading. Magazine vehicles keep firing the current clip after selecting
  the next shell type, and Loader Intuition publishes the committed shell even
  if its HUD notification fails.
- Swedish Siege mode now applies hydraulic hull aiming without calling an
  unsafe native physics path, restoring aiming in engineering mode.
- The vehicle editor restores hidden-but-playable vehicles, accepts decimal
  values for editable numeric fields stored as integers, labels elevation,
  depression, gun elevation speed, and turret traverse speed correctly, and
  rebuilds its own drifted overlays from saved logical edits.
- Garage item prices now use #1513-compatible integer values, and both
  standard and bond equipment can be removed free of charge through the
  appropriate shop fields.

## Credits

Thanks to 秋风扫落叶 for the bot behavior review and improvement suggestions.

When reporting a reproducible problem, please include the launcher activity
text, `server.log` beside the launcher, and the game's `python.log`, together
with the selected client build and map.

This project is released under the GNU GPL v3. It contains no World of Tanks
content and is not affiliated with Wargaming. Run the LAN server only on a
network you trust.
