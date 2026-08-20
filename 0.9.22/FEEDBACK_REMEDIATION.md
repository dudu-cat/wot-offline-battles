# 0.9.22 feedback remediation

This document tracks the August 2026 feedback against the exact Chinese HD
client `0.9.22.0.1 #1513`. It describes the current unreleased working tree.
Passing Python tests is not a substitute for running this 32-bit client on
Windows, so every native presentation, frame-pacing, and crash item retains an
explicit Windows acceptance step.

## Feedback coverage matrix

`Implemented` below means that the working tree has a code path and focused
tests. It does not mean that an old native client behavior has passed the
Windows acceptance checklist.

| Feedback | Working-tree status | Remaining boundary |
| --- | --- | --- |
| Mounted gun, turret, engine, shells, and derived values are wrong or empty | Implemented | Verify one non-stock fitting in the #1513 garage and battle UI. |
| Launcher vehicle-data GUI | Implemented as launcher-owned `res_mods` overlays | The safe editor intentionally rejects arbitrary XML shapes and never edits `scripts.pkg`. For a shared component, the UI lists every vehicle that the edit affects. |
| Selectable bot count | Implemented as total tanks per team, 1--15 | Verify 15, 7, and 4 tanks per team on Windows. |
| CTD followed by a permanent login stall | One corrupt-mod-configuration path has repair and reset operations | Native CTDs remain unproved without a repeatable log/event signature or a Windows soak. |
| No gravity, wrong weight, and a light tank pushing a heavy tank | Copied airborne/fall behavior and descriptor-mass contact are implemented | Native collision feel and fall transitions need Windows acceptance. |
| Yellow track nearly stops the tank; damaged ammo rack does not slow the current reload | Implemented | Verify damage and repair while moving/reloading in the native HUD. |
| Enemy outline through a hill or obstacle | Implemented with a static-world mouse ray; foliage stays transparent | Verify rocks, ridges, buildings, and foliage on Windows. |
| Wrong or duplicate capture flags | The exact standard-CTF visibility bit is selected and reapplied after client-ready | The new screenshot does not identify the build, map, or mode. Wrong coordinates or a second flag are not yet reproduced and are not claimed fixed. |
| Teammates push the player sideways | Friendly bots retain their own impulse and cannot transfer it to the local player | Remote-human contact and native feel still need a two-client check. |
| Bot team-killing | Launch-time ordinary and SPG friendly-lane admission is implemented | A teammate can still move into a shell that is already airborne. |
| Enemies render beyond the allowed distance | 445 m spotting and 565 m presentation/AOI ceilings are implemented | A projectile intentionally remains able to hit a non-rendered vehicle. |
| 32-bit memory pressure | Large identity/result payloads, histories, queues, and probe caches are bounded | The exact executable is large-address-aware, but its finite 32-bit address space remains; repeated-round Windows memory sampling is required. |
| Camouflage, paint, emblems, and inscriptions for every human | Implemented per LAN identity; bots keep default appearance | Verify two differently customized clients in both directions. |
| No result screen, resources, XP, early-exit result, or full team table | Implemented through durable receipts and the #1513 full-team result packers | The offline economy is reconstructed, not proprietary retail coefficients; notification/window behavior needs Windows acceptance. |
| Movement stops as soon as drowning countdown begins | Implemented; movement stops only after actual drowning | Verify native countdown and movement together. |
| Ramming damage repeats or feels excessive | One damage edge per continuous contact episode is implemented | The disclosed 0.8.2 reconstruction is not the private retail armor/skill formula; no arbitrary damage multiplier was added. |
| Battle UI settings reset every round | Battle and lobby integer-setting commands now share one persistent account state | Verify damage log, damage indicator, and marker toggles across battle, garage, restart, and another battle. |
| `preferences.xml` graphics/input settings reset or affect the normal client | Implemented with a complete Packed XML `engine_config.xml` overlay that redirects only preferences to a persistent launcher-owned `LOCAL_APP_DATA` profile | Verify #1513 loads the `res_mods` overlay, keeps settings across battles/restarts, and never changes the normal client's `%APPDATA%` profile. |
| Aim circle closes too slowly | The duplicate pre-authority shot bloom is removed, and the native rotator receives the installed descriptor's final #1513 crew/equipment aim time | Verify the HUD circle on several tanks; do not replace it with a global speed multiplier. |
| Climbing is too slow | A signed-slope bug is fixed, and the selected engine's native `smplEnginePower` override is included with installed mass/terrain data | Uphill feel still needs a repeatable tank/slope Windows comparison; no global speed multiplier was added. |
| Low FPS, especially 60 FPS in countdown then below 10 in battle | Probe work, duplicate spotting observers, payload publication, and simulation cadence have been reduced and instrumented | The 60 FPS target is not established. The reported live transition needs the exact build plus same-map 29/13/7/3-bot measurements. |
| No teammate view after death | The #1513 postmortem switch mailbox, friendly live-target validation, camera/matrix reattachment, cycling, and fallback are implemented | Stock mouse/key input and real bot/human camera attachment still need Windows acceptance. |
| Native remote tank entities and track rendering | A default-off experimental `Vehicle` path exists beside the compound-model path | It is not production-enabled until repeated create/move/damage/death/cleanup rounds pass on Windows; it is not treated as an FPS fix. |

