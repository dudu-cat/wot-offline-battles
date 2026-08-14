# World of Tanks 0.9.22.0.1 LAN/offline port

This directory contains the version-local client layer for the pinned Chinese
HD client:

- client version: `0.9.22.0.1 #1513`
- executable architecture: 32-bit x86
- embedded runtime: CPython 2.7.7
- release entry format: `mod_*.pyc`
- package format: Store-only ZIP-compatible `.wotmod`

Version `0.4.0` is the productized #1513 release. The port now lives at the
repository's top-level `0.9.22` directory. Copy-ready clients always start at
`127.0.0.1:28782`; an address changed in the in-game LAN window is stored in
the user-owned `mods/configs/offline_lan_0922/server_endpoint.json`, which a
later overlay does not ship or replace. First garage entry suppresses the
stock CN automatic server-announcement browser before it is created; player-
opened browser links remain available. Waiting-room instructions use ordinary
host/connect language. A separate x64 Windows server download starts by double
click and always listens on `0.0.0.0:28782` with `server_random`.

Version `0.3.76` restored stock #1513 PREBATTLE aiming: after one initial
camera/gun alignment, the physical gun, stock reticle and optional server
marker remain frozen. The one native BATTLE period transition starts the
stock rotator and opens the existing movement/fire fence.

The offline neutral-coast share increases conservatively from `0.55` to
`0.65`. Exact #1513 resources provide vehicle mass, speed and terrain inputs,
but the native C++ W-release curve is not statically recoverable, so this is
not claimed to equal retail exactly. Type 62 stop regressions agree at 30, 60
and 120 FPS; Windows #1513 remains the feel acceptance boundary.

Version `0.3.75` closes the countdown aim split without opening gameplay early.
Exact #1513 PREBATTLE clears `PlayerAvatar.isOnArena()` and stops
`VehicleGunRotator`, while this port intentionally starts the input handler so
the camera and reticle remain usable. The runtime now publishes the native
targeting parameters first, temporarily admits only `VehicleGunRotator.start()`
through that guard and restores the Avatar flag in `finally`. The rotator,
reticle and physical turret can then follow the same live pose before BATTLE;
movement and shooting remain fenced by `_battle_live`.

For shared `route` and continued-route searches, baked A* now adds a small cost
for cells with fewer independently proved exits. Clearance-aware smoothing may
increase mean missing-link exposure by at most 0.25. Spawn joins, private local
recovery and direct collision law are unchanged. The preference therefore
centres Lakeville traffic only where the existing graph proves additional
room: it creates no link and relaxes no obstacle, shallow-water, grade or
one-cell-corridor rule.

Full-roster Bot observations now use a 0.40-second cadence. Their periodic
firing-lane work occupies the final 0.20 seconds, cover work occupies the first
0.20 seconds, and ordinary pairs beyond 585 m are conservatively marked
unshootable without a native lane ray. This leaves the independent selected-
target `SHOT_LANE_SECONDS = 0.20` final-fire gate unchanged. A complete server
observation may be less frequent, but a shot cannot be authorized by a lane
older than the previous contract.

Authority-Bot ammunition is now a finite battle state. The installed
descriptor's `maxAmmo` is divided among available categories at `3:2:1` for
ordinary vehicles and HE-led `1:1:4` for SPGs; missing categories are
redistributed instead of inventing a shell. Descriptor order supplies the
standard non-HE baseline, and a later non-HE round is treated as premium only
when its representative penetration is at least three percent higher. The
server chooses the planned next round from current target armor/health and
remaining stock: standard by default, HE for a safely fragile or finishable
target, and premium when standard penetration is inadequate. For a human
target, the authority resolves armor and class once from the installed vehicle
descriptor and caches that immutable profile by vehicle name; live pose/health
overlays cannot substitute a network-provided armor value.

`shell_index`, `next_shell_index`, `ammo_reload_pending` and `ammo_remaining`
cross the wire as one atomic Bot contract. The current round is consumed at
launch; the planned next round cannot become current until the copied reload
state reports ready. The
server rejects shape, exhaustion, conservation and loaded/next-boundary
violations, and snapshots restore the same state after authority takeover.
This does not add server admission for player ammunition or reload cadence.

Lakeville's compiled space carries both CTF and assault2 base instances behind
different native visibility masks. During deferred startup, exact #1513's late
`ClientVisibilityFlags` update can replace selected CTF bit `0x1` with the full
server mask and expose the assault2 instance as an apparent duplicate. Once
client readiness crosses that stock boundary, the runtime idempotently reapplies
the selected CTF bit. The CTF mask `0xffffff89` remains visible and assault2
mask `0xffffffc0` remains hidden. XML, capture rules, minimap, team assignment
and the one-base-per-team CTF objective are unchanged. Native countdown
tracking, realised corridor traffic, sustained performance, base visibility and
ammunition presentation remain Windows `#1513` acceptance items.

Version `0.3.74` narrows only the repeated LAN publication surface. The Bot
runtime keeps each complete local state for same-frame projectile launch and
other authority-client processing. At `LANClient.send_bot_state`, the v5 wire
copy contains only fields accepted by the server Bot-state sanitizer, including
the atomic optional shot-angle pair. Route, profile, physics, collision and
typed-proof internals no longer enter the immutable outbound snapshot.

The generic direction sample still refreshes every 0.0975 seconds. A completed
exact 3x3 straight-motion receipt may now be carried into that refreshed sample
only while its own origin, yaw, forward/reverse sign and actual-`dt` hull reach
remain strictly contained. Vertical, lateral or angular drift, a sign change,
exhausted forward coverage, a hard/deferred generic result or catalog contact
continues to fail closed and requires the normal world path.

Navigation and driving also share the same 1.5-metre waypoint-arrival radius,
so a cached graph target inside that stop radius cannot park a Bot while its
requested goal remains distant. A right-of-way wait leases stuck-timer
suppression for only 1.5 seconds. Brief yielding stays quiet; continuous
blocking then permits the existing bounded recovery instead of resetting it on
every frame. Windows `0.9.22.0.1 #1513` still owns final frame-pacing and
realised spawn-traffic acceptance.

Version `0.3.73` canonicalizes the spawn-plan team keys at the one LAN wire
boundary that publishes `battle_ready`. `SpawnPlanner` keeps its intentional
integer `1`/`2` keys internally, while the immutable asynchronous sender now
receives JSON-canonical `"1"`/`"2"` keys and can admit readiness. This fixes
the immediate post-manifest load exit without changing formations, team
assignment, the load barrier or server authority.

