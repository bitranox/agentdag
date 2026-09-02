# Handover: agentdag, 2026-09-02 ~11:10. Agent teams read at source; row 4 narrows. Two commits unpushed.

> **The `RESEARCH/` paths point into a private companion repo.** These documents cite it by
> repo-qualified path for the design documents, probe scripts and measurement notes they were
> derived from. The `RESEARCH/` prefix names that repo; it is deliberately not a relative path,
> because no relative path from here resolves to it. These citations do not resolve in a clone of
> this repo. They are kept rather than stripped because a claim that names its source is evidence
> of where it came from even when the source is not public.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped.

## In flight

**Two commits are UNPUSHED on `main`:** `ad9212c` and `79ca93b`, both documentation and planning
only, no `src/` change. The tree is clean. Nothing is part-done.

Pushing needs `make test` first per this repo's policy even though nothing under `src/` moved, and
that gate MUTATES the working tree (regenerates the Makefile, bumps dependency floors, reformats).
Expect churn that is not yours and commit it rather than carving it out.

**Another session may still be writing in the RESEARCH repo.** As of 11:10 it showed
`OPEN-WORK.md`, `handover.md` and `agentdag-working/EXECUTION-USER-REVIEW.md` modified. The last of
those is ours (this repo's `EXECUTION-USER-REVIEW.md` is a SYMLINK into that repo, so writing it
here versions it there). The other two are NOT. Commit in that repo BY PATHSPEC only.

## Shipped this session

**A six-dimension source-level comparison against Claude Code's Workflow tool and ultracode**, 13
agents, 3.86M subagent tokens, 905 tool uses. 83 falls-short rows against 38 better rows. The
finding that matters is not the ratio: four of six adversarial verifiers independently reported
that the BETTER bucket was probed with weaker instruments, one of them finding a fabricated
quotation in a better row's evidence. The bucket that justifies the project is the weaker-evidenced
half. Raw material is in the workflow journal under this session's `subagents/workflows/` directory
and dies with the session; nothing was written to `RESEARCH/`.

**Agent teams read at source** (CLI 2.1.258, byte-run extraction, version-string control at
176,743,511). It was never an arm in D2 nor in the 2026-08-20 elimination. It moved three
differentiator rows; the detail is in `PLANS/build-plan-high.md` under the agent-teams block and
risks 6 to 8.

**Two commits**, both plan and doc corrections. `ad9212c` corrected three claims measurement
falsified; `79ca93b` folded in the agent-teams read plus the decision-review actions.

**One memory fact:** `feedback-a-caveat-binds-only-inside-the-unit-it-qualifies`.

## Decided this session, with the reason

1. **The differentiator table gains an `eliminated against` column** (mine). The one-surface caveat
   had sat in prose above the table since 2026-08-30 and did not bind: six readers with the file
   open reproduced the error it warns about across 169 rows. A cell in the row is not skippable the
   way a paragraph is.
2. **The "one crash test and one real run" figure is WITHDRAWN, not replaced** (mine). No basis for
   a new number, and that page tiers provenance so a guess cannot be quoted later as a figure.
3. **Provenance is per row in the safety chain, and four of five links were re-verified here rather
   than labelled** (user chose tiering; the verification was the cheaper honest option once found).
4. **The safety chain moved 45 -> 38 by correcting its SIZE, not by adding an importance axis**
   (user, 2026-09-02). The first filing undersold the boundary decision as a guard fix. Sized
   honestly it outranks the judge op on the existing origin-then-size rule. The alternative -
   a third ranking key - was put and declined.
5. **`docs/` corrections state what IS; the dated correction narrative went into the plans.** Docs
   describe current code; the plans are where this project records how a claim moved.

## Decided against, so it is not redone as an oversight