## Implemented in the working tree

### Garage fittings and ammunition

- A vehicle now enters battle with the garage's complete mounted descriptor,
  instead of silently falling back to its stock descriptor when the fitting
  lookup misses.
- Buying and equipping a gun carries the gun compact descriptor through the
  exact command payload;
- a gun change first constructs and validates the new descriptor and a
  compatible, non-empty ammunition layout, then commits both together;
- the garage continues to publish the mounted turret, chassis, engine, radio,
  fuel tank, equipment, consumables, crew, shells, and their derived values.

### Safe vehicle-data editor

The launcher has a reversible 0.9.22 vehicle editor. It reads the installed
`scripts.pkg`, lists the vehicle members and fields that really exist in that
client, and writes launcher-owned full-member overrides under
`res_mods/0.9.22.0.1`. It never edits `scripts.pkg`.

The editor deliberately supports only existing numeric scalar fields and the
verified two-value `piercingPower` string shape. It preserves packed-XML types,
checks cross-field health relationships, refuses an overlay owned by another
tool, applies changes transactionally, and restores only files it owns. This is
less permissive than a raw XML editor, but avoids producing a client that can no
longer start because a packed value overflowed or changed type.

### Team size

The launcher exposes **Tanks per team (including players)** from 1 through 15.
The host sends that value to the 0.9.22 server; human slots are reserved first
and bots fill only the remaining slots. Calling this “bots per team” would be
ambiguous in a multiplayer room.

### Startup repair after a crash

Malformed or type-invalid `config.json` no longer prevents the bootstrap from
creating the offline Account. The invalid file is quarantined and a clean
configuration is created.

The launcher adds two distinct operations, both refused while the game runs:

- **Repair startup** validates the configuration and reinstalls the mod while
  keeping the endpoint, account, garage, vehicle overlays, battle results, and
  isolated client preferences;
- **Reset all offline data** removes the endpoint, account, garage,
  post-battle, configuration, and isolated client-preference files after an
  explicit confirmation. It does not remove vehicle-data overlays.

Both operations roll back if any step fails and leave unrelated mods alone.
This fixes one concrete “crash once, then remain at login forever” path. It does
not prove that every native CTD has been removed.

### Module damage, water, visibility, and contact symptoms

- an ammunition-rack damage or repair event rescales an in-progress reload by
  the completed fraction, instead of affecting only the following reload;
- a yellow track keeps normal mobility; a destroyed track still stops the
  vehicle until repaired;
- the drowning countdown no longer disables movement; actual drowning still
  does;
- the aimed outline requires an unobstructed mouse ray through the static
  world. Foliage remains transparent to this test;
- the installed client constants are used for the 445 m spotting ceiling and
  565 m vehicle AOI. A remembered spotted target outside AOI is not rendered,
  while a projectile can still travel to and damage it;
- copied tank contact uses descriptor mass. A live friendly bot owns its
  velocity response and does not transfer lateral push to the human player's
  tank; wreck contacts retain one team-independent response;
- the inherited per-frame lateral push decay is now time based.

These changes address the reported light-tank push, yellow-track immobility,
ammo-rack reload, drowning, through-cover outline, and unlimited presentation
distance bugs. Native collision feel still needs Windows acceptance.

The copied vehicle motion also retains vertical velocity, ballistic falling,
ground reacquisition, and fall damage instead of pinning tank height to the
latest ground sample. Descriptor mass is used for reciprocal overlap
separation and ramming input; it is not a single synthetic weight shared by
all tanks.

### Persistent battle settings and aim time

