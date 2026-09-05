# Handover, written 2026-09-05 03:20 CEST

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped and what it decided.

## The next action

**`OPEN-WORK.md` rank 05, the top-ranked open item: relaunch the corrected control's two
remaining problems.** Nothing is running. The credential has already rolled (check
`claudeAiOauth.expiresAt` in `~/.claude/.credentials.json`; it read 11:10 on 2026-09-05 when this
was written), so start at once, from the repo root:

```
.venv/bin/python scripts/scb_run_arm.py \
  --config ~/agentdag-eval/slopcodebench/run-corrected-control.yaml \
  --harness ~/agentdag-eval/slopcodebench/slop-code-bench \
  --problems-path ~/agentdag-eval/slopcodebench/scb-problems-cumulative \
  --log-dir ~/agentdag-eval/slopcodebench/runs/corrected-control-logs \
  --problem database_migration:3127 --problem dynamic_config_service_api:4501
```

Run it as a tracked background task and watch `corrected-control-logs/arm.log` for `END <problem>
rc=N` lines and `ARM COMPLETE` (or `REFRESH NEEDED`, if the token were to expire again; at 8 hours
it should not). Expect roughly 1h to 1.5h per problem under the host's current load. Do not run
`make test` while a problem is being timed: the bmk gate resyncs the venv the launcher runs from.

When `ARM COMPLETE`: `scripts/slopcodebench_readings.py` over the THREE run dirs (the finished
`opus-5_whole-spec_high_20260904T2353` plus the two new ones), read the band verdict off
`docs/probes/2026-09-05-slopcodebench-corrected-pair.md`, write the results BELOW its divider in
the shape of `docs/probes/2026-09-04-slopcodebench-control.md` `## Results`, gate, push.
SATURATED (`S >= 12`) stops the adapter build (Tasks 7-11 of
`PLANS/2026-09-04-corrected-pair-plan.md`) and moves the pair to the catalog's Hard problems under
a new pre-registration. Task 10 of that plan now carries a line the coordinator arm needs:
`kernel.deny_tools = []`, because the kernel closes `Task` by default since this session.

## In flight

- Nothing. The control's first problem finished (rc 0, 8 of 8 checkpoints, $40.51, 2h24m); the
  launcher exited; the watcher and timers this session armed died with it.
- No agent of mine is alive. The one reviewer dispatched tonight reported and its findings are
  applied.

## Committed, or not

Everything in agentdag is committed and pushed: `origin/main` is at `cbb2781` and CI plus CodeQL
were green on it and on `5aefbde` before it. Check with `git status --porcelain` and `git status
-sb` rather than trusting this line.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72); keep an explicit pathspec on
every commit.

**RESEARCH sibling repo** (`../RESEARCH`): 29 commits ahead of its remote, of which two are this
session's (the sealed E1 model read placed beside its key; the P4 probe note pointing at row 3). I
did not push it because the other 27 are not mine to publish wholesale. One foreign modified file
sits in its tree too.

Gitignored, so not in git: `EXECUTION-USER-REVIEW.md` (this night's decisions on top, user and own
in separate sections), `.bitranox/sdd/progress.md` and the task reports from the previous session.

## Decided this session, with the reason

- **C1 closed under option (c)** (user, after (a) collapsed: the only scorer named was the user,
  who is the anchored reader). Not run as designed; the panel's other 24 verdicts DISCARDED on a
  post-hoc finding that its executability lens is a per-arm constant across all 14 pairs while
  coverage and proportion vary, and a sealed second reader spreads arm A. Record: mid plan C1
  section. Follow-up is rank 39. The packet was NOT repaired: the absent briefs are E1's arm
  condition by design.
- **Rank 30: the P4 duplicate-execution finding is measured evidence in differentiator row 3**,
  not a row of its own and not a Partially-ships entry (user).
- **Rank 33: the run's actor is `[kernel] operator`, default `operator`** (user). Blank and the
  reserved `system` are refused. A structural test fails if production code reads the OS user.
