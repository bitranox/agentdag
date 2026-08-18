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

### Notes
- The baseline deliberately has no journal, no token cap and no unattended approve; those are
  later milestones and their absence is what makes their cost measurable.
- Codex (a second executor arm) and a per-turn token spend cap are not in this version of the
  coordinator kernel either.

## [0.0.1] - 2026-08-17

Beta name claim on PyPI: the package scaffold only, no coordinator yet.

### Added
- Project scaffold generated from `bitranox_template_py_cli` (bmk-managed, Clean Architecture
  layers: `domain/`, `application/`, `adapters/`, `composition/`).
