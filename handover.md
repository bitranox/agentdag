# Handover: agentdag, 2026-09-03 ~11:35. The `spec` round is set up and NOT yet run.

> **The `RESEARCH/` paths point into a private companion repo.** These documents cite it by
> repo-qualified path for the design documents, probe scripts and measurement notes they were
> derived from. The `RESEARCH/` prefix names that repo; it is deliberately not a relative path,
> because no relative path from here resolves to it. These citations do not resolve in a clone of
> this repo. They are kept rather than stripped because a claim that names its source is evidence
> of where it came from even when the source is not public.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped.

## The next action, and the reason this handover exists

**`OPEN-WORK.md` rank 04: run both arms of the `spec` round, score them, write the results under
the divider in `docs/probes/2026-09-03-spec-round1.md`.**

The pre-registration is written and COMMITTED. **Nothing above its divider may be edited** - a
counted run has begun under it. Read the six pre-registered bands BEFORE reading any number: two of
them say the round is mis-calibrated, and the previous round landed in exactly one of those.

Everything needed is tracked. Nothing depends on the dead session's scratchpad:

    scripts/eval_run_agentdag.py            arm A   (GOAL_FILE RUNS_DIR CEILING DEADLINE_S LOG)
    scripts/eval_run_single_agent.py        arm B   (TASK_FILE WORKSPACE CEILING DEADLINE_S GATE_CMD)
    scripts/runsum.py                       read a finished run's phases and per-node cost
    docs/probes/2026-09-03-spec-round1.md   the pre-registration
    docs/probes/2026-09-02-defects-round1.md  the previous round, for the reporting form

Setup, in order:

1. Seed from agentswarm at commit `071f36c`: `cases.copy_seed(cases.load("spec"), <dir>)`. Re-prove
   the floor is 0/81 on THAT copy, not merely in the agentswarm repo.
2. Build one goal text used by BOTH arms: a step-0 `cp -a <seed>/. .` line, then `case.task`
   verbatim. `plan-goal` mints its own empty `wt/root` and cannot be handed a pre-seeded directory,
   so the copy is a deviation applied to both arms rather than to one.
3. Hold 1,200,000 new tokens and 2400 s on both. Run them SEQUENTIALLY - latency is a scored axis
   and concurrent arms would share this machine.
4. Score with `cases.score`. For quality use
   `quality.py <workspace> --against cases/spec/hidden/reference`. **This differs from the
   `defects` round on purpose**: `spec`'s seed is stubs, so scoring against it measures the absence
   of code; the protocol says the control for a stubs case is the reference or another run.
5. Raw artifacts into `docs/evidence/2026-09-03-spec-round1/`. **Redact before committing** -
   absolute paths, and the operator's OS username, which agentdag writes into every `run_started`
   line as `by` (backlog 06). The repo's own `test_no_tracked_file_carries_a_private_name` catches
   both; it caught both last time and it is the reason the gate went red twice.

Arm A was started and stopped twice, deliberately: once to raise `parallel`, once to hand over
rather than orphan a run under a ceiling this session was holding. **No results exist.**

## In flight

**Nothing is part-done.** No half-written file, no background job of mine still running, and no
coordinator survived - verified with `procsig.py --cmdline 'run start plan-goal'`, not a bare
`pgrep` (a bare one self-matches; the guard blocked me for exactly that earlier).

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index, from a session before last.

## Committed, or not

    git status --porcelain          # only the foreign CLAUDE.md.bak entry
    git log --oneline @{u}..HEAD    # empty, once this handover's own commit is pushed

CI is GREEN on `5317023`, `b43cecd` and `beab496`, each read from its own watcher log. `285a456`
was pushed with a watch armed and had not reported when this was written; check it with
`ci_wait.py --sha $(git rev-parse HEAD)`.

## Shipped this session

- `7a886d8` the turn ceiling ends `NEEDS_CONTINUATION` with `turns_exhausted` typed and the tree
  kept, not a TRANSIENT `EXECUTOR_ERROR` that sent the retry path back into the same wall; and
  `wire_kernel` refuses a policy table offering a row nothing wires. Both were load-bearing for the
  `defects` round - two nodes continued where they would previously have died.
