# 0.6.0 Alpha 1 feedback closure

This ledger covers tester feedback for the exact Chinese HD client
`0.9.22.0.1 #1513` and launcher prerelease `0.6.0-alpha.1`. It distinguishes a
graceful battle-start refusal from a native process crash. Every
code-closeable report below has a candidate implementation and regression
coverage. The only evidence still required from a tester is a Windows dump for
an actual `WorldOfTanks.exe` termination or replay time-warp crash.

## Implemented in the feedback candidate

| Area | Report and root cause | Candidate result |
| --- | --- | --- |
| Battle startup (`#1513`) | The screenshots with `destructible filename disagrees` or `streamed destructible identity disagrees` are graceful battle refusals, not process crashes. Empty WGDE table-2 rows did not consume a streamed native item index, but the old baker counted them. A partial `wg_getChunkDestrFilenames` prefix could also disagree even when the unique object identity was correct. | Destructible catalog schema 5 compacts empty rows and all 41 supported map catalogs were rebuilt. A filename-prefix disagreement is diagnostic; unique matrix signature, exact wire and effect category remain fail-closed. This covers the countdown-to-Garage, restart-to-Garage and delayed `#1513` reports shown in images 2, 3, 5, 6, 7 and 8. |
| Battle startup (entity `1019`) | IDs from 1000 upward are synthetic remote vehicles. The public AOI facade can hide one while its private presentation still exists, causing `Vehicle entity 1019 is unavailable`. | Visibility-gated marker startup resolves the private presentation and no longer returns to the map picker merely because the public entity is outside the current AOI. |
| Shell-destructible props | Karelia's range towers, boxes, camouflage boxes and canisters are stock `fragile` destructibles, but the shell fallback considered only chunks already scanned near a vehicle. | Catalog load now prepares a bounded whole-map OBB index. Shells can validate, register and destroy a distant stock prop by streamed count, exact matrix signature, wire, descriptor kind and native effect category. Contradictory data remains fail-closed. |
| Projectile flight and impacts | Testers reported shells apparently flying away, disappearing, passing through objects or producing no ground response. Replicas lacked canonical projectile metadata, and a terminal could not distinguish a zero-damage vehicle collision from a world collision. | The candidate retains canonical launch metadata, preserves the native shot ray before normalization/scatter, advances the parabolic path through bounded collision chords, and carries `hit_vehicle` independently of damage. World terminals play the ground/rock impact path; wreck and fragile-object terminals stop the shell; old peers retain a compatibility fallback. |
| Penetration and module damage | Full-length frame chords let one shell test nearly every internal module, any HP loss became a yellow module, and the reconstructed range/overmatch/HEAT laws diverged from #1513. | The first vehicle material starts a `caliber / 100` metre trace budget. P100/P500 use the fixed 400-metre slope, every armor layer shares one penetration roll, material flags and shell-specific ricochet/normalization rules are honored, and HEAT continues through spaced armor with distance loss. Module HP is independent of residual penetration; duplicate boxes roll once, hidden damage remains normal above 50%, repaired modules keep explicit yellow state, and no-profile fallback fails closed. |
| HE, fire and Deadeye | HE direct/splash hits reused the solid internal ray, damaged fuel tanks could ignite early, engines rolled fire only when destroyed, and the trained Deadeye perk was ignored. | Every HE path uses a 45-degree cone with the same ten-caliber axial depth. Engines roll their descriptor fire chance on each successful module hit, fuel tanks ignite only at zero HP, and a completed Deadeye perk adds three percentage points only to AP, APCR and HEAT module/crew hit chances. |
| Blind-hit feedback | A local shot hitting or killing an unspotted enemy still reached `showShotResults`, battle ribbons, floating health presentation and critical/death effects, disclosing the hidden target. | Local attacks on enemies outside team spotting still settle authoritatively, but the visible client suppresses penetration/non-penetration voice, ribbons, floating damage, critical/ammo-rack/fire effects, kill presentation and vehicle-impact effects. Spotted targets and damage received by the local vehicle retain normal feedback. |
| Wreck and terrain occlusion | Wrecks were skipped by projectile collision and cursor occlusion, so shells could pass through them and an enemy outline could remain visible behind a wreck. Rock reports use the same exact static-world ray boundary. | Retained wreck hit testers stop shells without damaging the wreck, and the nearest world/wreck obstruction suppresses the target outline. |
| Base capture | The server paused enemy capture whenever a defender entered its own standard CTF base. | Defender presence no longer pauses or clears capture. Each invader continues contributing until that invader leaves, dies or takes qualifying damage. |
| Damage and kill totals | Vehicle damage and kills were copied into both the VEH and AVATAR result blocks. The stock summary adds both blocks, so one 880-HP kill appeared as 1,760 damage and two kills. | Combat statistics remain only in the VEH block; the corresponding avatar-only fields are zero in full and public results. Retries reuse the durable receipt instead of adding the event again. |
| Battle HUD totals | The stock #1513 Feedback panel can show total caused, assisted and armor-blocked damage. Caused/assist events existed, but blocked damage was available only in final settlement. | Every canonical enemy non-penetration publishes the stock `TANKING` event using the same bounded blocked amount as settlement. Ordered event IDs prevent retry double-counting. The stock per-hit feed and the `damage / assist / blocked` totals remain governed by the user's Feedback settings. |
| Detailed battle results | Aggregate statistics were present, but per-target interaction rows were absent from the Details view. | Bounded attacker-target rows are persisted in the receipt and packed with #1513's `VehicleInteractionDetails` serializer into `VEH_FULL_RESULTS.details`, including spots, hits, penetrations, damage, assistance and kills. |
| Crew defaults | Fresh crews exposed only two or three choices and commanders received Sixth Sense automatically. | Every newly generated crew member starts with eight untrained skill choices and no selected skill. Existing saved garages are preserved. |
| Crew reset and manual training | Reset read `shopRev` as the tankman ID, and the exact free-XP training command was missing. | Reset consumes `(shopRev, tankmanInvID, costIdx)`. Manual training consumes `(shopRev, tankmanInvID, freeXP)` and uses the pinned 1:10 conversion. |
| Accelerated crew training | `XP_TO_TMAN` was stored but post-battle settlement did not use it. | Every crew member receives ordinary battle XP. When accelerated training is enabled, the least-experienced member receives the equal bonus and vehicle XP is diverted to crew. Receipt markers make the award idempotent across retries and restarts, and account updates publish only the touched vehicle and crew. |
| Food and Removed RPM Limiter | National food such as rations and cola did not appear because it has no activation action. Removed RPM Limiter was previously modeled as a false permanent engine bonus. | Food is published as a passive, non-clickable READY item while its crew factor remains active. Removed RPM Limiter now uses the exact #1513 trigger lifecycle: toggle on/off, 1.1 engine-power factor, 1.5 engine HP per second loss, stock PREPARING state, and real damaged/destroyed-engine consequences. |
| Climbing | The old port retained a fixed `0.54` drive-traction coefficient from its 0.8.2 reconstruction. After rolling drag it cut drive near 24.8 degrees, before #1513's own longitudinal grip curve begins to release. This explains the `0.6 climbs weakly` report. | The stock multiplier is now `1.0`, leaving the recovered #1513 curve intact: full longitudinal grip through 27.5 degrees, then interpolation by ground-normal Y to 0.1 grip at 32 degrees. Selected engine power, installed mass, speed limits and terrain resistance still govern the result. |
| Damaged tracks | A yellow/repaired track was reported to make turning abnormally slow. | Mobility and traverse penalties apply only while a track is destroyed. A yellow/damaged but operational track does not engage the tracked handbrake or reduce drive/traverse; regression coverage pins that distinction. |
| Water and overturn | AI could reject every deep-water escape segment and stop permanently. The local warning/death consumer was absent, while the copied movement model still had no rigid-body rollover generation. | AI may take a bounded step toward shallower water and otherwise drowns after the same 10 seconds beyond 1.6 metres as the player. For the local player, a pose already beyond the implemented caution/danger angles publishes the stock warning states and is destroyed after 30 seconds continuously in danger, without assigning an attacker. The copied integrator caps terrain-following tilt at about 35 degrees and has no rollover torque, so collisions and terrain cannot naturally flip a vehicle. |
| Trench edges | A high trench lip could be mistaken for the only ground support, rolling the entire vehicle pose back even when the horizontal hull path was clear. | Layered front/centre/back support probes now rescan below an implausibly high lip and keep the trench floor. Genuine horizontal wall collision remains authoritative. |
| SPG strategic visibility | Team-known enemies beyond the normal 565 m/yellow AOI retained a minimap marker but lost the 3D model, making long-range artillery aiming impossible. | Marker memory is separated from model AOI. Ordinary views remain bounded to 565 m; a local SPG in the stock strategic camera can render every team-spotted target in shell range, and leaving strategic view hides the distant model without deleting its marker. |
| Shell switching (`-0.01 s`) | During an active reload, #1513's `ReloadingTimeState` retained the old start time. Publishing `CURRENT_SHELLS` before closing the old cycle could leave the native `-0.01` sentinel attached to the new shell and block firing. | The client now publishes old reload `0`, then `CURRENT_SHELLS`, then the new reload duration. A completed reload can fire directly after the switch; no switch-back workaround is required. |
| Local engine sound and instruments | Copied local physics moved tracks and smoke but left the stock `DetailedEngineState` links at zero, causing missing engine sound and stale RPM/gear presentation. | Simulated RPM and gear drive the exact `ownVehicleGear` and packed `ownVehicleAuxPhysicsData` properties at the native cadence. |
| Remote wheels, suspension, sound and sway | Compound-only teammates had belt scroll but not engine-owned wheels/suspension, acceleration swing or a complete remote engine-audio lifecycle. | Visible clients now use stock remote `Vehicle` entities by default. Copied LAN speed/turn/velocity/acceleration feed `DetailedEngineState`, `SwingingAnimator`, stock wheels and suspension; guarded `WGVehicleFilter.setTracksSpeed` supplies native track/wheel motion, with `PyTrackScroll` as the safe fallback. The hidden simulation worker remains compound-only. |
| Swedish TD Siege mode | `X` reached the setting RPC, but no authoritative Siege state transition existed. | `X` now requests a LAN-authoritative four-state transition (`DISABLED`, `SWITCHING_ON`, `ENABLED`, `SWITCHING_OFF`). The exact #1513 callback chain refreshes the vehicle descriptor, gun, aim and physics; stock transition times and speed caps are used; damaged engines apply the descriptor coefficient, destroyed engines refuse the change, firing is blocked during transitions, and state replicates to LAN peers. |
| Bot tactics | Bots had route/role scoring and a cover state machine, but recent damage was not a first-class signal and server orders always faced the target. | The server retains a bounded recent-attacker signal, breaks stale target leases after a hit, raises cover urgency, suppresses unsafe peeking and falls back to the last safe route. It also adds low-health retreat, crossfire withdrawal and ally-supported pushes. Only stopped, engaging, sufficiently armored turreted heavies/mediums/lights angle 12--30 degrees; TDs, SPGs, moving and recovery vehicles do not. |
| Random map | The server had an active compatible map pool, but the picker exposed only concrete maps. | `Random` is the first option. The `server_random` sentinel is resolved by the host from the active build's map pool and the chosen map is replicated to all clients. |
| Team sizes and side choice | One symmetric team size was used and humans were auto-balanced, with ties going to team 1. | The launcher and host protocol carry independent team-1/team-2 capacities. Single-player and LAN clients can choose Auto, Team 1 or Team 2 before joining and can switch in the room while capacity allows. The host owns both capacities and returns an explicit `team_full` refusal instead of silently moving the player. |
| Launcher scope | Packaged and visible launcher paths still supported 0.8.2. | Detection, session planning, server dispatch, payload staging, build checks and launcher documentation now support only exact #1513. Historical 0.8.2 source remains in the repository but is not a launch option. |
| Launcher console flashes | Periodic `tasklist` process checks could briefly create a console window and take focus from a full-screen battle. | Game-lifecycle polling now enumerates processes through the Windows Toolhelp API without starting `cmd`, while server and hidden-worker launches keep their no-window flags. |
| Launcher home | Author/distribution information and the QQ group were absent. | The home page now exposes selectable, copyable read-only text for author `伪红学家`, Bilibili `tiancaihb`, QQ group `302519768`, the GitHub URL and `本mod免费传播、开源、欢迎二创，使用无需付费，售卖与本人无关，仅供个人学习交流`. |
| Retail-client repair | A legacy shared `preferences.xml` can conflict with a current retail client and leave it stuck at loading. | The repair page offers `正式客户端卡在加载界面？点击清理配置…`. It moves only `%APPDATA%\Wargaming.net\WorldOfTanks\preferences.xml` to a timestamped backup, requires exact #1513 to be selected and the game to be closed, rejects links/non-files, and does not touch offline `%LOCALAPPDATA%` data. |
| Vehicle editor | Type 5 Heavy was hidden behind internal ID `japan:J20_Type_2605`. HE splash/module damage, gun angles, armor and mass fields were not all exposed systematically. | The browser resolves stock `.mo` names and shows `五式重战 (J20_Type_2605)` with internal-ID fallback; the exact package scan found 679 selectable vehicles without roster parse failures. The typed allowlist now includes HE `explosionRadius`, shell `damage/devices`, existing armor-thickness leaves, hull/chassis/turret/gun/component weights, gun depression/elevation curves, turret traverse limits and Swedish suspension-pitch limits. Packed scalar types, curve ordering, angle relationships and health relationships are validated atomically. Travel/Siege peers expose mode-specific angles while stock-identical invariant edits are mirrored into both descriptors. |

