# Handover: agentdag, 2026-09-01 ~13:55. Two user decisions closed. The macos CI block cleared.

> **The `RESEARCH/` paths point into a private companion repo.** These documents were written
> beside a private research repository and cite it by relative path for the design documents,
> probe scripts and measurement notes they were derived from. Those paths do not resolve in a
> clone of this repo. They are kept rather than stripped because a claim that names its source
> is evidence of where it came from even when the source is not public, and removing them would
> leave the assertions here with no provenance at all.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped.

## In flight

Nothing is part-done here and nothing is uncommitted. Work tree clean and `main` level with origin;
HEAD is the commit that wrote this file. Two live conditions the next session inherits:

**The macos CI block cleared; verify HEAD yourself rather than trusting this line.** A handover
cannot state its own commit's CI result, because writing the line is what creates the commit the
line is about - so run `ci_wait --sha $(git rev-parse HEAD)`. What IS settled: `8f18568` came back
`CI=success CodeQL=success`, `CI_RC=0`, with the macos cells running 28 steps each. For about four
hours this morning they sat at `status=queued, steps=0` across five
stacked runs, every run 9 of 12 green with ZERO failures, and three `ci_wait` calls returned
`CI_RC=2` (could not tell), never 1. It cleared on its own, which confirms the read taken at the
time: the block was external, so cancelling runs to "free capacity" would have gained nothing and
destroyed signal. Worth keeping only as the diagnosis: `steps=0` means a cell never executed a
line, and the FIRST run that morning had no competition and still never started, so a queue you
made yourself is not evidence you caused the wait.

**Another session is writing in the RESEARCH repo.** `landscape/OPENCLAW-2.0.md`, untracked-then-
staged, half-written (231 lines staged, 282 insertions unstaged on top). It is not ours. Commit
there BY PATHSPEC only; a bare `git add -A` would ship someone's half-written file under your
message.

## Shipped this session

**Rank 10, the three working files** (`25d6f8f`, `d011f1f`, `57f5ca2`, `976435c`). `PLANS/` and this
file are TRACKED here now; `EXECUTION-USER-REVIEW.md` is a SYMLINK to its one real home in the
private repo and is NOT published, because its spend figures are the SUBJECT of their own sentences
so redaction would destroy the entries rather than clean them. A publication guard
(`tests/test_repo_publishable.py`) now enforces what the read-through did by hand, and found three
leaks older than itself.

**Rank 20, the token-budget cut** (`21ef31c`). A third tier, `### Partially ships`, in
`PLANS/build-plan-high.md`. Three entries moved there.

**Rank 30, held not decided** (`a45c9ac`), plus the rerank that followed.

**C1 added to the backlog and component 5 corrected** (`b73394e`, `a45e871`).

**In RESEARCH** (`2488991`, `5a2c625`): the planning-loop design's decision-4 rule and op table now
describe the shipped mechanism instead of the retired `can_change_state` flag.

## Decided, do not reopen

1. **`### Partially ships` exists, and its rule is load-bearing.** A row must name the MECHANISM and
   the CONDITION; a row that cannot name both belongs in one of the two lists. Without that rule a
   middle tier is where a claim goes to avoid being decided.
2. **The rank-20 wording was NOT arbitrated.** The pre-registered trigger and the backlog recorded
   one criterion two opposite ways and the result fired both. The user decided on the MEASUREMENT
   instead. Do not go back and settle which reading was right; that is the post-hoc
   re-interpretation the pre-registration existed to prevent.
3. **No differentiator row for P4's non-idempotent resume, pending the mechanism.** The two
   candidates point opposite ways, so the row is not written on an inference.
4. **The macos queue is not to be cleared by cancelling runs.** See "In flight" for the measurement.
5. **The structural leak rules deliberately skip `/home/` and `/Users/`.** A KNOWN, accepted gap
   (user, 2026-09-01): the private-name list covers the real case on the local gate. Do NOT close it
   by widening the pattern and allow-listing the placeholder lines - that option was put and
   declined. The reason is in the test file itself.
