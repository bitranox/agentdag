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

## 4. Why it is built that way

Two reasons, and they point the same way.

The first is the rule from [why agentdag exists](why-agentdag.md): accessible is not loaded. A node
should be able to reach any knowledge and should start loaded with almost none of it, because
attention degrades long before a context window fills. Starting every node under an operator's full
instruction cascade is the most reliable way to make a contained task stop being contained.

The second is cost. Starting an agent costs roughly the same whatever you send it, because it pays for
its instruction cascade every time, and a full operator plugin set multiplies that fixed cost on every
single dispatch. In a graph that fans out over a fleet, that is paid once per branch, before any work
happens.

There is a third, quieter benefit. A run is reproducible in a way it could not be otherwise: the same
brief and the same policy produce the same dispatch, regardless of what the operator happens to have
installed this week.

## 5. What a node gets instead

Its brief, as its system prompt. A fixed short first instruction. A small tool set. Its own worktree.
Its model and effort, resolved from the tier policy rather than chosen by the node. And two hooks of
agentdag's own, which refuse edits outside its isolation root and refuse a configured list of shell
command shapes. Those, and what they do not catch, are in
[safety and sandbox](safety-and-sandbox.md).

## 6. Knowledge, and the grant that is coming

The other half of "accessible is not loaded" is the retrieval path, and it is the half that does not
exist yet.

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
| inherits your hooks, CLAUDE.md, skills | yes                    | no, deliberately                          |
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

- [Why agentdag exists](why-agentdag.md) is where "accessible is not loaded" comes from.
- [Executors and models](executors-and-models.md) is the mechanics of driving the binary, including
  credentials and whether a subscription works.
- [Safety and sandbox](safety-and-sandbox.md) is what the node's own hooks do and do not stop.
