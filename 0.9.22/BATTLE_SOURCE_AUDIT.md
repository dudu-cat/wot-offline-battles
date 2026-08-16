# 0.9.22 battle source audit

This is a source-provenance gate, not a feature checklist.  The default rule
for the #1513 port is to reuse the working 0.8.2 implementation.  A difference
is accepted only when it is one of:

1. an exact #1513 API, entity-stream or lifecycle adaptation;
2. a shared-authority operation intentionally moved to the LAN server;
3. #1513-only content data;
4. an explicitly listed open gap that blocks calling the port complete.

Run `python3 0.9.22/tools/audit_battle_sources.py .` from the repository
root.  The version 0.4.0 inventory contains 64 package modules. The gate fails
when the port gains an undocumented Python module, a reviewed
contract hash drifts, or a previously removed replacement law returns.

The release gate deliberately does not inspect the live 0.8.2 working tree.
Its reviewed 0.8.2 inventory and source hashes are frozen in
`tools/reviewed_082_source_manifest.json` at the 0.9.22 checkpoint
`0a96d75978dd7160c3392e5b1089bdbc07b9bd8b`.  This keeps a 0.9.22 build
reproducible from `0.9.22` while 0.8.2 continues to evolve.  New 0.8.2
changes require a separate parity review before they can update this manifest
or any reviewed port hash; they never become release law merely because both
versions share one checkout.

## Every #1513 module

