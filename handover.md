# Handover: agentdag, 2026-09-03 ~14:35. Round 1 is VOID and round 2 is pre-registered, not built.

> **The `RESEARCH/` paths point into a private companion repo.** These documents cite it by
> repo-qualified path for the design documents, probe scripts and measurement notes they were
> derived from. The `RESEARCH/` prefix names that repo; it is deliberately not a relative path,
> because no relative path from here resolves to it. These citations do not resolve in a clone of
> this repo. They are kept rather than stripped because a claim that names its source is evidence
> of where it came from even when the source is not public.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where this session stopped.

## The next action

**`OPEN-WORK.md` rank 04, and it is the top-ranked open item.** Build the checkpoint scorer the
round 2 pre-registration requires, prove its discrimination control, then run the three arms.

The pre-registration is at `docs/probes/2026-09-03-spec-round2.md` and **nothing above its divider
may be edited once a counted run begins** - none has begun, so it is still amendable until then.
It makes the instrument's validity a PRECONDITION, not a nicety:

1. Add a checkpoint score to both runners - `cases.score` after each prompt for the control,
   after each node lands for the two agentdag arms - recording tokens and elapsed time at the
   FIRST result reading 81/81, and stopping that arm there.
2. Before any arm runs, show the checkpoint scorer discriminates: 0/81 on the untouched seed and
   81/81 on `hidden/reference`. A scorer that cannot tell those apart cannot detect a crossing.
3. Run P (agentdag pinned to sonnet), S (agentdag as shipped), C (one sonnet agent), sequentially.

Arm P's policy is CONSTRUCTIBLE - verified this session, not assumed. A table with every role on
the sonnet row and haiku/opus/fable set `available: false` passes
`_refuse_a_policy_offering_an_unwired_executor` (which keys on the EXECUTOR string, and all four
Claude rows share the wired `claude` executor) and resolves every tier role to sonnet. The shipped
table resolves `mechanical->haiku standard->sonnet deep->opus top->fable`.

Seed each arm with `cases.copy_seed(cases.load("spec"), <dir>)` from a checkout of `agentswarm`,
re-prove the floor 0/81 on THAT copy, and **delete the `__pycache__` the floor run leaves inside
it** - `copy_seed` excludes bytecode on purpose and scoring puts it back, so a seed handed on
unswept carries stale bytecode into every arm. Put the run root OUTSIDE every git work tree:
`CredentialCopy` writes a copy of the operator's credentials into every node's home.

## In flight

**Nothing is part-done.** No half-written file, no background job of mine still running, no
coordinator alive. Round 1's arms both terminated and were scored.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index. It is now `OPEN-WORK.md` rank 72 with the
reason it must not be cleared from here; keep an explicit pathspec on every commit until it is.

## Committed, or not

    git status --porcelain          # only the foreign CLAUDE.md.bak entry
    git log --oneline @{u}..HEAD    # empty, once this handover's own commit is pushed

CI is green on `2b1f770`, `431a0d3`, `a126f67` and `23d90fd`, each read from its own watcher log.

## Decided this session, with the reason

1. **Round 1 is VOID, not repaired** (user). Three independent reasons, and the third is why a
   re-run was not the answer: arm A crashed on a defect since fixed; the held-fixed model-tier row
   was FALSE when written and no re-run repairs that; and the control SATURATED.
2. **Round 2 is THREE arms** (user) - pinned, shipped, control. Round 1 fused decomposition with
   model tier and could not tell them apart. Pinned-vs-control isolates the thesis;
   shipped-vs-pinned prices the tier policy.
3. **Round 2 measures cost to first crossing of 81/81, not score** (user), with each arm stopping
   at saturation. The case saturates, so score cannot separate anything; this keeps the case and
   changes the quantity, which avoids choosing a ceiling that decides where the answer lands.
4. **Round 2's wall-clock ceiling is 3600 s, not round 1's 2400 s** (user). Under cost-to-saturation
   the deadline bounds only an arm that never crosses, and round 1's control ran 2637 s with its
   crossing point unknown, so 2400 s could have stopped an arm short of a crossing it would
   have made.
5. **Round 1's control is NOT carried forward.** Its 760,578 tokens is the cost to its DEADLINE,
   not to its crossing, and its workspace is not a repository, so nothing can recover when it
   first reached 81/81.

## Decided against, so it is not redone

* **Re-running arm A alone.** It repairs the crash and leaves the false tier row published.
* **Lowering the ceiling until the control falls short.** It needs a calibration run and the
  ceiling would be one I chose, which is choosing where the answer lands - the objection that
  already retired an earlier fixture here.
* **Searching agentswarm for a harder case.** `defects` and `spec` have both saturated; a search
  may cost several control runs and end where we are.
* **Clearing `CLAUDE.md.bak` from the index.** 26 processes have this repo as their cwd across at
  least two session trees, so it may be live work.

## Still open, untouched

One line each; `OPEN-WORK.md` carries the detail and is the file to read.

* rank 05 - the real comparison on a scratch clone against a single-agent control.
* rank 25 - checkpoint C1, which is holding C2's already-collected arms.
* rank 30 - whether the non-idempotent-resume finding earns a differentiator row.
* rank 06, 07, 38 and below - see the file; none was touched this session.

## How to verify this still stands

    git status --porcelain                     # only the foreign CLAUDE.md.bak entry
    grep -c '^- \[ \]' OPEN-WORK.md            # 21 open
    env -u VIRTUAL_ENV make test               # read the RC from the LOG, never a job exit code
    .venv/bin/python -m pytest tests/ -q       # 1035 passed at the tip

Before pushing a new script here, run `pyright --pythonplatform Windows --pythonpath
.venv/bin/python`: CI type-checks against WINDOWS and a local pyright defaults to Linux, which is
how two commits went out red on 2026-09-03.

`PLANS/`, `OPEN-WORK.md` and this file are TRACKED here, so an overwritten handover is recoverable
from git. `EXECUTION-USER-REVIEW.md` is a SYMLINK into the private research repo and is gitignored.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
