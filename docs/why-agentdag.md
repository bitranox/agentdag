# Why agentdag exists

agentdag coordinates teams of AI agents. Every structural choice in it follows from one question:
what are a language model's real limits, as opposed to the limits the word "team" quietly imports
from human organisations.

The short answer is that the obvious structure is the wrong one. Lead agent, teammates, delegation,
a shared mailbox: all of that describes an organisation of people, and it answers a problem people
have. A model has a different problem. Build for the human problem and you get a system that looks
organised and degrades for reasons nobody can see.

---

## 1. A model is not a person

Put the two side by side. The last column is the one that matters, because a team's shape is nothing
more than the accumulated answer to these limits.

| Limit                        | A person                                                                                                      | A language model                                                                                                                                                                                          | What it does to the team                                                                                                                                                                                                             |
|------------------------------|---------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| How much it can hold at once | about four to seven things, and no training changes that                                                      | a window of hundreds of thousands of tokens, several hundred pages, but attention thins across it long before it is full                                                                                  | both need work cut into contained pieces, for opposite reasons: the person cannot fit it in, the model can fit it in and stops attending to the middle                                                                               |
| Getting knowledge in         | slow and expensive: days to weeks to bring somebody up to speed, which is the cost hierarchy exists to ration | any node reads the same store in seconds, so giving one node what another knows costs a read rather than a training-up                                                                                    | no chain of command and no trickle-down, because the scarcity layers were invented to manage is not there                                                                                                                            |
| What survives afterwards     | it sticks; yesterday is still in the room                                                                     | nothing survives a dispatch here: each node runs in a fresh client that remembers none of the last one                                                                                                    | anything that must outlive a step is written down, or it did not happen                                                                                                                                                              |
| Being interrupted            | tap them on the shoulder and they stop mid-sentence                                                           | reachable but not preemptable: a message lands at its next tool call, which may be after the thing you wanted to prevent                                                                                  | a message can steer a run, but nothing correctness depends on may wait for one, so exclusion is structural and order is fixed at plan time                                                                                           |
| Knowing it is wrong          | unreliable, but it can feel unsure and say so                                                                 | confidently wrong, and asking it about its own work adds nothing                                                                                                                                          | what decides is mechanical wherever a mechanical check exists; where judgement is unavoidable it comes from a separate node with no stake, counted by code                                                                           |
| Cost of one question         | two minutes of work costs two minutes                                                                         | a large fixed cost before any work: tens of thousands of tokens for the first node on a given model, and about a tenth of that for each later node ON THAT SAME MODEL, whether or not it shares its brief | give a node enough work to be worth starting, and keep a graph on FEW model rows: the cache key includes the model, so each row costs a full startup that only work repays. The brief is late in the prefix, so it may vary per node |
| Coordinating who does what   | cheap: a two-minute conversation settles it, which is why organisations run on meetings                       | dispatching to a fresh subagent separates the contexts properly; what returns is a summary, and the coordinator keeps every one of them                                                                   | constrain the return channel: a typed record and a path, so the coordinator decides what happens next without ever holding what happened                                                                                             |

Two of those rows are worth reading together, because they invert. For people, moving knowledge
between heads is the expensive part and talking is the cheap part, so organisations grow layers to
ration the first and run on meetings for the second. For a model it is the other way round: sharing
knowledge costs a read, and talking is what costs. Copy the human answer and you pay twice, once for
rationing something that is nearly free and again for spending freely on the thing that is not.

The human figures are the ordinary ones from general knowledge rather than anything measured here,
and nothing turns on the exact number. What matters is which way each row runs.

The two sides of the inversion are not equally established, and the difference is worth stating.
The COST side is measured: the per-node startup on a given model row, and the roughly one-tenth of
it that a later node on that same row pays. The TALKING side is not. That peers exchanging messages
would cost more than each reading what it needs is an inference from those figures, not a result
from running it, and no measurement here has put N talking agents on one task and counted what it
came to. Read the inversion as the reasoning the design was built on rather than as a finding, and
treat anything that actually runs that arm as able to move it.