| #1513 file | Classification | Source and exact reason for divergence |
|---|---|---|
| `__init__.py` | Port metadata | Target build and package version only; it contains no battle law. |
| `account_rpc/__init__.py` | #1513 API | Package boundary for the explicit #1513 Account mailbox. |
| `account_rpc/commands.py` | #1513 API | Command IDs come from #1513 `AccountCommands.pyc`; 0.8.2 IDs are not ABI-compatible. |
| `account_rpc/data.py` | #1513 API | Account-helper response shapes come from the exact #1513 consumers rather than 0.8.2 lobby snapshots. |
| `account_rpc/requests.py` | #1513 API | Retains the 0.8.2 request/response idea, but registers the RPCs actually called by #1513. |
| `account_rpc/server.py` | #1513 API | Explicit #1513 Account base methods replace the unsafe 0.8.2 catch-all mailbox. |
| `account_rpc/state.py` | State port | Same offline inventory/ownership intent as `state.py`, reduced to data consumed by #1513 account helpers. |
| `ai/__init__.py` | Package adapter | No law. |
| `ai/adapter.py` | Authority adapter | Converts JSON-compatible LAN states into the copied planner/driver contracts; navigation decisions remain in copied modules. |
| `ai/cover.py` | Exact copy | `bot_ai_cover.py`, byte-for-byte apart from newline normalization. |
| `ai/driver.py` | Latest 0.8.2 law plus #1513 descriptor adapter | Copied from the finalized 0.8.2 release law, including spawn-congestion recovery, proportional target-yaw steering and the 2026-08-08 drive-intent correction: explicit reverse commands flip tracked steering immediately, while a hull merely sliding backward under a forward command retains forward steering. The #1513 adapter retains its longer validated avoidance-branch hold so a bot commits past a wall instead of alternating between symmetric exits. Version 0.3.64 adds one narrow adapter signal for an intentional right-of-way wait: it resets only stuck/recovery timers and does not alter steering, routes or physical contact. #1513 item components deliberately reject the inherited legacy `get()` surface, so the one component reader dispatches real dictionaries to `get()` and native descriptors to attributes. The reviewed adaptation is SHA-256 pinned. |
| `ai/maps.py` | Copy plus #1513 data and reviewed-route overlay | `bot_ai_maps.py` with import paths changed and the separate #1513 map table appended; after the complete registry is assembled it applies the explicitly reviewed #1513 route batch. |
| `ai/maps_0922_extra.py` | #1513 data | Routes/bases for arenas absent from 0.8.2; no replacement movement law. |
| `ai/maps_extra.py` | Normalized copy | `bot_ai_maps_extra.py`; whitespace-only difference. |
| `ai/maps_group_a.py` | Final routes plus #1513 resource data | Retains the finalized 0.8.2 route skeletons. Karelia's team order and bases are replaced by the exact #1513 CTF resource coordinates, and comments identify Lakeville projection onto the newly baked terrain. The reviewed adaptation is SHA-256 pinned. |
| `ai/maps_group_b.py` | Final routes plus #1513 resource data | Retains the finalized supported-map routes, with the El Hallouf north-ridge gate needed by the #1513 baked graph. The obsolete Komarin data is not in the #1513 41-map standard allowlist and is never packaged as a graph. The reviewed adaptation is SHA-256 pinned. |
| `ai/maps_group_c.py` | Normalized copy | `bot_ai_maps_group_c.py`; whitespace-only difference. |
| `ai/navigation.py` | Current 0.8.2 graph-law adapter | Keeps the copied A*, link, hazard, grade, recovery and peer-avoidance laws with a build-specific graph-format name. The measured 0.8.2 render-thread budget is retained: baked A* advances fairly within 2.5 ms, duplicate same-frame ticks are coalesced, and cache housekeeping runs once per second. The `30cef49` baked-segment guard is also ported at the local fallback, path-compaction and direct-route shortcuts so a clear ray cannot cut through a cell marked shallow water; a tank already in such a cell may still drive out. The failed-edge penalty table currently has no production writer; an empty table therefore returns before rasterizing and scanning every candidate segment, while non-empty and expiry behavior remain byte-for-byte on the copied path. Version 0.3.75 applies a small baked link-completeness cost only to shared `route` and continued-route A*, and clearance-aware smoothing may not worsen mean missing-link exposure by more than 0.25. It adds no links and does not apply to spawn joins or local recovery, so collision, hazard, grade and unavoidable one-cell passage law remain unchanged. |
| `ai/planner.py` | Current 0.8.2 law port | Retains the finalized lineup/template and route handoff plus `e0a74c9` spawn joining: the initial hull yaw rejects a genuinely rearward connector and stores the real spawn as the first join anchor. Package imports and #1513 map data are the only version adapters. The reviewed adaptation is SHA-256 pinned. |
| `ai/reviewed_routes_20260811.py` | #1513 user-reviewed route data | Stores sparse strategic gates for the 38 changed maps through the Round 4 correction pass, then projects them through the validated safe graph baker. It explicitly removes Ensk's redundant rail-yard route and balances the two retained lanes at seven Bots each. Ordinary through-routes publish one validated geometry plus its strict reverse; the final graph validator still proves every directed segment. Redshire, Lost City, and The Pit were explicitly accepted unchanged, so all 41 #1513 maps have a recorded review disposition. |
| `artillery_arc_queue.py` | Bounded #1513 world-probe scheduler | Fairly schedules strategic and exact ballistic chords with a hard total of four native collision rays per rendered frame. Pending work never counts as a clear path, and only exact terminal proof may authorize launch. |
| `artillery_controller.py` | #1513 artillery adapter | Combines the server-owned rear SPG anchor with low/high ballistic planning, exact client world proof and native muzzle launch. A moving-target intent is frozen by source, target, shell and next fire sequence until it launches or a strict invalidation fence cancels it. |
| `ballistics.py` | Pure elapsed-flight law | Solves finite low/high gravity roots and moving-target interception without a BigWorld dependency. Solutions are bounded to the projectile lifetime and never turn a server staging anchor into geometry proof. |
| `battle_feedback.py` | 0.8.2 law plus #1513 presenter | Preserves the five-second observation window and three-second delayed Sixth Sense law.  The only presentation substitution is #1513's native `vehicleState.notifyStateChanged(VEHICLE_VIEW_STATE.OBSERVED_BY_ENEMY, value)` path; generation, alive, battle-period and actual commander-skill predicates fence delayed callbacks. |
| `battle_runtime.py` | #1513 native adapter | Keeps the local player on a stock #1513 Avatar/Vehicle while the copied 0.8.2 integrator owns its otherwise missing server transform stream, and preserves the 0.8.2 authoritative remote-carrier split through `entities/remote_vehicle.py`. The behavioral decomposition below identifies every law it is permitted to own. The selected gameplay bit is applied through `BigWorld.wg_setSpaceItemsVisibilityMask`, matching exact #1513 `ClientHangarSpace.create`: a client-only `createSpace()` is configured without requiring a server-published `BigWorld.spaces[spaceID]` data object or synchronous getter readback. Because exact #1513 reapplies its full server visibility mask late in `PlayerAvatar.__onInitStepCompleted`, version 0.3.75 idempotently restores the selected CTF bit after deferred client readiness; on Lakeville this keeps CTF mask `0xffffff89` visible and assault2 mask `0xffffffc0` hidden without changing objective data. Local input publication preserves fractional time across its nominal 30 Hz boundary, avoiding frame-rate-dependent under-sampling without replaying stale poses in bursts. The stock Lobby-to-BattleLoading transition runs before Account retirement so a failed map start returns through BattleLoading-to-Lobby instead of repopulating an undisposed Lobby view. The `c57c186` retained-event fix is intentionally not copied: it repairs the persistent synthetic 0.8.2 arena, whereas #1513 `PlayerAvatar.onBecomeNonPlayer` destroys the real `ClientArena`, whose `destroy` clears its event manager; this runtime separately cancels every callback and presenter it owns. The `7e3a1b2` no-rewind rule is applied to authority Bot poses so an older server echo never overwrites the current local integration. Version 0.3.76 restores stock PREBATTLE ownership: setup aligns the camera and gun once, but neither starts `VehicleGunRotator` nor enables battle input before the native BATTLE period transition. Version 0.3.70 consumes typed destructible-motion receipts: a real-speed `crushed` result may advance; a cap-admitted contact holds its submission tick and restores pre-step real speed; an exact pending-skin exit/recast may clear; and hard/static results retain the original wall response. The directional cap is never copied into pose, LAN or ram momentum. The same runtime supplies the Bot stack with a read-only far classifier, an exact 3x3 flat-corridor world receipt and an exact commit resolver. Straight motion may reuse the receipt only while the current hull sweep remains contained and no catalog contact exists; missing/deferred proof, drift, contact and coast/brake/turn/airborne paths remain world-first. No planning path may publish destruction. |
| `bootstrap.py` | #1513 lifecycle | Wotmod loading and the #1513 login/lobby state machine have no compatible 0.8.2 entry point. It installs and reverses the scoped automatic-announcement adapter before the lobby controller dispatches its first ready event. |
| `bot_runtime.py` | Authority adapter | Owns LAN serialization and authority failover. Navigation/cover/planning call copied AI modules. It carries the completed 0.8.2 performance decomposition plus monotonic pull-only logical probe counters: one per-frame traffic snapshot and spatial index feed local neighbours; strategic/local-driver decisions are staggered at the measured ~10 Hz cadence while physics, gun and local presentation advance on every rendered frame. Version 0.3.67 deliberately leaves the optional per-query probe clock unset, removing two diagnostic clock reads from every native query while retaining coarse frame-stage timing and probe counts. Probe order, results, cadences, deadlines and budgets are unchanged. Version 0.3.64 applies a deterministic throttle-only right-of-way rule to nearby same-team traffic before the copied integrator runs: followers respect the vehicle ahead, every Bot yields to a human, and the lower Bot id wins a crossing or merge conflict. Enemy traffic, tactical routes and `tank_collision` separation/ram damage are unchanged. The diagnostics never trigger a native query or feed their counts into gameplay. The BotRuntime/LAN publication boundary forms canonical Bot state at a nominal 30 Hz, before transport, matching the mature 0.8.2 split without dropping already-sequenced combat proposals; one Bot may consume at most one fire edge on each published frame, so even a 0.01-second intra-clip reload cannot jump the server's strict `fire_seq + 1` contract. A cross-base replay does not reserve an unpublished sequence: its fire/repair steps remain unpublished until the next real Bot-state publication coalesces them at canonical `ack + 1`. Version 0.3.62 also reuses each firing-lane key/distance, aggregates one observation record per target, and caches descriptor yaw limits without changing the probe sequence, readiness, budget, deadline or wire payload. Server-order changes invalidate only the affected Bot's decision and motion cache; live target-pose fields excluded from the server revision signature remain render-frame overlays and cannot flush all 29 caches. On observation and firing-lane refresh frames every cached contact still receives its current pose, health and alive state. On other render frames only the selected command target is copied and overlaid; unselected per-source spotting records remain immutable until they are consumed. The human lookup is built once at the original per-Bot refresh point, while copied Bot poses retain their ordered same-tick visibility. Human armor/class are derived from the installed descriptor, cached by vehicle name and excluded from live pose/health overlays. Bot observers apply the target descriptor's stationary/moving base camouflage, shot factor and the same prebaked pair-specific foliage bonus before the shared detection-distance law; 50-metre proximity remains unconditional, and every observer-target cache records the target `fire_seq` so one observer cannot consume the shot-camouflage edge for the others. Authority firing-lane pairs retain the 0.20-second freshness contract, but stable source/kind/target phases spread their native raycasts across the final 0.20 seconds before each 0.40-second observation. Periodic refreshes and current-fire safety checks share a hard 110-pair per-render-frame budget. Pairs outside the server's 560-metre engagement ceiling plus a conservative 25-metre relative-travel margin become ready/unshootable without a native collision query. At the supported 24 FPS floor, overflow remains pending and delays the complete canonical observation by at most one additional render frame plus its normal publication phase; a partial `shootable_by_bot_ids` set is never published. Cover sampling likewise retains at most three jobs per observation, schedules them at the start and stable thirds of the first 0.20-second half-window with at most one job per render frame, and waits for the complete batch before publication; the final half-window remains reserved for firing-lane refreshes. This reduces the measured cover-probe peak from `156+36` to `52+12` native calls without changing the total budget. Production passes the copied driver's continuous `turn` controller and explicit drive intent to `vehicle_physics.traverse_step`; only the network/UI `rotation_dir` remains a discrete sign. On supported centre ground, the vertical law now skips its unused front/back samples and probes them only when centre support is absent, preserving the realised pose and ledge fallback while reducing flat-ground queries. A fully grounded copied Bot with zero drive and turn reuses its last proven corridor and slope only while X, Y, Z and heading remain unchanged; motion restores the normal safety expiry. Version 0.3.70 gives the integrator five exact-contact outcomes: `clear` and real-speed `crushed` advance; `soft` retains only real impact speed while pose is blocked; `cap_crushed` holds the submission tick at pre-step real speed; and `hard` retains the original stop/recovery path. No hidden or capped velocity is accumulated. The runtime otherwise uses the exact copied pose integration, mature gun/reload/scatter rules and `tank_collision` resolver, then publishes the canonical result through the LAN snapshot. The reviewed adapter is SHA-256 pinned. |
| `combat_rules.py` | Law port | Ports `_offh_penetration`, `_offh_resolve_hull_hit`, HE direct/splash damage, radius and nominal-armor selection; access is extended from 0.8.2 dictionaries to #1513 `GunShot` and collision objects. Version 0.3.68 lazily samples one shell penetration factor at the first required penetration test, then reuses it while evaluating the range-dependent mean at each tested destructible and the eventual vehicle before subtracting cumulative external-object loss. A pure miss or HE/HEAT destructible stop consumes no penetration RNG. The exact private-server operation order is not public, so this same-family order is a high-confidence reconstruction rather than a source-identity claim. |
| `critical_damage.py` | Generated closure adapter | Generated from the complete 0.8.2 `_apply_module_damage` dependency slice: synthetic interior materials, layout ray hits, device/crew state, ammo-rack death, fire transitions and the all-device/all-crew drowning knockout remain copied bodies. The generator applies audited #1513 dictionary-to-attribute substitutions plus one presentation guard for a detached hit-proposal vehicle; the resulting reviewed closure adapter is pinned as one port-local contract hash. A firing client runs the law against an explicit snapshot of descriptor, pose, components and mutable critical state, so it cannot mutate a live `Vehicle` or fire native HUD/kill callbacks before server admission. The handwritten footer serializes that proposal, installs server-relayed state without re-rolling, advances the copied `device_damage.py` repair/fire constants, and exposes the copied small-repair/small-medkit/extinguisher transitions to the #1513 equipment mailbox adapter. |
| `compat.py` | #1513 lifecycle | Hooks exact #1513 Account/Avatar methods and signatures, pinned by ABI and lifecycle audits. It also refreshes the stock `VehicleMarkerPlugin.__playerVehicleID` cache only after Avatar and ArenaDP agree on the created local Vehicle, preserving the native `FROM_PLAYER` versus `FROM_ALLY` damage classification. The feedback adaptor is validated through the public shared repository without comparing its intentional `weakref.proxy(setup.arenaDP)` against the provider's strong ArenaDP reference by identity. |
| `config.py` | Config adapter | Same user-owned LAN settings intent as `paths.py`/`user_config.py`, using the #1513 mod directory. Version 0.4.0 keeps release-owned defaults in `config.json` and atomically persists only the validated host/port override in `server_endpoint.json`. A missing file uses `127.0.0.1:28782`; malformed user data fails safely to that loopback endpoint, and an older non-default `config.json` endpoint migrates once before future overlays restore release defaults. |
| `destructibles_authority.py` | 0.8.2 law plus #1513 transaction/ABI adapter | Retains the 0.8.2 per-space/per-chunk authority, but uses #1513 `encodeFragile(destrID, isShotDamage)`, passes the projectile-sync field explicitly, commits dedup/replay state only after native acceptance, and rolls back a failed controller-property append. Version 0.3.68 preserves the encoded shot bit but applies the local native manager order unsynchronized because the copied projectile path never receives the retail server's later `damagedDestructibles` payload. These are required version-boundary changes rather than replacement gameplay law. |
| `destructibles_compat.py` | #1513 API adapter | #1513 moved the chunk-ID helper, four encoders and destructible-type aliases from `AreaDestructibles` to `DestructiblesCache`; this restores only those names needed by the retained authority. |
| `destructibles_sensor.py` | 0.8.2 contact law plus strict #1513/LAN adapter | Retains the original collision, health, type and kinetic gates behind a stricter contact boundary. The #1513 seam carries the fragile shot bit, uses the destructible's world position for chunk ownership, marks results only after native acceptance and LAN publication, and propagates native failures instead of treating an intact object as passable. Version 0.3.67 enumerates the exact count written by `game.onChunkLoad(..., numDestructibles, ...)` into `DestructiblesManager.__loadedChunkIDs`; `wg_getChunkDestrFilenames` supplies only an optional named prefix and cannot truncate later native slots. A missing streamed count is retried and contradictory data fails closed. The checksum-pinned schema-v3 catalog covers all 41 maps. Its whole-map directory joins 61,625 unique quantized world-matrix signatures to canonical resources and collision-box selections, restoring identity for blank later slots. Eleven ambiguous signatures representing 28 candidates fail closed. Version 0.3.68 keeps normalized catalog keys but passes the resource's case-preserved descriptor filename to the case-sensitive native cache. Static BSP/material contact is evaluated first. For local-player movement, a dynamic item absent from that ray is considered only when the vehicle's exact swept OBB intersects its world OBB. Both paths use #1513's unchanged stock mass/speed/health gate and real transform scale. Player and authority-Bot shots use one ordered scene traversal. Native material identity remains first; an anonymous slot may fall back only to the nearest unique catalog OBB before the first static collision or nearest vehicle, so ambiguity and occlusion fail closed, and continuation starts after the exact registered OBB exit. Exact #1513 data sets `maxHpForShootingThrough=19` and all material reduction pairs to `(0, 25)`: AP/APCR/APHE retain damage and lose a cumulative fixed 25 mm through each scale-adjusted item at or below 19 HP; less than 1 mm remaining stops the shell. Above-threshold items stop it even when destroyed. Old #1513 HE/HEAT stop at the first destructible and HE explodes there. Version 0.3.70 separates real-speed swept contact from low-speed cap admission. The latter uses forward/reverse top speed only as stock-gate evidence and may submit only at the exact leading hull face plus a 0.075-m margin and current-frame physical travel; the receipt identifies that the cap was used without publishing it as motion. Pending fragile/module skins may be skipped only through the accepted identity's exact registered OBB exit and a real recast. Falling items follow the native animator matrix while airborne. At the native touchdown callback boundary the coarse catalog OBB is retired, leaving the moving/final native BSP, world rays and ground-support law authoritative; this prevents a fallen pole's broad catalog box from becoming an artificial wall. After a hide expires, static rays and backing geometry remain authoritative. Unknown, stale, missing, ambiguous, under-threshold or still-solid objects remain blocking. Adjacent independent items are ordered by unchanged native `(chunk,item)` identity rather than collapsed into one ambiguous contact. A pure read may classify only unique exact catalog OBBs as a stock-crushable soft path and advances after each exact OBB exit; it cannot destroy or publish. One ray may skip at most four such items, so a fifth fails closed. Authority-Bot destruction runs only at exact oriented-hull contact through the same native transaction, while a bounded zero-speed scan only registers streamed identity. Bounded `DESTR` chunk/contact diagnostics reuse existing state, add no native query and are rate-limited. The broad 0.8.2 object-origin proximity destroy workaround is not ported. The reviewed adapter is SHA-256 pinned. |
| `device_damage.py` | 0.8.2 law plus #1513 descriptor adapter | Device HP, saving throw, crew penalty, repair and fire laws remain copied. Only `_raw_hp` and `_misc_factor` dispatch real 0.8.2 dictionaries versus #1513 native component attributes; using the inherited `NoLegacyStuff.get()` is invalid and deliberately asserts. The complete reviewed adapter is pinned in the port-local source manifest. |
| `entities/__init__.py` | Package adapter | No law. |
| `entities/avatar_server.py` | #1513 API | Explicit stock Avatar mailbox methods required by #1513; the 0.8.2 fake Avatar surface is incompatible. The movement mailbox only relays input to LAN because stock `PlayerAvatar.moveVehicle` has already notified `WGVehicleFilter` before invoking the cell method. A vehicle-setting request accepted by the local battle runtime is likewise not echoed into stock `PlayerAvatar.updateVehicleSetting`; unhandled codes still take that native path. This avoids the stock warning branch for successfully consumed #1513 equipment code 16 without swallowing invalid or unsupported requests. |
| `entities/bigworld_binding.py` | #1513 API plus 0.8.2 pose adapter | Creates the local stock Vehicle and emits exact #1513 Avatar/Arena property streams. The local player retains `WGVehicleFilter.notifyInputKeysDown`; remote samples call the explicit Python presentation's `set_pose`/`set_aim` boundary through an injected authority resolver. That resolver reads the private `RemoteVehicleFactory` registry before the stock facade, so unspotted vehicles continue receiving pose and aim without being exposed to native aiming, collision, marker or minimap consumers. Native visual startup, local Avatar binding, drive and readiness remain on `BigWorld.entity`; client-side `Entity.teleport` remains forbidden. |
| `entities/remote_vehicle.py` | 0.8.2 carrier plus #1513 model adapter | Preserves the mature split between a synthetic gameplay vehicle id and a separate `OfflineEntity` visual, including descriptor collision components, health, pose, aim and deterministic cleanup. Its private factory remains the authoritative lookup for runtime code; the wrapped stock `BigWorld.entity`/`BigWorld.entities` surfaces expose a synthetic remote only when it is started, alive and spotted. This is a required #1513 AOI boundary: native `ProjectileMover`, gun-marker and minimap consumers must not collide with an unspotted or dead private record. The public Python class name remains `Vehicle`, as required by exact #1513 collision classification. #1513 substitutions use its `prepareCompoundAssembler` + `loadResourceListBG` model path, exact `PyCompoundModel.matrix = vehicle.matrix` world-pose provider, `CompoundAppearance.changeVisibility(modelVisible)` boundary, `setupTurretRotations` node binding, `assembleRecoil`, stock shoot extra and nine-argument `ProjectileMover.add`. A shared descriptor hit-test routine consumes the visible matrix for both remote vehicles and the copied local pose; the latter cannot use retail `Vehicle.collideSegmentExt` because its native filter stays at the spawn pose. The old `PyModelObstacle` bridge is deliberately absent: it duplicated the copied chassis-OBB contact resolver and made static look-ahead rays stop before visible body contact. The adapter does not create a retail remote `Vehicle`, whose `WGVehiclePhysics` requires unavailable server pose snapshots, or write ordinary `Model.position/yaw/pitch/roll/visibleAttachments` attributes that `PyCompoundModel` does not expose. |
| `entities/runtime.py` | #1513 API | Orders property assignment and enter/leave callbacks around the exact binding. |
| `foliage.py` | Current 0.8.2 concealment-law port | Copies the pair-specific sight-segment versus oriented-foliage-volume calculation, 32 m spatial lookup, 0.60 stacked-bush cap and 15 m firing-transparency boundary. It consumes only versioned prebaked rows and has no BigWorld dependency. |
| `gun_mechanics.py` | Inline-law adapter | Moves the 0.8.2 `_gun_state` closure into one object because #1513 uses a stock Avatar/Vehicle instead of the mock battle closure. Descriptor reads, 60/30/10 fallback ammunition, 100% crew plus commander conversion, empty countdown start, clip transitions and three-axis Gaussian scatter remain the original formulas. The local player's visible/current dispersion is not shadowed: #1513's stock Avatar owns movement, traverse, turret, additive and post-shot bloom, and the trusted-client shot samples the same read-only `VehicleGunRotator.dispersionAngle`. Bots, which have no stock Avatar, retain the copied descriptor-factor/convergence state. |
| `lan_client.py` | Protocol port | Retains LAN v5; adds round/build validation, bounded queues, a load barrier and victim health reporting needed by asynchronous #1513 entities. Server-admitted Bot observations are accepted only during the matching active round and are schema-validated before dispatch. Version 0.3.72 requires `projectile_ledger_v1` and strictly validates launch, progress, resolution, authority epoch, server time and active-projectile snapshots before dispatch. Version 0.3.73 canonicalizes SpawnPlanner's integer team keys to text only at the `battle_ready` wire boundary so the immutable sender can admit the load-barrier message. Version 0.3.74 projects full local Bot records to only the v5 server sanitizer's consumed fields immediately before outbound freezing; the complete update remains local for same-frame client-owned projectile resolution. |
| `lan_session.py` | Queue/UI adapter | Retains server-owned join/start state; connects it to the #1513 lobby and battle lifecycle. A Bot-observation relay is routed only to the runtime for its active round. The endpoint editor commits through `config.save_endpoint()` before reconnecting, while its player-facing text explains select/start, wait and reconnect actions without exposing the internal authority role name. |
| `lobby_ui.py` | #1513 lobby presentation adapter | Exact #1513 `ChinaController.onLobbyInited` opens the stock server-announcement browser whenever `battlesCount % 999 == 0`; the offline account starts at zero without an announcement service. This reversible adapter wraps only that exact lobby callback and returns before the stock automatic-open body when `auto_due` is true. Explicit `showBrowser` calls remain untouched, `BrowserController` stays enabled, and the training-settings map picker is unrelated. If the due check fails, the original lobby method still runs; uninstall restores only this adapter's own wrapper. |
| `internal_geometry.py` | Exact copy | Pure 0.8.2 primitive fitting and segment-intersection law used by internal module hits. |
| `internal_hit_layouts.py` | Import-only copy | The 0.8.2 layout builder/resolver with only its package imports moved from `offhangar` to `offline_lan_0922`. |
| `internal_layout_profiles.py` | Exact copy | The complete 251-vehicle 0.8.2 compiled internal-layout table.  #1513-only vehicles deliberately fall back to the copied compartment law until equivalent evidence-backed profiles exist. |
| `internal_layout_store.py` | Import-only copy | The 0.8.2 override/calibration store with only its package import moved to the #1513 package. |
| `map_catalog.py` | #1513 data/API | Enumerates standard CTF arenas from #1513 `ArenaType.g_cache`; 0.8.2 cannot know the new arena set. |
| `navigation_graph_schema.py` | #1513 release contract | Pure-data schema shared by the Python 2.7 runtime and Python 3 builder. It pins the exact 41 standard maps and rejects incomplete arrays, invalid anchors/bases, duplicate routes, non-finite coordinates, or routes outside the 2–16 waypoint protocol bound. |
| `prebaked_foliage.py` | 0.8.2 data-loader adapter | Ports the foliage manifest, whole-batch, checksum and row-validation contract. It deliberately requires a newly baked #1513 41-map batch under the real `mods/configs` filesystem and rejects 0.8.2 map coordinates or game-version metadata. |
| `prebaked_destructibles.py` | #1513 compiled-contact catalog adapter | Loads one checksum-pinned schema-v3 catalog for the active arena from the complete 41-map batch beside `config.json`. Every entry is tied to the pinned client version and supplies transformed BSMO bounds for fragile, falling or structure-module objects. The whole-map matrix-signature index preserves multiplicity: only 61,625 unique signatures are usable, while 11 ambiguous signatures and their 28 candidates remain explicit and fail closed. Missing, partial, stale, malformed or cross-map data likewise fails closed instead of making an unknown world object passable. The reviewed adapter is SHA-256 pinned. |
| `projectile_manager.py` | Bounded active-projectile owner | Owns the capped deterministic active set, elapsed advancement, fair progress cursors, adaptive per-projectile work budgeting, terminal removal and server-snapshot restoration without performing native queries itself. The reviewed implementation is SHA-256 pinned. |
| `projectile_runtime.py` | Pure trajectory/collision law | Advances every shell along one gravity parabola in adaptive chords no longer than 50 ms and with at most 5 cm parabola-to-chord sagitta. Static/destructible hits and relative moving-vehicle sweeps share chronological ordering, so a target may dodge after launch. The reviewed law is SHA-256 pinned. |
| `prebaked_navigation.py` | 0.8.2 graph adapter | Ports `prebaked_navigation.py`'s complete validation, checksum and batch-manifest contract. The #1513 game-version/41-standard-map allowlist and real `mods/configs` filesystem lookup deliberately reject the incompatible 0.8.2 graph batch and the non-standard 30v30 Alaska arena. |
| `queue_ui.py` | #1513 UI | Stock #1513 training-room Scaleform is the clickable host map picker.  It presents state only; the server owns room/start state. |
| `snapshot_sync.py` | Protocol port | Extracts ordered LAN entity/state records and smooth pose/aim samples. Local snapshots never rewind native player physics; remote samples are presented through the copied synthetic-vehicle boundary. A monotonic Bot-state revision prevents repeated 30 Hz server snapshots from resetting velocity between lower-rate authority publications, while health, critical and death state continue to apply on every snapshot. The reviewed adapter is SHA-256 pinned. |
| `spawn_planner.py` | Law plus #1513 data | Copies `_formation_slot`; reads exact #1513 CTF spawn points/bounds before applying that formation. |
| `spotting.py` | Current 0.8.2 law plus exact #1513 descriptor data | Ports the 50 m proximity, 500 m ceiling, five-second memory, moving/still, crew, shot and detection-range laws without BigWorld imports. Unlike the current 0.8.2 fallback table, the adapter reads `VehicleDescr.computeBaseInvisibility` and `gun.invisibilityFactorAtShot` from the exact #1513 descriptor; foliage remains additive per observer-target sight line. |
| `tank_collision.py` | Current 0.8.2 collision-law port | Ports `vehicle_collision.py` chassis-hit-tester shape (including mounted-hull vertical extension), exact vertical intervals, yaw-aware four-axis OBB SAT, inverse-mass 95% separation and perfectly inelastic pair response into an engine-free data function. Ram damage and pair cooldown remain the copied `offline_battle.py`/`physics.py` law. The completed 0.8.2 spatial index supplies a complete same/adjacent-cell broad phase whose cell size is derived from the largest chassis radius; exact OBB/contact semantics remain unchanged while distant all-pairs work is removed. The #1513 adapter indexes the first two values of its exact three-value `HitTester.bbox` contract, applies the same contact law to the copied local-player integrator and authority Bots, and reports admitted ram damage to the server. A version-local static-world veto prevents a contact correction from crossing a #1513 wall. |
| `user_config.py` | #1513 path adapter | Supplies the copied layout store's two atomic user-file operations under the #1513 mod-config directory; it owns no gameplay law. |
| `vehicle_physics.py` | Final 0.8.2 law port | Copied from the finalized 0.8.2 physics law plus its 2026-08-08 calibration: the stock 25-degree climb boundary uses 0.54 drive traction, progressive slip begins at tan=0.48, static perch holds to tan=0.50, and traverse sign follows explicit forward/reverse intent rather than incidental signed velocity. Version 0.3.70 preserves the established moving zero-throttle drivetrain share on flat ground, then progressively unloads only that share as current motion points farther downhill. At or beyond the static-perch tangent only descriptor rolling resistance remains; uphill coasting receives no downhill relief. Active opposite throttle, handbrake, zero-speed hold and uphill slip drag retain their separate laws. The reviewed final file is SHA-256 pinned because this repository's main checkout contains an older physics snapshot. |
| `waiting_room_ui.py` | 0.8.2 waiting-room law plus a #1513 native GUI adapter | Presents the reviewed 0.8.2 waiting room in #1513: the live room status, one map selector limited to the server map pool and one start button. Unlike the stock map window it also presents the players who wait for the host, and it never edits the server address, which the desktop launcher owns before the client starts. The native surface uses only calls proved in the exact client: `GUI.Simple`, `GUI.Window`, `GUI.Text`, `GUI.addRoot`, `GUI.delRoot`, `GUI.reSort`, texture `system/maps/col_white.dds`, font `default_small.font` and the `handleMouse*` script protocol. A client that cannot build that surface keeps `queue_ui.py`. |
| `world_collision.py` | Closure plus #1513 query adapter | Dedents `_check_horizontal_collision`; a thin wrapper supplies `BigWorld`/`Math`. Version 0.3.70 replaces the one-direction gradual-rise exception with a continuous bounded non-flat ground-profile predicate plus an actual ground-like collision-normal check. A continuous downhill or uphill profile may therefore remain drivable, while level ground, step discontinuities, flat walls and raised walls still reach the unchanged wall rays. Each occupied hull height is independent evidence, preventing a low prop from hiding a nearer upper wall. After accepted fragile/module contact, a residual pending skin may be skipped only through its exact registered OBB exit and another native recast; unrelated or backing geometry still blocks. The same helper may classify at most four adjacent proved-soft items, and a five-item chain fails closed. A directional-cap result remains planning evidence until the exact leading-hull commit seam accepts it. |

