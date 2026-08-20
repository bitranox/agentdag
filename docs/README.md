# agentdag documentation

agentdag coordinates teams of AI agents. It dispatches each node of a graph, gates what the node
produced on something mechanical, and branches only on typed records, never on prose an agent wrote.
The coordinator's decisions never depend on content, only on typed records and state, which is what
keeps it thin enough to run a long graph without losing the plot. The run directory it manages does
hold content: worktrees, transcripts, the files a node wrote.

The project is mid-build, and these documents describe the system as it is intended to work rather
than only the part of it that exists today. Where a mechanism is designed but not built, the text
says so in place, usually as a "Not yet" note at the end of the section that describes it.

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
| [Safety and sandbox](safety-and-sandbox.md)           | what is enforced today, what measurably is not, and what a real boundary would take                                         |
| [Claude Code integration](claude-code-integration.md) | what a dispatched node does and does not inherit from your setup, knowledge, and the self-improve loop                      |

Two reference documents sit beside these. [Module reference](systemdesign/module_reference.md) is the
file index for the source tree, and [the decision records](adr/) hold choices narrow enough to belong
in their own note. The [README](../README.md) is the command reference and stays the single source
for it; these documents link to it rather than restating it.

## Where the project stands

|       | Scope                                                                                                                                 | State     |
|-------|---------------------------------------------------------------------------------------------------------------------------------------|-----------|
| today | the baseline graph and the kernel beside it: journal, run store, replay, suspend and resume, the tier policy, the Claude arm, the CLI | built     |
| M3    | a cap that fires, a deadline that kills, cancel, and a retry path for a failed code node                                              | in flight |
| M4    | the Codex arm, and a graph running one branch on each model family                                                                    | next      |
| M5    | the first real run against a real fleet chore, attended                                                                               | next      |
| later | the network face, resources beyond one lock, knowledge grants, a planner node, further graphs, the drift review loop                  | later     |
| open  | any isolation at all: a node runs as the coordinator's own user, and a real boundary is undesigned                                    | wanted    |

The baseline graph is still in the repository and still runs, as the control the kernel is measured
against. It carries no journal, no cap and no unattended approval on purpose: what their absence
costs is the thing worth knowing.

The premises this design rests on are measured rather than assumed: what a dispatch costs, and so
how much work a node has to carry to be worth starting; what each executor's surface actually
offers, which is what the parity table reports; and what a coordinator does when it dies between a
node finishing and that node's record landing. Those measurements, and the full design corpus behind
them, are internal and not part of this repository, so these documents state what they establish
rather than linking to them.
