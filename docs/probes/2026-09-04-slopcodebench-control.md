# Probe: SlopCodeBench, control-only calibration arm

`OPEN-WORK.md` rank 05 requires a CONTROL-ONLY calibration arm before any coordinator arm, with
what counts as "the control fails" written down first. This is that arm. No agentdag coordinator
runs here and none is configured; the only system under test is one Claude Code agent.

Two rounds have already been voided by a control that saturated the case
(`2026-09-03-spec-round1.md`, `2026-09-03-spec-round2.md`). The purpose of running the control
ALONE, first, is that saturation is a property of the case and can be found for the price of one
arm instead of three.

**Everything above the divider was written before the run started and is not edited afterwards.**

## Pre-registration

### Why this benchmark

Chosen by the user on 2026-09-04 from the three candidates rank 05 left live. The published
single-agent numbers are the "control fails, on evidence" that rank 05 asks for, and unlike the
two voided rounds that evidence exists BEFORE we spend anything.

| Reference                 | Harness     | Model    | Strict solve | Core solve | Source     |
|---------------------------|-------------|----------|--------------|------------|------------|
| SlopCodeBench leaderboard | Claude Code | Opus 4.6 | 9.69%        | 65.31%     | Snorkel AI |
| SlopCodeBench leaderboard | Claude Code | Opus 4.7 | 8.16%        | 64.29%     | Snorkel AI |
| 3-problem subset          | Claude Code | Opus 5   | 24% (4/17)   | not given  | humanlayer |

The leaderboard figures are on OUR harness family and are far from saturated. The humanlayer
figure is the closest published estimate for the model this arm actually runs, and it is the
reason the subset below is the one it is.

### What is measured

Per checkpoint, from the harness's own metrics, whose formulas were read at source before this was
written (`src/slop_code/metrics/checkpoint/extractors.py:209-218`):

| Quantity             | Definition                                                             |
|----------------------|------------------------------------------------------------------------|
| `strict_pass_rate`   | all passed / all tests, INCLUDING prior checkpoints' regression tests  |
| `core_pass_rate`     | Core passed / Core total, the explicitly specified behaviours only     |
| `isolated_pass_rate` | this checkpoint's own tests only, regression tests removed             |
| `cost`, `duration`   | per checkpoint, as the harness records them                            |
| tokens               | input, output, cache_read, cache_write, reasoning, recorded separately |

These are DENSE test-level rates, not a binary solve. That is the resolution the score curve at a
shared ceiling needs, and it is finer than the published leaderboard quantity.

**Primary readings:** mean `strict_pass_rate` and mean `core_pass_rate` over all 17 checkpoints.

**Secondary reading, for comparison with the published figure only:** the COUNT of checkpoints
whose `strict_pass_rate` is exactly 1.0, as a fraction of 17. The humanlayer 4/17 is a binary
checkpoint-level solve, so only this second form may be compared to it. The primary readings may
not.

### Held fixed

| Setting          | Value                                                                              |
|------------------|------------------------------------------------------------------------------------|
| problems         | `circuit_eval` (8), `database_migration` (5), `dynamic_config_service_api` (4)     |
| checkpoints      | 17, the full sequence of each problem, in order                                    |
| problem catalog  | SCBench v1.0, commit `4d38d300059667d57e43c31969bc455f5c338b52`                    |
| harness          | `SprocketLab/slop-code-bench`, `uv sync` on Python 3.12                            |
| agent            | `claude_code`, `permission_mode: bypassPermissions`, `step_limit` 100              |
| Claude Code CLI  | pinned to `2.1.260` in the container, matching this host's binary                  |
| model            | `opus-5` (`claude-opus-5`)                                                         |
| prompt           | `just-solve`, the benchmark's own, unmodified                                      |
| thinking         | `high`                                                                             |
| environment      | `docker-python3.12-uv`                                                             |
| auth             | `CLAUDE_CODE_OAUTH_TOKEN`, Max subscription, tier `default_claude_max_20x`         |
| `pass_policy`    | `any-case`, the only value that does NOT early-stop at the first failed checkpoint |
| runs per problem | 1                                                                                  |

