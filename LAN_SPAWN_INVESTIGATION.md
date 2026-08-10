# LAN replica spawn stall — investigation report (2026-08-09)

Scope: two-PC LAN battle. The authority client runs at 10-20 fps during
loading. The replica client adds about one tank per second with a nearly
frozen game thread. The question: the spawn code path is identical on both
sides, so why is the replica so much worse, and why did early LAN builds
feel fine?

Code examined: `~/Downloads/WoT-0.8.2-release-v1.0.1-worktree` (uncommitted
working tree = build 1.8.15, verified byte-identical to the deployed
`WoT-0.8.2-client-plugin-test-1.8.15-20260809` package). Engine reference:
BigWorld 2.0.1 source (`v2v3v4/BigWorld-Engine-2.0.1` on GitHub),
`src/lib/romp/py_resource_refs.cpp` and `src/lib/romp/resource_ref.cpp`.

## 1. What the log actually says

Key lines from the replica log:

```
PERF window=5.2s role=replica bots=5  fps=0.6 frame=16.00ms callback=0.00ms(0%)  samples=1
PERF window=5.4s role=replica bots=9  fps=0.7 frame=16.00ms callback=6.87ms(43%) samples=1
SPAWN PERF ready=5/28  prepare=0ms load_wait=1334ms(avg)/2657ms(max) build=6ms(avg)/8ms(max)
SPAWN PERF ready=15/28 prepare=0ms load_wait=805ms(avg)/2657ms(max)  build=6ms(avg)/11ms(max)
LAN NET ... max_queue_age=1431ms max_pending=44
```

Three facts follow directly:

1. **`frame=16.00ms` is a fake number.** The tick computes
   `dt = BigWorld.time() - last` and falls back to `dt = 0.016` whenever
   `dt > 0.5` (offline_battle.py, the `_aih_tick` dt clamp). `fps=0.6`
   with `frame=16ms` therefore means: the game thread ran ~3 frames in 5
   seconds, and every frame gap exceeded 500 ms (about 1.4 s each). The
   game thread is the bottleneck, not the network.
2. **The mod's own Python work is NOT the stall.** The instrumented tick
   costs 0-10 ms (`callback=`), spawn `prepare` is 0 ms, spawn `build`
   (unpack refs, `_MockVeh`, `PyModelObstacle`, `createEntity`, GUI) is
   6 ms average, 11 ms max. Everything the mod times is cheap.
3. **"One tank per second" is not a loading rate — it is the frame rate.**
   `_spawn_next` submits exactly one `loadResourceListBG` request per
   rendered frame (zero-delay `BigWorld.callback` chain), and each request
   completes about one frame later (`load_wait` ≈ one 1.3 s frame). With
   1.4 s frames, tank arrival = 0.7/s. The tank rate is a symptom of the
   frame time, not the cause.

So ~1.35 s of every 1.4 s frame is spent in native code that none of the
mod timers see. It is spent between frames, and it correlates exactly with
tank model integration (fps was 51-70 during the countdown with bots=2,
and collapsed the moment placement began).

`max_queue_age=1431ms` / `max_pending=44` confirm the same thing from the
transport side: packets reached the machine but the game thread was ~1.4 s
late applying them.

## 2. The code paths really are identical — the asymmetry is elsewhere

Both roles run the same `_auto_spawn_teams` → `_spawn_next` →
`_mock_handleKeyEvent` → `loadResourceListBG` → `_on_bot_models_loaded`
pipeline. The manifest branch only changes who picks the lineup. Verified
in the working tree at offline_battle.py (manifest branch ~17680,
spawn chain ~17960, spawn entry ~16853).

The two real asymmetries:

**(a) Native per-model integration cost differs per machine, not per role.**
The per-tank stall is native work outside Python: background-loader vs
render-thread contention while a tank's textures/buffers are created, plus
first-draw work (texture upload, shader/effect preparation) when a new
model first renders, plus the per-bot native attachments the mod schedules
on separate callbacks (3 × `VehicleStickers` warm-up items per bot,
`WGVehicleFashion`, model world-add). On the authority machine this
totals ~50-100 ms per tank (10-20 fps during staging). On the replica
machine the same work costs ~1.3-1.5 s per tank. Same code, ~20x native
cost difference. The 1.8.14 build measured this directly: 28 requests
issued in one callback froze the replica's game thread ~42 s ≈ 1.5 s per
tank.

**(b) The shared countdown squeezes the replica hardest.** Since commit
`3d099a0` ("Synchronize LAN timing", Aug 7) LAN clients join the server's
single 30 s countdown at whatever value remains. The countdown starts when
the server broadcasts battle_start, so the slower-loading client joins
late (log: "joined with 21.3 s remaining"), then waits
`auto_spawn_delay_seconds = 10` before placement. The replica had ~11 s of
countdown left for ~40 s of staging work, so ~3/4 of the lineup spawned
inside the live battle at 0.7 fps. Before `3d099a0` every client ran its
own full countdown after its own load, so the same stall was hidden behind
a static pre-battle screen.

## 3. Why "it was fine when LAN was first implemented"

Git archaeology (branch `peng/pre-public-history`):

