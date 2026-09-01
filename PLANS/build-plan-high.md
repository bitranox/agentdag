# agentdag build plan - HIGH level (the picture, the slice, the cuts)

> **The `RESEARCH/` paths point into a private companion repo.** These documents were written
> beside a private research repository and cite it by repo-qualified path for the design
> documents, probe scripts and measurement notes they were derived from. The `RESEARCH/` prefix
> names that repo; it is deliberately not a relative path, because no relative path from here
> resolves to it. These citations do not resolve in a clone of this repo. They are kept rather than stripped because a claim that names its source
> is evidence of where it came from even when the source is not public, and removing them would
> leave the assertions here with no provenance at all.

Written 2026-08-21 against `RESEARCH/workflow/design/2026-08-21-decomposition-design.md`, the E1 measurements in
`RESEARCH/workflow/probes/`, and the differentiator analysis of 2026-08-20. Read this before the mid and detailed
plans; they are checked against this one, never the reverse.

**Precedence, and it splits by KIND of question (revised 2026-08-21).** This page used to say
flatly "if this page and a lower plan disagree, this page wins and the lower plan is wrong". That
rule misdirects, and it did: a 2026-08-21 audit found the disagreements ran the other way, because
this page is written in bursts while the lower plans are updated as work lands, so the governing
page is routinely the STALE one. A reader "correcting the lower plan to match" would have deleted
finished, gated work.

- On **scope and judgement** - what is cut, what a milestone is for, what order things go in, what
  the product is - THIS PAGE WINS. That is what an altitude is for, and no lower plan may quietly
  re-open it.
- On **state** - what is built, what a mechanism does now, what a test covers - the **most recently
  updated document wins**, and a disagreement is a SIGNAL TO READ THE CODE rather than a rule to
  apply. Every instance the audit found was of this kind: a crash-window test recorded as owed
  after it shipped, an M3 row missing two components, a run deadline described as built that does
  not exist. None was a scope disagreement.

When you cannot tell which kind a disagreement is, it is a state question - scope disagreements are
rare and loud, and they are settled with the user, not by precedence.

## What we are building, and for whom

**A job you can walk away from.** A coordinator that runs a graph of AI-agent nodes unattended:
the run outlives the session that started it, it survives a crash and resumes without redoing
finished work, it stops and waits when a human decision is genuinely required, and it cannot
repeat an irreversible effect. One developer builds it, one developer uses it, on one Linux box.

That sentence is the product. Everything below is judged against it.

What it is NOT: a multi-tenant service, a Hermes back end, a resource scheduler, a knowledge
platform, or a better way to decompose a task inside one session. The last one matters most and is
the hardest to keep cut, so it has its own section.

## The differentiators, and what each still needs

Established 2026-08-20 by ELIMINATION against the closest shipping implementation (Claude Code's
own Workflow tool, read at source level), not by assertion. Of the properties the corpus claimed,
**these already ship there**: work nodes, parallel fan-out, reduce, schema-typed records,
content-addressed journal with prefix replay, determinism by making the clock and RNG throw, and
sub-graphs. Those are removed from the list below and must stay removed. Three further properties
turned out to ship only under a CONDITION, and are held in `### Partially ships` below: neither
claimed as differentiators nor cut outright.

**That elimination covered ONE surface, and a 2026-08-30 doc re-read found that it matters.**
Claude Code documents four ways to run agents in parallel. Workflow was read at source level;
agent teams was assessed separately (`DECISIONS.md` carries a MEASURED row on its mailbox);
**agent view and routines were never assessed at all** - a sweep of RESEARCH on 2026-08-30 found
no mention of the FEATURE, by name or at concept level, with a control that had to come back
present. The few string matches are the ordinary English phrase, not the feature; the note
records the search and its control. Agent view is the surface built
around handing work off and checking back later, which is the nearest thing in the product to
this page's own opening sentence. Findings, all tier DOC-READ:
`RESEARCH/workflow/design/2026-08-30-claude-code-surface-re-read.md`.

**A second SHIPPING implementation was read at source on 2026-09-01: OpenClaw 2.0**
(`v2026.8.1`, MIT), whose `extensions/workboard/` is a genuine DAG scheduler - nine statuses, five
link types, dependency-gated promotion enforced at `store-core.ts:1132-1153`, cycle detection,
heartbeat leases, retry budgets. It was never in the 2026-08-20 elimination set, which covered
Claude Code surfaces only. It eliminates nothing on the list below and strengthens rows 1, 2 and 3.
Findings, tier SOURCE-READ: `RESEARCH/landscape/OPENCLAW-2.0.md`.

### Partially ships

A third tier, added 2026-09-01. Three entries were put on the already-ships list in the 2026-08-20
elimination and none of them survives as written: each ships, but only under a condition that the
one-word entry hid. A binary list forced each to be either claimed or cut, and both answers are
false, so they are held here instead.