## Damage law verified against the installed #1513 data

The installed `scripts.pkg` gives the Tortoise 120 mm
`_120mm_AP-T_L1A1` and `_120mm_APDS_L1A1` the same 400 average vehicle
damage. The authoritative law makes one uniform roll over 0.75--1.25, so a
penetrating AP or APCR hit on a full-health target is **300--500**. The
penetration roll also uses the stock **plus/minus 25%** boundary.

Applied damage is `min(rolled damage, remaining health)`. Therefore a displayed
200 can still be correct when the target had only 200 HP left, but the reported
full-health low rolls were a real authority-input bug. A human launch froze only
the vehicle type and shell slot. The authority peer reconstructed that vehicle's
stock gun instead of the fitted gun, so an E 50 carrying the 390-alpha 105 mm gun
was resolved with the stock 135-alpha 75 mm shell. The observed 108, 124, 136 and
138 hits all fit that wrong shell's 101.25--168.75 roll interval.

Every canonical launch now freezes the mounted shot's shell kind, caliber,
vehicle and module damage, near/far penetration, maximum range, velocity,
gravity and HE radius. Client-worker and dedicated-server regressions both keep
a 390-alpha fitted shot authoritative even when the fallback vehicle descriptor
still contains the 135-alpha stock shell. This also corrects the same fallback's
effect on penetration loss over distance, normalization/ricochet/overmatch,
module damage, destructible penetration and HE splash.

