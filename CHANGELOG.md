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

### Fixed
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
- Per-node write-set enforcement is a post-hoc scan, not a live block, and under `--parallel`
  greater than 1 a stray write landing inside a SIBLING's declared region is not attributable
  to one node; the scan reports that rather than naming one.
- `by` and `token_id` on a decision are not an authentication mechanism: any process running
  as the same operating-system user can record one.
- Cancel and deadline teardown reaps grandchildren only under the systemd scope. Under
  `NoScope` (off Linux, or with no live `systemd --user` manager) the coordinator's process
  group is killed on POSIX and only the one launched process on Windows.
- A failed CODE node (gate, scan, tally, discover) is final in this version: its record is
  served on every resume and no command mints a new attempt, so such a run can only be
  restarted as a new run. M3 adds the retry path.

## [0.0.1] - 2026-08-17

Beta name claim on PyPI: the package scaffold only, no coordinator yet.

### Added
- Project scaffold generated from `bitranox_template_py_cli` (bmk-managed, Clean Architecture
  layers: `domain/`, `application/`, `adapters/`, `composition/`).
