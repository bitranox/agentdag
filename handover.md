# Handover, written 2026-09-06 19:10 CEST

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where this session stopped and what it decided.

## The next action

**Start Task 12 of `PLANS/2026-09-04-corrected-pair-plan.md`: the plumbing dry run and
one-checkpoint parity.** It is the top-ranked open item (rank 05) and it is the CONTROLLER's own
work, not an implementer's - it drives the harness against a real checkpoint.

Three checks are recorded on rank 05 rather than resolved, and each one is cheap now and expensive
later:

- **Does the harness tally `agent.usage` for a checkpoint whose `run()` raised?** Force a short
  `timeout` in the arm config and read the recorded cost. The whole timeout-harvest fix rests on
  this, and nothing in the repo or in any probe exercises that path.
- **Does `kernel.deny_bash` need `curl --data` opened** for the `dynamic_config_service_api`
  checkpoint? It is an HTTP service and the shipped default closes that command. One line in the
  config's `settings` map, no code change.
- **Read a COORDINATOR node's own transcript** for `data.claude_code_version` and the permission
  mode, proving both took rather than assuming the config did. The control was checked this way;
  the coordinator arm has not been.

## In flight

Nothing. No agent of mine is alive; every backstop and monitor was stopped.

## Committed, or not

Everything is committed and pushed. `origin/main` is at `b171fc8`, and CI plus CodeQL are green on
both `77df509` (the Task 11 work) and `b171fc8` (the staleness marker on the previous handover),
read through `ci_wait` wrapped in `gate.py`.

Task 11 shipped as `c14ebf5` (agent type and installer), `f5050a8` (fix round 1), `101c6be` (fix
round 2), `4e6f452` (the vacuous-test fix), plus `ed35fe5` (backlog rank 63) and `77df509` (a
`types-pyyaml` floor bmk raised during the gate run).

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72), blob
`a2a5b9cfcaf0471f877025ecbd35da74bdd78e9b` mode `100644`, unchanged through five commits this
session. Every commit used a pathspec and was checked with `git show --name-only`.

Gitignored, so not in git: `EXECUTION-USER-REVIEW.md`, `.bitranox/sdd/progress.md` (the ledger) and
the per-task briefs, reports and review diffs beside it. Note that `handover.md` and `OPEN-WORK.md`
ARE tracked here, whatever the context-watcher's generic warning says.

## Decided this session, with the reason

- **Task 11's harness-side code ships as `.tmpl` DATA, with the installer as a normal gated module**
  (USER, from three options): no `.py` here may import the harness's `slop_code`, because pyright is
  strict with no `include` and walks `deploy/`, and pytest has no `testpaths` while `addopts` carries
  `--doctest-modules`, so collection imports what it finds. Both halves measured with a probe before
  the question was put. The rejected options were carving `deploy/` out of both tools, and pinning
  the harness as a dev dependency.
- **Build the `--policy` lever rather than document its absence** (own): the design names
  `--policy <arm tier policy>` and nothing could supply it, since the flag takes a file and has no
  `kernel.*` key. It is also the only path to the fourth fairness axis nobody had named - the control
  ran `thinking: high` while agentdag resolves effort from its own tier policy.
- **The coverage split goes into a second `.tmpl` module, not stubbed `slop_code` in `sys.modules`**
  (own): stubbing would hand-build a third-party API surface that drifts. My enumeration of what to
  move was INCOMPLETE and the fixer corrected it, which was right.
- **The timeout harvest snapshots the run store instead of parsing stdout** (fixer, ratified by the
  re-review at source): under `--foreground` no run id reaches stdout before the terminal line, so
  the instruction I gave would have harvested nothing. `FsRunDir.create` makes exactly one directory
  per run and `--runs` is validated but never created, so the set difference is sound.