Version `0.3.72` gives every player and authority-Bot shell real elapsed flight
instead of resolving a full-range ray at fire time. One shared projectile
runtime advances the gravity curve in collision chords of at most 25 ms and
sweeps moving vehicles over each matching interval. A target can therefore
move out of the path after launch, and moving-target fire must use a lead
solution. Local and relayed tracers consume the same canonical launch data.

The v5 LAN boundary now requires `projectile_ledger_v1`. The server retains
each accepted launch, its monotonic checked-through cursor, active-projectile
snapshots and terminal tombstone across shooter disconnect and Bot-authority
takeover. Only the current authority epoch may progress or resolve a shot.
Terminal resolution applies direct and bounded splash HP effects together with
all accepted shot-destructible receipts as one server transaction, so retries
cannot split those results or apply them twice.

SPGs still deploy to their stable server-selected rear route anchor. The
authority client then solves moving-target low- and high-arc candidates and
proves the chosen family against exact world collision through a fair queue
with a hard budget of four native rays per rendered frame. Low arc is the
faster, flatter option; high arc can clear intervening cover at the cost of a
longer flight. Once a moving-target launch reaches exact proof, its aim and
flight intent stays frozen through the native muzzle launch instead of being
re-keyed by every target update. A completed receipt outside the 1.5-metre
terminal tolerance may remain pinned only while the same target and full 3-D
velocity will cross its proved endpoint; that condition is rechecked every
frame without spending another ray. Otherwise the shot is rejected and re-led
through a new frozen exact proof, with a 120-second absolute lifecycle bound.
The exact
#1513 descriptor survey contains 52 SPGs, 133 installed shell entries and 43
distinct physical tuples, with speeds of 265--510 m/s and gravity of
125--190 m/s2. Across installed elevation limits and the baked maps' 89.106 m
maximum terrain drop, the longest reachable grounded case is the FV3805's
440 m/s, 146 m/s2, 70-degree high arc: 5.872907831 seconds. Stun remains
disabled: #1513 exposes native stun presentation fragments, but this port has
no complete server-owned penalty/duration ledger or medical-kit recovery
transaction.

Python and protocol tests cover the deterministic laws. The exact Windows
`0.9.22.0.1 #1513` client must still accept native tracer visuals, sustained
projectile/arc-probe performance, artillery feel and repeated-round cleanup.

Version `0.3.71` ports only the 0.8.2 mechanisms that preserve the existing
#1513 ownership split. `LANClient._send` now snapshots plain JSON and appends
every accepted message to a bounded reliable FIFO. It performs no JSON encoding
or socket write on the caller thread and does not coalesce input, Bot-state or
combat messages. The connection hello is still written synchronously before
`connected` is published. One sender thread owns subsequent encoding and
`sendall`; a generation fence prevents an old worker from mutating a restarted
transport. Invalid data is rejected before admission; overflow or sender
failure closes the transport rather than dropping an ordered message.

Player and authority-Bot vertical integration now reject a centre-support rise
above `min(frame climb, 0.85 m) + 0.02 m`. First terrain placement is unchanged.
A later rejection restores that tick's pose and applies the existing hard-wall
response; a Bot also clears its cached decision and world-motion receipt. The
same invalidation runs after a realised navigation rollback or hard motion
resolver result and feeds the attempted yaw into the copied finite failure
memory. The driver retains one deterministic escape side long enough to get
around a broad obstruction and aligns its hull before applying torque to a
meaningful ascent. Navigation smoothing preserves a turning point immediately
before a climb, and the same predicate covers the live hull-to-path reach,
two-point lookahead and partial-path continuation paths.

The native 0.8.2 `WGVehicleFilter`/physics experiment is deliberately not
ported into #1513. SPGs retain the existing server-selected rear route anchor
and zero-throttle arrival hold, but this release does not add open-sky proof,
a ballistic arc/collision budget, indirect-hit resolution or stun. Native
motion, collision feel, camera behavior and repeated-round cleanup remain
acceptance items on the exact Windows `0.9.22.0.1 #1513` client.

Version `0.3.70` replaces the old compatibility slice. It is a server-backed
standard-battle implementation with a stock map picker, native Avatar and
Vehicle entities, a playable local vehicle, LAN state, damage, 15 vehicles per
team, the copied tactical-bot stack and repeatable rounds. The removed `vertical_slice.py`
runtime is not packaged as a fallback.

Version `0.3.70` distinguishes drivable ground, an accepted crush, a pending
native skin and a real wall. The horizontal collision adapter accepts a
continuous bounded non-flat height profile only when the actual hit surface is
ground-like, in both uphill and downhill directions. Flat walls, raised walls
and step discontinuities still reach the unchanged wall rays. Neutral coasting
preserves the established flat-road drivetrain drag and progressively unloads
only that share when the vehicle is moving downhill. At or beyond the static-
hold tangent only descriptor rolling resistance remains; uphill coasting gets
no downhill relief. Opposite throttle, the handbrake and the static hold remain
active brakes.

At physical speed, local-player movement still needs exact swept-OBB contact
and the unchanged #1513 mass/speed/health kinetic gate. Accepted native
fragile/modules advance without the hard-wall speed impulse. At low speed or
from rest, a drive command may use only the matching forward/reverse descriptor
top speed to prove that same gate. Native submission is allowed only when the
exact leading hull face, its 0.075-m contact margin and this frame's physical
travel intersect the item. The pose holds for that submission tick and the real
pre-step speed is restored; the cap is not accumulated or published as vehicle,
network or ram momentum. Subsequent pending-skin handling skips only through
the accepted item's exact registered OBB exit and recasts the remaining segment.
A backing wall, falling item, expired-but-still-solid skin or unknown object
still blocks. This is an exact-contact start assist, not a proximity destroy
bypass or a global reduction in wall friction.

