# Probe: agentdag on `agentswarm`'s `defects` case, round 1

Run under `agentswarm/docs/evaluation-protocol.md` and reported in the form its
`docs/agentdag-comparison-handover.md` specifies, so the numbers can sit in one table beside
`agentswarm` round 10.

**Everything above the divider was written before the run started and is not edited afterwards.**

## Pre-registration

### What is being compared

agentdag's `plan-goal` workflow against a single Claude agent in one context, on the same case,
the same seeded directory, the same ceiling and the same deadline.

### Held fixed

| Setting            | Value                                                              |
|--------------------|--------------------------------------------------------------------|
| case               | `defects`, agentswarm commit `5a1c9ea`, `max_score` 20             |
| visible gate       | `pytest tests/ -q` (passes on the seed - it is a bar, not a score) |
| new-token ceiling  | 600,000, matching agentswarm round 10                              |
| wall-clock ceiling | 2400 s                                                             |
| worker model tier  | sonnet in both arms                                                |
| stop condition     | the ceiling, not the system judging itself finished                |
| runs per arm       | 1                                                                  |

### Token accounting, and the deviation that matters

The protocol charges `input_tokens + cache_creation_input_tokens`, keyed by message id, and
excludes cache reads and output. **agentdag's own accounting does not match this**, and the
divergence is not small: `outcome_from_usage` charges `input_total + output`, where
`input_total` includes `cache_read_input_tokens`. Measured on one real work node from the
2026-09-02 `lib_layered_config` run: 66,665 new tokens against 1,132,340 charged, a ratio of
**17.0x**.

That has two consequences and both are pre-registered here rather than discovered later:

1. **The ceiling is enforced externally, in the protocol's units.** agentdag's own per-node cap
   and run-wide row ceiling are computed from the inflated figure (`tokens_by_row` sums
   `charged_tokens`), so setting them to 600,000 would stop the run at roughly one seventeenth
   of the intended budget. Both arms are therefore capped by an external count using the
   protocol's definition.
2. **agentdag's internal budget mechanism is measuring conversation length, not work.** That is
   a finding about agentdag, reported below rather than corrected here, because correcting it
   mid-comparison would change two things at once.

### Deviations from the protocol, stated in advance

* **The workspace is copied into agentdag's run worktree by the run itself.** `plan-goal` mints
  its own empty `wt/root` and takes only a goal string, so it cannot be handed a pre-seeded
  directory the way the interface describes. The goal's first instruction is to copy the seeded
  material in. The directory scored is that worktree.
* **One run per arm.** The protocol is explicit that this cannot separate two systems differing
  by less than the run-to-run spread of either. Any difference below that is reported as
  indicative, never as a result.

### What each outcome band will mean

Pre-registered before any number is visible, including the band that says the case was
mis-calibrated for this comparison.

| Band                                              | Reading                                                                                                                   |
|---------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| agentdag scores 0 and the single agent scores > 0 | the coordinator's overhead is not repaid at this budget on this case                                                      |
| both score 0                                      | **MIS-CALIBRATED** - 600,000 new tokens is below this case's floor for both, and the run says nothing about either system |
| both land in 5..17                                | the intended discriminating band; compare `score_per_mtok` and quality, and read cost with it                             |
| both score at or near 20                          | **MIS-CALIBRATED** - saturated, as the protocol warns `defects` does at a generous budget                                 |
| agentdag scores higher at equal or lower cost     | the strongest available evidence for the coordinator, still one run, still indicative                                     |
| either arm fails to produce an importable tree    | read `hidden suite errors` separately from `failed` - all-errored and all-failed are opposite findings                    |

### Coordination metrics

Rendered as a dash for both arms, never as a zero. agentdag runs a DAG in a fixed order and the
single agent has no peers, so redundancy ratio, claim denials, write collisions and plan sharing
are undefined for both. A system scores perfectly on every one of them by doing nothing.

---

## Results

Written after the run. Nothing above this divider was edited.

Run 2026-09-02 22:27 to 2026-09-03 00:13 CEST. agentdag at `7a886d8`, agentswarm cases at
`5a1c9ea`. Raw artifacts in `docs/evidence/2026-09-02-defects-round1/`.

### The table

```
                              agentswarm r10   agentdag        single agent    single agent
                              (from handover)                  (to ceiling)    (one turn)
case                          defects          defects         defects         defects
new-token ceiling             600,000          600,000         600,000         600,000
new tokens actually spent     658,815          613,845         600,820         100,567
wall clock (s)                330.3            1,717.4         768.8           99.8
stop reason                   -                ceiling         ceiling         SELF-JUDGED

hidden score                  8/20             20/20           19/20           0/20
hidden suite errors           0                0               0               0
score per Mtok                12.14            32.58           31.62           0.00
score per minute              1.45             0.70            1.48            0.00

quality, weighted 0-10        (seed 5.36)      7.41            7.30            not scored
  seed read in the same run                    4.41            5.09
  delta vs that seed read                      +3.00           +2.21

visible gate                  -                82 passed       83 passed       48 passed
test functions left behind    48 (seeded 48)   82              83              48
changed lines in pipelib/     -                114             100             0

-- not comparable, dash for every arm --
redundancy ratio              0.0              -               -               -
claim denials                 -                -               -               -
coordination ratio            2.1              -               -               -
```