The subset is the humanlayer three and not a sample I chose. Choosing the problems myself is
choosing the answer, which is the reason rank 05 rejected a synthetic task in the first place.

One correction to the source of that subset, checked against the catalog before this was written:
humanlayer describe it as spanning easy, medium and hard. In catalog v1.0 all three problems are
rated **Medium**. The subset is therefore NOT a difficulty spread, and nothing here may claim it
is. It remains the right subset for calibration because it is the same problems, model and prompt
as the only published Opus 5 figure, which is the only property this arm needs from it.

### Why `pass_policy` is `any-case` and not the default

Read at source before the run (`src/slop_code/agent_runner/runner.py:689-705`): the runner
early-stops the whole problem the moment a checkpoint fails its pass policy, for every value
EXCEPT `any-case`. On the default `any`, a problem whose second checkpoint fails yields two
readings instead of five, and a score curve that stops at the first failure is not a curve.

This does not touch the scoring. `strict_pass_rate`, `core_pass_rate` and `isolated_pass_rate`
are computed from test counts, not from the pass policy, so `S` and `C` mean the same thing under
either value. What changes is only how many checkpoints get attempted.

A caveat this creates for the calibration band: the published 4 of 17 does not state its pass
policy. If that run early-stopped, its denominator counts checkpoints that were never attempted
as failures, which would bias it DOWNWARD relative to this arm. The band stays as pre-registered,
but a result in its upper half is not evidence of a discrepancy for that reason alone.

### What counts as "the control fails"

This is the decision this arm exists to make, and it is written before the run.

Let `S` be the count of the 17 checkpoints with `strict_pass_rate == 1.0`, and `C` the mean
`core_pass_rate` over the 17.

| Band       | Condition                | Verdict                                                          |
|------------|--------------------------|------------------------------------------------------------------|
| SATURATED  | `S >= 12` (about 70%)    | The control does NOT fail. Subset refuted, no coordinator arm.   |
| USABLE     | `1 <= S <= 11`           | The control fails with headroom. PROCEED to the coordinator arm. |
| FLOOR      | `S == 0` and `C < 0.20`  | Too hard to resolve an improvement. Subset refuted, pick easier. |
| FLOOR-EDGE | `S == 0` and `C >= 0.20` | Usable but fragile: report it and decide with the user.          |

The SATURATED threshold is 12 of 17 because at that point fewer than 6 checkpoints remain to be
won and the measurement's resolution is one checkpoint, so a coordinator could at best tie inside
the noise. That is precisely the failure that voided rounds 1 and 2, and this time it costs one
arm to find rather than three.

The FLOOR band exists because it is the mirror risk and it is the reason ProgramBench was set
aside: a control at zero cannot show an improvement either.

### Calibration band, and what a miss means

The published Opus 5 figure on this exact subset, prompt and harness is `S = 4` of 17. Binomial
noise on n=17 is wide, so the band that does NOT contradict it is `1 <= S <= 8`.

`S` outside `[1, 8]` does not refute the benchmark. It indicts THIS APPARATUS, and the run is
investigated before any number from it is believed or reported as a result. Naming that before
the run is what makes this a calibration arm rather than merely a control arm.

### The strain check, and why this subset alone cannot answer the thesis

Decided with the user on 2026-09-04, before the first counted checkpoint.

Rank 05 exists because decomposition can only pay where ONE AGENT'S CONTEXT is the binding
constraint. This subset was chosen for calibration, not for strain, and all three problems are
rated Medium. SlopCodeBench's difficulty is iterative degradation across checkpoints, which is not
the same pressure. So this arm carries an extra reading whose only job is to say whether the
condition rank 05 needs was present at all:

**Per checkpoint, `input + cache_write` tokens against the model's context limit.** If the control
never approaches that limit on any of the 17 checkpoints, then this subset prices iterative
degradation and says nothing about decomposition under context pressure, and a later arm on the
catalog's Hard problems is required before the thesis is tested. That conclusion is licensed
whatever the band verdict turns out to be: the two questions are separate and this document must
not let a USABLE verdict be read as "the thesis can now be tested here".