Five constraints do the work in that table. None of them is the one an org chart is designed around.

**A finite context window whose attention degrades before it fills.** This is the constraint that
surprises people, because it is not a capacity limit in the way a disk is. A model does not work
perfectly up to the last token and then stop. It gets worse gradually, and it gets worse in a
particular shape: it loses the middle. Give it twenty things to hold and it will answer confidently
about the first few and the last few. The literature calls this lost-in-the-middle, and pairs it with
context rot, the slower decline in quality as a window fills even when nothing has been dropped.
Both terms are used here from general knowledge rather than checked against the papers, and the
argument does not rest on the citation: anyone can watch attention degrade on their own workload
without one. That is the mechanism the design has to survive.

**No state except on disk.** A model holds nothing between one dispatch and the next. Whatever must
survive has to be written down somewhere a later process can read.

**Delivery only at the recipient's tool-call boundary.** You can reach a running agent, and it will
read what you sent. What a MESSAGE cannot do is preempt it: it arrives when the agent next pauses to
call a tool, and a long stretch of thinking postpones it.

Preemption itself does exist, but only for the process holding the agent's client. Measured, an
interrupt stops a dispatch mid-tool in under a tenth of a second, three seconds into a ninety-second
call, and the tool's own process is dead afterwards. That is a lever the coordinator has over its
own children. It is not a lever one agent has over another.

So steering by message works, and is genuinely useful for anything that tolerates latency. Using a
message as a lock or a stop button does not, because by the time it lands the agent may already have
done the thing you were writing to prevent. Between two agents, no guarantee may rest on delivery:
they are kept apart by disjoint work, not by telling one to wait.

**No self-verification.** A model asked whether its own work is correct will tell you it is. Not
always, but often enough that the answer is not reliable enough to decide anything on. Confidence is
free.

**A fixed price per dispatch.** Starting an agent costs a large amount before it reads a word of
your task, because its system prompt, its tool definitions and whatever settings cascade it
inherits all have to be sent first.

The FIRST node pays it in full. Measured on this machine, a node inheriting no settings at all still
sends tens of thousands of tokens before the work starts: two probes of trivial work measured 19,000
and 26,000, the difference being the brief and the tool set rather than anything the node did. So
the figure is not a constant to quote, it is a floor that moves with what you send.

Every LATER node in the same run pays about a tenth of it. Caching is a prefix match over the tools
and the system prompt; the shipped graph sends the same tool set and the same brief to every node,
and a node's working directory is a separate option rather than prompt text, so the prefix is stable
across nodes. Measured: two dispatches, identical brief, different working directories - the first
read nothing from cache, the second read 26,159 of its 26,161 input tokens from cache. Same token
count, and a cached read is billed at roughly a tenth.

So the cost of a fleet is one full startup plus a tenth of one per node after it, provided the nodes
run on the same model, and it survives a workflow that gives each node its OWN brief: the brief sits
late in the prefix, so rewriting one re-created about a seventh of the startup rather than all of
it. An earlier version of this paragraph said such a workflow "would pay it in full every time",
which measurement refuted. What it does NOT survive is a change of model: a prefix warmed on one row
gives another row nothing, so the cost is one full startup per row a graph uses.

Two limits on that, both worth knowing before budgeting against it. The tenth applies to the
STARTUP, not to the node: a node that works for many turns spends most of its budget on its own
growing conversation, and the shared prefix is then a small part of a large bill. And a cache entry
has a lifetime, so nodes dispatched far enough apart pay the startup again. The measurement above
was two dispatches seconds apart and says nothing about where that boundary falls.

The part that does not depend on that answer is the shape: the price is attached to starting a node
rather than to the size of the task it is given, so a node should carry enough work to be worth
starting at all.

## 2. Why the human org chart does not transfer