The lobby and battle entities now dispatch the same #1513 integer user-setting
commands into one durable account state. This closes the lifecycle hole where
damage log, damage indicator, and marker changes made after the lobby Account
was retired received a success response but were never stored.

The gun rotator receives the mounted descriptor's final aiming time after the
exact client factor dictionary has supplied crew and equipment effects. It is
tested with installed tier I, V, and X gun values. The accepted local fire path
previously applied native shot bloom immediately, then the authoritative shot
event applied it again. The second application did not enlarge the maximum
circle again, but it restarted convergence after the LAN round trip. Shot bloom
now has one owner: the stock authoritative `Vehicle.showShooting` path. No
blanket faster-aiming constant was introduced.

The copied climbing law now distinguishes uphill from downhill samples and
uses the installed engine's selected `xphysics` `smplEnginePower` value when it
exists. The representative #1513 descriptors inspected here use a native power
override 15 percent above the generic horsepower conversion. The old model
missed that override; a flat-road speed cap could conceal the error while an
uphill pull exposed it. Missing native data still falls back to the generic
conversion rather than inventing a vehicle-specific value.

### Crew knockout and ramming contact lifecycle

Critical proposals carry the installed descriptor's complete `crewRoles`
roster. Knocking out every actual crew member kills the vehicle even when hull
HP remains, and the server/client death, killer, frag, and crew-active state
follow the shot or ramming cause.

Ramming now damages once when two live hulls enter a continuous contact
episode. A timer alone cannot produce another hit while the hulls remain
overlapped; they must fully separate and contact again.

### Bot friendly-fire admission

Ordinary bot fire performs a final live friendly-hull check from the real gun
hardpoint to the target before launch. SPG fire reuses the already-proved
artillery receipt: every chord of that exact path is checked against live
friendly hulls, and the installed shell's HE radius is checked at the impact
point. A missing or malformed SPG path fails closed. The checks do not invent a
second ballistic model.

This is launch-time admission. A teammate who moves into an already airborne
shell's path or blast area can still be hit; the current authority has no
reliable future-motion reservation to prevent that without inventing one.

### Customization for every human player

Garage commands 116 through 119 use the exact client parsers and compact
descriptors. Seasonal outfits persist with the vehicle and survive a restart.
The local stock Vehicle receives its selected seasonal outfit in battle.

Each LAN client publishes its own bounded, validated seasonal descriptors. A
remote human's compound model applies that player's camouflage/paint fashions
and attaches that player's emblems and inscriptions through the stock
`Outfit`, camouflage, and `VehicleStickers` paths. The host's appearance is not
copied to everybody. Bots have no garage owner and keep the default appearance.
Outfits are carried with roster identity, not repeated in every 30 Hz state
snapshot.

### Battle results and early exit

The server freezes one result receipt for every round participant, including a
player who leaves after dying. The client persists unacknowledged and recent
receipts, requests command 1500, acknowledges with command 1501, and applies
credits, free XP, and per-vehicle dossier progress idempotently. A result can be
replayed after a client restart. Connected clients receive a battle-results
service-channel entry intended to open the stock result window.

Each receipt also freezes the public result rows for the whole round roster,
including both human teams and bots, their vehicle identity, combat counters,
death reason, and killer mapping. Every recipient receives the same public
table while personal rewards remain account-specific. Server receipts are
persisted atomically until acknowledgement and are globally bounded.

The compact result has been exercised against the exact #1513 Python 2 packers,
`BattleResultsCache.convertToFullForm`, and its native ACK contract. Reward
values are an explicitly labelled offline reconstruction because the retail
server's private coefficients are not present in the client. The reconstruction
keeps documented category relationships, does not grant credits merely for a
frag, applies the documented win XP relationship, and derives free XP from
combat XP. It is not presented as the proprietary retail economy.

## Partially addressed

### Frame rate

Bot simulation has a real-time ceiling with no catch-up spiral and one wire
projection per publication. Direct player spotting no longer repeats the same
native ray work once per copied friendly vehicle, positive team spotting is
shared, settled motion receipts avoid periodic world probes while their
spatial proof remains valid, and firing-lane work is skipped for targets the
team has not spotted. Countdown frames prewarm one stationary vehicle at a
time, but live authority ticks check the presentation pose of the full roster;
every render frame continues to interpolate every vehicle matrix. Proactive
exact-receipt refresh is bounded, but a vehicle keeps moving while its previous
receipt still contains the realised corridor, so queue rotation does not
produce a visible stop. Large customization and result blobs are no longer
repeated in every state snapshot, and result delivery is bounded per
connection. These changes reduce native probes, Python allocation,
serialization, and render-thread work without rotating live vehicle poses.

