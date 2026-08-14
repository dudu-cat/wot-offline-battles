# Pinned #1513 standard-battle server responsibility audit

This audit answers a narrower question than the client ABI and source-copy
audits: which retail game-server responsibilities are authoritative in the
LAN server, which are delegated to one client, and which are still absent?

The inventory is derived from the exact pinned client, not from current mod
behavior. `inspect_client.py` pins the complete `Avatar.def` and `Vehicle.def`
payloads from build #1513. Their direct server-facing surfaces contain:

- `Avatar`: 34 properties, 43 client methods, 28 cell methods and 33 base
  methods;
- `Vehicle`: 37 properties, 8 client methods, 48 cell methods and 7 base
  methods.

The standard-battle responsibilities are visible in names such as
`receiveVisibilityUpdate`, `receiveVisibilityLists`, `onDetectedByEnemy`,
`onConcealedFromEnemy`, `replenishAmmo`, `shoot`, `repair`,
`receiveHitAssistBonus`, `receiveFirstDetectionFromArena`,
`receiveTaggedDestructibleKill`, `sendFinalStats`, `updateOwnClientRTT`,
`radioDistance`, `circularVisionRadius`, `detectedVehicles`,
`isObservedByEnemy`, `ammo`, `stunInfo`, `publicStateModifiers` and
`crewCompactDescrs`. Flag, gas-attack and resource-point methods belong to
other gameplay modes and are outside this standard-mode port.

## Authority levels

- **Server** means the Python 3 process computes or admits one canonical value
  and every client receives it.
- **Authority client** means one elected #1513 client computes the result from
  proprietary map/vehicle data and the server validates its envelope, stores
  shared state and relays it.
- **Firing/victim client** means the server records a client proposal but
  cannot independently reproduce it.
- **Local presentation** means every client may compute a visual result from
  the full snapshot; it is not authoritative game state.

## Version 0.3.76 countdown freeze and neutral coast

Server countdown authority is unchanged. PREBATTLE keeps gun, reticle and
server marker frozen until the single native BATTLE transition starts stock
aiming and opens movement/fire.

Neutral-coast calibration remains authority-client physics. Its recovered
track-grip share increases from `0.55` to `0.65`; exact mass, speed and terrain
inputs are used, but the private native C++ W-release curve is not claimed. No
LAN message or server validation boundary changes.

## Version 0.3.75 countdown, tactical cadence, Bot ammo and CTF visibility

Server countdown authority is unchanged. During PREBATTLE the client now
publishes native targeting parameters and starts only the exact #1513
`VehicleGunRotator` through its private `PlayerAvatar.isOnArena()` guard. It
restores that guard immediately, while `_battle_live` continues to reject
movement and fire until the server's ordered BATTLE barrier. This is a local
targeting lifecycle repair, not an early gameplay transition.

Static route ownership also remains on the authority client. Shared strategic
A* legs receive a small preference for baked cells with more already-proved
exits, including Lakeville's narrow road, and smoothing preserves that
clearance. No graph link, collision, water, grade or one-cell passage is
relaxed; spawn joins and local recovery do not receive the preference.

The authority now publishes complete Bot observations every 0.40 seconds and
phases their ordinary lane refreshes through the final 0.20 seconds. Ordinary
pairs beyond 585 m need no native lane query because they cannot enter the
server's 560 m assignment envelope within the conservative travel margin. The
selected target retains a separate 0.20-second final-fire lane requirement.
The server may therefore receive shared tactical evidence less often, but it
does not grant fire from an older lane result.

Bot ammunition is now finite canonical state, although proprietary descriptor
capacity and reload advancement remain authority-client inputs. The client
allocates the installed gun's real capacity at `3:2:1` among available
standard/premium/HE categories, or HE-led `1:1:4` for SPGs. The server plans
only the next round--standard by default, HE for a safely fragile/finishable
target and higher penetration when standard is inadequate--without changing
the already loaded round. For human contacts, the authority derives armor/class
from the installed descriptor and caches the immutable profile by vehicle name;
live pose/health overlays cannot substitute a mutable player armor field before
the complete observation reaches the server planner.

