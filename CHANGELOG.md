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

### Notes
- The baseline deliberately has no journal, no token cap and no unattended approve; those are
  later milestones and their absence is what makes their cost measurable.

## [0.0.1] - 2026-08-17

Beta name claim on PyPI: the package scaffold only, no coordinator yet.

### Added
- Project scaffold generated from `bitranox_template_py_cli` (bmk-managed, Clean Architecture
  layers: `domain/`, `application/`, `adapters/`, `composition/`).
