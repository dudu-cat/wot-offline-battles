# Compatibility review: World of Tanks 0.9.22.0.1 #1513

This review is pinned to the Chinese HD client whose `version.xml` reports
`v.0.9.22.0.1 #1513`. The executable is 32-bit x86. Packaged client modules use
CPython 2.7 bytecode magic `03 f3 0d 0a`; the embedded build identifies itself
as Python 2.7.7.

Version 0.4.0 adds only release, local-configuration and lobby-presentation
adapters around the existing battle runtime. The copy-ready configuration
always begins at `127.0.0.1:28782`; a user edit is atomically stored in
`mods/configs/offline_lan_0922/server_endpoint.json`, outside the files shipped
by later overlays. Malformed user data fails safely to loopback. Exact #1513's
CN lobby opens an automatic server-announcement browser at zero battles; the
scoped adapter suppresses only that `onLobbyInited` automatic call before
creation. It does not replace explicit `showBrowser`, disable
`BrowserController`, affect the training-settings picker or intercept browsers
opened later by the player.

The x64 Windows server artifact is a PyInstaller deployment of the same Python
3 service. Its launcher fixes `0.0.0.0:28782`, `server_random` and 30 players;
the Windows CI gate checks the PE architecture, listener and v5 welcome. This
does not change the client/server protocol or the 32-bit x86 game client. The
artifact is currently unsigned, so SmartScreen trust remains a distribution
boundary rather than a compatibility claim.

Version 0.3.76 restored exact #1513's frozen PREBATTLE aiming boundary. After
one initial camera/gun alignment, the physical gun, stock marker and server
marker remain frozen until the single native BATTLE period transition starts
stock aiming and opens the existing movement/fire fence.

Exact resources expose vehicle mass, speed and terrain resistance, but not the
native C++ W-release curve. The neutral-coast share therefore moves
conservatively from `0.55` to `0.65`, without an exact-retail claim. Type 62
regression covers 30, 60 and 120 FPS; native feel remains a Windows #1513
acceptance item.

Version 0.3.75 repairs a lifecycle mismatch introduced by deliberately usable
PREBATTLE camera controls. Exact #1513's period transition clears the private
`PlayerAvatar.__isOnArena` flag and stops `VehicleGunRotator`; the enabled
input handler could therefore move the gun marker while the physical turret
remained at its spawn angle. The port now supplies native targeting parameters
before calling the exact rotator `start()` surface, temporarily sets only the
guard flag, restores it in `finally`, and verifies the rotator's private
started/maximum-turret-speed state. It does not call the full arena-start
gameplay transition. `_battle_live` continues to fence movement and fire until
the server's ordered BATTLE barrier.

The navigation change is confined to shared strategic route A*. It adds a
small cost derived from the baked cell's existing link completeness, and its
smoother rejects a shortcut that would increase mean missing-link exposure by
more than 0.25. Spawn joins and local recovery do not request the preference.
No collision, shallow-water, grade or link-validity predicate is weakened, so
an unavoidable one-cell passage remains the same passage rather than being
made artificially wider.

The full-pair observation period is 0.40 seconds and its phased lane-refresh
window is 0.20 seconds. The ordinary no-query envelope is 585 m: the server's
560 m assignment ceiling plus a conservative 25 m two-vehicle travel margin
across the longer window and presentation phases. This does not change
`SHOT_LANE_SECONDS = 0.20`; a selected target must still have an independent
final-fire lane result within that freshness bound. At the compatibility
boundary, lower periodic probe frequency is therefore allowed to delay shared
tactical knowledge but not to authorize fire from an older proof.

Bot inventory uses the installed descriptor capacity and at most five
descriptor-order shell summaries already admitted by protocol v5. Because the
stable descriptor seam does not expose store price, the first non-HE round is
the standard baseline and a later non-HE round is classified as premium only
when its representative penetration is at least 1.03 times the baseline.
Category weights are
`3:2:1` for ordinary vehicles and `1:1:4` for SPGs, redistributing any absent
category. Server planning prefers standard, selects HE only for a safely soft
or bounded finishing case, selects premium when normal penetration is below
the target-armor margin, and never requests an exhausted category. Human
contacts obtain armor/class from `build_vehicle_profile()` over the installed
descriptor and cache that result by vehicle name. Render-frame live overlays
update pose, health and team fields only, so they cannot replace this immutable
profile with an authority-wire armor claim.

Bot `shell_index`, `next_shell_index`, `ammo_reload_pending` and
`ammo_remaining` form one atomic snapshot. The authority consumes the
physically loaded round at launch and may promote only the previously planned
round at a completed reload boundary.
The server checks inventory shape, exact one-round conservation and the
loaded/next transition; canonical snapshots preserve all fields for authority
takeover. This is trusted-LAN Bot admission, not a new player reload validator
or a reconstruction of retail store economics.

Lakeville's compiled space contains CTF and assault2 base instances with
different visibility masks. The initial client-only space selection correctly
writes CTF bit `0x00000001`, but exact #1513's late
`ClientVisibilityFlags.SERVER_MASK` update may overwrite it with `0x000fffff`
before deferred client readiness completes. `_finish_entity_startup()` now
idempotently reapplies the selected gameplay bit after that stock boundary. In
the exact Lakeville data, CTF mask `0xffffff89` intersects bit 0 while assault2
mask `0xffffffc0` does not, so the observed write sequence
`1 -> SERVER_MASK -> 1` leaves only the CTF base visible. XML, capture rules,
minimap, team assignment
and the one-base-per-team CTF objective are unchanged. Windows #1513 remains
required to accept native prebattle turret motion, realised corridor traffic,
sustained frame pacing, base visibility and ammunition presentation.

Version 0.3.74 keeps full authority-Bot state inside the client but projects the
v5 `bot_state` wire copy to the server sanitizer's consumed fields before the
immutable outbound snapshot. This does not move projectile launch or Bot
simulation to the server: `BattleRuntime` continues to consume the original
complete update locally. The optional shot yaw/pitch pair remains atomic at the
projection boundary.

The short 0.0975-second generic planning cache remains a steering and slope
refresh. A typed exact 3x3 receipt has an independent containment contract and
may cross that refresh only under its exact origin, yaw, travel sign and
actual-`dt` forward-coverage checks. Any vertical, lateral or angular drift,
coverage exhaustion or sign change restores proof. The navigation adapter now
uses the driver's 1.5-metre arrival radius when rejecting a parked near target,
and an intentional traffic wait suppresses recovery for no more than 1.5
seconds. These are pure Python control boundaries; realised native frame pacing
and congested movement still require Windows #1513 acceptance.

Version 0.3.73 fixes one compatibility gap between the local spawn planner and
the 0.3.71 asynchronous sender. The planner intentionally returns a dictionary
keyed by integer team ids `1` and `2`, while the immutable sender rejects any
mapping whose keys are not already JSON text. `LANClient.send_battle_ready`
now converts only those known team keys to `"1"` and `"2"` at the protocol
boundary. The formation payload and server load-barrier contract are otherwise
unchanged.

Version 0.3.72 replaces the previous fire-time terminal ray with a shared
elapsed-time projectile law for player and authority-Bot shells. The canonical
launch records origin, velocity, gravity and lifetime. Its parabola is tested
in adaptive chords no longer than 50 ms and with at most 5 cm sagitta,
including a relative sweep for each moving vehicle, so an already-fired shell
does not follow a target and the target may dodge it.
Direct fire and SPG fire use moving-target lead before launch. The same launch
record drives local and relayed tracer presentation.

The matching server advertises and requires `projectile_ledger_v1`. It owns
launch identity, checked-through progress, active snapshots, authority epochs
and terminal tombstones. A launched shell remains live if its shooter leaves;
an elected successor restores active records and continues only from the
server-accepted cursor. One terminal resolution validates and commits direct
or bounded splash HP effects and shot-destructible receipts atomically. The
server still trusts the map-aware authority client for proprietary BSP,
destructible, vehicle and armor intersection results; the durable ledger does
not turn those local geometry queries into server simulation.

SPGs retain their server-owned rear deployment anchor. A bounded authority-
client controller evaluates low and high ballistic families against exact
world collision through a fair queue capped at four native rays per rendered
frame. It freezes a proved moving-target aim/flight intent through the matching
native muzzle launch, preventing target motion from replacing the pending proof
every tick. A receipt that finishes more than 1.5 metres from the target's
newly projected impact pose may wait only while the same identity and full 3-D
velocity will cross its proved endpoint, with that condition rechecked every
frame; otherwise it is rejected and re-led through another frozen exact proof.
The whole proof lifecycle has a 120-second absolute bound. The exact descriptor
survey finds 52 SPGs, 133 installed shell entries
and 43 distinct physical tuples. Speed spans 265--510 m/s and gravity spans
125--190 m/s2. Across installed elevation limits and the baked maps' 89.106 m
maximum terrain drop, the longest reachable grounded trajectory is the
FV3805's 440 m/s, 146 m/s2, 70-degree high arc at 5.872907831 seconds. Stun
remains disabled because the pinned client fragments do not
supply this port with a complete canonical penalty/duration ledger and
medical-kit recovery transaction. Python verification does not prove #1513
tracer visuals, projectile/arc-probe frame pacing, artillery feel or native
round cleanup; those remain Windows acceptance items.

Version 0.3.71 imports a constrained set of proven 0.8.2 mechanisms without
changing #1513 native ownership. The caller-facing LAN path freezes plain JSON
and appends every accepted message to a bounded reliable FIFO; it neither
serializes nor calls `sendall` on the game thread and it never coalesces ordered
input, Bot-state or combat payloads. Hello remains the synchronous first wire
message. The sender and receiver are fenced by one transport generation.
Invalid input is rejected before admission; overflow or sender failure closes
that transport rather than losing an ordered message silently.

The copied vertical integrators now distinguish first terrain placement from a
later centre-support jump. A rise above `min(frame climb, 0.85 m) + 0.02 m`
rolls the current tick back and reuses the established hard-wall response rather
than lifting the hull onto a wagon, roof or large prop. Bot support rejection,
realised navigation rollback and hard motion resolution all invalidate the
affected decision and typed motion receipt before remembering the attempted
yaw. The driver chooses one finite escape side around a broad obstacle and
aligns before applying forward torque to a meaningful ascent. The navigation
guard preserves a turn immediately before a climb in baked smoothing, live
reach, lookahead and partial-path continuation.

No native 0.8.2 `WGVehicleFilter`/physics experiment is present in this port.
The SPG boundary also remains unchanged from 0.3.70: a rear route anchor and
arrival hold are implemented, but open-sky proof, ballistic trajectory and arc
collision, indirect-hit resolution and stun are not. Exact Windows #1513
remains the acceptance boundary for native motion/contact feel, viewpoint
switching and repeated-round lifecycle safety.

The goal of version 0.3.70 is a complete playable vertical path, not another
login-only probe: local Account -> stock Lobby/join/map selection -> native map and
Avatar -> native local Vehicle plus remote presentations -> local movement/aim/fire -> synchronized
humans and bots -> damage/death/result -> cleanup -> a second round.

Version 0.3.70 narrows the copied horizontal-collision fast path to a
continuous bounded height profile whose actual collision normal is ground-
like. Unlike the previous one-direction rise test, the predicate is direction-
neutral, so a continuous downhill profile is not reclassified as a wall. Level
streets, step discontinuities, flat walls and raised walls still reach the
original hull rays. In the copied longitudinal law, neutral coasting preserves
the established flat-road drivetrain share and progressively unloads only that
share when current motion is downhill. At or beyond the static-hold tangent,
only descriptor rolling resistance remains. Uphill coasting gets no downhill
relief; opposite throttle, handbrake and the zero-speed hold still apply their
existing laws.

The destructible contact seam retains the exact descriptor filename and #1513
mass/speed/health gate. At physical speed, exact swept-hull/OBB contact supplies
the real kinetic input and an accepted fragile/module crush can advance without
the hard-wall speed response. At low speed or from rest under matching drive,
the forward/reverse descriptor top speed is only gate evidence. It can trigger
native submission only when the exact leading hull face, a 0.075-m margin and
this frame's real travel intersect the item. That submission holds the current
pose and restores pre-step real speed; the cap never enters copied vehicle,
LAN or ram state. On following ticks, a pending native skin can clear only by
advancing through its unique registered OBB exit and recasting the remainder.
Falling items, backing walls, expired-but-still-solid skins, ambiguous identity,
native rejection and under-threshold contacts remain blocking.

