# Claude working agreement

This file contains repository-specific working knowledge that is not obvious
from the implementation. Read it before changing the repository. When working
under a client version, also read its version-local guide before investigating
or editing that implementation: `0.8.2/CLAUDE.md` or `0.9.22/CLAUDE.md`.

## Working with Peng

- Discuss plans, evidence, tradeoffs, progress, and results with Peng in
  Chinese. Keep code, comments, commit messages, logs, and shared repository
  documentation in English.
- Lead with the observed outcome and supporting evidence. Separate a confirmed
  fact from an inference, and say exactly which boundary still needs a real
  Windows client.
- Prefer the smallest coherent fix. Do not add speculative compatibility
  layers, configuration, or infrastructure merely because they may be useful
  later.
- Present meaningful tradeoffs instead of silently choosing one. If empirical
  behavior contradicts documentation or a prior conclusion, reproduce the
  behavior first and update the conclusion.
- Treat Peng's gameplay observations as runtime evidence. A screenshot may be
  ambiguous, but do not dismiss the observation because the screenshot is
  ambiguous; inspect the exact map, client data, logs, and lifecycle.
- Fix an in-scope bug directly rather than handing it to an imaginary future
  owner. Preserve unrelated user changes in a dirty worktree.
- Peng has explicitly permitted validated changes to be committed and pushed
  directly to `main`. Never force-push. Do not create or push a release tag, or
  publish a release, until Peng explicitly requests it and the required native
  acceptance is complete.

## Start every task from the exact current state

Run these checks before relying on an old handoff, test count, hash, or path:

```bash
pwd
git status --short --branch
git worktree list
git log -5 --oneline --decorate
git rev-parse HEAD
git rev-parse origin/main
```

Before basing work intended for push, or reporting that `HEAD == origin/main`,
run `git fetch --prune origin`. Without a fetch, `origin/main` is only the last
locally observed remote state. An offline diagnostic task need not fetch.

Other agents and worktrees may be active. Coordinate ownership of shared files
before editing them, and rerun validation after the final files stop changing.
A passing test from a drifting intermediate tree is not release evidence.

Do not use destructive Git commands to clean up work that you did not create.
Generated `__pycache__`, `.pyc`, `.pyo`, logs, and release output should not be
committed; remove only exact generated targets after verifying what they are.

## Keep the two client lines separate

- `0.8.2/` targets its own client and Python 2.6 runtime. Its code, package,
  tests, and native-physics boundary are version-local. Follow
  `0.8.2/CLAUDE.md` for its legacy-runtime, collision, streaming, and package
  workflow.
- `0.9.22/` targets only Chinese HD client `0.9.22.0.1 #1513`, x86, with an
  embedded Python 2.7 runtime. Follow `0.9.22/CLAUDE.md` for its exact-client
  workflow.
- Do not transplant PYC files, private names, entity schemas, native offsets,
  map parsers, or lifecycle assumptions across versions. Reuse gameplay law
  only after proving the target adapter.
- `ports/0.9.22` is a retired path. The live port is the top-level `0.9.22/`
  directory.

## Evidence ladder

Use the lowest layer that answers the question, but never claim that it proves
a higher layer:

1. Current repository source and pure-data tests prove local logic.
2. Exact pinned resource data and bytecode prove build-specific Python
   contracts and static content.
3. Contract tests and audits prove that the local adapter matches the reviewed
   contract.
4. Independent package inspection proves what was actually shipped.
5. Only acceptance on the exact Windows client can prove the specific
   BigWorld rendering, native physics, timing, lifecycle, performance, and
   gameplay-feel claims exercised by that acceptance.
6. A native crash requires a first-chance/full dump or minidump plus the
   matching executable, package, `python.log`, and server log. Static guesses
   are not a crash diagnosis.

For example, a Python unit test can prove that a native call is made with the
reviewed arguments. It cannot prove that the native implementation renders,
owns memory safely, or feels identical to retail.

## Canonical documentation

Keep the documentation small. These files have owners; do not duplicate them:

- Root `README.md`: what the project is, how a player runs it, how to build it.
- `0.9.22/INSTALL.txt`: what the 0.9.22 package contains and how to play.
- `0.9.22/COMPATIBILITY_REVIEW.md`: exact-client interfaces and lifecycle
  evidence for the #1513 port.
- `launcher/LAUNCHER_README.txt`: the text shipped inside the launcher
  download, including the bundled-runtime licenses.
- `.github/workflows/tests.yml`: what CI actually executes.

Do not add a new document for a change that fits in code, a test, or one of the
files above. Do not hard-code a current test count, release status, CI URL, or
source hash in this instruction file.

## Change, validation, and handoff discipline

- Diagnose with read-only checks first. Implement only when the task includes a
  change.
- Test the narrow failure first, then the relevant subsystem, then the complete
  required gate. Use `PYTHONDONTWRITEBYTECODE=1` for Python tests where
  possible.
- For asynchronous tests, wait for a state transition or acknowledgement. Do
  not use a small fixed sleep as proof that a handler or worker consumed a
  message.
- Before pushing, inspect the staged diff, run `git diff --check`, commit one
  coherent change, push `main`, and verify the exact pushed commit's CI when
  the change affects runtime, packaging, CI, or release behavior.

A useful final handoff records:

- exact `HEAD`, branch, and whether `HEAD == origin/main`;
- files changed and the user-visible result;
- exact commands run and their results;
- package or runtime identity when relevant;
- what remains unproved, especially native Windows behavior;
- whether work was committed, pushed, tagged, or released.

Never describe a task as complete merely because the static tree is green when
the stated acceptance requires native Windows evidence.