### Replication: this arm is n=1, deliberately

The harness ships a `variance` command and a `configs/runs/variance.yaml`, which is the authors
saying run-to-run spread is material. This arm runs each problem ONCE anyway, decided with the
user before the run.

The reason it is safe HERE: the pre-registered bands are coarse (SATURATED at `S >= 12` of 17), so
plausible noise cannot flip the verdict this arm exists to produce.

The reason it is NOT yet safe for what comes next, stated now rather than after the numbers are
seen: a coordinator arm compared against a single control run measures SEPARATION, not causation.
Whether replication is needed for THAT comparison is decided from the per-checkpoint spread this
arm observes, read against the authors' published variance. That decision is about a future arm
and may not be used to revisit this arm's own band verdict, which the table above fixes.

### Void conditions, applying to this arm and to every later arm

Written as properties of a RUN, so that a later coordinator arm cannot be held to a laxer standard
than the control. An arm meeting any of these is VOID and is not tallied, not reported as a null
result:

1. Any checkpoint whose tests did not execute (harness error, docker failure, collection error).
   A checkpoint that scored 0 because nothing ran is not a checkpoint that scored 0.
2. Any checkpoint where the agent terminated on an auth failure. On this harness a rate limit is
   reported as an auth failure and no field distinguishes the two, so this is confirmed out of
   band before the run is called void or the token is called bad.
3. Any checkpoint where the agent hit the harness `step_limit` of 100 with work in flight. That is
   a bound this arm imposed, not a property of the system, and it is recorded per checkpoint.
4. Any run where the OAuth access token expired mid-problem. It expires 2026-09-04T19:19 and does
   not refresh inside the container, so a problem that straddles it is re-run, not repaired.

### Operational bounds

Problems run ONE at a time, not with `--num-workers`, so that a token refresh can be applied
between problems and so a failure is attributable to one problem. `--resume` exists and is the
recovery path for an interrupted problem.

The arm is COMPLETE only when all 17 checkpoints have a recorded reading. A partial arm is
reported as partial, per problem, and may NOT be compared against the published 4/17 until it is
complete: a subset of a subset is a different quantity.

### What this arm does not do

It does not measure agentdag. It cannot support any claim about decomposition, in either
direction. Its only outputs are the band verdict above, the calibration check, and the per
checkpoint cost and duration that a later coordinator arm needs in order to share a ceiling.

---

## Results

Run 2026-09-04, three invocations of `slop-code run --config run-control.yaml --problem <name>`,
one problem at a time, in the order `database_migration` (14:27 to 15:53), `circuit_eval`
(15:54 to 18:20), `dynamic_config_service_api` (19:20 to 21:31), all CEST. Run directories under
`~/agentdag-eval/slopcodebench/runs/opus-5_just-solve_high_20260904T{1427,1554,1920}`, outside
every git work tree. Every number below was computed by `scripts/slopcodebench_readings.py` or
read from the harness's own `checkpoint_results.jsonl` and `evaluation.json`; none was read off
a log by eye.

### Validity gate, applied before any tally

All four void conditions are clear on all 17 checkpoints:

1. Tests executed: every checkpoint carries pass and total counts in every category.
2. No auth-failure termination: every checkpoint's state is `ran`; no `ERROR`, no
   `HIT_RATE_LIMITED`.
3. Step limit: maximum 72 of 100 (`dynamic_config_service_api` checkpoint 4); no checkpoint
   reached the cap.
4. Token expiry: the first two problems finished at 18:20 against an expiry of 19:19; the token
   was refreshed at 19:20 (new expiry 2026-09-05 03:14) before the third problem was launched,
   exactly as the Operational bounds above prescribe.

### Readings

