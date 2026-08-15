# Claude guide for the original 0.8.2 client

This guide captures the engineering method and recurring lessons for the
top-level `0.8.2/` implementation. It supplements the repository-wide working
agreement in the root `CLAUDE.md`; it does not replace the current install,
protocol, performance, or native-acceptance documents.

## Fixed target and architecture boundary

The supported target is the original 32-bit Windows World of Tanks 0.8.2
client with its embedded CPython 2.6 runtime. Treat every BigWorld API, PYC,
resource layout, executable signature, and native-object lifecycle as
version-specific. A same-named surface from 0.9.22 or a public client source is
a lead, not proof for 0.8.2.

The runtime is deliberately hybrid:

- the local player is advanced by the Python movement and collision path;
- locally owned bots use `WGVehiclePhysics2` in one shared
  `WGDynamicsSimulator` batch;
- LAN replicas consume canonical network snapshots and must not become a
  second simulation owner;
- the Python 3 companion server owns shared LAN state and planning, but it
  does not replace the proprietary client collision, rendering, shell, or
  vehicle-data systems.

Normal offline play is a client-side compatibility simulation: `FakeServer`
and `CommandRouter` satisfy the stock client's expected account and command
surfaces. It is not a reconstructed BigWorld server. Keep server-side planning
code independent of BigWorld and keep wire payloads JSON-compatible.

Keep one owner for every pose, collision response, health transition,
destructible delivery, and authority epoch. A presentation provider, network
snapshot, Python correction, and native rigid body may all expose plausible
positions, but allowing two of them to write the same vehicle creates jitter,
teleports, or silent state divergence.

The client entry point is
`0.8.2/scripts/client/gui/mods/mod_offhangar.py`. Most implementation modules
live below `0.8.2/scripts/client/gui/mods/offhangar/`. Server and tooling code
under `0.8.2/` runs on Python 3; do not make the embedded client depend on
those Python 3 objects or libraries.

## Start from the exact source, install, and package

Before investigating a gameplay report, identify all of these independently:

- Git commit and worktree;
- client build and selected map;
- installed replacement-package identity;
- client `python.log` and, for LAN, the matching server log;
- whether the client actually loaded source or stale bytecode;
- authority or replica role and current round.

Do not infer one from another. A healthy server log does not prove that the
Windows client loaded the same source tree, and a directory named after the
latest version does not prove that its contents match the latest package.

The safe install/update operation is a complete replacement of
`res_mods/0.8.2`, while preserving `<game root>/offhangar_user/`. Never merge a
new package into an old mod tree. In particular:

- remove stale `mod_offhangar.pyc`, which otherwise hides the new source;
- retain the shipped `scripts/client/CameraNode.pyc` and
  `scripts/client/OfflineEntity.pyc`, which are required legacy loader/entity
  bytecode;
- keep user configuration and runtime state outside `res_mods`;
- confirm the source-loader message in `python.log` before diagnosing the new
  behavior.

Running Python 3 imports against client source can create ignored
`__pycache__` files beside package inputs. Prefer
`PYTHONDONTWRITEBYTECODE=1`, remove only exact generated paths, and rerun the
package audit before delivery.

## Python 2.6 discipline

Client modules must parse and run on CPython 2.6. A successful Python 3 test
or source grep is not a Python 2.6 grammar check. Avoid Python 3-only syntax,
libraries, exception forms, text assumptions, and APIs introduced after 2.6.
Be especially careful when touching large legacy files that retain tabs,
Python 2 exception syntax, or mixed historical line endings.

Use the same container boundary as CI for an exact grammar pass:

```bash
docker run --rm --entrypoint /bin/sh -v "$PWD:/work:ro" \
  zinainfra/python26 -c '
    cp -R /work/0.8.2/scripts /tmp/scripts
    for source in $(find /tmp/scripts/client/gui/mods -name "*.py" -type f); do
      python2.6 -m py_compile "$source" || exit 1
    done
  '
```

