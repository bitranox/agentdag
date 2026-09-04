# Handover, written 2026-09-04 23:55 CEST

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped and what it decided.

## The next action

**`OPEN-WORK.md` rank 05, the top-ranked open item, is mid-run.** The corrected control arm is
running unattended. First thing:

```
cat ~/agentdag-eval/slopcodebench/runs/corrected-control-logs/arm.log
```

- If it ends `ARM COMPLETE`: compute the readings (`scripts/slopcodebench_readings.py` over the
  run dir(s) named in the log; the calibration write-up in
  `docs/probes/2026-09-04-slopcodebench-control.md` under `## Results` is the shape to follow),
  read the band verdict off `docs/probes/2026-09-05-slopcodebench-corrected-pair.md`, write the
  results BELOW its divider, gate, push.
- If it ends `REFRESH NEEDED before <problem>` (expected: the token expires 2026-09-05 03:14 and
  `database_migration` would not fit after `circuit_eval`): make sure the credential has rolled
  over (`~/.claude/.credentials.json` `claudeAiOauth.expiresAt`; this session's own API calls
  refresh it, `claude auth status` does not), then relaunch with ONLY the remaining problems:

  ```
  .venv/bin/python scripts/scb_run_arm.py \
    --config ~/agentdag-eval/slopcodebench/run-corrected-control.yaml \
    --harness ~/agentdag-eval/slopcodebench/slop-code-bench \
    --problems-path ~/agentdag-eval/slopcodebench/scb-problems-cumulative \
    --log-dir ~/agentdag-eval/slopcodebench/runs/corrected-control-logs \
    --problem database_migration:3127 --problem dynamic_config_service_api:4501
  ```

  Each problem lands in its own run dir under `~/agentdag-eval/slopcodebench/runs/` named
  `opus-5_whole-spec_high_<stamp>`; the arm's readings are the union of those dirs.
- If a problem ends with a non-zero `rc`, read `corrected-control-logs/<problem>.log`; a problem
  is re-run whole, never resumed, under the pre-registration's void rules.

Only after the corrected control's verdict is written does the adapter build start (Tasks 7-11
in `PLANS/2026-09-04-corrected-pair-plan.md`); SATURATED (`S >= 12`) stops it and moves the pair
to the catalog's Hard problems under a new pre-registration.

## In flight

- The corrected control, problem `circuit_eval`, started 23:53:27 in run dir
  `~/agentdag-eval/slopcodebench/runs/opus-5_whole-spec_high_20260904T2353`; its
  `problem_catalog.json` records the derived catalog as the problem root, and checkpoint 1's
  saved `prompt.txt` carries one NEW marker (correct for N=1). Expected about 2h15m under the
  host's current load (load average about 30 from another session's nice-19 job; recorded per
  problem in `arm.log`).
- No agent of mine is alive. Every implementer and reviewer reported and was closed out.

## Committed, or not

Everything is committed and pushed; `origin/main` is at `6449953` and CI was green on `cc6f3c7`
(the later commits are docs, scripts and tests that passed `make test` before the push; check
CI on `6449953` with the `ci_wait` jig from `bitranox:compuse-toolbox`). Check with
`git status --porcelain` rather than trusting this line.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72); keep an explicit pathspec on
every commit.

Gitignored, so not in git: `EXECUTION-USER-REVIEW.md` (this evening's and night's decisions on
top), `.bitranox/sdd/progress.md` (the task ledger, with the launch command and every carried
Minor), `.bitranox/sdd/task-2-report.md` and `task-4-report.md`.

## Decided this session, with the reason

- **The calibration arm is closed: S = 5 of 17, USABLE, calibrated against the published 4.** The
  control repaired zero carried-forward defects, and read at source that is the harness's loop
  (no feedback, context reset per checkpoint, delta-only prompt, "that is all you need to do"),
  not the agent. A coordinator under that loop would be equally blind.
- **The fix is data, not a harness patch** (user, three options): a derived catalog whose
  `checkpoint_N.md` carries parts 1 to N, and a prompt that adds three sentences to `just-solve`.
  Hidden tests stay hidden. Rejected: feeding results back; context continuity.
- **The pair's settings** are in the pre-registration; the four the user decided (1.2M charged
  token guard per checkpoint, no wall-clock timeout, bash denies `git push`/`gh pr`/`gh release`
  only, parallel 8) and the ones I decided (pytest gate command, CLI 2.1.260 on both arms by
  removing the SDK's bundled binary from the coordinator image, work directly in `/workspace`,
  suspension as a normal end, cost from per-node `total_cost_usd`).
- **Coordinator nodes get the full CLI tool set** (user, against my six-on-both recommendation).
  A `Task`-spend probe must pass before the coordinator arm counts.
- **The coordinator adapter is in this plan** (user), but its build waits for the corrected
  control's verdict; the design agent's corrected premises are in the plan file at
  `~/.claude/plans/fix-that-the-linked-newell.md` section D and in the memory fact on the
  harness seams.
- **No `make test` while an arm of ours is being timed** was the rule tonight; the arm now
  running was launched AFTER the gate, and duration is a recorded condition, not a controlled one.

## Decided against, and why

- Pinning both corrected arms to CLI 2.1.259 (the SDK's bundled one): it would put a second
  deviation between the corrected control and the calibration arm. Fallback only if the SDK
  refuses the PATH binary.
- Re-running the Task 2 implementer when it returned a plan: it had inherited plan mode at
  dispatch; resuming it with an approval message was correct and cheaper.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- Ranks 25 and 30 (USER items) were not touched this session; 05 outranks them by the user's
  own reasoning recorded on the line.
- Rank 32 was re-scoped, not decided: on the SlopCodeBench route its cause cannot arise; the
  binding requirement is recorded on the line.
- Everything else sits at its own rank.

## Lessons for the next nap

- When a Stop-hook handover ask arrives while agents are in flight, get the user's timing decision
  once and then do not re-ask on the next fire; the answer covers the growth it predicted.
- tooling: `gate.py --summary` is a regex and crashes on an unbalanced paren; queued in
  contrib_queue.
- When a reviewer's Important is a false CLAIM in a docstring rather than a defect, it still goes
  back to the implementer: a wrong rationale about the instrument propagates as a fact.

## Files that matter

- `docs/probes/2026-09-05-slopcodebench-corrected-pair.md` - the pre-registration; nothing above
  its divider may change now that a counted checkpoint is running.
- `docs/probes/2026-09-04-slopcodebench-control.md` - the calibration arm, results written.
- `PLANS/2026-09-04-corrected-pair-plan.md` - the task list; Tasks 1-6 done or in flight, 7-13
  pending.
- `scripts/scb_run_arm.py`, `scripts/scb_cumulative_catalog.py`, `scripts/slopcodebench_readings.py`,
  `scripts/scb_prompts/whole-spec.jinja` - the corrected loop and its instruments.
- `~/agentdag-eval/slopcodebench/run-corrected-control.yaml`, `agent-claude-code-oauth.yaml`,
  `scb-problems-cumulative/CUMULATIVE-MANIFEST.json` - outside git, the run's inputs.

## How to verify

- `make test` green (last run before the push at `6449953`).
- `scripts/slopcodebench_readings.py` over the three calibration run dirs still reads S=5 of 17,
  C=0.828, repaired_defined=12, repaired_total=0 (the regression check in the Task 4 report).
- A rendered prompt from the derived catalog has parts 1..N in order, one NEW marker, no canary.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
