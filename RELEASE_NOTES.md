# World of Tanks Offline / LAN Battles 0.6.1

This release is built for and supported on World of Tanks 0.9.22.0.1 #1513
(Chinese HD).

Download `WoT-Offline-Battles-Launcher-Windows.zip`, extract the whole folder,
and run `WoT-Offline-Battles-Launcher.exe`.

## Stability and recovery

- Every admitted current-round fire intent now reaches an idempotent terminal
  result even when the worker cannot launch it. The client clears its native
  shot wait without consuming ammunition or starting reload, and later shots
  can continue from the acknowledged sequence.
- Future Bot publications are validated as one transaction before they can
  rebase simulation clocks or refresh worker liveness. A malformed publication
  can no longer keep a frozen authority alive by advancing only its timestamps.
- A complete Bot checkpoint that rebases the source clock after a coalesced or
  delayed interval now establishes the current firing and ammunition baseline
  without inventing shots for the missing interval. Later Bot shots continue
  normally instead of freezing after the rebased `fire_seq` gap.
- Worker Bot manifests remain immutable and pending until the LAN transport
  accepts them. Temporary backpressure retries the same lineup; a fixed startup
  deadline retires the failed worker and terminates the loading round instead of
  leaving the room permanently half-started.
- The server tick thread is supervised. One uncertain active tick produces an
  explicit no-settlement terminal result and the ordinary round reset; repeated
  failures close the listener instead of leaving a TCP server that answers while
  simulation is dead.
- Compatible 0.9.22 clients and simulation workers now recover from transient
  stalls, duplicate or reordered updates, queue pressure, and timing drift
  without unnecessarily ending the battle. Identity, round, roster, and
  message-shape boundaries remain enforced.
- Malformed snapshot/event rows and isolated per-line handler exceptions are
  contained to that row. Both player and worker transports continue with the
  following message instead of escalating that recoverable row into a client
  stop, disconnect, or room-wide system-error result.
- Projectile progress now converges each cumulative cursor independently, so
  a delayed or skipped worker snapshot no longer rejects the whole active
  projectile batch. Valid destruction receipts in a stale cursor are retained.
- Visible projectile movers now start from the worker-confirmed collision
  cursor instead of extrapolating ahead from wall-clock time. This addresses
  the reported case where the tracer appeared to pass a tank or strike the
  terrain before the authoritative hit arrived a few frames later.
- Current-round input, water, and destruction messages already queued when a
  vehicle dies or a battle ends are accepted as terminal no-ops. One stale
  player observation no longer discards unrelated live observations in the
  same trusted-worker batch.
- Native ramming receipts tolerate one bounded presentation-frame difference
  between the collision callback and copied vehicle pose, while remote contact
  points, invalid normals, identities, and physical values remain rejected.
- Rapid-fire projectile, muzzle, impact, and terrain visuals are bounded and
  fail independently. Visual overload no longer interrupts authoritative
  shots, hits, damage, statistics, or results.
- Clean victories, draws, garage returns, and intentional worker shutdowns are
  no longer reported as crashes, while genuine client failures still produce
  diagnostic reports.
- The current battle receipt is queued before terminal events and the final
  snapshot tear down the battle view. Results therefore settle on the garage
  return instead of waiting for the next press of the Battle button, while
  older unacknowledged receipts retain their normal reconnect order.
- An unreadable durable battle receipt is acknowledged and skipped instead of
  repeatedly stopping the same account on every reconnect. A structurally
  complete receipt is accepted even when its informational protocol label is
  stale.
- Fallen-tree foliage refreshes now stop before querying unloaded chunks or
  animator bodies that have already disappeared, closing a hidden-worker
  native crash path during chunk streaming.
- Track repair reports now converge when a repair kit or server-side repair
  has already advanced the canonical module state, instead of leaving the
  client in an endless repair-progress retry.
- The launcher and bundled map-data loaders accept structurally compatible
  deployed 0.9.22 data and recover when optional version metadata is missing
  or unreadable. Runtime navigation loads and validates the selected graph
  directly; an optional batch manifest can no longer make a valid map fail.

## Battle behavior

- Direct vehicle hits now enter the stock #1513 damage-sticker chain and leave
  persistent pierced or resisted marks on the correct frozen hull component.
  Ricochets retain their distinct stock impact effect and use the available
  resisted sticker; splash damage does not create a direct-hit decal.
- A lethal hit's final crew-state refresh now retains the real attacker, so
  player damage remains orange instead of being overwritten by generic red.
  Ally, blind-shot, and all-crew-knocked-out paths keep their original category.
- Destroying an unspotted enemy now finalizes its death immediately. Its wreck
  remains visible independently of spotting in normal, sniper, artillery, and
  overhead views, subject only to normal rendering distance and view culling.
- Bot overlap recovery now tests the real current hull pose rather than rotating
  its collision box to a future route heading. Failed steering directions use a
  circular cache, so equivalent angles around the +/-pi seam share one penalty
  instead of reopening the opposite turn every time `atan2` wraps.
