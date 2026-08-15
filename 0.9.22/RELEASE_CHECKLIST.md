# 0.4.0 release checklist

This checklist releases only World of Tanks `0.9.22.0.1 #1513`. Run every
command from the repository root with a clean checkout of the exact candidate
commit. Do not mix the 0.8.2 client/server or a different 0.9.22 build into the
acceptance run.

## 1. Freeze the candidate

- [ ] Record `git rev-parse HEAD`, `git status --short` and the exact #1513
      client directory used for review.
- [ ] Confirm the version is `0.4.0` in `meta.xml`, package `PORT_VERSION`,
      `build_wotmod.py`, `validate_wotmod.py`, `build_for_client.sh`, CI and
      the install documentation.
- [ ] Confirm the port exists only at top-level `0.9.22` and that no tracked
      file still refers to the retired nested release directory.
- [ ] Confirm no release source or documentation contains a private build
      endpoint or environment-variable endpoint override; the packaged default
      must be exactly `127.0.0.1:28782`.
- [ ] Record SHA-256 for every source/service/release file frozen by
      `tools/audit_battle_sources.py` and for
      `tools/reviewed_082_source_manifest.json`.
- [ ] Run `git diff --check` and validate every tracked JSON file.

## 2. Automated source and exact-client gates

- [ ] Run the full shared and port suites:

  ```bash
  (
    cd 0.8.2
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -m unittest discover -s tests -p 'test_*.py' -v
  )
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s 0.9.22/tests -p 'test_*.py' -v
  ```

- [ ] Run the 64-module provenance and frozen-hash gate:

  ```bash
  python3 0.9.22/tools/audit_battle_sources.py .
  ```

- [ ] Run the exact `0.9.22.0.1 #1513` inspection and ABI/lifecycle gates with
      CPython 2.7.18 where required:

  ```bash
  python3 0.9.22/tools/inspect_client.py "$WOT_0922_CLIENT"
  python2.7 0.9.22/tools/audit_embedded_types.py
  python2.7 0.9.22/tools/audit_client_abi.py "$WOT_0922_CLIENT"
  python2.7 0.9.22/tools/audit_lobby_consumers.py "$WOT_0922_CLIENT"
  python2.7 0.9.22/tools/audit_client_lifecycle.py "$WOT_0922_CLIENT"
  ```

- [ ] Compile every client source with CPython 2.7.18 in a temporary copy.
      Confirm 64 package modules plus the top-level loader compile, then remove
      all generated `.pyc`, `.pyo` and `__pycache__` files from the source tree.
- [ ] Confirm `python3 0.9.22/tools/audit_battle_sources.py .` still passes
      after the syntax check and that the working tree has no generated files.

## 3. Client build and package validation

- [ ] Build from the exact client and candidate commit:

  ```bash
  0.9.22/build_for_client.sh "$WOT_0922_CLIENT"
  ```

- [ ] Validate the generated WOTMOD independently:

  ```bash
  python3 0.9.22/tools/validate_wotmod.py \
    0.9.22/dist/org.peng.offline_lan_0922_0.4.0.wotmod
  ```

- [ ] Confirm the WOTMOD is Store-only, has no duplicate/case-colliding or
      unsafe paths, passes CRC, contains the exact CPython 2.7 adjacent-PYC
      manifest and contains no `.py`, `.pyo` or `__pycache__` member.
- [ ] Recompile all client sources with CPython 2.7.18 using the production
      filenames/timestamps and compare every PYC byte-for-byte with the WOTMOD.
- [ ] Confirm `meta.xml` reports mod id `org.peng.offline_lan_0922` and version
      `0.4.0`, and that the sidecar SHA-256 matches the standalone, exploded
      and outer-ZIP copies of the WOTMOD.
- [ ] Confirm the exploded overlay and outer ZIP have identical file and
      directory manifests, byte-identical files, Store compression, valid CRC,
      no unsafe/symlink paths and no stale prior-version package.
- [ ] Confirm all 41 navigation, foliage and destructible records pass their
      schema, census and per-map SHA-256 checks in source, exploded overlay and
      outer ZIP.
- [ ] Inspect the packaged `config.json`: endpoint is
      `127.0.0.1:28782`, and the overlay does **not** contain
      `server_endpoint.json`. Confirm the release directory/ZIP name hash is
      derived from the WOTMOD digest plus that endpoint.