- **Rank 38: the boundary is the existing hooks, hardened and stated** (user). `[kernel]
  deny_tools` closes WebFetch, WebSearch, Task by default; both denylists refuse a blank value and
  honour an explicit `[]`; README states the boundary in one paragraph and names Bash as the hole.
  The real sandbox adapter is rank 37.
- **The review's critical finding is stated, not fixed** (own): a background launch's coordinator
  re-reads config from files alone, so env, `--set` and `--profile` values bind the launching
  command, not the run. Pre-existing for every kernel key. README and CHANGELOG say so; rank 41
  holds the fix, with persist-on-the-run recommended.

## Decided against, and why

- Repairing the C1 packet's missing briefs: the panel judged exactly that material.
- Refusing an explicit empty denylist: the pre-registered corrected pair needs `deny_tools = []`
  for its coordinator arm, so `[]` is an operator statement and only a BLANK is refused.
- A known-tool allowlist for `deny_tools`: it would go stale with every CLI release; a shape check
  on the CLI's exact-string matcher class is what keeps whole-name matching.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- Rank 32 waits on the control's verdict (adapter requirement recorded on the line).
- Rank 34's remaining arm needs no timed arm running; it was blocked all night.
- Ranks 37, 39, 40, 41 are new or re-scoped tonight and sit at their ranks.
- Everything from 50 down was not touched.

## Lessons for the next nap

- When a backlog line calls an experiment's material handicapped, read the packet's own preamble
  first: the handicap was the arm condition by design, and repairing it would have broken the
  comparison.
- When the only executor named for a decision is the person the decision excluded, say so once,
  offer the fallback, and do not proceed under the original option.
- When a config key is added to a CLI that relaunches itself in the background, test the
  BACKGROUND path: an in-process test shares the launcher's config and cannot see that the child
  re-reads it from files alone.
- When a scorer returns a constant, check its neighbouring lenses on the same data: constant on
  both arms over all 14 while two other lenses varied localises the defect to that lens.
- When a reviewer without a shell cites a doc-only mechanism, fetch the doc before writing the
  mechanism into a docstring; it was right, and the docstring now cites it.
- tooling: `gate.py --name` must follow the `--gate` it labels; the usage error reads as a red
  gate in a background task.
- tooling: `anchor_edit.py` takes one anchor per call; a batch mode is queued in contrib_queue.

## Files that matter

- `docs/probes/2026-09-05-slopcodebench-corrected-pair.md` - the pre-registration; nothing above
  its divider may change.
- `docs/probes/2026-09-04-slopcodebench-control.md` - the calibration arm, the results shape.
- `PLANS/2026-09-04-corrected-pair-plan.md` - Tasks 7-13 pending; Task 10 carries the
  `deny_tools = []` line.
- `PLANS/build-plan-mid.md` (C1 section) and `PLANS/build-plan-high.md` (row 3, its paragraph,
  risk 2) - tonight's decision records.
- `src/agentdag/adapters/cli/commands/run.py` (`_operator_label`, `_config_denylist`,
  `_ENV_ALLOWLIST`), `src/agentdag/adapters/kernel/hooks_claude.py` (`deny_closed_tools`),
  `src/agentdag/adapters/config/defaultconfig.d/60-kernel.toml` - the boundary as shipped.
- `tests/test_cli_run_operator.py`, `tests/test_cli_run_denylists.py`,
  `tests/test_kernel_executor_port.py` - the tests that pin it.
- `~/agentdag-eval/slopcodebench/` - outside git: run config, catalog, run dirs, arm log.

## How to verify

- `make test` green (last run before the push of `cbb2781`).
- `scripts/slopcodebench_readings.py` over the three calibration run dirs still reads S=5 of 17,
  C=0.828, repaired_defined=12, repaired_total=0.
- `tests/test_cli_run_operator.py::test_no_production_module_reads_the_operating_account_name`
  fails on a planted `getpass.getuser(` in any `src/` file (proved tonight).

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
