# Handover, written 2026-09-05 06:20 CEST

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped and what it decided.

## The next action

**`OPEN-WORK.md` rank 05, the top-ranked open item: the corrected control is COMPLETE; read
it.** `~/agentdag-eval/slopcodebench/runs/corrected-control-logs/arm.log` ends with
`END database_migration rc=0` (05:16:39), `END dynamic_config_service_api rc=0` (06:17:16),
`ARM COMPLETE` and `LAUNCHER_RC=0`, so every problem of the arm has a zero exit code and
nothing is void beyond the interrupted dir named below. Nothing is running; the detached
wrapper `~/agentdag-eval/slopcodebench/relaunch_detached.sh` exited. Start straight at the
readings.

Run `scripts/slopcodebench_readings.py` over the THREE run dirs
(`opus-5_whole-spec_high_20260904T2353` for `circuit_eval`, `..._20260905T0412` for
`database_migration`, and `..._20260905T0516` for `dynamic_config_service_api` - confirm the
third's stamp with `ls -dt` under the eval runs folder), NOT the
`VOID-interrupted-opus-5_whole-spec_high_20260905T0352` dir, which is the run this session
stopped at twenty minutes and is void under the pre-registration. Read the band verdict off
`docs/probes/2026-09-05-slopcodebench-corrected-pair.md`, write the results BELOW its divider
in the shape of `docs/probes/2026-09-04-slopcodebench-control.md` `## Results`, run `make
test` (allowed once no problem is being timed), push. SATURATED (`S >= 12`) stops the adapter
build (Tasks 7-11 of `PLANS/2026-09-04-corrected-pair-plan.md`); Task 10 carries
`kernel.deny_tools = []` for the coordinator arm. A non-zero `rc` means re-run that problem
whole under the void rules.

`make test` is allowed again: no problem is being timed. This session ran every gate the bmk
one composes directly from `.venv` while the arm ran (pyright, lint-imports, bandit, ruff, the
full non-integration pytest through `gate.py`), all green; the pushes went out on that plus CI.

## In flight

- Nothing. The arm finished; no agent of mine is alive; the reviewer reported and its one
  finding is fixed and pushed. CI and CodeQL were green on `c566e7b` and on `4b19ad2`, each
  read from `ci_wait.py`'s own exit code.

## Committed, or not

Everything in agentdag is committed; `origin/main` is at this handover's commit, on top of
`4b19ad2`. Verify with `git status --porcelain` and `git status -sb`.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72); keep an explicit pathspec
on every commit.

**RESEARCH sibling** (`../RESEARCH`): 30 ahead of its remote, one foreign modified file; now
rank 73, blocked on the user.

Gitignored, so not in git: `EXECUTION-USER-REVIEW.md` (this session's entry on top: the user's
route 1 decision in its own section, then mine), `.bitranox/sdd/progress.md`.

## Decided this session, with the reason

- **The 03:52 relaunch was stopped at twenty minutes and relaunched detached at 04:12** (own).
  It had gone out as a `run_in_background` task, as the previous handover said; the tree-top
  store's measured fact is that such a tree is reaped on session end, and the harness runs the
  agent as a `docker exec` leaf with no SIGTERM handler, so a reap would have left it spending
  tokens in an orphaned container. SIGINT was a no-op (a background task starts with SIGINT
  ignored, Python keeps it); SIGTERM to the process group plus `docker stop` of the per-run
  container ended it cleanly. The partial run dir is renamed VOID and excluded.
- **Rank 41 under route 1** (user): a run persists the resolved settings on itself at start
  and every later launch reads them back. Built and pushed (`06a93fe`, `c566e7b`). My calls
  inside it: the credential keyfile CHOICE is persisted too and a vanished keyfile refuses the
  relaunch by name; a pre-settings state file falls back to config and says so; the mail
  sink's `[email]` section is not persisted (a password) but the choice is; the deadline sweep
  announces a crashed run through the run's own sink, falling back to its own with a printed
  line when the run's mail sink cannot be built on that host.
