# Handover, written 2026-09-10 13:05 CEST

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where this session stopped and what it decided.

## The next action

**Put rank 71 to the user, then rank 69, then rank 06 - one at a time.** Rank 05 is still the
top-ranked open item and its own `next:` now says the parity reading waits on 69 and 71, because
both change what a retry costs and what it scores. So the top item's next action IS these
decisions; nothing lower-ranked is jumping the queue.

One answer may settle 71 and 69 together: `cost_limits.max_retries: 0` in
`deploy/slopcodebench/agentdag.yaml` makes a failed checkpoint fail rather than be continued
blind, which also removes the arm's exposure to the unharvested-attempt defect. It does not fix
the defect for anything else, so 69 stays a real item either way.

Rank 06 is the one that gates Task 13's validity rather than its cost.

## In flight

Nothing running. The paid checkpoint finished 2026-09-08 04:17:31; its backstop and every CI
watcher have reported. One CI watch was still polling when this file was written - see
`How to verify`.

## Committed, or not

Everything is committed and pushed. This session's work is `f85cfe5..3a515b6` on `main`. CI and
CodeQL are green on `f85cfe5`, `d1e4cef`, `b417a04`, `5733663`, `88a4d3f` and `cf3138c`, each
read from a `gate.py` `[PASS]` line rather than from a task notification.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72), untouched again this session.
Every commit used a pathspec.

Gitignored, so not in git: `EXECUTION-USER-REVIEW.md` (this session's decisions are appended at
the top) and `.bitranox/sdd/`. `handover.md` and `OPEN-WORK.md` ARE tracked here.

Outside the repo and not in git: the harness clone is now PATCHED (it was read-only until this
session) at agentdag `cf3138c` with the arm's policy path baked into
`configs/agents/agentdag.yaml`; the arm images are built; and there are rehearsal and dry-run
directories under `~/agentdag-eval/slopcodebench/`.

## Decided this session, with the reason

- **The arm's five missing pre-registered settings were fixed without asking** (own): the frozen
  pre-registration decides all five, so there was no open choice - only a gap between what it says
  and what the arm did. The arm had no tier policy at all, so it would have run sonnet on every
  work node.
- **The arm policy is DERIVED from the shipped table, not written fresh** (own): the diff against
  `src/agentdag/policy/tier-policy.yaml` is then exactly the four pre-registered changes and
  nothing else, which a reviewer can check in one read. The cost is a long file of inherited
  commentary about rows that are switched off.
- **One available row means no escalation target, and that is recorded rather than worked around**
  (own): a blocked node now goes straight to an approve and suspends the checkpoint. A laddered
  table would retry it first, on a model the pre-registration forbids.
- **The tier policy PATH is written by the installer, not committed** (own): it is a launch fact,
  exactly like the version the config already fills in that way, and the arm config's own comment
  gave the rationale.
- **The Docker build was fixed by matching the build SHELL to the runtime shell** (own), not by
  rewriting the failing assertion to use absolute paths. Both build; only one makes the assertion
  test the property the arm depends on.
- **The auth classifier was widened and shipped rather than filed** (own): the failing input was
  measured and the fix is narrow. Case-insensitive on an asymmetry argument, and bounded by a test
  that fails if the markers are loosened to `auth` or `401`.
- **The paid checkpoint's parity number is VOID, not reported** (own): the run was disturbed, so
  the number measures a disturbed arm. It is written up as a finding, with the table present and
  labelled void rather than omitted.
- **What killed attempt 0 is UNDETERMINED** (own): no OOM record, and the only known coincidence
  is this session suspending in the same minute. Recorded as undetermined rather than blamed.

## Decided against, and why

- Fixing rank 06's second cause (the planner path not reading `record.error`): it is a coordinator
  behaviour change with more than one defensible answer, so it belongs to the user.
- Running a second paid checkpoint to replace the void one: ranks 69 and 71 both change what a
  retry costs and scores, so a reading taken before they are decided would likely be void too.
