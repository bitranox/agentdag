# Why agentdag exists

**Status:** design rationale, settled 2026-08-16 and not reopened. The mechanisms it argues for are
built out over the milestones that follow; see [the documentation index](README.md) for where each
one stands.

agentdag coordinates teams of AI agents. Every structural choice in it follows from one question
asked before any code was written: what are a language model's real limits, as opposed to the limits
the word "team" quietly imports from human organisations.

The short answer is that the obvious structure is the wrong one. Lead agent, teammates, delegation,
a shared mailbox: all of that describes an organisation of people, and it answers a problem people
have. A model has a different problem. Build for the human problem and you get a system that looks
organised and degrades for reasons nobody can see.

---

## 1. A model is not a person

**Status:** rationale, stable.

Five constraints actually bind a language model working as part of a system. None of them is the one
an org chart is designed around.

**A finite context window whose attention degrades before it fills.** This is the constraint that
surprises people, because it is not a capacity limit in the way a disk is. A model does not work
perfectly up to the last token and then stop. It gets worse gradually, and it gets worse in a
particular shape: it loses the middle. Give it twenty things to hold and it will answer confidently
about the first few and the last few. The literature calls this lost-in-the-middle, and pairs it with
context rot, the slower decline in quality as a window fills even when nothing has been dropped.
That is the mechanism the design has to survive.

**No state except on disk.** A model holds nothing between one dispatch and the next. Whatever must
survive has to be written down somewhere a later process can read.

**Delivery only at the recipient's tool-call boundary.** You cannot interrupt a running agent. A
message reaches it when it next pauses to call a tool, and not before. Any design that assumes an
agent can be told something mid-thought is describing a system that does not exist.

**No self-verification.** A model asked whether its own work is correct will tell you it is. Not
always, but often enough that the answer carries no information. Confidence is free.

**A fixed price per dispatch.** Starting an agent costs roughly the same whether you hand it a
sentence or a page, because it pays for its system prompt and its instruction cascade every time.
On this machine that overhead runs to tens of thousands of tokens before the work begins. Twenty
small items dispatched separately spend more on greetings than on work.

## 2. Why the human org chart does not transfer

**Status:** rationale, stable.

Hierarchy in a human organisation answers span of control. One person can direct only a few people,
can hold only so much in their head, and transferring what they know to someone else is slow and
expensive. Layers exist to ration those three scarcities.

Two of the three do not bind a model at all. Knowledge on disk is equally cheap for every agent to
reach, so "who knows what" is not a transfer problem, and there is no reason to arrange agents so
that information trickles down a chain. That much of the anti-hierarchy instinct is right.

The trap is in the third. It is tempting to conclude that because a model reads faster than a person
it has no bandwidth limit, and therefore that you should hand every agent everything. That is exactly
backwards. A model does have a hard bandwidth limit. It is the context window, and by constraint one
its attention degrades well before the window is full. Loading everything into an agent is the most
reliable way to make it wander.

So the two rules pull against each other: give every agent all the knowledge, and keep every agent's
task narrow enough that it does not drift. Resolving that tension is the whole design.

A design built around talking bandwidth instead of attention bandwidth reproduces the failure it set
out to avoid, in a new costume. The constraint you dismiss is the one that shapes you.

## 3. What the tools available today ask you to build

**Status:** a survey, and the dates matter. The CrewAI structure below is checked against that
project's current documentation. The two behavioural defaults are as our August 2026 sweep found
them, and they are dated in the text rather than stated flat, because a default can change in a
release without anyone announcing it and this document cannot watch three projects for you.

Almost every framework for putting several AI agents to work starts you in the same place: name the
roles. You declare that you want a researcher, a writer and an editor. You give each one a job
description. Then the framework arranges for them to talk to each other until something comes out
the other end.

That feels reasonable, because it is exactly how you would staff the job with people. It is also the
wrong shape, and the cheapest way to see why is to look at what it costs.

**Everyone is copied on everything.** In August 2026, AutoGen's graph mode sent every message to
every agent by default. The graph you drew controlled the order people spoke in, not who heard what:
in its own words, the execution graph does not control what messages an agent receives. Limiting who
sees what was something you switched on.

Imagine a meeting where nobody is allowed a private word. Every remark goes to all eight people in
the room. And before anyone speaks, they silently re-read the entire transcript of the meeting from
the beginning. People never work that way, because no one could. A model can be made to, every
single turn, and you are billed for every word it re-reads.

That is the money, in one sentence. A conversation between five agents does not cost five times what
one agent costs. It grows roughly with the square of the conversation, because every new message is
re-read by everybody on every turn that follows it. Teams get expensive fastest exactly when they
start working well, because working well means saying more.

**One whiteboard, several hands.** At the same date, LangGraph's standard way of holding state
accumulated every message into one shared object, the most widely copied pattern in the field and
carrying no warning in its own documentation. When it fanned work out to run in parallel, those
branches wrote back into that same shared state, and if two of them wrote the same thing the default
kept one and discarded the other without saying so. CrewAI shares the shape: a flow's state is one
mutable object that every step reads and writes.

Three people writing on one whiteboard at once. The last hand wins, the others are rubbed out, and
nobody is told. You find out later, from work that quietly went missing.

**The org chart, rebuilt in software.** CrewAI is the clearest case, because it is two things at
once, and this part is current rather than dated. Its Flows are a genuine dependency graph on the
outside, sensibly ordered, with typed state. But a step inside that graph starts a crew, and a crew
is a list of agents defined by their job titles, a senior research analyst and a technical writer
and a senior editor, working through tasks until something comes out. The framework's own
documentation teaches it that way. The good structure is the wrapper; the meeting is what runs
inside it.

