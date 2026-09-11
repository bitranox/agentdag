# The clean one-checkpoint parity reading, 2026-09-10

Task 12's paid dry run (`2026-09-08-slopcodebench-arm-dry-run.md`) produced a comparison that was
VOID: the harness dispatched the coordinator twice, the first attempt's spend was never harvested
while its workspace edits stayed in the score, and the retry ran with a 33-character goal. The
three defects that caused it are fixed and pushed (`OPEN-WORK.md` ranks 71, 69, 06). This is the
reading taken afterwards.

Each entry is marked with how it was established: **[measured]** here, **[read]** from a record
this run wrote, or **[inferred]**.

## What ran

One checkpoint, `dynamic_config_service_api` checkpoint_1, from the one-checkpoint catalog
`~/agentdag-eval/slopcodebench/scb-problems-cumulative-cp1`. Launched detached at 17:36:00 CEST,
`END ... rc=0` at 18:15:31, `ARM COMPLETE`, `LAUNCHER_RC=0`; 39.5 minutes wall clock. **[measured]**

* Run directory: `~/agentdag-eval/slopcodebench/task12-parity/runs/PARITY-agentdag_20260910T173601`.
  Named `PARITY-*` so it can never be mixed with the void `TASK12-DRYRUN-*` ones. Outside every git
  work tree, because a run directory holds a credential copy per node.
* Launcher and config: `task12-parity/launch_detached.sh` (RETIRED 2026-09-11 to a shim, its
  original kept beside it; the wait it wrapped is now `scb_run_arm.py --wait-for-token`) and
  `run-parity.yaml`, both copies of the
  dry run's with the run-directory naming changed and the expected duration raised from 1800 s to
  3000 s. That number gates only the token-coverage refusal, and 1800 contradicted this host's own
  measurement: the void run's surviving attempt alone took 2,487 s. **[measured]**
* Conditions recorded per the pre-registration: token valid to 2026-09-11 00:57:15 (7.3 h ahead at
  launch), `/proc/loadavg` 5.42 at start and 10.77 at end. The box was busier at the end than the
  void run's 4.65. **[measured]**
* The arm ran the reinstalled clone at agentdag `3c01315` in image
  `slop-code:agentdag-3c01315...-python3.12`, confirmed by the container in `docker ps` during the
  run. **[measured]**

## Validity gate, applied before any tally

| Condition                                       | Verdict         | Evidence                                                                                              |
|-------------------------------------------------|-----------------|-------------------------------------------------------------------------------------------------------|
| 1 tests executed                                | pass            | `infrastructure_failure: false`, 47 collected, pytest exit 1 (failures, not a collection error)       |
| 2 auth-failure termination                      | pass            | `tokens_by_row {"opus": 528302}` and ZERO records carrying an error, in the run's own journal         |
| 3 turn bound with work in flight                | pass, see below | no node's continuation chain was exhausted: `n-0002` handed over three times and then finished `done` |
| 4 token expired during the problem              | pass            | expiry 00:57:15, run ended 18:15:31                                                                   |
| 5 empty `diff.json` with a completed work node  | pass            | `diff.json` carries 83 file diffs                                                                     |
| 6 coordinator never reported a terminal outcome | pass            | `had_error: false`, no error message; the addendum's condition                                        |

All six pass, so this is a valid reading. **[read]**

## The readings script voids EVERY coordinator checkpoint, and that blocks Task 13

`scripts/scb_arm_report.py` reports this checkpoint `VOID (condition 3)`. It is an INSTRUMENT
ARTIFACT, not a property of the run, and it is the most important thing this run found.

`bound_hit` is `orphaned_tasks > 0 or max_turns_results > 0 or steps_missing_from_stream > 0`.
On this checkpoint the first two are 0 and the third is 173, which is the whole step count.
`steps_missing_from_stream` means "harness steps beyond the messages left in the agent's CLI
stream". A coordinator has no such stream: its steps are counted from its NODES' transcripts, so
the difference equals the entire step count on every coordinator run, always. **[measured]**

Two controls, answering differently, which is what makes it an artifact rather than a finding:

| Arm                                                        | Verdict  | `result_events`  | `new tokens` | `peak prompt` |
|------------------------------------------------------------|----------|------------------|--------------|---------------|
| this coordinator run                                       | VOID (3) | 0                | 0            | 0             |
| the earlier coordinator run (`TASK12-DRYRUN-*`)            | VOID (3) | 0                | 0            | 0             |
| a control-arm run (`opus-5_whole-spec_high_20260905T0516`) | none     | 1 per checkpoint | 70,130       | 94,987        |

So the detector works exactly where it was designed to and fails on the coordinator's shape.
**[measured]**