Each Bot publication carries loaded, planned-next, reload-pending and remaining
inventory atomically. The server validates shell/profile shape, exhaustion,
one exact decrement per accepted fire sequence and legal loaded/next/reload
transitions, stores those fields in canonical snapshots and gives the same
state to a successor authority. It still trusts the authority's descriptor
capacity and reload-ready timing, and this does not add player ammunition or
reload admission.

Lakeville's exact CTF objective still contains one base per team, and the server
capture/base record does not change. The compiled client space also contains an
assault2 base behind a different visibility bit. Exact #1513 can overwrite the
selected CTF bit with its full server mask late in startup, so the local runtime
now reapplies CTF bit `1` after deferred client readiness. This presentation fix
keeps CTF mask `0xffffff89` visible and assault2 mask `0xffffffc0` hidden without
changing XML, capture rules, minimap or team assignment.

## Version 0.3.74 authority-client load and traffic boundary

Server responsibility is unchanged. The Bot authority still computes complete
local Bot records and uses them for same-frame client-owned projectile and
motion laws. `LANClient.send_bot_state` now copies only the state fields the v5
server sanitizer accepts before enqueueing, so client-only route, profile,
physics, collision and typed-proof data do not become repeated wire work. The
server validates and relays the same canonical Bot pose, combat and optional
shot-angle fields as before.

Exact 3x3 world receipts remain authority-client geometry proof. They may cross
the 0.0975-second generic planning refresh only while strict receipt origin,
yaw, travel sign and actual-frame hull containment still hold. Navigation's
near-target guard now matches the driver's 1.5-metre arrival radius. A
right-of-way wait suppresses local stuck recovery for at most 1.5 seconds;
continuous blocking thereafter is handled by the existing client recovery law.
No traffic or collision authority moves to the server.

## Version 0.3.73 load-barrier boundary fix

Server responsibility is unchanged. The client now converts SpawnPlanner's
local integer team keys `1` and `2` to the canonical JSON text keys `"1"` and
`"2"` before enqueueing `battle_ready`. This lets the existing asynchronous
sender admit the formation payload; the server still owns the round-scoped
load barrier and validates the same two-team schema.

## Version 0.3.72 projectile-ledger update

The v5 handshake now requires `projectile_ledger_v1`. The server admits each
player or authority-Bot launch under one canonical round-scoped id and stores
its origin, velocity, gravity, limits and monotonic checked-through cursor.
Active records are included in revisioned snapshots. They remain live after a
shooter disconnect, and a Bot-authority change increments the authority epoch
so only the elected successor may resume progress or terminal resolution from
the stored cursor. Terminal tombstones make retries idempotent.

The authority client still owns exact proprietary BSP, destructible, vehicle
and armor intersection. It advances every shell through gravity chords no
longer than 25 ms, including moving-vehicle sweeps, and submits the chronological
result. The server validates its envelope and atomically commits one terminal
direct or bounded splash effect set together with all accepted projectile
destructible receipts. It does not independently reproduce the map geometry,
lead solution, penetration result, ammunition inventory or reload clock.

Server SPG responsibility remains the stable rear deploy anchor and ordinary
Bot macro priority. The elected client adds bounded low/high arc solving, exact
collision proof capped at four native rays per rendered frame and a frozen
moving-target launch intent. A completed exact receipt can wait only for the
same target and 3-D velocity to cross its proved endpoint; otherwise it is
re-led, and the total proof lifecycle is bounded. Stun is deliberately disabled
because no complete
server-owned penalty/duration ledger and medical-kit recovery transaction
exists. Native tracer visuals, sustained trajectory/probe performance,
artillery feel and repeated-round cleanup remain Windows #1513 acceptance
items.

## Version 0.3.71 client-boundary update

The LAN server and protocol-v5 authority rules are unchanged. On each client,
ordinary outbound payloads are now frozen and enqueued on a bounded reliable
FIFO with no coalescing; hello remains the synchronous first message. One
generation-isolated sender owns later serialization and socket writes. Invalid
data is rejected before admission; overflow or sender failure closes the
transport instead of silently losing ordered input, Bot-state or combat
messages.

The authority client also imports four bounded 0.8.2 recovery mechanisms. A
false raised centre support rolls back only the current player/Bot tick. A Bot
hard contact, that support rollback or a realised navigation rollback clears
the affected decision and motion receipt before replanning. The driver retains
one finite escape side and aligns before applying forward torque to a meaningful
climb. Navigation preserves the pre-climb turning point through smoothing,
live reach, lookahead and partial-path continuation. These changes do not move
collision or movement authority to the server.

