# Handover: agentdag, 2026-09-04 ~12:30. The spec round is closed; rank 05 has a benchmark route and a plumbing test that passed.

> **The `RESEARCH/` paths point into a private companion repo.** These documents cite it by
> repo-qualified path for the design documents, probe scripts and measurement notes they were
> derived from. The `RESEARCH/` prefix names that repo; it is deliberately not a relative path,
> because no relative path from here resolves to it. These citations do not resolve in a clone of
> this repo. They are kept rather than stripped because a claim that names its source is evidence
> of where it came from even when the source is not public.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where this session stopped.

## The next action

**`OPEN-WORK.md` rank 05, the top-ranked open item.** Pick the benchmark for the real measurement
from the three live candidates, then run a CONTROL-ONLY calibration arm before any coordinator arm,
with what counts as "the control fails" written down first.

Rank 08 does NOT block that first step: a control-only arm has no coordinator, so the unseeded-
checkpoint defect cannot reach it. Rank 08 must be settled before the COORDINATOR arm runs, or the
score curve charges the coordinator for its own read-confinement.

## In flight

**Nothing is part-done and no process of mine is alive.** The three eval trees are cold and complete.

## Committed, or not

`OPEN-WORK.md` is MODIFIED and uncommitted - rank 05 extended and rank 08 added, this session's
whole reconciliation. Commit it with this file.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index. It is rank 72 with the reason it must not
be cleared from here; keep an explicit pathspec on every commit until it is.

## Decided this session, with the reason

- **The spec round 2 is complete and rank 04 is closed.** Arm C crossed 81/81 at 339,482 new tokens
  and 940 s on its FIRST prompt. The band that fires is "P crosses at higher cost than C" - P spent
  at least 1.59x the control's tokens and 2.74x its wall clock, "at least" because C's single
  checkpoint makes its crossing a coarser upper bound and that bias runs against the conclusion. S
  against C is NOT claimed: its 0.73x margin sits inside that resolution error.
- **The round cannot decide the thesis, and that is its main result.** A control that finishes in one
  prompt refutes the case text's claim for the second round running, and a saturating control means
  a coordinator can at best tie.
- **The task for rank 05 comes from a published benchmark, not the backlog.** All 20 open items were
  sized; only three are large and each fails a different criterion. Detail is in rank 05.
- **The measured quantity is now a SCORE CURVE at a shared ceiling** (final score plus cost to any
  threshold an arm reaches), because an unsaturated benchmark has no crossing. The checkpoint
  apparatus already records this as its readings list.
- **Commits carry NO `Co-Authored-By` trailer**, per the pinned rule, though 16 of the 30 commits
  before this session do. Raised with the user and NOT answered - see below.

## Decided against, and why

- **Commit0** - chosen, set up, then rejected by the user. Apparatus survives with all 16 lite
  libraries sized.
- **ProgramBench for the FIRST test** - chosen, then set aside by the user as too big: its own
  baseline is 6 hours and 1000 steps per instance and this host is under the baseline container spec
  (16 CPUs and 41 GB against 20 and 60). It stays a live candidate for the REAL measurement.
- **SWE-bench Verified and SWE-bench Pro** - ruled out as saturated at 96-97 and about 81 percent.
  Picking either buys back the disease that voided two rounds.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- An unanswered question to the user: the `Co-Authored-By` contradiction between the pinned rule and
  this repo's own history. Both commits this session were made without the trailer.
- Everything else is in the backlog at its own rank.

## Files that matter

- `docs/probes/2026-09-03-spec-round2.md` - the finished three-arm round, all results under the divider.
- `OPEN-WORK.md` - ranks 05 and 08 carry this session's whole state.
- `scripts/eval_run_agentdag.py`, `scripts/eval_run_single_agent.py`, `scripts/eval_checkpoint.py` -
  the runners and the checkpoint scorer, unchanged and working.

**Outside the repo, and not in any git tree** (an agentdag run directory holds credential copies):

- `~/agentdag-eval/spec-round2/` - the pinned scorer and venv every arm has used. Do not rebuild it.
- `~/agentdag-eval/smoke-plumbing/` - the passing plumbing run: `setup.py` builds the seed plus BOTH
  ends of the discrimination control, `run-armC.sh` and `run-armS.sh` are the working arm scripts.
- `~/agentdag-eval/commit0/` and `~/agentdag-eval/programbench/` - the two set-aside benchmarks,
  installed with their task tables extracted.

## How to verify

    git status --porcelain                 # only the foreign CLAUDE.md.bak, once this is committed
    env -u VIRTUAL_ENV make test           # the gate; green at 78d7d15 and ff8fd2c
    cd ~/agentdag-eval/smoke-plumbing && \
      ~/agentdag-eval/spec-round2/scorer-venv/bin/python setup.py   # floor 1/9, ceiling 9/9, PASS

The gate must not run while an eval arm is in flight: it re-syncs the project `.venv` that the
runners execute from.

---

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