Authority-Bot planning uses the same strict catalog boundary without moving
authority to a distance probe. The existing staggered driver cadence retains
its three-lane 15-metre low-speed and 20-metre above-5-m/s corridor. A pure read
may classify a segment as soft only by resolving unique stock-crushable OBBs
and advancing past each exact exit. At most four adjacent items may be skipped;
a fifth fails closed. Generic planner alternatives retain their six horizontal
rays. Only the finally selected flat, straight, powered motion sample adds an
exact read-only 3x3 receipt: commit-width lateral lanes, all three commit heights
and 15 metres of forward coverage, bound to origin, yaw and direction. An
ordinary straight frame may skip a fresh world query only when its actual-`dt`
leading-hull sweep remains strictly inside that typed receipt and no catalog
OBB touches the hull. A hard proof blocks; a deferred proof is not cached.
Missing or stale proof, vertical/lateral/yaw drift, catalog contact, coasting,
braking, turning and airborne motion remain world-first. Destruction and LAN
publication occur only at exact hull contact, and the directional cap remains
gate-only. Final-motion receipts have a hard 13-job render-frame budget. The
waiting rotation retains only Bots that actually made the eligible final-motion
request; idle, hard-blocked, turning or airborne Bots drop out. Unattempted
receiptless work keeps initial-backlog priority over refreshes. Once its native
callback itself defers, it loses that priority and rotates behind the other
enrolled requests, so neither a persistent callback deferral nor a refresh can
starve the other. Initial deadlines cover the full 0.0975-second decision
interval. A deferred eligible Bot pauses for that frame at pre-step real speed,
does not call route-failure recovery, does not cache the deferred result and
does not substitute an authoritative world sweep. The strict 24-FPS scheduler
test drains 29 startup
jobs as 13/13/3 and grows the receipt cache as 13/26/29; native Windows frame
time remains outside this deterministic proof. A bounded
low-rate zero-speed scan merely registers the streamed chunk. This supplements
the older native sensor without restoring the permissive 0.8.2 pivot workaround.

The server macro planner now stages each SPG at one cached rear-side point
chosen from direction-neutral own/enemy route geometry, then emits a zero-
throttle hold inside the arrival radius. This is a portable server order, not
proof of an open ballistic corridor. The client-side arc solver, arc collision
budget and indirect-hit loop are not yet claimed by this review; native Windows
#1513 placement remains a release acceptance item.

Version 0.3.69 adds two constrained adapters without moving proprietary
terrain or camera law into the Python server. The canonical base state gives
the server macro planner an `invaders` trigger and the exact threatened base.
It retains a stable one-to-three-Bot response chosen by distance/profile-speed
ETA, normally leaving one living Bot on its previous task. Responders keep the
normal contact and firing-lane gates; capture-contributor identity can rank
only an already visible and individually shootable contact, so no hidden pose
crosses this boundary.

The postmortem switch mailbox now delegates to a local server-style attachment
only after the stock postmortem delay. It admits living friendlies, changes
the attached matrix, and invokes the exact `PlayerAvatar.onSwitchViewpoint`
callback transactionally. A selected synthetic remote entity is exposed to
native lookup only for that observation; death/removal selects the nearest
living ally and cleanup revokes the exposure. Windows #1513 remains required
to accept the native switch controls, camera continuity and repeated-round
teardown.

The 0.3.68 destructible boundary is pinned to a schema-v3 catalog baked from
all 41 exact #1513 map packages. A checksum-pinned whole-map directory maps
61,625 unique world-matrix signatures to fragile, falling and structure-module
resources plus transformed BSMO bounds. This recovers identity for native
chunk slots whose filename is blank while preserving the engine's chunk/item
index. Eleven ambiguous signatures covering 28 candidates fail closed.
Local-player movement does not infer a dynamic prop from a nearby pivot: its
swept OBB must intersect the exact item OBB, then the stock mass/speed/health
kinetic gate decides whether native destruction may be requested. Native
fragile/module acceptance retains a synthetic block through the stock
0.2-second hiding delay. Falling items refresh their catalog OBB from the
native animator only until the first touchdown callback; that coarse OBB then
retires while the moving/final native BSP and ground support remain
authoritative. Static world rays and backing collision remain authoritative
after a hiding interval; the catalog is a strict contact/identity source, not
permission to bypass an intact wall. The broad object-origin proximity workaround used by
the legacy 0.8.2 implementation is deliberately not transplanted.
Authority Bots retain the existing streamed proximity/native contact sensor;
the new dynamic-only OBB supplement is not wired into Bot movement.

The catalog retains normalized keys for deterministic indexing, but native
`DestructiblesCache.getDescByFilename` receives the resource's case-preserved
descriptor filename. This is an exact-build ABI requirement: the cache lookup
is case-sensitive and rejecting a lowercase synthetic filename occurs before
the unchanged stock mass/speed/health kinetic gate. Version 0.3.68 neither
lowers that gate nor restores the broad 0.8.2 pivot-proximity workaround.

The shot path also retains native material identity as its first choice. If a
#1513 native slot is anonymous, only the nearest unique catalog OBB on the
bounded shot segment may supply identity. The first static collision and
nearest vehicle cap the search, while ambiguity fails closed. Traversal resumes
from the exact registered OBB exit plus a small epsilon, not a fixed jump that
could skip a thick structure or its backing geometry.

The exact #1513 `destructibles.xml` supplies both numeric shooting-through
contracts: `maxHpForShootingThrough` is `19`, and every listed material has
`projectilePiercingPowerReduction` factor/minimum values `(0, 25)`. Version
0.3.68 therefore lets AP, APCR and APHE continue only through an item whose
scale-adjusted health is at most 19. Each accepted item leaves damage unchanged
and adds a fixed 25 mm penetration loss; multiple items accumulate. The first
operation that actually needs penetration lazily samples one shell factor and
reuses it, while the range-dependent mean is evaluated at each tested obstacle
distance and again at the vehicle. A pure miss or HE/HEAT stopped by a
destructible consumes no penetration RNG. A sampled remainder below 1 mm makes
the shell disappear at that
obstacle. An above-threshold item may be destroyed but stops traversal. Under
the pre-1.13 HE mechanics used by #1513, HE and HEAT stop at the first
destructible, and HE explodes at that point.

The threshold and material reduction are exact pinned-resource evidence.
Official same-family mechanics descriptions support the shell-family split,
cumulative penetration reduction and unchanged damage. The proprietary retail
0.9.22 server implementation and its exact operation order are not published,
however. The lazy one-factor, per-tested-hit-range, then cumulative-reduction
order above is therefore documented as a high-confidence reconstruction rather than an exact
server-source copy. The resulting fragile/module payload preserves its encoded
shot bit, but the local manager order is unsynchronized: the copied projectile
path does not deliver the retail server's later `damagedDestructibles` payload
required to release a projectile-synchronized native order.

The complete streamed-slot boundary comes from the exact #1513 native path:
`game.onChunkLoad(spaceID, chunkID, numDestructibles, isOutside)` writes
`numDestructibles` into the active `DestructiblesManager`. Version 0.3.68 reads
that manager count and enumerates every native index. It uses
`wg_getChunkDestrFilenames` only for its available filename prefix; a short
prefix no longer proves that later fragile, structure or falling slots do not
exist. A missing native count is retried after streaming rather than guessed,
and a contradictory filename list fails closed.

For Windows verification, bounded `DESTR` lines report one aggregate for each
newly scanned chunk plus each first distinct contact stage. The logger reuses
the same enumeration/contact result, adds no BigWorld query, caps chunk/contact
identities per battle and emits at most one line every 0.25 seconds. Frame
diagnostics retain callback-stage timing and logical probe counts, but version
0.3.68 does not install the optional per-query Bot probe clock. Removing those
two clock calls per native probe is behavior-preserving: the probe sequence,
return values, freshness windows, deadlines and 110-pair safety budget are
unchanged. Straight-line Windows driving remains the frame-pacing acceptance
test; this source review cannot claim that the visible hitch is eliminated.

The previous 0.3.65 schema-v2 catalog supplied transformed OBBs but joined
runtime slots by native filename. That field exists for trees but is blank for
many #1513 non-tree slots; schema v3 closes that identity gap.

The stock `BigWorld.entity`/`entities` facade is an AOI surface, not the LAN
authority registry. Unspotted or dead synthetic vehicles remain private there;
only the injected pose/aim resolver reads them for simulation. Native visual
startup, local Avatar binding, drive and readiness continue through the stock
facade, so an internal update cannot accidentally reveal an enemy.

The local Account inventory is derived from the pinned client's initialized
vehicle catalogue. Only definitions that can produce a complete stock vehicle,
crew, module and ammunition record are published; event, IGR-only and observer
types are excluded. Inventory ids start at one, tankman ids are globally unique,
and every crew foreign key, installed item, unlock and shop-price entry is
validated before native lobby consumers receive the snapshot.

## Exact-build evidence reviewed

The following groups were extracted from the local `scripts.pkg` and reviewed
at their call sites and lifecycle boundaries:

- connection and Account: `connection_mgr.py`, `Account.py`, `PlayerEvents.py`,
  the Account sync helpers and lobby requesters listed in the consumer matrix
  below, server settings and lobby context;
- lobby and map selection: `gui/app_loader`, `LobbyHeader.fightClick`, Scaleform
  view loaders, `TrainingSettingsWindow`, arena cache and the generated
  prebattle aliases;
- battle entry and exit: `OfflineMapCreator.py`, `Avatar.py`,
  `AvatarInputHandler.py`, battle session/controller repositories and arena
  listeners;
- entity contracts: `Avatar.def`, `Vehicle.def`, `Vehicle.py`,
  `ClientArena.py`, `constants.py`, filters, gun rotation and item descriptors;
- presentation and combat calls: vehicle ammo/reload/targeting callbacks,
  `getCurShotPosition`, `showShooting`, health callbacks, kill arena updates
  and collision methods;
- copied-motion camera consumers: `AccelerationSmoother.update` plus arcade
  and sniper `__calcCurOscillatorAcceleration`; these read filter velocity
  and acceleration independently of the compound root matrix;
- resources: all standard arena definitions exposed by the local cache and the
  tank descriptors/models used by the playable and bot vehicle pools.

This matters because commonly available public 0.9.22 decompilations are from
different builds. For example, a similarly named API may exist in another
revision while being absent in `#1513`.

## Account and lobby lifecycle

The mod installs narrow, reversible adapters around the exact Account, Avatar,
Vehicle and connection boundaries. The fake connection constructs a real
`PlayerAccount`, supplies the server settings and RPC shapes consumed by the
lobby repositories, and then calls the native player and GUI lifecycle.

Exact build `#1513` unconditionally calls `BigWorld.clearAllSpaces()` at the
start of `PlayerAccount.onBecomePlayer()`. A client-only Account created inside
its own temporary space would therefore delete itself during promotion. The
offline wrapper suppresses only that one call while the native method executes
and restores the engine function in `finally`; online Accounts and later space
cleanup retain stock behavior. Delayed Account RPC callbacks also re-check the
current player identity before delivery, so a retired Account cannot receive a
late response during a battle/lobby transition. The lifecycle regression fake
uses destructive `clearAllSpaces()` semantics rather than a logging-only stub.

BigWorld entity destruction also clears the Python Entity's entire instance
dictionary. The exact Account repository survives across replacement Account
entities, while `AccountSyncData.setAccount()` saves its persistent cache
through the old weak proxy before rebinding that cache to the new Account. The
offline constructor therefore prebinds that one cache before native repository
reuse. The initialization sentinel is set only after the native constructor
returns. A separate retirement token is opened immediately before native
`onBecomePlayer()`, because that method can attach global helpers and chat and
then fail; the ready sentinel is set only after the complete promotion passes
validation. FakeServer and uncancellable Avatar resource callbacks require the
ready sentinel and current-player identity, so a zombie object cannot receive
a late mailbox callback even during the destruction tick.

The LOGGED_ON notification, Account construction and promotion are one
transaction. Any listener or constructor failure clears client-only spaces,
resets connection status, invokes every disconnect boundary independently and
deletes the retained Account repository even if an earlier event listener
raises. Shutdown restores every patched class and host entry in `finally`.

Before any bulk entity clear, the current offline Account or Avatar now runs
its complete native `onBecomeNonPlayer()` method exactly once. This detaches
`ChatManager.playerProxy` and every Account/Avatar helper while the entity
fields still exist; the later engine callback is ignored by the closed
retirement token. If native retirement itself raises before its late chat
detach, the wrapper still clears `ChatManager.playerProxy` and preserves the
first error. The regression fake clears the retired object's entire `__dict__`,
exercises Account -> Avatar -> replacement Account, and injects failures after
partial Account/Avatar promotion, reproducing the native failure mode in which
`ChatManager.switchPlayerProxy()` first cleans the old proxy.

The Account surface was checked consumer-first against the local `#1513` PYC,
not inferred from another 0.9.22 build:

| Producer | Exact consumer contract covered |
| --- | --- |
| `CMD_SYNC_DATA` / `AccountSyncData` | `rev` and `prevRev`; every initial `Account._update` subscriber receives an explicit cache value instead of depending on a missing-key fallback. |
| `Stats` / `StatsRequester` / lobby controllers | Zeroed money and account scalars; mapping-shaped restrictions, referral data and clan locks; a non-empty `dailyPlayHours`; and full daily/weekly `playLimits`. Zero periods mean exhausted parental-control time in this build. `mayConsumeWalletResources` starts true because false is the native wallet's `SYNCING` state, and `tutorialsCompleted` carries the completed offline bitmask. |
| `Inventory` / `InventoryRequester` | All item-type indices exist; vehicle `compDescr` and crew maps exist; `repair` is a two-item tuple and `shellsLayout` is a mapping. |
| `QuestProgress` / personal-mission requesters | `quests`, `tokens`, and `potapovQuests`; both `regular` and `training` contain `slots`, `selected`, and `lastIDs`, while `compDescr` is always present. |
| goodies, vehicle rotation, recycle bin, ranked, badges, New Year | Readable empty caches exist. `groupLocks` contains both directly indexed lists, the ranked helper can directly index an empty `ranked` cache, and `ClientNewYear` plus the New Year controller accept the empty sync/goodie mappings without fabricated event data. |
| `Shop` / `ShopRequester` / `RefSystem` | Mandatory `sellPriceFactor`; all directly read item/goodie collections; currency-mapped `paidRemovalCost`; exact berth, slot, and free-XP tuple arities; and the four-key disabled referral configuration, including integer `posByXPinTeam = 0`. |
| `DossierCache` / `DossierRequester` | The stream body is the exact `(revision, dossierChanges)` pair; an empty change list completes synchronization without fabricating dossier data. |
| `ClientChat` and BW Chat2 | Both client mailboxes exist. Offline chat commands are accepted as one-way no-ops: `CHAT_COMMANDS` indices are never echoed as `CHAT_ACTIONS`, and no malformed partial action is delivered to `ClientChat.onChatAction`. |
| initial server settings / lobby controllers / `ClientRanked` | `file_server`, regional settings, the four-item roaming tuple and the directly indexed two-item `wallet` retain their native shapes; roaming item 3 is the host list consumed by `predefined_hosts`, while `ranked_config` is present and explicitly disabled because `ClientRanked` indexes it directly. `elenSettings` and the server-owned tutorial are explicitly disabled because their exact missing-section defaults start unsupported event-board or tutorial GUI lifecycles. |

The controller chain itself was enumerated from `game_control.__init__` and
`new_year.__init__`. `NewYearController` is invoked first, followed by the
registered stock controllers through `GameStateTracker.onLobbyStarted`; among
that complete chain, `wallet` is the only direct `serverSettings[...]` lookup.
The later lobby-loaded consumers read Trade-In and restore configuration through
`ShopRequester` objects whose exact `#1513` defaults are disabled and complete,
so the offline producer deliberately does not invent those optional schemas.

The machine-readable source for these assertions is
`tools/account_lobby_consumer_contract.json`. Consumer-contract tests
deserialize the same extended and compressed payloads used by the fake mailbox
and exercise its direct keys, tuple arities, mailbox arities and callback
ordering. This is static and simulated Python coverage; it is not a claim that
every optional stock lobby view or server command outside the map-picker path
is implemented.

The EULA save path uses the exact `CMD_ADD_INT_USER_SETTINGS = 1600` and
`CMD_DEL_INT_USER_SETTINGS = 1601` commands. The offline Account persists those
integer settings in `mods/configs/offline_lan_0922/account_state.json` and
returns them in the next `syncData.intUserSettings`, so accepting the EULA is
not lost across client restarts. A malformed settings request fails without
mutating the last valid state.

`tools/audit_lobby_consumers.py` also scans every code object in the exact
`scripts.pkg` for literal subscripts rooted at the raw Account
`serverSettings` mapping. The build fails if that complete consumer inventory
changes or if a hard producer path is absent. This caught the separate
`predefined_hosts` use of `serverSettings['roaming'][3]`; the typed
`ServerSettings` wrapper only consumes the first three values, so a three-item
test fixture was insufficient even though the wrapper itself initialized.

It also caught a multi-round boundary. `OfflineMapCreator.destroy()` calls
`BigWorld.clearEntitiesAndSpaces()`, which removes the fake Account as well as
the battle entities. Its broad exception handler can fall back to `cancel()`,
which resets ids without clearing entities or spaces. Cleanup now records every
map-create attempt, runs the stronger stock destroy even after a rejected map,
verifies that no Avatar remains, and retries the engine clear directly before
it considers ownership released.

After a clean teardown, the offline Account is recreated through the same
patched constructor. The native `Account.showGUI` synchronization coroutine,
not BattleRuntime, owns the eventual `g_appLoader.showLobby()` call. The next
picker waits for native Lobby space, HangarSpace and the current vehicle model;
opening it synchronously after Account construction would race cursor and
Scaleform ownership. A server-initiated next `battle_start` uses the same gate:
the message is retained and fenced by round id until the native lobby is ready,
so a waiting roster and next start delivered in one network poll cannot replace
an Account while its hangar is still assembling.

`PlayerAvatar.onBecomePlayer()` removes the prebattle dispatcher. On a failed
battle start, exact `#1513` broadcasts IGR state to the still-live Hangar before
normal `Account.onAccountShowGUI` would recreate that dispatcher. Recovery now
creates and verifies the stock dispatcher before constructing the replacement
Account, closing that observable `getFunctionalState()` gap. During final game
shutdown, `guiModsFini` still runs before `SoundGroups.destroy`; a one-shot
instance guard hides only a retired Account/Avatar missing `inputHandler` for
that late call and then removes itself.

The required order is:

```text
OfflineMapCreator.destroy()
  -> restore_lobby_account()
  -> Account.showGUI() / native synchronization
  -> native g_appLoader.showLobby()
  -> wait for Lobby + HangarSpace + vehicle model
  -> if local player is room host, open the next TrainingSettingsWindow
```

## Stock map-selection lifecycle

Before the local Account creates the lobby, a chain-safe adapter intercepts the
exact `LobbyHeader.fightClick(self, mapID, actionName)` boundary. Exact `#1513`
Flash stores that Python callback when `LobbyHeaderMeta` first binds its script;
patching the class after `HANGAR_READY` can repaint the button but leaves Flash
calling the old bound function. The first click now joins the LAN waiting room
rather than calling the stock prebattle dispatcher. While LAN mode owns the
button it never falls through to retail matchmaking: that unsupported path
opens `Waiting('prebattle/join')` and cannot receive its server completion. The
server elects the first connected 0.9.22 player as room host and includes that
id in `welcome`, `roster` and `battle_start`; only the host opens
`TRAINING_SETTINGS_WINDOW_PY` through the exact
`ViewLoadParams(alias, alias)` contract. A scoped wrapper replaces only that
window instance's arena cache with server-offered standard maps, puts the
editable `LAN SERVER: host:port` endpoint in the native description field and
sends the chosen geometry. Guests remain in the hangar and wait for the
server-owned start. A guest request is rejected before map validation or map
mutation. Waiting-room host departure elects the lowest connected id and the
new host then receives the picker. Unmarked stock training windows continue
down their original methods.

There is one explicit pre-welcome settings path. The first **Battle!** click
starts the connection without opening a window. A failed connection opens the
same native form automatically while retry continues; another click while
connecting can also open it. Its provisional map choice does not confer host
authority: after `welcome`, a guest selection is discarded and only the
elected host may request the start. Manually closing a host picker leaves it
closed until that host clicks **Battle!** again, avoiding asynchronous cursor
recapture while Scaleform is retiring the view.

Before creating the first Account, bootstrap waits until the exact app loader
has entered `GUI_GLOBAL_SPACE_ID.LOGIN` for two consecutive engine ticks.
`personality.init()` loads mods before `personality.start()` starts the native
Start/IntroVideo-to-Login state machine; creating an Account in that interval
lets `LoginState.init()` destroy it with `clearEntitiesAndSpaces()`. The same
clear invalidates an in-flight hangar CompoundAssembler, which explains the
observed `R11_MS-1` resource-dictionary KeyError despite complete vehicle
resources. No vehicle-specific exception or resource replacement is needed.

The Battle adapter is installed immediately before Account promotion, while
the client is still in stable Login space, so the first Scaleform binding sees
the LAN callback. The wrapper then waits for the exact public
`LOBBY_VIEW_LOADED` event, Lobby GUI
space, initialized hangar space and (when present) a completed hangar vehicle
model before declaring the lobby ready. Merely finding an initialized
Scaleform application is insufficient because that object already exists in
the login/EULA space. The hangar timeout starts only after the lobby event, so
first-run EULA interaction is not treated as a startup failure. Raw class
members are preserved so Python 2 unbound-method identity is restored
correctly. A chain-safe `onWindowClose` adapter releases picker ownership when
the user presses Cancel, and programmatic close is idempotent even if Scaleform
has already retired the weak view. The stock window remains
responsible for mouse and cursor behavior; no transparent hotkey overlay or
F12/`0` handler is installed.

A first-chance Windows dump identified a stricter boundary in the picker
action. `updateTrainingRoom` was synchronously closing its own Scaleform view
and then returning `True`. The native dispatcher still attempted to convert
that non-`None` result through the view whose display-object pointer had just
been cleared, producing a `NULL + 0x0c` access violation before battle setup
began. The accepted action is now void, matching the stock/public observer
shape, and the owner closes the picker with `BigWorld.callback(0.0, ...)` only
after the current Scaleform event returns. If `battle_start` arrives first,
the network poll cancels that callback, closes the picker once, and only then
crosses the Account-to-Avatar boundary. There is no synchronous-close fallback.

## Self-drawn LAN waiting room

The LAN room is now presented with the port's own native components. The stock
map window described above remains the fallback for a client that cannot build
them. The room carries the reviewed 0.8.2 waiting-room presentation: the live
room status, one map selector limited to the server map pool, one start button
for the host and one close control. It also presents the players who wait for
the host, which the stock window cannot do. The desktop launcher owns the
server address before the client starts, so the room never edits an endpoint.

Every native call is proved in exact build #1513:

| Interface | Exact evidence |
| --- | --- |
| `GUI.Simple(texture)`, `GUI.Window(texture)`, `GUI.Text(value)` and the component properties used here | `scripts/client/PostProcessing/ChainView.pyc`, `scripts/client/bwobsolete_tests/GUITest.pyc`, `scripts/client/bwobsolete_helpers/PyGUI/Utils.pyc` |
| Texture `system/maps/col_white.dds` on every drawn rectangle | `misc.pkg` member; see the two rendering facts below |
| Font `default_small.font` | `system/fonts/default_small.font` package member |
| `GUI.addRoot`, `GUI.delRoot`, `GUI.reSort` and an overlay at `position.z = 0.1` with `focus` and `moveFocus` | `scripts/client/new_year/fade_window.pyc` |
| `handleMouseClickEvent`, `handleMouseEnterEvent`, `handleMouseLeaveEvent`, `handleMouseButtonEvent` | `scripts/client/PostProcessing/ChainView.pyc` |
| The lobby already attaches `GUI.mcursor` through `BigWorld.setCursor` | `scripts/client/gui/Scaleform/managers/Cursor.pyc` `attachCursor` |

Two rendering facts govern how this room may look. Neither is derivable from
the client scripts; both were established on the real #1513 client and both
contradict what the source reads suggested:

1. An **untextured** `GUI.Simple` or `GUI.Window` draws nothing. `GUI.Window('')`
   does appear in `ChainView.pyc`, so an empty texture is a legal state, but it
   is not a visible one. A build that drew the panel, the buttons and the
   pointer as untextured flat colour rendered only its `GUI.Text`; the buttons
   still worked because hit testing does not depend on drawing.
2. Vertex `colour` is **never applied** to a textured component. A row of test
   quads varying `materialFX` (`SOLID`, `BLEND`, `ADD`), `colour` (white, dark
   blue, green) and texture name (`.dds`, `.bmp`) all drew the same white.

So every visible rectangle carries `col_white.dds` and is white, and all
readable contrast comes from `GUI.Text`, whose `colour` **is** honoured: the
room uses dark labels on the white buttons and light labels over the hangar.
Hover feedback recolours the label rather than the button. The panel itself
stays untextured and therefore invisible, which keeps the hangar visible behind
the floating text.

A child component's `position` in `CLIP` mode is relative to its **parent**
rect, not the screen. A pointer parented to the 680 px panel therefore tracked
at exactly half the mouse displacement in a 1360 px window. The drawn arrow is
a set of `GUI.addRoot` components at absolute clip coordinates, sized in
`PIXEL`, so it follows the cursor one-to-one at any resolution.

`shadow` and `dropShadow` appear in no #1513 client script, so the room does not
set them. `wg_inputKeyMode` is proved only for the Scaleform overlay component,
so the room sets it optionally and logs a skip.

Static inspection cannot prove that a native component receives mouse events
while the Scaleform lobby is displayed. The room logs the surface it built, and
a client that raises during construction keeps the stock map window.

## Battle and entity lifecycle

The client delegates space, mapping, Avatar construction, camera setup and
teardown to the exact `OfflineMapCreator`. It temporarily selects the normal
battle branch while `PlayerAvatar.onBecomePlayer` runs, but preserves the one
native `AvatarFilter` established before world entry. A strict local mailbox
implements only the exact early Account/Avatar/Vehicle server calls needed by
this client.

The Lobby-to-Avatar transition also follows the exact `#1513` native ownership
order. It requires a fully initialized HangarSpace, calls
`PlayerAccount.onBecomeNonPlayer()` so chat, all Account helpers, current and
preview vehicles, HangarSpace, camera, input handlers, callbacks and geometry
are retired by their native owners, verifies both HangarSpace readiness flags
are false, and only then calls `BigWorld.clearEntitiesAndSpaces()`. The reverse
Avatar-to-Account transition runs `PlayerAvatar.onBecomeNonPlayer()` before
`OfflineMapCreator.destroy()` for the same reason. Calling the bulk clear first
leaves global managers holding an object whose instance dictionary has already
been erased. Every cleanup boundary remains best-effort if an earlier one
fails; if neither a clean Avatar teardown nor a replacement Account can be
proved, the fake WoT connection is retired instead of leaving a LOGGED_ON
client without a valid player. During synchronous map creation,
`game.abort()` is scoped to a recoverable Python failure so a rejected arena
cannot silently schedule process shutdown; the original function is restored
without overwriting a newer third-party wrapper.

