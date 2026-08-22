# Measurements

What is actually known about this system, separated from what is assumed.

Every claim below is either MEASURED (something was run and a number came back) or READ (a specific
line of code or documentation says so). Anything that is neither belongs in the design documents,
not here.

This file exists because the surrounding documents were repeatedly wrong in one direction. A claim
that sounds like a mechanism reads as though it were checked, and several were not. Sections 4 and 5
are the important half: what is load-bearing and untested, and what was believed and turned out
false.

---

## 1. How to read an entry, and how to add one

An entry names what was established, how, and what it does NOT establish. The last part is the one
that keeps a measurement honest: a number measured once, on one shape of input, bounds less than it
appears to.

An entry also says WHO established it, because that turned out to matter. Three provenances appear:

- **run here** - executed while writing this file, numbers copied from the output.
- **probe corpus** - measured by the session that built the kernel, in an internal probe with a
  write-up; read here, not re-run.
- **cited** - a reviewer reported it with a file and a line, and nobody re-opened that file
  afterwards. This is the weakest tier and it is marked rather than blended in, because a design
  intention described as a mechanism reached these documents twice in good faith.

To add one: run something or cite a file and line, and say which. A careful argument is not a
measurement however convincing, and it goes in the design instead. If a measurement later turns out
to have been misread, move it to section 5 rather than deleting it.

---

## 2. Measured

One of these was run while writing this file; the rest come from the kernel session's probe corpus
and were read here rather than re-run. Each says which.

### A later node in the same run reads its startup from cache

**Run here, 2026-08-20**, two dispatches through the shipped executor, identical brief and tool set,
different working directories, seconds apart.

| node   | input tokens | of which cache_read |
|--------|--------------|---------------------|
| first  | 26,161       | 0                   |
| second | 26,161       | 26,159              |

The second node read all but two of its input tokens from cache. The token COUNT is unchanged; only
the price falls, and a cached read bills at about a tenth of an uncached one.

**Does not establish:** anything about a multi-turn node. Both probe nodes ran a single turn, where
the terminal usage is the whole cost. A real work node's own growing conversation dominates its
bill, and this shared prefix is then a small part of a large one. Nor does it establish how far
apart two dispatches can be before the cache entry expires: these were seconds apart.

### The fixed startup cost is not a constant

**Probe corpus 2026-08-17, plus the run here 2026-08-20**: two separate probes of equally trivial
work, both with no inherited settings, measured about 19,000 input tokens in one and about 26,000 in
the other. The difference is the brief and the tool set, not anything either node did.

**Consequence:** any figure derived from a single dispatch probe inherits whichever brief that probe
happened to use. That includes the minimum-node-size threshold in the design.

### What an inherited settings cascade costs

**Probe corpus 2026-08-17**, one trivial prompt, three arms differing only in which settings the child
loads:

| setting sources        | total input tokens |
|------------------------|--------------------|
| none                   | 170                |
| the project's settings | 6,441              |
| the full operator set  | 38,790             |

The 170 arm is a bare child with no tools; no working node comes near it. What the arms establish is
the RATIO between them.

**Does not establish** what a cascade costs a RUN. These are three first dispatches, and a cascade is
an identical prefix on every node after the first, so the per-node price and the per-run price come
apart. "A later node in the same run reads its startup from cache" above measures a second dispatch
reading 26,159 of 26,161 input tokens from cache; if a cascade caches the same way, a 16-node run
sends 16 x 38,790 and pays a small fraction of it. Nobody has measured a cached cascade, so that is
an inference, and the figure binds a token cap either way, since a cap counts tokens and cannot see
the discount.

### A dispatch was stopped mid-tool in under a tenth of a second, once

**Probe corpus 2026-08-20.** An interrupt issued three seconds into a ninety-second Bash call ended
the stream 0.088 s later, and the tool's own process was dead afterwards, its last output unchanged
six seconds on. At a turn boundary the same interrupt took 0.551 s.

