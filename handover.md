# Handover, written 2026-09-12 00:40 CEST

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where this session stopped and what it decided.

## The next action

**Nothing is running, and nothing is uncommitted.** The next action is rank 05, the top open item:
launch the re-run of `database_migration` for the counted coordinator arm. The exact command and
the order of all three problems are on rank 05's own line - run it from there rather than from a
copy here.

Before launching, read addendum 4 of `docs/probes/2026-09-05-slopcodebench-corrected-pair.md`. It
was pre-registered in this session and it RESETS Task 13: the 2026-09-11 `database_migration` run
is discarded, all three problems are run under the new behaviour, and the control is not re-run.

## In flight

Nothing. Four pushed commits, CI and CodeQL green on `5f36183` and `48d0c6a`, both read from a
`ci_wait` verdict. The watch on `ee6c3c9` (docs only) was still running when this was written -
check it with `ci_wait.py --sha ee6c3c95dee173a77fdaf89648ff725903c3be62` before assuming it.

## Committed, or not

Everything is committed and pushed. `git log --oneline cf12356..HEAD` is this session's work.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72), untouched again. Every commit
used a pathspec.

## What this session changed, and why

The user asked whether the results were promising. Reading the coordinator's own run records
answered a different and better question: the arm had been measured with a defect of ours still on
it.

- **The row ceiling was reserved, not spent.** `_run_cap_refusal` refused whenever
  `charged + node_cap` would cross `tokens_per_row`, so the pre-registered 1,200,000 behaved as
  900,000 for any node declaring the shipped 300,000 cap. Checkpoints 3, 4 and 5 of the counted run
  - every checkpoint where the two arms diverge - each stopped there with about 21 percent unspent,
  each refusing a node whose brief was already written. Checkpoint 5's refused successor held a
  handover naming the one-line fix its predecessor had just diagnosed. Fixed in `00d77ec` /
  `01fa4e8`: refuse only a row with nothing left, and narrow an outrunning node's cap to the
  headroom.
- **A spent budget destroyed work.** The token-cap path returned FAILED with `artefact_refs`
  emptied by design. The turn ceiling had already been corrected the same way on 2026-09-02 after
  it killed six work nodes namelessly; the token cap had not. `5f36183` makes a spent budget the
  THIRD reason ORed into the one handover arming decision, so it spends the same measured grace as
  the context ceiling and keeps the worktree. `_on_turn`, `_budget_outcome` and `_Interruptible`
  are retired rather than left beside their replacement.
- **A budget refusal was laundered.** The re-plan ladder read it as "the planner node wrote no
  plan.json" and spent every `max_replans` attempt against a bound that cannot move, then suspended
  on the replan question. `48d0c6a` reports it un-replannable, the same fix rank 06 already made
  for a provider refusal at the same place.
- **`ee6c3c9`** pre-registers all of it as addendum 4, with a falsifier, and files ranks 88 and 89.

## Decided this session, with the reason

Four user decisions, each asked on its own, all recorded in `EXECUTION-USER-REVIEW.md`.

- **Option A on the measurement budget** (user): run `dynamic_config_service_api` next and
  re-decide, over finishing the full pair or stopping. It is the one remaining problem whose signal
  can beat the noise floor, because the control COLLAPSES there (strict 0.329 and 0.556).
- **B + D** (user): fix the reserve AND make budget exhaustion hand over, rather than either alone.
- **On D's shape, "1,2"** (user): both - the node hands over AND a grant-able budget question is
  recorded.
- **Option A on the sequencing** (user), after I corrected my estimate: go to the paid run, file the
  grant as backlog. My original description of the grant as "reusing a shipped pattern verbatim"
  was WRONG - grants are keyed per approve node id and per limit field, so tokens need a 4th root
  question minted in both workflow programs. The half that pays off unattended shipped anyway.

Mine, the ones that outlive the commits:

- **The deadline path was deliberately NOT changed** alongside the budget. A node out of wall-clock
  time has no successor waiting on more of it, and the grace costs the very thing that ran out.
  Filed as rank 89 with the argument on both sides rather than a verdict.
- **`cap_hit` kept its name** on the handover record rather than gaining a `stopped_by_budget`
  synonym: it is already registered as branchable in `composition/kernel.py`, and a second name for
  one fact would need its own registration. Settled by a lookup, not by taste.
- **The proof ran on haiku, not opus.** The handover path is agentdag code reading usage numbers and
  is model-independent; spending opus quota would come out of the queued paid run's window.
- **The superseded reserve test was removed, not adapted**, since the rule it states is gone - but
  its one unique assertion, that a refusal is journalled as a normal started/result pair, was folded
  into the replacement first.
- **Two test defects were fixed rather than worked around**: `instances` is a ClassVar appended to
  and never reset, so a deadline test was reading the budget test's client; and the cap tests used
  the immediate interrupt as a proxy for where a crossing is DETECTED, which is now where it ARMS.

## Decided against, and why

- **The narrow token grant** - reusing the existing approve node so one GRANT raises both
  `max_nodes_per_run` and `tokens_per_row`. Under an hour, and refused: it makes the existing
  question text ("grant the run another whole node budget") a lie and silently raises a ceiling the
  person did not ask about. Written into rank 88 so it is not re-proposed as a shortcut.