## Climbing formula boundary

The candidate reads the stock descriptor's selected engine power, installed
mass, speed limits and terrain resistance. The installed #1513
`g_defaultTankXPhysicsCfg` also provides the longitudinal two-point grip curve
now used by the candidate: `(cos 27.5 degrees, 1.0)` to
`(cos 32 degrees, 0.1)`, interpolated in ground-normal Y. The former fixed
0.54 cap was therefore demonstrably not the #1513 default and has been removed.

Wargaming's public material confirms the relationships among engine power,
weight/load, terrain resistance and speed limits, but does not publish the
complete native force integrator. The claim here is exact-build recovery of the
grip constants and correction of the early cap, not that a public Wargaming
article specifies every term of the copied simulation.

## Bot AI and public projects

The current bot combines role/range scoring, focus limits, shared contacts,
base defence, flanking, shell choice, artillery deployment, geometry-probed
cover reservations, cover approach/hold/peek/return, recent-hit withdrawal,
low-health disengagement, crossfire avoidance, ally-supported pushes and
bounded armor angling. Damage reaches the planner through the authoritative
server path rather than a local visual event.

No mature drop-in World of Tanks battle AI was found publicly. The old
`the-tuxedo-cat/wot-offline-server` targets 0.9.22 but is largely abandoned;
`WOTClassicReborn/WotOffline` describes full battle gameplay and AI as future
work. They are useful architecture references, not an AI implementation that
can safely replace this exact-build planner.