- **Rank 34 closed** (own): the masked-gate block fired live on a backgrounded `ci_wait` twice
  on this harness, and driving the hook with the harness's own stdin payload blocks a
  backgrounded `make test` by name while three controls pass.
- **Rank 59 opened, not decided**: a resume line now names the run's owner rather than the
  relaunching operator. The reviewer called it a tradeoff; nothing reads that field today.

## Decided against, and why

- Persisting the `[email]` section on the run: it holds the SMTP password, and a run directory
  is copied out as evidence.
- Refusing a pre-settings run directory outright: old run dirs are the evidence for shipped
  probe notes, and the precedent for an added state field is a documented `None`.
- Re-dispatching the reviewer with a pinned model tier after the gate flagged the omission:
  the report was already useful; noted in the review log instead.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- Rank 32 waits on the control's verdict.
- Rank 37's next action is a design note (guarantees and refuting probes) before any code; it
  was about to be started when rank 41 was decided instead, and was not begun.
- Ranks 39, 40 sit as before; 59 and 73 are new this session.
- Everything from 50 down was not touched.

## Lessons for the next nap

- When a handover tells you to run a long job as a tracked background task, check the store
  first: the measured reap-on-session-end fact contradicted it, and ground truth wins over the
  channel; relaunch detached before the cost grows, not after.
- When a harness runs its agent as a `docker exec` leaf, killing the client tree leaves the
  agent spending tokens inside the container; stop the container by id after the tree.
- When a process tree was started as a background task, SIGINT is ignored all the way down;
  reach for SIGTERM on the process group and expect no cleanup handler unless you read one.
- When a test's precondition is "this host lacks X", set it explicitly with an override rather
  than assuming the machine: this box carries an SMTP section for the integration tests.
- When a fake substitutes a collaborator at one seam, a code path that builds the collaborator
  itself bypasses the fake; add the seam the path actually crosses (here `send_notification`).
- When the RED fails on a precondition the feature has not created yet, re-read its failure
  after the first GREEN step, then prove it by a killed mutation before calling it verified.
- tooling: dispatch a reviewer with an explicit model tier; the gate only says so afterwards.
- Carried from the 03:20 handover, not yet napped: when a backlog line calls an experiment's
  material handicapped, read the packet's own preamble first; when the only executor named for
  a decision is the person it excluded, say so once and offer the fallback; when a config key
  is added to a CLI that relaunches itself, test the BACKGROUND path; when a scorer returns a
  constant, check its neighbouring lenses; when a shell-less reviewer cites a doc-only
  mechanism, fetch the doc first; tooling: `gate.py --name` must follow its `--gate`, and
  `anchor_edit.py` takes one anchor per call.

## Files that matter

- `~/agentdag-eval/slopcodebench/relaunch_detached.sh`, `.../runs/corrected-control-logs/arm.log`
  - the arm and its verdict lines, outside git.
- `docs/probes/2026-09-05-slopcodebench-corrected-pair.md` - the pre-registration; nothing above
  its divider may change.
- `docs/probes/2026-09-04-slopcodebench-control.md` - the calibration arm, the results shape.
- `PLANS/2026-09-04-corrected-pair-plan.md` - Tasks 7-13 pending.
- `src/agentdag/adapters/cli/commands/run.py` (`_resolve_settings`, `_settings_of`,
  `_build_wiring`, `_crash_sink_for`, `_credential_from`), `src/agentdag/domain/models.py`
  (`RunSettings`), `src/agentdag/application/kernel/run.py` (`_write_state` carries it).
- `tests/test_cli_run_settings.py` - the nine tests that pin route 1.

## How to verify

- `scripts/slopcodebench_readings.py` over the three calibration run dirs still reads S=5 of
  17, C=0.828, repaired_defined=12, repaired_total=0.
- `.venv/bin/python -m pytest tests/test_cli_run_settings.py -p no:cacheprovider -q` is 9
  passed; the full non-integration suite was 1239 passed through `gate.py`.
- `tests/test_cli_run_settings.py::test_apply_deadlines_tells_a_crashed_run_through_the_sink_that_run_was_started_with`
  fails with two mails instead of one against a sweep that uses its own sink (proved this
  session).

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
