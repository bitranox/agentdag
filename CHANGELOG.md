# Changelog

All notable changes to this project will be documented in this file following
the [Keep a Changelog](https://keepachangelog.com/) format.


## [Unreleased]

### Added
- Graph A (fleet migration) baseline: `agentdag graph-a scratch` mirrors a list of real
  repositories into a scratch fleet, and `agentdag graph-a run` migrates every mirror in its
  own worktree, runs the project gate, tallies the outcome and pushes what passed after one
  console approval.
- `domain/graph_a.py` (records and pure decisions), `application/graph_a_ports.py` (the five
  ports plus the wiring record), `application/graph_a.py` (the graph as a deterministic
  program), `adapters/graph_a/` (git CLI, make gate, filesystem run store, Claude Agent SDK
  work node, console approve) and `composition/graph_a.py` (production wiring).
- `WireGraphA` port on `AppServices`, so the CLI receives the graph A wiring by injection
  instead of importing the composition root.
- `typed_click.argument`, completing the strictly-typed rich-click decorator facade.
- Per-node credential isolation: each work node runs against its own `CLAUDE_CONFIG_DIR`
  under the run store, holding its own copy of the login created owner-only in one step, so a
  node's token refresh lands in that copy and never in the operator's file, and parallel nodes
  never share one credential file.
- `agentdag graph-a scratch --refresh`, which deletes an existing scratch mirror and re-reads
  the real repository instead of silently reusing a stale one, through `GitPort.remove_mirror`
  so the read-only object files git writes are removed on Windows too.
- Neither scratch clone keeps a remote: the mirror does not point back at the real repository
  and a run's worktree does not point at the mirror, so a work node's reflex `git push` has no
  route into the fleet. A node with unrestricted Bash is not contained by that; containment is
  a sandbox, which the baseline does not have.
- Every push target is validated before the first node is dispatched, so a fleet naming a real
  repository is refused without spending a run and without asking anybody to approve a push
  that cannot happen. The apply step keeps the same check as its invariant.
- The coordinator kernel and its CLI, `agentdag run` (`start`, `status`, `records`, `resume`,
  `approve`): a journal (`journal.jsonl` plus an audit copy), a resumable run directory, a
  tier policy table resolving specs to model rows and executors, and a Claude Agent SDK
  executor with an allowlisted per-node environment and a per-node credential (an OAuth-token
  keyfile, or a private owner-only copy of the operator's own login). The kernel's primitives -
  work, gate, scan, map, reduce, stage, approve, apply - run the SAME `graph-a` workflow the
  M1 baseline runs, replay-pure across a crash or a suspend: a relaunch re-dispatches only what
  the journal shows never finished, and serves everything else from its recorded result.
- `agentdag run start` launches detached by default, under a real `systemd --user --scope`
  unit on Linux (measured live) or a plain child process elsewhere, so the command returns
  immediately; `--foreground` drives the same coordinator in-process, the path every test
  exercises. `agentdag graph-a ...` stays in the repo unchanged as the M1 baseline - the
  control this kernel is measured against until M5 compares the two.
- A `Sandbox` port (`application/kernel/sandbox.py`) and its `none` adapter
  (`adapters/kernel/sandbox_none.py`, wired by default): definition only - a container
  adapter is a later, deliberately parked task. `SandboxGuarantees` is stamped onto every
  dispatched node's record (`ResultRecord.sandbox`) at the point the record is built, work
  and code nodes alike, and persisted with it - `record.json` and the journal's `result`
  line both carry it, so `agentdag run records --json` shows it too (the default plain-text
  output does not) - instead of only the README's prose; `NoSandbox` declares `filesystem`,
  `network_egress` and `separate_uid`
  all `false` - the honest, unchanged-in-kind truth about today's kernel. A record served
  from the journal on replay keeps the declaration it was originally dispatched under,
  even when a later launch is wired with a different `Sandbox` adapter.
- A per-node deadline. `deadline_s` is clamped to the policy's `deadline_ceiling_s` by
  `Coordinator.work` before it reaches the executor, and the executor checks it at the same
  per-turn seam the token cap uses, against a different quantity entirely: wall-clock seconds
  since dispatch start, read from an injected clock, never a token count. A node stopped that
  way is recorded `cancelled`/`deadline`, `transient=False`, with no artefact refs, because an
  interrupted dispatch's own terminal message never says it was interrupted in either
  direction, so the record is stamped on the path that called `interrupt()`. There is no
  deadline for the run as a whole.
- `agentdag run cancel`, and the startup sweep for a scope a dead coordinator left behind.
  `request_cancel` writes a `CancelIntent` under `decisions/`, marks the run `cancelling` and
  returns at once; `resolve_cancel` kills the run's scope and, only once its lock can be taken,
  folds the intent and the VERIFIED outcome into the journal, never trusting the stop verb's
  own return where the scope cannot confirm a cross-process kill at all, and never re-journaling
  a cancel two callers raced to resolve. Two new journal lines, `cancel_requested` and `cancel`.
  `sweep_stale_scope` is the unconditional housekeeping half, called before every relaunch, so a
  scope still draining from a dead coordinator is stopped before a new one dispatches into the
  same worktrees. `agentdag run resume` now refuses a cancelling or cancelled run rather than
  silently working past a cancel.
- An owner for the approve deadline, and `agentdag run apply-deadlines` to drive it. A suspended
  approve node's coordinator has EXITED, so nothing was applying the payload's default at
  `decide_by`; the pass is external by necessity, runs over every run in a runs directory and
  takes each run's lock. `decide_by` is READ from the payload and never recomputed, because the
  payload's content hash is the approve node's dispatch identity and a deadline read from the
  clock would move on every launch. A human and the pass racing for one (node id, payload hash)
  resolve on the filesystem's own atomic link: exactly one wins, the loser is refused, and the
  journal carries one decision. The applied decision carries `by=system`, `reason=deadline` and a
  `token_id` naming the timer, which moved the run summary's "was a human involved" question off
  `token_id` and onto `by`. A `systemd --user` timer and service sit in the repository under
  `deploy/` for an operator to install; nothing here installs anything, and the wheel ships only
  `src/agentdag`, so an installed user does not receive those unit files.
- A notification sink, so a run tells an absent operator what it did. Four states are notifiable:
  `suspended` (carrying the payload's question and its `decide_by`), `done` and `failed`, which
  the coordinator emits as it takes them, and `crashed`, which it cannot emit because a crash is
  the process leaving without writing anything. `crashed` is observed from outside by the same
  periodic pass that applies approve deadlines, and only when three facts agree: the state says
  running, the lock is takeable, and the journal is non-empty. That gives `RunStatus.CRASHED` its
  first producer. A sink cannot fail a run - whatever it raises is contained, because a mail
  server being down is not a run failure - and the cost of that is silence. Two sinks behind one
  port: the no-op default, and mail through the `btx_lib_mail` adapter the email commands already
  use; an explicit `mail` with no SMTP host refuses rather than falling back to silence.
- `agentdag notify-test`, the one place a broken sink is loud. It resolves the sink through the
  same function a run does, so it cannot report healthy what `run start` would refuse, then emits
  a probe event and turns the result into a message and an exit code. With no sink configured it
  says so and exits 0. Its limit is worth knowing: it proves the sink worked once, now.
- A retry path for a failed CODE node, in two halves. The coordinator re-dispatches automatically
  when the record says the failure was infrastructure rather than an answer: the error must be
  transient, the kind must be one the coordinator runs as code, and the attempt number must be
  under `thresholds.max_attempts` (shipped as 2, so one retry; 1 disables it). A red gate is
  `failed` with no error at all because it ran and reported a real answer, so it is never retried
  automatically, and design 2.3 rule 5 owns a model node's retry by escalating a rank instead.
- `agentdag run retry RUN_ID NODE_ID`, the operator verb for the case the automatic rule cannot
  reach: somebody who fixes the repo by hand changes nothing a journal key can see, so that
  failure is otherwise served back on every later launch. The grant is a journal line folded from
  an inbox file the way an approve decision is, bound to the (node id, journal key) pair of the
  failed attempt, and self-limiting by construction because the attempt it authorises runs under
  `attempt + 1`, an identity field, so it lands on a different key and can never match twice. It
  is its own verb rather than a flag on `resume`, because a red gate does not fail the run and
  `resume` refuses a done run by design. The verb walks the node's attempt chain first and refuses
  a grant naming a key the relaunch could never walk to, naming the attempt the relaunch reaches.
  No journal event is added for either half: `attempt` is an identity field, so every try already
  appends its own `started` and `result` under its own key.
- The context ceiling of design 3.8, first half: a node whose SINGLE turn's context passes its
  row's `handover_at_tokens` is interrupted and ends `needs_continuation` instead of running on.
  It is a third quantity at the same turn seam and deliberately not the token cap's: a spend cap
  asks how much a dispatch has used in total, which only a sum can answer, while a context ceiling
  asks how full the window is right now, which only a single turn's own figure can answer. It is
  checked last and only when neither hard stop fired, so a node out of budget or out of time is not
  offered a successor that would outlive the bound that stopped it.
- That record KEEPS its artefact ref and carries no error. The cap and deadline paths empty
  `artefact_refs` so a half-finished worktree is never presented as complete, which is right when a
  node stops for good; a handover is the opposite case, because that tree is what the successor
  continues from. `ResolvedRow` now carries `handover_at_tokens`, so the figure reaches the
  executor from the model row that owns it rather than from a node or a run limit.
- The context ceiling of design 3.8, second half: a node that ends `needs_continuation` is now
  CONTINUED by a successor rather than returned as-is. The successor is dispatched with
  `continuation + 1` - an identity field, so a different journal key and a genuine re-run, exactly
  as `attempt + 1` works for a retry - and its attempt counter starts over, because a successor is a
  fresh dispatch whose own transient failures deserve the allowance any node gets.
- `max_continuations` bounds the chain and is the only thing that does, since a handover is not a
  failure and no retry rule or budget refusal is reached by handing over. Reaching it dispatches a
  body that refuses with `continuation_limit` rather than returning a record built on the spot, so
  the refusal is journaled and hashed like any other outcome and a replay reaches the same end.
- `ResultRecord.continuation` records which link of a chain produced a record, defaulted so a
  journal line written before this still validates; the shipped result-record schema carries it.
- A node crossing its context ceiling is ASKED to hand over before it is stopped, because a node
  interrupted at the moment of asking cannot write the record its successor is meant to read. The
  wording is measured rather than chosen: over 40 dispatches a notice claiming the node was near
  its context ceiling was refused 4 of 4, since the node reads its own remaining budget and finds
  the claim false, while a notice asserting only that the coordinator is ending the turn, against a
  prompt that pre-authorises it, was obeyed 4 of 4. So the duty travels in the node's own prompt,
  where it has standing, and the notice through the executor's `PreToolUse` hook, which is not a
  channel that can confer any. The grace is bounded, counted in API requests keyed by `message_id`
  rather than in stream events, and `interrupt()` at its expiry is the shipped backstop.
- The coordinator stamps a handover record's identity where it persists the record. The shipped
  schema requires `node_id`, `attempt` and `continuation`, the duty asks a node for none of them,
  and a node could not answer honestly anyway, so every compliant record failed the full schema on
  exactly those three keys. The stamp uses the CURRENT spec and happens only on a REAL dispatch, so
  a replayed handover keeps the identity it was written with. The node's own bytes are kept beside
  it as `handover.as-written.json`, written BEFORE the stamp, because stamping reformats and
  reorders and that wording is the evidence every faithfulness question is answered from. An absent
  or unparseable record is left exactly as found.
- `grace_expired` on the record, declared TYPED, so a node that wrote its handover and stopped can
  be told from one that was cut off when the grace ran out. Both ended `needs_continuation`
  carrying the same three ceiling figures and nothing on the record separated them.

- Per-node write-set enforcement at the hook, not only in the post-hoc scan. A node's writes are
  judged against ITS OWN declared `write_set` globs plus its own `nodes/<node_id>/<hash8>/`, and an
  EMPTY declaration denies every write rather than allowing any. Before this the request carried
  `write_set` to the executor and no adapter read it, so the PreToolUse hook bounded writes only by
  the whole run directory - one node could write another's tree and the run would not notice until
  the isolation scan, which sees a content diff and cannot say whose write it saw. `domain.scan.is_covered`
  is the matcher both the hook and the scan use; their lists differ deliberately for that reason.
  Still unenforced either way: neither hook sees a write made by shell redirection.
- The design-2.4 spec validator: nine refuse rules over a frozen context value object in
  `domain/validate.py`, plus `validate_dispatchable`, which adds the one rule needing real
  filesystem resolution (a `brief_ref` that resolves inside the run store) behind a port so the
  traversal-defeating `realpath` stays inside the validator instead of becoming the caller's job.
  **It is unreachable from any real path in `src/`, and that is deliberate rather than an
  oversight.** Design 2.4 governs PLANNER-emitted specs; graph A's `apply` node is hand-authored and
  legitimate and these rules refuse it correctly, so wiring the chain into the dispatch path would
  refuse a graph that runs today. It gates a planner's output, and there is no planner yet. Two of
  the three `RunLimits` fields it reads - `planner_kinds` and `per_kind_ceiling` - therefore bound
  nothing until that milestone lands, and the third, `top_role_budget_floor`, has no reader at all.

### Fixed
- A rate limit killed a run permanently and reported it as a login failure. The Claude CLI
  describes an exhausted quota and a rejected credential identically - the same
  "Not logged in - Please run /login" text, `authentication_failed`, a null
  `api_error_status` - so nothing in the stream separates them: measured 2026-08-24 on SDK
  0.2.144, three dispatches failed that way while the same credential returned HTTP 429 from
  the API in the same minute. A `CredentialProbe` now asks the Messages API directly and
  reads the status code the CLI discarded, and a quota refusal ends the launch `suspended`
  and resumable instead of `failed`, keeping every node the run had finished. The re-label
  is an upgrade on positive evidence only: a probe that could not ask leaves the
  classification untouched, and what it observed (`http 404`, an unreachable endpoint, a
  missing token) is appended to the node's error message so `record.json` carries it - an
  unrecognised status means the probe itself is broken, and by verdict alone that is
  indistinguishable from a healthy timeout.
- `escalation.on_auth_failure` decided nothing. It was declared, validated, shipped in
  `tier-policy.yaml` and read by no Python at all, so a run died from `transient=False`
  alone and the setting was documentation. It is now typed `FailureAction` and actually
  compared, alongside a new `escalation.on_rate_limit` (shipped `suspend_run`). Note that
  marking such an error transient would not have helped either: `_auto_retries` requires
  `spec.kind in CODE_KINDS`, which excludes `work` by design (2.3 rule 5), and the retry
  path has no backoff.
- `Suspended` raised from inside a node body became a failed record. It is a plain
  `Exception` and `_run_body`'s broad catch swallowed it, which is worse than mislabelling
  it: `build_replay_index` serves every recorded result whatever its status, so the record
  would have been read back by the very resume the suspend exists to enable.
- The result-record schema's `error.type` enum was missing `continuation_limit`, which the
  `ErrorType` enum has, so a producer could emit a record the validator would reject. Both
  are now pinned to each other by a set-equality test.
- The per-node token cap counted each API request once per content block, so it fired at
  roughly 1.5x to 1.8x a dispatch's real usage. The CLI emits one `AssistantMessage` stream
  event per content block and every one repeats that request's `message_id` and `usage`,
  while the running sum added on every event. Measured across the stored dispatches under
  the run store: 19 events over 12 distinct message ids, and 10/6, 24/16, 41/23, 26/17, with
  the distinct-id count equal to `num_turns` in four of the five. The sum is now accumulated
  per `message_id`, so a request is counted once however many blocks it arrives in. This is
  why a node holding about 250000 tokens was interrupted against a 400000 cap and its
  finished, correct work was discarded unexamined.

### Notes
- The baseline deliberately has no journal, no token cap and no unattended approve; those are
  later milestones and their absence is what makes their cost measurable.
- Codex (a second executor arm) and a per-turn token spend cap are not in this version of the
  coordinator kernel either.

### What the kernel does not enforce
- The per-node credential and the environment allowlists stop ACCIDENTAL leakage through
  inherited environment variables. Nodes are not isolated by operating-system user or by a
  sandbox in this version: a node runs as the same user as the coordinator, so its own Bash
  tool can read files elsewhere on the machine and make outbound network requests.
- The Bash denylist blocks only the exact command shapes the policy lists. Measured against
  the shipped policy, `curl -XPOST`, `curl -d`, a GET carrying its data in the URL,
  `git -C some/path push` and `python3 -c ...` all pass.
- Per-node write-set enforcement is a live block on the matched write tools plus a post-hoc
  scan, and NEITHER sees a write made through shell redirection rather than a matched tool.
  Under `--parallel` greater than 1 the scan works from a content diff, so a stray write
  landing inside a SIBLING's declared region is not attributable to one node; the scan reports
  that rather than naming one.
- `by` and `token_id` on a decision are not an authentication mechanism: any process running
  as the same operating-system user can record one.
- Cancel and deadline teardown reaps grandchildren only under the systemd scope. Under
  `NoScope` (off Linux, or with no live `systemd --user` manager) the coordinator's process
  group is killed on POSIX and only the one launched process on Windows.
- Once a failed CODE node's attempts are spent, nothing AUTOMATIC mints a further one: the
  record is served on every resume until a person grants an attempt with `agentdag run retry`.
  Nothing withdraws a grant, and nothing lists a run's grants short of reading its journal.

## [0.0.1] - 2026-08-17

Beta name claim on PyPI: the package scaffold only, no coordinator yet.

### Added
- Project scaffold generated from `bitranox_template_py_cli` (bmk-managed, Clean Architecture
  layers: `domain/`, `application/`, `adapters/`, `composition/`).
