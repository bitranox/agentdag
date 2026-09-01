# agentdag build plan - MID level (components, interfaces, verification per milestone)

> **The `RESEARCH/` paths point into a private companion repo.** These documents were written
> beside a private research repository and cite it by repo-qualified path for the design
> documents, probe scripts and measurement notes they were derived from. The `RESEARCH/` prefix
> names that repo; it is deliberately not a relative path, because no relative path from here
> resolves to it. These citations do not resolve in a clone of this repo. They are kept rather than stripped because a claim that names its source
> is evidence of where it came from even when the source is not public, and removing them would
> leave the assertions here with no provenance at all.

Written 2026-08-17, revised 2026-08-21. Checked against the **2026-08-21 rewrite of
`build-plan-high.md`, commit `fa069c8`** (the one subtitled "the picture, the slice, the cuts",
written against `RESEARCH/workflow/design/2026-08-21-decomposition-design.md` and the E1 measurements in
`RESEARCH/workflow/probes/`). The body of this revision was written against `6881e74`; that page has since moved
three commits (`80c8f03`, `d3164f4`, `fa069c8`) and this page was re-checked against the result
rather than left claiming a pin it no longer matched. If
anything here widens the slice that page drew, that page wins and this one is wrong. The detailed
plan (`build-plan-detailed.md`) is written from this one, milestone by milestone, and only for the
milestone about to start.

What the high plan's rewrite changed on this page, so a reader of the 2026-08-17 version can see
it rather than infer it:

- the product is stated as **a job you can walk away from**, and four differentiators are what the
  build is judged against. All four are recorded there as BUILT; what remains of them is one crash
  test and one real run, both inside M5.
- **M4 (the Codex arm), the MCP north face and the whole deferred tail are CUT.** Their sections
  moved to `## Cut`, with the reason, rather than being deleted. There is no M4 any more; the
  numbering gap is deliberate.
- **decomposition became a milestone with sections of its own.** Sections for C1, C2 and M6 are new
  on this page. The 2026-08-21 text of this bullet read "decomposition is GATED, not scheduled. Two
  checkpoints (C1, C2) decide whether M6 is built at all". That gate was dropped later the same day
  as CROSS-AXIS, and the bullet is corrected rather than deleted so a reader of the older text can
  see it went; leaving it would have this page's summary contradicting its own M6 row and M6
  section. See `DECISIONS.md` item 1, and the M6 section for the reason.
- **M3's `stage`/`apply` exit criterion has an owner**, stated in the M3 section.
- **D2 is settled**: REBUILD stands, on the user's 2026-08-20 ruling.

**Effort figures.** The 2026-08-17 version headed milestones with day counts: S0 half a day, M1 one
day, D2 one day, M2 3-4 days, M3 3 days, M4 2 days, M5 1 day. None of them carried a stated basis;
the high plan dropped its own for that reason. They are recorded in this paragraph as UNEVIDENCED
history and appear nowhere else on this page. No replacement estimate is made.

Repository (D7): `projects/public/KI/agentdag`, created from `bitranox_template_py_cli` and its
`rename.sh`, bmk-managed, layered per the bitranox stack (domain / application / adapters /
composition, import-linter enforced), Python 3.12+, `uv`. Package `agentdag`, CLI `agentdag`.

---

## The slice, and what each milestone is judged against

| milestone                   | state                                     | judged by                                                                                                                     |
|-----------------------------|-------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| M0 design fixes             | DONE                                      | schemas validate against their examples; no C-id's old text survives a grep                                                   |
| S0 slice-0 probes           | DONE                                      | each probe has a written result under `RESEARCH/workflow/design/probes/`                                                |
| M1 graph A baseline         | DONE                                      | the baseline ran attended on scratch clones; see the caveat in that section                                                   |
| D2 adopt versus rebuild     | DONE, REBUILD stands                      | a decision, recorded, with its re-open trigger ruled on                                                                       |
| M2 the kernel               | DONE                                      | the section-9 rows M2 owns, each with its positive control                                                                    |
| M3 the three properties     | OPEN, part built on the M3 branch         | the section-9 negative tests with their positive controls, `stage`/`apply` INCLUDED                                           |
| M5 first real run           | OPEN, sequenced AFTER M6 as its demo      | the high plan's "What done means for the whole build", quoted in that section                                                 |
| C1 validate the judge panel | OPEN, needs the user, no code             | agreement on >= 5 of 6, or the panel's other 24 verdicts are discarded                                                        |
| C2 arm C/D                  | COLLECTED (both arms), judging waits C1   | informs M6's SHAPE - granularity, and whether briefs and a cost model earn their cost; it does NOT decide whether M6 is built |
| M6 decomposition            | OPEN, ungated (2026-08-21); M5 follows it | a wrong-by-construction plan re-plans, converges, and REPLAYS to the same plans                                               |
| M4 the Codex arm            | CUT                                       | see `## Cut`                                                                                                                  |
| L1 to L8 the deferred tail  | CUT, except L4 which M6 supersedes        | see `## Cut`                                                                                                                  |

---

## M0 - design fixes (documents only)

State: DONE. Inputs: review themes T3-T13, contradictions C1-C25. Output: `workflow/design/`
updated, schemas re-validated, second snapshot `workflow/staging-2/` committed with the commit
noted. The record below is what M0 changed; it is history and is not re-argued here.

Fix list, grouped by file (each item names the finding it closes):

- Design doc: executor `code` for code kinds and `-` for map/batch, schema conditional on kind
  (C1); the escalation rule when no higher row lists the role -> suspend into approve (C17);
  `stage_into` in the 2.1 field list and IN the journal key (C10); `isolation: none` redefined as
  "no worktree; writes confined to the node's artefact dir" (C8); run-root prefixes `manifest/`,
  `intents/`, `artefacts/` as the exception to read_only_prefixes (C7); journal vs audit membership
  stated once: journal = replay-affecting lines, audit = a superset copy (C6); `policy_version`
  and per-row token counters in state.json (C14); until-*/route are coordinator code and NOT
  node records - remove from the kind enum, draw as brackets (C16); map/batch tier_role null
  (C13); C24, C25 text fixes; a `run_limits` block (per-row run token cap, deadline ceiling,
  planner kind allowlist, per-kind ceilings, budget floor for `top`) referenced from 2.3/2.4
  (C15, M18); `agents_empty_result` defined and carried as `error.type` (M23, C25); the
  free-text lint replaced by "records carry `typed_fields`; the coordinator branches only on
  those" (M7); the journal key includes the hash of the assembled input written into the node
  dir (M20); whole-spec validation adds stage_into, brief_ref inside the run store, deps exist and
  acyclic (M19); the three unmeasured thresholds text (C24); Continue-As-New deleted, journal size
  a run_summary field (E9); policy review loop reduced to logging (E11, M25); every journal and
  audit line carries `at` UTC with explicit offset (O19).
- Section 3.4 lifecycle: single-writer lock file `{host, boot_id, pid, pid_start_time}`;
  decisions written by the server as files under `decisions/`, folded in by the coordinator; per-
  run systemd scope; startup sweep; cancel returns at once, verified by cgroup empty; deadline
  kills the cgroup (T4). Approve deadline owned by the server, default applied and journaled with
  `by: system`, defaults with external side effects rejected at validation, human-distinct identity
  for side-effect gates (T5). Executor auth failure is a distinct non-transient error; run.start
  refuses when a credential's remaining lifetime is shorter than the run's deadline (O22).
- Section 3.6 resources: host-level lease store; rate rows count completed calls in the window;
  probe cadence and TTL; "no eviction" instead of hysteresis; mutex rows carry `enforce`
  where a real lock exists; two-concurrent-runs test in section 9 (T6). Older texts corrected in
  COORDINATOR-DESIGN.md and A2 (C18-C20).
- Section 5/7 enforcement: post-node gate = isolation-root scan (mtime manifest before/after or
  inotify), not a per-worktree diff; Bash writes covered by the scan (M1, M2). Token cap call
  sites named: `max_turns` plus a per-turn usage check on the stream that interrupts the client
  when the row cap is passed; overshoot bounded by one turn, stated (M3). Derived-USD cap
  unavailable on rows with a null price pair (M4). Reconciliation as a second journal line
  `usage_reconciled` (M5). Replay-purity test = replayed key sequence equals the journal's (M6).
  Stage/apply: dedup key written before the effect, external state verified on replay (M9).
- Section 3.1/3.3 secrets and store: allowlisted child env, credentials read at call sites, nodes
  behind a `Sandbox` port (`none` | container, with a VM adapter only if work is pointed at repos we
  do not control; container FIRST in M3, before the other M3 mechanisms, because the measured hole
  is network egress; a separate-unix-user adapter is decided against, not deferred - it costs a
  store permission redesign the container does not need and leaves egress open), each adapter
  declaring what it enforces and that declaration journaled per node, token file 0600 hashed with
  identity/scopes/expiry and SIGHUP reload,
  args as typed template fields, run store `/var/lib/agentdag/runs` dirs 0700 files 0600 not SMB-
  exported, transcript/telemetry scrub before write, run store not a knowledge-index dataset by default,
  probe/enforce as argv allowlist, policy table not writable by the coordinator user (T3);
  retention per artefact class, per-run disk ceiling, worktrees from a bare mirror under the run
  dir with fileMode=false, pruned on exit and at startup, content-only diff (T13).
- Knowledge: 2.1 marks `knowledge`/`stage_into` as "not in slice 1, needs the knowledge-index project's 4.4 + 4.9";
  needs_context on unreadable knowledge; per-node scoped tokens; namespace definition (T8, C23).
- Schemas: node-spec (executor code, requires, knowledge marked deferred, no until-*/route kinds),
  result-record (`error.type` enum incl. `agents_empty_result`, `auth_failure`), journal-line
  (`usage_reconciled`, `run_started`, `resume`, `cancel_requested` with identity; `at` on every
  line), approve-payload (options ids = decision values), tier YAML (`run_limits`, mutex `enforce`,
  rate semantics comment), mcp-surface (event-name table mapping every journal event, C3; run.status
  per-row tokens, C12; policy runtime not build-time, C21; cancel returns at once, O25; approve
  scope per workflow, O21; owner on runs, O7).
- Graphs: `budget_usd` -> `budget`, branch on `charged_tokens` per row (C11); effort `-` where
  inherited (C9); A2's two stale passages (C18); C's `g_citations` described honestly (M24).

Verification of M0: schemas validate against their examples; a grep for each C-id's old text
finds nothing; the second snapshot's README names the commit.

Since superseded, and recorded here so the fix list is not read as current: the `knowledge` grant
is no longer the mechanism by which a node knows anything (see `## Decided, not yet owned by any
milestone`), and the mcp-surface fixes belong to a surface that is now cut.

---

## S0 - slice-0 probes (results into `RESEARCH/workflow/design/probes/`)

State: DONE. Each row below has a written result. Two of them fed work that is now cut and
therefore feed nothing: the Codex rollout probe (`s0-codex-rollout.md`) was M4's input, and the
mcp per-tool scopes probe (`s0-mcp-scopes.md`) was the MCP north face's. They are kept because a
measurement does not stop being true when the work it was for is dropped.