- [ ] Record filenames, byte sizes and SHA-256 for the WOTMOD, sidecar and
      copy-ready client ZIP.

## 4. Windows server artifact

- [ ] Let the `windows-server` GitHub Actions job build the executable on
      `windows-latest` with Python 3.11.9 x64 and the pinned PyInstaller
      version.
- [ ] Require its smoke gate to prove PE machine `0x8664`, listener
      `0.0.0.0:28782` and a valid protocol-v5 `welcome` for the pinned client
      build before artifacts are uploaded.
- [ ] Download the CI artifacts and confirm the ZIP contains only
      `WoT-0.9.22-LAN-Server.exe` and `README.txt`; record size and SHA-256 for
      both the ZIP and standalone EXE.
- [ ] On a clean Windows x64 machine, double-click the EXE without arguments.
      Confirm the console stays open, the server selects `server_random`, a
      local client can connect through `127.0.0.1:28782`, Ctrl+C stops it, and
      a port-in-use error remains visible.
- [ ] With the scoped firewall rule absent, confirm that first launch explains
      and opens one UAC prompt for the exact EXE and TCP `28782`; approve it,
      relaunch, and confirm that no second prompt appears. Cancelling must be
      nonfatal and must leave a clear warning in the server console. The rule
      intentionally permits any remote address/profile for VM compatibility;
      document that the server is for trusted networks only.
- [ ] Record that the artifact is unsigned. Treat an unknown-publisher
      SmartScreen prompt as an explicit release boundary; verify SHA-256 and do
      not claim publisher trust or code signing.

## 5. Native Windows #1513 acceptance

- [ ] Start from a clean exact `0.9.22.0.1 #1513` installation with only the
      `0.4.0` WOTMOD and complete release overlay installed.
- [ ] On first garage entry, verify the automatic CN server-announcement
      browser is suppressed before creation. Open a normal player browser link
      afterward and verify explicit browser use remains available.
- [ ] With server and client on one PC, confirm the unedited client connects to
      `127.0.0.1:28782`.
- [ ] Change the endpoint through the in-game LAN window, reconnect, restart
      the client and verify `server_endpoint.json` preserves the value. Install
      a fresh `0.4.0` overlay and verify the saved endpoint still wins.
- [ ] Verify the host sees `SELECT A MAP, THEN CLICK CREATE TO START`, guests
      see whom they are waiting for, and no player-facing instruction requires
      understanding the internal authority role.
- [ ] Complete both a one-player round and a multi-client LAN round. Verify
      map selection, 15-v-15 roster, loading barrier, host transfer, battle
      end, return to waiting and a second round without restarting the client.
- [ ] During PREBATTLE, verify the physical gun, stock reticle, optional server
      marker and vehicle remain frozen. At the single BATTLE transition, verify
      aiming, movement and fire unlock together.
- [ ] Verify one CTF base per team, Lakeville narrow-road departures, finite Bot
      ammunition/round selection, ordinary shell flight time, moving-target
      lead, SPG low/high arcs and projectile handoff after shooter/authority
      changes.
- [ ] Verify native tracers, gun audio, damage feedback, consumables, spotting,
      postmortem camera, destructibles and round cleanup. Stun remains outside
      the implemented canonical loop and must not be represented as supported.
- [ ] Record client FPS/frame pacing and server `PERF` diagnostics in sustained
      15-v-15 play. Preserve `python.log` and server logs; no traceback, native
      crash, repeated protocol rejection or stale callback is acceptable.

## 6. Publish

- [ ] Commit the frozen candidate, push `main`, and require every GitHub Actions
      job to pass on that exact commit.
- [ ] Create an annotated `v0.4.0` tag on the same commit and push it:

  ```bash
  git tag -a v0.4.0 -m "release: World of Tanks 0.9.22 LAN 0.4.0"
  git push origin main
  git push origin v0.4.0
  ```

- [ ] Publish the client ZIP, WOTMOD, sidecar, Windows server ZIP/EXE, install
      instructions and a SHA-256 manifest. State the exact #1513-only target,
      unsigned Windows-server boundary and remaining native acceptance limits.
- [ ] Download every published file into a clean directory and repeat CRC,
      manifest, version and SHA-256 checks. Record the release URL, tag commit
      and final hashes in the release handoff.