Authority-Bot direction planning may treat a path as soft only when a pure read
of the checksum-pinned catalog proves each encountered OBB stock-crushable and
advances to its exact exit. Planning never publishes a destroy result. The
existing staggered driver cadence keeps its three-lane 15-metre corridor at low
speed and 20-metre corridor above 5 m/s. Planner alternatives retain their six
horizontal rays. Only the finally selected flat, straight, powered sample adds
a typed read-only receipt from exact commit-width lanes at `-halfWidth`, centre
and `+halfWidth`, all three commit heights, over 15 metres. The receipt binds
its origin, yaw and forward/reverse direction. A normal straight-motion frame
may skip fresh world rays only when actual `dt` keeps its leading hull sweep
strictly inside that receipt and no catalog OBB touches the hull. A hard proof
marks the direction blocked; a deferred proof is not cached. Missing or expired
proof, vertical/lateral/yaw drift, catalog contact, coasting, braking, turning
and airborne motion remain world-first. Final-motion receipt work is capped at
13 jobs per render frame. The waiting rotation retains only Bots that actually
reached this eligible request; idle, hard-blocked, turning or airborne Bots drop
out. Unattempted receiptless work keeps initial-backlog priority over refreshes.
Once its native callback itself defers, it loses that priority and rotates behind
the other enrolled requests, so neither a persistent callback deferral nor a
refresh can starve the other. Initial deadlines are spread across the
complete 0.0975-second decision interval. A deferred eligible Bot pauses that
frame at pre-step real speed, does not remember a route failure, and neither
caches the deferred result nor falls back to another world sweep. The strict
24-FPS scheduler check drains 29 startup receipts in 13/13/3 jobs with 13/26/29
cached proofs; native Windows frame time remains an acceptance item. Low-speed
start uses the same leading-face, directional-cap proof as the player and never
publishes the cap as speed. A low-rate stationary registration pass only
discovers a streamed chunk. One ray may skip at most four adjacent proved-soft
items through their exact exits; the fifth fails closed pending native Windows
acceptance.

SPGs now use the server's own/enemy route geometry to select one stable
rear-side route anchor. They deploy to that point, stop within the bounded
arrival radius and hold while retaining ordinary legal visible-target data.
This is rear staging only: the server anchor is not proof of open sky, and this
candidate does not yet claim client-side ballistic arc solving or indirect-hit
resolution. Windows #1513 must still accept the realised placement and motion.

Version `0.3.69` adds server-directed own-base defense. The server's canonical
`invaders` count activates a stable group of one to three eligible responders,
ranked by travel ETA from their current distance and vehicle speed. The group
travels only to the exact threatened base, normally leaves at least one living
Bot on its existing assignment, and persists briefly across capture-update
edges to avoid order churn. Responders retain ordinary targeting and may fire
only at already visible, individually shootable contacts. Capture-contributor
identity can prioritize such an existing contact; it never supplies an unseen
position or bypasses spotting and firing-lane admission.

After the local vehicle dies and the stock postmortem delay has ended, the
native viewpoint request now performs one complete local server-style attach
to a living friendly vehicle. It updates the attached matrix and invokes the
stock `PlayerAvatar.onSwitchViewpoint` callback as one transaction. Dead,
enemy, missing and not-yet-ready targets are rejected. If the currently
observed ally dies or disappears, the client selects the nearest living ally;
synthetic remote visibility is widened only for the selected postmortem target
and is revoked on the next switch or cleanup. Windows #1513 still owns final
acceptance of the native viewpoint keys, camera continuity and cleanup.

This release includes the accepted Round 4 tactical-route batch. Ordinary
through-routes use one validated graph geometry in strict reverse for the two
teams. Ensk intentionally has two balanced seven-Bot lanes; Himmelsdorf keeps
its separate local `rear_guard`. The complete 41-map graph manifest is rebuilt
from exact #1513 terrain, water and BSP collision before packaging.

`0.3.68` keeps the checksum-pinned, 41-map schema-v3 destructible catalog and
preserves each catalog resource's case-sensitive native descriptor filename.
The #1513 `DestructiblesCache` lookup is case-sensitive; passing the normalized
lowercase catalog key made a valid structure fail before the unchanged stock
mass/speed/health kinetic calculation. The normalized key remains only the
catalog index, while movement and shot transactions now use the exact native
filename. Version 0.3.70 does not lower the stock collision-speed gate: its
leading-face start admission uses directional top speed only to prove that
gate at exact current-frame contact, not as the standstill/proximity bypass
that 0.3.68 deliberately excluded.

Version `0.3.67` fixed the runtime slot boundary that prevented most non-tree
entries from reaching the catalog. Exact #1513 `game.onChunkLoad` supplies
`numDestructibles` to the native manager; that manager count defines the
complete chunk slot range.
`wg_getChunkDestrFilenames` is treated only as the named filename prefix, not
as a slot-count API. The whole-map instance directory contains 61,625 unique
world-matrix signatures and recovers the canonical resource plus collision-box
selection for later native slots whose filename is blank. The 11 ambiguous
signatures, representing 28 candidates, fail closed. Native chunk and item
indices are never synthesized or renumbered. For local-player motion, a
dynamic object that is absent from the static world ray is considered only
when that vehicle's swept OBB intersects the exact item's transformed BSMO
OBB. Both the dynamic and native-material paths use #1513's stock
mass/speed/health kinetic gate and real item scale. Fragile and structure
acceptance starts the stock 0.2-second hiding interval, during which motion
remains blocked. Falling objects instead keep an exact OBB synchronized to the
native animator matrix and retain their final fallen collision pose. After a
hide interval, the static world ray and any backing geometry remain authoritative,
so an unknown, stale, ambiguous, under-threshold or still-solid object is not
made passable merely because a destruction result entered the LAN ledger. The
legacy 0.8.2 object-origin proximity destroy workaround is intentionally not
ported.
Authority Bots retain their existing streamed proximity/native contact sensor;
this release does not claim the dynamic-only OBB supplement for Bot movement.

Player shells still try the native material identity first. When an anonymous
native slot does not provide one, `0.3.68` selects only the nearest unique
catalog OBB on the shot segment. The first static collision and nearest vehicle
cap that segment, so an ambiguous item or a prop behind a wall or tank fails
closed. Continuation resumes just beyond the exact registered OBB exit rather
than a fixed distance, so a thick structure cannot be skipped into an unrelated
surface.

The pinned #1513 `destructibles.xml` sets
`maxHpForShootingThrough` to `19` and every material's
`projectilePiercingPowerReduction` pair to `(0, 25)`. Consequently AP, APCR and
APHE may continue only through an item whose scale-adjusted health is at most
19, losing a fixed 25 mm of penetration for each item while retaining shell
damage. Multiple items accumulate that loss. The first operation that actually
needs a penetration test lazily samples one random factor for the shell and
reuses it; the range-dependent mean is evaluated at each tested obstacle and
at the eventual vehicle. A pure miss, or HE/HEAT stopped by a destructible,
does not consume penetration RNG. If the sampled penetration remaining at an
obstacle drops below 1 mm, the shell disappears there. Old-physics #1513 HE
and HEAT stop at the first destructible; HE produces its explosion at that
point. An item above the health threshold may still be destroyed but stops the
shell.

