# Handover, written 2026-09-05 18:25 CEST

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped and what it decided.

## The next action

**`OPEN-WORK.md` rank 05, the top-ranked open item: the corrected control is COMPLETE and
written up; start Task 8 of `PLANS/2026-09-04-corrected-pair-plan.md` through the
subagent-driven loop.** The brief is already extracted at `.bitranox/sdd/task-8-brief.md`. Read
the ledger `.bitranox/sdd/progress.md` before dispatching anything: Task 7 is complete there.
The `plan-execution` model-gate receipt is armed (`skill_receipt.py start plan-execution`), so a
dispatch without `model` is denied; keep dispatching implementer (opus or sonnet by the skill's
tiers), task reviewer, fix agent, re-review. Tasks 8, 9 and 10 all edit
`src/agentdag/adapters/kernel/executor_claude.py` and `adapters/cli/commands/run.py`, so they
run one after another, never in parallel; Task 11 after them.

Two things the Task 8 brief cannot know, hand them to the implementer verbatim:

- Route 1 (`06a93fe`, `c566e7b`) made kernel settings persist on the run as `RunSettings`
  (`src/agentdag/domain/models.py:436`), resolved once by `_resolve_settings` and read back by
  every background, resume, approve and retry launch through `_settings_of` and
  `_build_wiring` (`adapters/cli/commands/run.py:1015-1065`). A new `kernel.gate_command` must
  join that model, be persisted, and be TESTED on the background relaunch path, or the child
  runs a different gate than the command that typed it.
- The brief's line numbers are a day old; grep the symbol first (`MakeTestGate` at
  `gate_make.py:127`, `_build_gate_make_test` at `composition/kernel.py:409`, `OpSpec` at
  `registry.py:104`).

CI on `3a05456` was still running when this was written (`ci_wait` via `gate.py`, log in this
session's scratchpad); confirm it with
`uv run <compuse-toolbox>/scripts/ci_wait.py --sha 3a0545641ada770aeaec727f5f86e1a3bf041c08`
before building on it. The push before it (`b088faf`) went green only after a rerun: its Python
3.12 Ubuntu cell died in `setup-uv`'s own version fetch ("fetch failed") before any project step,
a runner transient, fixed by `gh run rerun --failed`.

## In flight

- Nothing. The `circuit_eval` re-run ended 18:07 (`END circuit_eval rc=0`, `LAUNCHER_RC=0` in
  `~/agentdag-eval/slopcodebench/runs/corrected-control-logs/arm.log`); no agent of mine is
  alive; the last review reported and its one finding is fixed in `3a05456`.

## Committed, or not

Everything in agentdag is committed and pushed; `origin/main` is at `3a05456` (or at this
handover's commit on top of it). Verify with `git status --porcelain` and `git status -sb`.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72); keep a pathspec on every
commit. **RESEARCH sibling** (`../RESEARCH`) is rank 73, blocked on the user.

Gitignored, so not in git: `EXECUTION-USER-REVIEW.md` (this session's entry on top),
`.bitranox/sdd/progress.md` (the ledger), `.bitranox/sdd/task-7-report.md`, `task-8-brief.md`.

## Decided this session, with the reason

- **`circuit_eval` of the corrected control is VOID under condition 3, as a whole problem**
  (own): checkpoint 8's first CLI process hit the 100-turn bound with three background `Monitor`
  tasks in flight and the harness retried it with `--continue`. I did not read the rule against
  its refuted premise; a pre-registration is a commitment device.
- **Re-run now, detached, same settings** (user, three options offered). It ran clean, S 6 of
  17 for the arm, band USABLE. The void run's readings stay on record under
  `VOID-condition-3.txt` in `opus-5_whole-spec_high_20260904T2353` and enter nothing.
- **The band verdict was read before the re-run by the bound argument** (own): 8 void
  checkpoints cannot lift S past 10, so USABLE stood whatever the re-run returned. Captured as a
  memory fact.
- **`new_tokens` is summed from the stream keyed by message id** (own): the harness records only
  the last result event's usage, and a background-task wake-up emits a fresh result. Every
  single-result checkpoint reads identically under both sources.
- **Task 7's `Tokens.cache_write` is `int | None = None`, omitted when unset** (own, ratifying
  the implementer): a required field refuses every stored record and an emitted null re-keys
  journal records. The reviewer verified it against `record_hash` and `canonical_json`.
- **Task 7's last Minor was closed by me, not by another fix round** (own): the probe note's
  phantom script became an inline snippet, executed once, output pasted. The final whole-branch
  review should confirm the note reproduces.

## Decided against, and why

- Reading checkpoint 8's harness retry as the control's continuation and tallying it: the
  pre-registration says void, and its retry replaced the first process's stream and cost record,
  so the checkpoint is unmeasurable on cost and tokens regardless.
- Waiting for the coordinator arm to interleave the re-run: the pre-registration does not require
  interleaving, the other two problems already ran sequentially, and the host would have idled.
- Running `make test` while the launcher was alive: it runs from this repo's `.venv`, which the
  bmk gate resyncs.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- Rank 32 is settled for the SCB route by the earlier check; nothing new.
- Rank 37, 39, 40 as before; 59 and 73 as the previous handover left them.
- Everything from 50 down was not touched.

## Lessons for the next nap

- When a pre-registration states a bound on an arm ("the control stops at 100 turns"), read the
  harness's retry and continuation path at source before freezing it: SlopCodeBench retries a
  max-turns error with `--continue` up to `max_retries` (default 2) more times.
- When a harness retries a process, expect the retry to REPLACE the stream and the cost record,
  so a retried checkpoint is unmeasurable from the record alone; void it on that ground too.
- tooling: `backstop.py --done-file` needs a NON-EMPTY file; a `touch`ed sentinel is empty, the
  backstop never sees it as done, and the deadline fires after the work finished (twice this
  session). Write a byte into the sentinel.
- When a CI cell dies inside a third-party action's own setup (setup-uv "fetch failed") before
  any project step ran, rerun the failed job before reading code.
- When a fix agent's report lives in a gitignored file, any artifact a committed note cites (a
  replay script) must be committed or inlined in the same change, or the note is unreproducible.
- When a memory fact says a field is never set and a task then sets it, update the fact in the
  same session (done for `cost_usd` in the SDK-usage fact).

## Files that matter

- `docs/probes/2026-09-05-slopcodebench-corrected-pair.md` - the pre-registration; results
  below its divider now cover the full corrected control. Nothing above the divider changes.
- `scripts/scb_arm_report.py`, `scripts/slopcodebench_readings.py` - the only things that
  compute a figure for the write-ups; `tests/test_scb_arm_report.py`,
  `tests/test_slopcodebench_readings.py`.
- `PLANS/2026-09-04-corrected-pair-plan.md` - Tasks 8-13 pending.
- `.bitranox/sdd/progress.md` - the plan ledger; `.bitranox/sdd/task-8-brief.md`.
- `~/agentdag-eval/slopcodebench/runs/opus-5_whole-spec_high_20260905T{1556,0412,0516}` - the
  corrected control's three valid run dirs, outside git.

## How to verify

- `scripts/scb_arm_report.py` over the three valid corrected run dirs reads S=6, C=0.890,
  `Void: none`; over the three calibration dirs S=5, C=0.828, `Void: none`.
- `.venv/bin/python -m pytest tests/test_scb_arm_report.py tests/test_slopcodebench_readings.py tests/test_kernel_schemas.py -p no:cacheprovider -q` passes; `make test` was green at `3a05456` (RC 0 read from its log).

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