### Version 0.4.0 product layout and lobby configuration

The port is now rooted at top-level `0.9.22`; the audit resolves the repository
root once and derives source, service, documentation and release-data paths
from that layout. `build_wotmod.py` emits only `127.0.0.1:28782` in the
copy-ready release and never distributes `server_endpoint.json`.

`config.py`, `lan_session.py`, `bootstrap.py` and `lobby_ui.py` implement the
local product seams described in the module inventory. They do not change a
battle law or a server-ownership boundary. The Windows PyInstaller launcher is
deployment-only and remains covered by its launcher/CI tests rather than the
Python 2.7 wotmod source inventory.

Version 0.3.63 adds two behavior-preserving Bot hot-path reductions. Firing-
lane refreshes reuse only observer-independent target geometry at the same
ordered point in the render-frame loop; observer visibility and selected
aim/fire overlays remain source-local. Detection may skip a native visibility
query only when the mathematical upper bound--zero foliage and guaranteed line
of sight--still reports hidden. The 50 m unconditional path, 500 m ceiling,
cache expiry, decision cadence, movement/collision integration and all non-
visibility probe sequences remain unchanged.

The abandoned `battle_rpc.py`/`battle_rpc_translator.py` parallel battle stack
was deleted.  It was neither the 0.8.2 path nor the active #1513 path and could
silently drift from both.