The native 0.8.2 `WGVehicleFilter`/physics experiment is not ported. Server SPG
responsibility remains rear-anchor deploy/hold only; there is still no open-sky
proof, ballistic-arc collision budget, indirect-hit admission or stun ledger.
Exact Windows #1513 remains the native motion, collision, camera and lifecycle
acceptance boundary.

## Coverage matrix

| Retail responsibility | Current owner | Status and exact boundary |
|---|---|---|
| Room, host, build compatibility, map, teams and slots | Server | Implemented. Mixed client builds are rejected and only the current host may start a #1513 round. |
| Native load barrier, shared countdown, battle clock and round reset | Server | Implemented. All participating human Vehicles and the bot manifest must be ready before one shared 15-second countdown. `battle_live` is an ordered tick-zero barrier before snapshot one; clients project relative timing from the network-thread receive timestamp on a monotonic clock with bounded half-RTT correction and reject older timing ticks. A graceful departure during loading atomically revises membership and re-evaluates the barrier; a failed transition send removes the failed member and republishes a corrected higher-revision roster before survivors proceed. |
| Roster identity and bot authority failover | Server | Implemented. The server owns ids/slots and transfers the elected authority; the authority client supplies vehicle profiles and the initial map-resolved poses. |
| Standard base capture, elimination, timeout and terminal winner | Server | Implemented. Exact base coordinates are uploaded once at the load barrier; capture progress, elimination and timeout are computed centrally. A canonical nonzero `invaders` count also supplies the Bot planner with only the exact threatened base, time remaining and contributor identities; it does not reveal contributor positions. Lakeville's separate assault2 base is a local compiled-space visibility concern: the client reapplies selected CTF bit `1` after late readiness, while the server objective record and capture law remain unchanged. |
| Player camera, zoom, reticle, speedometer, tachometer, markers and outlines | Local presentation | These are client-owned consumers of authoritative state, not server transforms. The server must provide correct pose/visibility/reload/health state, but it does not send a camera position every frame. The canonical pose provider is installed before native Avatar/input startup, linked into both own and attached `ConsistentMatrices`, and presented to the new control before arcade/sniper `enable()` runs; the post-transition hook verifies rather than repairs provider identity. Copied speed plus simulated RPM are published through their audited native consumers. A stationary camera, stale sniper view or frozen needle is therefore a client presentation regression, not server authority. |
| Human pose, speed and aim | Firing/local client | **Partial.** The server range-checks numeric fields and relays snapshots, but accepts the client's pose within broad world bounds. It does not validate acceleration, traverse, terrain, water, walls or gun limits. |
| Bot pose and decisions | Authority client plus server macro planner | **Partial by design.** The server emits macro orders and validates roster/state shape; one client runs the copied terrain, driving, collision, gun and tactical laws. Version 0.3.69 chooses a stable group of one to three eligible own-base responders by distance/profile-speed ETA, targets only an actually threatened base and normally leaves one living Bot on its prior task. Responders retain ordinary visible/per-Bot-shootable target gates, so a capture contributor cannot expose an unspotted vehicle. Version 0.3.70 additionally derives a direction-neutral own/enemy route axis, assigns each SPG one cached rear-side route anchor, deploys it there and emits a zero-throttle hold after arrival; base defense remains higher priority. The server does not prove open sky or solve a ballistic arc. Bot obstacle planning remains client-local on the staggered 15/20-metre corridor and keeps six horizontal rays for generic alternatives. Only the finally selected flat/straight/powered sample may add a 15-metre exact 3x3 typed world receipt. Straight motion skips fresh per-frame world rays only under actual-`dt` containment with no catalog contact; hard proof blocks, deferred proof is not cached, and missing/stale proof, drift plus coast/brake/turn/airborne paths remain world-first. Receipt jobs are capped at 13 per render frame. The waiting rotation retains only Bots that actually made this eligible request; idle, hard-blocked, turning or airborne Bots drop out. Unattempted receiptless work keeps initial-backlog priority over refreshes. Once its native callback itself defers, it loses that priority and rotates behind the other enrolled requests, so neither a persistent callback deferral nor a refresh can starve the other. An unserved eligible Bot pauses at pre-step real speed without failure recovery, caching or world fallback. Initial deadlines cover 0.0975 seconds; a strict 24-FPS simulation drains 29 startup jobs as 13/13/3. Nearby same-team traffic receives a deterministic throttle-only right-of-way decision before integration; routes, terrain and physical contact remain client-resolved. |
| Human-human and human-bot tank contact | Local/authority client | **Partial.** The server canonically applies admitted ram HP and cooldowns, but it does not solve contact position/impulse. Two clients can disagree about separation under latency. |
| Static collision, falling, water and death zones | Local/authority client | **Partial.** The map-aware client owns these probes. Human self-damage is a downward-only health report; bot falling and terrain motion are active, while bot drowning remains open. |
| Enemy spotting and concealment | Local presentation | **Partial.** Every snapshot currently contains every vehicle. Each receiving client runs the copied proximity/LOS/five-second-memory law and applies one visibility gate to model, marker, minimap and native target lookup. The server does not own `detectedVehicles`/`isObservedByEnemy`, cannot ensure clients agree, and cannot redact unspotted enemies or their events. |
| Radio relay, camouflage, foliage and view modifiers | Local presentation | **Partial.** Descriptor camouflage, movement/firing penalties, pair-specific baked bushes, descriptor view range and critical optics/crew factors are implemented. Relay still considers all living allies; radio range, binocular activation and optional equipment/food/skill modifiers are not canonical server calculations. |
| Sixth Sense | Authority client observation, server relay and local HUD | Partial. The bot authority reports whether an enemy bot sees each human. The server validates those observations, reduces them to round-scoped visibility fields and broadcasts them to every participating client; each target's local controller applies the perk delay. This is still not a server-computed shared visibility ledger. |
| Gun selection, ammunition, clip state and reload cadence | Player firing client; Bot authority client plus server ledger/planner | **Partial.** Player ammunition and reload legality remain client-owned. For Bots, the authority derives finite stock from the installed descriptor capacity and advances copied reload/clip timing; it also derives human armor/class from an immutable descriptor profile rather than a live player armor field. The server receives that observation plus the shell profile, plans only the next standard/HE/higher-penetration category, and never rewrites the currently loaded round. It validates atomic loaded/next/reload-pending/inventory shape, one exact inventory decrement per admitted fire sequence and legal reload-boundary promotion, stores those fields in snapshots and preserves them across authority takeover. The server still does not independently prove descriptor capacity, human descriptor armor or reload-ready time. |
| Aim limits, dispersion and shot RNG | Firing/authority client | Partial by trusted-LAN design. The local #1513 stock reticle owns the player's current dispersion and the shot samples that same read-only angle before the firing state advances. Bot rays use copied descriptor laws. The server neither generates nor verifies yaw/pitch/scatter. |
| Projectile/world/armor collision, ricochet, penetration and HE splash | Authority client trajectory/geometry plus server projectile ledger | Partial. Every player and authority-Bot shot is first admitted into the durable round ledger, then the authority advances its gravity curve through at-most-25-ms chords and moving-vehicle relative sweeps. A target may dodge after launch. Exact #1513 data supplies the 19 scaled-HP threshold and fixed 25 mm loss: AP/APCR/APHE keep damage and accumulate loss per eligible item; old HE/HEAT stop at the first item and HE bursts there. One factor is sampled lazily at the first required penetration test and reused while the range mean is evaluated at each tested hit; pure misses and HE/HEAT destructible stops consume none. This exact operation order is a high-confidence same-family reconstruction because the private retail server source is unavailable. The server owns launch identity, monotonic progress, active snapshots, authority epochs, terminal tombstones and atomic direct/bounded-splash HP application. It still does not recompute proprietary destructible/static/vehicle ordering, collision layers, armor result or splash candidates. |
| Module, crew, ammo-rack, fuel, fire and repairs | Firing/victim/authority client plus server ledger | Partial. A firing client computes a bounded critical proposal on detached state, so native target state cannot change before admission. Modern proposals carry the target's exact base/ack token and separate pre-critical hull damage: a token match admits the full module state and any ammo-rack damage amplification, while a stale token applies only hull damage and emits an explicit rejection reason. Local repair HP is interpolated every frame for the native HUD, while player checkpoints and bot repair/fire ticks cross the network with strict base/proposal/ack revisions. Bot fire elapsed time and one-second tick phase survive authority transfer, and delayed snapshots cannot rewind or double-apply them. The server does not independently recompute the proprietary collision/device law or own consumable inventory; bot drowning remains open. |
| Equipment, food, crew skills and passive modifiers | Client descriptor/account | Open. Stock crew records and baseline commander/critical factors exist, but passive optional-device, food and skill effects are not yet carried into all battle equations. |
| Active consumables, equipment cooldowns and repair choices | Client only | Open. `activateEquipment`, `repair` and `replenishAmmo` exist on the retail server surface, but protocol v5 has no canonical inventory, cooldown or charge ledger. A client can therefore apply a repair or consumable without server admission. |
| Destructible trees, falling objects, fences and structures | Reporting client plus server result ledger | Implemented for player collision, authority-Bot collision, and player/authority-Bot shots. A map-aware client resolves the proprietary chunk/item result; the server validates its bounded identity/pose payload, deduplicates it, stores a per-round revision and broadcasts it. Every client applies the accepted result through the copied authority registry. The client enumerates the exact per-chunk count stored by #1513 `game.onChunkLoad`; its filename API is only a named prefix, so later blank non-tree slots retain native indices and reach the 41-map schema-v3 catalog. Its 61,625 unique matrix signatures recover resources and exact OBB selections; 11 ambiguous signatures representing 28 candidates fail closed. Movement and shot transactions retain the case-sensitive descriptor filename. At real physical speed, player/Bot crushing requires exact swept-hull/OBB contact and the unchanged stock gate. At low speed or from rest under matching drive, descriptor top speed is gate-only evidence: native submission is allowed only at the exact leading face plus its 0.075-m margin and current-frame travel, then pose holds for that tick at pre-step real speed. The cap never enters vehicle, network or ram state. A pending native skin clears only through its accepted OBB's exact exit plus a real backing-ray recast. A Bot's farther path classifier remains read-only, may skip at most four unique exact OBBs proven stock-crushable and fails closed on a fifth; its zero-speed scan only registers streamed identity. Falling items, backing walls and still-solid geometry remain authoritative. A shell without native identity may use only the nearest unique catalog OBB before the first static collision or vehicle; ambiguity and occlusion fail closed, and recast starts after the exact OBB exit. Exact #1513 data admits AP/APCR/APHE through an item of at most 19 scaled HP with unchanged damage and cumulative fixed 25 mm loss; less than 1 mm remaining stops it. Above-threshold items and old HE/HEAT stop traversal, with HE exploding there. The encoded shot bit remains replicated, while the local native order is unsynchronized because the copied projectile path has no later `damagedDestructibles` payload. The legacy 0.8.2 pivot-proximity workaround is not ported. The server does not recompute proprietary geometry, kinetic admission or the moment a native collision skin disappears. Bounded `DESTR` diagnostics add no native query. Windows #1513 remains the presentation and clearance acceptance boundary. |
| Vehicle HP, death, frags and team-killer state | Server | Implemented once a hit/self-damage proposal is admitted. Attacker identity, HP, death and the simple +1/-1 frag law are canonical. Monotonic combat events are delivered before snapshots that contain their resulting HP. Clients merge durable state on acceptance but defer native shot/hit/critical/health/death presentation in one FIFO journal until every referenced staged Vehicle is arena-ready; accepted and applied ids are distinct, so neither a snapshot echo nor asynchronous model loading can swallow the one-shot cause. Every admitted shot also carries its shell/result/world impact pose and enters the stock feedback paths without changing the canonical result. |
| Detection/damage/track assistance and first-spot accounting | None | Open. The server does not maintain spot ownership or assist windows and cannot produce these statistics yet. |
| Detailed battle results, credits, XP and dossiers | None/fake account | Open. Only winner, reason, base team, live frags and team-killer state exist. `personal`/`players`/`vehicles`, economy and dossier progression are absent. |
| Disconnect during battle and bot-authority transfer | Server | Implemented for leave and authority failover. A launched player shell is retained after its shooter leaves, while active Bot/player projectiles are restored from the revisioned ledger after authority-epoch takeover and resume only after their accepted checked-through cursor. |
| Mid-battle reconnect or late join | None | Open. A new TCP connection is rejected after the room leaves `waiting`; there is no session token or state reattachment. The internal `late_join` handshake flag only closes a start-race for a connection admitted while the room was still waiting; it is not genuine mid-battle joining. |
| Death, postmortem observation and spectator authority | Canonical HP/death plus local #1513 presentation | **Partial.** After the stock death delay, the client validates a living friendly target, transactionally rebinds the attached matrix and invokes `PlayerAvatar.onSwitchViewpoint`. Only the selected synthetic ally is exposed to native entity lookup, and its death/removal falls back to the nearest living ally. The LAN server still has no observer permission ledger, reconnect-to-spectator state or cross-client spectator target, and native Windows #1513 switching/cleanup remains an acceptance boundary. |
| Auto-aim/focus target ownership | Client only | Open. The retail surface includes `autoAim`, `onAutoAimVehicleLost` and targeting updates, but the LAN server does not validate or reproduce target acquisition. This is lower priority than visibility and gun admission, but it is another server-facing state machine rather than a camera concern. |
| Snapshot interpolation, RTT and clock projection | Server relay plus receiving client | Partial. The server ticks at 30 Hz; hello is sent before the socket becomes poll-visible, RTT ends in the network thread, and relative battle deadlines use the same monotonic clock domain. There is still no server rewind, shot lag compensation or authoritative movement correction. |
| Stun, siege/wheeled modes and artillery trajectory | Server staging/ledger plus authority-client trajectory | **Partial.** SPGs retain the stable rear-side route anchor, deploy/arrival state and zero-throttle hold, with base defense still able to pre-empt it. Version 0.3.72 adds moving-target low/high solutions, a fair exact collision queue capped at four native rays per rendered frame, frozen launch intent and elapsed indirect projectile resolution. The route anchor itself is not open-sky proof. Stun remains disabled because there is no complete canonical penalty/duration and medical-kit recovery loop; siege and wheeled special-mobility state machines also remain unsupported. |
| Anti-cheat and hostile-client validation | None | Intentionally out of scope for a trusted LAN, but the trust boundary must remain explicit whenever client-reported pose, ray, damage or critical state is described as canonical. |