Hierarchy in a human organisation answers span of control. One person can direct only a few people,
can hold only so much in their head, and transferring what they know to someone else is slow and
expensive. Layers exist to ration those three scarcities.

Two of the three bind a model differently enough that hierarchy is the wrong answer to them.
Knowledge on disk is equally cheap for every agent to
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

Almost every framework for putting several AI agents to work starts you in the same place: name the
roles. You declare that you want a researcher, a writer and an editor. You give each one a job
description. Then the framework arranges for them to talk to each other until something comes out
the other end.

That feels reasonable, because it is exactly how you would staff the job with people. It is also the
wrong shape, and the cheapest way to see why is to look at what it costs.

Two of the three observations below carry a date. That is deliberate: a framework's default can
change in a release without anyone announcing it, this document cannot watch three projects on your
behalf, and a dated claim that has since moved reads as out of date rather than as wrong. The
CrewAI structure is checked against that project's current documentation and so is stated flat.

**Everyone is copied on everything.** In August 2026, AutoGen's graph mode sent every message to
every agent by default. The graph you drew controlled the order people spoke in, not who heard what:
in its own words, the execution graph does not control what messages an agent receives. Limiting who
sees what was something you switched on.

Imagine a meeting where nobody is allowed a private word. Every remark goes to all eight people in
the room. And before anyone speaks, they silently re-read the entire transcript of the meeting from
the beginning. People never work that way, because no one could. A model can be made to, every
single turn, and you are billed for every word it re-reads.

That is one arrangement, and it is the worst case rather than the normal one. The arrangement most
people actually build is a master dispatching to subagents, where you say which master talks to
which subagent, and it deserves credit rather than a strawman. It separates contexts properly.
Claude Code's own documentation is explicit: a subagent "starts with a fresh, isolated context
window", it "doesn't see your conversation history, the skills you've already invoked, or the files
Claude has already read", and when it finishes it "returns only the summary". Its own tool calls
never enter the parent at all.

So the parent does not accumulate the work. It accumulates one summary per dispatch, and each of
those is sent again on every later turn the parent takes, because the API is stateless and the whole
conversation goes with every request. That cost is real, but it is over summaries rather than over
transcripts, which is a different order of magnitude, and a cached re-read is billed at roughly a
tenth rather than at nothing.

Which leaves one way to get it wrong, and it is the one that actually happens: when the summary IS
the content. Ask a subagent to research something and report, and it hands back its findings as
text, because text is what the channel carries. Now the parent holds the findings. Do that a dozen
times and the coordinator's context is the whole job, assembled one summary at a time. This harness
nudges you into it, too: a subagent that tries to write its findings to a file is told to return
them as text instead.

So the rule that falls out is not "avoid hierarchy". Hierarchy already separates the contexts, and
that is most of the win. The rule is about what the return channel is allowed to carry: a typed
record and a path to the work, so the coordinator can decide what happens next without ever holding
what happened. Records, not content, is a constraint on the channel rather than an instruction to
the agent.

The uncomfortable part is that a team gets expensive fastest exactly when it starts working well,
because working well means having more to report.

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
message to a running agent cannot preempt it, so no message is guaranteed to land before the thing
it would prevent.

So the question is not how to arrange the meeting better. It is what you build if you never hold one.

## 4. Capability is loaded, a corpus is retrieved

The resolution is one distinction, and the phrase "give every agent everything" is what hides it. Two
different things travel under it. One is standing capability: the settings, tools, skills, hooks,
accumulated memory and instruction cascade an operator's own session runs with. The other is bulk
content: a repository, a corpus, the twenty documents somebody might need to read. They behave in
opposite ways, so the design answers them separately.

**A node gets the operator's whole environment.** Settings, tools, skills, self-learning memory,
hooks, the CLAUDE.md cascade: the same set the operator's own session carries, because that set IS
the capability. The skills are how a task gets done properly rather than plausibly, and the memory is
the accumulated record of which traps this machine has already fallen into. A node without them is not
a leaner agent, it is a worse one doing the same work, rediscovering by hand what the store already
knows. There is no version of "as capable as the operator" that withholds what makes the operator
capable.

