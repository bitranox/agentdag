# Handover, written 2026-09-10 15:20 CEST

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where this session stopped and what it decided.

## The next action

**A PAID COUNTED RUN IS IN FLIGHT. Do not start another; do not run `make test` expecting a quiet
box.** Task 13's first problem, `database_migration`, started 2026-09-11 00:53:37 in
`~/agentdag-eval/slopcodebench/task13/`. Judge it by the `END` and `LAUNCHER_RC` lines in
`logs/arm.log`, never by a task notification. Disarm by killing the pid in `logs/launcher.pid`;
stop a running container with `docker stop` on the `slop-code:agentdag-*` one.

When it ends, the launcher AUTOMATICALLY re-checks the token and attempts
`dynamic_config_service_api` next. That is intended. If the token no longer covers it the launcher
exits 3 having started nothing, which is not a failure - relaunch when a window opens.

Then, in order:

1. Read the arm with `scripts/scb_arm_report.py <run dir>` and apply EVERY void condition,
   including the addendum's condition 6. Read `tokens_by_row` before believing any score: a
   suspended checkpoint that charged zero is an auth failure, not a result.
2. Decide `circuit_eval` with the REAL ratio in hand rather than my extrapolation. It needs about
   10.5 h of token on that extrapolation, against lifetimes of 7 to 8, so it may need its catalog
   split by checkpoint. The measured ratio below suggests the time half of that estimate was too
   pessimistic.
3. The results go BELOW the divider of `docs/probes/2026-09-05-slopcodebench-corrected-pair.md`,
   and the order deviation (short problems first) is recorded there.

## In flight

`database_migration` is RUNNING as above, in its own detached process group, into a token valid to
08:52. Two of its five checkpoints were evaluated in the first 43 minutes.

**Provisional readings, which are NOT an outcome** - a later checkpoint can repair what an earlier
one broke, and that is what `repaired` measures:

| ck | arm         | strict | core  | cost USD | seconds | steps | peak    | new tokens |
|----|-------------|--------|-------|----------|---------|-------|---------|------------|
| 1  | control     | 1.000  | 1.000 | 1.04     | 253     | 8     | 58,973  | 29,982     |
| 1  | coordinator | 1.000  | 1.000 | 2.53     | 472     | 13    | 79,151  | 127,436    |
| 2  | control     | 0.984  | 1.000 | 1.36     | 291     | 14    | 73,154  | 46,090     |
| 2  | coordinator | 0.903  | 0.667 | 10.59    | 2,108   | 56    | 142,208 | 495,666    |

**My sizing extrapolation is already wrong on time.** It scaled everything by 4.43x wall clock from
one checkpoint of the easiest kind. Two checkpoints in 43 minutes puts this problem near the
control's own pace rather than 4.4x it, so the 17 h figure for the whole arm is too pessimistic.
The COST side is holding or worse: checkpoint 2 alone cost 10.59 against the control's 1.36.

## Committed, or not

Everything is committed and pushed. This session's work is `git log --oneline 6fe5ed5..origin/main`
- run it rather than trusting a count here, which the commit adding this file and every correction
to it would move. It is one commit per decision, plus the dependency floor bmk raised, plus this
file and its corrections.

CI and CodeQL are green on every commit carrying code - `126eb46`, `b3a13c4` and `38a8eba` - each
read from a `ci_wait` verdict rather than a task notification, and the doc-only commits after them
were watched the same way. `9c02e5a` was pushed together with `38a8eba`, and a push builds the HEAD
sha only, so it has no run of its own: checked against `gh run list`, not assumed. Whatever commit
adds the LAST version of this file cannot be covered here; check it with
`uv run <compuse-toolbox>/scripts/ci_wait.py --sha $(git rev-parse HEAD)`.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72), untouched again this session.
Every commit used a pathspec.