**Does not establish** three things, and the third is the one a reader will meet. Nothing about what
one agent can do to another: this is the process holding the SDK client stopping its own child, and
a message between two agents is not preemptive. Nothing about typical latency: both figures are
single observations on an idle host. And nothing about a process TREE - the tool interrupted here
was one bash script with no children, while a real node's tool call is a build spawning a test
runner spawning more. Whether an interrupt reaps descendants is the same question that separates a
process-group kill from a single-pid kill in the scope adapter, and it is untested.

### The tool hooks deny what they are specified to deny, and miss what was predicted

**Probe corpus 2026-08-18.** With `permission_mode="dontAsk"`, the SDK invokes the outside-root hook
for every `Write`/`Edit`/`MultiEdit`/`NotebookEdit`, honours its denial, and still allows an in-root
write with no prompt. The command denylist denies a listed command.

Neither hook sees an out-of-root write made by shell redirection through Bash, because that is not a
tool call either hook matches. This was predicted by the design before it was measured, and it is
why a post-node scan exists.

### A planner's emissions validated against the schema

**Probe corpus 2026-08-17**, 20 sequential emissions: 20 of 20 parsed as JSON and passed both the node
schema and the five validation rules.

**Does not establish** that a planner node is safe to build on: all 20 chose the same node kind for
the same prompt, so the run measures conformance on one scenario rather than judgement across many.

### A subscription token authenticates a headless child

**Probe corpus 2026-08-17.** An OAuth token authenticates an SDK child whose configuration directory is
empty, which is the mechanism the executor uses for subscription rather than metered billing.

---

## 3. Verified against source

Each of these was read at the stated place rather than inferred. Line numbers move, so the file and
symbol are the durable part. The last column says whether it was read while writing this file or
taken from a reviewer's citation without re-opening it.

| Claim                                                                                                                               | Where                                                               | Who checked |
|-------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|-------------|
| The brief is the node's system prompt, passed as a plain string                                                                     | `executor_claude.py`, `_options_for`                                | read here   |
| A node's working directory is a separate option, never prompt text                                                                  | same                                                                | read here   |
| No settings cascade is inherited: setting sources are explicitly empty                                                              | same                                                                | read here   |
| The child environment is a true allowlist, and everything else is blanked rather than omitted                                       | `executor_claude.py`, `_allowlisted_env` / `_blank_everything_else` | cited       |
| No API-key path exists; credentials are a token file or a private per-node copy                                                     | `executor_claude.py`, credential sources                            | cited       |
| The CLI never reads a credential's content; only the executor does, at dispatch                                                     | `adapters/cli/commands/run.py`, `_resolve_credential`               | cited       |
| A journal key excludes dependency names, deadline and budget, and includes the brief's content rather than its path                 | `domain/keys.py`                                                    | read here   |
| A record hash covers the record's whole canonical JSON, including its measured duration                                             | `domain/keys.py`, `record_hash`                                     | read here   |
| The audit copy is written and synced before the journal, so the journal is never ahead of it                                        | `adapters/kernel/journal_jsonl.py`                                  | cited       |
| A decision is keyed by node id and payload hash together                                                                            | `application/kernel/context.py`, approve                            | read here   |
| An apply is guarded by a marker touched only after the effect succeeded                                                             | `application/kernel/context.py`, `_apply_one`                       | cited       |
| The isolation scan compares content manifests and cannot attribute a write into a sibling's declared region                         | `application/kernel/context.py`, scan                               | cited       |
| Nothing intersects two nodes' declared write sets                                                                                   | whole tree: `write_set` has three consumers                         | read here   |
| `charged_tokens` sums input and output where input already includes cached reads, so the run-level total cannot be priced correctly | `executor_claude.py`, `_run`'s running sum                          | read here   |
| The Messages API is stateless; the full conversation is resent on every request                                                     | Anthropic API documentation                                         | read here   |
| A subagent starts in a fresh isolated context and returns only its summary                                                          | Claude Code documentation                                           | read here   |

---

## 4. Load-bearing and NOT measured