| probe                                        | how                                                                                                                                                                                                                                                                                                  | decides                                                                                                                          |
|----------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| dispatch cost, isolated child                | one `ClaudeSDKClient` with `setting_sources=[]`, `system_prompt` string, one trivial turn; read `ResultMessage.model_usage` and per-turn `usage`                                                                                                                                                     | `min_node_minutes` per row; whether `batch` matters at all (E4)                                                                  |
| planner validity                             | 20 emissions of a node spec from a `deep`-row model given the schema and a scenario-B brief; validate each with the schema and the 2.4 rules                                                                                                                                                         | rejection rate -> how much validation machinery; whether graph B is viable (E12)                                                 |
| Codex rollout per thread                     | one paid `codex` tool call through `codex mcp-server` (tiny prompt), then find the rollout file for the returned `threadId`                                                                                                                                                                          | reconciliation viable or Codex nodes stay charged in full (M5)                                                                   |
| mcp per-tool scopes                          | read `mcp` 1.29 `server/auth/*` and `fastmcp` for a per-tool scope hook; if none, note `get_access_token().scopes` inside tool code (M10)                                                                                                                                                            | how the CLI/MCP later checks `run:approve` per tool                                                                              |
| `Agent` through `claude mcp serve` (S-later) | one paid `Agent` call through `claude mcp serve` (tiny prompt, `mcp` stdio client; MEASURED 2026-08-17 that `tools/list` shows it with `prompt`, `subagent_type`, `model`, `isolation`, no `cwd`/usage): does it run headlessly, what returns, is there usage; result into `workflow/design/probes/` | whether a zero-code emergency row `mcp:claude/Agent` exists; a third column in the design's parity table either way (design 6.1) |
| scopes and users on the Linux dev host       | as a non-root service user: `systemd-run --user --scope`, `loginctl enable-linger`; can it create a scope, does the coordinator user differ from the node user                                                                                                                                       | D4; whether nodes get their own uid and cgroup                                                                                   |

The dispatch-cost row's number was later superseded: `m2-kernel.md` measured about 26,000 first-turn
tokens against S0's 170, which is why `min_node_minutes` is re-derived rather than reused
(`RESEARCH/workflow/design/min-node-minutes-derivation.md`).

---

## M1 - the baseline of graph A, in Python, on scratch clones