| problem                      | ck | strict | core  | iso   | peak prompt | new tokens | cost USD | seconds | steps | failed own | failed inherited | regression tests |
|------------------------------|----|--------|-------|-------|-------------|------------|----------|---------|-------|------------|------------------|------------------|
| `circuit_eval`               | 1  | 1.000  | 1.000 | 1.000 | 69,556      | 44,621     | 1.57     | 393     | 11    | 0          | 0                | 0                |
| `circuit_eval`               | 2  | 1.000  | 1.000 | 1.000 | 80,051      | 60,851     | 1.73     | 323     | 20    | 0          | 0                | 36               |
| `circuit_eval`               | 3  | 1.000  | 1.000 | 1.000 | 124,575     | 103,680    | 3.50     | 750     | 25    | 0          | 0                | 99               |
| `circuit_eval`               | 4  | 1.000  | 1.000 | 1.000 | 94,396      | 69,955     | 2.47     | 595     | 30    | 0          | 0                | 205              |
| `circuit_eval`               | 5  | 0.998  | 1.000 | 0.963 | 121,830     | 97,389     | 3.54     | 940     | 30    | 1          | 0                | 430              |
| `circuit_eval`               | 6  | 0.998  | 1.000 | 1.000 | 122,590     | 102,140    | 3.88     | 763     | 47    | 0          | 1                | 457              |
| `circuit_eval`               | 7  | 0.998  | 1.000 | 1.000 | 173,901     | 150,058    | 6.49     | 1386    | 53    | 0          | 1                | 508              |
| `circuit_eval`               | 8  | 0.998  | 1.000 | 1.000 | 211,733     | 191,743    | 9.18     | 2829    | 61    | 0          | 1                | 529              |
| `database_migration`         | 1  | 1.000  | 1.000 | 1.000 | 55,011      | 58,073     | 1.17     | 208     | 7     | 0          | 0                | 0                |
| `database_migration`         | 2  | 0.806  | 0.000 | 0.478 | 81,858      | 60,237     | 1.60     | 360     | 8     | 12         | 0                | 39               |
| `database_migration`         | 3  | 0.851  | 1.000 | 1.000 | 136,004     | 111,355    | 4.10     | 861     | 22    | 0          | 12               | 62               |
| `database_migration`         | 4  | 0.855  | 0.833 | 0.867 | 164,598     | 140,521    | 6.01     | 964     | 44    | 4          | 12               | 87               |
| `database_migration`         | 5  | 0.825  | 0.333 | 0.650 | 129,363     | 105,234    | 4.54     | 734     | 42    | 7          | 16               | 117              |
| `dynamic_config_service_api` | 1  | 0.957  | 1.000 | 0.957 | 96,579      | 99,849     | 2.74     | 563     | 15    | 2          | 0                | 0                |
| `dynamic_config_service_api` | 2  | 0.968  | 1.000 | 0.978 | 135,199     | 113,821    | 4.21     | 822     | 30    | 1          | 2                | 47               |
| `dynamic_config_service_api` | 3  | 0.342  | 0.417 | 0.342 | 182,190     | 158,113    | 6.36     | 1099    | 44    | 4          | 0                | 0                |
| `dynamic_config_service_api` | 4  | 0.519  | 0.500 | 0.519 | 252,814     | 233,393    | 11.63    | 2017    | 72    | 24         | 0                | 0                |

Column notes: `peak prompt` is the largest single-request prompt occupancy in the checkpoint
(`input + cache_read + cache_write` on one request, per the strain-check correction in the
readings script); `new tokens` is the protocol's cost unit, `input + cache_write` summed over the
checkpoint; `failed own` counts failed tests whose origin is this checkpoint and `failed
inherited` those whose origin is an earlier one (the harness keys each test group as
`<origin checkpoint>-<category>`); `regression tests` is the size of the checkpoint's regression
suite. `dynamic_config_service_api` checkpoints 3 and 4 carry no regression suite at all, so
their `failed inherited` is 0 by construction, not by repair.

**Primary readings:** mean `strict_pass_rate` **0.889**, mean `core_pass_rate` **0.828** over 17.

**Secondary reading, comparable to the published figure:** `S = 5` of 17 checkpoints with
`strict_pass_rate == 1.0`.

Totals: cost USD 74.73 by the harness's own cost model (the run was on a Max subscription, so this
is quota, not an invoice); 15,608 seconds of checkpoint time; 1,901,033 new tokens.

### Band verdict