Version 0.3.61 keeps the copied pose as the only compound-root owner. The
render loop mutates its persistent matrix without polling or assigning the
native `PyCompoundModel.matrix`; exact #1513
`CompoundAppearance.__linkCompound` already rebinds a refreshed model from the
overlaid `Vehicle.matrix`. `compat.py` exposes copied velocity and acceleration
only while the exact `AccelerationSmoother`, arcade oscillator or sniper
oscillator caller is executing. Tracks, suspension, physics and effects retain
the real WGVehicleFilter.

Version 0.3.68 retains the observational frame-pacing diagnostic at coarse
callback boundaries, delays attribution by one callback, and emits one bounded
summary every five seconds. `bot_runtime.py` still exposes monotonic pull-only
logical probe totals. Its optional per-query timer is not installed, so the
elapsed fields stay zero without two extra high-resolution clock reads per
native probe. The measurements do not enter a LAN payload, cache decision,
deadline or work budget; the diagnostic never repeats a native query in order
to measure it. On ordinary terrain the vertical law selects centre support, so
front/back are queried only when centre is absent; the fallback result is
unchanged while a 29-Bot flat frame drops from 87 ground calls to 29.

The first #1513 targeting tick reads `Vehicle.appearance.turretMatrix` and
`gunMatrix`, not the packed server-angle field. After the live vehicle pose and
arcade camera are bound, `battle_runtime.py` therefore calls the exact public
`VehicleGunRotator.reset()` once, echoes zero turret/gun angles, and only then
runs `_ammo_tick`. This resets the loading-time matrices without restarting
the rotator timer, marker lifecycle or sound objects.

## Shared battle service files

The 0.3.62 server boundary also separates wire cause from death attribution.
`reported_attacker(_bot)` is validated and retained only for the fatal ledger
and frag record. It is never copied into a non-attack `client_simulation`
health event, which both the server and client validators intentionally reject.
It retains strict contiguous Bot fire/combat revisions and records the exact
first rejection reason in a continuous cascade; an accepted batch resets that
diagnostic fence. This exposes client/server fire, base and acknowledgement
values without weakening protocol admission or flooding the server log.

### Version 0.3.76 frozen PREBATTLE aim and conservative neutral coast

`battle_runtime.py` supersedes 0.3.75's early aiming adapter and restores the
stock #1513 PREBATTLE freeze. Initial setup aligns camera/gun once, but no
private arena-start bypass runs. Gun, stock marker and server marker remain
frozen until the single native BATTLE period transition starts stock aiming;
`_battle_live` opens movement and fire at that same boundary.

`vehicle_physics.py` raises `COAST_BRAKE_SHARE` from `0.55` to `0.65`. Exact
#1513 resources supply mass, speed and terrain inputs, while a zero legacy
`brakeForce` retains the track-grip fallback. The private native C++ W-release
curve is not statically recoverable, so the change is a conservative
calibration rather than an exact-retail claim. Type 62 stop regressions cover
30, 60 and 120 FPS.

### Version 0.3.75 prebattle tracking, centred routes, finite Bot ammo and CTF visibility

Exact #1513 `PlayerAvatar.__onArenaPeriodChange(PREBATTLE)` clears
`__isOnArena` and stops `VehicleGunRotator`, while this port separately starts
`AvatarInputHandler` so camera and marker controls work during the countdown.
`battle_runtime.py` now calls `_publish_targeting_info()` before the rotator if
its private `__maxTurretRotationSpeed` is not ready, temporarily admits only
`VehicleGunRotator.start()` through the Avatar guard, restores
`_PlayerAvatar__isOnArena` in `finally`, and verifies
`_VehicleGunRotator__isStarted`. The full arena-start path is not invoked and
`_battle_live` remains the movement/fire fence.

`ai/navigation.py` applies `_baked_clearance_penalty` only when
`TerrainNavigator._prefers_baked_clearance` identifies a shared `route` or its
continued route. The score is derived only from the count of already-proved
baked links. `shortcut_preserves_baked_clearance` prevents smoothing from
raising mean missing-link exposure by more than 0.25. No link is synthesized,
and spawn joins, private recovery, collision, shallow-water and grade checks
retain their prior paths; a graph with only one valid cell-width remains
usable and unchanged.

`bot_runtime.py` changes only the full-pair tactical cadence:
`OBSERVATION_SECONDS = 0.40`, `SHOT_LANE_REFRESH_SECONDS = 0.20` and
`SHOT_LANE_QUERY_DISTANCE = 585.0`. The first half-window remains available for
the bounded cover batch, and periodic lane work occupies the final half. The
independent selected-target `SHOT_LANE_SECONDS = 0.20`, hard 110-pair frame
budget, complete-observation rule and final-fire gate do not change. Thus
server tactical knowledge may arrive less frequently, but an older lane proof
cannot authorize a shot.