- `f390c73` the `defects` round: probe, evidence, backlog 06.
- `5317023` **the token unit.** `charged_total` = input + cache_creation + output, cache reads
  excluded. A node declaring no budget takes `kernel.default_node_tokens` (300,000) instead of
  being exempt. `max_turns` 25 to 2000; `tokens_per_row` rescaled; `deadline_ceiling_s` to 86,400.
- `b43cecd` restored the row ceilings' cost shape, which my rescale had flattened.
- `beab496` `parallel` 2 to 8, plus the `spec` pre-registration.
- `285a456` the two eval runners, tracked so a round outlives a session.

## The two findings that outlive any round

**agentdag's ceilings were denominated in a unit that grows with conversation length.**
`charged_tokens` was `input_total + output`, and `input_total` includes cache reads: measured
1,132,340 charged against 66,665 of new context on one real node, 17.0x. `tokens_by_row` sums that,
so every cap bound conversation length rather than work. Fixed in `5317023`. **Records written
before that commit are not comparable with later ones on that field.**

**A green visible gate is not evidence of work, and the CONTROL arm proved it.** On `defects` the
single agent, left to judge itself, stopped after one turn at 17 percent of budget having changed
NOTHING - 0/20, zero lines - with the seeded suite green, because that suite passes on defective
code by design. Re-prompted while budget remained, the same agent reached 19/20. The entire
distance between 0 and 19 was the stopping rule. That is the strongest evidence this project has
for its own thesis, and it came off the arm meant to refute it.

## The `defects` round's verdict, so it is not misquoted

**MIS-CALIBRATED.** Both arms saturated - agentdag 20/20, single agent 19/20 - so the round does
NOT show agentdag beating a single agent. Correctness and cost per point were not separated (one
hidden test; 3 percent). Latency was the only axis that separated them and agentdag lost, 2.23x
wall clock for 1.02x tokens. Quality separated nothing and said so itself: the scorer read the same
untouched seed as 4.41 and 5.09 across two runs, six times the gap between the arms.

## Decided this session, with the reason

1. **Planner reads are ALLOWLISTED** to its node dir and cwd, and a confined node gets no Bash
   (user), over deny-Bash-only - a shell command's read set is not decidable from its text.
2. **The charge includes OUTPUT** (user), diverging from agentswarm's protocol deliberately:
   agentdag reads the terminal cumulative usage, so the protocol's reason for excluding output does
   not apply, and the policy prices output at 5x input.
3. **The row ceilings keep their cost shape on a rescale** (user) - I had flattened them.
4. **`parallel` raised to 8** (user), after it quartered a four-wide and then a seven-wide fan-out.
5. **The `defects` result is labelled BEST CASE** (user) - I chose a maximally decomposable task
   and said so before the numbers landed.

## Corrections made, so they are not re-derived

- I misread a gate's exit status off a background job twice. The guard shipped as bitranox-skills
  6.0.0 exists because of it, and it BLOCKED me later in the same session on the same shape.
- I claimed the runaway `find /` demonstrated backlog 65's deadline defect. It did not. **That
  defect is still UNTESTED.**
- My first rank-05 fixture failed its own premise: the task was not too big for one context, and I
  had not checked the size claim before building it. The agentswarm cases replaced it entirely.
- I flattened the policy row ceilings unasked; restored in `b43cecd`.
- I reported a gate green before reading its RC, twice, and the log said `RC=2` both times.

## How to verify this still stands

    git status --porcelain                     # only the foreign CLAUDE.md.bak entry
    grep -c '^- \[ \]' OPEN-WORK.md            # 20 open
    env -u VIRTUAL_ENV make test               # read RC from the LOG, never a job exit code
    .venv/bin/python -m pytest tests/ -q       # 1034 passed at 285a456

`PLANS/`, `OPEN-WORK.md` and this file are TRACKED here, so an overwritten handover is recoverable
from git. `EXECUTION-USER-REVIEW.md` is a SYMLINK into the private research repo and is gitignored.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
