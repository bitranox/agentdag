# agentdag documentation

agentdag coordinates teams of AI agents. It dispatches each node of a graph, gates what the node
produced on something mechanical, and branches only on typed records, never on prose an agent wrote.
The coordinator holds records and state and never content, which is what keeps it thin enough to run
a long graph without losing the plot.

The project is mid-build. These documents describe the system as it is intended to work, and every
chapter and section opens with a status line saying whether the paragraphs under it describe code
that exists, code that half exists, or a plan. A section that is only partly built ends with what is
missing.

## Where to start

Read [why agentdag exists](why-agentdag.md) first if you want to know whether the idea is any good,
and [the architecture overview](architecture-overview.md) first if you want to know what it does.
The overview alone answers every common question at least briefly; the topic documents are for when
one of those answers is the one you actually needed.

| Document                                              | What it covers                                                                                                              |
|-------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| [Why agentdag exists](why-agentdag.md)                | the five constraints that bind a language model, and why the shape that follows is a DAG scheduler rather than an org chart |
| [Architecture overview](architecture-overview.md)     | the bird's eye view: how a run is launched, how work and results move, how the graph's shape is decided, what runs a node   |
| [Execution model](execution-model.md)                 | nodes, records, the journal key, replay and the crash window, approvals, parallelism, the tier policy                       |
| [Executors and models](executors-and-models.md)       | how a model is actually driven, credentials and subscriptions, the Codex arm, and where other model families attach         |
| [Safety and sandbox](safety-and-sandbox.md)           | what is enforced today, what measurably is not, and what the sandbox port becomes                                           |
| [Claude Code integration](claude-code-integration.md) | what a dispatched node does and does not inherit from your setup, knowledge, and the self-improve loop                      |

Two reference documents sit beside these. [Module reference](systemdesign/module_reference.md) is the
file index for the source tree, and [the decision records](adr/) hold choices narrow enough to belong
in their own note. The [README](../README.md) is the command reference and stays the single source
for it; these documents link to it rather than restating it.

## Where the project stands

| Milestone | Scope                                                                                                                                                                                         | State            |
|-----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------|
| M0        | close the design's contradictions before anything is built                                                                                                                                    | done             |
| S0        | measure the premises the design rests on rather than assuming them                                                                                                                            | done             |
| M1        | the baseline: one graph, no journal, no cap, no unattended approval                                                                                                                           | done             |
| D2        | adopt a durable-execution platform, or rebuild against the same failure tests                                                                                                                 | decided: rebuild |
| M2        | the kernel: journal, run store, replay, tier policy, the Claude arm, the CLI                                                                                                                  | done             |
| M3        | the three properties: a cap that fires, a deadline that kills, approve and cancel. The sandbox port is defined and declares that it isolates nothing; a real boundary behind it is undesigned | in flight        |
| M4        | the Codex arm, and a graph that runs one branch on each family                                                                                                                                | planned          |
| M5        | the first real run, attended, against a real fleet chore                                                                                                                                      | planned          |
| later     | the network face, resources beyond one lock, knowledge grants, a planner node, further graphs, the drift review loop                                                                          | planned          |

The M1 baseline is still in the repository and still runs. It is the control the kernel is measured
against, which is why it was built deliberately without a journal, a cap or an unattended approval:
what their absence costs is the thing worth knowing.

The full design corpus, including the probes behind the measured claims and the milestone plans at
three altitudes, is internal and not part of this repository. What it decided is stated in these
documents rather than linked.
