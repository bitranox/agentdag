# Handover: agentdag, 2026-09-01 ~22:35. Rank 35 closed by measurement. C1 still unscored.

> **The `RESEARCH/` paths point into a private companion repo.** These documents were written
> beside a private research repository and cite it by repo-qualified path for the design
> documents, probe scripts and measurement notes they were derived from. The `RESEARCH/` prefix
> names that repo; it is deliberately not a relative path, because no relative path from here
> resolves to it. These citations do not resolve in a clone of this repo. They are kept rather
> than stripped because a claim that names its source is evidence of where it came from even when
> the source is not public, and removing them would leave the assertions here with no provenance
> at all.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped.

## In flight

Nothing is part-done and nothing of mine is uncommitted. Both repos are level with origin.

**Verify HEAD's CI yourself rather than trusting a line here.** A handover cannot state its own
commit's CI result: writing the line creates the commit the line describes. What IS settled is one
commit back - `52b5ab6` returned `CI=success CodeQL=success`, exit 0, confirmed twice.

**Another session is still writing in the RESEARCH repo.** `landscape/OPENCLAW-2.0.md` and
`workflow/design/probes/always-loaded-cost.md` are staged-and-modified there and are NOT ours.
Commit in that repo BY PATHSPEC only; a bare `git add -A` would ship someone's half-written file
under your message. Every commit this session did so.

## Shipped this session

**Rank 35, closed by measurement.** P4's second executor is the worker the supervisor respawns
after a crash. Arm D (arm B with the respawn waited for as an EVENT, SIGKILLed, and no replacement
asserted) ran interleaved against arm B, D B D B D B, twice.

    arm                              counted  duplicated  clean
    D  respawned worker SIGKILLed       3          0        3
    B  respawned worker left alive      2          2        0

In RESEARCH: `c475a02` (arm D, the step-shape detector, its S5 controls), `7349851` (the SIGKILL
route, restart-vs-race in the verdict, the interleaved runner), `edd8e33` (clear the roster between
runs), `fcfce61` (the note and the uniform-gate tally), `03684ec` + `3d2fd5c` (both corrections
below). In agentdag: `e46bd59` + `52b5ab6` (backlog).

**Four memory facts written** (one updated to recurrence 3, three new) covering the removal-route
confound, gate asymmetry between arms, the quiescence deadlock, and a self-updating tool.

## Decided, do not reopen

1. **Arm D removes the respawn by SIGKILL, never `claude stop`** (user, 2026-09-01). Arm A had
   already measured stop-then-resume as clean, so a graceful stop makes a clean arm D predicted by
   two mechanisms. The confounded first run is kept unread as
   `RESEARCH/workflow/probes/probe_bg_session_p1_p4.p4-isolation-graceful-stop.json`.
2. **A run counts only if the resume carried the leg to an END, and that gate applies to BOTH
   arms.** Applied to one arm it inverts the result: the sweep's own first tally read arm B as 2 of
   3 clean by counting legs that wrote one and two steps.
3. **Three of each arm, interleaved, not blocked** (user, 2026-09-01). A sequential sweep confounds
   the arm with the wall clock.
4. **A third worker VOIDS the arm; it is not killed in turn.** A kill-until-quiet loop changes the
   route again and need never terminate.
5. **`### Partially ships` exists, and its rule is load-bearing.** A row must name the MECHANISM and
   the CONDITION; a row that cannot name both belongs in one of the two lists.
6. **The rank-20 wording was NOT arbitrated.** The user decided on the MEASUREMENT. Do not go back
   and settle which reading was right.
7. **The structural leak rules deliberately skip `/home/` and `/Users/`** (user, 2026-09-01). A
   KNOWN, accepted gap; the option to close it was put and declined. The reason is in
   `tests/test_repo_publishable.py` itself.
8. **The plans' Python fences are committed as ruff format leaves them** (user, 2026-09-01).

## Decided against, so it is not redone as an oversight

- **The P4 probe was NOT made session-scoped.** It takes `worker_pids_before` from the WHOLE roster
  and SIGKILLs every pid in it, which is how arms A, B and C were always measured; changing it
  mid-sweep would have made the new runs incomparable with them. Consequence to respect: while that
  probe runs, do not start a background Claude session on this machine.