**The rule for this tier: a row must name the MECHANISM and the CONDITION under which it holds.**
A row that cannot name both belongs in one of the two lists, not here. Without that rule a middle
tier is where a claim goes to avoid being decided.

- **a hard token budget.** Mechanism: `WorkflowBudgetExceededError`, raised at batch dispatch,
  counting OUTPUT tokens. Condition: only when the session's turn budget is set; with no budget
  the guard returns immediately and the only always-on backstop is the 1000-agent cap. The
  "Large workflow" badge is advisory as documented and throws nothing, and the Agent SDK caps
  DOLLARS (`max_budget_usd`, covering subagent spend) plus `max_turns`, never total tokens.
  So "a hard token budget already ships" is true for an operator who set a turn budget and false
  by default, which is the unattended case this project is built for.
  Measured by probe P3, 2026-08-31: `RESEARCH/workflow/design/probes/cli-surface-p2-p3.md`.
- **per-agent worktree isolation.** Mechanism: `isolation: worktree` frontmatter. Condition: on
  SUBAGENTS only; the word does not occur in the workflows page at all.
- **no-barrier pipelining.** Mechanism: completing a task "unblocks the dependent tasks".
  Condition: in AGENT TEAMS only; workflows' `parallel()` explicitly "waits for all of them".

The last two do not change WHETHER the property ships, only WHERE, which matters because the
surfaces carry different limits. Every other entry stays cut: they were read at source, and a
documentation silence is not a refutation.

No count is given deliberately. The figure this page carried, "nine of fourteen", cannot be
reconciled with its own enumeration - the list ran to eleven items - and the grouping that produced
nine is recorded nowhere. The LIST is the claim; the tally was never verified and is not repeated.