6. **The plans' Python fences are committed as ruff format leaves them** (user, 2026-09-01). A
   tracked file not already in canonical form re-dirties after every gate run.

## Decided against, so it is not redone as an oversight

- **The judge was NOT started.** Not blocked by Checkpoint B, which names component 5 as the
  unblocker - blocked by C1, see the next action.
- **The RESEARCH design doc's dated filename did not make it immutable.** `git log` showed three
  amendments including a correction commit, so it was corrected. Do not re-open that as a style
  question.

## Still open, untouched - one line each, detail in OPEN-WORK.md

- Rank 25 USER: score checkpoint C1, the six-pair control packet.
- Rank 30 USER: does P4's resume finding deserve a differentiator row? Held on rank 35.
- Rank 35 FOUND: which two executors ran concurrently in P4's arm B.
- Rank 40 FOUND: build component 5's judge op and the completion ladder.
- Rank 50 FOUND: 167 unframed memory bodies.
- Rank 60 FOUND: decide the degenerate-dispatch rule.
- Rank 70 FOUND: the ragged-table check's placement in `repo-gate`.
- Rank 75 FOUND: no P4 arm kills the supervisor or reboots the machine.

## The exact next action

**`OPEN-WORK.md` rank 25, score checkpoint C1.** It is the top-ranked open item and it needs the
USER, roughly ten minutes, no code: score six pairs cold from
`../RESEARCH/workflow/probes/e1_control_packet.md` - a preference per pair, a 1-5 executability
score per plan, one line of why - and only then open
`../RESEARCH/workflow/probes/e1_control_key.json`. Agreement on >= 5 of 6, or the panel's other 24
verdicts are DISCARDED, not caveated.

It gates two things, which is why it outranks the build. C2's arms are collected and deliberately
record `"judged": false`. And the planning-loop design makes a judge op a REAL model dispatch
(`judge:<lens>` -> `Coordinator.work`); what keeps that off the decided-by-a-model path is a
stake-free fresh node, lenses COUNTED BY CODE, and a panel whose trustworthiness is MEASURED - and
C1 is that measurement, cited by name. Building rank 40 first builds against an unvalidated
instrument.

If the user declines C1, rank 35 is the fallback: one probe run, re-running P4 arm B with the
respawned worker stopped first so exactly one executor remains. It SPENDS live dispatches, so it
needs their go.

## Files that matter

    OPEN-WORK.md                                                    read before this file
    tests/test_repo_publishable.py                                  the publication guard, and why it skips /home/
    .private-markers                                                gitignored; the guard is inert without it
    PLANS/build-plan-high.md                                        `### Partially ships`, the new tier
    PLANS/build-plan-detailed.md                                    P1-P4 block; the P3 and P4 entries carry the decisions
    PLANS/build-plan-mid.md                                         component 5, corrected
    src/agentdag/composition/kernel.py                              the spec left for whoever builds the judge
    ../RESEARCH/workflow/design/2026-08-28-planning-loop-design.md  how a judge produces a verdict
    ../RESEARCH/workflow/probes/e1_control_packet.md                C1's input

## How to verify this still stands

    git -C . status --porcelain && git -C . log -1 --oneline       # clean; HEAD is the handover commit itself
    .venv/bin/python -m pytest tests/ -q                          # 1024 passed
    .venv/bin/python -m pytest tests/test_repo_publishable.py -q  # 4 passed, guard live
    grep -c '^- \[ \]' OPEN-WORK.md                               # 8 open
    grep -n 'Partially ships' PLANS/build-plan-high.md            # the tier exists
    gh run list --limit 3 --json headSha,status,conclusion        # expect CI + CodeQL success

`PLANS/`, `OPEN-WORK.md` and this file are TRACKED here. `EXECUTION-USER-REVIEW.md` is a SYMLINK
into the private research repo, so editing it through this path versions it there; it is still
gitignored here. Nothing is a hand-refreshed copy any more, which is what used to go stale.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