The numeric threshold and reduction are exact pinned-build data. Public
official same-family descriptions establish the shell-family outcomes and
cumulative, damage-preserving traversal, but the retail 0.9.22 private server's
precise operation order is not published. Lazily sampling and reusing one shell
RNG factor, applying range loss at each tested hit distance and then subtracting
the cumulative obstacle loss is therefore an explicit high-confidence
reconstruction, not a claim of
server-source identity. The encoded shot-damage bit is retained for the native
effect and LAN replay, but the local manager order is issued without projectile
synchronization: the copied projectile path does not receive #1513's later
`damagedDestructibles` payload that would release a synchronized order.

This candidate writes bounded `DESTR` diagnostics to `python.log`: one
aggregate per newly scanned chunk and one line per first distinct contact
stage, capped per battle and rate-limited to one line every 0.25 seconds. The
diagnostic reuses already-read slot/contact state and performs no extra native
collision query. Frame diagnostics retain the existing coarse callback stages
and logical probe counts, but the per-query high-resolution probe clock is no
longer installed. That removes two diagnostic clock reads from every native
Bot probe without changing probe order, results, cadence, deadlines or safety
budgets. Windows #1513 sustained straight-line driving remains the pacing
acceptance boundary.

Version `0.3.65` introduced the previous schema-v2 transformed-OBB boundary,
but its runtime identity join still depended on the native slot filename.
Trees supplied that field; many fragile, falling and structure slots in #1513
did not. Schema v3 replaces that incomplete join without changing native item
identity or weakening collision admission.

Friendly Bot traffic now has deterministic yielding at close same-lane,
crossing and merge conflicts. Followers respect the vehicle ahead, Bots yield
to humans, and the lower Bot id has right of way when two Bots would otherwise
enter the same conflict symmetrically. An intentional wait no longer advances
stuck/reverse recovery. This does not change route selection, enemy contact or
the physical tank-contact resolver. Server `BOT COMBAT` lines now include the
real damage source and both teams, so same-team ram damage cannot be mistaken
for friendly gunfire.

The version retains the authority-client visibility and firing-lane reductions
from `0.3.63` without changing publication or probe cadences. This remains a
measured reduction, not a claim that frame pacing is solved. Native Windows
#1513 acceptance must still verify low- and high-speed contact against fences,
sheds and checkpoint props on multiple maps, visual removal before
pass-through, and sustained driving frame pacing.

`0.3.62` keeps every movement, sensing and collision cadence from `0.3.61`.
It removes repeated firing-lane keys, distance calculations, observation
copies and descriptor yaw-limit reads without changing native probe order,
budgets, deadlines or wire output. A combat-state rebase no longer reserves an
unpublished sequence: replayed fire and repair steps are coalesced with the
next real publication at the server's exact `ack + 1`. The server retains its
strict contiguous validator and now reports only the first occurrence of each
continuous rejection reason with the relevant fire/base/ack values.

Solid-contact destruction now keeps the exact #1513 point/normal material
probe first and uses the mature incoming-ray probe only as a fallback for
compiled fragile skins that the first direction misses. Both paths require a
descriptor hit at the same contact, an aligned surface normal, and either a
fragile object or a valid structure module. Unknown low solids remain
blocking. This is intended to restore Redshire field fences and base modules
without making rocks or static walls ghost geometry. Native Windows #1513
remains the acceptance boundary for both destructible visual removal and the
remaining frame-pacing issue; this release does not claim the hitch is gone.

`0.3.61` kept the measured `0.3.60` movement and combat contracts, then
removes two more behavior-equivalent sources of authority-client Python work.
When the failed-edge table is empty, navigation no longer rasterizes and
searches every candidate route segment for a penalty that cannot exist. On
render frames that neither collect an observation nor refresh firing lanes,
each Bot overlays only its selected target's current pose, health and alive
state instead of copying all cached enemy records. Observation/lane frames
still refresh every contact, and copied decision records remain immutable.
Successful local equipment code 16 is consumed once instead of being echoed
into stock `PlayerAvatar`'s unsupported warning branch. No movement integral,
decision frequency, collision cadence, engagement distance or 110-pair safety
budget changes in this release.

`0.3.60` used the previous diagnostic trace to remove two measured
authority-client spikes without changing the local pose integrator. A firing-
lane pair beyond the server engagement ceiling plus a conservative relative-
travel margin becomes ready/unshootable without a native ray; a changed
server order invalidates only that Bot's decision and motion cache. The
110-pair near-combat safety budget and complete-observation rule remain
unchanged. A fully settled copied Bot reuses its last proven corridor and
slope until pose, heading or motion changes. It is also a focused frame-pacing
diagnostic build. Once battle callbacks
are running it writes one bounded `PERF` report to `python.log` every five
seconds. Each slow-frame row attributes one callback's coarse stage work and
real probe counts to the following render interval (`cause` and `next`). The
`v=2` format introduced separate visibility, firing-lane, cover, ground and
motion timing fields. Version 0.3.68 retains the format and logical counts but
leaves those per-query elapsed fields at zero instead of surrounding every
native probe with clock reads. BigWorld time, the coarse high-resolution frame
clock and the 100 ms simulation cap remain separate. It performs no extra
collision query, does not change a gameplay deadline or budget, and records
its own log-write time so that measurement output is not mistaken for the
original hitch. Windows #1513 results remain the release acceptance boundary
rather than a claim that the final pacing issue is solved.