`AvatarObserver.remoteCamera` is not a Python helper object in this build. Its
exact `REMOTE_CAMERA_DATA` alias is a fixed dictionary with `time` (`FLOAT64`),
`shotPoint` (`VECTOR3`), and `zoom` (`UINT8`); the producer now supplies that
mapping with a zero `Math.Vector3`. The inspector pins the hashes of
`alias.xml`, `Avatar.def`, and `AvatarObserver.def`, while the property test
rejects the previously accepted object/`None` shape.

`PlayerAvatar.leaveArena()` calls its base mailbox before the rest of its
native cleanup. The local bridge therefore schedules runtime teardown for the
next engine tick instead of destroying the Avatar reentrantly. LANSession then
retires that participant from only the active server round, restores the local
Account and keeps the waiting-room socket. The server transfers bot authority
to another participating client, or records a draw when no simulator remains;
the departed client cannot consume a duplicate start for the same round and is
re-enabled only by the next waiting roster. Local failure events are accepted
only for the synchronously starting or currently active round; duplicates from
the departed round and delayed failures from an older round cannot retire a
newer Avatar or send a second leave request. Explicit VOIP queries used by
vehicle markers are present and conservatively disabled. The postmortem switch
bridge reproduces the Python-visible outcome of the retail cell attachment
locally: it validates a living friendly target, updates
`ConsistentMatrices.attachedVehicleMatrix`, exposes only that selected
synthetic entity to native lookup and invokes the stock viewpoint callback. It
is deliberately limited to an active postmortem control after the delay;
enemy, dead, absent and not-ready vehicles fail closed.

Local Vehicle creation is gated by the complete pinned `Vehicle.def` SHA-256
`e585c59235ebb2cfbb7857645878ed095360a8efe5df666c055e59a74e6a55c5`,
uses all of its client properties, and publishes the exact
18-item compressed `VEHICLE_ADDED` tuple, native descriptors and native local
entity creation. Exact bytecode shows that `Vehicle.prerequisites()` builds appearance
resources asynchronously: the id returned from client-only `createEntity` can
exist before `BigWorld.entity(id)` is available. The bridge therefore separates
metadata from readiness. Immediately after Avatar creation it creates the local
Vehicle, publishes `VEHICLE_ADDED`, selects `playerVehicleID` while the entity
is not yet in-world, and invokes the native
`ArenaLoadController.invalidateArenaInfo()`. This establishes
`Lobby(4) -> BattleLoading(5)` before a completed space can request
`Battle(6)`. A scoped AppLoader guard makes both callback orders idempotent: a
premature battle-page request first establishes loading, while a late loading
request cannot regress an active battle. The Avatar name/team are seeded from
the same server roster, so `ArenaDataProvider` can resolve the local entry by id
or name. The compatibility wrapper does not repeat the player-id notifier from
inside `PlayerAvatar.vehicle_onEnterWorld`: in exact `#1513`, doing so can mark
`VEHICLE_ENTERED` and start visuals before the native handler initializes its
own-vehicle matrices. Stock `vehicle_onEnterWorld` and its `setClientReady`
mailbox therefore run in their original order; only a later BigWorld callback
accepts the entity after registry presence, `inWorld`, `isStarted`, and a
descriptor are all true. `onVehicleChanged`, client attributes,
`AVATAR_READY`, and `PERIOD` then publish exactly once.

The final `PERIOD` publication is itself a synchronous mailbox boundary in
exact `#1513`: `PlayerAvatar.__onArenaPeriodChange()` calls
`__setIsOnArena(True)`, which immediately calls `moveVehicle(..., False)` and
then `Avatar.base.vehicle_moveWith(flags)` before `updateArena()` returns. The
bridge opens `_client_ready` only after every materialization gate and the
preceding ready publications have passed, but before entering that period
callback. If period publication raises, the input gate is closed again and the
first failure remains latched. The lifecycle audit pins all three stock methods
and their synchronous call order.

Two full Windows dumps isolated the complete client-created Vehicle filter
boundary. The first access violation was in `WGVehicleFilter.syncGunAngles`
inside `Vehicle.__startWGPhysics`; the second run passed that address and
failed in `WGVehicleFilter.syncStabilisedYPR` inside
`PlayerAvatar.__onSetOwnVehicleAuxPhysicsData`. Both native methods reach the
same absent retail server-connection/filter chain. A complete exact-`#1513`
bytecode scan inventories every Python reference to those two methods and finds
four call sites: `Vehicle.__startWGPhysics`, `Vehicle.set_gunAnglesPacked`,
`CompoundAppearance.__onModelsRefresh`, and the Avatar auxiliary-physics
handler. The build audit rejects a missing or additional call site instead of
silently widening this compatibility seam. Reviewed public 0.9.22 observer
layers omit the initial and auxiliary calls; the packed-angle path is specific
to this LAN snapshot implementation, while damaged-model refresh is a stock
late path that must also be safe. During each exact handler only, the
compatibility layer presents a transparent filter proxy whose unsafe method is
a no-op. Physics creation, descriptor initialization, arena bounds, ownership,
`setVehiclePhysics`, visibility, speed providers, packed property values, model
refresh, auxiliary track/RPM updates and filter identity outside the scoped
stacks remain stock. Every scope is removed in `finally`, including when the
original handler raises; normal online execution delegates untouched.

Remote presentations have a separate readiness gate. Their newest health and
pose are coalesced while the #1513 compound assembler loads. A removal drops
the synthetic identity immediately; its late resource callback observes the
missing identity and cannot create an untracked visual. Map loading and local
Vehicle readiness have independent timeouts, and callback handles carry
generation tokens so an uncancellable callback from an earlier attempt cannot
clear a newer round's handle. Two false cross-version assumptions were removed
during review:

- build `#1513` calls `Vehicle.cell.trackRelativePointWithGun(point)`; the
  bridge now exposes that exact mailbox;
- `ARENA_UPDATE` has no `VEHICLE_REMOVED` value and `ClientArena` has no
  corresponding handler. Individual removal destroys the entity, while kill
  state uses the native `VEHICLE_KILLED` update and full cleanup uses the
  arena teardown.

Local input follows the exact stock path: `PlayerAvatar.moveVehicle` calls
`WGVehicleFilter.notifyInputKeysDown` before the explicit Avatar mailbox
relays the same flags. The mailbox must not notify the filter a second time or
bypass the stock movement guards. The client-created Vehicle has no retail
game-server transform stream, so its installed `WGVehiclePhysics` cannot be
the authoritative pose source. The copied 0.8.2 longitudinal, traverse,
terrain and collision
integrator owns the player pose and publishes it through the exact #1513
`Vehicle.model.matrix`, `ConsistentMatrices.__setTarget`,
`PlayerAvatar.getOwnVehicleSpeeds` and `PlayerAvatar.updateOwnVehiclePosition`
boundaries. The exact consumer audit proves that both `_SpeedStateHandler` and
stock shot-dispersion calculation read `getOwnVehicleSpeeds`; overriding only
`Vehicle.getSpeed` leaves the speedometer and movement bloom at zero. This is one pose owner,
not a second integrator layered over native server motion. The adapter now
leaves `PlayerAvatar.getOwnVehicleShotDispersionAngle` untouched: #1513 owns
the visible movement/traverse/turret/shot bloom, with all three motion
coefficients scaled to 25% so a fast light tank remains usable offline. The
trusted local shot samples the same read-only
`VehicleGunRotator.dispersionAngle` before firing, so the smaller HUD circle is
also the actual shot cone. The copied matrix is installed before the native
input handler starts and linked into both the attached and own
`ConsistentMatrices` sources; rebinding only
`_PlayerAvatar__ownVehicleStabMProv` leaves camera-direction and minimap
consumers at the spawn translation. During arcade/sniper changes the adapter
supplies the copied source before the new control's `enable()` and
`focusOnPos()` calculations run. The post-transition listener only verifies
that identity and raises on a stale provider. The exact fixed-turret gun path
receives the same pose through a caller-scoped filter proxy, without replacing
the native `WGVehicleFilter` object.
Remote humans and
bots are different:
retail `WGVehicleFilter` expects game-server pose samples after its input state,
and the offline connection has no such stream. The adapter therefore restores
the 0.8.2 carrier boundary: a Python gameplay vehicle owns authoritative
pose/health/collision and a separate `OfflineEntity` owns the rendered model.
The only version-specific substitution is #1513's verified
`prepareCompoundAssembler` resource path. This restores the map-base formation
and copied bot integrator without feeding a second physics owner.
`BigWorld.Entity.teleport` remains forbidden; #1513 rejects it for an in-world
client Vehicle as `Operation is not allowed`.

LAN pose samples retain the fractional remainder of the nominal 30 Hz
publication interval. Clearing the entire accumulator quantised a 40 FPS
render loop to 20 Hz and 45/50/75 FPS to 22.5/25/25 Hz. At most one current
pose is sent per rendered frame, so recovery from a slow frame never bursts
stale samples.

The player-visible spotting path now copies the 0.8.2 50-metre proximity,
two-height static LOS and allied observer relay. Its deterministic no-skill
memory uses the historical 5--10 second rule's guaranteed ten-second
disappearance bound. Enemy
compound models and their stock marker/minimap visuals cross one visibility
boundary, so an unspotted vehicle cannot remain visible in only one UI layer.
Authority Bot snapshots also retain the 0.8.2 no-rewind rule: the client that
integrates a Bot never reapplies its older server echo pose, while other
clients continue to interpolate those canonical snapshots.

Reload presentation follows #1513's event contract rather than its simulation
tick. The runtime sends `updateVehicleGunReloadTime` once when a reload starts
and once when it completes; the stock HUD derives the continuous remaining
time from `BigWorld.timeExact()`. Re-sending a decreasing value every 100 ms
restarted the client interpolation on each tick and produced a stepped
countdown.

The runtime publishes the exact `PREBATTLE` period tuple before enabling the
round and changes to `BATTLE` only after the countdown. `battle_live` is queued
as the tick-zero wire barrier and the tick thread publishes it before advancing
or emitting a snapshot. The client rejects an older timing tick, records the
receive time in the network thread, and projects the deadline on a monotonic
clock with half the measured RTT; main-thread stalls and wall-clock corrections
therefore cannot rewind the period. The authority's first
canonical bot manifest creates local bots without a server round trip, while
all bot `createEntity` calls are staggered during that countdown. Pose-less
`battle_start.bots` reservations are never inserted into `SnapshotSync`; doing
so allowed an empty map-loading snapshot to tombstone the entire lineup before
the authority manifest arrived.

## Aiming, shooting, health and death

The exact relative-aim call treats the point as relative coordinates. Stopping
gun tracking reconstructs world aim from the current hull yaw. A local shot
uses the public `gunRotator.getCurShotPosition()` boundary, performs client
map/vehicle collision and armor checks, and reports the proposed hit to the
server. The echoed server shot event calls `Vehicle.showShooting()` with the
descriptor's positive `gun.burst[0]` and the authoritative flag. Exact #1513
then cancels the local Avatar's shot-wait callback; zero is not a single-shot
sentinel and leaves the native firing extra unbounded. Remote events use the
same finite presentation without claiming prediction.

Critical-hit calculation follows the same proposal/commit boundary. The
firing client runs the copied 0.8.2 device law against an explicit detached
snapshot of the target descriptor, pose, collision components and critical
state. That calculation cannot change the live target or invoke native kill
and damage-panel callbacks. The proposal carries the target's exact base/ack
token and its pre-critical hull damage separately. If the target was repaired,
extinguished or otherwise revised before the report arrives, the server keeps
the ordinary hull damage and shot feedback but rejects the stale module state
and any obsolete ammo-rack damage amplification explicitly. A monotonic server
event is delivered before the snapshot containing its new HP/critical state;
the client presents stock shot results and battle events, then installs the
accepted revision exactly once.
Repair reports remain pending until the server acknowledges their proposal
revision, so a successful socket write or an older snapshot cannot rewind the
HUD state.

The stock debug controller reads `BigWorld.statPing()` and
`statLagDetected()`, which report the unavailable retail transport in this
client-only battle. A scoped, identity-safe `DebugPanel.updateDebugInfo`
wrapper substitutes only the attached LAN client's measured RTT and connection
state while the offline battle is active, and restores the stock method during
shutdown. The hello frame is sent atomically before `connected` becomes visible
to the poller, so a ping can never precede the required first protocol frame.

Server health is applied through the entity's health callback and the native
Avatar vehicle-health path. Crossing zero publishes `VEHICLE_KILLED`; a dead
local vehicle cannot move or fire, and a dead bot stops movement, targeting and
late fire events. The elimination result freezes all inputs until the server
returns the room to waiting.