These are assumptions the design rests on that nothing has tested. The list is what surfaced during
one pass of documentation work, not the output of a deliberate audit of the design's premises, so
read it as a floor rather than the set. Nobody has gone looking for the rest.

- **Whether the coordinator survives the failure it is designed around, in production.** Crash and
  resume are proven by tests that kill a run between two journal lines. No real run has crashed on
  its own and been resumed.
- **Whether two nodes' write sets can be trusted not to collide.** A node can no longer write through
  the edit tools into a region it did not declare, but the kernel still never intersects two nodes'
  declared sets, and the isolation scan still excuses a write into any declared region. Two branches
  given the same declared path would collide silently, and a write made by shell redirection is seen
  by neither hook.
- **Whether the caching result holds for real work.** Measured on single-turn nodes; a real node
  runs many turns.
- **How long a cached prefix survives.** Untested.
- **Whether one graph's shape generalises.** Everything measured end to end here is the same fleet
  migration, with one brief shared by every node. A workflow whose nodes differ has never run.
- **Whether the cost model is right at all.** The run-level token total collapses cached and
  uncached input into one number, so money derived from it is wrong from the second node onward.
- **Whether unattended subscription use survives rate limiting.** The terms question was read; the
  behaviour under sustained load was not.
- **Every claim about a third-party framework.** AutoGen, LangGraph and CrewAI behaviour comes from
  a survey in August 2026 and from one check of CrewAI's current documentation. None of it was run.

---

## 5. Believed, then found false

Kept deliberately. Each of these was written down as fact, by someone who had a reason, and each was
wrong. The pattern is worth more than the individual entries: every one failed in the direction of
claiming more mechanism, more enforcement, or more generality than existed.

| Was believed                                                                     | What is true                                                                                                                                                             | How it surfaced                                                             |
|----------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| The sandbox port ships, declaring its guarantees on every record                 | No sandbox exists on the default branch, and no record carries an isolation field                                                                                        | A checker read the branch                                                   |
| A dispatch costs tens of thousands of tokens, because of the instruction cascade | A cascade-free dispatch still costs tens of thousands; the cascade is what varies                                                                                        | A probe was read properly                                                   |
| A dispatch costs about 170 tokens                                                | That is a bare child with no tools; no working node is that cheap                                                                                                        | The first correction over-corrected                                         |
| Everything downstream reads the map manifest                                     | Nothing reads it; the shipped graph folds branch records in memory                                                                                                       | A checker grepped for a reader                                              |
| A running agent cannot be interrupted                                            | It cannot be PREEMPTED by a message; the client holding it can stop it mid-tool                                                                                          | The interrupt probe                                                         |
| Verification is never another agent                                              | Self-assessment is what fails; an independent judge is a designed mechanism                                                                                              | Read against the plans                                                      |
| There is no runtime lock to reach for                                            | The gate held a host-wide file lock for exactly that purpose; bmk (>= 3.17.0) now guards its own shared tool environment instead, and the gate serialises nothing itself | A checker read the gate, then a later refactor moved the guarantee into bmk |
| The state file and the crash window are how a resume recognises a crash          | Nothing reads either; the next launch simply finds no result for a key                                                                                                   | A checker grepped for consumers                                             |
| An effect either has not happened or is marked                                   | There is a third state: performed but unmarked, which is the whole reason for the re-check                                                                               | A checker read the two non-atomic steps                                     |
| The parent accumulates its subagents' work                                       | It accumulates one summary per dispatch; the work never enters its context                                                                                               | The documentation said so                                                   |

---

## 6. What would raise confidence most

In the order the answers would change a decision:

1. Run a real fleet migration and kill the coordinator during it, then resume. Everything about
   crash recovery is currently proven by tests written alongside the code they test.
2. Measure a multi-turn node, to learn what fraction of a real node's cost the startup actually is.
3. Run a graph whose nodes do not share a brief, which is the case every measurement here excludes.
4. Give two branches the same declared write path and see what the system does, since the answer is
   currently "nothing checks it".