**The cost objection is weaker than it looks, and caching is why.** Loading the cascade is not cheap:
in the probe that measured it, the arm loading the full operator set sent 38,790 input tokens of
startup against 170 for an arm with no settings and no tools, and it is the ratio between the arms
that carries rather than either end. But that price is paid once. The cascade is an identical prefix
on every node in a run, the same text in the same order with nothing per-node in it, so the first node
pays it in full, so sixteen nodes at 38,790 apiece is a run carrying over 620,000 tokens of startup.

This paragraph used to continue that every node after the first "reads almost all of it back from
cache at roughly a tenth of the price", making the 620,000 "largely fictional as a bill". It flagged
that as an inference - the caching had been measured with no cascade loaded - and said that if the
inference were wrong the trade collapses, so it was the first thing to measure once a node loaded
one. It has now been measured, twice, and the answer splits.

**A loaded cascade does cache.** Two back-to-back dispatches with an identical brief in an
identical working directory read the whole prefix back. An earlier reading where only half of it
came back was traced, by capturing and diffing the real request bodies, to a one-shot notice that
this machine's own session-start hooks inject into a fresh project directory - not to anything in
the cascade or the tooling. That notice lands after the last cache breakpoint, so it does not cost
the prefix.

It is worth saying what the same capture showed about hooks, because it bears on the conditions
below: a session-start hook injects one-shot, state-dependent text into a node's prompt, and since
every node runs in a fresh workspace, every node looks like a brand-new project to one. Loaded
without care, such a hook will hand an unattended agent an instruction that has nothing to do with
its task.

**The larger correction is that the cache key includes the model.** A prefix warmed on one model
was re-sent to two others and both read nothing back, while a second dispatch to the same model read
all of it. That could have been an artifact of the tooling rendering a different system prompt per
model, so it was re-run on a prompt whose text was entirely under our control: two models that
rendered it one token apart still shared nothing, and each still cached perfectly for itself.

Since a graph assigns nodes to rows by role, a run pays one full startup PER MODEL ROW it uses
rather than one per run, and loading the cascade multiplies each of those. Nothing about sharing a
brief, a working directory or a warm-up dispatch changes that; it is a structural floor.

The lever it points at is using FEWER distinct rows in a graph. It is not an argument against cheap
rows, which is a mistake an earlier version of this section made: a cold start is paid once per row
per run, while the rate difference between rows is paid per token of work, so a cheaper row repays
its own cold start once the work routed there is large enough - on the order of tens of thousands of
tokens, which any node doing real work passes. Where the cold start does dominate, the node was too
small to be worth starting, which is the granularity argument rather than a pricing one.

So the trade is not free, and the honest form of this section is that loading the cascade buys a
more capable node at a real per-node price that multiplies with the number of model rows in play.
Whether that price is worth paying is a live decision again, not a settled one. The capability
argument below is unaffected: it never rested on the discount.

**A corpus is still retrieved, never delivered.** Standing capability is small, identical across nodes
and cacheable. A body of work content is none of the three: every node needs a different slice, so
none of it caches, and handing a node twenty documents it might need is the reliable way to make it
wander, by constraint one. So the mechanism for bulk knowledge stays what it was. A node gets its
task, the slice it certainly needs, and a path to everything else, and it fetches what its brief
names. This is not a novel idea so much as one already implemented three times over in the surrounding
tooling: a skill is an index line with its body fetched on demand; a stored fact is a pointer with its
body fetched on demand; a semantic index sits above both. It is also why loading the whole environment
is not the thing section 2 warns against: that environment is mostly index lines, and the bodies
behind them stay on disk until something asks for one.

Four things have to hold for a node to run under the operator's environment, and none of them is
optional.