- `d0ec9f5` (Aug 2, "LAN battle MVP") + `66d1395` ("keep local bots in LAN
  mode"): the first working LAN only synchronized the two human players.
  **Bots were fully local**, spawned exactly like offline mode, no
  manifest, no bot streaming, and each client had its own full countdown
  after its own load. Any spawn hitch looked identical to offline play and
  stayed behind the countdown.
- `e5e3de4` (Aug 3, "synchronize LAN bots and battle rules"): shared
  lineup + authority/replica split introduced. The 28-tank staged spawn on
  both clients dates from here.
- `3d099a0` (Aug 7): shared countdown (see 2b) — the stall became visible
  in live battle on the slower machine.
- The v1.0.0 config even shipped a `preload_bots` experimental flag
  ("warm bot model+BSP cache during loading screen to cut spawn FPS
  hitch") — the spawn hitch is a known, pre-LAN, offline phenomenon. LAN
  did not create it; LAN (shared lineup + shared countdown + a slow
  machine) made it impossible to hide.

Also, "fps was higher back then" during battle is expected: spotting
(`2422b24`), chassis-body collisions (`2a78690`), server-directed tactics,
and the diagnostics themselves all landed Aug 6-7.

## 4. Engine facts (BigWorld 2.0.1 source)

- `BigWorld.loadResourceListBG` never loads synchronously. It always
  queues a `BGResourceRefsLoader` background task
  (py_resource_refs.cpp:50-85); the Python callback runs later on the main
  thread via `doMainThreadTask`.
- The background task calls `ResourceRef::getOrLoad` → `Model::get(id)` —
  a shared cache. A warm re-request is a cache hit, not a disk reload.
- `refs[path]` (in the callback) is what creates the per-bot instance:
  `pyInstance_model` → `PyModel::pyNew` (resource_ref.cpp:105-118). That
  cost is inside the mod's `build` timer and measured 6 ms on the replica.

Consequence: the comment in 1.8.15 ("a second loadResourceListBG call for
a warm model clones it synchronously") does not match the engine. The 42 s
freeze of 1.8.14 was not the submission call and not `refs[]`; it was the
burst of native integration/first-draw work landing on the following
frames. This matters because 1.8.15's fix ("submit one request per frame")
throttled the wrong stage — it serialized submissions, but the per-tank
native cost stayed, so the replica still crawls at one tank per frame.

## 5. What is NOT the problem (ruled out)

- Network transport: 30 snapshots/s arrive fine; `max_socket_gap` is
  small; snapshots are coalesced to one apply per frame in `_poll`.
- Replica-only Python work (`_apply_snapshot`, smoothing, spotting): all
  instrumented, single-digit ms.
- The spawn pipeline's own Python steps: prepare 0 ms, build 6 ms.
- Manifest/lineup preparation: 54 ms for 28 bots / 16 types / 56 BSPs.

## 6. Recommended directions (no code yet)

Ordered by leverage:

1. **Gate the countdown on client readiness (retail behavior).** Retail
   WoT never starts the pre-battle timer until every client has loaded its
   vehicles; that is what the loading/"waiting for players" phase is for.
   Add a `lineup_ready` ack to the protocol: the server starts (or holds)
   `PREBATTLE_COUNTDOWN_SECONDS` until all clients report their staged
   lineup complete. The slow machine then does its 40 s of native work
   behind a static screen, exactly like the MVP/offline days, and the
   battle starts clean on both ends. This fixes the *experienced* bug even
   if the native cost is never reduced. (Also drop or shrink the fixed
   `auto_spawn_delay_seconds=10` on LAN clients — the manifest and ground
   collision are ready long before that.)
2. **Warm the first draw during staging, not during battle.** After each
   model completes, it is hidden (`visible=False`) until placement;
   first-draw costs then hit during live battle when the tank becomes
   visible/spotted. While the countdown screen is up, render each staged
   model once (e.g. one hidden-then-shown frame off-screen, one per
   frame) so texture upload / shader prep happens inside the gated window.
3. **Profile the replica machine's native cost once, properly.** The
   asymmetry (50-100 ms vs 1.4 s per tank) is a machine property. Two
   cheap experiments distinguish the mechanisms:
   - Same machine, offline 15v15, same map: if spawn frames are equally
     long, it is pure native/model-integration cost (expected).
   - Swap which PC is authority: the stall should follow the machine, not
     the role.
   Then check the obvious machine-level suspects on the slow PC: GPU
   driver age, D3D9 through a translation layer (Wine/CrossOver — note
   `_load_server_timing` already anticipates macOS), 32-bit address-space
   pressure, disk. If it is Wine, native `d3dx9_*` overrides typically
   collapse effect/shader costs by an order of magnitude.
4. **Trim per-bot native objects created during staging.** Each bot also
   schedules 3 `VehicleStickers` warm-ups, a `WGVehicleFashion`, and a
   `PyModelObstacle`. On the slow machine, consider deferring stickers and
   fashion until after period 3 starts (or first damage), so staging only
   pays for the model itself.
5. **The authority's 10-20 fps in battle is a separate problem** (bot AI +
   physics + spotting + publish on one client). The PERF buckets already
   exist to break it down; treat it after the replica staging fix.

## 7. Update 2026-08-10: build 1.8.17 dual-log analysis (decisive)

Build 1.8.17 prefetches everything up front (`loadResourceListBG` bulk warm
of 68 unique component paths, then `BigWorld.fetchModel` × 112 for
independent per-bot instances — the retail `VehicleAppearance.__fetchModels`
path), and assembles one bot per rendered frame from the prefetched refs.
Both clients ran the identical build on the same map with the same 28-bot
manifest. Server ran on a third machine (the Mac).

Measured, same code, same battle:

| stage                        | desktop (replica) | laptop (authority) |
|------------------------------|-------------------|--------------------|
| lineup dependencies warm     | 685 ms (bg)       | 513 ms (bg)        |
| fetchModel × 112 components  | 16 ms             | 49 ms              |
| spawn `load_wait`            | 0 ms              | 0 ms               |
| spawn `build` (Python)       | 4-6 ms            | 9-11 ms            |
| **entities assembled × 28**  | **65 881 ms**     | **2 172 ms**       |
| per-bot world-entry cost     | ~2 353 ms         | ~78 ms             |
| fps during assembly          | 0.4-0.5           | ~7-31              |
| fps after assembly (30 tanks)| 39-46             | 12-15              |

Conclusions this pins down:

1. **Every mod-side stage is now measured and cheap.** Data loading,
   instance creation, and per-bot Python build are all ≤ tens of ms on
   both machines. What remains per assembled bot is native work the mod
   never calls directly: `createEntity`/`entity.model` world-add plus the
   first render of that model (D3D9 GPU resource upload / effect-shader
   first-bind).
2. **The 30x gap is a property of the desktop machine, not of the role or
   the code.** The desktop is otherwise the faster renderer (44 fps in
   countdown, 39-46 fps with all 30 tanks visible afterwards); it is
   specifically *first use of a new tank model* that costs ~2.3 s there.
   Swapping authority/replica would move the AI load but not this stall.
3. **The laptop's 12-15 fps in battle is a different, code-side problem,
   now fully itemized:** `callback` 50-68 ms per frame (≈85-100% of the
   frame), dominated by `bot_loop` 27-45 ms, `physics` 10-18 ms,
   `nav_target` up to 16 ms. That is mod Python AI cost on the authority
   and is optimizable in code.

### New bug found in 1.8.17 (bots never fire)

Laptop (authority) log: `SMART_AI initialization failed: float() argument
must be a string or a number` at `_offh_spot_motion`
(offline_battle.py:3261). Cause: `_MockVeh.__getattr__` returns `None` for
unknown attributes, so `hasattr(vehicle, '_offh_spot_still_since')` is
always True and the initializer branch never runs; `float(None)` raises.
The codebase documents this exact `__getattr__` trap elsewhere (~line
5667). Server log confirms the impact: `contacts=0 targets=0 fire=0` and
`aim ... alive:0` for the entire round — bots drove routes but never
acquired targets or fired. Fix pattern: read with
`getattr(vehicle, '_offh_spot_still_since', None)` and treat `None` as
unset.

### Experiments to characterize the desktop's 2.3 s/model cost

1. Offline (non-LAN) 15v15 on the desktop, same map: expect the same ~66 s
   assembly — proves LAN is irrelevant.
2. Immediately play a second battle in the same client session on the
   desktop: if assembly is suddenly fast, the cost is a per-session
   first-use cache (driver shader/effect JIT); if still ~66 s, it is
   per-battle GPU re-upload (note `full_space_release: true` releases the
   battle space between rounds).
3. Identify the desktop GPU + driver (dxdiag). Suspects for a modern
   machine being pathologically slow at D3D9 first-use while fast at
   steady-state: no native D3D9 driver (D3D9On12 translation — Intel Arc,
   Windows on ARM), an old/broken driver, or an injected overlay.
4. High-leverage mitigation to try with zero mod changes: run the desktop
   client through DXVK (native Windows d3d9.dll wrapper) or dgVoodoo2.
   Multi-second per-new-model D3D9 stalls are the textbook symptom these
   wrappers eliminate (async pipeline compile + persistent cache).

### Structural fix that works regardless of machine

Gate the server countdown on readiness: each client sends an
`lineup_ready`/`assembled` ack after its last bot enters the world; the
server starts (or holds) `PREBATTLE_COUNTDOWN_SECONDS` until all clients
have acked, exactly like retail's "waiting for players" phase. The desktop
then pays its 66 s behind a static screen and both battles start clean.
Complementary: start assembly at fetch-ready instead of waiting the fixed
`auto_spawn_delay_seconds = 10`, and warm each model with one hidden
render during the gated window so first-draw costs are also prepaid.

## 8. Corrections to in-code comments (when next editing)

- offline_battle.py ~17716: the "second loadResourceListBG call for a warm
  model clones it synchronously" comment misattributes the stall (see §4).
- The `frame=` field of PERF is misleading under long frames because of
  the `dt > 0.5 → 0.016` fallback; report the real wall-clock gap (or a
  separate `stall=` max) so the next log read shows 1.4 s frames directly.