`bot_runtime.py` also derives one finite inventory from the installed
descriptor `maxAmmo`. `_bot_ammo_distribution` assigns categories at `3:2:1`
for ordinary vehicles or HE-led `1:1:4` for SPGs and redistributes categories
the gun does not expose. `_BotAmmoState` keeps loaded, planned-next,
reload-pending and remaining state distinct: launch decrements the actual
loaded round, and only a completed reload edge promotes the prior next round.
`lan_client.py` carries `shell_index`, `next_shell_index`,
`ammo_reload_pending` and `ammo_remaining` atomically in the finite v5 Bot
projection.

`server/server_bot_ai.py` chooses only the planned next round from current
stock: standard by default, HE for a safely soft/finishable target and a later
higher-penetration non-HE round when standard penetration is inadequate.
Human contacts obtain their armor/class from `build_vehicle_profile()` over the
installed descriptor, cached by vehicle name. Live contact refreshes overlay
pose, health and team fields only; they cannot replace that immutable profile
with a mutable player-record armor field. The authority publishes the derived
armor in its complete observation for the server planner.

`server/lan_battle_server.py` checks inventory shape, exhaustion, one exact
decrement per fire sequence and loaded/next/reload-boundary legality, then
retains those canonical fields in snapshots. A promoted authority restores the
same loaded, next, pending and remaining state instead of generating a new
loadout. Player ammunition/reload admission is not moved to the server.

Lakeville's compiled space contains separate CTF and assault2 base instances.
The initial client-only-space setup writes the selected CTF bit `0x1`, but exact
#1513 can later overwrite it with `ClientVisibilityFlags.SERVER_MASK` before
deferred client readiness. `_finish_entity_startup()` therefore idempotently
calls `_configure_standard_space_visibility()` after that stock boundary. Exact
Lakeville mask `0xffffff89` remains visible under bit 0 and assault2 mask
`0xffffffc0` remains hidden, matching the tested
`1 -> SERVER_MASK -> 1` write sequence. XML, capture law, minimap, team
assignment and the one-base-per-team CTF objective record are unchanged.

### Version 0.3.74 bounded Bot publication and spawn recovery

`BotRuntime.update` continues to produce complete local authority state. This
is intentional: after publication, `battle_runtime.py` still consumes those
same records to launch Bot projectiles, including artillery's frozen exact
trajectory proof. `LANClient.send_bot_state` alone projects each record to the
finite v5 sanitizer field set before `_send` recursively freezes the payload.
The audit requires that projection, its atomic optional `shot_yaw`/
`shot_pitch` pair and the absence of client-only route/profile/physics fields
from the wire allowlist. It does not permit replacing the complete local state.

The 0.0975-second generic motion-plan deadline is independent of an exact typed
3x3 receipt's coverage. A refreshed clear, flat, powered, straight sample may
carry the old receipt only through `_contained_cached_world_receipt`, whose
origin, yaw, direction sign, vertical/lateral drift and actual-`dt` forward
reach must remain inside the exact proof. The ordinary hard/deferred and
catalog-contact paths still fail closed.

The navigation adapter imports the driver's single 1.5-metre arrival constant
and uses it both for target arrival and the far-goal parked-node fallback. The
traffic-wait adapter accumulates actual clamped driver steps and clears
stuck/recovery state only during a 1.5-second lease. If yielding remains active
after that lease, the copied finite recovery law is allowed to advance. None of
these adapters changes strategic routes, physical contact or server authority.

### Version 0.3.73 battle-ready wire canonicalization

`SpawnPlanner` intentionally keeps its local formation dictionary keyed by the
integer team ids `1` and `2`. The immutable reliable sender introduced in
0.3.71 accepts only mappings whose keys are already JSON text, so passing that
dictionary through unchanged rejected `battle_ready` before it reached the
sender worker. `LANClient.send_battle_ready` now copies only the two admitted
teams to `"1"` and `"2"` at the wire boundary. The executable audit pins the
adapter hash and the conversion steps; no formation, barrier or server
authority law changed.

### Version 0.3.72 elapsed projectiles and exact artillery

`projectile_runtime.py` is the pure trajectory/collision law shared by every
player and authority-Bot shell. It evaluates one gravity parabola in adaptive
chords no longer than 50 ms and with at most 5 cm sagitta, then transforms a
vehicle's start/end pose into a relative sweep for the same interval.
`projectile_manager.py` owns the bounded active
set, deterministic advancement, progress cursors and snapshot restoration;
`battle_runtime.py` supplies the exact #1513 BSP, destructible, vehicle, armor
and native tracer adapters. A terminal hit therefore occurs after elapsed
flight, not on the fire frame, and a moving vehicle can leave the swept path.

`ballistics.py` solves finite low/high ballistic roots and moving-target
intercepts. `artillery_controller.py` binds the server-selected rear SPG anchor
to target/shell/source identity, chooses a ballistic family, and freezes a
proved moving-target aim/flight intent until the matching native muzzle launch.
`artillery_arc_queue.py` schedules strategic and exact world chords fairly with
a hard four-native-ray render-frame budget. The exact launch probe still fails
closed on changed identity, source drift, expiry or blocked geometry; the
server-provided rear point by itself is not treated as open-sky evidence.

`lan_client.py` and `server/lan_battle_server.py` add the required
`projectile_ledger_v1` boundary. The server stores canonical launch parameters,
monotonic checked-through progress, active snapshots, authority epochs and
terminal tombstones. Shooter disconnect does not cancel an already-launched
shell. A successor authority resumes from server state, and terminal direct or
bounded splash HP effects plus shot-destructible receipts commit atomically.
The server does not reproduce proprietary client geometry or independently
prove ammunition/reload legality.

Stun remains disabled. The #1513 client exposes presentation fragments, but
the port has no complete server-owned penalty/duration state and medical-kit
recovery transaction. Static/Python proof also does not accept native tracer
visuals, sustained projectile/arc-probe performance, artillery feel or
repeated-round cleanup in the exact Windows #1513 client.

The production modules and their #1513 adapters are SHA-256 pinned by the
executable source audit after independent exact-head review. Any later drift
therefore fails the release gate instead of silently changing projectile or
artillery ownership.

### Version 0.3.71 selected 0.8.2 ports

`lan_client.py` moves ordinary outbound work behind an immutable plain-JSON
snapshot and a bounded reliable FIFO. `_send` never serializes or writes the
socket and it appends every accepted message without coalescing. The connection
worker still writes hello synchronously before publishing `connected`; one
sender thread performs all later JSON encoding and `sendall` calls in FIFO
order. The 256-message/1-MiB bounds and finite-number/depth/node validation
reject invalid snapshots before admission. The transport-generation fence
ignores stale worker activity, while overflow or sender failure closes the
current transport. This is an adapter around the unchanged v5 protocol, not a
new authority or combat law.

`tank_collision.py` supplies one shared pure support-rise predicate. For both
`battle_runtime.py` and `bot_runtime.py`, first terrain placement remains
unchanged; a later centre support above `min(frame climb, 0.85 m) + 0.02 m`
restores only that tick's pose rather than snapping the hull onto a wagon, roof
or large prop. The player reuses the existing hard-wall speed/grind response.
The Bot zeroes the rejected tick and invokes the same realised-motion
invalidation used by a navigation rollback and a hard resolver result.

That `bot_runtime.py` invalidation removes the affected decision and typed
motion receipt before passing the exact attempted yaw to
`ai/driver.py::remember_failure`. The copied driver now keeps one deterministic
escape side for a finite interval, avoiding symmetric left/right grinding while
still permitting the mirror branch after the bias expires. It also stops
forward torque while the hull is materially misaligned with a meaningful climb.
`ai/navigation.py` independently preserves a turning point immediately before
such a climb. The predicate gates baked-path smoothing plus live hull reach,
lookahead and bounded partial-path continuation, while flat corners and straight
climbs remain eligible for shortcutting.

These six production modules are separately SHA-256 pinned and the executable
audit verifies the enqueue/hello/generation contract, all three Bot
invalidation sites, first-placement ordering, rollback/wall response, finite
escape bias, uphill alignment and the smoothing/reach/lookahead/continuation
calls. The native 0.8.2 `WGVehicleFilter`/physics experiment is not ported.
Artillery remains the existing rear-anchor deploy/hold mechanism; open-sky
proof, ballistic arc and collision budgeting, indirect-hit resolution and stun
remain absent. Native motion/contact feel, postmortem camera behavior and
repeated-round teardown still require the exact Windows #1513 acceptance run.

### Version 0.3.70 movement and artillery-staging seams

`world_collision.py` now requires both a continuous bounded non-flat sampled
height profile and the actual ground-like hit normal before taking the copied
drivable-terrain path. The predicate is direction-neutral, so a downhill
profile is not turned into wall grinding merely because the sample delta is
negative. Level ground, sharp steps, flat walls and raised walls still execute
the unchanged blocking rays. `vehicle_physics.py` keeps the established flat-
road drivetrain share during neutral coasting, then progressively unloads only
that share when current motion points downhill. At or beyond the static-hold
tangent, descriptor rolling resistance is the remaining longitudinal drag.
Uphill coasting receives no downhill relief; active opposite drive, handbrake,
static hold and uphill slip remain separate laws.

`destructibles_sensor.py` exposes exact typed results without weakening the
stock gate. Real physical speed supplies the ordinary swept-contact gate and an
accepted fragile/module `crushed` result may advance. Low-speed or stationary
matching drive may instead present only the corresponding descriptor top speed
as gate evidence. Native submission is still limited to the exact leading hull
face plus a 0.075-m margin and this frame's physical travel. Its typed receipt
causes `battle_runtime.py`/`bot_runtime.py` to hold that submission tick and
restore pre-step real speed; the cap never becomes pose, LAN or ram momentum.
On following ticks, an accepted pending skin clears only through its unique
registered OBB exit and a real backing-ray recast. Falling, backing, ambiguous,
rejected, under-threshold and still-solid contacts remain `hard`.