Gitignored, so not in git: `EXECUTION-USER-REVIEW.md` (this session's three entries are at the top)
and `.bitranox/sdd/`. `handover.md` and `OPEN-WORK.md` ARE tracked here.

## Decided this session, with the reason

Three user decisions, each asked on its own and each recorded in full in `EXECUTION-USER-REVIEW.md`
with what I decided around it.

- **Rank 71, `cost_limits.max_retries: 0`** (user, over overriding `retry()` to re-send the task or
  to resume the killed run): one attempt per checkpoint makes a checkpoint's cost and its score
  describe the SAME attempt, which the paid dry run's did not.
- **Rank 69, harvest on every raising path** (user, over relying on the void rule or adding a
  structured partial marker): shipped as an INVARIANT - `run()` is a handler around
  `_launch_and_read()` - not as a harvest added to the one path that lacked it.
- **Rank 06, both the coordinator fix and the void rule** (user, over either alone): they answer
  different questions, and a rule written after a counted checkpoint is not pre-registration.

Mine, the ones that outlive the commits:

- **The void rules are an ADDENDUM below the pair document's divider**, not edits above it, and the
  addendum's first paragraph says it binds the coordinator arm only and changes nothing about how
  the control's results below it were read.
- **Void condition 6 is narrow on purpose**: a run that printed a terminal line and ended neither
  done nor suspended is a coordinator RESULT and is scored. Only a coordinator that never reported
  an outcome voids its problem.
- **Rank 06's second half is written as how the FROZEN condition 2 is EVALUATED on a coordinator**,
  not as a new condition: condition 2 already voids an auth-failure checkpoint; what it could not
  do is fire on a tree of nodes where no single process terminated.
- **The sub-plan path was left alone.** It already stops on a `NotPlanned` rather than looping, so a
  refusal there costs at most one further planner dispatch.
- **Paid runs are launched DETACHED** (`setsid nohup`), because a session-tied background task dies
  with its session and would leave a coordinator spending inside an orphaned container. Judge such a
  run by the `END` and `LAUNCHER_RC` lines in its `arm.log`, never by a task notification.
- **Task 13 waits for the token rather than racing it.** The harness FREEZES the token into the
  container at launch and nothing inside refreshes it, so a problem that outlives it dies mid-run
  and is VOID under condition 4. `launch_when_token_allows.sh` polls the credential and starts only
  when the window covers the problem. It worked: the token sat at 0.14 h, rolled to 7.98 h at
  00:53, and the launcher caught it within five minutes.
- **The launch condition was checked in BOTH directions before arming** (3.99 waits, 5.20 launches),
  because the dangerous direction is a false "fits" that spends money. The script writes its own PID
  to a pidfile: the launch command contains the script's name, so a `pgrep -f` liveness check would
  match the launching shell, and the guard caught exactly that.
- **The launcher's expected duration was raised from 1800 s to 3000 s.** It gates only the
  token-coverage refusal, and 1800 contradicted this host's own measurement - the void run's
  surviving attempt alone took 2,487 s.
- **The reading's VOID verdict was adjudicated, not reported.** The report script said condition 3;
  two controls (the earlier coordinator run, and a control-arm run) showed it fires on the
  coordinator's SHAPE rather than on anything the run did. Reporting that void as a result would
  have thrown away a valid paid checkpoint and hidden the blocker.

## Decided against, and why

- Building a `slop_code` stub so `agent.py.tmpl` could be unit-tested: the module's own header says
  it cannot be imported here, and a hand-built third-party surface drifts silently. The shape is
  guarded at source instead, which proves the SHAPE holds and not that the harvest reads the right
  records. That gap is stated in the commit and in the review log.
- Reinstalling the harness clone: a reinstall is part of a launch, not of these changes. Recorded in
  rank 05 instead, where the launch will read it.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- Count them from the file rather than trusting a number here. Rank 05 and rank 07 are named above;
  the rest (32, 37, 39, 40, 50, 55 through 68, 70, 72, 73, 75, 77, 80, 85, 87) are as before.
- Ranks 06, 69 and 71 were CLOSED this session. Rank 07 is NEW and blocks 05. Rank 66 has only its
  (c) left; 67 is the cost half of what 71 closed and stays open.

## Lessons for the next nap

Carried from the previous handover, still uncaptured - written for a nap that never ran, and they
die here if they are not carried again:

- When a review closes a finding by ADDING tests, check the new tests are not vacuous.
- When a fixer deviates from your instruction and gives a reason, judge the reason on merit.
- When you enumerate what to extract for testability, expect the enumeration itself to be wrong.
- When a defect survives a careful self-review, look for the probe that STUBBED the component
  whose real behaviour is the defect.
- When a config takes its value from a DEFAULT, the default is a claim nobody checked.
- When a build asserts something at build time, check the build runs under the same shell the
  product does.
- When a run of zeros looks like a result, ask what a total failure would have looked like.
- When one silent failure has two causes, fix the first and RE-RUN before believing it.
- When a detector matches one literal string, it was written against one sample.
- When a harness retries, find out what goal the retry was given.
- When an artifact names a run id, check it is the run you watched.
- tooling: the ci-watch Stop gate is satisfied by READING the verdict, not by arming a watcher.
- tooling: a mutation battery was hand-written four times in one session; the jig is queued.

New this session:

- An extrapolation from ONE measurement can be wrong in one dimension and right in the other. The
  4.43x wall-clock ratio from a single easy checkpoint over-predicted Task 13's duration badly while
  the cost ratio held or worsened. Say which dimension a scaled estimate was measured on.
- A measurement instrument written for one arm's SHAPE can void every run of the other arm and look
  like a finding. Adjudicate a void against its SOURCE with a control from each arm before believing
  it; here the control-arm run answered differently and settled it in one pass.
- A coordinator's zeros and an auth failure's zeros are the same shape. `tokens_by_row` is what
  separates them, and it must be read before any score is believed.
- When a module cannot be imported by its own repo, its SHAPE can still be guarded at source with
  AST assertions - and such a guard needs an explicit vacuity check, because every assertion about
  "no raise escapes the handler" is satisfied by a body that cannot raise at all.
- Check a premise at SOURCE before framing a decision's options: `_harvest_abandoned` turned out to
  be dead in the arm's configuration (no timeout is pre-registered, so `timed_out` can never be
  true), which changed the weight of the whole item.