- **The credential-copy fallback is an open question, not a defect** (own, rank 63): a keyfile path
  naming no file selects the credential copy BY DESIGN and says so; the refusal one function below
  governs a relaunch whose persisted keyfile has gone. The Task 11 report reads that refusal as
  covering `run start` and is wrong about it.
- **One Minor deferred to the final whole-branch review** (own): `usage_from_records` computes
  `spend_of` twice per checkpoint. Fixing it means touching `agent.py.tmpl` again and reopening a
  verification cycle for redundant arithmetic.

## Decided against, and why

- Fixing rank 62 (the tool-omission contradiction) in this session: it is prose in a green tree, and
  it belongs with the final whole-branch review where the whole claim family can be enumerated by
  MEANING in one pass.
- A fifth review agent for the last fix: it was a test-only change of a shape I specified exactly,
  and a verifier had already independently confirmed the production branch correct by mutation.
- Running Task 10's live probe or letting an implementer run it: real spend, controller's job. Now
  filed as rank 64 so it stops living only in a handover.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- Ranks 32, 37, 39, 40, 50, 55 through 62, 65, 68, 70, 72, 73, 75, 77, 80, 85, 87 as before.
- Ranks 63, 64 and 66 are NEW this session: the credential-copy question, Task 10's unrun live
  probe, and three known gaps in the shipped coordinator arm.

## Lessons for the next nap

Three were captured to the store already this session (the dispatch-constraint rule, the gate
walking `deploy/`, and gate.py's verdict stream). These are not:

- When a review closes a finding by ADDING tests, check the new tests are not vacuous: three rounds
  here, and the last found a case whose input could never reach the branch it was named for.
- When a fixer deviates from your instruction and gives a reason, judge the reason on merit - one
  deviation this session was better than the instruction it replaced, and shipping my version would
  have harvested nothing.
- When you enumerate what to extract for testability, expect the enumeration itself to be wrong: my
  list would have left exactly the ranges the review named as uncovered still uncovered.
- When a defect survives a careful self-review, look for the probe that STUBBED the component whose
  real behaviour is the defect - that is how a launch that detached every run passed its own test.
- tooling: the ci-watch Stop gate is satisfied by READING the verdict, not by arming a watcher, so a
  backgrounded `ci_wait` still needs its output read before the turn ends.

## Files that matter

- `PLANS/2026-09-04-corrected-pair-plan.md` - Tasks 12 and 13 pending; the arm-fairness note sits
  directly above Task 12.
- `.bitranox/sdd/progress.md` - the ledger, and the only record of the carried Minors the final
  whole-branch review should triage. Gitignored, so `git clean -fdx` destroys it.
- `.bitranox/sdd/task-10-report.md` section 5 - the unrun live probe's config, command and both read
  points (rank 64).
- `deploy/slopcodebench/` - the arm: `agentdag.yaml` (the three fairness settings, compared against
  `~/agentdag-eval/slopcodebench/agent-claude-code-oauth.yaml`), `agentdag_scb_agent/agent.py.tmpl`,
  `support.py.tmpl`, `docker.j2`.
- `scripts/scb_install_agentdag.py` and `tests/test_scb_install_agentdag.py`,
  `tests/test_scb_agent_support.py` - the installer and the only automated coverage of the arm.
- `~/agentdag-eval/slopcodebench/slop-code-bench` - the harness clone, commit `06b5c06`, READ-ONLY
  until Task 12 runs the installer against it.

## How to verify

- `git log --oneline 4c28829..HEAD` shows this session's work.
- The gate is `make test` through `gate.py`, and it read `[PASS] make test (rc=0)` at `4e6f452`.
- CI and CodeQL were green on `77df509` and `b171fc8`
  (`ci_wait.py --sha 77df5098b481538e97ee6d37eb80ab61733fb840`).
- The arm's own tests: `.venv/bin/python -m pytest tests/test_scb_agent_support.py
  tests/test_scb_install_agentdag.py -q` reads 85 passed.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