`0.3.61` installs one copied pose before native input startup and feeds that
same provider to attached, own, camera, gun, sniper and minimap consumers. It
gates native entity lookup by spotted/alive state, uses visible-pose descriptor
collision for incoming fire, restores the native RPM channel, and presents
server-admitted hit direction, health, stock shot results and battle events.
Internal bot pose and aim updates use the private authority registry, so an
unspotted vehicle keeps simulating without leaking back into the native AOI.
Critical hits are proposed on detached state and committed only by revisioned
server events. Accepted and natively applied ids are separate, so the ordered
event journal waits for staged Vehicles and snapshot echoes cannot swallow
fatal feedback or rewind repairs. The reticle and physical shot still share one dispersion angle;
when server aim is enabled, the local cell echoes that same ray and angle into
the second marker instead of leaving a stale oversized circle on screen.
Tank contact now uses the current 0.8.2 descriptor-sized chassis OBB law rather
than a hull circle chain, and native R/F cruise keeps its 25%/50% flags and HUD.
Every synthetic descriptor now loads its exact #1513 BSP hit testers before
Bot collision or avoidance reads `bbox`; one factory owns and releases that
geometry, and missing geometry is rejected instead of using a default body.
The measured 0.8.2 performance structure is also carried over without the
legacy profiler's per-frame output: one spatial broadphase and one traffic snapshot are
shared across each bot tick, expensive planning/steering is staggered near
10 Hz while copied motion, gun and local presentation advance every rendered
frame; only LAN Bot-state publication remains capped at 30 Hz. Navigation work
uses a bounded search slice with once-per-second cache housekeeping.
This release also separates shared spotting from each Bot's current firing
lane: only Bots with a current local lane receive that target, while blocked
Bots keep their validated route. Spawn-to-route joins are Bot-scoped rather than
reusing the first tank's egress path. Player and authority-Bot motion run the
retained tree, column and fragile contact sensor before publishing a pose, and
native destruction is committed only after #1513 accepts it. Combat events
carry an explicit shot, fire, ram or non-attack cause; the marker receives the
verified local vehicle identity after #1513 refreshes its initial zero ArenaDP
cache, and friendly ramming no longer emits a projectile-hit or enemy-efficiency
notification. The #1513 material probe is decoded at one strict seven-field
boundary, including its explicit no-hit flag, before the copied 0.8.2 contact
law sees the result.
Version 0.3.61 also rebakes all 41 spawn formations against compiled map BSP
with the largest #1513 chassis footprint, selects only the standard CTF space
visibility bit, and phase-distributes bounded periodic Bot sensing. Solid
contact destruction preserves the native item/chunk field order, while marker
startup is transactional so a partially subscribed plugin cannot retain the
initial zero player id and relabel player damage as ally damage.
The pinned client configures a client-only space through
`BigWorld.wg_setSpaceItemsVisibilityMask`, as its stock HangarSpace does
immediately after `createSpace()`. The LAN runtime writes the single CTF bit
without requiring a server-published `BigWorld.spaces[spaceID]` data object or
a synchronous getter readback. It also enters stock BattleLoading before
retiring the lobby Account; an early map failure therefore disposes the old
Lobby view and returns through the legal BattleLoading-to-Lobby transition
without duplicate listeners.
Player damage feedback now follows #1513's real weak-proxy ArenaDP contract
instead of comparing a private proxy to its referent by object identity. The
native PlayerAvatar remains the sole owner of filter input notification, while
the cell mailbox only relays flags to the LAN server. Local pose publication
also preserves its nominal 30 Hz phase at 40, 45, 50 and 75 FPS instead of
quantising those render rates down to 20-25 Hz.
Version 0.3.61 removes the audited camera render-cadence split: the persistent
compound matrix is mutated without polling or relinking its native provider
each frame, while only the exact arcade/sniper camera acceleration consumers
see velocity and acceleration derived from that copied pose. The stock
WGVehicleFilter remains installed for tracks, suspension and vehicle effects.
On ordinary terrain the vertical Bot law always selects its centre sample, so
`0.3.61` queries that point first and reads the front/back fallback only when
the centre has no support. This preserves the realised pose and ledge fallback
while reducing a 29-Bot flat frame from 87 ground callbacks to 29. The LAN
server also keeps reported attacker identity as death-ledger metadata
only; a non-attack `client_simulation` health event can no longer carry an
attacker field that terminates the strict battle tick.

Version 0.3.61 additionally aligns the initial arcade camera with the spawned
hull through stock `setToVehicleDirection()`, then calls the exact public
`VehicleGunRotator.reset()` once after the live pose is bound. The zero turret
and gun angles are echoed before the first targeting tick can restore a loading-
time appearance angle; no timer, marker lifecycle or sound object is restarted.
Bot authority replaces the server's pre-input formation placeholder with the
current local world pose, preventing a false battle-start Sixth Sense event.
A first direct local spot now publishes the exact #1513 `SPOTTED` and
`TARGET_VISIBILITY` feedback while allied relay and five-second memory remain
presentation-only. Enemy visibility caches include the target fire sequence,
so every observer applies a new shot's camouflage penalty. Bot firing-lane rays
retain their original freshness, use stable per-identity phases and share a
hard 110-pair per-frame budget with live-fire checks. At 24 FPS an overflow
delays the complete observation by at most one extra render frame plus its
normal publication phase; no partial firing-lane set is sent. Pairs beyond the
server's maximum engagement range plus one observation of relative travel are
marked unshootable without a native collision query. Server-order cache
invalidation is per Bot and ignores live target-pose fields already refreshed
each render frame, so one tactical change cannot force a 29-Bot decision and
motion-probe burst. Ordered health events normalize
their display health before a cause-free snapshot, so repeated player hits are
not repainted as unknown red damage. Postmortem validation mirrors all three
stock camera-provider branches: attached matrix, a still-registered live
Vehicle matrix, and the steady provider after entity removal.

Version 0.3.61 also sends the packed #1513 objective bases to the server and
keeps capture contribution per vehicle: leaving the circle or taking hull,
module, crew, fire or ammo-rack damage drops only that vehicle's points, while
a defender pauses progress and repair does not erase it. Placeholder poses are
excluded. Bot fire is consumed only on a 30 Hz publication frame, preventing a
rapid clip from skipping the server's strict sequence and disabling the whole
authority batch. Bot detection now uses the same descriptor camouflage, shot
penalty and prebaked foliage bonus as player-facing spotting. Three expensive
cover probes are spread across their 0.2-second observation window, and nearby
destructible lookup uses spatial bins plus exact #1513 haystack, wood-fence and
old-truck footprints instead of a pivot-only scan. An intact low solid remains
blocking unless native destruction accepts it.