State: DONE, attended, written up in `RESEARCH/workflow/design/probes/m1-baseline.md`. **Read that write-up with
its caveat**: it names no artifact by path, and most of its evidence is gone (run 1's store and the
RET commits were destroyed by M2's own `git clone --mirror --refresh`), so its numbers are not
checkable. The high plan no longer rests the whole-build criterion on beating M1's interaction
count, because that count is 1 and one approve is mandatory by design.

Purpose: the honest baseline (E13). Decided by the user 2026-08-17: Python, not bash - structured
and testable; `uv run` from a PEP 723 header; the best libraries as the sibling projects use them
(pydantic, rich-click, filelock, the Agent SDK, lib_cli_exit_tools; git and make via subprocess);
scratch clones only; tests green on Linux and Windows. Decided 2026-08-17 (D7 pulled forward): it lives
INSIDE the `agentdag` repo created from `bitranox_template_py_cli` (bmk-managed; the gate and the
three-OS CI come with it), in the template's layers - `domain/graph_a.py` (records, pure functions),
`application/graph_a_ports.py` + `application/graph_a.py` (ports, the graph as code),
`adapters/graph_a/` (git CLI, make gate under filelock, fs run store, Claude SDK work, console
approve), `adapters/cli/commands/graph_a.py`, `composition/graph_a.py`.

Components (typed pydantic records throughout; the graph as code in `run_graph`): `scratch`
(bare `git clone --mirror` of each REAL repo into `<scratch>/origin/<name>.git` - the ONLY push
targets, guarded in `apply`); `discover` (REPOS.txt -> paths); `make_worktree` (clone of the
scratch origin under `<run>/wt/<name>`, `core.fileMode=false`); `work` (`ClaudeSDKClient`, `cwd`
= worktree, `setting_sources=[]`, `system_prompt` = the brief, `acceptEdits`, `max_turns` 25;
returns turns/tokens/cost from `ResultMessage`); `gate` (`make test`, exit code kept per repo);
`reduce_tally` (passed/failed + rows); `stage` (intents with dedup key `<name>-<sha>`); `approve`
(`click.confirm`, attended on purpose); `apply` (done marker per key, an external-state check on
the target ref, push to the scratch origin's default branch; refuses any target outside
`<scratch>/origin`).

Verification: tests at the real seams (no monkeypatching of own code) - domain pure functions;
adapters over real temp git repos (mirror/clone/head/default branch, the gate as an injected
command's exit code plus an `integration` test on real `make`, the store layout); the graph end to
end with a work FAKE that commits a file and a yes-approver, asserting the scratch origin advanced
and the REAL repo did not; apply once then replay pushes nothing; apply refuses a non-scratch
target. `make test` green (ruff, pyright strict, import-linter, pytest, bandit, pip-audit); CI green
on ubuntu/windows/macos once the repo is on GitHub (the user's go was a STOP point); one
hand run on the Windows dev host for the SDK env edge. Then end to end on scratch clones of two real repos with
a trivial brief, then with the real fleet-chore brief, attended; interactions counted (measured 1);
wall time, tokens per branch, and the properties the baseline lacks (replay after a crash, a token
cap, an unattended approve) written to `m1-baseline.md` - D2's input.

The 2026-08-17 text of this section had the gate running under one host-wide `filelock`. That lock
was retired once bmk 3.17.0 began guarding its own tool environment; the gate no longer serialises.

---

## D2 - adopt versus rebuild (a document)

State: DONE and SETTLED. `RESEARCH/workflow/design/D2-adopt-vs-rebuild.md`: for replay, cap and approve, it
compares (a) the hand-rolled kernel of M2-M3 with (b) a DBOS-backed coordinator
(Postgres-checkpointed steps, exactly-once side effects) and (c) Temporal, against the SAME
section-9 negative tests, the operational requirements of T3-T4 (single writer, scopes, secrets),
the dependency footprint on the Linux dev host, and the two-tier journal-format claim. It decided REBUILD,
and M2 was built on that decision.

**Its re-open trigger fired and has been ruled on.** The document set the trigger at about 300
lines of resume plus journal code; `m2-kernel.md` measured 522 raw lines and 237 code lines,
independently re-counted. The user ruled on 2026-08-20 that **the code-line reading governs**, so
237 against a ~300 threshold does not meet the condition and **REBUILD stands**. The trigger is
therefore closed, not merely unanswered, and this is not re-opened on a raw-line count.

---

## M2 - the kernel

State: DONE, on `main`. The layout and interfaces below are what shipped; later milestones are
written against them.

Package layout (`src/agentdag/`):

- `domain/`: `models.py` (NodeSpec, ResultRecord, JournalLine variants, RunState - Pydantic,
  frozen; mirrors the schemas), `enums.py` (Kind, Status, ExecutorKind, Isolation), `keys.py`
  (canonical spec -> `v2:sha256:...`, the enumerated field set of design 3.2), `errors.py`
  (BudgetExceeded, DeadlineExceeded, LockHeld, SpecRejected, AuthFailure).
- `application/ports.py`: `Executor` protocol (`run(spec, brief, env, cwd) -> ResultRecord`),
  `Journal` (append/replay), `Store` (node dir, artefacts), `Clock` (the ONLY time source),
  `Lock` (acquire/release with holder identity), `Scope` (start/kill a run's cgroup).
- `application/use_cases/`: `dispatch.py` (key -> replay or run; started/result lines), `run.py`
  (load workflow module, resolve policy, loop over ready nodes with deps), `resume.py`, `gate.py`,
  `reduce.py`.
- `adapters/`: `journal_jsonl.py` (single writer, O_APPEND, one file), `lock_file.py`
  (`{host, boot_id, pid, pid_start_time}` O_EXCL, liveness by pid+start_time), `clock_utc.py`,
  `scope_systemd.py` (`systemd-run --scope`; kill = stop scope; cgroup-empty check),
  `executor_claude.py` (ClaudeSDKClient, allowlisted env, `setting_sources=[]`, PreToolUse hook
  for path deny + Bash denylist, `max_turns`, per-turn usage check), `store_fs.py` (run dir
  layout, 0700/0600, worktree from bare mirror), `policy_yaml.py` (tier table + run_limits, read-
  only), `cli/` (`agentdag run start|status|records|resume|cancel|approve`, rich-click).
- `composition/`: production wiring.
- `workflows/graph_a.py`: graph A as a coordinator program using the primitives.

Interfaces that later milestones rely on: `Executor.run` signature; `Journal.append(line) ->
None` and `Journal.replay() -> dict[key, ResultRecord]`; `Lock.acquire(run_dir) -> LockToken`;
`Clock.now() -> datetime (UTC)`; `Scope.start(run_id, argv) -> ScopeHandle`, `Scope.kill(handle)
-> verified: bool`. Added by M2 and relied on by M3 and M5: the coordinator's `stage`, `apply`,
`approve` and `map` primitives, and the run directory's `intents/<kind>/<key>.json` layout.
The MARKER half of that layout is no longer what M2 shipped: M3 replaced the single
`done/<kind>/<key>` with the two-phase `attempted/` then `done/` pair, because one marker written
after the effect cannot describe the window it leaves. See this page's M3 section; a reader of the
M2 text alone gets the wrong guarantee.

Verification (from section 9, the rows M2 owns): crash-window resume re-dispatches exactly the
started-without-result node; replay of a finished run dispatches zero nodes and its dispatch key
sequence equals the journal's; a coordinator calling `time.time()` fails at run import (the
import-time check) and, in slice 1, the cheap form: primitives receive the `Clock`; the lock
refuses a second coordinator on the same run dir; the isolation-root scan flags a file written
outside the worktree by a Bash `tee`; a `{}` result is `failed` with `error.type =
agents_empty_result`; no known token prefix appears in any transcript of a test run (grep).

---

## M3 - the three properties

State: OPEN, and **larger than this section used to imply**. M3 is Tasks 19-27 in the detailed
plan, not the six mechanisms this page listed. Everything that was on the `feat/kernel-m3` branch
is now merged to `main` (the branch is a strict ancestor of it).

BUILT and tested: the `Sandbox` port with its `none` adapter (Task 19); the token cap at both call
sites (20); the node deadline (20); `run cancel` with the startup sweep (21); the approve
deadline's owner (22); `stage`/`apply`'s two-phase marker with its crash-window negative tests
(the criterion that had had no owner - closed 2026-08-21); the notification sink and the crash
detector (23).

STILL OPEN, and it was undercounted as "two things left" in a handover on 2026-08-21. The table
below was re-checked against the code on 2026-08-27, at agentdag `main` `d737d86`, 112 commits
after this page was last written. One row has since CLOSED and is kept, corrected, rather than
deleted; one has moved half-way. Where this page and the code disagreed, the code won.

| #   | what                                                                                       | state                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
|-----|--------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 24  | the retry path                                                                             | **CLOSED 2026-08-22, under a different verb than this page named.** The 2026-08-21 text read "the retry path, `resume --from <node>` - not started; no `--from` in the CLI", and it is corrected rather than deleted because `--from` was not built late: it was DECLINED (`DECISIONS.md` item 13 - a red gate does not fail the run, graph A routes it into a tally row and the run reaches `done`, so the verb would have to relaunch a DONE run, which two of `resume`'s stated properties refuse). What shipped is TWO mechanisms, not one. Automatic: a code-kind node whose failure was TRANSIENT is re-dispatched by the coordinator, `Coordinator._auto_retries` at `application/kernel/context.py:1216`, capped by `policy.max_attempts` (2 in the shipped table, so one retry) - item 11. Operator: `agentdag run retry RUN_ID NODE_ID` at `adapters/cli/commands/run.py:387`, bound to the (node id, journal KEY) of the failed attempt, so the granted attempt runs under `attempt + 1` and the grant can never match twice - item 13. Design: `RESEARCH/workflow/design/2026-08-22-retry-grant.md`. Item 13 records what stays open inside it: **nothing withdraws a grant, and nothing lists a run's grants short of reading its journal** |
| 25  | serve a stored record only to the node it belongs to, plus a `key_collisions` drift signal | not started - and this is a LIVE DEFECT, not just unbuilt scope. RE-VERIFIED at source 2026-08-27: `node_id` is absent from `_IDENTITY_FIELDS` (`domain/keys.py:29-43`), the serve path matches on `call.key` alone with no node check (`application/kernel/dispatch.py:177-180`), and `key_collisions` has ZERO occurrences in `src/` and in `tests/`. So two nodes with the same brief, inputs and spec fields genuinely collide and the second is handed the first's record. Rare in a hand-authored graph, ordinary in a model-emitted one, which makes it an M6 prerequisite. Worth recording beside it: the dispatcher's own docstring (`dispatch.py:63-70`, written 2026-08-18 in `de5b0e4`) still calls this "deliberate dedup ... not a collision". It predates the user's 2026-08-20 ruling that it is a defect, so the code and its own docstring now disagree about what this is, and closing 25 means editing both                                                                                                                                                                                                                                                                                                                                |
| 26  | the carried Minors                                                                         | one of five was done at the 2026-08-21 revision (the approve refusal branches); a SECOND has since moved HALF-WAY, off-plan. `StrEnum` for `TierRow.billing`/`Escalation.*`: `Escalation.on_auth_failure` and `Escalation.on_rate_limit` are now typed `FailureAction`, shipped with the auth and rate-limit work rather than as this Minor, while `TierRow.billing` (`domain/policy.py:67`), `Escalation.then` and `Escalation.no_higher_row` are still bare `str`. Fully open: `--parallel`/`--policy` persisted in `state.json` (`RunState` carries `policy_version` and neither of these); the scanner-vs-live-executor vanish race; the alias-rebinding evasion of `workflow_check` (`t = time; t.time()` is invisible to `_alias_map`, `application/kernel/workflow_check.py:176`, which walks only `Import`/`ImportFrom`)                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 26a | gate-lock retirement                                                                       | unchanged. Lock removed; the concurrency test proves two gates run at once, but the half this plan itself calls "the half that actually matters" - a genuinely failing branch still fails while another gate runs beside it - is untested                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 27  | the attended M3 run, its measured note, the PR                                             | not started. Re-checked 2026-08-27: there is still no `RESEARCH/workflow/design/probes/m3-kernel.md`, and `bitranox/agentdag` still carries only PR #1 (the graph A baseline) and PR #2 (the M2 kernel), both merged                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| -   | the `bmk-tool-env` resource decision                                                       | open, and the USER's; the two-concurrent-runs test follows it. It has since acquired a contradiction in shipped CONFIG rather than in prose - see "Shipped off-plan, after this page was last written" below - and the row-to-milestone table further down records what the decision leaves conditional                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
Components and where each stands:

- **Token cap**: `budget.tokens{row: n}` per node; the claude adapter passes `max_turns` and checks
  streamed usage per turn, interrupting the client when the row cap is passed; the run-level cap
  from `run_limits` refuses the next dispatch; overshoot bounded by one turn, stated in the record.
  Built on the M3 branch.
- **Deadline**: the NODE deadline is enforced at the token cap's own turn seam and records
  `cancelled` with `error.type = deadline`. Built on the M3 branch, with negative tests and a
  control. **The RUN deadline does NOT exist and this page used to say it did.** Ground truth
  2026-08-21: the only run-scoped quantity is `deadline_ceiling_s`, whose own docstring calls it
  "the largest `deadline_s` any node may declare" and which is applied as
  `min(spec.deadline_s, ceiling)` - a per-node clamp. Design section 9 carries ONE deadline row and
  it is per-node. Nothing kills a run on elapsed wall-clock except the operator's own `run cancel`.
  Whether a run-level kill is wanted is OPEN, owned by nobody, and NOT part of M3.
- **Cancel**: intent recorded, returns at once; verified when the scope's cgroup is empty; journal
  `cancel {verified: true}`; a startup sweep reports whether it confirmed a left-behind scope
  stopped. Built on the M3 branch.
- **Approve**: the node writes the payload, `state=suspended`, the process exits; `agentdag run
  approve` validates the identity, writes `decisions/<node>.json` by temp+rename, relaunches; a
  timer (`agentdag-approve-timer`, systemd) applies the payload's default at `decide_by` and
  journals `by: system`; a default with an external side effect is rejected at validation; the
  deadline is owned by one place and `decide_by` is never recomputed. Built on the M3 branch.
- **`stage`/`apply` - THIS IS M3'S, and it is the criterion no earlier task owned. DONE
  2026-08-21**, and the verification changed the mechanism. `stage` writes
  `intents/<kind>/<dedup_key>.json` BEFORE any effect, as M2 shipped it. What the verification
  exposed is that a single `done/<kind>/<dedup_key>` marker, written AFTER the effect, cannot
  describe the window it leaves: a crash in there applies an irreversible effect with nothing
  recording it, and the docstring claiming `perform` is called "at most once per dedup key, ever"
  was false across it. The journal's crash window does not cover it either, being per NODE while
  one apply node carries every intent its stage node staged.
  **Decided with the user 2026-08-21: apply records in two phases** -
  `attempted/<kind>/<dedup_key>` before the effect and `done/<kind>/<dedup_key>` after - so the
  pair says PER DEDUP KEY whether that effect may already have happened. The kernel supplies the
  fact and does not act on it: `perform` (now the `PerformIntent` protocol, taking
  `may_have_landed`) owns the policy, because whether a repeat is harmless depends on what the
  effect is. A readable effect ignores it and reads the target - graph A compares the REF, not
  whether the object exists, so a push whose objects transferred and whose ref update was rejected
  is retried rather than abandoned. An effect that CANNOT be read back has nothing else to go on
  and refuses.
  Verification, both RED-verified by mutation:
  `test_a_crash_between_the_push_and_its_marker_replays_without_pushing_twice` (real origin, real
  push, crash injected at the shipped `git=` seam, control winds the ref back and requires a push)
  and `test_an_effect_that_cannot_be_read_back_refuses_the_repeat_the_next_launch_would_make`
  (the consumer proof: the fact reaches a perform with no external state, and stops the repeat).
  **Open, and NOT part of this**: the run summary does not aggregate `key_facts["resumed"]`, so an
  operator sees a resumed effect on the apply record and not in the run's own summary line.
- **Notification sink** (decided 2026-08-18). **DONE 2026-08-21** (`e40f8ae`), and the build
  settled one thing the decision left open. A typed `RunEvent` goes through one `Notifier` port to
  one of two sinks: `NoNotifier` (the default) and `MailNotifier` over the repo's `btx_lib_mail`
  adapter, chosen by `kernel.notify`. Claude Code's PushNotification is NOT built - a third sink is
  additive behind the same port.
  **Who emits which event**, because the two source documents disagreed: the design (line 431) says
  the deadline owner emits and "never the exited coordinator", the mid plan said "the coordinator
  and the approve timer". Read as the design speaking about the EXITED coordinator, the split is
  by EXIT: the coordinator emits `suspended` (carrying the payload's own text and `decide_by`, read
  back from the file the decider is shown), `done` and `failed`, because those are its three exits;
  `crashed` is the exit that writes nothing, so nobody inside can emit it. The plan's own negative
  test decides it - "a run that suspends produces EXACTLY one notification" is what a
  coordinator-side emit gives for free, while a periodic pass over a suspended run would re-send
  every tick.
  **`crashed` therefore has a DETECTOR**, in `application/kernel/crash.py`, run by
  `run apply-deadlines` (decided with the user 2026-08-21). A crash needs three facts, not one:
  state `running`, a lock that can be taken (a live coordinator holds its own for the whole
  launch), and a non-empty journal (the first line is appended AFTER the lock). The second and
  third separate a crashed run from a STARTING one, since `run start` writes `state=running` before
  the background coordinator exists. Writing `state=crashed` is also the dedup, and it gives
  `RunStatus.CRASHED` its first producer - nothing had ever written that value.
  A sink cannot fail a run: `emit_best_effort` contains whatever it raises. The cost is stated
  where it is paid - a failing sink is silent, since the journal has one writer and no line type
  for this. Mail here is the OPERATOR's channel, not a node's side effect, so it does not go
  through stage/apply; a node has no handle on the port.
- **The one resource** - and it needs a decision M3 owns. The 2026-08-17 text had
  `bmk-tool-env` as a host-level lease under `/run/lock/agentdag/`, taken by the gate wrapper so it
  held across concurrent runs. **The gate no longer serialises**: bmk 3.17.0 guards its own tool
  environment and the wrapper's lock was retired. The `bmk-tool-env` row nonetheless still exists
  in the shipped tier policy, in `node-spec.schema.json` and in graph A's `requires`. M3 must
  settle whether lease admission ships at all in this slice or the row goes; the two-concurrent-runs
  test below follows whichever way that lands, and this page must not become a fifth place
  asserting a retired lock as current.

Verification: the section-9 rows for budget cap, node deadline, approve suspends,
approve auth, cancel verified, **stage/apply idempotency across a crash between the effect and its
marker**, plus the two-concurrent-runs test if the resource decision keeps it - each with its
positive control, since a negative test whose input can never reach the mechanism passes vacuously.
### Which section-9 row belongs to which milestone

The design points HERE for this list: "the list is the acceptance contract of the build plan; which
rows belong to which milestone is in `./build-plan-mid.md`"
(`RESEARCH/workflow/design/2026-08-17-agentdag-design.md:970-971`). So does the high plan's M3 exit criterion:
"the section-9 rows THIS MILESTONE OWNS pass with their positive controls (see the mid plan's
assignment; four section-9 rows are unreachable under the current cuts and are not M3's)"
(`build-plan-high.md:209`). **No such table has ever existed on this page**, and neither document
names the four. M3's exit criterion has therefore not been evaluable since it was written. The
table below is written 2026-08-27 to close that. It covers all twenty rows of design section 9 in
the design's own order (`2026-08-17-agentdag-design.md:975-994`).

Read `owner` as ASSIGNED BY A DOCUMENT wherever the evidence column cites one. Two rows are cited
by nothing; they are marked UNASSIGNED and are not quietly given an owner here.

| #  | section-9 row                | owner                                                                                                             | evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
|----|------------------------------|-------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1  | budget cap                   | M3                                                                                                                | M3's verification list above names it                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 2  | context ceiling (3.8)        | UNASSIGNED - it was deferred out of M3 and then built off-plan                                                    | deferred OUT of M3 deliberately at `build-plan-detailed.md:2951`, to wait on Task 27's measurements; BUILT anyway 2026-08-22 to 08-24. Needs an owner named, and the owner inherits the missing consumer - see "Shipped off-plan, after this page was last written"                                                                                                                                                                                                        |
| 3  | deadline                     | M3, PER-NODE only                                                                                                 | M3's verification list names "node deadline". There is no run-level deadline and this page used to say there was - see the Deadline bullet above; whether a run-level kill is wanted is open and owned by nobody                                                                                                                                                                                                                                                           |
| 4  | approve suspends             | M3                                                                                                                | M3's verification list                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 5  | approve auth                 | M3                                                                                                                | M3's verification list                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 6  | crash-window resume          | M2                                                                                                                | M2's verification list: "crash-window resume re-dispatches exactly the started-without-result node"                                                                                                                                                                                                                                                                                                                                                                        |
| 7  | replay purity                | M2                                                                                                                | M2's verification list: the key sequence equals the journal's, plus the import-time `time.time()` check                                                                                                                                                                                                                                                                                                                                                                    |
| 8  | mcp round-trip               | UNREACHABLE - blocked by a CUT                                                                                    | the MCP north face is cut (`## Cut`, "The MCP north face / server surface (L1)"), so there is no `mcp` node whose malformed content could be observed                                                                                                                                                                                                                                                                                                                      |
| 9  | resource overlap -> serial   | UNREACHABLE - blocked by a CUT                                                                                    | L2, resources beyond one lock, is cut (`## Cut`, "The deferred tail"); capacity-amount admission is not built                                                                                                                                                                                                                                                                                                                                                              |
| 10 | two concurrent runs          | SPLIT. The LOCK half is M2's and DONE; the MUTEX half is M3's and CONDITIONAL on the open `bmk-tool-env` decision | M2's verification list has "the lock refuses a second coordinator on the same run dir"; M3's has "the two-concurrent-runs test if the resource decision keeps it"; the decision is the `-` row of the STILL OPEN table above                                                                                                                                                                                                                                               |
| 11 | write-set enforcement        | M2, the isolation-root scan - extended off-plan                                                                   | M2's verification list: "the isolation-root scan flags a file written outside the worktree by a Bash `tee`". Extended by `DECISIONS.md` item 12 to judge a write against the node's OWN declared set - see "Shipped off-plan"                                                                                                                                                                                                                                              |
| 12 | stage/apply idempotency      | M3, DONE 2026-08-21, with the mechanism changed                                                                   | the `stage`/`apply` bullet above: the single `done/` marker became the two-phase `attempted/` then `done/` pair                                                                                                                                                                                                                                                                                                                                                            |
| 13 | cancel verified              | M3                                                                                                                | M3's verification list                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 14 | empty result counted         | M2                                                                                                                | M2's verification list: a `{}` result is `failed` with `error.type = agents_empty_result`                                                                                                                                                                                                                                                                                                                                                                                  |
| 15 | auth failure                 | **UNASSIGNED**                                                                                                    | no milestone on this page, in `build-plan-high.md` or in `build-plan-detailed.md` claims it. It needs an owner or an explicit "not in this slice". The 2026-08-26 rate-limit work landed next door to it without claiming it                                                                                                                                                                                                                                               |
| 16 | approve default has an owner | M3                                                                                                                | Task 22 in the BUILT list above, and the Approve bullet                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 17 | secrets stay out             | M2                                                                                                                | M2's verification list: "no known token prefix appears in any transcript of a test run (grep)"                                                                                                                                                                                                                                                                                                                                                                             |
| 18 | spec validation              | M6                                                                                                                | `build-plan-detailed.md:2996` makes "a real spec validator (nothing reads `planner_kinds` today)" part of M6's FIRST task. The validator itself was built off-plan 2026-08-22 and is UNWIRED, which does not move the row - see "Shipped off-plan"                                                                                                                                                                                                                         |
| 19 | tier clamp                   | **UNASSIGNED**                                                                                                    | no milestone claims it, and the ground under it has moved: `DECISIONS.md` item 9 changed the NEIGHBOURING `run_limits` rule, `per_kind_ceiling`, from clamp to REFUSE, while this row's own field `top_role_budget_floor` remains parsed and never enforced. Verified 2026-08-27: it occurs only at `domain/policy.py:155` (the field) and `policy/tier-policy.yaml:238` (the value), not even in the unwired validator, so the row's negative test has nothing to observe |
| 20 | unreviewed knowledge         | UNREACHABLE - blocked by a CUT                                                                                    | the row's own text is "LATER, with the knowledge grant (3.7) ... (needs the knowledge-index project's 4.4 + 4.9, cross-repo)" (`2026-08-17-agentdag-design.md:994`), and `knowledge` is cut as a MECHANISM (`## Cut`, last section)                                                                                                                                                                                                                                        |

**On the high plan's "four unreachable" - this is a DERIVATION made here on 2026-08-27, not a claim
recovered from an earlier document. Nobody ever wrote the list down.** Three rows are blocked by a
CUT and cannot be reached at all under the current cuts: `mcp round-trip`, `resource overlap ->
serial`, `unreviewed knowledge`. That is three, not four. The only remaining row whose reachability
turns on anything other than work being done is `two concurrent runs`, and it is CONDITIONAL rather
than cut: its lock half already passes as M2's, and its mutex half stands or falls with the open
`bmk-tool-env` decision. So the answer is THREE, and the high plan is corrected to three as of 2026-08-27 (USER decision): its four reconciles ONLY if that conditional row is counted
among the unreachable ones. If the decision keeps lease admission the true figure is three; if it
drops the row it is four. Neither UNASSIGNED row is unreachable - being unowned is a different
problem, and this table does not close it.


---

## M5 - first real run

State: OPEN, and **sequenced after M6** (decided with the user 2026-08-21). This is where the
remaining cost of the differentiators is paid: the high plan puts it at one crash test and one real
run, and both are here.

**M5 is M6's demonstration, and the graph is MODEL-emitted.** The ordering was settled once M6
stopped being a gated maybe: a hand-authored graph for a complex task demonstrates the substrate on
a new task shape and says nothing about the thesis, which is the same objection this plan already
makes to demonstrating on graph A. Running M5 first would also mean authoring a second workflow
program on the scale of `graph_a.py` and its adapters - a cost that appeared in no estimate - and
then throwing it away when the planner landed.

Inputs: M3 closed; M6 built; and **ONE COMPLEX TASK, not a repository sweep** (decided with the
user 2026-08-21). Graph A is what M1 to M3 proved the substrate on and it stays that.

Selection takes three clauses, per the high plan's criterion:

1. it genuinely needs DECOMPOSITION - the eleven-in-fourteen shape E1 measured;
2. it needs JUDGEMENT somewhere, or the run tests scheduling rather than the thing being built;
3. it carries a REAL IRREVERSIBLE EFFECT, or apply-once and crash-resume cannot be exercised.

Clause 3 is the binding one and it was nearly missed. Reading E1's eleven single complex tasks,
six look like they end in a report or a diagnosis and would leave half the criterion untested,
while five look like they end in an effect (`docs-claims`, `ci-crossplat`, `feature-cap`,
`data-consolidate`, `ops-wedged`). **That reading is a judgement from one-sentence descriptions,
not a measured property** - scoping a security review to end in commits moves it across the line.
Treat it as a shortlist and apply clause 3 to the task as actually scoped. Prefer a commit-shaped
one for a first unattended run: `data-consolidate` rewrites a knowledge store and `ops-wedged`
touches a live host, and neither blast radius belongs here.

Components: task selection against those three clauses, applied to what the PLANNER must be able
to decompose rather than to what a human would; scratch clones as the only push targets,
unchanged - the effect must be real but its blast radius must not be; the run started unattended
(the 2026-08-17 text had it attended, and the high plan's criterion is an unattended run); one
planned interaction, the approve, whose payload must carry enough to answer without opening
anything else; a kill mid-flight and a resume; the `run_summary` with its raw signals.

Verification is the high plan's "What done means for the whole build", not a restatement invented
here: exactly one planned interaction and zero unplanned ones, counted honestly including every
time the operator had to look; the run killed mid-flight and resumed, redoing no finished unit;
spend visible per row in tokens with no row over its cap; no secret in the run store (grep for the
known token prefixes and find none); every push behind the one approve payload, and no effect
applied twice across the kill. **And the payload clause**: if the approve payload did not carry
enough for the user to answer without opening anything else, the run failed this criterion even if
every test above passed.

Anything that broke goes to HANDOVER as the next plan's input. M1's baseline is retired here as the
control; it is not a comparison target, for the reason given in the M1 section.

---

## C1 - validate the judge panel (a checkpoint; no code ships)

State: OPEN, needs the user, roughly ten minutes of their time. **C1 comes before C2 is judged.**
The panel that would score C2 is the instrument under test here, and running an unvalidated
instrument on a second experiment produces two results to discard instead of one.

Inputs: E1's 14 task pairs and the panel's 42 verdicts (`RESEARCH/workflow/probes/probe_e1_panel.result.json`);
the packet `RESEARCH/workflow/probes/e1_control_packet.md`, six pairs drawn by seed with the arm labels stripped
and the order seeded; the sealed key `RESEARCH/workflow/probes/e1_control_key.json`, which carries the mapping and
the pre-registered falsifier and is not opened until the packet is scored.

Components: no code and no build. The instrument is the three-lens panel (coverage, executability,
proportion). The control is one human scoring six pairs cold: a preference per pair, a 1-5
EXECUTABILITY score per plan, one line of why. The panel's verdicts stay hidden until scoring is
done.

Interfaces: the packet is self-contained (task text and both plans inline), so scoring it needs
nothing else open; the key is the only link between a label and an arm.

Verification: agreement with the panel on **>= 5 of 6** preferences. Otherwise the panel's verdicts
on the other 24 pairs are **DISCARDED, not caveated**. A second falsifier is registered in the key:
real spread in the human's arm A executability scores, where the panel sat at exactly 2.00 with
zero variance, also discards - a constant with no variance is a floor produced by a defect rather
than a distribution produced by the tasks.

Two limits, stated here so they are not claimed away later. **The format cannot be blinded**: one
plan in each pair is a JSON graph and the other is prose, and no design fixes that. So C1 tests
whether a human reaches the same verdict as the machine panel; it does NOT test whether either is
reacting to format rather than content, which is C2's job. And **every judge on the panel shares a
model family with the planner it grades**, which is the panel's largest validity threat and the
reason this checkpoint exists at all.

---

## C2 - arm C/D: does a graph that carries its instructions beat prose

State: COLLECTED. Both arms are complete in
`RESEARCH/workflow/probes/probe_decomposition_briefs.result.json` (arm C 14/14 parsed, schema-clean and acyclic;
arm D 13/14, one `parse_error` on `ci-crossplat`), and the file records `"judged": false` on
purpose - judging before C1 passes would only produce a second set of verdicts to discard. The
older `armC.partial.json` filename this section used to cite was superseded at `15825ea` and no
longer exists.

**What C2 now decides, and what it no longer decides (2026-08-21).** It informs M6's SHAPE - node
granularity, and whether briefs and a cost model earn their cost. It does NOT decide whether M6 is
built. That gate was dropped as cross-axis: C2 compares a structured graph against free prose at
producing a plan, while M6's reason to exist is whether a model-emitted plan can be EXECUTED
durably across a crash outside a session, which no arm measures.

Inputs: the same 14 tasks, the same schema, the same scorer and the same SDK call, **imported from
`probe_decomposition.py` rather than copied**, so arm C cannot silently drift from the arm A it is
compared against; arm A's result as the structural baseline; arm B, free prose, as the control.

Components: the source read of Claude Code's Workflow tool is **DONE** (2026-08-21, the CLI binary
at `~/.local/share/claude/versions/2.1.238`) and it did NOT confirm the premise. Workflow's script
is authored and AST-frozen before the run, so it does deterministic control flow; what read as
mid-run re-planning is the outer model editing the script between invocations behind a prefix
cache, with same-session-only resume. But a scope agent CAN return a schema-bounded work list the
script fans out over, so "the work list is not known up front" IS covered. What is left uncovered
is a model emitting a work-unit spec the ENGINE executes, durably, across a crash, outside a
session. **The high plan therefore marks M6's gating conclusion UNDER REVIEW**: it was reached from
the false premise, and whether the narrower capability justifies a milestone is a scope decision
rather than a further measurement.

The remaining component is `RESEARCH/workflow/probes/probe_decomposition_briefs.py`, one variable per step.

| arm | what it emits                                | what the step isolates         |
|-----|----------------------------------------------|--------------------------------|
| A   | node specs only (already run)                | topology, no instructions      |
| C   | `{spec, brief, output_contract, acceptance}` | A -> C isolates INSTRUCTIONS   |
| D   | arm C plus the node cost model               | C -> D isolates COST AWARENESS |

Interfaces: `spec` stays the existing `NodeSpec`, **unchanged**. The plan entry is a schema ABOVE
`NodeSpec`, which is exactly the shape M6 would formalise, so C2 measures the proposed fix rather
than the known-broken current state. The `output_contract` is the load-bearing part: the
coordinator may branch only on typed fields, so without a declared contract a plan is not
executable as control flow whatever its topology looks like.

Verification: mechanical scoring (parse rate, schema errors, acyclicity, dependency resolution,
node counts) comes from the probe itself and needs no panel. The 2026-08-21 text of this line read
"**arm C beats the prose control, or decomposition is cut**". That criterion was RETIRED the same
day, by this section's own "What C2 now decides, and what it no longer decides" paragraph, which
this line has contradicted ever since. It is corrected rather than deleted so a reader of the older
text can see it went: what C2 scores now informs M6's shape, and no score here cuts anything.

Caveats that travel with the numbers: arm D's budget figure is chosen, not measured, so arm D
answers how the planner responds to THIS cost model and never what the correct graph size is; and
the panel's `proportion` lens prompt states the per-node cost, so its verdict on graph size is not
independent of the prompt it was given.

---

## M6 - decomposition (a milestone; UNGATED as of 2026-08-21; DESIGNED 2026-08-28)

State: DESIGNED, NOT STARTED. Governing design: `RESEARCH/workflow/design/2026-08-28-planning-loop-design.md`,
six decisions taken with the user on 2026-08-28, each asked one at a time against its alternatives
(recorded in agentdag's gitignored `EXECUTION-USER-REVIEW.md` under that date). Sections 1, 2, 5
and 6 of `RESEARCH/workflow/design/2026-08-21-decomposition-design.md` still hold (the entry fields, the goal on
the run, what a node knows, plan approval by threshold); its section 3 flat loop is AMENDED by
decision 1. The three dispatch-seam designs of 2026-08-21/22 stay DEAD; decision 3 is the successor
their reviewers converged on.

The gate that once stood here (C1 then C2) was dropped as cross-axis on 2026-08-21; `DECISIONS.md`
item 1 governs. C1 and C2 inform M6's SHAPE, they do not decide it. Why it is a milestone at all:
decomposition on its own is covered by the closest predecessor (a schema-validated work list from a
scope agent); M6 earns its place only where it COMPOSES with the differentiators - a decomposition
that survives a crash, suspends at an approve when the plan is too expensive, and whose nodes
cannot repeat an effect.

**In one sentence:** a recursive DAG scheduler in which a planner node emits a plan for ITS OWN
subtree over a registry of operator-defined ops, re-plans only when a typed condition the plan
itself declared is refuted, and hands completion to code over records, a stake-free judge, or a
person - in that order. Every part of that is derived from the five constraints in
`agentdag/docs/why-agentdag.md`, not from how a human team would do it.

Inputs: the design above; the M2 kernel unchanged underneath. The substrate does not change:
journal, content-addressed keys, replay, the run lock, crash resume, `approve`, `stage`/`apply`,
worktree isolation, records rather than prose, content in the store rather than in any context.

### Components

1. **Plan, Entry, Condition** (decisions 1 and 2). A plan is `{goal, entries[], holds_while,
   done_when, deps}`; an entry is `{spec, op, args, brief, output_contract, acceptance}`, where
   `op` is a registered name or `plan` for a sub-goal. `Condition` is a small typed expression over
   `key_facts` fields - no free text, no model call; graph C's integer conditions are the floor.
   `NodeSpec` is untouched. Node ids are allocated by the coordinator; `deps` may name only nodes
   already in the graph or earlier entries of the same plan; a plan is accepted whole or refused
   whole with `reasons[]`. Detailed: Task 29.
2. **The op registry** (decision 3). The composition root registers named ops, each with a body
   constructor, an arg model, an output contract and a `can_change_state` flag; the coordinator
   dispatches by lookup; an unregistered op is refused at plan-accept time. This RETIRES
   `validate_dispatchable` (zero callers in `src/`) and `planner_kinds` (the registry IS the allow
   list; `apply` is simply never registered, so `DECISIONS.md` item 8 stands). Detailed: Task 30.
3. **The planner op and the recursive `execute` loop.** `dispatch(op="plan", goal, evidence)`
   returns a validated `Plan`; `execute(plan)` dispatches ready entries under `parallel`, recurses
   on `plan` entries, and evaluates each terminal record against its entry's `acceptance` and its
   plan's `holds_while`. A node finishing is NOT a re-plan trigger. Depth is counted per plan
   against `max_replans`.
4. **Trigger, stop notice, barrier, re-dispatch.** On a refuted `acceptance`, a refuted
   `holds_while`, or a `steer` record: every in-flight node of that subtree gets the STOP NOTICE
   (the decision-14 mechanism, grace overshoot measured 1.09 to 1.19x); barrier until the subtree
   is terminal; re-dispatch the planner with the subtree's records, the salvage handovers, the
   previous plan and the fired condition with its values. The new plan replaces the UNEXECUTED
   entries. Completed nodes are left alone; a sibling subtree is affected only through a premise
   its PARENT declared.
5. **Completion ladder and the decision-4 validator rule.** `done_when` may reference mechanical
   fields, a `judge` op's typed verdict, or an `approve`; a root `done_when` that still settles
   TRUE over the records each op declares for a run that accomplished nothing (a gate rc alone) is
   refused unless a judge or a before/after measurement is referenced. The validator half of this
   shipped 2026-08-31 (`c7ed9a0`), which RETIRED the `can_change_state` boolean this paragraph used
   to name: a per-op flag cannot answer a per-COMPARISON question, since the same op and field is
   evidence at `>= 1` and vacuous at `== 0`. Each op now declares `facts_if_no_work` and the rule
   settles the condition over those records with the SHIPPED evaluator. Above the plan-approval threshold `approve` is appended regardless.
   The judge is never the planner and never a worker.
6. **The human paths** (decision 5). `run start` takes a GOAL (today: a workflow name only,
   `run.py:266`); an ambiguous goal is PLANNED as a first entry `approve{question}` and the
   decision payload gains `answer`; `run steer RUN_ID` writes typed guidance to the journal and is
   trigger 3 of component 4. A person never edits a plan.
7. **Handover** (closes the M3 gap named under "After M3" in the detailed plan). Per-row
   `handover_at_tokens` at 40 percent of that row's window (400,000 on a 1M row, replacing the flat
   100,000), and the successor RECEIVES `handover.json` at re-dispatch - today `domain/handover.py`
   says nothing reads it back. The same read supplies component 4's salvage evidence.
8. **Where a node runs, and what its environment is made of** (decision 6). **This component also
   owns `NodeSpec.isolation`, decided by the user 2026-08-30** after Task 33's Step 5 claimed
   Checkpoint A had folded it into component 3 - which Checkpoint A's own finding 4 ("own
   worktree") contradicts. It is still parsed-never-enforced (`enforced.py isolation` exits 1;
   declared `models.py:260`, in the journal key, read by nothing), and the execute loop runs every
   entry in `ctx.cwd`. The open question this component answers: does an entry say where it runs
   through `spec.isolation`, or through an op's ARGS model (Checkpoint A finding 2, probe-RAN:
   `per_entry_worktree_n20`, 21 entries ACCEPTED) - and either way, what prepares the directory,
   since no port offers a cwd-preparation seam today and graph A hand-rolls its own
   (`_ensure_worktree`). **Two SHIPPED Claude Code mechanisms are adopt candidates for exactly
   this, recorded 2026-08-30, tier DOC-READ, not adopted here**
   (`RESEARCH/workflow/design/2026-08-30-claude-code-surface-re-read.md`):
   `isolation: worktree` on a subagent, giving "an isolated copy of the repository branched by
   default from your default branch rather than the parent session's HEAD", auto-cleaned when
   the subagent changes nothing; and `.worktreeinclude`, which copies gitignored files into a
   fresh checkout using gitignore syntax. The second bears directly on this component's own open
   item, how a node home obtains the bitranox plugin without the operator's `~/.claude`.
   **Check one thing before building**: the path chosen below, `<repo>/.claude/worktrees/<run>/`,
   is the SAME directory Claude Code uses for its own worktrees (`.claude/worktrees/<name>/`), so
   the two will coexist there. Establish whether they collide rather than finding out at runtime. The worktree goes
   under `<repo>/.claude/worktrees/<run>/` so the walk-up finds the ten ancestor
   `CLAUDE.md`/`CLAUDE.local.md` files and the memory store (measured 2026-08-28: a cwd under
   `/var/lib/agentdag` loads none of them); the credential-bearing home stays under
   `/var/lib/agentdag`. This relaxes Task 11/13's cwd-inside-run-root invariant (`context.py:262`)
   to cwd-inside-project-or-run-root; the isolation scan follows. **Task 28 ANSWERED 2026-08-28**
   (`RESEARCH/workflow/design/probes/cascade-worktree.md`, fifteen arms): the repo's
   `CLAUDE.md` loads ONCE from the worktree's checkout (the parent checkout's copy is skipped even
   when it differs), everything above the repo is walked, and the parent's untracked
   `CLAUDE.local.md` reaches the node. Three things the probe added to this component:
   - `setting_sources=["user", "project", "local"]`, all three (user, 2026-08-28). `local` is the
     only source that loads `CLAUDE.local.md` - without it a node has no memory index; `user` is
     the only source that loads the plugins - without it no skills and no hooks (measured: 0
     `bitranox:` mentions against 94). `local` also loads `.claude/settings.local.json`, from the
     parent checkout AND the worktree; how a conflicting key resolves is unmeasured.
   - **The node home's plugin set is curated**, and that is the cost lever. A node already runs
     under its own `node_dir/home/.claude` (`executor_claude.py` `_home_and_config_dir`), so
     `user` for a node means what THAT home enables. The operator's home brings 171 tools, 142 of
     them MCP schemas from browser, IDE and desktop plugins (157k of 269k tool chars, roughly 56k
     tokens derived at the body's chars-per-token) that a node never calls. Bitranox plugin in
     (skills, hooks, memory retrieval), those plugins out. Owed: how a node home obtains the
     bitranox plugin without the operator's `~/.claude` (the plugin cache and
     `installed_plugins.json` are per-home), and one probe arm on such a home for the real figure.
   - The lifecycle split (requirement 2: PreToolUse and PostToolUse load; SessionStart, Stop and
     UserPromptSubmit do not) is also where the plugin's PROMPT cost goes: hook OUTPUT is 17.4k
     chars of the injected block (session-banner 8,976, per-prompt recall 7,599, the minute label
     that breaks the cache 56), against ~3.5k for the skill listing itself.
   - **That 17.4k does NOT split evenly by class, and the difference decides the question below**
     (read from the hook wiring, 2026-08-28). The 8,976 block is `session-banner.py` on
     **SessionStart** - it injects the `meta-using-bitranox-skills` SKILL.md body, 8,401 chars plus
     a 1.9k wrapper. Earlier text here called it "the skill-router injection"; that is the wrong
     script. The real `skill-router.py` is on UserPromptSubmit and emits about one 130-char line
     per matched skill. So the classes carry roughly **SessionStart 8,976, UserPromptSubmit 7,655**
     (recall 7,599 + label 56 + the router's few hundred). Excluding the UserPromptSubmit class
     buys ~7.7k chars, not "the larger part of the tail".
   - **DECIDED 2026-08-28 (user): the UserPromptSubmit class is NOT excluded for nodes; recall
     stays, and the `remember` plugin stays.** Reason given: we want the learning signals, and
     requirement 2 item 3 already rules "memory reads yes" - recall IS that read path. What the
     decision does NOT buy is the write direction: `self-improve-gate.py` (Stop) and
     `self-improve-audit.py` (SessionEnd, PreCompact) are the capture hooks, Stop must not load
     for the correctness reason in requirement 2, and parallel writers to one store is a measured
     failure. Capture out of nodes stays where requirement 2 item 3 put it, in one reduce or synth
     node. The two directions are independent; do not read this decision as reopening Stop.
   - **The varying byte is killed at its source instead, by env var.** `user-prompt-hook.sh`
     resolves `_REMEMBER_STAMP="${REMEMBER_PROMPT_STAMP:-full}"`, and `remember` documents three
     values: `full` (default, `[14:30 CEST - jack - 45%]`), `stable` (`[jack]`), `off` (nothing).
     Note `full` varies in TWO fields, the clock and a context percentage that climbs every prompt,
     not just the minute the earlier text named. Set `REMEMBER_PROMPT_STAMP=stable` on the node
     PROCESS, not in the node home's `config.json`: the value reaches the hook through `log.sh` and
     the env cache, so it **fails open to `full`** whenever that resolution has not happened, which
     is exactly a fresh node home's first prompt. The executor already owns the node environment
     (`executor_claude.py` `_home_and_config_dir`), so this is an export there.
   - **STANDING OPTION, user-gated (user, 2026-08-28): the self-learning skills and hooks are
     THEMSELVES changeable WHERE AGENTDAG NEEDS IT.** The bitranox plugin is ours, so a constraint
     in this plan that reads as a fixed property of a hook is a choice we can revisit, per change,
     with the user's approval. Two limits, both from the user: the trigger is **agentdag needing
     it**, not a general improvement to the plugin, and each change is asked for before it is made.
     Note the bar is "agentdag needs it", NOT "cannot be met otherwise" - an earlier draft here
     said the latter, and the user loosened it, because a last-resort bar would push agentdag into
     an expensive workaround rather than a cheap plugin fix, which is the opposite of the point.
     It is recorded because several passages here were
     written as though the plugin were a third party. Four places it bites, each currently
     recorded as an immovable cost:
     1. **`recall-memory.py`'s 7,599 varying chars.** A node mode - bounded output, or keyed on
        the node's task rather than the whole brief - could make the block small, stable, or both,
        which is the difference between requirement 5 passing and failing.
     2. **`session-banner.py`'s 8,976 chars**, the whole `meta-using-bitranox-skills` body. A node
        variant could inject a trimmed form; the SessionStart exclusion is only necessary while
        the injection is all-or-nothing.
     3. **`self-improve-gate.py` on Stop**, which is the entire reason requirement 2 forbids Stop
        for nodes (a headless node hangs on the operator's gate). A gate that recognises a node
        and no-ops would let Stop load, and per-node capture becomes possible rather than
        impossible.
     4. **The parallel-writers failure** behind requirement 2 item 3. That is a property of the
        store's write path, not a law; locking there is an alternative to routing every write
        through one reduce or synth node.
     Treat each as an option to PUT to the user when the cost it imposes is about to be paid, not
     as work to schedule now. Where a passage below states one of these as a constraint, it means
     "given the plugin as it stands today".
   - **What the decision costs, recorded so it is not rediscovered as a surprise.** Recall's 7,599
     chars ride on every dispatch, roughly 1.9k tokens. Worse, recall keys on the prompt
     (`recall-memory.py` derives keywords from it), so a per-brief VARYING block stays in the tail;
     if the deterministic remainder (listing, agent types, MCP instructions, ~14k chars) sits
     BEHIND it, that does not cache either and the real cost is much larger. Unmeasured, and it is
     the ordering question owed to component 8's probe arm below. `remember`'s PostToolUse hook
     also keeps running after every node tool call, reading an unbounded stdin (its own docs:
     "hundreds of KB after a large Read"); that costs no tokens - its header comment claims a
     team-memory nudge as `additionalContext` but the code emits none - only latency and I/O.
   Recommended 2026-08-28, NOT decided: one trivial dispatch per model row before that row's first
   parallel fan-out, sequenced by the scheduler - a cache entry is readable only after the first
   response begins, so N nodes started cold on one row all pay the full write.
9. **Bounds and signals.** `RunLimits` gains `max_replans`, `max_nodes_per_run`,
   `max_nodes_per_plan`; the journal gains `plan_accepted`, `plan_invalidated` (with the fired
   condition and values) and `subtree_done`; the sink may forward them.
10. **Retirements.** `validate_dispatchable`, `planner_kinds`, and the "insertion mechanism" as a
    concept: a structure change is a sub-planner re-planning its own subtree, so nothing is ever
    inserted into a running graph.
11. **Provenance labelling on node-to-node input** (added 2026-09-01 from the OpenClaw 2.0 source
    read). M6 is where one node's output becomes another's input, so it is where laundering
    through the graph becomes possible. When output feeds a downstream node, label it
    machine-originated and de-privilege it explicitly, the way `input-provenance.ts:29-30` does
    ("treat it as inter-session data, not a direct end-user instruction"), re-hoisted idempotently.
    **It is prompt-level and therefore ADVISORY** - pair it with the tool-policy floor, never let
    it stand alone as the defence. Findings: `RESEARCH/landscape/OPENCLAW-2.0.md`.

### Sequencing, and the two checkpoints

- **Checkpoint A** - detailed Tasks 28 to 31: the double-load probe (28, DONE 2026-08-28: loaded
  once, decision 6 stands, `local` added to the sources), the schema (29), the registry (30), and
  graph A expressed as a Plan over that registry with no dispatch (31). Decision
  point: if graph A needs an op the registry lacks, the registry grows; if it needs a CONSTRUCT no
  op can express, the design is wrong and building stops. Nothing past this point is planned in
  detail until 31 has answered.
- Components 3 and 4, then **Checkpoint B**: E6 (a plan wrong by construction at node 3 must
  re-plan on `holds_while`, converge, and REPLAY to the same plans; a gate-only root `done_when`
  must be refused) and one live `steer` producing a re-plan that visibly incorporates it.
- Components 5 to 10, then M5 as the demonstration.

### Measurements kept from the earlier text of this section

**A per-node brief does NOT cost the cached prefix, measured 2026-08-27.** `why-agentdag.md`
section 1 once read as if per-node briefs would pay the startup in full every time. They do not: the
brief sits LATE in the prefix, so rewriting every paragraph of it re-created 3,743 of 26,860 tokens
(13.9 percent), and under a loaded cascade the delta is 375 tokens
(`RESEARCH/workflow/design/probes/prefix-order.md`). Design the brief for self-sufficiency and do not trim it for
cache reasons.

**Node sizing keeps its asymmetry:** too-small is knowable at PLAN time only and is handled by a
predicted floor; too-big is observable at RUN time and is handled by the handover ceiling. The floor
THRESHOLD is an open TODO (high plan, M6 row, 2026-08-28): the denominator was re-derived on new
tokens by user decision and `f = 0.10` does not survive it; the replacement waits on a measured
real-work `g`.

### Verification

E6 as above, plus the decision-4 refusal, plus the replay of a run that re-planned once.
Falsifiers: it re-plans forever; replay diverges; a gate-only root `done_when` is accepted.

### Open, and deliberately not decided (design section 11)

The condition language's exact grammar; the acceptance-failure ladder (retry, then re-plan, then
halt - unmeasured); the floor threshold; whether a node also gets the `claude_code` preset system
prompt (parked until the preset arm is re-measured on clean dispatches); the interrupt
token-accounting defect (M3 row).

### What this touches in M3, so it is not built twice

The handover-consumer gap recorded under "After M3" in the detailed plan is owned by component 7.
The cwd-inside-run-root invariant from Tasks 11 and 13 is relaxed by component 8, after Task 28.
Neither is M3 work any longer.
---

## Shipped off-plan, after this page was last written

Four tracks landed on agentdag `main` between 2026-08-22 and 2026-08-26 that no section of this
page schedules, plus one contradiction this page predicted and that has now come true in shipped
CONFIG. They are recorded here so they are visible rather than lost between sections, in the same
spirit as the section that follows. Nothing here re-opens scope; where a track needs an owner, that
is said and not answered.

**1. Design 3.8's CONTEXT CEILING - built, with its consumer missing.** The detailed plan deferred
this OUT of M3 deliberately (`build-plan-detailed.md:2951`: "Also deferred out of M3, deliberately
... Schedule it after Task 27's measurements say what a node's first-turn and total input actually
look like under the cap"). It was built anyway, 2026-08-22 to 2026-08-24: the handover DUTY carried
in the brief, the PreToolUse STOP NOTICE, the grace, `needs_continuation`, the successor dispatched
at `continuation + 1`, `max_continuations`, and the coordinator's identity stamp on the record
(`DECISIONS.md` items 14, 15 and 16). Three probe write-ups back it -
`RESEARCH/workflow/design/probes/handover-nudge-inject.md` (2026-08-22), `handover-grace-expiry.md` (2026-08-23),
and `live-handover.md` (2026-08-24), the last of which is one live run against agentdag's own
executor at 6 of 6 compliance, with its own caveat that 6 of 6 on one brief is not a rate. Task 27,
whose measurements this was to wait for, is still not started.

**The gap that comes with it: the handover record has a PRODUCER and no CONSUMER.** Verified
2026-08-27. The successor is dispatched with the SAME brief and the SAME input as its predecessor -
`application/kernel/context.py:1035-1050` bumps `continuation`, resets `attempt`, and re-enters
`_dispatch_once` with `brief=brief, input_obj=input_obj` untouched - and `_stamp_handover`'s own
docstring says it is "the coordinator's only read of `handover.json`"
(`context.py:1103-1106`). So a node hands over, the coordinator stamps identity onto what it wrote,
and nothing composes any of it into the successor's brief. The commit that recorded this says so in
its subject: `b449cae`, "Say what reads the handover record, which is nothing yet". This is the
producer-with-no-consumer shape this project has been caught by before, and whoever ends up owning
row 2 of the assignment table inherits the composition step, not only the mechanism.

**2. `write_set` enforcement per node** (`DECISIONS.md` item 12; `b46331a`, 2026-08-22). A node's
writes are judged against ITS OWN declared write set plus its own `nodes/<node_id>/<hash8>/`, and
an EMPTY write set denies every write. Before this the PreToolUse hook bounded writes only by the
whole run directory, which is the gap decision 8 named as why admitting a plan-emitted `apply` is
unsafe. It extends section 9's `write-set enforcement` row beyond what M2's isolation-root scan
gave it; the scan is unchanged and is still the only thing that sees a write made by shell
redirection. Item 12 also records that no live Claude node has run under it.

**3. The design-2.4 SPEC VALIDATOR - built and UNWIRED.** `src/agentdag/domain/validate.py`
(`0d8dbdd`, 2026-08-22, 23 tests in `tests/test_kernel_validate.py`) plus
`application/kernel/dispatchable.py`'s `validate_dispatchable` (`64788c5`, the same day). VERIFIED
2026-08-27: `validate_dispatchable` has ZERO callers in `src/` outside the module that defines it,
and `dispatchable.py:14` says so itself - "Nothing calls this yet". So `planner_kinds` and
`per_kind_ceiling` now have a READER but still bound nothing, and `top_role_budget_floor` is not
read even there. Section 9's `spec validation` row is M6's (`build-plan-detailed.md:2996`), so
wiring this is M6's to do; the code existing does not move the row.

**4. The rate-limit SUSPEND and its credential probe** (2026-08-26, `bf0c819` then `ad6087e`).
`SuspendReason` distinguishes `DECISION`, `QUOTA` and `CREDENTIAL`, so an operator is told what a
suspended run is waiting for rather than being left to guess; `ErrorType.RATE_LIMITED` carries the
provider's refusal; `Escalation.on_rate_limit` ships as `suspend_run`, defaulted rather than
required so a policy table written before the field still validates under `extra="forbid"`. It
exists because the provider's CLI reports an exhausted quota and a rejected credential identically,
which is what the credential probe asks the API to disambiguate. Nothing on this page scheduled it,
and it sits directly against section 9's `auth failure` row - one of the two rows the assignment
table above records as UNASSIGNED.

**And the contradiction this page warned about is now real, in shipped CONFIG.** The "one resource"
bullet in M3 says the retired `bmk-tool-env` lock still appears in the shipped tier policy, in
`node-spec.schema.json` and in graph A's `requires`, and that "this page must not become a fifth
place asserting a retired lock as current". This page is not that place; the CONFIG is. Verified
2026-08-27: `src/agentdag/policy/tier-policy.yaml:262` still carries
`enforce: ["flock", "/run/lock/agentdag/bmk-tool-env"]` for the lock retired on 2026-08-20 when bmk
3.17.0 began guarding its own tool environment, and the comment above it (`:253`) still reads "IN
SLICE 1 only the bmk-tool-env row exists, with its enforce lock". That belongs to the open
`bmk-tool-env` decision, which is the USER's. It is recorded here, not decided here, and the YAML
is not this page's to edit.

---

## Decided, not yet owned by any milestone

**A node gets the operator's full environment** - settings, tools, skills, self-learning memory,
hooks and CLAUDE.md, reversing `setting_sources=[]`. Decided with the user 2026-08-20. The high plan
lists it under the decisions it assumes and marks it decided and NOT yet built; no milestone on this
page owns it, and this section exists so that fact is visible rather than lost between sections.
The mechanism is `setting_sources=["user", "project", "local"]` - MEASURED 2026-08-28
(`RESEARCH/workflow/design/probes/cascade-worktree.md`): the self-learning memory lives in
`CLAUDE.local.md`, which loads only with `local`, and `local` also loads the project's
`.claude/settings.local.json` (from a worktree node: the parent's and its own).

Four things it requires, none optional:

1. the resolved cascade's hash goes INTO the journal key, or replay silently stops being pure;
2. hooks split by lifecycle - PreToolUse and PostToolUse load, Stop and SessionStart must not, or a
   headless node hangs on the operator's Stop gate;
3. memory reads yes, writes serialised through one reduce or synth node - parallel writers to one
   store is a measured failure on this machine;
4. the executor's own isolation hooks still bind, stacking on the operator's rather than being
   replaced.

A fifth requirement, added 2026-08-27 because the measurement below produced it: **the cascade has
to be made cacheable, or the decision has to be re-taken.** The four above are conditions on
correctness. This one is a condition on the cost argument the decision rested on, and it is not
currently satisfied by either candidate shape.

A sixth, added 2026-08-28 after the cascade-worktree probe and the user's ruling that knowledge
and tools are both required: **the node home's plugin set is curated, not copied.** The source
list is closed at `["user", "project", "local"]` - dropping `user` was considered and rejected
because it takes the skills and hooks with it - and the cost is taken out at the home instead:
bitranox in, the browser/IDE/desktop MCP plugins out. Component 8 of M6 owns it, with the two owed
items listed there.

**Requirement 5 is NOT satisfied by requirement 2, measured 2026-08-27**
(`RESEARCH/workflow/design/probes/cascade-cacheable.md`). The hypothesis worth testing was that excluding
SessionStart hooks - already mandatory under requirement 2 - removes the injection that produced the
49.7 percent arm, making requirement 5 free. It does not. Four back-to-back dispatches with an
identical prompt, cwd and model row read 0, 56.4, 56.4 and only then 100 percent: a full cache read
arrived on the FOURTH, not the second. Requirement 5 therefore stands as its own work.

Two things that run does not establish, so they are not built on: WHY the fourth differs (dispatch
index and elapsed time are perfectly collinear in a back-to-back sequence, and all three pairs come
back confounded), and whether the nudge fired at all (the prompt GREW by 268 tokens where the nudge
account predicts a shrink, so the size change is something else).

**A separating arm ruled TIME out** (`RESEARCH/workflow/design/probes/cascade-separator.md`, same day). Three
sequences, fresh cwd each: a second dispatch reads 56.4 percent whether it follows immediately or
after 180 seconds, to within four tokens, and two back-to-back controls differing only in cwd agree
exactly - so the cwd is inert and the wait is what C varies. Dispatch count survives as the
candidate and is NOT proven. The 56.4 percent split is structural, not noisy: five independent
second-dispatches across four directories and two runs land within a few tokens of
`create 23,480 / read 30,375`.

**So requirement 5's target is narrower than "make the cascade cacheable".** The question is what
the roughly 23.5k tail re-created on every later dispatch actually is, and whether it can be made
stable; `prompt-drift.md`'s three `ephemeral_1h` breakpoints with a 32KB role-system message
uncached after the last is where to look.

**The tail's composition is now measured by kind (2026-08-28, `cascade-worktree.md`, arm N under
the full triple).** The injected block after the last breakpoint is 31,941 chars: hook OUTPUT
17,369 (the bitranox SessionStart skill-router injection 8,976, the per-prompt memory recall 7,599,
a handoff block 624, the `remember` plugin's minute label 56), the skills listing 9,275, agent
types 3,535, MCP server instructions 1,431. The minute label is the byte that changes between
dispatches, and it lives inside hook output.

**Superseded in part on 2026-08-28, and the correction matters for requirement 5.** Two things this
paragraph got wrong. The 8,976 block is `session-banner.py` on SessionStart, not the skill-router
(see the split under component 8 above), so the two classes carry roughly SessionStart 8,976 and
UserPromptSubmit 7,655 rather than one being "the larger part". And the UserPromptSubmit class is
now DECIDED to stay for nodes, so the split no longer removes it. What the split removes is the
SessionStart output; the varying byte is removed separately, by `REMEMBER_PROMPT_STAMP=stable` on the
node process. **The remaining threat to requirement 5 is recall, not the stamp**: recall keys on
the prompt, so its 7,599 chars vary per brief and stay in the tail by decision. Whether the
deterministic remainder (listing, agent types, instructions, ~14k chars) sits behind it and is
therefore also uncacheable is the open question, and it is what requirement 5's settling run has to
answer: the four-dispatch `cascade-cacheable.md` sequence repeated with SessionStart excluded,
UserPromptSubmit PRESENT, and the stamp off. It has not been done.

**One finding cuts the other way and belongs in the cost case: the cascade prefix is shared ACROSS
working directories.** Every arm's first dispatch into a brand-new cwd read 45.7 percent
immediately, on warmth left by an earlier run in a different directory. Nodes in different
worktrees on one model row do share it, which is the shape agentdag actually dispatches.

It also has a stated prerequisite: `charged_tokens` sums input and output where input already
includes cached reads, so with the cascade loaded it would report roughly a fifth of an opus run
budget spent on a prefix that prompt caching makes nearly free after the first node. Fixing that
counter is a prerequisite, not a nicety. **Note what the measurement did to the SHAPE of that
prerequisite**: the premise "prompt caching makes it nearly free" is what failed, so the counter is
no longer merely over-reporting a discount that exists. About half of what it counts is charged at
full rate, and a corrected counter would show a cascade node costing real money rather than
cosmetically less.

### The measurement the risk asked for has been taken (2026-08-27)

The high plan's risk 3 called for ONE measurement of the new configuration. It is
`RESEARCH/workflow/design/probes/prefix-order.md`, two runs on the shipped SDK. Token counts, not prices: the tier
table carries a `cache_read` rate and no cache-write rate, and writes land in the `ephemeral_1h`
tier, so any price stated here would hide an assumed multiplier inside a number.

```
per later node, same model row     in_total   cached on repeat
today, setting_sources=[]            26,854             100.0%
cascade loaded                       47,288    100.0% or 49.7%   (see "not dependably" below)
```

**The finding that matters is not in that table: the cache key includes the MODEL.** A prefix
warmed on sonnet gave haiku and opus nothing. A third run removed the obvious confound - that the
CLI renders a different system prompt per row, so the text differs anyway - by sending a prompt
whose bytes are entirely ours (`setting_sources=[]`, `tools=[]`, plain string). On that prompt
**opus renders 23,556 tokens and fable 23,557, one apart, and fable read ZERO right after opus
warmed it**, while opus re-reading its own prefix read 23,554. Every cross-row read is exactly 0
rather than partial, which is not what merely-different-text produces (a late text change measures
86.1 percent cached).

`tier-policy.yaml` maps kinds to roles to rows by design, so **a run pays one full startup per model
row it uses**, and loading the cascade multiplies each of them rather than adding to one. It is a
structural floor: no brief convention, worktree convention or warm-up avoids it. Related, and easy
to trip over: the CLI renders a different startup SIZE per row for identical inputs (22,208 haiku,
23,556 opus, 23,557 fable, 29,064 sonnet), so a figure measured on one row does not transfer.

**This does NOT make a cheap row expensive**, and an earlier version of this section said it did.
A cold start is one-time per row per run while the rate difference is per token of WORK, so a
cheaper row repays its own cold start:

```
route work to   instead of   cold startup   break-even work
haiku           sonnet          20,340 tok        20,340 tok
haiku           opus            20,340 tok        10,170 tok
sonnet          opus            26,852 tok        80,556 tok
opus            fable           21,026 tok        42,052 tok
```

Those startup figures are the CURRENT configuration (`setting_sources=[]`). If the operator
environment lands, startup roughly doubles on the rows measured (26,852 to 47,288 on sonnet) and
every break-even above roughly doubles with it, because the break-even is proportional to the cold
start. The ordering does not change; the thresholds do.

In tokens of work, from the rate ratios in `tier-policy.yaml` plus one assumption - a cache write
costs 2x the input rate, the published `ephemeral_1h` multiplier, that tier being what the raw usage
shows. The tier table carries no write rate of its own; that is a real gap. M2 measured work nodes
at 5 to 28 turns, so any node doing real work clears these easily, and the cold start only dominates
for a node too small to have been worth starting - `min_node_tokens` from a new direction. The real
planning lever is keeping a graph on FEW rows, not on expensive ones.

**The cascade caches, but not dependably.** Run 1 measured 49.7 percent on two back-to-back
dispatches with an identical brief and working directory, and reproduced that exactly twelve minutes
later. Run 2 measured 100 percent on the same arm. What separates them is visible in the totals: in
run 1 the prompt GREW by 120 tokens between the two dispatches; in run 2 both sent the same count.

**Those 120 tokens are now identified** (`RESEARCH/workflow/design/probes/prompt-drift.md`): 227 characters of
`<BITRANOX-NEW-PROJECT>`, injected by this machine's own bitranox SessionStart hook, which fires
once per fresh project directory and self-silences. Captured by proxying the real request bodies and
diffing them, with the mechanism read at source. It is not the Claude CLI. It also does not break
the cached prefix - the injection lands after the last of three cache breakpoints - so the cascade's
cacheability is not what it threatens.

**What it threatens is worse, and it lands on requirement 2 above.** Every node runs in a fresh
worktree, so every node looks like a brand-new project to that hook: loaded as-is it would fire on
the first dispatch into each worktree and instruct an unattended node to run `/collect-knowledge`.
The recorded reason for excluding SessionStart hooks was that a Stop hook hangs a headless node;
this is a second, quieter failure with evidence behind it.

Two more things that capture established, both correcting assumptions on this page. **The cascade is
not in the system prompt**: `system` is 421 characters (just the brief) and the cascade arrives
inside `messages`, so any reasoning here about "an identical system-prompt prefix" has the wrong
mental model even where its conclusion holds. And **every dispatch makes a second, unrequested API
call** - a session-title generator, billed, and included in the cumulative `ResultMessage.usage`
that the per-node cap reads.

The bisect exonerates both sources individually: `['user']` (46,859) and `['project']` (27,054) each
cached 100 percent on repeat.

Not to blame, measured: a per-node brief costs 375 tokens on top under a cascade, and a per-node
working directory 435.

**Scope, carried here and not only in the probe note.** One host, one account, one operator cascade,
three model rows, one day. The direction of the model-row and brief-position findings should
generalise; the magnitudes are properties of what is installed on this machine, and the cascade
stability finding disagreed with itself inside one hour.

**So the decision is owed again.** "A node gets the operator's full environment" was decided with
the user on 2026-08-20, and the reason recorded in the decomposition design is that "the cost
objection is much weaker than it looks, and caching is why". Caching is weaker support than that
sentence assumed, and the per-row multiplication is a cost nobody had counted. The DECISION may well
still stand - a node without the operator's skills and memory is a worse agent, which was always the
real argument - but it now has to be taken against a measured cost rather than an assumed discount,
and that is the user's call. Recorded here, not decided here.

### Four small items from the OpenClaw 2.0 source read (2026-09-01)

Findings: `RESEARCH/landscape/OPENCLAW-2.0.md`. Each is a gap a second shipping
implementation covers and agentdag does not. They are recorded unowned rather than pushed into M3,
whose tail is already carrying work; the detailed plan notes them as candidates for that tail if
the user would rather they were owned.

Two things that gap analysis found ALREADY BUILT here, so they are not listed: generation/epoch
fencing (`FileRunLock` records host, boot id, pid and pid start time, with `holder_is_alive`, which
is what fencing buys - at RUN level; only the NODE level is uncovered) and per-node credential
isolation (`executor_claude.py` mints a per-node credential from a private owner-only copy).

| item                                                                         | gap in agentdag today                                                                   | size  | where it belongs                     |
|------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|-------|--------------------------------------|
| node-level lease with heartbeat and reclamation                              | `RunLock` covers the RUN and knows a dead holder; a NODE whose worker died has no lease | small | beside the crash-window work         |
| stuck-state taxonomy (stranded / no-heartbeat / blocked-too-long / repeated) | `ErrorType` says how a node FAILED, nothing says why one is NOT MOVING                  | small | kernel; cheap, high diagnostic value |
| credential TTL, bound to (run, node, attempt)                                | the per-node credential exists but is not execution-scoped                              | small | `executor_claude`                    |
| re-authorize under the writer barrier                                        | `approve` resolves authority before an await; the write lands later                     | small | the approve path (M3 owns approve)   |

**A precondition, not an item: the tool-authority fingerprint.** It guards mid-flight injection into
a RUNNING node, and nothing here can steer a running node - the insertion mechanism was designed
away on 2026-08-28. It binds any FUTURE steering design, and is deliberately not work in any
milestone.

---

## Cut

Everything below was scheduled on the 2026-08-17 version of this page and is cut by the 2026-08-21
high plan. It is recorded rather than deleted so a reader can see it was considered, and why it
went.

**Falsifier for the whole cut list, stated so it is not re-argued: if the differentiators ship and
nothing uses them, that answers the deferred tail without anyone having to argue about it.**

### M4 - the Codex arm. Cut: a second executor proves portability nobody has asked for.

**One thing the arm was buying is NOT cut with it.** It was the only empirical test that the
executor port is not shaped around one vendor, and this project has already had a port leak its
domain into its contract (`GatePort.run(worktree, log) -> int`, which no adapter outside software
could serve). M3 therefore keeps a cheap conformance check in place of the arm: a fake non-SDK
adapter satisfying the executor port in tests, or at minimum a reading of the port signature
asking what a second vendor would pass for each parameter. Weaker than a real adapter, and a
small fraction of its cost.

What it was: `adapters/executor_codex.py`, running `codex mcp-server` per node via an `mcp` stdio
client with an allowlisted env, `sandbox: workspace-write`, `approval-policy: never`, `cwd` = the
worktree, `base-instructions` from the brief, the output schema in the brief and `content`
validated as JSON with one `codex-reply` re-ask; `charged_tokens` = the full node budget at
dispatch, reconciled after the node from the rollout for the `threadId` into a `usage_reconciled`
journal line (S0 measured 2026-08-17 that the rollout exists, named by threadId, and that the last
`token_count` event's `total_token_usage` is the figure). The node got its own `CODEX_HOME`, and
the adapter read the `codex/event` notifications raw for progress.

What goes with it, so none of these is left looking scheduled:

- the executor conformance test against a fake in-repo MCP agent server, which would have proven
  both adapters against the same `result-record.schema.json` through a real seam. The Claude
  adapter stays on the direct SDK (design 6.1, decided 2026-08-18), so there is no second adapter
  left for the fake to hold to the contract.
- the `prefer_other_family` tier knob (Claude work -> Codex review and the reverse), and the M4
  measurement that would have decided whether it defaults ON: same-family fresh context versus
  cross-family, interleaved, a third judge confirming which findings are real. Independence and no
  stake remain the load-bearing properties, and they are already in the process; a different model
  family was a diversity lever whose value was never measured. It stays unmeasured.
- the `quota` rate resource per subscription row and quota-aware routing of review load.
- the UNRESEARCHED terms question for a ChatGPT subscription driven by a coordinator. The Anthropic
  side was answered by D3; the OpenAI side was never asked and now does not need to be.

### The MCP north face / server surface (L1). Cut: a CLI over the run dir is enough for one user.

This also removes the per-tool scope checks that S0's mcp-scopes probe was run for, and the
"later the server" emitter in M3's notification sink.

### The deferred tail. Cut as a whole.

L2 resources beyond one lock (T6); L3 knowledge grants when the knowledge-index project's 4.4 + 4.9 ship (T8); L5 graphs
A2 and C; L6 the drift review loop after ten runs (T11); L7 an `acp:<agent>` executor kind (Agent
Client Protocol), which is a second executor and falls to the same reason as the Codex arm; L8 the
memory store's dream as agentdag graph D; and policy versioning.

**L4 (planner plus graph B after E12's numbers) is NOT cut - it is SUPERSEDED by M6**, which is
gated on C1 and C2. E1 measured that graph B's re-planning is a 3-way route over three pre-drawn,
hand-authored nodes rather than the insertion of a planner-emitted spec, so L4 as written does not
survive into M6 unchanged; M6's insertion mechanism is what replaces it.

### `knowledge` grants as the mechanism by which a node knows anything. Cut as a mechanism.

The 2026-08-20 environment decision makes them an optimisation for large retrievals rather than the
mechanism, so nothing blocks on the knowledge-index project's 4.4 + 4.9 any more. The grant is not deleted from the
design; it stops being load-bearing.