- **The two `src/` truth fixes were NOT applied** (`executor_claude.py:345`'s "non-default"
  docstring, `60-kernel.toml:102-104`'s unfireable-backstop comment). They belong with the safety
  work at backlog 38, and touching `src/` pulls the bmk gate into a documentation commit. Cost
  accepted: two false statements stay in the tree until 38 is worked. This is the FIRST item of
  38's `next:` field.
- **The agent-teams findings were NOT re-verified before committing.** Surfaced by me in the
  decision review and carried to backlog 36 rather than fixed, because context had reached the
  handover threshold. Do not read the row-4 narrowing as settled.
- **Nothing was pushed**, and no `make test` was run.

## Still open, untouched - one line each, detail in OPEN-WORK.md

- Rank 15 USER: adopt or steer Claude Code's agent teams? Evidence in; the decision is the user's.
- Rank 25 USER: score checkpoint C1, the six-pair control packet.
- Rank 30 USER: does P4's resume finding deserve a differentiator row?
- Rank 36 FOUND: verify the agent-teams row-4 narrowing. Gates rank 15.
- Rank 38 FOUND: the unattended-safety chain, five composing defects.
- Rank 40 FOUND: build component 5's judge op and the completion ladder.
- Rank 50 FOUND: 167 unframed memory bodies.
- Rank 55 FOUND: nothing bounds an unattended run.
- Rank 60 FOUND: decide the degenerate-dispatch rule.
- Rank 65 FOUND: three scheduler defects that make `--parallel` mean less than it says.
- Rank 70 FOUND: the ragged-table check's placement in `repo-gate`.
- Rank 75 FOUND: no P4 arm kills the supervisor or reboots the machine.
- Rank 77 FOUND: an unattended run cannot be watched, listed or costed.
- Rank 80 FOUND: 3 of 8 P4 resume runs never reached an END, unexplained.
- Rank 85 FOUND: the confound jig exists and was not reached for.
- Rank 87 FOUND: no verb for the edit-and-re-run loop; a config-fix resume is served a stale failure.

## The exact next action

**`OPEN-WORK.md` rank 36, verify the agent-teams row-4 narrowing** - about ten minutes, and it goes
before the two USER items above it deliberately. Rank 15 is the top-ranked open item and its whole
trade rests on this claim; putting an unverified finding to the user as the basis for a scope
decision is the failure this repo has recorded twice. Rank 25 does not depend on it and stays
available if the user would rather spend the ten minutes there.

**This ordering is contested and the user may simply reverse it.** Putting 36 first overrides this
backlog's own first rule - a USER item outranks every FOUND item, and no count changes that - on a
GATING argument. The same argument put 36 above 38. There is precedent (rank 35 was reranked that
way on 2026-09-01), but the user had just declined an importance axis in favour of honest sizing,
and this is that axis under another name. If the ordering is wrong, the fix is one edit and the
next action becomes rank 15 or 25.

In the 2.1.258 binary, confirm three things. Extract printable byte-runs with a Python regex over
the raw bytes and prove the method on a control first; `strings` lands in the atom table and finds
the token without the code.

1. Inside `TaskUpdate.call`, the `TaskCompleted` hooks run BEFORE the status assignment and a block
   returns `success:false`. **If this is wrong, row 4 does not narrow and rank 15's trade changes.**
2. The teammate turn-end dispatch fires per in-progress task the teammate owns.
3. `exitTwoMeansMissingScript` downgrades exit-2 to non-blocking for `TaskCompleted`, which is the
   fail-open half that keeps row 4 alive in narrowed form.

Then push the two commits and watch CI.

## Files that matter

    OPEN-WORK.md                                          read before this file
    PLANS/build-plan-high.md                              the differentiator table, its new `eliminated against`
                                                          column, the agent-teams block, risks 6 to 8
    PLANS/build-plan-mid.md                               D2's scope line; the unattended-safety chain with
                                                          per-row provenance
    docs/safety-and-sandbox.md                            section 7, rewritten to what is true
    docs/execution-model.md                               section 8, split from-Python vs from-a-plan
    src/agentdag/adapters/kernel/executor_claude.py:345   the false "non-default" docstring, backlog 38
    src/agentdag/adapters/config/defaultconfig.d/60-kernel.toml:102-104
                                                          the unfireable-backstop comment, backlog 38
    src/agentdag/application/kernel/context.py:562        the budget fail-open, backlog 55
    EXECUTION-USER-REVIEW.md                              symlink into the private repo; this session's
                                                          decisions are the newest entry

## How to verify this still stands

    git status --porcelain && git log --oneline @{u}..HEAD   # clean; two commits unpushed
    grep -c '^- \[ \]' OPEN-WORK.md                          # 16 open
    .venv/bin/python -m pytest tests/ -q                     # no src/ change this session
    .venv/bin/python -c "from agentdag.composition.kernel import build_op_registry; \
      print(sorted(build_op_registry().names()))"
    # ['approve', 'gate:make-test', 'plan', 'reduce:count', 'scan', 'work'] - the set docs/execution-model
    # section 8 now splits from the Coordinator primitives

`PLANS/`, `OPEN-WORK.md` and this file are TRACKED here - verified 2026-09-02 with
`git ls-files --error-unmatch`, so an overwritten handover is recoverable from git.
`EXECUTION-USER-REVIEW.md` is a SYMLINK into the private research repo and is gitignored here.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
