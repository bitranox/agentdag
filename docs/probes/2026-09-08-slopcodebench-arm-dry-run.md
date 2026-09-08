# Task 12: the coordinator arm's plumbing dry run

Written 2026-09-08. Task 12 of `PLANS/2026-09-04-corrected-pair-plan.md`. This is the
controller's own record of what the dry run established before any counted coordinator
checkpoint runs. It is not a result, and nothing here belongs in a readings tally.

Provenance is marked per entry, because these were established four different ways and they
are not equally strong:

- **[source]** read in the code of this repo or of the harness clone.
- **[built]** the image was actually built, or the installer actually ran.
- **[measured]** a run happened and this is read off its own artifacts.
- **[inferred]** neither of the above; my reasoning, and weakest.

The harness clone is `~/agentdag-eval/slopcodebench/slop-code-bench` at `06b5c06`. The arm was
installed at agentdag `f85cfe5`.

## What the arm was short of, against its own frozen pre-registration

Task 12's first act was to check the shipped arm against the held-fixed table of
`docs/probes/2026-09-05-slopcodebench-corrected-pair.md` rather than against the code. Three
settings were missing, and for two of them a default supplied a plausible-looking wrong value,
which is why four rounds of review had not seen them. **[source]**

| Pre-registered                                           | Where it lands | What the arm did before                                                 |
|----------------------------------------------------------|----------------|-------------------------------------------------------------------------|
| `opus-5` on every node, no cheaper tier anywhere         | tier policy    | no policy at all, so the shipped table put every `work` node on sonnet  |
| `thinking: high`, every node receives it                 | tier policy    | no effort setting; `work` inherited                                     |
| `run_limits.tokens_per_row` 1,200,000 per checkpoint run | tier policy    | the shipped table's 8,000,000 on the opus row                           |
| `kernel.deny_bash` cut to the three git/gh entries       | arm config     | the shipped default, which also closes `curl -X POST` and `curl --data` |
| `kernel.parallel` 8                                      | arm config     | inherited from a default that can move                                  |

All five are now stated: `deploy/slopcodebench/arm-tier-policy.yaml` for the first three, the
`settings` map of `deploy/slopcodebench/agentdag.yaml` for the last two, and
`tests/test_scb_arm_preregistration.py` fails by name on each if any reverts.

## The three checks Task 12 carried

### (a) Does the harness tally `agent.usage` for a checkpoint whose `run()` raised?

**Yes**, and both halves were read at source. **[source]**

`Agent.run_checkpoint` (harness, `agent_runner/agent.py`) catches `AgentError`, sets
`had_error`, and returns `CheckpointInferenceResult(usage=self.usage, ...)` - the same mutable
tracker the agent harvested into. `_run_checkpoint` then builds the summary with
`usage=self.agent.usage`. On our side, `AgentdagAgent.run` calls `_harvest_abandoned(before)`
inside the `AgentError` message on the timeout path and `_harvest(run_id)` before the raise on
the unexpected-status path, so in both cases the harvest happens before the raise.

Two things that came with it and are not in any note yet:

- `max_retries` defaults to **2** on `AgentCostLimits`, and neither arm's config sets it. On
  the control a retry is the base class's `RETRY_PROMPT` through `--continue`. On the
  coordinator arm `retry()` is also the base class's, so a retry is a whole fresh `plan-goal`
  run against the same `/workspace` with the goal "Continue from where you left off." A
  checkpoint whose `run()` raises therefore costs up to **three** coordinator runs. **[source]**
- The 1,200,000 token guard is per RUN, and each retry is a new run with a fresh budget, so the
  effective per-checkpoint ceiling is 3,600,000, not 1,200,000. The write-up has to say this.
  **[source]**

### (b) Does `kernel.deny_bash` need `curl --data` opened?

**Yes**, and on fairness grounds rather than on whether the checkpoint strictly requires it.
The pre-registration's decision 3 already settled it: the calibration control used exactly
those commands four times on this problem to exercise the HTTP API it was building, and the
hidden tests reach the service over `httpx`, not `curl`. The arm was taking the shipped default
that closes them. Fixed in the arm config. **[source]**

### (c) A coordinator node's own `data.claude_code_version` and permission mode

**Still owed.** It needs a node transcript from a run that actually dispatched a node, and the
dry run's nodes never got past authentication. What the dry run did establish is the layer
below it: the image asserts `claude --version` is `2.1.260` at build time and deletes the SDK's
bundled binary, and that assertion now runs under a shell where it can fail. **[built]**

## What was built and proved without spending anything

- The installer runs clean against the harness clone and reports the commit it patched.
  `--policy` is new: the tier policy path is a launch fact, so it is written over the committed
  comment exactly as `--agentdag-version` fills the version in. **[built]**