They do **not** establish the requested 60 FPS target. At the reported 20--27
FPS, a 30 Hz ceiling alone cannot remove a simulation tick. The remaining
dominant path includes per-bot native ground and collision probes. Further work
must be justified by exact call-count evidence and then profiled in the Windows
client with 29, 13, 7, and 3 bots on the same map.

A dedicated graphical authority worker is the most promising way to remove
that path from every human client. A local 29-bot test seam confirms that a
non-authority `BotRuntime` performs no visibility, firing-lane, ground, or
motion probes. Human replicas would still render and interpolate all remote
vehicles and run their own vehicle physics, so the worker is not itself proof
of 60 FPS.

### Memory and crashes

Round state, receipt history, queued publications, and large identity blobs are
bounded, and the concrete corrupt-configuration login failure is repaired. The
exact executable is 32-bit and large-address-aware (up to about 4 GB on 64-bit
Windows), but its address space is still finite. A repeated-round Windows soak
is required; a native CTD without a dump or a reproducible log
signature remains unproven rather than “fixed by audit.”

### Client preferences isolation

Account-specific interface values are pinned to the `offline_account` section
so lobby and battle integer settings share one persistent offline account
state. Root graphics, window, frame-rate, zoom, and input values use the
engine's separate preferences-file contract.

Before launching exact #1513, the launcher now reads and validates the stock
Packed XML `res/engine_config.xml`, clones the complete document to
`res_mods/0.9.22.0.1/engine_config.xml`, and changes only its `preferences`
setting to:

- `path = WoTOfflineBattles/client_profiles/0.9.22/preferences.xml`;
- `pathBase = LOCAL_APP_DATA`.

The stock file is never modified. Missing or malformed stock data fails closed,
and an existing `engine_config.xml` overlay not proven to be this launcher's is
reported as a conflict and left byte-for-byte unchanged. An older
launcher-owned redirect can be updated only when every non-preferences value
still matches stock. Writes use a same-directory atomic replacement.

This is a persistent but deliberately new profile: settings from the normal
client are not copied into it. Repair retains it. The separately confirmed
Reset removes its `preferences.xml` together with the known offline state, but
does not remove vehicle-data overlays or another World of Tanks profile. The
clone, ownership-conflict, failure, repair/reset, and launcher-wiring paths have
automated tests; actual #1513 resource precedence and persistence still require
the Windows acceptance pass below.

### Post-battle economy

The stock transport, result window contract, persistence, and dossier/resource
accumulation are implemented. Exact retail credit/XP coefficients are
server-side proprietary data and cannot be recovered from this client. The
current offline reconstruction therefore needs product acceptance as an
offline rule, not an exact-retail claim.

## Not changed without stronger evidence

### Remote tanks as native Vehicle entities

The local player is a stock `Vehicle`. The working tree now contains an
isolated, default-off remote `Vehicle` experiment using #1513's entity,
appearance, outfit, track, death, GUI, and cleanup boundaries. It keeps the
existing pose overlay because no supported remote transform/filter setter was
established. The normal path remains the client-only compound model driven by
LAN state.

The experiment is deliberately not production-enabled and is not counted as a
frame-rate fix: hidden engine entities and duplicate native physics may instead
increase cost. It must create, move, damage, destroy, and clean up remote humans
and bots over repeated Windows rounds before replacing the compound path.

### Dedicated client-authority worker research

The exact #1513 executable cannot be treated as a true headless server. Its
normal client path requires a window and a Direct3D device, and its null-device
path explicitly rejects non-asset-processor clients. The `disableGUI` value in
the installed `engine_config.xml` belongs to the high-resolution `superShot`
feature; it is not a renderer switch. The installed package also contains no
CellApp, BaseApp, server scripts, or BigWorld server executable.

A separate hidden graphical client can instead run the existing copied
bot/world authority while every human client remains a replica for bots and
global projectiles. Human clients still run immediate local driving and report
their own native pose; moving that pose into the worker would add a round trip
and is not part of this design. The LAN protocol already has an authority id
and epoch, canonical bot snapshots, a projectile ledger, and takeover rebase.
Production support still needs a distinct worker connection and loading
barrier: a worker must not occupy a team slot, become room host, spawn a public
tank, or appear in battle results.

