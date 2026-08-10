# Compatibility review: World of Tanks 0.9.22.0.1 #1513

This review is pinned to the Chinese HD client whose `version.xml` reports
`v.0.9.22.0.1 #1513`. The executable is 32-bit x86. Packaged client modules use
CPython 2.7 bytecode magic `03 f3 0d 0a`; the embedded build identifies itself
as Python 2.7.7.

The goal of version 0.3.49 is a complete playable vertical path, not another
login-only probe: local Account -> stock Lobby/join/map selection -> native map and
Avatar -> native local Vehicle plus remote presentations -> local movement/aim/fire -> synchronized
humans and bots -> damage/death/result -> cleanup -> a second round.

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
vehicle markers are present and conservatively disabled. The local slice cannot
perform the cell-server attachment needed for postmortem spectator switching,
so the exact switch mailbox rejects the request without falsely updating only
the HUD.

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
`WGVehicleFilter.notifyInputKeysDown` and the explicit Avatar mailbox receives
the same flags. The client-created Vehicle has no retail game-server transform
stream, so its installed `WGVehiclePhysics` cannot be the authoritative pose
source. The copied 0.8.2 longitudinal, traverse, terrain and collision
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

The player-visible spotting path now copies the 0.8.2 50-metre proximity,
two-height static LOS, allied observer relay and five-second memory law. Enemy
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

This source wiring is not the same as final bot-behavior acceptance. The
finalized 0.8.2 spawn-congestion/OBB, reverse-steering and baked-route changes
are migrated as source-derived changes; real-client acceptance still has to
check them against #1513 terrain and presentation timing.

## Known deterministic parity gaps

The source audit deliberately keeps the following differences visible:

- the offline garage publishes stock configurations with empty optional-device
  slots. The player gun uses the copied 100% crew plus commander baseline and
  critical penalties, but passive optional-device, food and crew-perk modifiers
  are not wired;
- the server publishes terminal winner/reason/base team plus live frags and the
  human team-killer flag, but not the complete 0.8.2
  `personal`/`players`/`vehicles` battle-result record;
- bot drowning, bot destructible contact and artillery trajectory behavior
  remain open. The server also trusts authority-client bot gun timing and rays
  rather than independently validating ammunition and reload legality.

The local player path does include server-relayed critical state, fire,
drowning, exact fall/landing attribution, small repair/medkit/extinguisher
activation, native frag/team-killer updates, durable killer/reason metadata,
and server-deduplicated destructible results for collision and shots.
`BATTLE_SOURCE_AUDIT.md` is the authoritative per-file accounting.

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