## Intentional model-composition boundary

Arbitrary cross-vehicle assembly such as a light-tank chassis with a Maus
turret is intentionally not offered. Those parts are not independent scalar
records: nation/module compact descriptors, model attachment nodes, gun and
turret skeletons, collision/hit-test armor, suspension/tracks, wreck resources
and the server vehicle identity must agree. Repointing only the visible model
would create a tank whose presentation, armor and collision disagree; rewriting
all of those contracts is a new vehicle-authoring pipeline with a material
native-crash risk. The editor therefore stays at the validated scalar boundary
above. This is a closed safety decision, not unfinished feedback work.

## Only remaining external evidence: native crashes and replay rewind

The displayed `#1513` and entity-1019 messages are controlled battle refusals
addressed above. The reported all-computer pause followed by one of those
Garage notices belongs to that same fixed path. A real process termination
still cannot be diagnosed from a screenshot. Only an actual stable map CTD,
exit after one round, full-roster firing CTD, post-battle CTD or replay rewind
CTD requires the dump below.

Replay rewind itself is the stock #1513 `BattleReplay.pyc` time-warp path:
Left/Home reaches `onBeforeReplayTimeWarp`, effect cleanup and native
`beginTimeWarp`. There is no separate offline rewind implementation to patch
without a failing native stack.

