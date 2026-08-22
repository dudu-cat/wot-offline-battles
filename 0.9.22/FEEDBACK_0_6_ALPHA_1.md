# 0.6.0 Alpha 1 feedback triage

This ledger tracks tester feedback for the exact Chinese HD client
`0.9.22.0.1 #1513` and launcher prerelease `0.6.0-alpha.1`. Source and
simulation tests are not native acceptance: every presentation, audio,
physics and crash claim still needs the reported path on that Windows client.

## Implemented in the feedback candidate

| Area | Finding | Candidate change | Native acceptance still needed |
| --- | --- | --- | --- |
| Battle startup (`#1513`) | Empty rows in WGDE table 2 do not consume a streamed native item index. The old baker counted them, producing captures such as `live=(33152, 105) baked=(33152, 106)`. | Destructible catalog schema 5 compacts empty rows and all 41 map catalogs were rebuilt. A filename disagreement from the partial `wg_getChunkDestrFilenames` prefix is now diagnostic; unique matrix signature, exact wire and effect category remain fail-closed. | Start and replay the affected maps, especially Lakeville, Murovanka, Cliff, Westfield, Redshire and Fishing Bay. |
| Distant shell-destructible props | Karelia's shooting-range watch towers, boxes, camouflage boxes and canisters are stock `fragile` destructibles. The complete catalog was loaded, but shell fallback considered only chunks already scanned near a vehicle. | Catalog load now prepares a bounded whole-map OBB index. A shell that reaches an unregistered candidate lazily validates its streamed native count, exact matrix signature and wire, descriptor kind and native effect category before registering and destroying it. An unstreamed or contradictory candidate stops the shell without changing native state. | Shoot the reported Karelia range from both spawn directions, then repeat after driving close to and away from it. |
| Battle startup (entity `1019`) | IDs from 1000 upward are this mod's synthetic remote vehicles. The public AOI facade can intentionally hide one while the private presentation still exists. | Visibility-gated marker startup now resolves that private presentation rather than treating the hidden public entity as missing. | Repeat multi-round and death/spot transitions without `Vehicle entity ... is unavailable`. |
| Projectile presentation | Non-authority replicas lacked canonical projectile metadata, and the terminal event could not distinguish a zero-damage vehicle impact from a world impact. | Replicas retain metadata; the wire now carries `hit_vehicle` independently of direct damage. A world terminal can play its ground explosion and old clients retain a compatibility fallback. | Verify ground, rock and vehicle impacts from both LAN clients, including a full roster. |
| Wreck collision and outline | Dead records were skipped by projectile collision and were not part of cursor occlusion. | Retained wreck hit testers now stop a shell without damaging the wreck, and a nearer wreck blocks an enemy outline. Static world geometry already has a separate exact ray test. | Verify the reported wreck and rock locations; log world/wreck hit depths if a specific asset still fails. |
| Base capture contest | The server paused an enemy capture whenever a defender entered its own standard CTF base. Stock capture continues until each invader leaves, dies or takes qualifying damage. | Owner presence no longer pauses or clears capture progress. Leaving, death and damage still drop only the affected invader's contribution. | Enter the same base with one defender and one or more invaders, then damage and move each invader independently. |
| Battle HUD totals | The stock #1513 damage panel has total caused, assisted and armor-blocked values. Existing `DAMAGE` and assist events already drove the first two, but blocked damage existed only in the final receipt. | Each canonical non-penetrating enemy hit now carries the same bounded blocked amount used by settlement and publishes the exact stock `TANKING` event for the local defender. Ordered event IDs keep retries from double-counting it. Display still follows the user's Feedback settings. | Enable all three totals in the Feedback tab and compare them with the per-hit feed and final result. |
| Doubled post-battle damage and kills | The packer copied vehicle damage and kills into both the VEH and AVATAR result blocks. The stock summary adds those blocks, so one 880-damage kill appeared as 1,760 damage and two kills. | Vehicle combat statistics now remain only in the VEH block; the avatar-only fields are zero for these battles in both full and public results. | Finish a one-target battle and compare the team row with the durable server receipt. |
| Crew defaults | Fresh crews exposed three choices and commanders were preassigned Sixth Sense. | Every new crew member starts with eight untrained choices and no selected skill. Existing saved garages are preserved. | Create a clean offline profile and inspect every role. |
| Crew reset and manual training | The reset handler read `shopRev` as the tankman ID. The exact free-XP training command was absent. | Reset now reads `(shopRev, tankmanInvID, costIdx)`. Manual training accepts the exact `(shopRev, tankmanInvID, freeXP)` request and uses the pinned 1:10 conversion. | Exercise both buttons with a persisted crew. This does not implement the Elite-vehicle accelerated-training toggle. |
| Passive consumables | Food bonuses already reached the exact attribute-factor path, but food was omitted from the battle panel because it has no activation action. | National food is published as a passive READY item and remains non-clickable. | Verify its icon and a crew-dependent characteristic before and during battle. |
| Removed RPM Limiter | It was incorrectly supplied as an always-on 10% engine-power factor, despite having no toggle or engine-damage lifecycle. | It is no longer granted as a silent passive bonus. Fuel, oil and food remain active. | A real on/off limiter with engine damage remains a separate feature. |
| Local engine audio inputs | The copied local physics updated tracks and smoke, but left the stock `DetailedEngineState` links at zero. | The existing simulated RPM and gear now update the exact `ownVehicleGear` and packed `ownVehicleAuxPhysicsData` properties at the native cadence. | Listen on Windows #1513 through start, idle, forward/reverse acceleration, gear changes, stopping, death and the next round. Remote compound vehicles are a separate gap. |
| Random map | The server already had a compatible active map pool, but the room UI exposed only concrete maps. | `Random` is the first room option; `server_random` is accepted only as that sentinel and resolved from the active build's map pool when the host starts. Unknown maps remain rejected. | Start consecutive single-player and LAN rounds and confirm the selected map is shown to every client. |
| Launcher scope | Packaged and visible launcher paths still supported 0.8.2. | Detection, session planning, server dispatch, payload staging, build checks and launcher documentation now support only exact #1513. Historical 0.8.2 source remains in the repository. | Build the Windows launcher and confirm no 0.8.2 payload or selectable path remains. |
| Launcher home | Author/distribution information and the QQ group were absent. | The home page now shows selectable, copyable read-only text for author `伪红学家`, Bilibili `tiancaihb`, QQ group `302519768`, the GitHub URL and the requested free/open/non-sale notice. | Confirm selection and `Ctrl+C` in the packaged Windows UI. |
| Retail-client repair | A legacy shared `preferences.xml` can conflict with a current retail client. | The repair page can move only `%APPDATA%\Wargaming.net\WorldOfTanks\preferences.xml` to a timestamped backup. It requires exact #1513 to be selected and the game to be closed; missing files are harmless and links/non-files are rejected. Offline `%LOCALAPPDATA%` data is untouched. | Use a disposable profile to verify backup and retail-client regeneration. |
| Vehicle editor | Type 5 Heavy exists as `japan:J20_Type_2605`; internal IDs made the browser hard to understand. HE splash and module damage are real shell fields. | The browser loads stock `.mo` names and displays `五式重战 (J20_Type_2605)`, with internal-ID fallback. The audit found 679 selectable vehicles and no roster parse failures. Existing positive `explosionRadius` and `damage/devices` scalars are editable through the typed allowlist. | Open the packaged editor, edit a single-player profile and verify descriptor loading in battle. |