The battle foundation now reuses the proven 0.8.2 behavior instead of a
separate simplified implementation: the native countdown receives an advancing
offline server clock, roster entries have unique ready identities, standard
ArenaType spawn points (or the wide CTF base formation) are shared by players
and bots. Each spawn slot is resolved and grounded once, then reused by both
the manifest and entity creation; local structure probes keep it near its
retail anchor without falling back to map centre. Player input still enters
through the exact #1513 `PlayerAvatar.moveVehicle` mailbox, but the copied
0.8.2 force, traverse, terrain and pose integrator owns movement because a
client-only Vehicle has no retail game-server transform stream. Its pose is
published through the audited #1513 `Vehicle.model.matrix`, camera and speed
boundaries without calling the forbidden `Entity.teleport`. Authority bots use
the same copied 0.8.2 force, traverse and pose integrator. Remote gameplay vehicles
again use the mature 0.8.2 split between a synthetic vehicle identity and a
separate `OfflineEntity` visual; #1513's verified compound-model assembler
supplies the visual resources.

This is not yet a claim of complete 0.8.2 battle parity. The finalized 0.8.2
spawn-congestion recovery, reverse-steering correction, route planner and
server macro coordinator are now migrated and pinned by the source audit. All
41 supported standard maps use build-specific graphs baked from #1513 terrain,
static collision and water resources; the same safe graph anchors and heights
drive player and bot formation slots. Player-visible spotting now copies the
0.8.2 50-metre proximity, 500-metre ceiling, two-point LOS, allied relay and
five-second memory law across model, marker and minimap presentation. It also
uses exact #1513 moving/still descriptor camouflage, shot penalties and a
freshly baked 41-map pair-specific foliage index rather than 0.8.2 map data.
Passive equipment/skill
modifiers and detailed post-battle statistics remain explicit
open items in `BATTLE_SOURCE_AUDIT.md`; the current server does own live frags,
human team-killer state and the terminal winner/reason.

`SERVER_RESPONSIBILITY_AUDIT.md` separately records the retail game-server
surface and the current authority boundary. In particular, visibility,
reload/ammunition legality, movement validation, assist/first-detection and
detailed results are not mislabeled as server-owned merely because a client
can currently present or report them. Player-caused destructible results are
now server-deduplicated and replayed to every client; bot-to-object contact is
still called out separately.

The offline account discovers every complete standard-battle vehicle definition
from the pinned client at startup instead of maintaining a hard-coded tank list.
Each garage vehicle receives its stock configuration, a full trained crew and
default ammunition. Event, IGR-only and observer definitions are excluded. The
account starts with 100,000,000 credits, 100,000,000 free XP, 1,000,000 gold,
2,000 garage slots and 2,000 barracks berths.

This release fixes both halves of the real `#1513` battle-entry boundary. The
`VEHICLE_ADDED` roster is published before selecting the local Vehicle id, and
the compatibility wrapper no longer repeats the player-id notifier inside the
native `vehicle_onEnterWorld` stack. This preserves the stock own-vehicle matrix
initialization order. Full Windows dumps identified two unsafe native methods
that dereference the unavailable retail server/filter chain on client-created
entities: `WGVehicleFilter.syncGunAngles` and
`WGVehicleFilter.syncStabilisedYPR`. A complete `scripts.pkg` scan finds exactly
four Python call sites: initial Vehicle physics, packed gun-angle updates,
damaged-model refresh and the Avatar auxiliary-physics handler. Scoped adapters
suppress only those calls inside their exact stock handlers. Vehicle physics,
speed providers, model refresh, track/RPM updates and filter identity everywhere
else remain stock. A returned
client-only entity id is still not treated as a materialized tank: client
ready, `AVATAR_READY` and battle period wait for native `onEnterWorld`, registry
presence, `inWorld`, `isStarted` and a descriptor.

Exact `#1513` applies the battle `PERIOD` update synchronously and immediately
sends its initial `moveVehicle` mailbox from `PlayerAvatar.__setIsOnArena`.
The bridge therefore opens its input gate after every Vehicle readiness check
but immediately before publishing `PERIOD`; a failed publication closes the
gate and remains latched as a startup failure.

LAN JSON text is Unicode on the embedded Python 2 runtime, while BigWorld's
Avatar and Vehicle `STRING` properties require byte strings. The release now
normalizes those entity-bound values to UTF-8 and suppresses only the stock
world callbacks emitted for an Avatar whose constructor already failed. A
Python 2 build audit covers both Avatar and Vehicle string boundaries.

Map-start failure now restores the stock prebattle dispatcher before rebuilding
the Account lobby, while final process shutdown guards the exact late
`SoundGroups.destroy` lookup against a retired Account. Callback ownership is
generation-safe, so a late callback from an earlier attempt cannot cancel or
overwrite the next round.

## User flow

1. Start `server/lan_battle_server.py`. One client is sufficient for an
   offline round; additional clients can join the same waiting room over LAN.
2. Start the frozen client. After the native intro/login state finishes its
   destructive cleanup, the mod creates its local Account and enters Lobby.
   On the first garage entry it suppresses the stock CN automatic browser for
   the unavailable server announcement before creation. Browser links opened
   explicitly by the player are not intercepted.
3. Select the tank you want to use, then click the garage's native **Battle!**
   button. The LAN handshake uses that tank's exact type name and descriptor
   health; it does not silently fall back to MS-1. If no valid tank is selected,
   the client stays in the garage and displays a native warning. The button
   joins the LAN waiting room; it does not call retail matchmaking or a retail
   training-room service.
4. If the endpoint cannot be reached, the stock settings window opens
   automatically while the client retries. Clicking **Battle!** again while
   connecting also opens it. Any not-yet-accepted client can edit
   `LAN SERVER: host:port` there; editing the address does not make that player
   the room host.
5. The first waiting player is the room host. Only that client opens the stock
   training settings window as a local map picker. Its Description field shows
   the editable `LAN SERVER: host:port` endpoint on the first line, the live
   player list and `SELECT A MAP, THEN CLICK CREATE TO START`. Later players
   click the same **Battle!** button, remain in the garage and see that the
   battle opens automatically after the named host starts it.
   If the host closes the picker, it remains closed until that client clicks
   **Battle!** again.
6. The host chooses one server-offered standard map and uses the window's
   primary action. The server is authoritative for the host, selected map and
   start; guest start requests are rejected before they can change the map.
7. The server fills vacant slots with bots and broadcasts the same round to
   every waiting client.
8. A team-elimination result freezes the round. After five seconds the server
   returns connected players to the waiting room and the stock picker opens for
   the current host. If the waiting host leaves, ownership passes to the lowest
   connected player id and that client receives the picker.
9. Returning to the garage during a round retires that player, transfers bot
   authority when necessary and retains the LAN connection until the server's
   next waiting-room barrier.

