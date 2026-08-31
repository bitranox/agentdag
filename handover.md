# Handover: agentdag, 2026-09-01 ~00:10. Two things shipped, CI green. Nothing is owed.

> **The `RESEARCH/` paths point into a private companion repo.** These documents were written
> beside a private research repository and cite it by relative path for the design documents,
> probe scripts and measurement notes they were derived from. Those paths do not resolve in a
> clone of this repo. They are kept rather than stripped because a claim that names its source
> is evidence of where it came from even when the source is not public, and removing them would
> leave the assertions here with no provenance at all.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped.

## CI

Green on `ef59f7f`, which contains BOTH of this session's commits: `CodeQL=success CI=success`,
CI_RC=0. The local gate was green before the push too (`make test`, GATE_RC=0 read from its own
log, not from a pipe).

## In flight

Nothing is part-done and nothing is uncommitted. The work tree is clean and `main` is level with
origin.

## Shipped this session

**Probes P1 and P4** (in RESEARCH, `66ac34b` and `7391f6f`, pushed), completing the plan's P1-P4
block - all four boxes now ticked.

- **P1: not restored.** A background session outlives its terminal and genuinely progresses. The
  supervisor is a session leader in its own session and process group with no controlling terminal,
  re-parented to init, hosting the worker as its own child; the dispatching shell's pid survives
  only as a label in its argv. Six model turns ran after the shell was gone. It exits ~5s after the
  last session is removed.
- **P4: the documented sentence did not reproduce on any route.** Graceful stop plus resume: the
  in-flight subagent CONTINUES mid-task in the same dispatch, and the completed one is never
  re-executed. Crash with no resumer: the supervisor respawns the worker (`attempt: 2`) but the
  work HALTS. Crash plus resume: the in-flight unit ran TWICE concurrently, 22 steps and two ENDs,
  25 distinct tool_use ids interleaved in ONE subagent transcript. So resume is FINER-grained than
  documented, and the new finding is that resuming after a crash is neither exclusive nor
  idempotent.

**Component 5, step 1: decision 4's validator rule** (`c7ed9a0`, pushed). `can_change_state` is
retired. Each op declares the record it would carry in a run that accomplished nothing - gate
`{"rc": 0}`, scan `{"stray": 0}`, reduce:count `{"count": 0}`, while `work`, `approve` and `plan`
declare `None` because running one IS the accomplishment - and the rule settles `done_when` over
those records with the SHIPPED evaluator. The judge itself is NOT built; that is `OPEN-WORK.md`
rank 40.

**Rank 10: the three working files, settled.** `PLANS/` and this file are now TRACKED here, after a
read-through found 49 unpublishable lines in 10,091 and redacted the 35 that are in the plans (build
hosts, absolute paths into the local tree, a borrowed venv, a private sibling project, the fleet key
filename). `EXECUTION-USER-REVIEW.md` is NOT published: its 14 spend figures are the subject of their
own sentences, so redacting them destroys the entries. It became a symlink to the private repo
instead, which solves the staleness without publishing anything.

**A publication guard, and the three pre-existing leaks it found** (`d011f1f`, CI green). The
no-leak obligation on `PLANS/` and `handover.md` is now enforced by `tests/test_repo_publishable.py`
rather than by whoever remembers. Structural rules ship; the private-name list cannot, because a
shipped blocklist of hostnames publishes them, so it loads from a gitignored `.private-markers`
that binds the local gate. It failed on first run against defects older than itself: the mount path
in two graph_a tests, a build hostname in four docstrings, a private sibling project in two shipped
JSON schemas plus a docstring and a comment. All prose, no API change.

## Decided, do not reopen

1. **Decision 4 is decided by running the real evaluator over synthetic worst-case records**, not by
   re-walking the condition grammar. The user chose this over a per-field value beside the old flag.
   A second hand-rolled walk is what allowed the defect, and the validator now cannot drift from the
   semantics it guards.
2. **A declared no-work record must cover its op's whole output contract**, enforced at registration.
   A field left out is absent from the synthesized record, so its comparison goes undecided instead
   of True and the rule silently stops guarding it.
