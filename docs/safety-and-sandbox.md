# Safety and sandbox

**Status:** partial (M2). Real mechanisms, real limits, and the limits are measured rather than
guessed. The containment layer is M3 and is in flight.

The headline first, because everything else in this document is a detail beside it: **a node is not
contained today**. It runs as the same operating system user as the coordinator, with no sandbox and
no separate account, so its shell can read anything that user can read and can make outbound network
requests. Everything below raises the cost of an accident. None of it stops a determined process.

That is stated here, and in the README, and as three false booleans on every record the system
writes, because a safety claim that only lives in prose is the kind that gets believed.

---

## 1. The one boundary that is real: the scratch-clone rule

**Status:** built (M1, carried into M2).

A run never writes to a real repository. The real ones are read exactly once, into bare mirrors, and
those mirrors are the only push targets a run will accept. A target anywhere else stops the run
before the first node is dispatched, and the same check guards the push itself.

Neither clone keeps a remote. The mirror does not point back at the real repository and the worktree
does not point at the mirror, so an agent's reflex push has nowhere to go.

This is the strongest property the system currently has, and it is worth being precise about why: it
does not restrain the node, it removes the target. An agent with an unrestricted shell can still push
anywhere it can name. What the rule guarantees is that the obvious accident, the one that happens
because pushing is what you do after committing, cannot land anywhere that matters.

## 2. Tool hooks: what they stop, and what they demonstrably do not

**Status:** built (M2), with known and measured gaps.

Every dispatched agent node gets two hooks that run before a tool call.

The first refuses any file edit whose target resolves outside the node's isolation root. It resolves
the path fully first, so symbolic links and parent-directory traversal do not get around it, and it
fails closed: a matched call that names no path at all is denied rather than allowed.

The second refuses shell commands containing any of a configured list of forbidden shapes, matched
after collapsing whitespace.

The second one is the weak one, and the weakness is specific rather than theoretical. Measured
against the shipped list, a POST with an explicit method flag, a POST with a data flag, a plain GET
carrying its data in the URL, a push run with an explicit directory flag, and an inline interpreter
one-liner all pass. And neither hook sees a file written by shell redirection instead of by the edit
tool, because that is not a tool call the first hook matches.

So the denylist is a speed bump on the exact shapes it names. Treat it as one.

## 3. The backstop: scanning what actually changed

**Status:** built (M2), with one limit under parallelism.

Because a hook is a claim, something outside the node checks afterwards. A scan takes a content
manifest of the whole run tree before and after a node runs and reports every path that appeared,
changed or vanished and that no declared write set covers. A branch whose scan finds anything fails.

What it compares against is the node's own declared write set plus every other write set declared so
far in the run plus a few housekeeping areas, so a sibling branch's legitimate work is not reported as
a stray.

That is also where the limit is. Under parallelism greater than one, a stray write that lands inside
a *sibling's* declared region cannot be attributed to either node by content comparison alone, and
the scan says so rather than naming one. A write to a path nobody declared is caught regardless of
concurrency. Closing the first case needs a process boundary, which is section 7.

## 4. One writer, one lock

**Status:** built (M2).

At most one live coordinator per run directory. The lock is a file created exclusively, holding
enough to identify its holder: host, boot identifier, process id, and that process's start time.

A stale lock is broken only when the holder is provably dead. A different boot identifier means dead
by definition; otherwise the process must both exist and have the recorded start time, which is what
stops a recycled process id from looking like the original holder. Releasing is a no-op unless the
file still names the releasing process, so a lock someone else has since taken is never released by
the wrong owner.

Shared resources outside the run directory are handled at the resource. The test gate takes one
host-wide lock for every run in the system, because the build tool environment is shared across the
whole machine and two gates would rebuild it under each other.

## 5. Teardown: what a cancel can actually reap

**Status:** partial (M2). Depends on how the coordinator was launched, and that difference is real.