## Verified behavior and reports that need a narrower reproduction

### Climbing in 0.6

The candidate reads stock descriptor mass, selected engine power, speed limits,
terrain resistance and the exact native `smplEnginePower` factor. No sign,
unit, mass or selected-engine error was found. Wargaming's public material
confirms that engine power controls acceleration and that speed limit is a
separate cap, but it does not publish the complete force equation used by
`#1513`.

The mod's gravity, traction ceiling and rolling-resistance curve are therefore
a reconstruction, not an official formula. A local slope sweep also makes the
report plausible: representative vehicles can stall on medium/soft terrain
near a 24-degree slope while still climbing the firm-surface case. Do not tune
one global multiplier from feel alone. Capture the same vehicle, loadout,
route, terrain and input in stock/offline #1513 and compare speed, engine power,
slope and surface samples first.

### Tortoise 120 mm AP/APCR damage

The installed `scripts.pkg` gives both `_120mm_AP-T_L1A1` and
`_120mm_APDS_L1A1` 400 average vehicle damage. The authoritative law rolls
0.75--1.25, so a penetrating roll is 300--500; penetration uses the same
plus/minus 25% boundary. The server then applies
`min(rolled_damage, remaining_health)`. A displayed 200 is correct when the
target had 200 HP left. A full-health penetration below 300 is not correct and
needs that battle's projectile terminal log and result receipt.

### Reload `-0.01 s`

The current state machine clamps remaining time to zero. Changing shell before
reload completes queues the next shell and does not replace the active reload.
Static tests did not reproduce a negative value. Capture the vehicle, gun,
clip state, both shell indices and a video of the HUD transition; the remaining
candidate is a native Flash interpolation/event-order issue.

### Replay rewind crash