- **The image did not build.** The template's own closing assertion failed with
  `agentdag: command not found`, exit 127. The base image declares
  `SHELL ["/bin/bash", "-lc"]`, and a login bash sources `/etc/profile`, which ASSIGNS `PATH`
  rather than extending it, so every `ENV PATH` in the template was discarded inside every
  `RUN`. Probed in the base image: `bash -lc` resolves neither `claude` nor `agentdag`, `sh -c`
  resolves both; the control arm's own image cannot resolve `claude` under a login shell
  either. The agent spawns its runtime with `disable_setup=True`, so the harness execs
  `/bin/sh -c` and the command gets the image's own PATH - which is why the control has run 17
  checkpoints on an image whose login shell finds nothing. Declaring `SHELL ["/bin/sh", "-c"]`
  before the first `RUN` makes the build agree with the runtime. Removing the `ENV PATH` line
  then fails the build 127 again, so the assertion is live. **[measured]**
- The image then built at the tag the harness itself renders,
  `slop-code:agentdag-f85cfe5...-python3.12`, through `AgentdagConfig.get_docker_file`.
  **[built]**

## The zero-token rehearsal, and what it found

The arm was run end to end against `dynamic_config_service_api` with a deliberately invalid
credential, so every coordinator run would die at its first model call. Run directory
`~/agentdag-eval/slopcodebench/rehearsal/runs/REHEARSAL-agentdag_20260908T031423`. **[measured]**

**Every arm setting took.** The coordinator's own `state.json` records what it ran under:

```
parallel 8, max_turns 100, permission_mode bypassPermissions,
deny_bash ["git push","gh pr","gh release"], deny_tools [],
gate_command [".venv/bin/python","-m","pytest","-q"],
policy_path /tmp/agent_home/agentdag-policy.yaml,
policy_version sha256:33aefe8cb2f6b53e0b74c5a3f31b1b390a4ab41e30d67e096d0676fdac81a5f9,
tokens_by_row {"opus": 0}
```

That hash is the committed `arm-tier-policy.yaml`'s own content hash, so the container ran the
file in this repo and not a copy of anything. `tokens_by_row` naming only `opus` is the
single-row table taking effect. The run store was harvested into the checkpoint's artifacts,
so `save_artifacts` works.

**And it found the defect this dry run was worth running for.** With an invalid credential the
arm produced a complete, green-looking run of zeros:

- Each planner dispatch failed with
  `Failed to authenticate. API Error: 401 OAuth access token is invalid.`, recorded as
  `error.type: executor_error`, `transient: true`.
- A planner that errors writes no `plan.json`, so the re-plan path classified it as
  `plan_invalidated`, reason `"the planner node wrote no plan.json"`, and re-dispatched. Four
  planner dispatches in total: the first plus `max_replans: 3`.
- The run then suspended: `status: suspended`, `cursor: a_planning`,
  `suspend_reason: decision`.
- The agent treats a suspension as a normal end, so `run()` did not raise. The harness recorded
  `state: ran`, cost `0.0`, 0 of 47 tests, no error. All four checkpoints ran the same way,
  the harness printed a Run Summary, and every exit code in the chain was 0, including the
  launcher's `END dynamic_config_service_api rc=0` and `ARM COMPLETE`.

So `escalation.on_auth_failure: fail_run` never fires on this path: the auth failure is
laundered into "the planner wrote no plan.json" one layer up, and nothing downstream can tell
a credential failure from a coordinator that legitimately scored zero. This matters directly
to the counted arm, because the Claude CLI reports an exhausted subscription quota as
`authentication_failed` with no field distinguishing it - so a quota exhaustion partway through
Task 13 would produce an arm of zeros that reads as a real result, with rc 0 everywhere.

`tokens_by_row` is the signal that separates them: a checkpoint whose coordinator suspended
having charged zero tokens cannot have done any work. Whether that becomes a void rule for
Task 13, and whether agentdag should refuse to re-plan past an auth failure at all, are both
open (`OPEN-WORK.md`).

## Still owed by Task 12

- The paid one-checkpoint parity run. The comparator is on disk: the corrected control's
  `dynamic_config_service_api` `checkpoint_1` took 526 s, 2.32 USD, 14 steps, 116,901 charged
  tokens (3,270 input + 66,860 cache write + 46,771 output), and scored 47 of 47 strict.
  **[measured, from the control's own `checkpoint_results.jsonl`]**
- Check (c), which that run supplies.
- A one-checkpoint catalog is derived for it at
  `~/agentdag-eval/slopcodebench/scb-problems-cumulative-cp1`, because the harness runs every
  checkpoint a problem's `config.yaml` declares; it differs from the control's catalog only in
  the three dropped checkpoint entries, verified by diff, and `checkpoint_1.md` is byte
  identical.
