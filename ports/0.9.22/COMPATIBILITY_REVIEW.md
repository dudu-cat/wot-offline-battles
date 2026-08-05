# Compatibility review: World of Tanks 0.9.22.0.1 #1513

This review is pinned to the Chinese HD client whose `version.xml` reports
`v.0.9.22.0.1 #1513`. The executable is 32-bit x86. Packaged client modules use
CPython 2.7 bytecode magic `03 f3 0d 0a`; the embedded build identifies itself
as Python 2.7.7.

The goal of version 0.3.0 is a complete playable vertical path, not another
login-only probe: local Account -> stock Lobby/map selection -> native map and
Avatar -> native Vehicle entities -> local movement/aim/fire -> synchronized
humans and bots -> damage/death/result -> cleanup -> a second round.

## Exact-build evidence reviewed

The following groups were extracted from the local `scripts.pkg` and reviewed
at their call sites and lifecycle boundaries:

- connection and Account: `connection_mgr.py`, `Account.py`, `PlayerEvents.py`,
  the Account sync helpers and lobby requesters listed in the consumer matrix
  below, server settings and lobby context;
- lobby and map selection: `gui/app_loader`, Scaleform view loaders,
  `TrainingSettingsWindow`, arena cache and the generated prebattle aliases;
- battle entry and exit: `OfflineMapCreator.py`, `Avatar.py`,
  `AvatarInputHandler.py`, battle session/controller repositories and arena
  listeners;
- entity contracts: `Avatar.def`, `Vehicle.def`, `Vehicle.py`,
  `ClientArena.py`, `constants.py`, filters, gun rotation and item descriptors;
- presentation and combat calls: vehicle ammo/reload/targeting callbacks,
  `getCurShotPosition`, `showShooting`, health callbacks, kill arena updates
  and collision methods;
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
reuse. Initialization and player-promotion sentinels are set only after their
native methods return successfully; a partial Entity returned by BigWorld is
never promoted. FakeServer and uncancellable Avatar resource callbacks require
both sentinels and current-player identity, so a zombie object cannot receive a
late mailbox callback even during the destruction tick.

The LOGGED_ON notification, Account construction and promotion are one
transaction. Any listener or constructor failure clears client-only spaces,
resets connection status, invokes every disconnect boundary independently and
deletes the retained Account repository even if an earlier event listener
raises. Shutdown restores every patched class and host entry in `finally`.

The Account surface was checked consumer-first against the local `#1513` PYC,
not inferred from another 0.9.22 build:

| Producer | Exact consumer contract covered |
| --- | --- |
| `CMD_SYNC_DATA` / `AccountSyncData` | `rev` and `prevRev`; every initial `Account._update` subscriber receives an explicit cache value instead of depending on a missing-key fallback. |
| `Stats` / `StatsRequester` / lobby controllers | Zeroed money and account scalars; mapping-shaped restrictions, referral data and clan locks; a non-empty `dailyPlayHours`; and full daily/weekly `playLimits`. Zero periods mean exhausted parental-control time in this build. |
| `Inventory` / `InventoryRequester` | All item-type indices exist; vehicle `compDescr` and crew maps exist; `repair` is a two-item tuple and `shellsLayout` is a mapping. |
| `QuestProgress` / personal-mission requesters | `quests`, `tokens`, and `potapovQuests`; both `regular` and `training` contain `slots`, `selected`, and `lastIDs`, while `compDescr` is always present. |
| goodies, vehicle rotation, recycle bin, ranked, badges, New Year | Readable empty caches exist. `groupLocks` contains both directly indexed lists, the ranked helper can directly index an empty `ranked` cache, and `ClientNewYear` plus the New Year controller accept the empty sync/goodie mappings without fabricated event data. |
| `Shop` / `ShopRequester` / `RefSystem` | Mandatory `sellPriceFactor`; all directly read item/goodie collections; currency-mapped `paidRemovalCost`; exact berth, slot, and free-XP tuple arities; and the four-key disabled referral configuration, including integer `posByXPinTeam = 0`. |
| `DossierCache` / `DossierRequester` | The stream body is the exact `(revision, dossierChanges)` pair; an empty change list completes synchronization without fabricating dossier data. |
| `ClientChat` and BW Chat2 | Both client mailboxes exist. Offline chat commands are accepted as one-way no-ops: `CHAT_COMMANDS` indices are never echoed as `CHAT_ACTIONS`, and no malformed partial action is delivered to `ClientChat.onChatAction`. |
| initial server settings / lobby controllers / `ClientRanked` | `file_server`, regional settings, the four-item roaming tuple and the directly indexed two-item `wallet` retain their native shapes; roaming item 3 is the host list consumed by `predefined_hosts`, while `ranked_config` is present and explicitly disabled because `ClientRanked` indexes it directly. `elenSettings` is also explicitly disabled because its exact missing-section default is enabled and would start an unsupported HTTP event-board chain. |

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