### The verdict the pre-registration forces

**MIS-CALIBRATED.** Both arms landed at or near 20, which is the band written above the divider as
saturation. `defects` at a 600,000-token ceiling discriminated `agentswarm` round 10 at 8/20; it
does not discriminate these two arms, and the run therefore does not establish that either system
is better than the other.

That verdict is the pre-registered one and it stands regardless of how the numbers read, which is
the whole reason for writing the bands first. What follows is reported under it, not against it.

### What the run does establish

**On correctness, the two arms are not separated.** 20/20 against 19/20 is one hidden test, at one
run per arm, on a case that saturates. The protocol says in as many words that two systems
differing by less than the run-to-run spread of either have not been separated, and 20/20 has no
headroom to spread into at all.

**On cost per unit of correctness, they are not separated either.** 32.58 against 31.62 score per
Mtok is a 3 percent difference on a single run.

**On latency, agentdag is materially worse, and this is the one axis that separates them.** 0.70
against 1.48 score per minute: 2.23x the wall clock for 1.02x the tokens. The coordinator's
overhead here is TIME, not spend. Two contributors are visible in the journal and neither is a
mystery: the shipped `parallel = 2` default ran a plan whose four auditor entries were explicitly
independent two at a time, and two of those nodes exhausted the 25-turn ceiling and were
re-dispatched as continuations, each continuation paying a fresh startup.

**Quality does not separate them, and the instrument says so itself.** The two scoring runs read
the SAME untouched seed as 4.41 and 5.09 - a spread of 0.68 on identical input, six times the 0.11
between the arms. Report the deltas (+3.00, +2.21) with that spread attached or not at all.

**The single agent's own stopping rule is the sharpest result in the table, and it is not about
agentdag.** Left to decide for itself, it stopped after one turn at 17 percent of the budget having
changed NOTHING - 0/20, zero lines - with the visible suite green at the seeded 48. The case is
built so that suite passes on defective code, so a green bar is not evidence of work, and the agent
took it as such. Re-prompted while budget remained, the same agent reached 19/20. The difference
between 0/20 and 19/20 was entirely the stopping rule.

That is worth stating plainly because it cuts toward agentdag's thesis and was measured on the
control arm: an unattended run needs something other than the system's own judgement to decide it
is finished. It is also why the protocol demands the ceiling as the stop condition, and why the
one-turn round is reported here rather than discarded.

### Two agentdag defects this run found, both fixed before it, one during

Both were found by the FAILED `lib_layered_config` run earlier the same day and fixed in `7a886d8`:

* **The turn ceiling was a fatal, nameless error.** A dispatch that used every allowed turn came
  back `is_error` with subtype `error_max_turns` and was recorded FAILED with a TRANSIENT
  `EXECUTOR_ERROR`, so the retry path re-dispatched into the identical wall. It now ends
  `NEEDS_CONTINUATION` with `turns_exhausted` typed and the worktree kept, exactly as the context
  ceiling has always behaved. **This run depended on it**: `n-0002` and `n-0003` both hit
  `turns=26`, both continued, and on the morning's code this run would have collapsed the way the
  earlier one did.
* **The policy offered an executor nothing wires.** The `codex` row shipped `available: true`
  against `mcp:codex/codex`, left behind when the Codex arm was cut; three nodes resolved there and
  died at dispatch, each after a planner had been paid to name them. `wire_kernel` now refuses such
  a table before a run starts.

### The accounting divergence, which is a finding and not a footnote

agentdag charges `input_total + output`, where `input_total` includes `cache_read_input_tokens`.
Measured on one real node: **1,132,340 charged against 66,665 new tokens, 17.0x**. Across this run:
11,333,091 cache-read against 613,845 new. Since `tokens_by_row` sums exactly that figure, every
agentdag token ceiling - the per-node cap and the run-wide row ceiling - is denominated in a unit
that grows with conversation length rather than with work done. A node budget of 400,000 is roughly
23,500 new tokens of real work.

This is why the ceiling in this run was applied externally. It is also a live defect: agentdag
cannot currently be given a budget in the units anyone reasons about.

### A limitation this run did NOT resolve

`agentswarm` round 10 scored 8/20 where both of these arms scored 19-20 at the same ceiling on the
same case. That gap is unexplained. Candidates not separated here: a different model tier, a
different apparatus, and the re-prompting loop both of these arms ran under. It is stated rather
than explained away, and it is a reason to treat the cross-system column as context rather than as
a measured comparison.
