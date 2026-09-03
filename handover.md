# Handover: agentdag, 2026-09-04 ~19:30. Round 2 has two arms of three; arm C is staged and not run.

> **The `RESEARCH/` paths point into a private companion repo.** These documents cite it by
> repo-qualified path for the design documents, probe scripts and measurement notes they were
> derived from. The `RESEARCH/` prefix names that repo; it is deliberately not a relative path,
> because no relative path from here resolves to it. These citations do not resolve in a clone of
> this repo. They are kept rather than stripped because a claim that names its source is evidence
> of where it came from even when the source is not public.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where this session stopped.

## The next action

**`OPEN-WORK.md` rank 04, still the top-ranked open item.** Run arm C, then write the three-way
result. One command, from the repo root:

    bash ~/agentdag-eval/spec-round2/run-armC.sh > ~/agentdag-eval/spec-round2/armC-envelope.json

It takes up to an hour (ceilings 1,200,000 new tokens and 3600 s) and stops itself at the first
81/81. Then write arm C and the outcome-band reading into
`docs/probes/2026-09-03-spec-round2.md` under the divider, beside arms P and S.

**Do NOT run a bmk gate while an arm is in flight.** A gate re-syncs the project `.venv`, and the
coordinator runs out of it for the whole arm. Verify with direct `.venv/bin/python -m ruff/pyright/
pytest` calls instead, and run `make test` once the arm is done.

## In flight

**Nothing is part-done and no process of mine is alive.** Arms P and S both terminated at their own
crossings and are written up and pushed. Arm C has never been started.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index. It is `OPEN-WORK.md` rank 72 with the
reason it must not be cleared from here; keep an explicit pathspec on every commit until it is.

## Committed, or not

    git status --porcelain          # only the foreign CLAUDE.md.bak entry
    git log --oneline @{u}..HEAD    # empty, once this handover's own commit is pushed

CI green on `cd8bd27`, `071b77f` and `6cc4429`, each read from a foreground `ci_wait.py`.

## The results so far

Both arms crossed. Neither reached a ceiling.

| arm | policy                                | new tokens | wall s | nodes | crossing        |
|-----|---------------------------------------|------------|--------|-------|-----------------|
| P   | pinned sonnet                         | 540,736    | 2574   | 9     | 81/81 on node 9 |
| S   | shipped (fable planner, opus workers) | 248,650    | 1475   | 4     | 81/81 on node 4 |
| C   | one sonnet agent                      | not run    |        |       |                 |

Both pre-registered validity requirements are MET and recorded: the scorer discriminates 0/81 on
the seed from 81/81 on the reference, and both arms scored every checkpoint with zero failures on
monotonic series, so `racing_suspected` is false for each.

## Decided this session, with the reason

1. **Arm P STANDS as the decomposition arm** (user). Its pinned planner produced a 2-entry first
   plan where the shipped one produces six, but pinning covers the planner because the
   pre-registration pins the policy to one row, and the plan a pinned planner produces is the
   system's behaviour under that policy rather than an apparatus fault.
2. **The scorer is PINNED for the round** - a `git archive` export of agentswarm at `2f59254` under
   its own venv, at `~/agentdag-eval/spec-round2/scorer` and `scorer-venv`. Three other sessions
   were editing agentswarm, and a gate there re-syncs the venv the scorer runs under; a failed
   checkpoint is recorded rather than fatal, so the damage would have been a MISSED crossing.
   `cases/spec` is identical between `071f36c` and that export, so it carries the pinned case bytes.
3. **Scoring lives in agentswarm** (`cases score CASE WORKSPACE`, committed there as `2f59254`).
   agentdag's interpreter cannot import agentswarm, and a scorer reimplemented here would be a
   different instrument giving different numbers.
4. **Checkpoints are taken on node landings and prompt completions**, not on a timer, because the
   token figure only settles per dispatch - a finer score timeline could not locate a crossing's
   COST any better.

## Decided against, so it is not redone

* **A fourth arm pinning only the worker rows.** It is the arm that would isolate decomposition
  from worker tier, and it was rejected: amending after seeing arm P's shape is choosing the design
  from the data, which is what voided round 1's tier row.
* **Voiding arm P and re-pinning without the planner.** Same reason, harder: it discards a counted
  run because its result was inconvenient.
* **Running arm C alongside arm S.** Wall clock is a measured quantity, so overlapping the arms
  would confound it. Sequential, as pre-registered.

## Two readings I got wrong in flight, both corrected in the note

* A monitor grepping `"event": "result"` matched nothing because the journal now serialises
  compactly, so several "0 nodes landed" readings were the matcher failing, not the arm.
* Arm P's 2-entry plan and 0/81 at forty minutes read as a planner that could not decompose. It
  re-planned and crossed. Both are written into the results section, because the final numbers hide
  both.

## Still open, untouched

One line each; `OPEN-WORK.md` carries the detail and is the file to read.

* rank 05 - the real comparison on a scratch clone against a single-agent control.
* rank 06 - the run journal records the operator's OS username.
* rank 07 - HALF verified this session: the masked-gate block fired on a backgrounded `ci_wait.py`
  and `gate.py` passed, so the mechanism is live; whether its command set contains `make test` is
  still untested.
* rank 25, 30, 38 and below - see the file; none was touched.

## How to verify this still stands

    git status --porcelain                     # only the foreign CLAUDE.md.bak entry
    grep -c '^- \[ \]' OPEN-WORK.md            # 21 open
    env -u VIRTUAL_ENV make test               # read the RC from the LOG, never a job exit code
    ls ~/agentdag-eval/spec-round2             # run-armC.sh present, armC-envelope.json absent

Before pushing a new script here, run `pyright --pythonplatform Windows --pythonpath
.venv/bin/python` AND `--pythonversion 3.12`: CI checks both, and a local default run checks
neither. Two pushes went out red this session on Windows-only test defects.

`PLANS/`, `OPEN-WORK.md` and this file are TRACKED here, so an overwritten handover is recoverable
from git. `EXECUTION-USER-REVIEW.md` is a SYMLINK into the private research repo and is gitignored.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