The required order is:

```text
OfflineMapCreator.destroy()
  -> restore_lobby_account()
  -> Account.showGUI() / native synchronization
  -> native g_appLoader.showLobby()
  -> wait for Lobby + HangarSpace + vehicle model
  -> open the next stock TrainingSettingsWindow
```

## Stock map-selection lifecycle

The release opens `TRAINING_SETTINGS_WINDOW_PY` through the exact
`ViewLoadParams(alias, alias)` contract. A scoped wrapper replaces only that
window instance's arena cache with the server's standard-mode pool and sends
its chosen geometry to `request_start`. Other training windows continue down
the original method.

Before creating the first Account, bootstrap waits until the exact app loader
has entered `GUI_GLOBAL_SPACE_ID.LOGIN` for two consecutive engine ticks.
`personality.init()` loads mods before `personality.start()` starts the native
Start/IntroVideo-to-Login state machine; creating an Account in that interval
lets `LoginState.init()` destroy it with `clearEntitiesAndSpaces()`. The same
clear invalidates an in-flight hangar CompoundAssembler, which explains the
observed `R11_MS-1` resource-dictionary KeyError despite complete vehicle
resources. No vehicle-specific exception or resource replacement is needed.

After Account promotion, the wrapper waits for the exact public
`LOBBY_VIEW_LOADED` event, Lobby GUI
space, initialized hangar space and (when present) a completed hangar vehicle
model before it starts the LAN session. Merely finding an initialized
Scaleform application is insufficient because that object already exists in
the login/EULA space. The hangar timeout starts only after the lobby event, so
first-run EULA interaction is not treated as a startup failure. Raw class
members are preserved so Python 2 unbound-method identity is restored
correctly. A chain-safe `onWindowClose` adapter releases session ownership when
the user presses Cancel, and programmatic close is idempotent even if Scaleform
has already retired the weak view. The stock window remains
responsible for mouse and cursor behavior; no transparent hotkey overlay or
F12/`0` handler is installed.

## Battle and entity lifecycle

The client delegates space, mapping, Avatar construction, camera setup and
teardown to the exact `OfflineMapCreator`. It temporarily selects the normal
battle branch while `PlayerAvatar.onBecomePlayer` runs, but preserves the one
native `AvatarFilter` established before world entry. A strict local mailbox
implements only the exact early Account/Avatar/Vehicle server calls needed by
this client.

`PlayerAvatar.leaveArena()` calls its base mailbox before the rest of its
native cleanup. The local bridge therefore schedules runtime teardown for the
next engine tick instead of destroying the Avatar reentrantly. LANSession then
retires that participant from only the active server round, restores the local
Account and keeps the waiting-room socket. The server transfers bot authority
to another participating client, or records a draw when no simulator remains;
the departed client cannot consume a duplicate start for the same round and is
re-enabled only by the next waiting roster. Explicit VOIP queries used by
vehicle markers are present and conservatively disabled. The local slice cannot
perform the cell-server attachment needed for postmortem spectator switching,
so the exact switch mailbox rejects the request without falsely updating only
the HUD.

Vehicle creation uses all properties from the local `Vehicle.def`, the exact
18-item compressed `VEHICLE_ADDED` tuple, native descriptors and native entity
creation. `setClientReady` is an ordered barrier: even if native
`createEntity` re-enters `Vehicle.onEnterWorld` before returning, the bridge
does not publish `AVATAR_READY` or `PERIOD` until `VEHICLE_ADDED` has reached
`ClientArena` and the Avatar is bound to the same entity. Two false
cross-version assumptions were removed during review:

- build `#1513` calls `Vehicle.cell.trackRelativePointWithGun(point)`; the
  bridge now exposes that exact mailbox;
- `ARENA_UPDATE` has no `VEHICLE_REMOVED` value and `ClientArena` has no
  corresponding handler. Individual removal destroys the entity, while kill
  state uses the native `VEHICLE_KILLED` update and full cleanup uses the
  arena teardown.