No F12, `0`, or other battle-start hotkey is used. Setting `enabled` to `false`
disables this port; it does not select a second or legacy AI path.

Only arena definitions whose gameplay name is `ctf` are exposed. In this
client, `ctf` is the internal name of standard battle, not a separate
capture-the-flag variant. Assault and encounter definitions are excluded.

## Runtime ownership

The Python 3 server owns the room host, selected map, teams and slots, canonical
health, accepted shot events, frags, human team-killer state, active-round
retirement, terminal result and round reset. It
exposes a newline-delimited JSON protocol rather than emulating the original
BigWorld game server.

One elected client owns bot simulation because only the client can query the
proprietary map collision, water, terrain and tank resources. Bots receive
role-specific routes and stable randomized personalities. Local steering
checks nearby terrain, slope, water, obstacles and other vehicles; combat
includes role-dependent range, aiming, angling, peeking and optional
forward/backward jiggle. The server contains a portable planner boundary, but
the 0.9.22 runtime now consumes its revisioned global `bot_orders` for macro
targets. The elected authority client still owns terrain visibility probes and
local collision/water/slope avoidance, with its local planner as a fallback
when no server order is available.

Those bot components are wired and covered by pure-logic tests. The finalized
0.8.2 spawn/OBB and route corrections are migrated, and the authority client
does not reapply its older server echo over a locally integrated Bot pose. The
real #1513 client still has to validate the resulting movement visually.
Player spotting uses the copied proximity/LOS/team-relay law and starts or
stops the model, marker and minimap together.

The player's real `Vehicle` retains the stock #1513 input, gun, appearance and
HUD lifecycle. `PlayerAvatar.moveVehicle` sends the exact movement flags to
the LAN mailbox and native filter. The copied 0.8.2 integrator is the sole
authoritative pose owner and publishes position, yaw and speed through the
verified compound-model and camera providers. It does not call
`BigWorld.Entity.teleport`, which this client rejects with `Operation is not
allowed` after the entity enters the world.

Remote humans and bots are Python gameplay vehicles backed by separate
`OfflineEntity` compound models, descriptor hit testers and the copied 0.8.2
pose/health surface. The authority integrates and publishes the copied bot
poses; every client applies those samples directly to the presentation. Only
the local player uses `WGVehicleFilter.notifyInputKeysDown`. This distinction
is required because a retail remote `WGVehicleFilter` expects game-server pose
samples that the offline connection does not produce.

Current standard battles finish by team elimination, the copied standard-base
capture rule, or the server-owned 15-minute timeout. Retail matchmaking,
reconnect recovery, internet authentication, anti-cheat, alternate
module purchases and persistent garage mutations are outside this release.
Small repair kits, small medkits and hand extinguishers are exposed through the
exact #1513 activation-code path during battle.

Protocol v5 messages are now strictly round-scoped. Keep the client package
and `server/lan_battle_server.py` from the same checkout; this client rejects
an older server before entering the waiting room when its welcome lacks the
build or room-host contract.
The #1513 client also declares `wot-0.9.22.0.1-cn-1513` in its handshake. The
server pins each non-empty room to one client build and its exact map pool,
preventing 0.8.2 synthetic coordinates from being mixed with 0.9.22 world
coordinates. Separate server ports are required for simultaneous rooms using
different client versions.

## Configuration

The copy-ready overlay installs the release-owned defaults and map data under:

```text
mods/configs/offline_lan_0922/config.json
```

Every package sets the initial endpoint to `127.0.0.1:28782`. When the player
changes the endpoint in the in-game LAN window, the client writes only:

```text
mods/configs/offline_lan_0922/server_endpoint.json
```

The release overlay never contains that user-owned file, so installing a later
version does not replace the saved address. An older non-default endpoint in
`config.json` is migrated to this file on first load. A missing file uses the
loopback default; an invalid or truncated file fails safely back to loopback.
Saving uses temporary-and-backup replacement: if replacement fails, the
previous file is restored, the live endpoint is not changed and the player is
shown a writable-folder error.

The supported fields are:

```json
{
  "schema": 1,
  "enabled": true,
  "host": "127.0.0.1",
  "port": 28782,
  "name": "Player",
  "vehicle": "ussr:R11_MS-1",
  "max_health": 90,
  "startupTimeoutSeconds": 30.0,
  "prebattleCountdownSeconds": 15.0,
  "battleDurationSeconds": 900.0
}
```

Before receiving a server welcome, a failed connection opens the native
training settings window automatically; **Battle!** also reopens it while the
client is connecting. Once waiting,
only the room host owns that window as the map picker; guests continue to use
their saved endpoint. The server supplies the selected map and spawn. The
`vehicle` field seeds the initial offline garage record; the tank selected in
the carousel when **Battle!** is clicked is authoritative for the LAN join. The
legacy `max_health` field remains accepted for configuration compatibility, but
battle health is read from that selected vehicle's descriptor. The release does
not write a capability trace or vertical-slice status file; normal failures are
reported in `python.log` without verbose per-frame logging.

The real Avatar enters `PREBATTLE` for the server-owned 15-second countdown
copied from the 0.8.2 battle flow before the runtime accepts movement or
shooting, then receives a timed `BATTLE` period. The configuration value is an
engine-free fallback only; a connected battle uses `battle_live`. That message
is an ordered tick-zero wire barrier before the first snapshot. Relative
server timing is projected from the network-thread receive timestamp on a
monotonic clock (with half the measured RTT), so a stalled render frame or a
wall-clock adjustment cannot restart or skew the countdown.
Leaving during native loading removes that participant and re-evaluates the
barrier atomically. If a transition write fails, the server repairs membership
and sends survivors a newer authoritative roster before continuing.
Authority bot poses are materialized locally without waiting for their server
echo. Bot Vehicle creation is staggered across the countdown, matching the
0.8.2 loading pattern and avoiding a burst of 29 HD model prerequisites in one
32-bit BigWorld callback.

Authoritative shot events use the selected gun's finite burst count. This
closes the stock Avatar shot-acknowledgement wait and prevents one click from
leaving the native firing effect active indefinitely. During an offline LAN
battle the stock debug panel displays the independent LAN socket's measured
round-trip time and connection state instead of the absent retail transport's
`999`/red result.