One more, mid-run re-planning, was on that list and **does not survive a source read** (see "The
thesis" below). It is split rather than removed: the half Workflow covers is gone, and the half it
does not cover is not a differentiator on its own either.

| # | differentiator                                          | why it survives elimination                                                                                                | state                                          | what it still needs                          |
|---|---------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|------------------------------------------------|----------------------------------------------|
| 1 | the run outlives the session                            | UNDER RE-VERIFICATION 2026-08-30, see below: the predecessor's resume is documented same-session only, in its own contract | BUILT, measured: kill at +9s, resume exact     | one crash test on a real chore               |
| 2 | it stops and waits for a human decision, then resumes   | UNDER RE-VERIFICATION 2026-08-30, see below: a run that ends at the decision is not a run you walked away from             | BUILT on `main`, suspend-and-resume tested     | nothing; the deadline default merged         |
| 3 | it cannot repeat an irreversible effect                 | replay safety for side effects, not just for compute                                                                       | BUILT; crash window DEMONSTRATED, not asserted | nothing; the negative test landed 2026-08-21 |
| 4 | pass or fail is decided by something other than a model | this is what makes 1 to 3 SAFE to leave alone                                                                              | BUILT (non-AI gate, exit code)                 | nothing; STRENGTHENED 2026-09-01             |

**Row 4 is strengthened by the strongest evidence available for it, 2026-09-01.** OpenClaw's
workboard declares `WORKBOARD_PROOF_STATUSES = [passed, failed, skipped, unknown]` and a
`missing_proof` diagnostic - the data model for a mechanical gate. It does not enforce it.
`missing_proof` is produced at `store-card-helpers.ts:498`, cleared in `store-workflow.ts` and
`store-enrichment.ts`, and surfaced as a UI filter at `ui/src/pages/workboard/view.ts:131`; no
transition anywhere blocks on it. A card reaches `done` on the say-so of whatever moved it. So the
closest competitor built the field, named the diagnostic, and stopped short of making it decide.
That converts row 4 from "we chose differently" into "someone else arrived here and did not cross."

**Rows 1 and 2 are UNDER RE-VERIFICATION as of 2026-08-30, on DOC-READ evidence only.** The
BUILT column is unaffected - what is in question is the middle column, whether the property is
still uncovered elsewhere. Do not act on this before probes P1 and P2 in the detailed plan: the
claims below were read from the binary and measured, and a documentation read is the weaker
instrument, so this is a reason to probe rather than a correction.

- **Row 1.** The supporting sentence "the predecessor's resume is documented same-session only,
  in its own contract" is true of Workflow and FALSE of agent view, which states that a
  background session "keeps running without a terminal attached", hosted by a per-user
  supervisor process, so you can close your shell and "your dispatched work keeps going".
  What may still survive is NARROWER and this page does not currently claim it:
  **crash-durability at NODE granularity without redoing finished work**, rather than the run
  outliving the session. Agent view stops on machine shutdown and restarts in-flight subagents
  "from the beginning", which is the residue. Probes P1 and P4.
  **Second data point, 2026-09-01.** OpenClaw persists cards in SQLite and drives them from cron,
  so a run DOES outlive a session there - but recovery is reclaim-and-retry on lease expiry, not
  resume-without-redoing-finished-work. That is the same residue as agent view's "restarts in-flight
  subagents from the beginning", now found in a second, independent implementation. The narrowed
  claim is what survives, and it survives against both.
- **Row 2.** The Agent SDK ships exit-and-resume on human input: a `PreToolUse` hook returning
  the `defer` decision lets "the process ... exit and resume later from the persisted session".
  The residue is that this and agent view's pause are TOOL-level, not a gate the GRAPH declares
  over a node's output; workflows concede that case outright ("No mid-run user input"). Probe P2.
  **P2 ran 2026-08-31** (`RESEARCH/workflow/design/probes/cli-surface-p2-p3.md`): the resume
  machinery works as documented, measured end to end, so nothing is restored there - and the
  residue named above is confirmed at source rather than inferred. `defer` is accepted on
  `PreToolUse` alone, no turn-end hook has an output channel, and it therefore intercepts a call on
  the way IN; a completed node's output cannot be gated through it without the model emitting a
  sentinel call. It is also refused for a call served to a cloud session.
  **Second data point, 2026-09-01.** OpenClaw has approvals, but authority is scope- and
  device-tiered with first-answer-wins across surfaces (`operator-approval-authorization.ts:73-79`,
  with an explicit design comment), and it intercepts a TOOL CALL, not a node's declared output.
  Identical residue shape to the `defer` finding. Two implementations, same gap.
- **Rows 3 and 4 are STRENGTHENED** by the same pass and need no action. No surface offers
  side-effect idempotency, and workflows actively re-run completed agents after a mid-fan-out
  failure ("runs B, C, and D again"). **Row 3 again on 2026-09-01:** workboard has no general
  side-effect idempotency either. OpenClaw has it only pointwise - a suggestion-accept key, and a
  `turnId` plus request-hash fence on worker inference - with nothing equivalent to `stage`/`apply`.

**The BUILT column was checked against the code, not against a previous plan.** All four have a
consumer, not merely a definition, which matters here because three earlier mechanisms in this
project (the map manifest, the state file and crash window, and the Scope port's `kill`/`is_alive`)
turned out to have a producer and no caller, the last of them fully tested and never called.
`stage`/`apply` are called from `workflows/graph_a.py:165-170`, and replay safety for the side
effect is covered by `test_apply_replay_pushes_nothing_and_refuses_non_scratch`, alongside
`test_crash_window_is_redispatched_and_only_it` and two graph-A crash tests.

The earlier finding that the `stage`/`apply` exit criterion "has no owner" was a BOOKKEEPING gap,
and it is closed: M3 owns it, and the work landed on 2026-08-21. One thing is still NOT covered:

- **The crash window between the effect and its marker** was closed on 2026-08-21, and closing
  it CHANGED the mechanism: `apply` now records in two phases, `attempted/<kind>/<key>` before the
  effect and `done/<kind>/<key>` after, and the fact reaches the workflow through
  `PerformIntent(may_have_landed)`. Two kernel negative tests cover it, both RED-verified by
  mutation, each with a control that winds the ref back and requires a push. So the guarantee is
  demonstrated at kernel level, not asserted. Anything written before 14:00 on 2026-08-21
  describing a single `done` marker is stale.
- **The same guarantee on a real chore** with real side effects, which is the crash test in M5.

**Differentiator 4 is the one that is easy to mistake for a limitation.** Requiring a mechanical
oracle binds the system to domains that have one, in practice software. Read as a cost, relaxing it
looks like pure generality. It is not: you can only walk away from a run if something other than a
model decides whether the work passed. The restriction and the differentiator are one fact seen
from two sides, so relaxing it does not widen the product, it removes it.

**The remaining cost of the DIFFERENTIATORS is one crash test and one real run.** That is the
forward-looking number for the substrate, and it is not the remaining cost of this plan. Two things
sit beside it and must not be read into it: M3's own tail (Tasks 24-27 plus the resource decision),
and M6, which is a milestone as of 2026-08-21. **M6's size is UNMEASURED**: it was called "roughly
M3-sized again" on the strength of counting its task list against M3's, which is an inference and
not a measurement, and this page tiers provenance per claim precisely so that a guess cannot be
quoted later as a figure. What would calibrate it is M3's own record - the SDD ledger
(`.bitranox/sdd/progress.md`) and the repo's git history - and nobody has taken it. The substrate
being nearly finished is not the project being nearly finished.

## Demand: what actually justifies the build

**The subject is ONE COMPLEX TASK broken down across sub-agents.** The user, 2026-08-20:

> "forget about the 'repos' case - thats not relevant. the system should be able to handle and
> brake down complex tasks to differnt (sub) agents"

and immediately after, "agent teams should be organized in a way that is LLM compatible, not human
compatible". That settles what this page has to justify, and it is not fleet operations.

**Graph A, a migration over ~20 repositories, is the TEST VEHICLE.** It is kept because it is a
good one: many independent units, a mechanical gate per unit, and real irreversible side effects,
which is exactly the shape that exercises replay, `approve` and `stage`/`apply`. It is not the
product, and no part of this plan's justification may rest on how often it occurs.

**The demand evidence is E1's task corpus** (`workflow/probes/probe_decomposition.py`). Its own
docstring says the tasks are "drawn from actual work on this machine rather than invented", and
that provenance is RELAYED, not checked - nobody has walked the fourteen back to the sessions they
came from. The SHAPE split below is directly checkable from the file; the claim that each task is
real work rests on the probe author's word:

| shape                                                      | count   |
|------------------------------------------------------------|---------|
| a single complex task worth decomposing                    | 11 / 14 |
| multi-repo shaped (fleet lint, fleet API, release cascade) | 3 / 14  |

The eleven span debugging a silent performance fault, a closed-source driver, a market sweep, a
plan audit, a wedged host, a documentation-truth pass, cross-platform CI, a performance
attribution, a security review, a knowledge-store consolidation, and a feature with tests. That is
the work the coordinator is for, and the repo case is a fifth of it.

**What is NOT measured, stated so nobody quotes it later as if it were**: a RATE. Nobody has
counted how often a task of that kind arrives. The corpus shows the SHAPE of real work and says
nothing about frequency, and a frequency argument is what the old version of this section tried to
make from the wrong data.

**The repo-sweep measurement is kept, relabelled.** It is real and it sizes the VEHICLE, not the
product: sweeps touching >= 3 repos ran 8 to 23 per quarter with 7 touching >= 18 inside five
weeks, and of ~29 big sweeps only about 4 needed per-repo judgement (git history, 2026-08-20) -
the rest is template distribution `distribute.sh` already does without an agent. Read that as
"graph A is a realistic vehicle and will get exercised", never as the reason to build.

## The thesis, and why it IS a milestone (decided 2026-08-21)

The project's founding idea is a coordinator that decomposes a complex task across sub-agents. E1
tested it for the first time on 2026-08-20 and it half-survives.

**What E1 established (measured, `probe_decomposition.result.json`):** 14 real tasks, 241 nodes,
zero schema errors, 14/14 acyclic with every dependency resolving, and 0/14 collapsed to a single
work node. Shape tracked the task. The falsifier did not fire.

**What E1 broke:** the emitted graphs carry no instructions at all, because `NodeSpec` has
`brief_ref` (a path) and no field for brief text, and a planner as specified cannot write a brief
anywhere. A planner-emitted graph is not dispatchable. Two further gaps: the planner is never told
what a node costs, and how a planner-emitted node ENTERS a running graph is undesigned.

**What the predecessor actually covers, read from source 2026-08-21** (the CLI binary at
`~/.local/share/claude/versions/2.1.238`). The earlier claim that Workflow ships "mid-run
re-planning" is FALSE as stated, and the correction cuts both ways:

- Workflow's script is authored and AST-frozen BEFORE the run. `meta.phases` must be a pure
  literal, the script compiles to a `vm.Script` with `codeGeneration:{strings:false}`, and the run
  is one shot with no re-entry. Its own tool description says it is for control flow that is
  "deterministic ... rather than model-driven". What looked like re-planning is the OUTER model
  editing the script between invocations and relaunching behind a prefix cache; resume is
  same-session only. **That last clause is UNDER RE-VERIFICATION (2026-08-30, DOC-READ):**
  workflows now document `Move to background and exit` and `claude --resume` replay, and agent
  view keeps background sessions running with no terminal attached. See the block under the
  differentiator table. **"Dynamic workflow" there means model-AUTHORED, not mutable mid-run.**
- But a scope agent CAN return a schema-validated work list that the script fans out over (the
  shipped `deep-research` workflow bounds it `minItems:3, maxItems:6`). So **"the work list is not
  known up front" IS covered** and must not be claimed.

What is left uncovered is narrow and specific: **a model emitting a work-unit specification that
the ENGINE executes, durably, across a crash, outside a session.** That is the only form of
decomposition that composes with differentiators 1 to 4 rather than duplicating shipped work.

**The gating conclusion below is therefore UNDER REVIEW, not settled.** It was reached from the
false premise. The narrower uncovered capability may or may not justify a milestone, and that is a
scope decision rather than a fact.

**Decided with the user, 2026-08-21: M6 EXISTS and it is NOT gated on C1/C2.** The reason the
gate was dropped is that it was cross-axis. C1 and C2 ask whether a STRUCTURED GRAPH beats FREE
PROSE at producing a plan. The capability that survived the source read - and the only reason to
build M6 at all - is whether a MODEL-EMITTED plan can be EXECUTED durably, across a crash, outside
a session. No arm of either checkpoint measures that. A gate on the wrong axis reads as prudence
and survives review, which is exactly why it lasted.

So C1 and C2 are kept and RE-PURPOSED: they inform M6's SHAPE - how large a node should be, whether
briefs and a cost model earn their cost - and they no longer decide whether M6 is built.

**M6 does not wait on them** (decided 2026-08-21). It starts with the parts that do not depend on
node granularity - the insertion mechanism, which is undesigned and is its first real task, the
Plan schema, the goal on the run, the `RunLimits` bounds, and the three prerequisites it shares
with the substrate - and folds C1/C2's answer in when it arrives. Their falsifiers stand for what
they actually measure:

- **C1 - validate the judge panel.** A human scores six blinded pairs cold. Every judge on the E1
  panel shares a model family with the planner it grades, which is the panel's largest validity
  threat. Packet built: `RESEARCH/workflow/probes/e1_control_packet.md`. Falsifier pre-registered: disagreement on
  >= 2 of 6 discards the panel's other 24 verdicts rather than caveating them.
- **C2 - arm C/D.** Does a graph that CARRIES its instructions beat the prose control? Arm C adds
  briefs, output contracts and acceptance conditions; arm D adds the cost model on top. If arm C
  still loses, the structure is not earning its cost and sections 1 to 3 of the decomposition
  design are not worth building.
  **C2's first task, the source read, is DONE** (2026-08-21) and its result is above: the premise
  the gating rested on did not survive. What that means for whether M6 exists at all is the open
  scope decision, not a further measurement.

## Milestones

| M  | name                 | what exists at the end                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | exit criterion (a test, not a feeling)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
|----|----------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| M3 | the three properties | **Tasks 19-27**, not the six mechanisms this row used to list. Built: the `Sandbox` port and its `none` adapter; the token cap per row; the NODE deadline at the token cap's own turn seam recording `error.type = deadline`; `stage`/`apply`, the intent written before the effect and the effect recorded in two phases; `approve` that exits and resumes, with its deadline owner; cancel verified by cgroup empty; the notification sink; and the retry path (24), CLOSED 2026-08-22 under a verb NO plan names - automatic re-dispatch of a transient CODE-node failure (`Coordinator._auto_retries`, capped by `policy.max_attempts`) plus the operator verb `agentdag run retry RUN_ID NODE_ID`. Do not go looking for `resume --from`: it was declined with its reason, and both mechanisms are `DECISIONS.md` items 11 and 13. Open (served-dispatch collision 25 is CLOSED, agentdag `1934593` - the index is keyed by (node id, key), with a replay test, and both forms mutation-checked): the carried Minors (26), 26a's failing-branch-under-concurrency test, the attended run and PR (27), the `bmk-tool-env` resource decision, and a token-accounting defect found 2026-08-28 - an interrupt landing mid-request drops that request from `ResultMessage.usage`, so `record.tokens.in` under-reports (24 of 24 residuals equal the last request's context; two real run-store handovers short by 32,060 and 34,528 tokens, 13.7 and 12.9 percent)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | the section-9 rows THIS MILESTONE OWNS pass with their positive controls (see the mid plan's assignment; THREE section-9 rows are unreachable under the current cuts and are not M3's - mcp round-trip, resource overlap, unreviewed knowledge. This row said FOUR until 2026-08-27 and never listed them; the enumeration written that day finds three cut-blocked plus one CONDITIONAL on the open `bmk-tool-env` decision, so four holds only if the conditional row is counted and the original figure cannot be verified either way) |
| M5 | first real run       | ONE COMPLEX TASK, run unattended - never a repository sweep. It must carry a real irreversible effect, or half the criterion cannot be tested. **Sequenced AFTER M6 and decided 2026-08-21 to be its demonstration**: the graph is MODEL-emitted, not hand-authored, because a hand-authored graph demonstrates the substrate on a new task shape and says nothing about the thesis                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | see "What done means" below                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| C1 | validate the panel   | six human-scored pairs against the pre-registered falsifier                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | agreement on >= 5 of 6, or the panel's verdicts are discarded                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| C2 | arm C/D              | collected in full (arm C 14/14, arm D 13/14); judged only after C1 passes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | informs M6's node granularity and whether briefs and a cost model earn their cost - it no longer decides whether M6 is built                                                                                                                                                                                                                                                                                                                                                                                                              |
| M6 | decomposition        | **UNGATED as of 2026-08-21.** The Plan schema (spec + brief + output contract + acceptance), brief persistence and normalisation, the goal on the run, the re-plan loop as coordinator code, the insertion mechanism (DESIGNED AWAY 2026-08-28: `RESEARCH/workflow/design/2026-08-28-planning-loop-design.md`, six user decisions - recursive plans, `holds_while` premises, an op registry as the dispatch seam, a required judge where mechanical cannot move, steer-by-evidence, cwd under the project tree; a structure change is a sub-planner re-planning its own subtree, so nothing is ever inserted into a running graph and the three dead seam designs stay dead), plan approval by threshold, and the missing `RunLimits` bounds. Three prerequisites it shares with the substrate and must not inherit broken: the served-dispatch key collision (M3 Task 25), the operator-environment decision with its journal-key change (its source list SETTLED 2026-08-28 as `user`+`project`+`local` - `local` carries the memory index, `user` the skills and hooks, so no source is droppable; the cost lever is the node home's plugin set, risk 3), and the granularity floor, RE-DERIVED 2026-08-28 and now an OPEN TODO rather than a number. The user decided the denominator drops re-sent context (`overhead_fraction = startup / (startup + new content + output)`), which makes `f = 0.10` unreachable BY CONSTRUCTION: a node's fraction cannot fall below `startup / ceiling` (0.26 shipped, 0.473 plain operator env, 0.562 preset), and 132 of 132 measured nodes post 0.629 to 0.966, above the design's own 0.5 alarm. The replacement threshold is NOT set, because every candidate is a function of `g`, new tokens per turn - 292 MEASURED on synthetic tasks, only ~2,972 INFERRED for real work under a linear assumption from four dispatches. Settle it once a real run supplies `g`. Evidence and both candidate floors: `RESEARCH/workflow/design/probes/new-token-denominator.md` | a wrong-by-construction plan re-plans, converges, and REPLAYS to the same plans                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

## Cuts

Everything here was in a previous plan and is now cut, because it is not a differentiator and
nothing is blocked on it:

- **The Codex executor arm (old M4).** A second executor proves portability nobody has asked for.
  **What the arm was also buying is kept**: it was the only EMPIRICAL test that the executor port
  is not shaped around one vendor, and this project has already had a port leak its domain into
  its contract once (`GatePort.run` taking a worktree and returning an exit code, which no
  non-software adapter could serve). So M3 keeps a cheap conformance check - a fake non-SDK
  adapter satisfying the executor port in tests, or at minimum reading the port signature and
  asking what a second vendor would pass for each parameter. It is a weaker test than a real
  adapter, and it is bought at a small fraction of the cost. Port leaks are cheapest to fix
  before there are callers.
- **The MCP north face / server surface.** A CLI over the run dir is enough for one user.
- **Seven of the eight deferred items**: L1 the MCP north face, L2 resources beyond one lock, L3
  knowledge grants pending the knowledge-index project, L5 graphs A2 and C, L6 the drift review loop, L7 an `acp:`
  executor kind, L8 the memory-store dream as graph D. **L4 (planner plus graph B) is NOT cut: it
  is SUPERSEDED by M6.** Naming the tail by count rather than by item is what obscured that, so it
  is enumerated here. This bullet used to end "and gated on C1 and C2 with everything else", which
  contradicted this page's own M6 row above and `DECISIONS.md` item 1, where the gate was dropped
  as CROSS-AXIS and which governs. The gating clause is removed; the supersession stands.
  Corrected 2026-08-27.
- **`knowledge` grants as a mechanism for a node to know anything.** The 2026-08-20 environment
  decision makes them an optimisation for large retrievals rather than the mechanism, so they no
  longer block anything.

Falsifier for the whole cut list, stated so it is not re-argued: **if the differentiators ship and
nothing uses them, that answers the deferred tail without anyone having to argue about it.**

## Risks that could still sink it, each with its cheap test

1. **Arm C still loses to prose (C2).** Then the STRUCTURE is not earning its cost, and M6's plan
   entries shrink toward the minimum a durable engine needs to execute them. It no longer cuts M6:
   that gate was dropped on 2026-08-21 as cross-axis (see "The thesis"). Test: C2, collected.
2. **The panel is measuring format, not quality.** Its `executability` lens sits at EXACTLY 2.00
   with zero variance across all 14 tasks, which is a floor produced by a constant defect rather
   than a distribution produced by the tasks. Test: C1.
3. **The environment decision changes every cost number. MEASURED 2026-08-27, and what it found
   was not what this risk was watching for.** The risk said reversing `setting_sources=[]` moves
   per-node startup from ~26,000 tokens toward ~38,790 plus, with `min_node_tokens` scaling
   linearly, and asked for ONE measurement. That is `RESEARCH/workflow/design/probes/prefix-order.md`, two runs.
   The token growth is real (26,854 to 47,288 on the sonnet row, this machine's cascade). Two
   things it found that the risk did not anticipate:
   - **The cache key includes the MODEL.** A prefix warmed on sonnet gave haiku and opus nothing.
     A third run removed the confound that the CLI renders a different prompt per row: on a prompt
     whose bytes are entirely ours, opus renders 23,556 tokens and fable 23,557, and fable read
     ZERO right after opus warmed it while opus re-read its own at 100 percent. Since the tier
     table maps kinds to roles to rows BY DESIGN, a run pays one full startup PER ROW IT USES, and
     the cascade multiplies each. It is a structural floor, not a discipline problem, and no plan
     or design on this page accounts for it. It does NOT make cheap rows expensive: a cold start is
     one-time while a rate difference is per token of work, so a cheap row repays its start after
     10k to 80k tokens of work depending on the pair (mid plan has the table).
   - **The cascade caches, but not dependably.** Two back-to-back dispatches with identical inputs
     gave 49.7 percent in one run and 100 percent in another; in the first, the prompt grew by 120
     tokens between them and invalidated everything behind the divergence. What grew is unknown and
     is the cheapest open question here.
   Figures are token counts on purpose: the tier table carries a `cache_read` rate and NO
   cache-write rate, and writes land in the `ephemeral_1h` tier, so a price quoted here would hide
   an assumed multiplier inside a number. Magnitudes are this machine's cascade on three rows, one
   day; the DIRECTION generalises, the numbers are local.
   **This retires the risk and OPENS A DECISION**, because the 2026-08-20 environment decision was
   taken on a cost argument that measurement has now moved. It is recorded in the mid plan under
   "Decided, not yet owned by any milestone" and it is the user's to re-take, not this page's to
   reverse.
   - **The `user` source, not the CLAUDE.md walk-up, is what the environment decision actually
     costs. MEASURED 2026-08-28**, `RESEARCH/workflow/design/probes/cascade-worktree.md`
     arms N and O: one trivial one-turn dispatch sent 109,752 input tokens with
     `["user", "project", "local"]` against 42,276 with `["project", "local"]` on an identical
     tree - 67,476 more, and the captured bodies attribute it to TOOLS (171 definitions, 268,683
     chars, against 25 and 106,331). Every `CLAUDE.md` and `CLAUDE.local.md` in that PROJECT tree
     loaded in BOTH arms. **Dropping `user` is NOT the answer** (the user, 2026-08-28, correcting
     a "third option" this bullet briefly carried): the arm without it also lost the entire skill
     catalogue (0 `bitranox:` mentions against 94) and the hooks, because plugins are enabled in
     user settings - so it strips the knowledge and the guards the environment decision exists
     to give a node. What the bodies actually attribute the cost to is narrower: 142 of N's 171
     tools are MCP tools from browser, IDE and desktop plugins (chrome-devtools twice, playwright,
     desktop-commander, pycharm, context7 twice, lighthouse), 157,303 of the 268,683 tool chars;
     in messages the bitranox skill listing adds ~3.5k chars while hook OUTPUT adds 17.4k. That
     17.4k splits SessionStart ~8,976 (`session-banner.py`, the `meta-using-bitranox-skills` body -
     earlier text here misnamed it the skill-router injection) against UserPromptSubmit ~7,655
     (per-prompt recall 7,599 plus the stamp). **The lifecycle split excludes only the first.** On
     2026-08-28 the user decided the UserPromptSubmit class STAYS for nodes, because recall is the
     read path requirement 2 item 3 already grants; the cache-breaking stamp is removed instead by
     `REMEMBER_PROMPT_STAMP=stable` on the node process. **Standing option, user-gated (user,
     2026-08-28): where agentdag needs it, the bitranox self-learning skills and hooks can
     THEMSELVES be changed - they are ours, so a hook's behaviour is a choice, not a fixed
     constraint. The trigger is agentdag NEEDING it (not a stricter last-resort bar), and each
     change is asked for first. The mid plan lists the four places this changes the option space,
     including whether Stop can ever load for a node.** Recall's 7,599 chars therefore ride on every
     dispatch and, keying on the brief, keep a varying block in the tail - the cost is accepted,
     the cache consequence is owed to component 8's probe arm. The lever is the
     NODE HOME's plugin set, not the source list: a node already runs under `node_dir/home/.claude`
     (`executor_claude.py:269`), so component 8 decides what `user` means for a node. Put the
     bitranox plugin in (skills, hooks, memory retrieval) and the MCP-heavy plugins out, and the
     ~56k tokens of schemas (derived: chars at N's 2.8 chars/token, not measured separately) go
     while knowledge, skills and guards stay. That number is this machine's plugin set, not a
     property of the harness.
4. **Real tasks do not decompose usefully, or do not arrive often enough to matter.** The E1
   corpus establishes the SHAPE of such work (11 of 14 real tasks) but no RATE, and E1 measured
   emissions rather than executions. Test: M5, on a task that genuinely needs judgement. If the
   first real run has no judgement in it, it was not the right task.
5. **The competitor is one commit from enforcing proof.** OpenClaw's contract field and the
   `missing_proof` diagnostic already exist; making `done` require `proof.status == passed` is a
   small change. If it lands, row 4 narrows from "a non-model decides pass/fail" to "and the
   decision is replayable" - still ours, but a thinner claim. Test: at each openclaw release,
   re-read the `done` transition in `store-core.ts` and the contract; a proof check appearing
   there is the trigger to re-word row 4.

## Decisions this plan assumes

**Decided 2026-08-21, and this page is the record:** M6 EXISTS and is ungated (the C1/C2 gate was
cross-axis); M5 follows M6 as its demonstration, with a model-emitted graph. Also: there is NO
run-level deadline mechanism and there never was - `deadline_ceiling_s` is a per-node clamp
(`min(spec.deadline_s, ceiling)`), design section 9 carries one per-node deadline row, and the
claim that a separate RUN deadline "kills the scope" and was "built on the M3 branch" is false
wherever it still appears. Whether a run-level wall-clock kill is wanted at all is an OPEN
question, owned by nobody, and it is not part of M3.

D1 slice = graph A at N=2, baseline first (user, 2026-08-17). **D2 adopt-vs-rebuild: REBUILD
stands** - its re-open trigger fired on the raw-line reading and the user ruled on 2026-08-20 that
the CODE-line reading governs, so the condition is not met; the machinery sits at 237 code lines
against a ~300 threshold. D5 run store at `/var/lib/agentdag/runs`. D7 the repo is
`projects/public/KI/agentdag`, bmk-managed, MIT, on `main`. And, new: **a node gets the operator's
full environment** (user, 2026-08-20), which is decided and NOT yet built. Sharpened 2026-08-28
after the cascade-worktree probe (`RESEARCH/workflow/design/probes/cascade-worktree.md`):
the mechanism is `setting_sources=["user", "project", "local"]`, all three required - `local` is
the only source that loads `CLAUDE.local.md` (the memory index at every level) and `user` is what
carries the plugins (skills, hooks, MCP tools). "We need the knowledge and tools" (user), so the
source list is closed; what is open is what a node's OWN home enables (M6 component 8), since 142
of the 171 tools the operator's home brings are MCP schemas for browser, IDE and desktop plugins a
node never calls.

## What "done" means for the whole build

The 2026-08-17 version of this criterion was unsatisfiable: it required a human interaction count
lower than M1's measured baseline of 1, while one approve is mandatory by design. A run cannot have
fewer than one interaction and still show you the push list. Replaced with a criterion that can
actually be met or missed:

**The first real run is ONE COMPLEX TASK, not a repository sweep.** Graph A stays what M1 to M3
proved the substrate on; it is not what the product is demonstrated with, because a run over twenty
repositories shows the substrate working and shows nothing about decomposition.

Selecting the task takes three clauses, and the third is the one that is easy to forget:

1. it genuinely needs DECOMPOSITION - the eleven-in-fourteen shape from E1, not a task one agent
   would do better in one context. Since 2026-08-21 M5 follows M6 and the graph is MODEL-emitted,
   so this clause selects a task the PLANNER must be able to decompose, not one a human will;
2. it needs JUDGEMENT somewhere, or the run tests scheduling rather than the thing being built;
3. it carries a REAL IRREVERSIBLE EFFECT, or the apply-once and crash-resume halves of the
   criterion below cannot be exercised at all.

Clause 3 rules out much of the corpus. Reading E1's eleven single complex tasks, five look like
they end in an effect (`docs-claims`, `ci-crossplat`, `feature-cap`, `data-consolidate`,
`ops-wedged`) and six look like they end in a report or a diagnosis. **That split is a JUDGEMENT
made from one-sentence task descriptions, not a property of the corpus** - a security review or a
plan audit could equally well be scoped to end in commits, which would move it across the line. Use
it as a starting shortlist, not as a filter that has already run: the question to ask of a
candidate is whether the task AS YOU WILL SCOPE IT ends in something irreversible.

For a FIRST unattended run prefer a commit-shaped one, with scratch clones still the only push
target - `data-consolidate` rewrites a knowledge store and `ops-wedged` touches a live host, and
neither blast radius belongs in a first run.

Such a task ran unattended with:

- **exactly one planned interaction** - the approve - and **zero unplanned ones**: no intervention
  the design did not schedule, counted honestly including every time the operator had to look;
- **the run killed mid-flight and resumed**, redoing no finished unit of work;
- **spend visible per row in tokens**, and no row over its cap;
- **no secret in the run store** (grep for the known token prefixes and find none);
- **every push behind the one approve payload**, and no effect applied twice across the kill.

If the approve payload did not carry enough for the user to answer without opening anything else,
the run failed this criterion even if every test above passed.