Local input drives the real Vehicle entity with bounded kinematic motion,
client ground/water/slope/obstacle queries and native own-vehicle/HUD updates.
Remote snapshots are smoothed, while a local echoed pose only corrects drift
larger than five metres instead of rewinding the player's tank at network
frequency.

## Aiming, shooting, health and death

The exact relative-aim call treats the point as relative coordinates. Stopping
gun tracking reconstructs world aim from the current hull yaw. A local shot
uses the public `gunRotator.getCurShotPosition()` boundary, performs client
map/vehicle collision and armor checks, and reports the proposed hit to the
server. The echoed server shot event confirms the local shot after the native
Avatar waiting timer starts; remote shots use the predicted presentation path.

Server health is applied through the entity's health callback and the native
Avatar vehicle-health path. Crossing zero publishes `VEHICLE_KILLED`; a dead
local vehicle cannot move or fire, and a dead bot stops movement, targeting and
late fire events. The elimination result freezes all inputs until the server
returns the room to waiting.

## AI, room and round boundaries

Humans take real team slots first. Bots fill the unoccupied slots so each team
has exactly 15 vehicles. Battle-time late joins are rejected to prevent a
16th slot or an incomplete local manifest. A waiting-room membership is not
published to other handlers until its own `welcome` has been sent under the
same state lock, so another player cannot start a battle whose first message to
the new client would arrive before its identity and round assignment.

The elected authority client runs tactical bots using standard-map annotations,
vehicle roles, persistent randomized personalities, bounded line-of-sight
caching and local avoidance of terrain, water, steep slopes, obstacles and
nearby vehicles. The Python server remains canonical for room phase, HP, shot
events, elimination and the three-second round reset. The next waiting roster
is a synchronization barrier: the previous battle runtime is destroyed before
either the map picker or a queued next battle can cross the native Lobby/Hangar
readiness gate. Per-round phase is monotonic, so a delayed same-round waiting
roster or start denial cannot cancel an accepted battle, and snapshots cannot
be reordered across that barrier.

The pure-data server planner emits revisioned global `bot_orders`, which the
0.9.22 authority now uses for macro targets after reporting bounded visibility
observations. BigWorld terrain, collision, water and slope probes remain local,
and the client planner is a fallback when no server order is available.
Base-capture rules are not part of 0.3.0; standard battles currently end by
elimination.

## Reference implementations reviewed

The migration compared the local build with several public offline layers,
including `Fedar459/WoTOfflineHangar0.9.22`,
`cyberjois/private-wot-server` and the Tuxedo WoT offline-server projects.
Useful patterns were adopted only after checking their corresponding local
`#1513` call sites. Broad login-view replacements, blanket exception handling,
forced process exit, global entity-clear bypasses and development hotkeys were
not carried into this runtime.

## Automated and package verification

The test suites cover configuration, protocol ordering, fake Account RPC data,
stock picker installation/restoration, exact battle mailboxes, Vehicle property
packing, local movement, aiming, shooting, health/death, snapshot barriers,
active-round leave and authority transfer, same-poll lobby/start interleaving,
bot authority, tactical maps, 15-per-team allocation, elimination and
multi-round reset.

The release build additionally:

1. inspects the exact client version, build, executable architecture and
   required resource archives;
2. reads exact code objects from `scripts.pkg` and compares all 92 stock method
   signatures, 18 direct-consumer literals, 20 lifecycle code names and 11
   `AccountCommands` constants used by the port, including variadic flags on
   the stock view loader;
3. checks 14 ordered lifecycle contracts and inventories every exact
   Account-helper `setAccount` implementation, so weak-reference, login-state,
   map rollback and leave-arena assumptions cannot silently change;
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
- the kinematic layer, bot update budget and HUD remain usable at the target
  frame rate; and
- a full result -> lobby -> picker -> second battle cycle completes without a
  new traceback.

Postmortem spectator attachment, SPG/strategic camera movement, battle-settings
capture-device enumeration and combat-equipment placement are outside the
current standard light/medium/heavy/tank-destroyer slice. Their exact mailboxes
are not generalized into silent no-ops.

No additional Python mismatch is known in the consumer matrix above. Optional
lobby features outside that matrix and all BigWorld-side behavior still remain
empirical. If a real-client check fails, preserve `python.log`; the package
intentionally avoids noisy per-frame tracing so the first actionable traceback
remains visible.