The same gap costs the pair its headline quantity: `new tokens` and `peak prompt` also read 0 for
a coordinator, and the pre-registration's measured quantity is the score curve against CUMULATIVE
NEW TOKENS. Read with this script as it stood, a counted coordinator arm would void every
checkpoint AND report zero for the axis the curve is plotted on. **[inferred]**

**FIXED the same day** (rank 07, closed). The cause under the cause: a node's `transcript.jsonl`
is the Agent SDK's own message objects, not the CLI's event stream, and the control's fold
excludes a cumulative result by `type == "result"` while the SDK writes `"ResultMessage"` - so a
whole dispatch's total was being fed into a PER-REQUEST peak. The first attempt at aggregating
them reported a peak of 1,874,489 against a 200,000 context window, which is what indicted the
fold rather than the data. The reading now takes new tokens from each dispatch's cumulative
`ResultMessage` and the peak from per-request `AssistantMessage` usage, and judges void condition
3 for a coordinator on an EXHAUSTED continuation chain, per the pre-registration's own carve-out.
Verified two ways: summing the dispatches reproduces the harness's independently recorded
`input + cache_write` to the token, and a control-arm run's rows and verdicts are byte-identical
to before the change. **[measured]**

The figures below therefore come from each run's own `inference_result.json`, whose coordinator
totals equal the journal's `tokens_by_row` exactly (244 + 366,467 + 161,591 = 528,302), not from
the report script's zeroed columns. **[measured]**

## The reading

Both arms on the same checkpoint, same prompt, same catalog, same model and effort, one attempt
each. Tokens are given in two units because the two documents use both: `charged` is
input + cache-write + output, the unit the dry run's table used and the one agentdag charges;
`new` is input + cache-write, the pre-registration's unit, which excludes output.

|                            | control          | coordinator, this run | ratio |
|----------------------------|------------------|-----------------------|-------|
| strict                     | 1.000 (47 of 47) | 0.957 (45 of 47)      | worse |
| core                       | 1.000            | 1.000                 | tie   |
| cost USD                   | 2.32             | 14.45                 | 6.2x  |
| charged tokens             | 116,901          | 528,302               | 4.5x  |
| new tokens                 | 70,130           | 366,711               | 5.2x  |
| cache read                 | 918,938          | 6,100,560             | 6.6x  |
| steps                      | 14               | 173                   | 12.4x |
| inference window           | 526 s            | 2,331 s               | 4.4x  |
| peak single-request prompt | 94,987           | 105,211               | 1.1x  |

**[measured]**

The last row is the one the thesis cares about, and it is the flattest. Decomposition is supposed
to relieve context pressure, and on this checkpoint the coordinator's busiest single node request
held about what the control's busiest request held: 105,211 against 94,987, both around half of a
200,000 window. So the coordinator paid 4.5x the tokens without ever being under the pressure the
split exists to relieve. That is consistent with this being a checkpoint the control finds easy,
and it is the reading to repeat where the control's occupancy actually approaches the window.
**[measured]**

For reference, the VOID dry run recorded 0.872, 9.29 USD, 393,329 charged and 121 steps. The clean
run cost MORE than that, which is consistent rather than surprising: the void figure was the
retry's spend alone and excluded a dead thirteen-minute attempt. **[read]**

## What the coordinator did, and where the cost went

Seven dispatches: the root planner, one plan accepted on the first try, then `n-0001` done,
`n-0002`, and `n-0003` done. `n-0002` returned `needs_continuation` three times before finishing
`done` on its fourth link. **[read]**

That chain is where the spend is. It is also exactly what the pre-registration recorded rather than
removed: "a control process at 100 turns stops; an agentdag node at 100 turns hands over to a
continuation. That is agentdag's structure and part of what is measured." The control's 14 steps
are one process that stopped; the coordinator's 173 are a planner plus three work nodes, one of
which ran four links. **[read]**

## What this does NOT say

* **The control solves this checkpoint perfectly, so the checkpoint cannot show decomposition
  helping.** At a ceiling the control already reaches, the best a coordinator can do is tie. This
  reading measures the OVERHEAD of the coordinator on a task one agent handles comfortably; it is
  not evidence about the thesis, which is about tasks one agent's context cannot hold. Read it as
  a cost reading with a score attached, not as a verdict on decomposition. **[inferred]**
* One run per arm measures SEPARATION, not causation. The gaps here are large enough that noise is
  an unlikely explanation for the cost, but nothing here estimates variance. **[inferred]**
* One checkpoint from a one-checkpoint catalog. These readings can never join a Task 13 tally, by
  the same rule that governed the dry run.

## Still owed

* Task 13 itself, the counted arm. The readings script's two coordinator gaps were fixed the same
  day; what remains is choosing checkpoints the control does NOT saturate, since this one cannot
  show decomposition helping at all.
