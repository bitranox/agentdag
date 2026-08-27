# AI transparency

The author and owner of this project is the human, [@bitranox](https://github.com/bitranox).
Every design and engineering decision is theirs, and they answer for everything published here.
An AI assistant (Claude, run through the Claude Code CLI) was used as a tool along the way,
mostly for the typing and the legwork under that direction. This page says where, plainly, so you
can weigh the work on its merits. The reasoning behind working this way is in
[ai-stance.md](ai-stance.md).

There is an obvious circularity here worth naming rather than skating past: agentdag is a
coordinator for teams of AI agents, and it was built with heavy AI assistance. That makes the
accountability question sharper, not softer, which is why this page is specific about who decided
what.

## The human's work

The shape of this software is the human's, start to finish. They set the problem, made every
call, and own the result.

- The question is theirs: what a team of language models actually needs, as against the structure
  the word "team" quietly imports from human organisations. [docs/why-agentdag.md](docs/why-agentdag.md)
  is that argument, and every structural choice in the coordinator follows from it.
- The load-bearing decisions are the human's, and they were taken as explicit decision walks: the
  AI lays out the realistic options with the upside AND the downside of each and a recommendation,
  the human picks, and the choice is recorded with its alternatives. Where the human chose against
  the recommendation, that stands and the recommendation does not come back.
- The rule that a run branches only on typed records, never on prose an agent wrote, and that a
  node's output is gated on something mechanical rather than on another model's opinion, is the
  human's design constraint. It is what makes an unattended run defensible.
- Scope is the human's: which milestones ship, which lines of work get cut, and when a result is
  strong enough to state plainly rather than as a first indication. On more than one occasion the
  human downgraded a claim the AI had written too confidently, and told it to observe the question
  in real runs instead of buying more probe repeats.
- The human reviewed and corrected the work at each step; what ships is what they signed off on.
- All 173 commits went out under the human's name and authority, with no AI co-author line and no
  generated-by footer. The human is responsible for what is published.

## Where the AI was used

As a tool, under the human's direction, it did the mechanical parts: writing the modules, the
tests and these docs to the human's design; tracing the Claude Agent SDK's actual behaviour where
the documentation did not settle a question; laying out options at each fork for the human to
choose from; and grinding through the variants, the measurements and the gate failures.

It also wrote the internal probes that produced most of what
[docs/measurements.md](docs/measurements.md) records, and several of the corrections in that file
are the AI catching its own earlier claims. That file exists because the surrounding documents had
been confidently wrong in one direction, which is the failure mode this way of working has to
guard against.

None of the decisions, and none of the accountability, were the AI's. The human directed and
approved every action and owns the result.

## What's been checked, and what hasn't

The whole suite runs on every change: 837 tests behind a five stage gate covering formatting,
lint, pyright in strict mode, a bandit scan and pytest. CI runs the same suite on Linux, Windows
and macOS across the supported Python versions. The layer boundaries are checked separately, by
two import-linter contracts run with `lint-imports`.

The honest limit is what the tests stand in for. Most of them drive injected seams and fakes
rather than a live model, because a real dispatch costs money and is not reproducible. So the
coordinator's own logic is well covered and the behaviour of a real model inside it is covered
much less. Live runs happen, and they are recorded separately as measurements rather than folded
into the suite.

[docs/measurements.md](docs/measurements.md) is the place to look before trusting any specific
claim about this system. It separates what was MEASURED from what was merely READ in the source,
names who established each entry, and states what each measurement does NOT establish. Its later
sections are the useful ones: what is load-bearing and untested, and what was believed and turned
out false.

## Checking it yourself

The layer boundaries are mechanically checked rather than merely documented, so the structure in
[docs/architecture-overview.md](docs/architecture-overview.md) is a description of what the import
graph actually permits: `lint-imports` holds the domain layer pure and the dependency direction
inward. The ports are typed protocols and the adapters are swappable, which is also why so much of
the system can be tested without a model at all.

[docs/execution-model.md](docs/execution-model.md) describes what a run does, and
[docs/safety-and-sandbox.md](docs/safety-and-sandbox.md) describes what bounds a node. Read the
second one before pointing this at anything you care about.

The tests need no credentials and no model: `.venv/bin/python -m pytest tests/`. The full gate,
`make test`, additionally reaches PyPI to check dependency floors, so that one is not offline.

## What this isn't

It isn't finished, and it isn't a product. Version 0.0.1, developed over a short and intense
period, run by its author and not widely deployed. The graphs that ship with it are test vehicles
for the coordinator, not the point of it.

It also isn't a way to avoid understanding what you are automating. It runs agents against real
working trees with real credentials. If you use it, read the safety notes and the execution model
so you know what a node can reach and what stops it.

## License and attribution

The text and code here are under the MIT License (see [`LICENSE`](LICENSE)). Anthropic's terms put
ownership of model output with the user, so the human owns this and answers for it.
