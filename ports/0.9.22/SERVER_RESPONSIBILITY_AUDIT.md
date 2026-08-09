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

## Coverage matrix

| Retail responsibility | Current owner | Status and exact boundary |
|---|---|---|
| Room, host, build compatibility, map, teams and slots | Server | Implemented. Mixed client builds are rejected and only the current host may start a #1513 round. |
| Native load barrier, shared countdown, battle clock and round reset | Server | Implemented. All participating human Vehicles and the bot manifest must be ready before one shared 15-second countdown. `battle_live` is an ordered tick-zero barrier before snapshot one; clients project relative timing from the network-thread receive timestamp on a monotonic clock with bounded half-RTT correction and reject older timing ticks. A graceful departure during loading atomically revises membership and re-evaluates the barrier; a failed transition send removes the failed member and republishes a corrected higher-revision roster before survivors proceed. |
| Roster identity and bot authority failover | Server | Implemented. The server owns ids/slots and transfers the elected authority; the authority client supplies vehicle profiles and the initial map-resolved poses. |
| Standard base capture, elimination, timeout and terminal winner | Server | Implemented. Exact base coordinates are uploaded once at the load barrier; capture progress, elimination and timeout are computed centrally. |
| Player camera, zoom, reticle, speedometer, tachometer, markers and outlines | Local presentation | These are client-owned consumers of authoritative state, not server transforms. The server must provide correct pose/visibility/reload/health state, but it does not send a camera position every frame. The canonical pose provider is installed before native Avatar/input startup, linked into both own and attached `ConsistentMatrices`, and presented to the new control before arcade/sniper `enable()` runs; the post-transition hook verifies rather than repairs provider identity. Copied speed plus simulated RPM are published through their audited native consumers. A stationary camera, stale sniper view or frozen needle is therefore a client presentation regression, not server authority. |
| Human pose, speed and aim | Firing/local client | **Partial.** The server range-checks numeric fields and relays snapshots, but accepts the client's pose within broad world bounds. It does not validate acceleration, traverse, terrain, water, walls or gun limits. |
| Bot pose and decisions | Authority client plus server macro planner | **Partial by design.** The server emits macro orders and validates roster/state shape; one client runs the copied terrain, driving, collision, gun and tactical laws. |
| Human-human and human-bot tank contact | Local/authority client | **Partial.** The server canonically applies admitted ram HP and cooldowns, but it does not solve contact position/impulse. Two clients can disagree about separation under latency. |
| Static collision, falling, water and death zones | Local/authority client | **Partial.** The map-aware client owns these probes. Human self-damage is a downward-only health report; bot falling and terrain motion are active, while bot drowning remains open. |
| Enemy spotting and concealment | Local presentation | **Partial.** Every snapshot currently contains every vehicle. Each receiving client runs the copied proximity/LOS/five-second-memory law and applies one visibility gate to model, marker, minimap and native target lookup. The server does not own `detectedVehicles`/`isObservedByEnemy`, cannot ensure clients agree, and cannot redact unspotted enemies or their events. |
| Radio relay, camouflage, foliage and view modifiers | Local presentation | **Partial.** Descriptor camouflage, movement/firing penalties, pair-specific baked bushes, descriptor view range and critical optics/crew factors are implemented. Relay still considers all living allies; radio range, binocular activation and optional equipment/food/skill modifiers are not canonical server calculations. |
| Sixth Sense | Authority client observation plus local HUD | Partial. The bot authority reports whether an enemy bot sees the local human and the local controller applies the perk delay. This is not yet the same shared visibility ledger required for all humans. |
| Gun selection, ammunition, clip state and reload cadence | Firing client | **Major gap.** The server enforces monotonic `fire_seq` and bounds `shell_index`, but it does not know the gun profile, decrement ammunition or reject a shot fired before reload/clip completion. |
| Aim limits, dispersion and shot RNG | Firing/authority client | Partial by trusted-LAN design. The local #1513 stock reticle owns the player's current dispersion and the shot samples that same read-only angle before the firing state advances. Bot rays use copied descriptor laws. The server neither generates nor verifies yaw/pitch/scatter. |
| Projectile/world/armor collision, ricochet, penetration and HE splash | Firing/authority client | Partial. The server deduplicates a shot, bounds range/damage and applies canonical HP; it does not recompute the collision layers, armor result or splash candidates. |
| Module, crew, ammo-rack, fuel, fire and repairs | Firing/victim/authority client plus server ledger | Partial. A firing client computes a bounded critical proposal on detached state, so native target state cannot change before admission. Modern proposals carry the target's exact base/ack token and separate pre-critical hull damage: a token match admits the full module state and any ammo-rack damage amplification, while a stale token applies only hull damage and emits an explicit rejection reason. Local repair HP is interpolated every frame for the native HUD, while player checkpoints and bot repair/fire ticks cross the network with strict base/proposal/ack revisions. Bot fire elapsed time and one-second tick phase survive authority transfer, and delayed snapshots cannot rewind or double-apply them. The server does not independently recompute the proprietary collision/device law or own consumable inventory; bot drowning remains open. |
| Equipment, food, crew skills and passive modifiers | Client descriptor/account | Open. Stock crew records and baseline commander/critical factors exist, but passive optional-device, food and skill effects are not yet carried into all battle equations. |
| Active consumables, equipment cooldowns and repair choices | Client only | Open. `activateEquipment`, `repair` and `replenishAmmo` exist on the retail server surface, but protocol v5 has no canonical inventory, cooldown or charge ledger. A client can therefore apply a repair or consumable without server admission. |
| Destructible trees, fences and structures | Reporting client plus server result ledger | Implemented for player collision and player shots. A map-aware client resolves the proprietary chunk/item result; the server validates its bounded identity/pose payload, deduplicates it, stores a per-round revision and broadcasts it. Every client applies the accepted result through the copied authority registry. Bot-to-object contact remains open. |
| Vehicle HP, death, frags and team-killer state | Server | Implemented once a hit/self-damage proposal is admitted. Attacker identity, HP, death and the simple +1/-1 frag law are canonical. Monotonic combat events are delivered before snapshots that contain their resulting HP. Clients merge durable state on acceptance but defer native shot/hit/critical/health/death presentation in one FIFO journal until every referenced staged Vehicle is arena-ready; accepted and applied ids are distinct, so neither a snapshot echo nor asynchronous model loading can swallow the one-shot cause. Every admitted shot also carries its shell/result/world impact pose and enters the stock feedback paths without changing the canonical result. |
| Detection/damage/track assistance and first-spot accounting | None | Open. The server does not maintain spot ownership or assist windows and cannot produce these statistics yet. |
| Detailed battle results, credits, XP and dossiers | None/fake account | Open. Only winner, reason, base team, live frags and team-killer state exist. `personal`/`players`/`vehicles`, economy and dossier progression are absent. |
| Disconnect during battle and bot-authority transfer | Server | Implemented for leave and authority failover. |
| Mid-battle reconnect or late join | None | Open. A new TCP connection is rejected after the room leaves `waiting`; there is no session token or state reattachment. The internal `late_join` handshake flag only closes a start-race for a connection admitted while the room was still waiting; it is not genuine mid-battle joining. |
| Death, postmortem observation and spectator authority | Client fragments | Open as a complete flow. HP/death is canonical, but observer permissions, viewpoint binding, postmortem target selection and reconnect-to-spectator state are not represented in protocol v5 or covered by native Windows lifecycle acceptance. |
| Auto-aim/focus target ownership | Client only | Open. The retail surface includes `autoAim`, `onAutoAimVehicleLost` and targeting updates, but the LAN server does not validate or reproduce target acquisition. This is lower priority than visibility and gun admission, but it is another server-facing state machine rather than a camera concern. |
| Snapshot interpolation, RTT and clock projection | Server relay plus receiving client | Partial. The server ticks at 30 Hz; hello is sent before the socket becomes poll-visible, RTT ends in the network thread, and relative battle deadlines use the same monotonic clock domain. There is still no server rewind, shot lag compensation or authoritative movement correction. |
| Stun, siege/wheeled modes and artillery trajectory | None/stock fragments | Open or unsupported. These can occur in standard battles even though flag/gas/resource gameplay is excluded. Ordinary tank-gun HE is supported; the full SPG/stun and special-mobility state machines are not. |
| Anti-cheat and hostile-client validation | None | Intentionally out of scope for a trusted LAN, but the trust boundary must remain explicit whenever client-reported pose, ray, damage or critical state is described as canonical. |

## Previously under-emphasized gaps

The earlier battle audit listed visible parity blockers, but it did not make
five server-authority gaps prominent enough:

1. visibility is per-client presentation rather than one server-owned ledger;
2. fire sequence is canonical, but reload and ammunition legality are not;
3. bot-to-object destructible contact is not yet reported;
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
3. Add a per-vehicle gun capability record at the load barrier so the server
   can own ammo, clips and earliest-next-shot admission while the client still
   computes proprietary collision and armor details.
4. Extend the existing destructible result ledger to authority-bot contact.
5. Build assist/result accounting from the server visibility and damage
   ledgers; implement reconnect only after those ledgers can be serialized.

This split remains compatible with a future Go server: the client-specific map
and descriptor probes cross a narrow protocol boundary, while portable room,
clock, visibility-memory, ammo/reload, HP, capture, statistics and reconnect
state move into the standalone process.