- Pending paths no longer become false failures at water or map edges. A chosen
  shallow ford remains authorized across control steps, repeated completed hard
  contacts penalize only that Bot's failed segment, and out-of-bounds goals are
  rejected before edge-cell rounding can send a vehicle beyond the red line.
- Malinovka assigns fewer vehicles to the single-egress western lake road and
  moves the released slots to the broad central and eastern routes without
  changing the full-team formation size.
- Route following now looks ahead by speed, begins terminal braking from the
  copied vehicle stopping distance, and counts translation or steadily
  converging heading as progress. Alternating in-place turns therefore enter a
  bounded recovery instead of keeping a visibly stuck Bot alive forever.
- Bots align their hull before committing to a route, reducing base spinning
  and stop-start movement. Discovered artillery is now a priority target
  without making every bot focus the same vehicle.
- Direct goals, A* fallbacks, path smoothing, and local steering now share the
  same shallow-water policy. Bots prefer a dry route, use only an A*-selected
  shallow crossing when unavoidable, and can still drive out after entering
  water.
- Heavy tanks keep moving through ordinary side turns and reserve stationary
  pivots for targets behind the hull. Final Bot movement, tank pushes, and
  rotation are constrained by the complete chassis at the official map edge.
- Target movement leases survive one incomplete firing-lane sample while fire
  authorization still fails closed. Cover approaches, peeks, returns, support
  holds, and retreats now have progress or arrival terminals; unreachable cover
  is temporarily retired, and support vehicles advance until the target is
  inside their real firing range.
- Engagement distance uses a small hysteresis band and dwell time, while stable
  support, cover, and retreat holds retain one hull-facing anchor as the target
  moves. This reduces the repeated forward/reverse and left/right indecision
  seen near thresholds and cover edges.
- Player driving, collision deflection, slope sliding, airborne carry, tank
  pushes, and pivoting now use the same complete-chassis map-edge constraint.
  A stale out-of-bounds pose can drive inward but cannot drift farther out.
- The 0.9.22 bot roster now draws from 142 varied Chinese display names across
  period, regional, poetic, everyday, and playful styles. Names are complete
  identities and no longer all receive the same numeric-suffix format.
- Authoritative artillery stun is enabled and presented through the stock
  0.9.22 client interface.

## Performance

- Full-team tactical synthesis and expensive firing-lane and cover scans run at
  1 Hz. The hidden worker now advances Bot control and copied motion on a fixed
  10 Hz clock independent of render FPS, keeps the last valid movement command
  between decisions, and limits a slow render callback to two roster catch-up
  steps while retaining the remaining elapsed debt. Projectile progression,
  admitted shot terminals, and ordered one-shot events remain on their own
  precise clocks; crossed burst edges keep their exact logical launch times.
- A* searches use deterministic elapsed-time expansion credit with a bounded
  per-frame budget and round-robin fairness. Machine load no longer changes a
  route merely because the former 2.5 ms CPU wall-clock cutoff expired, and one
  slow frame cannot concentrate an unlimited whole-roster search backlog.
- Repeated native muzzle reads and critical-module parsing are reused only
  within their safe authority tick, reducing worker-side Python and native
  boundary work without putting private cache state on the wire.

## Ammunition, vehicles, and the editor

- Repeated or double ammunition switches no longer leave the gun permanently
  reloading. Magazine vehicles keep firing the current clip after selecting
  the next shell type, and Loader Intuition publishes the committed shell even
  if its HUD notification fails.
- Swedish Siege mode now keeps the hydraulic hull, visible barrel, reticle,
  and fired ray on one copied pose, uses the active gun's real pitch limits,
  and recenters safely when leaving engineering mode without calling an unsafe
  native physics path. Exiting Siege also releases the hydraulic pose after
  #1513 restores the ordinary travel descriptor, so vertical gun movement is
  no longer left locked in travel mode.
- If a complete saved crew belongs to the wrong nation, the exact #1513
  attribute calculation retries once with that vehicle's default crew. Other
  vehicle-structure or native errors remain visible instead of being hidden.
- Large finite edited module-health and module-damage values now remain intact
  across the client, worker, and server instead of being rejected or truncated
  before the normal fire and ammunition-rack laws can run.
- The vehicle editor restores hidden-but-playable vehicles, accepts decimal
  values for editable numeric fields stored as integers, labels elevation,
  depression, gun elevation speed, and turret traverse speed correctly, and
  rebuilds its own drifted overlays from saved logical edits.
- The vehicle editor now includes an interactive #1513 armour viewer. Selecting
  an armour field highlights its exact hull, turret, track, or gun surface;
  clicking the model selects the matching field across categories. The view
  supports rotate, zoom, reset, collision-resource variants, and immediate
  nominal-thickness recolouring after edits.
- Garage item prices now use #1513-compatible integer values, and both
  standard and bond equipment can be removed free of charge through the
  appropriate shop fields.

## Credits

Thanks to 秋风扫落叶 for the contributed bot behavior improvements and review.

When reporting a reproducible problem, please include the launcher activity
text, `server.log` beside the launcher, and the game's `python.log`, together
with the selected client build and map.

This project is released under the GNU GPL v3. It contains no World of Tanks
content and is not affiliated with Wargaming. Run the LAN server only on a
network you trust.
