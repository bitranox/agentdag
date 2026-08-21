# Claude Code integration

This document answers the two questions people ask once they realise agentdag drives the same tool
they use interactively: does a dispatched node get my setup, and how does this relate to the agents
Claude Code already runs for me.

---

## 1. Two tiers, one journal format

Claude Code's own in-session workflow tool and this program are two tiers of the same idea, and they
share a journal format on purpose.

The in-session tool is the interactive prototype. It lives in the harness, it is quick to reach for,
and it cannot do the things that need a process to outlive a session: an approval that waits
overnight, a run started on a schedule, a run driven from another machine.

This program is the headless tier and is where the primitives are actually implemented. The in-session
tool is not a second implementation of them.

## 2. Claude Code as a client

The intended relationship is that Claude Code reaches agentdag as a client rather than containing it:
a server behind a bearer token exposing the same verbs the CLI has, so a session can start a run,
watch it, answer its approvals and cancel it, whether the run is on this machine or another.

Clients drive and observe runs. They do not submit graphs. Graphs are authored in this repository and
reviewed like any other code, which is deliberate: a graph is a program with a budget and side
effects, not a prompt.

**Not yet:** all of it. The same verbs reach the run directory through the CLI first, and the server
is built behind that surface rather than instead of it.

## 3. What a dispatched node inherits from your setup

Not your hooks. Not your CLAUDE.md cascade, at any level. Not your skills. Not your plugins. Not your
environment variables, beyond a named allowlist of the ones a process needs to function at all.

The executor asks for no setting sources, gives the node its own configuration directory, and blanks
every inherited variable it did not explicitly allow. A node starts with its brief, its working
directory, its model, and agentdag's own hooks. That is the whole of its world.

If you have been assuming that a dispatched node behaves like your session because it runs the same
binary, this is the paragraph to remember.

## 4. The environment a node is meant to carry

A node should start under the operator's own environment: the settings, the tools, the skills, the
self-learning memory store, the tool-call hooks and the CLAUDE.md cascade an interactive session runs
with. That set is the accumulated capability. The skills are how a task gets done properly rather
than plausibly, and the memory is the record of which traps this machine has already fallen into. A
node without them is not a leaner agent, it is a worse one doing the same work, rediscovering by hand
what the store already holds.

The cost objection is the obvious one and it is weaker than it looks. A full cascade is a large
startup, and [why agentdag exists](why-agentdag.md) has the measured ratio against a bare child. But
it is an identical prefix on every node in a run, the same text in the same order with nothing
per-node in it, so the first node pays it in full and every node after it sends the same token count
and reads almost all of it back from cache at roughly a tenth of the price. What that leaves is a run
whose token count is large and whose bill is not. It still binds a budget cap, which counts tokens
and cannot see the discount, and it barely touches what the run costs.

Four things have to hold before a node runs under an operator's environment, and none of them is
optional.

**Hooks split by lifecycle.** PreToolUse and PostToolUse are the ones worth inheriting, because they
are the operator's own guard rails on individual tool calls. SessionStart and Stop must not load: a
Stop hook written for an interactive session expects a person to answer it, and a headless node that
meets one hangs instead of finishing.

**The executor's own hooks bind on top, not instead.** Refusing a write outside the isolation root
and refusing a listed shell command shape are what make a node containable at all, and they are the
executor's guarantee rather than the operator's preference. An inherited hook set adds to them and
must not be able to replace them.

**Memory is read by every node and written by one.** N nodes writing the store at once is the
parallel-writer failure section 8 describes, in the one place whose entire value is that it
accumulates. Reads are free and parallel; writes go through a single node.

**The resolved cascade is part of what identifies a call.** Its hash joins the journal key, or replay
stops being replay: the same brief under a memory store that has since grown would be served from the
journal as though nothing had changed. The key already counts a node's knowledge grant among its
identity fields and carries a format version for exactly this kind of addition.

**Not yet:** any of it. The executor asks for no setting sources today, so the four requirements above
are conditions on a change rather than descriptions of one.

## 5. What a node gets today

Its brief, as its system prompt. A fixed short first instruction. A small tool set. Its own worktree.
Its model and effort, resolved from the tier policy rather than chosen by the node. And two hooks of
agentdag's own, which refuse edits outside its isolation root and refuse a configured list of shell
command shapes. Those, and what they do not catch, are in
[safety and sandbox](safety-and-sandbox.md).

## 6. Knowledge, and the grant that is coming

Bulk content behaves in the opposite way to standing capability. A cascade is small, identical across
nodes and cacheable. A body of work content is none of the three: every node needs a different slice,
so none of it caches, and handing a node twenty documents it might need is the reliable way to make
it wander. So a corpus is retrieved rather than delivered, and the retrieval path is the half that
does not exist yet.

The design gives a node a list of knowledge datasets it may reach, and a place to write things back
into the shared store. Both are blocked on work in the semantic index that has not shipped: a filter
that understands who owns what, and a staging queue for writes. Until they land, a node's knowledge is
its brief plus what it can read in its worktree.

That gap is the honest weakness in the current system, and it is worth naming rather than glossing.
A node today is well contained and under-informed. The intended balance is well contained and able to
ask.

## 7. How this compares with a Claude Code subagent

|                                        | A Claude Code subagent | An agentdag node                          |
|----------------------------------------|------------------------|-------------------------------------------|
| inherits your hooks, CLAUDE.md, skills | yes                    | not yet, the change is designed           |
| survives the session ending            | no                     | yes, the run directory is the state       |
| resumable after a crash                | no                     | yes, exactly the node that did not finish |
| result the caller can branch on        | its final text         | a typed record from a closed vocabulary   |
| can wait overnight for a human answer  | no                     | yes, the process exits and comes back     |
| spend accounted per model row          | no                     | measured now, a cap is planned            |
| runs a non-Claude model                | no                     | the port is there, the arm is planned     |

Neither is better. A subagent is right for something bounded inside a conversation you are having. A
node is right for something that has to survive the conversation, be paid for out of a budget, and be
provable afterwards.

## 8. The self-improve and memory loop

The memory consolidation pass that keeps this knowledge tree tidy is already a directed graph in
everything but name: it maps over levels and facts, folds the results, reviews them, and applies
changes through the memory engine's own scripts, which are mechanical checks and therefore natural
gates.

Run as a subagent fan-out, it demonstrates in the negative every property the coordinator exists to
provide. Parallel writers share a mutable target, so one can clobber another's work and report
success, which is the shared-mutable-artifact rule being violated rather than enforced. There is no
journal, so an interrupted pass starts over. There is no resume. There is no cap.

Expressing it as a graph is a later milestone rather than the first one, for a reason worth being
straight about: it is a graph whose write set is the knowledge store itself, which is the least
forgiving place to be learning what the coordinator gets wrong. The fleet migration goes first
because its blast radius is a directory of throwaway clones.

## 9. Getting told when something needs you

A suspended run is only useful if someone finds out. The design emits a typed event when a run
suspends, finishes, fails or crashes, from the coordinator, the approval timer or the server, and
never from a node. Sinks sit behind one port: mail through this repository's own mail adapter, a
no-op by default, and Claude Code's own push notification when it is the connected client.

Until that ships, a suspended run waits until someone runs the status command.

---

## Where to go next

- [Why agentdag exists](why-agentdag.md) is where the split between standing capability and bulk
  content comes from.
- [Executors and models](executors-and-models.md) is the mechanics of driving the binary, including
  credentials and whether a subscription works.
- [Safety and sandbox](safety-and-sandbox.md) is what the node's own hooks do and do not stop.