**The resolved cascade is part of what identifies a call.** Its hash joins the journal key, or replay
stops being replay: the same brief under a memory store that has since grown would be served from the
journal as though nothing had changed. The key already counts the knowledge grant among its identity
fields and carries a format version for exactly this kind of addition.

**Hooks split by lifecycle.** PreToolUse and PostToolUse are the ones worth inheriting, because they
are the operator's own guard rails on individual tool calls. SessionStart and Stop must not load: a
Stop hook written for an interactive session expects a person to answer it, and a headless node that
meets one hangs instead of finishing.

**Memory is read by every node and written by one.** N nodes writing the store at once is the
several-hands-one-whiteboard failure from section 3, reproduced in the one place whose entire value is
that it accumulates. Reads are free and parallel; writes go through a single node, which is section
6's one-writer rule applied to the store rather than to the tree.

**The executor's own isolation hooks bind on top, not instead.** Denying a write outside the isolation
root and denying a listed Bash command are what make a node containable at all, and they are the
executor's guarantee rather than the operator's preference. An inherited hook set adds to them and
must not be able to replace them.

None of that is shipped. Both executors ask for no setting sources today, so what a node actually gets
is its brief and its tools, and the knowledge grant is a field on the node spec that nothing resolves
into content. The four requirements are conditions on the change rather than descriptions of it.

The practical form of the distinction is that the coordinator never gathers results as text. A node
writes what it produced to disk and returns a typed record naming it. The next node that needs the
content reads the file. Nothing accumulates in a single growing context just because it passed
through.

## 5. What falls out is a DAG scheduler

Take the five constraints seriously and the shape is not a hierarchy and not a peer team. It is a
dependency scheduler, and its ancestors are Make, Bazel and MapReduce rather than any org chart.

**The coordinator holds records and state, never content, and stays thin.** It knows which nodes ran,
what each returned, and what is still to do. It does not know what any of them produced, beyond
their typed facts and, when one fails, its error line. This matters
more than it sounds: the coordinator is the one context in the system that everything passes through,
so it is the first thing to rot if content flows into it. A fat coordinator is the default failure,
not an exotic one.

**Each node is one agent with one contained task and retrieval access to the rest.** Contained is the
operative word, and it is a bound on drift as much as on scope.

**Parallel where independent, serial where dependent, and the test for independent is precise:**
branches are independent when they share no mutable artifact. That is sharper than it first looks. Two
agents reading the same files in parallel is fine. Two agents making un-reconciled decisions about the
same file is the failure mode the whole field has been arguing about, and it is what this rule rules
out by construction. The kernel does not check it: nothing intersects two nodes' declared write
sets, and the isolation scan deliberately excuses a write into any declared region, including a
sibling's. It is a discipline the shipped graph keeps, not a mechanism it is held to.

Where branches do share something mutable, either order them at plan time or serialise at the
resource itself rather than around everything that touches it. The test gate is the case in point:
the build environment every gate shares is guarded by the build tool that owns it, held shared for as
long as a gate is running and taken exclusively only while it is being upgraded, so gates run
concurrently and only the upgrade ever waits. A lock around the gate instead is strictly blunter: it
serialises whole test runs to protect a resource that already guards itself, and the concurrency
setting then bounds the agent nodes and not the gates. What is not available is a lock built out of
messages to a running agent: by constraint three a message arrives only at its next tool call, so it
cannot be relied on to land before the write it would prevent.

**Misclassify in the safe direction.** Running a parallel task serially is merely slow. Running a
serial task in parallel is destructive: divergent decisions, wasted dispatches, work that has to be
thrown away. Because the two errors cost so differently, serial is the safe guess and fanning out is
something to argue for. That is a rule for whoever writes a graph rather than a setting: the shipped
concurrency default is two, and the fleet graph fans out over every member without proving
anything.