Critical-hit proposals are compare-and-swap updates against the target's
canonical module revision. They also carry the pre-critical hull damage: if a
delayed proposal targets a state that has since repaired or extinguished, the
server still applies the ordinary hit and feedback but rejects the stale module
state and any obsolete ammo-rack damage amplification. Bot repair and fire use
the same copied 0.8.2 constants under a revisioned server ledger. Fire elapsed
time, one-second tick phase and igniter identity survive authority transfer, so
the ten-second burnout neither restarts nor loses its final-kill attribution.

## Windows server

The Windows server download contains `WoT-0.9.22-LAN-Server.exe` and a short
README. Double-click the executable and keep its console open while playing.
It has no required arguments and always binds `0.0.0.0:28782`, allows up to 30
players and starts with the `server_random` map policy. A client on the same PC
uses the default `127.0.0.1`; a client on another PC uses the server PC's LAN
address saved through the in-game window. Allow TCP 28782 on private networks
if Windows Firewall prompts.

The executable is built as an x64 console application by the Windows CI job.
That job verifies the PE architecture, the exact listener and a v5 protocol
welcome before publishing the ZIP and standalone EXE artifacts. The current
artifact is not code-signed, so Windows SmartScreen may display an unknown-
publisher warning. Verify the release SHA-256 and obtain it only from the
project release; signed distribution remains a separate release boundary.

Developers may run the source server with the same zero-configuration defaults:

```bash
python3 0.9.22/server/lan_battle_server.py
```

## Build

The complete #1513 source checkpoint lives below this directory: `src/` is the
client, `server/` is the Python 3 LAN service, `tests/` is the port-only test
suite, and `tools/` contains the build and compatibility audits. Changes for
the #1513 service belong in `server/`; the repository-root server entry is the
legacy 0.8.2 line. From the repository root, run the port tests with:

```bash
python3 -m unittest discover -s 0.9.22/tests -p 'test_*.py' -v
```

The per-module 0.8.2/#1513 provenance and permitted differences are recorded
in [`BATTLE_SOURCE_AUDIT.md`](BATTLE_SOURCE_AUDIT.md). The release build runs
`tools/audit_battle_sources.py` and fails when a module is undocumented or a
copied 0.8.2 law/data file drifts. Follow
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) for the exact-client, package,
Windows server, native gameplay and publish gates.

The loader ignores source `.py` files, so release bytecode must be compiled by
CPython 2.7. Verify the exact client and build the package together:

```bash
0.9.22/build_for_client.sh \
  ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD
```

The copy-ready overlay always contains `127.0.0.1:28782`. For a server on
another machine, install the same artifact and change the endpoint once in the
in-game LAN window; the resulting user file survives later overlay upgrades.

The script uses local CPython 2.7 when available and otherwise the pinned
Docker build. Before compiling, it reads code objects directly from the pinned
client's `scripts.pkg`, rejects mismatches in the stock method signatures,
direct-consumer literals and lifecycle code paths used by this port, verifies
the exact `AccountCommands` constants, inventories every exact-client call site
for the guarded Vehicle filter syncs, runs the complete raw `serverSettings`
subscript inventory against the local producers, and checks the ordered
lifecycle contracts plus the complete Account-helper binding inventory.
Signature checks include
`*args`/`**kwargs` flags at the stock picker boundary, rather than comparing
only positional argument counts. It then validates the package
version, exact source-to-PYC module manifest, CPython 2.7 magic, absence of
Python source or optimized/cache bytecode, CRC, unique archive members,
explicit directory entries and Store compression for every member.

Signature matching is only one gate. The tests also validate the exact
Vehicle/Avatar property and mailbox schemas, the Account/Lobby direct-key and
tuple contracts recorded in `tools/account_lobby_consumer_contract.json`, and
LAN round/message state transitions, including active leave and a next battle
arriving before native Lobby/Hangar recovery. A conventional static type
checker cannot replace these checks because BigWorld injects dynamic Entity
properties and mailboxes at runtime.

To regenerate the complete strict standard-mode navigation set from the pinned
client, use the atomic batch baker. It validates all 41 graphs before replacing
the previous set and writes the checksum manifest last:

```bash
python3 0.9.22/tools/bake_all_navigation_0922.py \
  --client ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD \
  --output-dir 0.9.22/navgraphs --jobs 4
```

The matching foliage baker decodes the exact `SpTr`/`BWST` scene tables and
ctree-v106 bounds, then atomically publishes the complete 41-map checksum set:

```bash
python3 0.9.22/tools/bake_foliage_0922.py \
  --client ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD \
  --all --output-dir 0.9.22/foliage
```

The destructible baker joins the exact compiled BSMI/BSMO instance tables to
the pinned descriptor catalog, validates all 41 maps, and publishes its
checksum manifest atomically:

```bash
python3 0.9.22/tools/bake_destructibles_0922.py \
  --client ~/Downloads/World_of_Tanks_0.09.22.00.01_CH_1513_HD \
  --all --output-dir 0.9.22/destructibles
```

Outputs are written to `0.9.22/dist/`:

```text
org.peng.offline_lan_0922_0.4.0.wotmod
org.peng.offline_lan_0922_0.4.0.wotmod.sha256
WoT-0.9.22-LAN-Client-<release hash>/
WoT-0.9.22-LAN-Client-<release hash>.zip
```

The hash-named directory is directly mergeable into the game root. Each build
removes only older outputs produced by this port from `dist/`.

`SOUND_ERROR` event `2967843034` is the pinned client's
`hangar_v2_music_lobby` event, not a gun event. It can identify a failed lobby
music PostEvent during GUI/sound-bank transition. Preserve the timestamp and
whether lobby music is actually missing after a normal battle return; do not
diagnose firing audio from this ID alone.

## Exact-client review boundary

The implementation was checked against bytecode extracted from the local
Chinese `#1513` client, not only against public 0.9.22 source trees from other
builds. `COMPATIBILITY_REVIEW.md` records the reviewed interfaces, adopted
reference patterns and remaining Windows runtime checks.

Do not install this package into a current/online client or use it to log into
a live account. It targets only the frozen offline test installation.

## License and unofficial status

Project code is distributed under the GNU General Public License version 3;
see the repository-level `LICENSE` and `THIRD_PARTY_NOTICES.md`. That license
does not grant rights to the World of Tanks client, assets, trademarks or
other Wargaming property. Users must supply their own lawfully obtained pinned
client.

This work includes trademarks and/or copyrighted works that are the exclusive
property of Wargaming. All rights reserved by Wargaming. This work is
unofficial and is not endorsed by Wargaming.