Exact #1513 creates one `ArenaDataProvider`, exposes it through
`BattleSessionProvider.getArenaDP()`, and stores a `weakref.proxy` to that same
provider inside `BattleFeedbackAdaptor`. A proxy and its referent cannot pass
an object-identity comparison. The compatibility check therefore verifies the
public shared feedback adaptor, active marker provider and real
`FROM_PLAYER` classifier without inspecting feedback's private proxy identity.
The ABI audit pins both the weak-proxy construction and the setup property's
public forwarding chain.

## AI, room and round boundaries

Humans take real team slots first. The first waiting 0.9.22 player owns map
selection and start; guests cannot race a second map choice into the room. Bots
fill the unoccupied slots so each team has exactly 15 vehicles. Battle-time late joins are rejected to prevent a
16th slot or an incomplete local manifest. A waiting-room membership is not
published to other handlers until its own `welcome` has been sent under the
same state lock, so another player cannot start a battle whose first message to
the new client would arrive before its identity and round assignment.

The elected authority client runs tactical bots using standard-map annotations,
vehicle roles, persistent randomized personalities, bounded line-of-sight
caching and local avoidance of terrain, water, steep slopes, obstacles and
nearby vehicles. The Python server remains canonical for room phase, HP, shot
events, elimination, capture, timeout and the copied five-second result
interval. The next waiting roster
is a synchronization barrier: the previous battle runtime is destroyed before
either the map picker or a queued next battle can cross the native Lobby/Hangar
readiness gate. Per-round phase is monotonic, so a delayed same-round waiting
roster or start denial cannot cancel an accepted battle, and snapshots cannot
be reordered across that barrier.

The pure-data server planner emits revisioned global `bot_orders`, which the
0.9.22 authority now uses for macro targets after reporting bounded visibility
observations. BigWorld terrain, collision, water and slope probes remain local,
and the client planner is a fallback when no server order is available. The
server copies the 0.8.2 standard-mode capture law: a 50-metre radius, one
update per second, at most three capture points per update, defender stop,
empty-base reset and victory at 100 points. Standard battles end by
elimination, capture or the server-owned 15-minute timeout.

The same canonical update drives a narrow defense context for the server
planner. One, two or three invaders request at most one, two or three eligible
responders respectively, selected by distance and vehicle profile speed and
sent only to a base that is currently invaded. The selection is stable across
updates and retains a short clear grace; dead, ungrounded, engine-destroyed or
double-tracked Bots are replaced. Travel overrides route movement but preserves
ordinary visible-target aim and fire admission. No unspotted invader position
is included in a Bot order.

This source wiring is not the same as final bot-behavior acceptance. The
finalized 0.8.2 spawn-congestion/OBB, reverse-steering and baked-route changes
are migrated as source-derived changes; real-client acceptance still has to
check them against #1513 terrain and presentation timing.

## Server-hosted bot authority

When every prerequisite is donated, the standalone Python 3 server itself owns
the bot authority under the reserved identity 0, and every connected client
runs in follower mode. The server hosts the same engine-free simulation the
elected client ran (BotRuntime, artillery arc queue, projectile ledger, combat
and critical-damage law) on its fixed 30 Hz tick, and feeds the results
through the same admission methods, so the wire protocol and follower
rendering are unchanged. World queries are answered from the shipped baked
navigation graphs, destructible catalogs and foliage maps; the launcher points
the server at the client install's `mods/configs/offline_lan_0922` tree
through `WOT_0922_SERVER_DATA`. Capture bases come from the navigation
graph's `objective_bases`.

Vehicle data stays interpreted only on the #1513 client. A client donates the
eligible-vehicle catalog (`descriptor_catalog`) after joining; at battle start
the server runs the mirrored 0.8.2 lineup law over that catalog and asks the
room host for the round's descriptor projections (`descriptor_request` /
`descriptor_bundle`) during the loading barrier. The server never returns a
#1513 round to a client-simulated battle: a start without a catalog or a
baked world is refused (`start_denied`), and a failed bundle, a failed
donation, or a donor that disconnects mid-request ends the round and returns
the room to the waiting phase with the failure reason
(`authority_status: failed`) shown on every client. Descriptor projection
keeps a bounded 30-second wait and native destructible identities keep an
independent 120-second bound after the server authority starts; expiring
either bound ends the round the same way.

Server-side world answers replace native probes with baked data. Direction
corridors, world receipts and obstacle sweeps use graph links and heights.
Line of sight, firing lanes and SPG arcs are stopped by the height field,
by every live destructible catalog box, and by the baked solid-static
occluders (`0.9.22/occluders`, produced by `tools/bake_occluders_0922.py`
from the BSMO collision bounds of every plain-static and preserved-structure
instance), with baked foliage concealment on top.

The server destroys map objects with the retail laws over donated native
values. The destructible catalogs (schema v4) bake every instance's native
wire identity and exact transform scale from the compiled `space.bin`:
WGDE table "1" rows are `(chunk_id, global_item_begin, item_count)`, each
table "2" row is one native item spanning an inclusive reference range in
table "3" (`ref_begin > ref_end` is a valid empty item that still consumes
an item index), and a table "3" reference selects an SpTr row when bit
`0x80000000` is set and a BSMI row otherwise. All 61,625 emitted instances
across the 41 shipped maps map one-to-one onto native wires under this
contract. A loading client therefore donates each map's complete
destructible identities immediately, without waiting for streamed chunks
(`destructible_map`: locator signature, native `chunk_id`/`item_index`,
healths pre-scaled by the native
`DestructiblesCache.scaledDestructibleHealth` with the baked exact scale,
`kineticDamageCorrection` and `unitVehicleMass`). The donation is
transactional: a missing or mismatched descriptor, an invalid scale, or a
duplicate wire fails the whole bundle. Streamed chunk scanning remains for
local collision bookkeeping and asserts that every streamed identity equals
the baked wire, failing closed on drift. Server authority starts only when
the installed donation covers every interactive instance in the baked
catalog; an incomplete census ends the round instead of simulating a
partial world. Bot hulls crush
fragiles and fell trees and columns
with the exact `0.5*m*v^2*0.00015` kinetic gate; AP-family shells pass
through items at or under 19 scaled HP for the fixed 25 mm piercing loss and
stop otherwise; every destruction is published through the ordinary
`destructible` events and projectile receipts, and human-reported
destructions mark the same server ledger so bots and shells respect them.

Remaining approximations, all still requiring exact-Windows acceptance:
occluders and destructibles use authored bounding boxes rather than
triangle-exact meshes; a felled column keeps its original box; bot shots
against vehicles resolve on hull oriented boxes with donated `primaryArmor`
per face instead of native per-plate hit testers; and bot muzzles are
approximated from donated mount offsets instead of the native `HP_gunFire`
node.

## Garage panel versus battle law: one dataset

The garage panel and the battle law must run on the same numbers. #1513 builds
those numbers in one place, and the port now calls that place instead of
approximating it:

```
gui/Scaleform/daapi/view/lobby/hangar/VehicleParameters.pyc
  -> items_parameters/params_helper.getParameters(item)
  -> items_parameters/params.VehicleParams.__init__
       factors = items_parameters/functions.getVehicleFactors(vehicle)
  -> items/utils.updateAttrFactorsWithSplit(descr, crewCDs, eqs, factors)
       every mounted consumable  -> Artefact.updateVehicleAttrFactors
       every optional device     -> Artefact.updateVehicleAttrFactors
       VehicleDescrCrew(descr, crewCDs, mainSkillBonuses)
         .onCollectFactors(factors)
         .camouflageFactor        -> factors['camouflage']
         .onCollectShotDispersionFactors -> factors['shotDispersion']
```

`loadout.attribute_factors` runs exactly that chain for the player's mounted
descriptor, the crew compact descriptors behind `g_currentVehicle.item.crew`
and the mounted consumables, and returns the same `factors` dictionary. Bots
run it with `utils.generateDefaultCrew`, so both sides differ only in the crew
that feeds them. Off the client the call returns `None` and the pure-data
fallback in `loadout.py` applies #1513's own curve,
`factor = 0.57 + 0.43 * efficiency` (`VehicleDescrCrew._processSkills`).

`VehicleParams` in this build exposes 38 properties. The table lists every one
that a crew skill, an optional device or a consumable can move, plus the
factors that never reach the panel but do reach the battle.

| Parameter | Garage source | Battle source | Same source | Status | What is missing |
|---|---|---|---|---|---|
| view range `circularVisionRadius` | `utils.getCircularVisionRadius` = `turret.circularVisionRadius * miscAttrs['circularVisionRadiusFactor'] * factors['circularVisionRadius']` | `battle_runtime._vision_radius` -> `spotting.effective_view_range` with `profile['vision_factor']` = the same factor entry | yes | done | nothing |
| stereoscope | `Stereoscope.updateVehicleAttrFactors` assigns `factors['circularVisionRadius'] = 1.25 / miscAttrs[...]` in both aspects, so the panel shows it always | the same value, divided back out of the base factor and reapplied after `activateWhenStillSec` | yes | done, deliberately gated | nothing |
| concealment still / moving | `params.__getInvisibilityValues` -> `utils.getClientInvisibility` -> `computeBaseInvisibility(factors['camouflage'], camoId)` then `(base + factors['invisibility'][0]) * factors['invisibility'][1]` | `battle_runtime._base_invisibility` + `spotting.effective_camouflage` with the same two factor entries and the same aspect split | yes | done | nothing |
| concealment after firing | `invisibilityFactorAtShot` from `gun` | `_shot_invisibility_factor` reads the same field | yes | done | nothing |
| camouflage net | `CamouflageNet` adds `invisibilityDeltas['camouflageNetBonus']` to the `WHEN_STILL` aspect only | the same aspect, gated on `activateWhenStillSec` | yes | done, deliberately gated | nothing |
| garage paint | `getClientInvisibility` passes `vehicle.getBonusCamo().id` into `computeBaseInvisibility` | the id captured in the garage snapshot, passed into the same call | yes | done | nothing |
| reload `gun/reloadTime` | `utils.getReloadTime` = `gun.reloadTime * miscAttrs['gunReloadTimeFactor'] * factors['gun/reloadTime']` | `GunState.reload` multiplies by `loadout['reload_factor']`, which is that product | yes | done | nothing |
| aim time `gun/aimingTime` | `utils.getGunAimingTime` | `GunState.aim_time` multiplies by `loadout['aim_time_factor']` | yes | done | nothing |
| shot dispersion | `utils.getClientShotDispersion(descr, factors['shotDispersion'][0])` | `GunState.base_dispersion` multiplies by `loadout['dispersion_factor']` = the same entry | yes | done | nothing |
| turret traverse | `utils.getTurretRotationSpeed` = `turret.rotationSpeed * factors['turret/rotationSpeed']` | `_publish_targeting_info` sends `turret.rotationSpeed * loadout['crew_factor']` | yes | done | nothing |
| gun traverse | `utils.getGunRotationSpeed` (panel uses it only on a turretless hull) | `_publish_targeting_info` sends `gun.rotationSpeed * loadout['gun_rotation_factor']` | yes | done | nothing |
| hull traverse `vehicle/rotationSpeed` | `utils.getChassisRotationSpeed` = `chassis.rotationSpeed * factors['vehicle/rotationSpeed']`, then divided by the average terrain resistance | `vehicle_physics.derive_params` multiplies `rotSpd` by the same entry; the panel's division by resistance is presentation | yes | done | nothing |
| terrain resistance | `params.__getTerrainResistanceFactors` = `factors['chassis/terrainResistance'] * physics['rollingFrictionFactors']` | `derive_params` multiplies `terrainResist` by exactly that product | yes | done | nothing |
| engine power | `params.enginePower` = `physics['enginePower'] * factors['engine/power']` | `derive_params` multiplies `powerW` by the same entry | yes | done | nothing |
| radio range | `utils.getRadioDistance` = `radio.distance * factors['radio/distance']` | the battle has no distance gate at all; the effective value is printed at battle start | matches the client | done, see below | nothing on the client; #1513 has no battle reader for it |
| repair speed | not a `VehicleParams` property in #1513, and no client code reads `factors['repairSpeed']` or `miscAttrs['repairSpeedFactor']`; the cell owns the formula | `critical_damage.tick_repair` takes `loadout['repair_factor']` = `factors['repairSpeed']`, the toolbox factor, and the large repair kit | same inputs, our own formula | done | nothing the client can supply; #1513 has no client formula |
| dispersion factors: movement, hull traverse, turret traverse, after shot | not a `VehicleParams` property; the panel shows only the aimed angle | `GunState.tick` reads `chassis.shotDispersionFactors` and `gun.shotDispersionFactors` from the descriptor | descriptor, both sides | done | nothing |
| Snap Shot, Smooth Ride | no `_skillProcessors` entry in this build's `VehicleDescrCrew`, and no client writer for `chassis/shotDispersionFactors`; the cell owns them | `loadout.py` keeps the 0.8.2 constants 0.925 and 0.96 | no | cannot be proved from #1513 | a #1513 writer for `chassis/shotDispersionFactors`; this build has none, so the two constants stay |
| a bot's own stereoscope | the panel is about the player's vehicle only | `_vision_range_pair` stores the moving and armed ranges per bot, `_note_source_stillness` stamps when the bot stops, and `_source_view_range` picks between them on the same `activateWhenStillSec` the player uses | not applicable | done | nothing |
| crew level from ventilation | `StaticAdditiveDevice.updateVehicleDescrAttrs` adds 5 to `miscAttrs['crewLevelIncrease']`; `TankmanDescr.efficiencyOnVehicle` returns it as the per-crewman addition | inside `attribute_factors`, so it moves every factor above at once | yes | done | nothing |
| crew level from food and Brothers in Arms | `utils._sumCrewLevelIncrease(eqs)` -> `factors['crewLevelIncrease']`; `skillsConfig.getSkill('brotherhood').crewLevelIncrease` when every slot has the skill | the same chain | yes | done | nothing |
| reload with the loader knocked out | the garage panel has no injured-crew state; `VehicleDescrCrew` takes `activityFlags` and a dead slot adds nothing to `summLevel` | `device_damage.crew_stat_factor(..., 'reload')` -> `1.043 / 0.57` = 1.830, applied in `GunState.commit_fire` | derived from the same curve | done | the intra-clip `clip_reload` is not multiplied |
| aim time and dispersion with the gunner knocked out | same `VehicleDescrCrew` curve on `gun/aimingTime` and the shot-dispersion factor | `device_damage.crew_stat_factor(..., 'aim_time'/'dispersion')` -> 1.830, applied every ammo tick | derived from the same curve | done | nothing |
| turret and gun traverse with the gunner knocked out | `_updateGunnerFactors` sets `turret/rotationSpeed` and `gun/rotationSpeed` to the factor | `device_damage.crew_stat_factor(..., 'turret_speed')` -> `0.57 / 1.043` = 0.546 | derived from the same curve | done | the gun's own rotation speed reuses the turret entry |
| mobility with the driver knocked out | `_updateDriverFactors` divides `chassis/terrainResistance[0..2]` by the factor | `device_damage.crew_stat_factor(..., 'mobility')` -> 0.546, applied to the throttle | no; the factor matches, the knob does not | partial | #1513 moves terrain resistance, this port moves the throttle; hull traverse has no crew term at all |
| radio range with the radio operator knocked out | `_updateRadiomanFactors` sets `radio/distance` to the factor | `device_damage.crew_stat_factor(..., 'signal')` -> 0.546 | derived from the same curve | not consumed | #1513 has no battle reader for radio range either, so nothing reads the factor |
| the commander bonus when he is knocked out | `_calcLeverIncreaseForNonCommander` drops `commanderLevel / 10` from every other role, and `circularVisionRadius` falls to his own factor | `device_damage.crew_stat_factor` applies `1.043` to times, `1/1.043` to speeds and `0.546` to view range | derived from the same curve | done | nothing |
| view range with the crew knocked out | `_updateCommanderFactors` and `radioman_finder` | `critical_damage.stat_factor(entity, 'vision')`, floored by `device_damage.clamp_vision_factor` | derived from the same curve | done | the floor is this port's own safety bound, not #1513's |
| a whole crew knocked out, and a burning tank | `VehicleDescrCrew.isCrewActive` is `True in activityFlags`; `isFire=True` forces every role efficiency to zero | neither state exists; `crew_active` follows health only | no | missing | no `health > 0 but crew out` state, and a fire does not zero the crew |
| `commander_universalist` covering a dead role | `_calculateSkillEfficiencies` adds `level / numInactive * 0.5` per dead slot when the commander lives | not modelled | no | missing | the only coverage law in #1513 |
| a bot's own crew injury | not applicable | bots apply `device_damage.crew_stat_factor` for dispersion, reload, turret speed and mobility; `critical_damage._crew_impaired` stays empty for a bot | partly | partial | a bot has no crew-KO view-range or hull-traverse term, and secondary roles are ignored |