None of this is anyone being careless. These are capable tools built by people who thought hard, and
the role metaphor is genuinely the most natural way to explain multi-agent work to somebody new. It
is a good explanation and a bad blueprint. It imports the one human constraint that does not apply,
and it quietly drops the two that do: attention degrades long before the window is full, and a
running agent cannot be interrupted or corrected mid-thought.

So the question is not how to arrange the meeting better. It is what you build if you never hold one.

## 4. Accessible is not loaded

**Status:** rationale, stable. The retrieval mechanism it calls for is planned, not built; see
[Claude Code integration](claude-code-integration.md).

The resolution is one distinction. Knowledge available to every node, without restriction, is the
genuine advantage a model team has over a human one. But available means retrievable, not delivered.
A node gets three things: its task, the slice it certainly needs, and a path to everything else.

This is not a novel idea so much as one already implemented three times over in the surrounding
tooling: a skill is an index line with its body fetched on demand; a stored fact is a pointer with
its body fetched on demand; a semantic index sits above both. agentdag inherits the same principle
rather than inventing another.

The practical form of it is that the coordinator never gathers results as text. A node writes what it
produced to disk and returns a typed record naming it. The next node that needs the content reads the
file. Nothing accumulates in a single growing context just because it passed through.

## 5. What falls out is a DAG scheduler

**Status:** rationale, stable. The kernel that implements it has shipped.

Take the five constraints seriously and the shape is not a hierarchy and not a peer team. It is a
dependency scheduler, and its ancestors are Make, Bazel and MapReduce rather than any org chart.

**The coordinator holds records and state, never content, and stays thin.** It knows which nodes ran,
what each returned, and what is still to do. It does not know what any of them said. This matters
more than it sounds: the coordinator is the one context in the system that everything passes through,
so it is the first thing to rot if content flows into it. A fat coordinator is the default failure,
not an exotic one.

**Each node is one agent with one contained task and retrieval access to the rest.** Contained is the
operative word, and it is a bound on drift as much as on scope.

**Parallel where independent, serial where dependent, and the test for independent is precise:**
branches are independent when they share no mutable artifact. That is sharper than it first looks. Two
agents reading the same files in parallel is fine. Two agents making un-reconciled decisions about the
same file is the failure mode the whole field has been arguing about, and it is what this rule
forbids. Where branches do share something mutable, order them at plan time. There is no runtime
locking to reach for, because by constraint three a running agent cannot be interrupted.

**Misclassify in the safe direction.** Running a parallel task serially is merely slow. Running a
serial task in parallel is destructive: divergent decisions, wasted dispatches, work that has to be
thrown away. Because the two errors cost so differently, the default is serial and fanning out is
something you prove, not something you assume.

**Verification is a non-AI node returning an exit code.** By constraint four a model cannot grade its
own work, and an agent grading another agent inherits the same problem with an extra dispatch. So the
check that decides anything is mechanical: run the tests, read the exit code. The gate is the step an
agent cannot satisfy by asserting that it did the work.

## 6. What survives from human organisations

**Status:** rationale, stable.

Three things, and they are worth keeping because they do not come from human limits at all.

Clear ownership. One writer per resource. Conflicts resolved in one place.

Those are concurrency rules. Any system with parallel workers and shared state needs them, whether
the workers are people, threads or agents. Human organisations discovered them because human
organisations are concurrent systems. Keep the rules, drop the hierarchy that happened to carry them.

## 7. Graph as code, not a graph object

**Status:** built. Every shipped workflow is an ordinary Python program.

A graph can be expressed two ways: as data compiled once and then walked, or as a program that calls
the primitives as functions. agentdag is the second, for three reasons in order of weight.

Re-planning. Real work does not know its own shape up front. A debugging run finds out what to do next
from what it just learned; a fleet chore does not know how many repositories it has until it looks.
Every graph-as-data system fixes its topology before the run starts. Every graph-as-code system
re-plans freely. Since a coordinator that cannot re-plan cannot do the interesting half of the work,
that decides it.

Determinism is achievable. The price of graph-as-code is that the coordinator program has to replay
identically, which means it may not read the clock or a random number. That is a real constraint and
it is enforced rather than requested.

The price is the point. Every model call is a node whose result is journaled, and the coordinator
branches only on typed fields of those records. That is precisely the discipline that keeps the
coordinator thin, so the cost of graph-as-code buys the property section 5 needs anyway.

## 8. Why build it rather than adopt something

**Status:** decided 2026-08-17: rebuild.

The honest version of this section starts with the number that decides it: how many runs per quarter.
A coordinator used two to four times a quarter pays back its build only if the build is small. That is
why the first milestone was a deliberately minimal baseline with no journal, no cap and no unattended
approval, and why the adopt-versus-rebuild question was asked against that baseline rather than
against a wish list.

Durable-execution platforms exist and they solve the hard half properly. Checkpoint and resume,
idempotency, retry, spend caps, an observability journal: those are runtime guarantees in that world,
not conventions a coordinator is asked to honour. The design was compared against one of them on the
same failure tests before anything was hand-rolled. The test it had to pass is the one paper designs
always fail: the coordinator process dies between a node finishing and its record being written. What
does the next run resume from, and does the side effect happen twice.

The decision was to rebuild, on the same negative tests, with those answers designed in rather than
assumed. What that costs and what it bought is [the execution model](execution-model.md).

---

## Where this leads

- [Architecture overview](architecture-overview.md) turns these principles into a system you can look
  at.
- [Execution model](execution-model.md) is section 5 and section 8 made concrete: nodes, records, the
  journal, and what happens when the machine dies mid-run.
- [Safety and sandbox](safety-and-sandbox.md) is honest about the gap between what the design intends
  and what is enforced today.
