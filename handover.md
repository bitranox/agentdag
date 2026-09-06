# STALE - read 2026-09-06, work continued

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped and what it decided.

## The next action

**Start Task 11 of `PLANS/2026-09-04-corrected-pair-plan.md`: the harness agent type under
`deploy/slopcodebench/`.** Extract the brief with the subagent-driven-development skill's
`task_brief.py`, read `.bitranox/sdd/progress.md` first (Tasks 8, 9 and 10 are all complete there),
and re-arm the model gate (`skill_receipt.py start plan-execution`) before dispatching, so no
dispatch can omit `model`.

This is a lower-ranked item than `OPEN-WORK.md` rank 05's own top line only in the sense that it IS
rank 05: Task 11 is the next step of the corrected-pair work that rank 05 tracks. Nothing above it
is live.

**Read the arm-fairness note now in the plan, directly above Task 12** - it is Task 11's business,
because Task 11 writes the coordinator arm's config, and all three settings there confound the pair
if they are missed.

## In flight

Nothing. No agent of mine is alive. The last dispatch (a verification of Task 10's second fix round)
reported before this was written, and its one Critical is filed as `OPEN-WORK.md` rank 62 rather
than fixed, because the tree was already green and pushed.

## Committed, or not

Everything is committed and pushed; `origin/main` is at `3df1a00` (or at this handover's commit on
top of it), CI and CodeQL green on that sha. Verify with `git status --porcelain` and `git status -sb`.

**Not mine:** `CLAUDE.md.bak` is still `AD` in the index (rank 72). Its index entry is blob
`a2a5b9cfcaf0471f877025ecbd35da74bdd78e9b` mode `100644` - a fix agent amended it into a commit by
accident this session, caught it, and restored it, and that blob and mode were checked against the
value recorded before the session started. Keep a pathspec on every commit; it has now nearly
shipped once.

Gitignored, so not in git: `EXECUTION-USER-REVIEW.md` (this session's three entries on top),
`.bitranox/sdd/progress.md` (the ledger), and the per-task briefs, reports and review diffs beside it.

## Decided this session, with the reason

- **Task 8's `gate_command` joins `RunSettings` and is tested on the BACKGROUND relaunch path**
  (own): kernel settings resolve once at `run start` and are read back by every later launch, so an
  unpersisted value binds only the process that typed it.
- **The op name stays `gate:make-test`** (own): the brief allowed a rename only if
  `plan.schema.json` enumerated op names; it enumerates node KINDS, so the condition is false.
- **`gate_command = []` is refused, `deny_tools = []` is honoured** (own): there is no runnable
  empty argv, but denying nothing is meaningful. Decided per key, not carried across.
- **Task 9's brief was WRONG about `FsRunDir.read_text`** (own, after the reviewer verified at
  source): of the seven `read_text` call sites only the planner's takes an executor-produced ref,
  and the plan ref is composed from `node_dir.relative_to(run_dir.root)`, so no ref reaching that
  port can be absolute. Widening it would have made the run-dir port an arbitrary-file reader.
- **The scan's workspace exclusion goes in `key_facts`, not a new journal line** (own): adding
  `unwatched_roots` to `scan`'s `output_contract` AND `facts_if_no_work` puts it on the `ResultLine`
  a fold reads. A new line type is not warranted for one field.
- **Task 10 exposes only `dontAsk` and `bypassPermissions`** (implementer, ratified): not on the
  safety axis - PreToolUse deny hooks fire under every mode and a hook deny short-circuits before
  the mode is evaluated, read at source - but because the other four need a person or a model
  classifier, which an unattended run has neither of.
- **The `.env` JSON-string refusal widening beyond Task 8's scope** (own): it changed what
  `deny_bash` and `deny_tools` accept, so a config shape accepted at `1d043bb` is now refused. The
  old behaviour failed OPEN on a safety boundary - `KERNEL__DENY_BASH=["git push"]` built a denylist
  matching that literal, not `git push`.
- **Task 10's Critical was filed, not fixed** (own): the verification landed after the push, the
  finding is a documentation contradiction rather than a functional defect, and the user had asked
  for the handover next.

## Decided against, and why

- Fixing rank 62 in a fifth round tonight: gate and CI are green, it is prose, and a fifth attempt
  at 01:20 by a tired controller is how the previous four went wrong.
- Running Task 10's live verification probe: it is a real dispatch against the operator's
  subscription and belongs with the controller, not an unattended implementer. Still owed.
- Letting an implementer run that probe: same reason, decided before Task 10 was dispatched.

## Open, untouched

One line each; `OPEN-WORK.md` holds the detail.

- Ranks 05 (its Tasks 11-13 remain), 32, 37, 39, 40 as before.
- Ranks 56, 57, 61 and 62 are NEW this session, all found by the Task 8-10 reviews.
- 59 and 73 as the previous handover left them; everything from 50 down was otherwise untouched.

## Lessons for the next nap

Carried forward un-napped from the outgoing handover (no nap has run since):

- When a pre-registration states a bound on an arm, read the harness's retry and continuation path
  at source before freezing it: SlopCodeBench retries a max-turns error with `--continue` up to
  `max_retries` (default 2) more times.
- When a harness retries a process, expect the retry to REPLACE the stream and the cost record, so a
  retried checkpoint is unmeasurable from the record alone; void it on that ground too.
- tooling: `backstop.py --done-file` needs a NON-EMPTY file; a `touch`ed sentinel is empty, so the
  backstop never sees it as done. Write a byte into the sentinel.
- When a CI cell dies inside a third-party action's own setup before any project step ran, rerun the
  failed job before reading code.
- When a fix agent's report lives in a gitignored file, any artifact a committed note cites must be
  committed or inlined in the same change, or the note is unreproducible.

New this session:

- When a claim lives in PROSE (a docstring, a comment, a config note), it needs the same
  verification as code: one claim family was shipped wrong in five consecutive rounds of one task,
  because a mutation battery asks whether an arm can FAIL, never whether a sentence is TRUE.
- When you verify that a wrong claim was removed, enumerate by MEANING and not by the previous
  round's literal wording - each of those five rounds grepped for the last round's string and missed
  the re-worded instance sitting a few lines away.
- When a measured row and a source reading disagree, look for a code path that makes BOTH true
  before choosing: `dontAsk`'s fallback denies an undecided call AND a read-only Bash command is
  auto-allowed by its own classifier before the mode is consulted, so the 8-of-24 measurement was
  never a contradiction.
- When a subagent reports that it recovered from a mistake, verify the recovery from GROUND TRUTH -
  here the index blob and mode plus the file list of every commit - because a plausible recovery
  report is not a recovery.
- When a brief tells you to record something in an external config, check the target EXISTS before
  treating the item as owed: Task 10's `kernel.deny_tools = []` line had nowhere to go, because
  every config under the eval tree drives the single-agent control.
- When comparing two arms, ask which settings an arm cannot even EXPRESS yet: the coordinator arm
  had no way to set the control's `permission_mode` until Task 10 made it configurable, so the pair
  would have been confounded by a value one side could not state.
- An implementer that commissions its OWN adversarial reviewer before reporting caught real defects
  twice this session that its own mutation battery could not - both times a false statement in
  prose.
- When judging whether a dispatched subagent is still alive, do NOT read its transcript file's size
  or mtime: the harness does not flush it, so a working agent's file sat at 160 bytes unchanged for
  15 minutes. An implementer nearly wrote off a reviewer on that reading, and that reviewer then
  returned a real defect. (Found by a subagent; it is not in the controller's transcript.)

## Files that matter

- `PLANS/2026-09-04-corrected-pair-plan.md` - Tasks 11, 12, 13 pending; the arm-fairness note sits
  directly above Task 12 and binds Task 11.
- `.bitranox/sdd/progress.md` - the ledger; `.bitranox/sdd/task-{8,9,10}-report.md` for the detail,
  and Task 10's report section 5 holds the unrun verification probe's config, command and read points.
- `src/agentdag/adapters/cli/commands/run.py`, `src/agentdag/adapters/kernel/executor_claude.py` -
  the two files Tasks 8, 9 and 10 all landed in; both carry standing size concerns.
- `src/agentdag/domain/models.py` - `RunSettings` now carries `gate_command`, `tools` and
  `permission_mode`; `PermissionMode` lives here too.

## How to verify

- `git log --oneline 1d043bb..HEAD` shows this session's work: Task 8 (`a184595..64f329a`), Task 9
  (`5c122b3..ea842b2`), Task 10 (`c93f5c1..8b5369d`), plus three backlog and plan commits.
- The gate is `make test` through `gate.py`, and it read `[PASS] make test (rc=0)` at `8b5369d`.
- CI and CodeQL were green on `3df1a00`
  (`ci_wait.py --sha 3df1a004533b7961b620e89d7213db3fff74bf86`).

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