Do not parse an entire Python 2 client module with Python 3 `ast` merely to
read one top-level build constant. The 0.8.2 packager intentionally reads the
individual assignment expression without parsing the surrounding Python 2
grammar.

Python determines local variables for the whole function at compile time. An
inner `import ... as name` can therefore shadow a dependency captured by an
earlier nested function even when the import appears later in source. This has
already disabled both player-to-bot collision and terrain classification in
the same tick. Keep battle-scope dependencies bound once, add an executable
lexical-scope regression when this matters, and never convert a collision
exception into a silent `pass`.

## Establish a 0.8.2 engine contract before adapting it

For every BigWorld or stock-client dependency, record and verify:

- exact executable, package member, PYC, XML, or map asset;
- callable name, argument count, tuple width, callback signature, and return
  sentinel;
- whether a mask includes or excludes a class of geometry;
- entity, space, streaming, and arena prerequisites;
- native versus Python ownership and any synchronous re-entry;
- state readback or direct consumer proving that the call took effect;
- duplicate-call, partial-startup, failure, and teardown behavior.

Inspect both producer and consumer. The presence of a resource field or native
string is not proof that the target client reads it. A successful Python
wrapper call is not proof that a native object accepted or retained the state.

Collision callbacks in this client receive
`(matKind, collFlags, itemId, chunkId)`. A false callback result skips that
candidate. The collision-mask bits are exclusion flags: the stock terrain
classifier uses mask `128` for the reviewed candidate set and mask `136` to
exclude terrain bit `8` and isolate the non-terrain subset. Preserve these
semantics in fakes and tests; an inverted fake can make unsafe production code
look correct.

The native filter bridge is locked to one reviewed executable and one x86
extension ABI. Audit both before treating a bridge artifact as compatible:

```bash
python3 0.8.2/tools/audit_native_filter_bridge.py \
  --exe /path/to/WorldOfTanks.exe \
  --pyd 0.8.2/scripts/client/gui/mods/offhangar/offhangar_native_seed.pyd
```

The audit proves pinned PE identity, reviewed signatures, imports, exports, and
bridge markers. It does not prove Windows lifecycle, physics, presentation,
or memory safety.

Rebuild the bridge only when native source or ABI work requires it.
`0.8.2/tools/build_native_filter_bridge.sh` overwrites the tracked PYD and is
not a read-only audit command.

## Native physics and pose ownership

Authority-side native bots must be solved together. One
`WGDynamicsSimulator.update(dt, vehicle_physics, physical_bodies)` call owns
the frame reset, terrain and track contacts, bot-to-bot pairs, force solve, and
integration. Calling it once per vehicle loses pair contacts. The third
argument is a sequence of `WGPhysicalBody`, not a place to insert the Python
player or another `WGVehiclePhysics2`.

Initialize client-created bodies with the reviewed server-style physics flags,
track/carcass contact gates, and readbacks. Movement must update both
`WGVehiclePhysics2.movementSignals` and the filter input surface; neither one
alone owns both force and prediction in this adapter. Keep the lightweight
`OfflineEntity.isStarted` false so stock camera and appearance code do not
treat it as a complete retail `Vehicle`.

The pinned bridge publishes each solved physics root into the corresponding
filter history. Gameplay, networking, and the rendered chassis must then
consume the same canonical root. Do not reintroduce an old entity/filter
provider as a second chassis writer or add smoothing because a distant marker
appears to jump. First capture the physics matrix, mock matrix, chassis root,
`HP_gui`, filter/body matrix, and placing compensation in the same frame and
classify which boundary moved.

The Python player does not participate in the native vehicle batch. Its
contact with a native-owned bot is a hybrid oriented-box solve that corrects
only the Python-owned participant; teleporting the native body would be
overwritten by its solver. Bot-to-bot collision remains native. Broad-phase
metrics that stay at zero during visible overlaps are evidence that the
resolver did not run, not evidence that tanks lack collision volume.