**Nothing decides on prose, and nothing grades itself.** By constraint four a model asked about its
own work is not a source of information, so the checks that decide anything in the shipped graph are
programs: run the tests and read the exit code, walk the tree and list what was written outside the
declared set. A gate is the step an agent cannot satisfy by asserting that it did the work. An exit
code is the simplest form of that, not the only one; what matters is that the result is a typed
field the coordinator can branch on rather than a paragraph.

That is not the same as saying a model may never judge. Plenty of useful questions have no exit
code, and for those the design keeps a judgement node: a fresh context that reads the store and
forms a view. Two properties do the work there, and neither is mechanical. The judge is independent
of the work, and it has no stake in the verdict. Self-assessment fails both; a separate node fails
neither.

Putting the review on a different model family is a further lever on the same axis. It is designed,
it is off by default, and it is off because we have not measured whether family diversity finds real
defects that a fresh context of the same family misses. The measurement is specified rather than
assumed, and if the answer is no, the claim gets retired rather than kept as folklore. Where several
judgements are collected, the counting is to be done by code, never by one more model asked to
summarise the others. That is a designed pattern; nothing in the shipped graph collects several
judgements yet.

## 6. What survives from human organisations

Three things, and they are worth keeping because they do not come from human limits at all.

Clear ownership. One writer per resource. Conflicts resolved in one place.

Those are concurrency rules. Any system with parallel workers and shared state needs them, whether
the workers are people, threads or agents. Human organisations discovered them because human
organisations are concurrent systems. Keep the rules, drop the hierarchy that happened to carry them.

## 7. Graph as code, not a graph object

A graph can be expressed two ways: as data compiled once and then walked, or as a program that calls
the primitives as functions. agentdag is the second, for three reasons in order of weight.

Re-planning. Real work does not know its own shape up front. A debugging run finds out what to do next
from what it just learned; a fleet chore does not know how many repositories it has until it looks.
A graph-as-data system fixes its topology before the run starts unless it ships a specific escape
hatch for dynamic dispatch. A graph-as-code system
re-plans freely. Since a coordinator that cannot re-plan cannot do the interesting half of the work,
that decides it.

Determinism is achievable. The price of graph-as-code is that the program has to replay identically,
which means it may not reach for the system clock or a random number; the coordinator's injected
clock is the one sanctioned source, because it replays. That constraint is partly mechanised rather
than merely asked for: a static check refuses a workflow module that names the clock, randomness or
uuid directly. It reads only that module, so a helper function can still hide one.

The price is the point. Every model call is a node whose result is journaled, and the shipped
coordinator branches on typed fields of those records rather than on prose. That is a discipline the
graph keeps rather than something the kernel refuses to compile - the lint the design calls for does
not exist - and it is what keeps the coordinator thin, so the cost of graph-as-code buys the
property section 5 needs anyway.

## 8. Why build it rather than adopt something

The number that decides this is runs per quarter. A coordinator used a handful of times a quarter
repays only a small build, so the build stays small, and the baseline graph that sits beside the
kernel carries no journal, no cap and no unattended approval on purpose. It is the control the
kernel is measured against, and what its absences cost is the thing worth knowing.

Durable-execution platforms solve the hard half properly, and adopting one is the serious
alternative rather than a straw man. Checkpoint and resume, idempotency, retry, spend caps, an
observability journal: in that world those are runtime guarantees, not conventions a coordinator is
asked to honour.

The test that decides between building and adopting is the one a design on paper always fails. The
coordinator process dies between a node finishing and its record being written. What does the next
run resume from, and does the side effect happen twice? Anything built here has to answer that with
a mechanism rather than an intention, and [the execution model](execution-model.md) is that answer.

---

## Where this leads

- [Architecture overview](architecture-overview.md) turns these principles into a system you can look
  at.
- [Execution model](execution-model.md) is section 5 and section 8 made concrete: nodes, records, the
  journal, and what happens when the machine dies mid-run.
- [Safety and sandbox](safety-and-sandbox.md) is honest about the gap between what the design intends
  and what is enforced today.