Ventilation does not move the concealment number, in the panel or in the
battle, and that is #1513's own law rather than a gap. Ventilation is a
`StaticAdditiveDevice` writing `miscAttrs['crewLevelIncrease'] = 5`, which
`TankmanDescr.efficiencyOnVehicle` returns as the per-crewman addition, so it
raises the level of skills a crewman already has. Concealment reads
`factors['camouflage']`, which `VehicleDescrCrew.onCollectFactors` fills from
`_camouflageFactor`; `_calculateSkillEfficiencies` sets that skill's efficiency
to `0.0` when the crew has no `camouflage` skill, and `_processSkills` turns
that into the constant `0.57`. A crew without the camouflage skill therefore
keeps the same concealment at any crew level. Ventilation does move view range
by about 2.15% and reload, aim time, traverse and radio distance by about
2.27%, and those are the values to compare against the panel.

Two consequences are deliberate and must not be "fixed" into agreement:

- the panel shows the stereoscope and the camouflage net unconditionally,
  while the battle waits `activateWhenStillSec`. That is retail behaviour;
- the panel divides `chassisRotationSpeed` by the average terrain resistance
  to present one mobility number. The physics keeps the two separate.

Radio range has no battle consumer because #1513 itself has none. A search of
every `scripts.pkg` member for `radioDistance` returns eleven modules, and all
of them are lobby presentation or item definitions: `params.pyc`,
`params_helper.pyc`, `formatters.pyc`, `fitting_select_popover.pyc`,
`tooltips/module.pyc`, three `locale` tables, `skills_components.pyc` and
`skills_readers.pyc` for the radio operator skill, and `vehicles.pyc` for the
descriptor field. `Avatar.pyc`, `Vehicle.pyc` and the arena and vision modules
never read it. So the exact client does not gate spotting, relay or anything
else on radio distance, and a distance gate in this port would diverge from the
build it targets rather than match it. The effective distance is printed at
battle start so the value can still be compared with the panel. What WG's own
server did with the value cannot be proved from a client, and is not claimed.

The `PARAMS` lines printed once per battle start carry the effective view
range, both concealment values, the after-shot factor, reload, aim time,
dispersion and its four factors, the three traverse speeds in degrees, the
three terrain resistances, engine power, both speed limits, the repair factor
and the radio distance, plus a `source=` field that says whether the numbers
came from the client factor dictionary or from the fallback.

### What a live battle actually reported, and the two defects it exposed

The table above states the intended source for each row. A battle on the exact
client reported `source=fallback`, `vision_factor=1.0000` and `vents=False`
while ventilation was mounted, so two separate defects kept every row on the
fallback path:

- `loadout._artefact` tested a mounted item with `value == 0`. #1513
  `FittingItem.__eq__` guards only against `None` and then reads `other.intCD`,
  so the comparison raised `'int' object has no attribute 'intCD'` and
  `attribute_factors` returned `None` for the whole loadout. The guard now
  tests the integer case only after the type check;
- the battle built the player's descriptor with `VehicleDescr(typeName=...)`.
  That descriptor carries the stock modules and an empty `optionalDevices`, so
  no mounted device could be seen at all. `_local_battle_descriptor` now builds
  it from the garage item's own `makeCompactDescr()`, which is the compact
  descriptor #1513 itself uses to build `gui_items.Vehicle.descriptor`.

The same fitted compact descriptor is donated to the server authority, so the
health, armour and gun the server resolves damage against are the ones the
garage panel measured. The server forgets each player's projection when a round
ends, because a player can refit between rounds.

`conceal_move == conceal_still` in that report is not a defect.
`VehicleDescriptor.computeBaseInvisibility` applies the same crew, vehicle and
camouflage terms to both members of `type.invisibility`, and a light tank
carries the same pair for moving and still.

## A client-only vehicle has no engine-owned filter

`BigWorld.WGVehicleFilter` is an entity filter. The engine allocates its native
implementation only when an entity owns the filter, and every Python method on
the wrapper reads that implementation through one virtual getter,
`mov eax, [this + 0x14]; ret`. A filter that
`vehicle_systems/model_assembler.createVehicleFilter` builds and that no entity
adopts keeps `this + 0x14 == 0` for its whole life: the base constructor at
`0x0065cdf0` writes the zero, and no method in the wrapper's table reaches the
allocating virtual.

That is fatal, not recoverable. `setTracksSpeed` (`0x006e30f0`) parses its four
arguments, fetches the filter, and on NULL runs
`MF_ASSERT_DEV FAILED: pFilter` from
`wot\lib\wot_entity_filters\py_wg_vehicle_filter.cpp(555)`. BigWorld's
dev-critical handler calls `_set_abort_behavior(0, _WRITE_ABORT_MSG |
_CALL_REPORTFAULT)` and then `abort()`, so the process leaves with exit code 3,
no CRT text, no Windows Error Reporting event, and nothing in `python.log`. A
`try`/`except` cannot catch it, and the client exposes no way to read `pFilter`
first.

A full-memory dump taken at process termination proved this on build
`32183ee`: the receiver was a `BigWorld.WGVehicleFilter` with `pFilter == 0`,
the arguments were `(0.0, True, 0.0, True)`, and of the 59 live filter
instances in that process exactly one, the player's entity filter, had an
implementation. The abort landed on the first frame that moved the bots,
before the diagnostic line that follows the call.

Exit code 3 alone does not prove an assertion. An unhandled access violation
leaves the process the same way, as the outline crash below shows. Search the
crashing stack for the saved `EXCEPTION_RECORD` and `CONTEXT` before you name
the cause.

The rule for this port: on a client-only vehicle, treat every
`WGVehicleFilter` method as a call that can terminate the process, not as a
call that can raise. The same assertion exists in
`wot\lib\wot_entity_filters\wg_gun_angles_filter.hpp`, which is the trap
`compat.py::_OfflineVehicleFilterSyncProxy.syncGunAngles` already avoids.

### Animated bot belts are closed until bots are real entities

`bot_track_animation` stays off by default and the port no longer calls
`setTracksSpeed` at all. What remains behind the flag is the stock pair,
`PyTrackScroll.setMode` and `PyTrackScroll.setExternal`, which is what #1513's
`CompoundAppearance.changeEngineMode` and `updateTracksScroll` call and which
never touches `WGVehicleFilter`. That pair is safe, and it is also inert for a
bot:

- `PyTrackScroll.setData` stores a raw, non-owning pointer to the native filter
  at the controller's `+0x14`, and `activate` registers a 20 Hz updater whose
  first instruction pair is `mov esi, [edi + 0x14]; test esi, esi; je`. With a
  NULL filter the updater returns without reading its inputs.
- The belt scroll the chassis fashion draws comes from the filter's own routine
  at `0x006eee00`, which converts the override fields `+0x4a8`/`+0x4ac`/`+0x4b0`
  /`+0x4b1` into `+0x48c`/`+0x490`, the pair `movementInfo` exposes. Both live
  on the native filter.
- Peng's own battle log confirms it from the Python side: for a whole battle
  `leftScroll`/`rightScroll` read `0.0` and `leftContact`/`rightContact` read
  `True`, which are exactly the values `TrackScroller`'s constructor at
  `0x00762f80` writes. The updater never ran once.

So animated bot belts require bots built as real
`BigWorld.createEntity('Vehicle', ...)` entities, so that the engine owns each
filter. That is the previously estimated three-round change, and it is Peng's
call whether to spend it. Nothing smaller can move the belts.

## The vehicle outline outlives the wreck model swap

Exit code 3 does not always mean an assertion. The second full dump, taken at
`11:50:28` on build `47664e3` after Peng killed an enemy vehicle, carries the
same `-t` signature as the first: one thread, `Eip 0x00010002`, `Ebx 3`, no
`ExceptionStream`, nothing in `python.log`. It is not an `MF_ASSERT_DEV`. No
assertion text exists anywhere on the stack. BigWorld's unhandled-exception
filter formats its own report instead, and that report is still in place at
`0x0019e974`:

```
The BigWorld Client has encountered an unhandled exception and must close
(EXCEPTION_ACCESS_VIOLATION : 0xC0000005)
```

with `Read @ 0x00000008` at `0x001af1c8`. The filter keeps the real
`EXCEPTION_RECORD` at `0x001af738` and the faulting `CONTEXT` at `0x001af788`.
They give the fault directly: code `0xC0000005`, `ExceptionAddress 0x00ab9e31`
(RVA `0x6b9e31`), a read of address `0x00000008`, `Eax 0`, `Esp 0x001afa74`,
`Ebp 0x001afa78`. Search the stack for a saved `CONTEXT` before you conclude
that a BigWorld exit-code-3 dump is an assertion.

The frame-pointer chain from `Ebp 0x001afa78` reconstructs the whole call, and
the exe's RTTI names most of it:

| Frame | Return address | Function | Identity |
| --- | --- | --- | --- |
| 0 | fault at `0x00ab9e31` | RVA `0x6b9e20` | scene-object transform lookup |
| 1 | `0x00af3bd7` | RVA `0x6f3b90` | walks one outline list |
| 2 | `0x00af4dff` | RVA `0x6f4db0` | `BW::wargaming::EdgeDrawer` frame update |
| 3 | `0x00631af3` | RVA `0x231aa0` | `BW::CanvasApp::tick`, vtable slot 4 |
| 4 | `0x0063af6d` | RVA `0x23af30` | `BW::MainLoopTasks::tick` |
| 5 | `0x0063af94` | RVA `0x23af30` | `BW::MainLoopTasks::tick`, outer group |
| 6 | `0x0061e4be` | RVA `0x21e370` | holds the literal `MainLoopTask::tick` |
| 7 | `0x00612d56` | RVA `0x2121e0` | application frame |