`S = 5` and `C = 0.828`: **USABLE**. The control fails with headroom, and the coordinator arm may
proceed on this subset.

**Calibration:** `S = 5` is inside the pre-registered band `[1, 8]` around the published Opus 5
figure of 4, so this apparatus is not indicted. The pass-policy caveat above applies in the
expected direction: this arm attempted every checkpoint under `any-case`, and the published run's
policy is unknown, so a reading one above the published figure is not evidence of a discrepancy.

### The strain check

Peak single-request occupancy exceeds 200,000 tokens on 2 of 17 checkpoints (`circuit_eval` 8 at
211,733 and `dynamic_config_service_api` 4 at 252,814) and 150,000 on 5 of 17. Per checkpoint,
occupancy rises with the checkpoint index on every problem. The condition rank 05 needs, one
agent's context as the binding constraint, was PRESENT on this subset. The pre-registration's
own expectation, that a Medium subset chosen for calibration might never approach the limit, is
refuted by the measurement, so a USABLE verdict here may be read as "the thesis can be tested on
this subset" after all. That reading is licensed by the strain numbers, not by the band.

### How the control fails: it never goes back

Splitting each checkpoint's failed tests by the checkpoint they originated in gives the identity
`inherited(n) == inherited(n-1) + own(n-1)` on every one of the 12 transitions that carry a
regression suite, with no exception. The control repaired ZERO carried-forward defects in 17
checkpoints. Concretely: `circuit_eval`'s whole strict score of 4 out of 8 is decided by one
test, `test_json_errors`, introduced and failed at checkpoint 5 and still failing at 8;
`database_migration`'s checkpoint 2 scored 0 of 3 on Core and those same tests fail as
regressions through checkpoints 3, 4 and 5.

Read at source in the harness, that is not the agent's choice and not the stop condition (no
checkpoint reached the step cap; every one ended because the agent declared itself finished).
The loop is one-way and feedback-free in four independent ways: no evaluation result ever
reaches the agent (the `concurrent_evaluation` docstring at `agent_runner/models.py:172-183` says
so outright); the context is reset per checkpoint (`runner.py:662`, `finish_checkpoint(
reset_context=True)`; `--continue` only on the internal retry path); the prompt carries that
checkpoint's DELTA spec only (the saved `prompt.txt` sizes match the checkpoint `.md` sizes, and
checkpoint 6's prompt has zero occurrences of "regression", "previous checkpoint", "failing" or
"test suite"); and the prompt says "That is all you need to do." The agent was never told
`test_json_errors` was broken and had no way to find out.

A coordinator arm under this same loop would get the same delta, the same workspace and the same
silence, so it could not know either. What that means for the comparison is decided and
pre-registered in a separate document, not here: this arm's outputs are the band verdict, the
calibration check, the strain reading and the per-checkpoint readings above, and nothing in this
section may be read as a claim about decomposition.

### Conditions the readings were taken under

- **Host load.** Another session's `semdex` job (`preembed_vectors.py`, nice 19, about 9 to 13
  cores on live samples) ran from 15:14:51 through the end of the arm. `database_migration` had
  a quiet host for its first 47 minutes; `circuit_eval` and `dynamic_config_service_api` ran
  fully loaded. The job runs at nice 19 and the checkpoint work is dominated by model latency,
  but the per-checkpoint `seconds` column is not comparable across the three problems, nor to a
  later arm on a quiet host, without this caveat. Cost and token readings are unaffected.
- **CLI version.** The saved traces' init lines report `claude_code_version` 2.1.260 on every
  checkpoint, matching the pin in the held-fixed table.
- **Tool use.** Across all 17 checkpoints the control invoked Bash 549 times, Write 3 times and
  Read once. The CLI advertised 25 tools including `Task`, `Skill`, `WebSearch` and `WebFetch`;
  `Task` was never used, so the control did not fan out sub-agents, though nothing stopped it.
- **Replication input.** This arm is n=1 per problem, so it observed no per-checkpoint spread of
  its own; the replication decision for a later comparison must draw on the authors' published
  variance instead, and is made in that comparison's pre-registration.