The Bot direction probe retains the staggered copied-driver cadence, with a
three-lane 15-metre corridor at low speed and 20 metres above 5 m/s. It may skip
only a pure-read chain of unique stock-crushable catalog OBBs and advances after
each exact exit. The skip cap is four; a fifth item fails closed. Normal straight
planner alternatives retain those six horizontal rays. Only the finally
selected flat, straight, powered motion sample requests the typed world receipt:
three exact commit-width lanes, the 0.6/1.1/1.6-metre commit heights and 15
metres of forward coverage. The receipt binds origin, yaw and travel direction.
Per-render world rays may be omitted only when the actual-`dt` leading-hull
sweep remains strictly inside that receipt and no catalog OBB intersects the
hull. A hard receipt marks the motion sample blocked; a deferred receipt is not
cached. A generic corridor `clear` result, missing/expired proof, pose/yaw drift
or catalog contact cannot authorize the bypass. Coasting, braking, turning and
airborne motion remain world-first. Final-motion receipt jobs are hard-capped at
13 per render frame. The waiting rotation retains only Bots that actually
reached this eligible request; idle, hard-blocked, turning or airborne Bots drop
out. Unattempted receiptless work keeps initial-backlog priority over refreshes.
Once its native callback itself defers, it loses that priority and rotates behind
the other enrolled requests, so neither a persistent callback deferral nor a
refresh can starve the other. Initial deadlines are uniformly spread across the
full 0.0975-second decision interval. Budget or native-callback deferral pauses
that eligible Bot for one frame at pre-step real speed, does not call
`remember_failure`, does not cache a deferred result and does not move the same
nine-ray cost into the authoritative per-frame sweep. At a strict simulated 24
FPS, 29 startup receipts consume 13/13/3 jobs and grow the cache 13/26/29. The
stationary scan only registers streamed chunks, and neither planning path marks,
publishes or visually changes an object.

`server_bot_ai.py` derives a direction-neutral own/enemy axis from uploaded
route geometry and caches one rear-side point from each SPG's safe route. It
emits `artillery_deploy` until the Bot reaches the bounded arrival radius and
then `artillery_hold` with zero throttle; base-defense diversion remains higher
priority. The anchor is server-side staging evidence only. This release section
does not claim open-sky validation, a client ballistic arc solver, arc collision
budgeting or indirect-hit resolution; those remain Windows/client acceptance
and implementation boundaries.

### Version 0.3.69 bounded behavior seams

The server's canonical capture update now passes only `invaders`, time
remaining, exact threatened own-base coordinates and contributor identities to
the macro planner; it does not add contributor poses. `server_bot_ai.py` keeps
a stable one-to-three-responder incident, ranks eligible nearby/fast Bots by
ETA, targets only the threatened base and normally leaves one living Bot on
its prior assignment. `bot_runtime.py` keys navigation by the stable threatened
base rather than a changing combat target. Responders retain the ordinary
visible and per-Bot shootable gates; a contributor can be prioritized only if
it is already a legal contact, so unseen invader state never becomes a target
order.

The #1513 postmortem bridge now accepts a viewpoint request only in the stock
postmortem control after its delay and only for a living friendly Vehicle. The
runtime transactionally rebinds `ConsistentMatrices.attachedVehicleMatrix` and
invokes `PlayerAvatar.onSwitchViewpoint`. `remote_vehicle.py` exposes only the
selected synthetic ally to native entity lookup, and clears that exposure on
the next switch or cleanup. Death/removal of the selected ally falls back to
the nearest living ally. These are #1513 lifecycle/presentation adapters, not
new combat law; native Windows camera continuity remains an acceptance gate.

| Service file | Classification | Source and exact reason for divergence |
|---|---|---|
| `server/lan_battle_server.py` | Protocol/shared-law adapter | Retains the 0.8.2 LAN v5 room, snapshot, combat and authority model. The #1513 load barrier and exact client-build/map-pool separation are required because its native Vehicle entities enter asynchronously. Standard capture and round timing are intentionally server-owned so every client observes one result. The final load prerequisite queues one tick-zero `battle_live` barrier; the tick thread sends it before advancing time or publishing snapshot one. Relative timing is projected from the network-thread receive timestamp on a monotonic clock with bounded half-RTT correction, and older timing ticks cannot rewind it. Admitted combat events receive monotonic round-local ids and are emitted before the snapshot that contains their resulting state. Player repair reports and authority-bot repair/fire publications carry explicit base, proposal and acknowledgement revisions, so a socket write is never mistaken for server acceptance and a later snapshot cannot rewind or double-apply progress. The server reduces each validator-accepted authority observation to canonical visibility fields and broadcasts that standalone, round-scoped message to every participating #1513 client; it does not expose unvalidated pose, health or firing-lane claims through that presentation path. Firing-client critical proposals additionally compare the target base/ack token before mutation; a stale proposal retains only its pre-critical hull damage and cannot restore old module state or reuse an obsolete ammo-rack damage amplification. Destructible geometry remains client-resolved; the server validates the bounded identity payload, deduplicates it, stores a per-round revision and broadcasts the accepted result. Version 0.3.64's `BOT COMBAT` diagnostic adds the admitted source plus attacker and target teams, without changing admission or damage. |
| `server/server_bot_ai.py` | Current 0.8.2 service law | The production macro coordinator consumes only authority-reported contacts/cover, reuses the port-local exact copy of the 0.8.2 cover scorer and emits portable JSON orders. Team-visible contact memory is shared intelligence, but only Bots listed in that contact's current `shootable_by_bot_ids` may receive a target assignment; blocked Bots stay on their existing route instead of converging on the shared red dot. A non-brawler inside its close-range limit withdraws along its validated route before the general firing-envelope hold can capture that case, while a normally ranged shooter still engages. The `e0a74c9` hull-yaw/rear-connector spawn-join law is ported on this server path as well, because live LAN orders take priority over the client fallback. Route points remain lanes rather than parking places. The #1513 client applies each immutable server-order revision once and owns the copied local cadence/spatial optimization, so the service does not duplicate client-frame caches. Both reviewed service modules are SHA-256 pinned. |

## `battle_runtime.py` behavioral decomposition