- Building the mutation-battery jig inline: four near-identical batteries were written this
  session, and that is queued for the contribution loop rather than fixed in a work session.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- Ranks 32, 37, 39, 40, 50, 55 through 65, 68, 70, 72, 73, 75, 77, 80, 85, 87 as before.
- Ranks 06, 69 and 71 are NEW this session and all three want a user decision: the auth-failure
  laundering, the unharvested attempt, and the retry that cannot see the task.
- Rank 66 lost its (a): the launch script is harvested now, proven end to end. Its (b) became rank
  67 with a number. Only (c) is left on it.

## Lessons for the next nap

Carried from the OUTGOING handover, still uncaptured - these were written for a nap that never
ran, and they die here if they are not carried again:

- When a review closes a finding by ADDING tests, check the new tests are not vacuous.
- When a fixer deviates from your instruction and gives a reason, judge the reason on merit.
- When you enumerate what to extract for testability, expect the enumeration itself to be wrong.
- When a defect survives a careful self-review, look for the probe that STUBBED the component
  whose real behaviour is the defect.
- tooling: the ci-watch Stop gate is satisfied by READING the verdict, not by arming a watcher.

New this session:

- When a config takes its value from a DEFAULT, the default is a claim nobody checked: check every
  setting a frozen decision names against what the code actually resolves, not against the file.
- When a build asserts something at build time, check the build runs under the same shell the
  product does - a base image's login SHELL discards the image's own ENV inside every RUN.
- When a run of zeros looks like a result, ask what a total failure would have looked like: an
  auth failure produced `state: ran`, cost 0.0, 0 of 47, and rc 0 at every level.
- When one silent failure has two causes, fix the first and RE-RUN before believing it: the
  classifier fix was correct, changed nothing at the run level, and every test stayed green.
- When a detector matches one literal string, it was written against one sample: a second real
  sample refuted `"Not logged in"` as the whole of what an auth failure says.
- When a harness retries, find out what goal the retry was given - this one re-planned a task it
  could not see, from a 33-character prompt.
- When an artifact names a run id, check it is the run you watched: the id in the artifacts was
  not the one running live, and that was the only visible sign of a retry.
- tooling: a mutation battery was hand-written four times in one session; the jig is queued.

## Files that matter

- `PLANS/2026-09-04-corrected-pair-plan.md` - Task 12 is done bar a clean parity reading; Task 13
  is next and is the counted arm.
- `docs/probes/2026-09-08-slopcodebench-arm-dry-run.md` - everything Task 12 established, with a
  provenance tier per entry and the void reasoning for the paid run.
- `docs/probes/2026-09-05-slopcodebench-corrected-pair.md` - the FROZEN pre-registration. Its
  held-fixed table above the divider is what the arm is checked against.
- `deploy/slopcodebench/arm-tier-policy.yaml` and `deploy/slopcodebench/agentdag.yaml` - the arm.
- `tests/test_scb_arm_preregistration.py` - the guard that would have caught the five gaps.
- `scripts/scb_install_agentdag.py`, `scripts/scb_run_arm.py` - installer and launcher.
- `.bitranox/sdd/progress.md` - gitignored, and the only record of the carried Minors the final
  whole-branch review should triage.
- Outside the repo: `~/agentdag-eval/slopcodebench/` holds the harness clone, the catalogs
  (`scb-problems-cumulative`, and `-cp1` derived this session for one checkpoint), the rehearsal
  directory with its deliberately invalid credential, and `task12-dryrun/` with the paid run.

## How to verify

- `git log --oneline f85cfe5~1..HEAD` shows this session's work.
- The gate is `make test` through `gate.py`; it read `[PASS] make test (rc=0)` at every commit.
- CI: `uv run <compuse-toolbox>/scripts/ci_wait.py --sha <full sha>` for each of the six above.
  The seventh, `3a515b6`, was still polling when this file was written - check it first.
- The arm against its pre-registration:
  `.venv/bin/python -m pytest tests/test_scb_arm_preregistration.py -q` reads 6 passed.
- That the paid run is void rather than a reading: its `infer.log` carries a `retry_attempt` field
  and a second `agent.agentdag.start` whose `prompt_chars` is 33.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