- **Debugging `--set kernel.default_node_tokens`**, which could not be shown to take effect during
  the proof. The run ceiling was an already-proven lever for the same purpose. It was never
  isolated, so this is NOT a finding about `--set`; treat it as unverified if it comes up.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail. Count them from the file rather than trusting a
number here.

- Rank 05 is the next action and carries the full launch state.
- Ranks 88 and 89 are NEW this session (the token grant; the deadline path).
- Ranks 06, 07, 09, 69, 71 stayed closed. The rest (32, 37, 39, 40, 50, 55 through 68, 70, 72, 73,
  75, 77, 80, 85, 87) are as before.

## Lessons for the next nap

Carried from the previous handover, still uncaptured - they die here if they are not carried again:

- When a review closes a finding by ADDING tests, check the new tests are not vacuous.
- When a fixer deviates from your instruction and gives a reason, judge the reason on merit.
- When you enumerate what to extract for testability, expect the enumeration itself to be wrong.
- When a defect survives a careful self-review, look for the probe that STUBBED the component whose
  real behaviour is the defect.
- When a config takes its value from a DEFAULT, the default is a claim nobody checked.
- When a build asserts something at build time, check the build runs under the same shell the
  product does.
- When a run of zeros looks like a result, ask what a total failure would have looked like.
- When one silent failure has two causes, fix the first and RE-RUN before believing it.
- When a detector matches one literal string, it was written against one sample.
- When a harness retries, find out what goal the retry was given.
- When an artifact names a run id, check it is the run you watched.
- When you inject a seam you observe the CALL, not its cost - something must still measure duration.
- When you retire a predecessor, COUNT the copies first.
- When a gate resyncs a venv, it can be the thing that breaks a long-running process reading from it.
- When you scale an estimate from one measurement, say which DIMENSION it was measured on.
- When an instrument voids every run of one arm, adjudicate against a control from each arm.
- When a coordinator scores zero, read `tokens_by_row` before believing it is not an auth failure.
- When a module cannot be imported by its own repo, guard its SHAPE at source with a vacuity check.
- When a decision's options rest on a premise, check the premise at SOURCE before framing them.
- When a parametrized arm is inert, look UPSTREAM of the code under test for what short-circuited it.
- When you put options to the user, the set is not finished until it carries a RECOMMENDATION.
- tooling: the ci-watch Stop gate is satisfied by READING the verdict, not by arming a watcher.
- tooling: a mutation battery was hand-written four times in one session; the jig is queued.

New this session:

- When a measurement is disappointing, check whether a constraint of YOUR OWN was still on the
  subject before reading the number as a property of the subject.
- When you judge an effect, measure the NOISE FLOOR from your own data first: the same control arm
  scored 6 of 8 and 4 of 8 on one problem, which is larger than the between-arm difference.
- When one fix routes work into a second path, check what that path does before shipping the first:
  removing the reserve would have fed nodes into a cap path that discarded their worktrees.
- When a bound is reached, ask whether it is a FAULT or a scheduled event - a repo can hold both
  answers for sibling bounds and only notice when they are put side by side.
- When you preserve an artifact, ask whether you preserved the REASONING with it: keeping the
  worktree but interrupting at the moment of asking yields a tree with no note attached.
- When a test asserts a proxy (an interrupt count, a turn count) for a property, a behaviour change
  moves the proxy without moving the property - re-express it rather than retargeting the number.
- When a shared ClassVar is never reset, `instances[0]` is the first of the SESSION, not of the test.
- When you estimate a change as "reusing an existing pattern", check what the pattern is KEYED on
  before quoting the estimate - and correct it out loud when it proves wrong.
- tooling: a backgrounded gate's completion notice reports the LAST command's exit code; the
  block-masked-gate-exit hook caught this and `gate.py` is the way through.

## Files that matter

- `docs/probes/2026-09-05-slopcodebench-corrected-pair.md` - the FROZEN pre-registration; addendum
  4 (new) resets Task 13 and carries the falsifier.
- `docs/probes/2026-09-11-budget-handover-proof.md` - the end-to-end proof, its raw artifacts named
  by path, and an explicit statement of what it does NOT show.
- `src/agentdag/application/kernel/context.py` - `_run_cap_refusal`, `_cap_to_headroom`.
- `src/agentdag/adapters/kernel/executor_claude.py` - `_Handover` (three arming reasons),
  `_past_spend_cap`, `_handover_outcome`.
- `src/agentdag/application/kernel/planner.py` - the un-replannable budget refusal.
- `deploy/slopcodebench/arm-tier-policy.yaml`, `deploy/slopcodebench/agentdag.yaml` - the arm.
- Outside the repo: `~/agentdag-eval/slopcodebench/` (harness, catalogs, task13) and
  `~/agentdag-eval/handover-proof/` (the proof runs). Both hold credential copies - never move a
  run directory into a git work tree.

## How to verify

- `git log --oneline cf12356..HEAD` shows this session's work.
- The gate is `make test`; it read `{"result":"pass","stages":5,"scripts":12}` at each commit.
- CI: `uv run <compuse-toolbox>/scripts/ci_wait.py --sha <full sha>`; verdicts on stdout, not in
  the `--log`.
- The changed behaviour: `.venv/bin/python -m pytest tests/test_kernel_executor_claude.py
  tests/test_kernel_context.py tests/test_kernel_planner.py -q`.
- The arm against its pre-registration: `.venv/bin/python -m pytest
  tests/test_scb_arm_preregistration.py tests/test_scb_agent_harvest.py -q`.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