| Behavior | Required source | Current status / permitted difference |
|---|---|---|
| Shared load and countdown | 0.8.2 15-second prebattle | Ported. All #1513 human vehicles first report native readiness and the authority publishes the canonical bot manifest; the server then emits one ordered tick-zero barrier and opens a shared 15-second countdown while the client continues the original staggered bot materialization. Receive-thread timestamps, monotonic deadlines and timing-tick rejection remove frame-stall, wall-clock and out-of-order rewind errors. |
| Team roster, markers and spotting | `network_battle.py` roster plus current `offline_battle.py` spotting/foliage | Ported through stock #1513 Arena/Vehicle records. Enemy model, marker and minimap visibility share one boundary with 50 m proximity, 500 m ceiling, two-point solid LOS, allied relay, five-second memory, moving/still descriptor camouflage, shot penalty and pair-specific bush cover. The 41-map foliage batch is baked from #1513 `SpTr`/`BWST` world transforms and ctree-v106 bounds; it does not reuse 0.8.2 coordinates or the newer-client vehicle fallback table. |
| Vehicle lineup | `bot_ai.py` matchmaking template plus `offline_battle.py` descriptor enumeration | Ported from final release baseline `7e3a1b2`: every battle chooses a one-, two- or three-tier band around the player from the complete eligible #1513 catalog, builds one mirrored tier/class template, accounts for every LAN human, caps each team at one SPG and sorts roles into formation rows. There is deliberately no process-wide/preloaded vehicle pool. |
| Spawn layout | `_formation_slot` and arena CTF bases | Ported through `SpawnPlanner`; stock #1513 bases/bounds are the only data difference.  Each `(team, slot)` pose is resolved and grounded once, then reused by both manifest and entity creation.  A deterministic local hull-height/ground probe avoids structures without replacing the retail anchor with map centre. |
| Longitudinal and traverse motion | `physics.py` plus exact #1513 input/presentation APIs | Stock `PlayerAvatar.moveVehicle` supplies the audited `1/2/4/8` flags, but a client-created Vehicle has no retail game-server transform stream. The exact copied integrator is therefore the sole pose owner for player and Bots. The player pose is exposed through #1513 `Vehicle.model.matrix`, camera and position/yaw consumers. The copied speed/turn speed additionally overrides exact `PlayerAvatar.getOwnVehicleSpeeds`, because both `_SpeedStateHandler` and `getOwnVehicleShotDispersionAngle` consume that method rather than `Vehicle.getSpeed`; the ABI audit pins that consumer chain. The tachometer is published through exact `VEHICLE_VIEW_STATE.RPM` using #1513's three-gear simulated-RPM law. Bot poses directly update the 0.8.2-style presentation and are published to the server. |
| Tank contact and ram damage | `vehicle_collision.py` `chassis_shape`, `vertical_overlap`, `obb_contact`, `pair_response`, `build_spatial_index`, `nearby_ids`; `offline_battle.py` `_tank_resolve`; `physics.py` `ram_damage` | Ported as the engine-free `tank_collision.py` law. The local player and authority Bots receive the same descriptor-sized, yaw-aware chassis contact, mass-weighted correction, e=0 forward impulse and decaying lateral push; existing spawn overlap is separated rather than treated as a blocked route. The copied per-tick spatial broad phase removes distant O(N²) candidate scans without changing exact pair resolution. A #1513-only world probe vetoes only the resulting contact displacement when it would cross static geometry. Synthetic remote models do not also install `PyModelObstacle`, because the world probe would treat that second live tank shape as a static wall and stop ahead of the copied chassis contact. Ram pairs are cooldown-gated both in the copied client law and by a server-owned canonical pair clock that survives authority failover, then applied to both canonical HP records. |
| Terrain pose and static walls | 0.8.2 physics/probes plus #1513 collision queries | Copied player and Bot footprint, water, slope, ground and obstacle laws produce the authoritative poses. Because #1513 reports smooth ground beneath city walls differently from 0.8.2, version 0.3.70 requires a continuous bounded non-flat profile plus an actual ground-like hit normal before treating either an uphill or downhill sample as drivable. Flat streets, sharp steps, flat walls and raised walls still execute the copied horizontal hull rays. Neutral coasting retains the established flat-road drivetrain drag but progressively unloads that share with downhill grade; at or past the static-hold tangent only descriptor rolling resistance remains. Uphill coasting, deliberate braking and the static hold keep their own laws. The native player compound model and synthetic remote presentations consume those poses without `Entity.teleport` or a parallel pose owner. |
| Local camera follows copied pose | 0.8.2 pose ownership plus exact #1513 `ConsistentMatrices`, camera and aiming lifecycles | Ported with required ABI substitutions. The canonical copied matrix is installed before native Avatar/input-handler startup. Exact #1513 `ConsistentMatrices.__linkOwnVehicle` is then linked to that provider rather than leaving `ownVehicleMatrix` on the retail filter's spawn-only `bodyMatrix`; attached, own, stabilised, gun and minimap consumers therefore share one translation. During an arcade/sniper transition, the adapter supplies the canonical filter source before the new control's `enable()`/`focusOnPos()` calculations run; the post-transition hook only asserts source identity and raises on stale providers. Fixed-turret aiming receives a scoped filter proxy for the exact `VehicleGunRotator` consumer without replacing the native filter object. This prevents both the persistent birth-position camera split and the one-frame sniper direction jump. |
| Destructibles | `destructibles_authority.py` plus `offline_battle.py` contact sensors | The 0.8.2 kinetic law is retained behind strict #1513 encoders and native transaction boundaries, but its permissive object-origin proximity destroy workaround is not. Schema v3 supplies exact transformed dynamic fragile/falling OBB contact and structure-module identity across all 41 maps; 61,625 unique signatures recover blank filenames, while 11 ambiguous signatures and 28 candidates fail closed. Version 0.3.70 keeps real-speed swept crushing and adds one bounded low-speed/start path: matching forward/reverse top speed may prove the stock gate only after this frame's leading hull face reaches the exact OBB. Native submission then holds one tick at pre-step real speed, never publishes the cap as vehicle/network/ram momentum, and later pending-skin clearance requires the exact registered OBB exit plus a native recast. Airborne falling, backing and still-solid geometry blocks; after touchdown, native BSP/world collision decides. Bot look-ahead remains a pure read over exact unique stock-crushable OBBs; it may skip at most four items, with a fifth failing closed. Only the finally selected flat/straight/powered sample may add the 15-metre exact 3x3 world receipt. Ordinary straight motion reuses it only when actual-`dt` containment holds and no catalog contact exists; hard proof blocks, deferred proof is not cached, and missing/stale proof, drift plus coast/brake/turn/airborne paths remain world-first. Zero-speed scanning only registers streamed chunks. Player and Bot shells share an ordered break-and-recast scene: exact static/vehicle caps and registered OBB exits are preserved; exact #1513 `19` HP and `(0, 25)` data produce AP/APCR/APHE fixed-25 cumulative, damage-preserving traversal, while old HE/HEAT stop at the first destructible and HE bursts there. A successful result crosses one explicit LAN seam; the server stores and deduplicates it, and every client replays it through the same authority registry. Static BSP/backing collision remains authoritative. |
| Macro/local AI | `server/server_bot_ai.py`, `bot_ai.py`, `bot_ai_driver.py`, `bot_ai_navigation.py` | Production macro orders are the intentional server offload described above. Team spotting retains validated contact memory, but only explicit per-Bot firing-lane evidence creates a target assignment or permits fire; Bots without that local lane continue their route. The first spawn-to-route join is Bot-scoped so a whole team cannot inherit one hull's cached egress; later route segments remain shared. Local navigation keeps the latest driver congestion recovery, cancels obsolete private joins, rejects shallow-water shortcuts and probes close firing lanes rather than assuming they are clear. Version 0.3.70 selects a stable rear-side route anchor for each SPG from direction-neutral own/enemy route geometry, deploys there and holds after arrival; base defense remains higher priority. The anchor is staging only and does not claim open sky or a client indirect-fire solution. One immutable server-order revision is normalized once; local planning/steering refreshes on a staggered ~10 Hz cadence while movement and presentation advance every rendered frame and LAN Bot state remains capped at 30 Hz. #1513 graphs are accepted only after a build-specific bake/manifest is published. |
| Bot cover, targeting and player spotting | `bot_ai_cover.py`, descriptor turret range, `offline_battle.py` spotting | Ported. The #1513 adapter supplies LOS, prebaked foliage, slope, water, congestion, peek and escape affordances. Both observation directions use the copied 50-metre proximity, descriptor view range, stationary/moving target camouflage, 0.75-second shot penalty and detection-distance law; the player-facing path additionally retains ally relay and five-second presentation memory. |
| Sixth Sense | `_offh_has_sixth_sense`, `_offh_update_sixth_sense` | Ported. Generated #1513 commanders receive the real `commander_sixthSense` skill bit. An enemy Bot that legitimately detects the local player can trigger the indicator even when that particular observer has not been spotted by the player's team; team-shared placeholders are not treated as direct Bot sight. Enemy-bot observations retain the copied five-second memory and three-second delay, then enter the stock #1513 vehicle-state controller. Every participating LAN client consumes the same validated server relay, while the authority does not pre-consume its outgoing proposal; cleanup cancels the battle-owned delayed callback. |
| Direct armour hit | `_offh_penetration`, `_offh_resolve_hull_hit` | Ported including range loss, shell kind, normalization, ricochet, overmatch, spaced armour, tracks and HEAT stop. #1513 owns `piercingPower` on `GunShot`, not shell. One factor is sampled lazily at the first required penetration test and reused for the shot; each tested hit evaluates its range mean before cumulative obstacle loss. This ordering is a documented high-confidence reconstruction because the private 0.9.22 server source is unavailable. |
| HE burst and near miss | `_offh_he_splash` and copied HE helpers | Ported.  Direct victims are excluded from the second pass; every other vehicle inside the descriptor radius gets the original burst-to-hull ray, nominal facing armour, +/-25% roll, distance decay and explosion-specific module saving throw.  A target-specific report key lets one shell update several server-owned HP records without accepting a duplicate against the same target. |
| Module/crew critical hit | `_apply_module_damage`, `device_damage.py`, internal layout modules | Ported. The generation tool extracts the complete closure dependency slice and the audit pins the reviewed generated adapter plus its copied support modules in the port-local manifest. The firing client calculates a bounded proposal on a detached vehicle-state snapshot; it cannot alter the live target. A #1513 proposal is bound to the target's exact critical/combat base and acknowledgement token and carries its pre-critical hull damage separately. The server accepts the full critical state and any ammo-rack damage amplification only when that token still matches; a stale but otherwise valid proposal applies the original hull damage, records `stale_target_state`, and cannot rewind a repair or fire transition. The server broadcasts the admitted event before the containing snapshot; receiving clients install it once without re-rolling. Player presentation uses audited #1513 `showVehicleDamageInfo` and `updateVehicleMiscStatus` instead of 0.8.2 mock Flash calls. |
| Fire, drowning, repairs and consumables | `_offh_ignite`, `_offh_extinguish`, `_offh_knock_out_everything`, `_offh_activate_equipment`, `device_damage.py`, `_offh_water_depth` | Ported for the local player. Fire uses the copied 5% max-HP one-second tick, 10-second burnout and fuel-tank regen-cap transition. Water uses the copied 0.3-second probe, 0.5/1.6 m thresholds and 10-second timeout. The exact #1513 activation code selects one damaged module or crew member for a small kit, or extinguishes fire, consumes the item once and republishes canonical critical state. Authority bots run the same copied repair and fire constants. Their server ledger carries combat base/ack revisions plus fire elapsed/tick phase, so a delayed echo cannot rewind a repair, double-count a fire tick, or restart the ten-second duration after authority failover; the server also retains the igniter through a delayed burnout death for frag attribution. Bot drowning remains open. |
| Player and bot gun, ammunition and dispersion | 0.8.2 `_gun_state` closure, #1513 `PlayerAvatar`, and bot firing loop | Player ammunition/reload remains in `gun_mechanics.py`, but the stock #1513 Avatar is the sole owner of the local reticle. Its audited `getOwnVehicleShotDispersionAngle` consumes the copied m/s and rad/s pose overlay plus descriptor factors, additive factor, aiming history and shot bloom. The copied hull matrix is also bound to the independent `_PlayerAvatar__ownVehicleStabMProv` used by `VehicleGunRotator`; otherwise the rendered hull moves while turret angles and the muzzle ray remain relative to the stale spawn pose. The trusted-client cell echo updates `gunAnglesPacked` from the native rotator's current speed-limited angle before server correction. Full-speed light-tank movement legitimately expands stock #1513 dispersion by roughly eight times, so the offline battle scales all three native motion coefficients to 25%; HUD and physical scatter still consume the same native angle and stationary/after-shot laws are unchanged. If the user enables server aim, the local cell also feeds that same native ray and angle through `VehicleGunRotator.setShotPosition`; this reproduces 0.8.2's paired-marker refresh and prevents the otherwise unserved second marker from retaining its initial oversized circle. The adapter never replaces the stock dispersion method. Exact #1513 returns a native gun-ray vector that can reject component mutation, so the adapter copies that ray into a mutable `Math.Vector3` before normalization or Gaussian scatter; unknown contracts still raise and print the original traceback. Authority bots preserve descriptor full/intra-clip reloads, loader/ammo-rack factors, turret/gun speed and limits, destroyed-gun gating, alignment, an independent current firing lane and deterministic Gaussian shot direction. Version 0.3.75 also allocates finite descriptor `maxAmmo` at ordinary `3:2:1` or SPG `1:1:4`, keeps loaded/next/reload-pending/inventory atomic across authority takeover, and lets the server plan only the next available standard/HE/higher-penetration category from descriptor-derived target armor. Their copied driver retains its continuous proportional turn value through the physics integrator; `rotation_dir` is only the discrete network/UI projection, not the physics command. The finite physical `shot_yaw`/`shot_pitch` and shell speed form a canonical launch; collision then advances over elapsed flight rather than resolving the full range on the fire frame. #1513 reload updates are edge events so the stock HUD interpolates continuously. |
| Projectile flight and artillery arcs | `projectile_runtime.py`, `projectile_manager.py`, `ballistics.py`, `artillery_arc_queue.py`, `artillery_controller.py` | Ported as bounded #1513 adapters. Every shell follows its gravity curve through adaptive chronological chords no longer than 50 ms and with at most 5 cm sagitta, including moving-vehicle relative sweeps. Direct and artillery fire lead moving targets; SPGs try low/high families from the rear anchor, exact world proof consumes at most four native rays per rendered frame, and a pending moving-target launch holds one frozen aim/flight intent. The server ledger retains launches, progress, authority takeover and atomic terminal HP/destructible effects. Stun remains disabled pending a complete canonical penalty and medical-kit loop. |
| Health, death and terminal result | `network_battle.py` and 0.8.2 Arena callbacks | Local and remote wreck state share one event path and preserve durable attacker identity, death reason, critical state and drowning display HP. Accepted and natively applied event ids are separate: authoritative state is merged immediately, while a strict FIFO journal waits until every referenced staged Vehicle has entered the arena before presenting shot/hit/critical/health/death exactly once. A pending `keep_corpse` retains its live initial state so native arena-add precedes its fatal transition; unknown or lost entities fail the battle with the original traceback. Server-owned `VEHICLE_STATISTICS` publishes enemy frags and friendly-fire deductions; the human team killer additionally receives the exact #1513 `TEAM_KILLER` update. The server publishes only the terminal winner/reason/base team; the detailed 0.8.2 personal/player/vehicle result record remains open. |
| Standard base capture | 0.8.2 50 m / 1 Hz / max-three cappers law | Moved unchanged to the server.  The authority uploads the exact `ArenaType` base coordinates at the native load barrier; coarse annotations are only a legacy-client fallback. Version 0.3.69 reads that canonical result to divert a stable, ETA-ranked one-to-three-Bot responder group only to an actually threatened own base. Movement diversion does not weaken contact visibility or firing-lane admission. Lakeville keeps one CTF objective per team; the local #1513 adapter reapplies selected visibility bit `1` after the late stock server-mask update so the separate assault2 base instance remains hidden, without changing server capture data. |
| Authority failover | `network_battle.py` | Ported; promotion merges immutable manifest profile/route with the last canonical pose, health, aim and fire sequence instead of respawning bots. Repair/fire base, acknowledgement, elapsed duration and tick phase are canonical too. If the old authority advanced the same base after the promoted client consumed its last snapshot, the new authority performs one explicit canonical handoff reset, discards overlapping local proposal numbers and resumes after the server acknowledgement. Server-owned ram pair cooldowns do not reset when authority changes. Version 0.3.72 also resets local artillery proofs and restores every active projectile from the server's authority-epoch snapshot at its accepted progress cursor. |
| Rendering and effects | Split local/remote adapter | The player retains stock #1513 shooting/health/death methods. Remote compound models use the copied mock health/collision surface; the exact #1513 turret/gun node binder consumes their aim matrices, while shot events reuse the stock shoot extra and recoil assembler. Local and relayed tracers consume the canonical origin, velocity, gravity and launch time while damage authority advances the same elapsed trajectory. Server-accepted impacts enter a strict ordered journal before stock `showShotResults`, `onBattleEvents`, `showOwnVehicleHitDirection` and session-provider health paths. Snapshot state cannot overtake that one-shot cause, and native presentation waits for staged entities instead of calling half-created compound models. Cleanup stops active extras, projectile presentation and the shared tracer mover before restoring entity lookup wrappers. |