After creating a real D3D device, the worker may disable world drawing and
avoid player GUI/presentation while retaining the map, observer Avatar, native
world queries, `BotRuntime`, projectiles, and destructibles. This gives the
operating system a separate process/core for authority work without adding an
IPC round trip to local steering. It still duplicates a map, a 32-bit address
space, D3D resources, and some GPU residency. Hidden/minimized callbacks and an
observer-only Avatar must be proven on Windows. On worker loss, the safe
default is to pause and await a replacement or end the round, not silently move
the load back to a human client.

Using a hidden client as an authoritative native Vehicle worker is a separate,
later gate; the dedicated copied-authority design does not require it. The
exact scripts create a `WGVehiclePhysics` instance for every stock Vehicle,
and its filter exposes input and pose providers, so multiple locally controlled
vehicles are not ruled out statically. They are not proved either. Two real
client-created Vehicles must first move independently without pose overlays or
retail transform messages, collide with walls, slopes, water, and each other,
and then scale to 30 vehicles inside a 33.3 ms worker tick. An all-tank worker
would add a process round trip to player input, camera, and contact feedback;
it is not recommended without a measured prediction/reconciliation design.

### CTF flag presentation

In this client, standard battle is internally named `ctf`; selecting it and
showing its bases is intentional. Lakeville also contains assault objects with
different visibility masks, and #1513 may overwrite the selected mask late in
space startup. The current late CTF-mask reapply is evidence based. A report of
an extra flag, a flag at the wrong coordinates, or the wrong mode needs the map
name and a screenshot before changing the native visibility mask. Hiding all
flagpoles would break standard battles.

The new screenshot proves that two base presentations can appear close
together, but it does not identify the client build, map, gameplay mode, or
spawn. It therefore does not prove whether the wrong `ArenaType` was selected,
the installed map data contains nearby non-CTF objects, or an older build was
tested. The issue remains open pending those facts.

### Ramming damage

The current damage law is a disclosed 0.8.2 reconstruction using relative
normal speed and descriptor mass. It is continuous-contact gated and does not
repeatedly damage on a timer while the same hulls remain overlapped, but it
lacks the retail server's private armor, angle, equipment, and skill
calculation. No arbitrary cap or multiplier was
changed merely to make the report disappear. A reproducible pair of vehicles,
speeds, angle, and observed damage is required to replace this law responsibly.

### Existing release-note limits

The following limits remain real unless separately accepted on Windows:

- remote bot track/road-wheel animation;
- the 32-bit address-space ceiling;
- individual crew injury penalties beyond the implemented roles and
  full-roster knockout lifecycle;
- the VK 168.02 whose model resources are absent from this installed client.

## Windows acceptance checklist

No automated step in this work operates the VM. The shortest useful manual
pass is:

1. On one tank, change the gun and another module, select ammunition, restart,
   and verify garage values, ammo count, reload, penetration, and damage in
   battle.
2. Run the same map with per-team sizes 15, 7, and 4. Record the on-screen FPS
   and one `PERF` log interval after the countdown for each run.
3. Give two human clients visibly different summer camouflage, emblems, and
   inscriptions. Verify both directions in battle, then restart both clients
   and repeat. Verify bots remain default.
4. Damage and repair the ammo rack during a reload; yellow and destroy a track;
   enter water and drive during the countdown; test an enemy behind a rock and
   behind foliage; test a remembered enemy beyond presentation distance.
5. Put a teammate in an ordinary bot's gun line, then in an SPG path/impact
   area. Verify the bot waits and fires after the teammate clears.
6. Die, leave early, finish the round on the other client, then verify the
   teammate-view cycle, notification, clickable stock result window, complete
   team table, credits/free XP/dossier change, and replay after restarting the
   first client.
7. Stop the game, corrupt `config.json`, run **Repair startup**, and verify the
   invalid file is quarantined and the garage/result history remains. Exercise
   **Reset all offline data** only when that destructive result is wanted;
   verify it clears the isolated preferences but retains vehicle-data overlays.
8. For a wrong flag, capture the map name, spawn, mode, and screenshot. For a
   CTD, retain `python.log` and the Windows crash event; a minidump is useful
   when available but is not required to report the reproduction steps.
9. Change zoom steps, FPS limit, resolution/window mode, sensitivity, damage
   log, damage indicator, and enemy markers. Complete a battle, return to the
   garage, restart, and repeat. The first offline run is expected to use a new
   profile. Record the normal client's `%APPDATA%` preferences timestamp before
   and after this pass and verify that file was not touched.