The exact `BattleReplay.pyc` maps Left/Home to the stock replay time-warp path,
including `onBeforeReplayTimeWarp`, effect cleanup and native
`beginTimeWarp`. This is not a custom offline rewind feature. Compare the same
replay and timestamp in unmodified and offline #1513, then collect a dump from
the visible client.

## Confirmed gaps not implemented in this candidate

| Area | Current boundary | Required next slice |
| --- | --- | --- |
| Elite accelerated crew training | `XP_TO_TMAN` is stored and enabled by default, but post-battle settlement never reads it or awards tankman XP. | Allocate ordinary and lowest-XP bonus crew experience from an idempotent battle receipt without duplicating XP after restart/retry. |
| Swedish TD Siege mode | Vehicles start `DISABLED`, `X` reaches the exact setting RPC, but the runtime has no `SIEGE_MODE_ENABLED` branch. | Implement authoritative `SWITCHING_ON/OFF -> ENABLED/DISABLED`, the exact `Vehicle.onSiegeStateUpdated` chain, movement/aiming limits and LAN replication. A HUD-only toggle is unsafe. |
| SPG targets beyond the yellow/draw circle | Team spotting, minimap marking and 3D-model visibility are currently coupled to one 565 m presentation gate. | Separate team-known/minimap state from model AOI and validate the strategic-camera rule against stock #1513 before widening any model range. |
| Remote engine audio | The local vehicle now feeds RPM/gear into the stock auxiliary-physics links. Compound remote vehicles still have no complete stock `DetailedEngineState`/audition lifecycle. | Design a remote audition or real-Vehicle path and verify full-roster performance, start, idle, acceleration, reverse, death and round cleanup. |
| Remote wheels and suspension | Compound remotes animate turret/recoil and receive terrain pitch/roll, but have no stock wheel, suspension or acceleration-swing animator. | This requires an engine-owned remote Vehicle/filter path. A cosmetic body pitch would not fix wheels and could desynchronise the visible model from its hit tester. |
| Detailed battle results | Aggregate shots, hits, penetrations, dealt/received/blocked/assisted damage are frozen in the receipt and packed into full/public rows. Per-target interaction details are absent. | Add bounded attacker-target interaction rows through the durable receipt and exact result packer, then verify the stock Details view. |
| Removed RPM Limiter | The false permanent bonus is removed; activation and continuous engine damage remain absent. | Add an explicit active state and replicated engine damage before showing it as usable. |
| Separate team sizes and team choice | One symmetric team size is used; humans are auto-balanced and a tie goes to team 1. | Extend the protocol to authoritative side capacities and a pre-join preferred team with an explicit `team_full` refusal. The host must own both counts. |

## Bot AI review and public projects

The active bot already has role scoring, focus limits, shared contacts, base
defence, flanking, range control, shell choice, artillery deployment,
geometry-probed cover reservations and a cover approach/hold/peek/return state
machine. It does not react to a recent attacker as a first-class signal and
server orders still face the target directly, so it neither reliably seeks
cover *because it was hit* nor applies the legacy 12--30 degree armor-angle
helper.

No mature drop-in World of Tanks battle AI was found publicly. The old
`the-tuxedo-cat/wot-offline-server` targets 0.9.22 but is largely abandoned;
`WOTClassicReborn/WotOffline` explicitly says full battle gameplay and AI are
future work. The next bounded AI slice should be:

1. replicate a bounded recent-attacker/recent-damage signal;
2. increase cover urgency and shorten exposed peek time after damage;
3. angle only turreted, sufficiently armored vehicles while preserving gun
   traverse and movement safety;
4. add low-health disengagement, crossfire avoidance and ally-supported push
   scoring before adding more map routes.

## Crash and soak plan

The captured `#1513` messages are graceful battle-start refusals, not proof of
a native crash. The following still require a real dump: stable CTDs on some
maps, one-minute freezes followed by Garage/map picker, process exit after one
round, full-roster firing CTDs, post-battle CTDs and replay rewind CTDs.

Both the visible client and hidden worker use `WorldOfTanks.exe`. Attach after
the launcher handoff and select the process whose command line contains
`offline-player-`; the worker contains `offline-worker-`. Resolve one exact
installation path and require exactly one match before attaching:

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
size, round number and reproduction timestamp.

## Reference material

- Wargaming, *Upgrading Your Vehicles*:
  <https://worldoftanks.eu/en/content/guide/newcomers-guide/upgrading_vehicles/>
- Wargaming, *World of Tanks Game Manual*:
  <https://worldoftanks.eu/dcont/fb/pdf/world_of_tanks_game_manual_en.pdf>
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