- A parametrized arm can be inert because a setting UPSTREAM of the code under test short-circuits
  it: the rate-limited arm suspended before the planner was ever handed a record. This is the
  already-recorded "a RED that failed for the wrong reason" shape, met again in a fixture.
- An option set is not finished until it carries a RECOMMENDATION. I put rank 71 to the user with
  upsides and downsides and no recommendation, against a pinned iron rule.

## Files that matter

- `PLANS/2026-09-04-corrected-pair-plan.md` - Task 12 is done; Task 13 is next and is the counted arm.
- `docs/probes/2026-09-05-slopcodebench-corrected-pair.md` - the FROZEN pre-registration, now with
  an addendum below its divider carrying both of this session's void decisions.
- `docs/probes/2026-09-10-slopcodebench-parity-reading.md` - the CLEAN parity reading, its validity
  gate condition by condition, and the instrument defect that became rank 07.
- `docs/probes/2026-09-08-slopcodebench-arm-dry-run.md` - what Task 12 established, per-entry
  provenance, and why THAT paid run's parity number is void. Now points at the reading above.
- `deploy/slopcodebench/arm-tier-policy.yaml`, `deploy/slopcodebench/agentdag.yaml` - the arm.
- `deploy/slopcodebench/agentdag_scb_agent/agent.py.tmpl` - the adapter, guarded by
  `tests/test_scb_agent_harvest.py` (shape) and `tests/test_scb_arm_preregistration.py` (settings).
- `.bitranox/sdd/progress.md` - gitignored, and the only record of the carried Minors the final
  whole-branch review should triage.
- Outside the repo: `~/agentdag-eval/slopcodebench/` holds the harness clone (current, see above),
  the catalogs, the rehearsal directory and `task12-dryrun/`.

## How to verify

- `git log --oneline 6fe5ed5..HEAD` shows this session's work.
- The gate is `make test` through `gate.py`; it read `[PASS] make test (rc=0)` at each of the three.
- CI: `uv run <compuse-toolbox>/scripts/ci_wait.py --sha <full sha>`; verdicts on stdout, not in the
  `--log`.
- The arm against its pre-registration: `.venv/bin/python -m pytest
  tests/test_scb_arm_preregistration.py tests/test_scb_agent_harvest.py -q` reads 13 passed.
- The coordinator fix: `.venv/bin/python -m pytest tests/test_kernel_planner.py
  tests/test_kernel_root.py -q`; the root arm names the rehearsal's own symptom.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