There is no Python frame on this stack. The crash is the engine's own
per-frame outline pass, one or more frames after the mod's Python call
returned.

The faulting routine takes a receiver, a matrix output pointer and a two-part
key. It calls the hash lookup at RVA `0x6b9da0`, which returns the record or
NULL, and then reads the record without a test:

```
0x6b9e2c  call 0x6b9da0                      ; find(handle, kind)
0x6b9e31  imul ecx, dword ptr [eax + 8], 0x54 ; eax == 0  ->  read at 0x8
```

The receiver at `0x0fab9900` reads `BW::SpatialFeature` through its vtable.
Its `std::unordered_map` at `+0x20` held 3256 records keyed by a
`(uint32 handle, uint8 kind)` pair, and each record indexes an `0x54`-byte
entry in the space's transform array. The call means "give me the world matrix
of scene object (handle, kind)". A missing scene object is a NULL return and an
immediate access violation.

The dump then names the missing object exactly. The `EdgeDrawer` at
`0x1711d780` held one entry in its list at `+4`, and that entry's key list held
one key, `(0x21, 12)`. The map holds 30 records of kind 12. Their handles are
the odd numbers `0x01` through `0x3b` plus `0x01000000`, and `0x21` is the
single one absent. The one scene object the outline still pointed at was the
only vehicle compound the client had just released. The second list, at
`+0x10`, held one entry whose seven keys were all present, so it would have
drawn normally.

`python.log` closes the loop:

```
11:50:28.375  EFFECT armour_hit  at=(-185.0, 26.7, -158.2)
11:50:28.478  WRECK swap id=2130706475 pose=(-185.9, 25.9, -159.9)
11:50:28.518  EFFECT world_explosion ...
```

The crash report is stamped `08.19.2026 at 11:50:28`, and a
`BW::PyVectorCopy<Vector3>` holding `(-184.887, 26.639, -157.859)` sits in the
heap block next to the outline list.

The port creates the dangling reference itself.
`battle_runtime.py::_update_target_outline` picks the nearest aimed-at, alive,
visible remote vehicle at most every 0.125 s and calls
`BigWorld.wgAddEdgeDetectEntity(vehicle.bw_entity, color, 0, False)`. The
engine records that entity's current compound handles. When the vehicle dies,
`_apply_health` asks `remote_vehicle.py::request_wreck` for the destroyed
compound, `BigWorld.loadResourceListBG` answers on a later frame, and
`attach_wreck_model` assigns `self.bw_entity.model = model`. That releases the
old compound and removes handle `0x21` from the space. No step between the two
calls `wgDelEdgeDetectEntity`. `_clear_target_outline` runs only on the next
outline pass, on `_stop_remote_visual`, or on teardown, so the swap can land in
any of the roughly four frames inside the refresh window. The next
`CanvasApp::tick` reads the freed handle.

Exact `#1513` never allows that window. `CompoundAppearance.__onModelsRefresh`
is the stock damaged-model swap, and its bytecode order is `deactivate(False)`,
`__prepareSystemsForDamagedVehicle`, `__setupModels`, `setVehicle`, `activate`,
`__reattachComponents`. `CompoundAppearance.deactivate` calls
`ComponentSystem.deactivate`, which reaches
`vehicle_systems/components/highlighter.py::Highlighter.deactivate`, and that
method calls `BigWorld.wgDelEdgeDetectEntity(vehicle)` whenever a highlight is
on. The retail client always drops the edge-detect registration before it
replaces the compound model.

The rule for this port: `wgAddEdgeDetectEntity` binds the entity's current
compound, not the entity. Remove the registration before any code replaces
that compound. The smallest fix is to clear the outline where the port already
drops the player's other attachments to a vehicle that just died, next to
`self._release_target_lock(engine_id)` in `_apply_health`, with the two lines
`_stop_remote_visual` already uses:

```python
if self._outlined_engine_id == engine_id:
    self._clear_target_outline()
```

`_update_target_outline` rejects a dead vehicle, so the outline cannot return
and no second guard is needed. `request_wreck` runs later in the same method
and its load is asynchronous, so the removal is always ordered before the
model changes.

Every step above except one comes from the dump, the exe disassembly and the
exact `#1513` bytecode. The inference is that the outlined vehicle is the
vehicle Peng killed: the `EdgeDrawer` stores scene handles and not entity ids,
so the link rests on the single `WRECK swap` line immediately before the crash
and on `0x21` being the only kind-12 handle missing. Only a run on the exact
Windows client can prove that the fix removes the crash.

### The third dump repeats the same fault on a build that predates the fix

A third full dump was taken at `12:28:33` after another kill. It is the same
fault, the same call chain and the same missing key as the dump above. The
process that produced it did not carry the outline fix.

The dump holds one thread, `Eip 0x00010002`, `Ebx 3` and no `ExceptionStream`.
The unhandled-exception filter's report sits at `0x0019e974`:

```
Application C:/World_of_Tanks_0.09.22.00.01_CH_1513_HD/WorldOfTanks.exe
crashed 08.19.2026 at 12:28:33

Message:
The BigWorld Client has encountered an unhandled exception and must close
(EXCEPTION_ACCESS_VIOLATION : 0xC0000005)
```

with `Read @ 0x00000008` at `0x001af1c8`. The saved `EXCEPTION_RECORD` at
`0x001af738` reads code `0xC0000005`, `ExceptionAddress 0x00ab9e31`,
`NumberParameters 2`, parameters `0` and `8`. The saved `CONTEXT` at
`0x001af788` reads `Eax 0`, `Esi 0x0fab9b80`, `Esp 0x001afa74`,
`Ebp 0x001afa78`. The frame-pointer chain repeats the second dump exactly:

| Frame | Return address | Function | Identity |
| --- | --- | --- | --- |
| 0 | fault at `0x00ab9e31` | RVA `0x6b9e20` | scene-object transform lookup |
| 1 | `0x00af3bd7` | RVA `0x6f3b90` | walks one outline list |
| 2 | `0x00af4dff` | RVA `0x6f4db0` | `EdgeDrawer` frame update, first list |
| 3 | `0x00631af3` | RVA `0x231aa0` | `BW::CanvasApp::tick` |
| 4 | `0x0063af6d` | RVA `0x23af30` | `BW::MainLoopTasks::tick` |
| 5 | `0x0063af94` | RVA `0x23af30` | `BW::MainLoopTasks::tick`, outer group |
| 6 | `0x0061e4be` | RVA `0x21e370` | holds the literal `MainLoopTask::tick` |
| 7 | `0x00612d56` | RVA `0x2121e0` | application frame |

RTTI names both objects from their vtables: the receiver `0x0fab9b80` is
`BW::SpatialFeature` (`.?AVSpatialFeature@BW@@`, vtable `0x0150c288`) and the
caller's owner `0x1701d840` is `BW::wargaming::EdgeDrawer`
(`.?AVEdgeDrawer@wargaming@BW@@`, vtable `0x0150e748`).

The arguments read directly from the frame: matrix output `0x001afaac`,
handle `0x21`, kind `12`. The `SpatialFeature` map at `this + 0x20` holds
3260 records, 30 of them kind 12, and `(0x21, 12)` is absent. The drawer's
first list at `+4` holds one entry whose single key is `(0x21, 12)`. Its
second list at `+0x10` holds one entry with seven keys, and every one of them
is present. The process holds exactly one `EdgeDrawer`, and the global pointer
that `wgAddEdgeDetectEntity` and `wgDelEdgeDetectEntity` read, `0x01d20c70`,
holds that same `0x1701d840`. So the fault is again an outline that points at a
compound the client already released, on the one drawer this port writes to.

The build is the difference. The client that crashed loaded
`mods/0.9.22.0.1/org.peng.offline_lan_0922_0.4.0.wotmod`, and that file still
held the pre-fix build. Compiling `battle_runtime.py` from both revisions and
comparing every code object proves it: all 373 code objects in the shipped
`battle_runtime.pyc` are byte-identical to `47664e3`, and exactly the two
functions the fix changed, `BattleRuntime._apply_health` and
`BattleRuntime._cleanup`, differ from `f1a1250`. `python.log` shows the
crashing session started at `12:26:04` and loaded that same file, which
`f1a1250` at `12:18:36` never reached. That file has SHA-256
`6bb17963e458f7c679f57bcc0929154870bbf3c68656dc390c7b5d2cc92291fa`. The WOTMOD
left in `0.9.22/dist` is older still, so the fix reaches the client only
through a fresh build.

The dump also shows the second, subtler half of the same trap, and explains why
several kills before this one were harmless. The `BattleRuntime` instance
dictionary in the dump holds `_outlined_engine_id = None`, so Python had
already run `_clear_target_outline`, yet the engine still held the key. The
removal path explains that. `wgDelEdgeDetectEntity` (RVA `0x252cf0`) does not
remove by entity. It reads the entity's *current* model at `+0xe8`, takes the
scene key at that model's `+0x10`, and passes it to `EdgeDrawer::removeKey`
(RVA `0x6f4480`), which scans the first list for `handle` and `kind` and then
the second, and does nothing when neither matches. Once
`attach_wreck_model` has run `self.bw_entity.model = model`, the entity reports
the wreck's key, so the removal deletes nothing and the dead key stays.

Four facts then fix the order of events without any timing argument:

1. `_outlined_engine_id` is `None`, and in the pre-fix build only
   `_clear_target_outline` writes that, so the clear ran;
2. `python.log` ends with
   `12:28:33.164 WRECK swap id=2130706475 pose=(-239.9, 34.1, -222.7)`, 99 ms
   after the `armour_hit` at `(-237.7, 35.0, -221.4)`. `attach_wreck_model`
   prints that line, and `_wreck_loaded` calls it only when `bw_entity` is not
   `None`, so the vehicle still had a visual entity;
3. `_clear_target_outline` skips the removal only when the vehicle or its
   `bw_entity` is missing, so it did call `wgDelEdgeDetectEntity`;
4. the key is still in the list, so that removal matched nothing.

The clear therefore ran after the model swap. The window is a race, and that
is why the three earlier kills in the same battle, at `12:28:04`, `12:28:10`
and `12:28:16`, were harmless. `_apply_health` starts the asynchronous wreck
load, and `_update_target_outline` drops a dead target only on its next
0.125-second pass. A load that finishes after that pass removes the right key.
A load that finishes before it leaves the dead key for the next
`CanvasApp::tick`.

`f1a1250` closes both orderings, because it removes the outline inside
`_apply_health` before `request_wreck` is issued at all. No further code change
follows from this dump. What follows is a build step: rebuild the WOTMOD from
`f1a1250` or later and replace the file under `mods/0.9.22.0.1`. Which wreck
loads fast enough to lose the race is an inference, most plausibly one whose
compound another kill already cached; the dump proves the ordering, not the
cause of the load time.

## A burning remote vehicle has no flame, because nothing starts the extra

Peng reports that an enemy vehicle never shows fire while it burns. The
burning state exists in this port, but it stops at the Python object. Nothing
reaches the effect that #1513 plays.

First, one reading correction. `bot_fire_seen` in the `PERF collections` line
is not a burning counter. `_resolve_bot_fire` compares each bot's `fire_seq`
and launches a projectile, so the number counts bots whose gun shot has been
seen. It says nothing about fire.

### What #1513 actually does

The flame is an entity extra, not a property of the appearance:

1. `scripts/entity_defs/Vehicle.def` declares `publicStateModifiers`, and
   `Vehicle.pyc` is the only member in the whole package that reads it;
2. `Vehicle.set_publicStateModifiers` is the property-change callback. It runs
   only when `isStarted`, keeps the previous frozenset in
   `__prevPublicStateModifiers`, and passes the two set differences to
   `Vehicle.__updateModifiers`;
3. `__updateModifiers` calls `typeDescriptor.extras[idx].stopFor(self)` for
   every removed index and `startFor(self)` for every added one, the second
   inside `try`/`except Exception: LOG_CURRENT_EXCEPTION`;
4. `vehicle_extras.Fire._start` reads `vehicle.appearance.isUnderwater`, calls
   `__playEffect` when the vehicle is dry, and then calls
   `vehicle.appearance.switchFireVibrations(True)`;
5. `Fire.__playEffect` picks one variant with
   `random.choice(vehicle.typeDescriptor.type.effects['flaming'])` and plays it
   through `vehicle.appearance.boundEffects.addNew(None, effects, stages,
   True, **data)`.

`items/vehicles._readExtras` builds the list as `[NoneExtra]` plus one entry
per `<extras>` child of `scripts/item_defs/vehicles/common/vehicle.xml`. That
file holds 95 children and `fire` is the 22nd of them, so `extras[22]` is
`extrasDict['fire']` on every descriptor in this build.

`_readVehicleEffects` turns the vehicle's own `<damagedStateGroup>` into the
group name, so `small` selects `smallFlaming` in
`scripts/item_defs/vehicles/common/vehicle_effects.xml`. That group holds two
variants, each a pixie from `particles/Tank/destruction/flaming_small_*.xml` at
node `HP_Fire_1`, a WWISE sound with a PC and an NPC name, and a
`stopEmission` at the `noEmission` stage.

### Why the port showed nothing

Four separate reasons, and each one alone was enough:

- the port never writes `publicStateModifiers`. It is set once to `()` in
  `bigworld_binding` and read once in `remote_vehicle`, so even the local
  player's stock `Vehicle` never receives a modifier;
- a bot is not a `Vehicle` entity. Every remote vehicle is an `OfflineEntity`
  with a Python `RemoteVehicle`, so the engine has no property to replicate and
  no `set_` callback to call;
- the state stopped at `critical_damage.apply_payload`, which sets
  `vehicle.is_on_fire`. `_present_critical` then returned before its fire
  branch for any record that is not `local`, and that branch is a damage-info
  panel index rather than an effect;
- `_RemoteAppearance` had none of the three members `Fire` touches. It defined
  `compoundModel`, `models`, `modelsDesc`, `damageState`, `isLoaded`,
  `isInWater`, `gunRecoil`, `engineAudition`, `turretMatrix` and `gunMatrix`,
  so `Fire._start` raised `AttributeError` on `isUnderwater`.

### What now drives the flame

The extra machinery already worked in this port. `_start_shooting_effect`
drives `extrasDict['shoot']` with `stopFor`/`startFor` on a `RemoteVehicle`,
and `EntityExtra.startFor(self, entity, args=None)` takes exactly the one
argument `__updateModifiers` passes. Three edits complete the fire path.

`_RemoteAppearance` gained the three members `Fire` reads. `isUnderwater`
starts `False`. `switchFireVibrations(start)` returns `None`, which matches
retail, because `CompoundAppearance.switchFireVibrations` needs a
`peripheralsController` that `vehicle_assembler` builds only for the player.
`boundEffects` creates one `bound_effects.ModelBoundEffects` over the current
compound on first use, `detach` destroys it while that compound is still
alive, and the new `abandon` forgets it without a native call once BigWorld
has already freed the space. Construction is deferred rather than done in
`attach` so that a vehicle that never burns never imports the stock module.

`_stop_shooting_effect` became `_stop_extras`, in the shape of retail
`Vehicle.__stopExtras`: a loop over `self.extras.items()` that calls
`typeDescriptor.extras[index].stop(data)`. The old bare `self.extras.clear()`
would orphan a running fire's `EffectsListPlayer` across the wreck swap.
`attach_wreck_model` and `detach_visual` both call it before they touch the
compound, so the flame is always released before the model it hangs on.

`_present_critical` now resolves the entity first and matches the extra to
`is_on_fire` before the branch that returns for a non-local record. It is
level-driven rather than event-driven, because `_apply_critical_state` skips
an unchanged canonical state. One owner covers every vehicle: bots and allied
bots through `RemoteVehicle`, and the local player through the stock
`Vehicle`, whose `CompoundAppearance` already carries all three members.
`_detach_local_presentation` stops the player's flame when the port releases
that vehicle.

Death keeps the crash-2 ordering explicit. `_apply_health` runs
`critical_damage.apply_death`, which extinguishes the fire, and presents that
state before it asks for the wreck, so `stopFor` always precedes
`request_wreck`. One detail differs from retail: the port assigns
`entity.health` after that presentation, so `Fire._cleanup` reads a positive
health and takes the `keyOff()` branch instead of retail's
`stop(forceCallback=True)`. The flame then fades on the old compound and the
wreck swap destroys the bound effects while that compound is still alive.

The fire is unrelated to the outline crash. Nothing in this port ever created
a fire effect, so no effect held a reference to the compound the wreck swap
released.

Static reading cannot prove the rest. A Windows run has to show that the pixie
loads and draws on the bot's compound, that `_findTargetNode` finds `HP_Fire_1`
rather than falling back to the model root, that the WWISE sound picks the NPC
name for a remote vehicle, and that no `EffectsListPlayer` callback survives
the round. Underwater suppression is a deliberate difference: retail drives
`isUnderwater` from a water sensor, and a constant `False` keeps a flame on a
bot that drowns.

## Known deterministic parity gaps

The source audit deliberately keeps the following differences visible:

- the offline garage now publishes the complete optional-device and equipment
  catalogue (`items/__init__` item types 9 and 11) with shop prices, unlocks and
  owned stock, and the battle law consumes the same attribute factors the
  garage panel consumes, as recorded in the section above. What is
  the account command surface that MOUNTS them is now implemented in
  `account_rpc/garage.py`, which keeps one mutable copy of the bootstrap
  snapshot so the fitting writers share a single live record. The handled
  commands, all verified against this build's `AccountCommands.pyc`, are
  `CMD_EQUIP` 101 (module and gun swap), `CMD_EQUIP_OPTDEV` 102,
  `CMD_EQUIP_SHELLS` 103, `CMD_EQUIP_EQS` 104,
  `CMD_SET_AND_FILL_LAYOUTS` 108, `CMD_TMAN_ADD_SKILL` 151 and
  `CMD_TMAN_DROP_SKILLS` 152. Optional devices and modules are rebuilt through
  `VehicleDescr.installOptionalDevice`/`removeOptionalDevice`/`installComponent`
  and `makeCompactDescr`, crew skills through `TankmanDescr.addSkill`, and each
  accepted mutation is pushed with `PlayerAccount.update`, which unpickles its
  argument into the normal `_update` event path. A gun swap refills the default
  ammunition, because the new gun's shells would otherwise disagree with the
  shell inventory that `data._validate_selected_vehicle` cross-checks;
- purchases are implemented: `CMD_BUY_ITEM` 302 carries
  `(cacheRev, intCompactDescr, count, goldForCredits)` and
  `CMD_BUY_AND_EQUIP_ITEM` 308 carries
  `[cacheRev, compDescr, vehInvID, slotIdx, isPaidRemoval, gunCompDescr]`;
  `CMD_VEH_SETTINGS` 107 is the per-vehicle settings mask, not a purchase.
  Balances are unlimited by choice: the offline shop publishes every item at
  zero price, so a deduction would always subtract nothing, and ownership is the
  only part of a purchase with an observable effect. Buying a VEHICLE is a
  separate surface that is not implemented: `Shop.buy` routes a vehicle to
  `buyVehicle`, which needs its own command plus a new inventory record, crew
  and slot;
- the garage now persists to `mods/configs/offline_lan_0922/garage_state.json`,
  a sibling of `account_state.json` so each file keeps one owner. It stores
  mounted devices and modules through the vehicle's compact descriptor, plus
  consumables, shells, layouts, settings, learned crew skills and owned stock.
  Records are keyed on `vehicleTypeCompactDescr` and on the crew slot index,
  never on inventory ids, because those are renumbered whenever the vehicle in
  `config.json` changes. The file is never shipped in the overlay, is written
  atomically after each accepted change, and any unreadable or wrong-schema
  content logs one line and falls back to the stock garage;
- every module a vehicle type lists is published as owned and unlocked, so the
  research tree can mount any gun, turret, engine, chassis or radio. The lists
  come from the vehicle's own type, so a premium hull still offers only its own
  modules;
- the battle uses the garage loadout: the player's shells come from the mounted
  layout mapped onto the gun's shot order, and the consumables come from the
  mounted slots, so an empty slot carries nothing. Bots keep a synthetic
  loadout by design;
- the spotting law now applies the situational devices and the vision and
  concealment crew skills for the player and for authority bots. Coated optics
  stay implicit through `miscAttrs['circularVisionRadiusFactor']`, which
  `StaticFactorDevice.updateVehicleDescrAttrs` folds into the descriptor;
  binoculars and the camouflage net are applied explicitly because #1513 gives
  them only `updateVehicleAttrFactors`, which writes a caller-owned dict this
  port does not build. Binoculars replace the optics factor rather than stacking
  with it, exactly as `Stereoscope.updateVehicleAttrFactors` does, and the
  camouflage net adds `type.invisibilityDeltas['camouflageNetBonus']` to the
  stationary branch only. Both wait the descriptor's own
  `activateWhenStillSec`. Commander qualification, Recon
  (`commander_eagleEye`, best single crewman), Situational Awareness
  (`radioman_finder`, best single crewman) and crew Camouflage (averaged over
  the whole crew) are read from the garage crew;
- what remains unwired on that path: the stationary predicate itself is server
  law in retail, published through
  `Avatar.updateVehicleOptionalDeviceStatus`, and this client ships no cell
  script, so the port uses its own speed threshold with the client's 3.0 second
  delay; the camouflage paint bonus
  (`invisibilityDeltas['camouflageBonus']`) and `invisibilityDeltas`
  `firePenalty` are still not applied;
- the server publishes terminal winner/reason/base team plus live frags and the
  human team-killer flag, but not the complete 0.8.2
  `personal`/`players`/`vehicles` battle-result record;
- bot drowning and the complete stun penalty/medical-kit loop remain open.
  Authority-Bot movement now runs the same server-relayed destructible contact
  boundary as player movement. When server authority is active, the server
  advances both human and Bot projectile trajectories and resolves their
  baked-world collisions and damage. It still trusts each human client for its
  pose, launch parameters, reload/ammunition legality and native descriptor
  donation. Human fire, drowning, repair/equipment progression and parts of
  spotting also remain client-originated. This is a trusted-LAN architecture,
  not an anti-cheat or a claim that every calculation runs on the server.

The local player path does include server-relayed critical state, fire,
drowning, exact fall/landing attribution, small repair/medkit/extinguisher
activation, native frag/team-killer updates, durable killer/reason metadata,
and server-deduplicated destructible results for collision and shots.
Each module states its own origin in its docstring.

## Reference implementations reviewed

The migration compared the local build with several public offline layers,
including the Tuxedo 0.9.22 observer, WOTClassicReborn's later observer fork,
the full `webiumsk/WOT-0.9.20.0` client source and
`Fedar459/WoTOfflineHangar0.9.22`. Tuxedo's useful pattern is the separation of
the training-window selection action from the later observer start; its direct
entity clear starts from Login rather than a fully initialized Hangar and is
therefore not copied into this Lobby path. The 0.9.20 source was useful for
checking Account, HangarSpace and Avatar ownership order, then every adopted
name and ordering was verified against local `#1513` bytecode. Broad
login-view replacements, blanket exception handling, forced process exit,
global entity-clear bypasses and development hotkeys were not carried into
this runtime.

## Automated and package verification

The test suites cover configuration, protocol ordering, fake Account RPC data,
stock picker installation/restoration, exact battle mailboxes, Vehicle property
packing, local movement, aiming, shooting, health/death, snapshot barriers,
active-round leave and authority transfer, same-poll lobby/start interleaving,
bot authority, tactical maps, 15-per-team allocation, elimination and
multi-round reset.

The release build additionally:

1. inspects the exact client version, build, executable architecture, required
   resource archives and pinned Avatar entity-definition hashes;
2. reads exact code objects from `scripts.pkg` and compares every stock method
   signature, direct-consumer literal, lifecycle name and `AccountCommands`
   constant used by the port, including variadic flags on the stock view
   loader, and inventories the complete exact-client call-site set for the two
   unsafe retail filter sync methods;
3. checks the ordered lifecycle contracts and inventories every exact
   Account-helper `setAccount` implementation, including the native
   Account-to-Hangar-to-Avatar retirement order, chat-proxy detachment and
   callback-registry initialization;
4. compiles every packaged source with CPython 2.7;
5. removes source and stale Python 3 bytecode;
6. requires the packaged PYC manifest to match every current source module and
   checks every PYC magic value;
7. rejects duplicate or corrupt archive members, `.pyo` and `__pycache__`;
8. requires Store compression and explicit directory entries for all wotmod
   members; and
9. produces a checksum and hash-named copy-ready client overlay.

The ABI gate is intentionally paired with consumer-contract tests. Python code
object signatures cannot describe Entity `.def` flags, mailbox wire arities,
dictionary keys or tuple lengths, so those are checked separately against the
actual producer data. LAN tests likewise reject malformed messages and stale
round identifiers before they cross a battle lifecycle barrier.

## Remaining empirical boundary

Static exact-bytecode review and simulated lifecycle tests cannot execute the
Windows BigWorld engine. The first real-client run still has to verify:

- the local engine accepts the complete native Vehicle property set and all
  30 entity presentations on the selected graphics/content configuration;
- the stock picker owns and releases the visible mouse correctly;
- map-specific collision/water queries produce sensible local steering on the
  real spaces;
- low- and high-speed contact on multiple maps destroys the intended fragile,
  falling and structure-module objects; low-speed cap admission holds its
  submission tick without publishing synthetic momentum, pending skins clear
  only through their exact registered OBB exit and a backing-ray recast, falling
  objects track and retain their native final collision pose, a five-item soft
  chain fails closed, and surviving backing collision remains solid;
- the kinematic layer, bot update budget and HUD remain usable at the target
  frame rate; this source validation does not claim that all visible movement
  hitches are resolved; and
- a full result -> lobby -> picker -> second battle cycle completes without a
  new traceback.

SPG/strategic camera movement, battle-settings capture-device enumeration and
combat-equipment placement remain outside the current standard vehicle-control
slice. Their exact mailboxes are not generalized into silent no-ops.

No additional Python mismatch is known in the consumer matrix above. Optional
lobby features outside that matrix and all BigWorld-side behavior still remain
empirical. If a real-client check fails, preserve `python.log`; the package
intentionally avoids noisy per-frame tracing so the first actionable traceback
remains visible.