Native startup is fail closed. A body is not active merely because it was
allocated: live support, filter ownership, native settings, warmup pose, and
canonical output all have to pass. Keep faulted native bodies as safe static
collision obstacles when that is the reviewed state, and do not silently fall
back to a second Python movement owner.

Native destructible callbacks collect only plain data while the shared solver
is live. Drain and apply that data after every body output has been captured;
mutating the destructible world from inside the native callback can invalidate
the batch that is still iterating it.

## Terrain, static collision, and destructibles

Terrain and obstacle handling is a classification pipeline, not a single ray:

1. sweep the complete movement integrated for this frame;
2. classify each lower-hull contact using its actual lane and authored flags;
3. compare slope along the direction of travel, not total cross-slope;
4. preserve every non-terrain contact for exact material/destructible handling;
5. after a successful destructible delivery, continue the same lane to the
   movement endpoint;
6. stop at the bounded scan limit and fail closed if the segment is not proved
   clear.

Do not replace the hit lane with the chassis centre line. A cross-slope may
make the two tracks sit at different heights while remaining easy to drive,
and a centre-line seam can otherwise create a false wall. Conversely, a
smooth centre line must not hide an abrupt step under one track.

Ground support accepts only an upward-facing surface. If a roof, wagon,
destroyed collision skin, underside, or over-high layer is rejected, continue
below it and accept only a real lower floor. Do not turn a rejected upper
surface into invented support, and do not turn it into `airborne` without
checking the lower layer. A short-lived destroyed BSP skin may remain visible
to collision after the authority accepted destruction.

Direction and kinetic energy are separate. A zero-speed W/S probe may choose
the sweep direction, but the crush gate must still receive the real zero
speed. Even a seemingly tiny invented speed can destroy a high-correction
stock object with a very heavy vehicle.

Destructible crushability follows the stock mass, speed squared, object scale,
health, material/module, and kinetic-correction contract. Always pass the
actual colliding vehicle, including for bots. Identity comes from the exact
hit point and surface-normal material probe, not from proximity to a model
pivot; long fences often have an endpoint pivot far from the contact.

Treat native destruction as a commit boundary:

- validate descriptor, identity, health, and crushability first;
- issue one unambiguous native delivery;
- commit deduplication, health, and local ledger state only after acceptance;
- leave failure retryable only when delivery definitely did not occur;
- treat an ambiguous native exception as a fault, not permission to send a
  second destructive order.

Unknown material, failed authority delivery, callback error, or exhausted
scan budget remains solid.

## Prebaked navigation, canonical spawns, and streaming

Stock maps use versioned prebaked navigation and canonical two-team spawn
formations. For a stock map, missing files, bad checksums, stale manifests,
missing validation proofs, skipped obstacle models, incomplete formations, or
non-finite poses are release errors. Do not hide them with a procedural grid
or runtime formation fallback.

The baker and runtime must agree on physical safety:

- sample the raw terrain under the representative full vehicle footprint;
- reject water, map edges, unsupported corners, and unsafe height spread;
- rasterize solid structures and static BSP instead of treating every
  destructible as unconditionally soft;
- require zero skipped spawn-obstacle models, using reviewed collision or a
  conservative bounding fallback when appropriate;
- validate pairwise formation clearance with the largest supported envelope;
- terminate capture routes at a full-hull-clear node inside the capture circle
  rather than at an obstructed flag centre;
- preserve the final predecessor or revalidate the actual terminal arrival
  yaw after route sampling.

Run bakers into a temporary directory first. A complete stock-map refresh must
publish graph files and their manifest atomically; a half-written catalog is
not a usable intermediate release.

`spaceLoadStatus == 1` proves only that the current camera neighbourhood is
ready. It does not prove that a distant spawn chunk is resident. An authority
must gate native placement on a real `wg_collideSegment` support hit close to
the canonical baked height. Baked Y is a consistency check, never a substitute
for live collision.