Both the visible client and hidden worker are named `WorldOfTanks.exe`. Attach
by PID to the process whose command line contains `offline-player-`; the worker
contains `offline-worker-`. Resolve one exact installation path and require
exactly one visible match:

```powershell
$gameExe = (Get-Item 'C:\Games\World_of_Tanks\WorldOfTanks.exe').FullName
$visible = @(Get-CimInstance Win32_Process -Filter "Name='WorldOfTanks.exe'" |
    Where-Object {
        $_.ExecutablePath -eq $gameExe -and
        $_.CommandLine -match 'offline-player-'
    })
if ($visible.Count -ne 1) {
    throw 'Expected exactly one visible offline-player process.'
}
$dumpDir = "C:\WotDumps\visible-$($visible[0].ProcessId)"
New-Item -ItemType Directory -Force $dumpDir
& 'C:\Tools\procdump64.exe' -accepteula -ma -e -t `
    ([int]$visible[0].ProcessId) $dumpDir
```

Keep the dump together with the exact executable version, `python.log`,
launcher/server log, Windows Application Error event, map, vehicle, roster
size, round number and reproduction timestamp. No other feedback item in this
ledger is being deferred to tester operation.

## Reference material

- Wargaming, *Upgrading Your Vehicles*:
  <https://worldoftanks.eu/en/content/guide/newcomers-guide/upgrading_vehicles/>
- Wargaming, *World of Tanks Game Manual*:
  <https://worldoftanks.eu/dcont/fb/updated_manuals/world_of_tanks_game_manual_en_eu_web_8_8.pdf>
- Wargaming, *Features of Update 9.16* (stock battle damage totals):
  <https://worldoftanks.com/en/news/general-news/916-features/>
- Wargaming, *Update 9.17 release notes* (Swedish TD Siege mode):
  <https://worldoftanks.eu/en/content/docs/release_notes/release_note-9_17/>
- Wargaming, *Update 9.22 list of changes*:
  <https://worldoftanks.com/en/content/docs/release_notes/update-9-22-list-of-changes/>
- Microsoft Sysinternals, *ProcDump*:
  <https://learn.microsoft.com/en-us/sysinternals/downloads/procdump>
- Public offline-server references:
  <https://github.com/the-tuxedo-cat/wot-offline-server> and
  <https://github.com/WOTClassicReborn/WotOffline>