3. **One shipped test REVERSED, with the user's explicit decision on it.**
   `test_root_done_when_whose_only_state_change_is_negated` now expects `Accepted`. Its old ground
   ("`Not(w)` holds while the work entry never runs") is false: with no work record the condition
   evaluates `None`, and `execute.py` completes only on `True`. Do not "restore" it.
4. **`work` declares `None`, not a zeroed record.** The zeroed version was tried and backed out: it
   made `work.status == "done"` refusable and broke 12 root tests on the shipped planner path.
5. **Three P4 arms, not one.** Arm B alone reads as "a crash duplicates work" and does not show
   that; the roster showed the supervisor had respawned the worker while the resume also ran.
6. **Probe sessions are pinned to sonnet at low effort**, and no VARIADIC option ever precedes the
   prompt: `--allowedTools <tools...>` swallowed it as a tool name, starting a session with nothing
   submitted - which looks exactly like a session that ran and did no work.

## Decided against, so it is not redone as an oversight

- **The degenerate `turns == 0` case was NOT folded into decision 4.** That shape can still settle a
  root plan on a `work` node that ran and reported zero turns. It is a real gap, but the node DID
  run, so it is not decision 4's never-started question and wants its own rule. `OPEN-WORK.md`
  rank 60.
- **Arm B's mechanism was not chased to ground.** Which two executors ran is inferred, labelled as
  such in the note. Rank 80.
- **No P4 arm kills the SUPERVISOR or reboots the machine**, which the docs say ends sessions
  outright. Stated as a limitation in the note rather than left to be discovered.

## Still open, untouched - one line each, detail in OPEN-WORK.md

- Rank 20 USER: does P3 restore the cut item? Asked twice, unanswered.
- Rank 30 USER: does the non-idempotent-resume finding deserve a differentiator row?
- Rank 40 FOUND: build the judge op and the completion ladder.
- Rank 50 FOUND: 167 unframed memory bodies.
- Rank 60 FOUND: the degenerate-dispatch rule.
- Rank 70 FOUND: the ragged-table check's placement in `repo-gate`.
- Rank 80 FOUND: which two executors ran in P4's arm B.

## The exact next action

**`OPEN-WORK.md` rank 40, the judge op and the completion ladder.** Ranks 20 and 30 outrank it but
both are blocked on the user and have been asked three times; they cannot be worked, only answered.
Rank 40 is what this session's decision-4 validator leads into: the validator half shipped, the
judge itself is untouched. Set its `facts_if_no_work` by READING the emitter it writes, because for
a judge, what a verdict reads on a run that achieved nothing IS the question.

## Files that matter

    OPEN-WORK.md                                                    read before this file
    PLANS/build-plan-detailed.md                                    P1-P4 block, all four ticked
    src/agentdag/application/kernel/plan_validate.py                the rewritten decision-4 rule
    src/agentdag/application/kernel/registry.py                     OpSpec.facts_if_no_work
    src/agentdag/composition/kernel.py                              the six declarations
    ../RESEARCH/workflow/design/probes/bg-session-p1-p4.md          the P1/P4 note
    ../RESEARCH/workflow/probes/probe_bg_session_p1_p4.py           re-runnable, four arms

## How to verify this still stands

    git -C . log -1 --format=%h && git -C . status --porcelain    # ef59f7f, clean, level with origin
    readlink EXECUTION-USER-REVIEW.md                              # points into ../RESEARCH, not a copy
    git ls-files PLANS handover.md | wc -l                         # 4, tracked here now
    grep -c "^- \[ \] \*\*P[1-4]" PLANS/build-plan-detailed.md     # 0, all four ticked
    .venv/bin/python -m pytest tests/ -q                           # 1021 passed

`PLANS/`, `OPEN-WORK.md` and this file are TRACKED here as of 2026-09-01.
`EXECUTION-USER-REVIEW.md` is a SYMLINK to its one real home in the private research repo, so
editing it through this path versions it there; it is still gitignored here. Nothing is a
hand-refreshed copy any more, which is what used to go stale.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