The streaming bootstrap expands the projection range from playable bounds,
verifies the setter by readback, freezes the accepted lineup, and retains that
coverage while native owners depend on it. Restore the exact original range
only after native owners have stopped and before the space is unmapped. If
native teardown or range restoration fails, retain the state for retry. A LAN
authority promotion is subject to the same live-support fence before it may
write bot poses.

Holding full playable-map collision coverage is a correctness tradeoff: it can
raise render distance and retain many chunks in a 32-bit process. Only Windows
FPS and working-set measurements can accept that cost.

## LAN authority and asynchronous state

A client send returning true means that a payload entered the local socket
queue. It is not application acceptance. The server-accepted bot manifest is
therefore fenced by round identity, a nonce, exact bot ids, and an explicit
result message. The authority must not create bots from a locally proposed
lineup while that result is pending.

For canonical lineup work:

- compute expected bot ids after excluding connected human `(team, slot)`
  occupancy;
- freeze the first accepted complete manifest and map frame;
- accept an identical retry idempotently;
- reject a same-id retry whose vehicle, profile, pose, route, or frame changed;
- rebuild authority work from the accepted canonical manifest, not from a new
  random local job list;
- reject late joins once the round lineup is frozen unless the protocol also
  implements atomic bot replacement and broadcast removal.

Round id, authority tenure, manifest nonce, order revision, health/event
sequence, and full-handoff fences are independent. A delayed ACK or snapshot
from an old round or demoted authority must not install state. During
promotion, apply complete pose/rules state and pass the streaming gate before
releasing the handoff fence. During demotion or cleanup, stop native owners
before releasing their streamed terrain.

Tests for threaded handlers and socket queues must wait for an ACK, state
transition, or queue condition. A fixed short sleep creates races and is not
proof that the server installed the message. Reliable events and coalesced
snapshots also have different ordering and retention rules; do not infer FIFO
atomicity across them.

## Performance and diagnostics

Start with a reproducible map, lineup, role, and captured `PERF`, `LAN NET`,
and server timing windows. Compare like with like. Many stage metrics are
nested, so do not add them together; distinguish milliseconds from call
counts. A server that holds 30 Hz does not prove that the authority render
thread or native ray workload is healthy.

Prefer behavior-equivalent optimizations:

1. reject work with a mathematically strict bound before native geometry;
2. stop traversal as soon as the historical result cap is reached;
3. reuse same-frame support or coordinate transforms behind identity fences;
4. remove duplicate broad-phase or serialization work;
5. distribute bounded work fairly without delaying canonical pose commits.

Do not lower collision, projectile, visibility, or route-safety cadence merely
to improve a timing line. Dense foliage optimizations must preserve exact
candidate order, transparency, and camouflage output. Collision shortcuts
must preserve complete movement sweeps, including low-FPS large-`dt` frames.

Diagnostics should classify a boundary, not continuously print every frame.
Use thresholds, a short ring buffer, per-vehicle cooldown, and a battle-global
cap. Once a cap is reached, return before expensive matrix or ray sampling.
Reset diagnostic state at battle teardown.

`0.8.2/PERFORMANCE_TEST.txt` owns the current metric definitions and Windows
acceptance procedure. Do not duplicate a changing metric list here.

## Testing legacy runtime behavior

Use the smallest executable regression that reproduces the real control flow.
Extracting a nested helper from `offline_battle.py` can be valuable, but inject
the same enclosing bindings and call it with engine-like data. Source-string
assertions alone cannot prove runtime behavior.

Test doubles must obey the reviewed native contract:

- a collision hit must lie inside the requested segment;
- mask bits and callback filtering must match the engine;
- surface normals and material identity must describe the same contact;
- stateful setters must support the same readback or failure mode;
- asynchronous servers must provide an observable acknowledgement boundary.

Every bug test needs a safety counterpart. Examples include terrain-only
clear versus terrain followed by a wall, destroyed fence followed by an intact
wall, rejected roof over real ground versus rejected roof over void, and
authority ACK versus stale-round ACK.

Run validation in layers. Useful commands include:

```bash
cd 0.8.2
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_player_slope_obstacle_regression \
  tests.test_player_ground_contact_regression \
  tests.test_player_native_collision_contract

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests -p 'test_*.py' -v
```

The working directory matters because the 0.8.2 suite imports version-local
server modules. Running discovery from the repository root with only
`-s 0.8.2/tests` is not equivalent.

For a release candidate, also run the exact CPython 2.6 compile, relevant
native ABI audit, all-stock-map graph/manifest validation, package build and
independent archive inspection, then the native Windows acceptance named in
`0.8.2/START_NATIVE_TEST_HERE.txt`.

## Packaging and release discipline

`0.8.2/tools/package_native_experiment.py` builds only tracked runtime
payloads. An untracked new module can pass local tests yet be silently absent
from the replacement package. Confirm new runtime files are in the Git index
before packaging.

Build into a fresh temporary directory and pass the candidate version from
`0.8.2/VERSION.txt`:

```bash
python3 0.8.2/tools/package_native_experiment.py \
  --output-dir /path/to/empty/output \
  --version <candidate-version>
```

The package audit owns the current required-entry list. Independently verify
wrapper paths, CRC, `SHA256SUMS.txt`, source-to-package bytes, the exact two
required PYC files, native bridge identity, client/server/offline build
identity, and absence of tests, tools, native sources, user state, stale
bytecode, or another client version. Shared `LICENSE`,
`THIRD_PARTY_NOTICES.md`, and `licenses/` remain at repository root and are
copied into each release by its packager.

Do not hard-code a current version, test count, package hash, or release status
in this guide. `0.8.2/VERSION.txt`, the current release notes, CI workflow, and
fresh package output own those facts.

`START_PROBE_HERE.txt`, `NATIVE_FILTER_BRIDGE_TEST_NOTES.txt`,
`NATIVE_NAVMESH_TEST.txt`, and `LAN_SPAWN_INVESTIGATION.md` preserve useful
historical experiment evidence. They are not the current release checklist and
may contain superseded identities, counts, hashes, or procedures.

Do not tag or publish merely because static gates pass. A native release still
needs exact Windows proof for startup, both teams, movement, slopes, vehicle
and world collision, destructibles, authority handoff, cleanup, FPS, and
32-bit working set.

## Failure evidence and recurring traps

For gameplay reports, preserve the complete screenshot or video, map and
approximate coordinates, direction of travel, vehicle, input, FPS, authority
role, package identity, client log, and matching server log. Deduplicate
repeated attachments by content hash before counting them as independent
runs. Use map assets and timestamps to test a hypothesis, but state when a
screenshot cannot uniquely identify an instance or coordinate.

For a native crash, collect:

- exact `WorldOfTanks.exe` and bridge/package SHA-256;
- exact Git commit and installed package identity;
- deterministic reproduction steps;
- complete `python.log` and matching LAN server log;
- first-chance/full dump or minidump from the crashing process.

Recurring traps include:

- diagnosing new source while stale `mod_offhangar.pyc` is still installed;
- using Python 3 parsing or imports as proof of Python 2.6 compatibility;
- assuming `spaceLoadStatus` means the whole map is resident;
- substituting baked height for live collision support;
- treating all destructibles as navigation-soft or all upward normals as
  terrain;
- profiling the chassis centre instead of the lower-hull lane that hit;
- stopping a sweep after destroying the first object;
- inventing probe velocity that leaks into kinetic damage;
- committing a dedup key before native destruction is accepted;
- interpreting a queued LAN send as server acceptance;
- releasing an authority or streaming fence before all dependent owners stop;
- swallowing a physical exception and allowing motion;
- trusting a fake that ignores segment, mask, callback, or lifecycle guards;
- packaging before a new runtime file is tracked;
- treating static tests or archive audits as native Windows acceptance.

When evidence contradicts an earlier diagnosis, keep the evidence and replace
the diagnosis. The goal is not to defend the previous patch; it is to preserve
the smallest contract that explains and fixes the actual 0.8.2 behavior.