## Previously under-emphasized gaps

The earlier battle audit listed visible parity blockers, but it did not make
five server-authority gaps prominent enough:

1. visibility is per-client presentation rather than one server-owned ledger;
2. player reload/ammunition legality and Bot descriptor/reload-ready proof
   remain trusted-client inputs, although Bot stock transitions are now
   canonical and conserved;
3. destructible contact geometry remains client-resolved even though accepted
   player and authority-Bot results are stored in the server ledger;
4. movement is relayed, not server-validated or corrected;
5. assist/first-detection and detailed result accounting do not exist.

This pass also found three lower-priority omissions that were previously
grouped too loosely: active consumable/equipment admission, the complete
death-to-observer lifecycle, and auto-aim target ownership. They should stay
explicit so that a locally working HUD is not mistaken for a server-complete
battle protocol.

These are independent of the pending 0.8.2 improvements to spotting formulas,
crew, equipment and skills. Porting those formulas improves simulation, but it
does not by itself move their authority to the server.

## Recommended order

1. Finish native client correctness first: camera/pose binding, collision and
   repeatable battle cleanup must be stable before authority moves.
2. Import the final 0.8.2 visibility and modifier laws, then expose one bounded
   observation profile to the server. The server should own per-team detected
   sets, five-second memory, radio relay and per-recipient snapshot redaction;
   map ray casts may remain on the elected client.
3. Extend the existing Bot shell profile into a server-verifiable gun
   capability/load-barrier record, then add player capability state so the
   server can own clip and earliest-next-shot admission while clients still
   compute proprietary collision and armor details.
4. Keep the existing authority-Bot contact reports on the destructible result
   ledger; add stronger actor/provenance validation only if the trusted-LAN
   boundary changes.
5. Build assist/result accounting from the server visibility and damage
   ledgers; implement reconnect only after those ledgers can be serialized.

This split remains compatible with a future Go server: the client-specific map
and descriptor probes cross a narrow protocol boundary, while portable room,
clock, visibility-memory, ammo/reload, HP, capture, statistics and reconnect
state move into the standalone process.
