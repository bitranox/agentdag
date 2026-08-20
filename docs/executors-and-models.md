# Executors and models

**Status:** the Claude arm is built; the Codex arm is planned; other families reach the same port
later. This document is the south face: what actually happens when the coordinator dispatches a node
to a model.

---

## 1. One port, several arms

**Status:** built, one adapter wired.

There is a single executor port. An adapter behind it takes a request naming the node's directory,
its working directory, its brief, its prompt, its model and effort, its turn ceiling, its isolation
root, its declared write set and its command denylist, and returns the same record shape every other
node returns.

Not every node reaches a model at all. Gates, reductions, scans, stages and applies are code, run in
the coordinator's own process, and produce genuinely typed facts rather than anything a model said.
That is a feature of the design and not an implementation shortcut: the steps that decide things are
the ones that are not models.

## 2. A Claude node

**Status:** built.

A Claude node is a child process running the same Claude Code binary a person runs interactively,
started headlessly through the official Agent SDK and spoken to over a streaming JSON protocol on
standard input and output. One process per node. Not an HTTP call to a model API, and not a
long-lived shared session.

The node is configured entirely per dispatch: its working directory is its own worktree, its system
prompt is the brief, its model and effort come from the tier policy, its turn ceiling comes from
configuration, and its tool set is a fixed small list. Two tool hooks are installed, which
[safety and sandbox](safety-and-sandbox.md) covers.

The setting sources are explicitly empty. That is one line of configuration and it is the difference
between a node that starts under whatever the operator has installed and one that starts under
nothing but its brief. See [Claude Code integration](claude-code-integration.md) for what that costs
and what it buys.

The prompt the node receives as its first message is short and fixed. Everything specific to the work
is in the brief. The model's prose reply is written to a transcript and is not parsed for structure;
the record is built from what the run reports about itself.

## 3. Credentials, and whether a subscription works

**Status:** built for Claude. Yes for a subscription; the metered path is not implemented.

Two credential sources, chosen once per invocation and printed when a run starts.

A token keyfile, when one is configured and present: the executor reads it fresh on every call and
hands the node a token with an otherwise empty configuration directory. This is the path for a
subscription login rather than a metered key.

Otherwise a private copy of the operator's own Claude Code credential file, created owner-only, one
per node. A token refresh inside a node lands in the node's copy rather than in the operator's file,
and parallel nodes never share one writable login.

There is no API-key handling anywhere in this code path. The command-line tool itself never reads a
credential's content either: only the executor does, inside the coordinator process, at the moment it
dispatches. An operator with no credential at all is not a special case, the node simply fails with
the CLI's own not-logged-in message.

The policy table records per model row whether it bills against a subscription or a meter, so a graph
can be reasoned about on cost before it runs.

**Open, and honestly so:** whether unattended use of a subscription token by an always-on server is
permitted and survives rate limiting is not settled. The tier table keeps a metered row and a budget
either way, so the answer changes which row a graph uses rather than whether the design works.

## 4. Environment: an allowlist, not a filter

**Status:** built.

The node's environment is built from a named allowlist: the path, locale, temporary directory, proxy
and certificate variables, and the platform variables Windows needs. Everything else the coordinator
inherited is explicitly blanked rather than merely omitted, because the SDK merges the environment it
is given over the ambient one, so leaving a variable out would leak it.

The reason it is an allowlist and not a pattern is worth stating, because it is the kind of thing
that looks over-engineered until it fails. A regular expression looking for secret-shaped names does
not catch an agent socket or a cloud credential variable, since neither name contains anything that
looks like a secret. Naming what may pass is the only version of this that works.

The test gate has its own, separate allowlist, deliberately narrower.

## 5. Codex, the second arm

**Status:** planned. Measured against its real surface, not assumed.

A Codex node is one `codex mcp-server` process per node, spoken to over MCP on standard input and
output. Its surface was probed rather than guessed, and it reaches parity on most of what a node
needs: a working directory, per-node instructions in two flavours, a model, an effort setting through
its configuration, and a sandbox mode plus a never-ask approval policy that together do the write-set
job the Claude arm does with a hook.

It misses on two, and the design compensates instead of pretending otherwise.

Its result envelope is typed but its content is prose, so the adapter puts the required output shape
in the brief, validates the content as JSON, and re-asks once on the same thread when it does not
match. Threads are cheap to continue, which is what makes one re-ask the right number.