- **The claim "nothing a caller does prevents the respawn" was retracted, not softened.** Read at
  source instead: `scheduleRespawn` is capped at 20 attempts with a 10s backoff and is suppressed
  only when the session already settled on disk or an interactive-lineage session was stopped by an
  external signal. Neither covers a `--bg` worker killed mid-turn. No caller-facing switch was found
  by seven guessed tokens NOR by enumerating the settings object, so both documents say "none
  found", not "none exists".
- **The judge was NOT started.** Blocked by C1, not by Checkpoint B.

## Still open, untouched - one line each, detail in OPEN-WORK.md

- Rank 25 USER: score checkpoint C1, the six-pair control packet.
- Rank 30 USER: does P4's resume finding deserve a differentiator row? No longer blocked.
- Rank 40 FOUND: build component 5's judge op and the completion ladder.
- Rank 50 FOUND: 167 unframed memory bodies.
- Rank 60 FOUND: decide the degenerate-dispatch rule.
- Rank 70 FOUND: the ragged-table check's placement in `repo-gate`.
- Rank 75 FOUND: no P4 arm kills the supervisor or reboots the machine.
- Rank 80 FOUND: 3 of 8 P4 resume runs never reached an END, unexplained.
- Rank 85 FOUND: the confound jig exists and was not reached for.

## The exact next action

**`OPEN-WORK.md` rank 25, score checkpoint C1.** Top-ranked, needs the USER, roughly ten minutes,
no code: score six pairs cold from `RESEARCH/workflow/probes/e1_control_packet.md` - a preference
per pair, a 1-5 executability score per plan, one line of why - and only then open
`RESEARCH/workflow/probes/e1_control_key.json`. Agreement on >= 5 of 6, or the panel's other 24
verdicts are DISCARDED, not caveated.

It gates two things, which is why it outranks the build. C2's arms are collected and deliberately
record `"judged": false`. And the planning-loop design makes a judge op a REAL model dispatch
(`judge:<lens>` -> `Coordinator.work`); what keeps that off the decided-by-a-model path is a
stake-free fresh node, lenses COUNTED BY CODE, and a panel whose trustworthiness is MEASURED - and
C1 is that measurement, cited by name. Building rank 40 first builds against an unvalidated
instrument.

If the user declines C1, rank 30 is now the fallback rather than rank 35: it is a USER decision
that costs no dispatches, and the mechanism it was waiting on is measured.

## Files that matter

    OPEN-WORK.md                                                     read before this file
    tests/test_repo_publishable.py                                   the publication guard, and why it skips /home/
    .private-markers                                                 gitignored; the guard is inert without it
    PLANS/build-plan-high.md                                         `### Partially ships`, the new tier
    PLANS/build-plan-mid.md                                          C1 and C2 in detail; component 5
    src/agentdag/composition/kernel.py                               the spec left for whoever builds the judge
    RESEARCH/workflow/probes/e1_control_packet.md                    C1's input
    RESEARCH/workflow/probes/e1_control_key.json                     the sealed key, opened only after scoring
    RESEARCH/workflow/design/probes/bg-session-p1-p4.md              the P4 note, arm D folded in
    RESEARCH/workflow/probes/probe_bg_session_p1_p4.py               the probe; arm D is --stop-respawn-before-resume
    RESEARCH/workflow/probes/run_p4_interleaved.py                   the D/B sweep
    RESEARCH/workflow/probes/analyze_p4_interleaved.py               the tally, one gate for both arms

## How to verify this still stands

    git status --porcelain && git log -1 --oneline                  # clean; HEAD is the handover commit
    .venv/bin/python -m pytest tests/ -q                            # expect 1024 passed
    grep -c '^- \[ \]' OPEN-WORK.md                                 # 9 open
    uv run RESEARCH/workflow/probes/probe_bg_session_p1_p4.py | \
      python3 -c 'import json,sys; print(json.load(sys.stdin)["S5_step_shape_controls"]["all_hold"])'
                                                                    # True; no dispatch, source arms only
    python3 RESEARCH/workflow/probes/analyze_p4_interleaved.py | \
      python3 -c 'import json,sys; print(json.load(sys.stdin)["tally"])'
                                                                    # D 3 counted 0 duplicated, B 2 counted 2 duplicated

`PLANS/`, `OPEN-WORK.md` and this file are TRACKED here. `EXECUTION-USER-REVIEW.md` is a SYMLINK
into the private research repo, so editing it through this path versions it there; it is still
gitignored here.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
