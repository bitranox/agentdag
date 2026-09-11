# Proving the budget handover end to end before spending on it

Written 2026-09-11. The mechanism behind addendum 4 of
`docs/probes/2026-09-05-slopcodebench-corrected-pair.md`, run on a real coordinator before any of
it reached the counted arm.

Raw artifacts, outside every git work tree because an agentdag run directory holds a credential
copy per node: `~/agentdag-eval/handover-proof/` - `proof-tier-policy.yaml`, `proof{,2,3,4,5,6}.log`,
`runs/`, `workspace{,2,...,6}/`.

## What was being proved, and why a unit test could not

Three bounds can stop a coordinator node. Before this work only one of them preserved the work:

| bound                          | fires when                     | work preserved                        |
|--------------------------------|--------------------------------|---------------------------------------|
| `handover_at_tokens` (context) | the window is full             | yes, hands over                       |
| `tokens_per_row` (run total)   | `charged + node_cap` > ceiling | no, node refused                      |
| `token_cap` (per node)         | the node's allowance is spent  | no, `artefact_refs` emptied by design |

The unit tests all supply their own outcome object, so none of them can see what a real dispatch
does with the ladder around it. Both defects below were found by running the thing.

## Method

`agentdag run start plan-goal` against a purpose-built tier policy, on `haiku` rather than `opus`:
the handover path is agentdag code reading usage numbers and is model-independent, and the queued
paid run needs the opus quota. Six runs, under ~250k haiku tokens in total.

The ceiling was calibrated from the counted run's own dispatches (planner 37k-74k, work node
91k-140k) and then re-calibrated three times against what each proof run actually spent. The final
figure, 10,000, was chosen because every dispatch observed across the six runs spent more than
11,000, so the first turn must cross it - a deterministic trigger rather than a lucky window.

## Result 1: the clamp works, and the refusal boundary holds

Run `20260911T133428Z-97819a`, ceiling 30,000. The planner charged 32,006 - the one-turn overshoot
the clamp accepts - and every later dispatch was refused with the row genuinely spent. An earlier
run recorded the clamp itself: a node admitted under 17,652 (the headroom) rather than its declared
cap, which it then stayed under.

## Result 2: a spent cap now hands the work over

Run `20260911T133729Z-530867`, ceiling 10,000, BEFORE the grace change:

    p_root cont=0 charged={haiku: 14194} status=needs_continuation cap_hit=True
           refs=[.../workspace5] err=None
    p_root cont=1 charged={}            status=failed              err=budget_exceeded

The worktree survived, a successor was dispatched, and it was correctly refused once the row had
nothing left. Under the previous code that first record was `failed` with `refs=[]`.

## Result 3: preserving the tree is not preserving the reasoning

That same run wrote NO `handover.json`. The cap path interrupted on the crossing turn, so the node
was stopped at the moment of asking - decision 14's finding met again at a different bound. Run
`20260911T144137Z-d3e134`, same ceiling, after making the budget a third arming reason:

    p_root cont=0 charged={haiku: 35440} status=needs_continuation cap_hit=True
           grace_used=2 expired=False refs=[.../workspace6, nodes/p_root/7acd23b0/plan.json]
    handover document: nodes/p_root/7acd23b0/handover.json

`grace_used=2` with `expired=False` means the node was asked, used two of its three grace requests
and stopped of its own accord. Its handover names what it had done, what was left, the next step,
and a clean write set. The grace cost about 21k tokens here on a deliberately tiny ceiling; it is a
fixed three requests, not a multiple, so the proportion falls away at the arm's real ceiling.

## Result 4: the refusal was being laundered

Run `20260911T133428Z-97819a` again. Once the row was spent, each refused planner dispatch was
recorded by the re-plan ladder as "the planner node wrote no plan.json", so it spent every
`max_replans` attempt re-dispatching into a bound that cannot move, then suspended asking whether to
grant more RE-PLANS - the one thing that could not help. Three dead dispatches per exhausted
checkpoint, which at opus prices is roughly 90k tokens each time. Fixed in `48d0c6a`.

## What this does not show

No arm of this proof ran on `opus`, on the SlopCodeBench harness, or inside its container. It
establishes the mechanism, not the arm. It also says nothing about whether the changes improve the
coordinator's SCORE: they remove a truncation that made the score a floor, and the pre-registered
falsifier in addendum 4 is what decides whether they bound anything on the real problem.