It reports no usage in the result at all. So a Codex node has its whole budget charged at dispatch,
and the real numbers are reconciled afterwards from the session log Codex writes, appended as a
second journal line rather than as an edit to the record already written. If the log cannot be read,
the charge stands. A cap that goes quiet when it cannot measure is not a cap.

It also needs its own configuration home for the same reason a Claude node does: the server otherwise
loads the operator's own skills and tools unasked, which is both a cost and a contamination.

## 6. Parity, side by side

**Status:** left column built, right column planned.

| What the coordinator needs | Claude, via the Agent SDK                                | Codex, via MCP                                                     |
|----------------------------|----------------------------------------------------------|--------------------------------------------------------------------|
| per-node instructions      | the brief as system prompt                               | base instructions replace, developer instructions add              |
| model and effort           | both first class                                         | model first class, effort through configuration                    |
| working directory          | yes                                                      | yes                                                                |
| schema-validated result    | output shape in the brief, adapter validates, one re-ask | same, re-asked on the same thread                                  |
| usage reporting            | per turn and at the end                                  | absent, so charged at dispatch and reconciled from its session log |
| in-node spend cap          | seam exists, the check and interrupt are planned         | none possible, which is why the budget is charged up front         |
| write-set enforcement      | a tool hook denying writes outside the isolation root    | its own sandbox mode plus the working directory                    |
| hard deadline              | interrupt, then terminate the process                    | terminate the process, one server per node so threads die with it  |
| long-lived node            | the executor's own compaction                            | reply on the thread, with a compaction prompt                      |
| billing                    | subscription token works headlessly                      | relayed as metered                                                 |

Four rules hold whatever is in the right-hand column, and they are what make adding an arm safe:

- An adapter that cannot report usage gets its budget charged in full at dispatch and reconciled from
  whatever log it does write.
- An adapter that cannot enforce a write set runs in a worktree and passes a scan of the whole
  isolation root afterwards. Both arms get that scan regardless, because a hook or a sandbox is a
  claim until something outside the node checks it.
- An authentication failure from any adapter is non-transient: it fails the run and does not escalate
  to another row.
- Every node, whatever ran it, returns the same record shape. That is the contract the coordinator
  depends on and the reason a second arm is an adapter rather than a redesign.

## 7. Why Claude is not reached through MCP too

**Status:** decided (2026-08-18).

It would be tidier to have one transport for every model. It was considered and rejected for a
specific reason rather than a stylistic one.

Claude Code can act as an MCP server, exposing its session's tool set including an agent tool. Against
what a node actually needs, that surface is missing the working directory, the system prompt and
setting-source control, the effort setting, the turn ceiling, and usage in the result. It also runs
inside the operator's own configuration with the whole plugin cascade, which is exactly the thing the
executor takes trouble to exclude.

So a Claude row reached over MCP could only be a wrapper we wrote ourselves around the same call the
adapter already makes: one extra process and one extra hop, re-exporting per-turn usage, interruption
and write-set denial over a protocol, for no new capability. The port is the independence boundary,
and the per-vendor field map exists either way.

This reopens if the Claude login and the coordinator ever have to live on different machines. Then
the wrapper is a remote executor, which is an added row rather than a change of direction.

## 8. Other models

**Status:** planned. The shape exists; no adapter does.

Two paths, and they are not equivalent.

An MCP server plus a per-vendor field map covers anything that exposes an agent as a tool. The
executor field on a node specification already accepts a server-and-tool name for exactly this. What
the parity table above shows is that vendors' agent-as-a-tool surfaces are dialects rather than one
protocol, so each arm is a transport plus a mapping, and the mapping is where the work is.

The vendor-neutral candidate is the Agent Client Protocol, which was designed for driving a coding
agent from a client rather than for exposing tools to a model. It has a session with a working
directory, a prompt call that returns a stop reason and usage, cancellation with a defined stop
reason, and, notably, a permission request where the *client* decides every sensitive tool call,
which is a cross-vendor write-set enforcement point MCP has no equivalent for. It is the successor
candidate for a neutral executor kind once its usage reporting is stable and its adapters track
current SDKs. Until then it would be a third arm behind the same port, not a replacement for the
first one.

A completions server for judgement nodes, where no agent loop is wanted at all, is a later row.

---

## Where to go next

- [Safety and sandbox](safety-and-sandbox.md) is what bounds any of these while it runs.
- [Claude Code integration](claude-code-integration.md) is what a dispatched node does and does not
  inherit from your own setup.
- [Execution model](execution-model.md) is what the coordinator does with the record that comes back.