| Launch                           | What a kill reaches                                             |
|----------------------------------|-----------------------------------------------------------------|
| systemd user scope, on Linux     | the whole control group, so every descendant, verified as empty |
| plain child process, other POSIX | the coordinator's process group, which reaches its children     |
| plain child process, Windows     | only the one launched process                                   |

Under the scope, stopping is not trusted on the stop command's exit code alone: the control group is
polled until it is genuinely empty. Off it, a grandchild that escaped its process group survives, and
the honest summary is that a node's grandchildren are only reliably reaped in the first row.

Which one you get is decided by probing at startup rather than by trying and falling back, because a
scope that should have worked and did not is worth stopping on.

**Not yet:** cancel as an operation, and a deadline that kills the scope and records a deadline
error, are M3.

## 6. Things that look like access control and are not

**Status:** built as recorded facts, not as controls.

A decision record carries who recorded it and under which token. Those fields record what the
recording process said about itself. Any process running as the same operating system user can write
a decision file, so the run directory's own permissions are the actual control, and the fields are
provenance rather than authentication.

The environment allowlist stops accidental leakage through inherited variables. It does not stop a
node from reading a credential file off the disk, because a node can read the disk.

The per-node credential copy stops parallel nodes from fighting over one login file and stops a
node's token refresh from rewriting the operator's. It is hygiene, not isolation.

Files the system writes are owner-only where it matters: the journal, its audit copy, and every
credential copy.

## 7. The sandbox port, today

**Status:** port only (M3, in flight).

Nodes run behind a sandbox port with two halves. One is a declaration made once per run: what
isolation this run's nodes have, as three booleans for filesystem, network egress and separate user.
The other is a per-dispatch preparation step around an agent node's body, shaped as a context manager
so an adapter can set something up and tear it down.

Exactly one adapter is wired, and it declares all three booleans false, which is the truth. Its
preparation step passes the request through unchanged.

Two things make shipping that no-op worth doing rather than a formality. The declaration is stamped
onto every record the run writes, so the isolation a piece of work ran under is a journaled fact that
travels with it, and a record replayed from the journal keeps the declaration it was originally
dispatched under rather than being restamped with today's. And the seam now exists in the one place a
boundary has to attach, which means the container adapter is an adapter rather than a refactor.

## 8. What the sandbox becomes

**Status:** planned (M3 for the container, later for the rest).

**A container per node.** Its own home, its own mounts, and, the part that matters most, its own
network namespace with egress denied by default and allowed only to the model API. The request shape
the port already passes is built for exactly this: the node directory and the worktree as the two
writable mounts, the isolation root as the ceiling, a working directory that can be rewritten to an
in-container path, the environment to inject, and an egress allowlist. The current adapter ignores
all of it, which is why its egress boolean stays false however that list is filled in.

The container comes ahead of the other M3 mechanisms, ahead of the spend cap and the deadline,
because egress is the hole that was measured rather than the one that was theorised. The command
denylist section above is the measurement.

**A virtual machine per node** is the step after that, and only becomes necessary if work is ever
pointed at repositories outside our control.

**A separate operating system user per node** was considered and decided against, which is worth
recording because it is the obvious first idea. It costs more than the container for the filesystem
half of the problem, and it delivers nothing at all for the network half, which is the half that
matters.

## 9. How to run it sensibly in the meantime

**Status:** current advice, not a mechanism.

Point runs at scratch mirrors, which the shipped graph enforces anyway. Run the coordinator as a user
that does not have anything you would mind an agent reading. Keep the run directory's permissions
tight, since they are the real control over decisions. Prefer the systemd scope on Linux so a
runaway can actually be reaped. And read a run's records rather than its transcripts when deciding
whether it did what you wanted, because the records are the part the system itself checked.

---

## Where to go next

- [Executors and models](executors-and-models.md) covers the per-arm half of enforcement, including
  what Codex's own sandbox mode does that the Claude arm does with a hook.
- [Execution model](execution-model.md) covers the run lock, the crash window and the approval
  binding in their own context.