## Every 0.8.2 source file

Every filename below is also enumerated in the executable audit.  A grouped
reason applies to every explicitly named file in that row.

| 0.8.2 files | Disposition and reason |
|---|---|
| `bot_ai.py`, `bot_ai_cover.py`, `bot_ai_driver.py`, `bot_ai_maps.py`, `bot_ai_maps_extra.py`, `bot_ai_maps_group_a.py`, `bot_ai_maps_group_b.py`, `bot_ai_maps_group_c.py`, `bot_ai_navigation.py`, `physics.py` | Copied, import-normalized, or minimally adapted to exact #1513 map resources as the authoritative AI/physics laws. Exact/normalized copies are compared directly; reviewed final-release adaptations are SHA-256 pinned. |
| `offline_battle.py` | Decomposed law source.  Spawn, lineup, motion, terrain/static probes, cover, penetration/spaced armour, health/death and standard capture are traced above. The `c57c186` retained-event sweep applies only to the persistent 0.8.2 synthetic arena; #1513 uses the audited stock `ClientArena.destroy` event-manager cleanup and explicit battle-owned callback teardown. The `b57f56f` manual music lifecycle is also specific to 0.8.2's synthetic Account: the real #1513 `PlayerAvatar` natively applies user sound preferences, enters arena music/ambience after GUI startup and leaves it during Avatar retirement; lifecycle bytecode gates verify both sides so a duplicate subscription is not copied. The `7e3a1b2` current-tick safety guard is ported without any historical-pose rollback. The still-unported sections are explicit blockers below. |
| `network_battle.py` | Protocol/authority source.  LAN v5, room ownership, snapshots, events, interpolation and authority failover are ported into server plus thin #1513 modules. |
| `offline_battle_stack.py` | Its session ownership/cleanup intent is ported; its 0.8.2 Account/Avatar construction cannot be copied into the #1513 lifecycle. |
| `device_damage.py` | Copied laws with the audited #1513 native-attribute descriptor seam described above. Its runtime integration and authoritative state transport are tracked in the blocker section below. |
| `destructibles_authority.py` | Retains the 0.8.2 authority with the audited #1513 fragile encoding, explicit projectile-sync and post-native commit/rollback boundary described above. `destructibles_compat.py` supplies only names moved to `DestructiblesCache`; local movement and shot sensors retain the original gameplay gates but fail strictly at the native boundary. |
| `EXrequests.py`, `command_handlers.py`, `command_router.py`, `data.py`, `server.py`, `state.py` | 0.8.2 Account RPC/lobby implementation.  Replaced only by exact #1513 Account RPC consumers; these are not battle laws. |
| `_constants.py`, `paths.py`, `user_config.py` | Configuration/persistence source.  Only LAN settings needed by #1513 are carried by `config.py`; battle tuning must stay in copied law modules. |
| `lan_settings.py`, `lan_waiting_room.py` | `waiting_room_ui.py` ports the 0.8.2 waiting-room presentation onto the exact #1513 native GUI, and `queue_ui.py` remains the stock training-room map picker for a client that cannot build that surface. The 0.8.2 LAN settings panel is not ported: the desktop launcher owns the server address before the client starts. |
| `session_guards.py` | 0.8.2 game-session monkey patches are ABI-incompatible.  Equivalent #1513 boundaries are explicit in `compat.py` and covered by lifecycle audits. |
| `pen_indicator.py` | Not copied because stock #1513 gun-marker/penetration UI is used through audited native entities; it contains presentation, not hit authority. |
| `__init__.py`, `logging.py`, `utils.py` | Package/support helpers.  Replaced with package-local helpers or stock #1513 utilities; no combat law is discarded. |
| `internal_geometry.py`, `internal_hit_layouts.py`, `internal_layout_profiles.py`, `internal_layout_store.py` | Copied exactly or with package-import-only normalization.  The 251 legacy profiles remain evidence for their original vehicles; newly added #1513 vehicles use the copied fallback compartment law rather than invented hard-coded layouts. |
| `internal_layout_debug.py`, `physics_monitor.py` | Optional X-ray/telemetry output only.  The user explicitly requested no trace-heavy debug output, so these presentation/telemetry modules are not shipped. |
| `bw_script.py`, `dis_cam_update.py`, `dis_cameras.py`, `dis_rotate.py`, `dis_setup.py`, `fix_app.py`, `fix_app_regex.py`, `fix_camera_bypass.py`, `fix_camera_hook.py`, `fix_chassis_cleanly.py`, `fix_chassis_crash.py`, `fix_force_cam.py`, `fix_force_cam2.py`, `fix_force_camera.py`, `fix_force_camera2.py`, `fix_force_camera3.py`, `fix_hook2.py`, `fix_swinging_override.py`, `fix_target_yaw.py`, `fix_typo.py`, `inject_active_cam.py`, `inject_active_cam2.py`, `inject_aih.py`, `inject_enable_log.py`, `inject_logger.py`, `inject_logger_setup.py`, `inject_mouse.py`, `inject_shift.py`, `inject_swinging.py`, `patch_manual_cam.py`, `remove_shift.py`, `test_matrix.py` | One-off source-rewrite, disassembly or investigation scripts with hard-coded 0.8.2 Windows paths.  They are development history, not runtime behavior, and must never enter the wotmod. |

## Open parity blockers

These are the remaining places where the working 0.8.2 battle has behavior
that the #1513 port does not yet reproduce.  They are not explained away as
"version differences":

- passive optional-device, food and crew-skill modifiers; the current offline
  garage publishes stock vehicles with empty optional-device slots, while the
  100% crew plus commander baseline and critical penalties are active;
- detailed battle-result statistics (`personal`, `players` and `vehicles`);
  the terminal winner/reason and live frags/team-killer state are already
  server-owned;
- bot drowning; the copied movement, terrain, slope and falling laws are
  active, while the copied drowning law currently applies only to the local
  player;
- stun penalties, durations and medical-kit recovery; artillery trajectories,
  ordinary tank-gun HE splash and elapsed near misses are ported, but stun is
  deliberately disabled without that complete canonical loop;
- exact battle presentation effects not already supplied safely by a stock
  #1513 Vehicle method.

Until those rows are implemented or explicitly descoped by the user, this
audit can prove source coverage and prevent replacement laws, but it cannot
label the #1513 battle feature-complete.

The same limit applies to movement quality, projectile/artillery feel and
destructible presentation:
source, catalog and pure-logic gates do not execute the Windows BigWorld
engine. Multi-map #1513 acceptance must still prove tracer visuals, shell timing
and dodgeability, low/high arc behavior, projectile/arc-probe frame pacing,
intended object removal, the hiding-to-pass-through transition, surviving
backing collision and the remaining visible hitch level; this audit does not
claim all frame pacing is resolved.
